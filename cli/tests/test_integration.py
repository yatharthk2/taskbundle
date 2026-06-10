"""End-to-end integration test: drive the full init → validate → run loop on the hello-task
example against a REAL container runtime, asserting the documented exit codes.

Skipped automatically when no container runtime is available, so the default (runtime-free)
unit suite stays green without Docker — this is the one test that needs it. Run it where Docker
is present (locally / a Docker-enabled CI job) to cover the real container flow the unit tests mock.
"""
# ----------------------------- Imports -----------------------------
import tempfile
import unittest
from pathlib import Path

import task
from taskbundle import containers as C

# ----------------------------- Runtime gate -----------------------------
HELLO = Path(__file__).resolve().parent.parent / "examples" / "hello-task"


def _runtime_available() -> bool:
    try:
        C.resolve_runtime(None)
        return True
    except C.ContainerRuntimeError:
        return False


# ----------------------------- The full loop -----------------------------
@unittest.skipUnless(_runtime_available(), "no container runtime — skipping the end-to-end test")
class TestHelloTaskEndToEnd(unittest.TestCase):
    """The documented quickstart, asserted against a real image: init builds + smoke-checks,
    validate's guardrail holds and the golden patch flips fail2pass, a golden solver resolves,
    and a noop solver does not."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        d = Path(self._tmp.name)
        self.db = str(d / "ledger.sqlite")
        self.report = str(d / "report.json")
        self.artifacts = str(d / "artifacts")
        self.bundle = str(HELLO)

    def tearDown(self):
        self._tmp.cleanup()

    def test_full_loop(self):
        # init: build the image + smoke-check it
        self.assertEqual(task.main(["init", self.bundle, "--db", self.db]), 0)
        # validate: baseline guardrail holds AND the golden patch flips fail2pass
        self.assertEqual(
            task.main(["validate", self.bundle, "--check-patch", "--db", self.db]), 0)
        # run (golden): resolves → exit 0
        self.assertEqual(
            task.main(["run", self.bundle, "--db", self.db,
                       "--report", self.report, "--artifacts", self.artifacts]), 0)
        # run (noop): no edits → not resolved → exit 9
        self.assertEqual(
            task.main(["run", self.bundle, "--solver", "noop", "--db", self.db,
                       "--report", self.report, "--artifacts", self.artifacts]), 9)


if __name__ == "__main__":
    unittest.main()
