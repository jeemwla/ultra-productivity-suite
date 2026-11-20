"""PC Sentinel's own sensor helper: where it listens, whether it answers, and the "Try again" setup step.

The helper (pc_sentinel/sensor_helper, UltraSuiteSensorHelper.exe) is a read-only Windows service built on
LibreHardwareMonitorLib. It serves the same JSON as LibreHardwareMonitor's data.json, on 127.0.0.1 only, at a port
owned by this edition. The values below mirror pc_sentinel/sensor_helper/src/HelperEdition.cs (a test checks both).

The helper ports are deliberately not in core/edition_profiles.json "ports": the Dock treats any occupied owned port
as a running core, so a helper that runs as a service would stop the Dock from starting the core.
"""
import asyncio
import configparser
import os
import sys
import threading
import time

HELPER_NAME = "UltraSuiteSensorHelper"
HELPERS = {
    "Public": {"port": 8095, "service": "UltraSuiteSensorHelper", "folder": "Ultra Suite Sensor Helper"},
    "Dev": {"port": 18095, "service": "UltraSuiteSensorHelperDev", "folder": "Ultra Suite Sensor Helper (Dev)"},
}
RESULT_FILE = "sensor-setup-result.ini"
RETRY_COOLDOWN_SEC = 60


def edition() -> str:
    raw = (os.getenv("ULTRASUITE_EDITION") or "Public").strip().lower()
    return "Dev" if raw == "dev" else "Public"


def helper_identity(name=None) -> dict:
    return HELPERS[name or edition()]


def helper_base_url(name=None) -> str:
    override = os.getenv("ULTRASUITE_SENSOR_HELPER_URL")
    if override:
        return override.rstrip("/")
    return f"http://127.0.0.1:{helper_identity(name)['port']}"


def valid_health(health, name=None) -> bool:
    """Only this edition's own read-only helper counts (never the other edition's, never an unrelated server)."""
    return (isinstance(health, dict) and health.get("helper") == HELPER_NAME
            and health.get("edition") == (name or edition()) and health.get("read_only") is True)


async def fetch(client):
    """(tree, health) from this edition's helper. Raises when it is absent, foreign or still starting."""
    base = helper_base_url()
    resp = await client.get(base + "/health")
    resp.raise_for_status()
    health = resp.json()
    if not valid_health(health):
        raise ValueError("not this edition's sensor helper")
    resp = await client.get(base + "/data.json")
    resp.raise_for_status()
    return resp.json(), health


def service_installed(name=None) -> bool:
    """Read-only registry check for this edition's helper service."""
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            rf"SYSTEM\CurrentControlSet\Services\{helper_identity(name)['service']}"):
            return True
    except OSError:
        return False


def bundled_exe():
    """The helper shipped with this install (<install>\\core\\sensor-helper), or None when running from source."""
    override = os.getenv("ULTRASUITE_SENSOR_HELPER_EXE")
    candidates = [override] if override else []
    if getattr(sys, "frozen", False):
        candidates.append(os.path.join(os.path.dirname(sys.executable), "sensor-helper", HELPER_NAME + ".exe"))
    return next((p for p in candidates if p and os.path.isfile(p)), None)


def result_dir() -> str:
    """Where the elevated setup writes its outcome: this edition's own local data folder."""
    local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(local, "UltraSuite-Dev" if edition() == "Dev" else "UltraSuite")


def last_result(directory=None) -> dict:
    """The last setup outcome written by the helper (installer or Try again), or {}."""
    path = os.path.join(directory or result_dir(), RESULT_FILE)
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with open(path, encoding="utf-8-sig") as f:
            parser.read_file(f)
        r: dict = dict(parser["result"])
    except (OSError, KeyError, configparser.Error):
        return {}
    r["restart"] = r.get("restart") == "1"
    r["elevated"] = r.get("elevated") == "1"
    try:
        r["at"] = os.path.getmtime(path)
    except OSError:
        pass
    return r


def summary(health) -> dict:
    """What Sentinel's page needs about the helper (no paths, no machine names)."""
    if not isinstance(health, dict):
        return {"running": False, "installed": service_installed(), "can_set_up": bool(bundled_exe())}
    driver = health.get("driver")
    if not isinstance(driver, dict):
        driver = {}
    return {"running": True, "installed": True, "can_set_up": bool(bundled_exe()), "version": health.get("version"),
            "sensor_count": health.get("sensor_count"), "driver": driver.get("device"),
            "driver_installed": driver.get("installed"), "driver_version": driver.get("version"),
            "memory_integrity": driver.get("memory_integrity"), "elevated": health.get("elevated")}


# ----------------------------------------------------------------------------- Try again (elevated setup)
_retry_lock = threading.Lock()
_last_retry = {"at": 0.0}


def _alarm_ports():
    own = os.getenv("ULTRASUITE_ALARM_PORT") or ("18081" if edition() == "Dev" else "8081")
    ports = [(int(own), True)]
    if edition() == "Dev":
        ports.append((8081, False))  # a running consumer alarm also blocks; an absent one does not
    return ports


async def alarm_block_reason(client_factory=None):
    """A reason not to install now (alarm ringing or due within 30 minutes, or unknown), or None."""
    import httpx
    try:
        from devtools import alarm_blocks_update  # core/devtools.py, on sys.path in the packaged and source layouts
    except ImportError:
        return "Couldn't confirm that no alarm is due, so sensor setup was not started. Try again in a moment."
    factory = client_factory or (lambda: httpx.AsyncClient(timeout=5))
    async with factory() as c:
        for port, required in _alarm_ports():
            try:
                nxt = (await c.get(f"http://127.0.0.1:{port}/api/next"))
                nxt.raise_for_status()
                ring = (await c.get(f"http://127.0.0.1:{port}/api/ring"))
                ring.raise_for_status()
                reason = alarm_blocks_update(nxt.json(), ring.json())
            except httpx.ConnectError:
                if not required:
                    continue
                reason = alarm_blocks_update(None, None)
            except Exception:
                reason = alarm_blocks_update(None, None)
            if reason:
                return reason.replace("the update", "sensor setup").replace("Update after", "Set up sensors after")
    return None


def provision_arguments(result_directory) -> str:
    return (f'--provision --edition {edition()} --pawnio '
            f'--result "{os.path.join(result_directory, RESULT_FILE)}"')


def launch_setup(exe, result_directory, shell_execute=None) -> bool:
    """Starts the elevated setup; Windows shows its own permission prompt. True when the prompt was shown."""
    os.makedirs(result_directory, exist_ok=True)
    if shell_execute is None:
        import ctypes
        shell_execute = ctypes.windll.shell32.ShellExecuteW
    # ShellExecute returns a value above 32 on success (the user may still decline the prompt).
    return shell_execute(None, "runas", exe, provision_arguments(result_directory), None, 0) > 32


async def retry(now=None, alarm_check=None, launcher=None) -> dict:
    """Try again: re-check right away, and set up the helper and driver again when the helper is not working."""
    now = time.time() if now is None else now
    exe = bundled_exe()
    if not exe:
        return {"started": False, "message": "This copy of PC Sentinel has no sensor helper to set up (it runs from "
                                             "source). Install Ultra Suite, or use LibreHardwareMonitor."}
    if not _retry_lock.acquire(blocking=False):
        return {"started": False, "message": "Sensor setup is already starting."}
    try:
        if now - _last_retry["at"] < RETRY_COOLDOWN_SEC:
            return {"started": False, "message": "Sensor setup was just started. Give it a minute, then try again."}
        reason = await (alarm_check or alarm_block_reason)()
        if reason:
            return {"started": False, "message": reason}
        ok = await asyncio.to_thread(launcher or launch_setup, exe, result_dir())
        if ok:
            _last_retry["at"] = now
            return {"started": True, "message": "Windows will ask for permission once. Full sensors appear a few "
                                                "seconds after setup finishes."}
        return {"started": False, "message": "Windows did not start sensor setup."}
    finally:
        _retry_lock.release()


def register(app):
    @app.post("/api/sensors/helper/retry")
    async def _retry():
        from . import lhm_collector
        lhm_collector.forget_cached_state()
        return await retry()
