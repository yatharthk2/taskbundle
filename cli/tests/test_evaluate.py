"""evaluate: JUnit parsing, guardrail judgment, and the bucket runner (no container runtime needed)."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from taskbundle import evaluate as EV

_XML = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="4">
  <testcase classname="t" name="test_pass"/>
  <testcase classname="t" name="test_fail"><failure message="boom">x</failure></testcase>
  <testcase classname="t" name="test_err"><error message="oops">y</error></testcase>
  <testcase classname="t" name="test_skip"><skipped/></testcase>
</testsuite></testsuites>"""


def _bucket(name, statuses, exit_code=0, expected=False):
    results = [EV.TestResult(f"{name}_{i}", s) for i, s in enumerate(statuses)]
    return EV.BucketResult(name, results, exit_code, expected=expected)


class TestParseJunit(unittest.TestCase):
    def test_maps_each_status(self):
        got = {r.name: r.status for r in EV.parse_junit(_XML)}
        self.assertEqual(got, {
            "test_pass": EV.PASSED, "test_fail": EV.FAILED,
            "test_err": EV.ERROR, "test_skip": EV.SKIPPED,
        })

    def test_empty_and_garbage_are_safe(self):
        self.assertEqual(EV.parse_junit(""), [])
        self.assertEqual(EV.parse_junit("not <xml"), [])

    def test_worst_status_wins_not_last_child(self):
        # a trailing <skipped> must never mask a real failure/error on the same testcase
        fs = EV.parse_junit('<testsuite><testcase name="t"><failure/><skipped/></testcase></testsuite>')
        self.assertEqual([(r.name, r.status) for r in fs], [("t", EV.FAILED)])
        es = EV.parse_junit('<testsuite><testcase name="t"><error/><skipped/></testcase></testsuite>')
        self.assertEqual([(r.name, r.status) for r in es], [("t", EV.ERROR)])

    def test_namespaced_testcases_are_found(self):
        ns = '<ns:testsuite xmlns:ns="urn:x"><ns:testcase name="t"><ns:failure/></ns:testcase></ns:testsuite>'
        self.assertEqual([(r.name, r.status) for r in EV.parse_junit(ns)], [("t", EV.FAILED)])


class TestBucketProps(unittest.TestCase):
    def test_clean_vs_failures(self):
        good = _bucket("p2p", [EV.PASSED, EV.PASSED])
        self.assertTrue(good.clean and good.all_passed and good.any_passed)
        bad = _bucket("p2p", [EV.PASSED, EV.FAILED])
        self.assertFalse(bad.clean or bad.all_passed)
        self.assertTrue(bad.any_passed)

    def test_empty_bucket_is_vacuously_clean_but_not_produced(self):
        empty = EV.BucketResult("x", [], 5)
        self.assertTrue(empty.clean)        # nothing failed
        self.assertFalse(empty.produced)    # ...but nothing ran
        self.assertFalse(empty.all_passed)


class TestJudge(unittest.TestCase):
    def test_baseline_holds(self):
        p2p = _bucket("pass2pass", [EV.PASSED, EV.PASSED])
        f2p = _bucket("fail2pass", [EV.FAILED, EV.FAILED])
        self.assertTrue(EV.judge_baseline(p2p, f2p))

    def test_baseline_breaks_if_f2p_passes(self):
        p2p = _bucket("pass2pass", [EV.PASSED])
        f2p = _bucket("fail2pass", [EV.FAILED, EV.PASSED])   # one passes on baseline
        self.assertFalse(EV.judge_baseline(p2p, f2p))

    def test_baseline_breaks_on_p2p_regression(self):
        p2p = _bucket("pass2pass", [EV.PASSED, EV.FAILED])
        f2p = _bucket("fail2pass", [EV.FAILED])
        self.assertFalse(EV.judge_baseline(p2p, f2p))

    def test_baseline_requires_f2p_to_run(self):
        p2p = _bucket("pass2pass", [EV.PASSED])
        f2p = EV.BucketResult("fail2pass", [], 1)            # no results (e.g. patch/collection failure)
        self.assertFalse(EV.judge_baseline(p2p, f2p))

    def test_baseline_rejects_all_skipped_f2p(self):
        p2p = _bucket("pass2pass", [EV.PASSED, EV.PASSED])
        # all-skipped fail2pass: "did not pass" but never genuinely failed → not a valid baseline
        self.assertFalse(EV.judge_baseline(p2p, _bucket("fail2pass", [EV.SKIPPED, EV.SKIPPED])))
        # a genuine failure (even alongside a skip) still counts
        self.assertTrue(EV.judge_baseline(p2p, _bucket("fail2pass", [EV.FAILED, EV.SKIPPED])))

    def test_patched_flip(self):
        p2p = _bucket("pass2pass", [EV.PASSED])
        f2p = _bucket("fail2pass", [EV.PASSED, EV.PASSED])
        self.assertTrue(EV.judge_patched(p2p, f2p))

    def test_patched_not_fully_flipped(self):
        p2p = _bucket("pass2pass", [EV.PASSED])
        f2p = _bucket("fail2pass", [EV.PASSED, EV.FAILED])
        self.assertFalse(EV.judge_patched(p2p, f2p))


class TestVacuousCleanHole(unittest.TestCase):
    """A pass2pass bucket that HAD tests but produced nothing must not read as clean."""

    def test_empty_p2p_with_tests_is_not_clean_or_resolved(self):
        p2p = EV.BucketResult("pass2pass", [], 5, expected=True)   # had tests, 0 results
        f2p = _bucket("fail2pass", [EV.PASSED, EV.PASSED])
        self.assertTrue(p2p.unproduced)
        self.assertFalse(p2p.clean)                                # the fix: no longer vacuous
        self.assertFalse(EV.judge_patched(p2p, f2p))               # so NOT resolved

    def test_empty_p2p_without_tests_stays_resolvable(self):
        p2p = EV.BucketResult("pass2pass", [], 0, expected=False)  # genuinely no p2p tests
        f2p = _bucket("fail2pass", [EV.PASSED])
        self.assertFalse(p2p.unproduced)
        self.assertTrue(p2p.clean)                                 # nothing to regress
        self.assertTrue(EV.judge_patched(p2p, f2p))

    def test_clean_p2p_still_resolves(self):
        p2p = _bucket("pass2pass", [EV.PASSED, EV.PASSED], expected=True)
        f2p = _bucket("fail2pass", [EV.PASSED])
        self.assertTrue(p2p.clean)
        self.assertTrue(EV.judge_patched(p2p, f2p))

    def test_regression_is_distinct_from_no_results(self):
        regressed = EV.BucketResult("pass2pass", [EV.TestResult("t_zero", EV.FAILED)], 1, expected=True)
        empty = EV.BucketResult("pass2pass", [], 5, expected=True)
        self.assertFalse(regressed.clean)                # both block RESOLVED ...
        self.assertFalse(empty.clean)
        self.assertEqual(regressed.failed, ["t_zero"])   # ... but a regression names offenders
        self.assertFalse(regressed.unproduced)
        self.assertEqual(empty.failed, [])               # ... while the empty case is `unproduced`,
        self.assertTrue(empty.unproduced)                #     never counted as a regression


class TestRunBucketRunner(unittest.TestCase):
    """run_bucket uses the bundle's `test_cmd` (any JUnit-emitting runner), defaulting to pytest.
    Runtime-free: it spies on the container command, no Docker."""

    def _command_for(self, **kw):
        captured = {}

        def spy(runtime, tag, command, **rest):
            captured["command"] = command
            return EV.C.ExecResult(0, EV._XML_DELIM)   # delimiter → an empty XML is parsed

        with mock.patch.object(EV.C, "run_in_image", side_effect=spy), tempfile.TemporaryDirectory() as td:
            (Path(td) / "test_x.py").write_text("def test_x(): pass\n")
            EV.run_bucket("docker", "t:1", td, name="pass2pass", **kw)
        return captured["command"]

    def test_default_runner_is_pytest(self):
        cmd = self._command_for()
        self.assertIn("python -m pytest", cmd)
        self.assertIn("TASKBUNDLE_JUNIT", cmd)        # the runner contract is exported

    def test_custom_test_cmd_replaces_pytest(self):
        # a non-Python runner (e.g. Go via gotestsum) is honored instead of the pytest default
        cmd = self._command_for(test_cmd='gotestsum --junitfile "$TASKBUNDLE_JUNIT" -- "$TASKBUNDLE_BUCKET/..."')
        self.assertIn("gotestsum", cmd)
        self.assertNotIn("pytest", cmd)
        self.assertIn("TASKBUNDLE_BUCKET", cmd)       # the bundle's runner sees the mounted tests


if __name__ == "__main__":
    unittest.main()
