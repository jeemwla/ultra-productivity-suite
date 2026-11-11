"""Handshake regression tests. No listeners, real speed tests, or audio devices."""
import asyncio
import importlib
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core"))
sys.path.insert(0, os.path.join(ROOT, "pc_sentinel"))
import devtools


class LocalOrigins(unittest.TestCase):
    def test_exact_page_origins(self):
        for host in ("localhost", "127.0.0.1", "[::1]"):
            for port in (8081, 8084, 8098, 8301):
                for scheme in ("http", "https"):
                    with self.subTest(host=host, port=port, scheme=scheme):
                        self.assertTrue(devtools.request_allowed("POST", "localhost:8081", f"{scheme}://{host}:{port}"))

    def test_other_origins_rejected(self):
        for origin in ("null", "https://evil.example", "http://localhost:3000", "http://localhost",
                       "http://localhost:8300", "http://127.0.0.1:8081.evil.example", "http://localhost:8081/",
                       "http://user@localhost:8081", "http://localhost:8081\n", "ws://localhost:8081"):
            with self.subTest(origin=origin):
                self.assertFalse(devtools.request_allowed("POST", "localhost:8081", origin))

    def test_denial_never_calls_downstream(self):
        async def check(headers):
            downstream, send = AsyncMock(), AsyncMock()
            await devtools.LocalWebSocketGuard(downstream)({"type": "websocket", "headers": headers}, AsyncMock(), send)
            downstream.assert_not_awaited()
            send.assert_awaited_once_with({"type": "websocket.close", "code": 1008})
        for headers in ([], [(b"host", b"evil.example")],
                        [(b"host", b"localhost"), (b"origin", b"null")],
                        [(b"host", b"localhost"), (b"origin", b"")],
                        [(b"host", b"localhost"), (b"origin", b"http://localhost:8081"), (b"origin", b"https://evil.example")],
                        [(b"host", b"localhost"), (b"host", b"evil.example")]):
            with self.subTest(headers=headers):
                asyncio.run(check(headers))


class RealSocketRoutes(unittest.TestCase):
    def test_sentinel_sockets_deny_before_starting_speed_test(self):
        module = importlib.import_module("backend.main")
        with patch.object(module.speedtest, "run", new_callable=AsyncMock) as run:
            client = TestClient(module.app, base_url="http://127.0.0.1:8084")
            try:
                for path in ("/ws", "/ws/speedtest"):
                    for headers in ({"Origin": "https://evil.example"}, {"Origin": "null"},
                                    {"Origin": "http://localhost:3000"}, {"Host": "evil.example"}):
                        with self.subTest(path=path, headers=headers):
                            with self.assertRaises(WebSocketDisconnect) as denied:
                                with client.websocket_connect("ws://127.0.0.1:8084" + path, headers=headers):
                                    self.fail("untrusted handshake accepted")
                            self.assertEqual(denied.exception.code, 1008)
                run.assert_not_awaited()
                with client.websocket_connect("ws://127.0.0.1:8084/ws/speedtest", headers={"Origin": "http://localhost:8084"}):
                    pass
                run.assert_awaited_once()
                for headers in ({"Origin": "http://127.0.0.1:8084"}, {}):
                    with client.websocket_connect("ws://127.0.0.1:8084/ws", headers=headers) as ws:
                        self.assertIn("hardware", ws.receive_json())
            finally:
                client.close()

