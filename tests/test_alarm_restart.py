"""Run the real scheduler against disposable SQLite data and a controlled clock.

Only the scheduler/database function definitions are loaded from main.py. App
startup, settings, HTTP and the audio helper are never imported or executed.
"""
import ast
import asyncio
import contextlib
import datetime
import importlib.util
import io
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "code_red_alarm" / "backend"
spec = importlib.util.spec_from_file_location("restart_clock", BACKEND / "clock.py")
clock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clock)
tree = ast.parse((BACKEND / "main.py").read_text(encoding="utf-8"))
SCHEDULER = {"init_db", "log_event", "scheduler_loop", "_alarm_dict", "read_heartbeat", "write_heartbeat",
             "record_missed", "claim_due_snooze", "HEARTBEAT_EVERY", "GAP"}
definitions = [node for node in tree.body
               if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in SCHEDULER)
               or (isinstance(node, ast.Assign) and any(getattr(t, "id", None) in SCHEDULER for t in node.targets))]
CODE = compile(ast.Module(body=definitions, type_ignores=[]), str(BACKEND / "main.py"), "exec")


class AlarmRestart(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=ROOT / "private")
        self.addCleanup(temp.cleanup)
        self.path = str(Path(temp.name) / "alarms.db")
        self.now = datetime.datetime(2026, 11, 12, 7, 5)
        self.rang = []
        self.ns = {
            "asyncio": SimpleNamespace(), "contextlib": contextlib,
            "datetime": datetime, "json": json, "sqlite3": sqlite3,
            "DB_PATH": self.path, "Optional": Optional,
            "local_now": lambda: self.now, "due_day": clock.due_day,
            "claim_due_timer": lambda: None,
            "alarm_delivery_enabled": lambda: True, "time": time, "CATCH_UP": clock.CATCH_UP,
            "missed_occurrence": clock.missed_occurrence, "snoozed_until": clock.snoozed_until,
        }
        exec(CODE, self.ns)
        self.ns["init_db"]()

    def save(self, **overrides):
        alarm = dict(id="saved", label="Fixture", time_hhmm="07:00",
                     target_date="2026-11-12", weekdays="[]", recurring=0,
                     enabled=1, last_triggered_date=None)
        alarm.update(overrides)
        with contextlib.closing(sqlite3.connect(self.path)) as conn:
            columns = ", ".join(alarm)
            values = ", ".join("?" for _ in alarm)
            conn.execute(f"INSERT INTO alarms ({columns}) VALUES ({values})", tuple(alarm.values()))
            conn.commit()

    def saved(self, alarm_id="saved"):
        with contextlib.closing(sqlite3.connect(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            return dict(conn.execute("SELECT * FROM alarms WHERE id = ?", (alarm_id,)).fetchone())

    def ticks(self, *times, initially_ringing=False, startup_time=None, cancel_at_trigger=False):
        """Start a fresh scheduler, then cancel it after exactly these ticks."""
        times = iter(datetime.datetime.fromisoformat(t) for t in times)
        self.now = next(times)
        startup = [datetime.datetime.fromisoformat(startup_time)] if startup_time else []
        self.ns["local_now"] = lambda: startup.pop() if startup else self.now
        state = SimpleNamespace(is_ringing=initially_ringing)
        self.ns["ring_state"] = state
        self.ns["broadcast_state"] = AsyncMock()

        async def trigger(alarm):
            # The persisted occurrence must be claimed before any audio attempt.
            saved = self.saved(alarm["id"])
            self.assertIsNotNone(saved["last_triggered_date"])
            if alarm["target_date"] and not alarm["recurring"]:
                self.assertFalse(saved["enabled"])
            self.rang.append(alarm["id"])
            if cancel_at_trigger:
                raise asyncio.CancelledError  # process exits after the durable claim
            state.is_ringing = True

        async def sleep(seconds):
            self.assertEqual(seconds, 1.0)
            state.is_ringing = False  # simulated completion/dismissal between ticks
            try:
                self.now = next(times)
            except StopIteration:
                raise asyncio.CancelledError

        self.ns["trigger_alarm_now"] = trigger
        self.ns["asyncio"].sleep = sleep
        output = io.StringIO()
        # Every awaited double completes synchronously. Drive the coroutine
        # directly so this offline test needs no event-loop sockets on Windows.
        with contextlib.redirect_stdout(output), self.assertRaises(asyncio.CancelledError):
            self.ns["scheduler_loop"]().send(None)
        self.assertNotIn("[SchedulerLoop] Error:", output.getvalue())

    def test_restart_recovers_missed_one_time_and_persists_before_trigger(self):
        self.save()
        self.ticks("2026-11-12T07:05:00", "2026-11-12T07:05:01")
        self.assertEqual(self.rang, ["saved"])
        self.assertEqual(self.saved()["last_triggered_date"], "2026-11-12")
        self.assertFalse(self.saved()["enabled"])
        self.ticks("2026-11-12T07:06:00")
        self.assertEqual(self.rang, ["saved"], "a second process must not replay the occurrence")

    def test_exact_catch_up_boundary_is_inclusive(self):
        self.save()
        self.ticks("2026-11-12T07:30:00")
        self.assertEqual(self.rang, ["saved"])

    def test_older_than_window_is_untouched(self):
        self.save()
        before = self.saved()
        self.ticks("2026-11-12T07:30:00.000001", "2026-11-12T08:00:00")
        self.assertEqual(self.rang, [])
        self.assertEqual(self.saved(), before)

    def test_midnight_restart_uses_scheduled_date_and_weekday(self):
        self.save(time_hhmm="23:55", target_date="2026-11-11", weekdays='["Wed"]')
        self.ticks("2026-11-12T00:10:00")
        self.assertEqual(self.rang, ["saved"])
        self.assertEqual(self.saved()["last_triggered_date"], "2026-11-11")

    def test_recurring_occurrence_is_not_replayed_after_restart(self):
        self.save(recurring=1, target_date=None, weekdays='["Thu"]')
        self.ticks("2026-11-12T07:05:00", "2026-11-12T07:05:01")
        self.assertEqual(self.rang, ["saved"])
        self.assertTrue(self.saved()["enabled"])
        self.assertEqual(self.saved()["last_triggered_date"], "2026-11-12")
        self.ticks("2026-11-12T07:06:00")
        self.assertEqual(self.rang, ["saved"])

    def test_midnight_recurring_occurrence_is_claimed_for_yesterday(self):
        self.save(time_hhmm="23:55", target_date=None, recurring=1, weekdays='["Wed"]')
        self.ticks("2026-11-12T00:10:00")
        self.assertEqual(self.rang, ["saved"])
        self.assertEqual(self.saved()["last_triggered_date"], "2026-11-11")
        self.ticks("2026-11-12T00:11:00")
        self.assertEqual(self.rang, ["saved"])

    def test_exit_after_claim_does_not_replay_on_next_start(self):
        self.save(recurring=1, target_date=None)
        self.ticks("2026-11-12T07:05:00", cancel_at_trigger=True)
        self.assertEqual(self.rang, ["saved"])
        self.assertEqual(self.saved()["last_triggered_date"], "2026-11-12")
        self.ticks("2026-11-12T07:06:00")
        self.assertEqual(self.rang, ["saved"])

    def test_handled_disabled_future_wrong_date_and_weekday_stay_silent(self):
        self.save(id="handled", recurring=1, last_triggered_date="2026-11-12")
        self.save(id="disabled", enabled=0)
        self.save(id="future-time", time_hhmm="07:10")
        self.save(id="future-date", target_date="2026-11-13")
        self.save(id="wrong-weekday", weekdays='["Wed"]')
        before = {aid: self.saved(aid) for aid in
                  ("handled", "disabled", "future-time", "future-date", "wrong-weekday")}
        self.ticks("2026-11-12T07:05:00", "2026-11-12T07:05:01")
        self.assertEqual(self.rang, [])
        self.assertEqual({aid: self.saved(aid) for aid in before}, before)

    def test_current_minute_and_future_tick_still_ring_once(self):
        self.save(id="current", time_hhmm="07:05")
        self.save(id="future", time_hhmm="07:06")
        self.ticks("2026-11-12T07:05:30", "2026-11-12T07:06:00", "2026-11-12T07:06:01")
        self.assertEqual(self.rang, ["current", "future"])

    def test_startup_backlog_survives_an_initially_busy_tick(self):
        self.save()
        self.ticks("2026-11-12T07:05:00", "2026-11-12T07:05:01", initially_ringing=True)
        self.assertEqual(self.rang, ["saved"])

    def test_backward_clock_change_does_not_reopen_catch_up_window(self):
        self.save(time_hhmm="06:50")
        self.ticks("2026-11-12T08:00:00", "2026-11-12T07:00:00", "2026-11-12T07:00:01")
        self.assertEqual(self.rang, [])
        self.assertIsNone(self.saved()["last_triggered_date"])

    def test_backward_clock_change_between_initialization_and_first_tick(self):
        self.save(time_hhmm="06:50")
        self.ticks("2026-11-12T07:00:00", startup_time="2026-11-12T08:00:00")
        self.assertEqual(self.rang, [])

    def test_forward_clock_jump_keeps_existing_catch_up(self):
        self.save()
        self.ticks("2026-11-12T06:00:00", "2026-11-12T07:05:00", "2026-11-12T07:05:01")
        self.assertEqual(self.rang, ["saved"])

    def events(self):
        with contextlib.closing(sqlite3.connect(self.path)) as conn:
            return [(r[0], r[1]) for r in conn.execute("SELECT alarm_id, kind FROM alarm_events WHERE kind != 'due' ORDER BY id")]

    def test_sleep_longer_than_catch_up_logs_the_missed_alarm_without_ringing_it(self):
        self.save(id="early", recurring=1, target_date=None, weekdays='["Thu"]', time_hhmm="05:45")
        self.save(id="late", recurring=1, target_date=None, weekdays='["Thu"]', time_hhmm="06:50")
        self.ticks("2026-11-12T05:00:00", "2026-11-12T07:00:00", "2026-11-12T07:00:01")
        self.assertEqual(self.rang, ["late"], "the alarm inside the 30-minute window still rings after waking")
        self.assertEqual(self.events(), [("early", "missed")])
        self.assertIsNone(self.saved("early")["last_triggered_date"], "a miss is logged, never recorded as rung")

    def test_restart_logs_alarms_missed_while_the_engine_was_off_once(self):
        self.save(id="morning", recurring=1, target_date=None, weekdays='["Thu"]', time_hhmm="05:45")
        self.ticks("2026-11-12T05:00:00")
        self.ticks("2026-11-12T09:00:00")
        self.assertEqual(self.rang, [])
        self.assertEqual(self.events(), [("morning", "missed")])
        self.ticks("2026-11-12T09:05:00")
        self.assertEqual(self.events(), [("morning", "missed")], "a later restart does not log it again")

    def test_a_fresh_install_reports_nothing_missed(self):
        self.save(id="morning", recurring=1, target_date=None, weekdays='["Thu"]', time_hhmm="05:45")
        self.ticks("2026-11-12T09:00:00")
        self.assertEqual(self.events(), [])

    def snooze(self, until, used=1):
        with contextlib.closing(sqlite3.connect(self.path)) as conn:
            conn.execute("UPDATE alarms SET snoozed_until = ?, snoozes_used = ?, snooze_limit = 3, enabled = 0 WHERE id = 'saved'", (until, used))
            conn.commit()

    def test_a_snooze_rings_again_after_a_restart_and_is_cleared_first(self):
        self.save(last_triggered_date="2026-11-12", enabled=0)
        self.snooze("2026-11-12T07:14:00")
        self.ticks("2026-11-12T07:10:00", "2026-11-12T07:13:59")
        self.assertEqual(self.rang, [])
        self.ticks("2026-11-12T07:14:00", "2026-11-12T07:14:01")
        self.assertEqual(self.rang, ["saved"])
        saved = self.saved()
        self.assertIsNone(saved["snoozed_until"])
        self.assertEqual(saved["snoozes_used"], 1, "the count carries over so the limit holds")

    def test_a_snooze_that_ended_while_the_pc_was_off_is_logged_not_rung(self):
        self.save(last_triggered_date="2026-11-12", enabled=0)
        self.snooze("2026-11-12T07:14:00")
        self.ticks("2026-11-12T08:00:00")
        self.assertEqual(self.rang, [])
        self.assertIsNone(self.saved()["snoozed_until"])
        self.assertEqual(self.events(), [("saved", "missed")])

    def test_spring_forward_rings_an_alarm_inside_the_skipped_hour(self):
        self.save(id="dst", recurring=1, target_date=None, weekdays='["Sun"]', time_hhmm="02:30")
        self.ticks("2027-03-14T01:59:59", "2027-03-14T03:00:00", "2027-03-14T03:00:01")
        self.assertEqual(self.rang, ["dst"])

    def test_fall_back_does_not_ring_the_repeated_hour_twice(self):
        self.save(id="dst", recurring=1, target_date=None, weekdays='["Sun"]', time_hhmm="01:30")
        self.ticks("2026-11-01T01:29:59", "2026-11-01T01:30:00", "2026-11-01T01:59:59", "2026-11-01T01:00:00",
                   "2026-11-01T01:30:00", "2026-11-01T01:30:01")
        self.assertEqual(self.rang, ["dst"])
        self.assertEqual(self.events(), [(None, "clock_changed")])


if __name__ == "__main__":
    unittest.main()
