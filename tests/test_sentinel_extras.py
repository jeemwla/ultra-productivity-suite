"""Sensor setup guide, Max Performance benchmark and the optional PSU catalog refresh (no network, no hardware)."""
import csv
import io
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import call, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pc_sentinel"))

from backend import benchmark as bm  # noqa: E402
from backend import psu_catalog, psu_refresh  # noqa: E402
from backend import sensor_setup as ss  # noqa: E402

CPU_T = [{"kind": "cpu", "sensors": [{"type": "Temperature", "value": 55}]}]
CPU_EMPTY = [{"kind": "cpu", "sensors": [{"type": "Temperature", "value": None}, {"type": "Load", "value": 10}]}]


class SensorGuide(unittest.TestCase):
    def state(self, monitors, drivers, level, groups):
        return ss.guide(monitors, drivers, level, groups)["state"]

    def test_states(self):
        self.assertEqual(self.state({}, {}, "basic", []), "lhm_missing")
        self.assertEqual(self.state({"lhm_installed": True}, {"pawnio": True}, "basic", []), "lhm_stopped")
        self.assertEqual(self.state({"lhm_installed": True, "lhm_running": True}, {"pawnio": False}, "basic", []), "driver_missing")
        self.assertEqual(self.state({"lhm_running": True}, {"pawnio": False}, "full", CPU_EMPTY), "driver_missing")
        self.assertEqual(self.state({"lhm_running": True}, {"pawnio": True}, "full", CPU_EMPTY), "driver_missing")
        self.assertEqual(self.state({"lhm_running": True}, {"pawnio": True}, "full", CPU_T), "ok")
        self.assertEqual(self.state({"lhm_running": True}, {"winring0": True}, "full", CPU_T), "legacy_driver")
        self.assertEqual(self.state(None, None, "full", CPU_T), "ok")

    def test_every_state_links_the_official_pages_and_explains(self):
        g = ss.guide({}, {}, "basic", [])
        self.assertEqual(g["links"]["lhm"], "https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases")
        self.assertEqual(g["links"]["pawnio"], "https://github.com/namazso/PawnIO.Setup/releases")
        self.assertIn("PawnIO", ss.guide({"lhm_running": True}, {}, "basic", [])["text"])
        self.assertIn("Defender", ss.guide({"lhm_running": True}, {"winring0": True}, "full", CPU_T)["text"])

    def test_driver_facts_never_raise(self):
        facts = ss.driver_facts()
        self.assertEqual(set(facts), {"pawnio", "winring0"})


class Benchmark(unittest.TestCase):
    def test_short_run_is_bounded_and_complete(self):
        # Exercise orchestration, deadline forwarding and aggregation without hashing,
        # threads or sleeps. The old test ran a real multi-core workload.
        with patch.object(bm.time, "perf_counter", side_effect=[100, 101.5]), \
                patch.object(bm.os, "cpu_count", return_value=8), \
                patch.object(bm, "cpu_pass", side_effect=[100, 800, 120, 880]) as cpu, \
                patch.object(bm, "wake_pass", side_effect=[[40, 10, 30, 20], [80, 20, 60, 40]]) as wake:
            r = bm.run("test", passes=2, cpu_seconds=0.15, wakes=20, clock=lambda: 1234)
        deadline = 100 + bm.HARD_LIMIT_S
        self.assertEqual(cpu.call_args_list, [call(1, .15, deadline), call(8, .15, deadline)] * 2)
        self.assertEqual(wake.call_args_list, [call(20, deadline)] * 2)
        self.assertEqual((r["single_mbps"], r["multi_mbps"], r["wake_us"], r["wake_p95_us"]), (110, 840, 37.5, 60))
        self.assertEqual((r["seconds"], r["ts"], r["threads"], r["passes"], r["label"]), (1.5, 1234, 8, 2, "test"))
        self.assertEqual(r["single_spread_pct"], 18.2)
        self.assertFalse(bm._lock.locked())

    def test_failed_fake_workload_releases_the_measurement_lock(self):
        with patch.object(bm, "cpu_pass", side_effect=ValueError("fixture")):
            with self.assertRaisesRegex(ValueError, "fixture"):
                bm.run("test")
        self.assertFalse(bm._lock.locked())

    def test_compare_calls_a_change_real_only_beyond_the_noise(self):
        before = {"single_mbps": 2000, "single_spread_pct": 2, "multi_mbps": 9000, "multi_spread_pct": 15, "wake_us": 40, "wake_spread_pct": 5}
        after = {"single_mbps": 2100, "single_spread_pct": 2, "multi_mbps": 9900, "multi_spread_pct": 5, "wake_us": 20, "wake_spread_pct": 5}
        c = bm.compare(before, after)
        self.assertEqual(c["single_mbps"], {"better_pct": 5.0, "noise_pct": 3.0, "real": True})
        self.assertEqual(c["multi_mbps"]["real"], False, "10% inside a 15% spread is not claimed")
        self.assertEqual(c["wake_us"]["better_pct"], 50.0, "lower wake-up time is better")
        self.assertIsNone(bm.compare(before, {})["single_mbps"])

    def test_results_persist_and_summary_pairs_off_and_on(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "b.json")
            self.assertEqual(bm.load_results(p), [])
            bm.save_result({"single_mbps": 1, "max_active": False}, p)
            runs = bm.save_result({"single_mbps": 2, "max_active": True}, p)
            s = bm.summary(runs)
            self.assertEqual((s["before"]["single_mbps"], s["after"]["single_mbps"]), (1, 2))
            self.assertIsNotNone(s["compare"])
            for _ in range(20):
                bm.save_result({"single_mbps": 3, "max_active": None}, p)
            self.assertEqual(len(bm.load_results(p)), bm.KEEP)
            with open(p, "w") as fh:
                fh.write("[broken")
            self.assertEqual(bm.load_results(p), [])
        self.assertIsNone(bm.summary([{"single_mbps": 1, "max_active": None}])["compare"], "no claim without both runs")

    def test_only_one_measurement_at_a_time(self):
        bm._lock.acquire()
        try:
            with self.assertRaises(RuntimeError):
                bm.run("x", passes=1, cpu_seconds=0.01, wakes=1)
        finally:
            bm._lock.release()


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


HEADER = ["Brand", "Series", "Sub-series", "Variant", "Wattage", "Tier", "Year", "Size", "ATX", "Input", "Modularity",
          "Efficiency", "Topology", "Secondary", "Regulation", "ODM", "Platform", "Notes"]


def sheet(n, extra=()):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(HEADER)
    for i in range(n):
        w.writerow(["Fixture" if i == 0 else "", f"S{i}", "-", "-", "650/750W", "A", "2025", "ATX", "ATX 3.x", "Wide", "Full", "G",
                    "LLC", "DC-DC", "DMA", "ODM", f"P{i}", "editorial note"])
    for row in extra:
        w.writerow(row)
    return out.getvalue()


class PsuRefresh(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        d = self.dir.name
        self.cache, self.state, self.bundle = (os.path.join(d, x) for x in ("cache.json", "state.json", "bundle.json"))
        doc, _ = psu_refresh.normalize(list(csv.reader(io.StringIO(sheet(10)))), "2026-11-01T00:00:00Z")
        with open(self.bundle, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)

    def tearDown(self):
        self.dir.cleanup()

    def refresh(self, text, now=1_800_000_000):
        return psu_refresh.refresh(fetch=lambda: text, now=now, cache=self.cache, state_file=self.state, bundle=self.bundle)

    def test_good_sheet_replaces_the_cache_with_factual_fields_and_credit(self):
        st = self.refresh(sheet(12))
        self.assertIsNone(st["last_error"])
        self.assertEqual(st["records"], 12)
        loaded = psu_catalog.load(_cache=self.cache, _bundle=self.bundle)
        self.assertEqual(loaded["storage"], "local_cache")
        self.assertEqual(loaded["source"]["name"], "SPL's PSU Tier List")
        raw = read(self.cache)
        self.assertNotIn("editorial note", raw, "Notes are dropped")
        self.assertNotIn('"tier"', raw.lower())

    def test_changed_columns_truncated_or_failed_fetch_keep_the_current_catalog(self):
        self.refresh(sheet(12))
        before = read(self.cache)
        bad_header = sheet(12).replace("Wattage", "Power", 1)
        for text in (bad_header, sheet(3), "not,a,sheet\n"):
            st = self.refresh(text)
            self.assertTrue(st["last_error"])
            self.assertEqual(read(self.cache), before)
        def boom():
            raise OSError("offline")
        st = psu_refresh.refresh(fetch=boom, now=1, cache=self.cache, state_file=self.state, bundle=self.bundle)
        self.assertIn("offline", st["last_error"])
        self.assertEqual(read(self.cache), before)

    def test_schedule(self):
        day = 86400
        self.assertFalse(psu_refresh.due({}, 10 * day, enabled=False), "off by default")
        self.assertTrue(psu_refresh.due({}, 10 * day, enabled=True))
        self.assertFalse(psu_refresh.due({"last_success_ts": 0}, 13 * day, enabled=True))
        self.assertTrue(psu_refresh.due({"last_success_ts": 0}, 14 * day, enabled=True))
        self.assertFalse(psu_refresh.due({"last_success_ts": 0, "last_error": "x", "last_attempt_ts": 14 * day}, 14 * day + 3600, enabled=True))
        self.assertTrue(psu_refresh.due({"last_success_ts": 0, "last_error": "x", "last_attempt_ts": 14 * day}, 14 * day + 7 * 3600, enabled=True))

    def test_refresh_preference_defaults_off(self):
        from backend import preferences
        self.assertFalse(preferences.defaults()["psu_refresh"])
        self.assertTrue(preferences.validate({"psu_refresh": True})["psu_refresh"])


class BenchmarkFairness(unittest.TestCase):
    BASE = {"single_mbps": 2000, "single_spread_pct": 2, "multi_mbps": 9000, "multi_spread_pct": 2, "wake_us": 40,
            "wake_spread_pct": 2, "threads": 8, "cpu": "Fixture CPU", "ac": True, "background_load_pct": 3}

    def test_unlike_runs_are_never_called_better_or_worse(self):
        after = {**self.BASE, "single_mbps": 2600}
        self.assertTrue(bm.compare(self.BASE, after)["single_mbps"]["real"])
        for change, phrase in (({"ac": False}, "battery"), ({"threads": 16}, "threads"),
                               ({"cpu": "Other CPU"}, "different processors"), ({"background_load_pct": 55}, "busy")):
            c = bm.compare(self.BASE, {**after, **change})
            self.assertFalse(c["single_mbps"]["real"], change)
            self.assertTrue(any(phrase in r for r in c["not_comparable"]), (change, c["not_comparable"]))
        self.assertEqual(bm.compare(self.BASE, after)["not_comparable"], [])

    def test_unknown_context_does_not_block_a_comparison(self):
        old = {k: v for k, v in self.BASE.items() if k not in ("ac", "cpu", "background_load_pct")}
        self.assertEqual(bm.comparable(old, self.BASE), [])

    def test_run_records_background_load(self):
        r = bm.run("x", passes=1, cpu_seconds=0.05, wakes=5, load=lambda: 12.5)
        self.assertEqual(r["background_load_pct"], 12.5)


if __name__ == "__main__":
    unittest.main()
