#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

//! Ultra Suite desktop app.
//! - Ultra Dock ("taskbar" window): a taskbar organizer. It works on the apps you have OPEN.
//!   Apps in the keep list stay as normal taskbar buttons; every other open app's taskbar button is
//!   tucked away (ITaskbarList::DeleteTab) and grouped by category in a strip placed right after the
//!   last taskbar icon. Click a group to slide its apps open, click an app to switch to it.
//!   Every hidden button is restored when the organizer is turned off or the app quits.
//! - "alarm" / "sentinel" / "insights": app windows served by the bundled core.
//! CLI: --open hub|alarm|sentinel|insights  --background  --show-dock  --quit  --uninstall-survey  --event <kind>

use base64::Engine;
mod recovery;
mod wake;
mod dock_safety;
mod dock_layout;
mod launch;
mod edition;
use serde::Serialize;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant, SystemTime};
use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindow, WebviewWindowBuilder, WindowEvent};

const DEFAULT_ORG: &str = include_str!("../../config/organizer.json");
const COLLECTOR: &str = env!("ULTRASUITE_COLLECTOR_URL");

static STRIP_W: AtomicU32 = AtomicU32::new(60);
static STRIP_FITS: AtomicBool = AtomicBool::new(false);
static RENDER_OVERFLOW: AtomicBool = AtomicBool::new(false);
/// Measured free taskbar space for the strip, in CSS pixels (u32::MAX until the first measurement).
static AVAIL_CSS: AtomicU32 = AtomicU32::new(u32::MAX);
/// Space the folder plan may use: the measured space plus what hiding the pending native buttons will free.
static LAYOUT_CSS: AtomicU32 = AtomicU32::new(u32::MAX);
/// Collapsed strip width the page last measured (no folder open), in CSS pixels.
static STRIP_BASE_W: AtomicU32 = AtomicU32::new(0);
/// Every app the Dock may group, by category in display order, with those whose native button still shows
/// and how many are hidden now. The tracker plans the strip for all of them, not only the ones already listed.
#[derive(Default)]
struct Candidates { groups: Vec<(String, Vec<String>)>, pending: std::collections::HashSet<String>, hidden_apps: usize }
static CANDIDATES: Mutex<Option<Candidates>> = Mutex::new(None);
static UI_RENDERED: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
static RENDERED_WINDOWS: Mutex<Vec<isize>> = Mutex::new(Vec::new());
fn uptime_secs() -> u64 {
    static START: std::sync::OnceLock<Instant> = std::sync::OnceLock::new();
    START.get_or_init(Instant::now).elapsed().as_secs()
}
static STRIP_HIDDEN: AtomicBool = AtomicBool::new(false);
static ORGANIZE: AtomicBool = AtomicBool::new(true);
static CORE: Mutex<Option<Child>> = Mutex::new(None);
static GUARD: Mutex<Option<Child>> = Mutex::new(None);
static ICONS: Mutex<Option<HashMap<String, String>>> = Mutex::new(None);
static SNAPSHOT: Mutex<Vec<WinInfo>> = Mutex::new(Vec::new());
static LAUNCHES: launch::Launches = launch::Launches::new();
static STARTUP_URL: std::sync::OnceLock<tauri::Url> = std::sync::OnceLock::new();

#[derive(Serialize, Clone, PartialEq)]
struct WinInfo { hwnd: isize, title: String, exe: String, name: String, category: String, active: bool }

#[cfg(windows)]
fn no_window(cmd: &mut Command) -> &mut Command {
    use std::os::windows::process::CommandExt;
    cmd.creation_flags(0x0800_0000)
}

fn expand_env(input: &str) -> String {
    let mut out = String::new();
    let mut rest = input;
    while let Some(start) = rest.find('%') {
        out.push_str(&rest[..start]);
        let after = &rest[start + 1..];
        match after.find('%') {
            Some(end) => {
                let name = &after[..end];
                match std::env::var(name) { Ok(v) => out.push_str(&v), Err(_) => { out.push('%'); out.push_str(name); out.push('%'); } }
                rest = &after[end + 1..];
            }
            None => { out.push('%'); rest = after; }
        }
    }
    out.push_str(rest);
    out
}

fn appdata(sub: &str) -> PathBuf {
    std::env::var("APPDATA").map(PathBuf::from).unwrap_or_else(|_| std::env::temp_dir()).join("UltraSuite").join(sub)
}

// ----------------------------------------------------------------------------- organizer config
struct OrgCfg { keep: Vec<String>, order: Vec<String>, cats: Vec<(String, Vec<String>)>, overrides: HashMap<String, String> }
static ORG_CACHE: Mutex<Option<(SystemTime, std::sync::Arc<OrgCfg>)>> = Mutex::new(None);

fn org_path() -> PathBuf { appdata("organizer.json") }

fn org_config() -> std::sync::Arc<OrgCfg> {
    let p = org_path();
    if !p.exists() { if let Some(d) = p.parent() { let _ = std::fs::create_dir_all(d); } let _ = std::fs::write(&p, DEFAULT_ORG); }
    let mt = std::fs::metadata(&p).and_then(|m| m.modified()).unwrap_or(SystemTime::UNIX_EPOCH);
    if let Ok(g) = ORG_CACHE.lock() { if let Some((t, c)) = g.as_ref() { if *t == mt { return c.clone(); } } }
    let v: Value = std::fs::read_to_string(&p).ok().and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_else(|| serde_json::from_str(DEFAULT_ORG).expect("bundled organizer.json"));
    let strs = |x: &Value| x.as_array().map(|a| a.iter().filter_map(|s| s.as_str().map(|s| s.to_lowercase())).collect::<Vec<_>>()).unwrap_or_default();
    let order: Vec<String> = v["order"].as_array().map(|a| a.iter().filter_map(|s| s.as_str().map(String::from)).collect()).unwrap_or_default();
    let mut cats = vec![];
    if let Some(m) = v["categories"].as_object() { for (k, list) in m { cats.push((k.clone(), strs(list))); } }
    cats.sort_by_key(|(k, _)| order.iter().position(|o| o == k).unwrap_or(99));
    let overrides = v["overrides"].as_object().map(|m| m.iter().filter_map(|(k, x)| x.as_str().map(|s| (k.to_lowercase(), s.to_string()))).collect()).unwrap_or_default();
    let cfg = std::sync::Arc::new(OrgCfg { keep: strs(&v["keep"]), order, cats, overrides });
    if let Ok(mut g) = ORG_CACHE.lock() { *g = Some((mt, cfg.clone())); }
    cfg
}

fn categorize(exe: &str, cfg: &OrgCfg) -> String {
    let lower = exe.to_lowercase();
    let file = lower.rsplit('\\').next().unwrap_or(&lower).to_string();
    if let Some(c) = cfg.overrides.get(&file) { return c.clone(); }
    // the most specific (longest) matching keyword wins: "signalrgb" beats "signal"
    let mut best: Option<(&String, usize)> = None;
    for (cat, keys) in &cfg.cats {
        for k in keys { if lower.contains(k.as_str()) && best.map(|b| k.len() > b.1).unwrap_or(true) { best = Some((cat, k.len())); } }
    }
    best.map(|b| b.0.clone()).unwrap_or_else(|| "Other".into())
}

fn is_kept(exe: &str, cfg: &OrgCfg) -> bool {
    let lower = exe.to_lowercase();
    cfg.keep.iter().any(|k| lower.ends_with(k.as_str()) || lower.contains(k.as_str()))
}

// ----------------------------------------------------------------------------- taskbar pins
/// Apps pinned to the taskbar stay normal taskbar buttons. Tucking a pinned app's button away leaves the pin
/// without its "running" underline and puts a second copy of the app in a folder, so pins always win.
/// Two sources: shortcut files (normal programs) and the Taskband "Favorites" value (Store/MSIX apps such as
/// Claude, which only appear there as a package family name and install folder).
#[derive(Default, Debug, PartialEq)]
struct Pins { exes: Vec<String>, dirs: Vec<String>, pkgs: Vec<(String, String)> }
static PINS: Mutex<Option<(Instant, std::sync::Arc<Pins>)>> = Mutex::new(None);

/// Printable UTF-16 runs of at least 6 characters, tried at both byte alignments.
fn utf16_strings(bytes: &[u8]) -> Vec<String> {
    let mut out = vec![];
    for start in 0..2 {
        let mut cur = String::new();
        for ch in bytes[start.min(bytes.len())..].chunks_exact(2) {
            let c = u16::from_le_bytes([ch[0], ch[1]]);
            if (0x20..0x7f).contains(&c) { cur.push(c as u8 as char); }
            else { if cur.len() >= 6 { out.push(std::mem::take(&mut cur)); } cur.clear(); }
        }
        if cur.len() >= 6 { out.push(cur); }
    }
    out
}

/// Sort the strings found in the Favorites value into exe paths, install folders and package family names.
fn pins_from_strings(strings: &[String], pins: &mut Pins) {
    for s in strings {
        let s = s.trim().to_lowercase();
        let b = s.as_bytes();
        if b.len() > 3 && b[1] == b':' && b[2] == b'\\' {
            if s.ends_with(".exe") { pins.exes.push(s.clone()); }
            else if s.contains("\\windowsapps\\") { pins.dirs.push(format!("{}\\", s.trim_end_matches('\\'))); }
            continue;
        }
        // "claude_pzs8sxrjxfjjc", "claude_5.1231.2.0_x64__pzs8sxrjxfjjc" or "claude_pzs8sxrjxfjjc!claude"
        let id = s.split('!').next().unwrap_or("");
        if let Some((head, publisher)) = id.rsplit_once('_') {
            let name = head.split('_').next().unwrap_or("");
            if publisher.len() == 13 && publisher.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit())
                && !name.is_empty() && !name.contains(' ') {
                let p = (name.to_string(), publisher.to_string());
                if !pins.pkgs.contains(&p) { pins.pkgs.push(p); }
            }
        }
    }
}

/// A shortcut's folder counts too (Discord's pin points at Update.exe, its window comes from app-x\Discord.exe),
/// except broad system folders, which would sweep in unrelated programs.
fn add_pin_target(target: &str, pins: &mut Pins) {
    let t = target.trim().to_lowercase();
    if !t.ends_with(".exe") { return; }
    pins.exes.push(t.clone());
    let windir = std::env::var("WINDIR").unwrap_or_else(|_| "c:\\windows".into()).to_lowercase();
    if let Some((dir, _)) = t.rsplit_once('\\') {
        if !dir.starts_with(&windir) && dir.matches('\\').count() >= 2 { pins.dirs.push(format!("{dir}\\")); }
    }
}

#[cfg(windows)]
fn read_pins() -> Pins {
    use windows::core::{w, Interface, HSTRING};
    use windows::Win32::System::Com::*;
    use windows::Win32::System::Registry::{RegGetValueW, HKEY_CURRENT_USER, RRF_RT_REG_BINARY};
    use windows::Win32::UI::Shell::{IShellLinkW, ShellLink};
    let mut pins = Pins::default();
    if let Ok(appdata) = std::env::var("APPDATA") {
        let dir = PathBuf::from(appdata).join(r"Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar");
        for e in std::fs::read_dir(dir).into_iter().flatten().flatten() {
            let path = e.path();
            if path.extension().map(|x| x.eq_ignore_ascii_case("lnk")) != Some(true) { continue; }
            unsafe {
                let Ok(link) = CoCreateInstance::<_, IShellLinkW>(&ShellLink, None, CLSCTX_INPROC_SERVER) else { continue };
                let Ok(pf) = link.cast::<IPersistFile>() else { continue };
                if pf.Load(&HSTRING::from(path.as_os_str()), STGM_READ).is_err() { continue; }
                let mut buf = [0u16; 1024];
                if link.GetPath(&mut buf, std::ptr::null_mut(), 0).is_ok() {
                    let n = buf.iter().position(|&c| c == 0).unwrap_or(buf.len());
                    add_pin_target(&String::from_utf16_lossy(&buf[..n]), &mut pins);
                }
            }
        }
    }
    unsafe {
        let key = w!(r"Software\Microsoft\Windows\CurrentVersion\Explorer\Taskband");
        let mut size = 0u32;
        if RegGetValueW(HKEY_CURRENT_USER, key, w!("Favorites"), RRF_RT_REG_BINARY, None, None, Some(&mut size)).is_ok() && size > 0 {
            let mut data = vec![0u8; size as usize];
            if RegGetValueW(HKEY_CURRENT_USER, key, w!("Favorites"), RRF_RT_REG_BINARY, None, Some(data.as_mut_ptr() as *mut _), Some(&mut size)).is_ok() {
                data.truncate(size as usize);
                pins_from_strings(&utf16_strings(&data), &mut pins);
            }
        }
    }
    pins
}

#[cfg(windows)]
fn pinned_apps() -> std::sync::Arc<Pins> {
    if let Ok(g) = PINS.lock() { if let Some((t, p)) = g.as_ref() { if t.elapsed() < Duration::from_secs(10) { return p.clone(); } } }
    let p = std::sync::Arc::new(read_pins());
    if let Ok(mut g) = PINS.lock() { *g = Some((Instant::now(), p.clone())); }
    p
}

fn is_pinned(exe: &str, pins: &Pins) -> bool {
    let lower = exe.to_lowercase();
    pins.exes.iter().any(|e| *e == lower)
        || pins.dirs.iter().any(|d| lower.starts_with(d.as_str()))
        || pins.pkgs.iter().any(|(name, publisher)| lower.contains(&format!("\\windowsapps\\{name}_")) && lower.contains(&format!("__{publisher}\\")))
}

#[cfg(test)]
mod pin_tests {
    use super::*;

    fn utf16(s: &str) -> Vec<u8> { s.encode_utf16().flat_map(|c| c.to_le_bytes()).collect() }

    #[test]
    fn store_app_pin_matches_its_window() {
        // what the owner's Taskband Favorites value holds for Claude, wrapped in binary noise
        let mut blob = vec![0x14, 0x00, 0x1f, 0x80];
        for s in ["Claude_pzs8sxrjxfjjc", "\u{1}", r"C:\Program Files\WindowsApps\Claude_5.1231.2.0_x64__pzs8sxrjxfjjc", "\u{2}", r"Assets\Square150x150Logo.png"] {
            blob.extend(utf16(s));
        }
        let mut pins = Pins::default();
        pins_from_strings(&utf16_strings(&blob), &mut pins);
        assert!(pins.pkgs.contains(&("claude".into(), "pzs8sxrjxfjjc".into())));
        assert!(is_pinned(r"C:\Program Files\WindowsApps\Claude_5.1231.2.0_x64__pzs8sxrjxfjjc\app\claude.exe", &pins));
        // an updated Claude lives in a new versioned folder and still matches by package name
        assert!(is_pinned(r"C:\Program Files\WindowsApps\Claude_5.2000.0.0_x64__pzs8sxrjxfjjc\app\claude.exe", &pins));
        assert!(!is_pinned(r"C:\Users\x\AppData\Local\Claude-Data\claude_pzs8sxrjxfjjc\claude-code\claude.exe", &pins));
        assert!(!is_pinned(r"C:\Program Files\WindowsApps\Spotify_1.2.3.0_x64__zpdnekdrzrea0\Spotify.exe", &pins));
    }

    #[test]
    fn shortcut_pin_covers_its_folder_but_not_windows() {
        let mut pins = Pins::default();
        add_pin_target(r"C:\Users\x\AppData\Local\DiscordPTB\Update.exe", &mut pins);
        add_pin_target(r"C:\Windows\System32\cmd.exe", &mut pins);
        assert!(is_pinned(r"C:\Users\x\AppData\Local\DiscordPTB\app-1.0.1\DiscordPTB.exe", &pins));
        assert!(is_pinned(r"C:\Windows\System32\cmd.exe", &pins));
        assert!(!is_pinned(r"C:\Windows\System32\notepad.exe", &pins), "a system-folder pin must not sweep in its neighbours");
        assert!(!is_pinned(r"C:\Users\x\AppData\Local\Discord\app-1.0\Discord.exe", &pins));
    }
}

fn pretty_name(exe: &str) -> String {
    let stem = exe.rsplit('\\').next().unwrap_or(exe).trim_end_matches(".exe").trim_end_matches(".EXE");
    let mut c = stem.chars();
    match c.next() { Some(f) => f.to_uppercase().collect::<String>() + c.as_str(), None => stem.to_string() }
}

// ----------------------------------------------------------------------------- windows of other apps
#[cfg(windows)]
fn enum_app_windows() -> Vec<(isize, String, String)> {
    use windows::core::{BOOL, PWSTR};
    use windows::Win32::Foundation::{CloseHandle, HWND, LPARAM};
    use windows::Win32::Graphics::Dwm::{DwmGetWindowAttribute, DWMWA_CLOAKED};
    use windows::Win32::System::Threading::{OpenProcess, QueryFullProcessImageNameW, PROCESS_NAME_WIN32, PROCESS_QUERY_LIMITED_INFORMATION};
    use windows::Win32::UI::WindowsAndMessaging::*;
    unsafe extern "system" fn cb(h: HWND, l: LPARAM) -> BOOL {
        let out = &mut *(l.0 as *mut Vec<(isize, String, String)>);
        if !IsWindowVisible(h).as_bool() { return true.into(); }
        if let Ok(o) = GetWindow(h, GW_OWNER) { if !o.is_invalid() { return true.into(); } }
        let ex = GetWindowLongW(h, GWL_EXSTYLE) as u32;
        if ex & WS_EX_TOOLWINDOW.0 != 0 && ex & WS_EX_APPWINDOW.0 == 0 { return true.into(); }
        let mut cloaked: u32 = 0;
        let _ = DwmGetWindowAttribute(h, DWMWA_CLOAKED, &mut cloaked as *mut _ as *mut _, 4);
        if cloaked != 0 { return true.into(); }
        let mut pid = 0u32;
        GetWindowThreadProcessId(h, Some(&mut pid));
        if pid == std::process::id() { return true.into(); }
        // InternalGetWindowText never sends a message, so a busy window (or our own main thread) can't stall us
        let mut buf = [0u16; 512];
        let len = InternalGetWindowText(h, &mut buf);
        if len <= 0 { return true.into(); }
        let title = String::from_utf16_lossy(&buf[..len as usize]);
        let mut exe = String::new();
        if let Ok(ph) = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid) {
            let mut pbuf = vec![0u16; 1024];
            let mut n = pbuf.len() as u32;
            if QueryFullProcessImageNameW(ph, PROCESS_NAME_WIN32, PWSTR(pbuf.as_mut_ptr()), &mut n).is_ok() {
                exe = String::from_utf16_lossy(&pbuf[..n as usize]);
            }
            let _ = CloseHandle(ph);
        }
        let lower = exe.to_lowercase();
        // Windows' own shell windows, and Store-app frames whose taskbar button can't be moved
        const SKIP: [&str; 11] = ["\\textinputhost.exe", "\\shellexperiencehost.exe", "\\searchhost.exe", "\\startmenuexperiencehost.exe",
            "\\lockapp.exe", "\\applicationframehost.exe", "\\systemsettings.exe", "\\ultrasuite-core.exe", "\\shellhost.exe",
            "\\widgets.exe", "\\crossdeviceresume.exe"];
        if exe.is_empty() || SKIP.iter().any(|s| lower.ends_with(s)) { return true.into(); }
        out.push((h.0 as isize, title, exe));
        true.into()
    }
    let mut v: Vec<(isize, String, String)> = vec![];
    unsafe { let _ = EnumWindows(Some(cb), LPARAM(&mut v as *mut _ as isize)); }
    v
}

#[cfg(windows)]
fn taskbar_list() -> Option<windows::Win32::UI::Shell::ITaskbarList> {
    use windows::Win32::System::Com::*;
    use windows::Win32::UI::Shell::{ITaskbarList, TaskbarList};
    unsafe {
        let t: ITaskbarList = CoCreateInstance(&TaskbarList, None, CLSCTX_INPROC_SERVER).ok()?;
        t.HrInit().ok()?;
        Some(t)
    }
}

/// Put every taskbar button we tucked away back where it was.
#[cfg(windows)]
fn restore_all() {
    use windows::Win32::System::Com::*;
    unsafe { let _ = CoInitializeEx(None, COINIT_APARTMENTTHREADED); }
    if let Some(t) = taskbar_list() { recovery::restore_all(&t); }
}

#[cfg(windows)]
fn organizer_loop() {
    use windows::Win32::System::Com::*;
    use windows::Win32::UI::WindowsAndMessaging::GetForegroundWindow;
    unsafe { let _ = CoInitializeEx(None, COINIT_APARTMENTTHREADED); }
    restore_from_last_run();
    let Some(tbl) = taskbar_list() else { return };
    let mut last_reapply = Instant::now();
    let mut last_guard_retry = Instant::now();
    let started = Instant::now();
    let mut listing = dock_safety::StripListing::default();
    let mut burst = dock_layout::RehideBurst::default();
    let mut hidden_now: std::collections::HashSet<isize> = Default::default();
    loop {
        // Between full passes, watch the foreground window: activating a window makes Windows put its hidden
        // taskbar button back, so hide it again right away (and for a short burst after) instead of 4 s later.
        for _ in 0..5 {
            std::thread::sleep(Duration::from_millis(140));
            if !(ORGANIZE.load(Ordering::Relaxed) && HEALTHY.load(Ordering::Relaxed)) { continue; }
            let fg = unsafe { GetForegroundWindow().0 as isize };
            if let Some(h) = burst.tick(fg, started.elapsed().as_millis() as u64, |h| hidden_now.contains(&h)) {
                recovery::hide_window(h, true, &tbl);
            }
        }
        let cfg = org_config();
        let pins = pinned_apps();
        let wins = enum_app_windows();
        let fg = unsafe { GetForegroundWindow().0 as isize };
        // the watchdog says the dock is frozen or its strip isn't showing the folders: give every button back
        let guarded = guard_running();
        let on = guarded && ORGANIZE.load(Ordering::Relaxed) && HEALTHY.load(Ordering::Relaxed);
        let rendered = RENDERED_WINDOWS.lock().map(|r| r.clone()).unwrap_or_default();
        let mut snap = vec![];
        let reapply = last_reapply.elapsed() > Duration::from_secs(4);
        let now_ms = started.elapsed().as_millis() as u64;
        let mut pending: std::collections::HashSet<String> = Default::default();
        let mut hidden_exes: std::collections::HashSet<String> = Default::default();
        let mut cands: Vec<(usize, String, String, String)> = vec![];
        hidden_now.clear();
        for (h, title, exe) in &wins {
            let kept = is_kept(exe, &cfg) || is_pinned(exe, &pins);
            // Only a window the strip has rendered may be hidden, so a render error cannot lose apps.
            let hidden = on && !kept && rendered.contains(h) && recovery::hide_window(*h, reapply, &tbl);
            if !hidden { recovery::restore_window(*h, &tbl); } else { hidden_now.insert(*h); }
            if !kept {
                let cat = categorize(exe, &cfg);
                cands.push((cfg.order.iter().position(|o| *o == cat).unwrap_or(98), pretty_name(exe), cat, exe.clone()));
                if hidden { hidden_exes.insert(exe.clone()); } else { pending.insert(exe.clone()); }
            }
            // List a window only while its native button is hidden (or briefly while hiding is first tried), so an
            // app never shows twice: native button plus strip copy.
            if !kept && ORGANIZE.load(Ordering::Relaxed) && listing.decide(*h, now_ms, on, hidden) {
                snap.push(WinInfo { hwnd: *h, title: title.clone(), exe: exe.clone(), name: pretty_name(exe),
                                    category: categorize(exe, &cfg), active: *h == fg });
            }
        }
        listing.retain(&wins.iter().map(|w| w.0).collect());
        cands.sort();
        let mut groups: Vec<(String, Vec<String>)> = vec![];
        if ORGANIZE.load(Ordering::Relaxed) {
            for (_, _, cat, exe) in cands {
                match groups.iter_mut().find(|g| g.0 == cat) {
                    Some(g) => if !g.1.contains(&exe) { g.1.push(exe) },
                    None => groups.push((cat, vec![exe])),
                }
            }
        }
        let pending: std::collections::HashSet<String> = pending.difference(&hidden_exes).cloned().collect();
        if let Ok(mut c) = CANDIDATES.lock() { *c = Some(Candidates { groups, pending, hidden_apps: hidden_exes.len() }); }
        if !on { recovery::restore_all(&tbl); }
        if !guarded && last_guard_retry.elapsed() > Duration::from_secs(10) {
            spawn_guard();
            last_guard_retry = Instant::now();
        }
        if reapply { recovery::prune_closed(); last_reapply = Instant::now(); }
        let order = &cfg.order;
        snap.sort_by(|a, b| (order.iter().position(|o| *o == a.category).unwrap_or(98), &a.name).cmp(&(order.iter().position(|o| *o == b.category).unwrap_or(98), &b.name)));
        if let Ok(mut s) = SNAPSHOT.lock() { *s = snap; }
    }
}

// ----------------------------------------------------------------------------- watchdog
/// Safety net: the organizer may only tuck buttons away while the dock can show them. Every 2 seconds this
/// pings the strip's window (its main thread) and checks that the strip page is still asking for the folder
/// list. The rule: buttons stay tucked away only while the strip is on screen and alive, or while the taskbar
/// itself is hidden (auto-hide, a full-screen game), when nobody can see taskbar buttons anyway. Otherwise the
/// organizer puts every button back on the normal taskbar until the dock recovers.
static UI_SEEN: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
static HEALTHY: AtomicBool = AtomicBool::new(false);  // nothing is tucked away until the strip is up
static STRIP_SHOWN: AtomicBool = AtomicBool::new(false);
static TB_VISIBLE: AtomicBool = AtomicBool::new(true);
static STRIP_HWND: std::sync::atomic::AtomicIsize = std::sync::atomic::AtomicIsize::new(0);

fn now_secs() -> u64 { SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0) }

/// Pure decision, kept separate so it can be tested: is the dock fit to keep buttons tucked away?
#[cfg(test)]
fn dock_healthy(missed_pings: u32, strip_shown: bool, taskbar_visible: bool, secs_since_ui: u64) -> bool {
    let _ = taskbar_visible;
    missed_pings < 2 && strip_shown && secs_since_ui < 5
}

#[cfg(windows)]
fn strip_is_present(hwnd: windows::Win32::Foundation::HWND) -> bool {
    use windows::Win32::Foundation::RECT;
    use windows::Win32::Graphics::Dwm::{DwmGetWindowAttribute, DWMWA_CLOAKED};
    use windows::Win32::UI::WindowsAndMessaging::{GetWindowRect, IsIconic, IsWindowVisible};
    use windows::Win32::Graphics::Gdi::{GetMonitorInfoW, MonitorFromWindow, MONITORINFO, MONITOR_DEFAULTTONEAREST};
    unsafe {
        let mut rect = RECT::default();
        let mut cloaked = 0u32;
        let present = IsWindowVisible(hwnd).as_bool() && !IsIconic(hwnd).as_bool()
            && DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, &mut cloaked as *mut _ as _, 4).is_ok()
            && cloaked == 0 && GetWindowRect(hwnd, &mut rect).is_ok()
            && rect.right - rect.left > 8 && rect.bottom - rect.top > 20;
        if !present { return false; }
        let mut monitor = MONITORINFO { cbSize: std::mem::size_of::<MONITORINFO>() as u32, ..Default::default() };
        if !GetMonitorInfoW(MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST), &mut monitor).as_bool() { return false; }
        let inside_w = (rect.right.min(monitor.rcMonitor.right) - rect.left.max(monitor.rcMonitor.left)).max(0) as i64;
        let inside_h = (rect.bottom.min(monitor.rcMonitor.bottom) - rect.top.max(monitor.rcMonitor.top)).max(0) as i64;
        inside_w * inside_h * 100 >= (rect.right - rect.left) as i64 * (rect.bottom - rect.top) as i64 * 90
    }
}

#[cfg(windows)]
fn all_taskbars_covered() -> bool {
    use windows::core::w;
    use windows::Win32::UI::WindowsAndMessaging::FindWindowW;
    // There is currently one strip. ITaskbarList removes buttons globally, so
    // per-window monitor filtering would still lose mirrored secondary buttons.
    // Fail open until every taskbar has its own healthy strip.
    unsafe { FindWindowW(w!("Shell_SecondaryTrayWnd"), None).map(|h| h.is_invalid()).unwrap_or(true) }
}

#[cfg(windows)]
fn watchdog() {
    use windows::Win32::Foundation::{HWND, LPARAM, WPARAM};
    use windows::Win32::UI::WindowsAndMessaging::{SendMessageTimeoutW, SMTO_ABORTIFHUNG, WM_NULL};
    let mut missed = 0u32;
    let mut was = true;
    loop {
        std::thread::sleep(Duration::from_secs(2));
        let h = STRIP_HWND.load(Ordering::Relaxed);
        let answered = h == 0 || unsafe {
            let mut res = 0usize;
            SendMessageTimeoutW(HWND(h as _), WM_NULL, WPARAM(0), LPARAM(0), SMTO_ABORTIFHUNG, 1500, Some(&mut res)).0 != 0
        };
        missed = if answered { 0 } else { missed + 1 };
        if missed >= 5 { restart_frozen_dock(); }
        let seen = UI_RENDERED.load(Ordering::Relaxed);
        let since = if seen == 0 { u64::MAX } else { uptime_secs().saturating_sub(seen - 1) };
        let ok = dock_safety::Visibility {
            enabled: ORGANIZE.load(Ordering::Relaxed), guard_ready: guard_running(),
            main_responsive: missed < 2,
            strip_visible: STRIP_SHOWN.load(Ordering::Relaxed) && strip_is_present(HWND(h as _)),
            strip_fits: STRIP_FITS.load(Ordering::Relaxed) && !RENDER_OVERFLOW.load(Ordering::Relaxed),
            all_taskbars_covered: all_taskbars_covered(), rendered_age: since,
        }.may_hide();
        HEALTHY.store(ok, Ordering::Relaxed);
        if ok != was { println!("[watchdog] dock {}", if ok { "healthy again: organizing" } else { "visibility checks failed: native taskbar mode" }); was = ok; }
    }
}

/// Last resort when the main thread has not answered for about 10 seconds. By then the organizer has already
/// put every taskbar button back (two missed pings make the dock unhealthy). Start a fresh copy and leave; the
/// background services are a separate process and keep running, so alarms are unaffected. At most 3 restarts
/// in 10 minutes, so a dock that keeps freezing settles into "normal taskbar" instead of looping.
#[cfg(windows)]
fn restart_frozen_dock() {
    let log = appdata("dock_restarts.txt");
    let now = now_secs();
    let mut recent: Vec<u64> = std::fs::read_to_string(&log).unwrap_or_default().lines()
        .filter_map(|l| l.trim().parse().ok()).filter(|t| now.saturating_sub(*t) < 600).collect();
    if recent.len() >= 3 { return; }
    recent.push(now);
    let _ = std::fs::write(&log, recent.iter().map(|t| t.to_string()).collect::<Vec<_>>().join("\n"));
    let Ok(exe) = std::env::current_exe() else { return };
    println!("[watchdog] main thread frozen: restarting the dock");
    // the new copy waits for this one to be gone, so the single-instance check doesn't hand it back to us
    let mut cmd = Command::new(exe);
    cmd.args(["--background", "--restart-wait", &std::process::id().to_string()]);
    if cmd.spawn().is_ok() { std::process::exit(0); }
}

/// `--guard <pid>`: a tiny separate process that outlives the dock. When the dock exits for any reason (quit,
/// crash, killed from Task Manager), it restores that owner's versioned recovery journal. The
/// in-process watchdog covers a frozen dock; this covers a dead one. No window framework, just Win32.
#[cfg(windows)]
fn run_guard(owner: recovery::Owner) {
    use windows::Win32::Foundation::{CloseHandle, WAIT_OBJECT_0};
    use windows::Win32::System::Com::*;
    use windows::Win32::System::Threading::{OpenEventW, OpenProcess, SetEvent, WaitForSingleObject, EVENT_MODIFY_STATE, INFINITE, PROCESS_SYNCHRONIZE};
    use windows::core::PCWSTR;
    unsafe {
        if recovery::process_owner(owner.pid) == Some(owner) {
            if let Ok(h) = OpenProcess(PROCESS_SYNCHRONIZE, false, owner.pid) {
                let name = guard_event_name(owner);
                if let Ok(event) = OpenEventW(EVENT_MODIFY_STATE, false, PCWSTR(name.as_ptr())) {
                    let _ = SetEvent(event);
                    let _ = CloseHandle(event);
                }
                let result = WaitForSingleObject(h, INFINITE);
                let _ = CloseHandle(h);
                if result != WAIT_OBJECT_0 { return; }
            }
        }
        let _ = CoInitializeEx(None, COINIT_APARTMENTTHREADED);
        if let Some(t) = taskbar_list() {
            for _ in 0..5 {
                recovery::recover_owner(owner, &t);
                std::thread::sleep(Duration::from_millis(500));
            }
        }
    }
}

#[cfg(windows)]
fn spawn_guard() {
    use windows::core::PCWSTR;
    use windows::Win32::Foundation::{CloseHandle, WAIT_OBJECT_0};
    use windows::Win32::System::Threading::{CreateEventW, WaitForSingleObject};
    if guard_running() { return; }
    if let (Ok(exe), Some(owner)) = (std::env::current_exe(), recovery::process_owner(std::process::id())) {
        let name = guard_event_name(owner);
        let Ok(event) = (unsafe { CreateEventW(None, true, false, PCWSTR(name.as_ptr())) }) else { return; };
        if let Ok(mut child) = Command::new(exe).args(["--guard", &owner.pid.to_string(), &owner.started.to_string()]).spawn() {
            let ready = unsafe { WaitForSingleObject(event, 2_000) } == WAIT_OBJECT_0;
            if ready {
                if let Ok(mut guard) = GUARD.lock() { *guard = Some(child); }
                else { let _ = child.kill(); let _ = child.wait(); }
            } else { let _ = child.kill(); let _ = child.wait(); }
        }
        unsafe { let _ = CloseHandle(event); }
    }
}

#[cfg(windows)]
fn guard_event_name(owner: recovery::Owner) -> Vec<u16> {
    format!("Local\\UltraSuite.DockGuard.{}.{}\0", owner.pid, owner.started).encode_utf16().collect()
}

#[cfg(windows)]
fn guard_running() -> bool {
    GUARD.lock().ok().and_then(|mut guard| guard.as_mut().map(|child| matches!(child.try_wait(), Ok(None)))).unwrap_or(false)
}

/// `--restart-wait <pid>`: wait (up to 15 s) for the frozen copy to exit before starting.
#[cfg(windows)]
fn wait_for_old_copy(args: &[String]) {
    use windows::Win32::Foundation::CloseHandle;
    use windows::Win32::System::Threading::{OpenProcess, WaitForSingleObject, PROCESS_SYNCHRONIZE};
    let Some(pid) = args.iter().position(|a| a == "--restart-wait").and_then(|i| args.get(i + 1)).and_then(|p| p.parse::<u32>().ok()) else { return };
    unsafe {
        if let Ok(h) = OpenProcess(PROCESS_SYNCHRONIZE, false, pid) {
            let _ = WaitForSingleObject(h, 15_000);
            let _ = CloseHandle(h);
        }
    }
}

#[cfg(test)]
mod watchdog_tests {
    use super::dock_healthy;
    #[test]
    fn decides_when_buttons_must_come_back() {
        // (missed pings, strip shown, taskbar visible, seconds since the strip last refreshed)
        assert!(dock_healthy(0, true, true, 1));
        assert!(dock_healthy(1, true, true, 1), "one slow ping is tolerated");
        assert!(!dock_healthy(2, true, true, 1), "a frozen main thread gives the buttons back");
        assert!(!dock_healthy(0, true, true, 20), "a shown strip that stopped refreshing gives them back");
        assert!(!dock_healthy(0, false, true, 1), "taskbar on screen but no strip: the apps must not vanish");
        assert!(!dock_healthy(0, false, false, 600), "a hidden strip never authorizes hiding native buttons");
    }
}

/// The strip lists other apps only where it can stand in for their native taskbar buttons. While a taskbar it cannot
/// cover keeps every native button visible, listing the same apps in the strip would show each app twice, and
/// clicking the copy would only bring up the window the native button already opens. Brief health dips do not
/// empty the strip, so it does not flicker while it resizes.
fn strip_lists_windows(organize: bool, all_taskbars_covered: bool) -> bool { organize && all_taskbars_covered }

#[cfg(test)]
mod strip_listing_tests {
    use super::strip_lists_windows;
    #[test]
    fn apps_are_listed_only_where_native_buttons_can_be_replaced() {
        assert!(strip_lists_windows(true, true));
        assert!(!strip_lists_windows(true, false), "an uncovered taskbar keeps native buttons, so no duplicates");
        assert!(!strip_lists_windows(false, true), "grouping off: native buttons only");
        assert!(!strip_lists_windows(false, false));
    }
}

#[tauri::command]
fn get_windows() -> Value {
    UI_SEEN.store(now_secs(), Ordering::Relaxed);  // polling is diagnostic, not proof of rendering
    #[cfg(windows)]
    let covered = all_taskbars_covered();
    #[cfg(not(windows))]
    let covered = false;
    let wins = if strip_lists_windows(ORGANIZE.load(Ordering::Relaxed), covered) {
        SNAPSHOT.lock().map(|s| s.clone()).unwrap_or_default()
    } else {
        vec![]
    };
    let reason = if !ORGANIZE.load(Ordering::Relaxed) { Some("Grouping is off; native taskbar buttons stay visible.") }
        else if !covered { Some("Native taskbar mode: multiple taskbars need their own Dock strips.") }
        else if !HEALTHY.load(Ordering::Relaxed) { Some("Native taskbar mode until the Dock is visible, fits, and finishes rendering.") }
        else { None };
    let layout = strip_plan(&wins);
    let avail = AVAIL_CSS.load(Ordering::Relaxed);
    json!({ "organize": ORGANIZE.load(Ordering::Relaxed), "windows": wins, "order": org_config().order, "fallback_reason": reason,
            "layout": layout, "avail": if avail == u32::MAX { Value::Null } else { json!(avail) } })
}

/// Group the listed windows into the folders and apps the strip shows, sized to the taskbar's free space.
fn strip_plan(wins: &[WinInfo]) -> Value {
    let mut groups: Vec<(String, Vec<String>)> = vec![];
    for w in wins {  // already sorted by category order, then name
        let pos = match groups.iter().position(|g| g.0 == w.category) { Some(p) => p, None => { groups.push((w.category.clone(), vec![])); groups.len() - 1 } };
        if !groups[pos].1.contains(&w.exe) { groups[pos].1.push(w.exe.clone()); }
    }
    let room = LAYOUT_CSS.load(Ordering::Relaxed);
    let plan = dock_layout::plan(&groups, if room == u32::MAX { f64::INFINITY } else { room as f64 });
    json!({
        "items": plan.items.iter().map(|i| json!({ "key": i.key, "label": i.label, "folder": i.folder, "exes": i.exes })).collect::<Vec<_>>(),
        "width": plan.width, "compacted": plan.compacted, "over_six": plan.over_six, "left_out": plan.left_out,
    })
}

/// Bring another app's window forward. Runs on a throwaway thread and only uses calls that don't wait on the
/// target: a busy or frozen app (a launcher, a game, Docker) once blocked the main thread here forever, which
/// froze the whole dock while its taskbar buttons were tucked away.
#[tauri::command]
fn activate(hwnd: isize) {
    #[cfg(windows)]
    std::thread::spawn(move || unsafe {
        use windows::Win32::Foundation::HWND;
        use windows::Win32::UI::Input::KeyboardAndMouse::{keybd_event, KEYBD_EVENT_FLAGS, KEYEVENTF_KEYUP, VK_MENU};
        use windows::Win32::UI::WindowsAndMessaging::*;
        let h = HWND(hwnd as _);
        if IsIconic(h).as_bool() { let _ = ShowWindowAsync(h, SW_RESTORE); }
        // a quick Alt tap lets a background app hand focus to another window (standard Windows rule)
        keybd_event(VK_MENU.0 as u8, 0, KEYBD_EVENT_FLAGS(0), 0);
        keybd_event(VK_MENU.0 as u8, 0, KEYEVENTF_KEYUP, 0);
        let _ = SetForegroundWindow(h);
    });
}

#[tauri::command]
fn set_organize(on: bool) { ORGANIZE.store(on, Ordering::Relaxed); }

// ----------------------------------------------------------------------------- icons
#[cfg(windows)]
fn icon_png(target: &str, size: i32) -> Option<Vec<u8>> {
    use windows::core::PCWSTR;
    use windows::Win32::Foundation::SIZE;
    use windows::Win32::Graphics::Gdi::*;
    use windows::Win32::System::Com::*;
    use windows::Win32::UI::Shell::*;
    unsafe {
        let _ = CoInitializeEx(None, COINIT_APARTMENTTHREADED);
        let wide: Vec<u16> = target.encode_utf16().chain(std::iter::once(0)).collect();
        let factory: IShellItemImageFactory = SHCreateItemFromParsingName(PCWSTR(wide.as_ptr()), None).ok()?;
        let hbm = factory.GetImage(SIZE { cx: size, cy: size }, SIIGBF_ICONONLY | SIIGBF_BIGGERSIZEOK).ok()?;
        let mut bmp = BITMAP::default();
        GetObjectW(hbm.into(), std::mem::size_of::<BITMAP>() as i32, Some(&mut bmp as *mut _ as *mut _));
        let (w, h) = (bmp.bmWidth, bmp.bmHeight.abs());
        if w <= 0 || h <= 0 { let _ = DeleteObject(hbm.into()); return None; }
        let mut bi = BITMAPINFO::default();
        bi.bmiHeader.biSize = std::mem::size_of::<BITMAPINFOHEADER>() as u32;
        bi.bmiHeader.biWidth = w; bi.bmiHeader.biHeight = -h; bi.bmiHeader.biPlanes = 1;
        bi.bmiHeader.biBitCount = 32; bi.bmiHeader.biCompression = BI_RGB.0;
        let mut buf = vec![0u8; (w * h * 4) as usize];
        let hdc = GetDC(None);
        GetDIBits(hdc, hbm, 0, h as u32, Some(buf.as_mut_ptr() as *mut _), &mut bi, DIB_RGB_COLORS);
        ReleaseDC(None, hdc);
        let _ = DeleteObject(hbm.into());
        for px in buf.chunks_mut(4) {
            let (b, g, r, a) = (px[0] as u32, px[1] as u32, px[2] as u32, px[3] as u32);
            let un = |c: u32| if a > 0 { ((c * 255 + a / 2) / a).min(255) as u8 } else { 0 };
            px[0] = un(r); px[1] = un(g); px[2] = un(b); px[3] = a as u8;
        }
        let mut out = Vec::new();
        {
            let mut enc = png::Encoder::new(&mut out, w as u32, h as u32);
            enc.set_color(png::ColorType::Rgba); enc.set_depth(png::BitDepth::Eight);
            let mut wr = enc.write_header().ok()?;
            wr.write_image_data(&buf).ok()?;
        }
        Some(out)
    }
}

/// Icon from a window handle (WM_GETICON / class icon), for programs whose file has no usable icon.
#[cfg(windows)]
fn window_icon_png(hwnd: isize) -> Option<Vec<u8>> {
    use windows::Win32::Foundation::{HWND, LPARAM, WPARAM};
    use windows::Win32::Graphics::Gdi::*;
    use windows::Win32::UI::WindowsAndMessaging::*;
    unsafe {
        let h = HWND(hwnd as _);
        let mut res: usize = 0;
        let _ = SendMessageTimeoutW(h, WM_GETICON, WPARAM(ICON_BIG as usize), LPARAM(0), SMTO_ABORTIFHUNG, 300, Some(&mut res));
        let mut hicon = HICON(res as _);
        if hicon.is_invalid() { hicon = HICON(GetClassLongPtrW(h, GCLP_HICON) as _); }
        if hicon.is_invalid() { return None; }
        let size = 48i32;
        let hdc = GetDC(None);
        let mem = CreateCompatibleDC(Some(hdc));
        let mut bi = BITMAPINFO::default();
        bi.bmiHeader.biSize = std::mem::size_of::<BITMAPINFOHEADER>() as u32;
        bi.bmiHeader.biWidth = size; bi.bmiHeader.biHeight = -size; bi.bmiHeader.biPlanes = 1;
        bi.bmiHeader.biBitCount = 32; bi.bmiHeader.biCompression = BI_RGB.0;
        let mut bits: *mut core::ffi::c_void = std::ptr::null_mut();
        let dib = CreateDIBSection(Some(mem), &bi, DIB_RGB_COLORS, &mut bits, None, 0).ok()?;
        let old = SelectObject(mem, dib.into());
        let _ = DrawIconEx(mem, 0, 0, hicon, size, size, 0, None, DI_NORMAL);
        let buf = std::slice::from_raw_parts(bits as *const u8, (size * size * 4) as usize).to_vec();
        SelectObject(mem, old);
        let _ = DeleteObject(dib.into());
        let _ = DeleteDC(mem);
        ReleaseDC(None, hdc);
        let mut rgba = buf;
        let any_alpha = rgba.chunks(4).any(|p| p[3] != 0);
        for px in rgba.chunks_mut(4) { px.swap(0, 2); if !any_alpha && (px[0] | px[1] | px[2]) != 0 { px[3] = 255; } }
        let mut out = Vec::new();
        {
            let mut enc = png::Encoder::new(&mut out, size as u32, size as u32);
            enc.set_color(png::ColorType::Rgba); enc.set_depth(png::BitDepth::Eight);
            let mut wr = enc.write_header().ok()?;
            wr.write_image_data(&rgba).ok()?;
        }
        Some(out)
    }
}

/// Microsoft Store (MSIX) apps keep their icon in the app package, not in the program file, so the file gives
/// a blank. Their identity (e.g. "OpenAI.Codex_2p2nqsd0c76g0!App") points at the Start menu entry, which has it.
#[cfg(windows)]
fn package_aumid(hwnd: isize) -> Option<String> {
    use windows::core::PWSTR;
    use windows::Win32::Foundation::{CloseHandle, HWND};
    use windows::Win32::Storage::Packaging::Appx::GetApplicationUserModelId;
    use windows::Win32::System::Threading::{OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION};
    use windows::Win32::UI::WindowsAndMessaging::GetWindowThreadProcessId;
    unsafe {
        let mut pid = 0u32;
        GetWindowThreadProcessId(HWND(hwnd as _), Some(&mut pid));
        let ph = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid).ok()?;
        let mut buf = vec![0u16; 512];
        let mut len = buf.len() as u32;
        let r = GetApplicationUserModelId(ph, &mut len, Some(PWSTR(buf.as_mut_ptr())));
        let _ = CloseHandle(ph);
        if r.0 != 0 { return None; }
        let n = buf.iter().position(|&c| c == 0).unwrap_or(0);
        if n == 0 { None } else { Some(String::from_utf16_lossy(&buf[..n])) }
    }
}

#[tauri::command]
async fn get_icon(target: String, hwnd: Option<isize>) -> Option<String> {
    if let Some(c) = ICONS.lock().ok().and_then(|m| m.as_ref().and_then(|m| m.get(&target).cloned())) { return Some(c); }
    let t = target.clone();
    #[cfg(windows)]
    let png = tauri::async_runtime::spawn_blocking(move || {
        let store_app = t.to_lowercase().contains("\\windowsapps\\");
        let from_package = || hwnd.and_then(package_aumid).and_then(|id| icon_png(&format!("shell:AppsFolder\\{id}"), 64));
        (if store_app { from_package() } else { None })
            .or_else(|| icon_png(&expand_env(&t), 64))
            .or_else(|| hwnd.and_then(window_icon_png))
    }).await.ok().flatten()?;
    #[cfg(not(windows))]
    let png: Vec<u8> = { let _ = t; return None; };
    let url = format!("data:image/png;base64,{}", base64::engine::general_purpose::STANDARD.encode(png));
    if let Ok(mut m) = ICONS.lock() { m.get_or_insert_with(HashMap::new).insert(target, url.clone()); }
    Some(url)
}

#[tauri::command]
fn set_strip_width(width: f64, rendered: Option<Vec<isize>>, base: Option<f64>) {
    if !width.is_finite() || width < 0.0 { return; }
    if let Some(b) = base.filter(|b| b.is_finite() && *b >= 0.0) { STRIP_BASE_W.store(b.ceil().min(1800.0) as u32, Ordering::Relaxed); }
    RENDER_OVERFLOW.store(width > 1800.0, Ordering::Relaxed);
    STRIP_W.store(width.ceil().max(20.0).min(1800.0) as u32, Ordering::Relaxed);
    if let Some(windows) = rendered {
        if windows.len() <= 4096 {
            if let Ok(mut ack) = RENDERED_WINDOWS.lock() { *ack = windows; UI_RENDERED.store(uptime_secs() + 1, Ordering::Relaxed); }
        }
    }
}

#[tauri::command]
fn open_config() -> Result<(), String> {
    let _ = org_config();
    Command::new("notepad.exe").arg(org_path()).spawn().map(|_| ()).map_err(|e| e.to_string())
}

#[tauri::command]
fn quit(app: AppHandle) {
    #[cfg(windows)]
    restore_all();
    stop_core();
    app.exit(0);
}

#[tauri::command]
fn open_app_view(app: AppHandle, name: String) { open_view(&app, &name); }

#[tauri::command]
async fn send_event(kind: String, reason: Option<String>, message: Option<String>) -> bool {
    // a network call: keep it off the main thread
    tauri::async_runtime::spawn_blocking(move || post_event(&kind, reason, message)).await.unwrap_or(false)
}

fn post_event(kind: &str, reason: Option<String>, message: Option<String>) -> bool {
    if COLLECTOR.is_empty() { return false; }
    let consented = std::fs::read_to_string(appdata("consent.txt")).unwrap_or_default().trim() == "yes";
    // Typed feedback and an answered uninstall survey are sent because the person chose to send them.
    // Everything else (install, heartbeat, a skipped survey) needs the "yes" from setup.
    let explicit = kind == "feedback" || (kind == "uninstall" && reason.is_some());
    if !consented && !explicit { return false; }
    let body = json!({"type": kind, "version": env!("CARGO_PKG_VERSION"), "reason": reason, "message": message,
                      "os": std::env::consts::OS, "id": anon_id()}).to_string().replace('\'', "''");
    let ps = format!("try {{ Invoke-RestMethod -Method Post -TimeoutSec 8 -Uri '{}' -ContentType 'application/json' -Body '{}' | Out-Null }} catch {{ exit 1 }}", COLLECTOR, body);
    let mut cmd = Command::new("powershell.exe");
    cmd.args(["-NoProfile", "-NonInteractive", "-Command", &ps]);
    #[cfg(windows)]
    no_window(&mut cmd);
    cmd.status().map(|s| s.success()).unwrap_or(false)
}

fn anon_id() -> String {
    let p = appdata("id.txt");
    if let Ok(s) = std::fs::read_to_string(&p) { if s.trim().len() >= 16 { return s.trim().to_string(); } }
    let id = format!("{:x}{:x}", SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0), std::process::id());
    if let Some(d) = p.parent() { let _ = std::fs::create_dir_all(d); }
    let _ = std::fs::write(&p, &id);
    id
}

// ----------------------------------------------------------------------------- app windows
/// Gives an app window its own Windows identity (AppUserModelID), so the taskbar treats it as its own app:
/// separate button, its own icon, and "Pin to taskbar" relaunches that app instead of the whole suite.
/// Writes AppUserModel string properties (5 = ID, 2 = relaunch command, 3 = relaunch icon, 4 = display name).
#[cfg(windows)]
unsafe fn write_aumid_props(store: &windows::Win32::UI::Shell::PropertiesSystem::IPropertyStore, vals: &[(u32, String)]) {
    use windows::core::PCWSTR;
    use windows::Win32::Foundation::PROPERTYKEY;
    use windows::Win32::System::Com::StructuredStorage::{PropVariantClear, PROPVARIANT};
    use windows::Win32::System::Variant::VT_LPWSTR;
    use windows::Win32::UI::Shell::SHStrDupW;
    const FMT: windows::core::GUID = windows::core::GUID::from_u128(0x9f4c2855_9f79_4b39_a8d0_e1d42de1d5f3);
    // relaunch details first, the ID last (Windows reads the others when the ID is set)
    for (pid, v) in vals.iter().rev() {
        let w: Vec<u16> = v.encode_utf16().chain(std::iter::once(0)).collect();
        // the string must live in COM memory: PROPVARIANT frees it with CoTaskMemFree
        let Ok(owned) = SHStrDupW(PCWSTR(w.as_ptr())) else { continue };
        let mut pv = PROPVARIANT::default();
        (*pv.Anonymous.Anonymous).vt = VT_LPWSTR;
        (*pv.Anonymous.Anonymous).Anonymous.pwszVal = owned;
        let _ = store.SetValue(&PROPERTYKEY { fmtid: FMT, pid: *pid }, &pv);
        let _ = PropVariantClear(&mut pv);
        std::mem::forget(pv);
    }
    let _ = store.Commit();
}

/// Start menu shortcuts carry the same AppUserModelID as their window, so a pinned shortcut and the
/// running app are one taskbar button (instead of a pin plus a separate running button).
#[cfg(windows)]
fn fix_shortcut_ids() {
    use windows::core::{Interface, HSTRING, PCWSTR};
    use windows::Win32::System::Com::*;
    use windows::Win32::UI::Shell::PropertiesSystem::IPropertyStore;
    use windows::Win32::UI::Shell::{IShellLinkW, ShellLink};
    let Ok(appdata) = std::env::var("APPDATA") else { return };
    let dir = PathBuf::from(appdata).join(r"Microsoft\Windows\Start Menu\Programs");
    unsafe {
        let _ = CoInitializeEx(None, COINIT_APARTMENTTHREADED);
        for (file, name) in [("Ultra Suite.lnk", "hub"), ("PC Sentinel.lnk", "sentinel"), ("Code Red Alarm.lnk", "alarm"), ("Ultra Suite Insights.lnk", "insights")] {
            let path = dir.join(file);
            if !path.exists() { continue; }
            let Ok(link) = CoCreateInstance::<_, IShellLinkW>(&ShellLink, None, CLSCTX_INPROC_SERVER) else { continue };
            let Ok(pf) = link.cast::<IPersistFile>() else { continue };
            if pf.Load(&HSTRING::from(path.as_os_str()), STGM_READWRITE).is_err() { continue; }
            let Ok(store) = link.cast::<IPropertyStore>() else { continue };
            write_aumid_props(&store, &[(5, format!("UltraSuite.{name}"))]);
            let _ = pf.Save(PCWSTR::null(), true);
        }
    }
}

#[cfg(windows)]
fn set_app_identity(app: &AppHandle, win: &WebviewWindow, name: &str, title: &str) {
    use windows::Win32::Foundation::HWND;
    use windows::Win32::UI::Shell::PropertiesSystem::{IPropertyStore, SHGetPropertyStoreForWindow};
    let Ok(h) = win.hwnd() else { return };
    let hwnd = HWND(h.0 as _);
    let exe = std::env::current_exe().map(|p| p.display().to_string()).unwrap_or_default();
    let icon = [app.path().resource_dir().ok().map(|r| r.join("appicons")),
                std::env::current_exe().ok().and_then(|p| p.parent().map(|d| d.join("appicons"))),
                Some(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("appicons"))]
        .into_iter().flatten().map(|d| d.join(format!("{name}.ico"))).find(|p| p.exists())
        .map(|p| format!("{},0", p.display())).unwrap_or_else(|| format!("{exe},0"));
    let aumid = format!("UltraSuite.{}", name);
    let cmd = format!("\"{exe}\" --open {name}");
    let vals: Vec<(u32, String)> = vec![(5, aumid), (2, cmd), (3, icon), (4, title.to_string())];
    unsafe {
        let Ok(store) = SHGetPropertyStoreForWindow::<IPropertyStore>(hwnd) else { return };
        write_aumid_props(&store, &vals);
    }
}
#[cfg(not(windows))]
fn set_app_identity(_app: &AppHandle, _win: &WebviewWindow, _name: &str, _title: &str) {}

fn open_view(app: &AppHandle, name: &str) {
    if name == "hub" { open_hub(app); return; }
    let Some(view) = launch::view(name) else { return };
    if let Some(win) = app.get_webview_window(name) { show_view_window(&win); }
    let Some(permit) = LAUNCHES.begin(view) else { return };
    let app = app.clone();
    // Creating WebView2 in a synchronous Windows command/event callback can deadlock.
    // https://docs.rs/tauri/2.12.5/tauri/webview/struct.WebviewWindowBuilder.html#method.new
    if let Err(error) = std::thread::Builder::new().name(format!("launch-{}", view.name)).spawn(move || {
        let _permit = permit;
        open_view_worker(&app, view);
    }) { eprintln!("[launch] worker could not start: {error}"); }
}

/// The Ultra Suite hub: one launcher window that lists every app with live status cards (hub.html).
/// It is a local page, so it opens at once even while the core is still starting.
fn open_hub(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("hub") { show_view_window(&win); return; }
    static HUB_OPENING: AtomicBool = AtomicBool::new(false);
    if HUB_OPENING.swap(true, Ordering::AcqRel) { return; }
    let app = app.clone();
    // same rule as open_view: never build a WebView2 window inside a synchronous callback
    let spawned = std::thread::Builder::new().name("launch-hub".into()).spawn(move || {
        #[cfg(windows)]
        let com = unsafe { windows::Win32::System::Com::CoInitializeEx(None, windows::Win32::System::Com::COINIT_APARTMENTTHREADED) }.is_ok();
        let built = WebviewWindowBuilder::new(&app, "hub", WebviewUrl::App("hub.html".into()))
            .title("Ultra Suite").inner_size(1180.0, 780.0).min_inner_size(380.0, 480.0).center().visible(false).build();
        match built {
            Ok(win) => {
                let w2 = win.clone();
                win.on_window_event(move |e| { if let WindowEvent::CloseRequested { api, .. } = e { api.prevent_close(); let _ = w2.hide(); } });
                if let Ok(img) = tauri::image::Image::from_bytes(include_bytes!("../icons/apps/dock.png")) { let _ = win.set_icon(img); }
                set_app_identity(&app, &win, "hub", "Ultra Suite");
                show_view_window(&win);
            }
            Err(error) => eprintln!("[launch] hub window creation failed: {error}"),
        }
        ensure_core(&app);
        #[cfg(windows)]
        if com { unsafe { windows::Win32::System::Com::CoUninitialize(); } }
        HUB_OPENING.store(false, Ordering::Release);
    });
    if let Err(error) = spawned { eprintln!("[launch] hub worker could not start: {error}"); HUB_OPENING.store(false, Ordering::Release); }
}

/// What the hub needs that a web page cannot see: version, Dock state and which optional editions are installed.
#[tauri::command]
fn hub_info() -> Value {
    let local = std::env::var("LOCALAPPDATA").map(PathBuf::from).ok();
    let start = std::env::var("APPDATA").map(|a| PathBuf::from(a).join(r"Microsoft\Windows\Start Menu\Programs")).ok();
    let insights = start.map(|d| d.join("Ultra Suite Insights.lnk").exists()).unwrap_or(false) || port_open(8301);
    let dev = local.map(|d| d.join("Ultra Suite (Dev)").join("Ultra Suite Dev.exe").exists()).unwrap_or(false);
    #[cfg(windows)]
    let covered = all_taskbars_covered();
    #[cfg(not(windows))]
    let covered = false;
    // Why the Dock is on but leaving the taskbar as it is (the same reasons the strip's menu shows)
    let dock_note = if !covered { Some("More than one taskbar (a second monitor), so every taskbar button stays visible.") }
        else if !HEALTHY.load(Ordering::Relaxed) { Some("Not enough room on the taskbar right now, so every button stays visible.") }
        else { None };
    json!({"version": env!("CARGO_PKG_VERSION"), "dock_on": !STRIP_HIDDEN.load(Ordering::Relaxed),
           "dock_grouping": covered && HEALTHY.load(Ordering::Relaxed), "dock_note": dock_note,
           "insights_installed": insights, "dev_installed": dev})
}

#[tauri::command]
fn hub_set_dock(on: bool) { set_dock_enabled(on); }

fn show_view_window(win: &WebviewWindow) {
    let _ = win.unminimize();
    let _ = win.show();
    #[cfg(windows)]
    unsafe {
        use windows::Win32::{Foundation::{HWND, RECT}, Graphics::Gdi::*, UI::WindowsAndMessaging::*};
        if let Ok(handle) = win.hwnd() {
            let hwnd = HWND(handle.0 as _);
            let mut rect = RECT::default();
            let mut monitor = MONITORINFO { cbSize: std::mem::size_of::<MONITORINFO>() as u32, ..Default::default() };
            if !IsIconic(hwnd).as_bool() && GetWindowRect(hwnd, &mut rect).is_ok()
                && GetMonitorInfoW(MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST), &mut monitor).as_bool() {
                let area = monitor.rcWork;
                let visible_w = rect.right.min(area.right) - rect.left.max(area.left);
                let visible_h = rect.bottom.min(area.bottom) - rect.top.max(area.top);
                let width = (rect.right - rect.left).min(area.right - area.left);
                let height = (rect.bottom - rect.top).min(area.bottom - area.top);
                if visible_w < 80 || visible_h < 48 || rect.right - rect.left > width || rect.bottom - rect.top > height {
                    let x = rect.left.clamp(area.left, area.right - width);
                    let y = rect.top.clamp(area.top, area.bottom - height);
                    let _ = SetWindowPos(hwnd, None, x, y, width, height, SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS);
                }
            }
        }
    }
    let _ = win.set_focus();
}

fn open_view_worker(app: &AppHandle, view: launch::View) {
    #[cfg(windows)]
    struct Apartment(bool);
    #[cfg(windows)]
    impl Drop for Apartment { fn drop(&mut self) { if self.0 { unsafe { windows::Win32::System::Com::CoUninitialize(); } } } }
    #[cfg(windows)]
    let _apartment = Apartment(unsafe { windows::Win32::System::Com::CoInitializeEx(None, windows::Win32::System::Com::COINIT_APARTMENTTHREADED) }.is_ok());
    let name = view.name;
    let url = format!("http://127.0.0.1:{}/", view.port);
    let startup = format!("starting.html?app={name}");
    if let Some(win) = app.get_webview_window(name) {
        if LAUNCHES.loaded(view) { ensure_core(app); return; }
        if let Some(base) = STARTUP_URL.get() {
            let mut loading = base.clone();
            loading.set_query(Some(&format!("app={name}")));
            let _ = win.navigate(loading);
        }
        wait_for_view(app, &win, view, &url);
        return;
    }
    let (w, h) = if name == "alarm" { (1360.0, 920.0) } else { (1440.0, 960.0) };
    // every app window gets its own icon (title bar, taskbar, Alt+Tab)
    let icon_bytes: &[u8] = match name {
        "alarm" => include_bytes!("../icons/apps/alarm.png"),
        "sentinel" => include_bytes!("../icons/apps/sentinel.png"),
        _ => include_bytes!("../icons/apps/insights.png"),
    };
    let builder = WebviewWindowBuilder::new(app, name, WebviewUrl::App(startup.into()))
        .title(view.title).inner_size(w, h).min_inner_size(360.0, 400.0).center().visible(false)
        .on_page_load(move |_, payload| {
            let loaded = payload.event() == tauri::webview::PageLoadEvent::Finished
                && payload.url().host_str() == Some("127.0.0.1") && payload.url().port() == Some(view.port);
            LAUNCHES.set_loaded(view, loaded);
        });
    match builder.build() {
      Ok(win) => {
        let w2 = win.clone();
        win.on_window_event(move |e| { if let WindowEvent::CloseRequested { api, .. } = e { api.prevent_close(); let _ = w2.hide(); } });
        if let Ok(img) = tauri::image::Image::from_bytes(icon_bytes) { let _ = win.set_icon(img); }
        set_app_identity(app, &win, name, view.title);
        show_view_window(&win);
        wait_for_view(app, &win, view, &url);
      }
      Err(error) => eprintln!("[launch] {} window creation failed: {error}", view.name),
    }
}

fn wait_for_view(app: &AppHandle, win: &WebviewWindow, view: launch::View, url: &str) {
    let deadline = Instant::now() + Duration::from_secs(30);
    ensure_core(app);
    while Instant::now() < deadline {
        if view.ready(http_get(view.port, view.probe).as_deref()) {
            if let Err(error) = win.navigate(url.parse().unwrap()) { eprintln!("[launch] {} navigation failed: {error}", view.name); }
            return;
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    let _ = win.eval("document.getElementById('status').textContent='The local service is not ready yet. Try again, or check that the app service is running.';document.getElementById('retry').disabled=false;document.getElementById('retry').textContent='Try again';");
}

fn port_open(port: u16) -> bool {
    TcpStream::connect_timeout(&format!("127.0.0.1:{}", port).parse().unwrap(), Duration::from_millis(250)).is_ok()
}

fn core_exe(app: &AppHandle) -> Option<PathBuf> {
    let mut c = vec![];
    if let Ok(r) = app.path().resource_dir() { c.push(r.join("core").join("ultrasuite-core.exe")); }
    if let Ok(exe) = std::env::current_exe() { if let Some(d) = exe.parent() { c.push(d.join("core").join("ultrasuite-core.exe")); } }
    c.into_iter().find(|p| p.exists())
}

fn ensure_core(app: &AppHandle) {
    if port_open(8081) && port_open(8084) { return; }
    if let Ok(mut g) = CORE.lock() {
        if let Some(ch) = g.as_mut() { if ch.try_wait().ok().flatten().is_none() { return; } }
        if let Some(exe) = core_exe(app) {
            let mut cmd = Command::new(&exe);
            if let Some(d) = exe.parent() { cmd.current_dir(d); }
            // An inherited IANA TZ ("America/Los_Angeles") makes the Windows C runtime fall back to UTC,
            // which once made an alarm ring 8 hours off. The core should follow the Windows time zone.
            cmd.env_remove("TZ");
            #[cfg(windows)]
            no_window(&mut cmd);
            if let Ok(ch) = cmd.spawn() { *g = Some(ch); }
        }
    }
    for _ in 0..60 { if port_open(8081) && port_open(8084) { break; } std::thread::sleep(Duration::from_millis(200)); }
}

fn stop_core() { if let Ok(mut g) = CORE.lock() { if let Some(mut ch) = g.take() { let _ = ch.kill(); } } }

fn http_get(port: u16, path: &str) -> Option<String> {
    let mut s = TcpStream::connect_timeout(&format!("127.0.0.1:{}", port).parse().ok()?, Duration::from_millis(300)).ok()?;
    s.set_read_timeout(Some(Duration::from_millis(800))).ok()?;
    write!(s, "GET {} HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n", path).ok()?;
    let mut buf = String::new();
    s.read_to_string(&mut buf).ok()?;
    buf.split("\r\n\r\n").nth(1).map(|b| b.to_string())
}

fn http_get_slow(port: u16, path: &str, secs: u64) -> Option<String> {
    let mut s = TcpStream::connect_timeout(&format!("127.0.0.1:{}", port).parse().ok()?, Duration::from_millis(500)).ok()?;
    s.set_read_timeout(Some(Duration::from_secs(secs))).ok()?;
    write!(s, "GET {} HTTP/1.0
Host: 127.0.0.1

", path).ok()?;
    let mut buf = String::new();
    s.read_to_string(&mut buf).ok()?;
    buf.split("

").nth(1).map(|b| b.to_string())
}

/// The version to announce, if the core reports a newer release with an installer that was not announced yet.
fn update_to_announce(info: &Value, already_announced: &str) -> Option<String> {
    if info["available"].as_bool() != Some(true) || info["asset"].as_str().map_or(true, |a| a.is_empty()) { return None; }
    let latest = info["latest"].as_str()?.trim();
    if latest.is_empty() || latest == already_announced.trim() { None } else { Some(latest.to_string()) }
}

/// Tell the user about a new release once per version, even when no app window is open. The check goes through the
/// local core (which caches GitHub for 6 hours), so this adds no extra GitHub requests.
fn update_notifier(app: AppHandle) {
    use tauri_plugin_notification::NotificationExt;
    std::thread::sleep(Duration::from_secs(90));
    loop {
        if let Some(info) = http_get_slow(8084, "/api/update", 20).and_then(|b| serde_json::from_str::<Value>(&b).ok()) {
            let seen = std::fs::read_to_string(appdata("update_announced.txt")).unwrap_or_default();
            if let Some(latest) = update_to_announce(&info, &seen) {
                let current = info["current"].as_str().unwrap_or("");
                let shown = app.notification().builder()
                    .title(format!("Ultra Suite {latest} is available"))
                    .body(format!("You have {current}. Choose Update in the Dock menu or in PC Sentinel. Updating takes about a minute."))
                    .show();
                if shown.is_ok() { let _ = std::fs::write(appdata("update_announced.txt"), &latest); }
            }
        }
        std::thread::sleep(Duration::from_secs(6 * 3600));
    }
}

#[cfg(test)]
mod update_notice_tests {
    use super::update_to_announce;
    use serde_json::json;
    #[test]
    fn announces_each_new_version_once() {
        let info = json!({"available": true, "latest": "2.2.12", "current": "2.2.11", "asset": "https://x/a.exe"});
        assert_eq!(update_to_announce(&info, ""), Some("2.2.12".into()));
        assert_eq!(update_to_announce(&info, "2.2.12
"), None, "already announced");
        assert_eq!(update_to_announce(&info, "2.2.11"), Some("2.2.12".into()), "an older announcement does not block a newer one");
    }
    #[test]
    fn stays_quiet_without_a_usable_newer_release() {
        assert_eq!(update_to_announce(&json!({"available": false, "latest": "2.2.12", "asset": "a"}), ""), None);
        assert_eq!(update_to_announce(&json!({"available": true, "latest": "2.2.12", "asset": null}), ""), None);
        assert_eq!(update_to_announce(&json!({"available": true, "latest": "2.2.12", "asset": ""}), ""), None);
        assert_eq!(update_to_announce(&json!({"available": true, "latest": "", "asset": "a"}), ""), None);
        assert_eq!(update_to_announce(&json!({"error": "ConnectError"}), ""), None);
    }
}

/// Keep the background services running 24/7: if the core ever stops (crash, killed), start it again.
fn core_supervisor(app: AppHandle) {
    std::thread::sleep(Duration::from_secs(20));
    loop {
        if !(port_open(8081) && port_open(8084)) {
            println!("[supervisor] background services not answering: restarting them");
            ensure_core(&app);
        }
        std::thread::sleep(Duration::from_secs(15));
    }
}

/// Ask Windows to wake one minute before the alarm. Actual wake support depends on
/// hardware and the user's power policy; a successful API call is not proof of wake.
#[cfg(windows)]
fn alarm_wake_timer() {
    use windows::Win32::System::Threading::{CancelWaitableTimer, CreateWaitableTimerW, SetWaitableTimer};
    let Ok(timer) = (unsafe { CreateWaitableTimerW(None, true, None) }) else { return };
    let mut armed: Option<i64> = None;
    loop {
        let body = http_get(8081, "/api/next");
        match wake::decide(wake::observe(body.as_deref()), wake::now_filetime(), armed) {
            wake::Action::Arm(due) => {
                // Positive FILETIME is an absolute UTC deadline, including time
                // spent asleep. Relative timers on Windows 8+ pause during sleep.
                if unsafe { SetWaitableTimer(timer, &due, 0, None, None, true) }.is_ok() {
                    armed = Some(due);
                    println!("[wake] absolute wake request armed");
                }
            }
            wake::Action::Cancel => {
                if unsafe { CancelWaitableTimer(timer) }.is_ok() { armed = None; }
            }
            wake::Action::Keep => {}
        }
        std::thread::sleep(Duration::from_secs(5));
    }
}

/// While an alarm rings, the PC must not drift back to sleep (after a timer wake Windows sleeps again within
/// about two minutes if nobody touches it) and the screen should stay on.
#[cfg(windows)]
fn keep_awake(on: bool) {
    use windows::Win32::System::Power::{SetThreadExecutionState, ES_CONTINUOUS, ES_DISPLAY_REQUIRED, ES_SYSTEM_REQUIRED};
    unsafe { SetThreadExecutionState(if on { ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED } else { ES_CONTINUOUS }); }
}

/// When an alarm rings, bring the alarm window in front of everything.
fn alarm_watch(app: AppHandle) {
    let mut ringing = false;
    loop {
        std::thread::sleep(Duration::from_millis(1000));
        let now = http_get(8081, "/api/ring").map(|b| b.replace(' ', "").contains("\"is_ringing\":true")).unwrap_or(false);
        #[cfg(windows)]
        if now != ringing { keep_awake(now); }
        // Window changes always run on the main thread (see taskbar_tracker for why).
        if now && !ringing {
            let a = app.clone();
            let _ = app.run_on_main_thread(move || {
                open_view(&a, "alarm");
                if let Some(w) = a.get_webview_window("alarm") {
                    let _ = w.unminimize();
                    let _ = w.set_always_on_top(true);  // above everything even if Windows refuses it focus
                    let _ = w.set_focus();
                    #[cfg(windows)]
                    if let Ok(h) = w.hwnd() { activate(h.0 as isize); }  // the Alt-tap route that also wins focus
                }
            });
        } else if !now && ringing {
            let a = app.clone();
            let _ = app.run_on_main_thread(move || {
                if let Some(w) = a.get_webview_window("alarm") { let _ = w.set_always_on_top(false); }
            });
        }
        ringing = now;
    }
}

// ----------------------------------------------------------------------------- strip placement
/// Keeps the strip next to the last taskbar icon. Runs on its own thread, so it must never wait on the main
/// thread: it uses only plain Win32 calls that return at once (SWP_ASYNCWINDOWPOS, ShowWindowAsync, DPI and
/// monitor queries). It used to call the window framework's size/position/monitor functions from here; those
/// hold the window's internal lock while waiting for the main thread, and when the main thread needed the same
/// lock (answering a UI Automation request) both waited forever: the 2026-11-11 freezes.
#[cfg(windows)]
fn taskbar_tracker(_app: AppHandle) {
    use windows::core::w;
    use windows::Win32::Foundation::{HWND, RECT};
    use windows::Win32::Graphics::Gdi::{GetMonitorInfoW, MonitorFromWindow, MONITORINFO, MONITOR_DEFAULTTOPRIMARY};
    use windows::Win32::System::Com::*;
    use windows::Win32::System::Variant::VARIANT;
    use windows::Win32::UI::Accessibility::*;
    use windows::Win32::UI::HiDpi::GetDpiForWindow;
    use windows::Win32::UI::WindowsAndMessaging::*;
    unsafe {
        let _ = CoInitializeEx(None, COINIT_MULTITHREADED);
        let uia: Option<IUIAutomation> = CoCreateInstance(&CUIAutomation, None, CLSCTX_INPROC_SERVER).ok();
        // Latest UI Automation reading of the taskbar: Start, the native buttons and the notification area
        let mut geom = dock_layout::TaskbarGeom::default();
        let (mut buttons, mut button_w) = (0usize, 0f64);
        let mut dock = dock_layout::Assessor::default();
        let started = Instant::now();
        let mut last_uia = Instant::now() - Duration::from_secs(10);
        let mut last = (i32::MIN, i32::MIN, 0u32, 0u32, false);
        loop {
            std::thread::sleep(Duration::from_millis(80));
            let sh = STRIP_HWND.load(Ordering::Relaxed);
            if sh == 0 { continue; }
            let hwnd = HWND(sh as _);
            let Ok(tb) = FindWindowW(w!("Shell_TrayWnd"), None) else { continue };
            let mut r = RECT::default();
            if GetWindowRect(tb, &mut r).is_err() { continue; }
            let mut mi = MONITORINFO { cbSize: std::mem::size_of::<MONITORINFO>() as u32, ..Default::default() };
            if !GetMonitorInfoW(MonitorFromWindow(tb, MONITOR_DEFAULTTOPRIMARY), &mut mi).as_bool() { continue; }
            let dpi = GetDpiForWindow(hwnd);
            let scale = if dpi > 0 { dpi as f64 / 96.0 } else { 1.0 };
            let mon_bottom = mi.rcMonitor.bottom;
            let tb_visible = r.top < mon_bottom - 6 && (r.bottom - r.top) > 20;
            // Full screen means: the window in front covers the whole monitor the taskbar is on. Windows' own
            // "busy" state can't be used: a full-screen picture on ANY screen (a USB sensor LCD, a second
            // monitor) sets it, which once hid the dock for good while its folders were still holding apps.
            let fullscreen = foreground_covers_taskbar_monitor(tb);
            TB_VISIBLE.store(tb_visible, Ordering::Relaxed);
            if tb_visible && last_uia.elapsed() > Duration::from_millis(350) {
                last_uia = Instant::now();
                if let Some(u) = uia.as_ref() {
                    let res = (|| -> windows::core::Result<(dock_layout::TaskbarGeom, usize, f64)> {
                        let el = u.ElementFromHandle(tb)?;
                        let mut g = dock_layout::TaskbarGeom { left: r.left, right: r.right, scale, ..Default::default() };
                        let sc = u.CreatePropertyCondition(UIA_AutomationIdPropertyId, &VARIANT::from("StartButton"))?;
                        if let Ok(rc) = el.FindFirst(TreeScope_Descendants, &sc).and_then(|e| e.CurrentBoundingRectangle()) {
                            g.start_left = Some(rc.left); g.start_right = Some(rc.right);
                        }
                        // right edge of the last native taskbar button
                        let (mut n, mut total) = (0usize, 0i64);
                        let bc = u.CreatePropertyCondition(UIA_ClassNamePropertyId, &VARIANT::from("Taskbar.TaskListButtonAutomationPeer"))?;
                        if let Ok(arr) = el.FindAll(TreeScope_Descendants, &bc) {
                            for i in 0..arr.Length().unwrap_or(0) {
                                if let Ok(rc) = arr.GetElement(i).and_then(|b| b.CurrentBoundingRectangle()) {
                                    if rc.right - rc.left > 4 {
                                        g.buttons_right = Some(g.buttons_right.map_or(rc.right, |v| v.max(rc.right)));
                                        n += 1; total += (rc.right - rc.left) as i64;
                                    }
                                }
                            }
                        }
                        // the notification area: "Show hidden icons", tray icons, and the Widgets button when the
                        // taskbar is left-aligned (it then sits next to the tray)
                        for id in ["SystemTrayIcon", "NotifyItemIcon", "WidgetsButton"] {
                            let c = u.CreatePropertyCondition(UIA_AutomationIdPropertyId, &VARIANT::from(id))?;
                            if let Ok(rc) = el.FindFirst(TreeScope_Descendants, &c).and_then(|e| e.CurrentBoundingRectangle()) {
                                if rc.right - rc.left > 2 { g.obstacles.push(rc.left); }
                            }
                        }
                        let avg = if n > 0 { total as f64 / n as f64 } else { 44.0 * scale };
                        Ok((g, n, avg))
                    })();
                    if let Ok((g, n, avg)) = res { geom = g; buttons = n; button_w = avg; }
                }
            }
            let h = (r.bottom - r.top).max(1) as u32;
            // The legacy notification-area window is a second, cheap reading of where the tray starts.
            let mut g = dock_layout::TaskbarGeom { left: r.left, right: r.right, scale, ..geom.clone() };
            if let Ok(tn) = FindWindowExW(Some(tb), None, w!("TrayNotifyWnd"), None) {
                let mut tr = RECT::default();
                if GetWindowRect(tn, &mut tr).is_ok() && tr.right - tr.left > 4 { g.obstacles.push(tr.left); }
            }
            let span = dock_layout::free_span(&g);
            let room = span.width();
            let now_ms = started.elapsed().as_millis() as u64;
            let a = {
                let guard = CANDIDATES.lock().ok();
                let empty = Candidates::default();
                let c = guard.as_ref().and_then(|g| g.as_ref()).unwrap_or(&empty);
                // The situation: the apps, the taskbar and the tray, none of which change when the Dock hides or
                // restores buttons (visible plus hidden buttons stays the same)
                use std::hash::{Hash, Hasher};
                let mut hs = std::collections::hash_map::DefaultHasher::new();
                c.groups.hash(&mut hs); (r.left, r.right, g.obstacles.iter().min().copied(), dpi, buttons + c.hidden_apps).hash(&mut hs);
                dock.assess(&dock_layout::Reading { groups: &c.groups, pending: &c.pending, room: room as f64, buttons, button_w,
                    centered: dock_layout::is_centered(&g), scale, page_base: STRIP_BASE_W.load(Ordering::Relaxed) as f64,
                    sig: hs.finish(), now_ms })
            };
            AVAIL_CSS.store((room as f64 / scale).floor() as u32, Ordering::Relaxed);
            LAYOUT_CSS.store(a.layout_room as u32, Ordering::Relaxed);
            let fits = a.fits;
            let x = span.x;
            let requested = (STRIP_W.load(Ordering::Relaxed) as f64 * scale).round() as i32;
            let w = requested.min(room).max(0) as u32;
            // A strip that cannot show even its menu is hidden rather than drawn as a sliver beside the tray,
            // unless room is on its way (the fit check's short grace while buttons are being hidden).
            let show = tb_visible && !fullscreen && !STRIP_HIDDEN.load(Ordering::Relaxed) && w > 8 && (w as i32 >= requested || fits);
            STRIP_FITS.store(show && fits, Ordering::Relaxed);
            STRIP_SHOWN.store(show, Ordering::Relaxed);
            let state = (x, r.top, w, h, show);
            if state == last { continue; }
            last = state;
            if show {
                // ASYNCWINDOWPOS: post the move to the window's own thread instead of waiting for it
                let _ = SetWindowPos(hwnd, Some(HWND_TOPMOST), x, r.top, w as i32, h as i32,
                                     SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_ASYNCWINDOWPOS);
            } else {
                let _ = ShowWindowAsync(hwnd, SW_HIDE);
            }
        }
    }
}

#[cfg(windows)]
unsafe fn foreground_covers_taskbar_monitor(tb: windows::Win32::Foundation::HWND) -> bool {
    use windows::Win32::Foundation::RECT;
    use windows::Win32::Graphics::Gdi::{GetMonitorInfoW, MonitorFromWindow, MONITORINFO, MONITOR_DEFAULTTONEAREST};
    use windows::Win32::UI::WindowsAndMessaging::*;
    let fg = GetForegroundWindow();
    if fg.is_invalid() || fg == tb { return false; }
    let mut pid = 0u32;
    GetWindowThreadProcessId(fg, Some(&mut pid));
    if pid == std::process::id() { return false; }
    let mut cls = [0u16; 64];
    let n = GetClassNameW(fg, &mut cls) as usize;
    let class = String::from_utf16_lossy(&cls[..n.min(cls.len())]);
    if ["Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"].contains(&class.as_str()) { return false; }
    let mon = MonitorFromWindow(fg, MONITOR_DEFAULTTONEAREST);
    if mon != MonitorFromWindow(tb, MONITOR_DEFAULTTONEAREST) { return false; }
    let mut mi = MONITORINFO { cbSize: std::mem::size_of::<MONITORINFO>() as u32, ..Default::default() };
    if !GetMonitorInfoW(mon, &mut mi).as_bool() { return false; }
    let mut r = RECT::default();
    if GetWindowRect(fg, &mut r).is_err() { return false; }
    let m = mi.rcMonitor;
    r.left <= m.left && r.top <= m.top && r.right >= m.right && r.bottom >= m.bottom
}

#[cfg(windows)]
fn make_noactivate(win: &WebviewWindow) {
    use windows::Win32::Foundation::HWND;
    use windows::Win32::UI::WindowsAndMessaging::*;
    if let Ok(h) = win.hwnd() {
        let hwnd = HWND(h.0 as _);
        unsafe { let ex = GetWindowLongPtrW(hwnd, GWL_EXSTYLE); SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex | (WS_EX_NOACTIVATE.0 | WS_EX_TOOLWINDOW.0) as isize); }
    }
}

fn set_autostart() {
    if let Ok(exe) = std::env::current_exe() {
        let val = format!("\"{}\" --background", exe.display());
        let mut cmd = Command::new("reg.exe");
        cmd.args(["add", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run", "/v", "UltraSuite", "/t", "REG_SZ", "/d", &val, "/f"]);
        #[cfg(windows)]
        no_window(&mut cmd);
        let _ = cmd.status();
    }
}

// ----------------------------------------------------------------------------- dock on/off (tray icon)
/// The "Dock" switch in the tray icon's menu (Windows' hidden-icons area). Off: every app goes back to the
/// normal taskbar and the strip disappears. On: folders come back. Remembered in %APPDATA%\UltraSuite\dock.txt.
static DOCK_ITEM: Mutex<Option<tauri::menu::CheckMenuItem<tauri::Wry>>> = Mutex::new(None);

fn dock_saved_on() -> bool {
    std::fs::read_to_string(appdata("dock.txt")).map(|s| s.trim() != "off").unwrap_or(true)
}

fn set_dock_enabled(on: bool) {
    ORGANIZE.store(on, Ordering::Relaxed);    // off: the organizer gives every button back
    STRIP_HIDDEN.store(!on, Ordering::Relaxed);
    let _ = std::fs::write(appdata("dock.txt"), if on { "on" } else { "off" });
    if let Ok(g) = DOCK_ITEM.lock() { if let Some(item) = g.as_ref() { let _ = item.set_checked(on); } }
}

fn build_tray(app: &tauri::App) -> tauri::Result<()> {
    use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem};
    use tauri::tray::TrayIconBuilder;
    let dock = CheckMenuItem::with_id(app, "dock", "Dock", true, dock_saved_on(), None::<&str>)?;
    let hub = MenuItem::with_id(app, "hub", "Open Ultra Suite", true, None::<&str>)?;
    let sentinel = MenuItem::with_id(app, "sentinel", "Open PC Sentinel", true, None::<&str>)?;
    let alarm = MenuItem::with_id(app, "alarm", "Open Code Red Alarm", true, None::<&str>)?;
    let quit_item = MenuItem::with_id(app, "quit", "Quit Ultra Suite", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&dock, &PredefinedMenuItem::separator(app)?, &hub, &sentinel, &alarm,
                                       &PredefinedMenuItem::separator(app)?, &quit_item])?;
    if let Ok(mut g) = DOCK_ITEM.lock() { *g = Some(dock); }
    TrayIconBuilder::with_id("ultra-suite")
        .icon(tauri::image::Image::from_bytes(include_bytes!("../icons/apps/dock.png"))?)
        .tooltip("Ultra Suite")
        .menu(&menu)
        .show_menu_on_left_click(true)
        .on_menu_event(|app, event| match event.id().as_ref() {
            "dock" => set_dock_enabled(STRIP_HIDDEN.load(Ordering::Relaxed)),  // hidden now: turn it on, and the reverse
            "hub" => open_view(app, "hub"),
            "sentinel" => open_view(app, "sentinel"),
            "alarm" => open_view(app, "alarm"),
            "quit" => quit(app.clone()),
            _ => {}
        })
        .build(app)?;
    Ok(())
}

fn handle_args(app: &AppHandle, args: &[String]) {
    let get = |flag: &str| args.iter().position(|a| a == flag).and_then(|i| args.get(i + 1)).cloned();
    if args.iter().any(|a| a == "--restore-taskbar") {
        set_dock_enabled(false);
        #[cfg(windows)]
        restore_all();
        return; // restore buttons without quitting the core or touching alarms
    }
    if let Some(v) = get("--open") { open_view(app, &v); }
    if let Some(k) = get("--event") { std::thread::spawn(move || { post_event(&k, None, None); }); }
    if args.iter().any(|a| a == "--show-dock") { set_dock_enabled(true); }
    if args.iter().any(|a| a == "--quit") {
        // clean shutdown (puts every tucked-away taskbar button back first)
        #[cfg(windows)]
        restore_all();
        stop_core();
        app.exit(0);
    }
}

#[cfg(windows)]
fn restore_from_last_run() {
    use windows::Win32::System::Com::*;
    unsafe { let _ = CoInitializeEx(None, COINIT_APARTMENTTHREADED); }
    if let Some(t) = taskbar_list() { recovery::recover_previous(&t); }
}

fn run_survey() {
    tauri::Builder::default()
        .setup(|app| {
            WebviewWindowBuilder::new(app, "survey", WebviewUrl::App("index.html?view=survey".into()))
                .title("Uninstall Ultra Suite").inner_size(520.0, 640.0).resizable(false).center().always_on_top(true).build()?;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![send_event, quit])
        .run(tauri::generate_context!())
        .expect("survey");
}

fn main() {
    // Phase 1 declares Dev identities, but legacy runtime integration is pending.
    if cfg!(feature = "dev-edition") && !edition::runtime_ready() {
        eprintln!("Ultra Suite (Dev): Dev runtime integration not ready; no runtime was started.");
        return;
    }
    let args: Vec<String> = std::env::args().collect();
    if args.iter().any(|a| a == "--uninstall-survey") { run_survey(); return; }
    #[cfg(windows)]
    if let Some(i) = args.iter().position(|a| a == "--guard") {
        if let (Some(pid), Some(started)) = (args.get(i + 1).and_then(|p| p.parse().ok()), args.get(i + 2).and_then(|p| p.parse().ok())) {
            run_guard(recovery::Owner { pid, started });
        }
        return;
    }
    #[cfg(windows)]
    wait_for_old_copy(&args);
    tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_single_instance::init(|app, argv, _cwd| {
            let rest: Vec<String> = argv.into_iter().skip(1).collect();
            if rest.is_empty() { open_view(app, "hub"); } else { handle_args(app, &rest); }
        }))
        .setup(move |app| {
            let handle = app.handle().clone();
            let strip = WebviewWindowBuilder::new(app, "taskbar", WebviewUrl::App("index.html?view=strip".into()))
                .title("Ultra Dock").decorations(false).transparent(true).always_on_top(true).skip_taskbar(true)
                .resizable(false).shadow(false).focused(false).visible(false).inner_size(60.0, 48.0)
                .on_page_load(|_, payload| {
                    let mut url = payload.url().clone();
                    url.set_path("/starting.html");url.set_query(None);url.set_fragment(None);
                    let _ = STARTUP_URL.set(url);
                }).build()?;
            #[cfg(windows)]
            make_noactivate(&strip);
            if let Ok(h) = strip.hwnd() { STRIP_HWND.store(h.0 as isize, Ordering::Relaxed); }
            if !dock_saved_on() { ORGANIZE.store(false, Ordering::Relaxed); STRIP_HIDDEN.store(true, Ordering::Relaxed); }
            if let Err(e) = build_tray(app) { println!("[tray] not available: {e}"); }
            // only the installed app registers itself to start with Windows (a dev build must never take over that entry)
            if !cfg!(debug_assertions) { set_autostart(); }
            #[cfg(windows)]
            if !cfg!(debug_assertions) { std::thread::spawn(fix_shortcut_ids); }
            let h1 = handle.clone();
            std::thread::spawn(move || ensure_core(&h1));
            #[cfg(windows)]
            {
                let h2 = handle.clone();
                std::thread::spawn(move || taskbar_tracker(h2));
                spawn_guard();
                std::thread::spawn(organizer_loop);
                std::thread::spawn(watchdog);
            }
            let h3 = handle.clone();
            std::thread::spawn(move || alarm_watch(h3));
            let h4 = handle.clone();
            std::thread::spawn(move || core_supervisor(h4));
            let h5 = handle.clone();
            std::thread::spawn(move || update_notifier(h5));
            #[cfg(windows)]
            std::thread::spawn(alarm_wake_timer);
            std::thread::spawn(|| loop {
                let stamp = appdata("last_heartbeat.txt");
                let now = SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
                let last: u64 = std::fs::read_to_string(&stamp).ok().and_then(|s| s.trim().parse().ok()).unwrap_or(0);
                if now.saturating_sub(last) >= 86_400 && post_event("heartbeat", None, None) { let _ = std::fs::write(&stamp, now.to_string()); }
                std::thread::sleep(Duration::from_secs(3600));
            });
            let rest: Vec<String> = args.iter().skip(1).cloned().collect();
            if !rest.iter().any(|a| a == "--background") {
                if rest.is_empty() { open_view(&handle, "hub"); } else { handle_args(&handle, &rest); }
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![get_windows, activate, set_organize, get_icon, set_strip_width, open_config, quit, open_app_view, send_event, hub_info, hub_set_dock])
        .build(tauri::generate_context!())
        .expect("error while building Ultra Suite")
        .run(|_app, e| {
            match e {
                tauri::RunEvent::ExitRequested { api, code, .. } => { if code.is_none() { api.prevent_exit(); } }
                tauri::RunEvent::Exit => {
                    #[cfg(windows)]
                    restore_all();
                }
                _ => {}
            }
        });
}
