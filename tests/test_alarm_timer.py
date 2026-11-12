"""Persistent countdown/API tests with disposable data and no real speaker."""
import asyncio
import contextlib
import datetime
import importlib
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


class AlarmTimer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory(dir=ROOT / "private")
        cls.env = patch.dict(os.environ, {"ULTRASUITE_DATA": cls.fixture.name,
                                         "HOST_AUDIO_BRIDGE_URL": "http://127.0.0.1:9", "BRIDGE_TOKEN": ""})
        cls.env.start()
        backend = ROOT / "code_red_alarm" / "backend"
        spec = importlib.util.spec_from_file_location("timer_test_pkg", backend / "__init__.py", submodule_search_locations=[str(backend)])
        package = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = package
        spec.loader.exec_module(package)
        cls.m = importlib.import_module("timer_test_pkg.main")

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        cls.fixture.cleanup()

    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=ROOT / "private")
        self.addCleanup(temp.cleanup)
        self.now = 1794484800.0
        patches = [patch.object(self.m, "DB_PATH", str(Path(temp.name) / "alarms.db")),
                   patch.object(self.m.time, "time", side_effect=lambda: self.now),
                   patch.object(self.m, "local_now", return_value=datetime.datetime(2026, 11, 12, 7, 5)),
                   patch.object(self.m, "ring_state", self.m.ActiveRingingState()),
                   patch.object(self.m, "_ring_helper", new=AsyncMock(side_effect=AssertionError("No audio in timer tests")))]
        for value in patches:
            value.start()
            self.addCleanup(value.stop)
        self.m.init_db()
        self.client = TestClient(self.m.app, base_url="http://127.0.0.1:8081")
        self.addCleanup(self.client.close)

    def start(self, seconds=60):
        response = self.client.post("/api/timer/start", json={"duration_sec": seconds})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def action(self, action, run_id):
        return self.client.post("/api/timer/" + action, json={"run_id": run_id})

    def test_fresh_timer_is_idle_and_alarms_are_empty(self):
        timer = self.client.get("/api/timer").json()
        self.assertEqual((timer["state"], timer["duration_sec"]), ("idle", 300))
        self.assertIsNone(self.m.claim_due_timer())
        self.assertEqual(self.client.get("/api/alarms").json()["alarms"], [])

    def test_timer_metadata_explicitly_names_edition_and_delivery_capability(self):
        for edition, enabled in (("Public", "1"), ("Dev", "0")):
            with self.subTest(edition=edition), patch.dict(os.environ, {"ULTRASUITE_EDITION": edition, "ULTRASUITE_ALARM_DELIVERY": enabled}):
                timer = self.client.get("/api/timer").json()
                self.assertEqual(timer["edition"], edition)
                self.assertEqual(timer["delivery_enabled"], enabled == "1")

    def test_running_timer_survives_initialization_and_elapsed_time(self):
        started = self.start()
        self.now += 17.25
        self.m.init_db()
        timer = self.client.get("/api/timer").json()
        self.assertEqual(timer["run_id"], started["run_id"])
        self.assertEqual(timer["state"], "running")
        self.assertEqual(timer["remaining_sec"], 42.75)
        self.assertIsNone(self.m.claim_due_timer())

    def test_pause_resume_preserve_fractional_remaining_time(self):
        started = self.start()
        self.now += 10.25
        paused = self.action("pause", started["run_id"]).json()
        self.assertEqual(paused["remaining_sec"], 49.75)
        self.now += 3600
        self.m.init_db()
        self.assertEqual(self.m.timer_snapshot()["remaining_sec"], 49.75)
        resumed = self.action("resume", started["run_id"]).json()
        self.assertEqual(resumed["deadline"], self.now + 49.75)
        self.now += 49.75
        self.assertEqual(self.m.claim_due_timer()["id"], started["run_id"])

    def test_completion_is_claimed_once_at_exact_deadline(self):
        started = self.start(1)
        self.now += .999
        self.assertIsNone(self.m.claim_due_timer())
        self.now = started["deadline"]
        alarm = self.m.claim_due_timer()
        self.assertEqual(alarm["id"], started["run_id"])
        self.assertTrue(alarm["is_timer"])
        self.assertFalse(alarm["code_red"])
        self.assertEqual(alarm["math_mission_difficulty"], 0)
        self.assertEqual(self.m.timer_snapshot()["state"], "finished")
        self.m.init_db()
        self.assertIsNone(self.m.claim_due_timer())

    def test_catch_up_boundary_and_stale_completion(self):
        for late, expected in ((1800, "finished"), (1800.000001, "expired")):
            with self.subTest(late=late):
                before = self.m.timer_snapshot()
                self.action("reset", before["run_id"])
                started = self.start(5)
                self.now = started["deadline"] + late
                alarm = self.m.claim_due_timer()
                self.assertEqual(bool(alarm), expected == "finished")
                self.assertEqual(self.m.timer_snapshot()["state"], expected)
                self.assertIsNone(self.m.claim_due_timer())

    def test_stale_window_cannot_reset_a_new_run(self):
        first = self.start()
        self.assertEqual(self.action("reset", first["run_id"]).status_code, 200)
        second = self.start(300)
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertEqual(self.action("reset", first["run_id"]).status_code, 409)
        self.assertEqual(self.m.timer_snapshot()["run_id"], second["run_id"])

    def test_active_timer_cannot_be_silently_replaced(self):
        first = self.start()
        self.assertEqual(self.client.post("/api/timer/start", json={"duration_sec": 10}).status_code, 409)
        self.action("pause", first["run_id"])
        self.assertEqual(self.client.post("/api/timer/start", json={"duration_sec": 10}).status_code, 409)
        self.assertEqual(self.m.timer_snapshot()["duration_sec"], 60)

    def test_invalid_durations_are_rejected_without_arming(self):
        for value in (0, -1, 86401, 1.5, True, "60"):
            with self.subTest(value=value):
                self.assertEqual(self.client.post("/api/timer/start", json={"duration_sec": value}).status_code, 422)
        self.assertEqual(self.m.timer_snapshot()["state"], "idle")
        self.assertEqual(self.start(86400)["duration_sec"], 86400)

    def test_invalid_transition_does_not_lose_the_completion(self):
        started = self.start(1)
        self.assertEqual(self.action("resume", started["run_id"]).status_code, 409)
        self.now = started["deadline"]
        self.assertEqual(self.action("pause", started["run_id"]).status_code, 409)
        self.assertIsNotNone(self.m.claim_due_timer())

    def test_reset_disarms_running_and_paused_timers(self):
        for pause in (False, True):
            started = self.start(10)
            if pause:
                self.action("pause", started["run_id"])
            reset = self.action("reset", started["run_id"]).json()
            self.assertEqual((reset["state"], reset["remaining_sec"]), ("idle", 10))
            self.now += 100
            self.assertIsNone(self.m.claim_due_timer())
            self.assertIsNone(self.client.get("/api/next").json()["in_sec"])

    def test_timer_participates_in_the_existing_wake_deadline(self):
        self.start(120)
        self.now += 17
        self.assertEqual(self.client.get("/api/next").json()["in_sec"], 103)
        with patch.object(self.m, "fetch_all_alarms", return_value=[{}]), patch.object(self.m, "next_ring", return_value=self.m.local_now() + datetime.timedelta(seconds=30)):
            self.assertEqual(self.client.get("/api/next").json()["in_sec"], 30)

    def test_other_websites_cannot_start_or_reset_timers(self):
        headers = {"Origin": "https://evil.example"}
        self.assertEqual(self.client.post("/api/timer/start", json={"duration_sec": 1}, headers=headers).status_code, 403)
        self.assertEqual(self.m.timer_snapshot()["state"], "idle")

    def test_ringing_timer_must_be_stopped_before_starting_another(self):
        self.m.ring_state.is_ringing = True
        self.m.ring_state.alarm = {"id": "timer-old", "is_timer": True}
        self.assertEqual(self.client.post("/api/timer/start", json={"duration_sec": 1}).status_code, 409)

    def test_dev_environment_and_core_gate_block_new_delivery_paths(self):
        for setting in ("environment", "core"):
            with self.subTest(setting=setting):
                started = self.start(1)
                self.action("pause", started["run_id"])
                gate = patch.dict(os.environ, {"ULTRASUITE_ALARM_DELIVERY": "0"}) if setting == "environment" else patch.object(self.m, "ULTRASUITE_DELIVERY_GATED", True, create=True)
                with gate:
                    self.assertFalse(self.m.timer_snapshot()["delivery_enabled"])
                    self.assertEqual(self.client.post("/api/timer/start", json={"duration_sec": 1}).status_code, 403)
                    self.assertEqual(self.action("resume", started["run_id"]).status_code, 403)
                    self.assertEqual(self.client.post("/api/audio/test").status_code, 403)
                    self.assertEqual(self.client.post("/api/alarms/fixture/test-ring").status_code, 403)
                    self.assertIsNone(self.m.claim_due_timer())
                    # No event loop/audio startup is needed to verify the guard.
                    with self.assertRaises(StopIteration):
                        self.m.trigger_alarm_now({"id": "fixture", "is_timer": True}).send(None)
                    self.assertFalse(self.m.ring_state.is_ringing)
                self.action("reset", started["run_id"])

    def test_scheduler_gives_alarm_priority_then_claims_timer_before_trigger(self):
        timer = self.start(1)
        self.now = timer["deadline"]
        alarm = self.client.post("/api/alarms", json={"label": "Fixture alarm", "time_hhmm": "07:00", "target_date": "2026-11-12"}).json()
        triggered = []

        async def trigger(item):
            if item.get("is_timer"):
                self.assertEqual(self.m.timer_snapshot()["state"], "finished")
            triggered.append(item["id"])
            self.m.ring_state.is_ringing = True

        async def end_tick(_):
            raise asyncio.CancelledError

        with patch.object(self.m, "trigger_alarm_now", new=trigger), patch.object(self.m, "broadcast_state", new=AsyncMock()), patch.object(self.m.asyncio, "sleep", new=end_tick):
            with self.assertRaises(asyncio.CancelledError):
                self.m.scheduler_loop().send(None)
            self.assertEqual(triggered, [alarm["id"]])
            self.assertEqual(self.m.timer_snapshot()["state"], "running")
            self.m.ring_state.is_ringing = False
            with self.assertRaises(asyncio.CancelledError):
                self.m.scheduler_loop().send(None)
        self.assertEqual(triggered, [alarm["id"], timer["run_id"]])
        self.assertIsNone(self.m.claim_due_timer())


if __name__ == "__main__":
    unittest.main()
