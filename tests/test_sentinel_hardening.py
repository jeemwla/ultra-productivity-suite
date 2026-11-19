"""PC Sentinel backend safety: bounded polling, quiet repeated errors, input validation, cached settings and the
developer preview mode. No hardware is read or changed: collectors and Windows calls are replaced with fakes."""
import asyncio
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pc_sentinel"))
sys.path.insert(0, os.path.join(ROOT, "core"))

from backend import laptop, preferences, system_tools  # noqa: E402
import dev_preview as preview  # noqa: E402
from backend import storage_writers  # noqa: E402

with patch.object(storage_writers, "register", lambda app: None):  # no background PDH sampler during tests
    from backend import main  # noqa: E402


def run(coro):
    return asyncio.run(coro)


class PollLoop(unittest.TestCase):
    def setUp(self):
        self.saved = {k: (dict(v) if isinstance(v, dict) else v) for k, v in main.latest.items()}
        main._errors.clear()
        self.prefs = {"router_ping": False, "router_vpn": False, "pages": {}, "revision": "0", "storage_status": "ok"}

    def tearDown(self):
        main.latest.clear()
        main.latest.update(self.saved)

    def test_hanging_collector_keeps_last_reading_and_marks_it_stale(self):
        main.latest["hardware"] = {"live": True, "source": "lhm", "summary": {"cpu_temp": 51}}

        async def hang():
            await asyncio.sleep(60)

        async def net():
            return {"online": True, "targets": []}

        with patch.object(main, "collect_hardware_telemetry", hang), patch.object(main, "get_live_ping_snapshot", lambda router_ping: net()), \
                patch.dict(main.JOB_TIMEOUT, {"hardware": 0.05}), patch.object(main.preferences, "load", return_value=self.prefs), \
                patch("builtins.print"):
            started = time.monotonic()
            run(main.poll_once())
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(main.latest["hardware"]["summary"]["cpu_temp"], 51)
        self.assertTrue(main.latest["hardware"]["stale"])
        self.assertTrue(main.latest["network"]["online"])
        self.assertNotIn("stale", main.latest["network"])

    def test_failing_collector_does_not_blank_the_other(self):
        main.latest["network"] = {"online": True, "targets": [{"id": "a"}]}

        async def hw():
            return {"live": True, "summary": {}}

        async def boom(router_ping):
            raise OSError("socket closed")

        with patch.object(main, "collect_hardware_telemetry", hw), patch.object(main, "get_live_ping_snapshot", boom), \
                patch.object(main.preferences, "load", return_value=self.prefs), patch("builtins.print") as printed:
            run(main.poll_once())
        self.assertTrue(main.latest["hardware"]["live"])
        self.assertEqual(main.latest["network"]["targets"], [{"id": "a"}])
        self.assertTrue(main.latest["network"]["stale"])
        self.assertIn("OSError: socket closed", printed.call_args[0][0])

    def test_repeated_error_prints_once_per_interval(self):
        with patch("builtins.print") as printed:
            self.assertTrue(main.log_error("hardware", "same", now=1000))
            for t in range(1001, 1100):
                self.assertFalse(main.log_error("hardware", "same", now=t))
            self.assertTrue(main.log_error("hardware", "different", now=1100))
            self.assertTrue(main.log_error("hardware", "different", now=1100 + main.ERROR_REPEAT_SECONDS))
        self.assertEqual(printed.call_count, 3)

    def test_health_before_the_first_snapshot_has_no_age(self):
        main.latest["ts"] = 0
        body = TestClient(main.app, base_url="http://127.0.0.1:8084").get("/api/health").json()
        self.assertFalse(body["ready"])
        self.assertIsNone(body["age_sec"])
        main.latest["ts"] = time.time() - 3
        body = TestClient(main.app, base_url="http://127.0.0.1:8084").get("/api/health").json()
        self.assertTrue(body["ready"])
        self.assertGreaterEqual(body["age_sec"], 2.9)


class PreferencesCache(unittest.TestCase):
    def test_file_is_read_again_only_after_it_changes(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "prefs.json"
            preferences._cache.clear()
            with patch.object(preferences, "_read", wraps=preferences._read) as reader:
                for _ in range(5):
                    self.assertEqual(preferences.load(_test_path=target)["storage_status"], "not_configured")
                self.assertEqual(reader.call_count, 1)
                preferences.save({"router_ping": True}, _test_path=target)
                self.assertTrue(preferences.load(_test_path=target)["router_ping"])
                calls = reader.call_count
                preferences.load(_test_path=target)
                self.assertEqual(reader.call_count, calls)

    def test_callers_cannot_change_the_cached_copy(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "prefs.json"
            preferences._cache.clear()
            first = preferences.load(_test_path=target)
            first["pages"]["overview"] = False
            self.assertTrue(preferences.load(_test_path=target)["pages"]["overview"])


class InputValidation(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        laptop.register(app)
        system_tools.register(app)
        self.c = TestClient(app)

    def test_brightness_requires_a_whole_percent(self):
        with patch.object(laptop, "set_brightness", return_value={"available": True, "percent": 40}) as setter:
            for body in ({}, {"percent": "50"}, {"percent": -1}, {"percent": 101}, {"percent": 12.5}, {"percent": True}, {"percent": None}):
                with self.subTest(body=body):
                    self.assertEqual(self.c.post("/api/laptop/brightness", json=body).status_code, 422)
            setter.assert_not_called()
            self.assertEqual(self.c.post("/api/laptop/brightness", json={"percent": 40}).status_code, 200)
            self.assertEqual(self.c.post("/api/laptop/brightness", json={"percent": 40.0}).status_code, 200)
            self.assertEqual(setter.call_count, 2)

    def test_refresh_rate_rejects_malformed_values_without_touching_windows(self):
        with patch.object(system_tools, "set_refresh") as setter:
            for body in ({}, {"device": "\\\\.\\DISPLAY1", "hz": "sixty"}, {"device": 5, "hz": 60}, {"device": "x", "hz": 0},
                         {"device": "x", "hz": [60]}, {"device": "x", "hz": 60.5}):
                with self.subTest(body=body):
                    self.assertEqual(self.c.post("/api/display", json=body).status_code, 400)
            setter.assert_not_called()

    def test_power_plan_rejects_non_text_ids(self):
        with patch.object(system_tools, "_run") as runner:
            for guid in (None, 123, ["381b4222-f694-41f0-9685-ff5bb260df2e"], "not-a-guid"):
                with self.subTest(guid=guid):
                    self.assertEqual(self.c.post("/api/power/plans", json={"guid": guid}).status_code, 400)
            runner.assert_not_called()

    def test_powershell_failures_read_as_empty(self):
        import subprocess
        for error in (FileNotFoundError("powershell"), subprocess.TimeoutExpired("powershell", 20)):
            with self.subTest(error=type(error).__name__), patch.object(laptop.subprocess, "run", side_effect=error):
                self.assertEqual(laptop._ps("Get-Date"), "")


class PreviewMode(unittest.TestCase):
    def test_off_unless_asked_and_never_without_a_throwaway_settings_folder(self):
        self.assertIsNone(preview.mode({}))
        self.assertIsNone(preview.mode({"SENTINEL_PREVIEW": "  "}))
        with self.assertRaises(RuntimeError):
            preview.mode({"SENTINEL_PREVIEW": "laptop"})
        with self.assertRaises(RuntimeError):
            preview.mode({"SENTINEL_PREVIEW": "phone", "ULTRASUITE_CONF": "x"})
        self.assertEqual(preview.mode({"SENTINEL_PREVIEW": "Laptop", "ULTRASUITE_CONF": "x"}), "laptop")

    def test_packaged_app_never_imports_the_preview(self):
        source = (Path(ROOT) / "pc_sentinel" / "backend" / "main.py").read_text(encoding="utf-8")
        self.assertNotIn("from . import preview", source)
        self.assertFalse((Path(ROOT) / "pc_sentinel" / "backend" / "preview.py").exists())
        with patch.dict(os.environ, {"SENTINEL_PREVIEW": ""}):
            self.assertEqual(main._load_preview(), (None, None))

    def test_fixture_has_no_personal_identifiers(self):
        text = (Path(ROOT) / "tests" / "fixtures" / "sentinel_preview.json").read_text(encoding="utf-8")
        self.assertIn("PREVIEW-PC", text)
        self.assertNotIn("\\Users\\", text)
        self.assertNotRegex(text, r'"serial": "(?!PREVIEW-)')

    def test_laptop_preview_has_a_battery_and_no_fans(self):
        p = preview.Preview("laptop", laptop, environ={})
        s = p.snapshot(t=100)
        self.assertEqual(s["hardware"]["device"]["type"], "laptop")
        self.assertTrue(s["hardware"]["device"]["has_battery"])
        self.assertFalse(s["hardware"]["cooling"]["has_fan_data"])
        self.assertFalse(any(x.get("type") == "Fan" for g in s["hardware"]["groups"] for x in g["sensors"]))
        states = {p.battery(t)["state"] for t in (10, 50, 100)}
        self.assertEqual(states, {"On battery", "Charging", "Plugged in, not charging"})
        on_battery = p.battery(10)
        self.assertLess(on_battery["rate_w"], 0)
        self.assertIsNotNone(on_battery["minutes_left"])
        self.assertIsNotNone(p.battery(50)["minutes_to_full"])
        self.assertEqual(p.battery(50)["health_pct"], 90.0)

    def test_held_battery_state(self):
        p = preview.Preview("laptop", laptop, environ={"SENTINEL_PREVIEW_BATTERY": "charging"})
        self.assertTrue(all(p.battery(t)["charging"] for t in range(0, 300, 20)))

    def test_desktop_preview_moves_but_stays_in_range(self):
        p = preview.Preview("desktop", laptop, environ={})
        self.assertFalse(p.battery()["has_battery"])
        loads = []
        for t in range(0, 120, 7):
            s = p.snapshot(t)["hardware"]["summary"]
            loads.append(s["cpu_load"])
            self.assertTrue(0 <= s["cpu_load"] <= 100 and 0 <= s["ram_load"] <= 100)
        self.assertGreater(max(loads) - min(loads), 10)

    def test_routes_answer_first_and_change_nothing_on_this_pc(self):
        app = FastAPI()
        p = preview.Preview("laptop", laptop, environ={})
        p.register(app)
        system_tools.register(app)
        laptop.register(app)
        c = TestClient(app)
        with patch.object(system_tools, "_run") as runner, patch.object(laptop, "set_brightness") as bright, \
                patch.object(system_tools, "setup_lhm") as setup:
            guid = next(x["guid"] for x in p.plans["plans"] if not x["active"])
            plans = c.post("/api/power/plans", json={"guid": guid}).json()["plans"]
            self.assertTrue(next(x for x in plans if x["guid"] == guid)["active"])
            self.assertEqual(c.post("/api/power/mode", json={"mode": "efficiency"}).json()["mode"], "efficiency")
            self.assertEqual(c.post("/api/laptop/brightness", json={"percent": 30}).json()["percent"], 30)
            self.assertEqual(c.post("/api/laptop/brightness", json={"percent": 300}).status_code, 422)
            for path in ("/api/setup/lhm", "/api/power/max", "/api/power/max/revert", "/api/power/benchmark", "/api/storage/writers/measure"):
                with self.subTest(path=path):
                    r = c.post(path)
                    self.assertEqual(r.status_code, 409)
                    self.assertIn("Preview mode", r.json()["detail"])
            self.assertTrue(c.get("/api/laptop/battery").json()["has_battery"])
            self.assertEqual(c.get("/api/processes/abc/icon").status_code, 404)
            runner.assert_not_called()
            bright.assert_not_called()
            setup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
