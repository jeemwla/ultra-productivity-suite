"""Developer preview mode: every page runs on fixture data and nothing on this PC is changed.

Turn it on with SENTINEL_PREVIEW=desktop or SENTINEL_PREVIEW=laptop. It also needs ULTRASUITE_CONF pointing at a
throwaway folder, so saved settings never reach the real Sentinel settings. The installed app never sets either variable.

The laptop preview shows the Battery page, the Overview battery card and the Cooling note for a laptop that reports
no fans, so those screens can be checked on a desktop. The battery cycles through on battery, charging and plugged in
(not charging) every 45 seconds; SENTINEL_PREVIEW_BATTERY=discharging|charging|plugged holds one state.

Routes that would change Windows (power plans and mode, Max Performance, refresh rate, brightness) keep their choice
in memory only. Routes that would install software or run a long measurement answer 409 with a plain explanation.
"""
import copy
import json
import math
import os
import time
from pathlib import Path

from fastapi import HTTPException

from . import laptop

KINDS = ("desktop", "laptop")
BATTERY_STATES = ("discharging", "charging", "plugged")
FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"
REFUSED = "Preview mode: this action is turned off, so nothing on this PC changes."


def mode(environ=None):
    """The preview kind, or None when preview mode is off. Misconfiguration fails loudly instead of touching real data."""
    environ = os.environ if environ is None else environ
    value = (environ.get("SENTINEL_PREVIEW") or "").strip().lower()
    if not value:
        return None
    if value not in KINDS:
        raise RuntimeError("SENTINEL_PREVIEW must be 'desktop' or 'laptop'.")
    if not (environ.get("ULTRASUITE_CONF") or "").strip():
        raise RuntimeError("SENTINEL_PREVIEW needs ULTRASUITE_CONF set to a throwaway folder.")
    return value


def load_fixture(folder=FIXTURES):
    with open(Path(folder) / "sentinel_preview.json", encoding="utf-8") as fh:
        return json.load(fh)


def _wave(t, period, lo, hi, phase=0.0):
    return lo + (hi - lo) * (0.5 + 0.5 * math.sin(2 * math.pi * (t / period) + phase))


def laptopize(snapshot):
    """Turns the desktop capture into a plausible laptop: a battery, no fan readings, laptop chassis."""
    s = copy.deepcopy(snapshot)
    hw = s["hardware"]
    dev = hw["device"]
    dev.update(type="laptop", has_battery=True, form_basis="Preview: laptop fixture (no real battery is read).",
               maker="Preview", model="Laptop preview", board="Preview laptop board")
    hw["cooling"] = {"fans": [], "controls": 0, "has_fan_data": False, "fancontrol": False, "fancontrol_path": None}
    summary = hw.setdefault("summary", {})
    summary.update(fans=[], fan_max_rpm=None)
    for group in hw.get("groups", []):
        group["sensors"] = [x for x in group.get("sensors", []) if x.get("type") not in ("Fan", "Control")]
    hw["sensor_count"] = sum(len(g.get("sensors", [])) for g in hw.get("groups", []))
    return s


def battery_state(t, environ=None):
    environ = os.environ if environ is None else environ
    held = (environ.get("SENTINEL_PREVIEW_BATTERY") or "").strip().lower()
    if held in BATTERY_STATES:
        return held
    return BATTERY_STATES[int(t // 45) % len(BATTERY_STATES)]


def battery(t, report, environ=None):
    """A battery built by the real merge code (laptop.build_battery) from synthetic Windows rows."""
    state = battery_state(t, environ)
    pct = round(_wave(t, 600, 35, 85))
    full, design = 68_400, 76_000
    remaining = round(full * pct / 100)
    charging, online = state == "charging", state != "discharging"
    status = {"ChargeRate": 45_000 if charging else 0, "DischargeRate": 0 if online else round(_wave(t, 40, 9_000, 21_000)),
              "RemainingCapacity": remaining, "Voltage": 15_800, "PowerOnline": online, "Charging": charging,
              "Discharging": not online, "Critical": False}
    rows = {"static": [{"DesignedCapacity": design, "ManufactureName": "Preview", "DeviceName": "Preview battery",
                        "Chemistry": 0x4E4F494C}],
            "full": [{"FullChargedCapacity": full}], "cycle": [{"CycleCount": 212}], "status": [status],
            "win": [{"EstimatedChargeRemaining": pct, "EstimatedRunTime": 71582788 if online else 180}]}
    sps = {"ac": online, "charging": charging, "no_battery": False, "percent": pct, "seconds_left": None}
    return laptop.build_battery(rows, sps, report)


def snapshot(base, t):
    """One live-looking snapshot: loads, temperatures, power, fans and latency move gently over time."""
    s = copy.deepcopy(base)
    hw, net = s["hardware"], s["network"]
    summary = hw.setdefault("summary", {})
    cpu_load = _wave(t, 37, 8, 72)
    summary.update(cpu_load=round(cpu_load, 1), cpu_temp=round(42 + cpu_load * 0.45, 1),
                   gpu_load=round(_wave(t, 53, 2, 64, 1.3), 1), gpu_temp=round(_wave(t, 53, 38, 66, 1.3), 1),
                   ram_load=round(_wave(t, 300, 48, 71), 1))
    if summary.get("ram_total_gb"):
        summary["ram_used_gb"] = round(summary["ram_total_gb"] * summary["ram_load"] / 100, 1)
    if summary.get("gpu_hotspot") is not None:
        summary["gpu_hotspot"] = round(summary["gpu_temp"] + 11, 1)
    if summary.get("cpu_power") is not None:
        summary["cpu_power"] = round(20 + cpu_load * 0.9, 1)
    if summary.get("gpu_power") is not None:
        summary["gpu_power"] = round(12 + summary["gpu_load"] * 1.4, 1)
    measured = (hw.get("power") or {}).get("measured") or {}
    if "CPU package" in measured and summary.get("cpu_power") is not None:
        measured["CPU package"] = summary["cpu_power"]
    if "Graphics card" in measured and summary.get("gpu_power") is not None:
        measured["Graphics card"] = summary["gpu_power"]
    if measured and isinstance(hw.get("power"), dict):
        hw["power"]["measured_subtotal_w"] = round(sum(v for v in measured.values() if isinstance(v, (int, float))), 1)
    for group in hw.get("groups", []):
        for i, sensor in enumerate(group.get("sensors", [])):
            if sensor.get("type") == "Load" and isinstance(sensor.get("value"), (int, float)) and group.get("kind") == "cpu":
                sensor["value"] = round(_wave(t, 11 + i % 7, 2, min(100, cpu_load * 1.6), i), 1)
    for i, fan in enumerate((hw.get("cooling") or {}).get("fans", [])):
        if isinstance(fan.get("rpm"), (int, float)) and fan.get("rpm"):
            fan["rpm"] = round(fan["rpm"] * (0.9 + 0.2 * _wave(t, 29, 0, 1, i)))
    for i, target in enumerate(net.get("targets", [])):
        target["latency_ms"] = round(_wave(t, 19, 9, 24, i), 1)
    if net.get("targets"):
        net["primary_ping_ms"] = net["targets"][0]["latency_ms"]
    net["jitter_ms"] = round(_wave(t, 23, 0.8, 4.5), 1)
    s["ts"] = time.time()
    return s


class Preview:
    def __init__(self, kind, fixture=None, environ=None):
        self.kind = kind
        self.environ = os.environ if environ is None else environ
        self.fx = fixture or load_fixture()
        base = self.fx["snapshot"]
        self.base = laptopize(base) if kind == "laptop" else base
        self.report = {}
        if kind == "laptop":
            with open(FIXTURES / "battery_report_laptop.xml", "rb") as fh:
                self.report = laptop.parse_battery_report(fh.read())
        self.plans = copy.deepcopy(self.fx["plans"])
        self.power_mode = copy.deepcopy(self.fx["power_mode"])
        self.display = copy.deepcopy(self.fx["display"])
        self.brightness = 70

    def snapshot(self, t=None):
        return snapshot(self.base, time.time() if t is None else t)

    def battery(self, t=None):
        if self.kind != "laptop":
            return {"has_battery": False, "pack": laptop.PACK_VERSION}
        return battery(time.time() if t is None else t, self.report, self.environ)

    def register(self, app):
        """Registered before the real routes, so these answer first. Starlette uses the first matching route."""
        fx = self.fx

        def refuse():
            raise HTTPException(409, REFUSED)

        @app.get("/api/preview", include_in_schema=False)
        async def _preview():
            return {"preview": True, "kind": self.kind}

        @app.get("/api/processes")
        async def _processes(all_apps: bool = False):
            return fx["processes"]

        @app.get("/api/processes/{group_id}/icon")
        async def _icon(group_id: str):
            raise HTTPException(404, "Preview mode has no app icons.")

        @app.get("/api/processes/{group_id}")
        async def _process(group_id: str):
            raise HTTPException(404, "Preview mode has no app details.")

        @app.get("/api/power/plans")
        async def _plans():
            return self.plans

        @app.post("/api/power/plans")
        async def _set_plan(body: dict):
            guid = body.get("guid")
            if not any(p["guid"] == guid for p in self.plans["plans"]):
                raise HTTPException(400, "Unknown power plan.")
            for p in self.plans["plans"]:
                p["active"] = p["guid"] == guid
            return self.plans

        @app.get("/api/power/mode")
        async def _mode():
            return self.power_mode

        @app.post("/api/power/mode")
        async def _set_mode(body: dict):
            if body.get("mode") not in ("efficiency", "balanced", "performance"):
                raise HTTPException(400, "Unknown power mode.")
            self.power_mode = {**self.power_mode, "mode": body["mode"]}
            return self.power_mode

        @app.get("/api/display")
        async def _display():
            return self.display

        @app.post("/api/display")
        async def _set_display(body: dict):
            for d in self.display.get("displays", []):
                if d.get("device") == body.get("device") and body.get("hz") in d.get("rates", []):
                    d["hz"] = body["hz"]
                    return self.display
            raise HTTPException(400, "Unknown display or refresh rate.")

        @app.get("/api/power/max")
        async def _max():
            value = copy.deepcopy(fx["power_max"])
            if self.kind == "laptop":
                value["profile"] = {**value.get("profile", {}), "kind": "laptop", "title": "Laptop"}
            return value

        @app.post("/api/power/max")
        async def _set_max():
            refuse()

        @app.post("/api/power/max/revert")
        async def _revert_max():
            refuse()

        @app.get("/api/power/benchmark")
        async def _bench():
            return {"runs": [], "before": None, "after": None, "compare": None}

        @app.post("/api/power/benchmark")
        async def _run_bench():
            refuse()

        @app.get("/api/storage/writers")
        async def _writers():
            return fx["writers"]

        @app.post("/api/storage/writers/measure")
        async def _measure():
            refuse()

        @app.post("/api/setup/lhm")
        async def _setup():
            refuse()

        @app.post("/api/vpn/toggle")
        async def _vpn():
            refuse()

        @app.get("/api/laptop/battery")
        async def _battery():
            return self.battery()

        @app.get("/api/laptop/battery/history")
        async def _history():
            return {"history": self.report.get("history", []) if self.kind == "laptop" else []}

        @app.get("/api/laptop/brightness")
        async def _brightness():
            if self.kind != "laptop":
                return {"available": False, "percent": None}
            return {"available": True, "percent": self.brightness}

        @app.post("/api/laptop/brightness")
        async def _set_brightness(body: dict):
            if self.kind != "laptop":
                refuse()
            value = body.get("percent")
            if type(value) is not int or not 0 <= value <= 100:
                raise HTTPException(422, "Brightness must be a whole number from 0 to 100.")
            self.brightness = value
            return {"available": True, "percent": value}
