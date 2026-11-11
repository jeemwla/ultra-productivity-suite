# Ultra Suite launch plan

## One-line pitch

**Your PC, organized, monitored and impossible to oversleep, in one free download.**

## Who it's for

- PC gamers who want a clean taskbar, live temps and honest ping/bufferbloat numbers
- Students who sleep through alarms and juggle school and gaming on one PC
- Stream deck owners (deck) who want useful knobs without scripting

## The apps and why people should download them

| App | The problem | Why Ultra Suite's answer is better |
|---|---|---|
| **Ultra Dock** | Taskbars fill up with every open app. Windows 11 removed toolbars and taskbar folders. | A taskbar organizer: your main apps stay as normal buttons and every other open app is grouped into phone-style folders (Games, Chat, AI, Web, Create, Tools) on the right of your icons. Click a folder and its apps slide open in place. It hides during fullscreen games and puts every button back when it quits. |
| **Code Red Alarm** | PC alarms are easy to silence, and phone alarms get snoozed in bed. | It forces the chosen speaker to full volume, blocks the volume keys while ringing, and Code Red mode only stops when you type a phrase (paste is blocked), so you have to get up. Works from your phone over Tailscale, and puts your volume back afterwards. |
| **PC Sentinel** | Hardware tools are either too technical (sensor dumps) or too shallow, and speed tests ignore lag under load. | A plain-language health verdict, then every fan, curve and wattage when you want detail. Works on any PC (Windows' own sensors when LibreHardwareMonitor isn't installed), shows top apps by CPU, GPU and memory, drive health and wear, and switches power plans, the power mode and refresh rates in one click. A 25-second speed test that matched Ookla, fast.com and OpenSpeedTest within a few percent in testing, plus a bufferbloat grade and "good for gaming / 4K / calls" ratings. Everything animates smoothly and updates every second. |
| **Laptop Pack** (add-on) | Battery tools are buried in brand apps (Armoury Crate, Vantage) or command lines. | Battery health against design capacity, wear, cycles, live watts, time left and a health history, on any laptop brand, in the same app as everything else. |
| **deck Deck Setup** | Stream deck knobs take plugins, scripting and manual layout. | One click installs a working profile: Spotify, Discord and master volume knobs, a PC-stats knob that toggles your router VPN, and a Gaming folder. |

## Honest positioning against what people use today

- Taskbar tools generally either restyle the taskbar or add a separate dock. Ultra Dock does neither: it organizes the buttons already in your taskbar into folders.
- Monitoring apps usually show either a gaming overlay or a full sensor list. PC Sentinel gives each its own section (Overview, Performance, Cooling, Power, Storage, Network, Sensors) and adds network quality.
- Popular speed test sites measure speed; few grade latency under load. PC Sentinel shows both, built into the same app as your hardware health.
- Everything runs locally. No account, no ads, open source (MIT).

## Launch checklist

1. GitHub release with installer (done: v2.0.0), README with a 20-second GIF of the taskbar groups sliding open
2. Short demo video: taskbar groups, alarm phrase lock, speed test gauge
3. Post where the audience is: r/Windows11, r/pcmasterrace, r/buildapc (Sentinel angle), deck user groups, Discord servers you're in
4. Ask 5 friends to install, then read their uninstall reasons and feedback (opt-in) before a wider post
5. Product Hunt once the Insights dashboard and feedback button are live

## What to measure

- Downloads per release (GitHub), install count (opt-in), uninstall count and reasons (opt-in survey)
- Feedback messages per week; top requested features
- 7-day retention: installs that are still sending the opt-in weekly heartbeat (planned)

## If people have no reason to keep it: improvement backlog

Priority order, based on the gaps found while building:

1. **Feedback button + Insights dashboard** so every complaint becomes a fix (next session)
2. **Drag to reorder and add apps** in the taskbar groups (no JSON editing)
3. **Top and side taskbar support** for Ultra Dock
4. **Sentinel alerts**: notify when CPU/GPU gets hot or ping spikes mid-game
5. **Beeper unread badges** on the Chat group
6. **Alarm snooze from phone** with the phrase, and a weekly sleep log
7. **Auto-updates** through the Tauri updater
