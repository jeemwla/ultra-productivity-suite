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

    def test_alarm_gate_requires_an_explicit_boolean_ringing_state(self):
        for ring in [{}, None, [], "idle", {"is_ringing": None}, {"is_ringing": 0},
                     {"is_ringing": 1}, {"is_ringing": "false"}, {"is_ringing": []}]:
            for nxt in [{"in_sec": None}, {"in_sec": 1801}]:
                with self.subTest(next_info=nxt, ring_info=ring):
                    self.assertIn("Couldn't confirm", devtools.alarm_blocks_update(nxt, ring))

    def test_alarm_gate_rejects_malformed_countdowns_without_raising(self):
        for due in [True, False, float("nan"), float("inf"), float("-inf"),
                    "1801", "", [], {}, complex(1801, 0)]:
            with self.subTest(in_sec=due):
                self.assertIn("Couldn't confirm", devtools.alarm_blocks_update(
                    {"in_sec": due}, {"is_ringing": False}))

    def test_null_countdown_requires_the_explicit_no_next_response(self):
        for nxt in [{"in_sec": None, "at": "2026-11-12T12:00:00"},
                    {"in_sec": None, "at": None}, {"in_sec": None, "error": "unavailable"}]:
            with self.subTest(next_info=nxt):
                self.assertIn("Couldn't confirm", devtools.alarm_blocks_update(nxt, {"is_ringing": False}))

    def test_alarm_gate_preserves_finite_countdown_boundaries(self):
        for due in [-10**1000, -1, -0.5, 0, 5, 1800, 1800.0]:
            with self.subTest(in_sec=due):
                self.assertIn("within 30 minutes", devtools.alarm_blocks_update(
                    {"in_sec": due}, {"is_ringing": False}))
        for due in [1800.5, 1801, 10**1000]:
            with self.subTest(in_sec=due):
                self.assertIsNone(devtools.alarm_blocks_update({"in_sec": due}, {"is_ringing": False}))


class FakeResponse:
    def __init__(self, payload=None, chunks=(), status_code=200, json_error=None):
        self._payload, self._chunks = payload, chunks
        self.status_code, self.json_error = status_code, json_error

    def json(self):
        if self.json_error:
            raise self.json_error
        return self._payload

    def raise_for_status(self):
        response = devtools.httpx.Response(self.status_code, request=devtools.httpx.Request("GET", "http://fixture.invalid"))
        response.raise_for_status()

    async def aiter_bytes(self):
        for c in self._chunks:
            yield c

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def fake_client_factory(release, alarm_next, alarm_ring, body, alarm_checks=None, events=None):
    events = [] if events is None else events
    checks = iter(alarm_checks) if alarm_checks is not None else None

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
            endpoint = url.rsplit("/", 1)[-1]
            if endpoint in ("next", "ring"):
                events.append(endpoint)
                if checks is None:
                    return FakeResponse(alarm_next if endpoint == "next" else alarm_ring)
                expected, result = next(checks)
                if expected != endpoint:
                    raise AssertionError((expected, endpoint))
                if isinstance(result, Exception):
                    raise result
                return result
            raise AssertionError(url)

        def stream(self, method, url, *a, **k):
            events.append("download")
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
        self.addCleanup(self.client.close)
        devtools._upd.update(at=0.0, data=None)
        self.body = b"fake installer bytes for the test"

    def release(self, digest):
        return {"tag_name": "v99.0.0", "body": "", "html_url": "u",
                "assets": [{"name": "UltraSuite-Setup-99.0.0-x64.exe", "browser_download_url": "https://example.invalid/a.exe",
                            "digest": digest}]}

    def run_install(self, digest, nxt=None, ring=None, alarm_checks=None, events=None):
        nxt = {"in_sec": None} if nxt is None else nxt
        ring = {"is_ringing": False} if ring is None else ring
        fake = fake_client_factory(self.release(digest), nxt, ring, self.body, alarm_checks, events)
        def launched(*args, **kwargs):
            if events is not None:
                events.append("launch")
        with mock.patch.object(devtools.httpx, "AsyncClient", fake), \
                mock.patch.object(devtools.tempfile, "gettempdir", return_value=self.tmp), \
                mock.patch.object(devtools.subprocess, "Popen", side_effect=launched) as popen:
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

    def test_malformed_alarm_checks_block_before_download_or_launch(self):
        good = "sha256:" + hashlib.sha256(self.body).hexdigest()
        idle = {"is_ringing": False}
        checks = [({"in_sec": None}, {}), ({"in_sec": 1801}, {"is_ringing": 0}),
                  ({"in_sec": None, "at": "2026-11-12T12:00:00"}, idle)]
        checks.extend(({"in_sec": due}, idle) for due in
                      [True, False, float("nan"), float("inf"), float("-inf"), "1801", [], {}])
        for nxt, ring in checks:
            with self.subTest(next_info=nxt, ring_info=ring):
                r, popen = self.run_install(good, nxt, ring)
                self.assertEqual(r.status_code, 409, r.text)
                self.assertIn("Couldn't confirm", r.json()["detail"])
                popen.assert_not_called()
                self.assertEqual(os.listdir(self.tmp), ["fe"], "no installer was downloaded")

    def test_finite_alarm_beyond_the_margin_allows_verified_install(self):
        good = "sha256:" + hashlib.sha256(self.body).hexdigest()
        r, popen = self.run_install(good, {"in_sec": 1800.5}, {"is_ringing": False})
        self.assertEqual(r.status_code, 200, r.text)
        popen.assert_called_once()

    def safe_checks(self):
        return [("next", FakeResponse({"in_sec": None})), ("ring", FakeResponse({"is_ringing": False}))]

    def test_http_error_with_safe_looking_payload_blocks_before_download(self):
        good = "sha256:" + hashlib.sha256(self.body).hexdigest()
        for endpoint in ("next", "ring"):
            for status in (302, 404, 500):
                with self.subTest(endpoint=endpoint, status=status):
                    checks = self.safe_checks()
                    checks[0 if endpoint == "next" else 1][1].status_code = status
                    events = []
                    r, popen = self.run_install(good, alarm_checks=checks, events=events)
                    self.assertEqual(r.status_code, 409, r.text)
                    self.assertIn("Couldn't confirm", r.json()["detail"])
                    popen.assert_not_called()
                    self.assertNotIn("download", events)
                    self.assertEqual(os.listdir(self.tmp), ["fe"])

    def test_changed_alarm_state_after_download_blocks_launch_and_removes_only_candidate(self):
        good = "sha256:" + hashlib.sha256(self.body).hexdigest()
        # This was the previous deterministic download path. It belongs to someone else.
        unrelated = os.path.join(self.tmp, "UltraSuite-Setup-99.0.0.exe")
        with open(unrelated, "wb") as f:
            f.write(b"unrelated existing file")
        cases = [
            ([("next", FakeResponse({"in_sec": None})), ("ring", FakeResponse({"is_ringing": True}))], "ringing"),
            ([("next", FakeResponse({"in_sec": 1800})), ("ring", FakeResponse({"is_ringing": False}))], "30 minutes"),
            ([("next", FakeResponse({"in_sec": -1})), ("ring", FakeResponse({"is_ringing": False}))], "30 minutes"),
            ([("next", FakeResponse({"in_sec": "1801"})), ("ring", FakeResponse({"is_ringing": False}))], "Couldn't confirm"),
            ([("next", FakeResponse({"in_sec": None})), ("ring", FakeResponse({}))], "Couldn't confirm"),
            ([("next", FakeResponse(json_error=ValueError("invalid JSON")))], "Couldn't confirm"),
            ([("next", FakeResponse({"in_sec": None})), ("ring", FakeResponse(json_error=ValueError("invalid JSON")))], "Couldn't confirm"),
        ]
        for endpoint in ("next", "ring"):
            checks = self.safe_checks()
            checks[0 if endpoint == "next" else 1] = (endpoint, devtools.httpx.ReadTimeout("fixture timeout"))
            cases.append((checks, "Couldn't confirm"))
            for status in (302, 404, 500):
                checks = self.safe_checks()
                checks[0 if endpoint == "next" else 1][1].status_code = status
                cases.append((checks, "Couldn't confirm"))
        for final_checks, word in cases:
            with self.subTest(final_checks=final_checks, reason=word):
                events = []
                r, popen = self.run_install(good, alarm_checks=self.safe_checks() + final_checks, events=events)
                self.assertEqual(r.status_code, 409, r.text)
                self.assertIn(word, r.json()["detail"])
                popen.assert_not_called()
                self.assertIn("download", events)
                self.assertEqual(sorted(os.listdir(self.tmp)), ["UltraSuite-Setup-99.0.0.exe", "fe"])
                with open(unrelated, "rb") as f:
                    self.assertEqual(f.read(), b"unrelated existing file")

    def test_fresh_independent_checks_follow_checksum_and_precede_launch(self):
        digest = hashlib.sha256(self.body).hexdigest()
        for final_next in ({"in_sec": None}, {"in_sec": 1800.5}):
            with self.subTest(final_next=final_next):
                events = []
                hasher = hashlib.sha256()
                def verified_digest():
                    events.append("checksum")
                    return hasher.hexdigest()
                wrapped_hash = mock.Mock(wraps=hasher)
                wrapped_hash.hexdigest.side_effect = verified_digest
                final = [("next", FakeResponse(final_next)), ("ring", FakeResponse({"is_ringing": False}))]
                with mock.patch.object(devtools.hashlib, "sha256", return_value=wrapped_hash):
                    r, popen = self.run_install("sha256:" + digest, alarm_checks=self.safe_checks() + final, events=events)
                self.assertEqual(r.status_code, 200, r.text)
                popen.assert_called_once()
                self.assertEqual(events, ["next", "ring", "download", "checksum", "next", "ring", "launch"])
                candidate = popen.call_args.args[0][0]
                self.assertEqual(os.path.dirname(candidate), self.tmp)
                with open(candidate, "rb") as f:
                    self.assertEqual(f.read(), self.body)
                os.remove(candidate)

    def test_bad_checksum_never_reaches_final_check_or_launch(self):
        events = []
        r, popen = self.run_install("sha256:" + "0" * 64, alarm_checks=self.safe_checks(), events=events)
        self.assertEqual(r.status_code, 502)
        self.assertIn("checksum", r.json()["detail"])
        self.assertEqual(events, ["next", "ring", "download"])
        popen.assert_not_called()
        self.assertEqual(os.listdir(self.tmp), ["fe"])


if __name__ == "__main__":
    unittest.main()
