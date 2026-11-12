"""Always-on heavy writer rule on synthetic sample streams (spec in the AMD SSD writes research, section 6)."""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pc_sentinel"))

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
    def test_steady_amd_bug_rate_is_a_notice_after_an_hour(self):
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


class KnownIssues(unittest.TestCase):
    def test_amd_entry_matches_and_retires_by_version(self):
        entries = sw.known_issues()
        hit = sw.match_issue("atieclxx#2", entries, {})
        self.assertEqual(hit["title"], "AMD External Events Utility")
        self.assertFalse(hit["retired"], "unknown driver version keeps the warning")
        self.assertFalse(sw.match_issue("atieclxx", entries, {"amd_adrenalin": "26.10.2"})["retired"])
        self.assertTrue(sw.match_issue("atieclxx", entries, {"amd_adrenalin": "26.11.1"})["retired"])
        self.assertTrue(sw.match_issue("atieclxx", entries, {"amd_adrenalin": "27.1.1"})["retired"])
        self.assertIsNone(sw.match_issue("chrome", entries, {}))

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
