"""Dev edition safety: separate identity, no alarm delivery, safe installer hooks and build, hub launchers."""
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))
import edition_runtime  # noqa: E402
import editions  # noqa: E402


def describe(*args, env=None):
    out = subprocess.run([sys.executable, str(ROOT / "core" / "suite_core.py"), *args, "--describe"],
                         capture_output=True, text=True, env=env, timeout=60, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


class IdentitySeparation(unittest.TestCase):
    def test_dev_core_uses_its_own_folders_and_ports_and_ignores_inherited_public_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = {k: v for k, v in os.environ.items() if k not in edition_runtime.DEV_BLOCKED_ENV}
            env = dict(base, APPDATA=os.path.join(tmp, "roaming"), LOCALAPPDATA=os.path.join(tmp, "local"),
                       ULTRASUITE_DATA=r"C:\public\data", ULTRASUITE_COLLECTOR="https://collector.invalid",
                       ULTRASUITE_OWNER="1", BRIDGE_TOKEN="public-token")
            dev = describe("--edition", "Dev", env=env)
            pub = describe(env=env)
            self.assertEqual(os.listdir(tmp), [], "--describe must not create folders")
        self.assertEqual(dev["edition"], "Dev")
        self.assertTrue(dev["settings_folder"].endswith("UltraSuite-Dev"))
        self.assertTrue(dev["data_folder"].endswith(os.path.join("UltraSuite-Dev", "data")))
        self.assertFalse(set(dev["ports"].values()) & set(pub["ports"].values()))
        self.assertEqual(set(dev["removed_env"]), {"ULTRASUITE_DATA", "ULTRASUITE_COLLECTOR", "ULTRASUITE_OWNER", "BRIDGE_TOKEN"})
        self.assertEqual(pub["removed_env"], [])
        self.assertFalse(dev["alarm_delivery"] or dev["self_update"] or dev["telemetry"])
        self.assertTrue(pub["alarm_delivery"] and pub["self_update"])

    def test_edition_environment_blanks_reporting_and_routes_the_speaker_helper(self):
        dev, pub = editions.get_profile("Dev"), editions.get_profile("Public")
        d = edition_runtime.edition_environment(dev, "conf", "data")
        self.assertEqual((d["ULTRASUITE_COLLECTOR"], d["ULTRASUITE_COLLECTOR_PUBLIC"]), ("", ""))
        self.assertEqual(d["HOST_AUDIO_BRIDGE_URL"], "http://127.0.0.1:18091")
        self.assertEqual(d["ULTRASUITE_ORIGIN_PORTS"], "18081,18084,18301")
        self.assertEqual((d["ULTRASUITE_SELF_UPDATE"], d["ULTRASUITE_ALARM_DELIVERY"]), ("0", "0"))
        p = edition_runtime.edition_environment(pub, "conf", "data")
        self.assertNotIn("ULTRASUITE_COLLECTOR", p)
        self.assertEqual(p["ULTRASUITE_ORIGIN_PORTS"], "8081,8084,8301,8098")


class DevAlarmGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["ULTRASUITE_DATA"] = cls.tmp.name
        backend = ROOT / "code_red_alarm" / "backend"
        spec = importlib.util.spec_from_file_location("dev_gate_alarm", backend / "__init__.py",
                                                      submodule_search_locations=[str(backend)])
        pkg = importlib.util.module_from_spec(spec)
        sys.modules["dev_gate_alarm"] = pkg
        spec.loader.exec_module(pkg)
        import importlib as il
        cls.alarm = il.import_module("dev_gate_alarm.main")
        edition_runtime.gate_alarm_delivery(cls.alarm)
        edition_runtime.add_alarm_delivery_guard(cls.alarm.app)

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("ULTRASUITE_DATA", None)
        cls.tmp.cleanup()

    def test_nothing_can_ring(self):
        import asyncio
        from fastapi.testclient import TestClient
        self.assertTrue(self.alarm.ULTRASUITE_DELIVERY_GATED)
        started, detail = asyncio.run(self.alarm._ring_helper({"id": "x", "code_red": False}, 5))
        self.assertFalse(started)
        self.assertIn("Dev edition", detail)
        with TestClient(self.alarm.app, base_url="http://127.0.0.1:18081") as client:
            headers = {"origin": "http://127.0.0.1:18081"}
            for path in ("/api/alarms/abc/test-ring", "/api/audio/test"):
                r = client.post(path, headers=headers)
                self.assertEqual(r.status_code, 403, path)
                self.assertIn("Dev edition", r.json()["detail"], "refused by the Dev gate, not by another check")
            self.assertIsNone(client.get("/api/next").json()["in_sec"], "no wake timer and no countdown in Dev")
            self.assertFalse(client.get("/api/ring").json()["is_ringing"])

    def test_gate_fails_closed_when_the_backend_changes(self):
        import types
        with self.assertRaises(RuntimeError):
            edition_runtime.gate_alarm_delivery(types.SimpleNamespace(scheduler_loop=lambda: None))


class DevSpeakerHelper(unittest.TestCase):
    def test_trigger_and_onescript_are_refused_but_other_requests_pass(self):
        calls = []

        class Base:
            def _auth(self):
                return True

            def _send(self, payload, status=200):
                calls.append((self.path, status, payload))

            def do_POST(self):
                calls.append((self.path, "base", None))

        class Siren:
            def start(self, *a):
                return True
        siren = Siren()
        handler_cls = edition_runtime.gated_bridge_handler(Base, siren)
        self.assertFalse(siren.start("id", 5, True))
        for path in ("/trigger", "/run-onescript", "/stop", "/output"):
            h = handler_cls()
            h.path = path
            h.do_POST()
        self.assertEqual([(c[0], c[1]) for c in calls],
                         [("/trigger", 403), ("/run-onescript", 403), ("/stop", "base"), ("/output", "base")])


class InstallerAndBuild(unittest.TestCase):
    def test_dev_hooks_never_touch_the_consumer_edition(self):
        text = (ROOT / "ultra_dock/src-tauri/installer-hooks-dev.nsh").read_text(encoding="utf-8")
        code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(";"))
        self.assertNotRegex(code, r"(?i)ultrasuite-core\.exe")
        self.assertNotRegex(code, r'(?i)"Ultra Suite\.exe"|\\Ultra Suite\.exe')
        self.assertNotRegex(code, r"(?i)APPDATA\\UltraSuite(?!-Dev)")
        self.assertNotRegex(code, r'(?i)Run" "UltraSuite"')
        self.assertNotIn("--uninstall-survey", code)
        self.assertNotIn("--event install", code)
        for name in ("Code Red Alarm (Dev)", "PC Sentinel (Dev)", "Ultra Dock (Dev)", "Insights (Dev)"):
            self.assertEqual(code.count(f"$SMPROGRAMS\\{name}.lnk"), 2, name)  # created and removed
        self.assertIn('"UltraSuiteDev"', code)

    def test_build_script_has_a_separate_dev_path(self):
        text = (ROOT / "build_release.ps1").read_text(encoding="utf-8")
        self.assertRegex(text, r'\[ValidateSet\("Public", "Dev"\)\]\[string\]\$Edition = "Public"')
        for needle in ("ultrasuite-dev-core", "--features\", \"dev-edition\"", "installer-hooks-dev.nsh",
                       "UltraSuite-Dev-Setup-$Version.exe", "CARGO_TARGET_DIR", "contains the consumer core",
                       "The Dev build changed tracked files"):
            self.assertIn(needle, text)
        self.assertIn('"../../build/dist/ultrasuite-core/" = $null', text)


class HubLaunchers(unittest.TestCase):
    def test_hub_launches_both_editions_with_its_own_ports(self):
        hub = (ROOT / "ultra_dock/ui/hub.html").read_text(encoding="utf-8")
        self.assertIn('invoke("edition_info")', hub)
        self.assertIn('invoke("launch_edition", { edition, name })', hub)
        self.assertIn("hubicons/dev/", hub)
        self.assertNotIn("const DEV = {", hub)
        main = (ROOT / "ultra_dock/src-tauri/src/main.rs").read_text(encoding="utf-8")
        fn = main.split("fn launch_edition", 1)[1].split("\n}\n", 1)[0]
        self.assertIn('matches!(name.as_str(), "hub" | "sentinel" | "alarm" | "insights")', fn)
        self.assertIn('cmd.args(["--open", &name])', fn)
        for path in ("ultra_dock/ui/hubicons/dev/sentinel.png", "ultra_dock/src-tauri/icons/dev/apps/dock/32.png"):
            self.assertTrue((ROOT / path).is_file(), path)


if __name__ == "__main__":
    unittest.main()
