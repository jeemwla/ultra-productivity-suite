"""Bounded before/after check for the Max Performance page (about 12 seconds, never more than 30).

Workload, identical every run:
  CPU       SHA-256 over a fixed 4 MB buffer, one thread and then every logical processor (hashlib releases the GIL
            for large buffers, so threads run in parallel). Score: MB hashed per second.
  Wake-up   One thread wakes another 200 times after a short idle gap; time from the signal to the woken thread
            running. Deep idle states and parked cores make this slower; it is what "snappiness" settings change.
Three passes per metric; the median is reported with the spread between passes, and a before/after difference is
only called real when it is larger than that spread (and at least 3%). Results stay on this PC.
"""
import hashlib
import json
import os
import statistics
import tempfile
import threading
import time
from pathlib import Path

PASSES = 3
CPU_SECONDS = 1.2           # per pass, per mode (single and multi)
WAKES = 200
MIN_REAL_DIFF_PCT = 3.0
HARD_LIMIT_S = 30.0
KEEP = 12
BUSY_PCT = 20.0             # background CPU load above this makes a run unreliable for comparison
_BUF = bytes(range(256)) * 16384  # 4 MB, same bytes every run
_lock = threading.Lock()


def _hash_for(seconds, stop_at):
    n, end = 0, min(time.perf_counter() + seconds, stop_at)
    while time.perf_counter() < end:
        hashlib.sha256(_BUF).digest()
        n += 1
    return n


def cpu_pass(threads, seconds, stop_at):
    counts = [0] * threads
    def work(i):
        counts[i] = _hash_for(seconds, stop_at)
    t0 = time.perf_counter()
    pool = [threading.Thread(target=work, args=(i,), daemon=True) for i in range(threads)]
    for t in pool:
        t.start()
    for t in pool:
        t.join()
    elapsed = max(1e-6, time.perf_counter() - t0)
    return sum(counts) * len(_BUF) / 1e6 / elapsed


def wake_pass(count, stop_at):
    ev, done, samples = threading.Event(), threading.Event(), []
    stamp = [0.0]
    def waiter():
        while not done.is_set():
            if ev.wait(0.05):
                samples.append((time.perf_counter() - stamp[0]) * 1e6)
                ev.clear()
    t = threading.Thread(target=waiter, daemon=True)
    t.start()
    for i in range(count):
        if time.perf_counter() > stop_at:
            break
        time.sleep(0.002 + (i % 3) * 0.001)  # let the cores go idle between wake-ups
        stamp[0] = time.perf_counter()
        ev.set()
        while ev.is_set() and time.perf_counter() - stamp[0] < 0.05:
            time.sleep(0)
    done.set()
    t.join(0.2)
    return samples


def _spread_pct(values):
    med = statistics.median(values)
    return round((max(values) - min(values)) / med * 100, 1) if med else None


def background_load(seconds=0.5):
    """System CPU load just before a run, in percent (None when unavailable)."""
    try:
        import psutil
        return round(psutil.cpu_percent(interval=seconds), 1)
    except Exception:
        return None


def run(label="", passes=PASSES, cpu_seconds=CPU_SECONDS, wakes=WAKES, clock=time.time, load=background_load):
    if not _lock.acquire(blocking=False):
        raise RuntimeError("A measurement is already running.")
    try:
        busy = load()
        start = time.perf_counter()
        stop_at = start + HARD_LIMIT_S
        threads = max(1, os.cpu_count() or 1)
        single, multi, wake_med, wake_p95 = [], [], [], []
        for _ in range(passes):
            single.append(cpu_pass(1, cpu_seconds, stop_at))
            multi.append(cpu_pass(threads, cpu_seconds, stop_at))
            w = sorted(wake_pass(wakes, stop_at))
            if w:
                wake_med.append(statistics.median(w))
                wake_p95.append(w[min(len(w) - 1, int(len(w) * 0.95))])
        return {
            "ts": clock(), "label": label, "threads": threads, "passes": passes, "background_load_pct": busy,
            "seconds": round(time.perf_counter() - start, 1),
            "single_mbps": round(statistics.median(single), 1), "single_spread_pct": _spread_pct(single),
            "multi_mbps": round(statistics.median(multi), 1), "multi_spread_pct": _spread_pct(multi),
            "wake_us": round(statistics.median(wake_med), 1) if wake_med else None,
            "wake_spread_pct": _spread_pct(wake_med) if wake_med else None,
            "wake_p95_us": round(statistics.median(wake_p95), 1) if wake_p95 else None,
        }
    finally:
        _lock.release()


METRICS = (("single_mbps", "single_spread_pct", True), ("multi_mbps", "multi_spread_pct", True), ("wake_us", "wake_spread_pct", False))


def comparable(before, after):
    """Reasons the two runs can't be compared fairly (empty list when they can)."""
    reasons = []
    if type(before.get("ac")) is not bool or type(after.get("ac")) is not bool:
        reasons.append("The power source was not reported for one or both runs. Measure again with a known power source.")
    elif before["ac"] != after["ac"]:
        reasons.append("One run was on battery power and the other was plugged in.")
    if before.get("threads") and after.get("threads") and before["threads"] != after["threads"]:
        reasons.append("The number of processor threads changed between runs.")
    if before.get("cpu") and after.get("cpu") and before["cpu"] != after["cpu"]:
        reasons.append("The runs were on different processors.")
    for name, r in (("first", before), ("second", after)):
        if isinstance(r.get("background_load_pct"), (int, float)) and r["background_load_pct"] > BUSY_PCT:
            reasons.append(f"The PC was busy during the {name} run ({r['background_load_pct']:.0f}% CPU in use beforehand).")
    return reasons


def compare(before, after):
    """Per metric: percent change (positive = better) and whether it exceeds the measured noise.
    A change is never "real" when the runs aren't comparable (see comparable())."""
    out = {}
    why = comparable(before or {}, after or {})
    for key, spread, higher_better in METRICS:
        a, b = (before or {}).get(key), (after or {}).get(key)
        if not a or b is None:
            out[key] = None
            continue
        change = (b - a) / a * 100 * (1 if higher_better else -1)
        noise = max(MIN_REAL_DIFF_PCT, (before or {}).get(spread) or 0, (after or {}).get(spread) or 0)
        out[key] = {"better_pct": round(change, 1), "noise_pct": round(noise, 1), "real": abs(change) > noise and not why}
    out["not_comparable"] = why
    return out


def context():
    """Power source and processor for the run, so unlike runs are never compared. Missing facts stay None."""
    out = {"ac": None, "cpu": None}
    try:
        from . import laptop
        out["ac"] = laptop.power_status().get("ac")
    except Exception:
        pass
    try:
        from .max_power import detect_device
        out["cpu"] = detect_device().get("cpu_name") or None
    except Exception:
        pass
    return out


def results_path():
    conf = os.environ.get("ULTRASUITE_CONF") or Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming") / "UltraSuite"
    return Path(conf) / "sentinel_benchmark.json"


def load_results(path=None):
    try:
        with Path(path or results_path()).open("rb") as fh:
            runs = json.loads(fh.read(200_000).decode("utf-8")).get("runs")
        return [r for r in runs if isinstance(r, dict) and isinstance(r.get("single_mbps"), (int, float))][-KEEP:] if isinstance(runs, list) else []
    except (OSError, ValueError, UnicodeError, AttributeError):
        return []


def save_result(result, path=None):
    destination = Path(path or results_path())
    runs = (load_results(destination) + [result])[-KEEP:]
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=destination.parent, prefix=".bench-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "runs": runs}, fh)
        os.replace(tmp, destination)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return runs


def summary(runs):
    """Latest run with Max Performance off ("before") and on ("after"), and their comparison."""
    before = next((r for r in reversed(runs) if r.get("max_active") is False), None)
    after = next((r for r in reversed(runs) if r.get("max_active") is True), None)
    return {"runs": runs[-KEEP:], "before": before, "after": after,
            "compare": compare(before, after) if before and after else None}


def register(app):
    import asyncio
    from fastapi import HTTPException

    @app.get("/api/power/benchmark")
    async def _get():
        return summary(load_results())

    @app.post("/api/power/benchmark")
    async def _run():
        from . import max_power
        try:
            st = await asyncio.to_thread(max_power.status) if os.name == "nt" else {"active": False}
        except Exception:
            st = {"active": None}
        label = "Max Performance on" if st.get("active") else "Max Performance off"
        try:
            result = await asyncio.to_thread(run, label)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from None
        result["max_active"] = st.get("active")
        result.update(await asyncio.to_thread(context))
        await asyncio.to_thread(save_result, result)
        return summary(load_results())
