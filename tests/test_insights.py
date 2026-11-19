"""Insights collector and dashboard tests: temporary database, fake GitHub transport, no network, no real ports.

Run from the repo root:  python -W ignore -m unittest tests.test_insights -v
"""
import asyncio
import csv
import importlib.util
import io
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core"))
LOCAL = "http://127.0.0.1:8301"


def load_insights():
    spec = importlib.util.spec_from_file_location("insights_suite_test", os.path.join(ROOT, "insights", "insights.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class InsightsCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._data = os.environ.get("ULTRASUITE_DATA")
        os.environ["ULTRASUITE_DATA"] = self._tmp.name
        self.m = load_insights()
        self.collector = TestClient(self.m.collector)
        self.dash = TestClient(self.m.dashboard, base_url=LOCAL)

    def tearDown(self):
        self.collector.close()
        self.dash.close()
        if self._data is None:
            os.environ.pop("ULTRASUITE_DATA", None)
        else:
            os.environ["ULTRASUITE_DATA"] = self._data
        self._tmp.cleanup()

    def rows(self, sql="SELECT type,version,os,reason,message,anon FROM events ORDER BY id"):
        self.m._ensure_schema(self.m.DB)
        c = sqlite3.connect(self.m.DB)
        try:
            return c.execute(sql).fetchall()
        finally:
            c.close()

    def add(self, kind, ts=None, **fields):
        with self.m.db() as c:
            c.execute("INSERT INTO events(ts,type,version,os,reason,message,anon) VALUES(?,?,?,?,?,?,?)",
                      (ts or time.time(), kind, fields.get("version"), fields.get("os"), fields.get("reason"),
                       fields.get("message"), fields.get("anon", "pc")))


class Collector(InsightsCase):
    def test_valid_event_is_stored_with_control_characters_removed(self):
        r = self.collector.post("/v1/event", json={"type": "feedback", "version": "2.2\x07.16", "os": "Windows\n11",
                                                    "reason": "sentinel:idea", "message": "line one\nline\x1b two\t!",
                                                    "id": "abc\x00def"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.rows(), [("feedback", "2.2.16", "Windows11", "sentinel:idea", "line one\nline two\t!", "abcdef")])

    def test_non_object_json_is_a_clear_400(self):
        for body in ("[1, 2]", '"install"', "3", "null"):
            r = self.collector.post("/v1/event", content=body, headers={"content-type": "application/json"})
            self.assertEqual(r.status_code, 400, body)
        self.assertEqual(self.collector.post("/v1/event", content="{bad").status_code, 400)
        self.assertEqual(self.collector.post("/v1/event", json={"type": "nope"}).status_code, 400)
        self.assertEqual(self.rows(), [])

    def test_declared_and_streamed_oversized_bodies_are_refused(self):
        big = json.dumps({"type": "feedback", "message": "x" * 5000})
        self.assertEqual(self.collector.post("/v1/event", content=big).status_code, 413)

        def chunks():  # no Content-Length: the cap must apply while reading
            for _ in range(10):
                yield b"x" * 1000
        self.assertEqual(self.collector.post("/v1/event", content=chunks()).status_code, 413)
        self.assertEqual(self.rows(), [])

    def test_forwarded_header_written_by_a_sender_does_not_pick_the_rate_key(self):
        for i in range(120):
            r = self.collector.post("/v1/event", json={"type": "heartbeat", "id": f"pc-{i}"},
                                    headers={"x-forwarded-for": f"10.0.0.{i}"})
            self.assertEqual(r.status_code, 200)
        r = self.collector.post("/v1/event", json={"type": "heartbeat", "id": "pc-new"}, headers={"x-forwarded-for": "10.9.9.9"})
        self.assertEqual(r.status_code, 429, "the per-address limit uses the resolved client, not the raw header")

    def test_legacy_path_and_health(self):
        self.assertEqual(self.collector.post("/ultrasuite/v1/event", json={"type": "install", "id": "a"}).status_code, 200)
        self.assertEqual(self.collector.get("/ultrasuite/v1/health").json(), {"ok": True})


class Limiter(unittest.TestCase):
    def setUp(self):
        self.m = load_insights()

    def test_window_limit_and_recovery(self):
        rl = self.m.RateLimiter()
        self.assertFalse(any(rl.limited("a", 3, 60, now=100 + i) for i in range(3)))
        self.assertTrue(rl.limited("a", 3, 60, now=103))
        self.assertFalse(rl.limited("a", 3, 60, now=200), "old hits leave the window")

    def test_idle_keys_are_pruned_and_key_count_is_capped(self):
        rl = self.m.RateLimiter(max_keys=3)
        for k in "abc":
            rl.limited(k, 5, 60, now=0)
        self.assertFalse(rl.limited("d", 5, 60, now=1000), "idle keys make room")
        self.assertEqual(set(rl.hits), {"d"})
        for k in "ef":
            rl.limited(k, 5, 60, now=1000)
        self.assertTrue(rl.limited("g", 5, 60, now=1001), "a table full of active keys refuses new ones")
        self.assertEqual(len(rl.hits), 3)


class Dashboard(InsightsCase):
    def stats(self, rng="month"):
        with patch.object(self.m, "github_stats", AsyncMock(return_value={"ok": True})):
            r = self.dash.get(f"/api/stats?range={rng}")
        self.assertEqual(r.status_code, 200)
        return r.json()

    def test_heartbeats_count_but_stay_off_the_chart(self):
        self.add("heartbeat")
        self.add("heartbeat", anon="other")
        self.add("install", version="2.2.16")
        self.add("uninstall", reason="too-busy", message="bye")
        self.add("uninstall")
        s = self.stats()
        self.assertEqual(s["counts"], {"heartbeat": 2, "install": 1, "uninstall": 2})
        self.assertEqual(sum(p["install"] + p["uninstall"] + p["feedback"] for p in s["series"]), 3)
        self.assertEqual(s["active_7d"], 2)
        self.assertEqual(s["reasons"], [["no answer", 1], ["too-busy", 1]])
        self.assertEqual(s["versions"], {"2.2.16": 1})
        self.assertEqual([f["message"] for f in s["feedback"]], ["bye"])
        self.assertTrue(all("id" in r for r in s["recent"]))
        self.assertEqual(s["net_installs"], -1)

    def test_unknown_range_is_reported_as_month(self):
        s = self.stats("forever")
        self.assertEqual((s["range"], len(s["series"])), ("month", 30))
        self.assertEqual(len(self.stats("day")["series"]), 24)

    def test_old_events_stay_out_of_the_period(self):
        self.add("install", ts=time.time() - 40 * 86400)
        s = self.stats()
        self.assertEqual(s["counts"], {})
        self.assertEqual(s["alltime"], {"install": 1})

    def test_security_headers_and_no_store_for_data(self):
        r = self.dash.get("/")
        self.assertEqual(r.headers["x-frame-options"], "DENY")
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")
        with patch.object(self.m, "github_stats", AsyncMock(return_value={})):
            self.assertEqual(self.dash.get("/api/stats").headers["cache-control"], "no-store")

    def test_dashboard_answers_only_local_host_names(self):
        other = TestClient(self.m.dashboard, base_url="http://rebind.example:8301")
        self.assertEqual(other.get("/api/stats").status_code, 403)
        other.close()

    def test_csv_export_escapes_formulas(self):
        self.add("feedback", message="=HYPERLINK(\"http://x\")", reason="-1", version="2.2.16", anon="pc,1")
        r = self.dash.get("/api/export.csv?range=week")
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r.headers["content-disposition"])
        self.assertIn("-week-", r.headers["content-disposition"])
        text = r.content.decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(rows[0][:3], ["id", "time", "type"])
        self.assertEqual(rows[1][6], "'=HYPERLINK(\"http://x\")")
        self.assertEqual(rows[1][5], "'-1")
        self.assertEqual(rows[1][7], "pc,1")


class Delete(InsightsCase):
    def test_bad_requests_are_400_and_change_nothing(self):
        self.add("install")
        for body in ({}, {"ids": "1"}, {"ids": [1.5]}, {"ids": [True]}, {"ids": ["x"]}, {"anon": 5},
                     {"anon": "  "}, {"ids": list(range(501))}):
            self.assertEqual(self.dash.post("/api/delete", json=body).status_code, 400, body)
        self.assertEqual(len(self.rows()), 1)

    def test_delete_by_ids_and_anon(self):
        for a in ("me", "me", "you", "them"):
            self.add("heartbeat", anon=a)
        ids = [r[0] for r in self.rows("SELECT id FROM events WHERE anon='you'")]
        self.assertEqual(self.dash.post("/api/delete", json={"ids": [str(ids[0])]}).json(), {"ok": True, "deleted": 1})
        self.assertEqual(self.dash.post("/api/delete", json={"anon": "me"}).json()["deleted"], 2)
        self.assertEqual([r[5] for r in self.rows()], ["them"])

    def test_page_on_another_site_cannot_delete(self):
        self.add("install")
        r = self.dash.post("/api/delete", json={"anon": "pc"}, headers={"origin": "https://example.com"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(len(self.rows()), 1)


def fake_github(handler):
    real = httpx.AsyncClient
    return patch.object(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


class GitHub(unittest.TestCase):
    def setUp(self):
        self.m = load_insights()
        self.calls = []
        self._token = os.environ.pop("GITHUB_TOKEN", None)

    def tearDown(self):
        if self._token is not None:
            os.environ["GITHUB_TOKEN"] = self._token

    def handler(self, release_count=130, repo_status=200, headers=None):
        def h(request):
            self.calls.append(request.url.path)
            path = request.url.path
            if path.endswith("/releases"):
                page = int(request.url.params.get("page", 1))
                start = (page - 1) * 100
                batch = [{"tag_name": f"v{i}", "assets": [{"download_count": 2}]} for i in range(start, min(start + 100, release_count))]
                return httpx.Response(200, json=batch)
            return httpx.Response(repo_status, json={"stargazers_count": 7, "message": "x"}, headers=headers or {})
        return h

    def run_async(self, coro):
        return asyncio.run(coro)

    def test_downloads_count_every_release_page(self):
        with fake_github(self.handler(130)):
            out = self.run_async(self.m.github_stats())
        self.assertTrue(out["ok"])
        self.assertEqual((len(out["releases"]), out["downloads_total"], out["stars"]), (130, 260, 7))

    def test_rate_limit_is_explained_and_retried_after_a_minute(self):
        with fake_github(self.handler(repo_status=403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "0"})):
            out = self.run_async(self.m.github_stats())
            self.assertFalse(out["ok"])
            self.assertIn("request limit", out["error"])
            n = len(self.calls)
            self.run_async(self.m.github_stats())
            self.assertEqual(len(self.calls), n, "cached within the error window")
            self.m._gh["at"] -= self.m.GITHUB_ERROR_TTL + 1
            self.run_async(self.m.github_stats())
            self.assertGreater(len(self.calls), n, "retried after the short error window")

    def test_failure_keeps_last_good_numbers_marked_stale(self):
        with fake_github(self.handler(5)):
            self.run_async(self.m.github_stats())
        self.m._gh["at"] -= self.m.GITHUB_TTL + 1
        with fake_github(self.handler(repo_status=500)):
            out = self.run_async(self.m.github_stats())
        self.assertTrue(out["ok"] and out["stale"])
        self.assertEqual(out["downloads_total"], 10)
        self.assertIn("500", out["error"])

    def test_bad_token_message(self):
        self.assertIn("GITHUB_TOKEN", self.m.github_problem(401, {}))

    def test_concurrent_dashboards_share_one_fetch(self):
        async def both():
            return await asyncio.gather(self.m.github_stats(), self.m.github_stats(), self.m.github_stats())
        with fake_github(self.handler(5)):
            self.run_async(both())
        self.assertEqual(self.calls.count(f"/repos/{self.m.REPO}"), 1)


class Storage(InsightsCase):
    def test_failed_write_rolls_back(self):
        with self.assertRaises(RuntimeError):
            with self.m.db() as c:
                c.execute("INSERT INTO events(ts,type) VALUES(1,'install')")
                raise RuntimeError("boom")
        self.assertEqual(self.rows(), [])

    def test_database_uses_write_ahead_log(self):
        with self.m.db() as c:
            self.assertEqual(c.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")


if __name__ == "__main__":
    unittest.main()
