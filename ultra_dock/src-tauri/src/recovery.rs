//! Taskbar recovery is a write-ahead journal, not a list saved after DeleteTab.
//! Each Dock process owns a separate generation identified by PID + creation time.
use serde::{Deserialize, Serialize};
use std::io;
const MAX_WINDOWS: usize = 4096;

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq, Hash)]
pub struct Owner { pub pid: u32, pub started: u64 }

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq, Hash)]
struct Window { hwnd: isize, process: Owner }

#[derive(Clone, Debug, Deserialize, Serialize)]
struct Journal { version: u32, owner: Owner, windows: Vec<Window> }

impl Journal {
    fn new(owner: Owner) -> Self { Self { version: 1, owner, windows: vec![] } }

    fn decode(bytes: &[u8], owner: Owner) -> io::Result<Self> {
        let value: Self = serde_json::from_slice(bytes).map_err(io::Error::other)?;
        let unique: std::collections::HashSet<_> = value.windows.iter().map(|w| w.hwnd).collect();
        if value.version != 1 || value.owner != owner || value.owner.pid == 0 || value.owner.started == 0
            || value.windows.len() > MAX_WINDOWS || unique.len() != value.windows.len()
            || value.windows.iter().any(|w| w.hwnd == 0 || w.process.pid == 0 || w.process.started == 0) {
            return Err(io::Error::new(io::ErrorKind::InvalidData, "unsupported recovery journal or wrong owner"));
        }
        Ok(value)
    }

    fn hide_with(&mut self, window: Window, persist: impl FnOnce(&Self) -> io::Result<()>, hide: impl FnOnce() -> bool) -> bool {
        // Capture a durable intent before the first possible taskbar side effect.
        if self.windows.len() >= MAX_WINDOWS && !self.windows.iter().any(|w| w.hwnd == window.hwnd) { return false; }
        let previous = self.windows.clone();
        self.windows.retain(|w| w.hwnd != window.hwnd);
        self.windows.push(window);
        if persist(self).is_err() { self.windows = previous; return false; }
        hide()  // a failed hide still leaves a harmless recovery intent
    }

    fn restore_with(&mut self, restore: impl Fn(&Window) -> bool, persist: impl FnOnce(&Self) -> io::Result<()>) -> io::Result<()> {
        let previous = self.windows.clone();
        self.windows.retain(|w| !restore(w));
        if self.windows == previous { return Ok(()); }
        if let Err(error) = persist(self) { self.windows = previous; return Err(error); }
        Ok(())
    }
}

#[cfg(windows)]
mod win {
    use super::*;
    use std::collections::HashSet;
    use std::fs::{self, OpenOptions};
    use std::io::{Read, Write};
    use std::os::windows::ffi::OsStrExt;
    use std::path::{Path, PathBuf};
    use std::sync::{Mutex, OnceLock};
    use windows::core::PCWSTR;
    use windows::Win32::Foundation::{CloseHandle, FILETIME, HANDLE, HWND, WAIT_ABANDONED, WAIT_OBJECT_0};
    use windows::Win32::Storage::FileSystem::{MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH};
    use windows::Win32::System::Threading::{CreateMutexW, GetProcessTimes, OpenProcess, ReleaseMutex, WaitForSingleObject, PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_SYNCHRONIZE};
    use windows::Win32::UI::Shell::ITaskbarList;
    use windows::Win32::UI::WindowsAndMessaging::{GetWindowThreadProcessId, IsWindow};

    struct Handle(HANDLE);
    impl Drop for Handle { fn drop(&mut self) { unsafe { let _ = CloseHandle(self.0); } } }
    struct GenerationLock(Handle);
    impl Drop for GenerationLock { fn drop(&mut self) { unsafe { let _ = ReleaseMutex(self.0.0); } } }

    fn lock_generation(_owner: Owner) -> Option<GenerationLock> {
        // All generations share a transaction lock. A late old guard must see a
        // newly persisted claim before it decides whether to restore that HWND.
        let name: Vec<u16> = "Local\\UltraSuite.DockJournal.Transactions\0".encode_utf16().collect();
        let handle = Handle(unsafe { CreateMutexW(None, false, PCWSTR(name.as_ptr())).ok()? });
        let result = unsafe { WaitForSingleObject(handle.0, 1_000) };
        if result == WAIT_OBJECT_0 || result == WAIT_ABANDONED { Some(GenerationLock(handle)) } else { None }
    }

    fn creation(handle: HANDLE) -> Option<u64> {
        let (mut created, mut exited, mut kernel, mut user) = (FILETIME::default(), FILETIME::default(), FILETIME::default(), FILETIME::default());
        unsafe { GetProcessTimes(handle, &mut created, &mut exited, &mut kernel, &mut user).ok()?; }
        Some(((created.dwHighDateTime as u64) << 32) | created.dwLowDateTime as u64)
    }

    pub fn process_owner(pid: u32) -> Option<Owner> {
        let handle = Handle(unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid).ok()? });
        Some(Owner { pid, started: creation(handle.0)? })
    }

    fn window_owner(hwnd: isize) -> Option<Window> {
        let mut pid = 0;
        unsafe { if GetWindowThreadProcessId(HWND(hwnd as _), Some(&mut pid)) == 0 { return None; } }
        Some(Window { hwnd, process: process_owner(pid)? })
    }

    // None means identity could not be established. Never interpret access denied as dead.
    fn owner_alive(owner: Owner) -> Option<bool> {
        let handle = match unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_SYNCHRONIZE, false, owner.pid) } {
            Ok(h) => Handle(h),
            Err(error) if error.code().0 as u32 == 0x80070057 => return Some(false), // invalid PID
            Err(_) => return None,
        };
        if creation(handle.0)? != owner.started { return Some(false); }
        match unsafe { WaitForSingleObject(handle.0, 0) }.0 { 0 => Some(false), 258 => Some(true), _ => None }
    }

    fn journal_path(owner: Owner) -> PathBuf { crate::appdata(&format!("dock-recovery-v1-{}-{}.json", owner.pid, owner.started)) }

    fn atomic_save(path: &Path, journal: &Journal) -> io::Result<()> {
        if let Some(parent) = path.parent() { fs::create_dir_all(parent)?; }
        let temp = path.with_extension(format!("{}.tmp", std::process::id()));
        let bytes = serde_json::to_vec(journal).map_err(io::Error::other)?;
        let result = (|| {
            let mut file = OpenOptions::new().create(true).truncate(true).write(true).open(&temp)?;
            file.write_all(&bytes)?; file.sync_all()?; drop(file);
            let from: Vec<u16> = temp.as_os_str().encode_wide().chain(Some(0)).collect();
            let to: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
            unsafe { MoveFileExW(PCWSTR(from.as_ptr()), PCWSTR(to.as_ptr()), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH) }
                .map_err(io::Error::other)
        })();
        if result.is_err() { let _ = fs::remove_file(&temp); }
        result
    }

    fn load(path: &Path, owner: Owner) -> io::Result<Journal> {
        let file = fs::File::open(path)?;
        let mut bytes = vec![];
        file.take(1_048_577).read_to_end(&mut bytes)?;
        if bytes.len() > 1_048_576 { return Err(io::Error::new(io::ErrorKind::InvalidData, "recovery journal too large")); }
        Journal::decode(&bytes, owner)
    }

    fn restore(window: &Window, taskbar: &ITaskbarList) -> bool {
        let hwnd = HWND(window.hwnd as _);
        if !unsafe { IsWindow(Some(hwnd)).as_bool() } { return true; }
        let mut pid = 0;
        unsafe { GetWindowThreadProcessId(hwnd, Some(&mut pid)); }
        if pid == 0 { return false; }
        if pid != window.process.pid { return true; } // handle now belongs to somebody else
        match process_owner(pid) {
            Some(owner) if owner != window.process => true, // recycled PID, don't touch its window
            Some(_) => unsafe { taskbar.AddTab(hwnd).is_ok() },
            None => false, // preserve unknown or inaccessible records for a later retry
        }
    }

    struct State { journal: Journal, hidden: HashSet<Window> }
    static CURRENT: OnceLock<Option<Mutex<State>>> = OnceLock::new();
    fn state() -> Option<&'static Mutex<State>> {
        CURRENT.get_or_init(|| process_owner(std::process::id()).map(|owner| Mutex::new(State { journal: Journal::new(owner), hidden: HashSet::new() }))).as_ref()
    }

    pub fn hide_window(hwnd: isize, reapply: bool, taskbar: &ITaskbarList) -> bool {
        let Some(window) = window_owner(hwnd) else { return false; };
        let Some(mut state) = state().and_then(|s| s.lock().ok()) else { return false; };
        if !reapply && state.hidden.contains(&window) { return true; }
        let owner = state.journal.owner;
        let Some(_lock) = lock_generation(owner) else { return false; };
        let path = journal_path(owner);
        let previously_hidden = state.hidden.contains(&window);
        let success = if state.journal.windows.contains(&window) && path.is_file() {
            unsafe { taskbar.DeleteTab(HWND(hwnd as _)).is_ok() }
        } else {
            state.journal.hide_with(window, |journal| atomic_save(&path, journal), || unsafe { taskbar.DeleteTab(HWND(hwnd as _)).is_ok() })
        };
        if success { state.hidden.insert(window); }
        success || previously_hidden
    }

    pub fn restore_window(hwnd: isize, taskbar: &ITaskbarList) { restore_current(Some(hwnd), taskbar); }
    pub fn restore_all(taskbar: &ITaskbarList) { restore_current(None, taskbar); }
    pub fn prune_closed() {
        let Some(mut state) = state().and_then(|s| s.lock().ok()) else { return; };
        let owner = state.journal.owner;
        let Some(_lock) = lock_generation(owner) else { return; };
        let path = journal_path(owner);
        let _ = state.journal.restore_with(|w| !unsafe { IsWindow(Some(HWND(w.hwnd as _))).as_bool() }, |j| atomic_save(&path, j));
        let outstanding: HashSet<_> = state.journal.windows.iter().copied().collect();
        state.hidden.retain(|w| outstanding.contains(w));
    }
    fn restore_current(only: Option<isize>, taskbar: &ITaskbarList) {
        let Some(mut state) = state().and_then(|s| s.lock().ok()) else { return; };
        if state.journal.windows.is_empty() { return; }
        if let Some(hwnd) = only { if !state.journal.windows.iter().any(|w| w.hwnd == hwnd) { return; } }
        let owner = state.journal.owner;
        let Some(_lock) = lock_generation(owner) else { return; };
        let path = journal_path(owner);
        let _ = state.journal.restore_with(|w| (only.is_none() || only == Some(w.hwnd)) && restore(w, taskbar), |j| atomic_save(&path, j));
        let outstanding: HashSet<_> = state.journal.windows.iter().copied().collect();
        state.hidden.retain(|w| outstanding.contains(w));
    }

    pub fn recover_owner(owner: Owner, taskbar: &ITaskbarList) {
        let Some(_lock) = lock_generation(owner) else { return; };
        if owner_alive(owner) != Some(false) { return; }
        let path = journal_path(owner);
        let Ok(mut journal) = load(&path, owner) else { return; }; // malformed and unknown versions stay on disk
        if journal.restore_with(|w| match other_claim(w, owner) {
            Some(true) => true, // a live newer owner has durably accepted responsibility
            Some(false) => restore(w, taskbar),
            None => false, // uncertain identity: retain for retry
        }, |j| atomic_save(&path, j)).is_ok() && journal.windows.is_empty() {
            let _ = fs::remove_file(path);
        }
    }

    fn owners() -> Vec<Owner> {
        let Ok(files) = fs::read_dir(crate::appdata("")) else { return vec![]; };
        let mut owners = vec![];
        for entry in files.flatten().take(10_000) {
            let name = entry.file_name().to_string_lossy().into_owned();
            let Some(body) = name.strip_prefix("dock-recovery-v1-").and_then(|s| s.strip_suffix(".json")) else { continue; };
            let Some((pid, started)) = body.split_once('-') else { continue; };
            if let (Ok(pid), Ok(started)) = (pid.parse(), started.parse()) { owners.push(Owner { pid, started }); }
        }
        owners
    }

    fn other_claim(window: &Window, previous: Owner) -> Option<bool> {
        for owner in owners().into_iter().filter(|owner| *owner != previous) {
            let Ok(journal) = load(&journal_path(owner), owner) else { continue; };
            if journal.windows.contains(window) {
                match owner_alive(owner) { Some(true) => return Some(true), None => return None, _ => {} }
            }
        }
        Some(false)
    }

    pub fn recover_previous(taskbar: &ITaskbarList) {
        for owner in owners() { recover_owner(owner, taskbar); }
        // Old HWND-only files cannot establish process identity. Leave them for the
        // old guard or the explicit legacy restore tool instead of trusting them here.
    }

    #[cfg(test)]
    mod file_tests {
        use super::*;
        #[test]
        fn atomic_replace_produces_complete_versioned_document() {
            let owner = process_owner(std::process::id()).unwrap();
            let stamp = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
            let path = std::env::temp_dir().join(format!("ultrasuite-journal-test-{}-{}.json", owner.pid, stamp));
            let mut journal = Journal::new(owner);
            atomic_save(&path, &journal).unwrap();
            for hwnd in 1..20 {
                journal.windows.push(Window { hwnd, process: owner });
                atomic_save(&path, &journal).unwrap();
                assert_eq!(load(&path, owner).unwrap().windows, journal.windows);
            }
            fs::remove_file(&path).unwrap();
        }
        #[test]
        fn current_process_identity_has_creation_time_and_is_live() {
            let owner = process_owner(std::process::id()).unwrap();
            assert!(owner.started > 0);
            assert_eq!(owner_alive(owner), Some(true));
            assert_eq!(owner_alive(Owner { started: owner.started - 1, ..owner }), Some(false));
        }
        #[test]
        fn generation_mutex_serializes_another_thread() {
            let owner = process_owner(std::process::id()).unwrap();
            let _lock = lock_generation(owner).unwrap();
            assert!(std::thread::spawn(move || lock_generation(owner).is_none()).join().unwrap());
        }
    }
}

#[cfg(windows)]
pub use win::{hide_window, process_owner, prune_closed, recover_owner, recover_previous, restore_all, restore_window};

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::Cell;
    fn owner(pid: u32) -> Owner { Owner { pid, started: 1000 + pid as u64 } }
    fn window(hwnd: isize) -> Window { Window { hwnd, process: owner(7) } }
    #[test]
    fn intent_is_durable_before_hide() {
        let stage = Cell::new(0);
        let mut journal = Journal::new(owner(1));
        assert!(journal.hide_with(window(42), |j| { assert_eq!(j.windows, vec![window(42)]); stage.set(1); Ok(()) }, || { assert_eq!(stage.get(), 1); stage.set(2); true }));
        assert_eq!(stage.get(), 2);
    }
    #[test]
    fn persistence_failure_never_hides() {
        let mut journal = Journal::new(owner(1));
        assert!(!journal.hide_with(window(42), |_| Err(io::Error::other("disk full")), || panic!("must not hide")));
        assert!(journal.windows.is_empty());
    }
    #[test]
    fn failed_hide_retains_recovery_intent() {
        let mut journal = Journal::new(owner(1));
        assert!(!journal.hide_with(window(42), |_| Ok(()), || false));
        assert_eq!(journal.windows, vec![window(42)]);
    }
    #[test]
    fn failed_restores_stay_recorded() {
        let mut journal = Journal { version: 1, owner: owner(1), windows: vec![window(1), window(2)] };
        journal.restore_with(|w| w.hwnd == 1, |_| Ok(())).unwrap();
        assert_eq!(journal.windows, vec![window(2)]);
    }
    #[test]
    fn failed_restore_commit_retains_all_intents() {
        let mut journal = Journal { version: 1, owner: owner(1), windows: vec![window(1)] };
        assert!(journal.restore_with(|_| true, |_| Err(io::Error::other("read only"))).is_err());
        assert_eq!(journal.windows, vec![window(1)]);
    }
    #[test]
    fn rejects_malformed_unknown_version_and_other_generation() {
        assert!(Journal::decode(b"{", owner(1)).is_err());
        let mut journal = Journal::new(owner(1));
        let bytes = serde_json::to_vec(&journal).unwrap();
        assert!(Journal::decode(&bytes, owner(2)).is_err());
        assert!(Journal::decode(&bytes, Owner { pid: 1, started: 9999 }).is_err());
        journal.version = 2;
        assert!(Journal::decode(&serde_json::to_vec(&journal).unwrap(), owner(1)).is_err());
    }
    #[test]
    fn same_handle_new_process_replaces_old_intent_before_hiding() {
        let mut journal = Journal { version: 1, owner: owner(1), windows: vec![window(42)] };
        let next = Window { hwnd: 42, process: owner(8) };
        assert!(journal.hide_with(next, |j| { assert_eq!(j.windows, vec![next]); Ok(()) }, || true));
        assert_eq!(journal.windows, vec![next]);
    }
    #[test]
    fn unchanged_restore_does_not_rewrite_the_journal() {
        let mut journal = Journal { version: 1, owner: owner(1), windows: vec![window(1)] };
        journal.restore_with(|_| false, |_| panic!("no changed state to persist")).unwrap();
    }
    #[test]
    fn rejects_duplicate_handles_and_missing_identity() {
        let journal = Journal { version: 1, owner: owner(1), windows: vec![window(1), window(1)] };
        assert!(Journal::decode(&serde_json::to_vec(&journal).unwrap(), owner(1)).is_err());
        let journal = Journal { windows: vec![Window { hwnd: 0, process: owner(7) }], ..Journal::new(owner(1)) };
        assert!(Journal::decode(&serde_json::to_vec(&journal).unwrap(), owner(1)).is_err());
    }
    #[test]
    fn bounded_journal_refuses_new_hides_before_capacity_is_exceeded() {
        let mut journal = Journal { version: 1, owner: owner(1), windows: (1..=MAX_WINDOWS as isize).map(window).collect() };
        assert!(!journal.hide_with(window(99999), |_| panic!("do not persist overflow"), || panic!("do not hide overflow")));
        assert!(Journal::decode(&serde_json::to_vec(&journal).unwrap(), owner(1)).is_ok());
    }
}
