#!/usr/bin/env python3
"""
Ultra Suite Laptop Pack (add-on for PC Sentinel).

Installs on top of Ultra Suite. When present, PC Sentinel gains:

  GET  /api/laptop/battery          health (full vs design capacity), wear, charge cycles, charge/discharge
                                    rate in watts, voltage, time left, chemistry, maker
  GET  /api/laptop/battery/history  capacity history from Windows' battery report (how health changed over time)
  GET  /api/laptop/brightness       built-in screen brightness
  POST /api/laptop/brightness       {"percent": 0-100}

Everything uses what Windows already provides (WMI root\\wmi battery classes, powercfg, powrprof),
so it works on any brand: ASUS, Lenovo, Dell, HP, MSI, Razer, Surface. No admin rights needed.
"""
import ctypes
import json
import os
import re
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET

PACK_VERSION = "1.0.0"
NO_WINDOW = 0x08000000
IS_WIN = os.name == "nt"
_cache = {}


def _ps(cmd: str, timeout=20) -> str:
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd], capture_output=True, text=True,
                       timeout=timeout, creationflags=NO_WINDOW if IS_WIN else 0)
    return r.stdout.strip()


def _cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    return val


# ----------------------------------------------------------------------------- battery
_BAT_PS = r"""
$o=@{}
try { $o.static=@(Get-CimInstance -Namespace root\wmi -ClassName BatteryStaticData -ErrorAction Stop | Select-Object DesignedCapacity,ManufactureName,DeviceName,Chemistry,SerialNumber) } catch {}
try { $o.full=@(Get-CimInstance -Namespace root\wmi -ClassName BatteryFullChargedCapacity -ErrorAction Stop | Select-Object FullChargedCapacity) } catch {}
try { $o.cycle=@(Get-CimInstance -Namespace root\wmi -ClassName BatteryCycleCount -ErrorAction Stop | Select-Object CycleCount) } catch {}
try { $o.status=@(Get-CimInstance -Namespace root\wmi -ClassName BatteryStatus -ErrorAction Stop | Select-Object ChargeRate,DischargeRate,RemainingCapacity,Voltage,PowerOnline,Charging,Discharging,Critical) } catch {}
try { $o.win=@(Get-CimInstance Win32_Battery -ErrorAction Stop | Select-Object EstimatedChargeRemaining,EstimatedRunTime,BatteryStatus,Name) } catch {}
$o | ConvertTo-Json -Depth 4 -Compress
"""
CHEM = {0x4E4F494C: "Lithium-ion", 0x4C4F4950: "Lithium polymer", 0x4E694D48: "Nickel metal hydride", 0x5043624C: "Lead acid"}


def _sum(rows, key):
    vals = [r.get(key) for r in rows or [] if isinstance(r.get(key), (int, float))]
    return sum(vals) if vals else None


def battery() -> dict:
    if not IS_WIN:
        return {"has_battery": False}
    try:
        d = json.loads(_ps(_BAT_PS) or "{}")
    except Exception:
        d = {}
    win = d.get("win") or []
    if not win and not d.get("status"):
        return {"has_battery": False, "pack": PACK_VERSION}
    design = _sum(d.get("static"), "DesignedCapacity")
    full = _sum(d.get("full"), "FullChargedCapacity")
    remaining = _sum(d.get("status"), "RemainingCapacity")
    st = (d.get("status") or [{}])[0]
    charge_mw = _sum(d.get("status"), "ChargeRate") or 0
    discharge_mw = _sum(d.get("status"), "DischargeRate") or 0
    rate_w = (charge_mw / 1000) if charge_mw else (-discharge_mw / 1000 if discharge_mw else 0.0)
    charge_pct = win[0].get("EstimatedChargeRemaining") if win else None
    if charge_pct is None and remaining and full:
        charge_pct = round(remaining / full * 100)
    run = win[0].get("EstimatedRunTime") if win else None
    minutes_left = None
    if discharge_mw and remaining:
        minutes_left = round(remaining / discharge_mw * 60)
    elif isinstance(run, int) and 0 < run < 71582788:  # Windows uses 71582788 for "plugged in / unknown"
        minutes_left = run
    minutes_to_full = round((full - remaining) / charge_mw * 60) if charge_mw and full and remaining is not None and full > remaining else None
    static = (d.get("static") or [{}])[0]
    health = round(full / design * 100, 1) if design and full else None
    cycles = _sum(d.get("cycle"), "CycleCount")
    state = "Charging" if st.get("Charging") else "On battery" if st.get("Discharging") else "Plugged in" if st.get("PowerOnline") else None
    return {
        "has_battery": True, "pack": PACK_VERSION,
        "charge_pct": charge_pct, "state": state, "plugged_in": bool(st.get("PowerOnline")),
        "design_mwh": design, "full_mwh": full, "remaining_mwh": remaining,
        "health_pct": min(100.0, health) if health else None, "wear_pct": round(max(0.0, 100 - health), 1) if health else None,
        "cycles": cycles if cycles else None,
        "rate_w": round(rate_w, 1), "voltage_v": round(st["Voltage"] / 1000, 2) if st.get("Voltage") else None,
        "minutes_left": minutes_left if not charge_mw else None, "minutes_to_full": minutes_to_full,
        "maker": (static.get("ManufactureName") or "").strip() or None, "model": (static.get("DeviceName") or "").strip() or None,
        "chemistry": CHEM.get(static.get("Chemistry") or 0), "verdict": _verdict(health, cycles),
    }


def _verdict(health, cycles):
    if health is None:
        return "Windows doesn't report this battery's capacity."
    if health >= 90:
        return "Like new."
    if health >= 80:
        return "Healthy. Normal wear for its age."
    if health >= 65:
        return "Worn. You'll notice shorter battery life."
    return "Heavily worn. A replacement would bring back full battery life."


def battery_history() -> dict:
    """Parses `powercfg /batteryreport /xml` (no admin needed) for capacity over time."""
    if not IS_WIN:
        return {"history": []}
    out = os.path.join(tempfile.gettempdir(), "ultrasuite_battery_report.xml")
    try:
        subprocess.run(["powercfg", "/batteryreport", "/xml", "/output", out], capture_output=True, timeout=40, creationflags=NO_WINDOW)
        root = ET.parse(out).getroot()
    except Exception:
        return {"history": []}
    ns = {"b": root.tag.split("}")[0].strip("{")} if root.tag.startswith("{") else {}
    q = (lambda p: root.findall(p, ns)) if ns else root.findall
    rows = []
    for e in q(".//b:HistoryEntry" if ns else ".//HistoryEntry"):
        a = e.attrib
        try:
            full, design = int(a.get("FullChargeCapacity", 0)), int(a.get("DesignCapacity", 0))
        except ValueError:
            continue
        if full and design:
            rows.append({"start": a.get("StartDate"), "end": a.get("EndDate"), "full_mwh": full, "design_mwh": design,
                         "health_pct": round(full / design * 100, 1), "cycles": int(a.get("CycleCount") or 0) or None})
    return {"history": rows[-52:]}


# ----------------------------------------------------------------------------- built-in screen brightness
def brightness() -> dict:
    if not IS_WIN:
        return {"available": False}
    v = _ps("try { (Get-CimInstance -Namespace root\\wmi -ClassName WmiMonitorBrightness -ErrorAction Stop | Select-Object -First 1).CurrentBrightness } catch { '' }")
    return {"available": v.isdigit(), "percent": int(v) if v.isdigit() else None}


def set_brightness(percent: int) -> dict:
    p = max(0, min(100, int(percent)))
    _ps(f"Get-CimInstance -Namespace root\\wmi -ClassName WmiMonitorBrightnessMethods | Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{{Timeout=1;Brightness={p}}} | Out-Null")
    return brightness()


# ----------------------------------------------------------------------------- routes
def register(app):
    import asyncio
    from fastapi import HTTPException

    @app.get("/api/laptop/battery")
    async def _bat():
        return await asyncio.to_thread(_cached, "bat", 4, battery)

    @app.get("/api/laptop/battery/history")
    async def _hist():
        return await asyncio.to_thread(_cached, "hist", 3600, battery_history)

    @app.get("/api/laptop/brightness")
    async def _br():
        return await asyncio.to_thread(_cached, "br", 2, brightness)

    @app.post("/api/laptop/brightness")
    async def _set_br(body: dict):
        _cache.pop("br", None)
        return await asyncio.to_thread(set_brightness, body.get("percent", 50))

    @app.get("/api/laptop/about")
    async def _about():
        return {"pack": PACK_VERSION}


if __name__ == "__main__":
    print(json.dumps(battery(), indent=1))
    print(json.dumps(brightness()))
    print(len(battery_history()["history"]), "history rows")
