"""run: solver selection, outcome classification, and solve-box isolation (no container)."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import task
from taskbundle import bundle as B
from taskbundle import containers as C
from taskbundle import solver as SV


def _under(child, parent) -> bool:
    """True if `child` is `parent` or lives beneath it (resolved)."""
    try:
        Path(child).resolve().relative_to(Path(parent).resolve())
        return True
    except ValueError:
        return False


class TestSolveSelection(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "task"
        B.scaffold(self.root, repo="repo", commit="v1")  # writes an empty patch.diff

    def tearDown(self):
        self._tmp.cleanup()

    def _args(self, solver):
        return SimpleNamespace(solver=solver, solver_network=None, solver_timeout=1)

    def test_golden_reads_patch(self):
        (self.root / "patch.diff").write_text("--- a/x\n+++ b/x\n")
        text, meta = task._solve(self._args("golden"), "docker", "t:1", self.root)
        self.assertEqual(meta["kind"], "golden")
        self.assertIn("+++ b/x", text)

    def test_golden_refuses_empty_patch(self):
        text, meta = task._solve(self._args("golden"), "docker", "t:1", self.root)
        self.assertIsNone(text)
        self.assertIsNone(meta)

    def test_noop_is_empty_patch(self):
        text, meta = task._solve(self._args("noop"), "docker", "t:1", self.root)
        self.assertEqual(text, "")
        self.assertEqual(meta["kind"], "noop")


class TestRunOutcome(unittest.TestCase):
    def test_resolved(self):
        self.assertEqual(task._run_outcome(True, 0, made_edits=True, scored=True), "resolved")

    def test_no_edits(self):
        self.assertEqual(task._run_outcome(False, 0, made_edits=False, scored=True), "no_edits")

    def test_patch_failed_when_nothing_scored(self):
        self.assertEqual(task._run_outcome(False, 0, made_edits=True, scored=False), "patch_failed")

    def test_unresolved_when_applied_but_red(self):
        self.assertEqual(task._run_outcome(False, 0, made_edits=True, scored=True), "unresolved")

    def test_timeout_beats_no_edits(self):
        self.assertEqual(task._run_outcome(False, 124, made_edits=False, scored=True), "solver_timeout")

    def test_solver_error_when_crashed_with_no_edits(self):
        # a non-zero exit with no patch is a crash, not a clean noop (exit 0 + no edits stays no_edits)
        self.assertEqual(task._run_outcome(False, 5, made_edits=False, scored=True), "solver_error")

    def test_invalid_baseline_beats_resolved_and_no_edits(self):
        # a malformed task (fail2pass already green unpatched) can't be "resolved", even by a no-op
        self.assertEqual(task._run_outcome(True, 0, made_edits=False, scored=True, baseline_ok=False),
                         "invalid_baseline")
        self.assertEqual(task._run_outcome(False, 0, made_edits=True, scored=True, baseline_ok=False),
                         "invalid_baseline")


class TestSolveBoxIsolation(unittest.TestCase):
    """The solve box must mount ONLY the problem statement — never a hidden bucket, and never
    an ancestor dir (e.g. the bundle root) that would smuggle the buckets in unnamed. Runtime
    -free: it inspects the volume set run_solver would mount; no container is started."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.bundle = Path(self._tmp.name) / "bundle"
        (self.bundle / "tests" / "pass2pass").mkdir(parents=True)
        (self.bundle / "tests" / "fail2pass").mkdir(parents=True)
        (self.bundle / "description.md").write_text("solve me")

    def tearDown(self):
        self._tmp.cleanup()

    def test_mounts_the_problem_never_the_buckets(self):
        captured = {}

        def spy(runtime, tag, command, **kw):
            captured.update(kw)
            return C.ExecResult(0, SV._DELIM)          # delimiter → run_solver parses an empty diff

        with mock.patch.object(SV.C, "run_in_image", side_effect=spy):
            SV.run_solver("docker", "t:1", "true", problem=self.bundle / "description.md")

        volumes = captured.get("volumes") or []
        hosts = [h for (h, _c, _m) in volumes]
        conts = [c for (_h, c, _m) in volumes]
        buckets = [self.bundle / "tests" / "pass2pass", self.bundle / "tests" / "fail2pass"]

        # the solver DOES see the problem statement ...
        self.assertTrue(any(Path(h).name == "description.md" for h in hosts))
        # ... nothing names a bucket ...
        self.assertFalse(any(("pass2pass" in c or "fail2pass" in c or "bucket" in c) for c in conts))
        # ... and nothing mounts a bucket or an ancestor of one (which would smuggle them in)
        for h in hosts:
            for b in buckets:
                self.assertFalse(_under(b, h), f"solve-box mount {h} exposes {b}")


class TestRunArtifacts(unittest.TestCase):
    """`_write_run_artifacts` persists the solver patch + per-stage logs + report copy under the
    per-run dir — runtime-free: the writer is exercised directly, no container."""

    def test_writes_patch_logs_and_report(self):
        diff = "--- a/mathx/core.py\n+++ b/mathx/core.py\n@@ -1 +1 @@\n-old\n+new\n"
        with tempfile.TemporaryDirectory() as td:
            out = task._write_run_artifacts(
                Path(td) / "7",
                patch_text=diff, solver_log="solver stdout/stderr",
                p2p_log="pass2pass pytest output", f2p_log="fail2pass pytest output",
                report={"id": 7, "outcome": "resolved"},
            )
            self.assertEqual((out / "solver.patch").read_text().strip(), diff.strip())  # the captured diff
            self.assertIn("solver stdout/stderr", (out / "solver.log").read_text())
            self.assertIn("pass2pass", (out / "pass2pass.log").read_text())
            self.assertIn("fail2pass", (out / "fail2pass.log").read_text())
            self.assertIn('"outcome": "resolved"', (out / "run-report.json").read_text())

    def test_skips_empty_patch_keeps_report(self):
        with tempfile.TemporaryDirectory() as td:
            out = task._write_run_artifacts(
                Path(td) / "8", patch_text="", solver_log="", p2p_log="", f2p_log="",
                report={"id": 8, "outcome": "no_edits"},
            )
            self.assertFalse((out / "solver.patch").exists())     # a noop solver wrote no patch
            self.assertTrue((out / "run-report.json").is_file())   # the report copy is always written


if __name__ == "__main__":
    unittest.main()
