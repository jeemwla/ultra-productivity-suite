/* PC Sentinel panels: sensor setup guide, Max Performance measurement, background writers. Pure HTML builders,
   exported for node tests. Every number shown comes from a measurement; missing data says so. */
(function (root) {
  "use strict";
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const fin = v => typeof v === "number" && Number.isFinite(v);
  const n1 = v => Number(v).toFixed(1), n0 = v => String(Math.round(v));

  function rate(bps) {
    if (!fin(bps)) return "–";
    if (bps >= 1e6) return `${n1(bps / 1e6)} MB/s`;
    if (bps >= 1e3) return `${n0(bps / 1e3)} KB/s`;
    return `${n0(bps)} B/s`;
  }
  function bytes(b) {
    if (!fin(b)) return "–";
    if (b >= 1e12) return `${n1(b / 1e12)} TB`;
    if (b >= 1e9) return `${n1(b / 1e9)} GB`;
    if (b >= 1e6) return `${n0(b / 1e6)} MB`;
    return `${n0(b / 1e3)} KB`;
  }

  /* ---------------------------------------------------------------- sensor setup */
  function sensorBanner(g) {
    if (!g || g.state === "ok") return null;
    const links = g.links || {};
    const kind = g.state === "legacy_driver" ? "info" : "warning";
    const lhm = links.lhm ? `<a href="${esc(links.lhm)}" target="_blank" rel="noopener">LibreHardwareMonitor releases (official)</a>` : "";
    const pawn = links.pawnio ? `<a href="${esc(links.pawnio)}" target="_blank" rel="noopener">PawnIO driver releases (official)</a>` : "";
    const what = `<details class="mt"><summary>What are these?</summary><p class="muted mt">LibreHardwareMonitor is a free, open-source program that reads temperatures, fan speeds and power. It needs a small kernel driver to reach the sensors. Older versions used WinRing0, which Microsoft Defender flags as vulnerable; current versions use PawnIO, which is installed separately. Ultra Suite only reads what they report.</p></details>`;
    return { kind, title: g.title || "Sensors", html: `${esc(g.text)} <span class="sg-links">${[lhm, pawn].filter(Boolean).join(" · ")}</span>${what}`,
      setup: g.state === "lhm_missing" };
  }

  function missingReading(g) {
    if (!g) return "needs full sensors";
    return g.state === "driver_missing" ? "sensor driver missing" : g.state === "lhm_stopped" ? "LibreHardwareMonitor not running" : "needs full sensors";
  }

  /* ---------------------------------------------------------------- Max Performance measurement */
  const METRIC = [["single_mbps", "One core", v => `${n0(v)} MB/s`], ["multi_mbps", "All cores", v => `${n0(v)} MB/s`], ["wake_us", "Wake-up delay", v => `${n1(v)} µs`]];

  function verdict(c) {
    if (!c) return "";
    if (!c.real) return `<span class="pill">No measurable difference (within ±${n1(c.noise_pct)}%)</span>`;
    return c.better_pct > 0 ? `<span class="pill good">${n1(c.better_pct)}% better</span>` : `<span class="pill warning">${n1(-c.better_pct)}% worse</span>`;
  }

  function benchHtml(s, running) {
    s = s || {};
    const b = s.before, a = s.after, cmp = s.compare || {};
    const cell = (r, k, f) => r && fin(r[k]) ? f(r[k]) : "not measured";
    const when = r => r ? new Date(r.ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
    const rows = METRIC.map(([k, label, f]) => `<tr><th scope="row">${label}</th><td class="num">${cell(b, k, f)}</td><td class="num">${cell(a, k, f)}</td><td>${b && a ? verdict(cmp[k]) : ""}</td></tr>`).join("");
    const hint = !b && !a ? "Measure once with Max Performance off, then use it and measure again. Each run takes about 12 seconds."
      : !a ? "Now use Max Performance and measure again to compare." : !b ? "Go back to your previous plan and measure to get a baseline." : "Differences inside the run-to-run spread are not counted.";
    return `<table class="bench"><thead><tr><th scope="col">Measured</th><th scope="col">Max Performance off${b ? `<br><span class="muted">${esc(when(b))}</span>` : ""}</th><th scope="col">Max Performance on${a ? `<br><span class="muted">${esc(when(a))}</span>` : ""}</th><th scope="col">Result</th></tr></thead><tbody>${rows}</tbody></table>
      <p class="muted mt">${running ? "Measuring… keep other apps quiet for a moment." : esc(hint)} Workload: SHA-256 hashing of a fixed 4 MB buffer, and 200 thread wake-ups after short idle gaps; three passes each.</p>`;
  }

  /* ---------------------------------------------------------------- background writers */
  const LEVEL_TEXT = { heavy: "Heavy", warning: "Warning", notice: "Notice" };
  const LEVEL_CLASS = { heavy: "critical", warning: "warning", notice: "" };

  const WINDOWS_PARTS = { system: "Windows kernel (System)", registry: "Windows registry", "memory compression": "Windows memory compression" };
  const exe = n => WINDOWS_PARTS[n] || `${n}.exe`;
  const confirmedFix = ki => ki && ki.fixed_in && ki.fixed_in.status === "confirmed" && ki.fixed_in.source_url && ki.fixed_in.confirmed_on;
  const retiredIssue = ki => ki && ki.retired && confirmedFix(ki);

  function writerSentence(r) {
    const ki = r.known_issue && !retiredIssue(r.known_issue) ? r.known_issue : null;
    const who = ki ? esc(ki.title) : `<b>${esc(r.name)}</b>`;
    if (!r.level) return `${who} wrote ${bytes(r.bytes_today)} today.`;
    return `${who} is writing ${rate(r.avg_bps_1h)} almost all the time (${n0(r.active_share_1h * 100)}% of the last hour). At this pace that is about ${bytes(r.per_day_estimate)} a day.${ki ? " A matching process name does not establish that the known issue caused these writes." : ""}`;
  }

  function writersHtml(rep, ignored) {
    ignored = ignored || [];
    if (!rep) return `<div class="muted">Reading write activity…</div>`;
    if (!rep.available) return `<div class="muted">Windows' per-process write counters aren't available on this PC.</div>`;
    const rows = (rep.top || []).filter(r => !ignored.includes(r.name));
    const flagged = rows.filter(r => r.level);
    const warm = rep.observed_minutes_1h < 48 ? `<p class="muted">Watching for ${rep.observed_minutes_1h} of the 60 minutes needed before anything can be flagged.</p>` : "";
    const table = rows.length ? `<table class="wr"><thead><tr><th scope="col">Program</th><th scope="col">Average, last hour</th><th scope="col">Active</th><th scope="col">Today</th><th scope="col">Status</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead><tbody>${rows.map(r => `<tr>
      <td>${esc(r.known_issue ? r.known_issue.title : r.name)}<div class="muted">${esc(exe(r.name))}${r.self ? " · this app" : ""}</div></td><td class="num">${rate(r.avg_bps_1h)}</td><td class="num">${n0(r.active_share_1h * 100)}%</td><td class="num">${bytes(r.bytes_today)}</td>
      <td>${r.level ? `<span class="pill ${LEVEL_CLASS[r.level]}">${LEVEL_TEXT[r.level]}</span>` : `<span class="pill good">Normal</span>`}</td>
      <td><button class="btn" type="button" data-wr-detail="${esc(r.name)}">Details</button></td></tr>`).join("")}</tbody></table>` : "";
    const head = flagged.length ? flagged.map(r => `<div class="banner ${r.level === "notice" ? "info" : "warning"} mt"><div class="ic">!</div><div><div class="t">${LEVEL_TEXT[r.level]}: constant background writes</div><div class="s">${writerSentence(r)}</div></div></div>`).join("")
      : `<p>No sustained writer flagged in the available samples.</p>`;
    const drive = fin(rep.drive_write_bps) ? `<p class="muted mt">All drives together: ${rate(rep.drive_write_bps)} right now.</p>` : "";
    const ign = ignored.length ? `<p class="muted mt">Ignored: ${ignored.map(n => `${esc(n)} <button class="linkbtn" type="button" data-wr-unignore="${esc(n)}">show again</button>`).join(", ")}</p>` : "";
    return `${head}${warm}${table}${drive}${ign}<p class="muted mt">Counts all write requests a program makes (files and devices). These counters do not identify a destination drive. Host writes are not flash wear. A quiet sample does not rule out an issue at another time.</p>`;
  }

  function writerDetail(r) {
    if (!r) return "";
    const ki = r.known_issue;
    const retired = retiredIssue(ki), fixed = ki && ki.fixed_in;
    const fixStatus = !fixed ? "" : confirmedFix(ki)
      ? `<p>Vendor-confirmed fix: ${esc(fixed.version)} (confirmed ${esc(fixed.confirmed_on)}). <a href="${esc(fixed.source_url)}" target="_blank" rel="noopener">Official confirmation</a></p>`
      : `<p>Candidate fix: ${esc(fixed.version)} is an unconfirmed reported target. A version number alone does not confirm a fix.</p>`;
    const fixes = ki && !retired ? (ki.fixes || []).map(f => `<li><b>${esc(f.title)}</b><p>${esc(f.steps)}</p>${f.command ? `<p class="muted">Command (run in an administrator PowerShell yourself):</p><pre>${esc(f.command)}</pre><p class="muted">Undo:</p><pre>${esc(f.undo || "")}</pre>` : ""}</li>`).join("") : "";
    const sources = ki ? (ki.sources || []).map(s => `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title)}</a>`).join("<br>") : "";
    return `<div class="wr-detail"><h3>${esc(ki ? ki.title : r.name)}</h3><p class="muted">${esc(exe(r.name))} · ${rate(r.avg_bps_1h)} average over the last hour · active ${n0(r.active_share_1h * 100)}% of minutes · ${bytes(r.bytes_today)} today</p>
      ${ki ? `<p>${esc(ki.explanation)}</p>${fixStatus}${retired ? `<p>Windows reports a driver version (${esc(ki.installed_version)}) at or past the documented fix. Observed writes still need investigation; a version match does not identify their cause.</p>` : `<ol>${fixes}</ol>${ki.check ? `<p class="muted">Check it yourself (no admin needed):</p><pre>${esc(ki.check)}</pre>` : ""}`}<p class="muted">Last reviewed: ${esc(ki.last_reviewed || "unknown")}. Sources:<br>${sources}</p>`
        : `<p>This program writes steadily. Common reasons: logging, sync or backup tools, browser caches, or a stuck update. Check its settings, update it, or ask its maker. Sentinel never stops programs itself.</p>`}
      <p><button class="btn" type="button" data-wr-ignore="${esc(r.name)}">Ignore this app</button></p></div>`;
  }

  function liveHtml(rep) {
    if (!rep || !rep.measure_elapsed && !rep.measuring) return "";
    const rows = (rep.live || []).slice(0, 10);
    return `<p class="muted">${rep.measuring ? `Measuring every 5 seconds: ${n0(rep.measure_elapsed || 0)} of ${rep.measure_seconds} s` : `Measured for ${n0(rep.measure_elapsed)} s`}</p>
      ${rows.length ? `<table class="wr"><thead><tr><th scope="col">Program</th><th scope="col">Average write rate</th></tr></thead><tbody>${rows.map(r => `<tr><td>${esc(r.name)}</td><td class="num">${rate(r.avg_bps)}</td></tr>`).join("")}</tbody></table>` : `<p class="muted">No writes measured yet.</p>`}`;
  }

  function overviewPill(rep, ignored) {
    const hit = ((rep && rep.flagged) || []).filter(r => !(ignored || []).includes(r.name) && (r.level === "warning" || r.level === "heavy"));
    return hit.length ? `<span class="pill ${hit.some(r => r.level === "heavy") ? "critical" : "warning"}">Constant writes: ${esc(hit[0].known_issue ? hit[0].known_issue.title : hit[0].name)}</span>` : "";
  }

  const api = { rate, bytes, sensorBanner, missingReading, benchHtml, writersHtml, writerDetail, liveHtml, overviewPill, writerSentence };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.SentinelPanels = api;
})(typeof window !== "undefined" ? window : globalThis);
