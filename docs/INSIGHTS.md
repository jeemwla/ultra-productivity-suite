# Ultra Suite Insights

Insights is the maintainer's dashboard. It is not one of the public apps and it does not run on users' PCs. The
core loads it only when `ULTRASUITE_OWNER=1` is set in settings.env, or in the Dev edition, where it is a local
sandbox with its own ports and database that never receives public events.

## Parts

The collector (127.0.0.1:8300, Dev 18300) receives events. The owner exposes it through Tailscale Funnel, so
`POST /v1/event` and `POST /ultrasuite/v1/event` accept a JSON object with:

| Field | Meaning | Stored as |
|---|---|---|
| `type` | `install`, `uninstall`, `feedback` or `heartbeat` (anything else is refused) | as sent |
| `version`, `os` | app version and Windows version | first 20 characters |
| `reason` | uninstall reason or `app:kind` for feedback | first 40 characters |
| `message` | feedback text | first 1,000 characters, line breaks kept |
| `id` | random per-PC identifier | first 64 characters |

Control characters are removed before storing. Bodies over 4 KB, non-object JSON and unknown types get a 4xx
answer. Each identifier may send 30 events an hour and each sender address 120. The address is kept in memory for
rate limiting only and never written to the database. Uvicorn resolves the address from the local Funnel proxy's
`X-Forwarded-For`, so a value a sender writes into that header can't choose the rate-limit key.

The dashboard (127.0.0.1:8301, Dev 18301) is private and answers only local requests.

## The dashboard page

The header shows the title (with a red Dev sandbox badge in Dev), the Appearance button, a live status chip, an
Export CSV button and a Day, Week, Month, Year switch. The chosen period is remembered.

Below it, six number cards: Installs and Uninstalls in the period (with all-time and net installed counts), PCs
active in the last 7 days, GitHub release downloads, Feedback, and Stars with forks and watchers.

The next row has a line chart of installs, uninstalls and feedback per hour (Day) or per day, and a bar list of
uninstall reasons. Hovering, touching or using the arrow keys on the chart shows the numbers for one point; screen
readers hear the same text.

Then the Feedback inbox, newest first, with a Remove button on each message (it asks before deleting), and the
GitHub card with downloads, stars, forks, traffic when a token allows it, and the newest three releases. Problems
with GitHub (rate limit, rejected token, missing access to traffic) are explained under the card, and the last good
numbers stay visible.

The last card lists the 40 most recent events in the period, including heartbeats.

When the dashboard can't refresh, the status chip turns red and says when the shown data was fetched; its tooltip
says why. Refreshing pauses while the tab is hidden.

## GitHub numbers

Public numbers (stars, forks, release downloads across all release pages) need no setup. Page views, visitors,
clones and referrers need `GITHUB_TOKEN` in settings.env, with access GitHub requires for repository traffic
(push access). Numbers are cached for 10 minutes; after a failure they are retried after 1 minute.
`INSIGHTS_REPO` changes the repository (default `jeemwla/ultra-productivity-suite`).

## Data and cleanup

Events are stored in `insights.db` in the edition's data folder (SQLite in WAL mode).

- `GET /api/export.csv?range=day|week|month|year` downloads the period's events. Cells that start like a
  spreadsheet formula are prefixed with `'` so they are shown as text.
- `POST /api/delete` with `{"anon": "<id>"}` removes every event from one identifier (useful for test events), or
  `{"ids": [1, 2]}` removes up to 500 events by number. Invalid requests get a 400 and change nothing.

## Running it alone

~~~powershell
python insights/insights.py
~~~

This serves the collector and dashboard without the rest of the core. `INSIGHTS_COLLECTOR_PORT` and
`INSIGHTS_DASHBOARD_PORT` choose other ports, and `ULTRASUITE_DATA` chooses another data folder, for example a
temporary one with sample events. A page on another port also needs that port listed in `ULTRASUITE_ORIGIN_PORTS`
before it can delete events.
