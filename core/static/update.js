/* Ultra Suite update notice, shared by every app.
   Checks /api/update (GitHub releases, cached by the core) and shows a small card when a newer version exists.
   Apps with their own update banner (PC Sentinel) mark it with id="upd" and this script stays quiet. */
(() => {
  const KEY = "ultra.update.skip";
  const css = `
  .uu-card{position:fixed;left:20px;bottom:20px;z-index:75;display:flex;gap:12px;align-items:center;max-width:min(420px,calc(100vw - 40px));
    background:var(--surface,#17181b);color:var(--ink,#f0efec);border:1px solid var(--line,#26282d);border-radius:14px;padding:12px 14px;
    box-shadow:0 18px 40px -16px rgba(0,0,0,.5);transform:translateY(16px);opacity:0;transition:transform .35s cubic-bezier(.22,1,.36,1),opacity .25s}
  .uu-card.show{transform:none;opacity:1}
  .uu-ic{width:32px;height:32px;border-radius:50%;display:grid;place-items:center;background:var(--accent,#3987e5);color:#fff;font-weight:700;flex:none}
  .uu-t{font:600 13.5px var(--font,system-ui)}.uu-s{font:12.5px var(--font,system-ui);color:var(--ink-3,#8f8e86)}
  .uu-b{height:32px;padding:0 12px;border-radius:9px;border:1px solid var(--line-strong,#34363c);background:transparent;color:inherit;font:600 12.5px var(--font,system-ui);cursor:pointer;white-space:nowrap}
  .uu-b.p{background:var(--accent,#3987e5);border-color:var(--accent,#3987e5);color:var(--accent-ink,#fff)}`;
  async function check() {
    if (document.getElementById("upd")) return;
    let u; try { u = await (await fetch("/api/update", { cache: "no-store" })).json(); } catch { return; }
    let skip = ""; try { skip = localStorage.getItem(KEY) || ""; } catch {}
    if (!u || !u.available || skip === u.latest) return;
    const st = document.createElement("style"); st.textContent = css; document.head.appendChild(st);
    const c = document.createElement("div"); c.className = "uu-card"; c.setAttribute("role", "status");
    c.innerHTML = `<div class="uu-ic">↑</div><div style="flex:1;min-width:0"><div class="uu-t">Ultra Suite ${u.latest} is available</div><div class="uu-s">You have ${u.current}. Updating takes about a minute.</div></div>
      <button class="uu-b" data-a="later">Later</button><button class="uu-b p" data-a="go">Update</button>`;
    document.body.appendChild(c); requestAnimationFrame(() => c.classList.add("show"));
    c.addEventListener("click", async (e) => {
      const a = e.target.closest("[data-a]"); if (!a) return;
      if (a.dataset.a === "later") { try { localStorage.setItem(KEY, u.latest); } catch {} c.classList.remove("show"); setTimeout(() => c.remove(), 400); return; }
      a.disabled = true; a.textContent = "Downloading…";
      try { const r = await fetch("/api/update/install", { method: "POST" }); if (!r.ok) throw new Error(); c.querySelector(".uu-s").textContent = "The installer is starting. Ultra Suite reopens when it's done."; a.textContent = "Started"; }
      catch { a.disabled = false; a.textContent = "Try again"; c.querySelector(".uu-s").textContent = "Couldn't download the update. Check your connection."; }
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => setTimeout(check, 1500)); else setTimeout(check, 1500);
})();
