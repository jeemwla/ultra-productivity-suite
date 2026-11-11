"""Clock speeds in the PC Sentinel summary come only from core clock sensors."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pc_sentinel"))
sys.path.insert(0, os.path.join(ROOT, "core"))


def sensor(name, typ, value):
    return {"name": name, "type": typ, "value": value}


class SummaryClocks(unittest.TestCase):
    def setUp(self):
        from backend import lhm_collector
        self.m = lhm_collector

    def summarize(self, cpu_sensors, gpu_sensors):
        groups = [{"kind": "cpu", "name": "CPU", "sensors": cpu_sensors},
                  {"kind": "gpu", "name": "GPU", "sensors": gpu_sensors}]
        return self.m.summarize(groups)

    def test_uses_the_reported_core_average_and_gpu_core_clock(self):
        s = self.summarize([sensor("Bus Speed", "Clock", 99.8), sensor("Cores (Average)", "Clock", 5197.0),
                            sensor("Core #1", "Clock", 5225.0), sensor("Core #1 (Effective)", "Clock", 3189.0)],
                           [sensor("GPU Memory", "Clock", 45.0), sensor("GPU Core", "Clock", 1244.0)])
        self.assertEqual(s["cpu_clock"], 5197.0)
        self.assertEqual(s["gpu_clock"], 1244.0)

    def test_averages_per_core_clocks_without_effective_or_bus_values(self):
        s = self.summarize([sensor("Bus Speed", "Clock", 100.0), sensor("Core #1", "Clock", 4000.0),
                            sensor("Core #2", "Clock", 5000.0), sensor("Core #2 (Effective)", "Clock", 900.0)], [])
        self.assertEqual(s["cpu_clock"], 4500)

    def test_never_reports_a_memory_clock_as_the_gpu_clock(self):
        s = self.summarize([], [sensor("GPU Memory", "Clock", 2500.0), sensor("GPU Core", "Load", 20.0)])
        self.assertIsNone(s.get("gpu_clock"))
        self.assertIsNone(s.get("cpu_clock"))


if __name__ == "__main__":
    unittest.main()
