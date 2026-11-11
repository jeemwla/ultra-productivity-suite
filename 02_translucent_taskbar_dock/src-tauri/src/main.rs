#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

//! Ultra Suite desktop app.
//! - Ultra Dock ("taskbar" window): a taskbar organizer. It works on the apps you have OPEN.
//!   Apps in the keep list stay as normal taskbar buttons; every other open app's taskbar button is
//!   tucked away (ITaskbarList::DeleteTab) and grouped by category in a strip placed right after the
//!   last taskbar icon. Click a group to slide its apps open, click an app to switch to it.
//!   Every hidden button is restored when the organizer is turned off or the app quits.
//! - "alarm" / "sentinel" / "insights": app windows served by the bundled core.
//! CLI: --open alarm|sentinel|insights  --background  --show-dock  --deck  --uninstall-survey  --event <kind>

use base64::Engine;
use serde::Serialize;
use serde_json::{json, Value};
use std::collections::{HashMap, HashSet};
use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant, SystemTime};
use tauri::{AppHandle, Manager, PhysicalPosition, PhysicalSize, WebviewUrl, WebviewWindow, WebviewWindowBuilder, WindowEvent};

const DEFAULT_ORG: &str = include_str!("../../config/organizer.json");
const COLLECTOR: &str = env!("ULTRASUITE_COLLECTOR_URL");

static STRIP_W: AtomicU32 = AtomicU32::new(60);
static STRIP_HIDDEN: AtomicBool = AtomicBool::new(false);
static ORGANIZE: AtomicBool = AtomicBool::new(true);
static CORE: Mutex<Option<Child>> = Mutex::new(None);
static ICONS: Mutex<Option<HashMap<String, String>>> = Mutex::new(None);
static HIDDEN: Mutex<Option<HashSet<isize>>> = Mutex::new(None);
static SNAPSHOT: Mutex<Vec<WinInfo>> = Mutex::new(Vec::new());

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
        let len = GetWindowTextLengthW(h);
        if len <= 0 { return true.into(); }
        let mut buf = vec![0u16; (len + 1) as usize];
        GetWindowTextW(h, &mut buf);
        let title = String::from_utf16_lossy(&buf[..len as usize]);
        let mut pid = 0u32;
        GetWindowThreadProcessId(h, Some(&mut pid));
        if pid == std::process::id() { return true.into(); }
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
    use windows::Win32::Foundation::HWND;
    use windows::Win32::System::Com::*;
    unsafe { let _ = CoInitializeEx(None, COINIT_APARTMENTTHREADED); }
    let ids: Vec<isize> = HIDDEN.lock().ok().and_then(|mut g| g.as_mut().map(|s| s.drain().collect())).unwrap_or_default();
    if let Some(t) = taskbar_list() { for h in ids { unsafe { let _ = t.AddTab(HWND(h as _)); } } }
    let _ = std::fs::remove_file(appdata("hidden_tabs.txt"));
}

#[cfg(windows)]
fn organizer_loop() {
    use windows::Win32::Foundation::HWND;
    use windows::Win32::System::Com::*;
    use windows::Win32::UI::WindowsAndMessaging::{GetForegroundWindow, IsWindow};
    unsafe { let _ = CoInitializeEx(None, COINIT_APARTMENTTHREADED); }
    restore_from_last_run();
    let Some(tbl) = taskbar_list() else { return };
    let mut last_reapply = Instant::now();
    loop {
        std::thread::sleep(Duration::from_millis(700));
        let cfg = org_config();
        let wins = enum_app_windows();
        let fg = unsafe { GetForegroundWindow().0 as isize };
        let on = ORGANIZE.load(Ordering::Relaxed);
        let mut snap = vec![];
        {
            let mut g = HIDDEN.lock().unwrap();
            let hidden = g.get_or_insert_with(HashSet::new);
            hidden.retain(|h| unsafe { IsWindow(Some(HWND(*h as _))).as_bool() });
            let reapply = last_reapply.elapsed() > Duration::from_secs(4);
            for (h, title, exe) in &wins {
                let kept = is_kept(exe, &cfg);
                let hw = HWND(*h as _);
                if on && !kept {
                    // apps re-add their own button when they re-show a window, so re-apply now and then
                    if !hidden.contains(h) || reapply { unsafe { let _ = tbl.DeleteTab(hw); } hidden.insert(*h); }
                    snap.push(WinInfo { hwnd: *h, title: title.clone(), exe: exe.clone(), name: pretty_name(exe),
                                        category: categorize(exe, &cfg), active: *h == fg });
                } else if hidden.remove(h) {
                    unsafe { let _ = tbl.AddTab(hw); }
                }
            }
            if !on { for h in hidden.drain() { unsafe { let _ = tbl.AddTab(HWND(h as _)); } } }
            if reapply { last_reapply = Instant::now(); }
        }
        save_hidden_list();
        let order = &cfg.order;
        snap.sort_by(|a, b| (order.iter().position(|o| *o == a.category).unwrap_or(98), &a.name).cmp(&(order.iter().position(|o| *o == b.category).unwrap_or(98), &b.name)));
        if let Ok(mut s) = SNAPSHOT.lock() { *s = snap; }
    }
}

#[tauri::command]
fn get_windows() -> Value {
    let wins = SNAPSHOT.lock().map(|s| s.clone()).unwrap_or_default();
    json!({ "organize": ORGANIZE.load(Ordering::Relaxed), "windows": wins, "order": org_config().order })
}

#[tauri::command]
fn activate(hwnd: isize) {
    #[cfg(windows)]
    unsafe {
        use windows::Win32::Foundation::HWND;
        use windows::Win32::UI::Input::KeyboardAndMouse::{keybd_event, KEYBD_EVENT_FLAGS, KEYEVENTF_KEYUP, VK_MENU};
        use windows::Win32::UI::WindowsAndMessaging::*;
        let h = HWND(hwnd as _);
        if IsIconic(h).as_bool() { let _ = ShowWindow(h, SW_RESTORE); }
        // a quick Alt tap lets a background app hand focus to another window (standard Windows rule)
        keybd_event(VK_MENU.0 as u8, 0, KEYBD_EVENT_FLAGS(0), 0);
        keybd_event(VK_MENU.0 as u8, 0, KEYEVENTF_KEYUP, 0);
        let _ = SetForegroundWindow(h);
        SwitchToThisWindow(h, true);
    }
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

#[tauri::command]
async fn get_icon(target: String, hwnd: Option<isize>) -> Option<String> {
    if let Some(c) = ICONS.lock().ok().and_then(|m| m.as_ref().and_then(|m| m.get(&target).cloned())) { return Some(c); }
    let t = target.clone();
    #[cfg(windows)]
    let png = tauri::async_runtime::spawn_blocking(move || icon_png(&expand_env(&t), 64).or_else(|| hwnd.and_then(window_icon_png)))
        .await.ok().flatten()?;
    #[cfg(not(windows))]
    let png: Vec<u8> = { let _ = t; return None; };
    let url = format!("data:image/png;base64,{}", base64::engine::general_purpose::STANDARD.encode(png));
    if let Ok(mut m) = ICONS.lock() { m.get_or_insert_with(HashMap::new).insert(target, url.clone()); }
    Some(url)
}

#[tauri::command]
fn set_strip_width(width: f64) { STRIP_W.store(width.max(20.0).min(1800.0) as u32, Ordering::Relaxed); }

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
fn send_event(kind: String, reason: Option<String>, message: Option<String>) -> bool { post_event(&kind, reason, message) }

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
fn open_view(app: &AppHandle, name: &str) {
    let (title, url, w, h) = match name {
        "alarm" => ("Code Red Alarm", "http://127.0.0.1:8081/", 1360.0, 920.0),
        "sentinel" => ("PC Sentinel", "http://127.0.0.1:8084/", 1440.0, 960.0),
        "insights" => ("Ultra Suite Insights", "http://127.0.0.1:8301/", 1440.0, 960.0),
        _ => return,
    };
    if let Some(win) = app.get_webview_window(name) { let _ = win.unminimize(); let _ = win.show(); let _ = win.set_focus(); return; }
    ensure_core(app);
    if let Ok(win) = WebviewWindowBuilder::new(app, name, WebviewUrl::External(url.parse().unwrap()))
        .title(title).inner_size(w, h).min_inner_size(900.0, 620.0).center().visible(true).build() {
        let w2 = win.clone();
        win.on_window_event(move |e| { if let WindowEvent::CloseRequested { api, .. } = e { api.prevent_close(); let _ = w2.hide(); } });
        let _ = win.set_focus();
    }
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

/// When an alarm rings, bring the alarm window in front of everything.
fn alarm_watch(app: AppHandle) {
    let mut ringing = false;
    loop {
        std::thread::sleep(Duration::from_millis(1000));
        let now = http_get(8081, "/api/ring").map(|b| b.replace(' ', "").contains("\"is_ringing\":true")).unwrap_or(false);
        if now && !ringing {
            let a = app.clone();
            let _ = app.run_on_main_thread(move || {
                open_view(&a, "alarm");
                if let Some(w) = a.get_webview_window("alarm") { let _ = w.set_always_on_top(true); let _ = w.set_focus(); }
            });
        } else if !now && ringing {
            if let Some(w) = app.get_webview_window("alarm") { let _ = w.set_always_on_top(false); }
        }
        ringing = now;
    }
}

// ----------------------------------------------------------------------------- strip placement
#[cfg(windows)]
fn taskbar_tracker(app: AppHandle) {
    use windows::core::w;
    use windows::Win32::Foundation::{HWND, RECT};
    use windows::Win32::System::Com::*;
    use windows::Win32::System::Variant::VARIANT;
    use windows::Win32::UI::Accessibility::*;
    use windows::Win32::UI::Shell::*;
    use windows::Win32::UI::WindowsAndMessaging::*;
    unsafe {
        let _ = CoInitializeEx(None, COINIT_MULTITHREADED);
        let uia: Option<IUIAutomation> = CoCreateInstance(&CUIAutomation, None, CLSCTX_INPROC_SERVER).ok();
        let (mut icons_right, mut tray_left): (Option<i32>, Option<i32>) = (None, None);
        let mut last_uia = Instant::now() - Duration::from_secs(10);
        let mut last = (i32::MIN, i32::MIN, 0u32, 0u32, false);
        loop {
            std::thread::sleep(Duration::from_millis(80));
            let Some(strip) = app.get_webview_window("taskbar") else { continue };
            let Ok(tb) = FindWindowW(w!("Shell_TrayWnd"), None) else { continue };
            let mut r = RECT::default();
            if GetWindowRect(tb, &mut r).is_err() { continue; }
            let Some(mon) = strip.primary_monitor().ok().flatten() else { continue };
            let scale = mon.scale_factor();
            let mon_bottom = mon.position().y + mon.size().height as i32;
            let tb_visible = r.top < mon_bottom - 6 && (r.bottom - r.top) > 20;
            let qs = SHQueryUserNotificationState().unwrap_or(QUNS_ACCEPTS_NOTIFICATIONS);
            let fullscreen = qs == QUNS_RUNNING_D3D_FULL_SCREEN || qs == QUNS_PRESENTATION_MODE || qs == QUNS_BUSY;
            if tb_visible && last_uia.elapsed() > Duration::from_millis(350) {
                last_uia = Instant::now();
                if let Some(u) = uia.as_ref() {
                    let res = (|| -> windows::core::Result<(i32, Option<i32>)> {
                        let el = u.ElementFromHandle(tb)?;
                        // right edge of the last taskbar button (or of Start if no apps are shown)
                        let mut right = {
                            let sc = u.CreatePropertyCondition(UIA_AutomationIdPropertyId, &VARIANT::from("StartButton"))?;
                            el.FindFirst(TreeScope_Descendants, &sc)?.CurrentBoundingRectangle()?.right
                        };
                        let bc = u.CreatePropertyCondition(UIA_ClassNamePropertyId, &VARIANT::from("Taskbar.TaskListButtonAutomationPeer"))?;
                        if let Ok(arr) = el.FindAll(TreeScope_Descendants, &bc) {
                            for i in 0..arr.Length().unwrap_or(0) {
                                if let Ok(b) = arr.GetElement(i) { if let Ok(rc) = b.CurrentBoundingRectangle() { if rc.right > right && rc.right - rc.left > 4 { right = rc.right; } } }
                            }
                        }
                        let tc = u.CreatePropertyCondition(UIA_AutomationIdPropertyId, &VARIANT::from("SystemTrayIcon"))?;
                        let tray = el.FindFirst(TreeScope_Descendants, &tc).ok().and_then(|t| t.CurrentBoundingRectangle().ok()).map(|rc| rc.left);
                        Ok((right, tray))
                    })();
                    if let Ok((rt, tl)) = res { icons_right = Some(rt); tray_left = tl; }
                }
            }
            let h = (r.bottom - r.top).max(1) as u32;
            let mut w = (STRIP_W.load(Ordering::Relaxed) as f64 * scale).round() as u32;
            let gap = (2.0 * scale).round() as i32;
            let x = icons_right.map(|v| v + gap).unwrap_or(r.left + (r.right - r.left) / 2);
            let limit = tray_left.unwrap_or(r.right - (480.0 * scale) as i32) - (8.0 * scale) as i32;
            if x + w as i32 > limit { w = (limit - x).max(0) as u32; }
            let show = tb_visible && !fullscreen && !STRIP_HIDDEN.load(Ordering::Relaxed) && w > 8;
            let state = (x, r.top, w, h, show);
            if state == last { continue; }
            last = state;
            let hwnd: HWND = match strip.hwnd() { Ok(hh) => HWND(hh.0 as _), Err(_) => continue };
            if show {
                let _ = strip.set_size(PhysicalSize::new(w, h));
                let _ = strip.set_position(PhysicalPosition::new(x, r.top));
                let _ = ShowWindow(hwnd, SW_SHOWNOACTIVATE);
                let _ = SetWindowPos(hwnd, Some(HWND_TOPMOST), 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE);
            } else {
                let _ = ShowWindow(hwnd, SW_HIDE);
            }
        }
    }
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

fn handle_args(app: &AppHandle, args: &[String]) {
    let get = |flag: &str| args.iter().position(|a| a == flag).and_then(|i| args.get(i + 1)).cloned();
    if let Some(v) = get("--open") { open_view(app, &v); }
    if let Some(k) = get("--event") { std::thread::spawn(move || { post_event(&k, None, None); }); }
    if args.iter().any(|a| a == "--deck") {
        if let Some(exe) = core_exe(app) {
            let mut cmd = Command::new(exe);
            cmd.args(["deck", "install", "--restart"]);
            #[cfg(windows)]
            no_window(&mut cmd);
            let _ = cmd.spawn();
        }
    }
    if args.iter().any(|a| a == "--show-dock") { STRIP_HIDDEN.store(false, Ordering::Relaxed); ORGANIZE.store(true, Ordering::Relaxed); }
    if args.iter().any(|a| a == "--quit") {
        // clean shutdown (puts every tucked-away taskbar button back first)
        #[cfg(windows)]
        restore_all();
        stop_core();
        app.exit(0);
    }
}

/// Crash safety: remember which taskbar buttons we tucked away, so a restart can put them back
/// even if the previous run was killed before it could restore them.
fn save_hidden_list() {
    if let Ok(g) = HIDDEN.lock() {
        let ids: Vec<String> = g.as_ref().map(|s| s.iter().map(|h| h.to_string()).collect()).unwrap_or_default();
        let _ = std::fs::write(appdata("hidden_tabs.txt"), ids.join("\n"));
    }
}

#[cfg(windows)]
fn restore_from_last_run() {
    use windows::Win32::Foundation::HWND;
    use windows::Win32::System::Com::*;
    use windows::Win32::UI::WindowsAndMessaging::IsWindow;
    let Ok(s) = std::fs::read_to_string(appdata("hidden_tabs.txt")) else { return };
    unsafe { let _ = CoInitializeEx(None, COINIT_APARTMENTTHREADED); }
    if let Some(t) = taskbar_list() {
        for h in s.lines().filter_map(|l| l.trim().parse::<isize>().ok()) {
            if unsafe { IsWindow(Some(HWND(h as _))).as_bool() } { unsafe { let _ = t.AddTab(HWND(h as _)); } }
        }
    }
    let _ = std::fs::remove_file(appdata("hidden_tabs.txt"));
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
    let args: Vec<String> = std::env::args().collect();
    if args.iter().any(|a| a == "--uninstall-survey") { run_survey(); return; }
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, argv, _cwd| {
            let rest: Vec<String> = argv.into_iter().skip(1).collect();
            if rest.is_empty() { open_view(app, "sentinel"); } else { handle_args(app, &rest); }
        }))
        .setup(move |app| {
            let handle = app.handle().clone();
            let strip = WebviewWindowBuilder::new(app, "taskbar", WebviewUrl::App("index.html?view=strip".into()))
                .title("Ultra Dock").decorations(false).transparent(true).always_on_top(true).skip_taskbar(true)
                .resizable(false).shadow(false).focused(false).visible(false).inner_size(60.0, 48.0).build()?;
            #[cfg(windows)]
            make_noactivate(&strip);
            set_autostart();
            let h1 = handle.clone();
            std::thread::spawn(move || ensure_core(&h1));
            #[cfg(windows)]
            {
                let h2 = handle.clone();
                std::thread::spawn(move || taskbar_tracker(h2));
                std::thread::spawn(organizer_loop);
            }
            let h3 = handle.clone();
            std::thread::spawn(move || alarm_watch(h3));
            std::thread::spawn(|| loop {
                let stamp = appdata("last_heartbeat.txt");
                let now = SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
                let last: u64 = std::fs::read_to_string(&stamp).ok().and_then(|s| s.trim().parse().ok()).unwrap_or(0);
                if now.saturating_sub(last) >= 86_400 && post_event("heartbeat", None, None) { let _ = std::fs::write(&stamp, now.to_string()); }
                std::thread::sleep(Duration::from_secs(3600));
            });
            let rest: Vec<String> = args.iter().skip(1).cloned().collect();
            if !rest.iter().any(|a| a == "--background") {
                if rest.is_empty() { open_view(&handle, "sentinel"); } else { handle_args(&handle, &rest); }
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![get_windows, activate, set_organize, get_icon, set_strip_width, open_config, quit, open_app_view, send_event])
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
