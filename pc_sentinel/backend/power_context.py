"""Optional, non-secret desktop PSU context. No hardware settings are changed.

The production store is separate from credentials and battery/adapter settings.
``_test_path`` is an in-process test seam, never a request parameter or an
environment override. POST merges supplied fields; JSON null clears a field.
"""
import json
import math
import os
import tempfile
import threading
from pathlib import Path


_FIELDS = {"rated_watts", "model", "form_factor"}
_DEFAULTS = {"rated_watts": None, "model": None, "form_factor": None}
_LOCK = threading.RLock()
_MAX_FILE_BYTES = 16_384
MIN_RATED_WATTS = 50
MAX_RATED_WATTS = 5000


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except (OverflowError, ValueError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _validate(changes):
    if not isinstance(changes, dict) or set(changes) - _FIELDS:
        raise ValueError("Only rated_watts, model and form_factor are supported.")
    clean = {}
    for field, value in changes.items():
        if value is None:
            clean[field] = None
        elif field == "rated_watts":
            watts = _number(value)
            if watts is None or not MIN_RATED_WATTS <= watts <= MAX_RATED_WATTS:
                raise ValueError(f"rated_watts must be a number from {MIN_RATED_WATTS} to {MAX_RATED_WATTS}, or null.")
            clean[field] = watts
        else:
            limit = 120 if field == "model" else 40
            if not isinstance(value, str) or len(value) > limit or (value and not value.isprintable()):
                raise ValueError(f"{field} must be printable text of at most {limit} characters, or null.")
            clean[field] = value.strip() or None
    return clean


def _path(_test_path=None):
    if _test_path is not None:
        return Path(_test_path)
    roaming = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    return roaming / "UltraSuite" / "sentinel_power.json"


def _read(path):
    try:
        with path.open("rb") as handle:
            raw = handle.read(_MAX_FILE_BYTES + 1)
        if len(raw) > _MAX_FILE_BYTES:
            raise ValueError("Oversized power context.")
        document = json.loads(raw.decode("utf-8-sig"))
        if (not isinstance(document, dict) or type(document.get("version")) is not int
                or document["version"] != 1 or set(document) - (_FIELDS | {"version"})):
            raise ValueError("Unsupported power context document.")
        values = _validate({key: value for key, value in document.items() if key != "version"})
        return {**_DEFAULTS, **values}, "ok"
    except FileNotFoundError:
        return dict(_DEFAULTS), "not_configured"
    except (ValueError, UnicodeError, RecursionError):
        return dict(_DEFAULTS), "invalid"
    except OSError:
        return dict(_DEFAULTS), "unavailable"


def _response(values, status):
    return {**values, "kind": "desktop_psu", "applies_to": "desktop",
            "source": "user_supplied", "controls_hardware": False,
            "storage_status": status}


def load_context(*, _test_path=None):
    with _LOCK:
        values, status = _read(_path(_test_path))
    return _response(values, status)


def save_context(changes, *, _test_path=None):
    """Merge validated fields and atomically replace a dedicated JSON document.

    Invalid requests do not touch the existing file. An invalid stored document
    falls back to empty context, and a valid save can replace it. I/O failures
    propagate to the route as an explicit unavailable response.
    """
    changes = _validate(changes)
    path = _path(_test_path)
    with _LOCK:
        current, _ = _read(path)
        values = {**current, **changes}
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                             dir=path.parent, prefix=".sentinel-power-",
                                             suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                json.dump({"version": 1, **values}, handle, ensure_ascii=False,
                          allow_nan=False, sort_keys=True, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return _response(values, "ok")


def headroom_advisory(rated_watts, samples, *, now_s, sustained_s=120.0,
                      max_gap_s=15.0, threshold=0.85):
    """Pure, quiet advisory for a trailing run of complete measured DC samples.

    Samples use monotonic seconds in ``at``, watts in ``total_w``, and explicit
    ``complete=True, estimated=False`` flags. Component subtotals and modeled
    totals from power_report are ineligible. Unknown, stale or incomplete data
    cannot produce a PSU assessment. No result diagnoses a required upgrade.
    """
    unavailable = {"state": "unavailable", "show": False, "severity": "none",
                   "reason": "incomplete_power", "text": None}
    capacity = _number(rated_watts)
    if capacity is None or not MIN_RATED_WATTS <= capacity <= MAX_RATED_WATTS:
        return {**unavailable, "reason": "capacity_not_configured"}
    now = _number(now_s)
    duration, gap, ratio = _number(sustained_s), _number(max_gap_s), _number(threshold)
    if now is None or not duration or not gap or ratio is None or not 0 < ratio <= 1:
        raise ValueError("Advisory timing and threshold parameters must be finite positive numbers.")
    quiet = {"state": "quiet", "show": False, "severity": "none",
             "reason": "insufficient_history", "text": None}
    start = previous = last = None
    last_complete = False
    for sample in samples:
        if not isinstance(sample, dict):
            return {**unavailable, "reason": "invalid_history"}
        at, watts = _number(sample.get("at")), _number(sample.get("total_w"))
        if at is None or at > now or (previous is not None and at <= previous):
            return {**unavailable, "reason": "invalid_history"}
        complete = sample.get("complete") is True and sample.get("estimated") is False and watts is not None
        if previous is not None and at - previous > gap:
            start = None
        if complete and watts / capacity >= ratio:
            if start is None:
                start = at
        else:
            start = None
        previous = last = at
        last_complete = complete
    if last is None:
        return quiet
    if now - last > gap:
        return {**unavailable, "reason": "stale_power"}
    if not last_complete:
        return unavailable
    if start is None:
        return {**quiet, "reason": "below_threshold"}
    if last - start < duration:
        return quiet
    return {"state": "advisory", "show": True, "severity": "info",
            "reason": "sustained_high_load",
            "text": "Measured DC load has stayed near the entered PSU rating. Review the measurement coverage and sustained workload if useful. This does not establish a PSU fault or a need to upgrade."}


def register(app, *, _test_path=None):
    """Routes inherit the app's existing local Host/Origin guard."""
    from fastapi import HTTPException

    @app.get("/api/power/context")
    def get_power_context():
        return load_context(_test_path=_test_path)

    @app.post("/api/power/context")
    def set_power_context(body: dict):
        try:
            return save_context(body, _test_path=_test_path)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except OSError:
            raise HTTPException(status_code=503, detail="Power context could not be saved.") from None
