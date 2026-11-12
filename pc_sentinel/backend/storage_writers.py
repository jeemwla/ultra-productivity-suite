"""Always-on heavy writer detection for the Storage page (read-only, nothing leaves the PC).

Every 30 seconds Sentinel reads Windows' per-process write counters (PDH "\\Process(*)\\IO Write Bytes/sec", which
works without admin rights, including for SYSTEM services) and the drives' own write rate. A process is flagged only
when it writes a lot AND almost all the time: installs, game updates, builds and backups are bursty and stay quiet.

  Level    Window   Average rate   Continuity (minutes with >100 KB written)
  notice   1 hour   0.5 MB/s       at least 90%
  warning  1 hour   2 MB/s         at least 90%
  heavy    6 hours  5 MB/s         at least 90%

Process write counters include all write I/O the process issues (files and devices), and host writes are not the
same as flash wear. Sentinel never stops a service or a process; fixes are shown for the user to run.
"""
import json
import os
import re
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

INTERVAL_S = 30
FAST_INTERVAL_S = 5
MEASURE_S = 120
KEEP_MINUTES = 24 * 60
KEEP_DAYS = 30
ACTIVE_BYTES_PER_MIN = 100 * 1024
MIN_COVERAGE = 0.8
LEVELS = [  # strongest first
    {"level": "heavy", "window_min": 360, "rate_bps": 5e6, "continuity": 0.9},
    {"level": "warning", "window_min": 60, "rate_bps": 2e6, "continuity": 0.9},
    {"level": "notice", "window_min": 60, "rate_bps": 0.5e6, "continuity": 0.9},
]
IGNORED_INSTANCES = {"_total", "idle"}
SELF_NAMES = {"ultrasuite", "ultra-suite", "ultra suite"}  # Ultra Suite's own process, so users can see its share


def base_name(instance):
    """PDH instance "chrome#3" -> "chrome"; groups restarts and multiple instances of one program."""
    return re.sub(r"#\d+$", "", str(instance)).strip().lower()


class WriterTracker:
    """Pure accumulator: feed(rates) at each sample, evaluate() for levels. Time is passed in (testable)."""

    def __init__(self, interval=INTERVAL_S):
        self.interval = interval
        self.last_t = None
        self.minutes = {}          # name -> {minute_index: bytes}
        self.observed = set()      # minute indexes where sampling happened
        self.daily = {}            # "YYYY-MM-DD" -> {name: bytes}
        self.seen = set()
        self.drive_bps = None

    def feed(self, t, rates, drive_bps=None, day=None):
        """rates: {pdh_instance: bytes per second averaged since the previous sample}."""
        prev, self.last_t = self.last_t, t
        if prev is None:
            self.seen = {base_name(k) for k in rates}
            return False
        dt = t - prev
        if dt <= 0 or dt > 2 * self.interval + 1:  # clock change, sleep or hibernate: skip this delta
            self.seen = {base_name(k) for k in rates}
            return False
        self.drive_bps = drive_bps
        minute = int(t // 60)
        self.observed.add(minute)
        totals = {}
        for inst, bps in rates.items():
            name = base_name(inst)
            if name in IGNORED_INSTANCES or not isinstance(bps, (int, float)) or bps < 0 or bps != bps:
                continue
            totals[name] = totals.get(name, 0.0) + bps * dt
        new = {n for n in totals if n not in self.seen}
        self.seen = set(totals) | {base_name(k) for k in rates}
        day = day or datetime.fromtimestamp(t).strftime("%Y-%m-%d")
        for name, b in totals.items():
            if name in new:  # first sample of a new program: its rate may cover time before we saw it
                continue
            m = self.minutes.setdefault(name, {})
            m[minute] = m.get(minute, 0.0) + b
            d = self.daily.setdefault(day, {})
            d[name] = d.get(name, 0.0) + b
        self._trim(minute)
        return True

    def _trim(self, minute):
        floor = minute - KEEP_MINUTES
        self.observed = {m for m in self.observed if m > floor}
        for name in list(self.minutes):
            self.minutes[name] = {m: b for m, b in self.minutes[name].items() if m > floor}
            if not self.minutes[name]:
                del self.minutes[name]
        for day in sorted(self.daily)[:-KEEP_DAYS]:
            del self.daily[day]

    def stats(self, name, now_minute, window_min):
        obs = [m for m in self.observed if now_minute - window_min < m <= now_minute]
        if not obs:
            return None
        per = self.minutes.get(name, {})
        written = sum(per.get(m, 0.0) for m in obs)
        active = sum(1 for m in obs if per.get(m, 0.0) > ACTIVE_BYTES_PER_MIN)
        return {"avg_bps": written / (len(obs) * 60), "continuity": active / len(obs), "coverage": len(obs) / window_min,
                "bytes": written}

    def evaluate(self, now, levels=LEVELS):
        now_minute = int(now // 60)
        today = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
        rows = []
        for name in self.minutes:
            hour = self.stats(name, now_minute, 60)
            if not hour:
                continue
            level = None
            for rule in levels:
                s = self.stats(name, now_minute, rule["window_min"])
                if s and s["coverage"] >= MIN_COVERAGE and s["avg_bps"] >= rule["rate_bps"] and s["continuity"] >= rule["continuity"]:
                    level = rule["level"]
                    break
            rows.append({"name": name, "level": level, "avg_bps_1h": round(hour["avg_bps"], 1),
                         "active_share_1h": round(hour["continuity"], 3), "coverage_1h": round(hour["coverage"], 3),
                         "bytes_today": round(self.daily.get(today, {}).get(name, 0.0)),
                         "per_day_estimate": round(hour["avg_bps"] * 86400)})
        order = {"heavy": 0, "warning": 1, "notice": 2, None: 3}
        rows.sort(key=lambda r: (order[r["level"]], -r["bytes_today"], -r["avg_bps_1h"]))
        return rows

    # daily totals survive restarts (written at most every 10 minutes, so Sentinel itself stays a light writer)
    def dump(self):
        return {"version": 1, "daily": {d: {n: round(b) for n, b in v.items() if b >= 1e6} for d, v in self.daily.items()}}

    def load(self, doc):
        try:
            if doc.get("version") != 1 or not isinstance(doc.get("daily"), dict):
                return False
            daily = {}
            for d, v in doc["daily"].items():
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(d)) or not isinstance(v, dict):
                    return False
                daily[d] = {str(n)[:80]: float(b) for n, b in v.items() if isinstance(b, (int, float)) and b >= 0}
            self.daily = dict(sorted(daily.items())[-KEEP_DAYS:])
            return True
        except (AttributeError, TypeError, ValueError):
            return False


# ----------------------------------------------------------------------------- known issues
def _version_tuple(v):
    parts = re.findall(r"\d+", str(v or ""))
    return tuple(int(p) for p in parts[:4]) if parts else None


def known_issues(path=None):
    try:
        with Path(path or Path(__file__).with_name("known_writers.json")).open("rb") as fh:
            doc = json.loads(fh.read(200_000).decode("utf-8"))
        return [e for e in doc.get("entries", []) if isinstance(e, dict) and e.get("exe")]
    except (OSError, ValueError, UnicodeError, AttributeError):
        return []


def match_issue(name, entries, versions):
    """Name match is context, not a diagnosis. Retire only against a dated vendor-confirmed fix."""
    for e in entries:
        if base_name(e["exe"]) == base_name(name):
            fixed = e.get("fixed_in") or {}
            have, need = _version_tuple((versions or {}).get(fixed.get("component"))), _version_tuple(fixed.get("version"))
            confirmed = (fixed.get("status") == "confirmed" and fixed.get("source_url")
                         and fixed.get("confirmed_on"))
            return {**e, "retired": bool(confirmed and have and need and have >= need),
                    "installed_version": (versions or {}).get(fixed.get("component"))}
    return None


def component_versions():
    """Installed AMD Software (Adrenalin) version when Windows records it; empty when unknown."""
    out = {}
    if os.name != "nt":
        return out
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\AMD\CN") as key:
            v = str(winreg.QueryValueEx(key, "RadeonSoftwareVersion")[0]).strip()
            if _version_tuple(v):
                out["amd_adrenalin"] = v
    except OSError:
        pass
    return out


# ----------------------------------------------------------------------------- sampler (Windows)
C_PROC = r"\Process(*)\IO Write Bytes/sec"
C_DISK = r"\PhysicalDisk(*)\Disk Write Bytes/sec"


class Sampler:
    def __init__(self, store_path=None):
        self.tracker = WriterTracker()
        self.lock = threading.Lock()
        conf = os.environ.get("ULTRASUITE_CONF") or Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming") / "UltraSuite"
        self.store_path = Path(store_path) if store_path else Path(conf) / "sentinel_writers.json"
        self.fast_until = 0.0
        self.live = {}
        self.live_started = None
        self.last_rates = {}
        self.drives = {}
        self.saved_at = time.time()
        self.thread = None
        self.available = os.name == "nt"
        try:
            with self.store_path.open("rb") as fh:
                self.tracker.load(json.loads(fh.read(500_000).decode("utf-8")))
        except (OSError, ValueError, UnicodeError):
            pass

    def start(self):
        if self.thread or not self.available:
            return
        self.thread = threading.Thread(target=self._run, name="sentinel-writers", daemon=True)
        self.thread.start()

    def _run(self):
        from .native_sensors import Pdh
        pdh = Pdh([C_PROC, C_DISK])
        if not pdh.ok:
            self.available = False
            return
        last = time.time()
        while True:
            fast = time.time() < self.fast_until
            time.sleep(FAST_INTERVAL_S if fast else INTERVAL_S)
            data, now = pdh.collect(), time.time()
            procs = data.get(C_PROC) or {}
            disks = {k: v for k, v in (data.get(C_DISK) or {}).items() if k.lower() != "_total"}
            with self.lock:
                self.tracker.interval = FAST_INTERVAL_S if fast else INTERVAL_S
                self.tracker.feed(now, procs, sum(disks.values()) if disks else None)
                self.drives = {k: round(v) for k, v in disks.items()}
                self.last_rates = {}
                for inst, bps in procs.items():
                    n = base_name(inst)
                    if n not in IGNORED_INSTANCES:
                        self.last_rates[n] = self.last_rates.get(n, 0.0) + bps
                if fast:
                    dt = now - last
                    for n, bps in self.last_rates.items():
                        self.live[n] = self.live.get(n, 0.0) + bps * dt
                if now - self.saved_at > 600:
                    self._save()
            last = now

    def _save(self):
        try:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.store_path.parent, prefix=".writers-", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.tracker.dump(), fh)
            os.replace(tmp, self.store_path)
        except OSError:
            pass
        self.saved_at = time.time()

    def measure(self):
        with self.lock:
            self.fast_until = time.time() + MEASURE_S
            self.live, self.live_started = {}, time.time()
        return self.report()

    def report(self, now=None):
        now = now or time.time()
        entries, versions = known_issues(), component_versions()
        with self.lock:
            rows = self.tracker.evaluate(now)
            live_s = (min(now, self.fast_until) - self.live_started) if self.live_started else 0
            live = sorted(({"name": n, "avg_bps": round(b / live_s, 1)} for n, b in self.live.items() if live_s > 0 and b > 0),
                          key=lambda r: -r["avg_bps"])[:15]
            drive_bps = self.tracker.drive_bps
            observed_min = len([m for m in self.tracker.observed if m > now // 60 - 60])
            drives = dict(self.drives)
        for r in rows:
            issue = match_issue(r["name"], entries, versions)
            r["known_issue"] = issue
            r["self"] = r["name"] in SELF_NAMES
        flagged = [r for r in rows if r["level"]]
        return {"available": self.available, "interval_s": INTERVAL_S, "observed_minutes_1h": observed_min,
                "top": rows[:5], "flagged": flagged, "drive_write_bps": drive_bps, "drives": drives,
                "measuring": now < self.fast_until, "measure_seconds": MEASURE_S,
                "measure_elapsed": round(live_s, 1) if self.live_started else None, "live": live,
                "levels": LEVELS, "versions": versions}


sampler = Sampler()


def register(app):
    import asyncio

    sampler.start()

    @app.get("/api/storage/writers")
    async def _writers():
        return await asyncio.to_thread(sampler.report)

    @app.post("/api/storage/writers/measure")
    async def _measure():
        return await asyncio.to_thread(sampler.measure)
