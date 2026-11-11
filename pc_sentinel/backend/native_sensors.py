#!/usr/bin/env python3
"""
Sensors from Windows itself, for PCs without LibreHardwareMonitor. Works on any CPU/GPU brand,
needs no admin rights and no extra software.

  CPU      load (total + per core), real clock (Processor Performance counter), thermal-zone temperature
  GPU      3D engine load and dedicated memory per adapter (PDH "GPU Engine" counters, any vendor);
           NVIDIA cards also report temperature, power, fan and clocks through nvidia-smi when present
  Memory   used / available / load
  Drives   usage per volume, read/write throughput
  Network  upload / download throughput
  Device   CPU, GPU, board and PC model names; desktop vs laptop

Also reports which hardware-monitor apps are installed or running (LibreHardwareMonitor, HWiNFO, ...).
Returns the same "groups" structure lhm_collector uses, so the UI treats every source the same.
"""
import ctypes
import json
import os
import shutil
import subprocess
import threading
import time
from ctypes import wintypes
from typing import Dict, List, Optional

import psutil

IS_WIN = os.name == "nt"
NO_WINDOW = 0x08000000


def _ps(cmd: str, timeout=15) -> str:
    try:
        return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd], capture_output=True, text=True,
                              timeout=timeout, creationflags=NO_WINDOW if IS_WIN else 0).stdout
    except Exception:
        return ""


# ----------------------------------------------------------------------------- PDH (Windows performance counters)
class _FMT(ctypes.Structure):
    _fields_ = [("CStatus", wintypes.DWORD), ("doubleValue", ctypes.c_double)]


class _ITEM(ctypes.Structure):
    _fields_ = [("szName", wintypes.LPWSTR), ("FmtValue", _FMT)]


PDH_FMT_DOUBLE, PDH_FMT_NOCAP100, PDH_MORE_DATA = 0x00000200, 0x00008000, 0x800007D2


class Pdh:
    """Tiny wrapper over pdh.dll for wildcard counters (English names, so it works on any Windows language)."""
    def __init__(self, paths: List[str]):
        self.ok = False
        self.counters = {}
        if not IS_WIN:
            return
        self.pdh = ctypes.WinDLL("pdh")
        self.q = wintypes.HANDLE()
        if self.pdh.PdhOpenQueryW(None, 0, ctypes.byref(self.q)) != 0:
            return
        for p in paths:
            h = wintypes.HANDLE()
            if self.pdh.PdhAddEnglishCounterW(self.q, p, 0, ctypes.byref(h)) == 0:
                self.counters[p] = h
        self.pdh.PdhCollectQueryData(self.q)
        self.ok = bool(self.counters)

    def collect(self) -> Dict[str, Dict[str, float]]:
        out = {}
        if not self.ok or self.pdh.PdhCollectQueryData(self.q) != 0:
            return out
        for p, h in self.counters.items():
            size, count = wintypes.DWORD(0), wintypes.DWORD(0)
            r = self.pdh.PdhGetFormattedCounterArrayW(h, PDH_FMT_DOUBLE | PDH_FMT_NOCAP100, ctypes.byref(size), ctypes.byref(count), None)
            if (r & 0xFFFFFFFF) != PDH_MORE_DATA or size.value == 0:  # status codes arrive as signed ints
                continue
            buf = ctypes.create_string_buffer(size.value)
            if self.pdh.PdhGetFormattedCounterArrayW(h, PDH_FMT_DOUBLE | PDH_FMT_NOCAP100, ctypes.byref(size), ctypes.byref(count), buf) != 0:
                continue
            items = ctypes.cast(buf, ctypes.POINTER(_ITEM))
            vals = {}
            for i in range(count.value):
                it = items[i]
                if it.FmtValue.CStatus in (0, 1):  # valid data
                    vals[it.szName] = it.FmtValue.doubleValue
            out[p] = vals
        return out


C_PERF = r"\Processor Information(*)\% Processor Performance"
C_FREQ = r"\Processor Information(*)\Processor Frequency"
C_THERM = r"\Thermal Zone Information(*)\High Precision Temperature"
C_GPU = r"\GPU Engine(*)\Utilization Percentage"
C_GMEM = r"\GPU Adapter Memory(*)\Dedicated Usage"


# ----------------------------------------------------------------------------- static device info (read once)
_info: Optional[dict] = None


def device_info() -> dict:
    global _info
    if _info is not None:
        return _info
    from .display_identity import computer_identity
    raw = _ps("$o=[ordered]@{};"
              "$p=Get-CimInstance Win32_Processor|Select-Object -First 1 Name,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed;"
              "$o.cpu=$p;$o.gpus=@(Get-CimInstance Win32_VideoController|Select-Object Name,DriverVersion,AdapterRAM);"
              "$o.board=Get-CimInstance Win32_BaseBoard|Select-Object -First 1 Manufacturer,Product;"
              "$o.system=Get-CimInstance Win32_ComputerSystem|Select-Object -First 1 Manufacturer,Model,PCSystemType,TotalPhysicalMemory;"
              "$o.chassis=@((Get-CimInstance Win32_SystemEnclosure|Select-Object -First 1).ChassisTypes);"
              "$o.battery=@(Get-CimInstance Win32_Battery).Count;"
              "$o.os=(Get-CimInstance Win32_OperatingSystem|Select-Object -First 1 Caption,BuildNumber);"
              "$o|ConvertTo-Json -Depth 4 -Compress")
    try:
        d = json.loads(raw)
    except Exception:
        d = {}
    chassis = d.get("chassis") or []
    laptop_types = {8, 9, 10, 11, 12, 14, 18, 21, 30, 31, 32}
    is_laptop = bool(d.get("battery")) and (bool(set(chassis) & laptop_types) or (d.get("system") or {}).get("PCSystemType") == 2)
    gpus = [g.get("Name") for g in (d.get("gpus") or []) if g.get("Name") and "basic display" not in g.get("Name", "").lower()]
    _info = {
        **computer_identity(),
        "cpu": ((d.get("cpu") or {}).get("Name") or "CPU").strip(),
        "cores": (d.get("cpu") or {}).get("NumberOfCores"), "threads": (d.get("cpu") or {}).get("NumberOfLogicalProcessors"),
        "base_mhz": (d.get("cpu") or {}).get("MaxClockSpeed"),
        "gpus": gpus, "board": " ".join(filter(None, [(d.get("board") or {}).get("Manufacturer"), (d.get("board") or {}).get("Product")])).strip(),
        "maker": (d.get("system") or {}).get("Manufacturer"), "model": (d.get("system") or {}).get("Model"),
        "type": "laptop" if is_laptop else "desktop", "has_battery": bool(d.get("battery")),
        "os": f"{(d.get('os') or {}).get('Caption', 'Windows')} (build {(d.get('os') or {}).get('BuildNumber', '?')})",
    }
    return _info


def _is_integrated(name: str) -> bool:
    n = name.lower()
    return any(k in n for k in ("uhd graphics", "iris", "intel(r) hd", "radeon(tm) graphics", "radeon graphics", "vega", "arc(tm) graphics")) and "rx " not in n


# ----------------------------------------------------------------------------- other monitoring apps
MONITORS = {
    "LibreHardwareMonitor": ["LibreHardwareMonitor.exe"], "OpenHardwareMonitor": ["OpenHardwareMonitor.exe"],
    "HWiNFO": ["HWiNFO64.exe", "HWiNFO32.exe"], "AIDA64": ["aida64.exe"], "NZXT CAM": ["NZXT CAM.exe"],
    "MSI Afterburner": ["MSIAfterburner.exe"], "FanControl": ["FanControl.exe"], "OCCT": ["OCCTGUI.exe", "OCCT.exe"],
    "Core Temp": ["Core Temp.exe"], "Armoury Crate": ["ArmouryCrate.exe"], "G-Helper": ["GHelper.exe"],
}


def detect_monitors() -> dict:
    running = {p.info["name"].lower() for p in psutil.process_iter(["name"]) if p.info.get("name")}
    found = [name for name, exes in MONITORS.items() if any(e.lower() in running for e in exes)]
    lhm_installed = bool(shutil.which("LibreHardwareMonitor")) or any(
        os.path.exists(p) for p in (os.path.expandvars(r"%ProgramFiles%\LibreHardwareMonitor\LibreHardwareMonitor.exe"),))
    if not lhm_installed:
        base = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages")
        if os.path.isdir(base):
            lhm_installed = any(d.lower().startswith("librehardwaremonitor") for d in os.listdir(base))
    return {"running": found, "lhm_installed": lhm_installed, "lhm_running": "LibreHardwareMonitor" in found}


# ----------------------------------------------------------------------------- live sampler
class NativeSampler:
    def __init__(self):
        self.pdh = Pdh([C_PERF, C_FREQ, C_THERM, C_GPU, C_GMEM]) if IS_WIN else None
        self.nvsmi = shutil.which("nvidia-smi") or (r"C:\Windows\System32\nvidia-smi.exe" if os.path.exists(r"C:\Windows\System32\nvidia-smi.exe") else None)
        self._last_io = None
        self._last_net = None
        self._last_t = time.time()
        psutil.cpu_percent(percpu=True)
        self._lock = threading.Lock()

    def _nvidia(self) -> List[dict]:
        if not self.nvsmi:
            return []
        try:
            out = subprocess.run([self.nvsmi, "--query-gpu=name,temperature.gpu,utilization.gpu,power.draw,fan.speed,memory.used,memory.total,clocks.gr,clocks.mem",
                                  "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=4, creationflags=NO_WINDOW).stdout
        except Exception:
            return []
        rows = []
        for line in out.strip().splitlines():
            f = [x.strip() for x in line.split(",")]
            if len(f) >= 9:
                num = lambda s: float(s) if s.replace(".", "", 1).isdigit() else None
                rows.append({"name": f[0], "temp": num(f[1]), "load": num(f[2]), "power": num(f[3]), "fan": num(f[4]),
                             "mem_used": num(f[5]), "mem_total": num(f[6]), "clock": num(f[7]), "mem_clock": num(f[8])})
        return rows

    def sample(self) -> dict:
        with self._lock:
            info = device_info()
            c = self.pdh.collect() if self.pdh else {}
            now = time.time(); dt = max(0.5, now - self._last_t); self._last_t = now
            groups = []
            def S(i, name, typ, val, unit, mn=None, mx=None):
                return {"id": i, "name": name, "type": typ, "value": round(float(val), 2), "min": mn, "max": mx, "unit": unit}

            # CPU
            per = psutil.cpu_percent(percpu=True)
            cpu = [S("/native/cpu/load/total", "CPU Total", "Load", sum(per) / max(1, len(per)), "%")]
            cpu += [S(f"/native/cpu/load/{i}", f"Thread #{i + 1}", "Load", v, "%") for i, v in enumerate(per)]
            perf = (c.get(C_PERF) or {}).get("_Total"); base = (c.get(C_FREQ) or {}).get("_Total") or info.get("base_mhz")
            if perf and base:
                cpu.append(S("/native/cpu/clock/effective", "Effective clock", "Clock", base * perf / 100.0, "MHz"))
            # ACPI thermal zones: on many desktops this is a board/ambient value, not the CPU, so it is labelled as such.
            # Values below 20 °C are placeholders some boards report and are ignored.
            temps = {k: v / 10.0 - 273.15 for k, v in (c.get(C_THERM) or {}).items() if v and 2930 < v < 4000}
            for i, (k, v) in enumerate(sorted(temps.items())):
                cpu.append(S(f"/native/thermal/{i}", f"Thermal zone {i + 1} (ACPI)", "Temperature", v, "°C"))
            groups.append({"name": info["cpu"], "kind": "cpu", "sensors": cpu})

            # GPUs: PDH per adapter LUID (any vendor) + nvidia-smi extras
            luid_load, luid_mem = {}, {}
            for inst, v in (c.get(C_GPU) or {}).items():
                if "engtype_3d" in inst.lower():
                    l = inst.split("_phys")[0].split("pid_")[-1].split("luid_")[-1]
                    luid_load[l] = luid_load.get(l, 0.0) + v
            for inst, v in (c.get(C_GMEM) or {}).items():
                l = inst.split("_phys")[0].split("luid_")[-1]
                luid_mem[l] = luid_mem.get(l, 0.0) + v
            names = sorted(info.get("gpus") or ["GPU"], key=_is_integrated)  # discrete first
            adapters = sorted(set(luid_load) | set(luid_mem), key=lambda l: -luid_mem.get(l, 0))  # most VRAM in use first
            nv = self._nvidia()
            for i, l in enumerate(adapters[:max(1, len(names))]):
                nm = names[i] if i < len(names) else f"GPU {i + 1}"
                s = [S(f"/native/gpu/{i}/load/core", "GPU Core", "Load", min(100.0, luid_load.get(l, 0.0)), "%"),
                     S(f"/native/gpu/{i}/smalldata/used", "GPU Memory Used", "SmallData", luid_mem.get(l, 0.0) / 1048576, "MB")]
                n = next((g for g in nv if g["name"].lower() in nm.lower() or nm.lower() in g["name"].lower()), None)
                if n:
                    if n["temp"] is not None: s.append(S(f"/native/gpu/{i}/temperature/core", "GPU Core", "Temperature", n["temp"], "°C"))
                    if n["power"] is not None: s.append(S(f"/native/gpu/{i}/power/package", "GPU Package", "Power", n["power"], "W"))
                    if n["fan"] is not None: s.append(S(f"/native/gpu/{i}/control/fan", "GPU Fan", "Control", n["fan"], "%"))
                    if n["clock"] is not None: s.append(S(f"/native/gpu/{i}/clock/core", "GPU Core", "Clock", n["clock"], "MHz"))
                groups.append({"name": nm, "kind": "gpu", "sensors": s})

            # Memory
            vm = psutil.virtual_memory()
            groups.append({"name": "Total Memory", "kind": "memory", "sensors": [
                S("/native/ram/load", "Memory", "Load", vm.percent, "%"),
                S("/native/ram/data/used", "Memory Used", "Data", vm.used / 1073741824, "GB"),
                S("/native/ram/data/available", "Memory Available", "Data", vm.available / 1073741824, "GB")]})

            # Drives (usage + throughput)
            io = psutil.disk_io_counters(perdisk=False)
            rd = wr = 0.0
            if io and self._last_io:
                rd = (io.read_bytes - self._last_io.read_bytes) / dt; wr = (io.write_bytes - self._last_io.write_bytes) / dt
            self._last_io = io
            vols = []
            for p in psutil.disk_partitions(all=False):
                if "cdrom" in p.opts or not p.fstype:
                    continue
                try:
                    u = psutil.disk_usage(p.mountpoint)
                    vols.append(S(f"/native/vol/{p.device}", f"{p.device.rstrip(chr(92))} used", "Load", u.percent, "%"))
                except Exception:
                    pass
            groups.append({"name": "Drives", "kind": "storage", "sensors": vols + [
                S("/native/disk/read", "Read rate", "Throughput", rd, "B/s"), S("/native/disk/write", "Write rate", "Throughput", wr, "B/s")]})

            # Network
            ni = psutil.net_io_counters()
            up = dn = 0.0
            if ni and self._last_net:
                up = (ni.bytes_sent - self._last_net.bytes_sent) / dt; dn = (ni.bytes_recv - self._last_net.bytes_recv) / dt
            self._last_net = ni
            groups.append({"name": "Network", "kind": "network", "sensors": [
                S("/native/net/up", "Upload speed", "Throughput", up, "B/s"), S("/native/net/down", "Download speed", "Throughput", dn, "B/s")]})

            # Battery (laptops)
            b = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
            if b is not None:
                groups.append({"name": "Battery", "kind": "battery", "sensors": [S("/native/battery/level", "Charge Level", "Level", b.percent, "%")]})
            return {"groups": groups, "info": info}


_sampler: Optional[NativeSampler] = None


def native_groups() -> dict:
    global _sampler
    if _sampler is None:
        _sampler = NativeSampler()
    return _sampler.sample()


if __name__ == "__main__":
    import pprint
    native_groups(); time.sleep(1.2)
    d = native_groups()
    pprint.pprint(d["info"])
    for g in d["groups"]:
        print(g["kind"], "|", g["name"], "|", [(s["name"], s["value"], s["unit"]) for s in g["sensors"][:6]])
    print(detect_monitors())
