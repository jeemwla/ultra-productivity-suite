/* PC Sentinel Overview edit mode: cards flow in order into a snapping grid, like a phone home screen.
   Plain JavaScript, no dependencies. The pure layout functions are exported for node tests. */
(function (root) {
  "use strict";
  const VERSION = 1;

  // Size in the 12-column layout; 6-column and 2-column layouts are derived (see sizeFor).
  const CARDS = [
    { id: "cpu", w: 3, h: 2, minW: 2, minH: 2, maxH: 4, tile: true },
    { id: "gpu", w: 3, h: 2, minW: 2, minH: 2, maxH: 4, tile: true },
    { id: "ram", w: 3, h: 2, minW: 2, minH: 2, maxH: 4, tile: true },
    { id: "pw", w: 3, h: 2, minW: 2, minH: 2, maxH: 4, tile: true },
    { id: "apps", w: 8, h: 5, minW: 4, minH: 3, maxH: 12 },
    { id: "quick", w: 4, h: 5, minW: 3, minH: 3, maxH: 12 },
    { id: "drives", w: 4, h: 2, minW: 3, minH: 2, maxH: 8 },
    { id: "net", w: 4, h: 2, minW: 3, minH: 2, maxH: 8 },
    { id: "cool", w: 4, h: 2, minW: 3, minH: 2, maxH: 8 },
    { id: "battery", w: 4, h: 2, minW: 3, minH: 2, maxH: 8 },
  ];

  function colsForWidth(px) { return px <= 760 ? 2 : px <= 1200 ? 6 : 12; }

  function sizeFor(card, cols) {
    if (cols >= 12) return [card.w, card.h];
    if (cols >= 6) return [card.w <= 3 ? 3 : 6, card.h];
    return [card.w <= 3 ? 1 : 2, card.h];
  }

  function limitsFor(card, cols) {
    const minW = cols >= 12 ? card.minW : cols >= 6 ? 2 : 1;
    return { minW: Math.min(minW, cols), maxW: cols, minH: card.minH, maxH: card.maxH };
  }

  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

  function defaults() { return { v: VERSION, order: CARDS.map(c => c.id), sizes: {} }; }

  /* Saved layout -> a valid one: unknown ids dropped, new cards appended in default order, sizes clamped. */
  function normalize(saved) {
    const known = new Set(CARDS.map(c => c.id));
    const out = defaults();
    if (!saved || typeof saved !== "object" || saved.v !== VERSION) return out;
    const order = Array.isArray(saved.order) ? saved.order.filter((id, i, a) => known.has(id) && a.indexOf(id) === i) : [];
    for (const c of CARDS) if (!order.includes(c.id)) {
      const at = CARDS.findIndex(x => x.id === c.id);
      const before = CARDS.slice(0, at).map(x => x.id).filter(id => order.includes(id)).pop();
      order.splice(before ? order.indexOf(before) + 1 : 0, 0, c.id);
    }
    out.order = order;
    for (const cols of ["12", "6", "2"]) {
      const src = saved.sizes && saved.sizes[cols];
      if (!src || typeof src !== "object") continue;
      const dst = {};
      for (const c of CARDS) {
        const s = src[c.id];
        if (!Array.isArray(s) || s.length !== 2 || !s.every(Number.isInteger)) continue;
        const L = limitsFor(c, +cols);
        dst[c.id] = [clamp(s[0], L.minW, L.maxW), clamp(s[1], L.minH, L.maxH)];
      }
      out.sizes[cols] = dst;
    }
    return out;
  }

  function sizeOf(layout, id, cols) {
    const card = CARDS.find(c => c.id === id);
    const saved = layout.sizes[String(cols)] && layout.sizes[String(cols)][id];
    return saved ? saved.slice() : sizeFor(card, cols);
  }

  /* Places cards in order, each at the first free cell at or after the previous card (CSS grid "sparse" placement). */
  function flow(layout, cols, visible) {
    const taken = new Set();
    const free = (x, y, w, h) => { for (let r = y; r < y + h; r++) for (let c = x; c < x + w; c++) if (taken.has(c + "," + r)) return false; return true; };
    const placed = {};
    let cx = 0, cy = 0;
    for (const id of layout.order) {
      if (visible && !visible(id)) continue;
      let [w, h] = sizeOf(layout, id, cols);
      w = Math.min(w, cols);
      let x = cx, y = cy;
      for (;;) {
        if (x + w > cols) { x = 0; y++; continue; }
        if (free(x, y, w, h)) break;
        x++;
      }
      for (let r = y; r < y + h; r++) for (let c = x; c < x + w; c++) taken.add(c + "," + r);
      placed[id] = { x, y, w, h };
      cx = x + w; cy = y;
    }
    return placed;
  }

  function moveTo(layout, id, index) {
    const order = layout.order.filter(x => x !== id);
    order.splice(clamp(index, 0, order.length), 0, id);
    return { ...layout, order };
  }

  /* Index among visible cards for a drop at cell (tx, ty): the order whose resulting slot is closest. */
  function nearestIndex(layout, id, tx, ty, cols, visible) {
    const ids = layout.order.filter(x => x !== id && (!visible || visible(x)));
    const current = layout.order.filter(x => !visible || visible(x)).indexOf(id);
    let best = current, bestScore = Infinity;
    for (let i = 0; i <= ids.length; i++) {
      const p = flow(moveVisible(layout, id, i, visible), cols, visible)[id];
      const score = Math.hypot(p.x - tx, (p.y - ty) * 1.2) + Math.abs(i - current) * 0.001;
      if (score < bestScore) { bestScore = score; best = i; }
    }
    return best;
  }

  /* Moves `id` to the visible position `index`, keeping hidden cards where they were relative to the rest. */
  function moveVisible(layout, id, index, visible) {
    const vis = layout.order.filter(x => x !== id && (!visible || visible(x)));
    vis.splice(clamp(index, 0, vis.length), 0, id);
    const order = [];
    let k = 0;
    for (const x of layout.order) order.push(!visible || visible(x) ? vis[k++] : x);
    return { ...layout, order };
  }

  function resize(layout, id, w, h, cols) {
    const card = CARDS.find(c => c.id === id);
    const L = limitsFor(card, cols);
    const key = String(cols);
    const sizes = { ...layout.sizes, [key]: { ...(layout.sizes[key] || {}), [id]: [clamp(Math.round(w), L.minW, L.maxW), clamp(Math.round(h), L.minH, L.maxH)] } };
    return { ...layout, sizes };
  }

  /* Keyboard: left/right step through the order; up/down jump to the slot nearest one row above or below. */
  function keyMove(layout, id, key, cols, visible) {
    const vis = layout.order.filter(x => !visible || visible(x));
    const i = vis.indexOf(id), p = flow(layout, cols, visible)[id];
    if (key === "ArrowLeft") return moveVisible(layout, id, i - 1, visible);
    if (key === "ArrowRight") return moveVisible(layout, id, i + 1, visible);
    if (key === "ArrowUp" || key === "ArrowDown") {
      const ty = key === "ArrowUp" ? Math.max(0, p.y - 1) : p.y + p.h;
      const all = flow(layout, cols, visible);
      const target = Object.entries(all).filter(([k, q]) => k !== id && (key === "ArrowUp" ? q.y < p.y : q.y > p.y))
        .sort((a, b) => Math.abs(a[1].y - ty) - Math.abs(b[1].y - ty) || Math.abs(a[1].x - p.x) - Math.abs(b[1].x - p.x))[0];
      if (!target) return layout;
      return moveVisible(layout, id, vis.indexOf(target[0]), visible);
    }
    return layout;
  }

  const api = { CARDS, VERSION, colsForWidth, sizeFor, limitsFor, defaults, normalize, sizeOf, flow, moveTo, moveVisible, nearestIndex, resize, keyMove };

  /* ---------------------------------------------------------------- browser controller */
  function createEditor(opt) {
    const grid = opt.grid, doc = grid.ownerDocument, win = doc.defaultView;
    let layout = normalize(opt.load()), editing = false, drag = null;
    const visible = id => opt.isVisible(id);
    const items = () => [...grid.querySelectorAll(":scope > .ov-item")];
    const itemFor = id => grid.querySelector(`:scope > .ov-item[data-card="${id}"]`);
    const cols = () => colsForWidth(win.innerWidth);
    const say = t => { if (opt.live) opt.live.textContent = t; };
    const label = id => (itemFor(id) && itemFor(id).dataset.label) || id;

    function metrics() {
      const cs = win.getComputedStyle(grid), rect = grid.getBoundingClientRect();
      const gap = parseFloat(cs.columnGap) || 16, rgap = parseFloat(cs.rowGap) || gap, n = cols();
      const colW = (grid.clientWidth - gap * (n - 1)) / n;
      const unit = parseFloat(cs.getPropertyValue("--ov-row")) || 64;
      const tracks = (cs.gridTemplateRows || "").split(/\s+/).map(parseFloat).filter(v => !isNaN(v));
      const tops = [];
      let t = 0;
      for (const h of tracks) { tops.push(t); t += h + rgap; }
      return { rect, gap, rgap, colW, unit, tops, n, rowTop: r => r < tops.length ? tops[r] : t + (r - tops.length) * (unit + rgap) };
    }

    function apply(animate) {
      const n = cols(), placed = flow(layout, n, visible);
      const before = new Map();
      if (animate && !opt.motionOff()) for (const el of items()) if (!el.hidden && el !== (drag && drag.el)) before.set(el, el.getBoundingClientRect());
      const rank = Object.entries(placed).sort((a, b) => a[1].y - b[1].y || a[1].x - b[1].x).map(([id]) => id);
      for (const el of items()) {
        const id = el.dataset.card, p = placed[id];
        el.hidden = !p;
        if (!p) continue;
        el.style.gridColumn = `${p.x + 1} / span ${p.w}`;
        el.style.gridRow = `${p.y + 1} / span ${p.h}`;
        el.style.order = String(rank.indexOf(id));
        el.dataset.w = p.w; el.dataset.h = p.h;
      }
      for (const [el, r] of before) {
        const now = el.getBoundingClientRect(), dx = r.left - now.left, dy = r.top - now.top;
        if (!dx && !dy) continue;
        el.style.transition = "none";
        el.style.transform = `translate(${dx}px,${dy}px)`;
        el.getBoundingClientRect();
        el.style.transition = "transform .28s cubic-bezier(.22,1,.36,1)";
        el.style.transform = "";
        el.addEventListener("transitionend", function done() { el.style.transition = ""; el.removeEventListener("transitionend", done); });
      }
      return placed;
    }

    function save() { opt.save(layout); }

    function setEditing(on) {
      editing = !!on;
      grid.classList.toggle("editing", editing);
      for (const el of items()) {
        el.tabIndex = editing ? 0 : -1;
        el.setAttribute("aria-roledescription", editing ? "movable card" : "card");
        let handle = el.querySelector(":scope > .ov-resize");
        if (editing && !handle) {
          handle = doc.createElement("span");
          handle.className = "ov-resize";
          handle.setAttribute("aria-hidden", "true");
          el.appendChild(handle);
        } else if (!editing && handle) handle.remove();
      }
      if (opt.onMode) opt.onMode(editing);
      say(editing ? "Edit mode on. Drag cards or use arrow keys to move the focused card; Shift with arrow keys resizes it. Press Escape or Done to finish." : "Layout saved.");
    }

    function reset() { layout = defaults(); save(); apply(true); say("Layout reset to the default."); }

    function cellAt(m, left, top) {
      const x = Math.round((left - m.rect.left) / (m.colW + m.gap));
      let y = 0, best = Infinity;
      for (let r = 0; r < m.tops.length + 12; r++) { const d = Math.abs(m.rowTop(r) - (top - m.rect.top)); if (d < best) { best = d; y = r; } }
      return { x: Math.max(0, x), y };
    }

    function onDown(e) {
      if (!editing || e.button > 0) return;
      const el = e.target.closest(".ov-item");
      if (!el || el.parentElement !== grid) return;
      e.preventDefault();
      const r = el.getBoundingClientRect(), resizing = !!e.target.closest(".ov-resize");
      drag = { el, id: el.dataset.card, resizing, sx: e.clientX, sy: e.clientY, ox: e.clientX - r.left, oy: e.clientY - r.top, w0: r.width, h0: r.height, cell0: [+el.dataset.w, +el.dataset.h], pid: e.pointerId };
      try { el.setPointerCapture(e.pointerId); } catch (_) { /* synthetic or already released pointer */ }
      el.classList.add(resizing ? "resizing" : "dragging");
    }

    function onMove(e) {
      if (!drag || e.pointerId !== drag.pid) return;
      const m = metrics(), n = cols();
      if (drag.resizing) {
        const w = Math.round((drag.w0 + (e.clientX - drag.sx) + m.gap) / (m.colW + m.gap));
        const h = Math.round((drag.h0 + (e.clientY - drag.sy) + m.rgap) / (m.unit + m.rgap));
        const cur = sizeOf(layout, drag.id, n);
        const next = resize(layout, drag.id, w, h, n), ns = sizeOf(next, drag.id, n);
        if (ns[0] !== cur[0] || ns[1] !== cur[1]) { layout = next; apply(true); say(`${label(drag.id)}: ${ns[0]} columns wide, ${ns[1]} rows tall`); }
        return;
      }
      const cell = cellAt(m, e.clientX - drag.ox, e.clientY - drag.oy);
      const index = nearestIndex(layout, drag.id, cell.x, cell.y, n, visible);
      const curIndex = layout.order.filter(visible).indexOf(drag.id);
      if (index !== curIndex) { layout = moveVisible(layout, drag.id, index, visible); apply(true); }
      // the card follows the pointer from its (new) slot
      drag.el.style.transition = "none";
      drag.el.style.transform = "";
      const slot = drag.el.getBoundingClientRect();
      drag.el.style.transform = `translate(${e.clientX - drag.ox - slot.left}px,${e.clientY - drag.oy - slot.top}px) scale(1.02)`;
    }

    function onUp(e) {
      if (!drag || (e && e.pointerId !== drag.pid)) return;
      const el = drag.el, wasResize = drag.resizing, id = drag.id;
      drag = null;
      el.classList.remove("dragging", "resizing");
      if (!opt.motionOff()) el.style.transition = "transform .26s cubic-bezier(.22,1,.36,1)";
      el.style.transform = "";
      el.addEventListener("transitionend", function done() { el.style.transition = ""; el.removeEventListener("transitionend", done); });
      save();
      const p = flow(layout, cols(), visible)[id];
      if (p) say(wasResize ? `${label(id)} resized to ${p.w} by ${p.h}.` : `${label(id)} moved to row ${p.y + 1}, column ${p.x + 1}.`);
    }

    function onKey(e) {
      if (!editing) return;
      const el = e.target.closest && e.target.closest(".ov-item");
      if (!el || el.parentElement !== grid || !/^Arrow/.test(e.key)) return;
      e.preventDefault();
      const id = el.dataset.card, n = cols();
      if (e.shiftKey) {
        const [w, h] = sizeOf(layout, id, n);
        const dw = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0, dh = e.key === "ArrowDown" ? 1 : e.key === "ArrowUp" ? -1 : 0;
        layout = resize(layout, id, w + dw, h + dh, n);
      } else layout = keyMove(layout, id, e.key, n, visible);
      apply(true); save(); el.focus();
      const p = flow(layout, n, visible)[id];
      say(`${label(id)}: row ${p.y + 1}, column ${p.x + 1}, ${p.w} by ${p.h}.`);
    }

    grid.addEventListener("pointerdown", onDown);
    grid.addEventListener("pointermove", onMove);
    grid.addEventListener("pointerup", onUp);
    grid.addEventListener("pointercancel", onUp);
    grid.addEventListener("keydown", onKey);
    doc.addEventListener("keydown", e => { if (editing && e.key === "Escape") { e.preventDefault(); if (drag) onUp(); setEditing(false); } });
    grid.addEventListener("click", e => { if (editing) { e.preventDefault(); e.stopPropagation(); } }, true);
    let lastCols = cols();
    win.addEventListener("resize", () => { if (cols() !== lastCols) { lastCols = cols(); apply(false); } });
    apply(false);
    return { apply, setEditing, reset, get editing() { return editing; }, get layout() { return layout; } };
  }

  api.createEditor = createEditor;
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.OverviewLayout = api;
})(typeof window !== "undefined" ? window : globalThis);
