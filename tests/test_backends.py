"""Fast unit tests for the parts of the backends that parse or calculate (no servers, no hardware needed).

Run from the repo root:  python -m unittest discover -s tests -v
"""
import importlib.util
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pc_sentinel"))
sys.path.insert(0, os.path.join(ROOT, "core"))


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class LhmParsing(unittest.TestCase):
    def setUp(self):
        from backend import lhm_collector
        self.m = lhm_collector

    def test_plain_numbers(self):
        self.assertEqual(self.m._to_float("45.0 °C"), 45.0)
        self.assertEqual(self.m._to_float("1,5 V"), 1.5)
        self.assertIsNone(self.m._to_float(None))
        self.assertIsNone(self.m._to_float("n/a"))

    def test_throughput_is_bytes_per_second(self):
        self.assertEqual(self.m._to_float("13.9 KB/s"), 13900.0)
        self.assertEqual(self.m._to_float("1.2 MB/s"), 1200000.0)
        self.assertEqual(self.m._to_float("512 B/s"), 512.0)


class DriveMatching(unittest.TestCase):
    def test_windows_and_lhm_names_match(self):
        from backend import storage_health
        drives = [{"name": "NVMe Samsung SSD 970", "temp": None}, {"name": "WD_BLACK SN7100 1TB", "temp": None}]
        groups = [{"kind": "storage", "name": "Samsung SSD 970 EVO Plus 1TB", "sensors": [
            {"name": "Composite Temperature", "type": "Temperature", "value": 47.2},
            {"name": "Percentage Used", "type": "Level", "value": 1},
        ]}]
        out = storage_health.merge_lhm(drives, groups)
        self.assertEqual(out[0]["temp"], 47)
        self.assertEqual(out[0]["life_left"], 99)
        self.assertIsNone(out[1]["temp"], "an unrelated drive must not pick up another drive's readings")


class PowerModeGuids(unittest.TestCase):
    def test_guid_round_trip(self):
        from backend import system_tools
        for g in system_tools.OVERLAYS.values():
            self.assertEqual(system_tools._GUID.parse(g).text(), g)


class LaptopPack(unittest.TestCase):
    def setUp(self):
        self.m = load("pc_sentinel/backend/laptop.py", "sentinel_laptop")

    def test_verdicts(self):
        self.assertEqual(self.m._verdict(95, 100), "Like new.")
        self.assertTrue(self.m._verdict(85, 300).startswith("Healthy"))
        self.assertTrue(self.m._verdict(70, 600).startswith("Worn"))
        self.assertTrue(self.m._verdict(50, 900).startswith("Heavily worn"))
        self.assertIn("doesn't report", self.m._verdict(None, None))

    def test_sum_ignores_missing(self):
        self.assertEqual(self.m._sum([{"A": 10}, {"A": None}, {"A": 5}], "A"), 15)
        self.assertIsNone(self.m._sum([], "A"))


class AlarmSchedule(unittest.TestCase):
    def setUp(self):
        import datetime
        self.dt = datetime.datetime
        self.m = load("code_red_alarm/backend/clock.py", "alarm_clock")
        self.one_time = {"time_hhmm": "02:20", "target_date": "2026-11-11", "weekdays": [], "last_triggered_date": None}

    def due(self, alarm, prev, now):
        return self.m.due_day(alarm, self.dt.fromisoformat(prev), self.dt.fromisoformat(now))

    def test_rings_in_its_minute(self):
        self.assertEqual(self.due(self.one_time, "2026-11-11 02:19:59", "2026-11-11 02:20:00"), "2026-11-11")
        self.assertEqual(self.due(self.one_time, "2026-11-11 02:20:30", "2026-11-11 02:20:31"), "2026-11-11")
        self.assertIsNone(self.due(self.one_time, "2026-11-11 02:18:00", "2026-11-11 02:19:00"))

    def test_catches_up_after_sleep_but_not_hours_late(self):
        self.assertEqual(self.due(self.one_time, "2026-11-11 01:00:00", "2026-11-11 02:45:00"), "2026-11-11")
        self.assertIsNone(self.due(self.one_time, "2026-11-11 01:00:00", "2026-11-11 03:46:00"))

    def test_does_not_ring_for_a_time_already_past_at_startup(self):
        self.assertIsNone(self.due(self.one_time, "2026-11-11 02:25:00", "2026-11-11 02:25:01"))

    def test_date_weekday_and_repeat_rules(self):
        self.assertIsNone(self.due(self.one_time, "2026-11-12 02:19:59", "2026-11-12 02:20:00"), "wrong date")
        done = dict(self.one_time, last_triggered_date="2026-11-11")
        self.assertIsNone(self.due(done, "2026-11-11 02:19:59", "2026-11-11 02:20:00"), "already rang today")
        weekday = {"time_hhmm": "06:30", "target_date": None, "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri"]}
        self.assertEqual(self.due(weekday, "2026-11-11 06:29:59", "2026-11-11 06:30:00"), "2026-11-11")  # Wednesday
        self.assertIsNone(self.due(weekday, "2026-11-14 06:29:59", "2026-11-14 06:30:00"), "Saturday")

    def test_late_night_alarm_after_midnight_wake(self):
        late = {"time_hhmm": "23:55", "target_date": None, "weekdays": [], "last_triggered_date": None}
        self.assertEqual(self.due(late, "2026-11-10 23:50:00", "2026-11-11 00:10:00"), "2026-11-10")

    def test_next_ring_for_wake_timer(self):
        nxt = lambda a, now: self.m.next_ring(a, self.dt.fromisoformat(now))
        weekday = {"enabled": True, "time_hhmm": "07:15", "target_date": None, "weekdays": ["Mon", "Wed", "Fri"]}
        self.assertEqual(nxt(weekday, "2026-11-11 05:00:00"), self.dt.fromisoformat("2026-11-11 07:15:00"))  # Wed
        self.assertEqual(nxt(weekday, "2026-11-11 08:00:00"), self.dt.fromisoformat("2026-11-13 07:15:00"))  # Fri
        once = {"enabled": True, "time_hhmm": "02:20", "target_date": "2026-11-12", "weekdays": []}
        self.assertEqual(nxt(once, "2026-11-11 23:00:00"), self.dt.fromisoformat("2026-11-12 02:20:00"))
        self.assertIsNone(nxt(dict(once, enabled=False), "2026-11-11 23:00:00"))
        self.assertIsNone(nxt(once, "2026-11-12 03:00:00"), "a one-time alarm in the past never comes back")
        rang = dict(weekday, last_triggered_date="2026-11-11")
        self.assertEqual(nxt(rang, "2026-11-11 07:15:30"), self.dt.fromisoformat("2026-11-13 07:15:00"))

    def test_local_now_matches_system_clock(self):
        import datetime
        gap = abs((self.m.local_now() - datetime.datetime.now()).total_seconds())
        if "TZ" not in os.environ:
            self.assertLess(gap, 5)


class AlarmApi(unittest.TestCase):
    """Create, edit and validate alarms through the real routes, on a throwaway database."""

    @classmethod
    def setUpClass(cls):
        import tempfile
        cls._data = os.environ.get("ULTRASUITE_DATA")
        os.environ["ULTRASUITE_DATA"] = tempfile.mkdtemp()
        d = os.path.join(ROOT, "code_red_alarm", "backend")
        spec = importlib.util.spec_from_file_location("alarm_pkg_test", os.path.join(d, "__init__.py"), submodule_search_locations=[d])
        pkg = importlib.util.module_from_spec(spec)
        sys.modules["alarm_pkg_test"] = pkg
        spec.loader.exec_module(pkg)
        import importlib as il
        cls.m = il.import_module("alarm_pkg_test.main")
        cls.m.init_db()
        from fastapi.testclient import TestClient
        cls.c = TestClient(cls.m.app, base_url="http://127.0.0.1:8081")  # no "with": the scheduler loop stays off

    @classmethod
    def tearDownClass(cls):
        if cls._data is None:
            os.environ.pop("ULTRASUITE_DATA", None)
        else:
            os.environ["ULTRASUITE_DATA"] = cls._data

    def body(self, **kw):
        b = {"label": "Gym", "time_hhmm": "07:15", "weekdays": ["Mon"], "recurring": True, "code_red": False,
             "custom_phrase": "", "ring_duration_sec": 600, "math_mission_difficulty": 0}
        b.update(kw)
        return b

    def test_edit_in_place(self):
        r = self.c.post("/api/alarms", json=self.body())
        self.assertEqual(r.status_code, 200)
        aid = r.json()["id"]
        r = self.c.put(f"/api/alarms/{aid}", json=self.body(label="Gym late", time_hhmm="07:45"))
        self.assertEqual(r.status_code, 200)
        a = next(x for x in r.json()["alarms"] if x["id"] == aid)
        self.assertEqual((a["label"], a["time_hhmm"], a["last_triggered_date"]), ("Gym late", "07:45", None))

    def test_rejects_other_websites_and_rebinding(self):
        r = self.c.delete("/api/alarms/x", headers={"Origin": "https://evil.example"})
        self.assertEqual(r.status_code, 403, "a website must not change alarms")
        r = self.c.get("/api/alarms", headers={"Host": "attacker.example"})
        self.assertEqual(r.status_code, 403, "DNS rebinding: requests not addressed to this PC are refused")
        self.assertEqual(self.c.post("/api/alarms", json=self.body(), headers={"Origin": "http://127.0.0.1:8081"}).status_code, 200)

    def test_rejects_bad_input(self):
        self.assertEqual(self.c.put("/api/alarms/nope", json=self.body()).status_code, 404)
        self.assertEqual(self.c.post("/api/alarms", json=self.body(time_hhmm="25:00")).status_code, 400)
        self.assertEqual(self.c.post("/api/alarms", json=self.body(weekdays=[], target_date=None)).status_code, 400)
        self.assertEqual(self.c.post("/api/alarms", json=self.body(code_red=True, custom_phrase=" ")).status_code, 400)


class InsightsChart(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._data = os.environ.get("ULTRASUITE_DATA")
        os.environ["ULTRASUITE_DATA"] = tempfile.mkdtemp()
        self.m = load("insights/insights.py", "insights_under_test")

    def tearDown(self):
        if self._data is None:
            os.environ.pop("ULTRASUITE_DATA", None)
        else:
            os.environ["ULTRASUITE_DATA"] = self._data

    def test_last_slot_is_today_in_local_time(self):
        import datetime, time
        now = time.time()
        keys = self.m.bucket_keys(now, 30)
        self.assertEqual(len(keys), 30)
        self.assertEqual(keys[-1], datetime.date.fromtimestamp(now).isoformat())
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(len(set(keys)), 30)

    def test_day_view_has_24_hours_ending_now(self):
        import datetime, time
        now = time.time()
        keys = self.m.bucket_keys(now, 1)
        self.assertEqual(len(keys), 24)
        self.assertEqual(keys[-1], datetime.datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:00"))


class Versions(unittest.TestCase):
    def test_version_compare(self):
        import devtools
        self.assertGreater(devtools._vtuple("2.10.0"), devtools._vtuple("2.9.1"))
        self.assertGreater(devtools._vtuple("v2.1.0"), devtools._vtuple("2.0.1"))
        self.assertEqual(devtools._vtuple("2.1"), devtools._vtuple("2.1.0"))

    def test_update_summary(self):
        import devtools
        notes = "## Ultra Suite 2.2.0: title\n\n### Fixed\n- **Alarms could ring hours off.** Long detail here.\n- Other"
        self.assertEqual(devtools.summarize_notes(notes), "Alarms could ring hours off.")
        lead = "## Title\n\nAlarms now ring on your PC's clock, and you can edit them.\n\n### Fixed\n- x"
        self.assertEqual(devtools.summarize_notes(lead), "Alarms now ring on your PC's clock, and you can edit them.")
        self.assertEqual(devtools.summarize_notes(""), "")
        self.assertLessEqual(len(devtools.summarize_notes("- " + "word " * 80)), 140)

    def test_local_guard_rule(self):
        import devtools
        ok = devtools.request_allowed
        self.assertTrue(ok("GET", "127.0.0.1:8084", None))
        self.assertTrue(ok("POST", "localhost:8081", "http://127.0.0.1:8081"))
        self.assertTrue(ok("POST", "127.0.0.1:8081", "http://tauri.localhost"))
        self.assertTrue(ok("POST", "127.0.0.1:8081", None), "no Origin: a local tool, not a browser page")
        self.assertFalse(ok("POST", "127.0.0.1:8081", "https://example.com"))
        self.assertFalse(ok("POST", "127.0.0.1:8081", "http://127.0.0.1.evil.com"))
        self.assertFalse(ok("GET", "evil.com", None))

    def test_version_file(self):
        import devtools
        with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as f:
            self.assertEqual(devtools.version(), f.read().strip())


if __name__ == "__main__":
    unittest.main()
