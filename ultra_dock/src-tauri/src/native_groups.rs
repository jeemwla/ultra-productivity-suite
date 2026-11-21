//! Optional "native folder buttons" mode (organizer.json `native_folder_buttons`, off by default).
//!
//! The default mode hides grouped apps with `ITaskbarList::DeleteTab`. On Windows 10 and 11 that also
//! removes the window from Alt+Tab and Task View, which is why the Dock has to give every button back
//! while a switcher is open. This mode never calls DeleteTab. It gives every window of one Dock folder
//! the same window-level AppUserModelID, so the taskbar combines the folder into ONE native button
//! (folder icon and name, hover thumbnails, click to cycle). The windows stay ordinary top-level
//! windows, so Alt+Tab and Task View keep listing each of them and the Dock never restores anything
//! when a switcher opens.
//!
//! Measured on Windows 11 26200 (scratch probe, 2026-11-20): a property written from another process
//! regroups the target's taskbar button at once, survives the writer exiting, keeps all windows in
//! Alt+Tab, and clearing it puts the window back in its own group.
//! https://learn.microsoft.com/windows/win32/shell/appids
//! https://learn.microsoft.com/windows/win32/api/shellapi/nf-shellapi-shgetpropertystoreforwindow
//! https://learn.microsoft.com/windows/win32/properties/props-system-appusermodel-relaunchcommand
//!
//! Safety contract, mirroring the DeleteTab journal in recovery.rs:
//! - The window's own identity (ID, relaunch command, icon, name, PreventPinning) is written to a
//!   per-process journal BEFORE the first property write. Nothing is written to disk elsewhere and
//!   no other app's files, shortcuts or settings change.
//! - Release puts the recorded identity back only while the HWND still belongs to the same process
//!   (PID plus creation time). Closed windows need nothing: the property dies with the window.
//! - The property outlives the Dock process and explorer restarts, so a crash leaves folders combined
//!   until the next Dock start, which restores every journal whose owner is gone.
//! - An app that changes its own ID while grouped is respected: its new identity becomes the one to
//!   restore, then the folder identity is applied again.
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use std::io;

pub const MAX_WINDOWS: usize = 4096;
/// AppUserModelIDs are limited to 128 characters: https://learn.microsoft.com/windows/win32/shell/appids
const MAX_APP_ID: usize = 128;
const FOLDER_MARK: &str = ".DockFolder.";

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Identity {
    pub app_id: Option<String>,
    pub relaunch_command: Option<String>,
    pub relaunch_icon: Option<String>,
    pub relaunch_name: Option<String>,
    pub prevent_pinning: Option<bool>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Entry { pub hwnd: isize, pub pid: u32, pub started: u64, pub folder: String, pub original: Identity }

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Journal { pub version: u32, pub pid: u32, pub started: u64, pub windows: Vec<Entry> }

impl Journal {
    pub fn new(pid: u32, started: u64) -> Self { Self { version: 1, pid, started, windows: vec![] } }

    pub fn decode(bytes: &[u8], pid: u32, started: u64) -> io::Result<Self> {
        let value: Self = serde_json::from_slice(bytes).map_err(io::Error::other)?;
        let unique: HashSet<_> = value.windows.iter().map(|w| w.hwnd).collect();
        if value.version != 1 || value.pid != pid || value.started != started || pid == 0 || started == 0
            || value.windows.len() > MAX_WINDOWS || unique.len() != value.windows.len()
            || value.windows.iter().any(|w| w.hwnd == 0 || w.pid == 0 || w.started == 0) {
            return Err(io::Error::new(io::ErrorKind::InvalidData, "unsupported regroup journal or wrong owner"));
        }
        Ok(value)
    }

    pub fn entry(&self, hwnd: isize) -> Option<&Entry> { self.windows.iter().find(|w| w.hwnd == hwnd) }

    /// Durable intent first: the caller persists the returned journal before touching the window.
    pub fn record(&self, entry: Entry) -> Option<Self> {
        if self.windows.len() >= MAX_WINDOWS && self.entry(entry.hwnd).is_none() { return None; }
        let mut next = self.clone();
        next.windows.retain(|w| w.hwnd != entry.hwnd);
        next.windows.push(entry);
        Some(next)
    }

    /// Drops entries the callback restored (or found closed). Failures stay for a later retry.
    pub fn release_with(&mut self, only: Option<isize>, mut restore: impl FnMut(&Entry) -> bool) -> bool {
        let before = self.windows.len();
        self.windows.retain(|w| !(only.is_none_or(|h| h == w.hwnd) && restore(w)));
        before != self.windows.len()
    }
}

/// FNV-1a keeps distinct folder names (including non-ASCII ones) apart after the ASCII slug.
fn fnv1a(text: &str) -> u32 {
    text.bytes().fold(0x811c9dc5u32, |h, b| (h ^ b as u32).wrapping_mul(0x0100_0193))
}

/// Window-level ID for one Dock folder, such as UltraSuite.DockFolder.Tools1a2b3c4d.
/// Sections stay alphanumeric and the whole ID has no spaces and fits 128 characters.
pub fn folder_app_id(prefix: &str, folder: &str) -> String {
    let slug: String = folder.chars().filter(|c| c.is_ascii_alphanumeric()).take(48).collect();
    let id = format!("{prefix}{FOLDER_MARK}{}{:08x}", if slug.is_empty() { "Folder" } else { &slug }, fnv1a(folder));
    id.chars().take(MAX_APP_ID).collect()
}

/// True for an ID this mode (either edition) wrote. Such a value is never recorded as an app's own.
pub fn is_folder_app_id(id: Option<&str>) -> bool { id.is_some_and(|id| id.contains(FOLDER_MARK)) }

/// Identity for a folder button. PreventPinning keeps the folder from being pinned, so the
/// relaunch command is only a requirement of the relaunch properties, never a shortcut.
pub fn folder_identity(prefix: &str, folder: &str, dock_exe: &str, system_root: &str) -> Identity {
    Identity {
        app_id: Some(folder_app_id(prefix, folder)),
        relaunch_command: Some(format!("\"{dock_exe}\"")),
        relaunch_icon: Some(format!("{system_root}\\System32\\shell32.dll,-4")),
        relaunch_name: Some(folder.chars().take(MAX_APP_ID).collect()),
        prevent_pinning: Some(true),
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum Step {
    /// The window already carries the wanted folder identity.
    Keep,
    /// Record `original` durably, then write the folder identity.
    Apply { original: Identity },
}

/// What one pass does with a window that belongs in `wanted`'s folder.
/// `recorded` is the journal's identity for the same HWND and process, if any.
pub fn reconcile(current: &Identity, wanted: &Identity, recorded: Option<&Identity>) -> Step {
    if current == wanted { return Step::Keep; }
    let original = if is_folder_app_id(current.app_id.as_deref()) {
        // Moving between folders, or a value left behind with no journal: keep the known original,
        // otherwise fall back to "no explicit identity", which is what most desktop apps have.
        recorded.cloned().unwrap_or_default()
    } else {
        // The app's own identity, including one it changed since the last pass.
        current.clone()
    };
    Step::Apply { original }
}

/// The mode runs only while the option is on, this Dock holds the taskbar lease and grouping is on.
/// It needs no strip-health gate: a combined button never takes a window off the taskbar or Alt+Tab.
pub fn engaged(option: bool, may_organize: bool, organize: bool) -> bool { option && may_organize && organize }

/// HWNDs this process grouped that the current pass no longer wants grouped.
pub fn to_release(journal: &Journal, wanted: &HashMap<isize, String>) -> Vec<isize> {
    journal.windows.iter().filter(|w| !wanted.contains_key(&w.hwnd)).map(|w| w.hwnd).collect()
}

pub fn journal_name(pid: u32, started: u64) -> String { format!("dock-regroup-v1-{pid}-{started}.json") }

pub fn parse_journal_name(name: &str) -> Option<(u32, u64)> {
    let (pid, started) = name.strip_prefix("dock-regroup-v1-")?.strip_suffix(".json")?.split_once('-')?;
    let parsed = (pid.parse().ok()?, started.parse().ok()?);
    (journal_name(parsed.0, parsed.1) == name).then_some(parsed)
}

#[cfg(windows)]
mod win {
    use super::*;
    use std::fs::{self, OpenOptions};
    use std::io::{Read, Write};
    use std::path::{Path, PathBuf};
    use std::sync::{Mutex, OnceLock};
    use windows::core::{GUID, PWSTR};
    use windows::Win32::Foundation::{HWND, PROPERTYKEY, VARIANT_FALSE, VARIANT_TRUE};
    use windows::Win32::System::Com::CoTaskMemAlloc;
    use windows::Win32::System::Com::StructuredStorage::{PropVariantClear, PropVariantToBoolean, PROPVARIANT};
    use windows::Win32::System::Variant::{VT_BOOL, VT_EMPTY, VT_LPWSTR};
    use windows::Win32::UI::Shell::PropertiesSystem::{IPropertyStore, SHGetPropertyStoreForWindow};
    use windows::Win32::UI::WindowsAndMessaging::{GetWindowThreadProcessId, IsWindow};

    // System.AppUserModel.* share one format ID. https://learn.microsoft.com/windows/win32/properties/props-system-appusermodel-id
    const FMTID: GUID = GUID::from_u128(0x9F4C2855_9F79_4B39_A8D0_E1D42DE1D5F3);
    const RELAUNCH_COMMAND: u32 = 2;
    const RELAUNCH_ICON: u32 = 3;
    const RELAUNCH_NAME: u32 = 4;
    const APP_ID: u32 = 5;
    const PREVENT_PINNING: u32 = 9;
    fn key(pid: u32) -> PROPERTYKEY { PROPERTYKEY { fmtid: FMTID, pid } }

    fn store(hwnd: isize) -> Option<IPropertyStore> {
        unsafe { SHGetPropertyStoreForWindow::<IPropertyStore>(HWND(hwnd as _)).ok() }
    }

    fn get_string(store: &IPropertyStore, pid: u32) -> Option<Option<String>> {
        let mut value = unsafe { store.GetValue(&key(pid)).ok()? };
        let vt = unsafe { value.Anonymous.Anonymous.vt };
        let text = if vt == VT_LPWSTR { Some(unsafe { value.Anonymous.Anonymous.Anonymous.pwszVal.to_string().ok()? }) }
            else if vt == VT_EMPTY { None } else { unsafe { let _ = PropVariantClear(&mut value); } return None };
        unsafe { let _ = PropVariantClear(&mut value); }
        Some(text)
    }

    /// The window store hands PreventPinning back as a string (measured: VT_LPWSTR after a VT_BOOL
    /// write), so PropVariantToBoolean does the documented conversion for every stored form.
    /// https://learn.microsoft.com/windows/win32/api/propvarutil/nf-propvarutil-propvarianttoboolean
    fn get_bool(store: &IPropertyStore, pid: u32) -> Option<Option<bool>> {
        let mut value = unsafe { store.GetValue(&key(pid)).ok()? };
        let vt = unsafe { value.Anonymous.Anonymous.vt };
        let flag = if vt == VT_EMPTY { Ok(None) } else { unsafe { PropVariantToBoolean(&value) }.map(|b| Some(b.as_bool())) };
        unsafe { let _ = PropVariantClear(&mut value); }
        flag.ok()
    }

    /// None when the window cannot be read (closed, or a higher-integrity process).
    pub fn read(hwnd: isize) -> Option<Identity> {
        let s = store(hwnd)?;
        Some(Identity {
            app_id: get_string(&s, APP_ID)?, relaunch_command: get_string(&s, RELAUNCH_COMMAND)?,
            relaunch_icon: get_string(&s, RELAUNCH_ICON)?, relaunch_name: get_string(&s, RELAUNCH_NAME)?,
            prevent_pinning: get_bool(&s, PREVENT_PINNING)?,
        })
    }

    fn set_string(store: &IPropertyStore, pid: u32, text: Option<&str>) -> bool {
        let wide: Vec<u16> = text.unwrap_or_default().encode_utf16().chain(Some(0)).collect();
        let mut value = PROPVARIANT::default();
        unsafe {
            if text.is_some() {
                // Same ownership as InitPropVariantFromString: COM task memory, released by PropVariantClear.
                let bytes = wide.len() * 2;
                let copy = CoTaskMemAlloc(bytes) as *mut u16;
                if copy.is_null() { return false; }
                std::ptr::copy_nonoverlapping(wide.as_ptr(), copy, wide.len());
                let inner = &mut value.Anonymous.Anonymous;
                inner.vt = VT_LPWSTR; inner.Anonymous.pwszVal = PWSTR(copy);
            }
            let ok = store.SetValue(&key(pid), &value).is_ok();
            let _ = PropVariantClear(&mut value);
            ok
        }
    }

    fn set_bool(store: &IPropertyStore, pid: u32, flag: Option<bool>) -> bool {
        let mut value = PROPVARIANT::default();
        unsafe {
            let inner = &mut value.Anonymous.Anonymous;
            if let Some(flag) = flag { inner.vt = VT_BOOL; inner.Anonymous.boolVal = if flag { VARIANT_TRUE } else { VARIANT_FALSE }; }
            store.SetValue(&key(pid), &value).is_ok()
        }
    }

    /// The taskbar refreshes when the ID changes, so the relaunch properties go first and the ID last.
    pub fn write(hwnd: isize, identity: &Identity) -> bool {
        let Some(s) = store(hwnd) else { return false; };
        let ok = set_string(&s, RELAUNCH_COMMAND, identity.relaunch_command.as_deref())
            & set_string(&s, RELAUNCH_ICON, identity.relaunch_icon.as_deref())
            & set_string(&s, RELAUNCH_NAME, identity.relaunch_name.as_deref())
            & set_bool(&s, PREVENT_PINNING, identity.prevent_pinning)
            & set_string(&s, APP_ID, identity.app_id.as_deref());
        ok && unsafe { s.Commit().is_ok() }
    }

    /// Some(true) while the HWND still belongs to the recorded process.
    fn same_process(entry: &Entry) -> Option<bool> {
        let hwnd = HWND(entry.hwnd as _);
        if !unsafe { IsWindow(Some(hwnd)).as_bool() } { return Some(false); }
        let mut pid = 0;
        unsafe { GetWindowThreadProcessId(hwnd, Some(&mut pid)); }
        if pid == 0 { return None; }
        if pid != entry.pid { return Some(false); }
        let owner = crate::recovery::process_owner(pid)?;
        Some(owner.started == entry.started)
    }

    /// True when nothing is left to restore for this entry.
    fn restore(entry: &Entry) -> bool {
        match same_process(entry) {
            Some(false) => true, // closed or recycled: the property went with the window
            None => false,       // identity unknown: retry later, never touch a stranger's window
            Some(true) => {
                // Only undo what this mode wrote; an app that took a new ID of its own keeps it.
                match read(entry.hwnd) {
                    Some(now) if !is_folder_app_id(now.app_id.as_deref()) => true,
                    Some(_) => write(entry.hwnd, &entry.original),
                    None => false,
                }
            }
        }
    }

    fn atomic_save(path: &Path, journal: &Journal) -> io::Result<()> {
        if let Some(parent) = path.parent() { fs::create_dir_all(parent)?; }
        let temp = path.with_extension(format!("{}.tmp", std::process::id()));
        let bytes = serde_json::to_vec(journal).map_err(io::Error::other)?;
        let result = (|| {
            let mut file = OpenOptions::new().create(true).truncate(true).write(true).open(&temp)?;
            file.write_all(&bytes)?; file.sync_all()?; drop(file);
            fs::rename(&temp, path)
        })();
        if result.is_err() { let _ = fs::remove_file(&temp); }
        result
    }

    fn load(path: &Path, pid: u32, started: u64) -> io::Result<Journal> {
        let mut bytes = vec![];
        fs::File::open(path)?.take(1_048_577).read_to_end(&mut bytes)?;
        if bytes.len() > 1_048_576 { return Err(io::Error::new(io::ErrorKind::InvalidData, "regroup journal too large")); }
        Journal::decode(&bytes, pid, started)
    }

    struct State { journal: Journal, path: PathBuf }
    static CURRENT: OnceLock<Option<Mutex<State>>> = OnceLock::new();
    fn state() -> Option<&'static Mutex<State>> {
        CURRENT.get_or_init(|| {
            let me = crate::recovery::process_owner(std::process::id())?;
            Some(Mutex::new(State { journal: Journal::new(me.pid, me.started), path: crate::appdata(&journal_name(me.pid, me.started)) }))
        }).as_ref()
    }

    /// Puts `hwnd` into `folder`'s native button. False leaves the window as it was (or as it
    /// partly is, with its original identity still journaled for release).
    pub fn apply(hwnd: isize, folder: &str, wanted: &Identity) -> bool {
        let Some(mut state) = state().and_then(|s| s.lock().ok()) else { return false; };
        let mut pid = 0;
        unsafe { GetWindowThreadProcessId(HWND(hwnd as _), Some(&mut pid)); }
        let Some(owner) = (pid != 0).then(|| crate::recovery::process_owner(pid)).flatten() else { return false; };
        let Some(current) = read(hwnd) else { return false; };
        let recorded = state.journal.entry(hwnd).filter(|e| e.pid == owner.pid && e.started == owner.started).map(|e| e.original.clone());
        match reconcile(&current, wanted, recorded.as_ref()) {
            Step::Keep => true,
            Step::Apply { original } => {
                let entry = Entry { hwnd, pid: owner.pid, started: owner.started, folder: folder.to_string(), original };
                let Some(next) = state.journal.record(entry) else { return false; };
                if next != state.journal {
                    if atomic_save(&state.path, &next).is_err() { return false; }
                    state.journal = next;
                }
                write(hwnd, wanted)
            }
        }
    }

    fn release_current(only: Option<isize>) -> io::Result<()> {
        let mut state = state().and_then(|s| s.lock().ok()).ok_or_else(|| io::Error::other("regroup state unavailable"))?;
        let before = state.journal.clone();
        state.journal.release_with(only, restore);
        if state.journal != before {
            if let Err(e) = atomic_save(&state.path, &state.journal) { state.journal = before; return Err(e); }
        }
        let left = state.journal.windows.iter().any(|w| only.is_none_or(|h| h == w.hwnd));
        if left { Err(io::Error::other("regroup release is incomplete")) } else { Ok(()) }
    }

    pub fn release(hwnd: isize) { let _ = release_current(Some(hwnd)); }
    pub fn release_all() -> io::Result<()> { release_current(None) }

    /// Releases every window this process grouped that `wanted` no longer lists.
    pub fn release_unwanted(wanted: &HashMap<isize, String>) {
        let unwanted = match state().and_then(|s| s.lock().ok()) { Some(s) => to_release(&s.journal, wanted), None => return };
        for hwnd in unwanted { release(hwnd); }
    }

    pub fn grouped_count() -> usize { state().and_then(|s| s.lock().ok()).map(|s| s.journal.windows.len()).unwrap_or(0) }

    /// Startup and lease recovery: restore the windows of every Dock process that is gone.
    pub fn recover_previous() -> io::Result<()> {
        let mut complete = true;
        for root in crate::recovery::journal_roots()? {
            let files = match fs::read_dir(&root) {
                Ok(files) => files,
                Err(e) if e.kind() == io::ErrorKind::NotFound => continue,
                Err(e) => return Err(e),
            };
            for entry in files.take(10_000) {
                let entry = entry?;
                let name = entry.file_name().to_string_lossy().to_string();
                let Some((pid, started)) = parse_journal_name(&name) else { continue };
                if pid == std::process::id() { continue; }
                if crate::recovery::owner_alive(crate::recovery::Owner { pid, started }) != Some(false) { complete = false; continue; }
                let path = entry.path();
                let Ok(mut journal) = load(&path, pid, started) else { complete = false; continue };
                if journal.windows.is_empty() { let _ = fs::remove_file(&path); continue; }
                journal.release_with(None, restore);
                if journal.windows.is_empty() { let _ = fs::remove_file(&path); }
                else { complete = false; let _ = atomic_save(&path, &journal); }
            }
        }
        if complete { Ok(()) } else { Err(io::Error::other("earlier folder buttons are not fully restored")) }
    }
}

#[cfg(windows)]
pub use win::{apply, grouped_count, read, recover_previous, release_all, release_unwanted, write};

#[cfg(test)]
mod tests {
    use super::*;

    fn own(id: &str) -> Identity { Identity { app_id: Some(id.into()), ..Default::default() } }

    #[test]
    fn folder_ids_are_valid_distinct_and_recognisable() {
        let tools = folder_app_id("UltraSuite", "Tools");
        assert!(tools.starts_with("UltraSuite.DockFolder.Tools"), "{tools}");
        assert!(!tools.contains(' ') && tools.len() <= MAX_APP_ID);
        assert_ne!(folder_app_id("UltraSuite", "게임"), folder_app_id("UltraSuite", "채팅"), "non-ASCII names stay apart");
        assert_ne!(folder_app_id("UltraSuite", "My Games"), folder_app_id("UltraSuite", "MyGames"));
        assert_ne!(folder_app_id("UltraSuite", "Tools"), folder_app_id("UltraSuite.Dev", "Tools"), "editions never share a button");
        assert!(folder_app_id("UltraSuite", &"x".repeat(400)).len() <= MAX_APP_ID);
        assert!(is_folder_app_id(Some(&tools)) && is_folder_app_id(Some(&folder_app_id("UltraSuite.Dev", "AI"))));
        assert!(!is_folder_app_id(Some("Chrome.UserData.Default")) && !is_folder_app_id(None));
    }

    #[test]
    fn the_mode_is_off_unless_the_option_the_lease_and_grouping_all_allow_it() {
        assert!(engaged(true, true, true));
        for (option, lease, organize) in [(false, true, true), (true, false, true), (true, true, false), (false, false, false)] {
            assert!(!engaged(option, lease, organize), "{option} {lease} {organize}");
        }
    }

    #[test]
    fn folder_identity_cannot_be_pinned_and_names_the_folder() {
        let id = folder_identity("UltraSuite", "Chat", "C:\\Apps\\Ultra Suite.exe", "C:\\Windows");
        assert_eq!(id.prevent_pinning, Some(true));
        assert_eq!(id.relaunch_name.as_deref(), Some("Chat"));
        assert_eq!(id.relaunch_command.as_deref(), Some("\"C:\\Apps\\Ultra Suite.exe\""));
        assert_eq!(id.relaunch_icon.as_deref(), Some("C:\\Windows\\System32\\shell32.dll,-4"));
    }

    #[test]
    fn reconcile_records_the_apps_own_identity_once() {
        let wanted = folder_identity("UltraSuite", "Web", "d.exe", "C:\\Windows");
        // A plain desktop app with no explicit ID.
        assert_eq!(reconcile(&Identity::default(), &wanted, None), Step::Apply { original: Identity::default() });
        // An app with its own ID (Chrome profiles set one per window).
        let chrome = own("Chrome.UserData.Profile1");
        assert_eq!(reconcile(&chrome, &wanted, None), Step::Apply { original: chrome.clone() });
        // Already grouped: nothing to do.
        assert_eq!(reconcile(&wanted, &wanted, Some(&chrome)), Step::Keep);
    }

    #[test]
    fn reconcile_never_records_a_folder_id_as_the_original() {
        let web = folder_identity("UltraSuite", "Web", "d.exe", "C:\\Windows");
        let ai = folder_identity("UltraSuite", "AI", "d.exe", "C:\\Windows");
        let chrome = own("Chrome.UserData.Profile1");
        assert_eq!(reconcile(&web, &ai, Some(&chrome)), Step::Apply { original: chrome }, "moving folders keeps the original");
        assert_eq!(reconcile(&web, &ai, None), Step::Apply { original: Identity::default() }, "orphaned folder id falls back to none");
    }

    #[test]
    fn an_app_that_changes_its_own_id_keeps_the_new_one_on_release() {
        let wanted = folder_identity("UltraSuite", "Web", "d.exe", "C:\\Windows");
        let renamed = own("Chrome.UserData.Profile2");
        assert_eq!(reconcile(&renamed, &wanted, Some(&own("Chrome.UserData.Profile1"))), Step::Apply { original: renamed });
    }

    #[test]
    fn journal_records_write_ahead_and_releases_only_what_was_restored() {
        let entry = |hwnd| Entry { hwnd, pid: 7, started: 9, folder: "Web".into(), original: Identity::default() };
        let journal = Journal::new(1, 2).record(entry(10)).unwrap().record(entry(11)).unwrap().record(entry(10)).unwrap();
        assert_eq!(journal.windows.iter().map(|w| w.hwnd).collect::<Vec<_>>(), vec![11, 10], "re-recording replaces, never duplicates");
        let mut partial = journal.clone();
        assert!(partial.release_with(None, |w| w.hwnd == 10));
        assert_eq!(partial.windows.len(), 1, "a failed restore stays journaled");
        let mut only = journal.clone();
        only.release_with(Some(11), |_| true);
        assert_eq!(only.windows.iter().map(|w| w.hwnd).collect::<Vec<_>>(), vec![10]);
        let wanted: HashMap<isize, String> = [(10, "Web".to_string())].into();
        assert_eq!(to_release(&journal, &wanted), vec![11]);
    }

    #[test]
    fn journal_capacity_is_bounded() {
        let mut journal = Journal::new(1, 2);
        journal.windows = (1..=MAX_WINDOWS as isize).map(|hwnd| Entry { hwnd, pid: 1, started: 1, folder: "F".into(), original: Identity::default() }).collect();
        assert!(journal.record(Entry { hwnd: 999_999, pid: 1, started: 1, folder: "F".into(), original: Identity::default() }).is_none());
        assert!(journal.record(Entry { hwnd: 5, pid: 1, started: 1, folder: "G".into(), original: Identity::default() }).is_some());
    }

    #[test]
    fn journal_decode_rejects_foreign_or_malformed_documents() {
        let good = Journal::new(4, 5).record(Entry { hwnd: 3, pid: 6, started: 7, folder: "Web".into(), original: own("A.B") }).unwrap();
        let bytes = serde_json::to_vec(&good).unwrap();
        assert_eq!(Journal::decode(&bytes, 4, 5).unwrap(), good);
        assert!(Journal::decode(&bytes, 4, 6).is_err(), "another owner's journal");
        assert!(Journal::decode(b"{\"version\":2,\"pid\":4,\"started\":5,\"windows\":[]}", 4, 5).is_err());
        assert!(Journal::decode(b"{\"version\":1,\"pid\":4,\"started\":5,\"windows\":[],\"extra\":1}", 4, 5).is_err());
        let mut dup = good.clone();
        dup.windows.push(dup.windows[0].clone());
        assert!(Journal::decode(&serde_json::to_vec(&dup).unwrap(), 4, 5).is_err());
    }

    #[test]
    fn journal_names_round_trip_and_never_match_the_deletetab_journal() {
        assert_eq!(parse_journal_name(&journal_name(12, 34)), Some((12, 34)));
        for name in ["dock-recovery-v1-12-34.json", "dock-regroup-v1-12-34.json.tmp", "dock-regroup-v1-012-34.json", "dock-regroup-v1-x-1.json"] {
            assert_eq!(parse_journal_name(name), None, "{name}");
        }
        assert!(!journal_name(1, 2).starts_with("dock-recovery-"), "recovery.rs treats unknown dock-recovery-* files as blocking");
    }
}

/// Live check against real windows. It opens no window of its own and touches nothing unless
/// ULTRA_DOCK_NATIVE_GROUPS_HWND names a window of a throwaway probe process.
#[cfg(all(test, windows))]
mod native_tests {
    use super::*;

    #[test]
    fn regroup_and_restore_a_probe_window() {
        let Some(hwnd) = std::env::var("ULTRA_DOCK_NATIVE_GROUPS_HWND").ok().and_then(|v| v.parse::<isize>().ok()) else { return };
        let before = read(hwnd).expect("probe identity is readable");
        let wanted = folder_identity("UltraSuite.Test", "Probe", "C:\\probe.exe", "C:\\Windows");
        assert!(write(hwnd, &wanted));
        assert_eq!(read(hwnd).unwrap(), wanted);
        assert!(write(hwnd, &before));
        assert_eq!(read(hwnd).unwrap(), before);
    }
}
