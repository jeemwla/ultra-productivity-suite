"""Applies one edition profile (Public or Dev) to the Python core: folders, ports, environment and Dev safety gates.

Importing this module has no side effects: it creates no folders, reads no settings and opens no sockets.
suite_core calls these helpers before it imports any app, so every service sees its own edition's values.

Dev safety (owner rule: only the consumer edition rings the owner's alarms):
- Dev never inherits Public settings from the environment (data folder, collector, add-on, bridge token).
- Dev's alarm scheduler never rings, its speaker helper refuses to play, and sound tests are refused.
- Dev never self-updates from GitHub and sends no telemetry or feedback.
"""
import asyncio
import os
import re

# Inherited values that point at Public data, Public services or production reporting. A Dev core started from a
# shell or a Public process could otherwise attach to the real alarm database or the real collector.
DEV_BLOCKED_ENV = (
    "ULTRASUITE_DATA", "ULTRASUITE_SETTINGS", "ULTRASUITE_CONF", "ULTRASUITE_COLLECTOR", "ULTRASUITE_COLLECTOR_PUBLIC",
    "ULTRASUITE_LOCAL_ADDON", "ULTRASUITE_OWNER", "ULTRASUITE_ALARM_PORT", "ULTRASUITE_SENTINEL_PORT",
    "ULTRASUITE_ORIGIN_PORTS", "ULTRASUITE_EDITION", "ULTRASUITE_SELF_UPDATE", "ULTRASUITE_ALARM_DELIVERY",
    "HOST_AUDIO_BRIDGE_URL", "HOST_AUDIO_BRIDGE_PORT", "ONESCRIPT_WEBHOOK_URL", "BRIDGE_TOKEN", "ONESCRIPT_PATH",
)
# Public keeps 8098 in its browser allowlist for compatibility with older windows.
PUBLIC_EXTRA_ORIGIN_PORTS = (8098,)
DELIVERY_OFF = "Dev edition: alarm delivery is off, so alarms saved here never ring."
SELF_UPDATE_OFF = "Dev edition: updates come from local builds, never from GitHub."


def folders(profile, environ):
    """(settings folder, data folder) for this edition, from the Windows profile folders. Creates nothing."""
    home = os.path.expanduser("~")
    roaming = environ.get("APPDATA") or home
    local = environ.get("LOCALAPPDATA") or home
    paths = profile.paths(roaming, local)
    return str(paths.roaming), str(paths.data)


def settings_file(profile, root, frozen, conf_dir):
    """Public in a source checkout uses the repo's .env; Dev always uses its own settings file."""
    dev_env = os.path.join(root, ".env")
    if not profile.is_dev and not frozen and os.path.exists(dev_env):
        return dev_env
    return os.path.join(conf_dir, "settings.env")


def sanitize_environment(profile, environ):
    """Dev: drop inherited Public values before any settings are read. Returns the names that were removed."""
    if not profile.is_dev:
        return []
    removed = [k for k in DEV_BLOCKED_ENV if k in environ]
    for k in removed:
        environ.pop(k, None)
    return removed


def origin_ports(profile):
    ports = [profile.port("alarm"), profile.port("sentinel"), profile.port("insights_dashboard")]
    if not profile.is_dev:
        ports += list(PUBLIC_EXTRA_ORIGIN_PORTS)
    return ports


def edition_environment(profile, conf_dir, data_dir):
    """Values every service must see for this edition. They override anything inherited or read from settings."""
    caps = profile.capabilities
    audio = profile.port("audio")
    env = {
        "ULTRASUITE_EDITION": profile.edition,
        "ULTRASUITE_CONF": conf_dir,
        "ULTRASUITE_ALARM_PORT": str(profile.port("alarm")),
        "ULTRASUITE_SENTINEL_PORT": str(profile.port("sentinel")),
        "ULTRASUITE_ORIGIN_PORTS": ",".join(str(p) for p in origin_ports(profile)),
        "ULTRASUITE_SELF_UPDATE": "1" if caps.github_self_update_enabled else "0",
        "ULTRASUITE_ALARM_DELIVERY": "1" if caps.alarm_delivery_enabled else "0",
        "HOST_AUDIO_BRIDGE_URL": f"http://127.0.0.1:{audio}",
        "ONESCRIPT_WEBHOOK_URL": f"http://127.0.0.1:{audio}/run-onescript",
    }
    if profile.is_dev:
        env["ULTRASUITE_DATA"] = data_dir
    if not caps.production_telemetry_allowed:
        env["ULTRASUITE_COLLECTOR"] = ""
        env["ULTRASUITE_COLLECTOR_PUBLIC"] = ""
    return env


def describe(profile, conf_dir, data_dir):
    """A plain summary for tests and support: which folders, ports and gates this core uses."""
    caps = profile.capabilities
    return {
        "edition": profile.edition, "product": profile.product_name, "core": profile.core_binary,
        "settings_folder": conf_dir, "data_folder": data_dir, "ports": dict(profile.ports),
        "alarm_delivery": caps.alarm_delivery_enabled, "self_update": caps.github_self_update_enabled,
        "telemetry": caps.production_telemetry_allowed,
    }


# ----------------------------------------------------------------------------- Dev alarm gate
ALARM_GATE_NAMES = ("scheduler_loop", "trigger_alarm_now", "_ring_helper", "broadcast_state")
_BLOCKED_POSTS = (re.compile(r"/api/alarms/[^/]+/test-ring/?"), re.compile(r"/api/audio/test/?"))


def gate_alarm_delivery(alarm_module):
    """Make the Code Red Alarm backend unable to ring. Fails closed: if the backend no longer has the functions this
    gate replaces, raise, and the core does not serve the alarm app at all."""
    missing = [n for n in ALARM_GATE_NAMES if not callable(getattr(alarm_module, n, None))]
    if missing:
        raise RuntimeError(f"Dev alarm gate cannot be applied (missing {', '.join(missing)}); alarm service not started")
    broadcast = alarm_module.broadcast_state

    async def scheduler_loop():
        print("[core] " + DELIVERY_OFF)
        while True:  # keep the live state flowing to open windows; nothing is ever due here
            try:
                await broadcast()
            except Exception:
                pass
            await asyncio.sleep(1.0)

    async def ring_helper(alarm_dict, duration):
        return False, DELIVERY_OFF

    async def trigger_alarm_now(alarm_dict):
        print("[core] refused to ring: " + DELIVERY_OFF)

    alarm_module.scheduler_loop = scheduler_loop  # the lifespan looks the name up when the app starts
    alarm_module._ring_helper = ring_helper
    alarm_module.trigger_alarm_now = trigger_alarm_now
    alarm_module.ULTRASUITE_DELIVERY_GATED = True
    return alarm_module


def add_alarm_delivery_guard(app, edition="Dev"):
    """Refuse every request that would play sound, and report no deliverable next alarm (no wake timer)."""
    from fastapi.responses import JSONResponse

    @app.middleware("http")
    async def _dev_delivery_guard(request, call_next):
        path = request.url.path
        if request.method.upper() == "POST" and any(p.fullmatch(path) for p in _BLOCKED_POSTS):
            return JSONResponse({"detail": DELIVERY_OFF, "delivery_enabled": False}, status_code=403)
        if request.method.upper() == "GET" and path.rstrip("/") == "/api/next":
            return JSONResponse({"in_sec": None, "delivery_enabled": False, "edition": edition})
        return await call_next(request)
    return app


def gated_bridge_handler(base_handler, siren=None):
    """A speaker helper that can list outputs and save a choice, but never plays the siren or runs OneScript."""
    if siren is not None:
        siren.start = lambda *args, **kwargs: False

    class DevBridgeHandler(base_handler):
        def do_POST(self):
            if self.path in ("/trigger", "/run-onescript"):
                if not self._auth():
                    return
                self._send({"ok": False, "started": False, "error": DELIVERY_OFF}, 403)
                return
            return super().do_POST()

    return DevBridgeHandler
