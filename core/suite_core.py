#!/usr/bin/env python3
"""
Ultra Suite core: runs every background service in one process (bundled as ultrasuite-core.exe).

  127.0.0.1:8091  speaker helper (output picker, volume lock, OneScript)
  127.0.0.1:8093  deck knob daemon
  127.0.0.1:8081  Code Red Alarm API + UI
  127.0.0.1:8084  PC Sentinel API + UI

Settings:  %APPDATA%\\UltraSuite\\settings.env   (created on first run; the repo's .env wins in dev)
Data:      %LOCALAPPDATA%\\UltraSuite\\data       (alarms, speaker choice, logs)

Subcommands:
  ultrasuite-core.exe                 run the services (exits if they're already running)
  ultrasuite-core.exe deck install  install the deck plugin + "Ultra Suite" profile
  ultrasuite-core.exe deck remove
"""
import os
import sys

FROZEN = getattr(sys, "frozen", False)


def fix_local_time():
    """Windows: drop an inherited TZ so the C runtime uses the system time zone.

    The Windows C runtime only understands POSIX-style TZ values ("PST8PDT"). An IANA name such as
    "America/Los_Angeles" (set by some launchers and dev tools) makes it silently fall back to UTC,
    which puts every local time in the suite off by hours. Windows apps should follow the zone set
    in Windows Settings, so the variable is removed and the runtime re-reads the system zone.
    """
    if os.name != "nt" or "TZ" not in os.environ:
        return
    os.environ.pop("TZ", None)  # CPython uses _wputenv here, so the C runtime sees the change
    try:
        import ctypes
        ctypes.cdll.ucrtbase._tzset()
    except Exception:
        pass


fix_local_time()
ROOT = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPDATA = os.environ.get("APPDATA", os.path.expanduser("~"))
LOCALAPPDATA = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
CONF_DIR = os.path.join(APPDATA, "UltraSuite")
DATA_DIR = os.path.join(LOCALAPPDATA, "UltraSuite", "data")
os.makedirs(CONF_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

if sys.stdout is None or sys.stderr is None:  # windowed exe: log to file
    _log = open(os.path.join(DATA_DIR, "core.log"), "a", buffering=1, encoding="utf-8")
    sys.stdout = sys.stdout or _log
    sys.stderr = sys.stderr or _log

# Explicit imports so PyInstaller bundles everything the services need
import asyncio            # noqa: E402
import importlib.util     # noqa: E402
import secrets            # noqa: E402
import socket             # noqa: E402
import subprocess         # noqa: E402
import threading          # noqa: E402
import types              # noqa: E402
import fastapi, uvicorn, httpx, pydantic, websockets, psutil  # noqa: E401,E402,F401
# standard-library modules the app files use (they're loaded at runtime, so PyInstaller can't see them)
import argparse, contextlib, ctypes, datetime, glob, hashlib, http.server, json, math, random, re, shutil  # noqa: E401,E402,F401
import sqlite3, statistics, struct, time, typing, urllib.request, uuid, wave, warnings  # noqa: E401,E402,F401
try:
    import winsound  # noqa: F401
except ImportError:
    pass
try:
    import sounddevice, comtypes  # noqa: E401,F401
    from pycaw import pycaw  # noqa: F401
except Exception:
    pass


def _gateway():
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "(Get-NetRoute -DestinationPrefix 0.0.0.0/0 | Sort-Object RouteMetric | Select-Object -First 1).NextHop"],
                             capture_output=True, text=True, timeout=8, creationflags=0x08000000).stdout.strip()
        return out or ""
    except Exception:
        return ""


def load_settings():
    """Dev: repo .env. Installed: %APPDATA%\\UltraSuite\\settings.env (created with safe defaults)."""
    dev_env = os.path.join(ROOT, ".env")
    path = dev_env if (not FROZEN and os.path.exists(dev_env)) else os.path.join(CONF_DIR, "settings.env")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write("# Ultra Suite settings (private to this PC)\n"
                    f"BRIDGE_TOKEN={secrets.token_hex(20)}\n"
                    f"GLINET_ROUTER_IP={_gateway()}\n"
                    "GLINET_ADMIN_PASSWORD=\nONESCRIPT_PATH=\nPEBBLE_DEVICE_MATCH=\nDEFAULT_RING_DURATION_SEC=600\n"
                    "TELEMETRY=ask\n")
    values = {}
    for line in open(path, encoding="utf-8-sig"):
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip()  # a later line wins over an earlier one
    for k, v in values.items():
        if k == "TZ" and os.name == "nt":
            continue  # Docker-only setting; on Windows it would push every clock in the suite to UTC
        os.environ.setdefault(k, v)
    fix_local_time()
    os.environ.setdefault("ULTRASUITE_DATA", DATA_DIR)
    os.environ["HOST_AUDIO_BRIDGE_URL"] = "http://127.0.0.1:8091"
    os.environ["ONESCRIPT_WEBHOOK_URL"] = "http://127.0.0.1:8091/run-onescript"
    os.environ["LHM_REMOTE_URL"] = os.environ.get("LHM_REMOTE_URL_OVERRIDE", "http://127.0.0.1:8085/data.json")
    os.environ["ULTRASUITE_SETTINGS"] = path
    os.environ.setdefault("ULTRASUITE_COLLECTOR", "https://jimpc.tail95b9d9.ts.net:10000/ultrasuite/v1/event")
    return path


def load_package(name, directory):
    """Import a folder as a package under a unique name (both apps call their folder 'backend')."""
    init = os.path.join(directory, "__init__.py")
    spec = importlib.util.spec_from_file_location(name, init, submodule_search_locations=[directory])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def port_busy(port):
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def run_services():
    if port_busy(8081) and port_busy(8084):
        print("[core] already running")
        return 0
    from http.server import ThreadingHTTPServer
    bridge = load_module("ultra_audio_bridge", os.path.join(ROOT, "01_pebble_code_red_alarm", "backend", "audio_enforcer.py"))
    knobs = load_module("ultra_knob_daemon", os.path.join(ROOT, "03_deck_deck_config", "plugin", "volume_knob_daemon.py"))
    if not port_busy(8091):
        threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", 8091), bridge.Handler).serve_forever, daemon=True).start()
    if not port_busy(8093):
        threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", 8093), knobs.KnobDaemonHandler).serve_forever, daemon=True).start()

    load_package("alarm_backend", os.path.join(ROOT, "01_pebble_code_red_alarm", "backend"))
    load_package("sentinel_backend", os.path.join(ROOT, "04_hardware_net_sentinel", "backend"))
    import importlib
    alarm_app = importlib.import_module("alarm_backend.main").app
    sentinel_app = importlib.import_module("sentinel_backend.main").app

    insights = None
    if os.environ.get("ULTRASUITE_OWNER") == "1":  # only on the owner's PC
        try:
            insights = load_module("ultra_insights", os.path.join(ROOT, "05_insights", "insights.py"))
        except Exception as exc:
            print(f"[core] insights not loaded: {exc}")

    async def main():
        servers = []
        if insights:
            for app_, port in ((insights.collector, 8300), (insights.dashboard, 8301)):
                if not port_busy(port):
                    servers.append(uvicorn.Server(uvicorn.Config(app_, host="127.0.0.1", port=port, log_level="warning", log_config=None)))
        for app, port in ((alarm_app, 8081), (sentinel_app, 8084)):
            if port_busy(port):
                continue
            cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", log_config=None, lifespan="on")
            servers.append(uvicorn.Server(cfg))
        await asyncio.gather(*(s.serve() for s in servers))

    print("[core] services starting")
    asyncio.run(main())
    return 0


def deck(cmd):
    gen = load_module("ultra_deck", os.path.join(ROOT, "03_deck_deck_config", "generate_deck_profile.py"))
    if not os.path.isdir(gen.DECK_DIR):
        print("deck Studio is not installed.")
        return 0
    if cmd == "remove":
        gen.remove()
    else:
        gen.install_plugin()
        gen.build_profile()
        if "--restart" in sys.argv:
            gen.restart_studio()
    return 0


if __name__ == "__main__":
    load_settings()
    args = sys.argv[1:]
    if args[:1] == ["deck"]:
        sys.exit(deck(args[1] if len(args) > 1 else "install"))
    sys.exit(run_services())
