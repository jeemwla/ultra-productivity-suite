# Ultra Dock (the Ultra Suite desktop app)

The Windows app that ties the suite together: the Ultra Dock taskbar organizer, plus the windows for PC Sentinel and
Code Red Alarm. Install it from the [latest release](https://github.com/jeemwla/ultra-productivity-suite/releases/latest).

## Ultra Dock: a taskbar organizer

- **Normal buttons stay normal.** Every app you pin to the taskbar keeps its own button with its "open" underline, and so do the apps in the keep list (default: Opera, Opera developer, File Explorer, Discord, Spotify).
- **Everything else goes into folders** next to your taskbar icons: Games, Chat, AI, Web, Create, Tools, Other. Each folder shows up to four real app icons and a count. Click it and the apps slide open; click an app to bring it forward.
- **One icon per app.** An app with several windows shows one icon with a count; click it to see its window titles and pick one.
- A Dock strip is created for each Windows taskbar. When the native gap is too small, the strip moves to a stable shelf just above the taskbar. The shelf stays visible with taskbar auto-hide; full-screen games temporarily return apps to the native taskbar.
- **Safe by design.** A watchdog checks the dock every 2 seconds. If it stops responding, every app goes straight back to the normal taskbar until the dock recovers. On Quit, on uninstall, and after a crash at the next start, every button is put back.
- **Updates.** A dot on the menu arrow means a new version is out; the menu then has an Update button.

The menu arrow opens an upward panel: Show all / Organize, Sentinel, Alarm, app count, Edit folders, Settings and Quit. The panel reserves its full width and height so its action labels stay readable.

### Edit the keep list and folders

Open Ultra Dock from Start, or choose Settings in the Dock menu or the suite hub. Choose how many managed apps
show separately (Automatic, or 0 to 30); the rest appear in folders. Windows pins and the Keep list stay outside
this count. Folders hold at most six apps, and a single app shows directly.

Edit folders opens the app grid in edit mode. Drag an app onto another app or folder, hold for the highlight,
and release to group them under the entered folder name. Keyboard users can select apps and choose Group selected.
Move out keeps an app separate. Settings also includes category names, order, matching words and per-app overrides.
Changes save automatically; a failed save preserves the edits and offers Save again.

The Advanced section still offers the settings file for manual editing:
`%APPDATA%\UltraSuite\organizer.json` (the Dev edition uses its separate profile).
Defaults: [`config/organizer.json`](config/organizer.json).

Native taskbar buttons are restored while Alt or the Windows key is held, and briefly after release, so switching
views can enumerate the windows. The Dock never hides or changes styles on application windows. Each screen must
acknowledge rendered apps before their native taskbar buttons can be removed.

## Separate apps in Windows

PC Sentinel and Code Red Alarm each open in their own window with their own Windows identity
(AppUserModelID `UltraSuite.sentinel`, `UltraSuite.alarm`), so each has its own taskbar button and icon, and a pinned
Start menu shortcut shares one button with its running window. Closing a window keeps its service running (alarms
still ring).

## Under the hood

- Tauri 2 (Rust): `src-tauri/src/main.rs`. Strip and uninstall survey UI: `ui/index.html`. Icons: `tools/make_icons.py` (Pillow).
- Starts the bundled `core\ultrasuite-core.exe` (all background services). Release builds register themselves to start at sign-in.
- Command line: `--open dock|sentinel|alarm|insights|hub`, `--background`, `--show-dock`, `--quit`, `--uninstall-survey`.
- Organizer: `ITaskbarList::DeleteTab` / `AddTab`; pins read from the pinned-shortcut folder and the Taskband registry value; UI Automation to place the strip; checked recovery journals for tucked-away taskbar buttons in the edition profile. Nothing that touches another app's window runs on the main thread.
- Installer: NSIS via Tauri (`installer-hooks.nsh`): install-folder choice, Start menu apps, an opt-in question for anonymous stats, a clean quit before updating or uninstalling, and an optional "why are you uninstalling?" survey.
- Tests: `cargo test --release` in `src-tauri` (pin matching, watchdog rules).

## Build

From the repo root: `build_release.ps1` (needs Rust MSVC, Visual Studio Build Tools C++, Node.js, Python with PyInstaller and Pillow).
For development, `dev.ps1` runs everything from source with live reload.
