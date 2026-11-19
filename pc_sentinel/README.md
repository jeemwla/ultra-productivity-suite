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
| `backend/main.py` | API on `127.0.0.1:8084`; one background loop polls every second and every open window gets the same snapshot, plus the last two minutes for the charts (`/api/history`) |
| `backend/lhm_collector.py` | Sensor sources (LibreHardwareMonitor web server, then WMI, then Windows counters), grouping, health score |
| `backend/native_sensors.py` | Windows performance counters for any GPU vendor, device info, other monitoring apps |
| `backend/storage_health.py` | Drive health and wear, merged with LibreHardwareMonitor SMART data |
| `backend/system_tools.py` | Top apps (CPU, GPU, video memory), power plans, power mode, refresh rate, LibreHardwareMonitor setup |
| `backend/laptop.py` | Battery page: health, cycles, watts, history (Windows battery report), brightness |
| `backend/net_monitor.py`, `speedtest.py` | Latency checks (`PING_TARGETS`) and the speed test |
| `backend/glinet_vpn.py`, `glinet_api.py` | Optional VPN button: runs the PowerShell script set in `ONESCRIPT_PATH` |
| `frontend/index.html` | The app's page |
| `frontend/battery_view.js`, `edit_layout.js`, `sentinel_panels.js`, `psu_picker.js` | Battery page, Overview layout editor, measurement and writer panels, PSU catalog picker |
| `dev_preview.py` | Developer preview with fixture data (not packaged) |

## How it behaves

- Each sensor source has its own deadline. If one stops answering, its last reading stays on screen marked as late, a banner says so, and the other readings keep updating. If the page loses its connection, a banner shows the time of the last reading.
- Alerts (System page) fire when a reading stays over its limit for 5 seconds, then wait 5 minutes before repeating. Limits stay inside each field's range.
- Motion follows the Appearance setting (Full, Reduced, Off) and the Windows animation setting: with either one reduced, numbers change without animating, fans stop spinning and page transitions are off.
- Closing the page during a speed test stops the test.
- Requests that change Windows (power plan, power mode, refresh rate, brightness) reject malformed values with a clear message and never guess a value.

## Developer preview

Every page, including the Battery page and the laptop Cooling note, can be checked on any PC with fixture data and without changing anything on it:

```powershell
cd pc_sentinel
$env:SENTINEL_PREVIEW = "laptop"   # or "desktop"
$env:ULTRASUITE_CONF = "$env:TEMP\sentinel-preview"   # required: settings saved in preview go here
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8098
```

The fixture is `tests/fixtures/sentinel_preview.json` (captured from a desktop with names, serial numbers and paths replaced). In the laptop preview the battery cycles between on battery, charging and plugged in every 45 seconds; `SENTINEL_PREVIEW_BATTERY=discharging`, `charging` or `plugged` holds one state. Power plan, power mode, refresh rate and brightness changes stay in memory; installing sensors, Max Performance, the measurement and router controls answer that preview mode keeps them off.

## Tests

From the repository root: `python -m unittest discover -s tests -p "test_*.py"` and `node --test tests/*.test.cjs`. Sentinel's own suites include `test_sentinel_hardening.py`, `test_sentinel_laptop.py`, `test_sentinel_extras.py`, `test_storage_writers.py`, `sentinel_ui_polish.test.cjs`, `battery_ui.test.cjs` and `storage_ui.test.cjs`. In a separate worktree, create an empty `private` folder first; some tests make temporary folders there.
