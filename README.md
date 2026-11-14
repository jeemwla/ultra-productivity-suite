# Ultra Suite

Free Windows utilities for organizing open apps, monitoring your PC, and setting local alarms.

[Download the Windows installer, v2.2.16 (x64)](https://github.com/jeemwla/ultra-productivity-suite/releases/download/v2.2.16/UltraSuite-Setup-2.2.16-x64.exe) · [Latest release and notes](https://github.com/jeemwla/ultra-productivity-suite/releases/latest)

The latest public installer is v2.2.16. Source on main and the owner's local Dev edition can contain changes that have not been published. The app descriptions below cover the current source; they do not mean every feature is in that installer.

This is an early release. Dock recovery and multi-monitor visibility work is active. The [v2.2.16 release notes](https://github.com/jeemwla/ultra-productivity-suite/releases/tag/v2.2.16) state that multi-monitor setups keep native taskbar buttons visible. Dock grouping can be switched off from the Ultra Suite tray menu.

## The apps

| App | What it does |
|---|---|
| Ultra Dock | Organizes running apps into taskbar folders, with per-app grouping settings and native buttons for pinned or kept apps. Includes the suite hub and links to the other app windows. |
| PC Sentinel | Shows CPU, GPU, memory and process usage, available cooling and drive sensors, battery information, and network measurements. Includes Windows power/display tools and an offline PSU catalog. |
| Code Red Alarm | Schedules local alarms with a chosen audio output, optional typed-phrase or arithmetic dismissal, and optional volume enforcement. Current source also includes saved countdown timers with pause, resume and reset. |

The apps have separate windows and Start menu entries. Their background services currently share a process. Further process isolation is tracked in the [development roadmap](ROADMAP.md).

## Install and get started

1. Download and run the x64 installer above.
2. Open PC Sentinel, Code Red Alarm, or Ultra Dock from Start.
3. For temperatures, fans and additional power sensors, use Sentinel's sensor setup or install the free [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor). Windows counters provide a smaller set of readings when it is absent.
4. Open Sentinel's [Settings / Extras](docs/SETTINGS.md) to choose pages and optional router controls. Router extras are off by default.

Python, Rust and Docker are not end-user prerequisites for the bundled desktop installer. Windows' WebView2 runtime is needed for the windows; initial [runtime setup](https://v2.tauri.app/distribute/windows-installer/#webview2-installation-options) can require a download.

Uninstall through Windows Settings > Apps. Back up any alarm schedules you need before removing app data.

## Features and limits

PC Sentinel includes CPU, GPU and memory readings; grouped process usage; cooling sensors; drive information; network latency; an on-demand speed test; Windows power/display controls; sensor tables; and a diagnostic export. Battery information is shown when a battery is detected. Desktop users can search an offline PSU catalog by brand, wattage, form factor and model, or enter details manually. See the [catalog sources and limitations](docs/PSU_CATALOG.md).

Sensor availability depends on the hardware, Windows permissions and the data source. Missing readings must remain unavailable. Component power estimates are not a wall-power measurement. A PSU rating describes capacity, not current consumption.

Code Red Alarm starts with no sample alarms. New drafts have no repeat days, Code Red and Wake-up math off, forced full volume off, and browser ringing off. The default output follows Windows; users can choose a specific speaker. Existing saved alarms and explicit preferences are retained.

Code Red Alarm runs on the local PC. It needs the PC and alarm service available. Speaker delivery, wake-from-sleep support and foreground behavior need validation on the intended machine. Do not rely on an untested installation for a critical reminder.

Ultra Dock's source includes category folders, per-app overrides, a managed-app count setting, and checked recovery journals for native taskbar buttons. Taskbar layout and switching behavior need native acceptance on the intended monitor setup. The source's newer recovery and layout changes are not evidence of a newer published installer.

## Public release and Dev edition

Desktop, sleep/wake, multi-monitor, laptop and clean-PC acceptance remain separate from automated tests.

The Dev edition is for the owner's local testing and is not offered here as a public download. Its app identities, data directories and service ports are separate from Public. Dev disables alarm delivery, wake scheduling, GitHub self-updates and production reporting; Dock grouping defaults to off.

Ultra Suite Insights is a maintainer dashboard for collected usage/feedback events and GitHub statistics. Its services load in owner mode or Dev. It is not a fourth consumer app or a requirement for the three public utilities.

The [roadmap](ROADMAP.md) tracks planned work. Roadmap entries are not released features.

## Appearance and accessibility

The apps provide light/dark appearance options and additional themes, with motion preferences. Work on collapsible navigation, theme-matched scrolling, keyboard access, compact layouts and larger process lists is tracked in the roadmap.

## Privacy and optional connections

Core services bind to loopback and run locally. Alarm scheduling and local hardware monitoring do not require a hosted account.

Network-dependent features include update checks, optional sensor setup downloads, latency probes, the on-demand speed test, configured VPN controls, optional usage reporting and submitted feedback.

Usage reporting is opt-in. Feedback sends the entered message and category with app/version information, Windows version and a random local identifier to the configured maintainer collector. Feedback and reporting are not required to use the local apps. The identifier is pseudonymous; it should not be described as proving anonymity.

Optional device integrations remain available for configured setups. Router-specific VPN controls require separate setup.

## For developers

| Path | Purpose |
|---|---|
| pc_sentinel/ | Hardware, storage, battery, network and system tools |
| code_red_alarm/ | Alarm scheduling, audio helper and frontend |
| ultra_dock/ | Tauri desktop windows, taskbar organizer and installer |
| core/ | Shared backend startup, themes, local API guard and update routes |
| insights/ | Owner/Dev-only event collector and maintainer dashboard |
| tests/ | Backend, native-policy and frontend-helper regression tests |

Run backend tests from the repository root:

~~~powershell
python -W ignore -m unittest discover -s tests
node --test tests/*.test.cjs
~~~

Run native tests in ultra_dock/src-tauri:

~~~powershell
cargo test --release
~~~

Tests use isolated data and must never reach the real audio helper. Check the next alarm before installs or disruptive work and preserve a 30-minute protection window. Development and production alarm databases must remain separate.

Build tools: Python with requirements.txt and the packaging tools, Rust/MSVC, Visual Studio C++ Build Tools, and Node.js. build_release.ps1 builds the installer from VERSION. Building and installing are separate actions.

## License

MIT. See [LICENSE](LICENSE). Third-party integrations retain their own licenses.
