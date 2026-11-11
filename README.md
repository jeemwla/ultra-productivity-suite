# Ultra Productivity Suite

A set of Windows desktop apps that work together: a hardware and network monitor, a wake-up alarm you can't ignore,
a taskbar organizer, deck deck controls, and an optional Laptop Pack. Each app is its own app in Windows, with its
own window, taskbar button and icon. Everything runs on your PC.

| App | What it does |
|---|---|
| PC Sentinel | Your PC at a glance: live CPU, GPU, memory and power tiles, top apps by CPU, GPU and memory, every fan with its RPM and curve, drive health and wear, latency, a 25-second speed test, every sensor organized by part, power plans, the Windows power mode slider, refresh-rate switching, temperature alerts and an exportable report. Works on any PC: it uses LibreHardwareMonitor when installed and Windows' own sensors otherwise. |
| Code Red Alarm | Alarms that force your chosen speaker to full volume and only stop when you solve a sum or type a phrase (no pasting). Pick any speaker, test it, and get your volume back afterwards. |
| Ultra Dock | A taskbar organizer: your main apps stay as normal buttons, and every other open app is grouped into phone-style folders (Games, Chat, AI, Web, Create, Tools) on the right of your taskbar icons. Click a folder and its apps slide open. |
| deck knobs | Plugin and profile for deck decks: Spotify, Discord and master volume knobs, a PC-stats knob and a Gaming folder. |
| Laptop Pack (add-on) | Adds battery health and wear, charge cycles, charge and discharge watts, time left, battery health history and screen brightness to PC Sentinel. Install it on top of Ultra Suite. |

## Install (Windows 10 or 11)

1. Download `UltraSuite-Setup-<version>-x64.exe` from the [latest release](https://github.com/jeemwla/ultra-productivity-suite/releases/latest) and run it. Pick a folder, click Install.
2. Open PC Sentinel, Code Red Alarm or Ultra Dock from the Start menu. The suite starts with Windows.
3. On a laptop, also run `UltraSuite-LaptopPack-<version>-x64.exe` from the same release.

Updates: when a new version is published, every app shows an update notice with an Update button.

Full sensors (temperatures, fans, power) come from the free [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor).
PC Sentinel offers a one-click setup for it; without it, Sentinel still shows load, memory, drives, network and battery from Windows itself.

Uninstall from Settings > Apps like any other program.

## Appearance

Every app has an Appearance button: System, Light, Dark and eight signature themes (Midnight, Sakura, Matcha, Ember,
Arctic, Paper, Terminal, Synthwave), plus a Motion setting (Full, Reduced, Off). The choice applies to all apps.

## Use it from your phone

Install [Tailscale](https://tailscale.com/download) on the PC and your phone with the same account. The suite publishes
the Alarm and Sentinel pages to your private tailnet only (`https://<pc>.<tailnet>.ts.net:8443` and `:8444`).

## Privacy

- All services listen on `127.0.0.1`; other devices reach them only through your own Tailscale network.
- Network use: the update check reads this repository's public release list; the speed test downloads from Cloudflare or DataPacket only when you press Start.
- Usage statistics (install, uninstall reason, daily heartbeat) are off unless you agree during setup, and never include personal data. The Feedback button sends only what you type.

## For developers

```
01_pebble_code_red_alarm/    Code Red Alarm: FastAPI backend, speaker bridge, frontend
02_translucent_taskbar_dock/ Ultra Suite desktop app (Tauri 2, Rust): app windows, Ultra Dock organizer, installer hooks, icons
03_deck_deck_config/       deck plugin (Node), profile generator, knob daemon
04_hardware_net_sentinel/    PC Sentinel: sensors (LibreHardwareMonitor, WMI, Windows counters), drives, power tools, speed test, VPN
05_insights/                 Owner dashboard for installs, uninstalls and feedback
06_laptop_pack/              Laptop Pack add-on and its installer script
core/                        Runs every service; shared themes, live reload, update check, icons
dev.ps1 / stop_dev.ps1       Dev mode: everything from source with live reload
build_release.ps1            Builds the Ultra Suite installer (version from VERSION)
build_laptop_pack.ps1        Builds the Laptop Pack installer
```

Build requirements: Python 3.11+ (requirements in each app folder, plus PyInstaller and Pillow), Rust (stable, MSVC),
Visual Studio Build Tools with the C++ workload, Node.js.

## License

MIT. See [LICENSE](LICENSE). Bundles the MIT-licensed deck plugin SDK and the `ws` library inside the deck plugin folder.
