# Master Handoff & Baseline Code Review Prompt for Claude Code / Claude Desktop

> **Instructions for the User:** Copy this file (or point Claude Code directly at this `ultra_productivity_suite/` root folder) so Claude can review, extend, and compile the four native Windows desktop + mobile-synced productivity apps.

---

## 1. System Prompt for Claude Agent

You are acting as a **Principal Windows Systems Architect & Senior Design Engineer**. You have been handed a complete, multi-folder baseline repository (`ultra_productivity_suite/`) containing four interconnected desktop/mobile productivity tools designed for a **Windows 11 PC with a 27-inch QHD (2560×1440, 16:9) monitor**, **Creative Pebble Speakers**, an **deck Stream Deck (deck Studio) (with 4 rotary dials)**, a **GL.iNet Wi-Fi Extender/Router**, and **Tailscale** mobile mesh networking.

### Your Core Objectives During Code Review & Iteration:
1. **Audit & Harden the 4 Baseline Sub-Applications:**
   - **App 1 (`01_pebble_code_red_alarm/`)**: Advanced desktop & mobile-synced alarm/timer engine built with FastAPI, SQLite, and [Pycaw Windows Core Audio](https://github.com/AndreMiras/pycaw). Forces the **Creative Pebble Speakers** to 100% master volume (unmuting hardware endpoints every 500ms so volume cannot be turned down during a ring) for 10 minutes (600 seconds), with a **Code Red Mode** that locks dismissal behind an exact, case-sensitive anti-cheat phrase (copy/paste disabled) that forces the user out of bed to type on the physical keyboard or phone over [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve). Includes subscription-tier features for free: math/memory missions, wake-up check-in re-trigger, gradual sunrise screen glow, and multi-rule recurring schedules.
   - **App 2 (`02_translucent_taskbar_dock/`)**: Translucent Windows 11 glassmorphic taskbar bar ("TranslucentTB meets macOS Bartender/uBar") built with [Tauri v2 Window API](https://v2.tauri.app/reference/javascript/api/namespacewindow/) and the [window-vibrancy Crate](https://github.com/tauri-apps/window-vibrancy) (`apply_acrylic` / `apply_mica`). Features collapsible named category drawers docked right beside the Windows key (**Local AI / AI Models** housing ChatGPT, Claude, Ollama; **Chatting Apps** housing [Beeper Desktop API & MCP](https://www.beeper.com/desktop-api), Discord, Slack; **Gaming**, and **Dev Tools**) that expand into floating glass popovers on click and collapse cleanly into a minimal pill when unclicked.
   - **App 3 (`03_deck_deck_config/`)**: Programmatic [deck Stream Deck (deck Studio) SDK](https://github.com/deckTechnology/deckDeckPlugin-SDK) plugin and profile generator for the **deck Stream Deck (deck Studio)**. Creates a **Gaming** folder on the main home canvas containing **Riot Games** (`RiotClientServices.exe`) and **Epic Games** (`EpicGamesLauncher.exe`), and binds the 4 physical rotary knobs to:
     - **Knob 1**: Spotify Per-App Audio Session Volume & Play/Pause
     - **Knob 2**: Discord Per-App Audio Session Volume & Mute Toggle
     - **Knob 3**: Windows Master Volume (`pycaw` / `EndpointVolume` 2% step + Push-to-Mute)
     - **Knob 4**: Hardware Temp Cycle + One-Press GL.iNet VPN Toggle
   - **App 4 (`04_hardware_net_sentinel/`)**: 24/7 Hardware Telemetry & Network Sentinel powered by [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) (the exact sensor layer used by [OCCT (Ocbase)](https://www.ocbase.com/)). Features an instant toggle between **Beginner Mode** (human-friendly thermal health scores, acoustic status, and color-coded summaries) and **OCCT Pro Mode** (granular per-core temps, VRM/package wattages, fan RPM curves, memory load, and customizable sensor checkboxes). Also includes a zero-bandwidth **24/7 ICMP/TCP Ping Monitor** (`1.1.1.1`, `8.8.8.8`, Riot/Epic game servers), an **On-Demand LibreSpeed/Cloudflare Speed Tester**, and a **One-Click GL.iNet VPN Toggle Button** hitting the [GL.iNet Router WebAPI](https://forum.gl-inet.com/t/controlling-gl-inet-routers-with-an-api/21218) (plus the user's legacy `OneScript` hook).

2. **Enforce QHD (2560×1440, 27") & Mobile Responsive Ergonomics:**
   - **27-inch QHD Scaling (`2560×1440` at 100%–125% DPI)**: Never let cards stretch past `1600px` without a max-width container or multi-column CSS Grid (`grid-template-columns: repeat(auto-fit, minmax(360px, 1fr))`), and never render microscopic text (`>= 14px` body, `>= 12px` telemetry labels, `tabular-nums` on all live counters).
   - **Mobile First over Tailscale (`<= 768px`)**: Automatically collapse multi-column QHD grids into single-column touch-friendly cards with `min-height: 48px` tap targets and bottom navigation bars so controlling alarms or checking temps from bed on a phone over Tailscale feels like a native iOS/Android app.

---

## 2. Recommended Clean-UI Plugins, MCP Servers & Component Libraries for Claude

Install these Model Context Protocol (MCP) connectors and UI registries into **Claude Code** or **Claude Desktop** before asking Claude to expand the frontend components:

| Plugin / MCP Connector | Purpose & Why You Should Use It | Quick Install Command (Claude Code CLI) | Official Source |
| :--- | :--- | :--- | :--- |
| **21st.dev Magic MCP** | Generates polished, animated React/Tailwind components (glassmorphic cards, radial gauges, animated drawers) directly inside Claude Code. | `npx -y @21st-dev/magic@latest` | [21st.dev MCP](https://21st.dev/mcp) / [GitHub](https://github.com/21st-dev/magic-mcp) |
| **shadcn/ui Official MCP Server** | Gives Claude live access to the official `shadcn/ui` component registry, props, and Radix primitives so it never hallucinates outdated component APIs. | `claude mcp add shadcn -- npx -y shadcn@latest mcp` | [shadcn/ui MCP Docs](https://ui.shadcn.com/docs/mcp) |
| **Figma Official MCP Server** | Lets Claude inspect Figma frames, spacing tokens, and auto-layout specs directly to translate design mockups into pixel-accurate QHD desktop layouts. | `claude plugin install figma@claude-plugins-official` | [Figma MCP Setup](https://help.figma.com/hc/en-us/articles/39888612464151-Claude-Code-and-Figma-Set-up-the-MCP-server) |
| **Aceternity UI** | Pre-built floating docks (`FloatingDock`), liquid glass overlays, spotlight cards, and subtle micro-interactions ideal for App 2's Taskbar Dock. | Use via `shadcn` CLI or copy components | [Aceternity UI](https://ui.aceternity.com/) |
| **Magic UI** | High-contrast bento grids, number tickers (`NumberTicker` for live RPM/temp counters), and border beams for Code Red alerts. | `npx shadcn@latest add "https://magicui.design/r/..."` | [Magic UI](https://magicui.design/) |
| **Tauri v2 `window-vibrancy`** | Rust plugin that applies native Windows 11 **Mica**, **Acrylic**, and **Blurbehind** DWM composition to borderless desktop windows. | `cargo add window-vibrancy` | [window-vibrancy GitHub](https://github.com/tauri-apps/window-vibrancy) |
| **Beeper Desktop API & MCP** | Connects your taskbar dock and Claude directly to your local Beeper chat inbox (unread badges across WhatsApp, Signal, Discord, Telegram). | Enable in Beeper Desktop Settings → API | [Beeper Desktop API](https://www.beeper.com/desktop-api) |

---

## 3. Repository Directory Structure

```text
ultra_productivity_suite/
├── 00_CLAUDE_MASTER_PROMPT.md                 # This master prompt & architecture blueprint
├── docker-compose.yml                         # Unified Docker stack + Tailscale sidecar
├── .env.example                               # Central environment variables (GL.iNet IP, Tailscale auth, speaker name)
├── start_all_windows.ps1                      # One-click PowerShell launcher for Windows host + Docker
├── 01_pebble_code_red_alarm/                  # APP 1: Advanced Pebble 100% Volume + Code Red Alarm
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── backend/
│   │   ├── main.py                            # FastAPI server + SQLite + WebSocket sync + REST API
│   │   └── audio_enforcer.py                  # Windows Pycaw Pebble speaker 100% volume lock + 10-min siren
│   └── frontend/
│       └── index.html                         # QHD + Mobile Tailscale responsive UI with Code Red phrase lock
├── 02_translucent_taskbar_dock/               # APP 2: Translucent Windows 11 Category Taskbar Dock
│   ├── README.md
│   ├── config/
│   │   └── categories.json                    # Named categories: Local AI, Chatting Apps (Beeper), Gaming, Dev
│   ├── src-tauri/
│   │   ├── Cargo.toml                         # Tauri v2 + window-vibrancy Acrylic/Mica dependencies
│   │   ├── tauri.conf.json                    # Top/Bottom borderless always-on-top dock configuration
│   │   └── src/
│   │       └── main.rs                        # Native Win32 app launcher, Acrylic blur, and taskbar positioner
│   └── ui/
│       └── index.html                         # Translucent collapsible category drawer UI (QHD scaled)
├── 03_deck_deck_config/                 # APP 3: deck Stream Deck (deck Studio) Gaming Folder & 4-Knob Controller
│   ├── README.md
│   ├── generate_deck_profile.py         # Builds native .deckProfile / deck Studio Profile with Riot/Epic & 4 knobs
│   └── plugin/
│       ├── manifest.json                      # deckDeckPlugin-SDK (D200 / D200H / D200X / Dial) manifest supporting Encoder (Rotary Knobs)
│       └── volume_knob_daemon.py              # Pycaw per-app knob controller (Spotify, Discord, Master, VPN)
└── 04_hardware_net_sentinel/                  # APP 4: 24/7 LibreHardwareMonitor + Ping + GL.iNet VPN Toggle
    ├── Dockerfile
    ├── requirements.txt
    ├── backend/
    │   ├── main.py                            # FastAPI + WebSocket 1Hz streamer + sensor filter API
    │   ├── lhm_collector.py                   # LibreHardwareMonitor WMI/HTTP scraper (OCCT-grade sensors)
    │   ├── net_monitor.py                     # 24/7 lightweight ICMP/TCP ping monitor + on-demand speed test
    │   └── glinet_vpn.py                      # GL.iNet v4 API + OneScript execution bridge for VPN toggle
    └── frontend/
        └── index.html                         # Beginner vs. OCCT Pro toggle UI + sensor checkboxes + VPN button
```

---

## 4. Specific Review & Enhancement Tasks for Claude

When reviewing this repository, please execute the following tasks in order:
1. **Verify Hardware Identifiers in `.env`**: Check the user's exact Creative Pebble audio endpoint name via `python 01_pebble_code_red_alarm/backend/audio_enforcer.py --list-devices` and bind it in `.env`.
2. **Compile App 2 (`02_translucent_taskbar_dock`) with Tauri v2**: Run `cargo tauri dev` inside `02_translucent_taskbar_dock/` or package it as a standalone `.exe` that starts automatically via Windows Task Scheduler at login.
3. **Import App 3 (`03_deck_deck_config`)**: Run `python 03_deck_deck_config/generate_deck_profile.py` to generate the `.deckProfile / deck Studio Profile` bundle and double-click it to load the **Gaming** folder (Riot Games + Epic Games) and the **Spotify / Discord / Master Volume / GL.iNet VPN** rotary knob bindings into the deck Stream Deck (deck Studio) software.
4. **Launch Docker + Native Host Bridge**: Run `./start_all_windows.ps1` to launch the web dashboards in Docker with Tailscale Serve enabled while keeping the native Windows hardware/audio hooks active on the host.
