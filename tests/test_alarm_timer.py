"""Multi-timer API tests with disposable data and no real speaker."""
import asyncio
import contextlib
import datetime
import importlib
import importlib.util
import os
from pathlib import Path
import sqlite3
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
        self.db = str(Path(temp.name) / "alarms.db")
        self.now = 1794484800.0
        self.bridge = AsyncMock(side_effect=AssertionError("No audio in timer tests"))
        patches = [patch.object(self.m, "DB_PATH", self.db),
                   patch.object(self.m.time, "time", side_effect=lambda: self.now),
                   patch.object(self.m, "local_now", return_value=datetime.datetime(2026, 11, 12, 7, 5)),
                   patch.object(self.m, "ring_state", self.m.ActiveRingingState()),
                   patch.object(self.m, "_ring_helper", new=self.bridge),
                   patch.object(self.m.httpx, "AsyncClient", side_effect=AssertionError("No speaker helper calls"))]
        for value in patches:
            value.start()
            self.addCleanup(value.stop)
        self.m.init_db()
        self.client = TestClient(self.m.app, base_url="http://127.0.0.1:8081")
        self.addCleanup(self.client.close)

    def create(self, seconds=60, **extra):
        response = self.client.post("/api/timers", json={"duration_sec": seconds, **extra})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        return next(t for t in body["timers"] if t["id"] == body["id"])

    def action(self, timer_id, action, **body):
        return self.client.post(f"/api/timers/{timer_id}/{action}", json=body or None)

    def timer(self, timer_id):
        return next(t for t in self.m.timer_snapshot()["timers"] if t["id"] == timer_id)

    def test_fresh_install_has_no_timers_and_names_delivery(self):
        snap = self.client.get("/api/timers").json()
        self.assertEqual(snap["timers"], [])
        self.assertEqual((snap["edition"], snap["delivery_enabled"]), ("Public", True))
        self.assertIsNone(self.m.claim_due_timer())
        self.assertIsNone(self.client.get("/api/next").json()["in_sec"])

    def test_metadata_names_edition_and_delivery_capability(self):
        for edition, enabled in (("Public", "1"), ("Dev", "0")):
            with self.subTest(edition=edition), patch.dict(os.environ, {"ULTRASUITE_EDITION": edition, "ULTRASUITE_ALARM_DELIVERY": enabled}):
                snap = self.client.get("/api/timers").json()
                self.assertEqual(snap["edition"], edition)
                self.assertEqual(snap["delivery_enabled"], enabled == "1")

    def test_create_runs_immediately_and_several_timers_run_together(self):
        tea = self.create(180, label="Tea")
        pasta = self.create(600, label="  Pasta  ")
        idle = self.create(30, start=False)
        self.now += 60
        snap = self.client.get("/api/timers").json()
        self.assertEqual([t["label"] for t in snap["timers"]], ["Tea", "Pasta", "Timer"])
        self.assertEqual([t["state"] for t in snap["timers"]], ["running", "running", "idle"])
        self.assertEqual(self.timer(tea["id"])["remaining_sec"], 120)
        self.assertEqual(self.timer(pasta["id"])["remaining_sec"], 540)
        self.assertEqual(self.timer(idle["id"])["remaining_sec"], 30)
        ring = self.client.get("/api/ring").json()
        self.assertEqual(ring["timers"], {"count": 3, "running": 2, "paused": 0, "next_in_sec": 120, "next_label": "Tea"})
        self.assertEqual(self.client.get("/api/next").json()["in_sec"], 120)

    def test_running_timer_survives_reload_and_restart(self):
        started = self.create()
        self.now += 17.25
        self.m.init_db()  # what a restarted engine does
        self.assertEqual(self.timer(started["id"])["state"], "running")
        self.assertEqual(self.timer(started["id"])["remaining_sec"], 42.75)
        self.assertIsNone(self.m.claim_due_timer())

    def test_pause_resume_keep_fractional_time_across_restart(self):
        started = self.create()
        self.now += 10.25
        paused = self.action(started["id"], "pause")
        self.assertEqual(paused.status_code, 200)
        self.assertEqual(self.timer(started["id"])["remaining_sec"], 49.75)
        self.now += 3600
        self.m.init_db()
        self.assertEqual(self.timer(started["id"])["remaining_sec"], 49.75)
        self.assertEqual(self.action(started["id"], "resume").status_code, 200)
        self.assertEqual(self.timer(started["id"])["deadline"], self.now + 49.75)
        self.now += 49.75
        self.assertEqual(self.m.claim_due_timer()["timer_id"], started["id"])

    def test_add_minute_extends_running_paused_and_finished_timers(self):
        started = self.create(120)
        self.now += 100
        self.action(started["id"], "add")
        self.assertEqual(self.timer(started["id"])["remaining_sec"], 80)
        self.assertEqual(self.timer(started["id"])["duration_sec"], 180)
        self.action(started["id"], "pause")
        self.action(started["id"], "add", seconds=30)
        self.assertEqual(self.timer(started["id"])["remaining_sec"], 110)
        self.action(started["id"], "resume")
        self.now += 110
        self.assertIsNotNone(self.m.claim_due_timer())
        self.assertEqual(self.action(started["id"], "add").status_code, 200)
        after = self.timer(started["id"])
        self.assertEqual((after["state"], after["remaining_sec"], after["duration_sec"]), ("running", 60, 60))

    def test_finish_is_claimed_once_at_the_exact_deadline(self):
        started = self.create(1)
        self.now += .999
        self.assertIsNone(self.m.claim_due_timer())
        self.now = started["deadline"]
        alarm = self.m.claim_due_timer()
        self.assertEqual((alarm["timer_id"], alarm["label"], alarm["is_timer"]), (started["id"], "Timer finished", True))
        self.assertFalse(alarm["code_red"])
        self.assertEqual(alarm["math_mission_difficulty"], 0)
        self.assertEqual(alarm["ring_duration_sec"], 60)
        self.assertEqual(self.timer(started["id"])["state"], "finished")
        self.m.init_db()
        self.assertIsNone(self.m.claim_due_timer())

    def test_two_timers_due_together_ring_one_after_the_other(self):
        first = self.create(5, label="First")
        second = self.create(5, label="Second")
        self.now += 5
        self.assertEqual(self.m.claim_due_timer()["timer_id"], first["id"])
        self.assertEqual(self.timer(second["id"])["state"], "running")
        self.assertEqual(self.m.claim_due_timer()["timer_id"], second["id"])
        self.assertIsNone(self.m.claim_due_timer())

    def test_catch_up_boundary_and_stale_completion(self):
        for late, expected in ((1800, "finished"), (1800.000001, "expired")):
            with self.subTest(late=late):
                started = self.create(5)
                self.now = started["deadline"] + late
                alarm = self.m.claim_due_timer()
                self.assertEqual(bool(alarm), expected == "finished")
                self.assertEqual(self.timer(started["id"])["state"], expected)
                self.assertIsNone(self.m.claim_due_timer())

    def test_reset_and_delete_disarm(self):
        for pause in (False, True):
            started = self.create(10)
            if pause:
                self.action(started["id"], "pause")
            self.assertEqual(self.action(started["id"], "reset").status_code, 200)
            reset = self.timer(started["id"])
            self.assertEqual((reset["state"], reset["remaining_sec"]), ("idle", 10))
        other = self.create(10)
        self.assertEqual(self.client.delete(f"/api/timers/{other['id']}").status_code, 200)
        self.assertEqual(self.client.delete(f"/api/timers/{other['id']}").status_code, 404)
        self.now += 100
        self.assertIsNone(self.m.claim_due_timer())
        self.assertIsNone(self.client.get("/api/next").json()["in_sec"])

    def test_invalid_input_is_rejected_without_arming(self):
        for value in (0, -1, 86401, 1.5, True, "60", None):
            with self.subTest(duration=value):
                self.assertEqual(self.client.post("/api/timers", json={"duration_sec": value}).status_code, 422)
        for body in ({"duration_sec": 60, "label": "x" * 81}, {"duration_sec": 60, "ring_duration_sec": 5},
                     {"duration_sec": 60, "ring_duration_sec": 601}, {"duration_sec": 60, "custom_phrase": "y" * 121}):
            with self.subTest(body=list(body)):
                self.assertEqual(self.client.post("/api/timers", json=body).status_code, 422)
        self.assertEqual(self.client.post("/api/timers", json={"duration_sec": 60, "code_red": True, "custom_phrase": " ab "}).status_code, 400)
        self.assertEqual(self.client.get("/api/timers").json()["timers"], [])
        started = self.create(60)
        for seconds in (0, 3601, 2.5, "60"):
            with self.subTest(add=seconds):
                self.assertEqual(self.action(started["id"], "add", seconds=seconds).status_code, 422)
        self.assertEqual(self.action(started["id"], "explode").status_code, 404)
        self.assertEqual(self.action("tmr-missing", "pause").status_code, 404)
        self.assertEqual(self.action(started["id"], "resume").status_code, 409)
        self.assertEqual(self.create(86400)["duration_sec"], 86400)

    def test_add_cannot_pass_24_hours(self):
        started = self.create(86400 - 30)
        self.assertEqual(self.action(started["id"], "add").status_code, 400)
        self.assertEqual(self.action(started["id"], "add", seconds=30).status_code, 200)

    def test_timer_count_is_capped(self):
        for _ in range(self.m.MAX_TIMERS):
            self.create(60, start=False)
        self.assertEqual(self.client.post("/api/timers", json={"duration_sec": 60}).status_code, 409)

    def test_invalid_transition_does_not_lose_the_completion(self):
        started = self.create(1)
        self.now = started["deadline"]
        self.assertEqual(self.action(started["id"], "pause").status_code, 409)
        self.assertEqual(self.action(started["id"], "add").status_code, 409)
        self.assertIsNotNone(self.m.claim_due_timer())

    def test_code_red_timer_is_opt_in_and_keeps_its_phrase_private_until_it_rings(self):
        plain = self.create(5)
        self.assertFalse(plain["code_red"])
        locked = self.create(5, label="Oven", code_red=True, custom_phrase=" I am at the oven ")
        self.assertTrue(locked["code_red"])
        self.assertNotIn("I am at the oven", self.client.get("/api/timers").text)
        self.now += 5
        self.m.claim_due_timer()
        alarm = self.m.claim_due_timer()
        self.assertEqual((alarm["label"], alarm["code_red"], alarm["custom_phrase"]), ("Oven finished", True, "I am at the oven"))

    def test_finished_timer_rings_through_the_alarm_path_and_dismiss_checks_the_phrase(self):
        locked = self.create(5, code_red=True, custom_phrase="Stand up now")
        self.now += 5
        alarm = self.m.claim_due_timer()
        self.m.ring_state.is_ringing, self.m.ring_state.alarm = True, alarm
        self.assertEqual(self.client.post("/api/alarms/dismiss", json={"alarm_id": locked["id"], "typed_phrase": "nope"}).status_code, 403)
        # Restarting or deleting a ringing Code Red timer does not silence it.
        self.assertEqual(self.client.delete(f"/api/timers/{locked['id']}").status_code, 200)
        self.assertTrue(self.m.ring_state.is_ringing)

    def test_adding_a_minute_quiets_a_ringing_plain_timer(self):
        started = self.create(5)
        self.now += 5
        self.m.ring_state.is_ringing, self.m.ring_state.alarm = True, self.m.claim_due_timer()
        stopped = []

        class Bridge:
            def __init__(self, **_):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def post(self, url, json):
                stopped.append(url)

        with patch.object(self.m.httpx, "AsyncClient", Bridge):
            self.assertEqual(self.action(started["id"], "add").status_code, 200)
        self.assertFalse(self.m.ring_state.is_ringing)
        self.assertEqual(len(stopped), 1)
        self.assertTrue(stopped[0].endswith("/stop"))
        self.assertEqual(self.timer(started["id"])["state"], "running")

    def test_legacy_single_countdown_is_carried_over_once(self):
        with contextlib.closing(sqlite3.connect(self.db)) as conn:
            conn.execute("UPDATE countdown SET run_id = 'timer-old', label = 'Legacy', state = 'running', duration_sec = 300, remaining_sec = 300, deadline = ? WHERE id = 1", (self.now + 120,))
            conn.commit()
        self.m.init_db()
        self.m.init_db()
        timers = self.m.timer_snapshot()["timers"]
        self.assertEqual([(t["id"], t["label"], t["state"], t["remaining_sec"]) for t in timers], [("timer-old", "Legacy", "running", 120)])

    def test_snapshot_revision_matches_rows_during_a_concurrent_update(self):
        started = self.create(60, label="Before")
        before = self.m.timer_snapshot()["revision"]
        with contextlib.closing(sqlite3.connect(self.db)) as conn:
            conn.execute("PRAGMA journal_mode = WAL")
        db = self.db

        class ConcurrentRead(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                if sql.startswith("SELECT revision"):
                    with contextlib.closing(sqlite3.connect(db)) as writer:
                        with writer:
                            writer.execute("UPDATE timers SET label = 'After' WHERE id = ?", (started["id"],))
                            writer.execute("UPDATE timer_meta SET revision = revision + 1 WHERE id = 1")
                return super().execute(sql, parameters)

        def connect():
            conn = sqlite3.connect(db, factory=ConcurrentRead)
            conn.row_factory = sqlite3.Row
            return conn

        with patch.object(self.m, "_timers_db", new=connect):
            snap = self.m.timer_snapshot()
        self.assertEqual((snap["timers"][0]["label"], snap["revision"]), ("Before", before))
        after = self.m.timer_snapshot()
        self.assertEqual((after["timers"][0]["label"], after["revision"]), ("After", before + 1))

    def test_revision_increases_on_every_change(self):
        revisions = [self.m.timer_snapshot()["revision"]]
        started = self.create(60)
        revisions.append(self.m.timer_snapshot()["revision"])
        self.action(started["id"], "pause")
        revisions.append(self.m.timer_snapshot()["revision"])
        self.client.delete(f"/api/timers/{started['id']}")
        revisions.append(self.m.timer_snapshot()["revision"])
        self.assertEqual(revisions, sorted(set(revisions)))

    def test_other_websites_cannot_start_timers(self):
        headers = {"Origin": "https://evil.example"}
        self.assertEqual(self.client.post("/api/timers", json={"duration_sec": 1}, headers=headers).status_code, 403)
        self.assertEqual(self.m.timer_snapshot()["timers"], [])

    def test_dev_environment_and_core_gate_block_every_ringing_path(self):
        for setting in ("environment", "core"):
            with self.subTest(setting=setting):
                paused = self.create(1)
                self.action(paused["id"], "pause")
                finished = self.create(1)
                self.now += 1
                self.m.claim_due_timer()
                gate = patch.dict(os.environ, {"ULTRASUITE_ALARM_DELIVERY": "0"}) if setting == "environment" else patch.object(self.m, "ULTRASUITE_DELIVERY_GATED", True, create=True)
                with gate:
                    self.assertFalse(self.m.timer_snapshot()["delivery_enabled"])
                    self.assertEqual(self.client.post("/api/timers", json={"duration_sec": 1}).status_code, 403)
                    self.assertEqual(self.client.post("/api/timers", json={"duration_sec": 1, "start": False}).status_code, 200)
                    self.assertEqual(self.action(paused["id"], "resume").status_code, 403)
                    self.assertEqual(self.action(finished["id"], "start").status_code, 403)
                    self.assertEqual(self.action(finished["id"], "add").status_code, 403)
                    self.assertEqual(self.client.post("/api/audio/test").status_code, 403)
                    self.assertIsNone(self.m.claim_due_timer())
                    with self.assertRaises(StopIteration):
                        self.m.trigger_alarm_now({"id": "fixture", "is_timer": True}).send(None)
                    self.assertFalse(self.m.ring_state.is_ringing)
                    self.assertEqual(self.action(paused["id"], "reset").status_code, 200)

    def test_scheduler_gives_alarms_priority_then_claims_timer_before_trigger(self):
        timer = self.create(1)
        self.now = timer["deadline"]
        alarm = self.client.post("/api/alarms", json={"label": "Fixture alarm", "time_hhmm": "07:00", "target_date": "2026-11-12"}).json()
        triggered = []

        async def trigger(item):
            if item.get("is_timer"):
                self.assertEqual(self.timer(timer["id"])["state"], "finished")
            triggered.append(item["id"])
            self.m.ring_state.is_ringing = True

        async def end_tick(_):
            raise asyncio.CancelledError

        with patch.object(self.m, "trigger_alarm_now", new=trigger), patch.object(self.m, "broadcast_state", new=AsyncMock()), patch.object(self.m.asyncio, "sleep", new=end_tick):
            with self.assertRaises(asyncio.CancelledError):
                self.m.scheduler_loop().send(None)
            self.assertEqual(triggered, [alarm["id"]])
            self.assertEqual(self.timer(timer["id"])["state"], "running")
            self.m.ring_state.is_ringing = False
            with self.assertRaises(asyncio.CancelledError):
                self.m.scheduler_loop().send(None)
        self.assertEqual(triggered, [alarm["id"], timer["id"]])
        self.assertIsNone(self.m.claim_due_timer())


if __name__ == "__main__":
    unittest.main()
