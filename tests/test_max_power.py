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

DESKTOP = {"battery": False, "modern_standby": False, "arm64": False, "hybrid": False, "cpu_name": "AMD Ryzen 7 9800X3D 8-Core Processor", "physical_cores": 8}
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
        r = mp.apply(True, run=pc, state_path=self.state, device=DESKTOP)
        self.assertTrue(r["exists"] and r["active"])
        self.assertEqual(pc.plans[NEW], mp.PLAN_NAME)
        self.assertEqual(pc.active, NEW)
        self.assertEqual(len(pc.values), len(mp.AC_SETTINGS))
        self.assertTrue(all(c[1] != "/setdcvalueindex" for c in pc.calls), "battery values are never changed")
        self.assertEqual(pc.values[(NEW, mp.SUB_PROCESSOR, "0cc5b647-c1df-4637-891a-dec35c318583")], 100, "no core parking")
        self.assertEqual(r["failed_settings"], [])
        # second time: reused, not duplicated again
        mp.apply(True, run=pc, state_path=self.state, device=DESKTOP)
        self.assertEqual(sum(1 for c in pc.calls if c[1] == "/duplicatescheme"), 1)

    def test_other_plans_are_never_modified(self):
        pc = FakePowercfg()
        mp.apply(True, run=pc, state_path=self.state, device=DESKTOP)
        touched = {c[2] for c in pc.calls if c[1] in ("/setacvalueindex", "/changename")}
        self.assertEqual(touched, {NEW})
        self.assertEqual(pc.plans[BAL], "Balanced")
        self.assertEqual(pc.plans[ULT], "Ultimate Performance")

    def test_revert_restores_the_plan_in_use_before(self):
        pc = FakePowercfg()
        mp.apply(True, run=pc, state_path=self.state, device=DESKTOP)
        r = mp.revert(run=pc, state_path=self.state, device=DESKTOP)
        self.assertEqual(pc.active, BAL)
        self.assertFalse(r["active"])

    def test_falls_back_to_high_performance_when_ultimate_is_missing(self):
        pc = FakePowercfg(ultimate_available=False)
        mp.apply(True, run=pc, state_path=self.state, device=DESKTOP)
        self.assertIn(["powercfg", "/duplicatescheme", mp.HIGH_PERFORMANCE], pc.calls)
        self.assertEqual(pc.plans[NEW], mp.PLAN_NAME)

    def test_create_without_activating_and_report_unsupported_settings(self):
        pc = FakePowercfg(fail_setting="0cc5b647-c1df-4637-891a-dec35c318583")
        r = mp.apply(False, run=pc, state_path=self.state, device=DESKTOP)
        self.assertEqual(pc.active, BAL, "not activated")
        self.assertTrue(r["exists"] and not r["active"])
        self.assertEqual(r["failed_settings"], ["Core parking off (all cores unparked)"])

    def test_revert_without_a_saved_plan_is_refused(self):
        with self.assertRaises(LookupError):
            mp.revert(run=FakePowercfg(), state_path=self.state, device=DESKTOP)


if __name__ == "__main__":
    unittest.main()


class FakeMode:
    def __init__(self, available=True, mode="balanced"):
        self.available, self.mode, self.sets = available, mode, []

    def get(self):
        return {"available": self.available, "mode": self.mode if self.available else None}

    def put(self, mode):
        self.sets.append(mode)
        self.mode = mode
        return self.get()


CPUS = [
    ("AMD Ryzen 7 9800X3D 8-Core Processor", 8), ("AMD Ryzen 7 7800X3D 8-Core Processor", 8),
    ("AMD Ryzen 7 5800X3D 8-Core Processor", 8), ("AMD Ryzen 9 7950X3D 16-Core Processor", 16),
    ("AMD Ryzen 9 9950X3D 16-Core Processor", 16), ("AMD Ryzen 9 7900X3D 12-Core Processor", 12),
    ("AMD Ryzen 9 9900X3D 12-Core Processor", 12), ("AMD Ryzen 9 7945HX", 16), ("AMD Ryzen AI 9 HX 370", 12),
    ("AMD Ryzen 7 7840HS", 8), ("AMD Ryzen 5 5600X", 6), ("AMD Ryzen Threadripper 7980X", 64),
    ("12th Gen Intel(R) Core(TM) i7-12700K", 12), ("13th Gen Intel(R) Core(TM) i9-13980HX", 24),
    ("Intel(R) Core(TM) Ultra 7 155H", 16), ("Intel(R) Core(TM) Ultra 9 285K", 24), ("Intel(R) Core(TM) i7-9700K", 8),
    ("Intel(R) Celeron(R) N4020", 2), ("Intel(R) Xeon(R) w9-3495X", 56), ("Intel(R) Core(TM) i5-8250U", 4),
    ("Snapdragon(R) X Elite - X1E-80-100", 12), ("Snapdragon(R) X Plus - X1P-42-100", 8),
    ("Microsoft SQ3", 8), ("", 0), ("Unknown processor", 4),
]
CORE_COUNTS = [2, 4, 6, 8, 12, 16, 24, 64]


class Scenarios(unittest.TestCase):
    """About 3,200 simulated machines: every mix of battery, Modern Standby, Arm and hybrid cores, 25 real CPU names
    and 8 core counts. Each runs create/apply/undo against a simulated Windows and checks the safety rules."""

    def test_every_device_gets_a_safe_profile_and_a_working_undo(self):
        import itertools
        counts = {}
        d = tempfile.mkdtemp(prefix="ultrasuite-maxpower-scen-")
        self.addCleanup(shutil.rmtree, d, True)
        n = 0
        for battery, standby, arm, hybrid in itertools.product([False, True], repeat=4):
            for cpu, _ in CPUS:
                for cores in CORE_COUNTS:
                    n += 1
                    dev = {"battery": battery, "modern_standby": standby, "arm64": arm, "hybrid": hybrid,
                           "cpu_name": cpu, "physical_cores": cores}
                    prof = mp.choose_profile(dev)
                    counts[prof["kind"]] = counts.get(prof["kind"], 0) + 1
                    state = os.path.join(d, f"s{n}.json")
                    pc, mode = FakePowercfg(), FakeMode()
                    r = mp.apply(True, run=pc, state_path=state, device=dev, mode_get=mode.get, mode_set=mode.put)
                    written = {k[2] for k in pc.values}
                    self.assertTrue(all(c[1] != "/setdcvalueindex" for c in pc.calls), "battery values never changed")
                    if arm or standby or ("X3D" in cpu.upper() and cores > 8):
                        self.assertEqual(prof["method"], "power_mode", dev)
                        self.assertFalse(any(c[1] in ("/duplicatescheme", "/setacvalueindex", "/setactive") for c in pc.calls),
                                         f"no plan may be created or switched here: {dev}")
                        self.assertEqual(mode.mode, "performance")
                        self.assertTrue(r["active"])
                        mp.revert(run=pc, state_path=state, device=dev, mode_get=mode.get, mode_set=mode.put)
                        self.assertEqual(mode.mode, "balanced", "undo restores the previous power mode")
                        continue
                    self.assertEqual(prof["method"], "plan", dev)
                    self.assertEqual(pc.plans[NEW], mp.PLAN_NAME)
                    self.assertEqual(mode.sets, [], "the power mode is not touched when a plan is used")
                    self.assertEqual({c[2] for c in pc.calls if c[1] in ("/setacvalueindex", "/changename")}, {NEW},
                                     "only the new plan is modified")
                    if battery:
                        self.assertNotIn(mp.MIN_STATE, written, f"laptops keep Windows' minimum clock: {dev}")
                        self.assertNotIn(mp.CORE_PARKING, written, f"laptops keep core parking: {dev}")
                    if hybrid:
                        self.assertNotIn(mp.CORE_PARKING, written, f"hybrid CPUs keep core parking: {dev}")
                    if not battery and not hybrid:
                        self.assertEqual(len(written), len(mp.AC_SETTINGS), f"plain desktops get every setting: {dev}")
                    self.assertTrue({"be337238-0d82-4146-a960-4f3749d470c7", "36687f9e-e3a5-4dbf-b1dc-15eb381c6863"} <= written,
                                    "every plan raises boost and energy preference")
                    mp.revert(run=pc, state_path=state, device=dev, mode_get=mode.get, mode_set=mode.put)
                    self.assertEqual(pc.active, BAL, "undo restores the previous plan")
        self.assertEqual(n, 16 * len(CPUS) * len(CORE_COUNTS))
        self.assertTrue(all(counts.get(k, 0) > 0 for k in ("arm", "modern_standby", "x3d_dual", "laptop", "hybrid", "desktop")), counts)

    def test_owner_pc_gets_the_full_desktop_plan(self):
        self.assertEqual(mp.choose_profile(DESKTOP)["kind"], "desktop")
        self.assertEqual(mp.choose_profile(DESKTOP)["skipped"], [])

    def test_power_mode_unavailable_is_reported_not_faked(self):
        dev = dict(DESKTOP, modern_standby=True)
        with self.assertRaises(RuntimeError):
            mp.apply(True, run=FakePowercfg(), state_path=os.path.join(tempfile.gettempdir(), "x.json"),
                     device=dev, mode_get=FakeMode(available=False).get, mode_set=FakeMode().put)
