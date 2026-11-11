"""Lifecycle tests for core/suite_core.py ServiceLoop and supervise() (review item R05).

Fake servers only: no sockets, no uvicorn listeners, no real apps, no alarm rings, no installed processes.
Run from the repo root:  python -W ignore -m unittest tests.test_lifecycle -v
"""
import asyncio
import importlib.util
import os
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_core():
    spec = importlib.util.spec_from_file_location("suite_core_under_test", os.path.join(ROOT, "core", "suite_core.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


core = _load_core()


class FakeServer:
    """Minimal uvicorn.Server stand-in: async serve(), should_exit, started."""

    def __init__(self, startup_delay=0.0, fail_on_start=False, ignore_exit=False, never_ready=False):
        self.startup_delay, self.fail_on_start = startup_delay, fail_on_start
        self.ignore_exit, self.never_ready = ignore_exit, never_ready
        self.should_exit = False
        self.started = False
        self.cancelled = False

    async def on_ready(self):
        pass

    async def serve(self):
        try:
            if self.startup_delay:
                await asyncio.sleep(self.startup_delay)
            if self.fail_on_start:
                raise SystemExit(1)  # what uvicorn does when it cannot bind its port
            if not self.never_ready:
                self.started = True
            await self.on_ready()
            while self.ignore_exit or not self.should_exit:
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class TickingAlarm(FakeServer):
    """Stands in for the alarm scheduler: records a timestamp every 50 ms on its own loop."""

    def __init__(self):
        super().__init__()
        self.ticks = []

    async def on_ready(self):
        async def tick():
            while not self.should_exit:
                self.ticks.append(time.monotonic())
                await asyncio.sleep(0.05)
        self._tick_task = asyncio.ensure_future(tick())


class BlockingSentinel(FakeServer):
    """A handler that blocks its event loop for 3 s with a synchronous call (the R05 failure mode)."""

    def __init__(self, block_s=3.0):
        super().__init__()
        self.block_s = block_s
        self.block_started = None
        self.block_ended = None

    async def on_ready(self):
        await asyncio.sleep(0.2)
        self.block_started = time.monotonic()
        time.sleep(self.block_s)  # deliberately blocks the whole loop
        self.block_ended = time.monotonic()


def _worker_threads():
    return [t for t in threading.enumerate() if t.name.startswith("ultrasuite-")]


def _max_gap(ticks, start, end):
    window = [t for t in ticks if start <= t <= end]
    if len(window) < 2:
        return float("inf")
    return max(b - a for a, b in zip(window, window[1:]))


class Isolation(unittest.TestCase):
    def tearDown(self):
        deadline = time.monotonic() + 5
        while _worker_threads() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(_worker_threads(), [], "a service thread leaked")

    def test_alarm_ticks_continue_while_sentinel_blocks_for_3_seconds(self):
        alarm, sentinel = TickingAlarm(), BlockingSentinel(3.0)
        a, s = core.ServiceLoop("alarm", [alarm]), core.ServiceLoop("services", [sentinel])
        try:
            self.assertTrue(a.start(5))
            self.assertTrue(s.start(5))
            deadline = time.monotonic() + 8
            while sentinel.block_ended is None and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertIsNotNone(sentinel.block_ended, "the blocking handler never finished")
            self.assertGreaterEqual(sentinel.block_ended - sentinel.block_started, 2.9)
            gap = _max_gap(alarm.ticks, sentinel.block_started, sentinel.block_ended)
            ticks = len([t for t in alarm.ticks if sentinel.block_started <= t <= sentinel.block_ended])
            self.assertLess(gap, 0.5, f"alarm loop stalled for {gap:.2f} s while Sentinel blocked")
            self.assertGreaterEqual(ticks, 30, f"only {ticks} alarm ticks during a 3 s block")
        finally:
            self.assertTrue(s.stop(5))
            self.assertTrue(a.stop(5))

    def test_control_one_shared_loop_does_stall(self):
        """The old design (one loop for everything) shows the problem the change removes."""
        alarm, sentinel = TickingAlarm(), BlockingSentinel(3.0)
        shared = core.ServiceLoop("shared", [alarm, sentinel])
        try:
            self.assertTrue(shared.start(5))
            deadline = time.monotonic() + 8
            while sentinel.block_ended is None and time.monotonic() < deadline:
                time.sleep(0.05)
            time.sleep(0.2)
            gap = _max_gap(alarm.ticks, sentinel.block_started - 0.2, sentinel.block_ended + 0.2)
            self.assertGreaterEqual(gap, 2.5, "a shared loop should stall the alarm during the block")
        finally:
            self.assertTrue(shared.stop(5))


class Lifecycle(unittest.TestCase):
    def tearDown(self):
        deadline = time.monotonic() + 5
        while _worker_threads() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(_worker_threads(), [], "a service thread leaked")

    def test_start_failure_is_reported_and_thread_ends(self):
        loop = core.ServiceLoop("alarm", [FakeServer(fail_on_start=True)])
        self.assertFalse(loop.start(5))
        self.assertEqual(loop.state, "failed")
        self.assertIsInstance(loop.error, SystemExit)
        self.assertTrue(loop.stop(2))
        self.assertEqual(loop.state, "failed", "stop must not hide a start failure")

    def test_readiness_timeout_then_cleanup(self):
        loop = core.ServiceLoop("services", [FakeServer(never_ready=True)])
        self.assertFalse(loop.start(0.3))
        self.assertIsInstance(loop.error, TimeoutError)
        self.assertTrue(loop.stop(2))
        self.assertFalse(loop.thread.is_alive())

    def test_orderly_shutdown_and_repeated_stop(self):
        fake = FakeServer()
        loop = core.ServiceLoop("alarm", [fake])
        self.assertTrue(loop.start(5))
        self.assertEqual(loop.state, "running")
        self.assertTrue(loop.stop(5))
        self.assertEqual(loop.state, "stopped")
        self.assertIsNone(loop.error)
        self.assertFalse(fake.cancelled, "a server that honours should_exit must not need cancelling")
        for _ in range(3):
            self.assertTrue(loop.stop(1))
        self.assertEqual(loop.state, "stopped")
        self.assertTrue(loop.loop.is_closed())

    def test_stop_cancels_a_server_that_ignores_should_exit(self):
        fake = FakeServer(ignore_exit=True)
        loop = core.ServiceLoop("services", [fake])
        self.assertTrue(loop.start(5))
        t0 = time.monotonic()
        self.assertTrue(loop.stop(0.5))
        self.assertLess(time.monotonic() - t0, 3.0)
        self.assertTrue(fake.cancelled)
        self.assertIsNone(loop.error, "a requested cancellation is not a failure")
        self.assertEqual(loop.state, "stopped")

    def test_stop_before_start_and_double_start(self):
        idle = core.ServiceLoop("alarm", [FakeServer()])
        self.assertTrue(idle.stop(1))
        self.assertEqual(idle.state, "stopped")
        loop = core.ServiceLoop("alarm", [FakeServer()])
        self.assertTrue(loop.start(5))
        with self.assertRaises(RuntimeError):
            loop.start(1)
        self.assertTrue(loop.stop(5))

    def test_one_server_ending_ends_its_group_only(self):
        quick, other = FakeServer(), FakeServer()
        group, neighbour = core.ServiceLoop("services", [quick]), core.ServiceLoop("alarm", [other])
        self.assertTrue(group.start(5) and neighbour.start(5))
        quick.should_exit = True
        deadline = time.monotonic() + 3
        while not group.finished and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(group.finished)
        self.assertFalse(neighbour.finished, "the alarm group must keep running")
        self.assertTrue(group.stop(2) and neighbour.stop(2))


class Supervise(unittest.TestCase):
    def tearDown(self):
        deadline = time.monotonic() + 5
        while _worker_threads() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(_worker_threads(), [], "a service thread leaked")

    def test_failed_start_stops_groups_already_running(self):
        ok_server = FakeServer()
        ok, bad = core.ServiceLoop("alarm", [ok_server]), core.ServiceLoop("services", [FakeServer(fail_on_start=True)])
        self.assertEqual(core.supervise([ok, bad], start_timeout=5, poll=0.02, stop_timeout=2), 1)
        self.assertEqual(ok.state, "stopped")
        self.assertEqual(bad.state, "failed")

    def test_requested_stop_returns_zero_and_joins_everything(self):
        a, s = core.ServiceLoop("alarm", [FakeServer()]), core.ServiceLoop("services", [FakeServer()])
        calls = {"n": 0}

        def should_stop():
            calls["n"] += 1
            return calls["n"] >= 3

        self.assertEqual(core.supervise([a, s], start_timeout=5, poll=0.02, stop_timeout=2, should_stop=should_stop), 0)
        self.assertEqual((a.state, s.state), ("stopped", "stopped"))

    def test_group_crash_while_running_returns_failure(self):
        class CrashesLater(FakeServer):
            async def on_ready(self):
                await asyncio.sleep(0.1)
                raise RuntimeError("handler crashed")

        a, s = core.ServiceLoop("alarm", [FakeServer()]), core.ServiceLoop("services", [CrashesLater()])
        self.assertEqual(core.supervise([a, s], start_timeout=5, poll=0.02, stop_timeout=2), 1)
        self.assertIsInstance(s.error, RuntimeError)
        self.assertEqual(a.state, "stopped")

    def test_no_service_groups_returns_without_waiting(self):
        self.assertEqual(core.supervise([]), 0)


if __name__ == "__main__":
    unittest.main()
