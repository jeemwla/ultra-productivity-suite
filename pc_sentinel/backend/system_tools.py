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
    """Windows counter utilization: busiest physical engine across reported GPUs per process.

    https://devblogs.microsoft.com/directx/gpus-in-the-task-manager/
    """
    global _gpu_pdh
    import math
    began = time.time()
    unavailable = {"processes": {}, "engine_complete": False, "memory_complete": False, "observed_at": began}
    if not IS_WIN:
        return unavailable
    try:
        if _gpu_pdh is None:
            from .native_sensors import Pdh
            _gpu_pdh = Pdh([r"\GPU Engine(*)\Utilization Percentage", r"\GPU Process Memory(*)\Dedicated Usage"])
        data = _gpu_pdh.collect()
    except Exception:
        return unavailable
    engine_path, memory_path = r"\GPU Engine(*)\Utilization Percentage", r"\GPU Process Memory(*)\Dedicated Usage"
    statuses = getattr(_gpu_pdh, "last_status", {})
    engine_complete = statuses.get(engine_path) == "complete"
    memory_complete = statuses.get(memory_path) == "complete"
    eng = {}
    for inst, v in (data.get(r"\GPU Engine(*)\Utilization Percentage") or {}).items():
        m = re.match(r"pid_(\d+)_(.*engtype_.+)$", inst)
        if not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
            engine_complete = False
            continue
        if m:
            k = (int(m.group(1)), m.group(2))
            eng[k] = eng.get(k, 0.0) + v
        elif inst != "_Total":
            engine_complete = False
    out = {}
    for (pid, _t), v in eng.items():
        out.setdefault(pid, {"gpu": None, "vram_mb": None, "engines": {}})
        out[pid]["gpu"] = max(out[pid]["gpu"] or 0.0, v)
        out[pid]["engines"][_t] = v
    for inst, v in (data.get(r"\GPU Process Memory(*)\Dedicated Usage") or {}).items():
        m = re.match(r"pid_(\d+)_", inst)
        if not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
            memory_complete = False
            continue
        if m:
            row = out.setdefault(int(m.group(1)), {"gpu": None, "vram_mb": None, "engines": {}})
            row["vram_mb"] = (row["vram_mb"] or 0.0) + v / 1048576
        elif inst != "_Total":
            memory_complete = False
    for row in out.values():
        row["gpu_complete"] = engine_complete
    return {"processes": out, "engine_complete": engine_complete,
            "memory_complete": memory_complete, "observed_at": began}


def _run(args, timeout=20):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW if IS_WIN else 0)


# ----------------------------------------------------------------------------- top apps
def top_apps(limit=8, *, all_limit=0, include_details=False, group_id=None):
    """Cache full group rankings, then apply each caller's response limits.

    CPU percentages use total machine capacity. First observations or an unknown
    creation identity have no CPU interval yet, retained explicitly in status
    fields. Windows path normalization is lexical; it does not verify signatures
    or collapse filesystem aliases. Unusable paths remain per-process groups.
    """
    import copy
    import hashlib
    import math
    import ntpath
    import threading
    from .process_names import describe_process, _absolute_windows_path

    if type(limit) is not int or not 1 <= limit <= 512 or type(all_limit) is not int or not 0 <= all_limit <= 512:
        raise ValueError("Top limit must be 1..512 and full-list limit 0..512.")

    def number(value, default=0.0):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return default
        try:
            value = float(value)
        except (ValueError, OverflowError):
            return default
        return value if math.isfinite(value) and value >= 0 else default

    # Concurrent to_thread requests share sampling state but get independent
    # response copies and independently sliced limits.
    with _proc_cache.setdefault("lock", threading.RLock()):
        now = time.monotonic()
        cached = _proc_cache.get("data")
        if cached is None or "all_apps" not in cached or not 0 <= now - _proc_cache["at"] < 2:
            ncpu = max(1, psutil.cpu_count() or 1)
            apps, retained, seen_pids = {}, set(), set()
            gpu_sample = _gpu_by_pid()
            envelope = isinstance(gpu_sample, dict) and "processes" in gpu_sample
            gpu = gpu_sample["processes"] if envelope else gpu_sample
            engine_complete = envelope and gpu_sample.get("engine_complete") is True
            memory_complete = envelope and gpu_sample.get("memory_complete") is True
            gpu_observed_at = number(gpu_sample.get("observed_at"), None) if envelope else None
            # New process_iter objects provide fresh creation times. Our own
            # records retain CPU intervals only across a confirmed lifetime.
            clear = getattr(psutil.process_iter, "cache_clear", None)
            if callable(clear):
                clear()
            for process in psutil.process_iter(["name", "memory_info", "exe", "create_time"], ad_value=None):
                pid = process.pid
                if pid in seen_pids:
                    continue
                seen_pids.add(pid)
                try:
                    info = process.info
                    raw_name = info.get("name")
                    if isinstance(raw_name, str) and raw_name.lower() in ("system idle process", "idle", "system", "registry", "memory compression"):
                        continue
                    exe = info.get("exe")
                    path = _absolute_windows_path(exe)
                    raw_name = raw_name if isinstance(raw_name, str) and raw_name else (ntpath.basename(path) if path else "Unknown process")
                    created = number(info.get("create_time"), None)
                    previous = _procs.get(pid)
                    same_lifetime = (created is not None and isinstance(previous, dict)
                                     and previous.get("created") == created)
                    tracked = previous["process"] if same_lifetime else process
                    cpu, cpu_status = 0.0, "sampled"
                    try:
                        reading = tracked.cpu_percent(None)
                        if same_lifetime:
                            measured_cpu = number(reading, None)
                            if measured_cpu is None:
                                cpu_status = "unavailable"
                            else:
                                cpu = min(100.0, measured_cpu / ncpu)
                        else:
                            cpu_status = "warming_up" if created is not None else "identity_unavailable"
                    except psutil.AccessDenied:
                        cpu_status = "unavailable"
                    _procs[pid] = {"process": tracked, "created": created}
                    retained.add(pid)
                    rss = number(getattr(info.get("memory_info"), "rss", None), None)
                    mem = rss / 1048576 if rss is not None else None
                    reading = gpu.get(pid) or {}
                    gpu_value = number(reading.get("gpu"), None)
                    observable_at_gpu_sample = created is not None and gpu_observed_at is not None and created <= gpu_observed_at
                    if envelope and not observable_at_gpu_sample:
                        gpu_value = None
                    no_engine = gpu_value is None and engine_complete and observable_at_gpu_sample
                    if gpu_value is None and engine_complete and observable_at_gpu_sample:
                        gpu_value = 0.0
                    gpu_pct = min(100.0, gpu_value) if gpu_value is not None else None
                    child_gpu_complete = gpu_pct is not None and (engine_complete if envelope else True)
                    vram = number(reading.get("vram_mb"), None)
                    if envelope and not observable_at_gpu_sample:
                        vram = None
                    if vram is None and memory_complete and observable_at_gpu_sample:
                        vram = 0.0
                    detail = describe_process({"name": raw_name, "exe": exe if isinstance(exe, str) else None,
                                               "file_description": info.get("file_description"), "pid": pid,
                                               "create_time": created, "canonical_exe": path,
                                               "cpu": round(cpu, 1) if cpu_status == "sampled" else None,
                                               "mem_mb": round(mem) if mem is not None else None,
                                               "gpu": round(gpu_pct, 1) if gpu_pct is not None else None,
                                               "vram_mb": round(vram) if vram is not None else None,
                                               "gpu_sample_status": "no_gpu_engine_instance" if no_engine else "reported" if gpu_pct is not None else "unavailable_or_new_process",
                                               "gpu_complete": child_gpu_complete,
                                               "cpu_sample_status": cpu_status,
                                               "creation_identity_available": created is not None})
                    detail["name"] = re.sub(r"\.exe$", "", raw_name, flags=re.I)
                    key = ("exe", path) if path else ("process", pid, created)
                    if key not in apps:
                        label = describe_process({"name": raw_name, "exe": exe if isinstance(exe, str) else None,
                                                  "file_description": info.get("file_description")})
                        label["name"] = re.sub(r"\.exe$", "", raw_name, flags=re.I)
                        stable_group_id = "exe:" + hashlib.sha256(path.encode("utf-8")).hexdigest() if path else f"pid:{pid}:{created if created is not None else 'unverified'}"
                        apps[key] = {**label, "canonical_exe": path, "group_id": stable_group_id,
                                     "grouping_source": "canonical_executable_path" if path else "individual_process",
                                     "cpu": 0.0, "mem_mb": 0.0, "gpu": 0.0, "vram_mb": 0.0,
                                     "count": 0, "pids": [], "processes": [], "cpu_complete": True,
                                     "mem_complete": True, "gpu_complete": True,
                                     "_gpu_engine_sums": {}, "_gpu_unmapped": False}
                    group = apps[key]
                    group["cpu"] += cpu
                    group["mem_mb"] += mem if mem is not None else 0
                    group["gpu"] += gpu_pct if gpu_pct is not None else 0
                    engines = (reading.get("engines") or {}) if not envelope or observable_at_gpu_sample else {}
                    for engine, value in engines.items():
                        valid = number(value, None)
                        if valid is not None:group["_gpu_engine_sums"][engine] = group["_gpu_engine_sums"].get(engine, 0.0) + valid
                    if gpu_pct and not engines:group["_gpu_unmapped"] = True
                    group["vram_mb"] += vram if vram is not None else 0
                    group["count"] += 1
                    group["pids"].append(pid)
                    group["processes"].append(detail)
                    group["cpu_complete"] = group["cpu_complete"] and cpu_status == "sampled"
                    group["mem_complete"] = group["mem_complete"] and mem is not None
                    group["gpu_complete"] = group["gpu_complete"] and child_gpu_complete
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            for pid in list(_procs):
                if pid not in retained:
                    del _procs[pid]
            rows = list(apps.values())
            for row in rows:
                if row["_gpu_engine_sums"] and not row["_gpu_unmapped"]:
                    row["gpu"] = max(row["_gpu_engine_sums"].values())
                row["gpu_percent_basis"] = "busiest_reported_physical_engine" if not row["_gpu_unmapped"] else "sum_of_process_reports"
                row["cpu_threshold_eligible"] = row["cpu"] >= 0.5
                row["gpu_threshold_eligible"] = row["gpu"] >= 0.5
                row["cpu"] = round(min(100.0, row["cpu"]), 1)
                row["mem_mb"] = round(row["mem_mb"])
                row["gpu"] = round(min(100.0, row["gpu"]), 1)
                row["vram_mb"] = round(row["vram_mb"])
                row["pids"].sort()
                row["processes"].sort(key=lambda child: child["pid"])
            tie = lambda row: row["group_id"]
            all_rows = sorted(rows, key=lambda row: (-row["cpu"], tie(row)))
            cached = {"all_apps": all_rows,
                      "by_cpu": [row for row in all_rows if row["cpu_threshold_eligible"]],
                      "by_mem": sorted(rows, key=lambda row: (-row["mem_mb"], tie(row))),
                      "by_gpu": sorted([row for row in rows if row["gpu_threshold_eligible"]],
                                       key=lambda row: (-row["gpu"], -row["vram_mb"], tie(row))),
                      "gpu_available": bool(engine_complete or any(number(v.get("gpu"), None) is not None for v in gpu.values())),
                      "gpu_collection_status": "complete" if engine_complete else "partial" if gpu else "unavailable",
                      "total_processes": sum(row["count"] for row in rows),
                      "total_groups": len(rows), "observed_at": time.time(),
                      "cpu_hidden_groups": sum(not row["cpu_threshold_eligible"] for row in rows),
                      "cpu_threshold_pct": 0.5, "gpu_threshold_pct": 0.5,
                      "cpu_percent_basis": "whole_machine",
                      "memory_basis": "summed_process_working_sets_may_include_shared_pages"}
            _proc_cache.update(at=time.monotonic(), data=cached)
        response = {**cached, "by_cpu": cached["by_cpu"][:limit] if group_id is None else [],
                    "by_mem": cached["by_mem"][:limit] if group_id is None else [],
                    "by_gpu": cached["by_gpu"][:limit] if group_id is None else [],
                    "all_apps": cached["all_apps"][:all_limit] if group_id is None else [r for r in cached["all_apps"] if r["group_id"] == group_id],
                    "all_included": all_limit > 0 or group_id is not None,
                    "truncated": bool(all_limit and cached["total_groups"] > all_limit)}
        # Copy only requested rows; compact polling never copies every child PID.
        memo = {}
        for key in ("by_cpu", "by_mem", "by_gpu", "all_apps"):
            response[key] = [memo.setdefault(id(row), {k:v for k,v in row.items() if not k.startswith("_") and (include_details or k not in ("processes", "pids"))}) for row in response[key]]
        response = copy.deepcopy(response)
    # Version-resource file I/O is outside the shared PDH/process sampling lock.
    from .process_metadata import file_description
    seen, reads = set(), 0
    for key in ("by_cpu", "by_mem", "by_gpu", "all_apps"):
        for row in response[key]:
            if id(row) in seen:
                continue
            seen.add(id(row))
            if row.get("name_source") == "windows_glossary" or not row.get("canonical_exe"):
                continue
            description, performed_read = file_description(row["canonical_exe"], cached_only=reads >= 24, with_status=True)
            reads += int(performed_read)
            if description:
                label = describe_process({**row, "name": row.get("raw_filename") or row["name"], "file_description": description})
                label["name"] = row["name"]
                row.update(label)
    return response


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
    async def _procs(limit: int = 8, all_limit: int = 0):
        import asyncio
        if not 1 <= limit <= 512 or not 0 <= all_limit <= 512:
            raise HTTPException(422, "Top limit must be 1..512 and full-list limit 0..512.")
        return await asyncio.to_thread(top_apps, limit, all_limit=all_limit)

    @app.get("/api/processes/{group_id}")
    async def _process_group(group_id: str):
        import asyncio
        result = await asyncio.to_thread(top_apps, 1, include_details=True, group_id=group_id)
        if not result["all_apps"]:
            raise HTTPException(404, "This process group is no longer present.")
        return {"group": result["all_apps"][0], "observed_at": result["observed_at"]}

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
