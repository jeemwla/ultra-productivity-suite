#!/usr/bin/env python3
"""
Hardware telemetry in LibreHardwareMonitor's data.json shape.

Sources, in order: PC Sentinel's own sensor helper (installed with the suite, this edition's port on 127.0.0.1),
then a LibreHardwareMonitor the user runs (its web server, 8085), then LibreHardwareMonitor's WMI output, then what
Windows reports by itself. The snapshot says which source is active ("source", "source_id") and why the earlier ones
were skipped ("sources"). It reads whatever CPU / GPU / RAM / drives / fans are reported, groups them by component,
and computes a plain-language summary. Nothing reachable means it says so (no made-up numbers).
"""
import os
import re
import time
from typing import Dict, List, Optional
import httpx

LHM_REMOTE_URL = os.getenv("LHM_REMOTE_URL", "http://127.0.0.1:8085/data.json")

KIND_BY_PREFIX = {
    "amdcpu": "cpu", "intelcpu": "cpu", "cpu": "cpu",
    "gpu-amd": "gpu", "gpu-nvidia": "gpu", "gpu-intel": "gpu", "atigpu": "gpu", "nvidiagpu": "gpu",
    "ram": "memory", "vram": "memory", "memory": "memory",
    "nvme": "storage", "hdd": "storage", "ssd": "storage",
    "lpc": "motherboard", "mainboard": "motherboard", "motherboard": "motherboard",
    "nic": "network", "battery": "battery", "psu": "psu",
}
UNIT_BY_TYPE = {"Temperature": "°C", "Fan": "RPM", "Load": "%", "Power": "W", "Voltage": "V",
                "Clock": "MHz", "Control": "%", "Level": "%", "Data": "GB", "SmallData": "MB",
                "Throughput": "B/s", "Factor": "", "Timing": "ns", "Current": "A", "Energy": "mWh"}
JUNK = re.compile(r"(limit|resolution|warning temperature|critical temperature|thermal sensor)", re.I)
_num = re.compile(r"-?\d+(?:[.,]\d+)?")


def _clean(text: str) -> str:
    text = "".join(ch for ch in str(text) if ch.isprintable())
    return re.sub(r"[^\w\s()\-#/.,+:&]", "", text).strip() or "Unknown"


_RATE = {"b/s": 1, "kb/s": 1e3, "mb/s": 1e6, "gb/s": 1e9}


def _to_float(val) -> Optional[float]:
    if val is None:
        return None
    s = str(val)
    m = _num.search(s)
    if not m:
        return None
    x = float(m.group(0).replace(",", "."))
    # LibreHardwareMonitor prints throughput as "13.9 KB/s" or "1.2 MB/s": normalize to bytes per second
    unit = s[m.end():].strip().lower()
    return x * _RATE[unit] if unit in _RATE else x


def _type_from_value(v: str) -> str:
    if "°C" in v or "�C" in v: return "Temperature"
    if "RPM" in v: return "Fan"
    if v.endswith("%"): return "Load"
    if v.endswith(" W"): return "Power"
    if v.endswith(" V"): return "Voltage"
    if "MHz" in v: return "Clock"
    return "Other"


def _walk(node: dict, path: List[str], groups: Dict[str, dict], hardware_id=None):
    hardware_id = node.get("HardwareId") or hardware_id
    text = node.get("Text", "")
    sid = node.get("SensorId")
    if sid:
        parts = sid.strip("/").split("/")
        kind = KIND_BY_PREFIX.get(parts[0], "other")
        hw_name = _clean(path[0]) if path else "Unknown"
        stype = node.get("Type") or _type_from_value(str(node.get("Value", "")))
        name = _clean(text)
        if JUNK.search(name):
            return
        value = _to_float(node.get("Value"))
        if value is None:
            return
        key = hardware_id or "/" + "/".join(parts[:-2])
        g = groups.setdefault(key, {"id": key, "name": hw_name, "kind": kind, "sensors": []})
        g["sensors"].append({
            "id": sid, "name": name, "type": stype,
            "value": value, "min": _to_float(node.get("Min")), "max": _to_float(node.get("Max")),
            "unit": UNIT_BY_TYPE.get(stype, ""),
        })
        return
    # Hardware nodes sit two levels down (root "Sensor" > machine > hardware)
    new_path = path + [text] if len(path) < 1 and node.get("HardwareId") else path
    for child in node.get("Children", []):
        _walk(child, new_path, groups, hardware_id)


def _pick(sensors: List[dict], stype: str, *names: str) -> Optional[dict]:
    pool = [s for s in sensors if s["type"] == stype]
    for want in names:
        for s in pool:
            if want.lower() in s["name"].lower():
                return s
    return pool[0] if pool else None


def _cpu_clock(sensors: List[dict]):
    """Average core clock in MHz: the sensor's own average, else the mean of per-core clocks (not 'Effective')."""
    clocks = [x for x in sensors if x["type"] == "Clock"]
    avg = next((x["value"] for x in clocks if x["name"].lower() == "cores (average)"), None)
    if avg is not None:
        return avg
    cores = [x["value"] for x in clocks if x["name"].lower().startswith("core #") and "effective" not in x["name"].lower()]
    return round(sum(cores) / len(cores)) if cores else None


def _gpu_clock(sensors: List[dict]):
    """GPU core clock in MHz, only from a sensor named for the core (never the memory clock)."""
    return next((x["value"] for x in sensors if x["type"] == "Clock" and x["name"].lower() in ("gpu core", "core")), None)


def summarize(groups: List[dict]) -> dict:
    by_kind = lambda k: [g for g in groups if g["kind"] == k]
    out: Dict[str, object] = {}
    cpu = next(iter(by_kind("cpu")), None)
    if cpu:
        s = cpu["sensors"]
        # only a real CPU sensor counts (ACPI thermal zones are often board/ambient values)
        t = next((x for want in ("Package", "Tctl", "Core (Tctl", "CPU Core", "Core Average", "CPU") for x in s
                  if x["type"] == "Temperature" and want.lower() in x["name"].lower() and "acpi" not in x["name"].lower()), None)
        out.update(cpu_name=cpu["name"], cpu_temp=t and t["value"],
                   cpu_load=(_pick(s, "Load", "CPU Total", "Total") or {}).get("value"),
                   cpu_power=(_pick(s, "Power", "Package", "CPU Package") or {}).get("value"),
                   cpu_clock=_cpu_clock(s))
    gpus = by_kind("gpu")
    gpu = max(gpus, key=lambda g: len(g["sensors"])) if gpus else None
    if gpu:
        s = gpu["sensors"]
        out.update(gpu_name=gpu["name"],
                   gpu_temp=(_pick(s, "Temperature", "GPU Core", "Core") or {}).get("value"),
                   gpu_hotspot=(_pick(s, "Temperature", "Hot Spot", "Hotspot", "Junction") or {}).get("value"),
                   gpu_load=(_pick(s, "Load", "GPU Core", "D3D 3D", "Core") or {}).get("value"),
                   gpu_power=(_pick(s, "Power", "GPU Package", "Package", "Board") or {}).get("value"),
                   gpu_clock=_gpu_clock(s))
    ram = next((g for g in groups if g["kind"] == "memory" and "virtual" not in g["name"].lower()), None)
    if ram:
        s = ram["sensors"]
        used = next((x["value"] for x in s if x["type"] == "Data" and "used" in x["name"].lower()), None)
        avail = next((x["value"] for x in s if x["type"] == "Data" and "available" in x["name"].lower()), None)
        out.update(ram_load=(_pick(s, "Load", "Memory") or {}).get("value"), ram_used_gb=used,
                   ram_total_gb=round(used + avail, 1) if used is not None and avail is not None else None)
    fans = [x for g in groups for x in g["sensors"] if x["type"] == "Fan" and x["value"] > 0]
    out["fans"] = [{"name": f["name"], "rpm": f["value"]} for f in fans]
    out["fan_max_rpm"] = max((f["value"] for f in fans), default=None)
    drive_temps = [x["value"] for g in by_kind("storage") for x in g["sensors"] if x["type"] == "Temperature"]
    out["storage_max_temp"] = max(drive_temps, default=None)

    # Plain-language health
    notes, score, missing = [], 100, []
    def judge(label, val, warm, hot):
        nonlocal score
        if val is None:
            missing.append(label)
            return
        if val >= hot:
            notes.append(f"{label} is hot ({val:.0f}°C)"); score -= 35
        elif val >= warm:
            notes.append(f"{label} is warm ({val:.0f}°C)"); score -= 12
    judge("CPU", out.get("cpu_temp"), 80, 90)
    judge("GPU", out.get("gpu_hotspot") or out.get("gpu_temp"), 85, 100)
    judge("Storage", out.get("storage_max_temp"), 65, 75)
    if (out.get("ram_load") or 0) >= 90:
        notes.append(f"Memory is almost full ({out['ram_load']:.0f}%)"); score -= 15
    score = max(0, score)
    out["health"] = health_verdict(score, notes, missing)
    return out


def health_verdict(score: int, notes: list, missing: list) -> dict:
    """Never claim 'All good' about readings that don't exist: name what couldn't be checked."""
    unchecked = [m for m in missing if m in ("CPU", "GPU")]
    if score >= 85 and unchecked:
        label = "No warnings in available readings"
        extra = f"{' and '.join(unchecked)} temperature unavailable, so {'it was' if len(unchecked) == 1 else 'they were'} not checked."
        return {"score": score, "label": label, "notes": notes + [extra], "unchecked": unchecked}
    label = "All good" if score >= 85 else ("Keep an eye on it" if score >= 60 else "Running hot")
    return {"score": score, "label": label, "notes": notes or ["Temperatures and memory are in a healthy range."], "unchecked": unchecked}


_FC_CACHE = {"path": None, "mtime": 0, "data": None}
_PEAK_RPM: Dict[str, float] = {}


def _fancontrol_config():
    """FanControl (Rem0o) user config, if installed: fan nicknames and which curve drives each fan."""
    cands = [os.getenv("FANCONTROL_CONFIG", "")]
    for base in (r"D:\FanControl", r"C:\FanControl", os.path.expandvars(r"%ProgramFiles(x86)%\FanControl"),
                 os.path.expandvars(r"%ProgramFiles%\FanControl"), os.path.expandvars(r"%USERPROFILE%\Documents\FanControl")):
        cands.append(os.path.join(base, "Configurations", "userConfig.json"))
    path = next((p for p in cands if p and os.path.exists(p)), None)
    if not path:
        return None
    mt = os.path.getmtime(path)
    if _FC_CACHE["path"] != path or _FC_CACHE["mtime"] != mt:
        try:
            import json
            d = json.load(open(path, encoding="utf-8-sig")).get("FanControl", {})
            _FC_CACHE.update(path=path, mtime=mt, data=d)
        except Exception:
            _FC_CACHE.update(path=path, mtime=mt, data=None)
    return _FC_CACHE["data"]


def _norm_id(sid: str) -> str:
    # LHM: /lpc/it8696e/0/fan/0   FanControl: /lpc/it8696e/fan/0
    return re.sub(r"/(\d+)/(fan|control)/", r"/\2/", sid or "").lower()


def cooling_report(groups: List[dict]) -> dict:
    fc = _fancontrol_config() or {}
    nick = {_norm_id(f.get("Identifier", "")): f.get("NickName") for f in fc.get("FanSensors", []) or []}
    curve_for_fan, curve_info = {}, {}
    for c in fc.get("FanCurves", []) or []:
        src = (c.get("SelectedTempSource") or {}).get("Identifier", "") if isinstance(c.get("SelectedTempSource"), dict) else ""
        curve_info[c.get("Name")] = {"name": c.get("Name"), "flat_percent": c.get("Percent") if c.get("Percent") is not None and not c.get("Points") else None,
                                     "temp_source": src}
    for ctl in fc.get("Controls", []) or []:
        pair = ctl.get("PairedFanSensor") or {}
        curve = ctl.get("SelectedFanCurve") or {}
        if isinstance(pair, dict) and pair.get("Identifier"):
            curve_for_fan[_norm_id(pair["Identifier"])] = {"curve": curve.get("Name") if isinstance(curve, dict) else None,
                                                          "control": ctl.get("NickName"), "enabled": ctl.get("Enable")}
    controls = {}
    for g in groups:
        for s in g["sensors"]:
            if s["type"] == "Control":
                controls[_norm_id(s["id"]).replace("/control/", "/fan/")] = s["value"]
    fans = []
    for g in groups:
        for s in g["sensors"]:
            if s["type"] != "Fan":
                continue
            key = _norm_id(s["id"])
            _PEAK_RPM[key] = max(_PEAK_RPM.get(key, 0), s["value"], s.get("max") or 0)
            link = curve_for_fan.get(key, {})
            if not link and g["kind"] == "gpu":
                # FanControl names GPU fans by the vendor driver (ADLX / NvAPI), not by the LHM id
                link = next((v for k, v in curve_for_fan.items() if ("adlx" in k or "nvapi" in k or "gpu" in k)), {})
            cinfo = curve_info.get(link.get("curve") or "", {})
            src = cinfo.get("temp_source", "")
            drives = ("CPU temperature" if "cpu" in src.lower() or "cpu" in (link.get("curve") or "").lower() else
                      "GPU temperature" if "gpu" in src.lower() or "gpu" in (link.get("curve") or "").lower() else
                      f"fixed {cinfo['flat_percent']}%" if cinfo.get("flat_percent") is not None else None)
            where = "Graphics card" if g["kind"] == "gpu" else "Motherboard header" if g["kind"] == "motherboard" else g["name"]
            if not drives and controls.get(key) is None and g["kind"] == "motherboard":
                drives = "Not controlled: runs at a fixed speed (often an AIO pump or pump header)"
            fans.append({
                "id": s["id"], "key": key, "name": nick.get(key) or s["name"], "sensor_name": s["name"], "location": where,
                "rpm": s["value"], "min_rpm": s.get("min"), "peak_rpm": _PEAK_RPM[key],
                "output_pct": round(100 * s["value"] / _PEAK_RPM[key], 1) if _PEAK_RPM[key] else None,
                "duty_pct": controls.get(key), "curve": link.get("curve"), "driven_by": drives,
                "spinning": s["value"] > 50,
            })
    fans.sort(key=lambda f: (f["location"] != "Motherboard header", f["name"]))
    return {"fans": fans, "controls": len(controls), "has_fan_data": bool(fans or controls),
            "fancontrol": bool(fc), "fancontrol_path": _FC_CACHE.get("path") if fc else None}


def power_report(groups: List[dict], summary: dict) -> dict:
    """Component measurements and disclosed models, never measured whole-system or wall watts."""
    import math

    def watts(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        try:
            value = float(value)
        except (OverflowError, ValueError):
            return None
        return value if math.isfinite(value) and value >= 0 else None

    def aggregate(group, names):
        # Choose one reported aggregate per device, never add its overlapping
        # board/package/core readings. A core-only reading is not package power.
        sensors = [s for s in group.get("sensors", []) if s.get("type") == "Power"
                   and not re.search(r"limit|offset|maximum|minimum", str(s.get("name", "")), re.I)]
        for name in names:
            for sensor in sensors:
                label = str(sensor.get("name", "")).strip().lower()
                if label == name or (name != "total" and label.startswith(name + " ")):
                    value = watts(sensor.get("value"))
                    if value is not None:
                        return value, sensor.get("name")
        return None, None

    cpus = [g for g in groups if g.get("kind") == "cpu"]
    cpu_values = [aggregate(g, ("cpu package", "package")) for g in cpus]
    if not cpus:
        cpu_values = [(watts(summary.get("cpu_power")), "summary.cpu_power")]
    cpu_complete = all(value is not None for value, _ in cpu_values)
    cpu = watts(sum(value for value, _ in cpu_values)) if cpu_complete else None
    cpu_complete = cpu_complete and cpu is not None
    gpus = [g for g in groups if g.get("kind") == "gpu"]
    gpu_values = [aggregate(g, ("total board", "gpu board", "board", "gpu package", "package", "gpu total", "total", "gpu power")) for g in gpus]
    gpu_complete = bool(gpus) and all(value is not None for value, _ in gpu_values)
    gpu = watts(sum(value for value, _ in gpu_values)) if gpu_complete else None
    gpu_complete = gpu_complete and gpu is not None
    readings = [value for value, _ in cpu_values + gpu_values if value is not None]
    subtotal = watts(sum(readings)) if readings else None
    measured_subtotal = round(subtotal, 1) if subtotal is not None else None
    unknown = []
    for index, (value, _) in enumerate(cpu_values):
        if value is None:
            unknown.append(f"CPU package {index + 1}")
    for index, (value, _) in enumerate(gpu_values):
        if value is None:
            unknown.append(f"GPU {index + 1} aggregate power")
    if not gpus:
        unknown.append("GPU power (no adapter readings)")
    if readings and subtotal is None:
        unknown.append("Component subtotal outside numeric range")
    dimms = sum(1 for g in groups if g.get("kind") == "memory"
                and "dimm" in " ".join(str(s.get("id", "")) for s in g.get("sensors", [])).lower())
    drives = sum(1 for g in groups if g.get("kind") == "storage")
    fans = sum(1 for g in groups for s in g.get("sensors", []) if s.get("type") == "Fan"
               and watts(s.get("value")) is not None and watts(s.get("value")) > 50)
    estimated = {"Motherboard and chipset": 18.0, "Memory": 4.0 * max(dimms, 2),
                 "Drives": 4.5 * drives, "Fans": 1.8 * fans, "USB devices and other": 10.0}
    components_complete = cpu_complete and gpu_complete
    dc = watts(sum(readings) + sum(estimated.values())) if components_complete else None
    modeled_dc = round(dc, 1) if dc is not None else None
    efficiency = 0.90
    wall = watts(modeled_dc / efficiency) if modeled_dc is not None else None
    return {
        "measured": {"CPU package": round(cpu, 1) if cpu is not None else None,
                     "Graphics card": round(gpu, 1) if gpu is not None else None},
        "measured_subtotal_w": measured_subtotal,
        "measured_total_w": None,
        "estimated": {name: round(value, 1) for name, value in estimated.items()},
        "estimated_subtotal_w": round(sum(estimated.values()), 1),
        "estimate_assumptions": {"Motherboard and chipset": "Fixed 18 W allowance, not a reading.",
                                 "Memory": "4 W per detected DIMM group, assuming at least two; not a reading.",
                                 "Drives": "4.5 W per detected drive group, not a reading.",
                                 "Fans": "1.8 W per reported fan above 50 RPM, not a reading.",
                                 "USB devices and other": "Fixed 10 W allowance, not a device inventory or reading."},
        "dc_total_w": modeled_dc,
        "dc_total_is_estimate": True,
        "modeled_dc_w": modeled_dc,
        "wall_w": None,
        "modeled_wall_w": round(wall, 1) if wall is not None else None,
        "wall_model": {"efficiency": efficiency, "is_measured": False,
                       "assumption": "Fixed 90% PSU efficiency assumption. Actual efficiency varies with PSU and load."},
        "coverage": {"complete": False, "component_readings_complete": components_complete,
                     "unknown_components": unknown, "unmeasured_components": list(estimated),
                     "detected_gpus": len(gpus), "gpus_with_power": sum(value is not None for value, _ in gpu_values),
                     "scope": "Reported CPU package and one aggregate per detected GPU; other system draw is modeled."},
        "gpu_readings": [{"id": group.get("id"), "name": group.get("name"),
                          "watts": value, "sensor": sensor}
                         for group, (value, sensor) in zip(gpus, gpu_values)],
        "advisory_eligible": False,
    }


KIND_ORDER = ["cpu", "gpu", "memory", "storage", "motherboard", "battery", "network", "psu", "other"]
_wmi_cache = {"at": 0.0, "groups": None}
_mon_cache = {"at": 0.0, "data": None}

WMI_PS = r"""
$ErrorActionPreference='SilentlyContinue'
foreach($ns in 'root\LibreHardwareMonitor','root\OpenHardwareMonitor'){
  $hw = Get-CimInstance -Namespace $ns -ClassName Hardware
  if($hw){ $s = Get-CimInstance -Namespace $ns -ClassName Sensor
    [ordered]@{ns=$ns; hw=@($hw|Select-Object Identifier,Name,HardwareType); s=@($s|Select-Object Identifier,Name,SensorType,Value,Min,Max,Parent)} | ConvertTo-Json -Depth 4 -Compress; break } }
"""


def _wmi_groups() -> Optional[List[dict]]:
    """LibreHardwareMonitor / OpenHardwareMonitor via WMI (works when their web server is off)."""
    if _wmi_cache["groups"] is not None and time.time() - _wmi_cache["at"] < 3:
        return _wmi_cache["groups"]
    import json, subprocess
    try:
        raw = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", WMI_PS], capture_output=True, text=True,
                             timeout=12, creationflags=0x08000000).stdout.strip()
        d = json.loads(raw) if raw else None
    except Exception:
        d = None
    groups = None
    if d and d.get("s"):
        names = {h["Identifier"]: h["Name"] for h in d.get("hw", [])}
        g: Dict[str, dict] = {}
        for s in d["s"]:
            if s.get("Value") is None or JUNK.search(_clean(s.get("Name", ""))):
                continue
            parent = s.get("Parent") or ""
            kind = KIND_BY_PREFIX.get(parent.strip("/").split("/")[0], "other")
            hname = _clean(names.get(parent, parent))
            grp = g.setdefault(parent, {"id": parent, "name": hname, "kind": kind, "sensors": []})
            stype = s.get("SensorType") or "Other"
            grp["sensors"].append({"id": s.get("Identifier"), "name": _clean(s.get("Name")), "type": stype, "value": float(s["Value"]),
                                   "min": s.get("Min"), "max": s.get("Max"), "unit": UNIT_BY_TYPE.get(stype, "")})
        groups = sorted(g.values(), key=lambda x: KIND_ORDER.index(x["kind"]) if x["kind"] in KIND_ORDER else 99)
    _wmi_cache.update(at=time.time(), groups=groups or [])
    return groups


def groups_from_tree(tree) -> List[dict]:
    """Sensor groups from a data.json tree (LibreHardwareMonitor's or the sensor helper's, same shape)."""
    if not isinstance(tree, dict):
        raise ValueError("data.json is not an object")
    groups: Dict[str, dict] = {}
    _walk(tree, [], groups)
    return sorted(groups.values(), key=lambda g: KIND_ORDER.index(g["kind"]) if g["kind"] in KIND_ORDER else 99)


def _has_cpu_temperature(groups) -> bool:
    return any(g.get("kind") == "cpu" and any(s.get("type") == "Temperature" for s in g.get("sensors", []))
               for g in groups or [])


# Sources in the order they are tried. "helper" is PC Sentinel's own sensor helper (installed with the suite);
# "lhm_http" is a LibreHardwareMonitor the user runs themselves; then WMI; then what Windows reports by itself.
SOURCE_LABELS = {"helper": "PC Sentinel sensor helper", "lhm_http": "LibreHardwareMonitor",
                 "lhm_wmi": "LibreHardwareMonitor (WMI)", "windows": "Windows built-in sensors"}
_helper_state = {"health": None}


def forget_cached_state():
    """Next read re-checks every source right away (used by the Try again button)."""
    _wmi_cache.update(at=0.0, groups=None)
    _mon_cache.update(at=0.0, data=None)
    _helper_state["health"] = None


async def _http_sources(client) -> tuple:
    """Try the helper, then a user's own LibreHardwareMonitor. Returns (groups, source_id, attempts, health).

    The helper wins when it reports a CPU temperature. Otherwise both are asked, and the one with more sensors wins
    (the helper on a tie), so a PC whose driver is blocked can still use a LibreHardwareMonitor the user runs."""
    from . import sensor_helper
    attempts, found, health = [], [], None
    try:
        tree, health = await sensor_helper.fetch(client)
        groups = groups_from_tree(tree)
        attempts.append({"id": "helper", "ok": bool(groups), "reason": None if groups else "no readings yet"})
        if groups:
            found.append(("helper", groups))
            if _has_cpu_temperature(groups):
                return groups, "helper", attempts, health
    except Exception as exc:
        attempts.append({"id": "helper", "ok": False, "reason": exc.__class__.__name__})
    try:
        resp = await client.get(LHM_REMOTE_URL)
        resp.raise_for_status()
        groups = groups_from_tree(resp.json())
        attempts.append({"id": "lhm_http", "ok": bool(groups), "reason": None if groups else "no readings"})
        if groups:
            found.append(("lhm_http", groups))
    except Exception as exc:
        attempts.append({"id": "lhm_http", "ok": False, "reason": exc.__class__.__name__})
    if not found:
        return None, None, attempts, health
    best = max(found, key=lambda f: (sum(len(g["sensors"]) for g in f[1]), f[0] == "helper"))
    return best[1], best[0], attempts, health


async def collect_hardware_telemetry() -> Dict:
    import asyncio
    from . import native_sensors as ns, storage_health as sh, sensor_helper
    level = "full"
    async with httpx.AsyncClient(timeout=1.5) as client:
        glist, source_id, attempts, health = await _http_sources(client)
    _helper_state["health"] = health
    if not glist:
        glist = await asyncio.to_thread(_wmi_groups)
        attempts.append({"id": "lhm_wmi", "ok": bool(glist), "reason": None if glist else "not available"})
        if glist:
            source_id = "lhm_wmi"
    if not glist:
        nat = await asyncio.to_thread(ns.native_groups)
        glist, source_id, level = nat["groups"], "windows", "basic"
        attempts.append({"id": "windows", "ok": True, "reason": None})
    source = SOURCE_LABELS[source_id]
    info = await asyncio.to_thread(ns.device_info)
    if _mon_cache["data"] is None or time.time() - _mon_cache["at"] > 15:
        from . import sensor_setup
        _mon_cache.update(at=time.time(), data=await asyncio.to_thread(ns.detect_monitors),
                          drivers=await asyncio.to_thread(sensor_setup.driver_facts))
    drives = sh.merge_lhm([dict(x) for x in await asyncio.to_thread(sh.drives)], glist)
    summary = summarize(glist)
    summary.setdefault("cpu_name", info.get("cpu"))
    helper = await asyncio.to_thread(sensor_helper.summary, health)
    helper["last_setup"] = await asyncio.to_thread(sensor_helper.last_result)
    out = {"live": True, "source": source, "source_id": source_id, "sources": attempts, "helper": helper,
           "level": level, "groups": glist, "summary": summary,
           "cooling": cooling_report(glist), "power": power_report(glist, summary), "drives": drives,
           "device": info, "monitors": _mon_cache["data"], "sensor_count": sum(len(g["sensors"]) for g in glist)}
    from . import sensor_setup
    out["sensor_setup"] = sensor_setup.guide(_mon_cache["data"], _mon_cache.get("drivers"), level, glist,
                                             helper=helper, source_id=source_id)
    if level == "basic":
        m = _mon_cache["data"] or {}
        out["help"] = ("LibreHardwareMonitor is installed but not running. Start it to add temperatures, fans and power."
                       if m.get("lhm_installed") else
                       "Showing what Windows provides. Install the free LibreHardwareMonitor to add temperatures, fans and power.")
        out["can_install_lhm"] = not m.get("lhm_installed")
    return out
