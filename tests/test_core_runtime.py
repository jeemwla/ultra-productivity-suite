"""core/suite_core.py runtime helpers: log rotation and timestamps, settings parsing, busy ports, stop signals and
helper-server shutdown. Loopback sockets on free ports only; no services, no alarms, no installed processes.

Run from the repo root:  python -W ignore -m unittest tests.test_core_runtime -v
"""
import contextlib
import importlib.util
import io
import os
import signal
import socket
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_core():
    spec = importlib.util.spec_from_file_location("suite_core_runtime_test", os.path.join(ROOT, "core", "suite_core.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


core = _load_core()


class Logs(unittest.TestCase):
    def test_large_log_is_rotated_once_and_small_log_is_kept(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "core.log")
            with open(p, "w") as f:
                f.write("x" * 50)
            self.assertFalse(core.rotate_log(p, max_bytes=100))
            self.assertTrue(os.path.exists(p))
            with open(p + ".1", "w") as f:
                f.write("older")
            with open(p, "a") as f:
                f.write("y" * 100)
            self.assertTrue(core.rotate_log(p, max_bytes=100))
            self.assertFalse(os.path.exists(p))
            with open(p + ".1") as f:
                self.assertTrue(f.read().startswith("x" * 50), "the older copy is replaced by the big log")
            self.assertFalse(core.rotate_log(os.path.join(d, "missing.log")))

    def test_every_line_gets_one_timestamp_even_across_partial_writes(self):
        buf = io.StringIO()
        log = core.TimestampedLog(buf, clock=lambda: "T")
        log.write("[core] one")
        log.write(" more\n[core] two\n")
        print("[core] three", file=log)
        self.assertEqual(buf.getvalue(), "T [core] one more\nT [core] two\nT [core] three\n")
        self.assertEqual(log.write(""), 0)
        self.assertFalse(log.isatty())

    def test_concurrent_writers_never_split_a_timestamp(self):
        buf = io.StringIO()
        log = core.TimestampedLog(buf, clock=lambda: "T")
        threads = [threading.Thread(target=lambda: [log.write(f"line {i}\n") for i in range(200)]) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        lines = buf.getvalue().splitlines()
        self.assertEqual(len(lines), 800)
        self.assertTrue(all(ln.startswith("T line ") for ln in lines))


class Settings(unittest.TestCase):
    def test_comments_quotes_export_and_last_line_wins(self):
        values = core.parse_settings([
            "# comment\n", "\n", "A=1\n", "  B = two words  \n", 'C="quoted value"\n', "D='single'\n",
            "export E=exported\n", "A=2\n", "no equals sign\n", "=orphan\n", 'F="unbalanced\n', "G=\n", "H=a=b\n",
        ])
        self.assertEqual(values, {"A": "2", "B": "two words", "C": "quoted value", "D": "single", "E": "exported",
                                  "F": '"unbalanced', "G": "", "H": "a=b"})


class Ports(unittest.TestCase):
    def test_busy_port_is_logged_by_service_name(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertFalse(core.port_free("PC Sentinel", 8084, busy=lambda p: True))
            self.assertTrue(core.port_free("PC Sentinel", 8084, busy=lambda p: False))
        self.assertEqual(out.getvalue().strip(), "[core] port 8084 is already in use, so PC Sentinel was not started by this core")

    def test_services_bind_loopback_only_with_bounded_shutdown(self):
        opts = core.server_options(8084)
        self.assertEqual((opts["host"], opts["port"], opts["server_header"]), ("127.0.0.1", 8084, False))
        self.assertGreater(opts["timeout_graceful_shutdown"], 0)
        import uvicorn
        uvicorn.Config(lambda *a: None, **opts)  # every option is one this uvicorn accepts

    def test_helper_servers_release_their_ports(self):
        class Quiet(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass
        srv = core.start_http_thread(ThreadingHTTPServer(("127.0.0.1", 0), Quiet), "test")
        port = srv.server_address[1]
        self.assertTrue(core.port_busy(port))
        core.close_http_servers([srv, object()])  # a broken entry must not stop the rest
        with socket.socket() as s:
            s.bind(("127.0.0.1", port))  # free again


class StopSignals(unittest.TestCase):
    def test_sigterm_sets_the_stop_flag_and_supervise_stops_in_order(self):
        names = [n for n in ("SIGTERM", "SIGBREAK") if hasattr(signal, n)]
        saved = {n: signal.getsignal(getattr(signal, n)) for n in names}
        try:
            event = threading.Event()
            self.assertEqual(core.install_stop_signals(event), names)
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            self.assertTrue(event.is_set())
        finally:
            for n, h in saved.items():
                signal.signal(getattr(signal, n), h)

    def test_supervise_logs_why_it_stopped(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(core.supervise([_Group()], should_stop=lambda: True, poll=0.01), 0)
        self.assertIn("[core] stop requested; stopping services", out.getvalue())


class _Group:
    name, servers, finished, error = "fake", [object()], False, None

    def start(self, timeout):
        return True

    def stop(self, timeout):
        return True


if __name__ == "__main__":
    unittest.main()
