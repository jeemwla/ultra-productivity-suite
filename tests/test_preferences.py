"""Isolated settings persistence and router polling. No live router or hardware."""
import asyncio
import copy
import importlib.util
import json
import tempfile
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


prefs = module("sentinel_preferences_test", "pc_sentinel/backend/preferences.py")
network = module("sentinel_network_test", "pc_sentinel/backend/net_monitor.py")


class Preferences(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "private")
        self.addCleanup(self.temp.cleanup)
        self.file = Path(self.temp.name) / "preferences.json"

    def test_public_defaults_off_and_battery_automatic(self):
        value = prefs.load(_test_path=self.file)
        self.assertFalse(value["router_vpn"])
        self.assertFalse(value["router_ping"])
        self.assertIsNone(value["pages"]["battery"])
        self.assertEqual(value["storage_status"], "not_configured")

    def test_merge_persists_revision_and_independent_defaults(self):
        first = prefs.save({"router_vpn": True, "pages": {"speed": False}}, _test_path=self.file)
        second = prefs.save({"router_ping": True}, _test_path=self.file)
        self.assertGreater(int(second["revision"]), int(first["revision"]))
        self.assertTrue(second["router_vpn"])
        self.assertFalse(second["pages"]["speed"])
        self.assertEqual(second, prefs.load(_test_path=self.file))
        self.assertTrue(prefs.defaults()["pages"]["speed"])

    def test_every_visibility_combination_has_a_route_or_is_rejected(self):
        standard = [p for p in prefs.PAGES if p != "battery"]
        for bits in range(1 << len(standard)):
            pages = {p: bool(bits & (1 << i)) for i, p in enumerate(standard)}
            with self.subTest(bits=bits):
                if bits:
                    self.assertEqual(prefs.validate({"pages": pages})["pages"]["overview"], pages["overview"])
                else:
                    with self.assertRaises(ValueError):
                        prefs.validate({"pages": pages})
                    pages["battery"] = True
                    self.assertTrue(prefs.validate({"pages": pages})["pages"]["battery"])

    def test_invalid_requests_do_not_touch_saved_file(self):
        prefs.save({"router_vpn": True}, _test_path=self.file)
        before = self.file.read_bytes()
        invalid = [None, [], {"router_vpn": 1}, {"router_ping": "yes"}, {"pages": []},
                   {"pages": {"settings": False}}, {"pages": {"overview": None}}, {"pages": {"battery": "auto"}},
                   {"pages": {"unknown": True}}, {"secret": "no"}]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                prefs.save(value, _test_path=self.file)
            self.assertEqual(before, self.file.read_bytes())

    def test_malformed_or_oversized_saved_file_uses_safe_defaults(self):
        for raw in (b'{', b'[]', b'\xff', b' ' * 16385,
                    b'{"version":true,"revision":1}', b'{"version":1,"revision":-1}'):
            self.file.write_bytes(raw)
            value = prefs.load(_test_path=self.file)
            self.assertFalse(value["router_vpn"])
            self.assertFalse(value["router_ping"])
            self.assertEqual(value["storage_status"], "invalid_or_unavailable")

    def test_failed_atomic_replace_preserves_old_record_and_cleans_temp(self):
        prefs.save({"router_ping": True}, _test_path=self.file)
        before = self.file.read_bytes()
        with patch.object(prefs.os, "replace", side_effect=PermissionError("fixture failure")):
            with self.assertRaises(PermissionError):
                prefs.save({"router_ping": False}, _test_path=self.file)
        self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual([p.name for p in self.file.parent.iterdir()], [self.file.name])

    def test_router_targets_are_not_polled_until_enabled(self):
        targets = [{"id": "internet", "name": "DNS", "host": "1.1.1.1", "port": 53},
                   {"id": "t2", "name": "Router", "host": "192.0.2.1", "port": 80}]
        history = {t["id"]: deque(maxlen=60) for t in targets}
        async def exercise():
            measure = AsyncMock(return_value=1.0)
            with patch.object(network, "PING_TARGETS", targets), patch.object(network, "history", history), patch.object(network, "measure_tcp_ping", measure):
                result = await network.get_live_ping_snapshot()
                self.assertEqual([t["id"] for t in result["targets"]], ["internet"])
                self.assertEqual(measure.await_count, 1)
                measure.reset_mock()
                result = await network.get_live_ping_snapshot(router_ping=True)
                self.assertEqual(len(result["targets"]), 2)
                self.assertEqual(measure.await_count, 2)
        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
