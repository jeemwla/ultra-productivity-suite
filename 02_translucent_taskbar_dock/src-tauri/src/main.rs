#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

//! Translucent category dock for Windows 11.
//! - Sizes itself to its content and sits just above the taskbar on the current monitor.
//! - Shows only apps that are installed on this PC (paths are checked at load time).
//! - The app list lives in %APPDATA%\UltraSuiteDock\apps.json (created on first run).

use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::process::Command;
use tauri::{Manager, PhysicalPosition, PhysicalSize, WebviewWindow};

const DEFAULT_CONFIG: &str = include_str!("../../config/categories.json");

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
                    Ok(val) => out.push_str(&val),
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

fn config_path() -> PathBuf {
    let base = std::env::var("APPDATA").map(PathBuf::from).unwrap_or_else(|_| std::env::temp_dir());
    base.join("UltraSuiteDock").join("apps.json")
}

fn read_config() -> Value {
    let path = config_path();
    if !path.exists() {
        if let Some(dir) = path.parent() { let _ = std::fs::create_dir_all(dir); }
        let _ = std::fs::write(&path, DEFAULT_CONFIG);
    }
    std::fs::read_to_string(&path)
        .ok()
        .and_then(|s| serde_json::from_str::<Value>(&s).ok())
        .unwrap_or_else(|| serde_json::from_str(DEFAULT_CONFIG).expect("bundled config is valid JSON"))
}

/// Start-menu apps on this PC: lowercase name -> AppID (works for Store apps, Steam games, etc.)
fn start_menu_apps() -> std::collections::HashMap<String, String> {
    let mut map = std::collections::HashMap::new();
    let mut cmd = Command::new("powershell.exe");
    cmd.args(["-NoProfile", "-NonInteractive", "-Command",
              "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | ConvertTo-Json -Compress"]);
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    if let Ok(out) = cmd.output() {
        if let Ok(v) = serde_json::from_slice::<Value>(&out.stdout) {
            let items = match v { Value::Array(a) => a, other => vec![other] };
            for it in items {
                if let (Some(n), Some(id)) = (it.get("Name").and_then(|x| x.as_str()), it.get("AppID").and_then(|x| x.as_str())) {
                    map.entry(n.to_lowercase()).or_insert_with(|| id.to_string());
                }
            }
        }
    }
    map
}

/// Returns the dock config with every app resolved against this PC.
/// Order: an existing file in "paths" -> a Start-menu entry named "start" (or the app's name) -> "url".
#[tauri::command]
fn get_config() -> Value {
    let mut cfg = read_config();
    let mut error = Value::Null;
    let start = start_menu_apps();
    if let Some(cats) = cfg.get_mut("categories").and_then(|c| c.as_array_mut()) {
        for cat in cats.iter_mut() {
            if let Some(apps) = cat.get_mut("apps").and_then(|a| a.as_array_mut()) {
                for app in apps.iter_mut() {
                    let paths: Vec<String> = app.get("paths").and_then(|p| p.as_array())
                        .map(|a| a.iter().filter_map(|x| x.as_str().map(expand_env)).collect())
                        .unwrap_or_default();
                    let mut found = paths.iter().find(|p| Path::new(p).exists()).cloned();
                    if found.is_none() {
                        let key = app.get("start").and_then(|s| s.as_str())
                            .or_else(|| app.get("name").and_then(|s| s.as_str()))
                            .unwrap_or("").to_lowercase();
                        if let Some(id) = start.get(&key) {
                            found = Some(if id.contains("://") { id.clone() } else { format!("shell:AppsFolder\\{}", id) });
                        }
                    }
                    let has_url = app.get("url").and_then(|u| u.as_str()).map(|u| !u.is_empty()).unwrap_or(false);
                    let available = found.is_some() || has_url && app.get("paths").is_none() && app.get("start").is_none();
                    app["resolved"] = found.map(Value::String).unwrap_or(Value::Null);
                    app["available"] = Value::Bool(available);
                }
            }
        }
    } else {
        error = json!("apps.json has no 'categories' list");
    }
    cfg["config_path"] = json!(config_path().to_string_lossy());
    cfg["error"] = error;
    cfg
}

fn open_target(target: &str) -> std::io::Result<std::process::Child> {
    // explorer.exe opens URLs, folders and protocol links without a shell
    Command::new("explorer.exe").arg(target).spawn()
}

#[tauri::command]
fn launch(path: Option<String>, args: Option<Vec<String>>, url: Option<String>) -> Result<String, String> {
    if let Some(p) = path.filter(|p| !p.is_empty()) {
        if p.starts_with("shell:") || p.contains("://") {
            return open_target(&p).map(|_| format!("Opened {}", p)).map_err(|e| e.to_string());
        }
        let p = expand_env(&p);
        if Path::new(&p).exists() {
            let mut cmd = Command::new(&p);
            if let Some(a) = args { cmd.args(a); }
            if let Some(dir) = Path::new(&p).parent() { cmd.current_dir(dir); }
            return cmd.spawn().map(|_| format!("Opened {}", p)).map_err(|e| e.to_string());
        }
    }
    if let Some(u) = url.filter(|u| !u.is_empty()) {
        return open_target(&u).map(|_| format!("Opened {}", u)).map_err(|e| e.to_string());
    }
    Err("This app isn't installed on this PC".into())
}

/// Resize the window to the content (CSS px) and pin it above the taskbar.
#[tauri::command]
fn set_layout(window: WebviewWindow, width: f64, height: f64, position: Option<String>) -> Result<(), String> {
    let monitor = window.current_monitor().ok().flatten()
        .or_else(|| window.primary_monitor().ok().flatten())
        .ok_or("no monitor")?;
    let scale = monitor.scale_factor();
    let area = monitor.work_area();
    let w = (width * scale).round() as i32;
    let h = (height * scale).round() as i32;
    let margin = (12.0 * scale).round() as i32;
    // Auto-hide taskbar: the work area is the whole screen, so leave room for the taskbar to pop up
    let full = monitor.size();
    let bottom_gap = if area.size.height >= full.height { (56.0 * scale).round() as i32 } else { margin };
    let aw = area.size.width as i32;
    let x = match position.as_deref() {
        Some("left") => area.position.x + margin,
        Some("right") => area.position.x + aw - w - margin,
        _ => area.position.x + (aw - w) / 2,
    };
    let y = area.position.y + area.size.height as i32 - h - bottom_gap;
    window.set_size(PhysicalSize::new(w.max(1) as u32, h.max(1) as u32)).map_err(|e| e.to_string())?;
    window.set_position(PhysicalPosition::new(x, y)).map_err(|e| e.to_string())?;
    if !window.is_visible().unwrap_or(true) {
        let _ = window.show();
    }
    Ok(())
}

#[tauri::command]
fn open_config() -> Result<(), String> {
    let _ = read_config(); // make sure it exists
    Command::new("notepad.exe").arg(config_path()).spawn().map(|_| ()).map_err(|e| e.to_string())
}

#[tauri::command]
fn quit(app: tauri::AppHandle) {
    app.exit(0);
}

#[cfg(target_os = "windows")]
fn round_corners(window: &WebviewWindow) {
    use windows_sys::Win32::Graphics::Dwm::{DwmSetWindowAttribute, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND};
    if let Ok(hwnd) = window.hwnd() {
        let pref: i32 = DWMWCP_ROUND;
        unsafe {
            DwmSetWindowAttribute(hwnd.0 as _, DWMWA_WINDOW_CORNER_PREFERENCE as u32,
                                  &pref as *const i32 as *const _, std::mem::size_of::<i32>() as u32);
        }
    }
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(w) = app.get_webview_window("main") { let _ = w.show(); let _ = w.set_focus(); }
        }))
        .setup(|app| {
            let window = app.get_webview_window("main").expect("main window");
            #[cfg(target_os = "windows")]
            {
                if window_vibrancy::apply_acrylic(&window, Some((16, 18, 24, 150))).is_err() {
                    let _ = window_vibrancy::apply_mica(&window, Some(true));
                }
                round_corners(&window);
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![get_config, launch, set_layout, open_config, quit])
        .run(tauri::generate_context!())
        .expect("error while running the dock");
}
