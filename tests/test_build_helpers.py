"""Behavior tests for tools/build_helpers.ps1 under Windows PowerShell 5.1.

They run real child commands (cmd.exe, Python) and real directory junctions in a throwaway folder under D:\\claude.
Nothing touches the repository build output, an installed app or any install directory. Skipped off Windows, when
Windows PowerShell 5.1 is missing, or when D:\\claude does not exist.
Each disposable PowerShell test session uses RemoteSigned for locally authored code. No persistent policy is changed;
Group Policy still takes precedence. See Microsoft Learn: about_Execution_Policies, 'Set a different policy for one session'.
Run from the repo root:  python -W ignore -m unittest tests.test_build_helpers -v
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HELPERS = os.path.join(ROOT, "tools", "build_helpers.ps1")
POWERSHELL = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
SCRATCH_PARENT = r"D:\claude"


def ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def run_ps(body, timeout=60):
    marker = "ULTRASUITE_BUILD_HELPERS_LOADED"
    script = "$ErrorActionPreference = 'Stop'; . " + ps_quote(HELPERS) + "; Write-Output '" + marker + "'; " + body
    result = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "RemoteSigned", "-Command", script],
                            capture_output=True, text=True, timeout=timeout)
    if marker not in result.stdout.splitlines():
        raise RuntimeError("Build helper did not load; refusing to count an unrelated shell failure as a passing rejection test. "
                           + result.stderr.strip())
    return result


@unittest.skipUnless(os.name == "nt" and os.path.isfile(POWERSHELL), "needs Windows PowerShell 5.1")
@unittest.skipUnless(os.path.isfile(HELPERS), "tools/build_helpers.ps1 is missing")
class NativeCommands(unittest.TestCase):
    def test_success_streams_output_and_continues(self):
        r = run_ps("Invoke-Native -Step 'echo' -FilePath 'cmd.exe' -ArgumentList @('/c', 'echo', 'hello-from-child'); Write-Output 'AFTER-STEP'")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("hello-from-child", r.stdout)
        self.assertIn("AFTER-STEP", r.stdout)

    def test_nonzero_exit_stops_the_script_at_that_step(self):
        r = run_ps("Invoke-Native -Step 'demo step' -FilePath 'cmd.exe' -ArgumentList @('/c', 'exit', '7'); Write-Output 'AFTER-STEP'")
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("AFTER-STEP", r.stdout, "the build must stop at the failing step")
        self.assertIn("demo step", r.stdout + r.stderr)
        self.assertIn("code 7", r.stdout + r.stderr)

    def test_python_child_exit_codes(self):
        ok = run_ps(f"Invoke-Native -Step 'py ok' -FilePath {ps_quote(sys.executable)} -ArgumentList @('-c', 'import sys; sys.exit(0)'); Write-Output 'AFTER-STEP'")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertIn("AFTER-STEP", ok.stdout)
        bad = run_ps(f"Invoke-Native -Step 'py fail' -FilePath {ps_quote(sys.executable)} -ArgumentList @('-c', 'import sys; sys.exit(3)'); Write-Output 'AFTER-STEP'")
        self.assertNotEqual(bad.returncode, 0)
        self.assertNotIn("AFTER-STEP", bad.stdout)
        self.assertIn("code 3", bad.stdout + bad.stderr)

    def test_a_previous_failure_does_not_leak_into_the_next_step(self):
        r = run_ps("try { Invoke-Native -Step 'first' -FilePath 'cmd.exe' -ArgumentList @('/c', 'exit', '5') } catch { Write-Output 'CAUGHT' }; "
                   "Invoke-Native -Step 'second' -FilePath 'cmd.exe' -ArgumentList @('/c', 'echo', 'ok'); Write-Output 'AFTER-SECOND'")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("CAUGHT", r.stdout)
        self.assertIn("AFTER-SECOND", r.stdout)

    def test_missing_command_stops_the_script(self):
        r = run_ps("Invoke-Native -Step 'missing' -FilePath 'ultrasuite-no-such-command-xyz' -ArgumentList @(); Write-Output 'AFTER-STEP'")
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("AFTER-STEP", r.stdout)


@unittest.skipUnless(os.name == "nt" and os.path.isfile(POWERSHELL), "needs Windows PowerShell 5.1")
@unittest.skipUnless(os.path.isfile(HELPERS), "tools/build_helpers.ps1 is missing")
@unittest.skipUnless(os.path.isdir(SCRATCH_PARENT), "scratch fixtures must live under D:\\claude")
class SafeDeletion(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="ultrasuite-buildhelpers-test-", dir=SCRATCH_PARENT)
        self.links = []
        self.build = os.path.join(self.base, "repo", "build")
        self.core = os.path.join(self.build, "dist", "ultrasuite-core")
        os.makedirs(os.path.join(self.core, "_internal"))
        for p in (os.path.join(self.core, "ultrasuite-core.exe"), os.path.join(self.core, "_internal", "lib.txt")):
            with open(p, "w") as f:
                f.write("artifact")
        self.outside = os.path.join(self.base, "outside")
        os.makedirs(self.outside)
        self.keep = os.path.join(self.outside, "keep.txt")
        with open(self.keep, "w") as f:
            f.write("must survive")

    def tearDown(self):
        for link in reversed(self.links):  # remove junctions first so cleanup can never follow them
            if os.path.lexists(link):
                os.rmdir(link)
        shutil.rmtree(self.base, ignore_errors=True)

    def junction(self, link, target):
        r = subprocess.run(["cmd.exe", "/c", "mklink", "/J", link, target], capture_output=True, text=True)
        if r.returncode != 0:
            self.skipTest(f"cannot create a junction here: {r.stdout} {r.stderr}")
        self.links.append(link)

    def remove(self, target, root=None):
        return run_ps(f"Remove-BuildOutput -Target {ps_quote(target)} -BuildRoot {ps_quote(root or self.build)}; Write-Output 'REMOVED'")

    def assertOutsideIntact(self):
        self.assertTrue(os.path.isfile(self.keep), "a file outside the build directory was deleted")
        with open(self.keep) as f:
            self.assertEqual(f.read(), "must survive")

    def test_removes_a_normal_target_inside_the_build_directory(self):
        r = self.remove(self.core)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("REMOVED", r.stdout)
        self.assertFalse(os.path.exists(self.core))
        self.assertTrue(os.path.isdir(os.path.join(self.build, "dist")), "only the target is removed")
        self.assertOutsideIntact()

    def test_missing_target_is_not_an_error(self):
        r = self.remove(os.path.join(self.build, "dist", "not-there"))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_dot_dot_escape_is_refused(self):
        r = self.remove(os.path.join(self.build, "dist", "..", "..", "..", "outside"))
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("REMOVED", r.stdout)
        self.assertOutsideIntact()

    def test_sibling_with_a_shared_prefix_is_refused(self):
        sibling = os.path.join(self.base, "repo", "buildx", "thing")
        os.makedirs(sibling)
        r = self.remove(sibling)
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue(os.path.isdir(sibling))

    def test_the_build_directory_itself_is_refused(self):
        r = self.remove(self.build)
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue(os.path.isdir(self.core))

    def test_junction_inside_the_tree_is_refused_and_not_followed(self):
        self.junction(os.path.join(self.core, "escape"), self.outside)
        r = self.remove(self.core)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("junction or link", r.stdout + r.stderr)
        self.assertOutsideIntact()
        self.assertTrue(os.path.isfile(os.path.join(self.core, "ultrasuite-core.exe")), "nothing is deleted when refused")

    def test_target_reached_through_a_junction_is_refused(self):
        real_dist = os.path.join(self.build, "dist")
        shutil.rmtree(real_dist)
        os.makedirs(os.path.join(self.outside, "ultrasuite-core"))
        with open(os.path.join(self.outside, "ultrasuite-core", "x.txt"), "w") as f:
            f.write("outside data")
        self.junction(real_dist, self.outside)  # build\dist now points outside
        r = self.remove(os.path.join(real_dist, "ultrasuite-core"))
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue(os.path.isfile(os.path.join(self.outside, "ultrasuite-core", "x.txt")))
        self.assertOutsideIntact()

    def test_build_directory_that_is_a_junction_is_refused(self):
        linked_root = os.path.join(self.base, "repo", "linked-build")
        self.junction(linked_root, self.outside)
        os.makedirs(os.path.join(self.outside, "dist"))
        with open(os.path.join(self.outside, "dist", "y.txt"), "w") as f:
            f.write("outside data")
        r = self.remove(os.path.join(linked_root, "dist"), root=linked_root)
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue(os.path.isfile(os.path.join(self.outside, "dist", "y.txt")))
        self.assertOutsideIntact()


@unittest.skipUnless(os.name == "nt" and os.path.isfile(POWERSHELL), "needs Windows PowerShell 5.1")
@unittest.skipUnless(os.path.isfile(HELPERS), "tools/build_helpers.ps1 is missing")
@unittest.skipUnless(os.path.isdir(SCRATCH_PARENT), "scratch fixtures must live under D:\\claude")
class FreshArtifact(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="ultrasuite-buildhelpers-test-", dir=SCRATCH_PARENT)
        self.path = os.path.join(self.base, "Ultra Suite_9.9.9_x64-setup.exe")

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def check(self):
        # NotBeforeUtc = "now" in the child, the same way build_release.ps1 records a step's start
        return run_ps(f"$s = [DateTime]::UtcNow.AddSeconds(-1); $f = Assert-FreshFile -Path {ps_quote(self.path)} -NotBeforeUtc $s -What 'Test installer'; Write-Output ('FRESH ' + $f.Name)")

    def test_stale_installer_is_refused(self):
        with open(self.path, "wb") as f:
            f.write(b"old build")
        old = time.time() - 3600
        os.utime(self.path, (old, old))
        r = self.check()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("stale", r.stdout + r.stderr)

    def test_newly_written_installer_is_accepted(self):
        r = run_ps(f"$s = [DateTime]::UtcNow; [IO.File]::WriteAllBytes({ps_quote(self.path)}, [byte[]](1,2,3)); "
                   f"$f = Assert-FreshFile -Path {ps_quote(self.path)} -NotBeforeUtc $s -What 'Test installer'; Write-Output ('FRESH ' + $f.Name)")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("FRESH Ultra Suite_9.9.9_x64-setup.exe", r.stdout)

    def test_missing_installer_is_refused(self):
        r = self.check()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("was not produced", r.stdout + r.stderr)

    def test_empty_installer_is_refused(self):
        open(self.path, "wb").close()
        r = self.check()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("empty", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
