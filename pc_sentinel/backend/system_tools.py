#!/usr/bin/env python3
"""
PC Sentinel system tools (any Windows PC, no admin needed unless noted):

  GET  /api/processes            top apps by CPU and memory (grouped by app, like Task Manager's Apps view)
  GET  /api/power/plans          Windows power plans + which one is active
  POST /api/power/plans {guid}   switch power plan
  GET  /api/display              each monitor: current mode + every refresh rate it supports at that resolution
  POST /api/display {device, hz} change a monitor's refresh rate (keeps resolution)
  POST /api/setup/lhm            install LibreHardwareMonitor (winget) with its web server on, and start it (one UAC prompt)
  GET  /api/report               full snapshot as JSON (for "Export report")
"""
import ctypes
import os
import re
import subprocess
import time
from ctypes import wintypes

import psutil
from fastapi import HTTPException

NO_WINDOW = 0x08000000
IS_WIN = os.name == "nt"
_proc_cache = {"at": 0.0, "data": None}
_procs = {}
_gpu_pdh = None


def _gpu_by_pid() -> dict:
    """Per-process GPU use the way Task Manager shows it: the busiest engine type (3D, Copy, Video...) per process."""
    global _gpu_pdh
    if not IS_WIN:
        return {}
    try:
        if _gpu_pdh is None:
            from .native_sensors import Pdh
            _gpu_pdh = Pdh([r"\GPU Engine(*)\Utilization Percentage", r"\GPU Process Memory(*)\Dedicated Usage"])
        data = _gpu_pdh.collect()
    except Exception:
        return {}
    eng = {}
    for inst, v in (data.get(r"\GPU Engine(*)\Utilization Percentage") or {}).items():
        m = re.match(r"pid_(\d+)_.*engtype_(.+)$", inst)
        if m:
            k = (int(m.group(1)), m.group(2))
            eng[k] = eng.get(k, 0.0) + v
    out = {}
    for (pid, _t), v in eng.items():
        out.setdefault(pid, {"gpu": 0.0, "vram_mb": 0.0})
        out[pid]["gpu"] = max(out[pid]["gpu"], v)
    for inst, v in (data.get(r"\GPU Process Memory(*)\Dedicated Usage") or {}).items():
        m = re.match(r"pid_(\d+)_", inst)
        if m:
            out.setdefault(int(m.group(1)), {"gpu": 0.0, "vram_mb": 0.0})["vram_mb"] += v / 1048576
    return out


def _run(args, timeout=20):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW if IS_WIN else 0)


# ----------------------------------------------------------------------------- top apps
def top_apps(limit=8):
    if _proc_cache["data"] is not None and time.time() - _proc_cache["at"] < 2:
        return _proc_cache["data"]
    ncpu = psutil.cpu_count() or 1
    apps = {}
    gpu = _gpu_by_pid()
    # psutil measures CPU between two calls on the SAME Process object, so keep them alive across polls
    alive = set()
    for p in psutil.process_iter(["name", "memory_info", "exe"]):
        alive.add(p.pid)
        tracked = _procs.get(p.pid)
        if tracked is None:
            tracked = _procs[p.pid] = p
            try:
                tracked.cpu_percent(None)  # prime; the real value arrives on the next poll
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        try:
            name = (p.info["name"] or "").replace(".exe", "")
            if not name or name.lower() in ("system idle process", "idle", "system", "registry", "memory compression"):
                continue
            cpu = tracked.cpu_percent(None) / ncpu
            mem = p.info["memory_info"].rss if p.info.get("memory_info") else 0
            a = apps.setdefault(name.lower(), {"name": name, "cpu": 0.0, "mem_mb": 0.0, "gpu": 0.0, "vram_mb": 0.0, "count": 0, "exe": p.info.get("exe")})
            a["cpu"] += cpu; a["mem_mb"] += mem / 1048576; a["count"] += 1
            g = gpu.get(p.pid)
            if g:
                a["gpu"] += g["gpu"]; a["vram_mb"] += g["vram_mb"]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    for pid in list(_procs):
        if pid not in alive:
            del _procs[pid]
    rows = list(apps.values())
    for r in rows:
        r["cpu"] = round(r["cpu"], 1); r["mem_mb"] = round(r["mem_mb"]); r["gpu"] = round(min(100.0, r["gpu"]), 1); r["vram_mb"] = round(r["vram_mb"])
    data = {"by_cpu": sorted(rows, key=lambda r: -r["cpu"])[:limit], "by_mem": sorted(rows, key=lambda r: -r["mem_mb"])[:limit],
            "by_gpu": sorted(rows, key=lambda r: (-r["gpu"], -r["vram_mb"]))[:limit], "gpu_available": bool(gpu),
            "total_processes": sum(r["count"] for r in rows)}
    _proc_cache.update(at=time.time(), data=data)
    return data


# ----------------------------------------------------------------------------- power plans
def power_plans():
    if not IS_WIN:
        return {"plans": []}
    out = _run(["powercfg", "/list"]).stdout
    plans = []
    for line in out.splitlines():
        m = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\s+\((.+?)\)\s*(\*)?", line, re.I)
        if m:
            plans.append({"guid": m.group(1), "name": m.group(2), "active": bool(m.group(3))})
    return {"plans": plans}


def set_power_plan(guid: str):
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", guid or ""):
        raise HTTPException(400, "Unknown power plan")
    r = _run(["powercfg", "/setactive", guid])
    if r.returncode != 0:
        raise HTTPException(500, "Windows didn't accept that power plan")
    return power_plans()


# ----------------------------------------------------------------------------- display refresh rate
class DEVMODEW(ctypes.Structure):
    _fields_ = [("dmDeviceName", wintypes.WCHAR * 32), ("dmSpecVersion", wintypes.WORD), ("dmDriverVersion", wintypes.WORD),
                ("dmSize", wintypes.WORD), ("dmDriverExtra", wintypes.WORD), ("dmFields", wintypes.DWORD),
                ("dmPositionX", wintypes.LONG), ("dmPositionY", wintypes.LONG), ("dmDisplayOrientation", wintypes.DWORD),
                ("dmDisplayFixedOutput", wintypes.DWORD), ("dmColor", wintypes.SHORT), ("dmDuplex", wintypes.SHORT),
                ("dmYResolution", wintypes.SHORT), ("dmTTOption", wintypes.SHORT), ("dmCollate", wintypes.SHORT),
                ("dmFormName", wintypes.WCHAR * 32), ("dmLogPixels", wintypes.WORD), ("dmBitsPerPel", wintypes.DWORD),
                ("dmPelsWidth", wintypes.DWORD), ("dmPelsHeight", wintypes.DWORD), ("dmDisplayFlags", wintypes.DWORD),
                ("dmDisplayFrequency", wintypes.DWORD), ("dmICMMethod", wintypes.DWORD), ("dmICMIntent", wintypes.DWORD),
                ("dmMediaType", wintypes.DWORD), ("dmDitherType", wintypes.DWORD), ("dmReserved1", wintypes.DWORD),
                ("dmReserved2", wintypes.DWORD), ("dmPanningWidth", wintypes.DWORD), ("dmPanningHeight", wintypes.DWORD)]


class DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32), ("DeviceString", wintypes.WCHAR * 128),
                ("StateFlags", wintypes.DWORD), ("DeviceID", wintypes.WCHAR * 128), ("DeviceKey", wintypes.WCHAR * 128)]


def displays():
    if not IS_WIN:
        return {"displays": []}
    from . import display_identity
    identities = display_identity.active_targets()
    u32 = ctypes.windll.user32
    out, i = [], 0
    while True:
        dd = DISPLAY_DEVICEW(); dd.cb = ctypes.sizeof(dd)
        if not u32.EnumDisplayDevicesW(None, i, ctypes.byref(dd), 0):
            break
        i += 1
        if not dd.StateFlags & 0x1:  # DISPLAY_DEVICE_ATTACHED_TO_DESKTOP
            continue
        cur = DEVMODEW(); cur.dmSize = ctypes.sizeof(cur)
        if not u32.EnumDisplaySettingsW(dd.DeviceName, -1, ctypes.byref(cur)):  # ENUM_CURRENT_SETTINGS
            continue
        rates, m = set(), 0
        while True:
            dm = DEVMODEW(); dm.dmSize = ctypes.sizeof(dm)
            if not u32.EnumDisplaySettingsW(dd.DeviceName, m, ctypes.byref(dm)):
                break
            m += 1
            if dm.dmPelsWidth == cur.dmPelsWidth and dm.dmPelsHeight == cur.dmPelsHeight and dm.dmDisplayFrequency > 1:
                rates.add(int(dm.dmDisplayFrequency))
        monitors, index = [], 0
        while index < 256:
            mon = DISPLAY_DEVICEW(); mon.cb = ctypes.sizeof(mon)
            if not u32.EnumDisplayDevicesW(dd.DeviceName, index, ctypes.byref(mon), 1):  # EDD_GET_DEVICE_INTERFACE_NAME
                break
            index += 1
            if mon.StateFlags & 1:  # DISPLAY_DEVICE_ACTIVE, excludes stale disconnected monitor children.
                monitors.append({"device_path": mon.DeviceID, "description": mon.DeviceString})
        identity = display_identity.describe_display(dd.DeviceName, monitors, identities["targets"])
        out.append({"device": dd.DeviceName, **identity, "gpu": dd.DeviceString,
                    "width": cur.dmPelsWidth, "height": cur.dmPelsHeight, "hz": int(cur.dmDisplayFrequency),
                    "rates": sorted(rates), "primary": bool(dd.StateFlags & 0x4)})
    return {"displays": sorted(out, key=lambda d: not d["primary"]), "identity_query_status": identities["status"]}


def set_refresh(device: str, hz: int):
    if not IS_WIN:
        raise HTTPException(400, "Windows only")
    d = next((x for x in displays()["displays"] if x["device"] == device), None)
    if not d or int(hz) not in d["rates"]:
        raise HTTPException(400, "That monitor doesn't support that refresh rate at its current resolution")
    u32 = ctypes.windll.user32
    dm = DEVMODEW(); dm.dmSize = ctypes.sizeof(dm)
    u32.EnumDisplaySettingsW(device, -1, ctypes.byref(dm))
    dm.dmDisplayFrequency = int(hz); dm.dmFields = 0x400000  # DM_DISPLAYFREQUENCY
    r = u32.ChangeDisplaySettingsExW(device, ctypes.byref(dm), None, 0x1, None)  # CDS_UPDATEREGISTRY
    if r != 0:
        raise HTTPException(500, f"Windows refused the change (code {r})")
    return displays()


# ----------------------------------------------------------------------------- Windows power mode slider (laptops and desktops)
OVERLAYS = {
    "efficiency": "961cc777-2547-4f9d-8174-7d86181b8a7a",
    "balanced": "00000000-0000-0000-0000-000000000000",
    "performance": "ded574b5-45a0-4f42-8737-46345c09c238",
}


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, s):
        h = s.replace("-", "")
        g = cls(int(h[:8], 16), int(h[8:12], 16), int(h[12:16], 16))
        for i in range(8):
            g.Data4[i] = int(h[16 + i * 2:18 + i * 2], 16)
        return g

    def text(self):
        return f"{self.Data1:08x}-{self.Data2:04x}-{self.Data3:04x}-" + "".join(f"{b:02x}" for b in self.Data4[:2]) + "-" + "".join(f"{b:02x}" for b in self.Data4[2:])


def power_mode() -> dict:
    if not IS_WIN:
        return {"mode": None, "available": False}
    try:
        fn = ctypes.windll.powrprof.PowerGetEffectiveOverlayScheme
    except AttributeError:
        return {"mode": None, "available": False}
    g = _GUID()
    if fn(ctypes.byref(g)) != 0:
        return {"mode": None, "available": False}
    cur = g.text()
    mode = next((k for k, v in OVERLAYS.items() if v == cur), "balanced")
    return {"mode": mode, "available": True}


def set_power_mode(mode: str) -> dict:
    if mode not in OVERLAYS:
        raise ValueError("Unknown power mode")
    fn = ctypes.windll.powrprof.PowerSetActiveOverlayScheme
    if fn(ctypes.byref(_GUID.parse(OVERLAYS[mode]))) != 0:
        raise RuntimeError("Windows didn't accept that power mode")
    return power_mode()


# ----------------------------------------------------------------------------- LibreHardwareMonitor one-click setup
LHM_CFG = """<?xml version="1.0" encoding="utf-8"?>
<configuration><appSettings><add key="runWebServerMenuItem" value="true" /><add key="listenerPort" value="8085" />
<add key="startMinMenuItem" value="true" /><add key="minTrayMenuItem" value="true" /></appSettings></configuration>"""


def setup_lhm():
    if not IS_WIN:
        raise HTTPException(400, "Windows only")
    base = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages")
    def find():
        if os.path.isdir(base):
            for d in os.listdir(base):
                if d.lower().startswith("librehardwaremonitor"):
                    p = os.path.join(base, d, "LibreHardwareMonitor.exe")
                    if os.path.exists(p):
                        return p
        return None
    exe = find()
    if not exe:
        r = _run(["winget", "install", "--id", "LibreHardwareMonitor.LibreHardwareMonitor", "-e", "--silent",
                  "--accept-source-agreements", "--accept-package-agreements", "--disable-interactivity"], timeout=300)
        exe = find()
        if not exe:
            raise HTTPException(500, "Install didn't finish. Get it from github.com/LibreHardwareMonitor/LibreHardwareMonitor")
    cfg = os.path.join(os.path.dirname(exe), "LibreHardwareMonitor.config")
    if not os.path.exists(cfg):
        open(cfg, "w", encoding="utf-8").write(LHM_CFG)
    # LibreHardwareMonitor needs admin for sensor drivers: Windows shows one approval prompt
    ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, None, None, 7)
    return {"ok": True, "path": exe}


def register(app):
    @app.get("/api/processes")
    async def _procs():
        import asyncio
        return await asyncio.to_thread(top_apps)

    @app.get("/api/power/plans")
    async def _plans():
        return power_plans()

    @app.post("/api/power/plans")
    async def _set_plan(body: dict):
        return set_power_plan(body.get("guid", ""))

    @app.get("/api/display")
    def _disp():
        return displays()

    @app.post("/api/display")
    def _set_disp(body: dict):
        return set_refresh(body.get("device", ""), int(body.get("hz", 0)))

    @app.get("/api/power/mode")
    async def _pmode():
        return power_mode()

    @app.post("/api/power/mode")
    async def _set_pmode(body: dict):
        try:
            return set_power_mode(str(body.get("mode", "")))
        except (ValueError, RuntimeError, OSError) as e:
            raise HTTPException(400, str(e))

    @app.post("/api/setup/lhm")
    async def _lhm():
        import asyncio
        return await asyncio.to_thread(setup_lhm)


if __name__ == "__main__":
    import json
    print(json.dumps(power_plans()))
    print(json.dumps(displays()))
    psutil.cpu_percent(None); [p.cpu_percent(None) for p in psutil.process_iter()]; time.sleep(1)
    print(json.dumps(top_apps(5)["by_cpu"]))
