//! Right-click menu for apps and folders in the Dock strip, without Windows calls.
//! Mirrors the native taskbar's app menu: open, new window, pin state, close.
//! The page only names what was clicked; every window acted on comes from the
//! organizer's own snapshot, so a page can never close an unrelated window.

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Action { Open, NewWindow, UnpinFromDock, CloseWindow, CloseAll, EditFolders, DockSettings }

impl Action {
    pub const ALL: [Action; 7] = [Action::Open, Action::NewWindow, Action::UnpinFromDock, Action::CloseWindow,
        Action::CloseAll, Action::EditFolders, Action::DockSettings];
    pub fn id(self) -> &'static str {
        match self {
            Action::Open => "dockapp_open", Action::NewWindow => "dockapp_new", Action::UnpinFromDock => "dockapp_unpin",
            Action::CloseWindow => "dockapp_close", Action::CloseAll => "dockapp_closeall",
            Action::EditFolders => "dockapp_edit", Action::DockSettings => "dockapp_settings",
        }
    }
    pub fn from_id(id: &str) -> Option<Action> { Action::ALL.into_iter().find(|a| a.id() == id) }
}

/// One row of the strip's window snapshot.
#[derive(Clone, Debug, PartialEq)]
pub struct Window { pub hwnd: isize, pub exe: String, pub name: String, pub category: String }

/// What a right-click resolved to. `window` is the single window the click named, if any.
#[derive(Clone, Debug, PartialEq)]
pub struct Target {
    pub exe: Option<String>,
    pub name: String,
    pub folder: Option<String>,
    pub window: Option<isize>,
    pub windows: Vec<isize>,
    /// Owner of each window when the menu opened; filled by the caller, checked before acting.
    pub owners: Vec<(isize, Identity)>,
}

/// Resolve a right-click against the snapshot. An app click names its exe (and the window
/// it shows); a folder click names its category key. Unknown names resolve to nothing.
pub fn resolve(snapshot: &[Window], exe: Option<&str>, hwnd: Option<isize>, folder: Option<&str>, folder_exes: &[String]) -> Option<Target> {
    if let Some(exe) = exe {
        let windows: Vec<_> = snapshot.iter().filter(|w| w.exe.eq_ignore_ascii_case(exe)).collect();
        let first = windows.first()?;
        let window = hwnd.filter(|h| windows.iter().any(|w| w.hwnd == *h));
        return Some(Target { exe: Some(first.exe.clone()), name: first.name.clone(), folder: None, window,
            windows: windows.iter().map(|w| w.hwnd).collect(), owners: vec![] });
    }
    let folder = folder?;
    let windows: Vec<_> = snapshot.iter()
        .filter(|w| folder_exes.iter().any(|e| e.eq_ignore_ascii_case(&w.exe)) || (folder_exes.is_empty() && w.category == folder))
        .map(|w| w.hwnd).collect();
    if windows.is_empty() { return None; }
    Some(Target { exe: None, name: folder.to_string(), folder: Some(folder.to_string()), window: None, windows, owners: vec![] })
}

/// Launching the same executable again opens a new window for ordinary desktop apps.
/// Packaged (Store) apps and the UWP frame host cannot be started from their exe path.
pub fn new_window_supported(exe: &str) -> bool {
    let lower = exe.replace('/', "\\").to_ascii_lowercase();
    let file = lower.rsplit('\\').next().unwrap_or(&lower);
    lower.ends_with(".exe") && lower.contains('\\') && !lower.contains("\\windowsapps\\")
        && !matches!(file, "applicationframehost.exe" | "explorer.exe")
}

/// Menu rows in display order. `None` is a separator.
pub fn items(target: &Target) -> Vec<Option<(Action, String)>> {
    let mut rows = vec![];
    if target.folder.is_some() {
        let n = target.windows.len();
        rows.push(Some((Action::CloseAll, if n == 1 { "Close window".to_string() } else { format!("Close all {n} windows") })));
        rows.push(None);
        rows.push(Some((Action::EditFolders, "Edit folders".to_string())));
        rows.push(Some((Action::DockSettings, "Dock settings".to_string())));
        return rows;
    }
    rows.push(Some((Action::Open, format!("Open {}", target.name))));
    if target.exe.as_deref().is_some_and(new_window_supported) {
        rows.push(Some((Action::NewWindow, "New window".to_string())));
    }
    rows.push(None);
    rows.push(Some((Action::UnpinFromDock, "Unpin from Dock".to_string())));
    rows.push(None);
    if target.window.is_some() || target.windows.len() == 1 {
        rows.push(Some((Action::CloseWindow, "Close window".to_string())));
    }
    if target.windows.len() > 1 {
        rows.push(Some((Action::CloseAll, "Close all windows".to_string())));
    }
    rows
}

/// The windows an action closes or opens. Open prefers the clicked window.
pub fn windows_for(action: Action, target: &Target) -> Vec<isize> {
    match action {
        Action::Open => target.window.or(target.windows.first().copied()).into_iter().collect(),
        Action::CloseWindow => target.window.or_else(|| (target.windows.len() == 1).then(|| target.windows[0])).into_iter().collect(),
        Action::CloseAll => target.windows.clone(),
        _ => vec![],
    }
}

/// "Unpin from Dock" keeps the app as a normal taskbar button: add its keep entry once.
pub fn with_keep(config: &serde_json::Value, entry: &str) -> serde_json::Value {
    let mut next = config.clone();
    if !next.is_object() { next = serde_json::json!({}); }
    let keep = next["keep"].as_array().cloned().unwrap_or_default();
    let mut keep: Vec<serde_json::Value> = keep.into_iter().filter(|v| v.is_string()).collect();
    if !keep.iter().any(|v| v.as_str().is_some_and(|s| s.eq_ignore_ascii_case(entry))) {
        keep.push(serde_json::Value::String(entry.to_string()));
    }
    next["keep"] = serde_json::Value::Array(keep);
    next
}

/// Unpin's read-modify-write. Only a missing settings file starts from the shipped
/// defaults. A file that cannot be read, is not valid JSON, or is not an object aborts
/// the change, so the owner's folders, order and keep list are never replaced.
pub fn unpin_config(existing: std::io::Result<String>, defaults: &str, entry: &str) -> Result<serde_json::Value, String> {
    let text = match existing {
        Ok(text) => text,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => defaults.to_string(),
        Err(error) => return Err(format!("Could not read the Dock settings, nothing was changed: {error}")),
    };
    let config: serde_json::Value = serde_json::from_str(&text)
        .map_err(|error| format!("The Dock settings file is not valid, nothing was changed: {error}"))?;
    if !config.is_object() { return Err("The Dock settings file is not valid, nothing was changed.".to_string()); }
    Ok(with_keep(&config, entry))
}

/// A window's owning process generation: process id and process start time.
pub type Identity = (u32, u64);

/// The handles an action may still touch. Windows recycles window handles, so each one
/// must still belong to the same process generation it had when the menu opened. A
/// handle never captured, or whose owner cannot be read now, is never acted on.
pub fn still_owned(wanted: &[isize], captured: &[(isize, Identity)], current: impl Fn(isize) -> Option<Identity>) -> Vec<isize> {
    wanted.iter().copied()
        .filter(|h| captured.iter().find(|(c, _)| c == h).is_some_and(|(_, id)| current(*h) == Some(*id)))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    fn w(hwnd: isize, exe: &str, cat: &str) -> Window {
        Window { hwnd, exe: exe.into(), name: exe.rsplit('\\').next().unwrap().trim_end_matches(".exe").into(), category: cat.into() }
    }
    fn snapshot() -> Vec<Window> {
        vec![w(1, r"C:\Apps\Chrome\chrome.exe", "Web"), w(2, r"C:\Apps\Chrome\chrome.exe", "Web"),
             w(3, r"C:\Program Files\WindowsApps\Spotify\Spotify.exe", "Media"), w(4, r"C:\Apps\Notes\notes.exe", "Work")]
    }
    fn actions(target: &Target) -> Vec<Action> { items(target).into_iter().flatten().map(|(a, _)| a).collect() }

    #[test]
    fn an_app_with_one_window_gets_the_native_taskbar_menu() {
        let t = resolve(&snapshot(), Some(r"c:\apps\notes\NOTES.exe"), Some(4), None, &[]).unwrap();
        assert_eq!(actions(&t), vec![Action::Open, Action::NewWindow, Action::UnpinFromDock, Action::CloseWindow]);
        assert_eq!(windows_for(Action::CloseWindow, &t), vec![4]);
        assert_eq!(items(&t)[0], Some((Action::Open, "Open notes".into())));
    }
    #[test]
    fn an_app_with_several_windows_offers_close_all() {
        let t = resolve(&snapshot(), Some(r"C:\Apps\Chrome\chrome.exe"), Some(1), None, &[]).unwrap();
        assert_eq!(actions(&t), vec![Action::Open, Action::NewWindow, Action::UnpinFromDock, Action::CloseWindow, Action::CloseAll]);
        assert_eq!(windows_for(Action::CloseAll, &t), vec![1, 2]);
        assert_eq!(windows_for(Action::CloseWindow, &t), vec![1], "the clicked window, never a guess");
        let unnamed = resolve(&snapshot(), Some(r"C:\Apps\Chrome\chrome.exe"), None, None, &[]).unwrap();
        assert!(!actions(&unnamed).contains(&Action::CloseWindow), "several windows and none named: no single close");
        assert!(windows_for(Action::CloseWindow, &unnamed).is_empty());
    }
    #[test]
    fn packaged_apps_do_not_offer_new_window() {
        let t = resolve(&snapshot(), Some(r"C:\Program Files\WindowsApps\Spotify\Spotify.exe"), None, None, &[]).unwrap();
        assert!(!actions(&t).contains(&Action::NewWindow));
        assert!(!new_window_supported(r"C:\Windows\System32\ApplicationFrameHost.exe"));
        assert!(!new_window_supported("chrome"), "a bare name is not a launchable path");
        assert!(new_window_supported(r"D:\Games\Tool\tool.exe"));
    }
    #[test]
    fn a_page_cannot_target_windows_outside_the_snapshot() {
        assert!(resolve(&snapshot(), Some(r"C:\Windows\System32\cmd.exe"), Some(99), None, &[]).is_none());
        let t = resolve(&snapshot(), Some(r"C:\Apps\Notes\notes.exe"), Some(1), None, &[]).unwrap();
        assert_eq!(t.window, None, "a window from another app is ignored");
        assert!(resolve(&snapshot(), None, None, Some("Missing"), &[]).is_none());
        assert!(resolve(&snapshot(), None, None, None, &[]).is_none());
    }
    #[test]
    fn a_folder_menu_closes_only_its_members_and_opens_editing() {
        let exes = vec![r"C:\Apps\Chrome\chrome.exe".to_string(), r"C:\Apps\Notes\notes.exe".to_string()];
        let t = resolve(&snapshot(), None, None, Some("More apps"), &exes).unwrap();
        assert_eq!(actions(&t), vec![Action::CloseAll, Action::EditFolders, Action::DockSettings]);
        assert_eq!(windows_for(Action::CloseAll, &t), vec![1, 2, 4]);
        let by_category = resolve(&snapshot(), None, None, Some("Web"), &[]).unwrap();
        assert_eq!(by_category.windows, vec![1, 2]);
    }
    #[test]
    fn ids_round_trip_and_unknown_ids_are_ignored() {
        for a in Action::ALL { assert_eq!(Action::from_id(a.id()), Some(a)); }
        assert_eq!(Action::from_id("dockpopup_quit"), None);
    }
    #[test]
    fn unpin_adds_one_keep_entry_and_preserves_the_rest() {
        let config = serde_json::json!({"keep":["\\obs64.exe"],"order":["Work"]});
        let next = with_keep(&config, "\\notes.exe");
        assert_eq!(next["keep"], serde_json::json!(["\\obs64.exe","\\notes.exe"]));
        assert_eq!(next["order"], config["order"]);
        assert_eq!(with_keep(&next, "\\NOTES.exe")["keep"], next["keep"], "unpinning twice adds nothing");
        assert_eq!(with_keep(&serde_json::json!(null), "\\a.exe")["keep"], serde_json::json!(["\\a.exe"]));
    }

    /// Codex review 2.2.30 P2: Windows recycles window handles. A handle that now belongs to
    /// another process (or another start of the same app) is never closed or activated.
    #[test]
    fn a_recycled_window_handle_is_never_acted_on() {
        let captured = [(4, (100, 7)), (5, (100, 7))];
        let same = |h: isize| match h { 4 | 5 => Some((100, 7)), _ => None };
        assert_eq!(still_owned(&[4, 5], &captured, same), vec![4, 5]);
        let other_app = |h: isize| if h == 4 { Some((200, 9)) } else { Some((100, 7)) };
        assert_eq!(still_owned(&[4, 5], &captured, other_app), vec![5], "handle 4 now belongs to another process");
        let restarted = |_| Some((100, 8));
        assert!(still_owned(&[4, 5], &captured, restarted).is_empty(), "same PID, new process start");
        assert!(still_owned(&[4], &captured, |_| None).is_empty(), "an unreadable owner is not a match");
        assert!(still_owned(&[6], &captured, |_| Some((100, 7))).is_empty(), "a handle never captured is not acted on");
    }

    /// Codex review 2.2.30 P2: Unpin must never replace a custom file with the defaults.
    #[test]
    fn unpin_never_replaces_settings_it_could_not_read() {
        use std::io::{Error, ErrorKind};
        let defaults = r#"{"keep":[],"order":["Web"]}"#;
        let custom = r#"{"keep":["a.exe"],"order":["Mine"],"categories":{"Mine":["x.exe"]}}"#;
        let next = unpin_config(Ok(custom.to_string()), defaults, "notes.exe").unwrap();
        assert_eq!(next["order"], serde_json::json!(["Mine"]), "unrelated settings survive");
        assert_eq!(next["categories"]["Mine"], serde_json::json!(["x.exe"]));
        assert_eq!(next["keep"], serde_json::json!(["a.exe", "notes.exe"]));
        assert!(unpin_config(Ok("{ broken".into()), defaults, "n.exe").is_err(), "malformed JSON aborts");
        assert!(unpin_config(Ok("[]".into()), defaults, "n.exe").is_err(), "a non-object document aborts");
        assert!(unpin_config(Err(Error::new(ErrorKind::PermissionDenied, "locked")), defaults, "n.exe").is_err(), "a failed read aborts");
        let fresh = unpin_config(Err(Error::new(ErrorKind::NotFound, "absent")), defaults, "n.exe").unwrap();
        assert_eq!(fresh["order"], serde_json::json!(["Web"]), "only a missing file starts from the defaults");
    }
}
