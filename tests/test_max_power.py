"""The Max Performance plan is created once from Ultimate (or High performance), sets only plugged-in values, never
touches other plans, and restores the previous plan. powercfg is fully simulated; nothing on this PC is changed."""
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pc_sentinel"))

from backend import max_power as mp  # noqa: E402

BAL, ULT, NEW = "381b4222-f694-41f0-9685-ff5bb260df2e", "86f3da30-a1b5-4f1c-9f17-6cfea6680f7d", "11111111-2222-3333-4444-555555555555"


class FakePowercfg:
    def __init__(self, ultimate_available=True, fail_setting=None):
        self.plans = {BAL: "Balanced", ULT: "Ultimate Performance"}
        self.active, self.calls, self.values = BAL, [], {}
        self.ultimate_available, self.fail_setting = ultimate_available, fail_setting

    def __call__(self, args):
        self.calls.append(args)
        op = args[1]
        if op == "/list":
            out = "\n".join(f"Power Scheme GUID: {g}  ({n}){' *' if g == self.active else ''}" for g, n in self.plans.items())
            return SimpleNamespace(returncode=0, stdout=out)
        if op == "/duplicatescheme":
            if args[2] == mp.ULTIMATE_TEMPLATE and not self.ultimate_available:
                return SimpleNamespace(returncode=1, stdout="")
            self.plans[NEW] = "Ultimate Performance" if args[2] == mp.ULTIMATE_TEMPLATE else "High performance"
            return SimpleNamespace(returncode=0, stdout=f"Power Scheme GUID: {NEW}  ({self.plans[NEW]})")
        if op == "/changename":
            self.plans[args[2]] = args[3]
            return SimpleNamespace(returncode=0, stdout="")
        if op == "/setacvalueindex":
            ok = args[4] != self.fail_setting
            if ok:
                self.values[(args[2], args[3], args[4])] = int(args[5])
            return SimpleNamespace(returncode=0 if ok else 1, stdout="")
        if op == "/setactive":
            if args[2] not in self.plans:
                return SimpleNamespace(returncode=1, stdout="")
            self.active = args[2]
            return SimpleNamespace(returncode=0, stdout="")
        raise AssertionError(args)


class MaxPower(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp(prefix="ultrasuite-maxpower-test-")
        self.addCleanup(shutil.rmtree, d, True)
        self.state = os.path.join(d, "max_power.json")

    def test_creates_once_from_ultimate_sets_only_ac_values_and_activates(self):
        pc = FakePowercfg()
        r = mp.apply(True, run=pc, state_path=self.state)
        self.assertTrue(r["exists"] and r["active"])
        self.assertEqual(pc.plans[NEW], mp.PLAN_NAME)
        self.assertEqual(pc.active, NEW)
        self.assertEqual(len(pc.values), len(mp.AC_SETTINGS))
        self.assertTrue(all(c[1] != "/setdcvalueindex" for c in pc.calls), "battery values are never changed")
        self.assertEqual(pc.values[(NEW, mp.SUB_PROCESSOR, "0cc5b647-c1df-4637-891a-dec35c318583")], 100, "no core parking")
        self.assertEqual(r["failed_settings"], [])
        # second time: reused, not duplicated again
        mp.apply(True, run=pc, state_path=self.state)
        self.assertEqual(sum(1 for c in pc.calls if c[1] == "/duplicatescheme"), 1)

    def test_other_plans_are_never_modified(self):
        pc = FakePowercfg()
        mp.apply(True, run=pc, state_path=self.state)
        touched = {c[2] for c in pc.calls if c[1] in ("/setacvalueindex", "/changename")}
        self.assertEqual(touched, {NEW})
        self.assertEqual(pc.plans[BAL], "Balanced")
        self.assertEqual(pc.plans[ULT], "Ultimate Performance")

    def test_revert_restores_the_plan_in_use_before(self):
        pc = FakePowercfg()
        mp.apply(True, run=pc, state_path=self.state)
        r = mp.revert(run=pc, state_path=self.state)
        self.assertEqual(pc.active, BAL)
        self.assertFalse(r["active"])

    def test_falls_back_to_high_performance_when_ultimate_is_missing(self):
        pc = FakePowercfg(ultimate_available=False)
        mp.apply(True, run=pc, state_path=self.state)
        self.assertIn(["powercfg", "/duplicatescheme", mp.HIGH_PERFORMANCE], pc.calls)
        self.assertEqual(pc.plans[NEW], mp.PLAN_NAME)

    def test_create_without_activating_and_report_unsupported_settings(self):
        pc = FakePowercfg(fail_setting="0cc5b647-c1df-4637-891a-dec35c318583")
        r = mp.apply(False, run=pc, state_path=self.state)
        self.assertEqual(pc.active, BAL, "not activated")
        self.assertTrue(r["exists"] and not r["active"])
        self.assertEqual(r["failed_settings"], ["Core parking off (all cores unparked)"])

    def test_revert_without_a_saved_plan_is_refused(self):
        with self.assertRaises(LookupError):
            mp.revert(run=FakePowercfg(), state_path=self.state)


if __name__ == "__main__":
    unittest.main()
