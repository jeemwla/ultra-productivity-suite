# Laptop Pack (add-on for PC Sentinel)

An optional add-on that installs on top of Ultra Suite. Try the main app first; add this if you're on a laptop.

## What it adds to PC Sentinel's Battery page

| Feature | Where the data comes from |
|---|---|
| Battery health: full charge capacity compared with the design capacity | `root\wmi` `BatteryFullChargedCapacity` and `BatteryStaticData` |
| Wear (capacity lost since new) and a plain verdict | calculated from the two above |
| Charge cycles | `root\wmi` `BatteryCycleCount` |
| Charging or discharging rate in watts, voltage | `root\wmi` `BatteryStatus` |
| Time left on battery, time until full | `BatteryStatus` remaining capacity and rate, `Win32_Battery` |
| Battery health over time | `powercfg /batteryreport /xml` history |
| Built-in screen brightness slider | `root\wmi` `WmiMonitorBrightness` |

All of it comes from Windows itself, so it works on any brand (ASUS, Lenovo, Dell, HP, MSI, Razer, Surface)
and needs no admin rights. Some laptops don't report cycle counts or design capacity; the page shows a dash
for anything Windows doesn't provide.

The Windows power mode slider (Best efficiency, Balanced, Best performance), power plans and refresh-rate
switching are part of the main app, since they help desktops too.

## Install

Download `UltraSuite-LaptopPack-<version>-x64.exe` from the
[latest release](https://github.com/jeemwla/ultra-productivity-suite/releases/latest) and run it. It installs to
`%APPDATA%\UltraSuite\addons\laptop_pack` and restarts Ultra Suite. Remove it from Windows Settings > Apps.

## For developers

`laptop_pack.py` exposes `register(app)` for the Sentinel FastAPI app and these routes:
`GET /api/laptop/battery`, `GET /api/laptop/battery/history`, `GET/POST /api/laptop/brightness`, `GET /api/laptop/about`.
Run `python laptop_pack.py` to print what it reads on the current PC.
Build the installer with `powershell -File build_laptop_pack.ps1` from the repo root.
