# Ultra Dock

A translucent Windows 11 bar that floats just above the taskbar and groups your apps into drawers (AI, Chat, Games, Tools). Click a group to open it, click again, press Esc, or click anywhere else to close it.

- **Finds your apps by itself.** Each app is matched by install path or by its Start-menu name, so Store apps, Steam games and portable installs all work. Groups with nothing installed are hidden.
- **Fits any screen.** It sizes itself to its content and positions itself on the monitor's work area (it also leaves room for an auto-hiding taskbar).
- **Native Windows look.** Acrylic blur with rounded corners via [window-vibrancy](https://github.com/tauri-apps/window-vibrancy); falls back to Mica.
- **Small.** About 6 MB, starts in under a second, uses the WebView2 runtime that ships with Windows 11.

## Use

`Setup.cmd` in the repo root installs it to `%LOCALAPPDATA%\UltraSuite\ultra-dock.exe` and starts it at sign-in.

The **⋯** button has: Position (left, center, right), Edit apps, Reload apps, Quit.

## Edit your apps

**⋯ → Edit apps** opens `%APPDATA%\UltraSuiteDock\apps.json`. Each app entry can have:

```json
{ "name": "Steam", "paths": ["%PROGRAMFILES(X86)%\\Steam\\steam.exe"], "start": "Steam" }
{ "name": "Discord", "start": "Discord" }
{ "name": "PC Sentinel", "url": "http://localhost:8084" }
{ "name": "Game", "paths": ["D:\\Games\\game.exe"], "args": ["-windowed"] }
```

The defaults live in [`config/categories.json`](config/categories.json). After editing, choose **⋯ → Reload apps**.

## Build

Requires Rust (stable, MSVC toolchain), Visual Studio Build Tools (C++ workload) and Node.js.

```
cd 02_translucent_taskbar_dock
npx @tauri-apps/cli@2 build --no-bundle
```

Output: `src-tauri/target/release/ultra-dock.exe`. Opening `ui/index.html` in a browser shows a preview with sample data.
