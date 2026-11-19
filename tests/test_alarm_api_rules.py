"""Alarm API rules: validation, snooze, toggling and old databases. Disposable data, no real speaker."""
import ast
import contextlib
import datetime
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime.datetime(2026, 11, 12, 7, 5)  # a Thursday


class AlarmApiRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / "private").mkdir(exist_ok=True)
        cls.fixture = tempfile.TemporaryDirectory(dir=ROOT / "private")
        cls.env = patch.dict(os.environ, {"ULTRASUITE_DATA": cls.fixture.name,
                                         "HOST_AUDIO_BRIDGE_URL": "http://127.0.0.1:9", "BRIDGE_TOKEN": ""})
        cls.env.start()
        backend = ROOT / "code_red_alarm" / "backend"
        spec = importlib.util.spec_from_file_location("rules_test_pkg", backend / "__init__.py", submodule_search_locations=[str(backend)])
        package = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = package
        spec.loader.exec_module(package)
        cls.m = importlib.import_module("rules_test_pkg.main")
        cls.clock = importlib.import_module("rules_test_pkg.clock")

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        cls.fixture.cleanup()

    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=ROOT / "private")
        self.addCleanup(temp.cleanup)
        self.db = str(Path(temp.name) / "alarms.db")
        self.now = NOW
        self.stops = []

        async def end_ring():
            self.stops.append(True)
            self.m.ring_state.is_ringing = False
            self.m.ring_state.alarm = None

        patches = [patch.object(self.m, "DB_PATH", self.db),
                   patch.object(self.m, "local_now", side_effect=lambda: self.now),
                   patch.object(self.m, "ring_state", self.m.ActiveRingingState()),
                   patch.object(self.m, "_ring_helper", new=AsyncMock(side_effect=AssertionError("No audio"))),
                   patch.object(self.m, "_end_ring", new=end_ring),
                   patch.object(self.m, "broadcast_state", new=AsyncMock()),
                   patch.object(self.m.httpx, "AsyncClient", side_effect=AssertionError("No speaker helper calls"))]
        for value in patches:
            value.start()
            self.addCleanup(value.stop)
        self.m.init_db()
        self.client = TestClient(self.m.app, base_url="http://127.0.0.1:8081")
        self.addCleanup(self.client.close)

    def post(self, **over):
        body = {"label": "Wake", "time_hhmm": "07:30", "weekdays": ["Mon"], "recurring": True, **over}
        return self.client.post("/api/alarms", json=body)

    def row(self, alarm_id):
        with contextlib.closing(sqlite3.connect(self.db)) as conn:
            conn.row_factory = sqlite3.Row
            return dict(conn.execute("SELECT * FROM alarms WHERE id = ?", (alarm_id,)).fetchone())

    def events(self):
        with contextlib.closing(sqlite3.connect(self.db)) as conn:
            return [r[0] for r in conn.execute("SELECT kind FROM alarm_events ORDER BY id")]

    def ring(self, alarm_id, **extra):
        alarm = self.row(alarm_id)
        alarm["weekdays"] = json.loads(alarm["weekdays"])
        alarm.update(extra)
        self.m.ring_state.is_ringing, self.m.ring_state.alarm = True, alarm
        return alarm

    # ---------- validation ----------
    def test_refuses_values_the_scheduler_would_never_ring(self):
        cases = {"Unknown day": {"weekdays": ["Monday"]},
                 "date isn't valid": {"weekdays": [], "target_date": "2026-02-30"},
                 "already passed": {"weekdays": [], "target_date": "2026-11-12", "time_hhmm": "07:04"},
                 "doesn't fall on": {"weekdays": ["Mon"], "target_date": "2026-11-12"},
                 "under 80": {"label": "x" * 81},
                 "at least 4": {"code_red": True, "custom_phrase": "  ab  "},
                 "under 120": {"code_red": True, "custom_phrase": "x" * 121},
                 "Off, Easy or Hard": {"math_mission_difficulty": 3},
                 "0 and 100": {"volume_percent": 150},
                 "up to 10": {"snooze_limit": 11},
                 "1 and 30": {"snooze_limit": 3, "snooze_minutes": 0},
                 "can't snooze": {"code_red": True, "custom_phrase": "I am up", "snooze_limit": 3}}
        for message, over in cases.items():
            with self.subTest(message):
                response = self.post(**over)
                self.assertEqual(response.status_code, 400, response.text)
                self.assertIn(message, response.json()["detail"])
        self.assertEqual(self.client.get("/api/alarms").json()["alarms"], [])

    def test_cleans_what_it_stores(self):
        body = self.post(label="  Gym  ", weekdays=["Sun", "Mon", "Mon"], custom_phrase="leftover").json()
        saved = self.row(body["id"])
        self.assertEqual(saved["label"], "Gym")
        self.assertEqual(json.loads(saved["weekdays"]), ["Mon", "Sun"])
        self.assertEqual(saved["custom_phrase"], "", "a phrase is only kept while Code Red is on")
        current = self.post(weekdays=[], target_date="2026-11-12", time_hhmm="07:05")
        self.assertEqual(current.status_code, 200, "the current minute still counts as ahead")
        later_off = self.post(weekdays=[], target_date="2026-11-01", enabled=False)
        self.assertEqual(later_off.status_code, 200, "a switched-off past one-time alarm can still be saved")

    def test_missing_alarms_answer_404_with_a_plain_message(self):
        self.assertEqual(self.client.delete("/api/alarms/alm-gone").status_code, 404)
        self.assertEqual(self.client.patch("/api/alarms/alm-gone/toggle").status_code, 404)
        response = self.client.put("/api/alarms/alm-gone", json={"label": "x", "time_hhmm": "07:00", "weekdays": ["Mon"]})
        self.assertEqual(response.status_code, 404)
        self.assertIn("gone", response.json()["detail"])

    def test_turning_on_a_passed_one_time_alarm_moves_it_to_the_next_occurrence(self):
        later = self.post(weekdays=[], target_date="2026-11-01", time_hhmm="08:00", enabled=False).json()["id"]
        earlier = self.post(weekdays=[], target_date="2026-11-01", time_hhmm="06:00", enabled=False).json()["id"]
        future = self.post(weekdays=[], target_date="2026-12-01", time_hhmm="06:00", enabled=False).json()["id"]
        for alarm_id in (later, earlier, future):
            self.assertEqual(self.client.patch(f"/api/alarms/{alarm_id}/toggle").status_code, 200)
        self.assertEqual(self.row(later)["target_date"], "2026-11-12", "08:00 is still ahead today")
        self.assertEqual(self.row(earlier)["target_date"], "2026-11-13", "06:00 already passed today")
        self.assertEqual(self.row(future)["target_date"], "2026-12-01", "a future date is kept")
        listed = {a["id"]: a for a in self.client.get("/api/alarms").json()["alarms"]}
        self.assertEqual(listed[earlier]["next_ring_local"], "2026-11-13T06:00:00")

    # ---------- snooze ----------
    def test_snooze_is_saved_limited_and_wakes_the_pc(self):
        alarm_id = self.post(weekdays=[], target_date="2026-11-12", time_hhmm="07:05", snooze_limit=2, snooze_minutes=9).json()["id"]
        with contextlib.closing(sqlite3.connect(self.db)) as conn:  # it rang: one-time alarms switch themselves off
            conn.execute("UPDATE alarms SET enabled = 0, last_triggered_date = '2026-11-12' WHERE id = ?", (alarm_id,))
            conn.commit()
        self.ring(alarm_id)
        response = self.client.post("/api/alarms/snooze", json={"alarm_id": alarm_id})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["left"], 1)
        saved = self.row(alarm_id)
        self.assertEqual((saved["snoozes_used"], saved["snoozed_until"]), (1, "2026-11-12T07:14:00"))
        self.assertFalse(self.m.ring_state.is_ringing)
        self.assertEqual(self.client.get("/api/next").json()["at"], "2026-11-12T07:14:00",
                         "the wake timer covers a snoozed one-time alarm even though it is switched off")
        self.ring(alarm_id)
        self.assertEqual(self.client.post("/api/alarms/snooze", json={"alarm_id": alarm_id}).status_code, 200)
        self.ring(alarm_id)
        last = self.client.post("/api/alarms/snooze", json={"alarm_id": alarm_id})
        self.assertEqual(last.status_code, 403)
        self.assertIn("No snoozes left", last.json()["detail"])
        self.assertTrue(self.m.ring_state.is_ringing, "a refused snooze keeps ringing")
        self.assertEqual(self.client.post("/api/alarms/dismiss", json={"alarm_id": alarm_id}).status_code, 200)
        saved = self.row(alarm_id)
        self.assertEqual((saved["snoozes_used"], saved["snoozed_until"]), (0, None), "stopping resets the snoozes")
        self.assertEqual(self.events(), ["snoozed", "snoozed", "dismissed"])

    def test_snooze_refuses_code_red_previews_timers_and_alarms_without_it(self):
        plain = self.post().json()["id"]
        locked = self.post(code_red=True, custom_phrase="I am up").json()["id"]
        for alarm_id, extra, code in ((plain, {}, 403), (locked, {}, 403), (plain, {"is_test": True}, 409),
                                      (plain, {"is_timer": True}, 409)):
            with self.subTest(alarm_id=alarm_id, extra=extra):
                self.ring(alarm_id, **extra)
                self.assertEqual(self.client.post("/api/alarms/snooze", json={"alarm_id": alarm_id}).status_code, code)
                self.assertTrue(self.m.ring_state.is_ringing)
        self.assertEqual(self.client.post("/api/alarms/snooze", json={"alarm_id": "other"}).status_code, 409)
        self.m.ring_state.is_ringing = False
        self.assertEqual(self.client.post("/api/alarms/snooze", json={"alarm_id": plain}).status_code, 409)

    def test_editing_or_switching_off_ends_a_pending_snooze(self):
        alarm_id = self.post(snooze_limit=3).json()["id"]
        for change in ("toggle", "edit"):
            with contextlib.closing(sqlite3.connect(self.db)) as conn:
                conn.execute("UPDATE alarms SET enabled = 1, snoozes_used = 1, snoozed_until = '2026-11-12T07:14:00' WHERE id = ?", (alarm_id,))
                conn.commit()
            if change == "toggle":
                self.client.patch(f"/api/alarms/{alarm_id}/toggle")
            else:
                self.client.put(f"/api/alarms/{alarm_id}", json={"label": "Wake", "time_hhmm": "07:30", "weekdays": ["Mon"], "snooze_limit": 3})
            self.assertIsNone(self.row(alarm_id)["snoozed_until"], change)

    # ---------- dismiss ----------
    def test_code_red_phrase_forgives_phone_punctuation_but_not_letters(self):
        alarm_id = self.post(code_red=True, custom_phrase="I'm up -- really").json()["id"]
        for typed, code in (("i’m up -- really", 403), ("I’m  up –- really ", 200)):
            with self.subTest(typed=typed):
                self.ring(alarm_id)
                self.assertEqual(self.client.post("/api/alarms/dismiss", json={"alarm_id": alarm_id, "typed_phrase": typed}).status_code, code)
        self.assertEqual(self.m.normalize_phrase("  a  b“ "), 'a b"')

    # ---------- old databases ----------
    def test_an_old_database_gains_snooze_columns_and_keeps_its_alarms(self):
        with contextlib.closing(sqlite3.connect(self.db)) as conn:
            conn.execute("DROP TABLE alarms")
            conn.execute('''CREATE TABLE alarms (id TEXT PRIMARY KEY, label TEXT NOT NULL, time_hhmm TEXT NOT NULL,
                target_date TEXT, weekdays TEXT NOT NULL, recurring INTEGER NOT NULL DEFAULT 0, enabled INTEGER NOT NULL DEFAULT 1,
                code_red INTEGER NOT NULL DEFAULT 0, custom_phrase TEXT, volume_percent INTEGER NOT NULL DEFAULT 100,
                ring_duration_sec INTEGER NOT NULL DEFAULT 600, wake_up_check_min INTEGER NOT NULL DEFAULT 5,
                math_mission_difficulty INTEGER NOT NULL DEFAULT 0, snooze_limit INTEGER NOT NULL DEFAULT 0,
                snoozes_used INTEGER NOT NULL DEFAULT 0, last_triggered_date TEXT)''')
            conn.execute("INSERT INTO alarms (id, label, time_hhmm, weekdays) VALUES ('old', 'Owner alarm', '06:00', '[\"Mon\"]')")
            conn.commit()
        self.m.init_db()
        self.m.init_db()  # running twice changes nothing
        saved = self.row("old")
        self.assertEqual((saved["label"], saved["snooze_minutes"], saved["snoozed_until"], saved["snooze_limit"]),
                         ("Owner alarm", 9, None, 0))

    def test_speaker_choice_is_written_atomically(self):
        # Only the two config functions run; the speaker helper module itself is never imported.
        tree = ast.parse((ROOT / "code_red_alarm/backend/audio_enforcer.py").read_text(encoding="utf-8"))
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("load_config", "save_config")]
        path = str(Path(self.db).with_name("device_config.json"))
        ns = {"os": os, "json": json, "CONFIG_PATH": path, "list_outputs": lambda: [{"name": "default"}]}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "audio-config", "exec"), ns)
        ns["save_config"]({"output": "Speakers", "lock_volume": True})
        self.assertEqual(ns["load_config"](), {"output": "Speakers", "lock_volume": True})
        self.assertFalse(os.path.exists(path + ".tmp"))
        Path(path).write_text("[1, 2]", encoding="utf-8")
        self.assertEqual(ns["load_config"]()["output"], "default", "a file that isn't settings falls back")


class ClockHelpers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("rules_clock", ROOT / "code_red_alarm" / "backend" / "clock.py")
        cls.c = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.c)

    def test_missed_occurrence_finds_the_latest_unrung_time_in_the_gap(self):
        c, at = self.c, datetime.datetime
        daily = {"enabled": True, "time_hhmm": "05:45", "weekdays": ["Tue", "Thu"]}
        self.assertEqual(c.missed_occurrence(daily, at(2026, 11, 9, 22), at(2026, 11, 12, 7)), ("2026-11-12", at(2026, 11, 12, 5, 45)))
        self.assertIsNone(c.missed_occurrence({**daily, "last_triggered_date": "2026-11-12"}, at(2026, 11, 12, 5), at(2026, 11, 12, 7)))
        self.assertIsNone(c.missed_occurrence(daily, at(2026, 11, 12, 6), at(2026, 11, 12, 7)), "nothing due in that stretch")
        self.assertIsNone(c.missed_occurrence({**daily, "enabled": False}, at(2026, 11, 9), at(2026, 11, 12, 7)))
        once = {"enabled": True, "time_hhmm": "08:00", "weekdays": [], "target_date": "2026-11-10"}
        self.assertEqual(c.missed_occurrence(once, at(2026, 11, 1), at(2026, 11, 12))[0], "2026-11-10")
        self.assertEqual(c.missed_occurrence(daily, at(2000, 1, 1), at(2026, 11, 12, 7))[0], "2026-11-12", "long gaps stay cheap")

    def test_roll_forward_and_snooze_aware_next_ring(self):
        c, at = self.c, datetime.datetime
        self.assertEqual(c.roll_forward("08:00", at(2026, 11, 12, 7, 5)), "2026-11-12")
        self.assertEqual(c.roll_forward("07:05", at(2026, 11, 12, 7, 5)), "2026-11-13")
        weekly = {"enabled": True, "time_hhmm": "09:00", "weekdays": ["Thu"], "snoozed_until": "2026-11-12T07:14:00"}
        self.assertEqual(c.next_ring(weekly, at(2026, 11, 12, 7, 5)), at(2026, 11, 12, 7, 14))
        self.assertEqual(c.next_ring(weekly, at(2026, 11, 12, 7, 20)), at(2026, 11, 12, 9))
        self.assertEqual(c.next_ring({**weekly, "snoozed_until": "garbage"}, at(2026, 11, 12, 7, 5)), at(2026, 11, 12, 9))


if __name__ == "__main__":
    unittest.main()
