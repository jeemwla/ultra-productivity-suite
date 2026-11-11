"""Behavior tests for tools/check_build_env.py (exact build-tool pins and the build input record).

Git fixtures live in a disposable folder under D:\\claude. Nothing is installed and no build runs.
Run from the repo root:  python -W ignore -m unittest tests.test_build_env -v
"""
import importlib.metadata
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tools", "check_build_env.py")
SCRATCH_PARENT = r"D:\claude"

_spec = importlib.util.spec_from_file_location("check_build_env", TOOL)
env = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(env)


def remove(path):
    """Delete a fixture folder, including git's read-only object files."""
    def writable(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)
    shutil.rmtree(path, onexc=writable) if sys.version_info >= (3, 12) else shutil.rmtree(path, onerror=writable)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


class Pins(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ultrasuite-buildenv-test-", dir=SCRATCH_PARENT if os.path.isdir(SCRATCH_PARENT) else None)
        self.addCleanup(remove, self.dir)

    def pins(self, text):
        return env.read_pins(write(os.path.join(self.dir, "req.txt"), text))

    def test_exact_pins_with_comments_are_read(self):
        got = self.pins("# build tools\nPyInstaller==6.22.3  # comment\n\npyinstaller_hooks-contrib==2026.10\n")
        self.assertEqual(got, {"pyinstaller": ("PyInstaller", "6.22.3"), "pyinstaller-hooks-contrib": ("pyinstaller_hooks-contrib", "2026.10")})

    def test_loose_duplicate_or_empty_pins_are_refused(self):
        for text in ["pyinstaller>=6\n", "pyinstaller\n", "pyinstaller==6.*\n", "pyinstaller==6.22.3; sys_platform == 'win32'\n",
                     "-r other.txt\n", "pyinstaller==6.22.3\nPyInstaller==6.22.2\n", "# nothing\n"]:
            with self.subTest(text=text):
                with self.assertRaises(env.EnvError):
                    self.pins(text)

    def test_version_mismatch_and_missing_package_are_reported_by_name(self):
        pins = {"pyinstaller": ("pyinstaller", "6.22.3"), "pillow": ("pillow", "13.0.0")}
        with self.assertRaises(env.EnvError) as ctx:
            env.check_pins(pins, {"pyinstaller": ("pyinstaller", "6.22.2")})
        self.assertIn("pyinstaller is 6.22.2, pinned 6.22.3", str(ctx.exception))
        self.assertIn("pillow is not installed", str(ctx.exception))
        env.check_pins(pins, {"pyinstaller": ("pyinstaller", "6.22.3"), "pillow": ("Pillow", "13.0.0")})

    def test_profile_prefix_is_masked(self):
        with mock.patch.dict(os.environ, {"USERPROFILE": r"C:\Users\someone"}):
            self.assertEqual(env._private(r"c:\users\someone\Python\python.exe"), r"%USERPROFILE%\Python\python.exe")

    def test_real_pins_file_is_exact(self):
        pins = env.read_pins(os.path.join(ROOT, "requirements-build.txt"))
        self.assertIn("pyinstaller", pins)

    def test_relative_paths_are_refused(self):
        r = subprocess.run([sys.executable, "-I", TOOL, "--repo", ".", "--requirements", "requirements-build.txt",
                            "--record", "build\\env.json"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("full path", r.stderr)


TOOLS = all(shutil.which(t) for t in ("git", "node", "npm.cmd" if os.name == "nt" else "npm", "rustc", "cargo"))


@unittest.skipUnless(os.path.isdir(SCRATCH_PARENT), "needs the D:\\claude scratch folder")
@unittest.skipUnless(TOOLS, "needs git, node, npm, rustc and cargo on PATH")
class Record(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ultrasuite-buildenv-test-", dir=SCRATCH_PARENT)
        self.addCleanup(remove, self.dir)
        self.repo = os.path.join(self.dir, "repo")
        write(os.path.join(self.repo, "a.txt"), "one\n")
        for args in (["init", "-q"], ["add", "a.txt"],
                     ["-c", "user.name=fixture", "-c", "user.email=fixture@invalid", "commit", "-q", "-m", "fixture"]):
            subprocess.run(["git", "-C", self.repo, *args], check=True, capture_output=True)
        try:
            version = importlib.metadata.version("pip")
        except importlib.metadata.PackageNotFoundError:
            self.skipTest("pip metadata is not available to pin in the fixture")
        self.req = write(os.path.join(self.dir, "req.txt"), f"pip=={version}\n")
        self.record = os.path.join(self.dir, "env.json")

    def run_tool(self, *extra, req=None):
        return subprocess.run([sys.executable, "-I", TOOL, "--repo", self.repo, "--requirements", req or self.req,
                               "--record", self.record, *extra], capture_output=True, text=True)

    def test_clean_source_records_commit_pins_and_tools(self):
        r = self.run_tool()
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.load(open(self.record, encoding="utf-8"))
        head = subprocess.run(["git", "-C", self.repo, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        self.assertEqual(data["source"], {"commit": head, "trackedChanges": []})
        self.assertIn("pip", data["pinned"])
        self.assertTrue(all(data["tools"].values()))
        self.assertTrue(any(line.lower().startswith("pip==") for line in data["installed"]))
        home = os.environ.get("USERPROFILE", "")
        if home:
            self.assertNotIn(home.lower(), json.dumps(data).lower())

    def test_tracked_changes_are_refused_unless_allowed(self):
        write(os.path.join(self.repo, "a.txt"), "two\n")
        r = self.run_tool()
        self.assertEqual(r.returncode, 2)
        self.assertIn("uncommitted", r.stderr)
        self.assertFalse(os.path.exists(self.record))
        r = self.run_tool("--allow-dirty")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.load(open(self.record, encoding="utf-8"))["source"]["trackedChanges"], ["a.txt"])

    def test_wrong_pin_is_refused_and_no_record_written(self):
        bad = write(os.path.join(self.dir, "bad.txt"), "pip==0.0.1\n")
        r = self.run_tool(req=bad)
        self.assertEqual(r.returncode, 2)
        self.assertIn("pinned 0.0.1", r.stderr)
        self.assertFalse(os.path.exists(self.record))


if __name__ == "__main__":
    unittest.main()
