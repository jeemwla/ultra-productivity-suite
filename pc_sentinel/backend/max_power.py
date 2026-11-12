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


def status(run=_run, state_path=None):
    plans = list_plans(run)
    mine = next((p for p in plans if p["name"] == PLAN_NAME), None)
    previous = _read_state(state_path).get("previous")
    return {"name": PLAN_NAME, "exists": bool(mine), "active": bool(mine and mine["active"]),
            "guid": mine["guid"] if mine else None,
            "previous": previous if any(p["guid"] == previous for p in plans) else None,
            "settings": [d for *_, d in AC_SETTINGS]}


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


def ensure_plan(run=_run):
    """Create the plan once (reusing it afterwards) and (re)apply its AC values. Returns (guid, failed_settings)."""
    existing = next((p for p in list_plans(run) if p["name"] == PLAN_NAME), None)
    guid = existing["guid"] if existing else (_duplicate(ULTIMATE_TEMPLATE, run) or _duplicate(HIGH_PERFORMANCE, run))
    if not guid:
        raise RuntimeError("Windows did not create the plan.")
    if not existing:
        run(["powercfg", "/changename", guid, PLAN_NAME, PLAN_DESCRIPTION])
    failed = [desc for sub, setting, value, desc in AC_SETTINGS
              if run(["powercfg", "/setacvalueindex", guid, sub, setting, str(value)]).returncode != 0]
    return guid, failed


def apply(activate=True, run=_run, state_path=None):
    plans = list_plans(run)
    current = next((p for p in plans if p["active"]), None)
    guid, failed = ensure_plan(run)
    if activate:
        if current and current["guid"] != guid:
            _write_state({"previous": current["guid"], "previous_name": current["name"]}, state_path)
        if run(["powercfg", "/setactive", guid]).returncode != 0:
            raise RuntimeError("Windows didn't accept that power plan.")
    return {**status(run, state_path), "failed_settings": failed}


def revert(run=_run, state_path=None):
    previous = _read_state(state_path).get("previous")
    plans = list_plans(run)
    if not previous or not any(p["guid"] == previous for p in plans):
        raise LookupError("No previous plan is saved.")
    if run(["powercfg", "/setactive", previous]).returncode != 0:
        raise RuntimeError("Windows didn't accept that power plan.")
    return status(run, state_path)


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
        except RuntimeError as exc:
            raise HTTPException(500, str(exc)) from None

    @app.post("/api/power/max/revert")
    async def _revert():
        try:
            return await asyncio.to_thread(revert)
        except LookupError as exc:
            raise HTTPException(409, str(exc)) from None
        except RuntimeError as exc:
            raise HTTPException(500, str(exc)) from None
