#!/usr/bin/env python3
"""
Hardware telemetry from LibreHardwareMonitor's Remote Web Server (data.json).

Works on any PC: it reads whatever CPU / GPU / RAM / drives / fans LibreHardwareMonitor
reports, groups them by component, and computes a plain-language summary.
If LibreHardwareMonitor is not reachable, it says so (no made-up numbers).
"""
import os
import re
from typing import Dict, List, Optional
import httpx

LHM_REMOTE_URL = os.getenv("LHM_REMOTE_URL", "http://host.docker.internal:8085/data.json")

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


def _to_float(val) -> Optional[float]:
    if val is None:
        return None
    m = _num.search(str(val))
    return float(m.group(0).replace(",", ".")) if m else None


def _type_from_value(v: str) -> str:
    if "°C" in v or "�C" in v: return "Temperature"
    if "RPM" in v: return "Fan"
    if v.endswith("%"): return "Load"
    if v.endswith(" W"): return "Power"
    if v.endswith(" V"): return "Voltage"
    if "MHz" in v: return "Clock"
    return "Other"


def _walk(node: dict, path: List[str], groups: Dict[str, dict]):
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
        g = groups.setdefault(hw_name, {"name": hw_name, "kind": kind, "sensors": []})
        g["sensors"].append({
            "id": sid, "name": name, "type": stype,
            "value": value, "min": _to_float(node.get("Min")), "max": _to_float(node.get("Max")),
            "unit": UNIT_BY_TYPE.get(stype, ""),
        })
        return
    # Hardware nodes sit two levels down (root "Sensor" > machine > hardware)
    new_path = path + [text] if len(path) < 1 and node.get("HardwareId") else path
    for child in node.get("Children", []):
        _walk(child, new_path, groups)


def _pick(sensors: List[dict], stype: str, *names: str) -> Optional[dict]:
    pool = [s for s in sensors if s["type"] == stype]
    for want in names:
        for s in pool:
            if want.lower() in s["name"].lower():
                return s
    return pool[0] if pool else None


def summarize(groups: List[dict]) -> dict:
    by_kind = lambda k: [g for g in groups if g["kind"] == k]
    out: Dict[str, object] = {}
    cpu = next(iter(by_kind("cpu")), None)
    if cpu:
        s = cpu["sensors"]
        t = _pick(s, "Temperature", "Package", "Tctl", "Core (Tctl", "CPU")
        out.update(cpu_name=cpu["name"], cpu_temp=t and t["value"],
                   cpu_load=(_pick(s, "Load", "CPU Total", "Total") or {}).get("value"),
                   cpu_power=(_pick(s, "Power", "Package", "CPU Package") or {}).get("value"))
    gpus = by_kind("gpu")
    gpu = max(gpus, key=lambda g: len(g["sensors"])) if gpus else None
    if gpu:
        s = gpu["sensors"]
        out.update(gpu_name=gpu["name"],
                   gpu_temp=(_pick(s, "Temperature", "GPU Core", "Core") or {}).get("value"),
                   gpu_hotspot=(_pick(s, "Temperature", "Hot Spot", "Hotspot", "Junction") or {}).get("value"),
                   gpu_load=(_pick(s, "Load", "GPU Core", "D3D 3D", "Core") or {}).get("value"),
                   gpu_power=(_pick(s, "Power", "GPU Package", "Package", "Board") or {}).get("value"))
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
    notes, score = [], 100
    def judge(label, val, warm, hot):
        nonlocal score
        if val is None:
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
    out["health"] = {"score": score,
                     "label": "All good" if score >= 85 else ("Keep an eye on it" if score >= 60 else "Running hot"),
                     "notes": notes or ["Temperatures and memory are in a healthy range."]}
    return out


async def collect_hardware_telemetry() -> Dict:
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(LHM_REMOTE_URL)
            resp.raise_for_status()
            tree = resp.json()
        groups: Dict[str, dict] = {}
        _walk(tree, [], groups)
        glist = sorted(groups.values(), key=lambda g: ["cpu", "gpu", "memory", "storage", "motherboard",
                                                         "network", "battery", "psu", "other"].index(g["kind"]))
        if not glist:
            raise ValueError("LibreHardwareMonitor returned no sensors")
        return {"live": True, "source": "LibreHardwareMonitor", "groups": glist, "summary": summarize(glist),
                "sensor_count": sum(len(g["sensors"]) for g in glist)}
    except Exception as exc:
        return {"live": False, "source": "LibreHardwareMonitor offline",
                "error": f"{exc.__class__.__name__}: {exc}"[:200],
                "help": "Start LibreHardwareMonitor and turn on Options > Remote Web Server (port 8085).",
                "groups": [], "summary": {}, "sensor_count": 0}
