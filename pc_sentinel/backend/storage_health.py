#!/usr/bin/env python3
"""
Drive health for any PC.

Layer 1 (always, no admin): Windows Storage API (MSFT_PhysicalDisk) for every drive:
  name, type (NVMe / SSD / HDD), bus, size, health status (Healthy / Warning / Unhealthy), operational status.
Layer 2 (if Windows allows it): MSFT_StorageReliabilityCounter: temperature, wear %, power-on hours,
  read/write errors. Windows often requires admin for this; when it's denied the layer is skipped quietly.
Layer 3 (if LibreHardwareMonitor is running): NVMe SMART from its sensors: life used, available spare,
  temperature, data written. Merged by drive name in lhm_collector.

Cached for 60 seconds (drive health changes slowly; the query takes ~1 s).
"""
import json
import os
import re
import subprocess
import time

NO_WINDOW = 0x08000000
_cache = {"at": 0.0, "data": None}

PS = r"""
$ErrorActionPreference='SilentlyContinue'
$out=@()
foreach($d in Get-PhysicalDisk){
  $r=$null; try { $r = $d | Get-StorageReliabilityCounter } catch {}
  $out += [ordered]@{
    id=$d.DeviceId; name=$d.FriendlyName; serial=($d.SerialNumber -replace '\s',''); firmware=$d.FirmwareVersion
    media=[string]$d.MediaType; bus=[string]$d.BusType; size=$d.Size; health=[string]$d.HealthStatus
    status=(@($d.OperationalStatus) -join ', '); spindle=$d.SpindleSpeed
    temp=$r.Temperature; temp_max=$r.TemperatureMax; wear=$r.Wear; hours=$r.PowerOnHours
    read_errors=$r.ReadErrorsTotal; write_errors=$r.WriteErrorsTotal; start_stop=$r.StartStopCycleCount
  }
}
$out | ConvertTo-Json -Depth 3 -Compress
"""


def drives(max_age=60):
    if _cache["data"] is not None and time.time() - _cache["at"] < max_age:
        return _cache["data"]
    data = []
    if os.name == "nt":
        try:
            raw = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", PS], capture_output=True, text=True,
                                 timeout=20, creationflags=NO_WINDOW).stdout.strip()
            parsed = json.loads(raw) if raw else []
            data = parsed if isinstance(parsed, list) else [parsed]
        except Exception:
            data = []
    out = []
    for d in data:
        media = (d.get("media") or "").upper()
        bus = (d.get("bus") or "").upper()
        kind = "NVMe SSD" if bus == "NVME" else "SSD" if media == "SSD" or media == "4" else "HDD" if media in ("HDD", "3") or (d.get("spindle") or 0) > 0 else "Drive"
        health = d.get("health") or "Unknown"
        health = {"0": "Healthy", "1": "Warning", "2": "Unhealthy"}.get(health, health)
        wear = d.get("wear")
        out.append({
            "name": (d.get("name") or "Drive").strip(), "kind": kind, "bus": d.get("bus"), "size_gb": round((d.get("size") or 0) / 1e9),
            "health": health, "status": d.get("status"), "firmware": d.get("firmware"),
            "temp": d.get("temp") or None, "temp_max": d.get("temp_max") or None,
            "life_left": (100 - wear) if isinstance(wear, (int, float)) and 0 <= wear <= 100 else None,
            "hours": d.get("hours") or None, "read_errors": d.get("read_errors"), "write_errors": d.get("write_errors"),
            "smart_source": "Windows" if any(d.get(k) for k in ("temp", "wear", "hours")) else None,
        })
    _cache.update(at=time.time(), data=out)
    return out


def merge_lhm(drv: list, groups: list) -> list:
    """Fill temperature / life / data written from LibreHardwareMonitor's storage sensors when available."""
    for g in groups:
        if g.get("kind") != "storage":
            continue
        gname = g["name"].lower()
        # Windows and LibreHardwareMonitor spell models differently ("NVMe Samsung SSD 970" vs "Samsung SSD 970 EVO Plus 1TB"),
        # so match on shared model words, ignoring bus and size words
        noise = {"nvme", "ssd", "hdd", "sata", "usb", "1tb", "2tb", "4tb", "500gb", "250gb", "512gb", "256gb", "1", "-", "_"}
        words = lambda s: {w for w in re.split(r"[\s_\-]+", s.lower()) if w and w not in noise}
        def score(x):
            if x["name"].lower() in gname or gname in x["name"].lower():
                return 99
            return len(words(x["name"]) & words(gname))
        best = max(drv, key=score, default=None)
        d = best if best is not None and score(best) >= 2 else None
        if not d:
            continue
        for s in g["sensors"]:
            n = s["name"].lower()
            if s["type"] == "Temperature" and ("composite" in n or "temperature" == n or d.get("temp") is None) and d.get("temp") is None:
                d["temp"] = round(s["value"])
            elif "percentage used" in n or n == "life used":
                d["life_left"] = max(0, 100 - round(s["value"]))
            elif "available spare" in n and "threshold" not in n:
                d["spare"] = round(s["value"])
            elif "data written" in n or "total bytes written" in n:
                d["written_gb"] = round(s["value"])
            elif "power on" in n and "hour" in n:
                d["hours"] = round(s["value"])
        d["smart_source"] = d.get("smart_source") or "LibreHardwareMonitor"
    return drv


if __name__ == "__main__":
    for d in drives():
        print(d)
