#!/usr/bin/env python3
"""
Ultra Suite Insights (owner only).

  collector  127.0.0.1:8300  public through Tailscale Funnel:  POST /v1/event  (also /ultrasuite/v1/event)
             accepts {type: install|uninstall|feedback|heartbeat, version, os, reason, message, id}
             small payloads only, validated, rate limited, no IPs stored
  dashboard  127.0.0.1:8301  private: the owner dashboard + /api/stats (events + GitHub numbers)

Data: %LOCALAPPDATA%\\UltraSuite\\data\\insights.db
GitHub: public numbers need nothing; views/clones/referrers need GITHUB_TOKEN (repo read) in settings.env.
"""
import asyncio
import datetime as dt
import json
import os
import sqlite3
import time
from collections import defaultdict, deque
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("ULTRASUITE_DATA") or os.path.join(HERE, "data")
os.makedirs(DATA, exist_ok=True)
DB = os.path.join(DATA, "insights.db")
REPO = os.environ.get("INSIGHTS_REPO", "jeemwla/ultra-productivity-suite")
TYPES = {"install", "uninstall", "feedback", "heartbeat"}


def db():
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, ts REAL, type TEXT, version TEXT, os TEXT,
                 reason TEXT, message TEXT, anon TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS ev_ts ON events(ts)")
    return c


# ----------------------------------------------------------------------------- public collector
collector = FastAPI(title="Ultra Suite collector", docs_url=None, redoc_url=None, openapi_url=None)
_hits = defaultdict(deque)


def _limited(key: str, limit=30, window=3600) -> bool:
    q, now = _hits[key], time.time()
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    return False


def _clean(v, n):
    return None if v is None else str(v).replace("\x00", "")[:n]


@collector.post("/v1/event")
@collector.post("/ultrasuite/v1/event")
async def event(request: Request):
    raw = await request.body()
    if len(raw) > 4096:
        raise HTTPException(413, "too large")
    try:
        e = json.loads(raw or b"{}")
    except Exception:
        raise HTTPException(400, "bad json")
    kind = e.get("type")
    if kind not in TYPES:
        raise HTTPException(400, "bad type")
    anon = _clean(e.get("id"), 64) or "unknown"
    # rate limit by anonymous id and by the forwarding address (address is used in memory only, never stored)
    if _limited(anon) or _limited(request.headers.get("x-forwarded-for", request.client.host if request.client else "?"), 120):
        raise HTTPException(429, "slow down")
    with db() as c:
        c.execute("INSERT INTO events(ts,type,version,os,reason,message,anon) VALUES(?,?,?,?,?,?,?)",
                  (time.time(), kind, _clean(e.get("version"), 20), _clean(e.get("os"), 20),
                   _clean(e.get("reason"), 40), _clean(e.get("message"), 1000), anon))
    return {"ok": True}


@collector.get("/v1/health")
@collector.get("/ultrasuite/v1/health")
async def health():
    return {"ok": True}


# ----------------------------------------------------------------------------- owner dashboard
dashboard = FastAPI(title="Ultra Suite Insights", docs_url=None, redoc_url=None, openapi_url=None)
_gh = {"at": 0, "data": None}


async def github_stats():
    if _gh["data"] and time.time() - _gh["at"] < 600:
        return _gh["data"]
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    h = {"Accept": "application/vnd.github+json", "User-Agent": "ultra-suite-insights"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    out = {"repo": REPO, "ok": False, "traffic": None}
    try:
        async with httpx.AsyncClient(timeout=10, headers=h) as cl:
            r = (await cl.get(f"https://api.github.com/repos/{REPO}")).json()
            rel = (await cl.get(f"https://api.github.com/repos/{REPO}/releases?per_page=30")).json()
            out.update(ok=True, stars=r.get("stargazers_count"), forks=r.get("forks_count"), watchers=r.get("subscribers_count"),
                       open_issues=r.get("open_issues_count"),
                       releases=[{"tag": x.get("tag_name"), "published": x.get("published_at"),
                                  "downloads": sum(a.get("download_count", 0) for a in x.get("assets", []))} for x in rel if isinstance(x, dict)])
            out["downloads_total"] = sum(x["downloads"] for x in out["releases"])
            if token:
                views = (await cl.get(f"https://api.github.com/repos/{REPO}/traffic/views")).json()
                clones = (await cl.get(f"https://api.github.com/repos/{REPO}/traffic/clones")).json()
                refs = (await cl.get(f"https://api.github.com/repos/{REPO}/traffic/popular/referrers")).json()
                out["traffic"] = {"views": views.get("count"), "visitors": views.get("uniques"), "clones": clones.get("count"),
                                  "cloners": clones.get("uniques"), "daily": views.get("views", []),
                                  "referrers": refs if isinstance(refs, list) else []}
    except Exception as exc:
        out["error"] = f"{exc.__class__.__name__}"
    _gh.update(at=time.time(), data=out)
    return out


@dashboard.get("/")
async def page():
    return FileResponse(os.path.join(HERE, "dashboard.html"), headers={"Cache-Control": "no-cache"})


def bucket_keys(now: float, days: int):
    """Chart slots in LOCAL time, oldest first, ending with the current hour (day view) or today.

    Slots used to step through UTC midnights while events were labelled by local date, so west of UTC
    the newest slot was yesterday and today's events never reached the chart.
    """
    if days == 1:
        end = dt.datetime.fromtimestamp(now).replace(minute=0, second=0, microsecond=0)
        return [(end - dt.timedelta(hours=i)).strftime("%Y-%m-%d %H:00") for i in range(23, -1, -1)]
    end = dt.date.fromtimestamp(now)
    return [(end - dt.timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days - 1, -1, -1)]


@dashboard.get("/api/stats")
async def stats(range: str = "month"):
    days = {"day": 1, "week": 7, "month": 30, "year": 365}.get(range, 30)
    now = time.time()
    since = now - days * 86400
    with db() as c:
        rows = c.execute("SELECT ts,type,version,os,reason,message,anon FROM events WHERE ts>=? ORDER BY ts DESC", (since,)).fetchall()
        alltime = dict(c.execute("SELECT type, COUNT(*) FROM events GROUP BY type").fetchall())
        active_ids = c.execute("SELECT COUNT(DISTINCT anon) FROM events WHERE ts>=? AND type IN ('heartbeat','install')", (now - 7 * 86400,)).fetchone()[0]
    counts = defaultdict(int)
    reasons = defaultdict(int)
    versions = defaultdict(int)
    bucket = "%Y-%m-%d %H:00" if days == 1 else "%Y-%m-%d"
    series = defaultdict(lambda: {"install": 0, "uninstall": 0, "feedback": 0})
    feedback = []
    for ts, kind, ver, osn, reason, msg, anon in rows:
        counts[kind] += 1
        key = dt.datetime.fromtimestamp(ts).strftime(bucket)
        if kind in ("install", "uninstall", "feedback"):
            series[key][kind] += 1
        if kind == "uninstall":
            reasons[reason or "no answer"] += 1
        if kind == "install" and ver:
            versions[ver] += 1
        if kind in ("feedback", "uninstall") and msg:
            feedback.append({"ts": ts, "type": kind, "reason": reason, "message": msg, "version": ver})
    # fill empty buckets so the chart is continuous
    pts = [{"t": k, **series.get(k, {"install": 0, "uninstall": 0, "feedback": 0})} for k in bucket_keys(now, days)]
    return JSONResponse({
        "range": range, "counts": counts, "alltime": alltime, "active_7d": active_ids,
        "net_installs": alltime.get("install", 0) - alltime.get("uninstall", 0),
        "series": pts, "reasons": sorted(reasons.items(), key=lambda x: -x[1]),
        "versions": versions, "feedback": feedback[:100],
        "recent": [{"ts": r[0], "type": r[1], "version": r[2], "reason": r[4]} for r in rows[:40]],
        "github": await github_stats(),
        "collector": os.environ.get("ULTRASUITE_COLLECTOR_PUBLIC", ""),
    })


@dashboard.post("/api/delete")
async def delete_events(body: dict):
    """Owner-only cleanup: delete events by anonymous id (e.g. your own test events) or by row ids."""
    anon, ids = body.get("anon"), body.get("ids") or []
    with db() as c:
        n = 0
        if anon:
            n += c.execute("DELETE FROM events WHERE anon=?", (str(anon)[:64],)).rowcount
        if ids:
            n += c.execute(f"DELETE FROM events WHERE id IN ({','.join('?' * len(ids))})", [int(i) for i in ids][:500]).rowcount
    return {"ok": True, "deleted": n}


try:
    import sys as _s2
    _s2.path.insert(0, os.path.join(HERE, "..", "core"))
    from devtools import add_shared_routes as _asr
    _asr(dashboard, HERE)
except Exception as _exc2:
    print(f"[devtools] disabled: {_exc2}")


async def serve():
    import uvicorn
    servers = [uvicorn.Server(uvicorn.Config(collector, host="127.0.0.1", port=8300, log_level="warning", log_config=None)),
               uvicorn.Server(uvicorn.Config(dashboard, host="127.0.0.1", port=8301, log_level="warning", log_config=None))]
    await asyncio.gather(*(s.serve() for s in servers))


if __name__ == "__main__":
    asyncio.run(serve())
