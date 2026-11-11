# Code Red Alarm

Alarms for people who sleep through alarms. Works with any speaker or headphones.

- **Any output.** Pick it under Sound (system default, speakers, headphones, monitor audio). Test plays 3 seconds on it.
- **Full volume lock.** While ringing, the chosen output is unmuted and held at 100%, so the volume keys can't silence it. Your previous volume comes back afterwards.
- **Code Red mode.** The alarm only stops when you type your phrase exactly. Paste and drop are blocked.
- **Wake-up math** (optional), repeat on weekdays or a single date, ring length from 1 to 30 minutes, and Preview to hear an alarm for 10 seconds.
- **Edit in place,** with a status on every alarm: "Rings in 3h 20m", "Rang", or "Missed".
- **Reliable timing.** Alarms follow the Windows clock and time zone. An alarm missed while the PC was busy or waking up still rings, up to 30 minutes late. If the alarm engine's clock ever disagrees with your device, a red banner says so.
- When an alarm rings, its window jumps in front of everything.

## Parts

| File | Role |
|---|---|
| `backend/main.py` | API on `127.0.0.1:8081`: alarms (SQLite), scheduler, live updates over WebSocket, speaker settings |
| `backend/clock.py` | Local time straight from Windows, and the "is this alarm due?" rule (unit tested) |
| `backend/audio_enforcer.py` | Speaker helper on `127.0.0.1:8091`: lists outputs, plays the siren on the chosen one ([sounddevice](https://python-sounddevice.readthedocs.io/)), volume lock ([pycaw](https://github.com/AndreMiras/pycaw)), runs the VPN script |
| `frontend/index.html` | The app's page (every theme, desktop and phone sizes) |

`python backend/audio_enforcer.py --list-devices` prints the outputs this PC has.
