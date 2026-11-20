"""Shared core routes: feedback forwarding and the saved appearance. Temporary folders and a fake collector
transport only; nothing is sent anywhere.

Run from the repo root:  python -W ignore -m unittest tests.test_core_shared -v
"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core"))
import devtools  # noqa: E402
import feedback  # noqa: E402


class Feedback(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.status = 200
        self._env = {k: os.environ.get(k) for k in ("ULTRASUITE_EDITION", "ULTRASUITE_COLLECTOR", "ULTRASUITE_CONF")}
        self._conf = tempfile.TemporaryDirectory()
        os.environ.update(ULTRASUITE_EDITION="Public", ULTRASUITE_COLLECTOR="https://collector.invalid/v1/event",
                          ULTRASUITE_CONF=self._conf.name)
        real = httpx.AsyncClient

        def handler(request):
            self.sent.append(json.loads(request.content))
            return httpx.Response(self.status, json={})
        self._patch = patch.object(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
        self._patch.start()
        app = FastAPI()
        feedback.add_feedback_route(app, "sentinel")
        self.c = TestClient(app)

    def tearDown(self):
        self._patch.stop()
        self.c.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._conf.cleanup()

    def test_feedback_is_filed_under_the_running_version(self):
        with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as f:
            current = f.read().strip()
        self.assertEqual(self.c.post("/api/feedback", json={"kind": "bug", "message": "it broke"}).status_code, 200)
        self.assertEqual(self.sent[0]["version"], current)
        self.assertEqual(self.sent[0]["reason"], "sentinel:bug")

    def test_unknown_kind_is_filed_as_an_idea(self):
        self.c.post("/api/feedback", json={"kind": "<script>", "message": "hello there"})
        self.assertEqual(self.sent[0]["reason"], "sentinel:idea")

    def test_too_long_message_is_refused_with_its_length_not_cut_silently(self):
        r = self.c.post("/api/feedback", json={"message": "x" * 1200})
        self.assertEqual(r.status_code, 400)
        self.assertIn("1200", r.json()["detail"])
        self.assertEqual(self.c.post("/api/feedback", json={"message": "x" * 5000}).status_code, 422)
        self.assertEqual(self.sent, [])

    def test_collector_rate_limit_is_explained(self):
        self.status = 429
        r = self.c.post("/api/feedback", json={"message": "hello there"})
        self.assertEqual(r.status_code, 429)
        self.assertIn("Try again later", r.json()["detail"])


class Theme(unittest.TestCase):
    def test_damaged_or_hand_edited_file_keeps_valid_fields_only(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "theme.json")
            with open(p, "w", encoding="utf-8") as f:
                f.write("{not json")
            self.assertEqual(devtools.read_theme(p), devtools.THEME_DEFAULTS)
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"theme": "matcha", "motion": 3, "density": "huge", "extra": "x"}, f)
            self.assertEqual(devtools.read_theme(p), {"theme": "matcha", "motion": "full", "density": "comfortable"})
            with open(p, "w", encoding="utf-8") as f:
                json.dump(["dark"], f)
            self.assertEqual(devtools.read_theme(p), devtools.THEME_DEFAULTS)

    def test_save_is_atomic_and_leaves_no_temporary_files(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "sub", "theme.json")
            devtools.write_json_atomic(p, {"theme": "dark"})
            with patch.object(devtools.json, "dump", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    devtools.write_json_atomic(p, {"theme": "light"})
            with open(p, encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"theme": "dark"}, "a failed save keeps the previous choice")
            self.assertEqual(os.listdir(os.path.dirname(p)), ["theme.json"])

    def test_merge_ignores_invalid_choices(self):
        cur = devtools.merge_theme(devtools.THEME_DEFAULTS, {"theme": "dark", "motion": "reduced", "density": "x"})
        self.assertEqual(cur, {"theme": "dark", "motion": "reduced", "density": "comfortable"})
        self.assertEqual(devtools.merge_theme(cur, "dark"), cur)


if __name__ == "__main__":
    unittest.main()
