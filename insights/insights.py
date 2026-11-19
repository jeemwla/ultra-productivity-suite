#!/usr/bin/env python3
"""
Ultra Suite Insights (owner only).

  collector  127.0.0.1:8300  public through Tailscale Funnel:  POST /v1/event  (also /ultrasuite/v1/event)
             accepts {type: install|uninstall|feedback|heartbeat, version, os, reason, message, id}
             small payloads only, validated, rate limited, no IPs stored
  dashboard  127.0.0.1:8301  private: the owner dashboard, /api/stats (events + GitHub numbers),
             /api/export.csv (events as a spreadsheet) and /api/delete (remove test events)

Data: %LOCALAPPDATA%\\UltraSuite\\data\\insights.db
GitHub: public numbers need nothing; views/clones/referrers need GITHUB_TOKEN (repo read) in settings.env.
Standalone run (no core): python insights/insights.py  (ports from INSIGHTS_COLLECTOR_PORT / INSIGHTS_DASHBOARD_PORT)
"""
import asyncio
import contextlib
import csv
import datetime as dt
import io
import json
import os
import re
import sqlite3
import threading
import time
from collections import deque
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("ULTRASUITE_DATA") or os.path.join(HERE, "data")
os.makedirs(DATA, exist_ok=True)
DB = os.path.join(DATA, "insights.db")
REPO = os.environ.get("INSIGHTS_REPO", "jeemwla/ultra-productivity-suite")
TYPES = {"install", "uninstall", "feedback", "heartbeat"}
CHART_TYPES = ("install", "uninstall", "feedback")
RANGES = {"day": 1, "week": 7, "month": 30, "year": 365}
MAX_BODY = 4096
MAX_DELETE_IDS = 500

# ----------------------------------------------------------------------------- storage
_schema_lock = threading.Lock()
_schema_ready = set()


def _ensure_schema(path):
    """Create the table once per database file. WAL lets the dashboard read while the collector writes."""
    with _schema_lock:
        if path in _schema_ready:
            return
        c = sqlite3.connect(path, timeout=5)
        try:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("""CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, ts REAL, type TEXT, version TEXT, os TEXT,
                         reason TEXT, message TEXT, anon TEXT)""")
            c.execute("CREATE INDEX IF NOT EXISTS ev_ts ON events(ts)")
            c.execute("CREATE INDEX IF NOT EXISTS ev_anon ON events(anon)")
            c.commit()
        finally:
            c.close()
        _schema_ready.add(path)


@contextlib.contextmanager
def db():
    """One short-lived connection: committed on success, rolled back on error, always closed."""
    _ensure_schema(DB)
    c = sqlite3.connect(DB, timeout=5)
    try:
        c.execute("PRAGMA busy_timeout=5000")
        yield c
        c.commit()
    except BaseException:
        c.rollback()
        raise
    finally:
        c.close()


# ----------------------------------------------------------------------------- public collector
collector = FastAPI(title="Ultra Suite collector", docs_url=None, redoc_url=None, openapi_url=None)


class RateLimiter:
    """Sliding-window counter per key. Idle keys are pruned, and the number of tracked keys is capped,
    so random ids sent by a stranger can't grow memory without bound."""

    def __init__(self, max_keys=20000):
        self.max_keys = max_keys
        self.hits = {}
        self.lock = threading.Lock()

    def limited(self, key, limit, window, now=None) -> bool:
        now = time.time() if now is None else now
        with self.lock:
            q = self.hits.get(key)
            if q is None:
                if len(self.hits) >= self.max_keys:
                    self.prune(window, now)
                if len(self.hits) >= self.max_keys:
                    return True  # still full of active keys: refuse instead of growing
                q = self.hits[key] = deque()
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= limit:
                return True
            q.append(now)
            return False

    def prune(self, window, now):
        for k in [k for k, q in self.hits.items() if not q or now - q[-1] > window]:
            del self.hits[k]


_rates = RateLimiter()


def _limited(key: str, limit=30, window=3600) -> bool:
    return _rates.limited(key, limit, window)


_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_CONTROL_KEEP_LINES = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean(v, n, multiline=False):
    """Text as stored: control characters removed (a message keeps its line breaks and tabs), then cut to n."""
    if v is None:
        return None
    text = (_CONTROL_KEEP_LINES if multiline else _CONTROL).sub("", str(v))
    return text.strip()[:n]


def client_key(request: Request) -> str:
    """The sender's address for rate limiting only (never stored). Uvicorn already resolves X-Forwarded-For from
    the local Funnel proxy into request.client, so a value a sender writes into that header can't pick the key."""
    return request.client.host if request.client else "?"


async def read_capped(request: Request, cap: int = MAX_BODY) -> bytes:
    """Read at most cap bytes. A larger body is refused as soon as it passes the cap, not after it is all in memory."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > cap:
        raise HTTPException(413, "too large")
    buf = bytearray()
    try:
        async for chunk in request.stream():
            buf += chunk
            if len(buf) > cap:
                raise HTTPException(413, "too large")
    except ClientDisconnect:
        raise HTTPException(400, "incomplete body")
    return bytes(buf)


def _insert_event(row):
    with db() as c:
        c.execute("INSERT INTO events(ts,type,version,os,reason,message,anon) VALUES(?,?,?,?,?,?,?)", row)


@collector.post("/v1/event")
@collector.post("/ultrasuite/v1/event")
async def event(request: Request):
    raw = await read_capped(request)
    try:
        e = json.loads(raw or b"{}")
    except Exception:
        raise HTTPException(400, "bad json")
    if not isinstance(e, dict):
        raise HTTPException(400, "expected a JSON object")
    kind = e.get("type")
    if kind not in TYPES:
        raise HTTPException(400, "bad type")
    anon = _clean(e.get("id"), 64) or "unknown"
    # rate limit by anonymous id and by the sender's address (address is used in memory only, never stored)
    if _limited(anon) or _limited("addr:" + client_key(request), 120):
        raise HTTPException(429, "slow down")
    row = (time.time(), kind, _clean(e.get("version"), 20), _clean(e.get("os"), 20),
           _clean(e.get("reason"), 40), _clean(e.get("message"), 1000, multiline=True), anon)
    await run_in_threadpool(_insert_event, row)
    return {"ok": True}


@collector.get("/v1/health")
@collector.get("/ultrasuite/v1/health")
async def health():
    return {"ok": True}


# ----------------------------------------------------------------------------- owner dashboard
dashboard = FastAPI(title="Ultra Suite Insights", docs_url=None, redoc_url=None, openapi_url=None)
GITHUB_TTL = 600          # good numbers are reused for 10 minutes
GITHUB_ERROR_TTL = 60     # a failed fetch is retried after a minute, not ten
GITHUB_RELEASE_PAGES = 5  # up to 500 releases are counted for the downloads total
_gh = {"at": 0, "data": None}
_gh_lock = None

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
}


@dashboard.middleware("http")
async def _security_headers(request, call_next):
    """The dashboard can't be framed by another page, and its data responses are never cached by the browser."""
    response = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


def github_problem(status: int, headers) -> str:
    """Plain words for a GitHub API failure, shown under the GitHub card."""
    if status in (403, 429) and (headers.get("x-ratelimit-remaining") == "0" or status == 429):
        reset = headers.get("x-ratelimit-reset", "")
        when = ""
        if reset.isdigit():
            when = " after " + dt.datetime.fromtimestamp(int(reset)).strftime("%H:%M").lstrip("0")
        return f"GitHub's hourly request limit was reached. Numbers refresh{when or ' later'}."
    if status == 401:
        return "GitHub rejected GITHUB_TOKEN. Replace it in settings.env, or remove it to use public numbers."
    if status == 404:
        return f"GitHub can't find {REPO}. Check INSIGHTS_REPO, or the token's access to a private repo."
    return f"GitHub answered {status}. Numbers refresh in a minute."


async def _fetch_github(token: str):
    h = {"Accept": "application/vnd.github+json", "User-Agent": "ultra-suite-insights", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    out = {"repo": REPO, "ok": False, "traffic": None}
    async with httpx.AsyncClient(timeout=10, headers=h) as cl:
        r = await cl.get(f"https://api.github.com/repos/{REPO}")
        if r.status_code != 200:
            out["error"] = github_problem(r.status_code, r.headers)
            return out
        repo = r.json()
        releases = []
        for page in range(1, GITHUB_RELEASE_PAGES + 1):
            rel = await cl.get(f"https://api.github.com/repos/{REPO}/releases", params={"per_page": 100, "page": page})
            if rel.status_code != 200:
                out["error"] = github_problem(rel.status_code, rel.headers)
                return out
            batch = [x for x in rel.json() if isinstance(x, dict)]
            releases += batch
            if len(batch) < 100:
                break
        out.update(ok=True, stars=repo.get("stargazers_count"), forks=repo.get("forks_count"),
                   watchers=repo.get("subscribers_count"), open_issues=repo.get("open_issues_count"),
                   releases=[{"tag": x.get("tag_name"), "published": x.get("published_at"),
                              "downloads": sum(a.get("download_count", 0) for a in x.get("assets", []) if isinstance(a, dict))}
                             for x in releases])
        out["downloads_total"] = sum(x["downloads"] for x in out["releases"])
        if token:
            views, clones, refs = [await cl.get(f"https://api.github.com/repos/{REPO}/traffic/{p}")
                                   for p in ("views", "clones", "popular/referrers")]
            if all(x.status_code == 200 for x in (views, clones, refs)):
                v, c, rf = views.json(), clones.json(), refs.json()
                out["traffic"] = {"views": v.get("count"), "visitors": v.get("uniques"), "clones": c.get("count"),
                                  "cloners": c.get("uniques"), "daily": v.get("views", []),
                                  "referrers": rf if isinstance(rf, list) else []}
            else:
                bad = next(x for x in (views, clones, refs) if x.status_code != 200)
                out["traffic_error"] = ("GITHUB_TOKEN can't read traffic. It needs push access to the repo."
                                        if bad.status_code in (403, 404) else github_problem(bad.status_code, bad.headers))
    return out


async def github_stats():
    global _gh_lock
    if _gh_lock is None:
        _gh_lock = asyncio.Lock()
    async with _gh_lock:  # several open dashboards share one fetch instead of each calling GitHub
        data = _gh["data"]
        ttl = GITHUB_TTL if data and data.get("ok") else GITHUB_ERROR_TTL
        if data and time.time() - _gh["at"] < ttl:
            return data
        token = os.environ.get("GITHUB_TOKEN", "").strip()
        try:
            out = await _fetch_github(token)
        except Exception as exc:
            out = {"repo": REPO, "ok": False, "traffic": None,
                   "error": f"Couldn't reach GitHub ({exc.__class__.__name__}). Numbers refresh in a minute."}
        if not out.get("ok") and data and data.get("ok"):
            out = {**data, "stale": True, "error": out.get("error")}  # keep the last good numbers on screen
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


def normalize_range(value) -> str:
    return value if value in RANGES else "month"


def summarize(range_name: str, now: Optional[float] = None) -> dict:
    """Everything the dashboard shows except GitHub. Heartbeats are counted in SQL instead of loaded row by row,
    so a year of daily check-ins from many PCs stays cheap."""
    days = RANGES[range_name]
    now = time.time() if now is None else now
    since = now - days * 86400
    with db() as c:
        counts = dict(c.execute("SELECT type, COUNT(*) FROM events WHERE ts>=? GROUP BY type", (since,)).fetchall())
        alltime = dict(c.execute("SELECT type, COUNT(*) FROM events GROUP BY type").fetchall())
        active_ids = c.execute("SELECT COUNT(DISTINCT anon) FROM events WHERE ts>=? AND type IN ('heartbeat','install')",
                               (now - 7 * 86400,)).fetchone()[0]
        chart_rows = c.execute("SELECT ts,type FROM events WHERE ts>=? AND type IN ('install','uninstall','feedback')",
                               (since,)).fetchall()
        reasons = c.execute("SELECT COALESCE(NULLIF(reason,''),'no answer'), COUNT(*) FROM events "
                            "WHERE ts>=? AND type='uninstall' GROUP BY 1 ORDER BY 2 DESC, 1", (since,)).fetchall()
        versions = dict(c.execute("SELECT version, COUNT(*) FROM events WHERE ts>=? AND type='install' "
                                  "AND COALESCE(version,'')<>'' GROUP BY version", (since,)).fetchall())
        feedback = c.execute("SELECT id,ts,type,reason,message,version FROM events WHERE ts>=? "
                             "AND type IN ('feedback','uninstall') AND COALESCE(message,'')<>'' "
                             "ORDER BY ts DESC LIMIT 100", (since,)).fetchall()
        recent = c.execute("SELECT id,ts,type,version,reason FROM events WHERE ts>=? ORDER BY ts DESC LIMIT 40",
                           (since,)).fetchall()
    bucket = "%Y-%m-%d %H:00" if days == 1 else "%Y-%m-%d"
    series = {}
    for ts, kind in chart_rows:
        key = dt.datetime.fromtimestamp(ts).strftime(bucket)
        slot = series.setdefault(key, dict.fromkeys(CHART_TYPES, 0))
        slot[kind] += 1
    pts = [{"t": k, **series.get(k, dict.fromkeys(CHART_TYPES, 0))} for k in bucket_keys(now, days)]
    return {
        "range": range_name, "counts": counts, "alltime": alltime, "active_7d": active_ids,
        "net_installs": alltime.get("install", 0) - alltime.get("uninstall", 0),
        "series": pts, "reasons": [list(r) for r in reasons], "versions": versions,
        "feedback": [{"id": i, "ts": ts, "type": k, "reason": r, "message": m, "version": v} for i, ts, k, r, m, v in feedback],
        "recent": [{"id": i, "ts": ts, "type": k, "version": v, "reason": r} for i, ts, k, v, r in recent],
    }


@dashboard.get("/api/stats")
async def stats(range: str = "month"):
    data = await run_in_threadpool(summarize, normalize_range(range))
    data["github"] = await github_stats()
    data["collector"] = os.environ.get("ULTRASUITE_COLLECTOR_PUBLIC", "")
    return JSONResponse(data)


def _csv_cell(v):
    """Spreadsheet-safe text: a value that starts like a formula is shown as text, never run."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


@dashboard.get("/api/export.csv")
def export_csv(range: str = "year"):
    """The selected period's events (newest first) as a CSV file for a spreadsheet."""
    name = normalize_range(range)
    since = time.time() - RANGES[name] * 86400
    with db() as c:
        rows = c.execute("SELECT id,ts,type,version,os,reason,message,anon FROM events WHERE ts>=? ORDER BY ts DESC",
                         (since,)).fetchall()
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow(["id", "time", "type", "version", "os", "reason", "message", "anonymous_id"])
    for i, ts, kind, ver, osn, reason, msg, anon in rows:
        w.writerow([i, dt.datetime.fromtimestamp(ts).isoformat(timespec="seconds"), *map(_csv_cell, (kind, ver, osn, reason, msg, anon))])
    stamp = dt.date.today().isoformat()
    return Response("﻿" + out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="ultra-suite-insights-{name}-{stamp}.csv"'})


def parse_delete(body) -> tuple:
    """(anon, ids) from a delete request, or HTTPException 400 with the reason."""
    if not isinstance(body, dict):
        raise HTTPException(400, "Send a JSON object with anon or ids.")
    anon = body.get("anon")
    ids = body.get("ids") or []
    if anon is not None and (not isinstance(anon, str) or not anon.strip()):
        raise HTTPException(400, "anon must be a non-empty text id.")
    if not isinstance(ids, list):
        raise HTTPException(400, "ids must be a list of event numbers.")
    if len(ids) > MAX_DELETE_IDS:
        raise HTTPException(400, f"Delete at most {MAX_DELETE_IDS} events at a time.")
    clean = []
    for i in ids:
        if isinstance(i, bool) or not (isinstance(i, int) or (isinstance(i, str) and i.isdigit())):
            raise HTTPException(400, "ids must be whole event numbers.")
        clean.append(int(i))
    if not anon and not clean:
        raise HTTPException(400, "Nothing to delete: send anon or ids.")
    return (anon.strip()[:64] if anon else None), clean


@dashboard.post("/api/delete")
def delete_events(body: dict):
    """Owner-only cleanup: delete events by anonymous id (e.g. your own test events) or by row ids."""
    anon, ids = parse_delete(body)
    with db() as c:
        n = 0
        if anon:
            n += c.execute("DELETE FROM events WHERE anon=?", (anon,)).rowcount
        if ids:
            n += c.execute(f"DELETE FROM events WHERE id IN ({','.join('?' * len(ids))})", ids).rowcount
    return {"ok": True, "deleted": n}


try:
    import sys as _s2
    _s2.path.insert(0, os.path.join(HERE, "..", "core"))
    from devtools import add_shared_routes as _asr
    _asr(dashboard, HERE)
except Exception as _exc2:
    print(f"[insights] shared routes disabled: {_exc2}")


def _port(name, default):
    value = os.environ.get(name, "")
    return int(value) if value.isdigit() and 1 <= int(value) <= 65535 else default


async def serve():
    import uvicorn
    servers = [uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", log_config=None,
                                             server_header=False, timeout_graceful_shutdown=5))
               for app, port in ((collector, _port("INSIGHTS_COLLECTOR_PORT", 8300)),
                                 (dashboard, _port("INSIGHTS_DASHBOARD_PORT", 8301)))]
    await asyncio.gather(*(s.serve() for s in servers))


if __name__ == "__main__":
    asyncio.run(serve())
