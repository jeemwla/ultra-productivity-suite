# Ultra Suite desktop app (app windows + Ultra Dock organizer)

The Windows app that ties the suite together. Install it from the [latest release](https://github.com/jeemwla/ultra-productivity-suite/releases/latest).

## Separate apps

PC Sentinel, Code Red Alarm and Insights each open in their own window with their own Windows identity
(AppUserModelID `UltraSuite.sentinel`, `UltraSuite.alarm`, `UltraSuite.insights`). Windows treats them as separate
apps: each has its own taskbar button and icon, and pinning one to the taskbar reopens that app. The Start menu
shortcuts carry the same IDs, so a pinned shortcut and its running window share one button. Closing a window keeps
its service running (alarms still ring); when an alarm rings, the alarm window jumps in front of everything.

## Ultra Dock: a taskbar organizer

- Your main apps stay as normal taskbar buttons (default: Opera, Opera developer, File Explorer, Discord, Spotify).
- Every other open app is grouped into phone-style folders on the right of your taskbar icons: Games, Chat, AI, Web, Create, Tools, Other.
- Each folder shows up to four real app icons and a count badge when there are more. Click it and the apps slide open in the taskbar; click an app to bring it forward.
- Hides when an auto-hide taskbar hides and during fullscreen games. Never takes keyboard focus.
- On Quit (or after a crash, at the next start) every taskbar button is put back.

The chevron menu opens: Show all / Organize, Sentinel, Alarm, Edit, Quit.

### Edit the keep list and categories

Menu > Edit opens `%APPDATA%\UltraSuite\organizer.json` (defaults: [`config/organizer.json`](config/organizer.json)):
`keep` lists the apps that stay as normal buttons (matched by path ending), `categories` maps keywords to folders,
`order` sets the folder order, and `overrides` pins a specific app to a folder. Changes apply within a second.

## Under the hood

- Tauri 2 (Rust): `src-tauri/src/main.rs`. Strip and uninstall survey UI: `ui/index.html`. Icons: `tools/make_icons.py` (Pillow).
- Starts the bundled `core\ultrasuite-core.exe` (all background services). Release builds register themselves to start at sign-in.
- Command line: `--open sentinel|alarm|insights`, `--background`, `--show-dock`, `--deck`, `--quit`, `--uninstall-survey`.
- Organizer: `ITaskbarList::DeleteTab` / `AddTab`, UI Automation to find the taskbar buttons, a crash-safe list of tucked-away buttons in `%APPDATA%\UltraSuite\hidden_tabs.txt`.
- Installer: NSIS via Tauri (`installer-hooks.nsh`): install-folder choice, Start menu apps, an opt-in question for anonymous stats, and an optional "why are you uninstalling?" survey.

## Build

From the repo root: `build_release.ps1` (needs Rust MSVC, Visual Studio Build Tools C++, Node.js, Python with PyInstaller and Pillow).
For development, `dev.ps1` runs everything from source with live reload.
