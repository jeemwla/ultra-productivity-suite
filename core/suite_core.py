#!/usr/bin/env python3
"""
Ultra Suite core: runs every background service in one process.

Two editions share this file (core/edition_profiles.json has every name, folder and port):
  Public  ultrasuite-core.exe      8081 alarm, 8084 Sentinel, 8091 speaker helper, 8093 add-on, 8300/8301 Insights
  Dev     ultrasuite-dev-core.exe  18081, 18084, 18091, 18093, 18300/18301; alarms never ring, no GitHub updates,
          no telemetry
The frozen core picks its edition from its own file name; a source run takes --edition Public|Dev (default Public).
--describe prints the edition's folders and ports as JSON and exits without creating anything.

Settings:  %APPDATA%\\UltraSuite\\settings.env   (created on first run; the repo's .env wins in dev)
Data:      %LOCALAPPDATA%\\UltraSuite\\data       (alarms, speaker choice, logs)

Run with no arguments; it exits if the services are already running.
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
_CORE_DIR = os.path.join(ROOT, "core")
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)
import editions  # noqa: E402  (pure contract: no side effects)
import edition_runtime  # noqa: E402  (pure helpers: no side effects)

# The edition comes only from this executable's name (frozen) or an explicit --edition (source), never from the
# environment, so a Public process cannot start a Dev core on Public data or the reverse.
PROFILE = editions.resolve_profile(sys.executable, FROZEN, sys.argv[1:])
REMOVED_ENV = edition_runtime.sanitize_environment(PROFILE, os.environ)
CONF_DIR, DATA_DIR = edition_runtime.folders(PROFILE, os.environ)
PORTS = PROFILE.ports
if __name__ == "__main__" and "--describe" in sys.argv[1:]:
    import json as _json
    print(_json.dumps({**edition_runtime.describe(PROFILE, CONF_DIR, DATA_DIR), "removed_env": REMOVED_ENV}))
    sys.exit(0)
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
    path = edition_runtime.settings_file(PROFILE, ROOT, FROZEN, CONF_DIR)  # Dev never reads the repo's .env
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write("# Ultra Suite settings (private to this PC)\n"
                    f"BRIDGE_TOKEN={secrets.token_hex(20)}\n"
                    f"GLINET_ROUTER_IP={_gateway()}\n"
                    "GLINET_ADMIN_PASSWORD=\nONESCRIPT_PATH=\nALARM_SPEAKER_MATCH=\nDEFAULT_RING_DURATION_SEC=600\n"
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
    # edition-owned values win over anything inherited or read from settings (Dev: its own data, no collector)
    os.environ.update(edition_runtime.edition_environment(PROFILE, CONF_DIR, DATA_DIR))
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
    alarm_port, sentinel_port, audio_port = PORTS["alarm"], PORTS["sentinel"], PORTS["audio"]
    if port_busy(alarm_port) and port_busy(sentinel_port):
        print("[core] already running")
        return 0
    delivery = PROFILE.capabilities.alarm_delivery_enabled
    print(f"[core] {PROFILE.product_name}: settings {CONF_DIR}, data {DATA_DIR}, "
          f"alarm delivery {'on' if delivery else 'off'}")
    from http.server import ThreadingHTTPServer
    bridge = load_module("ultra_audio_bridge", os.path.join(ROOT, "code_red_alarm", "backend", "audio_enforcer.py"))
    handler = bridge.Handler if delivery else edition_runtime.gated_bridge_handler(bridge.Handler, getattr(bridge, "siren", None))
    if not port_busy(audio_port):
        threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", audio_port), handler).serve_forever, daemon=True).start()
    start_local_addons(ThreadingHTTPServer)

    load_package("alarm_backend", os.path.join(ROOT, "code_red_alarm", "backend"))
    load_package("sentinel_backend", os.path.join(ROOT, "pc_sentinel", "backend"))
    import importlib
    alarm_module = importlib.import_module("alarm_backend.main")
    if not delivery:  # Dev: the scheduler, sound tests and wake requests can never ring (fails closed)
        edition_runtime.gate_alarm_delivery(alarm_module)
        edition_runtime.add_alarm_delivery_guard(alarm_module.app, PROFILE.edition)
    alarm_app = alarm_module.app
    sentinel_app = importlib.import_module("sentinel_backend.main").app

    insights = None
    # Public: only on the owner's PC. Dev is an owner-only build, so its local Insights sandbox always runs
    # (its own ports and database, never the public collector or Funnel).
    if os.environ.get("ULTRASUITE_OWNER") == "1" or PROFILE.is_dev:
        try:
            insights = load_module("ultra_insights", os.path.join(ROOT, "insights", "insights.py"))
        except Exception as exc:
            print(f"[core] insights not loaded: {exc}")

    def server(app_, port, lifespan="auto"):
        return uvicorn.Server(uvicorn.Config(app_, host="127.0.0.1", port=port, log_level="warning",
                                             log_config=None, lifespan=lifespan))

    # The alarm gets its own thread and event loop, so a slow or blocking Sentinel/Insights handler can't delay the
    # alarm scheduler. This is thread isolation inside one process: a crash of the whole core still stops both.
    alarm_servers = [] if port_busy(alarm_port) else [server(alarm_app, alarm_port, "on")]
    other_servers = [] if port_busy(sentinel_port) else [server(sentinel_app, sentinel_port, "on")]
    if insights:
        other_servers += [server(app_, port) for app_, port in ((insights.collector, PORTS["insights_collector"]),
                                                                (insights.dashboard, PORTS["insights_dashboard"]))
                          if not port_busy(port)]
    groups = [g for g in (ServiceLoop("alarm", alarm_servers), ServiceLoop("services", other_servers)) if g.servers]

    print("[core] services starting")
    return supervise(groups)


class ServiceLoop:
    """Runs a group of uvicorn-style servers on its own thread and its own asyncio event loop.

    A server here is anything with an ``async serve()`` coroutine, a writable ``should_exit`` flag and a ``started``
    flag that turns true once it is ready (uvicorn.Server has all three). The group ends when any of its servers
    ends. Lifecycle: ``start()`` once (bounded readiness wait), then ``stop()`` any number of times.
    States: new, starting, running, stopping, stopped, failed.
    """

    def __init__(self, name, servers):
        self.name = name
        self.servers = list(servers)
        self.state = "new"
        self.error = None
        self.thread = None
        self.loop = None
        self._tasks = []
        self._stop_requested = False
        self._done = threading.Event()
        self._lock = threading.Lock()

    @property
    def finished(self):
        return self._done.is_set()

    def _set_state(self, state):
        with self._lock:
            if self.state not in ("stopped", "failed") or state == "failed":
                self.state = state

    def start(self, timeout=20.0):
        """Start the thread and wait (at most ``timeout`` s) until every server reports ``started``.
        Returns True when ready; False when a server failed, the group ended early, or readiness timed out."""
        with self._lock:
            if self.state != "new":
                raise RuntimeError(f"service loop {self.name!r} was already started")
            self.state = "starting"
        self.thread = threading.Thread(target=self._run, name=f"ultrasuite-{self.name}", daemon=True)
        self.thread.start()
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            if self._done.is_set():
                self._set_state("failed")
                if self.error is None:
                    self.error = RuntimeError(f"{self.name} ended before it was ready")
                return False
            if self.servers and all(getattr(s, "started", False) for s in self.servers):
                self._set_state("running")
                return True
            if time.monotonic() >= deadline:
                self.error = self.error or TimeoutError(f"{self.name} was not ready within {timeout:g} s")
                self._set_state("failed")
                return False
            time.sleep(0.02)

    def _run(self):
        loop = asyncio.new_event_loop()
        self.loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._serve_all())
        except asyncio.CancelledError:
            if not self._stop_requested:
                self.error = self.error or RuntimeError(f"{self.name} was cancelled unexpectedly")
        except BaseException as exc:  # includes SystemExit, which uvicorn raises when it cannot bind its port
            self.error = self.error or exc
        finally:
            try:
                pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                for t in pending:
                    t.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.run_until_complete(loop.shutdown_asyncgens())
                for t in self._tasks:  # mark failures as seen (the first one is already in self.error)
                    if t.done() and not t.cancelled():
                        t.exception()
            except BaseException:
                pass
            finally:
                asyncio.set_event_loop(None)
                loop.close()
                if self.error is not None:
                    self._set_state("failed")
                self._done.set()

    async def _serve_all(self):
        self._tasks = [asyncio.ensure_future(s.serve()) for s in self.servers]
        done, pending = await asyncio.wait(self._tasks, return_when=asyncio.FIRST_COMPLETED)
        for s in self.servers:  # one server ending ends the group
            s.should_exit = True
        if pending:
            _, still = await asyncio.wait(pending, timeout=5.0)
            for t in still:
                t.cancel()
        for t in done:
            if not t.cancelled() and t.exception() is not None:
                raise t.exception()

    def stop(self, timeout=10.0):
        """Ask the servers to exit, wait up to ``timeout`` s, then cancel whatever is left and wait again.
        Safe to call repeatedly and before ``start()``. Returns True when the thread is gone."""
        with self._lock:
            if self.state == "new":
                self.state = "stopped"
                self._done.set()
                return True
            if self.state not in ("stopped", "failed"):
                self.state = "stopping"
            self._stop_requested = True
        for s in self.servers:
            s.should_exit = True
        thread = self.thread
        if thread is not None:
            thread.join(max(0.0, timeout))
            if thread.is_alive() and self.loop is not None and not self.loop.is_closed():
                def cancel_all():
                    for t in asyncio.all_tasks():
                        t.cancel()
                try:
                    self.loop.call_soon_threadsafe(cancel_all)
                except RuntimeError:  # loop closed between the check and the call
                    pass
                thread.join(max(0.0, timeout))
        gone = thread is None or not thread.is_alive()
        if gone:
            self._set_state("stopped" if self.error is None else "failed")
        return gone


def supervise(groups, *, start_timeout=20.0, poll=0.5, stop_timeout=10.0, should_stop=None):
    """Start each group in order (fail fast if one can't get ready), run until a group ends, ``should_stop()``
    returns true, or Ctrl+C, then stop every started group in reverse order. Returns a process exit code."""
    groups = list(groups)
    if not groups:
        return 0
    started = []
    try:
        for g in groups:
            started.append(g)
            if not g.start(start_timeout):
                print(f"[core] {g.name} failed to start: {g.error!r}")
                return 1
        print("[core] running: " + ", ".join(f"{g.name} ({len(g.servers)})" for g in groups))
        while True:
            for g in groups:
                if g.finished:
                    print(f"[core] {g.name} stopped" + (f": {g.error!r}" if g.error else ""))
                    return 0 if g.error is None else 1
            if should_stop is not None and should_stop():
                return 0
            time.sleep(poll)
    except KeyboardInterrupt:
        return 0
    finally:
        for g in reversed(started):
            if not g.stop(stop_timeout):
                print(f"[core] {g.name} did not stop within {2 * stop_timeout:g} s")


def start_local_addons(server_cls):
    """Optional personal extras that are not part of Ultra Suite and never ship with it.

    If %APPDATA%\\UltraSuite\\local\\volume_knob_daemon.py exists (or ULTRASUITE_LOCAL_ADDON names a file), it is
    served on 127.0.0.1:8093. When no such file exists, nothing runs.
    """
    places = [os.environ.get("ULTRASUITE_LOCAL_ADDON", ""), os.path.join(CONF_DIR, "local", "volume_knob_daemon.py")]
    path = next((p for p in places if p and os.path.isfile(p)), None)
    port = PORTS["addon"]  # 8093, Dev 18093 (Dev ignores the inherited ULTRASUITE_LOCAL_ADDON)
    if not path or port_busy(port):
        return
    try:
        knobs = load_module("ultra_knob_daemon", path)
        threading.Thread(target=server_cls(("127.0.0.1", port), knobs.KnobDaemonHandler).serve_forever, daemon=True).start()
        print(f"[core] local add-on: knob service from {path}")
    except Exception as exc:
        print(f"[core] local add-on failed: {exc}")


if __name__ == "__main__":
    load_settings()
    sys.exit(run_services())
