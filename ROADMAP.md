# Development roadmap

288 detailed work items across 24 public development phases. This is a planning and acceptance checklist, not a list of shipped features or a fixed delivery schedule.

Required items address the product specification and known correctness gaps. Candidates remain proposed options. A checked source-only item still needs its separate installed or hardware acceptance. Tests and compatibility limits must support any product claim.

Current changes can be ahead of the downloadable release. Use the [latest release](https://github.com/jeemwla/ultra-productivity-suite/releases/latest) for an installer.

The desktop apps remain local-first. New dependencies must be free. Hardware controls require supported capabilities and explicit opt-in. Unknown sensor readings stay unknown. Taskbar access and alarm protection take precedence over new features.

## Q01: Local service security and trust

Constrain local service access and describe the intended trust boundary.

- [x] Q01-01 Protect all three WebSocket handshakes (Source only, f26e781). Reject disallowed Host and Origin values on Alarm, Sentinel and speed-test sockets using the exact suite origin list.
- [ ] Q01-02 Document origin-less native-client trust (Required). State the same-user local-process assumption and distinguish it from browser-origin protection.
- [ ] Q01-03 Exercise HTTP mutation origin rules (Required). Accept suite mutations and reject foreign websites and rebinding hosts for every mutation route.
- [ ] Q01-04 Reject malformed origin encodings (Required). Cover duplicate headers, malformed URLs, misleading suffixes and unexpected port forms with bounded rejection behavior.
- [ ] Q01-05 Inventory service bind addresses (Required). Verify public app services bind to loopback by default and document each intentional exception.
- [ ] Q01-06 Constrain speed-test socket side effects (Required). Confirm rejected handshakes cannot start traffic, allocate workers or mutate test state.
- [ ] Q01-07 Bound socket message sizes (Required). Reject oversized messages and cap per-client queues without stalling other clients.
- [ ] Q01-08 Handle slow and abandoned clients (Required). Disconnect stalled clients within a defined budget and release subscriptions on disconnect.
- [ ] Q01-09 Add service identity verification (Required). Readiness responses identify app, profile, protocol and version before a client trusts an occupied port.
- [ ] Q01-10 Review optional local session tokens (Candidate). Compare protection gained and migration cost against the stated local-process threat model before deciding.
- [ ] Q01-11 Threat-model credential and update routes (Required). Record assets, callers, trust boundaries and controls for privileged local operations.
- [ ] Q01-12 Publish a security regression matrix (Required). Map each protected route to positive, negative and reconnect checks with reproducible evidence.

## Q02: Alarm occurrence delivery and recovery

Make each scheduled occurrence independently accountable and recoverable.

- [ ] Q02-01 Assign stable occurrence identifiers (Required). Identify one scheduled occurrence across scheduler, helper, history, snooze and dismissal without colliding with later rings.
- [ ] Q02-02 Separate due, accepted and audible evidence (Required). History labels distinguish scheduler due, helper acceptance, stream progress and separately observed sound.
- [ ] Q02-03 Scope dismissal to one occurrence (Required). A stale dismiss request cannot silence a different or later alarm occurrence.
- [ ] Q02-04 Persist recoverable delivery state (Required). A controlled engine restart preserves enough state to classify and recover an interrupted occurrence.
- [ ] Q02-05 Make helper retries idempotent (Required). A retried start for the same occurrence cannot create duplicate playback sessions.
- [ ] Q02-06 Define missed-occurrence policy (Required). Specify startup versus tick-gap handling, grace windows and user-visible missed states.
- [ ] Q02-07 Isolate delivery from slow UI clients (Required). A blocked browser or WebSocket subscriber does not delay scheduler delivery or persistence.
- [ ] Q02-08 Bound delivery retries and failure state (Required). After a documented retry budget, show delivery failure with actionable diagnostics and no false Rang state.
- [ ] Q02-09 Validate overlapping alarm delivery (Required). Two due alarms have explicit mixing or queue policy and independent dismiss/history records.
- [ ] Q02-10 Run an isolated end-to-end alarm fixture (Required). Use test ports and fake audio to verify create, due, start, snooze, dismiss and recovery as one flow.
- [ ] Q02-11 Observe an agreed real playback test (Required). Record actual sound, selected endpoint and foreground behavior separately from API events outside protected real alarms.
- [ ] Q02-12 Verify installed delivery after release (Required). Repeat a bounded delivery check on the installed artifact and record version and conditions.

## Q03: Alarm recurrence and clock semantics

Use a documented occurrence policy across backend, UI and wake scheduling.

- [x] Q03-01 Remove the eight-day occurrence horizon (Source only, f01c09d). Backend next_ring_local and UI fallback handle valid far-future one-time dates without an eight-day cutoff.
- [ ] Q03-02 Specify spring-forward gap behavior (Required). Choose and document skip or adjusted-time handling with fixtures around a nonexistent local time.
- [ ] Q03-03 Specify fall-back repeated-time behavior (Required). Choose first, second or both occurrence semantics with distinct IDs for repeated local clock times.
- [ ] Q03-04 Define time-zone travel behavior (Required). Describe whether alarms follow local time or a saved zone and preview the next affected occurrence.
- [ ] Q03-05 Handle manual wall-clock jumps (Required). Forward and backward clock edits follow the documented missed or duplicate policy without an unbounded catch-up loop.
- [ ] Q03-06 Verify weekly recurrence boundaries (Required). Check selected weekdays, Sunday rollover, disabled dates and month/year boundaries against backend results.
- [ ] Q03-07 Validate one-time dates on save (Required). Reject invalid or past dates according to policy and preserve an existing alarm when edits fail.
- [ ] Q03-08 Recompute next occurrence after edits (Required). Create, toggle, delete, snooze and time edits update countdown and backend next occurrence consistently.
- [ ] Q03-09 Show next occurrence with zone context (Required). Display date, local time and zone context where ambiguity matters, with an accessible absolute timestamp.
- [ ] Q03-10 Define leap-day recurrence handling (Required). A leap-day alarm has explicit behavior in non-leap years and a reproducible test vector.
- [ ] Q03-11 Add an occurrence policy table (Required). Examples cover DST, travel, startup catch-up and disabled alarms using current supported Windows time behavior.
- [ ] Q03-12 Cross-check recurrence with a reference fixture (Required). Generate deterministic dates from an independently reviewed reference model and compare full occurrence sequences.

## Q04: Audio endpoint selection and playback

Make playback readiness and endpoint failures understandable.

- [ ] Q04-01 Persist an output-device identity (Required). Save a stable endpoint identifier plus readable label and avoid relying solely on a friendly-name match.
- [ ] Q04-02 Handle unplugged selected speakers (Required). Show unavailable endpoint status and apply only the user's explicitly selected fallback policy.
- [ ] Q04-03 Define default-device switching behavior (Required). Specify whether a running and a future alarm follow Windows default changes, with separate controls if useful.
- [ ] Q04-04 Add a safe playback readiness preview (Required). A user-triggered bounded preview tests the chosen endpoint and reports helper versus observed playback distinctly.
- [ ] Q04-05 Detect muted or unavailable sessions (Required). Expose known mute, volume and session failures without claiming that software can prove acoustic audibility.
- [ ] Q04-06 Make volume lock opt-in and bounded (Required). Show its effect before activation and restore prior supported volume settings after normal end or failure.
- [ ] Q04-07 Validate bundled and custom audio formats (Required). Unsupported, missing or corrupt files produce a visible error and a documented fallback without hanging delivery.
- [ ] Q04-08 Bound decode and playback work (Required). Large or malformed audio inputs have time and memory limits and cannot block unrelated alarms.
- [ ] Q04-09 Restore device handles after resume (Required). A stale audio handle is reopened or fails clearly when endpoints change during sleep or driver restart.
- [ ] Q04-10 Provide accessible alarm controls (Required). Stop, snooze and Code Red challenge controls are keyboard reachable, labeled and usable at high DPI.
- [ ] Q04-11 Verify concurrent desktop audio behavior (Required). Record mixing, ducking and endpoint contention behavior with controlled playback rather than assuming exclusivity.
- [ ] Q04-12 Record installed endpoint test evidence (Required). Capture app version, device state and actual observed outcome for at least selected-device and missing-device cases.

## Q05: Alarm wake timers and always-on readiness

Keep supported wake behavior accurate without promising wake on every device.

- [x] Q05-01 Use absolute UTC wake deadlines (Source only, 2404966). Preserve armed deadlines through 1 to 180 second boundaries, schedule edits and transient lookup failure.
- [ ] Q05-02 Inspect Windows wake capability (Required). Report supported, disabled or unknown wake prerequisites using read-only capability evidence.
- [ ] Q05-03 Run a controlled sleep-wake alarm test (Required). Record sleep state, wake timer settings, wake event and alarm result on the installed artifact outside pending real alarms.
- [ ] Q05-04 Re-arm timers after system resume (Required). Resume and time-zone changes trigger a fresh next-occurrence lookup with bounded delay.
- [ ] Q05-05 Test cancellation for removed alarms (Required). Deleting or disabling the last pending alarm cancels only its obsolete wake schedule.
- [ ] Q05-06 Bound keep-awake to active needs (Required). Execution-state requests end on dismiss, expiration and failure and do not leave permanent sleep prevention.
- [ ] Q05-07 Show alarm readiness diagnostics (Required). Combine engine, helper, selected speaker and known wake prerequisites with explicit unknown states.
- [ ] Q05-08 Document shutdown and unsupported sleep limits (Required). User-facing support text distinguishes sleep wake from powered-off hardware and unsupported configurations.
- [ ] Q05-09 Handle locked-session delivery (Required). Record actual delivery and available controls at the lock screen without claiming unsupported foreground guarantees.
- [ ] Q05-10 Recover from service restart before due (Required). A restarted engine recalculates the next wake deadline without depending on a stale UI countdown.
- [ ] Q05-11 Verify wake behavior on battery policy (Required). Record supported laptop power-policy limits and do not silently change battery wake settings.
- [ ] Q05-12 Add a readiness self-check export (Candidate). Export redacted diagnostic states and timestamps without alarm labels or personal device names by default.

## Q06: Dock visibility recovery and journal safety

Preserve access to every running app through failures.

- [x] Q06-01 Write the versioned recovery journal before hiding (Source only, 6e8dfa6). Atomically persist process creation identity and generation transaction before hiding; bound records and retain failed restores.
- [ ] Q06-02 Verify live crash restoration (Required). After release integration, record native taskbar buttons before failure and their restoration after a controlled actual Dock crash. Do not treat journal zero alone as independent button-count proof.
- [ ] Q06-03 Verify guard failure before hiding (Required). If the independent guard is not ready or exits, restore buttons and refuse further hiding.
- [ ] Q06-04 Exercise recycled HWND protection (Required). A reused window handle with different process creation identity is never restored or hidden as the old window.
- [ ] Q06-05 Restore after Explorer restart (Required). Detect shell replacement, discard invalid identities and re-establish only a verified visible Dock state.
- [ ] Q06-06 Handle corrupted recovery files (Required). Truncated, malformed, oversized and unsupported-version journals fail safely without hiding new buttons.
- [ ] Q06-07 Preserve records on restore failure (Required). Failed native restore calls retain actionable journal entries and retry within a bounded budget.
- [ ] Q06-08 Test atomic journal interruption points (Required). Inject failure before write, after write, during hide and during restore with explicit expected button state.
- [x] Q06-09 Gate hiding on acknowledged visible fitting strips (Source only, a6aaa39). Source requires render acknowledgement, retry after failed render, monotonic freshness and visible/uncloaked/on-screen/fitting geometry; configurations lacking a strip per taskbar use native fallback.
- [ ] Q06-10 Enforce restart budgets and escalation (Required). Repeated freeze recovery stops restarting at the documented budget and leaves the taskbar accessible.
- [ ] Q06-11 Verify graceful quit restores everything (Required). Tray Quit and installer shutdown restore all tracked buttons before exit with recorded native evidence.
- [ ] Q06-12 Expose a recovery status and action (Required). Users can see why organization paused and invoke a safe restore action without browsing private scripts.

## Q07: Dock monitor and taskbar compatibility

Organize windows only where native taskbar access is verified.

- [ ] Q07-01 Implement a verified strip for each organized taskbar (Required). Each organized taskbar has its own mapped, visible, fitting, independently acknowledged strip. Until then, multi-taskbar configurations use native fallback without hiding.
- [ ] Q07-02 Handle mixed-DPI coordinate conversion (Required). Position and hit areas remain correct across tested per-monitor scaling transitions.
- [ ] Q07-03 Exercise portrait and rotated displays (Required). Dock bounds and overflow remain reachable on portrait monitors and after rotation changes.
- [ ] Q07-04 Handle monitor hot-plug and unplug (Required). Moving or removing a monitor restores hidden apps before reassigning organization to a valid taskbar.
- [ ] Q07-05 Respect auto-hidden taskbars (Required). Organization does not leave inaccessible buttons when the taskbar is temporarily hidden or revealed.
- [ ] Q07-06 Check supported taskbar orientations (Required). Verify available Windows configurations and show an unsupported fallback rather than assume bottom-only placement works.
- [ ] Q07-07 Preserve pinned-app native behavior (Required). Pinned apps retain their normal button, running underline and jump-list access without duplicate grouping.
- [ ] Q07-08 Respect full-screen apps per monitor (Required). Only the relevant full-screen state affects each strip, with restoration when the strip cannot be shown.
- [ ] Q07-09 Exercise Windows taskbar overflow (Required). Narrow taskbar and native overflow retain usable access to every organized and unorganized app.
- [ ] Q07-10 Handle primary-display changes (Required). Changing primary monitor preserves groups, positions and recovery ownership without orphaned hidden buttons.
- [ ] Q07-11 Verify taskbar settings combinations (Required). Record behavior for combined labels, multiple taskbars and supported alignment settings on actual Windows builds.
- [ ] Q07-12 Publish a Dock support matrix (Required). List tested OS, taskbar, DPI and monitor combinations, mark multi-strip work incomplete until implemented, and describe native fallback for unsupported configurations.

## Q08: Dock expansion and group interaction

Replace an expanded folder tile with reachable app icons and a compact collapse handle.

- [ ] Q08-01 Replace the expanded folder tile with a handle (Required). An open group shows its apps and a slim named collapse control without reserving the old folder tile width.
- [ ] Q08-02 Animate folder-to-app expansion (Required). Start and end geometry match actual reported width, and icons remain clickable throughout supported transition states.
- [ ] Q08-03 Reclaim width on group collapse (Required). Collapsing returns all used space and leaves neighboring groups aligned without clipped taskbar content.
- [ ] Q08-04 Support interrupted and rapid transitions (Required). Repeated clicks, switching groups and focus loss converge to one consistent open state without lost buttons.
- [ ] Q08-05 Add keyboard expansion and collapse (Required). Enter, Space, Escape and directional navigation have documented behavior and a visible focus indicator.
- [ ] Q08-06 Preserve focus across group rerender (Required). The active control retains logical focus after app count or window title changes.
- [ ] Q08-07 Honor reduced-motion preferences (Required). Expansion becomes immediate or uses the chosen minimal transition when reduced motion is enabled.
- [ ] Q08-08 Handle many apps in a narrow strip (Required). Overflow or scrolling remains reachable and the collapse handle stays visible at the smallest supported width.
- [ ] Q08-09 Show a multi-window app picker (Required). An app with several windows offers readable titles and activates the selected verified window.
- [ ] Q08-10 Maintain accurate group accessibility state (Required). Group names, counts, expanded state and picker ownership are exposed to assistive technology.
- [ ] Q08-11 Offer per-group ordering choices (Candidate). Persist a user-selected ordering rule with reset and no silent rearrangement after restart.
- [ ] Q08-12 Verify expansion in the installed desktop (Required). Record pointer, keyboard, DPI and recovery behavior in the native taskbar, beyond browser layout checks.

## Q09: Storage identity, capacity and health detail

Associate disk evidence correctly and explain physical capacity separately from usable volumes.

- [x] Q09-01 Match drives one-to-one using stable identities (Source only, a0345e8). Reject ambiguous, conflicting-serial and model-variant matches rather than assigning one sensor group to multiple drives.
- [ ] Q09-02 Separate physical disks from volumes (Required). Show which filesystem volumes belong to each physical device, including unknown or unsupported mapping states.
- [x] Q09-03 Render decimal capacity and actual volume usage (Source only, 10d66f9). Source shows physical bytes and GiB, per-volume total/free/used and filesystem, while preserving unknown values.
- [ ] Q09-04 Verify Storage layout in the native window (Required). Inspect current Storage renderer at narrow and wide widths, expanded detail and all themes; blocked preview is not a pass.
- [ ] Q09-05 Explain capacity conversion and overhead (Required). Help text distinguishes decimal manufacturer units, binary units, partitions and filesystem free space without invented missing-space values.
- [ ] Q09-06 Audit SMART health and wear semantics (Required). Label vendor-reported life/wear counters and unavailable data without implying a health guarantee.
- [ ] Q09-07 Show disk temperature provenance (Required). A temperature includes sensor source and freshness, with absent or stale readings displayed explicitly.
- [ ] Q09-08 Expose useful advanced device details (Required). Advanced view shows supported firmware, bus, sectors, hours, writes and error counters with units and unknown states.
- [ ] Q09-09 Protect disk identifiers in views and export (Required). Serials are masked by default and require a deliberate reveal or export inclusion choice.
- [ ] Q09-10 Handle removable and disconnected drives (Required). Hot removal and permission failures remove stale associations while preserving understandable last-known context.
- [ ] Q09-11 Verify identical disks and multi-volume devices (Required). Fixtures and available read-only hardware evidence cover duplicate labels, USB bridges and one disk with several volumes.
- [ ] Q09-12 Offer capacity and health trend history (Candidate). Evaluate a bounded optional history with explicit retention and no prediction of failure from missing evidence.

## Q10: Desktop power, PSU context and reversible plans

Explain measured coverage and let desktop users add optional PSU context.

- [ ] Q10-01 Add optional desktop PSU fields (Required). Wattage, model and form factor can each be omitted; no make or rating is a generic default.
- [ ] Q10-02 Persist optional personal PSU settings locally (Required). Save chosen PSU wattage, model and form factor only in personal settings, with reset and no export as public defaults.
- [ ] Q10-03 Distinguish sensor watts from estimates (Required). Each total describes its source, covered components, sampling interval and excluded or unknown contributors.
- [ ] Q10-04 Preserve missing component power (Required). Unknown CPU, GPU or board values do not silently become zero or a confident system total.
- [ ] Q10-05 Use a quiet uncertain-headroom advisory (Required). With optional rating and incomplete coverage, show a restrained qualified estimate rather than a persistent alarm or safe-capacity guarantee.
- [ ] Q10-06 Separate PSU rating from wall draw (Required). Copy explains that a nameplate rating is context; efficiency or conversion assumptions are visible when used.
- [ ] Q10-07 Gate desktop and laptop power interfaces (Required). Desktop PSU context and laptop battery/adapter context follow detected capabilities with an explicit device-type correction path.
- [ ] Q10-08 Preserve original Windows power settings (Required). Any supported opt-in plan change records prior state and offers a tested restore action.
- [ ] Q10-09 Research plans beyond Ultimate Performance (Required). Use current official Windows documentation and measured workloads before proposing a custom plan or benefit claim.
- [ ] Q10-10 Benchmark a candidate plan reversibly (Candidate). Compare workload result, energy and thermal effects against baseline with repeated runs and original plan restored.
- [ ] Q10-11 Handle denied or unsupported plan controls (Required). Unavailable settings explain the capability or permission limit and never report a failed write as success.
- [ ] Q10-12 Validate estimates with an optional external meter (Candidate). If a meter is available, record synchronized conditions and deviations without claiming software-only wall-meter accuracy.

## Q11: Display identities and safe display controls

Show identifiable displays and make supported changes recoverable.

- [ ] Q11-01 Collect actual monitor identification (Required). Decode available EDID or WMI identity and retain unknown labels when no trustworthy model is available.
- [ ] Q11-02 Map models to Windows display devices (Required). Associate readable model, connector and display number without guessing from enumeration order alone.
- [ ] Q11-03 Show the local computer name in the header (Required). Render the escaped Windows hostname with a safe fallback; CPU identity remains available in details.
- [ ] Q11-04 Handle duplicate monitor models (Required). Different connectors or optional masked identifiers distinguish identical screens without inventing unique model names.
- [ ] Q11-05 Refresh identity after hot-plug (Required). A display connect, disconnect or topology change updates mapping and clears stale device references.
- [ ] Q11-06 Expose supported refresh modes accurately (Required). List only supported modes for the selected display and preserve current mode when enumeration fails.
- [ ] Q11-07 Add timed display-change confirmation (Required). A supported refresh or mode change rolls back automatically unless the user confirms usable output.
- [ ] Q11-08 Recover display changes after interruption (Required). App crash or timeout cannot leave an unconfirmed experimental mode persisted by the utility.
- [ ] Q11-09 Represent HDR and advanced capability honestly (Required). Unsupported or unknown controls remain unavailable with capability details; no generic hardware assumption is made.
- [ ] Q11-10 Keep personal display identity out of default exports (Required). Mask serials and hostname, and let the user deliberately include relevant diagnostics.
- [ ] Q11-11 Verify mixed scale and orientation controls (Required). Available hardware checks keep model selection, resolution and orientation associated with the right display.
- [ ] Q11-12 Offer display profiles only after baseline validation (Candidate). Evaluate optional named profiles with per-display matching and rollback rather than global unverified settings.

## Q12: Readable processes and resource detail

Make the top list understandable while keeping the complete resource view available.

- [ ] Q12-01 Resolve friendly executable descriptions (Required). Prefer validated local metadata for display names and label missing descriptions as unknown.
- [ ] Q12-02 Add a reviewed Windows process glossary (Required). Explain common Windows executables with source-backed descriptions and no claim of identifying every process.
- [ ] Q12-03 Group app processes without losing identity (Required). A grouped app retains child PIDs and individual metrics in advanced detail with a documented grouping rule.
- [ ] Q12-04 Filter the compact CPU list at 0.5 percent (Required). The default compact list excludes entries below 0.5 percent CPU and visibly states the threshold and unit.
- [ ] Q12-05 Provide a scrollable full process list (Required). More apps exposes every collected process, including those under the compact threshold, with stable sorting.
- [ ] Q12-06 Keep CPU, GPU and memory filters independent (Required). A process with low CPU but meaningful GPU or memory usage is not accidentally hidden from those resource views.
- [ ] Q12-07 Expose safe advanced process identity (Required). On demand show PID, executable path, publisher evidence and related service information with access-denied states.
- [ ] Q12-08 Explain CPU percentage normalization (Required). Help text states whether readings represent the whole machine or logical-core capacity and matches backend units.
- [ ] Q12-09 Reconcile compact totals with full totals (Required). Filtering does not change overall resource totals, and hidden contribution is understandable.
- [ ] Q12-10 Handle process exit and PID reuse (Required). A process that exits or changes creation identity cannot inherit another process's name or details.
- [ ] Q12-11 Make names and paths safe to render (Required). Malformed metadata, control characters and markup-like names remain readable text without injection.
- [ ] Q12-12 Offer user-managed process aliases (Candidate). Evaluate optional local aliases without changing executable identity or presenting aliases as verified publisher metadata.

## Q13: Cooling sensors and opt-in control boundaries

Display trustworthy cooling data and avoid unsupported hardware writes.

- [ ] Q13-01 Label fan percentages by their real denominator (Required). Observed-peak percentages say so; a true controller duty cycle is shown only when independently supplied.
- [ ] Q13-02 Identify fan and pump sensor provenance (Required). Each RPM reading retains device/source identity and avoids claiming a header is a specific physical fan without evidence.
- [ ] Q13-03 Separate missing, stopped and stale fans (Required). Unknown RPM, reported zero RPM and old data have distinct states and do not generate the same conclusion.
- [ ] Q13-04 Cross-check CPU and GPU temperature mapping (Required). Multiple devices and sensor packages cannot silently map one reading to the wrong CPU or GPU.
- [ ] Q13-05 Bound hardware-provider polling (Required). Slow or missing providers have timeouts and cached results that do not stall Alarm or the UI.
- [ ] Q13-06 Detect optional sensor providers read-only (Required). Show detected and absent providers with current free installation guidance only after source verification.
- [ ] Q13-07 Provide sensor source and freshness detail (Required). Advanced view exposes update time, unit, source and confidence basis for merged readings.
- [ ] Q13-08 Gate every prospective cooling write (Required). Controls stay disabled unless model/provider support is established and the user explicitly opts in.
- [ ] Q13-09 Detect competing fan-control ownership (Required). Before any future write, identify supported ownership conflicts and avoid concurrent controllers changing the same channel.
- [ ] Q13-10 Design rollback for prospective fan controls (Candidate). Any proposed write has bounded range, supported restore behavior and a failure path before implementation approval.
- [ ] Q13-11 Offer an external fan-tool handoff (Candidate). Evaluate opening an installed free compatible tool instead of duplicating unsupported control logic.
- [ ] Q13-12 Verify sensor behavior across provider failure (Required). Restart, permission loss and unavailable provider fixtures preserve last-known context without false All good claims.

## Q14: Battery, adapter and OEM capability gating

Keep laptop features within measured battery and vendor support.

- [ ] Q14-01 Detect battery capability before showing controls (Required). Battery and laptop panels appear from actual capability evidence, with a clear unavailable state on desktops.
- [ ] Q14-02 Report battery health with units and limits (Required). Design capacity, full-charge capacity and derived health retain source and unknown values without unsupported precision.
- [ ] Q14-03 Explain battery wear and cycle semantics (Required). Unavailable or vendor-specific cycle counts are labeled and do not imply a complete wear diagnosis.
- [ ] Q14-04 Identify AC adapter context where available (Required). Show AC state and reported adapter capability separately from inferred charging watts or unknown adapter rating.
- [ ] Q14-05 Measure battery-life estimates over a window (Required). An estimate states conditions and available sample window and avoids a confident time from one unstable reading.
- [ ] Q14-06 Gate OEM charge-limit controls by model (Required). Expose only verified supported model/API combinations and require opt-in before changing a limit.
- [ ] Q14-07 Keep OEM performance controls opt-in (Required). Unsupported fan, GPU mode and power controls remain read-only with specific capability reasons.
- [ ] Q14-08 Prevent conflict with installed OEM utilities (Required). Supported detection and documentation describe ownership conflicts before an optional control is enabled.
- [ ] Q14-09 Restore supported laptop settings (Required). Opt-in changes save known prior values and offer restore without claiming unavailable vendor rollback capability.
- [ ] Q14-10 Exercise battery and adapter fixtures (Required). Cover no battery, removable battery, multiple batteries, unknown capacity, AC transitions and permission denial.
- [ ] Q14-11 Run acceptance on a real laptop (Required). Record exact model, Windows build, battery evidence and tested supported actions before claiming laptop coverage.
- [ ] Q14-12 Research laptop-specific efficiency suggestions (Candidate). Evaluate optional source-backed suggestions without claiming superiority over G-Helper or an OEM utility.

## Q15: Speed-test lifecycle and measurement method

Make cancellation real and reported throughput reproducible.

- [ ] Q15-01 Cancel active transfers and workers (Required). Stop ends sockets, producer work and accounting within a defined budget, rather than only hiding the UI.
- [ ] Q15-02 Count acknowledged upload data (Required). Reported upload throughput uses a documented server-acknowledged byte basis and rejects inflated local-buffer counts.
- [ ] Q15-03 Define download byte and duration accounting (Required). State payload bytes, time window and excluded setup time and verify math with deterministic transports.
- [ ] Q15-04 Separate latency measurement types (Required). Idle and loaded latency, jitter and loss labels correspond to a documented collection method or remain unavailable.
- [ ] Q15-05 Handle partial and failed runs honestly (Required). Timeouts, rejected requests and insufficient samples yield partial or failed results without a successful final gauge.
- [ ] Q15-06 Enforce one owned test session (Required). A reconnect or second client cannot accidentally start duplicate traffic or steal cancellation ownership.
- [ ] Q15-07 Bound transfer concurrency and duration (Required). Set finite worker, byte and elapsed-time caps with user-visible approximate duration and data-use notice.
- [ ] Q15-08 Verify low and high speed gauge scales (Required). Gauge labels, units and rounding remain correct from near-zero results through the advertised display range.
- [ ] Q15-09 Respect offline and metered conditions (Required). Detect known network limits where possible and require a deliberate start rather than automatic bandwidth consumption.
- [ ] Q15-10 Add deterministic fake-transport checks (Required). Fixtures cover cancellation, stalled reads, late acknowledgements, clock changes and connection failure without real traffic.
- [ ] Q15-11 Compare real results under an agreed protocol (Required). After explicit user approval for bandwidth use, record server region, connection, run order, median and spread against references.
- [ ] Q15-12 Offer optional run-history comparison (Candidate). Evaluate a bounded local comparison view that records method/version changes and avoids equating different servers.

## Q16: Independent app lifecycles and background work

Keep one app's work or crash from disrupting another app's core function.

- [ ] Q16-01 Map current process and service dependencies (Required). Document actual launch, shutdown, port, storage and shared-loop dependencies before splitting components.
- [ ] Q16-02 Move blocking Sentinel work off the alarm loop (Required). A controlled slow sensor or process call does not delay alarm scheduling beyond the accepted measured budget.
- [ ] Q16-03 Give each public app an independent launch path (Required). Sentinel, Alarm and Dock can start through their own Windows identity without opening unrelated windows.
- [ ] Q16-04 Decouple Alarm survival from Dock shutdown (Required). Closing or recovering the organizer does not terminate a pending or active alarm service.
- [ ] Q16-05 Make app close behavior explicit (Required). Each app offers a clear close-versus-background policy and does not silently adopt another app's lifecycle.
- [ ] Q16-06 Verify readiness beyond an open port (Required). Launchers validate service identity, profile and version and reject an unrelated process occupying the expected port.
- [ ] Q16-07 Bound restart backoff per component (Required). Repeated failures stop at a documented budget and surface a useful degraded state instead of an endless restart loop.
- [ ] Q16-08 Isolate dev and installed profiles (Required). Data directories, ports, audio helpers and process ownership stay separate so dev cannot consume real alarms.
- [ ] Q16-09 Honor per-app autostart choices (Required). A saved startup-off choice survives restart, update and reinstall unless the user deliberately changes it.
- [ ] Q16-10 Make IPC disconnect recoverable (Required). A UI reconnects to its own service without duplicate workers, lost settings or stale readiness claims.
- [ ] Q16-11 Measure cross-app crash isolation (Required). Controlled Sentinel, Alarm UI and Dock failures produce a recorded matrix of unaffected and affected functions.
- [ ] Q16-12 Plan module boundaries around ownership (Required). Refactor one responsibility at a time with preserved external behavior and reviewed dependency direction.

## Q17: Responsive navigation, themes and accessibility

Make dense functionality usable at different sizes and with individual feature choices.

- [ ] Q17-01 Add a collapsible persistent sidebar (Required). Pointer and keyboard can collapse navigation, retain the chosen state and still identify the active page.
- [ ] Q17-02 Adapt panels from small laptop to wide desktop (Required). Supported narrow and wide windows show reachable actions without unintended horizontal page overflow.
- [ ] Q17-03 Match scrollbars to every theme (Required). System, light, dark and signature themes give scrollbars readable track/thumb contrast and consistent hover states.
- [ ] Q17-04 Audit every theme's sidebar states (Required). Active, inactive, hover, focused and disabled navigation remain legible in every supported theme.
- [ ] Q17-05 Preserve native scrolling behavior (Required). Wheel, touchpad, keyboard and optional smooth scroll work without forced scroll hijacking.
- [ ] Q17-06 Honor reduced motion across apps (Required). Disable nonessential animated counters, transitions and auto-scrolling when requested while retaining state feedback.
- [ ] Q17-07 Complete keyboard navigation paths (Required). Every main action, dialog and expandable detail can be reached, invoked and exited without pointer input.
- [ ] Q17-08 Expose semantic labels and announcements (Required). Controls have names and state, and important async status changes are announced without continuous screen-reader noise.
- [ ] Q17-09 Check contrast, text scaling and focus (Required). Supported themes pass documented contrast targets and remain usable with increased text and Windows high-contrast settings.
- [ ] Q17-10 Add per-feature visibility choices (Required). Users can hide optional panels or integrations individually, with discoverable reset and no loss of saved data.
- [ ] Q17-11 Persist layouts without trapping new controls (Required). Saved collapsed sections and density survive restart while newly added essential actions remain discoverable.
- [ ] Q17-12 Run native accessibility acceptance (Required). Record real keyboard, screen-reader and DPI outcomes on the installed apps, with unresolved gaps listed explicitly.

## Q18: Alerts, event history and diagnostic export

Make notices accountable, bounded and private by default.

- [ ] Q18-01 Define alert states and severity meanings (Required). Normal, warning, critical, unknown and stale have explicit rules with no All good claim from missing sensors.
- [ ] Q18-02 Specify in-app versus background delivery (Required). Each alert type states when it can notify and whether its supporting service must be running.
- [ ] Q18-03 Add threshold hysteresis and debounce (Required). A noisy reading near a threshold cannot generate uncontrolled repeated alerts.
- [ ] Q18-04 Offer alert categories and quiet periods (Required). Users can opt into supported categories and mute noncritical notices with clear exceptions they choose.
- [ ] Q18-05 Persist alert source and resolution history (Required). Record time, metric, threshold, provenance and resolved state without storing unnecessary personal identifiers.
- [ ] Q18-06 Bound event retention and database growth (Required). Alarm and sensor event retention have clear limits, pruning and indexes verified against a large fixture.
- [ ] Q18-07 Version and migrate history schemas (Required). Upgrades back up or transactionally migrate supported schema versions and fail without corrupting prior records.
- [ ] Q18-08 Provide searchable event history (Required). Time range, app, event type and outcome filters return consistent chronological results with empty states.
- [ ] Q18-09 Export a redacted diagnostic report (Required). Default exports mask hostnames, serials, paths, alarm labels and private connection details.
- [ ] Q18-10 Preview optional export inclusions (Required). Before saving, users see which additional fields they deliberately selected and where the file will be written.
- [ ] Q18-11 Validate export units and timestamps (Required). CSV or JSON includes schema, unit, timezone and app version metadata and handles formula-like text safely.
- [ ] Q18-12 Offer session summaries as a candidate (Candidate). Evaluate an optional bounded session report with clear sample coverage rather than inventing data for gaps.

## Q19: Bounded stress workloads and validation

Design voluntary finite workloads with checked results and explicit limits.

- [ ] Q19-01 Define the stress feature's initial scope (Required). Specify supported CPU, memory or other workload goals and omit unvalidated claims of diagnostic completeness.
- [ ] Q19-02 Require explicit start with visible limits (Required). Before execution show duration, workers, memory bound and stop control, with conservative editable defaults.
- [ ] Q19-03 Enforce a hard elapsed-time deadline (Required). A separate control path terminates owned workload workers at the deadline even if UI updates stall.
- [ ] Q19-04 Cap worker and memory allocations (Required). Validate limits against current capacity and preserve configured headroom without assuming all devices are alike.
- [ ] Q19-05 Make cancellation interrupt real work (Required). Stop ends owned workers within a measured budget and leaves no orphaned stress process.
- [ ] Q19-06 Verify workload results against known answers (Required). Each implemented workload detects incorrect results using a reviewed validation method rather than only raising utilization.
- [ ] Q19-07 Add supported thermal stop conditions (Required). Opt-in thermal limits use identified fresh sensors and define behavior when readings become unavailable.
- [ ] Q19-08 Prevent tests near protected alarms (Required). Preflight checks real pending alarms and refuses conflicting stress runs without changing the alarm schedule.
- [ ] Q19-09 Separate workload failure from system diagnosis (Required). Results describe detected errors and test conditions without claiming the absence of errors proves hardware health.
- [ ] Q19-10 Capture bounded stress run evidence (Required). Save workload version, limits, duration, sample coverage, errors and stop reason with private identifiers masked.
- [ ] Q19-11 Research prospective GPU and storage workloads (Candidate). Evaluate correctness validation and safe finite bounds before deciding whether these optional workloads belong in scope.
- [ ] Q19-12 Measure stress overhead and recovery (Required). Record controller CPU/RAM, stop latency and post-run return to baseline under reproducible conditions.

## Q20: Reproducible builds, signed updates and release gates

Deliver verifiable artifacts while preserving alarms and user choices.

- [ ] Q20-01 Pin build toolchains and dependencies (Required). Record supported toolchain versions and lock Python, Rust and frontend inputs used for release builds.
- [ ] Q20-02 Define a clean release build recipe (Required). A documented clean environment produces traceable inputs and artifacts without requiring optional paid services.
- [ ] Q20-03 Gate builds on relevant offline checks (Required). A release cannot proceed past failed security, alarm, journal or other affected regression gates.
- [ ] Q20-04 Sign updater payloads with a protected free keypair (Required). Updater verifies supported signatures before execution; private signing material stays outside the repository and logs.
- [ ] Q20-05 Use private staging and an update lock (Required). Concurrent requests cannot overwrite another download or execute an incomplete predictable temporary file.
- [ ] Q20-06 Verify manifest, version and artifact integrity (Required). Reject tampered payloads, mismatched metadata and unsupported downgrade paths before launch.
- [ ] Q20-07 Enforce the alarm-aware install gate (Required). Install refuses during active alarms and within the established pending-alarm window using a fresh backend check.
- [ ] Q20-08 Preserve settings and startup preferences on upgrade (Required). Per-app choices, alarms, histories and disabled autostart survive the supported upgrade path.
- [ ] Q20-09 Publish artifact hashes and provenance (Required). Release notes identify commit, checks, installer digest and known limitations without implying updater signatures equal Windows code signing.
- [ ] Q20-10 Test failed and interrupted update recovery (Required). Network loss, invalid signature, lock contention and installer failure leave a usable previous installation.
- [ ] Q20-11 Verify fresh install and uninstall behavior (Required). An isolated installation validates folder choice, shortcuts, app identities, cleanup and voluntary feedback behavior.
- [ ] Q20-12 Close the source-to-installed evidence gap (Required). Record released version, installed artifact identity and native acceptance results for every completed source fix in the release.

## Q21: Privacy, consent and personal configuration boundaries

Keep public defaults generic and network behavior deliberate.

- [ ] Q21-01 Inventory collected and transmitted fields (Required). Trace telemetry, feedback, crash and update requests with destinations, purpose and retention expectations.
- [ ] Q21-02 Require explicit telemetry consent (Required). Default behavior and installer/UI controls match documented consent and do not send optional events before permission.
- [ ] Q21-03 Honor consent withdrawal immediately (Required). Turning collection off stops optional traffic and queued event delivery while preserving local app functions.
- [ ] Q21-04 Remove owner-specific public defaults (Required). Router addresses, personal app keep lists, hostnames, PSU and speaker preferences are absent from generic defaults.
- [ ] Q21-05 Gate the optional personal VPN interface (Required). VPN UI stays hidden unless explicitly enabled and supported local configuration is present.
- [ ] Q21-06 Migrate router secrets to Windows credentials (Required). Supported migration keeps secret values out of agent output, files committed to Git and application logs.
- [ ] Q21-07 Redact sensitive diagnostic logging (Required). Connection details, credentials, alarm names and personal identifiers are removed or deliberately opt-in before logs leave the machine.
- [ ] Q21-08 Verify install and uninstall event semantics (Required). Counts distinguish download, install, upgrade, heartbeat and voluntary uninstall feedback without invented active-user precision.
- [ ] Q21-09 Deduplicate queued telemetry events (Required). Event IDs and retry rules prevent one action being counted repeatedly after a reconnect.
- [ ] Q21-10 Publish local-data deletion choices (Required). Users can identify and delete optional histories and telemetry identifiers without silently removing saved alarms.
- [ ] Q21-11 Review third-party permissions and cost (Required). Each required dependency has current source-backed license/cost and permission notes, with no subscription, key or trial dependency.
- [ ] Q21-12 Exercise an offline privacy acceptance run (Required). Network observation confirms core app use remains local and consent-disabled optional traffic is absent.

## Q22: Windows and hardware compatibility acceptance

Record tested environments and provide usable fallbacks.

- [ ] Q22-01 Define the Windows support policy (Required). List intended Windows releases and architectures, then verify current platform support from primary documentation.
- [ ] Q22-02 Run a clean-machine installation check (Required). Use an available isolated clean Windows environment and record prerequisites, prompts, first run and failure states.
- [ ] Q22-03 Verify supported Windows 10 behavior (Required). Record tested build and public app results or explicitly mark Windows 10 support unverified.
- [ ] Q22-04 Verify supported Windows 11 behavior (Required). Record tested build and taskbar, WebView, audio and update outcomes rather than assume one build covers all.
- [ ] Q22-05 Check Intel and AMD CPU coverage (Required). Fixtures and available hardware confirm identity and missing-sensor handling without owner-specific assumptions.
- [ ] Q22-06 Check AMD, NVIDIA and Intel GPU mapping (Required). Multi-adapter and unsupported-provider cases retain correct device identity and honest unknown values.
- [ ] Q22-07 Exercise low-memory and low-end profiles (Required). Bounded constrained-environment tests record UI responsiveness, polling and app recovery without claiming hardware certification.
- [ ] Q22-08 Test offline and restricted-network startup (Required). No-network, DNS failure and unavailable update endpoints preserve core functions and clear status.
- [ ] Q22-09 Test standard-user and denied-access paths (Required). Apps operate at intended privilege and explain unavailable administrative data without unnecessary elevation.
- [ ] Q22-10 Exercise scaling and locale combinations (Required). Text expansion, decimal/date formats, non-ASCII names and high DPI remain usable in supported windows.
- [ ] Q22-11 Validate upgrade from the installed baseline (Required). An isolated 2.2.4 profile migrates alarms, settings and recovery state through the planned release path.
- [ ] Q22-12 Maintain a dated compatibility matrix (Required). Each row separates fixture evidence, real hardware evidence and untested conditions with versioned outcomes.

## Q23: Objective competitor benchmarks and product claims

Support specific product claims with repeatable comparable evidence.

- [ ] Q23-01 Select per-app comparison questions (Required). Define concrete alarm reliability, Dock access and Sentinel understanding/performance questions before choosing competitors.
- [ ] Q23-02 Verify current competitor scope and costs (Required). Use current primary product documentation for capabilities, licensing and required paid features; record the research date.
- [ ] Q23-03 Define controlled benchmark environments (Required). Record machine, OS, build, provider, background load and configuration for both suite and reference tools.
- [ ] Q23-04 Measure idle CPU and memory fairly (Required). Compare repeated equal-duration runs with matched sensor scope, report medians and spread, and include measurement overhead.
- [ ] Q23-05 Measure alarm delay under bounded load (Required). Record due-to-delivery timing and observed audio separately across controlled repeatable loads.
- [ ] Q23-06 Measure Dock responsiveness and recovery (Required). Record activation delay, freeze count and restoration time during a defined bounded UIA workload.
- [ ] Q23-07 Compare sensor readings with provenance (Required). Time-align matching sensor sources and units; disagreement is investigated rather than declaring a preferred tool correct.
- [ ] Q23-08 Compare speed-test methods transparently (Required). Report endpoint, duration, bytes, run order and variance, and avoid equating tests with materially different methods.
- [ ] Q23-09 Compare stress workloads with OCCT objectively (Required). Map only measured supported workloads, error checking and controls; list gaps and avoid unmeasured parity or superiority claims.
- [ ] Q23-10 Run practical task-based usability checks (Required). Use defined tasks such as find a hot device or recover a window, with observed completion and failure notes.
- [ ] Q23-11 Publish a claim-to-evidence scorecard (Required). Every public comparative claim links to method, dated results and limitations; unsupported best-in-niche claims remain excluded.
- [ ] Q23-12 Define improvement decisions from results (Required). Rank measured gaps and choose scoped follow-up work rather than using code size or test counts as quality evidence.

## Q24: Install-first GitHub, onboarding and naming

Help a new user install the three desktop apps and understand their limits.

- [ ] Q24-01 Lead the README with the public app purposes (Required). Opening text describes PC Sentinel, Code Red Alarm and Ultra Dock in practical user terms without owner tooling.
- [ ] Q24-02 Add a verified latest-installer link (Required). The prominent download points to the actual supported release asset and is checked after publication.
- [ ] Q24-03 Explain installation and first run (Required). Document folder selection, app shortcuts, background behavior and needed prerequisites using the verified artifact.
- [ ] Q24-04 Capture current native app screenshots (Required). Show installed desktop screens at readable size with personal hostnames, serials and alarm labels redacted.
- [ ] Q24-05 Describe useful audiences and workflows (Required). Examples cover specific observed tasks without universal hardware or replacement claims.
- [ ] Q24-06 Document support limits and known gaps (Required). State tested Windows/hardware conditions and unverified wake, audio, layout or laptop behavior honestly.
- [ ] Q24-07 Explain per-app startup and feature choices (Required). Onboarding shows independent launch, optional Dock, telemetry, integrations and reset choices without forcing every feature on.
- [ ] Q24-08 Provide a practical alarm readiness walkthrough (Required). A new user can select a speaker, understand wake limits and deliberately run a bounded preview.
- [ ] Q24-09 Document updates, uninstall and data handling (Required). Describe verified update integrity, uninstall effects, retained user data and optional feedback without unsupported signature claims.
- [ ] Q24-10 Prepare 20 replacement Sentinel name ideas (Required). Provide exactly 20 product-name candidates with brief rationale and explicitly unverified availability, separate from account usernames.
- [ ] Q24-11 Plan naming identity migration after selection (Candidate). If a name is chosen, review shortcuts, app IDs, pins, config paths and upgrade compatibility before renaming binaries.
- [ ] Q24-12 Separate user installation from developer detail (Required). Keep build instructions, architecture and contribution guidance below the install path, with reproducible links and no required paid tooling.
