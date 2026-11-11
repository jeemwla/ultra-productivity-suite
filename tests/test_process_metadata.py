import unittest
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import patch
from pc_sentinel.backend import process_metadata as m


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.stat=SimpleNamespace(st_mtime_ns=10,st_size=100)
        self.patches=[patch.object(m,'_cache',OrderedDict()),patch.object(m.os,'stat',side_effect=lambda *_:self.stat),patch.object(m,'_read_version',return_value='Example App'),patch.object(m,'_local_drive',return_value=True)]
        self.mocks=[p.start() for p in self.patches]
        self.addCleanup(lambda:[p.stop() for p in reversed(self.patches)])
    def test_repeated_description_uses_mtime_cache(self):
        self.assertEqual(m.file_description(r'C:\Apps\App.exe'),'Example App')
        self.assertEqual(m.file_description(r'c:\apps\app.exe'),'Example App');self.assertEqual(self.mocks[2].call_count,1)
    def test_replacement_invalidates_cached_description(self):
        m.file_description(r'C:\App.exe');self.stat.st_mtime_ns=20;m.file_description(r'C:\App.exe');self.assertEqual(self.mocks[2].call_count,2)
    def test_absent_resources_are_cached(self):
        self.mocks[2].return_value=None;m.file_description(r'C:\App.exe');m.file_description(r'C:\App.exe');self.assertEqual(self.mocks[2].call_count,1)
    def test_cached_only_never_reads_file(self):
        self.assertIsNone(m.file_description(r'C:\App.exe',cached_only=True));self.mocks[1].assert_not_called();self.mocks[2].assert_not_called()
    def test_invalid_paths_never_open_a_file(self):
        for p in [None,'App.exe',r'\\server\share\App.exe',r'C:\Apps\..\App.exe',r'C:\App.exe:stream']:
            self.assertIsNone(m.file_description(p))
        self.mocks[1].assert_not_called()
    def test_controls_and_spoofing_characters_are_not_labels(self):
        self.mocks[2].return_value='App\u202eexe';self.assertIsNone(m.file_description(r'C:\App.exe'))
    def test_access_denied_is_an_unknown_label(self):
        self.mocks[1].side_effect=PermissionError;self.assertIsNone(m.file_description(r'C:\App.exe'))
    def test_cache_is_bounded(self):
        with patch.object(m,'_MAX_CACHE',2):
            for i in range(3):m.file_description(fr'C:\App{i}.exe')
        self.assertEqual(len(m._cache),2);self.assertNotIn(r'c:\app0.exe',m._cache)
    def test_remote_or_unknown_drives_are_rejected_before_stat(self):
        self.mocks[3].return_value=False;self.assertIsNone(m.file_description(r'Z:\App.exe'));self.mocks[1].assert_not_called()
    def test_denied_path_has_a_bounded_negative_cache(self):
        self.mocks[1].side_effect=PermissionError
        with patch.object(m.time,'monotonic',return_value=100):
            self.assertEqual(m.file_description(r'C:\App.exe',with_status=True),(None,True))
            self.assertEqual(m.file_description(r'C:\App.exe',with_status=True),(None,False))
            self.assertEqual(self.mocks[1].call_count,1)
        with patch.object(m.time,'monotonic',return_value=401):m.file_description(r'C:\App.exe')
        self.assertEqual(self.mocks[1].call_count,2)


if __name__=='__main__':unittest.main()
