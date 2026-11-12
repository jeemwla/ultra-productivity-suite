"""Optional "Ultra Suite Max Performance" power plan (owner request, 2026-11-11).

Windows has no hidden plan above Ultimate Performance. This creates a copy of Ultimate Performance (or High
performance where Ultimate is unavailable) once, names it, and sets plugged-in (AC) values for the settings that
still save power in Ultimate. Battery (DC) values are left as Windows set them, so laptops on battery are unchanged.
It is only applied when the user asks, and the previous plan can be restored with one click. Existing plans are never
modified or deleted. Expect small, workload-dependent gains and more heat and power use.

Setting GUIDs are Microsoft's documented power setting identifiers; hidden settings are written by GUID because their
aliases may not be visible (on the owner PC, CPMINCORES and PERFBOOSTMODE aliases returned nothing).
"""
import json
import os
import re
import subprocess

PLAN_NAME = "Ultra Suite Max Performance"
PLAN_DESCRIPTION = "Ultimate Performance plus no core parking, 100% minimum processor state and no device power saving while plugged in."
ULTIMATE_TEMPLATE = "e9a42b02-d5df-448d-aa00-03f14749eb61"
HIGH_PERFORMANCE = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
GUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"

SUB_PROCESSOR = "54533251-82be-4824-96c1-47b60b740d00"
SUB_PCIE = "501a4d13-42af-4429-9fd1-a8218c268e20"
SUB_USB = "2a737441-1930-4402-8d77-b2bebba308a3"
SUB_DISK = "0012ee47-9041-4b5d-9b77-535fba8b1442"
# (subgroup, setting, AC value, plain description)
AC_SETTINGS = [
    (SUB_PROCESSOR, "893dee8e-2bef-41e0-89c6-b55d0929964c", 100, "Minimum processor state 100%"),
    (SUB_PROCESSOR, "bc5038f7-23e0-4960-96da-33abaf5935ec", 100, "Maximum processor state 100%"),
    (SUB_PROCESSOR, "0cc5b647-c1df-4637-891a-dec35c318583", 100, "Core parking off (all cores unparked)"),
    (SUB_PROCESSOR, "be337238-0d82-4146-a960-4f3749d470c7", 2, "Processor boost mode aggressive"),
    (SUB_PROCESSOR, "36687f9e-e3a5-4dbf-b1dc-15eb381c6863", 0, "Energy performance preference: maximum performance"),
    (SUB_PCIE, "ee12f906-d277-404b-b6da-e5fa1a576df5", 0, "PCI Express link power saving off"),
    (SUB_USB, "48e6b7a6-50f5-4782-a5d4-53bb8f07e226", 0, "USB selective suspend off"),
    (SUB_DISK, "6738e2c4-e8a5-4a42-b16a-e040e769756e", 0, "Hard disk never turns off"),
]


# ---------------------------------------------------------------------------------------------- device profile
# Which settings help depends on the machine (owner asked for every laptop and chip). Sources and reasoning:
# - Modern Standby (S0) laptops and Windows on Arm (Snapdragon) mostly expose only Balanced; copying Ultimate can fail
#   or displace another plan there, and Microsoft's supported lever is the power mode slider set to Best performance.
# - Dual-CCD Ryzen X3D desktops (7900X3D/7950X3D/9900X3D/9950X3D): AMD's gaming optimization relies on core parking
#   under the Balanced plan, so forcing all cores awake works against it; use Balanced + Best performance.
# - Intel hybrid (P- and E-cores): Thread Director places work; no solid evidence that disabling parking helps, so
#   core parking is left to Windows. Laptops: holding 100% minimum clock and all cores awake mostly adds heat and can
#   reduce boost headroom, so only boost, energy preference and device power saving are changed, plugged in only.
MIN_STATE, CORE_PARKING = "893dee8e-2bef-41e0-89c6-b55d0929964c", "0cc5b647-c1df-4637-891a-dec35c318583"


def detect_device():
    """Facts from Windows APIs (language-independent). Unknown values are False/0/empty."""
    info = {"battery": False, "modern_standby": False, "arm64": False, "hybrid": False, "cpu_name": "", "physical_cores": 0}
    if os.name != "nt":
        return info
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class SPS(ctypes.Structure):
        _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte), ("BatteryLifePercent", ctypes.c_ubyte),
                    ("SystemStatusFlag", ctypes.c_ubyte), ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD)]
    try:
        sps = SPS()
        if k32.GetSystemPowerStatus(ctypes.byref(sps)):
            info["battery"] = sps.BatteryFlag not in (128, 255)  # 128 = no system battery, 255 = unknown
    except (OSError, AttributeError):
        pass
    try:  # SYSTEM_POWER_CAPABILITIES.AoAc (Modern Standby) is the BOOLEAN at byte offset 20
        buf = (ctypes.c_ubyte * 128)()
        if ctypes.WinDLL("powrprof").CallNtPowerInformation(4, None, 0, ctypes.byref(buf), ctypes.sizeof(buf)) == 0:
            info["modern_standby"] = bool(buf[20])
    except (OSError, AttributeError):
        pass
    try:
        pm, nm = ctypes.c_ushort(), ctypes.c_ushort()
        if k32.IsWow64Process2(k32.GetCurrentProcess(), ctypes.byref(pm), ctypes.byref(nm)):
            info["arm64"] = nm.value == 0xAA64
    except (OSError, AttributeError):
        info["arm64"] = os.environ.get("PROCESSOR_ARCHITECTURE", "").upper() == "ARM64"
    try:  # one PROCESSOR_RELATIONSHIP per physical core; EfficiencyClass is at byte 9 of each record
        need = wintypes.DWORD(0)
        k32.GetLogicalProcessorInformationEx(0, None, ctypes.byref(need))
        raw = (ctypes.c_ubyte * need.value)()
        if need.value and k32.GetLogicalProcessorInformationEx(0, ctypes.byref(raw), ctypes.byref(need)):
            data, off, classes, cores = bytes(raw), 0, set(), 0
            while off + 10 <= len(data):
                size = int.from_bytes(data[off + 4:off + 8], "little")
                if size <= 0:
                    break
                classes.add(data[off + 9]); cores += 1; off += size
            info["hybrid"], info["physical_cores"] = len(classes) > 1, cores
    except (OSError, AttributeError, ValueError):
        pass
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
            info["cpu_name"] = str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
    except OSError:
        pass
    return info


def choose_profile(info):
    """The strongest safe approach for this machine: a tuned plan, or Windows' Best performance power mode."""
    name = (info.get("cpu_name") or "").upper()
    all_settings = [d for *_, d in AC_SETTINGS]
    def plan(kind, title, why, skip=()):
        keep = [x for x in AC_SETTINGS if x[1] not in skip]
        return {"kind": kind, "method": "plan", "title": title, "why": why, "settings": [d for *_, d in keep],
                "skipped": [d for _, g, _, d in AC_SETTINGS if g in skip]}
    def mode(kind, title, why):
        return {"kind": kind, "method": "power_mode", "title": title, "why": why,
                "settings": ["Windows power mode: Best performance (plugged in and on battery)"], "skipped": all_settings}
    if info.get("arm64"):
        return mode("arm", "Windows on Arm (for example Snapdragon)",
                    "These PCs are tuned around Windows' power modes; custom plans are usually not available. Best performance is the strongest supported setting.")
    if info.get("modern_standby"):
        return mode("modern_standby", "Laptop with Modern Standby",
                    "Windows mostly allows only the Balanced plan here, and adding plans can fail or replace another plan. Best performance on Balanced is the supported way to get full speed. For more, also use your laptop maker's performance mode (for example Turbo in Armoury Crate or G-Helper).")
    if "X3D" in name and info.get("physical_cores", 0) > 8:
        return mode("x3d_dual", "Ryzen X3D with two core complexes",
                    "AMD's game optimization parks the second core complex under the Balanced plan, so forcing all cores awake can lower game performance. Best performance on Balanced keeps that optimization working.")
    if info.get("battery"):
        return plan("laptop", "Laptop", "Plugged in: maximum boost and no device power saving. Minimum clock and core parking stay with Windows, because forcing them mostly adds heat and can reduce boost on laptops. Battery settings are not changed.",
                    skip=(MIN_STATE, CORE_PARKING))
    if info.get("hybrid"):
        return plan("hybrid", "Desktop with performance and efficiency cores", "Maximum clocks and boost with no device power saving. Core parking stays with Windows so Thread Director can place work on the right cores.",
                    skip=(CORE_PARKING,))
    return plan("desktop", "Desktop", "Every core awake at full clock, aggressive boost and no device power saving while plugged in.")


def _run(args):
    return subprocess.run(args, capture_output=True, text=True, timeout=20,
                          creationflags=0x08000000 if os.name == "nt" else 0)  # CREATE_NO_WINDOW


def _state_path():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "UltraSuite", "max_power.json")


def list_plans(run=_run):
    out = run(["powercfg", "/list"]).stdout or ""
    plans = []
    for line in out.splitlines():
        m = re.search(rf"({GUID})\s+\((.+?)\)\s*(\*)?", line)
        if m:
            plans.append({"guid": m.group(1).lower(), "name": m.group(2), "active": bool(m.group(3))})
    return plans


def _read_state(path=None):
    try:
        with open(path or _state_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(data, path=None):
    path = path or _state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def _duplicate(template, run):
    r = run(["powercfg", "/duplicatescheme", template])
    m = re.search(GUID, r.stdout or "")
    return m.group(0).lower() if r.returncode == 0 and m else None


def ensure_plan(run=_run, settings=None):
    """Create the plan once (reusing it afterwards) and (re)apply its AC values. Returns (guid, failed_settings)."""
    settings = AC_SETTINGS if settings is None else settings
    existing = next((p for p in list_plans(run) if p["name"] == PLAN_NAME), None)
    guid = existing["guid"] if existing else (_duplicate(ULTIMATE_TEMPLATE, run) or _duplicate(HIGH_PERFORMANCE, run))
    if not guid:
        raise RuntimeError("Windows did not create the plan.")
    if not existing:
        run(["powercfg", "/changename", guid, PLAN_NAME, PLAN_DESCRIPTION])
    failed = [desc for sub, setting, value, desc in settings
              if run(["powercfg", "/setacvalueindex", guid, sub, setting, str(value)]).returncode != 0]
    return guid, failed


def _mode_api():
    from . import system_tools
    return system_tools.power_mode, system_tools.set_power_mode


def _profile(device):
    return choose_profile(detect_device() if device is None else device)


def status(run=_run, state_path=None, device=None, mode_get=None):
    profile, saved = _profile(device), _read_state(state_path)
    if profile["method"] == "power_mode":
        m = (mode_get or _mode_api()[0])()
        return {"name": "Best performance", "profile": profile, "exists": False, "guid": None,
                "active": m.get("mode") == "performance", "mode_available": bool(m.get("available")),
                "previous": saved.get("previous_mode") or None, "settings": profile["settings"]}
    plans = list_plans(run)
    mine = next((p for p in plans if p["name"] == PLAN_NAME), None)
    previous = saved.get("previous")
    return {"name": PLAN_NAME, "profile": profile, "exists": bool(mine), "active": bool(mine and mine["active"]),
            "guid": mine["guid"] if mine else None,
            "previous": previous if any(p["guid"] == previous for p in plans) else None,
            "settings": profile["settings"]}


def apply(activate=True, run=_run, state_path=None, device=None, mode_get=None, mode_set=None):
    profile = _profile(device)
    if profile["method"] == "power_mode":
        get, put = mode_get or _mode_api()[0], mode_set or _mode_api()[1]
        current = get()
        if not current.get("available"):
            raise RuntimeError("This PC does not offer Windows power modes.")
        if activate:
            if current.get("mode") != "performance":
                _write_state({"previous_mode": current.get("mode") or "balanced"}, state_path)
            put("performance")
        return {**status(run, state_path, device, get), "failed_settings": []}
    plans = list_plans(run)
    current = next((p for p in plans if p["active"]), None)
    chosen = [x for x in AC_SETTINGS if x[3] in profile["settings"]]
    guid, failed = ensure_plan(run, chosen)
    if activate:
        if current and current["guid"] != guid:
            _write_state({"previous": current["guid"], "previous_name": current["name"]}, state_path)
        if run(["powercfg", "/setactive", guid]).returncode != 0:
            raise RuntimeError("Windows didn't accept that power plan.")
    return {**status(run, state_path, device), "failed_settings": failed}


def revert(run=_run, state_path=None, device=None, mode_get=None, mode_set=None):
    profile, saved = _profile(device), _read_state(state_path)
    if profile["method"] == "power_mode":
        previous = saved.get("previous_mode")
        if not previous:
            raise LookupError("No previous power mode is saved.")
        (mode_set or _mode_api()[1])(previous)
        return status(run, state_path, device, mode_get)
    previous = saved.get("previous")
    plans = list_plans(run)
    if not previous or not any(p["guid"] == previous for p in plans):
        raise LookupError("No previous plan is saved.")
    if run(["powercfg", "/setactive", previous]).returncode != 0:
        raise RuntimeError("Windows didn't accept that power plan.")
    return status(run, state_path, device)


def register(app):
    import asyncio
    from fastapi import HTTPException

    @app.get("/api/power/max")
    async def _status():
        if os.name != "nt":
            return {"name": PLAN_NAME, "exists": False, "available": False}
        return {**await asyncio.to_thread(status), "available": True}

    @app.post("/api/power/max")
    async def _apply(body: dict | None = None):
        if os.name != "nt":
            raise HTTPException(400, "Power plans are a Windows feature.")
        try:
            return await asyncio.to_thread(apply, bool((body or {}).get("activate", True)))
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(500, str(exc)) from None

    @app.post("/api/power/max/revert")
    async def _revert():
        try:
            return await asyncio.to_thread(revert)
        except LookupError as exc:
            raise HTTPException(409, str(exc)) from None
        except RuntimeError as exc:
            raise HTTPException(500, str(exc)) from None
