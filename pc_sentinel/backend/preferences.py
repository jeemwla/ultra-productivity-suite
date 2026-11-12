"""Non-secret, per-PC display and optional router-feature preferences."""
import json
import os
import tempfile
import threading
import time
from pathlib import Path

PAGES = ("overview", "performance", "cooling", "power", "storage", "network", "speed", "sensors", "system", "battery")
LOCK = threading.RLock()


def defaults():
    return {"pages": {p: None if p == "battery" else True for p in PAGES}, "router_vpn": False, "router_ping": False,
            "hide_cooling_without_fans": False}


BOOLEANS = ("router_vpn", "router_ping", "hide_cooling_without_fans")


def path():
    return Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming") / "UltraSuite/sentinel_preferences.json"


def validate(changes, current=None):
    if not isinstance(changes, dict) or set(changes) - {"pages", *BOOLEANS}:
        raise ValueError("Only pages, router_vpn, router_ping and hide_cooling_without_fans are supported.")
    value = defaults() if current is None else {**current, "pages": dict(current["pages"])}
    for name in BOOLEANS:
        if name in changes:
            if type(changes[name]) is not bool:
                raise ValueError("Optional settings must be true or false.")
            value[name] = changes[name]
    if "pages" in changes:
        if not isinstance(changes["pages"], dict) or set(changes["pages"]) - set(PAGES):
            raise ValueError("Unknown page. Settings / Extras is always available.")
        for name, visible in changes["pages"].items():
            if type(visible) is not bool and not (name == "battery" and visible is None):
                raise ValueError("Page visibility must be true or false; Battery also accepts automatic (null).")
            value["pages"][name] = visible
    if not any(v is True for v in value["pages"].values()):
        raise ValueError("Keep at least one page visible.")
    return value


def _read(destination):
    try:
        with destination.open("rb") as handle:
            raw = handle.read(16_385)
        if len(raw) > 16_384:
            raise ValueError("Preferences too large")
        document = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(document, dict) or type(document.get("version")) is not int or document["version"] != 1 or type(document.get("revision")) is not int:
            raise ValueError("Unsupported preferences")
        revision = document.pop("revision")
        if revision < 0:
            raise ValueError("Invalid revision")
        document.pop("version")
        return validate(document), revision, "ok"
    except FileNotFoundError:
        return defaults(), 0, "not_configured"
    except (OSError, ValueError, UnicodeError, RecursionError):
        return defaults(), 0, "invalid_or_unavailable"


def load(*, _test_path=None):
    with LOCK:
        value, revision, status = _read(Path(_test_path) if _test_path is not None else path())
    return {**value, "revision": str(revision), "storage_status": status}


def save(changes, *, _test_path=None):
    destination = Path(_test_path) if _test_path is not None else path()
    with LOCK:
        current, revision, _ = _read(destination)
        value = validate(changes, current)
        revision = max(revision + 1, time.time_ns())
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent,
                                             prefix=".sentinel-preferences-", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                json.dump({"version": 1, "revision": revision, **value}, handle, ensure_ascii=False, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return {**value, "revision": str(revision), "storage_status": "ok"}


def register(app):
    from fastapi import HTTPException

    @app.get("/api/preferences")
    def get_preferences():
        return load()

    @app.post("/api/preferences")
    def set_preferences(body: dict):
        try:
            return save(body)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except OSError:
            raise HTTPException(503, "Settings could not be saved. Your previous settings are retained.") from None
