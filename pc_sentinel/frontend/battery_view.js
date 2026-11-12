/* PC Sentinel Battery page. Pure HTML from /api/laptop/battery and its history; values Windows doesn't report
   are shown as "not reported", never guessed. Exported for node tests. */
(function (root) {
  "use strict";
  const NR = '<span class="nr">not reported</span>';
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const fin = v => typeof v === "number" && Number.isFinite(v);
  const n0 = v => String(Math.round(v)), n1 = v => Number(v).toFixed(1);
  const hm = m => fin(m) ? `${Math.floor(m / 60)}h ${String(Math.round(m % 60)).padStart(2, "0")}m` : null;
  const whRaw = mwh => fin(mwh) && mwh > 0 ? `${n1(mwh / 1000)} Wh` : null;

  function chargerSub(p) {
    if (p.charging) return "charging now";
    if (p.plugged_in === true && fin(p.charge_pct) && p.charge_pct < 99)
      return "charging is paused; a charge limit (for example 80% in Armoury Crate, G-Helper or Lenovo Vantage) does this";
    return p.battery_count > 1 ? `${p.battery_count} batteries, shown combined` : "";
  }

  function stat(k, v, sub) {
    return `<div class="bt-stat"><div class="muted">${k}</div><div class="big num">${v == null ? NR : v}</div>${sub ? `<div class="muted">${sub}</div>` : ""}</div>`;
  }

  function ring(pct) {
    const R = 54, C = 2 * Math.PI * R, low = fin(pct) && pct < 20;
    return `<svg width="140" height="140" viewBox="0 0 140 140" aria-hidden="true"><circle cx="70" cy="70" r="${R}" fill="none" stroke="var(--series-track)" stroke-width="12"/>
    <circle cx="70" cy="70" r="${R}" fill="none" stroke="${low ? "var(--critical)" : "var(--good)"}" stroke-width="12" stroke-linecap="round" stroke-dasharray="${C}" stroke-dashoffset="${C * (1 - (fin(pct) ? pct : 0) / 100)}" transform="rotate(-90 70 70)" style="transition:stroke-dashoffset .9s var(--ease)"/></svg>`;
  }

  function powerStat(p) {
    if (!fin(p.rate_w)) return stat(p.charging ? "Charging power" : "Power", null, "Windows isn't reporting the charge or discharge rate");
    if (p.charging) return stat("Charging at", `${n1(Math.abs(p.rate_w))} W`, "into the battery" + (fin(p.voltage_v) ? ` · ${n1(p.voltage_v)} V` : ""));
    if (p.rate_w < 0) return stat("Using", `${n1(Math.abs(p.rate_w))} W`, "from the battery" + (fin(p.voltage_v) ? ` · ${n1(p.voltage_v)} V` : ""));
    return stat("Power", `${n1(p.rate_w)} W`, p.plugged_in ? "running on the charger" : (fin(p.voltage_v) ? `${n1(p.voltage_v)} V` : ""));
  }

  function timeStat(p) {
    if (p.charging) return stat("Time to full", hm(p.minutes_to_full), p.minutes_to_full != null ? "at the current charging rate" : "needs a charge rate and remaining capacity");
    if (p.plugged_in) return stat("Time left", "On charger", "the battery isn't being used");
    return stat("Time left", hm(p.minutes_left), p.minutes_left != null ? "at the current pace" : "");
  }

  function dailyStat(p) {
    const d = p.daily_use;
    if (!d || !fin(d.pct_per_day)) return stat("Average daily use", null, "needs Windows' battery report with capacity history");
    return stat("Average daily use", `${n1(d.pct_per_day)}% <small>per day</small>`,
      `${n1(d.on_battery_hours_per_day)} h a day on battery · last ${n1(d.days)} days`);
  }

  function smooth(p) {
    if (p.length < 2) return "";
    let d = `M${p[0][0].toFixed(1)},${p[0][1].toFixed(1)}`;
    for (let i = 1; i < p.length; i++) { const [x0, y0] = p[i - 1], [x1, y1] = p[i], cx = (x0 + x1) / 2; d += ` C${cx.toFixed(1)},${y0.toFixed(1)} ${cx.toFixed(1)},${y1.toFixed(1)} ${x1.toFixed(1)},${y1.toFixed(1)}`; }
    return d;
  }

  function historySvg(h) {
    if (!h || h.length < 2) return `<div class="muted">Windows hasn't recorded enough capacity history yet.</div>`;
    const W = 800, Hh = 140, mn = Math.min(...h.map(x => x.health_pct)) - 3, mx = Math.max(100, ...h.map(x => x.health_pct));
    const x = i => 6 + i / (h.length - 1) * (W - 12), y = v => Hh - 6 - (v - mn) / (mx - mn) * (Hh - 16);
    const pts = h.map((r, i) => [x(i), y(r.health_pct)]), d = smooth(pts);
    return `<svg viewBox="0 0 ${W} ${Hh}" preserveAspectRatio="none" style="width:100%;height:140px;display:block" role="img" aria-label="Battery health from ${n1(h[0].health_pct)}% to ${n1(h[h.length - 1].health_pct)}%"><path d="${d} L${pts[pts.length - 1][0]},${Hh} L${pts[0][0]},${Hh} Z" fill="var(--good)" opacity=".1"/><path d="${d}" fill="none" stroke="var(--good)" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>
      <div style="display:flex;justify-content:space-between" class="muted"><span>${esc((h[0].start || "").slice(0, 10))} · ${n1(h[0].health_pct)}%</span><span>${esc((h[h.length - 1].end || "").slice(0, 10))} · ${n1(h[h.length - 1].health_pct)}%</span></div>`;
  }

  /* pack: /api/laptop/battery; hist: /api/laptop/battery/history; bright: /api/laptop/brightness; levelPct: a charge
     sensor from the live snapshot, used only when the battery API is unavailable. */
  function html(pack, hist, bright, levelPct) {
    const has = !!(pack && pack.has_battery);
    const pct = has && fin(pack.charge_pct) ? pack.charge_pct : fin(levelPct) ? levelPct : null;
    const state = has ? (pack.state ? esc(pack.state) : "State unavailable") : "No battery reported";
    const hero = `<div class="bt-hero"><div class="bt-ring">${ring(pct)}<div class="bt-ring-c"><div><div class="num bt-pct">${pct == null ? "–" : n0(pct)}<small>%</small></div><div class="muted">${state}</div></div></div></div>
    <div><div class="bt-verdict">${has ? esc(pack.verdict || "") : "Battery"}</div><div class="muted">${has ? [pack.maker, pack.model, pack.chemistry].filter(Boolean).map(esc).join(" · ") : ""}</div></div></div>`;
    if (!has) {
      return hero + `<div class="banner info mt"><div class="ic">+</div><div><div class="t">Battery details aren't available</div><div class="s">${pct == null ? "Windows is not reporting a battery. This is expected on desktops; on a laptop, check that Windows can detect its battery." : "A charge sensor is available, but Windows isn't reporting battery health, cycles or charge rate."}</div></div></div>`;
    }
    const p = pack, rel = !!p.capacity_relative;
    const wh = mwh => rel ? null : whRaw(mwh);
    const capacity = rel ? "this battery reports capacity in relative units, so watt-hours aren't shown" : wh(p.full_mwh) && wh(p.design_mwh) ? `${wh(p.full_mwh)} now, ${wh(p.design_mwh)} when new` : wh(p.design_mwh) ? `${wh(p.design_mwh)} when new; full charge capacity not reported` : "";
    const brightness = bright && bright.available ? `<div class="muted" style="margin-bottom:6px">Screen brightness</div><div style="display:flex;align-items:center;gap:12px"><input type="range" id="bt-br" min="0" max="100" value="${+bright.percent || 0}" style="flex:1;accent-color:var(--accent)" aria-label="Screen brightness"><b class="num" id="bt-brv">${+bright.percent || 0}%</b></div>` : "";
    return hero + `<div class="grid g4 mt bt-grid">
    ${stat("Health", fin(p.health_pct) ? n1(p.health_pct) + "%" : null, capacity)}
    ${stat("Charge cycles", fin(p.cycles) ? n0(p.cycles) : null, fin(p.cycles) ? "full charges so far" : "this battery doesn't report cycles")}
    ${powerStat(p)}
    ${timeStat(p)}
    ${stat("Wear", fin(p.wear_pct) ? n1(p.wear_pct) + "%" : null, "capacity lost since new")}
    ${stat("Remaining energy", wh(p.remaining_mwh), rel ? "relative units only" : wh(p.full_mwh) && wh(p.remaining_mwh) ? `of ${wh(p.full_mwh)}` : "")}
    ${dailyStat(p)}
    ${stat("Charger", p.plugged_in === true ? "Plugged in" : p.plugged_in === false ? "Unplugged" : null, chargerSub(p))}</div>
    <div class="grid g2 mt"><div><div class="muted" style="margin-bottom:6px">Battery health over time</div>${historySvg(hist && hist.history)}</div><div>${brightness}</div></div>
    <p class="muted mt">Charging power is what flows into the battery. Windows doesn't report the charger's total output, which also runs the PC.</p>`;
  }

  const api = { html, hm, NR };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.BatteryView = api;
})(typeof window !== "undefined" ? window : globalThis);
