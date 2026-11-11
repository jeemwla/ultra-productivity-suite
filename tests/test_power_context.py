"""Isolated power/context checks. No services, hardware probes or audio helpers."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pc_sentinel"))
sys.path.insert(0, str(ROOT / "core"))

from backend import power_context as context
from backend.lhm_collector import power_report


def gpu(value=None, *, name="GPU", sensors=None):
    return {"id": "/" + name, "kind": "gpu", "name": name,
            "sensors": sensors if sensors is not None else [
                {"type": "Power", "name": "GPU Board", "value": value}]}


def sample(at, watts=900, *, complete=True, estimated=False):
    return {"at": at, "total_w": watts, "complete": complete, "estimated": estimated}


class PowerReadings(unittest.TestCase):
    def test_missing_cpu_and_gpu_are_unknown_not_zero(self):
        report = power_report([], {})
        self.assertEqual(report["measured"], {"CPU package": None, "Graphics card": None})
        self.assertIsNone(report["measured_subtotal_w"])
        self.assertIsNone(report["dc_total_w"])
        self.assertIsNone(report["modeled_wall_w"])
        self.assertFalse(report["coverage"]["component_readings_complete"])

    def test_actual_zero_is_preserved(self):
        report = power_report([gpu(0)], {"cpu_power": 0})
        self.assertEqual(report["measured"], {"CPU package": 0.0, "Graphics card": 0.0})
        self.assertEqual(report["measured_subtotal_w"], 0.0)
        self.assertTrue(report["coverage"]["component_readings_complete"])
        self.assertIsNotNone(report["modeled_dc_w"])

    def test_invalid_cpu_readings_do_not_become_measurements(self):
        for value in (None, True, False, "42", -1, float("nan"), float("inf"), 10**500):
            with self.subTest(value=repr(value)):
                report = power_report([gpu(50)], {"cpu_power": value})
                self.assertIsNone(report["measured"]["CPU package"])
                self.assertEqual(report["measured_subtotal_w"], 50)
                self.assertIsNone(report["dc_total_w"])
                json.dumps(report, allow_nan=False)

    def test_invalid_gpu_readings_prevent_a_modeled_total(self):
        for value in (None, True, "12 W", -1, float("nan"), float("inf"), 10**500):
            with self.subTest(value=repr(value)):
                report = power_report([gpu(value)], {"cpu_power": 30})
                self.assertIsNone(report["measured"]["Graphics card"])
                self.assertEqual(report["measured_subtotal_w"], 30)
                self.assertIsNone(report["dc_total_w"])
                self.assertEqual(report["coverage"]["gpus_with_power"], 0)
                json.dumps(report, allow_nan=False)

    def test_one_missing_gpu_does_not_hide_the_other_reading(self):
        report = power_report([gpu(100, name="A"), gpu(None, name="B")], {"cpu_power": 40})
        self.assertIsNone(report["measured"]["Graphics card"])
        self.assertEqual(report["measured_subtotal_w"], 140)
        self.assertIsNone(report["dc_total_w"])
        self.assertEqual(report["gpu_readings"][0]["watts"], 100)
        self.assertIsNone(report["gpu_readings"][1]["watts"])
        self.assertIn("GPU 2 aggregate power", report["coverage"]["unknown_components"])

    def test_all_detected_gpu_aggregates_are_summed_once(self):
        sensors = [{"type": "Power", "name": name, "value": value}
                   for name, value in (("GPU Core", 60), ("GPU Package", 80), ("GPU Board", 100))]
        report = power_report([gpu(sensors=sensors), gpu(25, name="B")], {"cpu_power": 40})
        self.assertEqual(report["measured"]["Graphics card"], 125)
        self.assertEqual(report["measured_subtotal_w"], 165)
        self.assertEqual(report["gpu_readings"][0]["sensor"], "GPU Board")

    def test_partial_and_limit_sensors_are_not_aggregate_gpu_power(self):
        for name in ("GPU Core", "GPU Memory", "GPU Power Limit", "GPU Core Total"):
            with self.subTest(name=name):
                report = power_report([gpu(sensors=[{"name": name, "type": "Power", "value": 100}])], {"cpu_power": 40})
                self.assertIsNone(report["measured"]["Graphics card"])
                self.assertIsNone(report["dc_total_w"])

    def test_cpu_core_only_is_not_package_power(self):
        cpu = {"kind": "cpu", "sensors": [{"name": "CPU Core", "type": "Power", "value": 20}]}
        report = power_report([cpu, gpu(100)], {"cpu_power": 20})
        self.assertIsNone(report["measured"]["CPU package"])
        self.assertIsNone(report["dc_total_w"])

    def test_models_do_not_claim_measured_system_or_wall_power(self):
        report = power_report([gpu(100)], {"cpu_power": 40})
        self.assertIsNone(report["wall_w"])
        self.assertIsNone(report["measured_total_w"])
        self.assertEqual(report["dc_total_w"], report["modeled_dc_w"])
        self.assertEqual(report["modeled_wall_w"], round(report["modeled_dc_w"] / .9, 1))
        self.assertEqual(report["wall_model"]["efficiency"], .9)
        self.assertFalse(report["wall_model"]["is_measured"])
        self.assertTrue(report["dc_total_is_estimate"])
        self.assertFalse(report["coverage"]["complete"])
        self.assertFalse(report["advisory_eligible"])
        self.assertEqual(set(report["estimated"]), set(report["estimate_assumptions"]))

    def test_invalid_fan_readings_do_not_crash_power_reporting(self):
        board = {"kind": "motherboard", "sensors": [{"type": "Fan", "value": None},
                 {"type": "Fan", "value": True}, {"type": "Fan", "value": 600}]}
        report = power_report([board, gpu(100)], {"cpu_power": 40})
        self.assertEqual(report["estimated"]["Fans"], 1.8)

    def test_overflowing_subtotals_cannot_escape_as_infinity(self):
        report = power_report([gpu(1e308), gpu(1e308, name="B")], {"cpu_power": 40})
        self.assertIsNone(report["dc_total_w"])
        json.dumps(report, allow_nan=False)


class ContextStore(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ultra-power-test-", dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "dedicated" / "sentinel_power.json"

    def load(self):
        return context.load_context(_test_path=self.path)

    def save(self, body):
        return context.save_context(body, _test_path=self.path)

    def test_defaults_are_empty_and_reading_creates_no_file(self):
        value = self.load()
        self.assertIsNone(value["rated_watts"])
        self.assertIsNone(value["model"])
        self.assertIsNone(value["form_factor"])
        self.assertEqual(value["storage_status"], "not_configured")
        self.assertEqual(value["kind"], "desktop_psu")
        self.assertFalse(value["controls_hardware"])
        self.assertFalse(self.path.exists())

    def test_fields_are_independent_partial_updates_and_null_clears(self):
        self.save({"model": " Generic PSU ", "form_factor": "SFX-L"})
        self.assertIsNone(self.load()["rated_watts"])
        self.save({"rated_watts": 850})
        self.assertEqual(self.load()["model"], "Generic PSU")
        self.save({"model": None, "form_factor": " "})
        self.assertEqual({key: self.load()[key] for key in context._FIELDS},
                         {"rated_watts": 850, "model": None, "form_factor": None})
        document = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(set(document), context._FIELDS | {"version"})

    def test_invalid_changes_never_touch_existing_file(self):
        self.save({"rated_watts": 500})
        original = self.path.read_bytes()
        bad = [{"rated_watts": value} for value in (True, "500", 0, 49.9, 5001, -100, float("nan"), float("inf"), 10**500)]
        bad += [{"model": 123}, {"model": "x" * 121}, {"form_factor": "x" * 41},
                {"model": "line\nbreak"}, {"form_factor": []}, {"adapter_watts": 65}, [], None]
        for changes in bad:
            with self.subTest(changes=repr(changes)):
                with self.assertRaises(ValueError):
                    self.save(changes)
                self.assertEqual(self.path.read_bytes(), original)

    def test_rated_watt_bounds_and_fractional_values(self):
        for watts in (50, 5000, 850.5):
            with self.subTest(watts=watts):
                self.assertEqual(self.save({"rated_watts": watts})["rated_watts"], watts)

    def test_corruption_and_unknown_schema_fall_back_without_rewriting(self):
        self.path.parent.mkdir()
        for raw in (b"{broken", b"\xff", b"[]", b"null", b'{"version":2,"rated_watts":500}',
                    b'{"version":true,"rated_watts":500}', b'{"version":1,"rated_watts":-1}',
                    b'{"version":1,"rated_watts":500,"secret":"hidden"}', b" " * 16385):
            with self.subTest(raw=raw[:60]):
                self.path.write_bytes(raw)
                value = self.load()
                self.assertEqual(value["storage_status"], "invalid")
                self.assertIsNone(value["rated_watts"])
                self.assertEqual(self.path.read_bytes(), raw)
        self.save({"model": "Recovered"})
        self.assertEqual(self.load()["model"], "Recovered")
        self.assertIsNone(self.load()["rated_watts"])

    def test_unreadable_store_falls_back_distinctly(self):
        with patch.object(Path, "open", side_effect=PermissionError):
            value = self.load()
        self.assertEqual(value["storage_status"], "unavailable")
        self.assertIsNone(value["rated_watts"])

    def test_atomic_replace_keeps_old_document_until_complete_new_write(self):
        self.save({"rated_watts": 500})
        old = self.path.read_bytes()
        replace = os.replace
        seen = []
        def inspect_replace(source, target):
            seen.append(True)
            self.assertEqual(Path(target), self.path)
            self.assertEqual(Path(source).parent, self.path.parent)
            self.assertEqual(self.path.read_bytes(), old)
            self.assertEqual(json.loads(Path(source).read_text(encoding="utf-8"))["rated_watts"], 750)
            replace(source, target)
        with patch.object(context.os, "replace", side_effect=inspect_replace):
            self.save({"rated_watts": 750})
        self.assertTrue(seen)
        self.assertEqual(self.load()["rated_watts"], 750)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_replace_or_flush_failure_preserves_old_file_and_cleans_temp(self):
        self.save({"rated_watts": 500})
        old = self.path.read_bytes()
        for operation in ("replace", "fsync"):
            with self.subTest(operation=operation):
                with patch.object(context.os, operation, side_effect=OSError("test failure")):
                    with self.assertRaises(OSError):
                        self.save({"rated_watts": 750})
                self.assertEqual(self.path.read_bytes(), old)
                self.assertEqual(list(self.path.parent.glob("*.tmp")), [])


class HeadroomAdvisory(unittest.TestCase):
    def test_no_capacity_is_no_assessment(self):
        for watts in (None, 0, True, "1000", float("inf")):
            result = context.headroom_advisory(watts, [sample(0)], now_s=0)
            self.assertEqual(result["state"], "unavailable")
            self.assertFalse(result["show"])

    def test_single_peak_and_transient_load_stay_quiet(self):
        for samples in ([sample(0, 1100)], [sample(t) for t in range(0, 120, 10)]):
            result = context.headroom_advisory(1000, samples, now_s=samples[-1]["at"])
            self.assertEqual(result["state"], "quiet")
            self.assertFalse(result["show"])

    def test_sustained_complete_load_produces_only_informational_advisory(self):
        result = context.headroom_advisory(1000, [sample(t) for t in range(0, 121, 10)], now_s=120)
        self.assertEqual(result["state"], "advisory")
        self.assertEqual(result["severity"], "info")
        self.assertTrue(result["show"])
        self.assertIn("does not establish", result["text"])

    def test_incomplete_or_estimated_totals_never_produce_assessment(self):
        for kwargs in ({"complete": False}, {"estimated": True}):
            samples = [sample(t, **kwargs) for t in range(0, 121, 10)]
            result = context.headroom_advisory(1000, samples, now_s=120)
            self.assertEqual(result["state"], "unavailable")
            self.assertFalse(result["show"])
        result = context.headroom_advisory(1000, [{"at": 0, "total_w": 900}], now_s=0)
        self.assertEqual(result["state"], "unavailable")

    def test_unknown_or_below_threshold_reading_resets_sustained_period(self):
        for middle in (sample(60, None), sample(60, 100)):
            samples = [sample(t) if t != 60 else middle for t in range(0, 121, 10)]
            result = context.headroom_advisory(1000, samples, now_s=120)
            self.assertEqual(result["state"], "quiet")
            self.assertFalse(result["show"])

    def test_stale_gapped_and_reversed_history_do_not_trigger(self):
        stale = context.headroom_advisory(1000, [sample(t) for t in range(0, 121, 10)], now_s=150)
        self.assertEqual(stale["reason"], "stale_power")
        gapped = context.headroom_advisory(1000, [sample(0), sample(120)], now_s=120)
        self.assertFalse(gapped["show"])
        reversed_ = context.headroom_advisory(1000, [sample(10), sample(0)], now_s=10)
        self.assertEqual(reversed_["reason"], "invalid_history")

    def test_invalid_sample_values_and_parameters_are_bounded(self):
        for value in (True, -1, float("nan"), float("inf"), "900"):
            result = context.headroom_advisory(1000, [sample(0, value)], now_s=0)
            self.assertFalse(result["show"])
        for kwargs in ({"sustained_s": 0}, {"max_gap_s": 0}, {"threshold": 2}, {"now_s": float("nan")}):
            with self.assertRaises(ValueError):
                context.headroom_advisory(1000, [], **{"now_s": 0, **kwargs})


class PowerContextRoutes(unittest.TestCase):
    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from devtools import add_local_guard
        self.temp = tempfile.TemporaryDirectory(prefix="ultra-power-route-test-", dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "sentinel_power.json"
        app = FastAPI()
        context.register(app, _test_path=self.path)
        add_local_guard(app)
        self.client = TestClient(app, base_url="http://127.0.0.1:8084")
        self.addCleanup(self.client.close)

    def test_guarded_read_and_write_use_only_test_store(self):
        self.assertIsNone(self.client.get("/api/power/context").json()["rated_watts"])
        response = self.client.post("/api/power/context", json={"rated_watts": 850, "form_factor": "ATX"},
                                    headers={"Origin": "http://localhost:8084"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/power/context").json()["rated_watts"], 850)
        self.assertTrue(self.path.exists())

    def test_foreign_origin_and_rebinding_host_cannot_write(self):
        for headers in ({"Origin": "https://evil.example"}, {"Origin": "null"},
                        {"Origin": "http://localhost:3000"}, {"Host": "evil.example"}):
            response = self.client.post("/api/power/context", json={"rated_watts": 850}, headers=headers)
            self.assertEqual(response.status_code, 403)
        self.assertFalse(self.path.exists())
        self.assertEqual(self.client.get("/api/power/context", headers={"Host": "evil.example"}).status_code, 403)

    def test_invalid_payload_and_failed_save_are_explicit(self):
        self.assertEqual(self.client.post("/api/power/context", json={"rated_watts": True}).status_code, 422)
        self.assertEqual(self.client.post("/api/power/context", json={"adapter_watts": 65}).status_code, 422)
        with patch.object(context, "save_context", side_effect=OSError("sensitive path")):
            response = self.client.post("/api/power/context", json={"rated_watts": 850})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("sensitive path", response.text)


if __name__ == "__main__":
    unittest.main()
