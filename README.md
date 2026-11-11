# Ultra Suite

Three Windows desktop apps that work together. Each is its own app in Windows, with its own window, taskbar button
and icon, and everything runs on your PC.

| App | What it does |
|---|---|
| PC Sentinel | Your PC at a glance: live CPU, GPU, memory and power tiles, top apps by CPU, GPU and memory, every fan with its RPM and curve, drive health and wear, latency, a 25-second speed test, every sensor organized by part, power plans, the Windows power mode slider, refresh-rate switching, temperature alerts and an exportable report. On a laptop it adds a Battery page: health and wear, charge cycles, charge and discharge watts, time left, health history and screen brightness. Works on any PC: it uses LibreHardwareMonitor when installed and Windows' own sensors otherwise. |
| Code Red Alarm | Alarms that force your chosen speaker or headphones to full volume and only stop when you solve a sum or type a phrase (no pasting). Pick any output, test it, and get your volume back afterwards. |
| Ultra Dock | A taskbar organizer. Apps you pin to the taskbar, plus the ones you choose to keep, stay as normal buttons; every other open app goes into phone-style folders (Games, Chat, AI, Web, Create, Tools) next to your taskbar icons. Click a folder and its apps slide open; an app with several windows shows them as a list. If the dock ever stops responding, every app goes straight back to the normal taskbar. |

## Install (Windows 10 or 11)

1. Download `UltraSuite-Setup-<version>-x64.exe` from the [latest release](https://github.com/jeemwla/ultra-productivity-suite/releases/latest) and run it. Pick a folder, click Install.
2. Open PC Sentinel, Code Red Alarm or Ultra Dock from the Start menu. The suite starts with Windows.

Updates: when a new version is published, every app (and the Ultra Dock menu) shows an update notice with an Update button.

Full sensors (temperatures, fans, power) come from the free [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor).
PC Sentinel offers a one-click setup for it; without it, Sentinel still shows load, memory, drives, network and battery from Windows itself.

Uninstall from Settings > Apps like any other program. Ultra Dock puts every taskbar button back first.

## Appearance

Every app has an Appearance button: System, Light, Dark and eight signature themes (Midnight, Sakura, Matcha, Ember,
Arctic, Paper, Terminal, Synthwave), plus a Motion setting (Full, Reduced, Off). The choice applies to all apps.

## Privacy

- All services listen on `127.0.0.1` only.
- Network use: the update check reads this repository's public release list; the speed test downloads from Cloudflare or DataPacket only when you press Start.
- Usage statistics (install, uninstall reason, daily heartbeat) are off unless you agree during setup, and never include personal data. The Feedback button sends only what you type.

## For developers

```
pc_sentinel/        PC Sentinel: sensors (LibreHardwareMonitor, WMI, Windows counters), drives, battery, power tools, speed test, VPN
code_red_alarm/     Code Red Alarm: alarm engine, speaker helper, page
ultra_dock/         The desktop app (Tauri 2, Rust): Ultra Dock organizer, the app windows, installer hooks, icons
insights/           Optional dashboard for the maintainer (installs, uninstalls, feedback); off unless ULTRASUITE_OWNER=1
core/               Runs every service; shared themes, live reload, update check, icons
tests/              Unit tests:  python -m unittest discover -s tests
dev.ps1 / stop_dev.ps1   Dev mode: everything from source with live reload
build_release.ps1   Builds the installer (version from VERSION)
```

Build requirements: Python 3.11+ (`requirements.txt`, plus PyInstaller and Pillow), Rust (stable, MSVC),
Visual Studio Build Tools with the C++ workload, Node.js.

## License

MIT. See [LICENSE](LICENSE).
