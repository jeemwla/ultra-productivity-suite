//! Taskbar recovery is a write-ahead journal, not a list saved after DeleteTab.
//! Each Dock process owns a separate generation identified by PID + creation time.
use serde::{Deserialize, Serialize};
use std::io;
const MAX_WINDOWS: usize = 4096;

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq, Hash)]
#[serde(deny_unknown_fields)]
pub struct Owner { pub pid: u32, pub started: u64 }

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq, Hash)]
#[serde(deny_unknown_fields)]
struct Window { hwnd: isize, process: Owner }

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
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
        if self.windows != previous {
            if let Err(error) = persist(self) { self.windows = previous; return Err(error); }
        }
        if self.windows.is_empty() { Ok(()) } else { Err(io::Error::other("restoration is incomplete")) }
    }
}

/// All journals must already have been decoded under the shared transaction lock.
/// Unknown ownership cannot authorize AddTab, even when a known live claim also exists.
fn other_claim(window: &Window, others: &[Journal], alive: impl Fn(Owner) -> Option<bool>) -> Option<bool> {
    let mut claimed = false;
    for journal in others.iter().filter(|j| j.windows.contains(window)) {
        match alive(journal.owner) { Some(true) => claimed = true, None => return None, Some(false) => {} }
    }
    Some(claimed)
}

fn restore_unclaimed(journal: &mut Journal, others: &[Journal], alive: impl Fn(Owner) -> Option<bool>,
    restore: impl Fn(&Window) -> bool, persist: impl FnOnce(&Journal) -> io::Result<()>) -> io::Result<()> {
    journal.restore_with(|w| match other_claim(w, others, &alive) {
        Some(true) => true, // another live owner durably accepted responsibility; never call AddTab
        Some(false) => restore(w),
        None => false,
    }, persist)
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
        lock_named("Local\\UltraSuite.DockJournal.Transactions")
    }
    fn lock_named(name: &str) -> Option<GenerationLock> {
        let name: Vec<u16> = name.encode_utf16().chain(Some(0)).collect();
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

    fn journal_path(owner: Owner) -> PathBuf { crate::appdata(&journal_name(owner)) }
    fn journal_name(owner: Owner) -> String { format!("dock-recovery-v1-{}-{}.json", owner.pid, owner.started) }

    fn journal_roots() -> io::Result<Vec<PathBuf>> {
        // A missing environment or invalid contract cannot stand for an empty journal set.
        let root = std::env::var_os("APPDATA").filter(|s| !s.is_empty()).map(PathBuf::from)
            .ok_or_else(|| io::Error::other("APPDATA is unavailable"))?;
        ["Public", "Dev"].iter().map(|edition| {
            crate::edition::profile(edition).map(|p| p.paths(&root, &root).roaming).map_err(io::Error::other)
        }).collect()
    }

    struct Record { path: PathBuf, journal: Journal }
    fn scan_roots(roots: &[PathBuf]) -> io::Result<Vec<Record>> {
        let mut records = vec![];
        for root in roots {
            let files = match fs::read_dir(root) {
                Ok(files) => files,
                Err(e) if e.kind() == io::ErrorKind::NotFound => continue,
                Err(e) => return Err(e),
            };
            for (count, entry) in files.enumerate() {
                if count >= 10_000 { return Err(io::Error::other("recovery directory scan limit exceeded")); }
                let entry = entry?;
                let name = entry.file_name();
                let name = name.to_string_lossy();
                if !name.to_ascii_lowercase().starts_with("dock-recovery-") { continue; }
                // Unknown versions, interrupted writes, invalid filenames and malformed documents block the gate.
                let owner = name.strip_prefix("dock-recovery-v1-").and_then(|s| s.strip_suffix(".json"))
                    .and_then(|s| s.split_once('-')).and_then(|(pid, started)| Some(Owner { pid: pid.parse().ok()?, started: started.parse().ok()? }))
                    .filter(|owner| journal_name(*owner) == name)
                    .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "unknown recovery journal filename"))?;
                if !entry.file_type()?.is_file() { return Err(io::Error::other("recovery journal is not a regular file")); }
                let path = entry.path();
                records.push(Record { journal: load(&path, owner)?, path });
            }
        }
        Ok(records)
    }

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

    pub fn restore_window(hwnd: isize, taskbar: &ITaskbarList) { let _ = restore_current(Some(hwnd), taskbar); }
    pub fn restore_all(taskbar: &ITaskbarList) -> io::Result<()> { restore_current(None, taskbar) }
    pub fn prune_closed() {
        let Some(mut state) = state().and_then(|s| s.lock().ok()) else { return; };
        let owner = state.journal.owner;
        let Some(_lock) = lock_generation(owner) else { return; };
        let path = journal_path(owner);
        let _ = state.journal.restore_with(|w| !unsafe { IsWindow(Some(HWND(w.hwnd as _))).as_bool() }, |j| atomic_save(&path, j));
        let outstanding: HashSet<_> = state.journal.windows.iter().copied().collect();
        state.hidden.retain(|w| outstanding.contains(w));
    }
    fn restore_current(only: Option<isize>, taskbar: &ITaskbarList) -> io::Result<()> {
        let mut state = state().and_then(|s| s.lock().ok()).ok_or_else(|| io::Error::other("recovery state lock unavailable"))?;
        // Per-window maintenance has no side effect when there is no own intent.
        // Full restoration must still check disk, including when memory is empty.
        if only.is_some_and(|hwnd| !state.journal.windows.iter().any(|w| w.hwnd == hwnd)) { return Ok(()); }
        let owner = state.journal.owner;
        let _lock = lock_generation(owner).ok_or_else(|| io::Error::other("recovery transaction lock unavailable"))?;
        let path = journal_path(owner);
        let records = scan_roots(&journal_roots()?)?;
        // A durable own intent missing from memory must never be acknowledged as restored.
        if records.iter().any(|r| r.path == path && r.journal.windows != state.journal.windows) {
            return Err(io::Error::other("own recovery journal disagrees with memory"));
        }
        let others: Vec<_> = records.into_iter().filter(|r| r.path != path).map(|r| r.journal).collect();
        let result = restore_unclaimed(&mut state.journal, &others, owner_alive,
            |w| (only.is_none() || only == Some(w.hwnd)) && restore(w, taskbar), |j| atomic_save(&path, j));
        let outstanding: HashSet<_> = state.journal.windows.iter().copied().collect();
        state.hidden.retain(|w| outstanding.contains(w));
        result
    }

    pub fn recover_owner(owner: Owner, taskbar: &ITaskbarList) {
        let Some(_lock) = lock_generation(owner) else { return; };
        if owner_alive(owner) != Some(false) { return; }
        let Ok(roots) = journal_roots() else { return; };
        let Ok(mut records) = scan_roots(&roots) else { return; };
        for index in 0..records.len() {
            if records[index].journal.owner == owner {
                let _ = recover_record(&mut records, index, owner_alive, |w| restore(w, taskbar), atomic_save);
            }
        }
    }

    fn recover_record(records: &mut [Record], index: usize, alive: impl Fn(Owner) -> Option<bool>,
        restore: impl Fn(&Window) -> bool, persist: impl Fn(&Path, &Journal) -> io::Result<()>) -> io::Result<()> {
        let others: Vec<_> = records.iter().enumerate().filter(|(i, _)| *i != index).map(|(_, r)| r.journal.clone()).collect();
        let record = &mut records[index];
        // Keep the durable empty document as acknowledgement, avoiding unchecked deletion failures.
        restore_unclaimed(&mut record.journal, &others, alive, restore, |j| persist(&record.path, j))
    }

    /// The organizer holds the Dock lease and cannot hide until this returns Ok.
    /// The startup best-effort caller is also safe: every restore is serialized with durable claims.
    pub fn recover_previous(taskbar: &ITaskbarList) -> io::Result<()> {
        // Finish any interrupted own restoration first. Failure retains the lease's recovery gate.
        restore_all(taskbar)?;
        let me = process_owner(std::process::id()).ok_or_else(|| io::Error::other("current process identity unavailable"))?;
        let _lock = lock_generation(me).ok_or_else(|| io::Error::other("recovery transaction lock unavailable"))?;
        let mut records = scan_roots(&journal_roots()?)?;
        recover_records(&mut records, owner_alive, |w| restore(w, taskbar), atomic_save)
    }

    fn recover_records(records: &mut [Record], alive: impl Fn(Owner) -> Option<bool>,
        restore: impl Fn(&Window) -> bool, persist: impl Fn(&Path, &Journal) -> io::Result<()>) -> io::Result<()> {
        let mut complete = true;
        for index in 0..records.len() {
            if records[index].journal.windows.is_empty() { continue; }
            match alive(records[index].journal.owner) {
                Some(false) => { if recover_record(records, index, &alive, &restore, &persist).is_err() { complete = false; } }
                _ => complete = false, // a live or inaccessible prior owner is not restoration acknowledgement
            }
        }
        // Old HWND-only files cannot establish process identity. Leave them for the
        // old guard or the explicit legacy restore tool instead of trusting them here.
        if complete { Ok(()) } else { Err(io::Error::other("prior recovery is incomplete")) }
    }

    #[cfg(test)]
    mod file_tests {
        use super::*;
        use std::cell::Cell;

        struct Fixture(PathBuf);
        impl Fixture {
            fn new() -> Self {
                let stamp = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
                let root = std::env::temp_dir().join(format!("dock-lease-fake-{}-{stamp}", std::process::id()));
                fs::create_dir_all(root.join("Public")).unwrap();
                fs::create_dir_all(root.join("Dev")).unwrap();
                Self(root)
            }
            fn roots(&self) -> Vec<PathBuf> { vec![self.0.join("Public"), self.0.join("Dev")] }
            fn write(&self, edition: &str, journal: &Journal) -> PathBuf {
                let path = self.0.join(edition).join(journal_name(journal.owner));
                atomic_save(&path, journal).unwrap();
                path
            }
        }
        impl Drop for Fixture {
            fn drop(&mut self) {
                // Only remove the generated child of this test process's disposable TEMP.
                let root = self.0.canonicalize().unwrap();
                assert_eq!(root.parent(), Some(std::env::temp_dir().canonicalize().unwrap().as_path()));
                assert!(root.file_name().unwrap().to_string_lossy().starts_with("dock-lease-fake-"));
                let _ = fs::remove_dir_all(root);
            }
        }
        fn owner(pid: u32) -> Owner { Owner { pid, started: 1000 + pid as u64 } }
        fn window(hwnd: isize) -> Window { Window { hwnd, process: owner(7) } }

        #[test]
        fn checked_gate_retries_partial_dead_owner_recovery_and_failed_persistence() {
            let fixture = Fixture::new();
            let dead = Journal { windows: vec![window(1), window(2)], ..Journal::new(owner(1)) };
            let path = fixture.write("Public", &dead);
            let mut records = scan_roots(&fixture.roots()).unwrap();
            assert!(recover_records(&mut records, |_| Some(false), |w| w.hwnd == 1, atomic_save).is_err());
            assert_eq!(load(&path, owner(1)).unwrap().windows, vec![window(2)]);
            assert!(recover_records(&mut records, |_| Some(false), |_| true, |_, _| Err(io::Error::other("disk full"))).is_err());
            assert_eq!(records[0].journal.windows, vec![window(2)]);
            assert_eq!(load(&path, owner(1)).unwrap().windows, vec![window(2)]);
            // Restart from disk, rather than relying on an in-memory success flag.
            let mut records = scan_roots(&fixture.roots()).unwrap();
            recover_records(&mut records, |_| Some(false), |_| true, atomic_save).unwrap();
            assert!(load(&path, owner(1)).unwrap().windows.is_empty());
        }

        #[test]
        fn delayed_guard_observes_both_edition_claims_without_adding_a_tab() {
            for (old, new) in [("Public", "Dev"), ("Dev", "Public")] {
                let fixture = Fixture::new();
                let previous = Journal { windows: vec![window(42)], ..Journal::new(owner(1)) };
                fixture.write(old, &previous);
                let current = Journal { owner: owner(2), ..previous.clone() };
                fixture.write(new, &current);
                let mut records = scan_roots(&fixture.roots()).unwrap();
                let index = records.iter().position(|r| r.journal.owner == owner(1)).unwrap();
                recover_record(&mut records, index, |o| Some(o == owner(2)),
                    |_| panic!("a delayed guard must not re-add the new edition's window"), atomic_save).unwrap();
                assert!(records[index].journal.windows.is_empty());
                assert_eq!(load(&fixture.0.join(new).join(journal_name(owner(2))), owner(2)).unwrap().windows, vec![window(42)]);
            }
        }

        #[test]
        fn malformed_unknown_and_unreadable_roots_block_checked_recovery() {
            let fixture = Fixture::new();
            let path = fixture.0.join("Dev").join(journal_name(owner(1)));
            for bytes in [b"{".as_slice(), br#"{"version":2,"owner":{"pid":1,"started":1001},"windows":[]}"#,
                br#"{"version":1,"owner":{"pid":1,"started":1001},"windows":[],"unknown_claims":[]}"#,
                br#"{"version":1,"owner":{"pid":2,"started":1002},"windows":[]}"#] {
                fs::write(&path, bytes).unwrap();
                assert!(scan_roots(&fixture.roots()).is_err());
            }
            fs::remove_file(&path).unwrap();
            for name in ["dock-recovery-v2-1-1001.json", "dock-recovery-v1-x.json", "dock-recovery-v1-1-1001.99.tmp"] {
                let unknown = fixture.0.join("Public").join(name);
                fs::write(&unknown, b"{}").unwrap();
                assert!(scan_roots(&fixture.roots()).is_err());
                fs::remove_file(unknown).unwrap();
            }
            let file_root = fixture.0.join("not-a-directory");
            fs::write(&file_root, b"{}").unwrap();
            assert!(scan_roots(&[file_root]).is_err());
            assert!(scan_roots(&[fixture.0.join("absent-edition")]).unwrap().is_empty());
        }

        #[test]
        fn inaccessible_or_live_previous_owner_cannot_acknowledge_takeover() {
            let fixture = Fixture::new();
            fixture.write("Dev", &Journal { windows: vec![window(42)], ..Journal::new(owner(1)) });
            for status in [None, Some(true)] {
                let mut records = scan_roots(&fixture.roots()).unwrap();
                assert!(recover_records(&mut records, |_| status,
                    |_| panic!("unproven death must not restore"), |_, _| panic!("must preserve journal")).is_err());
                assert_eq!(records[0].journal.windows, vec![window(42)]);
            }
        }

        #[test]
        fn cross_edition_unknown_claim_preserves_old_intent_and_window_identity() {
            let mut previous = Journal { windows: vec![window(42)], ..Journal::new(owner(1)) };
            let other = Journal { owner: owner(2), ..previous.clone() };
            assert!(restore_unclaimed(&mut previous, &[other], |_| None,
                |_| panic!("unknown claim cannot authorize AddTab"), |_| panic!("no change")).is_err());
            let recycled = Window { process: owner(8), ..window(42) };
            let other = Journal { windows: vec![recycled], ..Journal::new(owner(2)) };
            let restored = Cell::new(false);
            restore_unclaimed(&mut previous, &[other], |_| Some(true), |_| { restored.set(true); true }, |_| Ok(())).unwrap();
            assert!(restored.get(), "a different PID/creation time is not a claim to this window");
        }
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
            let name = format!("Local\\UltraSuite.DockJournal.Test.{}", std::process::id());
            let _lock = lock_named(&name).unwrap();
            assert!(std::thread::spawn(move || lock_named(&name).is_none()).join().unwrap());
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
        assert!(journal.restore_with(|w| w.hwnd == 1, |_| Ok(())).is_err());
        assert_eq!(journal.windows, vec![window(2)]);
    }
    #[test]
    fn checked_restoration_cannot_acknowledge_a_failed_add_tab() {
        let mut journal = Journal { version: 1, owner: owner(1), windows: vec![window(1)] };
        assert!(journal.restore_with(|_| false, |_| Ok(())).is_err(), "a retained intent must block lease release");
    }
    #[test]
    fn checked_restoration_cannot_acknowledge_partial_dead_owner_recovery() {
        let mut journal = Journal { version: 1, owner: owner(1), windows: vec![window(1), window(2)] };
        assert!(journal.restore_with(|w| w.hwnd == 1, |_| Ok(())).is_err(), "partial restoration must block takeover");
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
        assert!(journal.restore_with(|_| false, |_| panic!("no changed state to persist")).is_err());
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
