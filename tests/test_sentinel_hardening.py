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

    def test_history_keeps_two_minutes_of_slim_samples(self):
        main.history.clear()
        snap = {"ts": 5, "hardware": {"summary": {"cpu_load": 40, "gpu_load": 3, "ram_load": 55, "cpu_temp": 60, "gpu_temp": 44,
                                                 "cpu_name": "x"}, "groups": [{"sensors": list(range(300))}],
                                     "power": {"measured": {"CPU package": 50}, "measured_subtotal_w": 50, "estimated": {"a": 1},
                                               "coverage": {"component_readings_complete": False, "scope": "x"}}}}
        sample = main.history_sample(snap)
        self.assertEqual(sample["summary"], {"cpu_load": 40, "gpu_load": 3, "ram_load": 55, "cpu_temp": 60, "gpu_temp": 44})
        self.assertEqual(sample["power"], {"measured": {"CPU package": 50}, "measured_subtotal_w": 50,
                                           "coverage": {"component_readings_complete": False}})
        self.assertNotIn("groups", str(sample))
        for i in range(main.HISTORY_SAMPLES + 30):
            main.history.append({**sample, "ts": i})
        body = TestClient(main.app, base_url="http://127.0.0.1:8084").get("/api/history").json()
        self.assertEqual(len(body["samples"]), main.HISTORY_SAMPLES)
        self.assertEqual(body["samples"][0]["ts"], 30)
        self.assertTrue(main.history_sample({"hardware": {"stale": True}})["stale"])
        main.history.clear()

    def test_health_before_the_first_snapshot_has_no_age(self):
        main.latest["ts"] = 0
        body = TestClient(main.app, base_url="http://127.0.0.1:8084").get("/api/health").json()
        self.assertFalse(body["ready"])
        self.assertIsNone(body["age_sec"])
        main.latest["ts"] = time.time() - 3
        body = TestClient(main.app, base_url="http://127.0.0.1:8084").get("/api/health").json()
        self.assertTrue(body["ready"])
        self.assertGreaterEqual(body["age_sec"], 2.9)


class Jitter(unittest.TestCase):
    def test_latency_page_jitter_matches_the_speed_test_measure(self):
        from backend import net_monitor
        self.assertEqual(net_monitor.jitter_ms([10, 12, 10, 12]), 2.0)
        # a slow drift is not jitter: a spread would call this 3.4 ms, the step size is 1 ms
        self.assertEqual(net_monitor.jitter_ms([10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21]), 1.0)
        self.assertEqual(net_monitor.jitter_ms([10, None, 30, 31, 33]), 1.5, "a lost reply is not a 20 ms step")
        self.assertIsNone(net_monitor.jitter_ms([10, None, 12]))
        self.assertIsNone(net_monitor.jitter_ms([]))
        self.assertEqual(net_monitor.jitter_ms([99, 99] + [10, 12] * 10), 2.0, "only the last 20 replies count")


class SpeedTestSocket(unittest.TestCase):
    def test_closing_the_page_stops_the_test(self):
        state = {"cancelled": False, "finished": False}

        async def fake_run(send):
            await send({"type": "phase", "phase": "download"})
            try:
                await asyncio.sleep(30)
                state["finished"] = True
            except asyncio.CancelledError:
                state["cancelled"] = True
                raise

        client = TestClient(main.app, base_url="http://127.0.0.1:8084")
        with patch.object(main.speedtest, "run", fake_run):
            started = time.monotonic()
            with client.websocket_connect("ws://127.0.0.1:8084/ws/speedtest", headers={"Origin": "http://127.0.0.1:8084"}) as ws:
                self.assertEqual(ws.receive_json()["phase"], "download")
            deadline = time.monotonic() + 5
            while not state["cancelled"] and time.monotonic() < deadline:
                time.sleep(0.02)
        self.assertTrue(state["cancelled"])
        self.assertFalse(state["finished"])
        self.assertLess(time.monotonic() - started, 6)

    def test_a_finished_test_still_closes_the_socket(self):
        async def quick(send):
            await send({"type": "done"})
            return {}

        with patch.object(main.speedtest, "run", quick):
            with TestClient(main.app, base_url="http://127.0.0.1:8084").websocket_connect("ws://127.0.0.1:8084/ws/speedtest", headers={"Origin": "http://127.0.0.1:8084"}) as ws:
                self.assertEqual(ws.receive_json()["type"], "done")
                from starlette.websockets import WebSocketDisconnect
                with self.assertRaises(WebSocketDisconnect):
                    ws.receive_json()


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

    def test_unknown_process_ids_are_refused_without_a_scan(self):
        ok = ("exe:" + "a" * 64, "pid:1234:1795049146.988", "pid:9:unverified", "pid:42:1795049146")
        for value in ok:
            with self.subTest(value=value):
                self.assertTrue(system_tools.valid_group_id(value))
        with patch.object(system_tools, "top_apps") as scan:
            for bad in ("exe:" + "a" * 63, "exe:" + "A" * 64, "x" * 5000, "pid:1:abc", "../etc", "pid:1:2:3"):
                with self.subTest(bad=bad[:20]):
                    self.assertEqual(self.c.get(f"/api/processes/{bad}").status_code, 404)
                    self.assertEqual(self.c.get(f"/api/processes/{bad}/icon").status_code, 404)
            scan.assert_not_called()

    def test_power_plan_rejects_non_text_ids(self):
        with patch.object(system_tools, "_run") as runner:
            for guid in (None, 123, ["381b4222-f694-41f0-9685-ff5bb260df2e"], "not-a-guid"):
                with self.subTest(guid=guid):
                    self.assertEqual(self.c.post("/api/power/plans", json={"guid": guid}).status_code, 400)
            runner.assert_not_called()

    def test_concurrent_battery_reads_share_one_run(self):
        import threading
        calls, gate = [], threading.Event()

        def slow():
            calls.append(1)
            gate.wait(2)
            return {"has_battery": False}

        laptop._cache.pop("t-share", None)
        results = []
        workers = [threading.Thread(target=lambda: results.append(laptop._cached("t-share", 30, slow))) for _ in range(6)]
        for w in workers:
            w.start()
        time.sleep(0.2)
        gate.set()
        for w in workers:
            w.join(5)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(results), 6)
        laptop._cache.pop("t-share", None)

    def test_powershell_failures_read_as_empty(self):
        import subprocess
        for error in (FileNotFoundError("powershell"), subprocess.TimeoutExpired("powershell", 20)):
            with self.subTest(error=type(error).__name__), patch.object(laptop.subprocess, "run", side_effect=error):
                self.assertEqual(laptop._ps("Get-Date"), "")


class WriterMeasurement(unittest.TestCase):
    def test_second_request_keeps_the_running_measurement(self):
        with tempfile.TemporaryDirectory() as td:
            sampler = storage_writers.Sampler(store_path=Path(td) / "w.json")
            sampler.measure(now=1000)
            sampler.live["game.exe"] = 5_000_000
            sampler.measure(now=1030)
            self.assertEqual(sampler.live_started, 1000)
            self.assertEqual(sampler.live, {"game.exe": 5_000_000})
            sampler.measure(now=1000 + storage_writers.MEASURE_S + 1)
            self.assertEqual(sampler.live, {}, "a new measurement starts once the last one finished")


class CatalogRefresh(unittest.TestCase):
    def test_only_one_refresh_runs_at_a_time(self):
        from backend import psu_refresh
        import threading
        started, release = threading.Event(), threading.Event()

        def slow_fetch():
            started.set()
            release.wait(5)
            raise OSError("offline")

        with tempfile.TemporaryDirectory() as td:
            args = dict(cache=Path(td) / "c.json", state_file=Path(td) / "s.json")
            worker = threading.Thread(target=lambda: psu_refresh.refresh(fetch=slow_fetch, **args))
            worker.start()
            self.assertTrue(started.wait(5))
            with self.assertRaises(psu_refresh.Busy):
                psu_refresh.refresh(fetch=lambda: "", **args)
            app = FastAPI()
            psu_refresh.register(app)
            with patch.object(preferences, "load", return_value={"psu_refresh": True}):
                r = TestClient(app).post("/api/power/catalog/refresh")
            self.assertEqual(r.status_code, 409)
            self.assertIn("already running", r.json()["detail"])
            release.set()
            worker.join(5)
            state = psu_refresh.refresh(fetch=lambda: (_ for _ in ()).throw(OSError("still offline")), **args)
            self.assertIn("still offline", state["last_error"])


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
