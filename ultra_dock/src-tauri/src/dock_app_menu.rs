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
}

/// Resolve a right-click against the snapshot. An app click names its exe (and the window
/// it shows); a folder click names its category key. Unknown names resolve to nothing.
pub fn resolve(snapshot: &[Window], exe: Option<&str>, hwnd: Option<isize>, folder: Option<&str>, folder_exes: &[String]) -> Option<Target> {
    if let Some(exe) = exe {
        let windows: Vec<_> = snapshot.iter().filter(|w| w.exe.eq_ignore_ascii_case(exe)).collect();
        let first = windows.first()?;
        let window = hwnd.filter(|h| windows.iter().any(|w| w.hwnd == *h));
        return Some(Target { exe: Some(first.exe.clone()), name: first.name.clone(), folder: None, window,
            windows: windows.iter().map(|w| w.hwnd).collect() });
    }
    let folder = folder?;
    let windows: Vec<_> = snapshot.iter()
        .filter(|w| folder_exes.iter().any(|e| e.eq_ignore_ascii_case(&w.exe)) || (folder_exes.is_empty() && w.category == folder))
        .map(|w| w.hwnd).collect();
    if windows.is_empty() { return None; }
    Some(Target { exe: None, name: folder.to_string(), folder: Some(folder.to_string()), window: None, windows })
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
}
