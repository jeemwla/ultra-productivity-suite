# PC Sentinel

Your PC's health and your connection at a glance. Works on any PC: Intel or AMD, any graphics card, desktop or laptop.

Sections (keys 1 to 9 jump between them):

1. **Overview:** a one-line health verdict, live CPU, GPU, memory and power tiles, top apps by CPU, GPU and memory, quick controls.
2. **Performance:** two-minute charts with hover values, per-thread load, temperatures.
3. **Cooling:** every fan with its RPM, % of its maximum and FanControl curve; rename fans.
4. **Power:** system power (measured plus estimated), PSU headroom, the Windows power mode slider, power plans, refresh-rate switching per monitor.
5. **Storage:** health, life left, temperature, hours and data written for every drive.
6. **Network:** latency to several targets with sparklines, throughput, an optional VPN button.
7. **Speed test:** about 25 seconds of ping, download and upload, with a bufferbloat grade and history.
8. **Sensors:** every sensor grouped by part, with search, filters and hide/show.
9. **Battery** (only on PCs with a battery): charge, health and wear, charge cycles, charge and discharge watts, time left, health history, screen brightness.
10. **System:** device details, sensor source, alert thresholds, updates, an exportable report, feedback.

Sensors come from [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) when it runs (one-click setup on the System page), otherwise from Windows' own counters. When a value isn't available, Sentinel says so instead of making one up.

## Parts

| File | Role |
|---|---|
| `backend/main.py` | API on `127.0.0.1:8084`; one background loop polls every second and every open window gets the same snapshot |
| `backend/lhm_collector.py` | Sensor sources (LibreHardwareMonitor web server, then WMI, then Windows counters), grouping, health score |
| `backend/native_sensors.py` | Windows performance counters for any GPU vendor, device info, other monitoring apps |
| `backend/storage_health.py` | Drive health and wear, merged with LibreHardwareMonitor SMART data |
| `backend/system_tools.py` | Top apps (CPU, GPU, video memory), power plans, power mode, refresh rate, LibreHardwareMonitor setup |
| `backend/laptop.py` | Battery page: health, cycles, watts, history (Windows battery report), brightness |
| `backend/net_monitor.py`, `speedtest.py` | Latency checks (`PING_TARGETS`) and the speed test |
| `backend/glinet_vpn.py`, `glinet_api.py` | Optional VPN button: runs the PowerShell script set in `ONESCRIPT_PATH` |
| `frontend/index.html` | The app's page |
