#!/usr/bin/env python3
"""
PC Sentinel battery features (the Battery page, shown only on PCs with a battery).

Built into PC Sentinel (it used to be a separate "Laptop Pack" add-on). Routes:

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


_NO_TIME = 0xFFFFFFFF
MAX_SANE_MW = 400_000  # rates above 400 W are firmware noise (unsigned wrap), not a reading


def power_status():
    """GetSystemPowerStatus: AC line, charging flag, percent and seconds left. Unknown values are None."""
    out = {"ac": None, "charging": None, "no_battery": None, "percent": None, "seconds_left": None}
    if not IS_WIN:
        return out
    from ctypes import wintypes

    class SPS(ctypes.Structure):
        _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte), ("BatteryLifePercent", ctypes.c_ubyte),
                    ("SystemStatusFlag", ctypes.c_ubyte), ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD)]
    try:
        s = SPS()
        if not ctypes.WinDLL("kernel32").GetSystemPowerStatus(ctypes.byref(s)):
            return out
    except (OSError, AttributeError):
        return out
    out["ac"] = {0: False, 1: True}.get(s.ACLineStatus)
    if s.BatteryFlag != 255:
        out["no_battery"] = bool(s.BatteryFlag & 128)
        out["charging"] = bool(s.BatteryFlag & 8)
    out["percent"] = s.BatteryLifePercent if s.BatteryLifePercent <= 100 else None
    out["seconds_left"] = s.BatteryLifeTime if s.BatteryLifeTime != _NO_TIME else None
    return out


def _rate(rows, key):
    """Summed mW for one rate field, or None when Windows doesn't report it (or reports nonsense)."""
    vals = [r.get(key) for r in rows or [] if isinstance(r.get(key), (int, float)) and 0 <= r.get(key) <= MAX_SANE_MW]
    return sum(vals) if vals else None


REPORT_CHEM = {"LION": "Lithium-ion", "LI-I": "Lithium-ion", "LIP": "Lithium polymer", "LIPO": "Lithium polymer",
               "NIMH": "Nickel metal hydride", "PBAC": "Lead acid"}


def build_battery(d: dict, sps: dict = None, report: dict = None) -> dict:
    """Merges WMI (root\\wmi battery classes, Win32_Battery), GetSystemPowerStatus and the battery report.
    Values Windows doesn't report stay None so the page can say "not reported"."""
    sps = sps or {}
    report = report or {}
    rep_bats = report.get("batteries") or []
    win = d.get("win") or []
    if not win and not d.get("status") and not rep_bats and sps.get("no_battery") is not False:
        return {"has_battery": False, "pack": PACK_VERSION}
    status_rows = d.get("status") or []
    st = status_rows[0] if status_rows else {}
    design = _sum(d.get("static"), "DesignedCapacity") or _sum(rep_bats, "design_mwh")
    full = _sum(d.get("full"), "FullChargedCapacity") or _sum(rep_bats, "full_mwh")
    remaining = _sum(status_rows, "RemainingCapacity")
    charge_mw, discharge_mw = _rate(status_rows, "ChargeRate"), _rate(status_rows, "DischargeRate")
    if sps.get("ac") is not None:
        plugged = sps["ac"]
    elif st.get("PowerOnline") is not None:
        plugged = bool(st["PowerOnline"])
    else:
        plugged = None
    charging = bool(st.get("Charging")) or bool(sps.get("charging"))
    if charge_mw is None and discharge_mw is None:
        rate_w = None
    elif charging:
        rate_w = charge_mw / 1000 if charge_mw is not None else None
    elif discharge_mw:
        rate_w = -discharge_mw / 1000
    else:
        rate_w = (charge_mw or 0) / 1000
    charge_pct = win[0].get("EstimatedChargeRemaining") if win else None
    if charge_pct is None:
        charge_pct = sps.get("percent")
    if charge_pct is None and remaining and full:
        charge_pct = round(remaining / full * 100)
    minutes_left = None
    if not charging and plugged is not True:
        if discharge_mw and remaining:
            minutes_left = round(remaining / discharge_mw * 60)
        elif isinstance(sps.get("seconds_left"), int):
            minutes_left = round(sps["seconds_left"] / 60)
        else:
            run = win[0].get("EstimatedRunTime") if win else None
            if isinstance(run, int) and 0 < run < 71582788:  # 71582788 means "plugged in / unknown"
                minutes_left = run
    minutes_to_full = (round((full - remaining) / charge_mw * 60)
                       if charging and charge_mw and full and remaining is not None and full > remaining else None)
    static = (d.get("static") or [{}])[0]
    rep0 = rep_bats[0] if rep_bats else {}
    health = round(full / design * 100, 1) if design and full else None
    cycles = _sum(d.get("cycle"), "CycleCount") or _sum(rep_bats, "cycles")
    if charging:
        state = "Charging"
    elif plugged is True:
        state = "Fully charged" if charge_pct is not None and charge_pct >= 99 else "Plugged in, not charging"
    elif plugged is False or st.get("Discharging"):
        state = "On battery"
    else:
        state = None
    chem = CHEM.get(static.get("Chemistry") or 0) or REPORT_CHEM.get((rep0.get("chemistry") or "").upper()) or rep0.get("chemistry")
    return {
        "has_battery": True, "pack": PACK_VERSION,
        "charge_pct": charge_pct, "state": state, "plugged_in": plugged, "charging": charging,
        "design_mwh": design, "full_mwh": full, "remaining_mwh": remaining,
        "health_pct": min(100.0, health) if health else None, "wear_pct": round(max(0.0, 100 - health), 1) if health else None,
        "cycles": cycles if cycles else None,
        "rate_w": round(rate_w, 1) if rate_w is not None else None,
        "charge_w": round(charge_mw / 1000, 1) if charging and charge_mw is not None else None,
        "discharge_w": round(discharge_mw / 1000, 1) if not charging and discharge_mw is not None else None,
        "voltage_v": round(st["Voltage"] / 1000, 2) if st.get("Voltage") else None,
        "minutes_left": minutes_left, "minutes_to_full": minutes_to_full,
        "maker": (static.get("ManufactureName") or "").strip() or rep0.get("maker") or None,
        "model": (static.get("DeviceName") or "").strip() or rep0.get("id") or None,
        "chemistry": chem or None, "verdict": _verdict(health, cycles),
        "daily_use": report.get("daily_use"), "platform_role": report.get("platform_role"),
    }


def battery() -> dict:
    if not IS_WIN:
        return {"has_battery": False}
    try:
        d = json.loads(_ps(_BAT_PS) or "{}")
    except Exception:
        d = {}
    sps = power_status()
    maybe = d.get("win") or d.get("status") or sps.get("no_battery") is False
    report = _cached("report", 1800, battery_report) if maybe else {}
    return build_battery(d, sps, report)


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


# ----------------------------------------------------------------------------- Windows battery report (powercfg /batteryreport /XML)
def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _int(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _when(v):
    from datetime import datetime, timezone
    if not v:
        return None
    try:
        t = datetime.fromisoformat(str(v).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


_DUR = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$")


def _duration_h(v):
    m = _DUR.match(str(v or "").strip())
    if not m or not any(m.groups()):
        return None
    d, h, mi, s = (float(x) if x else 0.0 for x in m.groups())
    return d * 24 + h + mi / 60 + s / 3600


def _children(node, name):
    return [c for c in node if _local(c.tag) == name] if node is not None else []


def _child(node, name):
    found = _children(node, name)
    return found[0] if found else None


def _text(node, name):
    c = _child(node, name)
    return c.text.strip() if c is not None and c.text and c.text.strip() else None


def daily_use(entries, start, end):
    """Average battery drain in percent of a full charge per day, from RecentUsage capacity samples.
    Only drops measured while on battery count; charging and plugged-in time add nothing."""
    if not start or not end or end <= start:
        return None
    days = (end - start).total_seconds() / 86400
    if days < 0.5:
        return None
    rows = sorted((e for e in entries if e["at"] is not None), key=lambda e: e["at"])
    if not rows or not any(e["full"] for e in rows):
        return None  # capacity never reported (desktop or unsupported firmware)
    used_pct, battery_h, samples = 0.0, 0.0, 0
    for a, b in zip(rows, rows[1:]):
        if a["ac"] is not False:
            continue
        battery_h += max(0.0, (b["at"] - a["at"]).total_seconds() / 3600)
        full = a["full"] or b["full"]
        if a["charge"] and b["charge"] and full and b["charge"] < a["charge"]:
            used_pct += (a["charge"] - b["charge"]) / full * 100
            samples += 1
    return {"pct_per_day": round(used_pct / days, 1), "on_battery_hours_per_day": round(battery_h / days, 1),
            "days": round(days, 1), "drain_samples": samples}


def parse_battery_report(text) -> dict:
    """Parses the XML from `powercfg /batteryreport /XML`. Missing fields stay None; nothing is estimated."""
    root = ET.fromstring(text)
    info, system = _child(root, "ReportInformation"), _child(root, "SystemInformation")
    batteries = []
    for b in _children(_child(root, "Batteries"), "Battery"):
        design, full, cycles = _int(_text(b, "DesignCapacity")), _int(_text(b, "FullChargeCapacity")), _int(_text(b, "CycleCount"))
        batteries.append({"id": _text(b, "Id"), "maker": _text(b, "Manufacturer"), "chemistry": _text(b, "Chemistry"),
                          "design_mwh": design or None, "full_mwh": full or None, "cycles": cycles or None})
    start, end = _when(_text(info, "ReportStartTime")), _when(_text(info, "ScanTime"))
    entries = []
    for e in _children(_child(root, "RecentUsage"), "UsageEntry"):
        a = e.attrib
        ac = _int(a.get("Ac"))
        entries.append({"at": _when(a.get("Timestamp")), "ac": None if ac is None else ac == 1, "type": a.get("EntryType"),
                        "charge": _int(a.get("ChargeCapacity")) or None, "full": _int(a.get("FullChargeCapacity")) or None})
    history = []
    for e in _children(_child(root, "History"), "HistoryEntry"):
        a = e.attrib
        full, design = _int(a.get("FullChargeCapacity")) or 0, _int(a.get("DesignCapacity")) or 0
        if full and design:
            s, t = _when(a.get("StartDate")), _when(a.get("EndDate"))
            dc_h = _duration_h(a.get("ActiveDcTime"))
            span = (t - s).total_seconds() / 86400 if s and t and t > s else None
            history.append({"start": a.get("StartDate"), "end": a.get("EndDate"), "full_mwh": full, "design_mwh": design,
                            "health_pct": round(full / design * 100, 1), "cycles": _int(a.get("CycleCount")) or None,
                            "battery_hours_per_day": round(dc_h / span, 1) if dc_h is not None and span else None})
    return {"batteries": batteries, "history": history[-52:], "daily_use": daily_use(entries, start, end) if batteries else None,
            "platform_role": _text(system, "PlatformRole"), "scan_time": _text(info, "ScanTime")}


def battery_report() -> dict:
    """Runs powercfg (no admin needed) into a private temp folder and parses it. Empty on any failure."""
    if not IS_WIN:
        return {}
    folder = tempfile.mkdtemp(prefix="ultrasuite_battery_")
    out = os.path.join(folder, "battery_report.xml")
    try:
        subprocess.run(["powercfg", "/batteryreport", "/XML", "/OUTPUT", out], capture_output=True, timeout=40, creationflags=NO_WINDOW)
        with open(out, "rb") as fh:
            return parse_battery_report(fh.read())
    except (OSError, subprocess.SubprocessError, ET.ParseError, ValueError):
        return {}
    finally:
        for fn in (lambda: os.remove(out), lambda: os.rmdir(folder)):
            try:
                fn()
            except OSError:
                pass


def battery_history() -> dict:
    """Capacity over time from the battery report (how health changed)."""
    return {"history": (_cached("report", 1800, battery_report) or {}).get("history", [])}


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
