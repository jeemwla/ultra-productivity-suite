"""Behavior tests for tools/package_stage.py.

Every test stages real files in a disposable fixture repository created under D:\\claude and removed afterwards.
Nothing touches the real build\\stage, build output, installed apps or install directories. Junction tests use
`mklink /J` and are skipped where it is unavailable.
Run from the repo root:  python -W ignore -m unittest tests.test_package_stage -v
"""
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tools", "package_stage.py")
REAL_MANIFEST = os.path.join(ROOT, "tools", "package_manifest.json")
SCRATCH_PARENT = r"D:\claude"
MARKER = "FIXTURE-CONTENT-MUST-NOT-BE-PRINTED"

_spec = importlib.util.spec_from_file_location("package_stage", TOOL)
ps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ps)

# The data inputs build_release.ps1 passes to PyInstaller with --add-data (folders and single files).
PACKAGED_INPUTS = ["code_red_alarm/backend", "code_red_alarm/frontend", "pc_sentinel/backend", "pc_sentinel/frontend",
                   "insights", "core/feedback.py", "core/devtools.py", "core/static", "VERSION",
                   "core/editions.py", "core/edition_profiles.json", "core/edition_runtime.py"]

LISTED = ["VERSION", "app/backend/__init__.py", "app/backend/main.py", "app/frontend/index.html", "core/static/icons/a.png"]


def sha(data):
    return hashlib.sha256(data).hexdigest()


@unittest.skipUnless(os.path.isdir(SCRATCH_PARENT), "needs the D:\\claude scratch folder")
class Fixture(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="ultrasuite-packagestage-test-", dir=SCRATCH_PARENT)
        self.junctions = []
        self.repo = os.path.join(self.base, "repo")
        self.build = os.path.join(self.repo, "build")
        self.stage = os.path.join(self.build, "stage")
        self.outside = os.path.join(self.base, "outside")
        os.makedirs(self.build)
        os.makedirs(self.outside)
        self.keep = self.write_abs(os.path.join(self.outside, "keep.txt"), b"outside")
        for rel in LISTED:
            self.write(rel, (MARKER + " " + rel).encode())
        self.manifest = self.write_manifest(LISTED)

    def tearDown(self):
        for j in reversed(self.junctions):
            if os.path.lexists(j):
                os.rmdir(j)  # removes the junction itself, never its target
        shutil.rmtree(self.base)

    def write_abs(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def write(self, rel, data):
        return self.write_abs(os.path.join(self.repo, *rel.split("/")), data)

    def write_manifest(self, files, extra=None, name="tools/package_manifest.json"):
        doc = {"version": 1, "files": files}
        doc.update(extra or {})
        return self.write(name, json.dumps(doc).encode())

    def junction(self, link, target):
        r = subprocess.run(["cmd", "/c", "mklink", "/J", link, target], capture_output=True, text=True)
        if r.returncode != 0:
            self.skipTest("mklink /J is not available here")
        self.junctions.append(link)

    def run_stage(self, manifest=None, stage=None):
        return ps.stage_package(self.repo, manifest or self.manifest, stage or self.stage)

    def payload_files(self, stage=None):
        return ps._payload_files(os.path.join(stage or self.stage, ps.PAYLOAD))

    def leftovers(self):
        return [n for n in os.listdir(os.path.dirname(self.stage)) if ".tmp-" in n or ".old-" in n]

    def assertRefused(self, fragment=None, **kw):
        with self.assertRaises(ps.StageError) as ctx:
            self.run_stage(**kw)
        if fragment:
            self.assertIn(fragment, str(ctx.exception))
        self.assertEqual(self.leftovers(), [], "a refused run must not leave temporary folders")
        self.assertTrue(os.path.isfile(self.keep))
        return ctx.exception


class RealManifest(unittest.TestCase):
    def test_lists_exactly_the_tracked_packaged_inputs(self):
        try:
            out = subprocess.run(["git", "-C", ROOT, "ls-files", "--", *PACKAGED_INPUTS],
                                 capture_output=True, text=True, check=True).stdout.split()
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("git is not available")
        with open(REAL_MANIFEST, encoding="utf-8") as f:
            files = json.load(f)["files"]
        self.assertEqual(sorted(files), sorted(out), "manifest must match the tracked files build_release.ps1 packages")
        self.assertEqual(len(files), len(set(files)))
        for entry in files:
            ps.check_entry(entry)  # every real entry passes the name rules

    def test_real_sources_pass_the_file_checks_without_copying(self):
        # lstat only: regular files, no links or junctions, single hard link, under the size cap. Nothing is written.
        planned = ps._plan_sources(ROOT, ps.load_manifest(REAL_MANIFEST))
        self.assertEqual(len(planned), len(json.load(open(REAL_MANIFEST, encoding="utf-8"))["files"]))

    def test_loads_through_the_strict_loader(self):
        self.assertEqual(len(ps.load_manifest(REAL_MANIFEST)), len(json.load(open(REAL_MANIFEST, encoding="utf-8"))["files"]))


class CleanStaging(Fixture):
    def test_stages_exactly_the_listed_files_with_a_matching_inventory(self):
        count, inventory = self.run_stage()
        self.assertEqual(count, len(LISTED))
        self.assertEqual(self.payload_files(), set(LISTED))
        self.assertEqual(inventory, os.path.join(self.stage, ps.INVENTORY))
        self.assertFalse(os.path.exists(os.path.join(self.stage, ps.PAYLOAD, ps.INVENTORY)), "inventory is outside the payload")
        with open(inventory, encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertEqual(lines[0] + "\n", ps.INVENTORY_HEADER)
        expected = []
        for rel in sorted(LISTED):
            data = (MARKER + " " + rel).encode()
            expected.append(f"{sha(data)}  {len(data)}  {rel}")
            with open(os.path.join(self.stage, ps.PAYLOAD, *rel.split("/")), "rb") as f:
                self.assertEqual(f.read(), data)
        self.assertEqual(lines[1:], expected)
        self.assertEqual(self.leftovers(), [])

    def test_inventory_is_byte_identical_across_runs(self):
        _, inventory = self.run_stage()
        first = open(inventory, "rb").read()
        _, inventory = self.run_stage()  # replaces the previous valid stage
        self.assertEqual(open(inventory, "rb").read(), first)
        shuffled = self.write_manifest(list(reversed(LISTED)), name="tools/other.json")
        _, inventory = self.run_stage(manifest=shuffled)
        self.assertEqual(open(inventory, "rb").read(), first, "manifest order must not change the inventory")
        self.assertEqual(self.leftovers(), [])

    def test_stray_private_cache_log_database_and_backup_files_are_not_copied(self):
        strays = [".env", "settings.env", "app/backend/settings.env", "app/backend/insights.db", "app/backend/run.log",
                  "app/backend/main.py.orig", "app/backend/__pycache__/main.cpython-313.pyc", "app/data/alarms.json",
                  "app/backend/code_red_siren.wav", "private/notes.md", "app/frontend/credentials.json"]
        for rel in strays:
            self.write(rel, b"stray")
        self.run_stage()
        self.assertEqual(self.payload_files(), set(LISTED))

    def test_replacing_a_previous_stage_drops_files_no_longer_listed(self):
        self.run_stage()
        smaller = self.write_manifest(LISTED[:2], name="tools/small.json")
        count, _ = self.run_stage(manifest=smaller)
        self.assertEqual(count, 2)
        self.assertEqual(self.payload_files(), set(LISTED[:2]))
        self.assertEqual(self.leftovers(), [])

    def test_cli_prints_only_count_and_inventory_path(self):
        r = subprocess.run([sys.executable, "-I", TOOL, "--repo", self.repo, "--manifest", self.manifest, "--stage", self.stage],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), [f"staged {len(LISTED)} files", f"inventory {os.path.join(self.stage, ps.INVENTORY)}"])
        self.assertNotIn(MARKER, r.stdout + r.stderr)

    def test_cli_refusal_exits_2_without_printing_contents(self):
        bad = self.write_manifest(LISTED + ["app/backend/missing.py"], name="tools/bad.json")
        r = subprocess.run([sys.executable, "-I", TOOL, "--repo", self.repo, "--manifest", bad, "--stage", self.stage],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("missing", r.stderr)
        self.assertNotIn(MARKER, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(self.stage))


class ManifestRefusals(Fixture):
    def refuse_manifest(self, files, fragment=None, extra=None):
        m = self.write_manifest(files, extra=extra, name="tools/case.json")
        err = self.assertRefused(fragment, manifest=m)
        self.assertFalse(os.path.exists(self.stage))
        return err

    def test_forbidden_files_are_refused_even_when_listed(self):
        for rel in [".env", "app/settings.env", "app/x.env", "app/insights.db", "app/x.sqlite3", "app/run.log",
                    "app/main.py.orig", "app/main.py.bak", "app/__pycache__/m.pyc", "app/data/alarms.json",
                    "private/notes.md", "app/credentials.json", "app/api_token.txt", "app/server.key", "app/cert.pem",
                    "app/device_config.json", "app/code_red_siren.wav", "logs/today.txt", ".devdata/x.json",
                    "build/dist/x.py", "app/node_modules/x.js"]:
            with self.subTest(rel=rel):
                self.write(rel, b"listed but forbidden")
                self.refuse_manifest(LISTED + [rel], "forbidden")

    def test_missing_listed_file_is_refused(self):
        self.refuse_manifest(LISTED + ["app/backend/missing.py"], "missing")

    def test_directory_listed_as_a_file_is_refused(self):
        self.refuse_manifest(["app/backend"], "not a regular file")

    def test_unsafe_path_forms_are_refused(self):
        for rel in ["/app/backend/main.py", "D:/x.py", "C:x.py", "../outside/keep.txt", "app/../VERSION", "app\\backend\\main.py",
                    "app//main.py", "./VERSION", "app/CON", "app/nul.txt", "app/name.", "app/name ", " VERSION",
                    "PROGRA~1/x.py", "app/main.py:stream", "app/a*b.py", ""]:
            with self.subTest(rel=rel):
                self.refuse_manifest(LISTED + [rel])

    def test_destination_collisions_are_refused(self):
        self.write("app/backend/Main.py", b"x")
        self.refuse_manifest(["app/backend/main.py", "app/backend/Main.py"], "twice")
        self.refuse_manifest(["VERSION", "VERSION"], "twice")
        self.refuse_manifest(["app/backend", "app/backend/main.py"], "both a file and a folder")
        self.write("app/backend.py", b"x")  # sorts between the two conflicting entries
        self.refuse_manifest(["app/backend", "app/backend.py", "app/backend/main.py"], "both a file and a folder")
        self.refuse_manifest(["app/backend/main.py", "APP/BACKEND"], "both a file and a folder")

    def test_malformed_manifests_are_refused(self):
        self.refuse_manifest(LISTED, "exactly", extra={"include": ["*.py"]})
        self.refuse_manifest([], "non-empty")
        dup = self.write("tools/dup.json", b'{"version": 1, "version": 1, "files": ["VERSION"]}')
        self.assertRefused("duplicate key", manifest=dup)
        wrong = self.write("tools/v2.json", json.dumps({"version": 2, "files": ["VERSION"]}).encode())
        self.assertRefused("version", manifest=wrong)
        globbed = self.write("tools/glob.json", json.dumps({"version": 1, "files": ["app/backend/*.py"]}).encode())
        self.assertRefused(manifest=globbed)

    def test_hard_linked_source_is_refused(self):
        try:
            os.link(self.keep, os.path.join(self.repo, "app", "backend", "linked.py"))
        except OSError:
            self.skipTest("hard links are not available here")
        self.refuse_manifest(LISTED + ["app/backend/linked.py"], "hard link")


class PathRefusals(Fixture):
    def test_relative_and_drive_relative_arguments_are_refused(self):
        rel_stage = os.path.relpath(self.stage)
        for kwargs in [dict(repo="repo"), dict(repo="D:repo"), dict(stage=rel_stage), dict(stage="D:build\\stage"),
                       dict(manifest="tools\\package_manifest.json")]:
            with self.subTest(**kwargs):
                args = dict(repo=self.repo, manifest=self.manifest, stage=self.stage)
                args.update(kwargs)
                with self.assertRaises(ps.StageError):
                    ps.stage_package(**args)

    def test_stage_must_be_a_strict_child_of_the_repository_build_folder(self):
        for stage in [self.build, self.repo, os.path.join(self.repo, "app", "stage"), os.path.join(self.outside, "stage"),
                      self.build + "x\\stage", os.path.join(self.build, "..", "stage")]:
            with self.subTest(stage=stage):
                with self.assertRaises(ps.StageError):
                    self.run_stage(stage=stage)
        self.assertTrue(os.path.isfile(self.keep))
        self.assertEqual(sorted(os.listdir(self.repo)), ["VERSION", "app", "build", "core", "tools"])

    def test_manifest_inside_build_or_outside_repo_is_refused(self):
        inside = self.write_abs(os.path.join(self.build, "m.json"), json.dumps({"version": 1, "files": LISTED}).encode())
        self.assertRefused("--manifest", manifest=inside)
        outside = self.write_abs(os.path.join(self.outside, "m.json"), json.dumps({"version": 1, "files": LISTED}).encode())
        self.assertRefused("--manifest", manifest=outside)

    def test_source_folder_junction_is_refused(self):
        self.write_abs(os.path.join(self.outside, "src", "evil.py"), b"outside source")
        self.junction(os.path.join(self.repo, "app", "linked"), os.path.join(self.outside, "src"))
        self.write_manifest(LISTED + ["app/linked/evil.py"])
        self.assertRefused("reparse point")
        self.assertFalse(os.path.exists(self.stage))

    def test_stage_parent_junction_is_refused(self):
        self.junction(os.path.join(self.build, "linked"), self.outside)
        self.assertRefused(stage=os.path.join(self.build, "linked", "stage"))
        self.assertEqual(sorted(os.listdir(self.outside)), ["keep.txt"], "nothing created through the junction")

    def test_build_folder_junction_is_refused(self):
        os.rmdir(self.build)
        self.junction(self.build, self.outside)
        self.assertRefused("reparse point")
        self.assertEqual(sorted(os.listdir(self.outside)), ["keep.txt"])

    def test_existing_stage_that_is_a_junction_is_refused(self):
        self.junction(self.stage, self.outside)
        self.assertRefused("reparse point")
        self.assertTrue(os.path.isfile(self.keep))

    def test_junction_inside_an_existing_stage_is_refused_and_not_followed(self):
        self.run_stage()
        self.junction(os.path.join(self.stage, ps.PAYLOAD, "app", "linked"), self.outside)
        self.assertRefused()
        self.assertTrue(os.path.isfile(self.keep))
        self.assertTrue(os.path.isdir(os.path.join(self.stage, ps.PAYLOAD)), "the previous stage is left as it was")


class FailureAndCleanup(Fixture):
    def test_copy_failure_leaves_the_previous_stage_and_no_partial_stage(self):
        self.run_stage()
        before = open(os.path.join(self.stage, ps.INVENTORY), "rb").read()
        self.write("app/backend/main.py", b"changed")
        real, calls = ps._read_source, []

        def fail_third(path, st):
            calls.append(path)
            if len(calls) == 3:
                raise OSError(28, "simulated disk full", path)
            return real(path, st)

        with mock.patch.object(ps, "_read_source", fail_third):
            with self.assertRaises(OSError):
                self.run_stage()
        self.assertEqual(self.leftovers(), [])
        self.assertEqual(open(os.path.join(self.stage, ps.INVENTORY), "rb").read(), before, "previous stage untouched")

    def test_source_changing_while_staged_is_refused(self):
        real = ps._read_source

        def grow(path, st):
            data = real(path, st)
            if path.endswith("main.py"):
                with open(path, "ab") as f:
                    f.write(b"# appended during copy")
            return data

        with mock.patch.object(ps, "_read_source", grow):
            self.assertRefused("changed while")
        self.assertFalse(os.path.exists(self.stage))

    def test_unrecognized_existing_folder_is_never_replaced(self):
        os.makedirs(self.stage)
        precious = self.write_abs(os.path.join(self.stage, "dist-output.exe"), b"not a stage")
        self.assertRefused("does not look like a package stage")
        self.assertTrue(os.path.isfile(precious))

    def test_existing_stage_whose_payload_no_longer_matches_its_inventory_is_kept(self):
        self.run_stage()
        extra = self.write_abs(os.path.join(self.stage, ps.PAYLOAD, "added-by-hand.txt"), b"x")
        self.assertRefused("does not match its inventory")
        self.assertTrue(os.path.isfile(extra))

    def test_cleanup_is_bounded_and_all_or_nothing(self):
        tree = os.path.join(self.build, "big")
        for i in range(10):
            self.write_abs(os.path.join(tree, f"f{i}.txt"), b"x")
        with self.assertRaises(ps.StageError):
            ps.remove_tree(tree, self.build, limit=5)
        self.assertEqual(len(os.listdir(tree)), 10, "nothing deleted when the limit is exceeded")
        with self.assertRaises(ps.StageError):
            ps.remove_tree(self.build, self.build)
        with self.assertRaises(ps.StageError):
            ps.remove_tree(self.outside, self.build)
        ps.remove_tree(tree, self.build)
        self.assertFalse(os.path.exists(tree))
        self.assertTrue(os.path.isfile(self.keep))


class OutputVerification(Fixture):
    def setUp(self):
        super().setUp()
        self.run_stage()
        self.internal = os.path.join(self.build, "dist", "core", "_internal")
        shutil.copytree(os.path.join(self.stage, ps.PAYLOAD), self.internal)
        # library folders PyInstaller adds are outside the judged app folders, even with names the stage forbids
        self.write_abs(os.path.join(self.internal, "certifi", "cacert.pem"), b"library data")
        self.write_abs(os.path.join(self.internal, "python313.dll"), b"runtime")

    def verify(self):
        return ps.verify_output(self.stage, self.internal)

    def test_matching_output_passes(self):
        self.assertEqual(self.verify(), len(LISTED))

    def test_stray_file_in_an_app_folder_is_named_and_refused(self):
        self.write_abs(os.path.join(self.internal, "app", "backend", "main.py.orig"), b"x")
        self.write_abs(os.path.join(self.internal, "app", "backend", "__pycache__", "main.cpython-313.pyc"), b"x")
        with self.assertRaises(ps.StageError) as ctx:
            self.verify()
        self.assertIn("2 unlisted file(s)", str(ctx.exception))
        self.assertIn("app/backend/main.py.orig", str(ctx.exception))

    def test_missing_or_changed_packaged_file_is_refused(self):
        os.remove(os.path.join(self.internal, "VERSION"))
        with self.assertRaises(ps.StageError) as ctx:
            self.verify()
        self.assertIn("missing file(s): VERSION", str(ctx.exception))
        self.write_abs(os.path.join(self.internal, "VERSION"), b"different")
        with self.assertRaises(ps.StageError) as ctx:
            self.verify()
        self.assertIn("differs", str(ctx.exception))

    def test_junction_in_an_app_folder_is_refused(self):
        self.junction(os.path.join(self.internal, "app", "linked"), self.outside)
        with self.assertRaises(ps.StageError) as ctx:
            self.verify()
        self.assertIn("link or junction", str(ctx.exception))

    def test_cli_verify_mode(self):
        r = subprocess.run([sys.executable, "-I", TOOL, "--stage", self.stage, "--verify-output", self.internal],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), [f"verified {len(LISTED)} packaged files"])
        self.write_abs(os.path.join(self.internal, "app", "run.log"), b"x")
        r = subprocess.run([sys.executable, "-I", TOOL, "--stage", self.stage, "--verify-output", self.internal],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("app/run.log", r.stderr)
        self.assertNotIn(MARKER, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
