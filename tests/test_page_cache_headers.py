"""App pages and scripts must be revalidated on every load (Cache-Control: no-cache), so an update is never hidden
behind an old cached copy in the app windows. Checks real responses from the routes."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pc_sentinel"))


class CacheHeaders(unittest.TestCase):
    def test_psu_picker_script_is_revalidated(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend import psu_catalog
        app = FastAPI()
        psu_catalog.register(app)
        r = TestClient(app).get("/psu_picker.js")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers.get("cache-control"), "no-cache")
        self.assertIn("javascript", r.headers.get("content-type", ""))


if __name__ == "__main__":
    unittest.main()
