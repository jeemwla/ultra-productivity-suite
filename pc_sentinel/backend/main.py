#!/usr/bin/env python3
"""
Hardware & Network Sentinel API.
A single background loop gathers hardware + network data every 2 seconds;
every open dashboard (PC or phone) receives the same cached snapshot.
"""
import asyncio
import os
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .lhm_collector import collect_hardware_telemetry
from .net_monitor import get_live_ping_snapshot, run_on_demand_speedtest
from .glinet_vpn import vpn_controller
from . import speedtest

latest = {"hardware": {"live": False, "source": "starting", "groups": [], "summary": {}},
          "network": {"online": False, "targets": []}, "vpn": vpn_controller.status(), "ts": 0}


async def poll_loop():
    while True:
        try:
            hw, net, _ = await asyncio.gather(collect_hardware_telemetry(), get_live_ping_snapshot(), vpn_controller.refresh())
            latest.update(hardware=hw, network=net, vpn=vpn_controller.status(), ts=time.time())
        except Exception as exc:
            print(f"[sentinel] poll error: {exc}")
        await asyncio.sleep(1.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(poll_loop())
    yield
    task.cancel()


app = FastAPI(title="Hardware & Network Sentinel", lifespan=lifespan)

from . import power_context  # noqa: E402  optional local desktop PSU context, no hardware writes
power_context.register(app)
from . import psu_catalog  # noqa: E402  offline factual picker, no telemetry-loop downloads
psu_catalog.register(app)

from . import system_tools  # noqa: E402  power plans, refresh rate, top apps, LibreHardwareMonitor setup
system_tools.register(app)


from . import laptop  # noqa: E402  battery health, history and screen brightness (Battery page, laptops only)
laptop.register(app)
# The battery features used to ship as a separate "Laptop Pack" add-on in %APPDATA%\UltraSuite\addons\laptop_pack.
# They are built in now; a leftover copy there is ignored (the installer removes it).


class VPNTogglePayload(BaseModel):
    enable: Optional[bool] = None


@app.get("/")
async def serve_dashboard():
    return FileResponse(os.path.join(os.path.dirname(__file__), "..", "frontend", "index.html"))


@app.get("/api/snapshot")
async def get_full_snapshot():
    return latest


@app.get("/api/health")
async def health():
    return {"ok": True, "hardware_live": latest["hardware"].get("live"), "age_sec": round(time.time() - latest["ts"], 1)}


@app.post("/api/speedtest")
async def trigger_speedtest():
    return await run_on_demand_speedtest()


@app.get("/api/vpn/status")
async def get_vpn_status():
    return vpn_controller.status()


@app.post("/api/vpn/toggle")
async def toggle_glinet_vpn(payload: VPNTogglePayload):
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

    try:
        await speedtest.run(send)
    finally:
        try:
            await ws.close()
        except Exception:
            pass


@app.websocket("/ws")
async def telemetry_ws(ws: WebSocket):
    await ws.accept()
    last = -1
    try:
        while True:
            if latest["ts"] != last:
                last = latest["ts"]
                await ws.send_json(latest)
            await asyncio.sleep(0.25)
    except (WebSocketDisconnect, RuntimeError):
        pass

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
