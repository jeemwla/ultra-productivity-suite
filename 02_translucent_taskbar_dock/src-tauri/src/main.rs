#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

//! Ultra Suite desktop app.
//! - "taskbar": a strip that lives inside the Windows taskbar, just left of the Start button.
//!   Groups (AI, Chat, Games, Tools) slide open inline to show app icons and slide closed again.
//!   It follows the taskbar (auto-hide, moving Start button) and hides during fullscreen games.
//! - "alarm" / "sentinel": app windows for Code Red Alarm and PC Sentinel (served by the bundled core).
//! - Starts the bundled background core (ultrasuite-core.exe), starts at sign-in, single instance.
//! CLI: --open alarm|sentinel|insights  --background  --uninstall-survey  --event install|uninstall  --deck

use base64::Engine;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::{Child, Command};
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Manager, PhysicalPosition, PhysicalSize, WebviewUrl, WebviewWindow, WebviewWindowBuilder, WindowEvent};

const DEFAULT_CONFIG: &str = include_str!("../../config/categories.json");
const COLLECTOR: &str = env!("ULTRASUITE_COLLECTOR_URL");

static STRIP_W: AtomicU32 = AtomicU32::new(320); // CSS px wanted by the strip UI
static STRIP_HIDDEN: AtomicBool = AtomicBool::new(false);
static CORE: Mutex<Option<Child>> = Mutex::new(None);
static ICONS: Mutex<Option<HashMap<String, String>>> = Mutex::new(None);

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
                match std::env::var(name) {
                    Ok(v) => out.push_str(&v),
                    Err(_) => { out.push('%'); out.push_str(name); out.push('%'); }
                }
                rest = &after[end + 1..];
            }
            None => { out.push('%'); rest = after; }
        }
    }
    out.push_str(rest);
    out
}

fn appdata(sub: &str) -> PathBuf {
    let base = std::env::var("APPDATA").map(PathBuf::from).unwrap_or_else(|_| std::env::temp_dir());
    base.join("UltraSuite").join(sub)
}

fn config_path() -> PathBuf {
    // keep the old dock location working for existing users
    let old = std::env::var("APPDATA").map(PathBuf::from).unwrap_or_default().join("UltraSuiteDock").join("apps.json");
    if old.exists() { old } else { appdata("apps.json") }
}

fn read_config() -> Value {
    let path = config_path();
    if !path.exists() {
        if let Some(d) = path.parent() { let _ = std::fs::create_dir_all(d); }
        let _ = std::fs::write(&path, DEFAULT_CONFIG);
    }
    std::fs::read_to_string(&path).ok().and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_else(|| serde_json::from_str(DEFAULT_CONFIG).expect("bundled config"))
}

fn start_menu_apps() -> HashMap<String, String> {
    let mut map = HashMap::new();
    let mut cmd = Command::new("powershell.exe");
    cmd.args(["-NoProfile", "-NonInteractive", "-Command",
              "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | ConvertTo-Json -Compress"]);
    #[cfg(windows)]
    no_window(&mut cmd);
    if let Ok(out) = cmd.output() {
        if let Ok(v) = serde_json::from_slice::<Value>(&out.stdout) {
            let items = match v { Value::Array(a) => a, o => vec![o] };
            for it in items {
                if let (Some(n), Some(id)) = (it["Name"].as_str(), it["AppID"].as_str()) {
                    map.entry(n.to_lowercase()).or_insert_with(|| id.to_string());
                }
            }
        }
    }
    map
}

#[tauri::command]
fn get_config() -> Value {
    let mut cfg = read_config();
    let start = start_menu_apps();
    if let Some(cats) = cfg.get_mut("categories").and_then(|c| c.as_array_mut()) {
        for cat in cats.iter_mut() {
            if let Some(apps) = cat.get_mut("apps").and_then(|a| a.as_array_mut()) {
                for app in apps.iter_mut() {
                    let paths: Vec<String> = app["paths"].as_array().map(|a| a.iter().filter_map(|x| x.as_str().map(expand_env)).collect()).unwrap_or_default();
                    let mut found = paths.iter().find(|p| Path::new(p).exists()).cloned();
                    let key = app["start"].as_str().or(app["name"].as_str()).unwrap_or("").to_lowercase();
                    let start_item = start.get(&key).map(|id| if id.contains("://") { id.clone() } else { format!("shell:AppsFolder\\{}", id) });
                    if found.is_none() { found = start_item.clone(); }
                    // Icons look best from the Start menu entry (same icon Windows shows)
                    app["icon"] = start_item.clone().or(found.clone()).map(Value::String).unwrap_or(Value::Null);
                    let url_only = app["url"].as_str().map(|u| !u.is_empty()).unwrap_or(false) && app.get("paths").is_none() && app.get("start").is_none();
                    app["available"] = Value::Bool(found.is_some() || url_only);
                    app["resolved"] = found.map(Value::String).unwrap_or(Value::Null);
                }
            }
        }
    }
    cfg["config_path"] = json!(config_path().to_string_lossy());
    cfg
}

#[tauri::command]
fn launch(app: AppHandle, path: Option<String>, args: Option<Vec<String>>, url: Option<String>) -> Result<String, String> {
    // Suite apps open as Ultra Suite windows, not browser tabs
    if let Some(u) = url.as_deref() {
        if u.starts_with("http://localhost:8081") || u.starts_with("http://127.0.0.1:8081") { open_view(&app, "alarm"); return Ok("alarm".into()); }
        if u.starts_with("http://localhost:8084") || u.starts_with("http://127.0.0.1:8084") { open_view(&app, "sentinel"); return Ok("sentinel".into()); }
    }
    if let Some(p) = path.filter(|p| !p.is_empty()) {
        if p.starts_with("shell:") || p.contains("://") {
            return Command::new("explorer.exe").arg(&p).spawn().map(|_| "ok".into()).map_err(|e| e.to_string());
        }
        let p = expand_env(&p);
        if Path::new(&p).exists() {
            let mut cmd = Command::new(&p);
            if let Some(a) = args { cmd.args(a); }
            if let Some(d) = Path::new(&p).parent() { cmd.current_dir(d); }
            return cmd.spawn().map(|_| "ok".into()).map_err(|e| e.to_string());
        }
    }
    if let Some(u) = url.filter(|u| !u.is_empty()) {
        return Command::new("explorer.exe").arg(&u).spawn().map(|_| "ok".into()).map_err(|e| e.to_string());
    }
    Err("This app isn't installed on this PC".into())
}

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
        bi.bmiHeader.biWidth = w;
        bi.bmiHeader.biHeight = -h;
        bi.bmiHeader.biPlanes = 1;
        bi.bmiHeader.biBitCount = 32;
        bi.bmiHeader.biCompression = BI_RGB.0;
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
            enc.set_color(png::ColorType::Rgba);
            enc.set_depth(png::BitDepth::Eight);
            let mut wr = enc.write_header().ok()?;
            wr.write_image_data(&buf).ok()?;
        }
        Some(out)
    }
}

#[tauri::command]
async fn get_icon(target: String) -> Option<String> {
    if let Some(c) = ICONS.lock().ok().and_then(|m| m.as_ref().and_then(|m| m.get(&target).cloned())) { return Some(c); }
    let t = target.clone();
    #[cfg(windows)]
    let png = tauri::async_runtime::spawn_blocking(move || icon_png(&expand_env(&t), 64)).await.ok().flatten()?;
    #[cfg(not(windows))]
    let png: Vec<u8> = { let _ = t; return None; };
    let url = format!("data:image/png;base64,{}", base64::engine::general_purpose::STANDARD.encode(png));
    if let Ok(mut m) = ICONS.lock() { m.get_or_insert_with(HashMap::new).insert(target, url.clone()); }
    Some(url)
}

// ----------------------------------------------------------------------------- strip sizing
#[tauri::command]
fn set_strip_width(width: f64) { STRIP_W.store(width.max(40.0).min(1800.0) as u32, Ordering::Relaxed); }

#[tauri::command]
fn open_config() -> Result<(), String> {
    let _ = read_config();
    Command::new("notepad.exe").arg(config_path()).spawn().map(|_| ()).map_err(|e| e.to_string())
}

#[tauri::command]
fn quit(app: AppHandle) { stop_core(); app.exit(0); }

#[tauri::command]
fn open_app_view(app: AppHandle, name: String) { open_view(&app, &name); }

#[tauri::command]
fn send_event(kind: String, reason: Option<String>, message: Option<String>) -> bool { post_event(&kind, reason, message) }

fn post_event(kind: &str, reason: Option<String>, message: Option<String>) -> bool {
    if COLLECTOR.is_empty() { return false; }
    let consent = std::fs::read_to_string(appdata("consent.txt")).unwrap_or_default();
    if kind != "feedback" && kind != "uninstall" && consent.trim() != "yes" { return false; }
    let body = json!({"type": kind, "version": env!("CARGO_PKG_VERSION"), "reason": reason, "message": message,
                      "os": std::env::consts::OS, "id": anon_id()}).to_string().replace('\'', "''");
    let ps = format!("try {{ Invoke-RestMethod -Method Post -TimeoutSec 8 -Uri '{}' -ContentType 'application/json' -Body '{}' | Out-Null }} catch {{}}", COLLECTOR, body);
    let mut cmd = Command::new("powershell.exe");
    cmd.args(["-NoProfile", "-NonInteractive", "-Command", &ps]);
    #[cfg(windows)]
    no_window(&mut cmd);
    cmd.status().map(|s| s.success()).unwrap_or(false)
}

fn anon_id() -> String {
    let p = appdata("id.txt");
    if let Ok(s) = std::fs::read_to_string(&p) { if s.trim().len() >= 16 { return s.trim().to_string(); } }
    let id = format!("{:x}{:x}", std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0), std::process::id());
    if let Some(d) = p.parent() { let _ = std::fs::create_dir_all(d); }
    let _ = std::fs::write(&p, &id);
    id
}

// ----------------------------------------------------------------------------- views
fn open_view(app: &AppHandle, name: &str) {
    let (title, url, w, h) = match name {
        "alarm" => ("Code Red Alarm", "http://127.0.0.1:8081/", 1360.0, 920.0),
        "sentinel" => ("PC Sentinel", "http://127.0.0.1:8084/", 1440.0, 960.0),
        "insights" => ("Ultra Suite Insights", "http://127.0.0.1:8301/", 1440.0, 960.0),
        _ => return,
    };
    if let Some(win) = app.get_webview_window(name) {
        let _ = win.unminimize(); let _ = win.show(); let _ = win.set_focus();
        return;
    }
    ensure_core(app);
    let built = WebviewWindowBuilder::new(app, name, WebviewUrl::External(url.parse().unwrap()))
        .title(title).inner_size(w, h).min_inner_size(900.0, 620.0).center().visible(true).build();
    if let Ok(win) = built {
        let w2 = win.clone();
        win.on_window_event(move |e| {
            if let WindowEvent::CloseRequested { api, .. } = e { api.prevent_close(); let _ = w2.hide(); }
        });
        let _ = win.set_focus();
    }
}

// ----------------------------------------------------------------------------- core
fn port_open(port: u16) -> bool {
    TcpStream::connect_timeout(&format!("127.0.0.1:{}", port).parse().unwrap(), Duration::from_millis(250)).is_ok()
}

fn core_exe(app: &AppHandle) -> Option<PathBuf> {
    let mut c = vec![];
    if let Ok(r) = app.path().resource_dir() { c.push(r.join("core").join("ultrasuite-core.exe")); }
    if let Ok(exe) = std::env::current_exe() { if let Some(d) = exe.parent() { c.push(d.join("core").join("ultrasuite-core.exe")); } }
    c.push(PathBuf::from(r"D:\claude\ultra_productivity_suite\build\dist\ultrasuite-core\ultrasuite-core.exe"));
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

fn stop_core() {
    if let Ok(mut g) = CORE.lock() { if let Some(mut ch) = g.take() { let _ = ch.kill(); } }
}

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
        let now = http_get(8081, "/api/ring").map(|b| b.contains("\"is_ringing\":true") || b.contains("\"is_ringing\": true")).unwrap_or(false);
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

// ----------------------------------------------------------------------------- taskbar tracking
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
        let mut start_left: Option<i32> = None;
        let mut last_uia = Instant::now() - Duration::from_secs(10);
        let mut last = (i32::MIN, i32::MIN, 0u32, 0u32, false);
        loop {
            std::thread::sleep(Duration::from_millis(90));
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
            if tb_visible && (start_left.is_none() || last_uia.elapsed() > Duration::from_millis(600)) {
                last_uia = Instant::now();
                if let Some(u) = uia.as_ref() {
                    let found = (|| -> windows::core::Result<i32> {
                        let el = u.ElementFromHandle(tb)?;
                        let cond = u.CreatePropertyCondition(UIA_AutomationIdPropertyId, &VARIANT::from("StartButton"))?;
                        let sb = el.FindFirst(TreeScope_Descendants, &cond)?;
                        Ok(sb.CurrentBoundingRectangle()?.left)
                    })();
                    if let Ok(x) = found { start_left = Some(x); }
                }
            }
            let h = (r.bottom - r.top).max(1) as u32;
            let w = (STRIP_W.load(Ordering::Relaxed) as f64 * scale).round() as u32;
            let gap = (4.0 * scale).round() as i32;
            let mut x = start_left.map(|s| s - w as i32 - gap).unwrap_or(r.left + (12.0 * scale) as i32);
            if x < r.left + (8.0 * scale) as i32 { x = r.left + (8.0 * scale) as i32; }
            let y = r.top;
            let show = tb_visible && !fullscreen && !STRIP_HIDDEN.load(Ordering::Relaxed);
            let state = (x, y, w, h, show);
            if state == last { continue; }
            last = state;
            let hwnd: HWND = match strip.hwnd() { Ok(h) => HWND(h.0 as _), Err(_) => continue };
            if show {
                let _ = strip.set_size(PhysicalSize::new(w, h));
                let _ = strip.set_position(PhysicalPosition::new(x, y));
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
        unsafe {
            let ex = GetWindowLongPtrW(hwnd, GWL_EXSTYLE);
            SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex | (WS_EX_NOACTIVATE.0 | WS_EX_TOOLWINDOW.0) as isize);
        }
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
    if args.iter().any(|a| a == "--show-dock") { STRIP_HIDDEN.store(false, Ordering::Relaxed); }
}

fn run_survey() {
    // Separate, single-window app used by the uninstaller (runs even if the suite is open)
    tauri::Builder::default()
        .setup(|app| {
            WebviewWindowBuilder::new(app, "survey", WebviewUrl::App("index.html?view=survey".into()))
                .title("Uninstall Ultra Suite").inner_size(520.0, 600.0).resizable(false).center().always_on_top(true).build()?;
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
                .title("Ultra Suite").decorations(false).transparent(true).always_on_top(true).skip_taskbar(true)
                .resizable(false).shadow(false).focused(false).visible(false).inner_size(320.0, 48.0).build()?;
            #[cfg(windows)]
            make_noactivate(&strip);
            set_autostart();
            let h1 = handle.clone();
            std::thread::spawn(move || ensure_core(&h1));
            #[cfg(windows)]
            { let h2 = handle.clone(); std::thread::spawn(move || taskbar_tracker(h2)); }
            let h3 = handle.clone();
            std::thread::spawn(move || alarm_watch(h3));
            let rest: Vec<String> = args.iter().skip(1).cloned().collect();
            if !rest.iter().any(|a| a == "--background") {
                if rest.is_empty() { open_view(&handle, "sentinel"); } else { handle_args(&handle, &rest); }
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![get_config, launch, get_icon, set_strip_width, open_config, quit, open_app_view, send_event])
        .build(tauri::generate_context!())
        .expect("error while building Ultra Suite")
        .run(|_app, e| { if let tauri::RunEvent::ExitRequested { api, code, .. } = e { if code.is_none() { api.prevent_exit(); } } });
}
