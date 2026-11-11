"""
Shared routes every Ultra Suite web app mounts (alarm, sentinel, insights):

  /__ui/<file>          shared UI assets: theme.css, theme.js, motion.js (core/static)
  /__dev/reload.js      live reload in dev mode (ULTRASUITE_DEV=1); empty script otherwise
  /__dev/version        newest modification time of the app's UI files
  /api/theme            GET/POST the theme chosen in any app, so all apps match
  /api/update           GET: is a newer Ultra Suite on GitHub? (cached, checked every 6 hours)
  /api/update/install   POST: download the newest installer and start it
  /api/about            app version, mode, data folder
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time

import httpx
from fastapi import HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
ROOT = os.path.dirname(HERE)
REPO = os.environ.get("ULTRASUITE_REPO", "jeemwla/ultra-productivity-suite")
DATA = os.environ.get("ULTRASUITE_DATA") or os.path.join(ROOT, "data")
THEMES = {"system", "light", "dark", "midnight", "sakura", "matcha", "ember", "arctic", "paper", "terminal", "synthwave"}
_upd = {"at": 0.0, "data": None}


def version() -> str:
    for p in (os.path.join(ROOT, "VERSION"), os.path.join(getattr(sys, "_MEIPASS", ROOT), "VERSION")):
        try:
            with open(p, encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            pass
    return "0.0.0"


def summarize_notes(notes: str, limit: int = 140) -> str:
    """One line for the update notice: the first plain sentence after the title, else the first bullet's lead."""
    def clean(s):
        return re.sub(r"[*_`#>]+", "", s).strip()
    lines = [ln.strip() for ln in (notes or "").splitlines() if ln.strip()]
    plain = [ln for ln in lines if not ln.startswith(("#", "-", "*", "|")) and len(clean(ln)) > 20]
    bullets = [ln[1:].strip() for ln in lines if ln.startswith(("- ", "* "))]
    pick = clean(plain[0]) if plain else clean(bullets[0]) if bullets else ""
    if not plain and pick:
        m = re.match(r"(.+?[.!?])(\s|$)", pick)  # a bullet's bold lead usually ends at its first full stop
        pick = m.group(1) if m else pick
    return pick if len(pick) <= limit else pick[:limit - 1].rsplit(" ", 1)[0] + "…"


def _vtuple(v: str):
    out = []
    for part in v.lstrip("vV").split("."):
        num = "".join(ch for ch in part if ch.isdigit())
        out.append(int(num) if num else 0)
    return tuple(out + [0] * (3 - len(out)))


DEV_JS = """(() => { let v = null; const tick = async () => {
  try { const r = await fetch('/__dev/version', {cache: 'no-store'}); const n = (await r.json()).v;
    if (v !== null && n !== v) location.reload(); v = n; } catch {}
  setTimeout(tick, 700); }; tick();
  const b = document.createElement('div'); b.textContent = 'DEV'; b.title = 'Live reload is on';
  b.style.cssText = 'position:fixed;left:50%;transform:translateX(-50%);top:6px;z-index:99;font:600 10px/1 system-ui;letter-spacing:.08em;padding:5px 7px;border-radius:6px;background:#e5484d;color:#fff;opacity:.85;pointer-events:none';
  document.addEventListener('DOMContentLoaded', () => document.body.appendChild(b)); })();"""


LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
# Only origins that actually serve suite pages. A different localhost service is
# not a trusted browser client. Keep this in sync with the native window URLs.
SUITE_ORIGIN = re.compile(
    r"(?:https?://(?:127\.0\.0\.1|localhost|\[::1\]):(?:8081|8084|8098|8301)"
    r"|https?://tauri\.localhost|tauri://localhost)\Z"
)


def host_name(host_header: str) -> str:
    h = (host_header or "").strip().lower()
    return h.split("]")[0] + "]" if h.startswith("[") else h.split(":")[0]


def request_allowed(method: str, host_header: str, origin) -> bool:
    """Local services answer only requests addressed to this PC (blocks DNS rebinding), and accept changes only
    from the suite's own pages: a website open in your browser can't change alarms, power settings or the VPN."""
    if host_name(host_header) not in LOCAL_HOSTS:
        return False
    if method.upper() in ("GET", "HEAD", "OPTIONS") or not origin:
        return True
    return bool(SUITE_ORIGIN.fullmatch(origin))


class LocalWebSocketGuard:
    """Check the handshake before any route accepts or starts work.

    Browser handshakes have an Origin. Origin-less native/local clients retain
    the HTTP API's existing trust model; this is not local-process authentication.
    A sandboxed page's literal 'null' Origin is never treated as missing.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            hosts = [v.decode("latin-1") for k, v in scope["headers"] if k.lower() == b"host"]
            origins = [v.decode("latin-1") for k, v in scope["headers"] if k.lower() == b"origin"]
            if (len(hosts) != 1 or len(origins) > 1
                    or host_name(hosts[0]) not in LOCAL_HOSTS
                    or (origins and not SUITE_ORIGIN.fullmatch(origins[0]))):
                await send({"type": "websocket.close", "code": 1008})
                return
        await self.app(scope, receive, send)


def add_local_guard(app):
    from fastapi.middleware.cors import CORSMiddleware
    app.add_middleware(CORSMiddleware, allow_origin_regex=SUITE_ORIGIN.pattern, allow_methods=["*"], allow_headers=["*"])
    app.add_middleware(LocalWebSocketGuard)

    @app.middleware("http")
    async def _local_only(request, call_next):
        if not request_allowed(request.method, request.headers.get("host", ""), request.headers.get("origin")):
            return JSONResponse({"detail": "Only Ultra Suite's own windows can do this."}, status_code=403)
        return await call_next(request)


def add_shared_routes(app, frontend_dir: str):
    dev = os.environ.get("ULTRASUITE_DEV") == "1"
    add_local_guard(app)

    @app.get("/__ui/icons/{name}")
    async def ui_icon(name: str):
        if not re.fullmatch(r"[a-z]+\.png", name or ""):
            raise HTTPException(404)
        p = os.path.join(STATIC, "icons", name)
        if not os.path.isfile(p):
            raise HTTPException(404)
        return FileResponse(p, media_type="image/png", headers={"Cache-Control": "max-age=3600"})

    @app.get("/__ui/{name}")
    async def ui_asset(name: str):
        if "/" in name or "\\" in name or ".." in name:
            raise HTTPException(404)
        p = os.path.join(STATIC, name)
        if not os.path.isfile(p):
            raise HTTPException(404)
        media = "text/css" if name.endswith(".css") else "application/javascript" if name.endswith(".js") else None
        return FileResponse(p, media_type=media, headers={"Cache-Control": "no-cache"})

    @app.get("/__dev/reload.js")
    async def dev_reload():
        return Response(DEV_JS if dev else "", media_type="application/javascript", headers={"Cache-Control": "no-store"})

    @app.get("/__dev/version")
    async def dev_version():
        newest = 0.0
        for base in (frontend_dir, STATIC):
            for dp, _, files in os.walk(base):
                for f in files:
                    try:
                        newest = max(newest, os.path.getmtime(os.path.join(dp, f)))
                    except OSError:
                        pass
        return {"v": newest}

    @app.get("/api/theme")
    async def get_theme():
        try:
            return json.load(open(os.path.join(DATA, "theme.json"), encoding="utf-8"))
        except Exception:
            return {"theme": "system", "motion": "full", "density": "comfortable"}

    @app.post("/api/theme")
    async def set_theme(body: dict):
        cur = await get_theme()
        if body.get("theme") in THEMES:
            cur["theme"] = body["theme"]
        if body.get("motion") in ("full", "reduced", "off"):
            cur["motion"] = body["motion"]
        if body.get("density") in ("comfortable", "compact"):
            cur["density"] = body["density"]
        os.makedirs(DATA, exist_ok=True)
        json.dump(cur, open(os.path.join(DATA, "theme.json"), "w", encoding="utf-8"))
        return cur

    @app.get("/api/about")
    async def about():
        return {"version": version(), "dev": dev, "data": DATA, "repo": REPO}

    @app.get("/api/update")
    async def check_update(force: bool = False):
        if _upd["data"] and not force and time.time() - _upd["at"] < 6 * 3600:
            return _upd["data"]
        cur = version()
        out = {"current": cur, "latest": cur, "available": False}
        try:
            async with httpx.AsyncClient(timeout=8, headers={"Accept": "application/vnd.github+json", "User-Agent": "ultra-suite"}) as c:
                r = (await c.get(f"https://api.github.com/repos/{REPO}/releases/latest")).json()
            tag = r.get("tag_name") or cur
            asset = next((a for a in r.get("assets", []) if a.get("name", "").lower().endswith(".exe") and "laptop" not in a["name"].lower()), None)
            out.update(latest=tag.lstrip("vV"), available=_vtuple(tag) > _vtuple(cur), notes=(r.get("body") or "")[:4000],
                       summary=summarize_notes(r.get("body") or ""),
                       url=r.get("html_url"), asset=asset.get("browser_download_url") if asset else None,
                       published=r.get("published_at"))
        except Exception as exc:
            out["error"] = exc.__class__.__name__
        _upd.update(at=time.time(), data=out)
        return out

    @app.post("/api/update/install")
    async def install_update():
        info = await check_update(force=True)
        if not info.get("available") or not info.get("asset"):
            raise HTTPException(409, "You're already on the newest version.")
        dest = os.path.join(tempfile.gettempdir(), f"UltraSuite-Setup-{info['latest']}.exe")
        try:
            async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
                async with c.stream("GET", info["asset"]) as r:
                    r.raise_for_status()
                    with open(dest, "wb") as f:
                        async for chunk in r.aiter_bytes():
                            f.write(chunk)
        except Exception:
            raise HTTPException(502, "Couldn't download the update. Check your connection and try again.")
        # The installer closes the running app itself, then reinstalls in place and restarts it.
        subprocess.Popen([dest], close_fds=True, creationflags=0x00000008 if os.name == "nt" else 0)  # DETACHED_PROCESS
        return {"ok": True, "started": dest}

    return app


def mount_static_page(app, frontend_dir: str):
    """Serve <frontend_dir>/index.html with no-cache so UI edits show on reload."""
    @app.get("/")
    async def page():
        return FileResponse(os.path.join(frontend_dir, "index.html"), headers={"Cache-Control": "no-cache"})
