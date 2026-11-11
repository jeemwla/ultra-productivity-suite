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
import math
import os
import re
import subprocess
import time

NO_WINDOW = 0x08000000
_cache = {"at": 0.0, "data": None}

PS = r"""
$ErrorActionPreference='SilentlyContinue'
$out=@()
$physical=@(Get-PhysicalDisk)
$disks=@(Get-Disk)
foreach($d in $physical){
  $r=$null; try { $r = $d | Get-StorageReliabilityCounter } catch {}
  $matches=@()
  if($d.UniqueId -and @($physical | Where-Object UniqueId -eq $d.UniqueId).Count -eq 1){
    $matches=@($disks | Where-Object UniqueId -eq $d.UniqueId)
  }
  if($matches.Count -ne 1 -and $d.SerialNumber){
    $serial=($d.SerialNumber -replace '\s','')
    if(@($physical | Where-Object {($_.SerialNumber -replace '\s','') -eq $serial}).Count -eq 1){
      $matches=@($disks | Where-Object {($_.SerialNumber -replace '\s','') -eq $serial})
    }
  }
  $disk=$null; $volumes=@()
  if($matches.Count -eq 1){
    $disk=$matches[0]
    foreach($part in @(Get-Partition -DiskNumber $disk.Number)){
      $volume=Get-Volume -Partition $part
      if($volume.Size -gt 0){
        $mount=if($volume.DriveLetter){[string]$volume.DriveLetter+':'}else{
          @($part.AccessPaths | Where-Object {$_ -notlike '*Volume{*'} | Select-Object -First 1) -join ''
        }
        if($mount){$volumes += [ordered]@{mount=$mount; filesystem=$volume.FileSystem; size=$volume.Size; free=$volume.SizeRemaining}}
      }
    }
  }
  $out += [ordered]@{
    id=$d.DeviceId; name=$d.FriendlyName; serial=($d.SerialNumber -replace '\s',''); firmware=$d.FirmwareVersion
    media=[string]$d.MediaType; bus=[string]$d.BusType; size=$d.Size; health=[string]$d.HealthStatus
    status=(@($d.OperationalStatus) -join ', '); spindle=$d.SpindleSpeed
    temp=$r.Temperature; temp_max=$r.TemperatureMax; wear=$r.Wear; hours=$r.PowerOnHours
    read_errors=$r.ReadErrorsTotal; write_errors=$r.WriteErrorsTotal; start_stop=$r.StartStopCycleCount
    volumes=$volumes; disk_number=$disk.Number; partition_style=[string]$disk.PartitionStyle
    logical_sector_bytes=$disk.LogicalSectorSize; physical_sector_bytes=$disk.PhysicalSectorSize
  }
}
$out | ConvertTo-Json -Depth 5 -Compress
"""


def _bytes(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None


def capacity_details(raw):
    """Keep decimal device capacity separate from binary and filesystem capacity."""
    size = _bytes(raw.get("size"))
    volumes = []
    for volume in raw.get("volumes") or []:
        total, free = _bytes(volume.get("size")), _bytes(volume.get("free"))
        if total is not None and free is not None and free > total:
            free = None
        volumes.append({"mount": volume.get("mount") or "Mounted volume", "filesystem": volume.get("filesystem") or None,
                        "size_bytes": total, "free_bytes": free,
                        "used_bytes": total - free if total is not None and free is not None else None})
    return {"size_bytes": size, "size_gib": round(size / 2**30, 2) if size is not None else None,
            "volumes": volumes, "disk_number": raw.get("disk_number"),
            "partition_style": raw.get("partition_style") or None,
            "logical_sector_bytes": raw.get("logical_sector_bytes"), "physical_sector_bytes": raw.get("physical_sector_bytes")}


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
            "id": str(d.get("id", "")), "serial": d.get("serial") or None,
            **capacity_details(d),
            "health": health, "status": d.get("status"), "firmware": d.get("firmware"),
            "temp": d.get("temp") or None, "temp_max": d.get("temp_max") or None,
            "life_left": (100 - wear) if isinstance(wear, (int, float)) and 0 <= wear <= 100 else None,
            "hours": d.get("hours") or None, "read_errors": d.get("read_errors"), "write_errors": d.get("write_errors"),
            "smart_source": "Windows" if any(d.get(k) for k in ("temp", "wear", "hours")) else None,
        })
    _cache.update(at=time.time(), data=out)
    return out


def _serial(value):
    return re.sub(r"\s+", "", str(value or "")).casefold()


def _model_words(value):
    words = re.findall(r"[a-z0-9]+", str(value or "").casefold())
    return {w for w in words if w not in {"nvme", "ssd", "hdd", "sata", "usb", "scsi", "disk", "drive"}
            and not re.fullmatch(r"\d+(?:gb|tb)", w)}


def _match_score(drive, group):
    ds, gs = _serial(drive.get("serial")), _serial(group.get("serial"))
    if ds and gs:
        return 1000 if ds == gs else 0  # a known conflict must never fall back to model words
    left, right = _model_words(drive.get("name")), _model_words(group.get("name"))
    common = left & right
    if len(common) < 2 or not any(any(c.isdigit() for c in w) for w in common):
        return 0  # a manufacturer plus generic words is not a model identity
    if left == right:
        return 100
    if left <= right or right <= left:
        return 80
    # Both have extra model terms: they could be different variants (EVO/PRO).
    # Refuse a guess even when they share a manufacturer and model number.
    return 0


def _drive_matches(drives, groups):
    """Conservative, one-to-one mutual unique best matches.

    No numeric LHM hardware index is assumed to be a Windows disk number.
    Compute ties against the complete candidate set, so iteration order cannot
    turn an ambiguous drive into an apparently unique leftover.
    """
    scores = [[_match_score(d, g) for g in groups] for d in drives]
    matches = []
    for i, row in enumerate(scores):
        best = max(row, default=0)
        if not best or row.count(best) != 1:
            continue
        j = row.index(best)
        column = [r[j] for r in scores]
        if max(column) == best and column.count(best) == 1:
            matches.append((i, j))
    return matches


def merge_lhm(drv: list, groups: list) -> list:
    """Fill readings only for an unambiguous one-to-one device match."""
    storage = [g for g in groups if g.get("kind") == "storage"]
    for i, j in _drive_matches(drv, storage):
        d, g = drv[i], storage[j]
        for s in g["sensors"]:
            if not isinstance(s.get("value"), (int, float)) or not math.isfinite(s["value"]):
                continue
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
