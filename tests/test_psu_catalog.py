"""Offline catalog validation and fallback. No live hardware or network."""
import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("psu_catalog", ROOT / "pc_sentinel/backend/psu_catalog.py")
catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catalog)


class Catalog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = ROOT / "pc_sentinel/backend/psu_catalog.json"
        cls.document = json.loads(cls.bundle.read_text(encoding="utf-8"))

    def test_bundled_facts_and_owner_model(self):
        d = catalog.read(self.bundle)
        self.assertGreater(len(d["records"]), 1000)
        self.assertGreater(len({r["brand"] for r in d["records"]}), 60)
        owner = [r for r in d["records"] if r["brand"] == "Corsair" and r["series"] == "SF"
                 and 1000 in r["wattage"]["variants_w"] and r["form_factor"] == "SFX"]
        self.assertTrue(owner)
        self.assertTrue(all("tier" not in r and "notes" not in r for r in d["records"]))
        self.assertTrue(d["source"]["retrieved_utc"])

    def test_corrupt_missing_or_oversized_cache_falls_back(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "private") as td:
            cache = Path(td) / "catalog.json"
            for payload in (None, b'{', b'{}', b' ' * (catalog.MAX_BYTES + 1), b'\xff'):
                if payload is not None:
                    cache.write_bytes(payload)
                d = catalog.load(_cache=cache, _bundle=self.bundle)
                self.assertEqual(d["storage"], "bundled")
                self.assertEqual(len(d["records"]), len(self.document["records"]))

    def test_valid_cache_is_used_and_invalid_cache_does_not_hide_bundle_failure(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "private") as td:
            cache = Path(td) / "catalog.json"
            cache.write_text(json.dumps(self.document), encoding="utf-8")
            self.assertEqual(catalog.load(_cache=cache, _bundle=Path(td)/"missing")["storage"], "local_cache")
            cache.write_text('{}')
            with self.assertRaises(FileNotFoundError):
                catalog.load(_cache=cache, _bundle=Path(td)/"missing")

    def test_malformed_specifications_fail_closed(self):
        changes = [{"variants_w":[True]}, {"variants_w":[1000,1000]}, {"range":{"min_w":900,"max_w":800},"variants_w":[]},
                   {"range":{"min_w":500,"max_w":900},"variants_w":[500]}, {"range":None,"variants_w":[]},
                   {"range":{"min_w":500,"max_w":900,"extra":1},"variants_w":[]}, {"uncertain":"false"}]
        for change in changes:
            with self.subTest(change=change):
                d = copy.deepcopy(self.document)
                d["records"][0]["wattage"].update(change)
                with self.assertRaises(ValueError):
                    catalog.validate(d)
        d = copy.deepcopy(self.document)
        d["records"].append(d["records"][0])
        with self.assertRaises(ValueError):
            catalog.validate(d)


if __name__ == "__main__":
    unittest.main()
