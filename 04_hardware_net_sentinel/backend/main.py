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

latest = {"hardware": {"live": False, "source": "starting", "groups": [], "summary": {}},
          "network": {"online": False, "targets": []}, "vpn": vpn_controller.status(), "ts": 0}


async def poll_loop():
    while True:
        try:
            hw, net = await asyncio.gather(collect_hardware_telemetry(), get_live_ping_snapshot())
            latest.update(hardware=hw, network=net, vpn=vpn_controller.status(), ts=time.time())
        except Exception as exc:
            print(f"[sentinel] poll error: {exc}")
        await asyncio.sleep(2.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(poll_loop())
    yield
    task.cancel()


app = FastAPI(title="Hardware & Network Sentinel", lifespan=lifespan)


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


@app.websocket("/ws")
async def telemetry_ws(ws: WebSocket):
    await ws.accept()
    last = -1
    try:
        while True:
            if latest["ts"] != last:
                last = latest["ts"]
                await ws.send_json(latest)
            await asyncio.sleep(0.5)
    except (WebSocketDisconnect, RuntimeError):
        pass
