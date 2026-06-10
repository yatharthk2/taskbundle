"""Build-env tier resolution (no runtime needed): existing > override > auto-detect."""
import tempfile
import unittest
from pathlib import Path

from taskbundle import bundle as B


class TestTierResolution(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "task"

    def tearDown(self):
        self._tmp.cleanup()

    def _bundle(self, *, pyproject=True, install_cmd="", build_cmd="", dockerfile=None):
        (self.root / "repo").mkdir(parents=True, exist_ok=True)
        if pyproject:
            (self.root / "repo" / "pyproject.toml").write_text("[project]\n")
        if dockerfile is not None:
            (self.root / "Dockerfile").write_text(dockerfile)
        return B.scaffold(self.root, repo="repo", commit="v1",
                          install_cmd=install_cmd or None, build_cmd=build_cmd or None)

    # ----- tier 1: existing Dockerfile wins and is never touched -----
    def test_existing_dockerfile_wins(self):
        task = self._bundle(dockerfile="FROM scratch\n")
        env = B.resolve_build_env(self.root, task)
        self.assertEqual(env.tier, B.TIER_EXISTING)
        self.assertFalse(env.generated)
        self.assertEqual((self.root / "Dockerfile").read_text(), "FROM scratch\n")  # untouched

    # ----- tier 2: task.json overrides drive generation -----
    def test_override_generates_with_commands(self):
        task = self._bundle(install_cmd="pip install .[dev]")
        env = B.resolve_build_env(self.root, task)
        self.assertEqual(env.tier, B.TIER_OVERRIDE)
        self.assertIn("RUN pip install .[dev]", (self.root / "Dockerfile").read_text())

    # ----- tier 3: auto-detect Python -----
    def test_autodetect_python(self):
        task = self._bundle(pyproject=True)
        env = B.resolve_build_env(self.root, task)
        self.assertEqual(env.tier, B.TIER_AUTODETECT)
        self.assertEqual(env.stack, "python")
        self.assertFalse(env.needs_edit)
        self.assertIn("pip install -e .", (self.root / "Dockerfile").read_text())

    # ----- tier 3 fail-soft: unknown stack → editable starter, no confident guess -----
    def test_autodetect_unknown_is_failsoft(self):
        task = self._bundle(pyproject=False)
        env = B.resolve_build_env(self.root, task)
        self.assertEqual(env.tier, B.TIER_AUTODETECT)
        self.assertEqual(env.stack, "generic")
        self.assertTrue(env.needs_edit)

    # ----- no-clobber, then explicit --regenerate -----
    def test_no_clobber_then_regenerate(self):
        task = self._bundle(pyproject=True)
        B.resolve_build_env(self.root, task)                       # generates (auto-detect)
        self.assertEqual(B.resolve_build_env(self.root, task).tier, B.TIER_EXISTING)
        self.assertEqual(B.resolve_build_env(self.root, task, regenerate=True).tier, B.TIER_AUTODETECT)

    # ----- slim-image correctness: apt-get update shares the install layer -----
    def test_generated_apt_update_same_layer(self):
        task = self._bundle(pyproject=True)
        B.resolve_build_env(self.root, task)
        self.assertIn("apt-get update && apt-get install", (self.root / "Dockerfile").read_text())


if __name__ == "__main__":
    unittest.main()
