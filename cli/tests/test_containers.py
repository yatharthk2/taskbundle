"""Runtime resolution + ensure_image build-vs-reuse decision (mocked — no docker)."""
import unittest
from unittest import mock

from taskbundle import containers as C


class TestRuntime(unittest.TestCase):
    def test_unknown_preferred_runtime_raises(self):
        with self.assertRaises(C.ContainerRuntimeError):
            C.resolve_runtime("__definitely_not_a_real_runtime__")


class TestEnsureImage(unittest.TestCase):
    def test_reuses_when_present(self):
        with mock.patch.object(C, "image_exists", return_value=True), \
             mock.patch.object(C, "build_image") as build, \
             mock.patch.object(C, "image_digest", return_value="sha256:abc"):
            out = C.ensure_image("docker", "t:1", ".", "Dockerfile", {})
        self.assertTrue(out.ok and out.digest == "sha256:abc")
        build.assert_not_called()

    def test_builds_when_absent(self):
        with mock.patch.object(C, "image_exists", return_value=False), \
             mock.patch.object(C, "build_image", return_value=C.ExecResult(0, "")) as build, \
             mock.patch.object(C, "image_digest", return_value="sha256:abc"):
            out = C.ensure_image("docker", "t:1", ".", "Dockerfile", {})
        self.assertTrue(out.ok)
        build.assert_called_once()

    def test_force_builds_even_when_present(self):
        with mock.patch.object(C, "image_exists", return_value=True), \
             mock.patch.object(C, "build_image", return_value=C.ExecResult(0, "")) as build, \
             mock.patch.object(C, "image_digest", return_value="x"):
            C.ensure_image("docker", "t:1", ".", "Dockerfile", {}, force=True)
        build.assert_called_once()

    def test_build_failure_is_not_ok(self):
        with mock.patch.object(C, "image_exists", return_value=False), \
             mock.patch.object(C, "build_image", return_value=C.ExecResult(1, "boom")), \
             mock.patch.object(C, "image_digest", return_value="x"):
            out = C.ensure_image("docker", "t:1", ".", "Dockerfile", {})
        self.assertFalse(out.ok)
        self.assertIn("boom", out.output)


class TestRunInImageArgs(unittest.TestCase):
    """The docker argv carries resource caps only when asked (default = no limit)."""

    def _argv(self, **kw):
        seen = {}
        def spy(cmd, *, timeout=None):
            seen["cmd"] = cmd
            return C.ExecResult(0, "")
        with mock.patch.object(C, "_run", side_effect=spy):
            C.run_in_image("docker", "img:1", "true", **kw)
        return seen["cmd"]

    def test_pids_limit_always_present(self):       # fork-bomb cap is baked in
        self.assertIn("--pids-limit", self._argv())

    def test_no_resource_caps_by_default(self):     # zero regression: no cap unless asked
        argv = self._argv()
        self.assertNotIn("--memory", argv)
        self.assertNotIn("--cpus", argv)

    def test_caps_passed_through_when_set(self):
        argv = self._argv(memory="2g", cpus="1.5")
        self.assertEqual(argv[argv.index("--memory") + 1], "2g")
        self.assertEqual(argv[argv.index("--cpus") + 1], "1.5")


class TestNonUtf8Output(unittest.TestCase):
    """A solver/test that prints non-UTF-8 bytes must not crash the runner (it would, under a
    strict decoder). Calls the real `_run` (no Docker) — this raised UnicodeDecodeError before
    errors='replace' was added."""

    def test_run_survives_non_utf8_bytes(self):
        res = C._run(["sh", "-c", "printf '\\xff\\xfe ok'"])
        self.assertEqual(res.exit_code, 0)
        self.assertIn("ok", res.output)   # invalid bytes are replaced; the ascii tail survives


if __name__ == "__main__":
    unittest.main()
