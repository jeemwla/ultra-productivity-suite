# Ultra Dock (the Ultra Suite desktop app)

The Windows app that ties the suite together: the Ultra Dock taskbar organizer, plus the windows for PC Sentinel and
Code Red Alarm. Install it from the [latest release](https://github.com/jeemwla/ultra-productivity-suite/releases/latest).

## Ultra Dock: a taskbar organizer

- **Normal buttons stay normal.** Every app you pin to the taskbar keeps its own button with its "open" underline, and so do the apps in the keep list (default: Opera, Opera developer, File Explorer, Discord, Spotify).
- **Everything else goes into folders** next to your taskbar icons: Games, Chat, AI, Web, Create, Tools, Other. Each folder shows up to four real app icons and a count. Click it and the apps slide open; click an app to bring it forward.
- **One icon per app.** An app with several windows shows one icon with a count; click it to see its window titles and pick one.
- **Never in the way.** Hides with an auto-hide taskbar and during full-screen games, and never takes keyboard focus.
- **Safe by design.** A watchdog checks the dock every 2 seconds. If it stops responding, every app goes straight back to the normal taskbar until the dock recovers. On Quit, on uninstall, and after a crash at the next start, every button is put back.
- **Updates.** A dot on the menu arrow means a new version is out; the menu then has an Update button.

The menu arrow opens: Show all / Organize, Sentinel, Alarm, Edit, Quit.

### Edit the keep list and folders

Menu > Edit opens `%APPDATA%\UltraSuite\organizer.json` (defaults: [`config/organizer.json`](config/organizer.json)):
`keep` lists apps that stay as normal buttons (matched by path), `categories` maps keywords to folders, `order` sets
the folder order, and `overrides` puts a specific app in a specific folder. Changes apply within a second. Pinning
an app to the taskbar keeps it as a normal button without editing anything.

## Separate apps in Windows

PC Sentinel and Code Red Alarm each open in their own window with their own Windows identity
(AppUserModelID `UltraSuite.sentinel`, `UltraSuite.alarm`), so each has its own taskbar button and icon, and a pinned
Start menu shortcut shares one button with its running window. Closing a window keeps its service running (alarms
still ring).

## Under the hood

- Tauri 2 (Rust): `src-tauri/src/main.rs`. Strip and uninstall survey UI: `ui/index.html`. Icons: `tools/make_icons.py` (Pillow).
- Starts the bundled `core\ultrasuite-core.exe` (all background services). Release builds register themselves to start at sign-in.
- Command line: `--open sentinel|alarm`, `--background`, `--show-dock`, `--quit`, `--uninstall-survey`.
- Organizer: `ITaskbarList::DeleteTab` / `AddTab`; pins read from the pinned-shortcut folder and the Taskband registry value; UI Automation to place the strip; a crash-safe list of tucked-away buttons in `%APPDATA%\UltraSuite\hidden_tabs.txt`. Nothing that touches another app's window runs on the main thread.
- Installer: NSIS via Tauri (`installer-hooks.nsh`): install-folder choice, Start menu apps, an opt-in question for anonymous stats, a clean quit before updating or uninstalling, and an optional "why are you uninstalling?" survey.
- Tests: `cargo test --release` in `src-tauri` (pin matching, watchdog rules).

## Build

From the repo root: `build_release.ps1` (needs Rust MSVC, Visual Studio Build Tools C++, Node.js, Python with PyInstaller and Pillow).
For development, `dev.ps1` runs everything from source with live reload.
