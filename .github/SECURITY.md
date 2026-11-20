# Security policy

## Supported versions

Security fixes go into the latest public release on the [releases page](https://github.com/jeemwla/ultra-productivity-suite/releases/latest). Older installers do not receive separate patches; the in-app updater and the releases page provide the fixed version.

## Reporting a vulnerability

Please report security problems privately through [Report a vulnerability](https://github.com/jeemwla/ultra-productivity-suite/security/advisories/new) on the Security tab. Public issues, discussions and pull requests are visible to everyone and are not the right place for an unfixed problem.

A useful report includes:

- the app and version (shown in the hub, or in Windows Settings > Apps > Installed apps)
- what an attacker can do, and what access they need first (local user, same network, a web page in a browser)
- steps or a small proof of concept
- whether the problem needs the Public or the Dev edition

Areas of particular interest are the local API guard in the background service, the updater and installer, local WebSocket endpoints, and anything that could stop Code Red Alarm from ringing.

The maintainer works on this project alone and in spare time, so there is no fixed response deadline. Reports are acknowledged in the advisory thread, and a fix is coordinated there before any public disclosure. Credit is given in the advisory unless you ask otherwise.

## Verifying downloads

Installers built by the on-demand GitHub Actions build carry a signed build provenance attestation. It can be checked with the GitHub CLI:

```
gh attestation verify <installer.exe> --repo jeemwla/ultra-productivity-suite
```
