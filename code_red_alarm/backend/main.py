#!/usr/bin/env python3
'''
Code Red Alarm: the alarm engine and API (works with any speaker or headphones).
Supports desktop QHD UI and remote phone control over Tailscale.
'''
import asyncio
import contextlib
import datetime
import json
import math
import os
import random
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from typing import List, NoReturn, Optional

import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .clock import CATCH_UP, WEEKDAYS, due_day, local_now, missed_occurrence, next_ring, roll_forward, snoozed_until

DB_PATH = os.path.join(os.environ.get("ULTRASUITE_DATA") or os.path.join(os.path.dirname(__file__), "..", "data"), "alarms.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
HOST_AUDIO_BRIDGE_URL = os.getenv("HOST_AUDIO_BRIDGE_URL", "http://127.0.0.1:8091")
BRIDGE_HEADERS = {"X-Bridge-Token": os.getenv("BRIDGE_TOKEN", "")}


def init_db():
    with contextlib.closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS alarms (
                id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                time_hhmm TEXT NOT NULL,
                target_date TEXT,
                weekdays TEXT NOT NULL,
                recurring INTEGER NOT NULL DEFAULT 0,
                enabled INTEGER NOT NULL DEFAULT 1,
                code_red INTEGER NOT NULL DEFAULT 0,
                custom_phrase TEXT,
                volume_percent INTEGER NOT NULL DEFAULT 100,
                ring_duration_sec INTEGER NOT NULL DEFAULT 600,
                wake_up_check_min INTEGER NOT NULL DEFAULT 5,
                math_mission_difficulty INTEGER NOT NULL DEFAULT 0,
                snooze_limit INTEGER NOT NULL DEFAULT 0,
                snoozes_used INTEGER NOT NULL DEFAULT 0,
                last_triggered_date TEXT
            )
        ''')
        # Snooze arrived later: add its columns to databases made by earlier versions. Nothing else changes.
        have = {row[1] for row in conn.execute("PRAGMA table_info(alarms)")}
        if "snooze_minutes" not in have:
            conn.execute("ALTER TABLE alarms ADD COLUMN snooze_minutes INTEGER NOT NULL DEFAULT 9")
        if "snoozed_until" not in have:
            conn.execute("ALTER TABLE alarms ADD COLUMN snoozed_until TEXT")
        conn.execute("CREATE TABLE IF NOT EXISTS alarm_events (id INTEGER PRIMARY KEY, ts TEXT, alarm_id TEXT, label TEXT, kind TEXT, detail TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS engine_meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute('''CREATE TABLE IF NOT EXISTS countdown (
            id INTEGER PRIMARY KEY CHECK (id = 1), run_id TEXT NOT NULL,
            label TEXT NOT NULL, state TEXT NOT NULL, duration_sec INTEGER NOT NULL,
            remaining_sec REAL NOT NULL, deadline REAL, revision INTEGER NOT NULL)''')
        conn.execute("INSERT OR IGNORE INTO countdown VALUES (1, '', 'Timer', 'idle', 300, 300, NULL, 0)")
        conn.execute('''CREATE TABLE IF NOT EXISTS timers (
            id TEXT PRIMARY KEY, label TEXT NOT NULL, state TEXT NOT NULL, duration_sec INTEGER NOT NULL,
            remaining_sec REAL NOT NULL, deadline REAL, created_at REAL NOT NULL,
            code_red INTEGER NOT NULL DEFAULT 0, custom_phrase TEXT NOT NULL DEFAULT '',
            ring_duration_sec INTEGER NOT NULL DEFAULT 60)''')
        conn.execute("CREATE TABLE IF NOT EXISTS timer_meta (id INTEGER PRIMARY KEY CHECK (id = 1), revision INTEGER NOT NULL)")
        conn.execute("INSERT OR IGNORE INTO timer_meta VALUES (1, 0)")
        # Earlier versions kept one countdown; carry a running or paused one over once, then leave that row unused.
        old = conn.execute("SELECT run_id, label, state, duration_sec, remaining_sec, deadline FROM countdown WHERE id = 1").fetchone()
        if old and old[0] and old[2] in ("running", "paused"):
            conn.execute("INSERT OR IGNORE INTO timers (id, label, state, duration_sec, remaining_sec, deadline, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (old[0], old[1], old[2], old[3], old[4], old[5], time.time()))
            conn.execute("UPDATE countdown SET state = 'migrated', deadline = NULL WHERE id = 1")
            conn.execute("UPDATE timer_meta SET revision = revision + 1 WHERE id = 1")
        conn.commit()


class AlarmCreate(BaseModel):
    label: str = Field(..., examples=["6:30 AM Standup & Cold Plunge"])
    time_hhmm: str = Field(..., pattern=r"^\d{2}:\d{2}$", examples=["06:30"])
    target_date: Optional[str] = Field(None, examples=["2026-11-11"])
    weekdays: List[str] = Field(default_factory=list)
    recurring: bool = False
    enabled: bool = True
    code_red: bool = False
    custom_phrase: str = ""
    volume_percent: int = 100
    ring_duration_sec: int = 600
    wake_up_check_min: int = 5
    math_mission_difficulty: int = 0
    snooze_limit: int = 0  # 0 keeps snooze off, as before; otherwise how many snoozes each ring allows
    snooze_minutes: int = 9


class DismissRequest(BaseModel):
    alarm_id: str
    typed_phrase: str = ""
    math_answer: Optional[int] = None


class SnoozeRequest(BaseModel):
    alarm_id: str


def normalize_phrase(text) -> str:
    """Compare Code Red phrases the way people type them: phone keyboards swap in curly quotes and dashes, and a
    double space is easy to miss. Letters, digits and case still have to match exactly."""
    table = {"‘": "'", "’": "'", "‚": "'", "′": "'", "“": '"', "”": '"', "„": '"',
             "–": "-", "—": "-", "−": "-", " ": " "}
    return " ".join("".join(table.get(c, c) for c in str(text or "")).split())


MAX_TIMERS = 20


class TimerCreate(BaseModel):
    duration_sec: int = Field(..., ge=1, le=86400, strict=True)
    label: str = Field("Timer", max_length=80)
    start: bool = True
    code_red: bool = False
    custom_phrase: str = Field("", max_length=120)
    ring_duration_sec: int = Field(60, ge=10, le=600, strict=True)


class TimerAction(BaseModel):
    seconds: int = Field(60, ge=1, le=3600, strict=True)


def alarm_delivery_enabled():
    """Core gates Dev explicitly; an absent flag preserves Public's existing behavior."""
    return not globals().get("ULTRASUITE_DELIVERY_GATED", False) and os.getenv("ULTRASUITE_ALARM_DELIVERY", "1") == "1"


def _timer_payload(row, now):
    """One timer as the page sees it. The Code Red phrase stays on the server until the timer rings."""
    result = dict(row)
    result["code_red"] = bool(result["code_red"])
    result.pop("custom_phrase", None)
    result["remaining_sec"] = max(0.0, row["deadline"] - now) if row["state"] == "running" else row["remaining_sec"]
    return result


def _timers_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _bump(conn):
    conn.execute("UPDATE timer_meta SET revision = revision + 1 WHERE id = 1")


def timer_snapshot():
    """Every timer plus the delivery metadata the page needs before it lets anything ring."""
    now = time.time()
    with contextlib.closing(_timers_db()) as conn:
        # Keep rows and their revision in one read snapshot, even if another request changes a timer.
        conn.execute("BEGIN")
        rows = conn.execute("SELECT * FROM timers ORDER BY created_at, rowid").fetchall()
        revision = conn.execute("SELECT revision FROM timer_meta WHERE id = 1").fetchone()[0]
    return {"timers": [_timer_payload(r, now) for r in rows], "revision": revision, "sampled_at": now,
            "edition": os.getenv("ULTRASUITE_EDITION", "Public"), "delivery_enabled": alarm_delivery_enabled(),
            "max_timers": MAX_TIMERS}


def timer_summary(snapshot=None):
    """Small status for the Ultra Suite hub and the Dock: how many timers run and which finishes first."""
    timers = (snapshot or timer_snapshot())["timers"]
    running = [t for t in timers if t["state"] == "running"]
    first = min(running, key=lambda t: t["remaining_sec"], default=None)
    return {"count": len(timers), "running": len(running), "paused": sum(t["state"] == "paused" for t in timers),
            "next_in_sec": math.ceil(first["remaining_sec"]) if first else None,
            "next_label": first["label"] if first else None}


def claim_due_timer():
    """Durably finish the first due timer before audio. An alarm already ringing takes priority (the scheduler only
    calls this while idle). Timers that finished longer ago than the alarm catch-up window become expired silently."""
    if not alarm_delivery_enabled():
        return None
    with contextlib.closing(_timers_db()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        now = time.time()
        due = conn.execute("SELECT * FROM timers WHERE state = 'running' AND deadline <= ? ORDER BY deadline, created_at, rowid", (now,)).fetchall()
        claimed = None
        for row in due:
            expired = now - row["deadline"] > CATCH_UP.total_seconds()
            if not expired and claimed is not None:
                continue  # rings after the current one is stopped
            conn.execute("UPDATE timers SET state = ?, remaining_sec = 0, deadline = NULL WHERE id = ?",
                         ("expired" if expired else "finished", row["id"]))
            if not expired:
                claimed = row
        if due:
            _bump(conn)
        conn.commit()
    if claimed is None:
        return None
    return {"id": claimed["id"], "timer_id": claimed["id"], "label": claimed["label"] + " finished", "is_timer": True,
            "code_red": bool(claimed["code_red"]), "custom_phrase": claimed["custom_phrase"],
            "math_mission_difficulty": 0, "ring_duration_sec": int(claimed["ring_duration_sec"])}


class ActiveRingingState:
    def __init__(self):
        self.is_ringing: bool = False
        self.alarm: Optional[dict] = None
        self.started_at: Optional[datetime.datetime] = None
        self.expires_at: Optional[datetime.datetime] = None
        self.math_question: Optional[str] = None
        self.math_solution: Optional[int] = None
        self.audio: Optional[str] = None  # starting / started / failed


ring_state = ActiveRingingState()
ws_clients: List[WebSocket] = []


async def broadcast_state():
    now = local_now()
    remaining_sec = 0
    if ring_state.is_ringing and ring_state.expires_at:
        remaining_sec = max(0, int((ring_state.expires_at - now).total_seconds()))
        if remaining_sec == 0:
            log_event(ring_state.alarm, "expired", "rang for its full length without being stopped")
            # Someone who slept through the whole ring gets the next snooze automatically, if the alarm has one left.
            auto = snooze_alarm(ring_state.alarm, now)
            if isinstance(auto, dict):
                log_event(ring_state.alarm, "snoozed", f"{auto['minutes']} min, until {auto['until']:%H:%M} "
                                                       f"({auto['used']} of {auto['limit']}), automatically after ringing out")
            ring_state.is_ringing = False
            ring_state.alarm = None
            ring_state.math_question = None
            ring_state.math_solution = None

    payload = {
        "type": "RING_STATE",
        "timers": timer_snapshot(),
        "is_ringing": ring_state.is_ringing,
        "alarm": ring_state.alarm,
        "remaining_sec": remaining_sec,
        "math_question": ring_state.math_question,
        "audio": getattr(ring_state, "audio", None) if ring_state.is_ringing else None,  # started / failed
        "server_time": now.strftime("%H:%M:%S"),
        "server_date": now.strftime("%Y-%m-%d (%a)"),
        "server_iso": now.strftime("%Y-%m-%dT%H:%M:%S"),  # the clock alarms ring by; the page compares it with its own
    }
    stale = []
    for ws in ws_clients:
        try:
            await ws.send_json(payload)
        except Exception:
            stale.append(ws)
    for ws in stale:
        if ws in ws_clients:
            ws_clients.remove(ws)


def log_event(alarm: Optional[dict], kind: str, detail: str = ""):
    """Ring history: due, audio_started, audio_failed, dismissed, expired (preview_* for previews).
    'Rang' in the app means the sound actually started, not merely that the scheduler picked the alarm."""
    alarm = alarm or {}
    if alarm.get("is_test"):
        kind = "preview_" + kind
    try:
        with contextlib.closing(sqlite3.connect(DB_PATH)) as conn:
            conn.execute("INSERT INTO alarm_events (ts, alarm_id, label, kind, detail) VALUES (?, ?, ?, ?, ?)",
                         (local_now().strftime("%Y-%m-%dT%H:%M:%S"), alarm.get("id"), alarm.get("label"), kind, detail[:300]))
            conn.commit()
    except Exception as exc:
        print(f"[AlarmEngine] history write failed: {exc}")


async def _ring_helper(alarm_dict: dict, duration: int):
    """Ask the speaker helper to ring; returns (started, detail). Never raises."""
    if not alarm_delivery_enabled():
        return False, "Sound delivery is disabled in this edition."
    try:
        async with httpx.AsyncClient(timeout=2.5, headers=BRIDGE_HEADERS) as client:
            r = await client.post(f"{HOST_AUDIO_BRIDGE_URL}/trigger", json={
                "alarm_id": alarm_dict["id"], "duration_sec": duration, "code_red": bool(alarm_dict["code_red"])})
        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code == 200 and data.get("started"):
            return True, str(data.get("output") or data.get("device") or "chosen output")
        return False, f"helper answered {r.status_code}: {data.get('error') or 'did not start (already ringing?)'}"
    except Exception as exc:
        return False, f"speaker helper unreachable: {exc.__class__.__name__}"


async def trigger_alarm_now(alarm_dict: dict):
    if not alarm_delivery_enabled():
        return
    now = local_now()
    ring_state.is_ringing = True
    ring_state.alarm = alarm_dict
    ring_state.started_at = now
    ring_state.audio = "starting"
    duration = int(alarm_dict.get("ring_duration_sec", 600))
    ring_state.expires_at = now + datetime.timedelta(seconds=duration)

    # Generate optional math challenge if configured
    diff = int(alarm_dict.get("math_mission_difficulty", 0))
    if diff > 0:
        a = random.randint(12 * diff, 49 * diff)
        b = random.randint(11 * diff, 39 * diff)
        c = random.randint(5, 25)
        ring_state.math_question = f"({a} + {b}) - {c}"
        ring_state.math_solution = (a + b) - c
    else:
        ring_state.math_question = None
        ring_state.math_solution = None

    # Tell the speaker helper to ring on the chosen output (forced to 100% and unmuted while ringing), and check
    # that it really started. If not (busy with a preview, briefly unreachable), stop it and try once more.
    started, detail = await _ring_helper(alarm_dict, duration)
    if not started and not alarm_dict.get("is_test"):
        try:
            async with httpx.AsyncClient(timeout=2.5, headers=BRIDGE_HEADERS) as client:
                await client.post(f"{HOST_AUDIO_BRIDGE_URL}/stop", json={})
        except Exception:
            pass
        await asyncio.sleep(1.0)
        started, detail = await _ring_helper(alarm_dict, duration)
    ring_state.audio = "started" if started else "failed"
    log_event(alarm_dict, "audio_started" if started else "audio_failed", detail)
    if not started:
        print(f"[AlarmEngine] speaker helper did not start ({detail}); open alarm windows still ring")

    await broadcast_state()


HEARTBEAT_EVERY = 30  # seconds between saved "engine was running" marks
GAP = datetime.timedelta(seconds=90)  # a longer pause between ticks means the PC slept or the engine stalled


def _alarm_dict(row) -> dict:
    alm = dict(row)
    alm["weekdays"] = json.loads(alm["weekdays"])
    return alm


def read_heartbeat():
    """The last moment the engine saved as running, or None on a fresh install."""
    try:
        with contextlib.closing(sqlite3.connect(DB_PATH)) as conn:
            row = conn.execute("SELECT value FROM engine_meta WHERE key = 'heartbeat'").fetchone()
        return datetime.datetime.fromisoformat(row[0]) if row else None
    except (sqlite3.Error, ValueError):
        return None


def write_heartbeat(now: datetime.datetime):
    try:
        with contextlib.closing(sqlite3.connect(DB_PATH)) as conn:
            conn.execute("INSERT OR REPLACE INTO engine_meta (key, value) VALUES ('heartbeat', ?)", (now.isoformat(timespec="seconds"),))
            conn.commit()
    except sqlite3.Error as exc:
        print(f"[SchedulerLoop] heartbeat write failed: {exc}")


def record_missed(since: datetime.datetime, now: datetime.datetime, why: str):
    """Log every alarm whose time fell in a stretch the engine could not cover and that is now too late to catch up
    on (more than CATCH_UP ago). Each stretch is checked once, so a restart never logs the same miss twice."""
    until = now - CATCH_UP
    if until <= since:
        return []
    found = []
    with contextlib.closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        for row in conn.execute("SELECT * FROM alarms WHERE enabled = 1").fetchall():
            alm = _alarm_dict(row)
            hit = missed_occurrence(alm, since, until)
            if hit:
                found.append(alm["id"])
                log_event(alm, "missed", f"{hit[0]} {alm['time_hhmm']}: {why}")
    return found


def claim_due_snooze(now: datetime.datetime):
    """A snoozed alarm whose snooze is over, cleared durably before it rings again. A snooze that ended longer ago
    than CATCH_UP (PC off) is logged as missed instead of ringing late."""
    with contextlib.closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute("SELECT * FROM alarms WHERE snoozed_until IS NOT NULL ORDER BY snoozed_until").fetchall()
        claimed, stale = None, []
        for row in rows:
            alm = _alarm_dict(row)
            at = snoozed_until(alm)
            if at is not None and at > now:
                continue
            conn.execute("UPDATE alarms SET snoozed_until = NULL WHERE id = ?", (alm["id"],))
            if at is None or now - at > CATCH_UP:
                stale.append(alm)
                continue
            claimed = alm
            break
        conn.commit()
    for alm in stale:  # history goes through its own connection, so only after this write lock is released
        log_event(alm, "missed", "the snooze ended while the alarm engine wasn't running")
    return claimed


async def scheduler_loop():
    prev = local_now()
    startup_catch_up = True
    print(f"[SchedulerLoop] started, local time {prev:%Y-%m-%d %H:%M:%S}")
    beat = read_heartbeat()
    if beat and beat < prev and alarm_delivery_enabled():
        record_missed(beat, prev, "the alarm engine wasn't running (PC off, asleep or the app closed)")
    write_heartbeat(prev)
    last_beat = time.monotonic()
    covered = prev  # misses before this moment were already looked for
    while True:
        try:
            now = local_now()
            if now < prev:  # clock moved back (DST, manual change): start over from here
                if prev - now > datetime.timedelta(minutes=2):
                    log_event(None, "clock_changed", f"the PC clock moved back from {prev:%H:%M} to {now:%H:%M}")
                prev = covered = now
                startup_catch_up = False
            elif now - prev > GAP and not ring_state.is_ringing and alarm_delivery_enabled():
                since = max(prev, covered)
                record_missed(since, now, f"the engine couldn't ring from {since:%H:%M} to {now:%H:%M} (PC asleep, busy or another alarm ringing)")
                covered = max(covered, now - CATCH_UP)
            if time.monotonic() - last_beat >= HEARTBEAT_EVERY:
                write_heartbeat(now)
                last_beat = time.monotonic()
            if alarm_delivery_enabled() and not ring_state.is_ringing:
                snoozed = claim_due_snooze(now)
                if snoozed:
                    print(f"[SchedulerLoop] snooze over for {snoozed['id']} at {now:%H:%M:%S}")
                    log_event(snoozed, "due", f"snooze {snoozed['snoozes_used']} ended, engine time {now:%H:%M:%S}")
                    await trigger_alarm_now(snoozed)
            if alarm_delivery_enabled() and not ring_state.is_ringing:
                with contextlib.closing(sqlite3.connect(DB_PATH)) as conn:
                    conn.row_factory = sqlite3.Row
                    rows = conn.execute("SELECT * FROM alarms WHERE enabled = 1").fetchall()
                    for row in rows:
                        alm = _alarm_dict(row)
                        day_iso = due_day(alm, None if startup_catch_up else prev, now)
                        if not day_iso:
                            continue
                        # A fresh occurrence starts with a full set of snoozes and replaces any snooze still pending.
                        conn.execute("UPDATE alarms SET last_triggered_date = ?, snoozes_used = 0, snoozed_until = NULL WHERE id = ?",
                                     (day_iso, alm["id"]))
                        alm["snoozes_used"], alm["snoozed_until"] = 0, None
                        if not alm["recurring"] and alm["target_date"]:
                            conn.execute("UPDATE alarms SET enabled = 0 WHERE id = ?", (alm["id"],))
                        conn.commit()
                        print(f"[SchedulerLoop] ringing {alm['id']} ({alm['time_hhmm']}) at {now:%H:%M:%S}")
                        log_event(alm, "due", f"scheduled {day_iso} {alm['time_hhmm']}, engine time {now:%H:%M:%S}")
                        await trigger_alarm_now(alm)
                        break
                if not ring_state.is_ringing:
                    timer_alarm = claim_due_timer()
                    if timer_alarm:
                        log_event(timer_alarm, "due", "countdown completed")
                        await trigger_alarm_now(timer_alarm)
                if not ring_state.is_ringing:
                    prev = now  # only advance while idle, so an alarm due mid-ring still rings afterwards
                    startup_catch_up = False
            await broadcast_state()
        except Exception as exc:
            print(f"[SchedulerLoop] Error: {exc}")
        await asyncio.sleep(1.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(scheduler_loop())
    yield
    task.cancel()


app = FastAPI(title="Code Red Alarm API", lifespan=lifespan)
# CORS and the local-only guard come from core/devtools.add_shared_routes (suite pages only)


def fetch_all_alarms() -> List[dict]:
    now = local_now()
    with contextlib.closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM alarms ORDER BY time_hhmm ASC").fetchall()
        result = []
        for r in rows:
            item = dict(r)
            item["weekdays"] = json.loads(item["weekdays"])
            item["recurring"] = bool(item["recurring"])
            item["enabled"] = bool(item["enabled"])
            item["code_red"] = bool(item["code_red"])
            upcoming = next_ring(item, now)
            item["next_ring_local"] = upcoming.isoformat() if upcoming else None
            result.append(item)
        return result


@app.get("/")
async def serve_ui():
    frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend", "index.html")
    return FileResponse(frontend_path, headers={"Cache-Control": "no-cache"})


@app.get("/api/ring")
async def ring_status():
    """Tiny status for the desktop app: is anything ringing right now?"""
    return {"is_ringing": ring_state.is_ringing, "alarm": (ring_state.alarm or {}).get("label"),
            "is_timer": bool((ring_state.alarm or {}).get("is_timer")), "timers": timer_summary()}


@app.get("/api/alarms/history")
async def alarm_history(limit: int = 50):
    """Recent ring events, newest first."""
    with contextlib.closing(sqlite3.connect(DB_PATH)) as conn:
        rows = conn.execute("SELECT ts, alarm_id, label, kind, detail FROM alarm_events ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),)).fetchall()
    return {"events": [dict(zip(("ts", "alarm_id", "label", "kind", "detail"), r)) for r in rows]}


@app.get("/api/next")
async def next_alarm():
    """Seconds until the next alarm rings. The desktop app sets a wake timer from this, so a sleeping PC wakes
    up a minute early and the alarm still rings."""
    now = local_now()
    times = [t for t in (next_ring(a, now) for a in fetch_all_alarms()) if t]
    due = timer_summary()["next_in_sec"]
    if due is not None:
        times.append(now + datetime.timedelta(seconds=due))
    if not times:
        return {"in_sec": None}
    at = min(times)
    return {"in_sec": int((at - now).total_seconds()), "at": at.strftime("%Y-%m-%dT%H:%M:%S")}


@app.get("/api/alarms")
async def list_alarms():
    return {"alarms": fetch_all_alarms()}


@app.get("/api/timers")
async def get_timers():
    return timer_snapshot()


def _need_delivery():
    if not alarm_delivery_enabled():
        raise HTTPException(403, "Timers can't ring in this edition.")


@app.post("/api/timers")
async def create_timer(payload: TimerCreate):
    label = payload.label.strip() or "Timer"
    phrase = payload.custom_phrase.strip()
    if payload.code_red and len(phrase) < 4:
        raise HTTPException(400, "Code Red needs a phrase of at least 4 characters.")
    if payload.start:
        _need_delivery()
    now = time.time()
    timer_id = "tmr-" + uuid.uuid4().hex[:12]
    with contextlib.closing(_timers_db()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT COUNT(*) FROM timers").fetchone()[0] >= MAX_TIMERS:
            raise HTTPException(409, f"You can keep up to {MAX_TIMERS} timers. Delete one first.")
        conn.execute("INSERT INTO timers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (timer_id, label, "running" if payload.start else "idle", payload.duration_sec, payload.duration_sec,
                      now + payload.duration_sec if payload.start else None, now, int(payload.code_red),
                      phrase if payload.code_red else "", payload.ring_duration_sec))
        _bump(conn)
        conn.commit()
    snap = timer_snapshot()
    snap["id"] = timer_id
    return snap


async def _stop_ring_for(timer_id: str, why: str):
    """Quiet a ringing timer that the person just restarted, reset or deleted. A Code Red timer keeps ringing: only
    its phrase stops it."""
    active = ring_state.alarm or {}
    if not (ring_state.is_ringing and active.get("timer_id") == timer_id) or active.get("code_red"):
        return
    log_event(active, "dismissed", why)
    await _end_ring()


@app.post("/api/timers/{timer_id}/{action}")
async def control_timer(timer_id: str, action: str, payload: Optional[TimerAction] = None):
    if action not in {"start", "pause", "resume", "reset", "add"}:
        raise HTTPException(404, "Unknown timer action.")
    payload = payload or TimerAction()
    now = time.time()
    with contextlib.closing(_timers_db()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM timers WHERE id = ?", (timer_id,)).fetchone()
        if not row:
            raise HTTPException(404, "That timer is gone. Refresh and try again.")
        state = row["state"]
        remaining = _timer_payload(row, now)["remaining_sec"]
        duration = row["duration_sec"]
        if state == "running" and remaining <= 0 and action != "reset":
            raise HTTPException(409, "This timer is finishing. Try again in a moment.")
        if action == "start" and state in ("idle", "finished", "expired"):
            _need_delivery()
            state, remaining, deadline = "running", duration, now + duration
        elif action == "pause" and state == "running":
            state, deadline = "paused", None
        elif action == "resume" and state == "paused":
            _need_delivery()
            state, deadline = "running", now + remaining
        elif action == "reset":
            state, remaining, deadline = "idle", duration, None
        elif action == "add":
            extra = payload.seconds
            if state in ("finished", "expired"):  # one more minute after it rang
                _need_delivery()
                state, remaining, duration = "running", extra, extra
            else:
                if remaining + extra > 86400:
                    raise HTTPException(400, "A timer can run for up to 24 hours.")
                if state == "running":
                    _need_delivery()
                remaining, duration = remaining + extra, min(86400, duration + extra)
            deadline = now + remaining if state == "running" else None
        else:
            raise HTTPException(409, "The timer can no longer do that. Refresh and try again.")
        conn.execute("UPDATE timers SET state = ?, remaining_sec = ?, deadline = ?, duration_sec = ? WHERE id = ?",
                     (state, remaining, deadline, duration, timer_id))
        _bump(conn)
        conn.commit()
    if action in ("start", "add", "reset"):
        await _stop_ring_for(timer_id, f"timer {action} while ringing")
    return timer_snapshot()


@app.delete("/api/timers/{timer_id}")
async def delete_timer(timer_id: str):
    with contextlib.closing(_timers_db()) as conn:
        cur = conn.execute("DELETE FROM timers WHERE id = ?", (timer_id,))
        if cur.rowcount:
            _bump(conn)
        conn.commit()
    if not cur.rowcount:
        raise HTTPException(404, "That timer is already gone.")
    await _stop_ring_for(timer_id, "timer deleted while ringing")
    return timer_snapshot()


MAX_LABEL, MAX_PHRASE, MIN_PHRASE = 80, 120, 4


def _bad(detail: str) -> NoReturn:
    raise HTTPException(status_code=400, detail=detail)


def _validate(payload: AlarmCreate) -> dict:
    """Check an alarm the way the scheduler will read it and return the clean values to store. Anything the
    scheduler would silently never ring (a misspelled day, an impossible date, a moment already gone) is refused
    with a plain message instead."""
    label = payload.label.strip()
    if not label:
        _bad("Give the alarm a name.")
    if len(label) > MAX_LABEL:
        _bad(f"Keep the name under {MAX_LABEL} characters.")
    hh, mm = (int(x) for x in payload.time_hhmm.split(":"))
    if hh > 23 or mm > 59:
        _bad("That isn't a valid time.")
    if not (60 <= payload.ring_duration_sec <= 3600):
        _bad("Ring duration must be between 1 and 60 minutes.")
    unknown = [d for d in payload.weekdays if d not in WEEKDAYS]
    if unknown:
        _bad(f"Unknown day: {unknown[0]}. Use Mon, Tue, Wed, Thu, Fri, Sat or Sun.")
    weekdays = [d for d in WEEKDAYS if d in payload.weekdays]  # calendar order, no repeats
    target = payload.target_date or None
    if target is not None:
        try:
            day = datetime.date.fromisoformat(target)
        except ValueError:
            day = None
        if day is None or len(target) != 10:
            _bad("That date isn't valid. Use a date like 2026-11-25.")
        if weekdays and WEEKDAYS[day.weekday()] not in weekdays:
            _bad("That date doesn't fall on one of the chosen days.")
        if payload.enabled:
            now = local_now().replace(second=0, microsecond=0)
            if datetime.datetime.combine(day, datetime.time(hh, mm)) < now:
                _bad("That date and time already passed. Pick a later one.")
    if not weekdays and not target:
        _bad("Pick at least one weekday or a specific date.")
    phrase = payload.custom_phrase.strip()
    if len(phrase) > MAX_PHRASE:
        _bad(f"Keep the phrase under {MAX_PHRASE} characters.")
    if payload.code_red and len(normalize_phrase(phrase)) < MIN_PHRASE:
        _bad(f"Code Red needs a phrase of at least {MIN_PHRASE} characters.")
    if not (0 <= payload.volume_percent <= 100):
        _bad("Volume must be between 0 and 100 percent.")
    if not (0 <= payload.wake_up_check_min <= 60):
        _bad("The wake-up check must be between 0 and 60 minutes.")
    if payload.math_mission_difficulty not in (0, 1, 2):
        _bad("Wake-up math is Off, Easy or Hard.")
    if not (0 <= payload.snooze_limit <= 10):
        _bad("An alarm can snooze up to 10 times.")
    if not (1 <= payload.snooze_minutes <= 30):
        _bad("A snooze lasts between 1 and 30 minutes.")
    if payload.code_red and payload.snooze_limit:
        _bad("Code Red alarms can't snooze. Turn snooze off or turn Code Red off.")
    return {"label": label, "time_hhmm": payload.time_hhmm, "target_date": target, "weekdays": json.dumps(weekdays),
            "recurring": int(payload.recurring),
            "enabled": int(payload.enabled), "code_red": int(payload.code_red), "custom_phrase": phrase if payload.code_red else "",
            "volume_percent": payload.volume_percent, "ring_duration_sec": payload.ring_duration_sec,
            "wake_up_check_min": payload.wake_up_check_min, "math_mission_difficulty": payload.math_mission_difficulty,
            "snooze_limit": payload.snooze_limit, "snooze_minutes": payload.snooze_minutes}


@app.put("/api/alarms/{alarm_id}")
async def update_alarm(alarm_id: str, payload: AlarmCreate):
    """Edit an alarm in place. It counts as new, so it can ring again today, and any pending snooze ends."""
    clean = _validate(payload)
    with contextlib.closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.execute(f'''
            UPDATE alarms SET {", ".join(k + " = ?" for k in clean)}, snoozes_used = 0, snoozed_until = NULL,
                last_triggered_date = NULL
            WHERE id = ?
        ''', (*clean.values(), alarm_id))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="That alarm is gone. It may have been deleted in another window.")
    return {"ok": True, "id": alarm_id, "alarms": fetch_all_alarms()}


@app.post("/api/alarms")
async def create_alarm(payload: AlarmCreate):
    alarm_id = f"alm-{uuid.uuid4().hex[:8]}"
    clean = _validate(payload)
    with contextlib.closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(f"INSERT INTO alarms (id, {', '.join(clean)}, snoozes_used, last_triggered_date) "
                     f"VALUES (?, {', '.join('?' for _ in clean)}, 0, NULL)", (alarm_id, *clean.values()))
        conn.commit()
    return {"ok": True, "id": alarm_id, "alarms": fetch_all_alarms()}


@app.patch("/api/alarms/{alarm_id}/toggle")
async def toggle_alarm(alarm_id: str):
    """Turn an alarm on or off. Turning on a one-time alarm whose moment already passed moves it to the next time
    that clock time comes up, so it never sits there switched on and marked missed. Turning off ends a snooze."""
    with contextlib.closing(sqlite3.connect(DB_PATH)) as conn:
        row = conn.execute("SELECT enabled, target_date, time_hhmm, weekdays FROM alarms WHERE id = ?", (alarm_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="That alarm is gone. It may have been deleted in another window.")
        enabled, target, hhmm, weekdays = row
        new_val = 0 if enabled else 1
        if new_val and target and not json.loads(weekdays or "[]"):
            now = local_now()
            hh, mm = (int(x) for x in hhmm.split(":"))
            try:
                passed = datetime.datetime.combine(datetime.date.fromisoformat(target), datetime.time(hh, mm)) <= now
            except ValueError:
                passed = True
            if passed:
                target = roll_forward(hhmm, now)
        conn.execute("UPDATE alarms SET enabled = ?, target_date = ?, last_triggered_date = NULL, snoozes_used = 0, "
                     "snoozed_until = NULL WHERE id = ?", (new_val, target, alarm_id))
        conn.commit()
    return {"ok": True, "alarms": fetch_all_alarms()}


@app.delete("/api/alarms/{alarm_id}")
async def delete_alarm(alarm_id: str):
    with contextlib.closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.execute("DELETE FROM alarms WHERE id = ?", (alarm_id,))
        conn.commit()
    if not cur.rowcount:
        raise HTTPException(status_code=404, detail="That alarm is already gone.")
    return {"ok": True, "alarms": fetch_all_alarms()}


@app.post("/api/alarms/{alarm_id}/test-ring")
async def test_ring_alarm(alarm_id: str, seconds: int = 10):
    """Preview an alarm: rings for a few seconds and can be dismissed with one tap."""
    if not alarm_delivery_enabled():
        raise HTTPException(403, "Sound delivery is disabled in this edition.")
    alarms = fetch_all_alarms()
    target = next((a for a in alarms if a["id"] == alarm_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Alarm not found")
    if ring_state.is_ringing and not (ring_state.alarm or {}).get("is_test"):
        raise HTTPException(status_code=409, detail="An alarm is ringing right now. Stop it first.")
    preview = dict(target)
    preview["is_test"] = True
    preview["ring_duration_sec"] = max(3, min(int(seconds), 30))
    preview["math_mission_difficulty"] = 0
    await trigger_alarm_now(preview)
    return {"ok": True, "message": f"Previewing '{target['label']}' for {preview['ring_duration_sec']} seconds."}


async def _bridge(method: str, path: str, payload: Optional[dict] = None):
    try:
        async with httpx.AsyncClient(timeout=4, headers=BRIDGE_HEADERS) as client:
            r = await (client.get(f"{HOST_AUDIO_BRIDGE_URL}{path}") if method == "GET"
                       else client.post(f"{HOST_AUDIO_BRIDGE_URL}{path}", json=payload or {}))
            return r.status_code, r.json()
    except Exception as exc:
        return 503, {"error": f"Speaker bridge offline: {exc.__class__.__name__}"}


@app.get("/api/audio")
async def audio_info():
    code, devices = await _bridge("GET", "/devices")
    if code != 200:
        return {"bridge_online": False, "devices": [{"name": "default", "label": "System default output"}],
                "selected": "default", "lock_volume": False, "error": devices.get("error")}
    return {"bridge_online": True, **devices}


class AudioOutputPayload(BaseModel):
    output: Optional[str] = None
    lock_volume: Optional[bool] = None


@app.post("/api/audio/output")
async def set_audio_output(payload: AudioOutputPayload):
    body = {k: v for k, v in payload.model_dump().items() if v is not None}
    code, data = await _bridge("POST", "/output", body)
    if code != 200:
        raise HTTPException(status_code=code, detail=data.get("error", "Could not change output"))
    return data


@app.post("/api/audio/test")
async def test_audio_output():
    if not alarm_delivery_enabled():
        raise HTTPException(403, "Sound delivery is disabled in this edition.")
    code, data = await _bridge("POST", "/trigger", {"alarm_id": "sound-check", "duration_sec": 3, "code_red": False})
    if code != 200:
        raise HTTPException(status_code=code, detail=data.get("error", "Speaker bridge offline"))
    return {"ok": True, "device": data.get("output")}


@app.post("/api/alarms/dismiss")
async def dismiss_active_alarm(req: DismissRequest):
    if not ring_state.is_ringing or not ring_state.alarm:
        return {"ok": True, "message": "No alarm currently ringing."}

    active = ring_state.alarm
    if req.alarm_id not in (None, "", "manual") and req.alarm_id != active.get("id"):
        raise HTTPException(status_code=409, detail="That alarm is no longer ringing; a different one is.")
    if active.get("code_red") and not active.get("is_test"):
        expected = normalize_phrase(active.get("custom_phrase"))
        if normalize_phrase(req.typed_phrase) != expected:
            raise HTTPException(
                status_code=403,
                detail="CODE RED LOCK: That phrase doesn't match yet. Type it exactly, capitals included."
            )

    if ring_state.math_solution is not None and not active.get("is_test"):
        if req.math_answer != ring_state.math_solution:
            raise HTTPException(status_code=403, detail="That answer isn't right. Work the sum out again.")

    log_event(active, "dismissed", f"after {int((local_now() - ring_state.started_at).total_seconds()) if ring_state.started_at else 0} s")
    if not active.get("is_test") and not active.get("is_timer"):
        _clear_snooze(active.get("id"))
    await _end_ring()
    await broadcast_state()
    return {"ok": True, "message": "Alarm stopped and your volume restored."}


async def _end_ring():
    """Clear the ringing state and ask the speaker helper to stop and restore the volume."""
    ring_state.is_ringing = False
    ring_state.alarm = None
    ring_state.math_question = None
    ring_state.math_solution = None
    try:
        async with httpx.AsyncClient(timeout=2.5, headers=BRIDGE_HEADERS) as client:
            await client.post(f"{HOST_AUDIO_BRIDGE_URL}/stop", json={})
    except Exception:
        pass


def _clear_snooze(alarm_id):
    with contextlib.closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("UPDATE alarms SET snoozes_used = 0, snoozed_until = NULL WHERE id = ?", (alarm_id,))
        conn.commit()


def snooze_alarm(active, now):
    """Save the next snooze for a ringing alarm. Returns {until, minutes, used, limit}, or (status, message) when it
    can't snooze: previews, timers and Code Red alarms never do, and an alarm only snoozes as often as it allows."""
    if not active:
        return 409, "Nothing is ringing right now."
    if active.get("is_test") or active.get("is_timer"):
        return 409, "Previews and timers don't snooze."
    if active.get("code_red"):
        return 403, "Code Red alarms can't snooze. Type the phrase to stop it."
    with contextlib.closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM alarms WHERE id = ?", (active.get("id"),)).fetchone()
        if not row:
            return 404, "That alarm was deleted. Stop it instead."
        limit, used, minutes = int(row["snooze_limit"] or 0), int(row["snoozes_used"] or 0), int(row["snooze_minutes"] or 9)
        if limit <= 0:
            return 403, "Snooze is off for this alarm."
        if used >= limit:
            return 403, "No snoozes left. Stop the alarm to turn it off."
        until = now.replace(microsecond=0) + datetime.timedelta(minutes=minutes)
        conn.execute("UPDATE alarms SET snoozes_used = ?, snoozed_until = ? WHERE id = ?",
                     (used + 1, until.isoformat(timespec="seconds"), active["id"]))
        conn.commit()
    return {"until": until, "minutes": minutes, "used": used + 1, "limit": limit}


@app.post("/api/alarms/snooze")
async def snooze_active_alarm(req: SnoozeRequest):
    """Quiet the ringing alarm for its snooze length. The snooze is saved, so it still rings after a restart, and the
    wake timer covers it."""
    active = ring_state.alarm
    if not ring_state.is_ringing or not active:
        raise HTTPException(409, "Nothing is ringing right now.")
    if req.alarm_id != active.get("id"):
        raise HTTPException(409, "That alarm is no longer ringing; a different one is.")
    result = snooze_alarm(active, local_now())
    if not isinstance(result, dict):
        raise HTTPException(*result)
    until, minutes = result["until"], result["minutes"]
    log_event(active, "snoozed", f"{minutes} min, until {until:%H:%M} ({result['used']} of {result['limit']})")
    await _end_ring()
    await broadcast_state()
    return {"ok": True, "until": until.isoformat(timespec="seconds"), "minutes": minutes, "left": result["limit"] - result["used"],
            "message": f"Snoozed until {until:%H:%M}."}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    ws_clients.append(ws)
    try:
        await broadcast_state()
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        if ws in ws_clients:
            ws_clients.remove(ws)

# Send feedback (forwards to the owner's collector)
try:
    import sys as _s, os as _o
    _s.path.insert(0, _o.path.join(_o.path.dirname(_o.path.abspath(__file__)), "..", "..", "core"))
    from feedback import add_feedback_route as _afr
    _afr(app, "alarm")
except Exception as _exc:  # feedback is optional
    print(f"[feedback] disabled: {_exc}")

# Shared Ultra Suite routes: themes, live reload, update check, about
try:
    import sys as _s2, os as _o2
    _s2.path.insert(0, _o2.path.join(_o2.path.dirname(_o2.path.abspath(__file__)), "..", "..", "core"))
    from devtools import add_shared_routes as _asr
    _asr(app, _o2.path.join(_o2.path.dirname(_o2.path.abspath(__file__)), "..", "frontend"))
except Exception as _exc2:
    print(f"[devtools] disabled: {_exc2}")
