#!/usr/bin/env python3
"""
Hardware & Network Sentinel API.
A single background loop gathers hardware + network data about once a second;
every open dashboard (PC or phone) receives the same cached snapshot.
"""
import asyncio
import os
import time
from collections import deque
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .lhm_collector import collect_hardware_telemetry
from .net_monitor import get_live_ping_snapshot, run_on_demand_speedtest
from .glinet_vpn import vpn_controller
from . import speedtest
from . import preferences

VPN_OFF = {"supported": False, "message": "Enable router controls in Settings / Extras."}
latest = {"hardware": {"live": False, "source": "starting", "groups": [], "summary": {}},
          "network": {"online": False, "targets": []}, "vpn": dict(VPN_OFF), "ts": 0}

POLL_SECONDS = 1.0
# With no window open and nobody asking for the snapshot, sensors are read every 5 s instead of every second;
# the next window to connect wakes the loop at once. Nothing on the page depends on readings taken while unseen.
IDLE_POLL_SECONDS = 5.0
IDLE_AFTER_SECONDS = 30.0
viewers = {"sockets": 0, "last": 0.0}


def mark_viewer(now=None):
    viewers["last"] = time.time() if now is None else now


def poll_interval(now=None):
    now = time.time() if now is None else now
    watched = viewers["sockets"] > 0 or now - viewers["last"] < IDLE_AFTER_SECONDS
    return POLL_SECONDS if watched else IDLE_POLL_SECONDS
# A collector that hangs (an unresponsive sensor web server, a stuck WMI query) must not freeze every open window.
# Each part gets its own deadline; on a miss the last good value stays and is marked stale.
JOB_TIMEOUT = {"hardware": 8.0, "network": 5.0, "vpn": 8.0}
ERROR_REPEAT_SECONDS = 300


def _load_preview():
    """SENTINEL_PREVIEW turns on the developer preview (pc_sentinel/dev_preview.py, run from the source folder only)."""
    if not (os.environ.get("SENTINEL_PREVIEW") or "").strip():
        return None, None
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        import dev_preview
    except ImportError:
        print("[sentinel] SENTINEL_PREVIEW is set but the preview is not part of this build; showing real data")
        return None, None
    from . import laptop
    kind = dev_preview.mode()
    return kind, dev_preview.Preview(kind, laptop)


PREVIEW_KIND, PREVIEW = _load_preview()
class LoopSignal:
    """An asyncio.Event per running event loop. A module-level Event binds to the first loop that waits on it, and a
    socket served on any other loop (a test client, an embedding server) would then end at once."""

    def __init__(self):
        self._events = {}

    def _event(self):
        loop = asyncio.get_running_loop()
        event = self._events.get(loop)
        if event is None:
            self._events = {l: e for l, e in self._events.items() if not l.is_closed()}
            event = self._events[loop] = asyncio.Event()
        return event

    def set(self):
        for loop, event in list(self._events.items()):
            if loop.is_closed():
                continue
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if loop is running:
                event.set()
            else:
                loop.call_soon_threadsafe(event.set)

    def clear(self):
        self._event().clear()

    async def wait(self):
        await self._event().wait()


_changed = LoopSignal()  # a new snapshot is ready
_wake = LoopSignal()  # a window opened: poll now
# The last two minutes of the numbers the charts draw, so a page opened now shows full charts instead of empty ones.
HISTORY_SAMPLES = 120
history = deque(maxlen=HISTORY_SAMPLES)
SUMMARY_KEYS = ("cpu_load", "gpu_load", "ram_load", "cpu_temp", "gpu_temp")
POWER_KEYS = ("measured", "measured_subtotal_w", "modeled_dc_w", "modeled_wall_w", "wall_model")


def history_sample(snapshot):
    hw = snapshot.get("hardware") or {}
    summary, power = hw.get("summary") or {}, hw.get("power") or {}
    slim_power = {k: power[k] for k in POWER_KEYS if k in power}
    coverage = power.get("coverage") or {}
    if "component_readings_complete" in coverage:
        slim_power["coverage"] = {"component_readings_complete": coverage["component_readings_complete"]}
    return {"ts": snapshot.get("ts"), "stale": bool(hw.get("stale")),
            "summary": {k: summary.get(k) for k in SUMMARY_KEYS}, "power": slim_power}
_errors = {}


def log_error(key, message, now=None):
    """Prints a poll error once, then at most every ERROR_REPEAT_SECONDS while it repeats (no log flood every second)."""
    now = time.time() if now is None else now
    last = _errors.get(key)
    if last and last[0] == message and now - last[1] < ERROR_REPEAT_SECONDS:
        return False
    _errors[key] = (message, now)
    print(f"[sentinel] {key} error: {message}")
    return True


async def _bounded(name, coro):
    try:
        return await asyncio.wait_for(coro, JOB_TIMEOUT[name])
    except asyncio.TimeoutError:
        log_error(name, f"no answer within {JOB_TIMEOUT[name]:.0f} s; keeping the last reading")
    except Exception as exc:  # one failing part never blanks the others
        log_error(name, f"{type(exc).__name__}: {exc}")
    return None


def _stale(previous):
    value = dict(previous)
    value["stale"] = True
    return value


async def poll_once():
    prefs = preferences.load()
    if PREVIEW:
        latest.update(PREVIEW.snapshot(), vpn=dict(VPN_OFF), preferences=prefs)
        history.append(history_sample(latest))
        _changed.set()
        return
    jobs = [_bounded("hardware", collect_hardware_telemetry()),
            _bounded("network", get_live_ping_snapshot(router_ping=prefs["router_ping"]))]
    if prefs["router_vpn"]:
        jobs.append(_bounded("vpn", vpn_controller.refresh()))
    results = await asyncio.gather(*jobs)
    hardware = results[0] if results[0] is not None else _stale(latest["hardware"])
    network = results[1] if results[1] is not None else _stale(latest["network"])
    vpn = vpn_controller.status() if prefs["router_vpn"] else dict(VPN_OFF)
    latest.update(hardware=hardware, network=network, vpn=vpn, preferences=prefs, ts=time.time())
    history.append(history_sample(latest))
    _changed.set()


async def poll_loop():
    while True:
        started = time.monotonic()
        try:
            await poll_once()
        except Exception as exc:
            log_error("poll", f"{type(exc).__name__}: {exc}")
        _wake.clear()
        try:
            await asyncio.wait_for(_wake.wait(), max(0.2, poll_interval() - (time.monotonic() - started)))
        except asyncio.TimeoutError:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(poll_loop())
    from . import psu_refresh
    refresher = asyncio.create_task(psu_refresh.loop())  # does nothing unless turned on in Settings / Extras
    yield
    task.cancel()
    refresher.cancel()


app = FastAPI(title="Hardware & Network Sentinel", lifespan=lifespan)
if PREVIEW:  # registered first so its fixture routes answer before the real ones
    PREVIEW.register(app)
    print(f"[sentinel] preview mode ({PREVIEW_KIND}): fixture data, no changes to this PC")
preferences.register(app)

from . import power_context  # noqa: E402  optional local desktop PSU context, no hardware writes
power_context.register(app)
from . import psu_catalog  # noqa: E402  offline factual picker, no telemetry-loop downloads
psu_catalog.register(app)
from . import max_power  # noqa: E402  optional Max Performance plan, applied only on request
max_power.register(app)

from . import system_tools  # noqa: E402  power plans, refresh rate, top apps, LibreHardwareMonitor setup
system_tools.register(app)
from . import benchmark  # noqa: E402  bounded before/after measurement for Max Performance
benchmark.register(app)
from . import psu_refresh  # noqa: E402  optional 14-day PSU catalog refresh (off by default)
psu_refresh.register(app)
from . import storage_writers  # noqa: E402  always-on heavy writer detection (read-only counters)
storage_writers.register(app)


from . import laptop  # noqa: E402  battery health, history and screen brightness (Battery page, laptops only)
laptop.register(app)
# The battery features used to ship as a separate "Laptop Pack" add-on in %APPDATA%\UltraSuite\addons\laptop_pack.
# They are built in now; a leftover copy there is ignored (the installer removes it).


class VPNTogglePayload(BaseModel):
    enable: Optional[bool] = None


@app.get("/")
async def serve_dashboard():
    return FileResponse(os.path.join(os.path.dirname(__file__), "..", "frontend", "index.html"), headers={"Cache-Control": "no-cache"})


UI_SCRIPTS = {"battery_view.js", "edit_layout.js", "sentinel_panels.js"}


@app.get("/ui/{name}", include_in_schema=False)
async def serve_ui_script(name: str):
    if name not in UI_SCRIPTS:
        raise HTTPException(404, "Not found")
    return FileResponse(os.path.join(os.path.dirname(__file__), "..", "frontend", name), media_type="text/javascript",
                        headers={"Cache-Control": "no-cache"})


@app.get("/api/snapshot")
async def get_full_snapshot():
    if poll_interval() != POLL_SECONDS:
        _wake.set()  # coming back from idle: the next reading follows at once instead of after the 5 s pause
    mark_viewer()
    return latest


@app.get("/api/history")
async def get_history():
    """Oldest first, about one sample a second while watched (every 5 s while no window is open)."""
    mark_viewer()
    _wake.set()
    return {"interval_s": POLL_SECONDS, "samples": list(history)}


@app.get("/api/health")
async def health():
    ready = latest["ts"] > 0
    return {"ok": True, "ready": ready, "hardware_live": latest["hardware"].get("live"),
            "stale": bool(latest["hardware"].get("stale") or latest["network"].get("stale")),
            "age_sec": round(time.time() - latest["ts"], 1) if ready else None, "preview": PREVIEW_KIND}


@app.post("/api/speedtest")
async def trigger_speedtest():
    return await run_on_demand_speedtest()


@app.get("/api/vpn/status")
async def get_vpn_status():
    if not preferences.load()["router_vpn"]:
        return dict(VPN_OFF)
    return vpn_controller.status()


@app.post("/api/vpn/toggle")
async def toggle_glinet_vpn(payload: VPNTogglePayload):
    if not preferences.load()["router_vpn"]:
        raise HTTPException(409, "Router VPN controls are disabled in Settings / Extras.")
    result = await vpn_controller.toggle_vpn(payload.enable)
    latest["vpn"] = result
    return result


@app.websocket("/ws/speedtest")
async def speedtest_ws(ws: WebSocket):
    """Runs one full speed test and streams every reading to the page."""
    await ws.accept()

    async def send(msg):
        try:
            await ws.send_json(msg)
        except Exception:
            pass

    async def closed():
        """Returns when the page goes away (tab closed, reload, navigation)."""
        try:
            while (await ws.receive())["type"] != "websocket.disconnect":
                pass
        except Exception:
            pass

    test = asyncio.create_task(speedtest.run(send))
    watcher = asyncio.create_task(closed())
    try:
        done, _ = await asyncio.wait({test, watcher}, return_when=asyncio.FIRST_COMPLETED)
        if test not in done:  # nobody is watching: stop downloading instead of using the line for the full run
            test.cancel()
            try:
                await test
            except (asyncio.CancelledError, Exception):
                pass
    finally:
        watcher.cancel()
        try:
            await ws.close()
        except Exception:
            pass


@app.websocket("/ws")
async def telemetry_ws(ws: WebSocket):
    await ws.accept()
    last = -1
    viewers["sockets"] += 1
    mark_viewer()
    _wake.set()  # a window just opened: fresh readings now, not after an idle pause
    try:
        while True:
            if latest["ts"] != last:
                last = latest["ts"]
                await ws.send_json(latest)
            # Wake on the next snapshot instead of checking four times a second; the timeout keeps a slow loop alive.
            _changed.clear()
            if latest["ts"] == last:
                try:
                    await asyncio.wait_for(_changed.wait(), 2.0)
                except asyncio.TimeoutError:
                    pass
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        viewers["sockets"] = max(0, viewers["sockets"] - 1)
        mark_viewer()

# Send feedback (forwards to the owner's collector)
try:
    import sys as _s, os as _o
    _s.path.insert(0, _o.path.join(_o.path.dirname(_o.path.abspath(__file__)), "..", "..", "core"))
    from feedback import add_feedback_route as _afr
    _afr(app, "sentinel")
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
