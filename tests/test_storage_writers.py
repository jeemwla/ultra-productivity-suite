"""Always-on heavy writer rule on synthetic sample streams (spec in the AMD SSD writes research, section 6)."""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pc_sentinel"))

with tempfile.TemporaryDirectory() as _import_dir, patch.dict(os.environ, {
    "APPDATA": _import_dir, "ULTRASUITE_CONF": _import_dir,
}):
    from backend import storage_writers as sw  # noqa: E402

T0 = 1_794_000_000.0  # fixed epoch, a whole minute


def stream(tracker, seconds, rate_fn, start=T0, step=30, extra=None):
    t = start
    tracker.feed(t, {"atieclxx": 0.0, **(extra or {})})
    while t < start + seconds:
        t += step
        tracker.feed(t, {"atieclxx": rate_fn(t), **(extra or {})})
    return t


def level_of(tracker, now, name="atieclxx"):
    return next((r["level"] for r in tracker.evaluate(now) if r["name"] == name), "absent")


class Rule(unittest.TestCase):
    def test_synthetic_steady_rate_is_a_notice_after_an_hour(self):
        tr = sw.WriterTracker()
        now = stream(tr, 3600, lambda t: 0.65e6)
        self.assertEqual(level_of(tr, now), "notice")
        row = tr.evaluate(now)[0]
        self.assertAlmostEqual(row["avg_bps_1h"], 0.65e6, delta=0.02e6)
        self.assertAlmostEqual(row["per_day_estimate"] / 1e9, 56.2, delta=2)

    def test_steady_5mbps_is_warning_in_one_hour_and_heavy_after_six(self):
        tr = sw.WriterTracker()
        now = stream(tr, 3600, lambda t: 5.5e6)
        self.assertEqual(level_of(tr, now), "warning")
        now = stream(tr, 5 * 3600, lambda t: 5.5e6, start=now)
        self.assertEqual(level_of(tr, now), "heavy")

    def test_bursty_50mbps_every_ten_minutes_never_alerts(self):
        tr = sw.WriterTracker()
        now = stream(tr, 6 * 3600, lambda t: 50e6 if int(t // 60) % 10 == 0 else 0.0)
        self.assertIsNone(level_of(tr, now))

    def test_short_observation_never_alerts(self):
        tr = sw.WriterTracker()
        now = stream(tr, 20 * 60, lambda t: 20e6)
        self.assertIsNone(level_of(tr, now), "20 minutes of data is not an hour")

    def test_continuity_boundary_89_vs_90_percent(self):
        def run(idle_minutes):
            tr = sw.WriterTracker()
            first = int(T0 // 60)
            now = stream(tr, 3600, lambda t: 0.0 if 10 <= int(t // 60) - first < 10 + idle_minutes else 3e6)
            return level_of(tr, now)
        self.assertEqual(run(6), "warning", "54 of 60 minutes active is 90%")
        self.assertIsNone(run(7), "53 of 60 minutes is 88%")

    def test_sleep_gap_and_new_process_are_not_counted(self):
        tr = sw.WriterTracker()
        tr.feed(T0, {"a": 0.0})
        self.assertTrue(tr.feed(T0 + 30, {"a": 1e6}))
        self.assertFalse(tr.feed(T0 + 3600, {"a": 1e6}), "an hour gap is sleep")
        self.assertTrue(tr.feed(T0 + 3630, {"a": 1e6, "newapp": 9e9}))
        self.assertNotIn("newapp", tr.minutes, "first sample of a new program is discarded")
        self.assertTrue(tr.feed(T0 + 3660, {"a": 1e6, "newapp": 1e3}))
        self.assertIn("newapp", tr.minutes)

    def test_restarted_instances_group_by_program(self):
        tr = sw.WriterTracker()
        tr.feed(T0, {"svc": 0, "svc#1": 0})
        tr.feed(T0 + 30, {"svc": 1e6, "svc#1": 1e6, "_Total": 5e6, "Idle": 1.0, "bad": -5, "nan": float("nan")})
        self.assertEqual(set(tr.minutes), {"svc"})
        self.assertAlmostEqual(sum(tr.minutes["svc"].values()), 60e6)

    def test_daily_rollup_round_trip_and_corruption(self):
        tr = sw.WriterTracker()
        stream(tr, 600, lambda t: 2e6)
        doc = json.loads(json.dumps(tr.dump()))
        other = sw.WriterTracker()
        self.assertTrue(other.load(doc))
        self.assertEqual(other.daily, {d: {n: round(b) for n, b in v.items()} for d, v in tr.daily.items()})
        for bad in ({}, {"version": 2}, {"version": 1, "daily": []}, {"version": 1, "daily": {"not-a-date": {}}}, {"version": 1, "daily": {"2026-11-12": {"x": "y"}}}):
            fresh = sw.WriterTracker()
            fresh.load(bad)
            self.assertIsInstance(fresh.daily, dict)

    def test_corrupt_store_file_starts_empty(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "w.json")
            with open(p, "w") as fh:
                fh.write("{not json")
            s = sw.Sampler(store_path=p)
            self.assertEqual(s.tracker.daily, {})
            self.assertIn("top", s.report())


class DriveCap(unittest.TestCase):
    """Regression: Sentinel once showed its own WebView2 window 'writing 4.1 MB/s, 352 GB a day' while the drives
    received about 1 MB/s in total and the app's files did not change. Request counters include inter-process traffic."""

    def test_program_is_never_shown_writing_more_than_the_drives_received(self):
        tr = sw.WriterTracker()
        t = T0
        tr.feed(t, {"msedgewebview2 (ultra suite)": 0.0}, drive_bps=1e6)
        while t < T0 + 3600:
            t += 30
            tr.feed(t, {"msedgewebview2 (ultra suite)": 4.1e6}, drive_bps=1e5)
        row = tr.evaluate(t)[0]
        self.assertIsNone(row["level"], "100 KB/s reached the drives: below every threshold")
        self.assertLessEqual(row["avg_bps_1h"], 1e5 + 1)
        self.assertAlmostEqual(row["issued_bps_1h"], 4.1e6, delta=0.05e6)
        self.assertTrue(row["capped"])
        today = max(tr.daily)
        self.assertLessEqual(tr.daily[today]["msedgewebview2 (ultra suite)"], 1e5 * 3600 + 1e5 * 60)

    def test_real_writer_still_flags_when_the_drives_saw_the_bytes(self):
        tr = sw.WriterTracker()
        t = T0
        tr.feed(t, {"atieclxx": 0.0}, drive_bps=3e6)
        while t < T0 + 3600:
            t += 30
            tr.feed(t, {"atieclxx": 2.5e6}, drive_bps=3e6)
        row = tr.evaluate(t)[0]
        self.assertEqual(row["level"], "warning")
        self.assertFalse(row["capped"])

    def test_missing_drive_counter_falls_back_to_raw_requests(self):
        tr = sw.WriterTracker()
        now = stream(tr, 3600, lambda t: 2.5e6)
        self.assertEqual(level_of(tr, now), "warning")

    def test_v1_store_with_raw_counters_is_discarded(self):
        tr = sw.WriterTracker()
        self.assertFalse(tr.load({"version": 1, "daily": {"2026-11-12": {"msedgewebview2": 1.93e10}}}))
        self.assertEqual(tr.daily, {})
        self.assertEqual(tr.dump()["version"], 2)


def table(*rows):
    """{pid: (image, ppid, create_time, write_bytes)}"""
    return {pid: (img, ppid, ct, w) for pid, img, ppid, ct, w in rows}


class ProcessAttribution(unittest.TestCase):
    def counters(self, snaps, rid=None):
        it = iter(snaps)
        return sw.ProcessCounters(snapshot=lambda: next(it), integrity=lambda pid: (rid or {}).get(pid, 0x2000))

    def test_same_named_processes_add_up_and_helpers_name_their_host(self):
        base = [(10, "Ultra Suite.exe", 1, 100, 0), (20, "msedgewebview2.exe", 10, 200, 0), (21, "msedgewebview2.exe", 20, 210, 0),
                (22, "msedgewebview2.exe", 20, 220, 0), (30, "SearchHost.exe", 1, 100, 0), (31, "msedgewebview2.exe", 30, 300, 0)]
        later = [(p, img, pp, ct, w + {20: 300, 21: 600, 22: 900, 31: 3000, 10: 30}.get(p, 0)) for p, img, pp, ct, w in base]
        c = self.counters([table(*base), table(*later)])
        self.assertEqual(c.rates(1000.0), (None, None))
        rates, sandboxed = c.rates(1030.0)
        self.assertAlmostEqual(rates["msedgewebview2 (ultra suite)"], (300 + 600 + 900) / 30)
        self.assertAlmostEqual(rates["msedgewebview2 (searchhost)"], 100)
        self.assertAlmostEqual(rates["ultra suite"], 1)
        self.assertEqual(sandboxed, 0)
        self.assertTrue(sw.is_self("msedgewebview2 (ultra suite)") and sw.is_self("ultrasuite-core"))
        self.assertFalse(sw.is_self("msedgewebview2 (searchhost)"))

    def test_untrusted_sandboxed_renderers_are_left_out_and_reported_apart(self):
        a = table((10, "Ultra Suite.exe", 1, 100, 0), (21, "msedgewebview2.exe", 10, 210, 0))
        b = table((10, "Ultra Suite.exe", 1, 100, 0), (21, "msedgewebview2.exe", 10, 210, 4_100_000 * 30))
        c = self.counters([a, b], rid={21: 0x0})
        c.rates(0.0)
        rates, sandboxed = c.rates(30.0)
        self.assertNotIn("msedgewebview2 (ultra suite)", rates)
        self.assertAlmostEqual(sandboxed, 4.1e6)

    def test_pid_reuse_counter_reset_and_new_processes_have_no_rate(self):
        a = table((5, "app.exe", 1, 100, 1_000_000), (6, "old.exe", 1, 100, 50))
        b = table((5, "app.exe", 1, 999, 10), (6, "old.exe", 1, 100, 40), (7, "new.exe", 1, 500, 9e9))
        c = self.counters([a, b])
        c.rates(0.0)
        rates, _ = c.rates(30.0)
        self.assertEqual(rates, {}, "reused PID, a counter going backwards and a brand new PID all wait for a baseline")

    def test_reused_parent_pid_does_not_borrow_a_newer_host_name(self):
        t = table((10, "Impostor.exe", 1, 900, 0), (20, "msedgewebview2.exe", 10, 200, 0))
        self.assertEqual(sw.label(20, t), "msedgewebview2", "parent started after the child: its PID was reused")

    def test_unreadable_snapshot_reports_nothing(self):
        c = sw.ProcessCounters(snapshot=lambda: None)
        self.assertEqual(c.rates(1.0), (None, None))

    @unittest.skipUnless(os.name == "nt", "Windows snapshot")
    def test_windows_snapshot_matches_psutil_for_this_process(self):
        snap = sw._snapshot_nt()
        self.assertIsNotNone(snap)
        image, ppid, _, written = snap[os.getpid()]
        import psutil
        me = psutil.Process()
        self.assertEqual(image.lower(), me.name().lower())
        self.assertEqual(ppid, me.ppid())
        self.assertLessEqual(written, me.io_counters().write_bytes)


class KnownIssues(unittest.TestCase):
    def test_amd_reported_target_never_retires_by_version_alone(self):
        entries = sw.known_issues()
        hit = sw.match_issue("atieclxx#2", entries, {})
        self.assertEqual(hit["title"], "AMD External Events Utility")
        self.assertFalse(hit["retired"], "unknown driver version keeps the warning")
        self.assertFalse(sw.match_issue("atieclxx", entries, {"amd_adrenalin": "26.10.2"})["retired"])
        self.assertFalse(sw.match_issue("atieclxx", entries, {"amd_adrenalin": "26.11.1"})["retired"])
        self.assertFalse(sw.match_issue("atieclxx", entries, {"amd_adrenalin": "27.1.1"})["retired"])
        self.assertIsNone(sw.match_issue("chrome", entries, {}))

    def test_only_vendor_confirmed_fix_with_dated_source_can_retire(self):
        fixed = {"component": "fixture", "version": "2.1", "status": "confirmed",
                 "source_url": "https://vendor.example/release-notes", "confirmed_on": "2026-11-12"}
        def match(fix, version="2.1"):
            return sw.match_issue("fixture", [{"exe": "fixture", "fixed_in": fix}], {"fixture": version})
        self.assertTrue(match(fixed)["retired"])
        self.assertTrue(match(fixed, "3.0")["retired"])
        for version in (None, "unknown", "2.0"):
            self.assertFalse(match(fixed, version)["retired"])
        for status in (None, "planned", "reported_target"):
            self.assertFalse(match({**fixed, "status": status})["retired"])
        for field in ("source_url", "confirmed_on", "version", "component"):
            self.assertFalse(match({k: v for k, v in fixed.items() if k != field})["retired"])

    def test_amd_advisory_attribution_and_verification_limit(self):
        entry = sw.known_issues()[0]
        self.assertEqual(entry["fixed_in"]["status"], "reported_target")
        self.assertIn("PA-365", entry["sources"][0]["title"])
        self.assertIn("under development", entry["explanation"])
        self.assertEqual(entry["last_reviewed"], "2026-11-12")
        self.assertNotIn("once a second", entry["explanation"])

    def test_every_entry_has_sources_and_undo_for_commands(self):
        for e in sw.known_issues():
            self.assertTrue(e["sources"] and all(s["url"].startswith("https://") for s in e["sources"]))
            for fix in e["fixes"]:
                if "command" in fix:
                    self.assertIn("undo", fix)

    def test_missing_known_issue_file_is_empty(self):
        self.assertEqual(sw.known_issues(os.path.join(ROOT, "no-such.json")), [])


if __name__ == "__main__":
    unittest.main()
