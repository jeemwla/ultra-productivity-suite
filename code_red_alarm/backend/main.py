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
from typing import List, Optional

import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .clock import due_day, local_now, next_ring

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
        conn.execute("CREATE TABLE IF NOT EXISTS alarm_events (id INTEGER PRIMARY KEY, ts TEXT, alarm_id TEXT, label TEXT, kind TEXT, detail TEXT)")
        conn.execute('''CREATE TABLE IF NOT EXISTS countdown (
            id INTEGER PRIMARY KEY CHECK (id = 1), run_id TEXT NOT NULL,
            label TEXT NOT NULL, state TEXT NOT NULL, duration_sec INTEGER NOT NULL,
            remaining_sec REAL NOT NULL, deadline REAL, revision INTEGER NOT NULL)''')
        conn.execute("INSERT OR IGNORE INTO countdown VALUES (1, '', 'Timer', 'idle', 300, 300, NULL, 0)")
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
    snooze_limit: int = 0


class DismissRequest(BaseModel):
    alarm_id: str
    typed_phrase: str = ""
    math_answer: Optional[int] = None


class TimerStart(BaseModel):
    duration_sec: int = Field(..., ge=1, le=86400, strict=True)
    label: str = Field("Timer", max_length=80)


class TimerAction(BaseModel):
    run_id: str


def alarm_delivery_enabled():
    """Core gates Dev explicitly; an absent flag preserves Public's existing behavior."""
    return not globals().get("ULTRASUITE_DELIVERY_GATED", False) and os.getenv("ULTRASUITE_ALARM_DELIVERY", "1") == "1"


def _timer_payload(row, now):
    result = dict(row)
    result["sampled_at"] = now
    result["edition"] = os.getenv("ULTRASUITE_EDITION", "Public")
    result["delivery_enabled"] = alarm_delivery_enabled()
    result["remaining_sec"] = max(0.0, row["deadline"] - now) if row["state"] == "running" else row["remaining_sec"]
    return result


def timer_snapshot():
    with contextlib.closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        return _timer_payload(conn.execute("SELECT * FROM countdown WHERE id = 1").fetchone(), time.time())


def claim_due_timer():
    """Durably finish a due timer before audio. An alarm already ringing takes priority."""
    if not alarm_delivery_enabled():
        return None
    with contextlib.closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM countdown WHERE id = 1").fetchone()
        now = time.time()
        if row["state"] != "running" or row["deadline"] > now:
            return None
        # Use the alarm engine's existing catch-up bound after sleep/restart.
        from .clock import CATCH_UP
        expired = now - row["deadline"] > CATCH_UP.total_seconds()
        conn.execute("UPDATE countdown SET state = ?, remaining_sec = 0, deadline = NULL, revision = revision + 1 WHERE id = 1",
                     ("expired" if expired else "finished",))
        conn.commit()
        if expired:
            return None
        return {"id": row["run_id"], "label": row["label"] + " finished", "is_timer": True,
                "code_red": False, "math_mission_difficulty": 0, "ring_duration_sec": 60}


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
            ring_state.is_ringing = False
            ring_state.alarm = None
            ring_state.math_question = None
            ring_state.math_solution = None

    payload = {
        "type": "RING_STATE",
        "timer": timer_snapshot(),
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


async def scheduler_loop():
    prev = local_now()
    startup_catch_up = True
    print(f"[SchedulerLoop] started, local time {prev:%Y-%m-%d %H:%M:%S}")
    while True:
        try:
            now = local_now()
            if now < prev:  # clock moved back (DST, manual change): start over from here
                prev = now
                startup_catch_up = False
            if alarm_delivery_enabled() and not ring_state.is_ringing:
                with contextlib.closing(sqlite3.connect(DB_PATH)) as conn:
                    conn.row_factory = sqlite3.Row
                    rows = conn.execute("SELECT * FROM alarms WHERE enabled = 1").fetchall()
                    for row in rows:
                        alm = dict(row)
                        alm["weekdays"] = json.loads(alm["weekdays"])
                        day_iso = due_day(alm, None if startup_catch_up else prev, now)
                        if not day_iso:
                            continue
                        conn.execute("UPDATE alarms SET last_triggered_date = ? WHERE id = ?", (day_iso, alm["id"]))
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
    return {"is_ringing": ring_state.is_ringing, "alarm": (ring_state.alarm or {}).get("label")}


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
    timer = timer_snapshot()
    if timer["state"] == "running":
        times.append(now + datetime.timedelta(seconds=math.ceil(timer["remaining_sec"])))
    if not times:
        return {"in_sec": None}
    at = min(times)
    return {"in_sec": int((at - now).total_seconds()), "at": at.strftime("%Y-%m-%dT%H:%M:%S")}


@app.get("/api/alarms")
async def list_alarms():
    return {"alarms": fetch_all_alarms()}


@app.get("/api/timer")
async def get_timer():
    return timer_snapshot()


@app.post("/api/timer/start")
async def start_timer(payload: TimerStart):
    if not alarm_delivery_enabled():
        raise HTTPException(403, "Timers are disabled in this edition.")
    if ring_state.is_ringing and (ring_state.alarm or {}).get("is_timer"):
        raise HTTPException(409, "Stop the ringing timer before starting another.")
    with contextlib.closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        state = conn.execute("SELECT state FROM countdown WHERE id = 1").fetchone()[0]
        if state in {"running", "paused"}:
            raise HTTPException(409, "Reset the current timer before starting another.")
        conn.execute("UPDATE countdown SET run_id = ?, label = ?, state = 'running', duration_sec = ?, remaining_sec = ?, deadline = ?, revision = revision + 1 WHERE id = 1",
                     ("timer-" + uuid.uuid4().hex, payload.label.strip() or "Timer", payload.duration_sec,
                      payload.duration_sec, time.time() + payload.duration_sec))
        conn.commit()
    return timer_snapshot()


@app.post("/api/timer/{action}")
async def control_timer(action: str, payload: TimerAction):
    if action not in {"pause", "resume", "reset"}:
        raise HTTPException(404, "Unknown timer action.")
    if action == "resume" and not alarm_delivery_enabled():
        raise HTTPException(403, "Timers are disabled in this edition.")
    with contextlib.closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM countdown WHERE id = 1").fetchone()
        if payload.run_id != row["run_id"]:
            raise HTTPException(409, "This timer has changed. Refresh and try again.")
        remaining = _timer_payload(row, time.time())["remaining_sec"]
        if action == "reset":
            state, remaining, deadline = "idle", row["duration_sec"], None
        elif action == "pause" and row["state"] == "running" and remaining > 0:
            state, deadline = "paused", None
        elif action == "resume" and row["state"] == "paused":
            state, deadline = "running", time.time() + remaining
        else:
            raise HTTPException(409, "The timer can no longer do that. Refresh and try again.")
        conn.execute("UPDATE countdown SET state = ?, remaining_sec = ?, deadline = ?, revision = revision + 1 WHERE id = 1",
                     (state, remaining, deadline))
        conn.commit()
    return timer_snapshot()


def _validate(payload: AlarmCreate):
    if not payload.label.strip():
        raise HTTPException(status_code=400, detail="Give the alarm a name.")
    hh, mm = (int(x) for x in payload.time_hhmm.split(":"))
    if hh > 23 or mm > 59:
        raise HTTPException(status_code=400, detail="That isn't a valid time.")
    if not (60 <= payload.ring_duration_sec <= 3600):
        raise HTTPException(status_code=400, detail="Ring duration must be between 1 and 60 minutes.")
    if not payload.weekdays and not payload.target_date:
        raise HTTPException(status_code=400, detail="Pick at least one weekday or a specific date.")
    if payload.code_red and not payload.custom_phrase.strip():
        raise HTTPException(status_code=400, detail="Code Red alarms require a non-empty verification phrase.")


@app.put("/api/alarms/{alarm_id}")
async def update_alarm(alarm_id: str, payload: AlarmCreate):
    """Edit an alarm in place. It counts as new, so it can ring again today."""
    _validate(payload)
    with contextlib.closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.execute('''
            UPDATE alarms SET label = ?, time_hhmm = ?, target_date = ?, weekdays = ?, recurring = ?, enabled = ?,
                code_red = ?, custom_phrase = ?, volume_percent = ?, ring_duration_sec = ?, wake_up_check_min = ?,
                math_mission_difficulty = ?, snooze_limit = ?, snoozes_used = 0, last_triggered_date = NULL
            WHERE id = ?
        ''', (payload.label.strip(), payload.time_hhmm, payload.target_date, json.dumps(payload.weekdays),
              int(payload.recurring), int(payload.enabled), int(payload.code_red), payload.custom_phrase.strip(),
              payload.volume_percent, payload.ring_duration_sec, payload.wake_up_check_min,
              payload.math_mission_difficulty, payload.snooze_limit, alarm_id))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Alarm not found")
    return {"ok": True, "id": alarm_id, "alarms": fetch_all_alarms()}


@app.post("/api/alarms")
async def create_alarm(payload: AlarmCreate):
    alarm_id = f"alm-{uuid.uuid4().hex[:8]}"
    _validate(payload)
    with contextlib.closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute('''
            INSERT INTO alarms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            alarm_id,
            payload.label,
            payload.time_hhmm,
            payload.target_date,
            json.dumps(payload.weekdays),
            int(payload.recurring),
            int(payload.enabled),
            int(payload.code_red),
            payload.custom_phrase.strip(),
            payload.volume_percent,
            payload.ring_duration_sec,
            payload.wake_up_check_min,
            payload.math_mission_difficulty,
            payload.snooze_limit,
            0,
            None
        ))
        conn.commit()
    return {"ok": True, "id": alarm_id, "alarms": fetch_all_alarms()}


@app.patch("/api/alarms/{alarm_id}/toggle")
async def toggle_alarm(alarm_id: str):
    with contextlib.closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.execute("SELECT enabled FROM alarms WHERE id = ?", (alarm_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Alarm not found")
        new_val = 0 if row[0] else 1
        conn.execute("UPDATE alarms SET enabled = ?, last_triggered_date = NULL WHERE id = ?", (new_val, alarm_id))
        conn.commit()
    return {"ok": True, "alarms": fetch_all_alarms()}


@app.delete("/api/alarms/{alarm_id}")
async def delete_alarm(alarm_id: str):
    with contextlib.closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("DELETE FROM alarms WHERE id = ?", (alarm_id,))
        conn.commit()
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
        expected = (active.get("custom_phrase") or "").strip()
        if req.typed_phrase.strip() != expected:
            raise HTTPException(
                status_code=403,
                detail="CODE RED LOCK: Exact custom phrase mismatch! Type the phrase character-for-character."
            )

    if ring_state.math_solution is not None and not active.get("is_test"):
        if req.math_answer != ring_state.math_solution:
            raise HTTPException(
                status_code=403,
                detail="Math mission answer incorrect! Solve the arithmetic equation to disarm."
            )

    log_event(active, "dismissed", f"after {int((local_now() - ring_state.started_at).total_seconds()) if ring_state.started_at else 0} s")
    ring_state.is_ringing = False
    ring_state.alarm = None
    ring_state.math_question = None
    ring_state.math_solution = None
    try:
        async with httpx.AsyncClient(timeout=2.5, headers=BRIDGE_HEADERS) as client:
            await client.post(f"{HOST_AUDIO_BRIDGE_URL}/stop", json={})
    except Exception:
        pass
    await broadcast_state()
    return {"ok": True, "message": "Alarm stopped and your volume restored."}


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
