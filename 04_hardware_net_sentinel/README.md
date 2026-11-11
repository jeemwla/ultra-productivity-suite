# PC Sentinel

Hardware health and network quality at a glance, on the PC or your phone.

- **Simple view:** a one-line health verdict, then CPU, graphics, memory and cooling with plain-language status.
- **Detailed view:** every sensor LibreHardwareMonitor reports, grouped by component, with search, type filters and per-sensor hide (remembered in the browser).
- **Connection:** latency to Cloudflare, Google and your router every 2 seconds with a 60-second sparkline, jitter and packet loss. Uses a few bytes per check.
- **Speed test:** about 8 seconds of download from Cloudflare, only when you press the button.
- **VPN button:** runs the PowerShell script set in `ONESCRIPT_PATH` and reports real success or failure.

Works with any CPU and GPU that [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) supports (Intel, AMD, NVIDIA). If LibreHardwareMonitor isn't running, the page says so instead of showing made-up numbers.

## Parts

| File | Role |
|---|---|
| `backend/main.py` | FastAPI app on port 8084; one background loop polls every 2 s and every open page gets the same snapshot |
| `backend/lhm_collector.py` | Reads `http://127.0.0.1:8085/data.json`, groups sensors, computes the summary and health score |
| `backend/net_monitor.py` | TCP latency checks (`PING_TARGETS` in `.env`) and the speed test |
| `backend/glinet_vpn.py` | VPN button: asks the host bridge to run your OneScript |
