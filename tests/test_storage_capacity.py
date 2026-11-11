import os
import sys
import unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "pc_sentinel"))
from backend.storage_health import capacity_details


class StorageCapacity(unittest.TestCase):
    def test_device_and_filesystem_capacity_are_distinct(self):
        value = capacity_details(dict(size=1_000_204_886_016, volumes=[dict(mount="C:", filesystem="NTFS", size=999_027_634_176, free=612_863_193_088)]))
        self.assertEqual(value["size_gib"], 931.51)
        volume = value["volumes"][0]
        self.assertLess(volume["size_bytes"], value["size_bytes"])
        self.assertEqual(volume["used_bytes"] + volume["free_bytes"], volume["size_bytes"])

    def test_full_and_empty_volumes_keep_zero(self):
        for free in (0, 2**30):
            with self.subTest(free=free):
                volume = capacity_details(dict(volumes=[dict(size=2**30, free=free)]))["volumes"][0]
                self.assertEqual(volume["free_bytes"], free)
                self.assertEqual(volume["used_bytes"], 2**30-free)

    def test_unknown_invalid_and_inconsistent_values_are_not_zero(self):
        for bad in (None, "bad", -1, True, float("inf")):
            with self.subTest(bad=bad):
                value = capacity_details(dict(size=bad, volumes=[dict(size=100, free=bad)]))
                self.assertIsNone(value["size_bytes"])
                self.assertIsNone(value["volumes"][0]["free_bytes"])
                self.assertIsNone(value["volumes"][0]["used_bytes"])
        volume = capacity_details(dict(volumes=[dict(size=100, free=101)]))["volumes"][0]
        self.assertIsNone(volume["free_bytes"])

    def test_no_volume_mapping_is_left_unknown(self):
        value = capacity_details(dict(size=1_000_000_000_000))
        self.assertEqual(value["volumes"], [])
        self.assertEqual(value["size_gib"], 931.32)
