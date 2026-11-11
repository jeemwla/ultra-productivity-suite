/* Ultra Suite appearance: themes, motion level, scroll reveal. Shared by every app.
   Add  <span data-theme-slot></span>  where the Appearance button should appear. */
(() => {
  const P = {
    light:     { name: "Light",     bg: "#f4f5f7", surface: "#fcfcfb", surface2: "#eff0f2", line: "#e3e5e9", lineStrong: "#d0d4da", ink: "#0b0b0b", ink2: "#52514e", ink3: "#7d7c78", accent: "#2a78d6", accentInk: "#ffffff", series2: "#eb6834", good: "#0ca30c", warning: "#d99a00", critical: "#d03b3b", dark: false },
    dark:      { name: "Dark",      bg: "#0f1012", surface: "#17181b", surface2: "#1f2024", line: "#26282d", lineStrong: "#34363c", ink: "#f0efec", ink2: "#c3c2b7", ink3: "#8f8e86", accent: "#3987e5", accentInk: "#ffffff", series2: "#d95926", good: "#3dd68c", warning: "#fab219", critical: "#ec5d5e", dark: true },
    midnight:  { name: "Midnight",  bg: "#0a0e1a", surface: "#111829", surface2: "#172035", line: "#1f2a44", lineStrong: "#2b3858", ink: "#e6ebff", ink2: "#aab4d4", ink3: "#7380a6", accent: "#7aa2ff", accentInk: "#0a0e1a", series2: "#ff9e64", good: "#5fd4a0", warning: "#ffc861", critical: "#ff6b81", dark: true },
    sakura:    { name: "Sakura",    bg: "#fff4f6", surface: "#fffafb", surface2: "#fde8ee", line: "#f6d5df", lineStrong: "#eebccb", ink: "#3d1f2a", ink2: "#7a4a5a", ink3: "#a7808d", accent: "#e2557f", accentInk: "#ffffff", series2: "#8a6fdf", good: "#2f9e6e", warning: "#c98a00", critical: "#d43f5a", dark: false },
    matcha:    { name: "Matcha",    bg: "#f1f4ea", surface: "#f9fbf4", surface2: "#e6ecdb", line: "#d8e0c8", lineStrong: "#c4cfae", ink: "#1f2a17", ink2: "#4d5c3d", ink3: "#7d8a6c", accent: "#5a8a3a", accentInk: "#ffffff", series2: "#b5762f", good: "#3f8a2e", warning: "#b98500", critical: "#c2452f", dark: false },
    ember:     { name: "Ember",     bg: "#15100e", surface: "#1e1714", surface2: "#271e1a", line: "#33281f", lineStrong: "#45362a", ink: "#f6e9e0", ink2: "#d0b8a8", ink3: "#9c8474", accent: "#ff7a3d", accentInk: "#15100e", series2: "#ffd166", good: "#7ed491", warning: "#ffcc4d", critical: "#ff5d5d", dark: true },
    arctic:    { name: "Arctic",    bg: "#eef5fa", surface: "#f8fcff", surface2: "#e2eef6", line: "#d2e3ef", lineStrong: "#bad3e6", ink: "#0e2433", ink2: "#3f5d72", ink3: "#7491a6", accent: "#1e88c8", accentInk: "#ffffff", series2: "#7a5af5", good: "#169c7c", warning: "#c18a00", critical: "#d64545", dark: false },
    paper:     { name: "Paper",     bg: "#f2ead8", surface: "#faf5e9", surface2: "#ebe1ca", line: "#ddd0b3", lineStrong: "#cbbb98", ink: "#3b2f22", ink2: "#6b5a45", ink3: "#958369", accent: "#a0522d", accentInk: "#fffaf0", series2: "#3f6f8f", good: "#4f7f3a", warning: "#a87400", critical: "#b23a2a", dark: false, serif: true },
    terminal:  { name: "Terminal",  bg: "#070b07", surface: "#0c120c", surface2: "#111a11", line: "#1a281a", lineStrong: "#254025", ink: "#c8f7c1", ink2: "#86c97e", ink3: "#5a8f54", accent: "#39d353", accentInk: "#041004", series2: "#e3c547", good: "#39d353", warning: "#e3c547", critical: "#ff5f56", dark: true, mono: true },
    synthwave: { name: "Synthwave", bg: "#140b26", surface: "#1d1236", surface2: "#271946", line: "#33205a", lineStrong: "#452c75", ink: "#f8e9ff", ink2: "#cfb3ec", ink3: "#9a7fc0", accent: "#ff4fd8", accentInk: "#140b26", series2: "#26e0ff", good: "#3cf0a8", warning: "#ffd24d", critical: "#ff5577", dark: true },
  };
  const ORDER = ["system", "light", "dark", "midnight", "sakura", "matcha", "ember", "arctic", "paper", "terminal", "synthwave"];
  const root = document.documentElement;
  const KEYS = ["bg","surface","surface-2","line","line-strong","ink","ink-2","ink-3","accent","accent-ink","accent-soft","series","series-2","series-track","s1","s2","s3","grid",
    "good","warning","critical","good-soft","warning-soft","critical-soft","red","red-soft","red-ink","green","green-soft","amber","amber-soft","font","font-display","mono"];
  const mix = (c, pct, base) => `color-mix(in srgb, ${c} ${pct}%, ${base})`;
  let state = { theme: "system", motion: "full" };
  try { Object.assign(state, JSON.parse(localStorage.getItem("ultra.appearance") || "{}")); } catch {}

  function apply(){
    const t = P[state.theme];
    for (const k of KEYS) root.style.removeProperty("--" + k);
    root.dataset.theme = state.theme; root.dataset.motion = state.motion;
    if (t) {
      const v = { bg: t.bg, surface: t.surface, "surface-2": t.surface2, line: t.line, "line-strong": t.lineStrong, ink: t.ink, "ink-2": t.ink2, "ink-3": t.ink3,
        accent: t.accent, "accent-ink": t.accentInk, "accent-soft": mix(t.accent, 16, t.surface), series: t.accent, "series-2": t.series2,
        "series-track": mix(t.accent, 20, t.surface), s1: t.accent, s2: t.series2, s3: t.good, grid: t.line,
        good: t.good, warning: t.warning, critical: t.critical, "good-soft": mix(t.good, 14, t.surface), "warning-soft": mix(t.warning, 16, t.surface),
        "critical-soft": mix(t.critical, 14, t.surface), red: t.critical, "red-soft": mix(t.critical, 14, t.surface), "red-ink": t.critical,
        green: t.good, "green-soft": mix(t.good, 14, t.surface), amber: t.warning, "amber-soft": mix(t.warning, 16, t.surface) };
      if (t.mono) { v.font = '"Cascadia Mono","JetBrains Mono",ui-monospace,Consolas,monospace'; v["font-display"] = v.font; }
      if (t.serif) { v["font-display"] = '"Iowan Old Style","Palatino Linotype",Georgia,serif'; }
      for (const [k, val] of Object.entries(v)) root.style.setProperty("--" + k, val);
      root.style.colorScheme = t.dark ? "dark" : "light";
    } else root.style.colorScheme = "";
    document.querySelectorAll('meta[name="theme-color"]').forEach(m => m.setAttribute("content", t ? t.bg : (matchMedia("(prefers-color-scheme: dark)").matches ? "#0f1012" : "#f5f6f8")));
    renderSwatches();
  }
  async function save(patch){
    Object.assign(state, patch); apply();
    try { localStorage.setItem("ultra.appearance", JSON.stringify(state)); } catch {}
    try { await fetch("/api/theme", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch) }); } catch {}
  }
  async function sync(){
    try { const s = await (await fetch("/api/theme", { cache: "no-store" })).json(); if (s.theme !== state.theme || s.motion !== state.motion) { Object.assign(state, s); try { localStorage.setItem("ultra.appearance", JSON.stringify(state)); } catch {} apply(); } } catch {}
  }

  /* ---------- Appearance popover ---------- */
  const css = `
  .ua-btn{display:inline-grid;place-items:center;width:34px;height:34px;border-radius:10px;border:1px solid var(--line);background:var(--surface);color:var(--ink-2);cursor:pointer;transition:transform .15s,border-color .15s,color .15s}
  .ua-btn:hover{color:var(--ink);border-color:var(--line-strong);transform:translateY(-1px)}
  .ua-pop{position:fixed;z-index:80;width:340px;max-width:calc(100vw - 24px);background:var(--surface);color:var(--ink);border:1px solid var(--line);border-radius:16px;
    box-shadow:0 24px 60px -20px rgba(0,0,0,.45);padding:14px;opacity:0;transform:translateY(-6px) scale(.98);transform-origin:top right;pointer-events:none;transition:opacity .18s,transform .22s cubic-bezier(.2,.9,.25,1)}
  .ua-pop.show{opacity:1;transform:none;pointer-events:auto}
  .ua-pop h4{font:600 13px var(--font,system-ui);margin:2px 2px 10px;color:var(--ink)}
  .ua-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:14px}
  .ua-sw{border:1px solid var(--line);border-radius:12px;padding:8px;cursor:pointer;background:transparent;text-align:left;color:var(--ink);transition:border-color .15s,transform .15s}
  .ua-sw:hover{transform:translateY(-1px);border-color:var(--line-strong)}
  .ua-sw[aria-pressed=true]{border-color:var(--accent);box-shadow:0 0 0 2px var(--accent-soft)}
  .ua-sw .pv{height:34px;border-radius:8px;display:flex;align-items:flex-end;gap:3px;padding:5px;margin-bottom:6px}
  .ua-sw .pv i{display:block;width:10px;border-radius:3px}
  .ua-sw span{font:600 11.5px var(--font,system-ui)}
  .ua-seg{display:flex;background:var(--surface-2);border:1px solid var(--line);border-radius:10px;padding:3px;gap:2px}
  .ua-seg button{flex:1;border:0;background:transparent;height:30px;border-radius:7px;font:600 12px var(--font,system-ui);color:var(--ink-2);cursor:pointer}
  .ua-seg button[aria-pressed=true]{background:var(--surface);color:var(--ink);box-shadow:0 1px 2px rgba(0,0,0,.15)}
  .ua-row{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-top:4px}
  .ua-row label{font:500 12.5px var(--font,system-ui);color:var(--ink-2)}
  [data-motion=off] *,[data-motion=off] *::before,[data-motion=off] *::after{animation:none!important;transition:none!important}
  [data-motion=reduced] *{animation-duration:.01ms!important;animation-iteration-count:1!important}
  .ua-reveal{opacity:0;transform:translateY(14px);transition:opacity .55s cubic-bezier(.2,.9,.25,1),transform .65s cubic-bezier(.2,.9,.25,1)}
  .ua-reveal.in{opacity:1;transform:none}
  [data-motion=off] .ua-reveal,[data-motion=reduced] .ua-reveal{opacity:1;transform:none}
  html{scroll-behavior:smooth} [data-motion=off]{scroll-behavior:auto}
  body{transition:background-color .35s ease,color .35s ease}`;
  const st = document.createElement("style"); st.textContent = css; document.head.appendChild(st);
  const ICON = '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="13.5" cy="6.5" r="1.5"/><circle cx="17.5" cy="10.5" r="1.5"/><circle cx="8.5" cy="7.5" r="1.5"/><circle cx="6.5" cy="12.5" r="1.5"/><path d="M12 2a10 10 0 1 0 0 20c.9 0 1.6-.7 1.6-1.6 0-.4-.2-.8-.4-1.1-.3-.3-.4-.7-.4-1.1 0-.9.7-1.6 1.6-1.6H16a6 6 0 0 0 6-6C22 6 17.5 2 12 2Z"/></svg>';
  let pop = null;
  apply();
  function renderSwatches(){
    if (!pop) return;
    pop.querySelectorAll(".ua-sw").forEach(b => b.setAttribute("aria-pressed", b.dataset.t === state.theme));
    pop.querySelectorAll(".ua-seg button").forEach(b => b.setAttribute("aria-pressed", b.dataset.m === state.motion));
  }
  function build(){
    pop = document.createElement("div"); pop.className = "ua-pop"; pop.setAttribute("role", "dialog"); pop.setAttribute("aria-label", "Appearance");
    const sw = (k) => { const t = P[k] || { bg: "linear-gradient(135deg,#f4f5f7 50%,#0f1012 50%)", accent: "#2a78d6", series2: "#eb6834", good: "#0ca30c", name: "System" };
      return `<button class="ua-sw" data-t="${k}" aria-pressed="false"><div class="pv" style="background:${t.bg};border:1px solid ${t.line || "rgba(127,127,127,.3)"}">
        <i style="height:60%;background:${t.accent}"></i><i style="height:85%;background:${t.series2}"></i><i style="height:40%;background:${t.good}"></i></div><span>${t.name}</span></button>`; };
    pop.innerHTML = `<h4>Theme</h4><div class="ua-grid">${ORDER.map(sw).join("")}</div>
      <div class="ua-row"><label>Motion</label><div class="ua-seg" style="width:200px"><button data-m="full">Full</button><button data-m="reduced">Reduced</button><button data-m="off">Off</button></div></div>`;
    document.body.appendChild(pop);
    pop.addEventListener("click", (e) => { const s = e.target.closest(".ua-sw"), m = e.target.closest("[data-m]"); if (s) save({ theme: s.dataset.t }); if (m) save({ motion: m.dataset.m }); });
    renderSwatches();
  }
  function toggle(btn){
    if (!pop) build();
    const show = !pop.classList.contains("show");
    if (show) { // open below or above the button, toward whichever side has room
      const r = btn.getBoundingClientRect(), h = pop.offsetHeight || 330, below = innerHeight - r.bottom > h + 16;
      pop.style.top = below ? (r.bottom + 8) + "px" : Math.max(12, r.top - h - 8) + "px";
      if (r.left < innerWidth / 2) { pop.style.left = Math.max(12, r.left) + "px"; pop.style.right = "auto"; pop.style.transformOrigin = below ? "top left" : "bottom left"; }
      else { pop.style.right = Math.max(12, innerWidth - r.right) + "px"; pop.style.left = "auto"; pop.style.transformOrigin = below ? "top right" : "bottom right"; } }
    pop.classList.toggle("show", show);
  }
  document.addEventListener("click", (e) => { if (pop && pop.classList.contains("show") && !pop.contains(e.target) && !e.target.closest(".ua-btn")) pop.classList.remove("show"); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && pop) pop.classList.remove("show"); });

  /* ---------- Scroll reveal for cards below the fold ---------- */
  function reveal(){
    if (!("IntersectionObserver" in window)) return;
    const io = new IntersectionObserver((es) => es.forEach(en => { if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); } }), { rootMargin: "0px 0px -8% 0px" });
    document.querySelectorAll("[data-reveal], .card").forEach((el, i) => {
      const r = el.getBoundingClientRect(); if (r.top < innerHeight * 0.92) return; // already visible: no animation
      el.classList.add("ua-reveal"); el.style.transitionDelay = `${Math.min(i % 4, 3) * 40}ms`; io.observe(el);
    });
  }
  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-theme-slot]").forEach(slot => {
      const b = document.createElement("button"); b.className = "ua-btn"; b.type = "button"; b.title = "Appearance"; b.setAttribute("aria-label", "Appearance");
      b.innerHTML = ICON; b.addEventListener("click", (e) => { e.stopPropagation(); toggle(b); }); slot.replaceWith(b);
    });
    setTimeout(reveal, 60);
  });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if (state.theme === "system") apply(); });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) sync(); });
  sync();
  window.UltraTheme = { set: (t) => save({ theme: t }), themes: ORDER };
})();
