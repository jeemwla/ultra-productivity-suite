# Ultra Suite desktop app (Ultra Dock + app windows)

The Windows app that ties the suite together. Install it from the [latest release](https://github.com/jeemwla/ultra-productivity-suite/releases/latest).

## Ultra Dock: app groups inside the taskbar

- Sits **inside the Windows taskbar, just left of the Start button**, at the taskbar's exact height. No floating window.
- Click **AI**, **Chat**, **Games** or **Tools** and the group's apps **slide open right in the taskbar**; click again (or move the mouse away) and they slide closed.
- Real app icons straight from Windows, including Store apps and Steam games.
- Follows the Start button as your taskbar icons change, hides when an auto-hide taskbar hides, and hides during fullscreen games.
- Never takes keyboard focus, so it can't interrupt what you're doing.
- Finds installed apps automatically by install path or Start-menu name; groups with nothing installed are hidden.

The **⋯** button opens: Alarm, Sentinel, Edit (your app list), Reload, Quit.

### Edit your apps

**⋯ → Edit** opens `%APPDATA%\UltraSuite\apps.json`. Entries can use any of:

```json
{ "name": "Steam", "paths": ["%PROGRAMFILES(X86)%\\Steam\\steam.exe"], "start": "Steam" }
{ "name": "Discord", "start": "Discord" }
{ "name": "PC Sentinel", "url": "http://localhost:8084" }
{ "name": "Game", "paths": ["D:\\Games\\game.exe"], "args": ["-windowed"] }
```

`start` is the app's name in the Start menu. Defaults live in [`config/categories.json`](config/categories.json). Save, then **⋯ → Reload**.

## App windows

Start menu entries **Code Red Alarm** and **PC Sentinel** open their own windows (closing a window keeps alarms running). When an alarm rings, the alarm window jumps in front of everything.

## Under the hood

- Tauri 2 (Rust) app: `src-tauri/src/main.rs`. UI for the strip and the uninstall survey: `ui/index.html`.
- Starts the bundled `core\ultrasuite-core.exe` (all background services) and registers itself to start at sign-in.
- Command line: `--open alarm|sentinel|insights`, `--background`, `--show-dock`, `--deck`, `--uninstall-survey`.
- Installer: NSIS via Tauri (`installer-hooks.nsh`): install-folder choice, Start menu apps, an opt-in question for anonymous stats, and an optional "why are you uninstalling?" survey.

## Build

From the repo root: `build_release.ps1` (needs Rust MSVC, Visual Studio Build Tools C++, Node.js, Python with PyInstaller).
