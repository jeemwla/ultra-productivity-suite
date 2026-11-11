# Ultra Productivity Suite

Four small Windows apps that work together: a wake-up alarm you can't ignore, a translucent app dock for the taskbar, deck Stream Deck knobs, and a live hardware and network monitor. Everything runs on your own PC. Nothing is sent to any cloud service.

| App | What it does | Opens at |
|---|---|---|
| **Code Red Alarm** | Alarms that force your chosen speaker to full volume and, in Code Red mode, only stop when you type a phrase (no pasting). Works from your phone over Tailscale. | http://localhost:8081 |
| **Ultra Dock** | A translucent bar above the taskbar that groups your apps (AI, Chat, Games, Tools) into drawers. It finds which apps you have installed by itself. | starts with Windows |
| **deck knobs** | Plugin and profile for deck decks: Spotify, Discord and master volume knobs, a PC-stats knob that toggles your VPN on press, and a Gaming folder (Riot, Epic). | inside deck Studio |
| **PC Sentinel** | CPU, GPU, memory, fan and drive health in plain language (Simple) or every sensor (Detailed), plus live latency, a speed test and a VPN button. | http://localhost:8084 |

## Install (any Windows 10 or 11 PC)

1. Download this repo: **Code → Download ZIP**, then unzip it somewhere permanent (for example `Documents\ultra-productivity-suite`).
2. Double-click **`Setup.cmd`**.
3. Click **Yes** when Windows asks for admin approval (once, so hardware sensors can start at sign-in).

Setup installs whatever is missing (Python, LibreHardwareMonitor), configures the suite for your PC, installs the dock and the deck plugin if you have deck Studio, turns on start-at-sign-in, and opens both dashboards. Running it again is safe.

After that, everything starts on its own when you sign in. To start it by hand, double-click **`Start Suite.cmd`**.

### Choose your speakers

Open the alarm app and go to **Sound**. Pick any output (or *System default*), then press **Test**. The alarm locks that speaker at 100% while ringing and puts your volume back afterwards.

### Use it from your phone

Install [Tailscale](https://tailscale.com/download) on the PC and your phone and sign in to the same account. The suite publishes both dashboards to your private tailnet only (never the public internet):

- `https://<your-pc-name>.<your-tailnet>.ts.net:8443`: alarm
- `https://<your-pc-name>.<your-tailnet>.ts.net:8444`: sentinel

The exact links are printed each time the suite starts.

## Customize

| What | Where |
|---|---|
| Dock apps and groups | Dock **⋯ → Edit apps** (edits `%APPDATA%\UltraSuiteDock\apps.json`), then **⋯ → Reload apps** |
| Dock position | Dock **⋯ → Position** |
| Ping targets, router IP, VPN script | `.env` in this folder |
| VPN button | Set `ONESCRIPT_PATH` in `.env` to a PowerShell script that accepts `-State on` or `-State off` and exits 0 on success |
| Run the web apps in Docker instead | Set `SUITE_MODE=docker` in `.env` (needs Docker Desktop) |

## Troubleshooting

| Symptom | Fix |
|---|---|
| Alarm page says the speaker helper is offline | Double-click `Start Suite.cmd` |
| Sentinel says sensors are offline | Open LibreHardwareMonitor, turn on **Options → Remote Web Server**, keep port 8085 |
| deck knobs don't respond | In deck Studio, select the **Ultra Suite** profile. The suite must be running. |
| Something else | Look in the `logs` folder, or run `Start Suite.cmd` and read the status list |

## Uninstall

Run `uninstall.ps1` (right-click → Run with PowerShell), then delete this folder.

## Privacy and security

- All services listen on `127.0.0.1` only. Other devices on your Wi-Fi can't reach them; your phone reaches them only through your own Tailscale network.
- The speaker helper requires a private token that Setup generates per PC and keeps in `.env`. `.env` is never committed to git.
- The speed test downloads from Cloudflare only when you press the button. Latency checks send a few bytes every 2 seconds.

## For developers

```
01_pebble_code_red_alarm/   FastAPI app + host audio bridge (speaker picker, volume lock)
02_translucent_taskbar_dock/ Tauri 2 (Rust) dock; build with: npx @tauri-apps/cli@2 build --no-bundle
03_deck_deck_config/      deck plugin (Node), profile generator, knob daemon (Python)
04_hardware_net_sentinel/   FastAPI app reading LibreHardwareMonitor + latency monitor
start_all_windows.ps1       Starts everything (native by default, -Docker for containers)
setup.ps1 / Setup.cmd       One-time setup on a new PC
```

Requirements for building the dock yourself: Rust (stable, MSVC) and Visual Studio Build Tools with the C++ workload.

## License

MIT. See [LICENSE](LICENSE). Bundles the MIT-licensed deck plugin SDK and the `ws` library inside the deck plugin folder.
