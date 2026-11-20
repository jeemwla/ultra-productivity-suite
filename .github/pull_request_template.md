## Summary

<!-- What changes for users or maintainers, and why. Link any issue with "Fixes #123". -->

## Apps touched

- [ ] Ultra Dock
- [ ] PC Sentinel
- [ ] Code Red Alarm
- [ ] Insights or core service
- [ ] Installer, build or CI

## Tests

- [ ] `python -W ignore -m unittest discover -s tests`
- [ ] `node --test tests/*.test.cjs`
- [ ] `cargo test --release` in `ultra_dock/src-tauri` (Public and `--features dev-edition`)
- [ ] UI changes checked at desktop and 1366x768, light and dark, with reduced motion (screenshots below)

## Safety

- [ ] No alarm rang on real speakers while testing; tests used isolated data and fake audio
- [ ] Nothing from `private/`, `build/`, per-PC data or a Dev installer is included
- [ ] `VERSION` is unchanged, or this is a release prepared by the release owner
- [ ] Existing features stay available; new behavior is an option where it could surprise users

## Screenshots

<!-- Before and after, for visible changes. -->
