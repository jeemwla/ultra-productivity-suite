"""Fast unit tests for the parts of the backends that parse or calculate (no servers, no hardware needed).

Run from the repo root:  python -m unittest discover -s tests -v
"""
import importlib.util
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "04_hardware_net_sentinel"))
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
        self.m = load("06_laptop_pack/laptop_pack.py", "laptop_pack")

    def test_verdicts(self):
        self.assertEqual(self.m._verdict(95, 100), "Like new.")
        self.assertTrue(self.m._verdict(85, 300).startswith("Healthy"))
        self.assertTrue(self.m._verdict(70, 600).startswith("Worn"))
        self.assertTrue(self.m._verdict(50, 900).startswith("Heavily worn"))
        self.assertIn("doesn't report", self.m._verdict(None, None))

    def test_sum_ignores_missing(self):
        self.assertEqual(self.m._sum([{"A": 10}, {"A": None}, {"A": 5}], "A"), 15)
        self.assertIsNone(self.m._sum([], "A"))


class Versions(unittest.TestCase):
    def test_version_compare(self):
        import devtools
        self.assertGreater(devtools._vtuple("2.10.0"), devtools._vtuple("2.9.1"))
        self.assertGreater(devtools._vtuple("v2.1.0"), devtools._vtuple("2.0.1"))
        self.assertEqual(devtools._vtuple("2.1"), devtools._vtuple("2.1.0"))

    def test_version_file(self):
        import devtools
        with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as f:
            self.assertEqual(devtools.version(), f.read().strip())


if __name__ == "__main__":
    unittest.main()
