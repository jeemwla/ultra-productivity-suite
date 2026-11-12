"""Edition persistence on disposable folders only. No sampler threads or workloads."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pc_sentinel"))
from backend import benchmark as bm, max_power  # noqa: E402

# The module-level sampler loads on import. Keep even that read away from user data.
with tempfile.TemporaryDirectory() as _import_dir, patch.dict(os.environ, {
    "APPDATA": _import_dir, "ULTRASUITE_CONF": _import_dir,
}):
    from backend import storage_writers as sw  # noqa: E402


class EditionHistory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {"APPDATA": str(self.root), "ULTRASUITE_CONF": ""})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_public_fallback_and_dev_histories_never_cross_read_or_write(self):
        public = self.root / "UltraSuite"
        dev = self.root / "UltraSuite-Dev"
        bm.save_result({"single_mbps": 11, "label": "Public"})
        pub_sampler = sw.Sampler()
        pub_sampler.tracker.daily = {"2026-11-12": {"public": 2e6}}
        pub_sampler._save()
        public_bytes = {p.name: p.read_bytes() for p in public.iterdir()}

        os.environ["ULTRASUITE_CONF"] = str(dev)
        self.assertEqual(bm.load_results(), [], "Dev must not read or migrate Public history")
        dev_sampler = sw.Sampler()
        self.assertEqual(dev_sampler.tracker.daily, {})
        self.assertFalse(dev.exists(), "reads do not create or migrate data")
        bm.save_result({"single_mbps": 22, "label": "Dev"})
        dev_sampler.tracker.daily = {"2026-11-12": {"dev": 3e6}}
        dev_sampler._save()
        self.assertEqual({p.name: p.read_bytes() for p in public.iterdir()}, public_bytes)
        self.assertEqual([r["label"] for r in bm.load_results()], ["Dev"])
        self.assertEqual(sw.Sampler().tracker.daily, dev_sampler.tracker.daily)
        dev_bytes = {p.name: p.read_bytes() for p in dev.iterdir()}

        os.environ["ULTRASUITE_CONF"] = str(public)
        self.assertEqual([r["label"] for r in bm.load_results()], ["Public"])
        self.assertEqual(sw.Sampler().tracker.daily, pub_sampler.tracker.daily)
        bm.save_result({"single_mbps": 33, "label": "Public again"})
        # Existing instances retain their destination even if the test environment changes.
        dev_sampler._save()
        pub_sampler._save()
        self.assertEqual({p.name: p.read_bytes() for p in dev.iterdir()}, dev_bytes)

    def test_explicit_destinations_override_edition_default(self):
        os.environ["ULTRASUITE_CONF"] = str(self.root / "Dev")
        bench = self.root / "injected" / "benchmark.json"
        writers = self.root / "injected" / "writers.json"
        bm.save_result({"single_mbps": 9}, bench)
        self.assertEqual(bm.load_results(bench)[0]["single_mbps"], 9)
        sampler = sw.Sampler(writers)
        sampler.tracker.daily = {"2026-11-12": {"fixture": 4e6}}
        sampler._save()
        self.assertEqual(sw.Sampler(writers).tracker.daily, sampler.tracker.daily)
        self.assertFalse((self.root / "Dev").exists())

    def test_sampler_default_alone_separates_public_and_dev(self):
        public = sw.Sampler()
        public.tracker.daily = {"2026-11-12": {"public-only": 2e6}}
        public._save()
        before = public.store_path.read_bytes()
        os.environ["ULTRASUITE_CONF"] = str(self.root / "UltraSuite-Dev")
        dev = sw.Sampler()
        self.assertNotEqual(public.store_path, dev.store_path)
        self.assertEqual(dev.tracker.daily, {})
        dev.tracker.daily = {"2026-11-12": {"dev-only": 3e6}}
        dev._save()
        self.assertEqual(public.store_path.read_bytes(), before)
        self.assertEqual(sw.Sampler().tracker.daily, dev.tracker.daily)
        os.environ.pop("ULTRASUITE_CONF")
        self.assertEqual(sw.Sampler().tracker.daily, public.tracker.daily)

    def test_empty_unset_and_home_fallbacks(self):
        for conf in ("", None):
            if conf is None:
                os.environ.pop("ULTRASUITE_CONF", None)
            else:
                os.environ["ULTRASUITE_CONF"] = conf
            for name, filename in ((bm.results_path, "sentinel_benchmark.json"),
                                   (lambda: sw.Sampler().store_path, "sentinel_writers.json")):
                self.assertEqual(name(), self.root / "UltraSuite" / filename)
                with patch.dict(os.environ, {"APPDATA": ""}), patch.object(Path, "home", return_value=self.root):
                    self.assertEqual(name(), self.root / "AppData/Roaming/UltraSuite" / filename)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_max_power_recovery_record_stays_shared_and_untouched(self):
        record = self.root / "UltraSuite" / "max_power.json"
        record.parent.mkdir()
        record.write_bytes(b'{"original_plan":"fixture-only"}')
        before = record.read_bytes()
        for edition in ("UltraSuite", "UltraSuite-Dev"):
            os.environ["ULTRASUITE_CONF"] = str(self.root / edition)
            self.assertEqual(Path(max_power._state_path()), record)
            bm.save_result({"single_mbps": 1})
            sw.Sampler()._save()
            self.assertEqual(record.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
