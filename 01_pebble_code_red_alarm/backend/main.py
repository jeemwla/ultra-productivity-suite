#!/usr/bin/env python3
'''
FastAPI Backend for App 1: Creative Pebble 100% Volume + Code Red Alarm Engine.
Supports desktop QHD UI and remote phone control over Tailscale.
'''
import asyncio
import datetime
import json
import os
import random
import sqlite3
import uuid
from contextlib import asynccontextmanager
from typing import List, Optional

import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

DB_PATH = os.path.join(os.environ.get("ULTRASUITE_DATA") or os.path.join(os.path.dirname(__file__), "..", "data"), "alarms.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
HOST_AUDIO_BRIDGE_URL = os.getenv("HOST_AUDIO_BRIDGE_URL", "http://host.docker.internal:8091")
BRIDGE_HEADERS = {"X-Bridge-Token": os.getenv("BRIDGE_TOKEN", "")}


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS alarms (
                id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                time_hhmm TEXT NOT NULL,
                target_date TEXT,
                weekdays TEXT NOT NULL,
                recurring INTEGER NOT NULL DEFAULT 1,
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
        # Seed a default Code Red alarm and a recurring weekday alarm if table is empty
        cur = conn.execute("SELECT COUNT(*) FROM alarms")
        if cur.fetchone()[0] == 0:
            seed_alarms = [
                (
                    "alm-codered-01",
                    "Morning Deep Work Launch (Code Red)",
                    "06:30",
                    None,
                    json.dumps(["Mon", "Tue", "Wed", "Thu", "Fri"]),
                    1,
                    0,  # seeded DISABLED: enable it yourself in the UI
                    1,
                    "I AM OUT OF BED AND READY TO BUILD",
                    100,
                    600,
                    5,
                    2,
                    0,
                    0,
                    None
                ),
                (
                    "alm-weekend-02",
                    "Weekend Gym & Hydration",
                    "08:15",
                    None,
                    json.dumps(["Sat", "Sun"]),
                    1,
                    0,  # seeded DISABLED
                    0,
                    "",
                    100,
                    600,
                    10,
                    1,
                    2,
                    0,
                    None
                ),
            ]
            conn.executemany('''
                INSERT INTO alarms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', seed_alarms)
            conn.commit()


class AlarmCreate(BaseModel):
    label: str = Field(..., example="6:30 AM Standup & Cold Plunge")
    time_hhmm: str = Field(..., pattern=r"^\d{2}:\d{2}$", example="06:30")
    target_date: Optional[str] = Field(None, example="2026-11-11")
    weekdays: List[str] = Field(default_factory=lambda: ["Mon", "Tue", "Wed", "Thu", "Fri"])
    recurring: bool = True
    enabled: bool = True
    code_red: bool = True
    custom_phrase: str = "I AM OUT OF BED AND READY TO BUILD"
    volume_percent: int = 100
    ring_duration_sec: int = 600
    wake_up_check_min: int = 5
    math_mission_difficulty: int = 1
    snooze_limit: int = 0


class DismissRequest(BaseModel):
    alarm_id: str
    typed_phrase: str = ""
    math_answer: Optional[int] = None


class ActiveRingingState:
    def __init__(self):
        self.is_ringing: bool = False
        self.alarm: Optional[dict] = None
        self.started_at: Optional[datetime.datetime] = None
        self.expires_at: Optional[datetime.datetime] = None
        self.math_question: Optional[str] = None
        self.math_solution: Optional[int] = None


ring_state = ActiveRingingState()
ws_clients: List[WebSocket] = []


async def broadcast_state():
    now = datetime.datetime.now()
    remaining_sec = 0
    if ring_state.is_ringing and ring_state.expires_at:
        remaining_sec = max(0, int((ring_state.expires_at - now).total_seconds()))
        if remaining_sec == 0:
            ring_state.is_ringing = False
            ring_state.alarm = None
            ring_state.math_question = None
            ring_state.math_solution = None

    payload = {
        "type": "RING_STATE",
        "is_ringing": ring_state.is_ringing,
        "alarm": ring_state.alarm,
        "remaining_sec": remaining_sec,
        "math_question": ring_state.math_question,
        "server_time": now.strftime("%H:%M:%S"),
        "server_date": now.strftime("%Y-%m-%d (%a)")
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


async def trigger_alarm_now(alarm_dict: dict):
    now = datetime.datetime.now()
    ring_state.is_ringing = True
    ring_state.alarm = alarm_dict
    ring_state.started_at = now
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

    # Notify Windows Host Pebble Audio Enforcer (100% volume + hardware unmute loop)
    try:
        async with httpx.AsyncClient(timeout=2.5, headers=BRIDGE_HEADERS) as client:
            await client.post(
                f"{HOST_AUDIO_BRIDGE_URL}/trigger",
                json={
                    "alarm_id": alarm_dict["id"],
                    "duration_sec": duration,
                    "code_red": bool(alarm_dict["code_red"])
                }
            )
    except Exception as exc:
        print(f"[AlarmEngine] Host audio bridge unreachable (web audio siren will still fire): {exc}")

    await broadcast_state()


async def scheduler_loop():
    while True:
        try:
            now = datetime.datetime.now()
            hhmm = now.strftime("%H:%M")
            today_iso = now.strftime("%Y-%m-%d")
            weekday_short = now.strftime("%a")  # Mon, Tue, Wed...

            if not ring_state.is_ringing:
                with sqlite3.connect(DB_PATH) as conn:
                    conn.row_factory = sqlite3.Row
                    rows = conn.execute("SELECT * FROM alarms WHERE enabled = 1").fetchall()
                    for row in rows:
                        alm = dict(row)
                        alm["weekdays"] = json.loads(alm["weekdays"])
                        if alm["time_hhmm"] != hhmm:
                            continue
                        if alm["last_triggered_date"] == today_iso:
                            continue
                        # Check date or weekday match
                        date_match = (not alm["target_date"]) or (alm["target_date"] == today_iso)
                        day_match = (not alm["weekdays"]) or (weekday_short in alm["weekdays"])
                        if date_match and day_match:
                            conn.execute(
                                "UPDATE alarms SET last_triggered_date = ? WHERE id = ?",
                                (today_iso, alm["id"])
                            )
                            if not alm["recurring"] and alm["target_date"]:
                                conn.execute("UPDATE alarms SET enabled = 0 WHERE id = ?", (alm["id"],))
                            conn.commit()
                            await trigger_alarm_now(alm)
                            break
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


app = FastAPI(title="Pebble 100% Volume + Code Red Alarm API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def fetch_all_alarms() -> List[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM alarms ORDER BY time_hhmm ASC").fetchall()
        result = []
        for r in rows:
            item = dict(r)
            item["weekdays"] = json.loads(item["weekdays"])
            item["recurring"] = bool(item["recurring"])
            item["enabled"] = bool(item["enabled"])
            item["code_red"] = bool(item["code_red"])
            result.append(item)
        return result


@app.get("/")
async def serve_ui():
    frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend", "index.html")
    return FileResponse(frontend_path)


@app.get("/api/ring")
async def ring_status():
    """Tiny status for the desktop app: is anything ringing right now?"""
    return {"is_ringing": ring_state.is_ringing, "alarm": (ring_state.alarm or {}).get("label")}


@app.get("/api/alarms")
async def list_alarms():
    return {"alarms": fetch_all_alarms()}


@app.post("/api/alarms")
async def create_alarm(payload: AlarmCreate):
    alarm_id = f"alm-{uuid.uuid4().hex[:8]}"
    if not payload.label.strip():
        raise HTTPException(status_code=400, detail="Give the alarm a name.")
    if not (60 <= payload.ring_duration_sec <= 3600):
        raise HTTPException(status_code=400, detail="Ring duration must be between 1 and 60 minutes.")
    if not payload.weekdays and not payload.target_date:
        raise HTTPException(status_code=400, detail="Pick at least one weekday or a specific date.")
    if payload.code_red and not payload.custom_phrase.strip():
        raise HTTPException(status_code=400, detail="Code Red alarms require a non-empty verification phrase.")
    with sqlite3.connect(DB_PATH) as conn:
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
    with sqlite3.connect(DB_PATH) as conn:
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
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM alarms WHERE id = ?", (alarm_id,))
        conn.commit()
    return {"ok": True, "alarms": fetch_all_alarms()}


@app.post("/api/alarms/{alarm_id}/test-ring")
async def test_ring_alarm(alarm_id: str, seconds: int = 10):
    """Preview an alarm: rings for a few seconds and can be dismissed with one tap."""
    alarms = fetch_all_alarms()
    target = next((a for a in alarms if a["id"] == alarm_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Alarm not found")
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
                "selected": "default", "lock_volume": True, "error": devices.get("error")}
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
    code, data = await _bridge("POST", "/trigger", {"alarm_id": "sound-check", "duration_sec": 3, "code_red": False})
    if code != 200:
        raise HTTPException(status_code=code, detail=data.get("error", "Speaker bridge offline"))
    return {"ok": True, "device": data.get("output")}


@app.post("/api/alarms/dismiss")
async def dismiss_active_alarm(req: DismissRequest):
    if not ring_state.is_ringing or not ring_state.alarm:
        return {"ok": True, "message": "No alarm currently ringing."}

    active = ring_state.alarm
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
    return {"ok": True, "message": "Alarm disarmed and Pebble speakers restored."}


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
