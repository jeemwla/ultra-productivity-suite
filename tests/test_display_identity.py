"""Read-only display/hostname fixtures. No display changes or hardware probes."""
import ctypes
import inspect
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pc_sentinel"))
from backend import display_identity as identity
from backend import native_sensors, system_tools

GUID = "{e6f07b5f-ee97-4a90-b076-33f57bf4eaa7}"
PATH_A = r"\\?\DISPLAY#AAA1234#5&example&0&UID10#" + GUID
PATH_B = r"\\?\DISPLAY#BBB5678#5&example&0&UID20#" + GUID
DISPLAY_A, DISPLAY_B = r"\\.\DISPLAY1", r"\\.\DISPLAY2"


def target(device=DISPLAY_A, path=PATH_A, model="Example Monitor A", target_id=10):
    return {"gdi_device": device, "device_path": path, "friendly_name": model,
            "friendly_from_edid": True, "adapter_id": "00000000:00000001", "target_id": target_id,
            "source_id": 0, "connector_instance": 1}


def edid(name="Example-Panel"):
    value = bytearray(128)
    value[:8] = b"\x00\xff\xff\xff\xff\xff\xff\x00"
    value[54:59] = b"\x00\x00\x00\xfc\x00"
    value[59:72] = (name.encode("ascii") + b"\n").ljust(13, b" ")[:13]
    value[127] = (-sum(value[:127])) % 256
    return bytes(value)


class DisplayMapping(unittest.TestCase):
    def test_reversed_enumeration_joins_by_gdi_source_not_order(self):
        records = [target(DISPLAY_B, PATH_B, "Panel B", 20), target()]
        with patch.object(identity, "_read_edid", side_effect=AssertionError("unnecessary registry read")):
            a = identity.describe_display(DISPLAY_A, [{"device_path": PATH_A}], records)
            b = identity.describe_display(DISPLAY_B, [{"device_path": PATH_B}], records)
        self.assertEqual(a["model"], "Example Monitor A")
        self.assertEqual(b["model"], "Panel B")
        self.assertEqual(a["monitors"][0]["target_id"], 10)

    def test_case_insensitive_exact_interfaces_and_sources(self):
        result = identity.describe_display(DISPLAY_A.lower(), [{"device_path": PATH_A.upper()}], [target()])
        self.assertEqual(result["model"], "Example Monitor A")

    def test_identical_model_names_keep_distinct_target_ids(self):
        records = [target(), target(DISPLAY_B, PATH_B, "Example Monitor A", 20)]
        a = identity.describe_display(DISPLAY_A, [{"device_path": PATH_A}], records)
        b = identity.describe_display(DISPLAY_B, [{"device_path": PATH_B}], records)
        self.assertEqual(a["model"], b["model"])
        self.assertNotEqual(a["monitors"][0]["target_id"], b["monitors"][0]["target_id"])

    def test_mirrored_source_returns_all_targets_without_selecting_one(self):
        records = [target(), target(DISPLAY_A, PATH_B, "Panel B", 20)]
        result = identity.describe_display(DISPLAY_A, [{"device_path": PATH_A}, {"device_path": PATH_B}], records)
        self.assertIsNone(result["model"])
        self.assertEqual(result["identity_status"], "multiple_targets")
        self.assertEqual({row["target_id"] for row in result["monitors"]}, {10, 20})

    def test_topology_disagreement_withholds_previously_known_model(self):
        result = identity.describe_display(DISPLAY_A, [{"device_path": PATH_B}], [target()])
        self.assertIsNone(result["model"])
        self.assertEqual(result["identity_status"], "topology_changed")

    def test_source_name_failure_can_join_only_an_exact_interface(self):
        record = target()
        record["gdi_device"] = None
        matched = identity.describe_display(DISPLAY_A, [{"device_path": PATH_A}], [record])
        self.assertEqual(matched["model"], "Example Monitor A")
        with patch.object(identity, "_read_edid", return_value=None):
            unmatched = identity.describe_display(DISPLAY_B, [{"device_path": PATH_B}], [record])
        self.assertIsNone(unmatched["model"])

    def test_generic_or_unconfirmed_names_do_not_become_models(self):
        for name, confirmed in (("Generic PnP Monitor", True), ("Driver Friendly Name", False), (None, True)):
            with self.subTest(name=name, confirmed=confirmed):
                record = target(model=name)
                record["friendly_from_edid"] = confirmed
                with patch.object(identity, "_read_edid", return_value=None):
                    result = identity.describe_display(DISPLAY_A, [], [record])
                self.assertIsNone(result["model"])
                self.assertEqual(result["identity_status"], "unknown")

    def test_fallback_uses_only_the_current_monitor_interface_edid(self):
        with patch.object(identity, "_read_edid", return_value="Mapped Panel") as read:
            result = identity.describe_display(DISPLAY_A, [{"device_path": PATH_A, "description": "Generic PnP Monitor"}], [])
        read.assert_called_once_with(PATH_A)
        self.assertEqual(result["model"], "Mapped Panel")
        self.assertEqual(result["monitors"][0]["mapping"], "gdi_monitor_interface")

    def test_unmapped_descriptions_and_gpu_names_are_not_guessed(self):
        result = identity.describe_display(DISPLAY_A, [{"description": "Graphics Adapter"}], [])
        self.assertIsNone(result["model"])
        self.assertEqual(result["name"], "Unknown monitor")

    def test_conflicting_duplicate_target_records_are_ambiguous(self):
        result = identity.describe_display(DISPLAY_A, [], [target(), target(model="Different Model")])
        self.assertEqual(len(result["monitors"]), 1)
        self.assertEqual(result["identity_status"], "ambiguous")
        self.assertIsNone(result["model"])

    def test_monitor_interfaces_are_not_exposed_in_default_rows(self):
        result = identity.describe_display(DISPLAY_A, [{"device_path": PATH_A}], [target()])
        self.assertNotIn(PATH_A, json.dumps(result))
        self.assertNotIn("device_path", result["monitors"][0])


class EdidValidation(unittest.TestCase):
    def test_checked_name_descriptor_is_returned(self):
        self.assertEqual(identity.edid_model(edid()), "Example-Panel")

    def test_invalid_header_checksum_length_and_nontext_are_unknown(self):
        bad_checksum = bytearray(edid()); bad_checksum[30] ^= 1
        bad_header = bytearray(edid()); bad_header[0] = 1
        for data in (None, "not bytes", b"", edid()[:127], bad_checksum, bad_header):
            self.assertIsNone(identity.edid_model(data))

    def test_generic_and_conflicting_name_descriptors_are_unknown(self):
        self.assertIsNone(identity.edid_model(edid("Monitor")))
        value = bytearray(edid("First Panel"))
        value[72:90] = edid("Second Panel")[54:72]
        value[127] = 0; value[127] = (-sum(value[:127])) % 256
        self.assertIsNone(identity.edid_model(value))

    def test_registry_lookup_is_read_only_and_exact(self):
        handle = Mock()
        handle.__enter__ = Mock(return_value=handle)
        handle.__exit__ = Mock(return_value=False)
        registry = SimpleNamespace(HKEY_LOCAL_MACHINE=1, KEY_READ=2, REG_BINARY=3,
                                   OpenKey=Mock(return_value=handle), QueryValueEx=Mock(return_value=(edid(), 3)))
        with patch.object(identity, "IS_WIN", True), patch.dict(sys.modules, {"winreg": registry}):
            self.assertEqual(identity._read_edid(PATH_A), "Example-Panel")
        self.assertEqual(registry.OpenKey.call_args.args,
                         (1, r"SYSTEM\CurrentControlSet\Enum\DISPLAY\AAA1234\5&example&0&UID10\Device Parameters", 0, 2))
        registry.QueryValueEx.assert_called_once_with(handle, "EDID")

    def test_unknown_or_malicious_interface_path_never_opens_registry(self):
        registry = SimpleNamespace(OpenKey=Mock(side_effect=AssertionError("must not open")))
        with patch.object(identity, "IS_WIN", True), patch.dict(sys.modules, {"winreg": registry}):
            for path in (None, "AAA1234", PATH_A.replace("AAA1234", r"..\Other"), PATH_A.replace(GUID, "{wrong-guid}")):
                self.assertIsNone(identity._read_edid(path))
        registry.OpenKey.assert_not_called()


class NativeQuery(unittest.TestCase):
    def api(self, *, query_results=(0,), source_ok=True, target_ok=True, available=True):
        def sizes(flags, paths, modes):
            self.assertEqual(flags, 2)
            paths._obj.value, modes._obj.value = 1, 2
            return 0
        results = iter(query_results)
        def query(flags, path_count, paths, mode_count, modes, topology):
            self.assertIsNone(topology)
            result = next(results)
            paths[0].flags = 1
            paths[0].targetInfo.targetAvailable = int(available)
            paths[0].targetInfo.id = 37
            paths[0].sourceInfo.id = 4
            return result
        def info(header_pointer):
            header = ctypes.cast(header_pointer, ctypes.POINTER(identity._HEADER)).contents
            if header.type == 1:
                source = ctypes.cast(header_pointer, ctypes.POINTER(identity._SOURCE_NAME)).contents
                source.viewGdiDeviceName = DISPLAY_B
                return 0 if source_ok else 5
            target_name = ctypes.cast(header_pointer, ctypes.POINTER(identity._TARGET_NAME)).contents
            target_name.flags = 1
            target_name.monitorFriendlyDeviceName = "Actual Target Panel"
            target_name.monitorDevicePath = PATH_B
            return 0 if target_ok else 5
        return SimpleNamespace(GetDisplayConfigBufferSizes=Mock(side_effect=sizes),
                               QueryDisplayConfig=Mock(side_effect=query), DisplayConfigGetDeviceInfo=Mock(side_effect=info))

    @unittest.skipUnless(os.name == "nt", "Windows WCHAR ABI")
    def test_windows_abi_sizes(self):
        self.assertEqual(ctypes.sizeof(identity._PATH_INFO), 72)
        self.assertEqual(ctypes.sizeof(identity._MODE_INFO), 64)
        self.assertEqual(ctypes.sizeof(identity._SOURCE_NAME), 84)
        self.assertEqual(ctypes.sizeof(identity._TARGET_NAME), 420)

    def test_active_path_source_and_target_ids_are_used(self):
        api = self.api()
        result = identity._query_targets(api)
        self.assertEqual(result["status"], "ok")
        row = result["targets"][0]
        self.assertEqual((row["gdi_device"], row["source_id"], row["target_id"]), (DISPLAY_B, 4, 37))
        self.assertEqual(row["device_path"], PATH_B)

    def test_hotplug_buffer_change_retries_boundedly(self):
        api = self.api(query_results=(122, 0))
        self.assertEqual(identity._query_targets(api)["status"], "ok")
        self.assertEqual(api.QueryDisplayConfig.call_count, 2)
        api = self.api(query_results=(122, 122, 122))
        self.assertEqual(identity._query_targets(api)["status"], "topology_changed")
        self.assertEqual(api.QueryDisplayConfig.call_count, 3)

    def test_removed_target_is_not_used_even_on_an_active_path(self):
        self.assertEqual(identity._query_targets(self.api(available=False))["targets"], [])

    def test_failed_source_or_target_query_retains_uncertainty(self):
        source_failed = identity._query_targets(self.api(source_ok=False))["targets"][0]
        self.assertIsNone(source_failed["gdi_device"])
        target_failed = identity._query_targets(self.api(target_ok=False))["targets"][0]
        self.assertIsNone(target_failed["friendly_name"])
        self.assertFalse(target_failed["friendly_from_edid"])

    def test_unavailable_api_and_nonwindows_fall_back(self):
        with patch.object(identity, "IS_WIN", False):
            self.assertEqual(identity.active_targets(), {"status": "unsupported", "targets": []})
        with patch.object(identity, "IS_WIN", True), patch.object(ctypes, "WinDLL", side_effect=OSError, create=True):
            self.assertEqual(identity.active_targets()["status"], "unavailable")


class ComputerName(unittest.TestCase):
    def test_physical_windows_hostname_is_used_without_env_or_cpu_guess(self):
        def name_ex(kind, buffer, size):
            self.assertEqual(kind, 5)
            if buffer is None:
                size._obj.value = 14
                return 0
            buffer.value = "TEST-WORK-PC"
            return 1
        kernel = SimpleNamespace(GetComputerNameExW=Mock(side_effect=name_ex))
        with patch.object(identity, "IS_WIN", True), patch.object(ctypes, "WinDLL", return_value=kernel, create=True), patch.dict(os.environ, {"COMPUTERNAME": "WRONG-ENV-NAME"}):
            result = identity.computer_identity()
        self.assertEqual(result, {"hostname": "TEST-WORK-PC", "hostname_source": "GetComputerNameExW"})

    def test_netbios_api_is_a_labeled_fallback(self):
        def netbios(buffer, size):
            buffer.value = "FALLBACK-PC"
            return 1
        kernel = SimpleNamespace(GetComputerNameExW=Mock(return_value=0), GetComputerNameW=Mock(side_effect=netbios))
        with patch.object(identity, "IS_WIN", True), patch.object(ctypes, "WinDLL", return_value=kernel, create=True):
            self.assertEqual(identity.computer_identity(), {"hostname": "FALLBACK-PC", "hostname_source": "GetComputerNameW"})

    def test_failed_api_is_unknown_without_cpu_or_environment_substitution(self):
        with patch.object(identity, "IS_WIN", True), patch.object(ctypes, "WinDLL", side_effect=OSError, create=True), patch.dict(os.environ, {"COMPUTERNAME": "UNVERIFIED"}):
            self.assertIsNone(identity.computer_identity()["hostname"])

    def test_device_info_adds_hostname_without_replacing_cpu(self):
        raw = json.dumps({"cpu": {"Name": "Fixture CPU"}, "system": {}, "gpus": []})
        with patch.object(native_sensors, "_info", None), patch.object(native_sensors, "_ps", return_value=raw), patch.object(identity, "computer_identity", return_value={"hostname": "FIXTURE-PC", "hostname_source": "test"}):
            value = native_sensors.device_info()
        self.assertEqual(value["hostname"], "FIXTURE-PC")
        self.assertEqual(value["cpu"], "Fixture CPU")


class DisplayIntegration(unittest.TestCase):
    def test_display_modes_preserve_gdi_identity_without_order_guessing(self):
        paths = {DISPLAY_A: PATH_A, DISPLAY_B: PATH_B}
        def devices(parent, index, pointer, flags):
            output = pointer._obj
            if parent is None:
                if index >= 2:
                    return 0
                output.DeviceName = (DISPLAY_B, DISPLAY_A)[index]
                output.DeviceString = "Fixture GPU"
                output.StateFlags = 1 | (4 if index == 1 else 0)
            else:
                self.assertEqual(flags, 1)
                if index:
                    return 0
                output.DeviceID = paths[parent]
                output.DeviceString = "Generic PnP Monitor"
                output.StateFlags = 1
            return 1
        def settings(device, index, pointer):
            if index not in (-1, 0):
                return 0
            output = pointer._obj
            output.dmPelsWidth, output.dmPelsHeight, output.dmDisplayFrequency = 1920, 1080, 60
            return 1
        user32 = SimpleNamespace(EnumDisplayDevicesW=devices, EnumDisplaySettingsW=settings)
        query = {"status": "ok", "targets": [target(), target(DISPLAY_B, PATH_B, "Panel B", 20)]}
        with patch.object(system_tools, "IS_WIN", True), patch.object(ctypes, "windll", SimpleNamespace(user32=user32), create=True), patch.object(identity, "active_targets", return_value=query):
            result = system_tools.displays()
        self.assertEqual([(row["device"], row["model"]) for row in result["displays"]],
                         [(DISPLAY_A, "Example Monitor A"), (DISPLAY_B, "Panel B")])
        self.assertEqual(result["displays"][0]["rates"], [60])

    def test_display_routes_are_sync_for_fastapi_threadpool_dispatch(self):
        routes = {}
        def decorator(path):
            def capture(function):
                routes[(path, function.__name__)] = function
                return function
            return capture
        app = SimpleNamespace(get=decorator, post=decorator)
        system_tools.register(app)
        self.assertFalse(inspect.iscoroutinefunction(routes[("/api/display", "_disp")]))
        self.assertFalse(inspect.iscoroutinefunction(routes[("/api/display", "_set_disp")]))


if __name__ == "__main__":
    unittest.main()
