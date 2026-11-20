# Code Red Alarm

Alarms for people who sleep through alarms. Works with any speaker or headphones.

- Any output. Pick it under Sound (system default, speakers, headphones, monitor audio). Test plays 3 seconds on it.
- Optional full volume lock, off by default. When enabled, the chosen output is unmuted and held at 100% while ringing. Its previous volume is restored afterwards.
- Code Red mode. The alarm only stops when you type your phrase exactly. Paste and drop are blocked. The ringing screen shows how much of the phrase is right so far, and curly quotes or dashes from a phone keyboard count as the plain ones. Code Red alarms never snooze.
- Snooze, off by default. Choose 5, 9, 10 or 15 minutes, up to 3 times per ring. A snooze is saved, so it still rings after a restart, and the PC wakes for it like any alarm. If nobody stops a ring, the alarm takes its next snooze by itself instead of going quiet.
- Wake-up math (optional), repeat on chosen days or a single date, ring length from 1 minute to 1 hour, and Preview to hear an alarm for 10 seconds.
- Edit in place, with a status on every alarm: "Rings in 3h 20m", "Snoozed until 7:14 AM", "Rang" or "Missed". A missed one-time alarm has a Reschedule button, and Delete can be undone for a few seconds.
- Recent activity lists rings, stops, snoozes and missed alarms. An alarm missed while the PC was off or asleep for more than 30 minutes is logged and shown in a banner instead of being skipped silently.
- Scrolling time picker. Choose an hour, minute and AM/PM in the app's themed dialog. The minute and hour columns loop. Arrow keys, Page Up/Down and Home/End work, Left and Right move between columns, and Enter sets the time. Cancel or Escape leaves it unchanged.
- Timers. Choose a preset, a recently used length, or any length from 1 second to 24 hours. Several can run at once; each shows when it ends. Timers are saved locally, keep counting with the window closed, and use the chosen alarm speaker. An alarm already ringing takes priority. A completion missed during sleep or restart is recovered within 30 minutes; older completions are marked finished without sound.
- Reliable timing. Alarms follow the Windows clock and time zone, including daylight saving changes. An alarm missed while the PC was busy, waking up or restarting still rings, up to 30 minutes late. If the alarm engine's clock ever disagrees with your device, a red banner says so. Times the scheduler could never ring (a misspelled day, an impossible or past date) are refused when saving.
- When an alarm rings, its window jumps in front of everything. If the speaker helper fails, the open page rings instead and says why.

## Parts

| File | Role |
|---|---|
| `backend/main.py` | API on `127.0.0.1:8081`: alarms (SQLite), scheduler, live updates over WebSocket, speaker settings |
| `backend/clock.py` | Local time straight from Windows, and the "is this alarm due?" rule (unit tested) |
| `backend/audio_enforcer.py` | Speaker helper on `127.0.0.1:8091`: lists outputs, plays the siren on the chosen one ([sounddevice](https://python-sounddevice.readthedocs.io/)), volume lock ([pycaw](https://github.com/AndreMiras/pycaw)), runs the VPN script |
| `frontend/index.html` | The app's page (every theme, desktop and phone sizes) |

`python backend/audio_enforcer.py --list-devices` prints the outputs this PC has.

Fresh installations start with no saved sample alarms. A new alarm draft has no repeat days selected,
Code Red off and Wake-up math off. It uses a single date until repeat days are explicitly chosen.
Browser ringing and forced full volume are off by default. Public installs use the Windows default audio
output unless a user chooses another device. Existing saved alarms and explicit audio preferences are preserved.
The timer starts idle. Dev edition blocks timer delivery and browser playback while retaining saved preferences.
