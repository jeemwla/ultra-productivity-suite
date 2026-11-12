"""Exercise the real config reader without importing its hardware/credential startup."""
import ast
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
tree = ast.parse((ROOT / "code_red_alarm/backend/audio_enforcer.py").read_text(encoding="utf-8"))
reader = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "load_config")
CODE = compile(ast.fix_missing_locations(ast.Module(body=[reader], type_ignores=[])), "actual-audio-config-reader", "exec")


class AudioDefaults(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.file = Path(self.temp.name) / "device_config.json"
        self.env = patch.dict(os.environ, {"ALARM_SPEAKER_MATCH": "", "PEBBLE_DEVICE_MATCH": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.namespace = {"os": os, "json": json, "CONFIG_PATH": str(self.file),
                          "list_outputs": lambda: [{"name": "default"}, {"name": "Speakers (Pebble V3)"}]}
        exec(CODE, self.namespace)

    def test_public_fresh_or_corrupt_config_uses_windows_default_and_no_volume_lock(self):
        for raw in (None, "{broken", "{}"):
            if raw is not None:
                self.file.write_text(raw, encoding="utf-8")
            self.assertEqual(self.namespace["load_config"](), {"output": "default", "lock_volume": False})

    def test_explicit_saved_audio_preferences_are_preserved(self):
        self.file.write_text(json.dumps({"output": "Chosen headphones", "lock_volume": True}), encoding="utf-8")
        self.assertEqual(self.namespace["load_config"](), {"output": "Chosen headphones", "lock_volume": True})

    def test_personal_output_requires_configuration_and_does_not_enable_volume_lock(self):
        with patch.dict(os.environ, {"ALARM_SPEAKER_MATCH": "Pebble V3"}):
            self.assertEqual(self.namespace["load_config"](), {"output": "Speakers (Pebble V3)", "lock_volume": False})
