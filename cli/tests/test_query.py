"""query: read-only ledger inspection — lookup, list, --json, errors (no runtime)."""
import contextlib
import io
import json as jsonlib
import tempfile
import unittest
from pathlib import Path

import task
from taskbundle import db


def _seed(db_path):
    """One row per command type (incl. a resolved and a patch_failed run)."""
    db.log_command("init", "ok", task_id="hello", summary="env reproducible",
                   details={"tier": "existing", "stack": "python", "runtime": "docker",
                            "image": "taskbundle-hello:v1", "digest": "sha256:abc123"}, db_path=db_path)
    db.log_command("validate", "ok", task_id="hello", summary="baseline guardrail holds",
                   details={"baseline": {"ok": True,
                            "pass2pass": {"passed": ["t_zero", "t_one"], "failed": [], "error": [], "skipped": []},
                            "fail2pass": {"passed": [], "failed": ["t_five"], "error": [], "skipped": []}},
                            "patched": None}, db_path=db_path)
    db.log_command("run", "ok", task_id="hello", summary="RESOLVED by golden solver",
                   details={"solver": {"kind": "golden", "command": "apply patch.diff", "exit": 0},
                            "outcome": "resolved", "resolved": True,
                            "pass2pass": {"passed": ["t_zero"], "failed": [], "error": [], "skipped": []},
                            "fail2pass": {"passed": ["t_five"], "failed": [], "error": [], "skipped": []},
                            "regressions": [], "resolved_tests": ["t_five"], "unresolved_tests": [],
                            "report_path": "/tmp/x/run-report.json",
                            "artifacts_root": "/tmp/x/artifacts"}, db_path=db_path)
    db.log_command("run", "ok", task_id="hello", summary="NOT resolved [patch_failed] by command solver",
                   details={"solver": {"kind": "command", "command": "sed ...", "exit": 0},
                            "outcome": "patch_failed", "resolved": False,
                            "pass2pass": {"passed": [], "failed": [], "error": [], "skipped": []},
                            "fail2pass": {"passed": [], "failed": [], "error": [], "skipped": []},
                            "regressions": [], "resolved_tests": [], "unresolved_tests": [],
                            "scoring_log": "error: patch does not apply"}, db_path=db_path)


class TestQuery(unittest.TestCase):
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

    # ----- lookup renders type-specific fields (human) -----
    def test_lookup_init(self):
        _seed(self.db)
        rc, out, _ = self._run(["query", "1", "--db", self.db])
        self.assertEqual(rc, 0)
        self.assertIn("init", out)
        self.assertIn("existing", out)        # tier
        self.assertIn("sha256:abc123", out)   # digest

    def test_lookup_validate(self):
        _seed(self.db)
        rc, out, _ = self._run(["query", "2", "--db", self.db])
        self.assertEqual(rc, 0)
        self.assertIn("held", out)            # guardrail held
        self.assertIn("2 passed", out)        # pass2pass counts

    def test_lookup_run_resolved(self):
        _seed(self.db)
        rc, out, _ = self._run(["query", "3", "--db", self.db])
        self.assertEqual(rc, 0)
        self.assertIn("resolved", out)        # outcome
        self.assertIn("golden", out)
        self.assertIn("run-report.json", out)  # report path (persisted by cmd_run)

    def test_lookup_run_artifacts_path_joined(self):
        _seed(self.db)
        rc, out, _ = self._run(["query", "3", "--db", self.db])
        self.assertEqual(rc, 0)
        self.assertIn("/tmp/x/artifacts/3", out)   # artifacts_root joined with the row id (<root>/<id>)

    # ----- --json mode -----
    def test_lookup_json_parseable(self):
        _seed(self.db)
        rc, out, _ = self._run(["query", "3", "--db", self.db, "--json"])
        self.assertEqual(rc, 0)
        rec = jsonlib.loads(out)
        self.assertEqual(rec["command"], "run")
        self.assertEqual(rec["details"]["outcome"], "resolved")

    # ----- list mode -----
    def test_list_shows_all_newest_first(self):
        _seed(self.db)
        rc, out, _ = self._run(["query", "--db", self.db])
        self.assertEqual(rc, 0)
        for kind in ("init", "validate", "run"):
            self.assertIn(kind, out)

    def test_list_json_is_array_newest_first(self):
        _seed(self.db)
        rc, out, _ = self._run(["query", "--db", self.db, "--json"])
        rows = jsonlib.loads(out)
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["id"], 4)    # newest first

    # ----- filters -----
    def test_filter_by_command(self):
        _seed(self.db)
        rc, out, _ = self._run(["query", "--db", self.db, "--command", "run", "--json"])
        self.assertEqual(rc, 0)
        rows = jsonlib.loads(out)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["command"] == "run" for r in rows))

    def test_filter_by_outcome(self):
        _seed(self.db)
        rc, out, _ = self._run(["query", "--db", self.db, "--outcome", "resolved", "--json"])
        rows = jsonlib.loads(out)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["details"]["outcome"], "resolved")

    def test_filter_by_task(self):
        _seed(self.db)
        _, out, _ = self._run(["query", "--db", self.db, "--task", "hello", "--json"])
        self.assertEqual(len(jsonlib.loads(out)), 4)

    def test_filter_no_match_is_friendly(self):
        _seed(self.db)
        rc, out, _ = self._run(["query", "--db", self.db, "--task", "nope"])
        self.assertEqual(rc, 0)
        self.assertIn("no commands match", out)

    # ----- stats scoreboard -----
    def test_stats_human(self):
        _seed(self.db)
        rc, out, _ = self._run(["query", "--db", self.db, "--stats"])
        self.assertEqual(rc, 0)
        self.assertIn("hello", out)
        self.assertIn("1/2", out)             # 1 of 2 runs resolved
        self.assertIn("resolved", out)

    def test_stats_json(self):
        _seed(self.db)
        _, out, _ = self._run(["query", "--db", self.db, "--stats", "--json"])
        s = jsonlib.loads(out)
        self.assertEqual(s["runs"], 2)
        self.assertEqual(s["by_outcome"]["resolved"], 1)
        self.assertEqual(s["per_task"]["hello"], {"resolved": 1, "runs": 2})

    # ----- the patch_failed shape must not choke the renderer -----
    def test_patch_failed_renders_human_and_json(self):
        _seed(self.db)
        rc, out, _ = self._run(["query", "4", "--db", self.db])
        self.assertEqual(rc, 0)
        self.assertIn("patch_failed", out)
        rc2, out2, _ = self._run(["query", "4", "--db", self.db, "--json"])
        self.assertEqual(rc2, 0)
        self.assertEqual(jsonlib.loads(out2)["details"]["outcome"], "patch_failed")

    # ----- unknown id -> dedicated exit 10 -----
    def test_unknown_id_exit_10(self):
        _seed(self.db)
        rc, _, err = self._run(["query", "999", "--db", self.db])
        self.assertEqual(rc, 10)
        self.assertIn("no command with id 999", err)

    # ----- empty / missing ledger -> friendly, exit 0, no file created -----
    def test_empty_ledger_is_friendly_and_read_only(self):
        rc, out, _ = self._run(["query", "--db", self.db])   # db never created
        self.assertEqual(rc, 0)
        self.assertIn("no commands recorded yet", out)
        self.assertFalse(Path(self.db).exists())             # read-only: nothing written

    def test_lookup_on_missing_db_is_unknown_id(self):
        rc, _, err = self._run(["query", "5", "--db", self.db])
        self.assertEqual(rc, 10)
        self.assertIn("no command with id 5", err)


if __name__ == "__main__":
    unittest.main()
