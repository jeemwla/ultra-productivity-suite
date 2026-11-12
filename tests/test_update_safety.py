"""The one-click updater verifies GitHub's published checksum and never starts while an alarm rings or is due soon.

GitHub, the download and the alarm service are faked; nothing is downloaded, run or installed.
"""
import hashlib
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core"))

import devtools  # noqa: E402


class Pure(unittest.TestCase):
    def test_expected_sha256_reads_only_a_well_formed_github_digest(self):
        h = "ab" * 32
        self.assertEqual(devtools.expected_sha256({"digest": "sha256:" + h.upper()}), h)
        for bad in [None, {}, {"digest": ""}, {"digest": "sha1:" + "a" * 40}, {"digest": "sha256:xyz"},
                    {"digest": "sha256:" + "a" * 63}]:
            self.assertIsNone(devtools.expected_sha256(bad), bad)

    def test_alarm_rule(self):
        idle, ringing = {"is_ringing": False}, {"is_ringing": True}
        self.assertIsNone(devtools.alarm_blocks_update({"in_sec": None}, idle), "no alarm scheduled")
        self.assertIsNone(devtools.alarm_blocks_update({"in_sec": 1801}, idle))
        self.assertIn("within 30 minutes", devtools.alarm_blocks_update({"in_sec": 1800}, idle))
        self.assertIn("within 30 minutes", devtools.alarm_blocks_update({"in_sec": 5}, idle))
        self.assertIn("ringing", devtools.alarm_blocks_update({"in_sec": None}, ringing))
        for nxt, ring in [(None, idle), ({"in_sec": None}, None), ({}, idle), ("x", idle)]:
            self.assertIn("Couldn't confirm", devtools.alarm_blocks_update(nxt, ring), "an unavailable check blocks")


class FakeResponse:
    def __init__(self, payload=None, chunks=()):
        self._payload, self._chunks = payload, chunks

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None

    async def aiter_bytes(self):
        for c in self._chunks:
            yield c

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def fake_client_factory(release, alarm_next, alarm_ring, body):
    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, *a, **k):
            if "releases/latest" in url:
                return FakeResponse(release)
            if url.endswith("/api/next"):
                return FakeResponse(alarm_next)
            if url.endswith("/api/ring"):
                return FakeResponse(alarm_ring)
            raise AssertionError(url)

        def stream(self, method, url, *a, **k):
            return FakeResponse(chunks=[body[:10], body[10:]])
    return FakeClient


class InstallRoute(unittest.TestCase):
    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import shutil
        self.tmp = tempfile.mkdtemp(prefix="ultrasuite-update-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        frontend = os.path.join(self.tmp, "fe")
        os.makedirs(frontend)
        app = FastAPI()
        devtools.add_shared_routes(app, frontend)
        self.client = TestClient(app, base_url="http://127.0.0.1")
        devtools._upd.update(at=0.0, data=None)
        self.body = b"fake installer bytes for the test"

    def release(self, digest):
        return {"tag_name": "v99.0.0", "body": "", "html_url": "u",
                "assets": [{"name": "UltraSuite-Setup-99.0.0-x64.exe", "browser_download_url": "https://example.invalid/a.exe",
                            "digest": digest}]}

    def run_install(self, digest, nxt=None, ring=None):
        nxt = {"in_sec": None} if nxt is None else nxt
        ring = {"is_ringing": False} if ring is None else ring
        fake = fake_client_factory(self.release(digest), nxt, ring, self.body)
        with mock.patch.object(devtools.httpx, "AsyncClient", fake), \
                mock.patch.object(devtools.tempfile, "gettempdir", return_value=self.tmp), \
                mock.patch.object(devtools.subprocess, "Popen") as popen:
            r = self.client.post("/api/update/install")
        return r, popen

    def test_matching_checksum_starts_the_installer(self):
        r, popen = self.run_install("sha256:" + hashlib.sha256(self.body).hexdigest())
        self.assertEqual(r.status_code, 200, r.text)
        popen.assert_called_once()

    def test_mismatched_download_is_deleted_and_never_run(self):
        r, popen = self.run_install("sha256:" + "0" * 64)
        self.assertEqual(r.status_code, 502)
        self.assertIn("checksum", r.json()["detail"])
        popen.assert_not_called()
        self.assertEqual(os.listdir(self.tmp), ["fe"], "the bad download was removed")

    def test_release_without_a_checksum_is_refused(self):
        r, popen = self.run_install(None)
        self.assertEqual(r.status_code, 409)
        popen.assert_not_called()

    def test_no_update_while_an_alarm_is_due_or_ringing(self):
        good = "sha256:" + hashlib.sha256(self.body).hexdigest()
        for nxt, ring, word in [({"in_sec": 600}, None, "30 minutes"), (None, {"is_ringing": True}, "ringing")]:
            devtools._upd.update(at=0.0, data=None)
            r, popen = self.run_install(good, nxt, ring)
            self.assertEqual(r.status_code, 409)
            self.assertIn(word, r.json()["detail"])
            popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
