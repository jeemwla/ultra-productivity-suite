# Troubleshooting

Start with the core log: `%LOCALAPPDATA%\UltraSuite\data\core.log` (Dev: `UltraSuite-Dev`). Each line has a time.
The previous log, when it grew past 5 MB, is `core.log.1`. [CORE.md](CORE.md) explains the main log lines.

## An app window stays empty or says it can't connect

The window needs its service on 127.0.0.1 (Alarm 8081, Sentinel 8084; Dev adds 10000). Look in core.log for
`port ... is already in use`. Another program, or a leftover core, holds the port. Choose Quit in the Ultra Dock
menu and open Ultra Suite again from Start. If the line names a port that another program needs, that program has
to move, because the suite's ports are fixed per edition.

Check which process holds a port:

~~~powershell
Get-NetTCPConnection -LocalPort 8084 -State Listen | Select-Object OwningProcess
~~~

## Alarms only ring in open windows

The speaker helper (port 8091) is not responding. Code Red Alarm's Sound card shows a warning when this happens.
Quit from the Ultra Dock menu and start the suite again. If core.log says port 8091 is in use, another program holds
it. The Dev edition never rings by design.

## Sentinel shows few temperatures or fans

Windows counters give a limited set of readings. Sentinel's sensor setup, or the free LibreHardwareMonitor running
with its web server on port 8085, adds temperatures, fans and power sensors. Readings a source can't provide stay
unavailable instead of showing zero.

## Settings changes seem ignored

`settings.env` is read when the core starts, so restart the suite after editing it. Values that belong to the edition
(ports, data folder, Dev gates) can't be changed there. A line without `=` is ignored. If the file can't be read at
all, core.log says so and the core uses defaults.

## Appearance resets to System

The shared appearance file is `theme.json` in the data folder. If it was damaged or edited by hand, each field that
isn't a known choice falls back to its default. Choosing the theme again in any app rewrites it.

## Feedback doesn't send

The message must be 3 to 1,000 characters. Feedback is off in the Dev edition and in builds without a collector.
"Too much feedback from this PC" means the hourly limit was reached; try again later.

## Insights shows a GitHub warning

The note under the GitHub card says what went wrong. A rate limit clears by itself, a rejected token needs a new
`GITHUB_TOKEN` (or none, for public numbers), and traffic numbers need a token with push access. See
[INSIGHTS.md](INSIGHTS.md).
