"""Readable process labels must not turn a spoofed filename into a Windows role."""
import copy
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "pc_sentinel"))
from backend.process_names import describe_process


class ProcessNames(unittest.TestCase):
    def describe(self, name="dwm.exe", exe=r"C:\Windows\System32\dwm.exe", **extra):
        return describe_process(dict(name=name, exe=exe, **extra), windows_dir=r"C:\Windows")

    def test_known_glossary_keeps_original_details_without_mutation(self):
        row = dict(name="dwm.exe", exe=r"C:\Windows\System32\dwm.exe", pid=62,
                   cpu=0.0, count=1, pids=[62], details={"sample": "unchanged"})
        before = copy.deepcopy(row)
        result = describe_process(row, windows_dir=r"C:\Windows")
        self.assertEqual(row, before)
        self.assertIsNot(result, row)
        for key, value in row.items():
            self.assertEqual(result[key], value)
        self.assertEqual(result["display_name"], "Desktop Window Manager")
        self.assertEqual(result["raw_filename"], "dwm.exe")
        self.assertTrue(result["system_path_match"])
        self.assertEqual(result["security_assessment"], "not_performed")

    def test_case_and_slash_handling_and_existing_stem_names(self):
        for name in ("DWM.EXE", "dWm", "dwm.exe"):
            for path in (r"c:\WINDOWS\SYSTEM32\DWM.EXE", "C:/Windows/System32/dwm.exe",
                         r"C:\Windows\SysWOW64\DWM.exe"):
                with self.subTest(name=name, path=path):
                    self.assertEqual(self.describe(name, path)["name_source"], "windows_glossary")

    def test_known_names_outside_exact_system_folder_stay_unknown(self):
        for path in (r"C:\Users\someone\dwm.exe", r"D:\Windows\System32\dwm.exe",
                     r"C:\Windows.old\System32\dwm.exe", r"C:\Windows\System32\extra\dwm.exe",
                     r"C:\Windows\System32evil\dwm.exe", r"C:\Windows\Temp\dwm.exe"):
            with self.subTest(path=path):
                result = self.describe(exe=path)
                self.assertFalse(result["system_path_match"])
                self.assertIsNone(result["explanation"])
                self.assertIsNone(result["source_url"])

    def test_path_aliases_and_ambiguous_syntax_are_not_treated_as_verified(self):
        for path in (r"System32\dwm.exe", r"C:Windows\System32\dwm.exe",
                     r"\Windows\System32\dwm.exe", r"\\server\Windows\System32\dwm.exe",
                     r"\\?\C:\Windows\System32\dwm.exe", r"C:\Windows\System32\..\System32\dwm.exe",
                     r"C:\Windows\System32\dwm.exe:stream", '"C:\\Windows\\System32\\dwm.exe"',
                     "C:\\Windows\\System32\\dwm.exe\x00", r"C:\Windows\System32.\dwm.exe"):
            with self.subTest(path=path):
                self.assertEqual(self.describe(exe=path)["name_source"], "filename")

    def test_name_and_executable_disagreement_does_not_select_role(self):
        self.assertIsNone(self.describe("svchost.exe")["explanation"])
        self.assertIsNone(self.describe("dwm.exe", r"C:\Windows\System32\notdwm.exe")["explanation"])

    def test_os_root_is_not_assumed_to_be_c_windows(self):
        row = dict(name="svchost", exe=r"E:\OS\System32\svchost.exe")
        result = describe_process(row, windows_dir=r"E:\OS")
        self.assertEqual(result["display_name"], "Windows Service Host")
        self.assertIn("filename alone does not identify", result["explanation"])
        self.assertFalse(describe_process(row, windows_dir=r"C:\Windows")["system_path_match"])
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(describe_process(row)["explanation"])

    def test_environment_windows_root_and_invalid_roots(self):
        with patch.dict(os.environ, {"SystemRoot": r"C:\Windows"}):
            self.assertTrue(describe_process(dict(name="dwm", exe=r"C:\Windows\System32\dwm.exe"))["system_path_match"])
        for root in ("", "Windows", "C:", r"\\server\Windows", r"C:\Windows\..\Windows"):
            self.assertFalse(describe_process(dict(name="dwm", exe=r"C:\Windows\System32\dwm.exe"), windows_dir=root)["system_path_match"])

    def test_metadata_has_explicit_unverified_provenance(self):
        result = self.describe("custom.exe", r"D:\Apps\custom.exe", file_description="  Example Editor  ")
        self.assertEqual(result["display_name"], "Example Editor")
        self.assertEqual(result["name_source"], "file_description")
        self.assertEqual(result["explanation_source"], "metadata_notice")
        self.assertIn("not been independently identified", result["explanation"])
        self.assertIsNone(result["source_url"])

    def test_metadata_cannot_override_path_matched_glossary(self):
        result = self.describe(file_description="A Game")
        self.assertEqual(result["display_name"], "Desktop Window Manager")

    def test_metadata_for_spoofed_windows_name_is_still_metadata(self):
        result = self.describe(exe=r"D:\Apps\dwm.exe", file_description="Desktop Window Manager")
        self.assertEqual(result["name_source"], "file_description")
        self.assertFalse(result["system_path_match"])
        self.assertEqual(result["security_assessment"], "not_performed")

    def test_missing_names_paths_and_metadata_do_not_invent_roles(self):
        for row in ({}, {"name": None, "exe": None}, {"name": "unknown.exe"},
                    {"name": "svchost", "exe": ""}):
            result = describe_process(row, windows_dir=r"C:\Windows")
            self.assertIsNone(result["explanation"])
            self.assertEqual(result["name_source"], "filename")
        self.assertEqual(describe_process({})["display_name"], "Unknown process")

    def test_path_only_row_uses_filename_and_unknown_role(self):
        result = self.describe(None, r"D:\Apps\Editor.EXE", pid=5)
        self.assertEqual(result["raw_filename"], "Editor.EXE")
        self.assertEqual(result["display_name"], "Editor")
        self.assertEqual(result["pid"], 5)
        self.assertIsNone(result["explanation"])

    def test_metadata_controls_bidi_and_non_text_values_are_rejected(self):
        for desc in (None, False, 5, "", " \t ", "Name\nInjected", "Name\u202e.exe", "N\x00ame"):
            with self.subTest(description=desc):
                result = self.describe("custom.exe", r"D:\custom.exe", file_description=desc)
                self.assertEqual(result["name_source"], "filename")
        self.assertEqual(len(self.describe("custom", None, file_description="a" * 200)["display_name"]), 160)

    def test_unicode_metadata_is_preserved_as_text(self):
        result = self.describe("custom", None, file_description="エディター <Beta>")
        self.assertEqual(result["display_name"], "エディター <Beta>")

    def test_runtime_broker_uses_sourced_glossary(self):
        result = self.describe("RuntimeBroker.exe", r"C:\Windows\System32\RuntimeBroker.exe")
        self.assertEqual(result["display_name"], "Runtime Broker")
        self.assertTrue(result["source_url"].startswith("https://support.microsoft.com/"))

    def test_input_must_be_a_row(self):
        with self.assertRaises(TypeError):
            describe_process(None)

    def test_forensic_display_makes_invisible_control_characters_visible(self):
        value=self.describe('tool\u202e.exe','C:\\Apps\\tool\u202e.exe')
        self.assertEqual(value['raw_filename'],'tool\u202e.exe')
        self.assertEqual(value['raw_filename_display'],'tool\\u202e.exe')
        self.assertEqual(value['exe_display'],'C:\\Apps\\tool\\u202e.exe')


if __name__ == "__main__":
    unittest.main()
