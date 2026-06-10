"""init flow without Docker: scaffold-only succeeds + logs; bad args exit 2."""
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import task  # the root entry-point module (cli/task.py)
from taskbundle import db


class TestInit(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.bundle = str(Path(self._tmp.name) / "task")
        self.db = str(Path(self._tmp.name) / "db.sqlite")

    def tearDown(self):
        self._tmp.cleanup()

    # quiet wrapper so test output stays clean
    def _run(self, argv):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return task.main(argv)

    def test_scaffold_only_succeeds_and_logs(self):
        rc = self._run(["init", self.bundle, "--repo", "https://x/y",
                        "--commit", "abc123", "--no-build", "--db", self.db])
        self.assertEqual(rc, 0)
        rec = db.get_command(1, self.db)
        self.assertEqual((rec["command"], rec["status"]), ("init", "ok"))
        self.assertFalse(rec["details"]["built"])

    def test_missing_args_exit_2(self):
        rc = self._run(["init", self.bundle, "--db", self.db])  # no task.json, no repo/commit
        self.assertEqual(rc, 2)

    def test_unknown_option_exit_2(self):
        # a usage error maps to a clean exit 2, never a traceback — guards the click lineage Typer
        # actually parses with (incl. a vendored typer._click whose exceptions aren't stdlib click's)
        self.assertEqual(self._run(["init", self.bundle, "--definitely-not-an-option"]), 2)


class TestUnexpectedError(unittest.TestCase):
    """ROB-1: an unexpected exception inside a command is caught, audited as an error row, and
    reported as a clean one-liner with exit 70 — never a bare traceback. TASKBUNDLE_DEBUG re-raises;
    the read-only `query` stays read-only even on a crash."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self._tmp.name) / "db.sqlite")

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = task.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_unexpected_error_is_audited_not_crash(self):
        # a non-BundleError deep in a command (here: B.load) must not escape as a bare traceback
        with mock.patch.object(task.B, "load", side_effect=RuntimeError("boom")):
            rc, _, err = self._run(["validate", "/tmp/nope", "--db", self.db])
        self.assertEqual(rc, 70)
        self.assertNotIn("Traceback", err)         # clean message, not a stack dump
        self.assertIn("boom", err)
        row = db.recent_commands(1, self.db)[0]     # the invocation is still audited
        self.assertEqual((row["command"], row["status"]), ("validate", "error"))
        self.assertIn("traceback_tail", row["details"])

    def test_debug_env_reraises_traceback(self):
        with mock.patch.object(task.B, "load", side_effect=RuntimeError("boom")), \
             mock.patch.dict(os.environ, {"TASKBUNDLE_DEBUG": "1"}):
            with self.assertRaises(RuntimeError):   # debugger opts back into the full stack
                task.main(["validate", "/tmp/nope", "--db", self.db])

    def test_query_crash_is_clean_and_read_only(self):
        db.log_command("init", "ok", task_id="x", summary="seed", db_path=self.db)  # 1 pre-existing row
        with mock.patch.object(task.DB, "recent_commands", side_effect=RuntimeError("boom")):
            rc, _, err = self._run(["query", "--db", self.db])
        self.assertEqual(rc, 70)
        self.assertNotIn("Traceback", err)
        self.assertIn("read-only", err)             # carve-out message
        self.assertEqual(len(db.recent_commands(10, self.db)), 1)  # the crash wrote NO row


if __name__ == "__main__":
    unittest.main()
