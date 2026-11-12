"""Pure contract tests. No application, registry, user settings or services."""
from dataclasses import FrozenInstanceError
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "core" / "editions.py"
CONTRACT = (ROOT / "core" / "edition_profiles.json").read_text(encoding="utf-8")


def load_module(name="edition_contract_test"):
    spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


editions = load_module()


class EditionContractTests(unittest.TestCase):
    def document(self):
        return json.loads(CONTRACT)

    def test_public_identity_and_exact_dev_identity(self):
        p, d = editions.get_profile("Public"), editions.get_profile("Dev")
        self.assertEqual((p.product_name, p.main_binary, p.core_binary, p.bundle_identifier),
                         ("Ultra Suite", "Ultra Suite.exe", "ultrasuite-core.exe", "com.ultrasuite.app"))
        self.assertEqual((d.product_name, d.main_binary, d.core_binary, d.bundle_identifier),
                         ("Ultra Suite (Dev)", "Ultra Suite Dev.exe", "ultrasuite-dev-core.exe", "com.ultrasuite.dev"))
        self.assertEqual(d.app_names, {"suite": "Ultra Suite (Dev)", "sentinel": "PC Sentinel (Dev)",
                         "alarm": "Code Red Alarm (Dev)", "dock": "Ultra Dock (Dev)", "insights": "Insights (Dev)"})
        self.assertEqual(d.aumid("sentinel"), "UltraSuite.Dev.sentinel")
        self.assertEqual(d.shortcut("alarm"), "Code Red Alarm (Dev).lnk")
        self.assertFalse(d.runtime_ready)
        self.assertTrue(p.runtime_ready)

    def test_owned_port_table_and_external_sensor_separation(self):
        p, d = editions.get_profile(), editions.get_profile("dev")
        self.assertEqual(tuple(p.ports[k] for k in ("alarm", "sentinel", "audio", "addon", "insights_collector", "insights_dashboard")),
                         (8081, 8084, 8091, 8093, 8300, 8301))
        self.assertEqual(tuple(d.ports[k] for k in ("alarm", "sentinel", "audio", "addon", "insights_collector", "insights_dashboard")),
                         (18081, 18084, 18091, 18093, 18300, 18301))
        self.assertFalse(set(p.ports.values()) & set(d.ports.values()))
        self.assertEqual(editions.catalog().external_lhm_default_port, 8085)
        self.assertNotIn(8085, p.ports.values())
        self.assertNotIn(8085, d.ports.values())
        with self.assertRaises(ValueError):
            d.port("lhm")

    def test_dev_is_inert_and_monochrome(self):
        d = editions.get_profile("Dev")
        self.assertFalse(any(vars(d.capabilities).values()))
        self.assertEqual(vars(d.icons), {"colorway": "black", "glyph": "light", "outline": "light", "badge": "Dev"})

    def test_contract_is_deeply_immutable_and_copies_input(self):
        doc = self.document()
        c = editions.validate_catalog(doc)
        d = c.profile("Dev")
        with self.assertRaises(FrozenInstanceError):
            d.product_name = "changed"
        with self.assertRaises(FrozenInstanceError):
            d.capabilities.wake_enabled = True
        for mapping, key, value in ((c.profiles, "Dev", None), (d.ports, "alarm", 1), (d.app_names, "alarm", "changed")):
            with self.assertRaises(TypeError):
                mapping[key] = value
        doc["profiles"]["Dev"]["ports"]["alarm"] = 1
        self.assertEqual(d.port("alarm"), 18081)

    def test_import_and_selection_do_not_activate_any_runtime(self):
        with patch("pathlib.Path.mkdir", side_effect=AssertionError("directory creation")), \
             patch("socket.socket", side_effect=AssertionError("network access")), \
             patch("subprocess.Popen", side_effect=AssertionError("process launch")), \
             patch("os.getenv", side_effect=AssertionError("environment profile selection")):
            module = load_module("edition_contract_no_runtime_test")
            profile = module.resolve_profile("python.exe", False, ["--edition=Dev"])
            derived = profile.paths(Path("fixture-roaming"), Path("fixture-local"))
            self.assertEqual(derived.roaming, Path("fixture-roaming") / "UltraSuite-Dev")
            self.assertEqual(derived.local, Path("fixture-local") / "UltraSuite-Dev")
            self.assertEqual(derived.data, Path("fixture-local") / "UltraSuite-Dev" / "data")
            self.assertEqual(derived.install, Path("fixture-local") / "Ultra Suite (Dev)")

    def test_source_resolution_ignores_inherited_edition_overrides(self):
        with patch.dict("os.environ", {"ULTRASUITE_EDITION": "Dev", "ULTRASUITE_DATA_DIR": "fake-dev-path"}):
            self.assertEqual(editions.resolve_profile("python.exe", False).edition, "Public")
        self.assertEqual(editions.resolve_profile("python.exe", False, ["--open", "sentinel", "--edition", "dEv"]).edition, "Dev")
        self.assertEqual(editions.resolve_profile("python.exe", False, ["--edition=PUBLIC"]).edition, "Public")

    def test_frozen_identity_is_authoritative(self):
        for path, name in ((r"C:\fixture\ULTRASUITE-CORE.EXE", "Public"), ("/fixture/ultrasuite-dev-core.exe", "Dev")):
            with self.subTest(path=path):
                self.assertEqual(editions.resolve_profile(path, True).edition, name)
                self.assertEqual(editions.resolve_profile(path, True, ["--edition=" + name]).edition, name)
        for path, args in (("unknown.exe", []), ("Ultra Suite.exe", []), ("ultrasuite-core.exe", ["--edition=Dev"]),
                           ("ultrasuite-dev-core.exe", ["--edition=Public"])):
            with self.subTest(path=path, args=args), self.assertRaises(ValueError):
                editions.resolve_profile(path, True, args)

    def test_invalid_flags_and_identity_requests_fail_closed(self):
        for args in (["--edition"], ["--edition="], ["--edition=unknown"], ["--edition", "--open"],
                     ["--edition=Dev", "--edition=Dev"], ["--edition=Dev", "--edition", "Public"], [1], None, "--edition=Dev"):
            with self.subTest(args=args), self.assertRaises(ValueError):
                editions.resolve_profile("python.exe", False, args)
        for name in ("Development", " Public ", "", None, 1):
            with self.subTest(name=name), self.assertRaises(ValueError):
                editions.get_profile(name)
        for method in (editions.get_profile().app_name, editions.get_profile().aumid, editions.get_profile().shortcut, editions.get_profile().port):
            with self.assertRaises(ValueError):
                method("undeclared")

    def test_schema_types_unknown_fields_and_unsafe_components(self):
        changes = [(("schema",), True), (("schema",), 2), (("external_lhm_default_port",), 0),
                   (("profiles", "Dev", "runtime_ready"), True), (("profiles", "Dev", "edition"), "Public"),
                   (("profiles", "Dev", "ports", "alarm"), True), (("profiles", "Dev", "ports", "alarm"), 65536),
                   (("profiles", "Dev", "ports", "alarm"), 8085), (("profiles", "Dev", "ports", "alarm"), 8081),
                   (("profiles", "Dev", "ports", "alarm"), 18091), (("profiles", "Dev", "run_key"), "UltraSuite"),
                   (("profiles", "Dev", "roaming_dir_name"), "../UltraSuite"), (("profiles", "Dev", "local_dir_name"), "CON"),
                   (("profiles", "Dev", "main_binary"), "bad.bin"), (("profiles", "Dev", "bundle_identifier"), "com.Bad.dev"),
                   (("profiles", "Dev", "app_names", "alarm"), "Code Red Alarm"),
                   (("profiles", "Dev", "capabilities", "wake_enabled"), 1),
                   (("profiles", "Dev", "capabilities", "alarm_delivery_enabled"), True),
                   (("profiles", "Dev", "icons", "colorway"), "orange")]
        for keys, value in changes:
            doc = self.document()
            node = doc
            for key in keys[:-1]:
                node = node[key]
            node[keys[-1]] = value
            with self.subTest(keys=keys, value=value), self.assertRaises(ValueError):
                editions.validate_catalog(doc)
        for keys in ((), ("profiles", "Dev"), ("profiles", "Dev", "capabilities"), ("profiles", "Dev", "icons")):
            doc = self.document()
            node = doc
            for key in keys:
                node = node[key]
            node["unknown"] = "invalid"
            with self.subTest(keys=keys), self.assertRaises(ValueError):
                editions.validate_catalog(doc)

    def test_duplicate_json_keys_are_rejected_at_every_map_level(self):
        for needle, replacement in (( '"schema": 1', '"schema": 1, "schema": 1'),
                                     ('"alarm": 18081', '"alarm": 18081, "alarm": 18081'),
                                     ('"sentinel": "PC Sentinel (Dev)"', '"sentinel": "PC Sentinel (Dev)", "sentinel": "Other (Dev)"')):
            modified = CONTRACT.replace(needle, replacement, 1)
            self.assertNotEqual(modified, CONTRACT)
            with self.assertRaises(ValueError):
                editions.parse_catalog(modified)

    def test_future_apps_require_both_editions_and_valid_full_aumids(self):
        doc = self.document()
        doc["profiles"]["Public"]["app_names"]["planner"] = "School Planner"
        doc["profiles"]["Public"]["ports"]["planner"] = 8200
        with self.assertRaises(ValueError):
            editions.validate_catalog(doc)
        doc["profiles"]["Dev"]["app_names"]["planner"] = "School Planner (Dev)"
        doc["profiles"]["Dev"]["ports"]["planner"] = 18200
        c = editions.validate_catalog(doc)
        self.assertEqual(c.profile("Dev").aumid("planner"), "UltraSuite.Dev.planner")
        # Prefix alone is valid; the complete prefix.dot.app must fit 128 chars.
        doc["profiles"]["Dev"]["aumid_prefix"] = "D" * 120
        doc["profiles"]["Public"]["app_names"]["a" * 32] = "Future App"
        doc["profiles"]["Dev"]["app_names"]["a" * 32] = "Future App (Dev)"
        with self.assertRaises(ValueError):
            editions.validate_catalog(doc)

    def test_rust_embeds_same_json_and_dev_guard_precedes_legacy_side_effects(self):
        rust = (ROOT / "ultra_dock/src-tauri/src/edition.rs").read_text(encoding="utf-8")
        self.assertIn('include_str!("../../../core/edition_profiles.json")', rust)
        main = (ROOT / "ultra_dock/src-tauri/src/main.rs").read_text(encoding="utf-8").split("fn main() {", 1)[1]
        self.assertLess(main.index('cfg!(feature = "dev-edition")'), main.index("std::env::args()"))
        self.assertIn("Dev runtime integration not ready", main)


if __name__ == "__main__":
    unittest.main()
