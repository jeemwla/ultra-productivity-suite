# Ultra Suite

Three free Windows utilities: understand your PC, set local alarms, and organize open apps.

[Download the Windows installer, v2.2.7 (x64)](https://github.com/jeemwla/ultra-productivity-suite/releases/download/v2.2.7/UltraSuite-Setup-2.2.7-x64.exe) · [Latest release and notes](https://github.com/jeemwla/ultra-productivity-suite/releases/latest)

This is an early release. Dock multi-monitor visibility issues are being repaired. Keep Dock grouping off from the Ultra Suite tray menu if buttons become inaccessible. New fixes on main can be ahead of the downloadable installer.

## Pick what helps you

| App | Why use it? | Examples |
|---|---|---|
| PC Sentinel | See which apps use resources, whether the PC is running hot, drive health, battery information, and network latency. | Students investigating slow coursework software, creators watching render workloads, gamers checking temperatures, or anyone checking a sluggish laptop. |
| Code Red Alarm | Schedule alarms locally, choose an audio output, and optionally require a typed phrase or arithmetic to dismiss. | Study breaks, personal routines, and reminders while the Windows session is running. |
| Ultra Dock | Group open apps to reduce taskbar clutter, with a native-taskbar fallback when the strip cannot safely replace buttons. | People who keep many work, chat, browser, or creative apps open. Multi-monitor grouping remains under development. |

The apps have separate windows and Start menu entries. Their background services currently share a process. Further process isolation is tracked in the [development roadmap](ROADMAP.md).

## Install and get started

1. Download and run the x64 installer above.
2. Open PC Sentinel, Code Red Alarm, or Ultra Dock from Start.
3. For temperatures, fans and additional power sensors, use Sentinel's full-sensor setup or install the free [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor). Windows counters provide a smaller set of readings when it is absent.
4. Choose the features you need. Dock grouping can be switched off in the Ultra Suite tray menu.

Python, Rust and Docker are not end-user prerequisites for the bundled desktop installer. Windows' WebView2 runtime is needed for the windows; initial [runtime setup](https://v2.tauri.app/distribute/windows-installer/#webview2-installation-options) can require a download.

Uninstall through Windows Settings > Apps. Back up any alarm schedules you need before removing app data.

## What the apps show

PC Sentinel includes CPU, GPU and memory readings; grouped process usage; cooling sensors; drive information; network latency; an on-demand speed test; Windows power/display controls; sensor tables; and a diagnostic export. Battery information is shown when a battery is detected.

Sensor availability depends on the hardware, Windows permissions and the data source. Missing readings must remain unavailable. Component power estimates are not a wall-power measurement. A PSU rating describes capacity, not current consumption.

Code Red Alarm runs on the local PC. It needs the PC and alarm service available. Speaker delivery, wake-from-sleep support and foreground behavior need validation on the intended machine. Do not rely on an untested installation for a critical reminder.

Ultra Dock keeps pinned/selected apps on the native taskbar and groups other open apps. Recovery and visibility work is active. A folder should never be the only route to an app unless its replacement is actually available.

## Development status

Main now includes tested source changes for WebSocket origin checks, conservative drive matching, write-ahead Dock recovery, far-future alarm occurrences, absolute UTC wake deadlines, clearer storage capacity, and stricter Dock visibility gating.

The v2.2.7 installer is built from this source and packages only an exact list of app files, checked file by file. Installed desktop, sleep/wake, multi-monitor, laptop and clean-PC acceptance remain separate checks.

The [roadmap](ROADMAP.md) contains 24 public phases and 288 detailed engineering and acceptance tasks. Candidate features remain proposals. Stress-test parity, universal hardware support and best-in-category claims have not been established.

## Appearance and accessibility

The apps provide light/dark appearance options and additional themes, with motion preferences. Work on collapsible navigation, theme-matched scrolling, keyboard access, compact layouts and larger process lists is tracked in the roadmap.

## Privacy and optional connections

Core services bind to loopback and run locally. Alarm scheduling and local hardware monitoring do not require a hosted account.

Network-dependent features include update checks, optional sensor setup downloads, latency probes, the on-demand speed test, configured VPN controls, optional usage reporting and submitted feedback.

Usage reporting is opt-in. Feedback sends the entered message and category with app/version information, Windows version and a random local identifier to the configured maintainer collector. Feedback and reporting are not required to use the local apps. The identifier is pseudonymous; it should not be described as proving anonymity.

Router-specific VPN controls, owner analytics and personal peripherals are optional configuration. They are not general hardware prerequisites.

## For developers

| Path | Purpose |
|---|---|
| pc_sentinel/ | Hardware, storage, battery, network and system tools |
| code_red_alarm/ | Alarm scheduling, audio helper and frontend |
| ultra_dock/ | Tauri desktop windows, taskbar organizer and installer |
| core/ | Shared backend startup, themes, local API guard and update routes |
| insights/ | Optional maintainer dashboard |
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
