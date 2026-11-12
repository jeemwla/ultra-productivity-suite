"""Temporary edition stores, loaded without importing running applications."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_module(relative):
    spec = importlib.util.spec_from_file_location("edition_path_fixture", ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EditionPaths(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"APPDATA": str(self.root / "roaming"),
                                         "LOCALAPPDATA": str(self.root / "local")}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.prefs = load_module("pc_sentinel/backend/preferences.py")
        self.catalog = load_module("pc_sentinel/backend/psu_catalog.py")
        self.context = load_module("pc_sentinel/backend/power_context.py")
        self.feedback = load_module("core/feedback.py")

    def assert_store_paths(self, folder):
        self.assertEqual(self.prefs.path(), folder / "sentinel_preferences.json")
        self.assertEqual(self.catalog.cache_path(), folder / "psu_catalog.json")
        self.assertEqual(self.context._path(), folder / "sentinel_power.json")
        self.feedback.anon_id()
        self.assertTrue((folder / "id.txt").is_file())

    def test_public_fallback_is_unchanged_when_conf_unset_or_empty(self):
        for conf in (None, ""):
            if conf is not None:
                os.environ["ULTRASUITE_CONF"] = conf
            self.assert_store_paths(self.root / "roaming" / "UltraSuite")

    def test_explicit_edition_conf_is_used_without_extra_ultrasuite_subfolder(self):
        folder = self.root / "custom dev conf"
        os.environ["ULTRASUITE_CONF"] = str(folder)
        self.assert_store_paths(folder)
        self.assertFalse((self.root / "roaming" / "UltraSuite").exists())

    def test_home_fallback_for_sentinel_paths_is_preserved(self):
        os.environ.pop("APPDATA")
        with patch.object(Path, "home", return_value=self.root):
            folder = self.root / "AppData" / "Roaming" / "UltraSuite"
            self.assertEqual(self.prefs.path(), folder / "sentinel_preferences.json")
            self.assertEqual(self.catalog.cache_path(), folder / "psu_catalog.json")
            self.assertEqual(self.context._path(), folder / "sentinel_power.json")

    def test_real_persistence_remains_separate_between_public_and_dev(self):
        bundle = json.loads((ROOT / "pc_sentinel/backend/psu_catalog.json").read_text(encoding="utf-8"))
        folders = [self.root / "roaming" / name for name in ("UltraSuite", "UltraSuite-Dev")]
        ids = []
        for index, folder in enumerate(folders):
            with patch.dict(os.environ, {"ULTRASUITE_CONF": str(folder)}):
                self.prefs.save({"router_ping": bool(index)})
                self.context.save_context({"rated_watts": 500 + index * 250})
                ids.append(self.feedback.anon_id())
                fixture = {**bundle, "records": bundle["records"][index:index + 1]}
                self.catalog.cache_path().write_text(json.dumps(fixture), encoding="utf-8")
        self.assertNotEqual(*ids)
        for index, folder in enumerate(folders):
            with patch.dict(os.environ, {"ULTRASUITE_CONF": str(folder)}):
                self.assertEqual(self.prefs.load()["router_ping"], bool(index))
                self.assertEqual(self.context.load_context()["rated_watts"], 500 + index * 250)
                self.assertEqual(self.feedback.anon_id(), ids[index])
                result = self.catalog.load()
                self.assertEqual(result["storage"], "local_cache")
                self.assertEqual(result["records"], self.catalog.validate({**bundle, "records": bundle["records"][index:index + 1]})["records"])

    def test_explicit_test_path_overrides_conf_for_power_context(self):
        os.environ["ULTRASUITE_CONF"] = str(self.root / "dev")
        destination = self.root / "fixture.json"
        self.assertEqual(self.context._path(destination), destination)
        self.context.save_context({"rated_watts": 750}, _test_path=destination)
        self.assertTrue(destination.is_file())
        self.assertFalse((self.root / "dev").exists())


if __name__ == "__main__":
    unittest.main()
