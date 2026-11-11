"""Device identity must survive equal labels, changed ordering, and partial metadata."""
import copy
import itertools
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "pc_sentinel"))
from backend import lhm_collector as lhm, storage_health as sh


def group(name, temp, **extra):
    return dict(name=name, kind="storage", sensors=[dict(name="Temperature", type="Temperature", value=temp)], **extra)


class DriveIdentity(unittest.TestCase):
    def test_identical_names_are_not_guessed(self):
        drives = [dict(name="Samsung SSD 970", id="0", temp=None), dict(name="Samsung SSD 970", id="1", temp=None)]
        groups = [group("Samsung SSD 970", 35), group("Samsung SSD 970", 55)]
        for ds in itertools.permutations(drives):
            for gs in itertools.permutations(groups):
                with self.subTest(ds=[x["id"] for x in ds], gs=[x["sensors"][0]["value"] for x in gs]):
                    self.assertEqual([d["temp"] for d in sh.merge_lhm(copy.deepcopy(ds), gs)], [None, None])

    def test_one_group_cannot_be_reused_and_tie_cannot_be_resolved_by_order(self):
        drives = [dict(name="Samsung SSD 970", temp=None), dict(name="Samsung SSD 970", temp=None)]
        self.assertEqual([d["temp"] for d in sh.merge_lhm(drives, [group("Samsung SSD 970", 55)])], [None, None])
        drive = [dict(name="Samsung SSD 970", temp=None)]
        self.assertIsNone(sh.merge_lhm(drive, [group("Samsung SSD 970", 55), group("Samsung SSD 970", 35)])[0]["temp"])

    def test_serials_resolve_identical_models_in_any_order(self):
        drives = [dict(name="Samsung SSD 970", serial=" A 1 ", temp=None), dict(name="Samsung SSD 970", serial="B2", temp=None)]
        groups = [group("Samsung SSD 970", 35, serial="a1"), group("Samsung SSD 970", 55, serial="B2")]
        for ds in itertools.permutations(drives):
            for gs in itertools.permutations(groups):
                out = sh.merge_lhm(copy.deepcopy(ds), gs)
                self.assertEqual({sh._serial(x["serial"]): x["temp"] for x in out}, {"a1": 35, "b2": 55})

    def test_known_conflicting_serial_never_uses_name_fallback(self):
        out = sh.merge_lhm([dict(name="Samsung SSD 970", serial="one", temp=None)], [group("Samsung SSD 970", 55, serial="other")])
        self.assertIsNone(out[0]["temp"])

    def test_truncated_names_preserve_windows_values_and_match_each_device_once(self):
        drives = [dict(name="NVMe Samsung SSD 970", temp=42), dict(name="WD_BLACK SN7100 1TB", temp=None)]
        out = sh.merge_lhm(drives, [group("Samsung SSD 970 EVO Plus 1TB", 55), group("WD BLACK SN7100", 35)])
        self.assertEqual([x["temp"] for x in out], [42, 35])

    def test_weak_and_conflicting_models_remain_unknown(self):
        for left, right in (("Samsung SSD 970 EVO", "Samsung SSD 980 EVO"), ("Samsung SSD", "Samsung SSD 970"),
                            ("Samsung SSD 970 PRO", "Samsung SSD 970 EVO"), ("", ""), ("Drive", "Drive")):
            with self.subTest(left=left, right=right):
                self.assertIsNone(sh.merge_lhm([dict(name=left, temp=None)], [group(right, 55)])[0]["temp"])

    def test_nonfinite_sensor_values_do_not_break_telemetry(self):
        for value in (None, "bad", float("nan"), float("inf")):
            with self.subTest(value=value):
                self.assertIsNone(sh.merge_lhm([dict(name="Samsung SSD 970", temp=None)], [group("Samsung SSD 970", value)])[0]["temp"])

    def test_empty_inputs(self):
        self.assertEqual(sh.merge_lhm([], []), [])
        self.assertEqual(sh.merge_lhm([], [group("Samsung SSD 970", 55)]), [])


class CollectorIdentity(unittest.TestCase):
    def test_web_tree_keeps_identical_hardware_labels_separate(self):
        tree = {"Children": [dict(Text="Samsung SSD 970", HardwareId=f"/nvme/{i}", Children=[
            dict(Text="Temperature", SensorId=f"/nvme/{i}/temperature/0", Type="Temperature", Value=f"{35 + i * 20} °C")]) for i in (0, 1)]}
        groups = {}
        lhm._walk(tree, [], groups)
        self.assertEqual(set(groups), {"/nvme/0", "/nvme/1"})
        self.assertEqual([g["sensors"][0]["value"] for g in groups.values()], [35, 55])

    def test_wmi_keeps_identical_hardware_labels_separate(self):
        fixture = {"hw": [dict(Identifier=f"/nvme/{i}", Name="Samsung SSD 970") for i in (0, 1)],
                   "s": [dict(Parent=f"/nvme/{i}", Identifier=f"/nvme/{i}/temperature/0", Name="Temperature", SensorType="Temperature", Value=35+i*20) for i in (0, 1)]}
        with patch.dict(lhm._wmi_cache, {"at": 0, "groups": None}), patch("subprocess.run") as run:
            run.return_value.stdout = json.dumps(fixture)
            groups = lhm._wmi_groups()
        self.assertEqual({g["id"] for g in groups}, {"/nvme/0", "/nvme/1"})
        self.assertEqual([g["sensors"][0]["value"] for g in groups], [35, 55])
