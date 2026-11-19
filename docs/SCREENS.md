# What each app looks like

Text descriptions of the current source's main screens, for readers using a screen reader and for checking a build
against the source. Every app follows the shared appearance: System, Light, Dark and eight more themes, plus a
motion setting (full, reduced, off), chosen from the Appearance button and shared by all apps on the PC.

## Ultra Suite hub

A left sidebar lists PC Sentinel, Code Red Alarm and Ultra Dock (and Insights in owner mode), each with a status dot,
and shows the version at the bottom. The main area greets the user, explains that it shows every app with what it is
doing now, and shows an update banner with an Update now button when a newer public release exists. One card per
app shows a status pill (it starts as Checking) and a live summary, such as the next alarm or a key sensor reading.

## PC Sentinel

A sidebar (a compact bar on narrow windows) lists the pages, each with a one-line description:

| Page | Shows |
|---|---|
| Overview | Health, live tiles and quick controls |
| Performance | CPU, graphics and memory over the last 2 minutes |
| Cooling | Every fan, how fast it spins, and what drives it |
| Power | System power, power plans and display refresh rates |
| Storage | Drive health, wear and space |
| Network | Latency, throughput and VPN |
| Speed test | Ping, download and upload in about 25 seconds |
| Sensors | Every sensor, organized by part |
| Battery | Charge, health and power use (shown when a battery is found, or by choice) |
| System | This PC, sensor source, alerts and updates |
| Settings / Extras | Choose pages and optional features for this PC ([details](SETTINGS.md)) |

Readings a source can't provide are shown as unavailable, never as zero.

## Code Red Alarm

The header reads "Alarms and timers that ring on your speaker", with connection and speaker status chips and an
Alarm / Timer switch.

The Alarm tab has a New alarm form (name, time, how long it rings, repeat days or a single date, Wake-up math off,
easy or hard, and Code Red with a phrase to type exactly, paste blocked), a Sound card (which speaker plays alarms,
a Test button, optional forced full volume and optional ringing in the browser) and the list of saved alarms. When
the speaker helper isn't responding, the Sound card says so and how to restart it.

The Timer tab has a New timer form (up to 24 hours, a name, how long it rings and optional Code Red) and the list of
running and paused timers with pause, resume and reset.

## Ultra Dock settings

The Dock settings window has four sections:

- Layout: grouping on or off, and how many apps keep their own button before the rest go into folders (minus and plus
  buttons around Automatic). Folders hold up to 6 apps and never just one.
- Open apps: each running app with its folder, or a normal taskbar button. Apps pinned in Windows stay as they are.
  Edit turns the list into a grid for making folders.
- Categories: folder order and the words that sort apps into them; unmatched apps go to Other.
- Always keep on the taskbar, and Advanced: open the settings file in Notepad, or reset categories, order and the
  keep list to the shipped defaults.

## Ultra Suite Insights (maintainer only)

See [INSIGHTS.md](INSIGHTS.md#the-dashboard-page).
