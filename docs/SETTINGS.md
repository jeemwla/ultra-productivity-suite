# Settings / Extras

Open the gear beside Appearance in PC Sentinel's sidebar or compact navigation bar.
Settings / Extras always remains available, even when pages are hidden.

- Select which pages appear, then choose Save settings. At least one page must remain visible.
- Battery can follow automatic battery detection, always show, or stay hidden.
- Router VPN controls and router latency are off in a fresh public installation. Enable them only for a router
  that is already configured separately. Enabling the controls does not change the current VPN connection.
- Reset draft to defaults only changes the form. Save settings applies it.

These non-secret settings are saved in `%APPDATA%\UltraSuite\sentinel_preferences.json` and shared by Sentinel
windows on that PC. Newer saved revisions take precedence over older HTTP or WebSocket responses. Unsaved form
edits survive incoming telemetry. Failed saves keep the draft and the previous stored record.

Hiding a page changes navigation. It does not stop hardware monitoring, alarm services or an active speed test.
Disabling router extras stops router status polling or configured router latency probes, as appropriate.
The router VPN toggle endpoint rejects requests while its feature is disabled. These preferences are feature
choices, not authentication or a substitute for the existing loopback Host/Origin protections.

Neither a personal router address nor a password is written by this page. Router setup remains separate.
The public installer does not bundle the owner's preferences. Appearance and motion remain per-browser settings.
