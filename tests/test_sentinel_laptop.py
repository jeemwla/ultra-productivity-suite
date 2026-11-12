"""Laptop or desktop decision, SMBIOS chassis parsing, battery report parsing and battery merging.
Pure functions with fixtures in the real powercfg /batteryreport /XML format; nothing on this PC is read or changed."""
import os
import struct
import sys
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pc_sentinel"))

from backend import form_factor as ff  # noqa: E402
from backend import laptop  # noqa: E402
from backend import max_power as mp  # noqa: E402
from backend import native_sensors as ns  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fixture(name):
    with open(os.path.join(FIX, name), "rb") as fh:
        return fh.read()


def smbios(*structs):
    """RawSMBIOSData: 4 version bytes, DWORD length, then structures each followed by its string set."""
    table = b"".join(structs) + bytes([127, 4, 0xFF, 0xFF]) + b"\x00\x00"
    return bytes([0, 3, 4, 0]) + struct.pack("<I", len(table)) + table


def enclosure(kind, strings=b"Maker\x00"):
    formatted = bytes([3, 9, 0x03, 0x00, 1, kind, 0, 0, 0])
    return formatted + (strings + b"\x00" if strings else b"\x00\x00")


class FormFactor(unittest.TestCase):
    def test_smbios_chassis_is_found_after_other_structures(self):
        bios = bytes([0, 6, 0, 0, 1, 2]) + b"Vendor\x00Ver\x00\x00"
        self.assertEqual(ff.chassis_from_smbios(smbios(bios, enclosure(10))), [10])
        self.assertEqual(ff.chassis_from_smbios(smbios(enclosure(0x80 | 3, strings=b""))), [3], "lock bit is ignored")

    def test_broken_smbios_never_raises(self):
        for raw in (b"", b"\x00" * 7, bytes([0, 3, 4, 0]) + struct.pack("<I", 50) + b"\x03\x02", smbios(bytes([3, 200, 0, 0]))):
            self.assertIsInstance(ff.chassis_from_smbios(raw), list)

    def test_chassis_beats_a_ups_battery(self):
        r = ff.classify({"battery": True, "chassis_types": [3], "platform_role": 1})
        self.assertEqual(r["type"], "desktop")
        self.assertIn("UPS", r["basis"])

    def test_portable_chassis_is_a_laptop_even_without_a_battery(self):
        self.assertEqual(ff.classify({"battery": False, "chassis_types": [10]})["type"], "laptop")

    def test_role_and_battery_fallbacks(self):
        self.assertEqual(ff.classify({"chassis_types": [2], "platform_role": 2})["type"], "laptop")
        self.assertEqual(ff.classify({"chassis_types": [], "platform_role": 1})["type"], "desktop")
        self.assertEqual(ff.classify({"battery": True})["type"], "laptop")
        self.assertEqual(ff.classify({"modern_standby": True})["type"], "laptop")
        self.assertEqual(ff.classify({})["type"], "desktop")

    def test_every_combination_gives_one_answer(self):
        count = 0
        for battery in (False, True):
            for ms in (False, True):
                for chassis in ([], [1], [2], [3], [7], [9], [10], [13], [31], [35], [3, 10]):
                    for role in (None, 0, 1, 2, 3, 8):
                        r = ff.classify({"battery": battery, "modern_standby": ms, "chassis_types": chassis, "platform_role": role})
                        self.assertIn(r["type"], ("laptop", "desktop"))
                        self.assertTrue(r["basis"])
                        if any(c in ff.PORTABLE_CHASSIS for c in chassis):
                            self.assertEqual(r["type"], "laptop")
                        elif any(c in ff.DESKTOP_CHASSIS for c in chassis):
                            self.assertEqual(r["type"], "desktop")
                        count += 1
        self.assertEqual(count, 264)

    def test_max_power_uses_the_shared_decision(self):
        ups_desktop = {"battery": True, "modern_standby": False, "arm64": False, "hybrid": False, "cpu_name": "Fixture", "physical_cores": 8,
                       "chassis_types": [7], "platform_role": 1}
        self.assertEqual(mp.choose_profile(ups_desktop)["kind"], "desktop")
        self.assertEqual(mp.choose_profile({**ups_desktop, "chassis_types": [10], "platform_role": 2})["kind"], "laptop")

    def test_device_form_merges_wmi_fallbacks(self):
        r = ns.device_form({"chassis": [9], "battery": 1}, facts={"battery": False, "chassis_types": []})
        self.assertEqual((r["type"], r["has_battery"]), ("laptop", True))
        r = ns.device_form({"chassis": [], "system": {"PCSystemType": 1}}, facts={})
        self.assertEqual((r["type"], r["has_battery"]), ("desktop", False))


class BatteryReport(unittest.TestCase):
    def test_laptop_report(self):
        r = laptop.parse_battery_report(fixture("battery_report_laptop.xml"))
        self.assertEqual(r["platform_role"], "Mobile")
        self.assertEqual(r["batteries"][0], {"id": "FX76", "maker": "FixtureCell", "chemistry": "LIon",
                                             "design_mwh": 76000, "full_mwh": 68400, "cycles": 142, "relative": False})
        # 30% + 5% + 50% of a full charge drained on battery over a 3-day report window
        self.assertEqual(r["daily_use"]["pct_per_day"], 28.3)
        self.assertEqual(r["daily_use"]["on_battery_hours_per_day"], 4.3)
        self.assertEqual(r["daily_use"]["days"], 3.0)
        self.assertEqual([h["health_pct"] for h in r["history"]], [92.5, 90.0])
        self.assertEqual([h["battery_hours_per_day"] for h in r["history"]], [2.0, 2.0])

    def test_sparse_report_leaves_missing_fields_empty(self):
        r = laptop.parse_battery_report(fixture("battery_report_sparse.xml"))
        b = r["batteries"][0]
        self.assertEqual(b["design_mwh"], 50000)
        self.assertIsNone(b["full_mwh"])
        self.assertIsNone(b["cycles"])
        self.assertIsNone(b["maker"])
        self.assertIsNone(b["chemistry"])
        self.assertIsNone(r["daily_use"], "capacity never reported, so no daily use is invented")
        self.assertEqual(r["history"], [])

    def test_desktop_report_has_no_battery(self):
        r = laptop.parse_battery_report(fixture("battery_report_desktop.xml"))
        self.assertEqual((r["batteries"], r["history"], r["daily_use"], r["platform_role"]), ([], [], None, "Desktop"))

    def test_short_or_backwards_windows_have_no_daily_use(self):
        now = datetime(2026, 11, 12, tzinfo=timezone.utc)
        row = {"at": now, "ac": False, "charge": 100, "full": 100}
        self.assertIsNone(laptop.daily_use([row], now, now + timedelta(hours=2)))
        self.assertIsNone(laptop.daily_use([row], now, now - timedelta(days=1)))
        plugged = laptop.daily_use([dict(row, ac=True), dict(row, ac=True, at=now + timedelta(hours=5))], now, now + timedelta(days=2))
        self.assertEqual((plugged["pct_per_day"], plugged["on_battery_hours_per_day"]), (0.0, 0.0), "always plugged in is a real zero")

    def test_durations(self):
        self.assertEqual(laptop._duration_h("P1DT10H48M16S"), 24 + 10 + 48 / 60 + 16 / 3600)
        self.assertEqual(laptop._duration_h("PT0S"), 0.0)
        self.assertIsNone(laptop._duration_h(""))
        self.assertIsNone(laptop._duration_h("garbage"))


class BatteryMerge(unittest.TestCase):
    REPORT = None

    def setUp(self):
        self.report = laptop.parse_battery_report(fixture("battery_report_laptop.xml"))

    def test_no_battery_anywhere(self):
        self.assertFalse(laptop.build_battery({}, {"no_battery": True}, {})["has_battery"])

    def test_charging_with_full_wmi_data(self):
        d = {"static": [{"DesignedCapacity": 76000, "ManufactureName": "Fixture", "DeviceName": "FX76", "Chemistry": 0x4E4F494C}],
             "full": [{"FullChargedCapacity": 68400}], "cycle": [{"CycleCount": 150}],
             "status": [{"ChargeRate": 45000, "DischargeRate": 0, "RemainingCapacity": 34200, "Voltage": 16800, "PowerOnline": True, "Charging": True}],
             "win": [{"EstimatedChargeRemaining": 50, "EstimatedRunTime": 71582788}]}
        b = laptop.build_battery(d, {"ac": True, "charging": True, "no_battery": False, "percent": 50, "seconds_left": None}, self.report)
        self.assertEqual((b["state"], b["charge_pct"], b["rate_w"], b["charge_w"]), ("Charging", 50, 45.0, 45.0))
        self.assertEqual(b["minutes_to_full"], 46)
        self.assertIsNone(b["minutes_left"])
        self.assertEqual((b["health_pct"], b["wear_pct"], b["cycles"]), (90.0, 10.0, 150))
        self.assertEqual(b["daily_use"]["pct_per_day"], 28.3)
        self.assertEqual(b["chemistry"], "Lithium-ion")

    def test_on_battery_time_left(self):
        d = {"status": [{"ChargeRate": 0, "DischargeRate": 17100, "RemainingCapacity": 34200, "PowerOnline": False, "Discharging": True}],
             "win": [{"EstimatedChargeRemaining": 50}]}
        b = laptop.build_battery(d, {"ac": False, "charging": False, "no_battery": False}, {})
        self.assertEqual((b["state"], b["rate_w"], b["discharge_w"], b["minutes_left"]), ("On battery", -17.1, 17.1, 120))
        self.assertIsNone(b["minutes_to_full"])

    def test_report_fills_what_wmi_lacks(self):
        b = laptop.build_battery({"win": [{"EstimatedChargeRemaining": 80}]}, {"ac": True, "charging": False}, self.report)
        self.assertEqual((b["design_mwh"], b["full_mwh"], b["cycles"], b["maker"]), (76000, 68400, 142, "FixtureCell"))
        self.assertEqual(b["chemistry"], "Lithium-ion")
        self.assertEqual(b["state"], "Plugged in, not charging")

    def test_missing_values_stay_missing(self):
        b = laptop.build_battery({"win": [{}]}, {}, {})
        self.assertTrue(b["has_battery"])
        for key in ("charge_pct", "state", "plugged_in", "design_mwh", "full_mwh", "health_pct", "wear_pct", "cycles",
                    "rate_w", "charge_w", "discharge_w", "voltage_v", "minutes_left", "minutes_to_full", "daily_use", "chemistry"):
            self.assertIsNone(b[key], key)

    def test_nonsense_rates_are_ignored(self):
        d = {"status": [{"ChargeRate": 4294967295, "DischargeRate": 4294967295, "PowerOnline": True}], "win": [{"EstimatedChargeRemaining": 100}]}
        b = laptop.build_battery(d, {"ac": True}, {})
        self.assertIsNone(b["rate_w"])
        self.assertEqual(b["state"], "Fully charged")

    def test_charging_without_a_charge_rate_is_not_zero(self):
        d = {"status": [{"DischargeRate": 0, "Charging": True}], "win": [{"EstimatedChargeRemaining": 40}]}
        self.assertIsNone(laptop.build_battery(d, {"ac": True, "charging": True}, {})["rate_w"])


class CoolingAndSettings(unittest.TestCase):
    def test_cooling_report_says_when_no_fan_data_exists(self):
        from unittest.mock import patch
        from backend import lhm_collector as lc
        with patch.object(lc, "_fancontrol_config", return_value=None):
            none = lc.cooling_report([{"name": "CPU", "kind": "cpu", "sensors": [{"id": "/cpu/0/temperature/0", "type": "Temperature", "name": "Tctl", "value": 60}]}])
            ctl = lc.cooling_report([{"name": "EC", "kind": "motherboard", "sensors": [{"id": "/lpc/ec/control/0", "type": "Control", "name": "Fan", "value": 40}]}])
        self.assertEqual((none["has_fan_data"], none["fans"], none["controls"]), (False, [], 0))
        self.assertEqual((ctl["has_fan_data"], ctl["controls"]), (True, 1))

    def test_hide_cooling_preference_is_a_strict_optional_boolean(self):
        import tempfile
        from backend import preferences as prefs
        self.assertFalse(prefs.defaults()["hide_cooling_without_fans"])
        self.assertTrue(prefs.validate({"hide_cooling_without_fans": True})["hide_cooling_without_fans"])
        for bad in (1, "yes", None):
            with self.assertRaises(ValueError):
                prefs.validate({"hide_cooling_without_fans": bad})
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "p.json")
            prefs.save({"router_vpn": True}, _test_path=f)
            self.assertFalse(prefs.load(_test_path=f)["hide_cooling_without_fans"], "older files without the key stay off")
            prefs.save({"hide_cooling_without_fans": True}, _test_path=f)
            value = prefs.load(_test_path=f)
            self.assertTrue(value["hide_cooling_without_fans"])
            self.assertTrue(value["router_vpn"])

    def test_ui_scripts_are_served_revalidated_and_nothing_else(self):
        from fastapi.testclient import TestClient
        from backend import main
        client = TestClient(main.app, base_url="http://127.0.0.1:8084")
        for name in ("battery_view.js", "edit_layout.js"):
            r = client.get("/ui/" + name)
            self.assertEqual(r.status_code, 200, name)
            self.assertEqual(r.headers.get("cache-control"), "no-cache")
            self.assertIn("javascript", r.headers.get("content-type", ""))
        for name in ("index.html", "..%2Fbackend%2Fmain.py", "psu_picker.js"):
            self.assertEqual(client.get("/ui/" + name).status_code, 404, name)


class BatteryScenarios(unittest.TestCase):
    """Laptop battery data seen in the wild, without a laptop: every mix of present, missing and odd values."""

    def test_relative_capacity_batteries_hide_watt_hours_but_keep_health(self):
        xml = fixture("battery_report_laptop.xml").replace(b"<RelativeCapacity>0</RelativeCapacity>", b"<RelativeCapacity>1</RelativeCapacity>")
        r = laptop.parse_battery_report(xml)
        self.assertTrue(r["batteries"][0]["relative"])
        b = laptop.build_battery({"win": [{"EstimatedChargeRemaining": 70}]}, {"ac": False}, r)
        self.assertTrue(b["capacity_relative"])
        self.assertEqual(b["health_pct"], 90.0)
        normal = laptop.build_battery({"win": [{}]}, {}, laptop.parse_battery_report(fixture("battery_report_laptop.xml")))
        self.assertFalse(normal["capacity_relative"])

    def test_two_batteries_are_combined(self):
        d = {"static": [{"DesignedCapacity": 23000}, {"DesignedCapacity": 47000}],
             "full": [{"FullChargedCapacity": 21000}, {"FullChargedCapacity": 42000}],
             "status": [{"RemainingCapacity": 10000, "DischargeRate": 5000, "PowerOnline": False},
                        {"RemainingCapacity": 20000, "DischargeRate": 4000, "PowerOnline": False}],
             "win": [{"EstimatedChargeRemaining": 48}, {"EstimatedChargeRemaining": 48}]}
        b = laptop.build_battery(d, {"ac": False}, {})
        self.assertEqual((b["design_mwh"], b["full_mwh"], b["health_pct"], b["battery_count"]), (70000, 63000, 90.0, 2))
        self.assertEqual((b["rate_w"], b["minutes_left"]), (-9.0, 200))

    def test_thousands_of_mixed_inputs_keep_every_invariant(self):
        import itertools
        import math
        statics = [None, [{"DesignedCapacity": 76000, "Chemistry": 0x4E4F494C}], [{"DesignedCapacity": 0}]]
        fulls = [None, [{"FullChargedCapacity": 68400}], [{"FullChargedCapacity": 80000}]]
        statuses = [None, [{}],
                    [{"ChargeRate": 45000, "DischargeRate": 0, "RemainingCapacity": 30000, "Voltage": 16800, "PowerOnline": True, "Charging": True}],
                    [{"ChargeRate": 0, "DischargeRate": 15000, "RemainingCapacity": 30000, "PowerOnline": False, "Discharging": True}],
                    [{"ChargeRate": 0, "DischargeRate": 0, "RemainingCapacity": 54720, "PowerOnline": True}],
                    [{"ChargeRate": 4294967295, "DischargeRate": 4294967295, "RemainingCapacity": 0}]]
        wins = [None, [{}], [{"EstimatedChargeRemaining": 44, "EstimatedRunTime": 71582788}],
                [{"EstimatedChargeRemaining": 100, "EstimatedRunTime": 300}]]
        spss = [{}, {"ac": True, "charging": True, "no_battery": False, "percent": 44, "seconds_left": None},
                {"ac": False, "charging": False, "no_battery": False, "percent": 44, "seconds_left": 5400},
                {"ac": True, "charging": False, "no_battery": False, "percent": 80, "seconds_left": None},
                {"ac": None, "charging": None, "no_battery": True, "percent": None, "seconds_left": None}]
        reports = [{}, laptop.parse_battery_report(fixture("battery_report_laptop.xml")),
                   laptop.parse_battery_report(fixture("battery_report_sparse.xml"))]
        sane = lambda r, k: isinstance(r.get(k), int) and 0 <= r[k] <= laptop.MAX_SANE_MW
        count = 0
        for st, fu, ss, wi, sp, rp in itertools.product(statics, fulls, statuses, wins, spss, reports):
            d = {k: v for k, v in (("static", st), ("full", fu), ("status", ss), ("win", wi)) if v is not None}
            b = laptop.build_battery(d, sp, rp)
            count += 1
            if not b["has_battery"]:
                self.assertEqual(set(b), {"has_battery", "pack"})
                continue
            for k, v in b.items():
                if isinstance(v, float):
                    self.assertFalse(math.isnan(v) or math.isinf(v), (k, d, sp))
            if b["charge_pct"] is not None:
                self.assertTrue(0 <= b["charge_pct"] <= 100)
            if b["health_pct"] is not None:
                self.assertTrue(0 < b["health_pct"] <= 100)
                self.assertAlmostEqual(b["wear_pct"], round(100 - b["health_pct"], 1))
            if b["charging"]:
                self.assertIsNone(b["minutes_left"], "no time left while charging")
                self.assertTrue(b["rate_w"] is None or b["rate_w"] >= 0)
                self.assertEqual(b["state"], "Charging")
            else:
                self.assertIsNone(b["minutes_to_full"])
            if b["plugged_in"] is True:
                self.assertIsNone(b["minutes_left"], "no time left on the charger")
            if b["minutes_left"] is not None:
                self.assertGreater(b["minutes_left"], 0)
            if not (ss and any(sane(r, "ChargeRate") or sane(r, "DischargeRate") for r in ss)):
                self.assertIsNone(b["rate_w"], "no rate reported means no watts shown")
            if not (ss and ss[0].get("Voltage")):
                self.assertIsNone(b["voltage_v"])
        self.assertEqual(count, 3 * 3 * 6 * 4 * 5 * 3)


if __name__ == "__main__":
    unittest.main()
