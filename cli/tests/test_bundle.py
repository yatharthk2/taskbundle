"""Bundle: scaffolding (no Dockerfile here), no-clobber, load/validate, image tag."""
import json
import tempfile
import unittest
from pathlib import Path

from taskbundle import bundle as B


class TestBundle(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "task"

    def tearDown(self):
        self._tmp.cleanup()

    def test_scaffold_creates_skeleton_without_dockerfile(self):
        B.scaffold(self.root, repo="repo", commit="v1")
        for p in ("task.json", "description.md", "patch.diff", "tests/pass2pass", "tests/fail2pass"):
            self.assertTrue((self.root / p).exists(), p)
        # the Dockerfile is resolved later (tiered), not scaffolded; env not split into task.json
        self.assertFalse((self.root / "Dockerfile").exists())
        self.assertEqual(set(json.loads((self.root / "task.json").read_text())), {"id", "repo", "commit"})

    def test_scaffold_does_not_clobber(self):
        self.root.mkdir(parents=True)
        (self.root / "description.md").write_text("MINE")
        B.scaffold(self.root, repo="repo", commit="v1")
        self.assertEqual((self.root / "description.md").read_text(), "MINE")

    def test_load_round_trip(self):
        B.scaffold(self.root, repo="https://x/y", commit="abc123def4567")
        t = B.load(self.root)
        self.assertEqual(t.repo, "https://x/y")
        self.assertEqual(t.image_tag(), "taskbundle-task:abc123def456")  # 12-char commit

    def test_load_missing_file(self):
        with self.assertRaises(B.BundleError):
            B.load(self.root)

    def test_load_missing_required_field(self):
        self.root.mkdir(parents=True)
        (self.root / "task.json").write_text(json.dumps({"id": "x", "repo": "r"}))  # no commit
        with self.assertRaises(B.BundleError):
            B.load(self.root)

    def test_is_local_repo(self):
        self.assertTrue(B.is_local_repo("repo"))
        self.assertTrue(B.is_local_repo("./repo"))
        self.assertFalse(B.is_local_repo("https://github.com/x/y"))
        self.assertFalse(B.is_local_repo("git@github.com:x/y.git"))

    def test_build_args(self):
        local = B.Task(id="t", repo="repo", commit="v1")
        remote = B.Task(id="t", repo="https://github.com/x/y", commit="abc123")
        self.assertEqual(B.build_args(local), {})
        self.assertEqual(B.build_args(remote), {"REPO": "https://github.com/x/y", "COMMIT": "abc123"})

    def test_image_tag_sanitizes_commit_label(self):
        # a version-label commit with a '/' (illegal in a docker tag) must be sanitized, not passed raw
        self.assertEqual(B.Task("t", "r", "release/1.0").image_tag(), "taskbundle-t:release-1.0")
        # a clean SHA / label is unchanged (no regression)
        self.assertEqual(B.Task("hello-task", "r", "v1").image_tag(), "taskbundle-hello-task:v1")

    def test_rendered_dockerfile_materializes_repo_with_its_own_tests(self):
        # design requirement: the solver SEES the repo's non-bucket tests. They reach the solve box
        # only because the WHOLE repo tree is baked into the image (buckets live OUTSIDE repo/, so
        # they're never brought in). Assert both the local-COPY and remote-clone materialization.
        local = B._render_dockerfile(B.Task("t", "repo", "v1"), B.PYTHON, install="pip install -e .", build="")
        self.assertIn("COPY repo /workspace/repo", local)   # entire repo/ (incl repo/tests/*) → image
        remote = B._render_dockerfile(B.Task("t", "https://x/y.git", "abc"), B.PYTHON, install="", build="")
        self.assertIn("git clone", remote)
        self.assertIn("git checkout", remote)


if __name__ == "__main__":
    unittest.main()
