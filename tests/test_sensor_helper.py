"""PC Sentinel's own sensor helper: source order and fallbacks, data.json parsing, ports and Dev isolation, the
guide's plain-language states, the alarm-gated Try again, and agreement between the C#, Python and NSIS copies of
the same identities and pins. No network, no hardware, no services, nothing elevated or installed."""
import asyncio
import json
import os
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pc_sentinel"))
sys.path.insert(0, os.path.join(ROOT, "core"))

from backend import lhm_collector as lc  # noqa: E402
from backend import sensor_helper as sh  # noqa: E402
from backend import sensor_setup as ss  # noqa: E402

HELPER_DIR = os.path.join(ROOT, "pc_sentinel", "sensor_helper")
HOOKS = os.path.join(ROOT, "ultra_dock", "src-tauri")


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def sensor(sid, name, stype, value):
    return {"id": 0, "Text": name, "Min": value, "Value": value, "Max": value, "SensorId": sid, "Type": stype,
            "RawValue": "1", "ImageURL": "images/transparent.png", "Children": []}


def tree(cpu_temp=True, extra_fans=0):
    """A document shaped like the helper's (and LibreHardwareMonitor's) data.json."""
    cpu_sensors = [sensor("/amdcpu/0/load/0", "CPU Total", "Load", "12.3 %")]
    if cpu_temp:
        cpu_sensors.append(sensor("/amdcpu/0/temperature/2", "Core (Tctl/Tdie)", "Temperature", "61.3 \u00b0C"))
    fans = [sensor(f"/lpc/nct6799d/0/fan/{i}", f"Fan #{i + 1}", "Fan", "1033 RPM") for i in range(extra_fans)]
    return {"id": 0, "Text": "Sensor", "Min": "Min", "Value": "Value", "Max": "Max", "ImageURL": "", "Children": [
        {"id": 1, "Text": "TESTPC", "Min": "", "Value": "", "Max": "", "ImageURL": "images_icon/computer.png", "Children": [
            {"id": 2, "Text": "AMD Ryzen 7 7800X3D", "HardwareId": "/amdcpu/0", "Min": "", "Value": "", "Max": "",
             "ImageURL": "images_icon/cpu.png", "Children": [
                 {"id": 3, "Text": "Temperatures", "Min": "", "Value": "", "Max": "", "Children": cpu_sensors}]},
            {"id": 4, "Text": "B650", "HardwareId": "/motherboard", "Min": "", "Value": "", "Max": "", "Children": [
                {"id": 5, "Text": "Nuvoton NCT6799D", "HardwareId": "/lpc/nct6799d/0", "Min": "", "Value": "", "Max": "",
                 "Children": [{"id": 6, "Text": "Fans", "Min": "", "Value": "", "Max": "", "Children": fans}]}]}]}]}


def health(edition="Public", device="ok", **extra):
    h = {"ok": True, "helper": "UltraSuiteSensorHelper", "version": "1.0.0", "edition": edition,
         "port": 8095 if edition == "Public" else 18095, "read_only": True, "elevated": True, "sensor_count": 300,
         "driver": {"installed": device != "not_installed", "version": "2.2.0.0", "device": device,
                    "device_error": 0, "memory_integrity": False}}
    h.update(extra)
    return h


class FakeResponse:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise ValueError(f"HTTP {self.status_code}")

    def json(self):
        return self.data


class FakeClient:
    """Routes GETs by URL; a missing route behaves like a closed port."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    async def get(self, url):
        self.calls.append(url)
        if url not in self.routes:
            raise httpx.ConnectError("refused")
        return FakeResponse(self.routes[url])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


PUB = "http://127.0.0.1:8095"


def routes(helper_tree=None, helper_health=None, lhm_tree=None):
    r = {}
    if helper_health is not None:
        r[PUB + "/health"] = helper_health
    if helper_tree is not None:
        r[PUB + "/data.json"] = helper_tree
    if lhm_tree is not None:
        r[lc.LHM_REMOTE_URL] = lhm_tree
    return r


class Env(unittest.TestCase):
    def setUp(self):
        self._env = patch.dict(os.environ, {"ULTRASUITE_EDITION": "Public"})
        self._env.start()
        for k in ("ULTRASUITE_SENSOR_HELPER_URL", "ULTRASUITE_SENSOR_HELPER_EXE", "ULTRASUITE_ALARM_PORT"):
            os.environ.pop(k, None)
        sh._last_retry["at"] = 0.0

    def tearDown(self):
        self._env.stop()


class Identities(Env):
    def test_python_matches_the_helper_source(self):
        cs = read("pc_sentinel", "sensor_helper", "src", "HelperEdition.cs")
        found = dict((m[0], {"service": m[1], "folder": m[3], "port": int(m[4])}) for m in re.findall(
            r'new HelperEdition\(\s*"(\w+)", "([^"]+)", "([^"]+)", "([^"]+)", (\d+)\)', cs))
        self.assertEqual(found, {k: dict(v) for k, v in sh.HELPERS.items()})

    def test_ports_never_collide_with_suite_or_lhm_ports(self):
        profiles = json.loads(read("core", "edition_profiles.json"))
        owned = {p for prof in profiles["profiles"].values() for p in prof["ports"].values()}
        helper_ports = {v["port"] for v in sh.HELPERS.values()}
        self.assertEqual(len(helper_ports), 2)
        self.assertFalse(helper_ports & owned)
        self.assertNotIn(profiles["external_lhm_default_port"], helper_ports)
        self.assertEqual(sh.HELPERS["Dev"]["port"], 10000 + sh.HELPERS["Public"]["port"])
        # The Dock treats any occupied owned port as a running core, so the helper port must not be an owned port.
        for prof in profiles["profiles"].values():
            self.assertNotIn("sensor_helper", prof["ports"])

    def test_dev_uses_its_own_helper_only(self):
        self.assertEqual(sh.helper_base_url(), "http://127.0.0.1:8095")
        os.environ["ULTRASUITE_EDITION"] = "dev"
        self.assertEqual(sh.edition(), "Dev")
        self.assertEqual(sh.helper_base_url(), "http://127.0.0.1:18095")
        self.assertFalse(sh.valid_health(health("Public")))
        self.assertTrue(sh.valid_health(health("Dev")))
        self.assertIn("--edition Dev", sh.provision_arguments(r"C:\x"))
        self.assertTrue(sh.result_dir().endswith("UltraSuite-Dev"))

    def test_only_a_read_only_helper_of_this_edition_counts(self):
        self.assertTrue(sh.valid_health(health()))
        for bad in (health("Dev"), dict(health(), helper="Other"), dict(health(), read_only=False), [], None):
            self.assertFalse(sh.valid_health(bad))

    def test_pins_agree_everywhere(self):
        pins = json.loads(read("pc_sentinel", "sensor_helper", "pins.json"))["pawnio"]
        cs = read("pc_sentinel", "sensor_helper", "src", "DriverStatus.cs")
        const = dict(re.findall(r'public const (?:string|long) (\w+) = "?([^";]+)"?;', cs))
        self.assertEqual(const["Version"], pins["version"])
        self.assertEqual(const["Url"], pins["url"])
        self.assertEqual(const["Sha256"], pins["sha256"])
        self.assertEqual(int(const["Size"]), pins["size"])
        self.assertEqual(const["InstallArguments"], "-install -silent")
        nsh = read("ultra_dock", "src-tauri", "installer-sensors.nsh")
        self.assertIn(f'!define ULTRA_PAWNIO_PIN "{pins["version"]}"', nsh)
        self.assertIn(pins["sha256"], read("pc_sentinel", "sensor_helper", "THIRD_PARTY_NOTICES.txt"))
        self.assertTrue(pins["url"].startswith("https://github.com/namazso/PawnIO.Setup/releases/download/"))

    def test_nuget_pins_are_exact_and_hashed(self):
        for p in json.loads(read("pc_sentinel", "sensor_helper", "pins.json"))["nuget"]:
            self.assertRegex(p["version"], r"^\d+(\.\d+){1,3}$")
            self.assertEqual(len(p["sha512"]), 88, p["id"])
            self.assertTrue(p["license"])

    def test_never_unrestricted_and_never_uninstalls_pawnio(self):
        code = []
        for name in ("installer-sensors.nsh", "installer-hooks.nsh", "installer-hooks-dev.nsh"):
            code += [(name, l) for l in read("ultra_dock", "src-tauri", name).splitlines() if not l.strip().startswith(";")]
        for name in os.listdir(os.path.join(HELPER_DIR, "src")):
            code += [(name, l) for l in read("pc_sentinel", "sensor_helper", "src", name).splitlines()
                     if not l.strip().startswith("//")]
        for name, line in code:
            self.assertNotIn("unrestricted", line.lower(), name)
            self.assertNotRegex(line, r"PawnIO.*-uninstall|uninstall\.exe", name)

    def test_installer_editions_stay_separate(self):
        pub = read("ultra_dock", "src-tauri", "installer-hooks.nsh")
        dev = read("ultra_dock", "src-tauri", "installer-hooks-dev.nsh")
        self.assertIn('ULTRA_SENSOR_SETUP "Public" "UltraSuiteSensorHelper" "Ultra Suite Sensor Helper"', pub)
        self.assertIn('ULTRA_SENSOR_REMOVE "Public" "UltraSuiteSensorHelper" ', pub)
        self.assertIn('ULTRA_SENSOR_SETUP "Dev" "UltraSuiteSensorHelperDev" "Ultra Suite Sensor Helper (Dev)"', dev)
        self.assertIn('ULTRA_SENSOR_REMOVE "Dev" "UltraSuiteSensorHelperDev" ', dev)
        self.assertNotIn("UltraSuiteSensorHelperDev", pub)
        self.assertNotRegex(dev, r'"UltraSuiteSensorHelper"')
        nsh = read("ultra_dock", "src-tauri", "installer-sensors.nsh")
        self.assertIn("$UpdateMode <> 1", nsh)          # an /UPDATE install keeps the helper
        self.assertIn("MB_DEFBUTTON1", nsh)             # Yes is the default answer
        self.assertIn("IfSilent ultra_sens_done", nsh)  # silent installs never surprise anyone with a prompt
        self.assertIn('"/SENSORS="', nsh)


class Parsing(Env):
    def test_helper_tree_parses_like_lhm(self):
        groups = lc.groups_from_tree(tree(extra_fans=2))
        cpu = next(g for g in groups if g["kind"] == "cpu")
        temp = next(s for s in cpu["sensors"] if s["type"] == "Temperature")
        self.assertEqual((cpu["name"], temp["value"], temp["unit"]), ("AMD Ryzen 7 7800X3D", 61.3, "\u00b0C"))
        board = next(g for g in groups if g["id"] == "/lpc/nct6799d/0")  # sub-hardware keeps its own group
        self.assertEqual((board["kind"], board["name"], len(board["sensors"])), ("motherboard", "B650", 2))
        self.assertEqual(board["sensors"][0]["value"], 1033.0)

    def test_dash_values_are_skipped(self):
        doc = tree(extra_fans=1)
        doc["Children"][0]["Children"][1]["Children"][0]["Children"][0]["Children"][0]["Value"] = "-"
        groups = lc.groups_from_tree(doc)
        self.assertFalse(any(g["id"] == "/lpc/nct6799d/0" for g in groups))

    def test_rejects_non_objects(self):
        for bad in ([], "x", None):
            with self.assertRaises(ValueError):
                lc.groups_from_tree(bad)


def run(coro):
    return asyncio.run(coro)


class SourceOrder(Env):
    def test_complete_helper_wins_without_asking_lhm(self):
        c = FakeClient(routes(tree(), health(), tree(extra_fans=20)))
        groups, sid, attempts, h = run(lc._http_sources(c))
        self.assertEqual(sid, "helper")
        self.assertNotIn(lc.LHM_REMOTE_URL, c.calls)
        self.assertEqual(h["edition"], "Public")
        self.assertEqual(attempts[0], {"id": "helper", "ok": True, "reason": None})

    def test_falls_back_to_users_lhm(self):
        groups, sid, attempts, h = run(lc._http_sources(FakeClient(routes(lhm_tree=tree()))))
        self.assertEqual(sid, "lhm_http")
        self.assertIsNone(h)
        self.assertEqual([a["id"] for a in attempts], ["helper", "lhm_http"])
        self.assertFalse(attempts[0]["ok"])

    def test_partial_helper_loses_only_to_a_richer_lhm(self):
        partial = tree(cpu_temp=False, extra_fans=3)
        sid = run(lc._http_sources(FakeClient(routes(partial, health(device="not_running"), tree(extra_fans=10)))))[1]
        self.assertEqual(sid, "lhm_http")
        sid = run(lc._http_sources(FakeClient(routes(partial, health(device="not_running"), tree(cpu_temp=False)))))[1]
        self.assertEqual(sid, "helper")

    def test_foreign_or_other_edition_helper_is_ignored(self):
        c = FakeClient(routes(tree(extra_fans=50), health("Dev"), tree()))
        groups, sid, attempts, h = run(lc._http_sources(c))
        self.assertEqual(sid, "lhm_http")
        self.assertNotIn(PUB + "/data.json", c.calls)

    def test_nothing_reachable(self):
        self.assertEqual(run(lc._http_sources(FakeClient({})))[:2], (None, None))

    def collect(self, http_result, wmi=None):
        from backend import native_sensors as ns, storage_health as st, sensor_setup
        native = {"groups": [{"id": "w", "name": "CPU", "kind": "cpu", "sensors": []}]}

        async def http(client):
            return http_result[0], http_result[1], list(http_result[2]), http_result[3]
        with patch.object(lc, "_http_sources", side_effect=http), \
                patch.object(lc, "_wmi_groups", return_value=wmi), \
                patch.object(lc.httpx, "AsyncClient", return_value=FakeClient({})), \
                patch.object(ns, "native_groups", return_value=native), \
                patch.object(ns, "device_info", return_value={}), \
                patch.object(ns, "detect_monitors", return_value={}), \
                patch.object(sensor_setup, "driver_facts", return_value={}), \
                patch.object(st, "drives", return_value=[]), \
                patch.object(st, "merge_lhm", return_value=[]), \
                patch.object(sh, "bundled_exe", return_value=None), \
                patch.object(sh, "service_installed", return_value=False), \
                patch.object(sh, "last_result", return_value={}):
            lc.forget_cached_state()
            return run(lc.collect_hardware_telemetry())

    def test_wmi_then_windows(self):
        wmi = [{"id": "/amdcpu/0", "name": "CPU", "kind": "cpu", "sensors": [
            {"id": "t", "name": "Package", "type": "Temperature", "value": 50.0, "min": None, "max": None, "unit": "\u00b0C"}]}]
        out = self.collect((None, None, [{"id": "helper", "ok": False, "reason": "ConnectError"}], None), wmi=wmi)
        self.assertEqual(out["source_id"], "lhm_wmi")
        self.assertEqual([a["id"] for a in out["sources"]], ["helper", "lhm_wmi"])
        out = self.collect((None, None, [{"id": "helper", "ok": False, "reason": "ConnectError"},
                                         {"id": "lhm_http", "ok": False, "reason": "ConnectError"}], None))
        self.assertEqual((out["source_id"], out["level"]), ("windows", "basic"))
        self.assertEqual([a["id"] for a in out["sources"]], ["helper", "lhm_http", "lhm_wmi", "windows"])
        self.assertFalse(out["helper"]["running"])

    def test_helper_source_reports_ok(self):
        res = (lc.groups_from_tree(tree()), "helper", [{"id": "helper", "ok": True, "reason": None}], health())
        out = self.collect(res)
        self.assertEqual(out["source"], "PC Sentinel sensor helper")
        self.assertEqual(out["sensor_setup"]["state"], "ok")
        self.assertTrue(out["helper"]["running"])
        self.assertEqual(out["helper"]["driver"], "ok")


CPU_T = [{"kind": "cpu", "sensors": [{"type": "Temperature", "value": 55}]}]
CPU_NONE = [{"kind": "cpu", "sensors": [{"type": "Load", "value": 10}]}]


class Guide(Env):
    def g(self, helper, groups=CPU_NONE, level="full", source="helper", monitors=None, drivers=None):
        return ss.guide(monitors or {}, drivers or {}, level, groups, helper=helper, source_id=source)

    def running(self, device="ok", **kw):
        h = {"running": True, "installed": True, "can_set_up": True, "driver": device, "sensor_count": 40}
        h.update(kw)
        return h

    def test_states(self):
        self.assertEqual(self.g(self.running(), CPU_T)["state"], "ok")
        missing = self.g(self.running("not_installed"))
        self.assertEqual((missing["state"], missing["retry"]), ("helper_driver_missing", True))
        offline = self.g(self.running("not_installed", last_setup={"pawnio": "download_failed"}))
        self.assertIn("couldn't be downloaded", offline["text"])
        self.assertEqual(self.g(self.running("not_running"))["state"], "helper_driver_blocked")
        self.assertEqual(self.g(self.running("not_running", last_setup={"restart": True}))["state"], "restart_needed")
        self.assertEqual(self.g(self.running("ok"))["state"], "cpu_unsupported")
        self.assertFalse(self.g(self.running("ok"))["retry"])
        self.assertEqual(self.g(self.running("ok", sensor_count=0))["state"], "helper_partial")
        stopped = self.g({"running": False, "installed": True, "can_set_up": True}, source="windows", level="basic")
        self.assertEqual(stopped["state"], "helper_stopped")
        fresh = self.g({"running": False, "installed": False, "can_set_up": True}, source="windows", level="basic")
        self.assertEqual((fresh["state"], fresh["retry"]), ("helper_missing", True))
        declined = self.g({"running": False, "installed": False, "can_set_up": True, "last_setup": {"elevated": False, "service": "x"}},
                          source="windows", level="basic")
        self.assertIn("permission wasn't given", declined["text"])

    def test_blocked_driver_never_suggests_weakening_security(self):
        text = self.g(self.running("not_running", memory_integrity=True))["text"]
        self.assertIn("Memory integrity is on", text)
        for phrase in ("turn off", "disable", "switch off"):
            self.assertNotIn(phrase, text.lower())

    def test_users_own_lhm_still_counts_and_old_states_stay(self):
        helper = {"running": False, "installed": False, "can_set_up": True}
        self.assertEqual(self.g(helper, CPU_T, source="lhm_http", monitors={"lhm_running": True},
                                drivers={"pawnio": True})["state"], "ok")
        # No helper information (running from source): the earlier LibreHardwareMonitor guidance is unchanged.
        self.assertEqual(ss.guide({}, {}, "basic", [])["state"], "lhm_missing")
        self.assertEqual(self.g({"running": False, "installed": False, "can_set_up": False}, [], "basic", "windows")["state"],
                         "lhm_missing")

    def test_texts_follow_the_owner_style(self):
        cases = [self.running(d) for d in ("not_installed", "not_running", "ok")] + [
            {"running": False, "installed": True, "can_set_up": True}, {"running": False, "can_set_up": True}]
        for h in cases:
            r = self.g(h, source="helper" if h.get("running") else "windows", level="full" if h.get("running") else "basic")
            self.assertNotIn("\u2014", r["title"] + r["text"])
            self.assertNotRegex(r["text"], r"\bI (think|recommend)\b")


class LastResult(Env):
    def test_reads_the_setup_outcome(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(sh.last_result(d), {})
            with open(os.path.join(d, sh.RESULT_FILE), "w", encoding="utf-8") as f:
                f.write("[result]\r\nelevated=1\r\npawnio=installed\r\npawnio_exit=3010\r\nrestart=1\r\nservice=running\r\nmessage=Restart\r\n")
            r = sh.last_result(d)
            self.assertEqual((r["pawnio"], r["restart"], r["elevated"], r["service"]), ("installed", True, True, "running"))
            with open(os.path.join(d, sh.RESULT_FILE), "w", encoding="utf-8") as f:
                f.write("not an ini")
            self.assertEqual(sh.last_result(d), {})


class Retry(Env):
    def setUp(self):
        super().setUp()
        self.calls = []

    def launcher(self, exe, directory):
        self.calls.append((exe, directory))
        return True

    async def no_alarm(self):
        return None

    def test_needs_a_bundled_helper(self):
        with patch.object(sh, "bundled_exe", return_value=None):
            r = run(sh.retry(alarm_check=self.no_alarm, launcher=self.launcher))
        self.assertFalse(r["started"])
        self.assertEqual(self.calls, [])

    def test_alarm_blocks_setup(self):
        async def ringing():
            return "An alarm is ringing. Set up sensors after you stop it."
        with patch.object(sh, "bundled_exe", return_value=r"C:\x\UltraSuiteSensorHelper.exe"):
            r = run(sh.retry(alarm_check=ringing, launcher=self.launcher))
        self.assertFalse(r["started"])
        self.assertIn("alarm", r["message"])
        self.assertEqual(self.calls, [])

    def test_starts_once_then_cools_down(self):
        with patch.object(sh, "bundled_exe", return_value=r"C:\x\UltraSuiteSensorHelper.exe"):
            first = run(sh.retry(now=1000.0, alarm_check=self.no_alarm, launcher=self.launcher))
            second = run(sh.retry(now=1010.0, alarm_check=self.no_alarm, launcher=self.launcher))
            third = run(sh.retry(now=1100.0, alarm_check=self.no_alarm, launcher=self.launcher))
        self.assertTrue(first["started"])
        self.assertFalse(second["started"])
        self.assertTrue(third["started"])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[0][1], sh.result_dir())

    def test_launch_uses_runas_with_fixed_arguments(self):
        seen = []

        def shell(hwnd, verb, exe, params, cwd, show):
            seen.append((verb, exe, params, show))
            return 42
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(sh.launch_setup(r"C:\x\h.exe", d, shell_execute=shell))
        verb, exe, params, show = seen[0]
        self.assertEqual((verb, exe, show), ("runas", r"C:\x\h.exe", 0))
        self.assertTrue(params.startswith("--provision --edition Public --pawnio --result "))
        self.assertIn(sh.RESULT_FILE, params)
        self.assertNotIn("unrestricted", params)
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(sh.launch_setup(r"C:\x\h.exe", d, shell_execute=lambda *a: 5))

    def alarm(self, routes_by_port):
        class Client(FakeClient):
            async def get(self, url):
                port = int(url.split(":")[2].split("/")[0])
                self.calls.append(url)
                if port not in routes_by_port:
                    raise httpx.ConnectError("refused")
                nxt, ring = routes_by_port[port]
                return FakeResponse(nxt if url.endswith("/api/next") else ring)
        return run(sh.alarm_block_reason(lambda: Client({})))

    def test_alarm_gate(self):
        quiet = ({"in_sec": None}, {"is_ringing": False})
        self.assertIsNone(self.alarm({8081: quiet}))
        self.assertIn("ringing", self.alarm({8081: ({"in_sec": None}, {"is_ringing": True})}))
        self.assertIn("30 minutes", self.alarm({8081: ({"in_sec": 600}, {"is_ringing": False})}))
        self.assertIn("sensor setup", self.alarm({}))  # unknown state blocks
        os.environ["ULTRASUITE_EDITION"] = "Dev"
        self.assertIsNone(self.alarm({18081: quiet}))  # no consumer alarm service running is fine for Dev
        self.assertIn("ringing", self.alarm({18081: quiet, 8081: ({"in_sec": None}, {"is_ringing": True})}))


class Packaging(unittest.TestCase):
    def test_manifest_and_build_include_the_helper(self):
        manifest = json.loads(read("tools", "package_manifest.json"))["files"]
        self.assertIn("pc_sentinel/backend/sensor_helper.py", manifest)
        build = read("build_release.ps1")
        self.assertIn("build_sensor_helper.ps1", build)
        self.assertIn("sensor-helper", build)


if __name__ == "__main__":
    unittest.main()
