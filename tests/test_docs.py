"""Docs stay accurate: relative links resolve, documented ports match core/edition_profiles.json, and the owner's
writing rules hold (no em dashes). Reads files only.

Run from the repo root:  python -W ignore -m unittest tests.test_docs -v
"""
import glob
import json
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = [os.path.join(ROOT, "README.md")] + sorted(glob.glob(os.path.join(ROOT, "docs", "*.md")))


def text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class Docs(unittest.TestCase):
    def test_relative_links_point_at_files(self):
        for path in DOCS:
            for target in re.findall(r"\]\(([^)\s]+)\)", text(path)):
                if re.match(r"[a-z]+:", target) or target.startswith("#"):
                    continue
                file_part = target.split("#", 1)[0]
                resolved = os.path.normpath(os.path.join(os.path.dirname(path), file_part))
                self.assertTrue(os.path.exists(resolved), f"{os.path.relpath(path, ROOT)} links to missing {target}")

    def test_no_em_dashes(self):
        for path in DOCS:
            self.assertNotIn("—", text(path), os.path.relpath(path, ROOT))

    def test_core_port_table_matches_the_edition_profiles(self):
        with open(os.path.join(ROOT, "core", "edition_profiles.json"), encoding="utf-8") as f:
            profiles = json.load(f)["profiles"]
        rows = {}
        for line in text(os.path.join(ROOT, "docs", "CORE.md")).splitlines():
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) >= 3 and cells[1].isdigit() and cells[2].isdigit():
                rows[cells[0]] = (int(cells[1]), int(cells[2]))
        names = {"alarm": "Code Red Alarm API and page", "sentinel": "PC Sentinel API and page", "audio": "Speaker helper",
                 "addon": "Local add-on (optional)", "insights_collector": "Insights collector",
                 "insights_dashboard": "Insights dashboard"}
        for key, label in names.items():
            self.assertEqual(rows.get(label), (profiles["Public"]["ports"][key], profiles["Dev"]["ports"][key]), label)

    def test_sentinel_pages_in_screens_doc_match_the_app(self):
        app = text(os.path.join(ROOT, "pc_sentinel", "frontend", "index.html"))
        block = app[app.index("const PAGES"):]
        block = block[:block.index("];")]
        pages = re.findall(r'\["\w+", "([^"]+)"', block)
        screens = text(os.path.join(ROOT, "docs", "SCREENS.md"))
        for page in pages:
            self.assertIn(f"| {page} |", screens, page)


if __name__ == "__main__":
    unittest.main()
