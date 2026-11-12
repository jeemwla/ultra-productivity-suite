"""Always-on heavy writer detection for the Storage page (read-only, nothing leaves the PC).

Every 30 seconds Sentinel reads each process's cumulative write counter (one NtQuerySystemInformation snapshot, which
needs no admin rights and includes SYSTEM services) and the drives' own write rate (PDH PhysicalDisk). A process is
flagged only when it writes a lot AND almost all the time: installs, game updates, builds and backups are bursty and
stay quiet.

  Level    Window   Average rate   Continuity (minutes with >100 KB written)
  notice   1 hour   0.5 MB/s       at least 90%
  warning  1 hour   2 MB/s         at least 90%
  heavy    6 hours  5 MB/s         at least 90%

Why the raw counter is not trusted on its own: Windows' per-process "write" counter counts every write request the
process issues, including traffic to other processes (pipes, shared sections) and network sends. Browser engines
(Chrome, Edge WebView2, Electron) move megabytes per second between their own processes, which once made Sentinel
report its own WebView2 window as writing 350 GB a day while the drives received almost nothing. So:
  1. Processes at Untrusted integrity (sandboxed browser renderers) cannot open files at all; their counter is
     inter-process traffic and is left out (reported as a total for transparency).
  2. Each program's bytes per minute are capped by what all physical drives received in that minute. A program can
     never be shown writing more to disk than the disks were written. Levels use the capped value.
  3. Every process is counted (PIDs are tracked one by one, so 14 helper processes with the same name add up instead
     of one of them hiding the rest), and WebView2 helpers are named after the app that hosts them.
Host writes are still not the same as flash wear. Sentinel never stops a service or a process; fixes are shown for
the user to run.
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
IGNORED_INSTANCES = {"_total", "idle", "system idle process"}
# Ultra Suite's own processes (both editions), so users can see its share and never mistake it for a stranger.
SELF_NAMES = {"ultrasuite", "ultra-suite", "ultra suite", "ultrasuite-core", "ultra suite dev", "ultrasuite-dev-core"}
EMBEDDED_RUNTIMES = {"msedgewebview2"}  # helper processes named after the app that hosts them


def base_name(instance):
    """PDH instance "chrome#3" -> "chrome"; groups restarts and multiple instances of one program."""
    return re.sub(r"#\d+$", "", str(instance)).strip().lower()


def is_self(name):
    """'ultra suite' or a hosted helper such as 'msedgewebview2 (ultra suite)'."""
    m = re.fullmatch(r"(.+?) \((.+)\)", name)
    return name in SELF_NAMES or bool(m and m.group(2) in SELF_NAMES)


class WriterTracker:
    """Pure accumulator: feed(rates) at each sample, evaluate() for levels. Time is passed in (testable)."""

    def __init__(self, interval=INTERVAL_S):
        self.interval = interval
        self.last_t = None
        self.minutes = {}          # name -> {minute_index: bytes the process issued}
        self.disk = {}             # minute_index -> bytes all physical drives received
        self.observed = set()      # minute indexes where sampling happened
        self.daily = {}            # "YYYY-MM-DD" -> {name: bytes, capped by the drives}
        self.seen = set()
        self.drive_bps = None
        self.sandboxed_bps = None

    def capped(self, name, minute):
        """Bytes a program can have written to disk in one minute: never more than the drives received then."""
        b = self.minutes.get(name, {}).get(minute, 0.0)
        return min(b, self.disk[minute]) if minute in self.disk else b

    def feed(self, t, rates, drive_bps=None, day=None, sandboxed_bps=None):
        """rates: {program: bytes per second averaged since the previous sample}. drive_bps: all physical drives."""
        prev, self.last_t = self.last_t, t
        if prev is None:
            self.seen = {base_name(k) for k in rates}
            return False
        dt = t - prev
        if dt <= 0 or dt > 2 * self.interval + 1:  # clock change, sleep or hibernate: skip this delta
            self.seen = {base_name(k) for k in rates}
            return False
        self.drive_bps = drive_bps
        self.sandboxed_bps = sandboxed_bps
        minute = int(t // 60)
        self.observed.add(minute)
        disk_ok = isinstance(drive_bps, (int, float)) and drive_bps == drive_bps and drive_bps >= 0
        old_disk = self.disk.get(minute)
        if disk_ok:
            self.disk[minute] = (old_disk or 0.0) + drive_bps * dt
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
            before = m.get(minute, 0.0)
            m[minute] = before + b
            # Daily totals grow by the change in this minute's capped value (never negative: both inputs only grow).
            capped_before = min(before, old_disk) if old_disk is not None else before
            gained = max(0.0, self.capped(name, minute) - capped_before)
            d = self.daily.setdefault(day, {})
            d[name] = d.get(name, 0.0) + gained
        self._trim(minute)
        return True

    def _trim(self, minute):
        floor = minute - KEEP_MINUTES
        self.observed = {m for m in self.observed if m > floor}
        self.disk = {m: b for m, b in self.disk.items() if m > floor}
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
        issued = sum(per.get(m, 0.0) for m in obs)
        caps = [self.capped(name, m) for m in obs]
        written = sum(caps)
        active = sum(1 for b in caps if b > ACTIVE_BYTES_PER_MIN)
        return {"avg_bps": written / (len(obs) * 60), "continuity": active / len(obs), "coverage": len(obs) / window_min,
                "bytes": written, "issued_bps": issued / (len(obs) * 60)}

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
                         "issued_bps_1h": round(hour["issued_bps"], 1),
                         "capped": hour["issued_bps"] > hour["avg_bps"] * 1.05 + 1024,
                         "active_share_1h": round(hour["continuity"], 3), "coverage_1h": round(hour["coverage"], 3),
                         "bytes_today": round(self.daily.get(today, {}).get(name, 0.0)),
                         "per_day_estimate": round(hour["avg_bps"] * 86400)})
        order = {"heavy": 0, "warning": 1, "notice": 2, None: 3}
        rows.sort(key=lambda r: (order[r["level"]], -r["bytes_today"], -r["avg_bps_1h"]))
        return rows

    # daily totals survive restarts (written at most every 10 minutes, so Sentinel itself stays a light writer)
    # Version 2: totals are capped by the drives. Version 1 files held raw request counters (inflated by
    # inter-process traffic) and are discarded on load rather than shown as drive writes.
    STORE_VERSION = 2

    def dump(self):
        return {"version": self.STORE_VERSION,
                "daily": {d: {n: round(b) for n, b in v.items() if b >= 1e6} for d, v in self.daily.items()}}

    def load(self, doc):
        try:
            if doc.get("version") != self.STORE_VERSION or not isinstance(doc.get("daily"), dict):
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


# ----------------------------------------------------------------------------- per-process counters (Windows)
C_DISK = r"\PhysicalDisk(*)\Disk Write Bytes/sec"
UNTRUSTED_BELOW = 0x1000  # integrity RIDs below Low (Untrusted, 0x0) cannot open files for writing


def program_name(image):
    """'Ultra Suite.exe' -> 'ultra suite'; kernel entries keep their Windows names."""
    n = str(image or "").strip()
    return (n[:-4] if n.lower().endswith(".exe") else n).lower()


def _snapshot_nt():
    """One NtQuerySystemInformation(SystemProcessInformation) call: every process, no admin needed, a few ms.
    Returns {pid: (image, parent_pid, create_time, write_transfer_bytes)} or None when unavailable."""
    import ctypes
    import struct
    if os.name != "nt" or struct.calcsize("P") != 8:  # offsets below are the documented x64 layout
        return None
    ntdll = ctypes.WinDLL("ntdll")
    size = 1 << 20
    for _ in range(6):
        buf = ctypes.create_string_buffer(size)
        need = ctypes.c_ulong(0)
        status = ntdll.NtQuerySystemInformation(5, buf, size, ctypes.byref(need)) & 0xFFFFFFFF
        if status == 0:
            break
        if status != 0xC0000004:  # STATUS_INFO_LENGTH_MISMATCH: grow and retry; anything else is a failure
            return None
        size = max(size * 2, need.value + (256 << 10))
        if size > 64 << 20:
            return None
    else:
        return None
    raw, out, off = buf.raw, {}, 0
    for _ in range(100000):
        if off + 256 > len(raw):
            return None
        nxt = struct.unpack_from("<I", raw, off)[0]
        create = struct.unpack_from("<q", raw, off + 32)[0]
        name_len, name_ptr = struct.unpack_from("<H", raw, off + 56)[0], struct.unpack_from("<Q", raw, off + 64)[0]
        pid, ppid = struct.unpack_from("<QQ", raw, off + 80)
        write = struct.unpack_from("<q", raw, off + 240)[0]
        image = ctypes.wstring_at(name_ptr, name_len // 2) if name_ptr and name_len else ("System Idle Process" if pid == 0 else "")
        base = ctypes.addressof(buf)
        if name_ptr and not (base <= name_ptr < base + len(raw)):  # names live inside our buffer; anything else is corrupt
            return None
        out[int(pid)] = (image, int(ppid), int(create), int(write))
        if nxt == 0:
            return out
        off += nxt
    return None


def _snapshot_psutil():
    """Slower fallback (about 0.3 s for 500 processes) with the same shape."""
    try:
        import psutil
    except ImportError:
        return None
    out = {}
    for p in psutil.process_iter(["pid", "name", "ppid", "create_time"]):
        try:
            out[p.info["pid"]] = (p.info["name"] or "", p.info["ppid"] or 0, int((p.info["create_time"] or 0) * 1e7),
                                  int(p.io_counters().write_bytes))
        except Exception:  # noqa: BLE001 - a process may exit or deny access mid-scan
            continue
    return out


def integrity_rid(pid):
    """Mandatory integrity level RID of a process (0x0 Untrusted, 0x1000 Low, 0x2000 Medium ...), None if unreadable."""
    if os.name != "nt" or pid <= 4:
        return None
    import ctypes
    from ctypes import wintypes
    k32, adv = ctypes.WinDLL("kernel32"), ctypes.WinDLL("advapi32")
    k32.OpenProcess.restype = wintypes.HANDLE
    adv.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
    adv.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
    h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    tok = wintypes.HANDLE()
    try:
        if not adv.OpenProcessToken(wintypes.HANDLE(h), 0x0008, ctypes.byref(tok)):  # TOKEN_QUERY
            return None
        n = wintypes.DWORD(0)
        adv.GetTokenInformation(tok, 25, None, 0, ctypes.byref(n))  # TokenIntegrityLevel
        if not 0 < n.value <= 4096:
            return None
        info = ctypes.create_string_buffer(n.value)
        if not adv.GetTokenInformation(tok, 25, info, n, ctypes.byref(n)):
            return None
        sid = ctypes.c_void_p(ctypes.cast(info, ctypes.POINTER(ctypes.c_void_p))[0])
        count = adv.GetSidSubAuthorityCount(sid).contents.value
        return int(adv.GetSidSubAuthority(sid, count - 1).contents.value) if count else None
    finally:
        if tok:
            k32.CloseHandle(tok)
        k32.CloseHandle(wintypes.HANDLE(h))


def label(pid, table, limit=8):
    """Program name; WebView2 helpers become 'msedgewebview2 (host app)' after the nearest non-WebView2 ancestor.
    An ancestor only counts when it started before the child (a reused parent PID is ignored)."""
    image, ppid, created, _ = table[pid]
    name = program_name(image)
    if name not in EMBEDDED_RUNTIMES:
        return name
    child_created = created
    for _ in range(limit):
        parent = table.get(ppid)
        if not parent or parent[2] > child_created or ppid == pid:
            break
        pname = program_name(parent[0])
        if pname != name:
            return f"{name} ({pname})"
        child_created, ppid = parent[2], parent[1]
    return name


class ProcessCounters:
    """Turns cumulative per-PID write counters into per-program rates since the previous call."""

    def __init__(self, snapshot=None, integrity=integrity_rid):
        self.snapshot = snapshot or (lambda: _snapshot_nt() or _snapshot_psutil())
        self.integrity = integrity
        self.prev = None           # {(pid, create): write_bytes}
        self.prev_t = None
        self.rid = {}              # (pid, create) -> integrity RID, read once per process

    def rates(self, now):
        """({program: bytes/s}, sandboxed bytes/s), or (None, None) on the first call or when unreadable."""
        table = self.snapshot()
        if not table:
            return None, None
        current = {(pid, row[2]): row[3] for pid, row in table.items()}
        prev, prev_t, self.prev, self.prev_t = self.prev, self.prev_t, current, now
        self.rid = {k: v for k, v in self.rid.items() if k in current}
        if prev is None or prev_t is None or now <= prev_t:
            return None, None
        dt, out, sandboxed = now - prev_t, {}, 0.0
        for key, written in current.items():
            before = prev.get(key)
            if before is None or written < before:  # new process (no baseline yet) or counter reset
                continue
            bps = (written - before) / dt
            if bps <= 0:
                continue
            if key not in self.rid:
                self.rid[key] = self.integrity(key[0])
            rid = self.rid[key]
            if rid is not None and rid < UNTRUSTED_BELOW:  # sandboxed renderer: inter-process traffic only
                sandboxed += bps
                continue
            name = label(key[0], table)
            out[name] = out.get(name, 0.0) + bps
        return out, sandboxed


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
        pdh = Pdh([C_DISK])
        counters = ProcessCounters()
        counters.rates(time.time())  # baseline
        last = time.time()
        while True:
            fast = time.time() < self.fast_until
            time.sleep(FAST_INTERVAL_S if fast else INTERVAL_S)
            now = time.time()
            procs, sandboxed = counters.rates(now)
            data = pdh.collect() if pdh.ok else {}
            if procs is None:
                if counters.prev is None:
                    self.available = False
                last = now
                continue
            self.available = True
            disks = {k: v for k, v in (data.get(C_DISK) or {}).items() if k.lower() != "_total"}
            with self.lock:
                self.tracker.interval = FAST_INTERVAL_S if fast else INTERVAL_S
                self.tracker.feed(now, procs, sum(disks.values()) if disks else None, sandboxed_bps=sandboxed)
                self.drives = {k: round(v) for k, v in disks.items()}
                drive_total = sum(disks.values()) if disks else None
                self.last_rates = {}
                for n, bps in procs.items():
                    if n not in IGNORED_INSTANCES:  # live view uses the same cap as the levels
                        self.last_rates[n] = min(bps, drive_total) if drive_total is not None else bps
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
            sandboxed_bps = self.tracker.sandboxed_bps
        for r in rows:
            issue = match_issue(r["name"], entries, versions)
            r["known_issue"] = issue
            r["self"] = is_self(r["name"])
        flagged = [r for r in rows if r["level"]]
        return {"available": self.available, "interval_s": INTERVAL_S, "observed_minutes_1h": observed_min,
                "method": "process_writes_capped_by_drives", "sandboxed_bps": sandboxed_bps,
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
