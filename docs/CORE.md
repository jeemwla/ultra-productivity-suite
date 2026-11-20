# Ultra Suite core

The core is one background process that serves every app's local API. The installed Public edition runs it as
`ultrasuite-core.exe`; the Dev edition runs `ultrasuite-dev-core.exe`. Ultra Dock starts it and stops it.
From source it runs with `python core/suite_core.py` (add `--edition Dev` for the Dev profile).

## Services and ports

Every service listens on 127.0.0.1 only. Nothing in the core accepts connections from another computer.

| Service | Public port | Dev port | Notes |
|---|---|---|---|
| Code Red Alarm API and page | 8081 | 18081 | Own thread and event loop, so a slow Sentinel request can't delay an alarm |
| PC Sentinel API and page | 8084 | 18084 | Shares the second thread with Insights |
| Speaker helper | 8091 | 18091 | Plays alarms on the chosen output; Dev never rings |
| Local add-on (optional) | 8093 | 18093 | Runs only when a personal add-on file exists |
| Insights collector | 8300 | 18300 | Owner mode and Dev only, see [INSIGHTS.md](INSIGHTS.md) |
| Insights dashboard | 8301 | 18301 | Owner mode and Dev only |

`core/edition_profiles.json` is the single source for these numbers. To check an edition's ports and folders
without starting anything:

~~~powershell
python core/suite_core.py --edition Dev --describe
~~~

## Folders

| Edition | Settings | Data and logs |
|---|---|---|
| Public | `%APPDATA%\UltraSuite\settings.env` | `%LOCALAPPDATA%\UltraSuite\data` |
| Dev | `%APPDATA%\UltraSuite-Dev\settings.env` | `%LOCALAPPDATA%\UltraSuite-Dev\data` |

`settings.env` holds `KEY=value` lines. Blank lines and lines starting with `#` are ignored, a value may be
wrapped in single or double quotes, an `export ` prefix is accepted, and a later line wins over an earlier one.
If the file can't be read, the core logs that and starts with built-in defaults. Values that belong to the edition
(ports, data folder, delivery gates) always override what the file says.

## Logs

The installed core has no console, so it writes to `core.log` in the data folder. Every line starts with the local
date and time. When the file passes 5 MB at startup it is renamed to `core.log.1` (replacing the older copy) and a
new `core.log` starts, so at most about 10 MB of log is kept.

Useful lines:

- `services starting (N s after launch...)`: how long startup took before the services began to bind.
- `running: alarm (1), services (N)`: every service is ready.
- `port 8084 is already in use, so PC Sentinel was not started by this core`: another program, or a second
  core, holds that port. See [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
- `stop requested; stopping services` or `Ctrl+C; stopping services`, then `stopped`: a clean stop.

## Starting and stopping

At startup the core exits at once with `already running` when both the alarm and Sentinel ports answer. Otherwise
it starts the alarm group first and waits up to 20 s for it to be ready, then the other services.

A clean stop (Ctrl+C, Ctrl+Break or SIGTERM in a console) stops the services in reverse order, so the alarm
stops last. Open requests get up to 5 s to finish, then the speaker helper and add-on release their ports.
Ultra Dock currently ends the core process directly when it quits, so this ordered stop applies to console and
source runs.

## Local-only protections

Every app mounts the shared routes from `core/devtools.py`:

- Requests must be addressed to `127.0.0.1`, `localhost` or `[::1]`. A web page that points its own domain name at
  this PC (DNS rebinding) gets 403.
- Changes (POST and other non-GET methods) from a browser are accepted only from the suite's own pages. A website
  open in a browser can't change alarms, power settings or the router VPN.
- Websocket handshakes follow the same Host and Origin rules.
- Services do not send a `Server` header.

These checks are not authentication against other programs running as the same Windows user.

## Shared routes

| Route | Purpose |
|---|---|
| `/__ui/theme.js`, `/__ui/update.js`, `/__ui/icons/<name>.png` | Shared appearance code and app icons |
| `/api/theme` | The appearance chosen in any app (theme, motion, density), saved atomically to `theme.json` |
| `/api/about` | Version, edition and data folder |
| `/api/update`, `/api/update/install` | Public update check and verified install; disabled in Dev |
| `/api/feedback` | Alarm and Sentinel feedback, forwarded with the running version; disabled in Dev |
