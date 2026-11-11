# Code Red Alarm

Alarms for people who sleep through alarms.

- **Any speaker.** Pick the output under **Sound** (System default, speakers, headphones, monitor audio). **Test** plays 3 seconds on it.
- **Full volume lock.** While ringing, the chosen speaker is unmuted and held at 100% every 0.4 s, so the volume keys can't silence it. Your previous volume is restored afterwards.
- **Code Red mode.** The alarm only stops when you type your phrase exactly. Paste and drop are blocked.
- **Wake-up math** (optional), repeat on weekdays or a single date, ring length from 1 to 30 minutes, and **Preview** to hear an alarm for 10 seconds.
- **Phone control.** Open it on your phone over Tailscale. Every open page rings and can stop the alarm.

## Parts

| File | Role |
|---|---|
| `backend/main.py` | FastAPI app on port 8081: alarms (SQLite in `data/`), scheduler, WebSocket, speaker settings |
| `backend/audio_enforcer.py` | Runs on the PC itself on `127.0.0.1:8091`: lists outputs, plays the siren on the chosen one ([sounddevice](https://python-sounddevice.readthedocs.io/)), volume lock ([pycaw](https://github.com/AndreMiras/pycaw)), runs the VPN OneScript |
| `frontend/index.html` | The web app (light and dark, desktop and phone) |

`python backend/audio_enforcer.py --list-devices` prints the outputs this PC has.
