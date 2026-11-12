"""Edition gates on disposable FastAPI apps. No real apps, settings or network."""
import asyncio
import importlib.util
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


def load_module(relative):
    spec = importlib.util.spec_from_file_location("shared_gate_fixture", ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SharedGates(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = patch.dict(os.environ, {"APPDATA": str(self.root / "roaming"),
                                     "LOCALAPPDATA": str(self.root / "local"),
                                     "ULTRASUITE_DATA": str(self.root / "data"),
                                     "ULTRASUITE_CONF": str(self.root / "conf")}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def shared_app(self, edition="Public", ports=None):
        os.environ["ULTRASUITE_EDITION"] = edition
        if ports is not None:
            os.environ["ULTRASUITE_ORIGIN_PORTS"] = ports
        module = load_module("core/devtools.py")
        module.version = lambda: "2.2.16"
        app = FastAPI()
        module.add_shared_routes(app, str(self.root))
        client = TestClient(app, base_url="http://127.0.0.1")
        self.addCleanup(client.close)
        return module, client

    def test_about_reports_public_and_dev_identity(self):
        for edition in ("Public", "Dev"):
            with self.subTest(edition=edition):
                _, client = self.shared_app(edition)
                info = client.get("/api/about").json()
                self.assertEqual(info.get("edition"), edition)
                self.assertEqual(info["data"], str(self.root / "data"))
                self.assertEqual(info["version"], "2.2.16")

    def test_disabled_updates_skip_network_and_stale_public_cache(self):
        for edition, flag in (("Dev", "0"), ("Dev", "1"), ("Public", "0")):
            with self.subTest(edition=edition, flag=flag):
                os.environ["ULTRASUITE_SELF_UPDATE"] = flag
                module, client = self.shared_app(edition)
                module._upd.update(at=time.time(), data={"available": True, "asset": "cached-public"})
                with patch.object(module.httpx, "AsyncClient", side_effect=AssertionError("network")) as network, \
                     patch.object(module.subprocess, "Popen", side_effect=AssertionError("launch")) as launch:
                    for query in ("", "?force=true"):
                        response = client.get("/api/update" + query)
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response.json(), {"current": "2.2.16", "latest": "2.2.16",
                                                          "available": False, "edition": edition})
                    self.assertEqual(client.post("/api/update/install").status_code, 403)
                    network.assert_not_called()
                    launch.assert_not_called()

    def test_dev_feedback_refuses_before_identity_or_network_even_with_collector(self):
        os.environ.update(ULTRASUITE_EDITION="Dev", ULTRASUITE_COLLECTOR="https://collector.invalid")
        module = load_module("core/feedback.py")
        app = FastAPI()
        module.add_feedback_route(app, "fixture")
        with TestClient(app) as client, \
             patch.object(module, "anon_id", side_effect=AssertionError("identifier")) as identity, \
             patch.object(module.platform, "release", side_effect=AssertionError("platform")) as platform, \
             patch.object(module.httpx, "AsyncClient", side_effect=AssertionError("network")) as network:
            for message in ("A fixture message", "x"):
                self.assertEqual(client.post("/api/feedback", json={"message": message}).status_code, 403)
            identity.assert_not_called()
            platform.assert_not_called()
            network.assert_not_called()
        self.assertFalse((self.root / "conf" / "id.txt").exists())

    def test_public_feedback_still_sends_with_fake_client(self):
        os.environ.update(ULTRASUITE_EDITION="Public", ULTRASUITE_COLLECTOR="https://collector.invalid")
        module = load_module("core/feedback.py")
        app = FastAPI()
        module.add_feedback_route(app, "fixture", "2.2.16")
        fake = AsyncMock()
        fake.post.return_value.status_code = 200
        with TestClient(app) as client, \
             patch.object(module.httpx, "AsyncClient") as factory, \
             patch.object(module.platform, "release", return_value="fixture"):
            factory.return_value.__aenter__.return_value = fake
            self.assertEqual(client.post("/api/feedback", json={"message": "fixture feedback"}).json(), {"ok": True})
            body = fake.post.call_args.kwargs["json"]
            self.assertEqual(body["id"], (self.root / "conf" / "id.txt").read_text())
            self.assertEqual(body["message"], "fixture feedback")

    def test_icon_selection_and_validation_use_only_chosen_edition_directory(self):
        for edition in ("Public", "Dev"):
            with self.subTest(edition=edition):
                module, client = self.shared_app(edition)
                static = self.root / "static"
                (static / "icons" / "dev").mkdir(parents=True, exist_ok=True)
                (static / "icons" / "alarm.png").write_bytes(b"public fixture")
                (static / "icons" / "dev" / "alarm.png").write_bytes(b"dev fixture")
                (static / "icons" / "publiconly.png").write_bytes(b"public only")
                module.STATIC = str(static)
                response = client.get("/__ui/icons/alarm.png")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, b"dev fixture" if edition == "Dev" else b"public fixture")
                self.assertEqual(response.headers["content-type"], "image/png")
                self.assertEqual(client.get("/__ui/icons/publiconly.png").status_code, 404 if edition == "Dev" else 200)
                for name in ("missing.png", "Alarm.png", "alarm.svg", "alarm..png", "..%5Calarm.png", "%2E%2E%2Falarm.png"):
                    self.assertEqual(client.get("/__ui/icons/" + name).status_code, 404, name)

    def test_default_origins_keep_exact_public_ports(self):
        module, _ = self.shared_app()
        self.assert_origins(module, (8081, 8084, 8098, 8301), (18081, 18084, 18301, 8300, 8085, 3000))

    def test_configured_origins_accept_only_declared_ports(self):
        module, client = self.shared_app("Dev", "18081, 18084,18301,18081")
        self.assert_origins(module, (18081, 18084, 18301), (8081, 8084, 8098, 8301, 18091, 18093, 18300, 3000))
        for origin, status in (("http://localhost:18084", 200), ("http://localhost:8084", 403)):
            self.assertEqual(client.post("/api/theme", json={"theme": "dark"}, headers={"Origin": origin}).status_code, status)
            preflight = client.options("/api/theme", headers={"Origin": origin, "Access-Control-Request-Method": "POST"})
            self.assertEqual(preflight.status_code, 200 if status == 200 else 400)
            self.assertEqual(preflight.headers.get("access-control-allow-origin"), origin if status == 200 else None)

    def assert_origins(self, module, allowed, denied):
        for host in ("localhost", "127.0.0.1", "[::1]"):
            for scheme in ("http", "https"):
                for port in allowed:
                    self.assertTrue(module.request_allowed("POST", "localhost:18081", f"{scheme}://{host}:{port}"))
                for port in denied:
                    self.assertFalse(module.request_allowed("POST", "localhost:18081", f"{scheme}://{host}:{port}"))
        for origin in ("null", "http://localhost", "http://localhost:18084/", "http://localhost:18084.evil.invalid",
                       "http://user@localhost:18084", "http://localhost:18084\n", "http://localhost:018084", "ws://localhost:18084"):
            self.assertFalse(module.request_allowed("POST", "localhost", origin), origin)
        for origin in ("tauri://localhost", "http://tauri.localhost", "https://tauri.localhost"):
            self.assertTrue(module.request_allowed("POST", "localhost", origin))
        self.assertFalse(module.request_allowed("GET", "evil.invalid", None))
        self.assertTrue(module.request_allowed("POST", "localhost", None))

    def test_invalid_port_configuration_fails_closed_at_import(self):
        for value in ("", "18081,", "18081,.*", "18081|8081", "0", "65536", "-1", "1.5", "１８０８１"):
            with self.subTest(value=value), patch.dict(os.environ, {"ULTRASUITE_ORIGIN_PORTS": value}):
                with self.assertRaises(ValueError):
                    load_module("core/devtools.py")

    def test_websocket_guard_uses_edition_ports_before_downstream(self):
        module, _ = self.shared_app("Dev", "18081,18084,18301")

        async def check(origin, allowed):
            downstream, send = AsyncMock(), AsyncMock()
            scope = {"type": "websocket", "headers": [(b"host", b"localhost:18084"), (b"origin", origin)]}
            receive = AsyncMock()
            await module.LocalWebSocketGuard(downstream)(scope, receive, send)
            if allowed:
                downstream.assert_awaited_once_with(scope, receive, send)
            else:
                downstream.assert_not_awaited()
                send.assert_awaited_once_with({"type": "websocket.close", "code": 1008})

        for origin, allowed in ((b"http://localhost:18084", True), (b"http://localhost:8084", False), (b"null", False)):
            asyncio.run(check(origin, allowed))


if __name__ == "__main__":
    unittest.main()
