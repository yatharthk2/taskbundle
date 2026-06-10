"""Task bundle: on-disk layout, task.json schema, and build-environment resolution.

The build environment is ONE Dockerfile, resolved by strict precedence (highest wins):
  1. existing    — a Dockerfile already in the bundle → used verbatim
  2. override    — task.json install_cmd/build_cmd → generate a Dockerfile from those
  3. auto-detect — recognize the stack and generate a Dockerfile with sane defaults
The Dockerfile owns the ENVIRONMENT (the FROM/base image). task.json never does.
"""
from __future__ import annotations

# ----------------------------- Imports -----------------------------
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# ----------------------------- Schema -----------------------------
TASK_JSON = "task.json"
REQUIRED_FIELDS = ("id", "repo", "commit")            # which code + metadata
OVERRIDE_FIELDS = ("install_cmd", "build_cmd", "test_cmd", "smoke_cmd")  # all optional

# tier names (also logged to the ledger)
TIER_EXISTING = "existing"
TIER_OVERRIDE = "override"
TIER_AUTODETECT = "auto-detect"


class BundleError(Exception):
    """Malformed or missing bundle inputs (shown as a clean CLI error)."""


# ----------------------------- Task model -----------------------------
@dataclass
class Task:
    id: str
    repo: str
    commit: str
    install_cmd: str = ""
    build_cmd: str = ""
    test_cmd: str = ""
    smoke_cmd: str = ""

    def image_tag(self) -> str:
        """Deterministic, docker-safe tag from id + commit. BOTH are sanitized to a valid docker
        reference — `commit` may be a SHA or a version label like 'release/1.0' (a '/' is illegal
        in a tag), so an unsanitized label would otherwise produce an invalid reference."""
        slug = re.sub(r"[^a-z0-9_.-]+", "-", self.id.lower()).strip("-") or "task"
        ref = re.sub(r"[^A-Za-z0-9_.-]+", "-", self.commit)[:12].strip("-") or "latest"
        return f"taskbundle-{slug}:{ref}"


def task_to_dict(task: Task) -> dict:
    """Minimal task.json: required fields + any non-empty overrides."""
    d = {"id": task.id, "repo": task.repo, "commit": task.commit}
    for f in OVERRIDE_FIELDS:
        if getattr(task, f):
            d[f] = getattr(task, f)
    return d


# ----------------------------- Stack detection -----------------------------
@dataclass
class Stack:
    name: str
    base_image: str
    install_cmd: str
    test_cmd: str
    smoke_cmd: str


PYTHON = Stack(
    "python", "python:3.11-slim",
    # the default test/smoke runner is pytest, so ensure it exists on a slim base
    "pip install -e . || pip install -r requirements.txt; pip install pytest",
    "python -m pytest -q", "python -m pytest --collect-only -q",
)
GENERIC = Stack("generic", "debian:stable-slim", "", "", "")


def detect_stack(repo_dir: Path) -> Stack:
    """Recognize the stack from marker files. Python only for now.

    EXTENSION PATH (documented, not built): add one branch + one install line each —
    poetry (poetry.lock), uv (uv.lock), node (package.json), go (go.mod).
    """
    if (repo_dir / "pyproject.toml").is_file() or (repo_dir / "requirements.txt").is_file():
        return PYTHON
    return GENERIC


# ----------------------------- Build-env resolution -----------------------------
@dataclass
class BuildEnv:
    tier: str            # existing | override | auto-detect
    stack: str           # detected stack name
    dockerfile: Path
    generated: bool
    needs_edit: bool     # generic starter the user must edit before it can build
    install_cmd: str
    test_cmd: str
    smoke_cmd: str


def dockerfile_path(bundle_dir: str | Path) -> Path:
    return Path(bundle_dir) / "Dockerfile"


def is_local_repo(repo: str) -> bool:
    """Local (ships inside the bundle) unless it looks like a remote git URL."""
    return "://" not in repo and not repo.startswith("git@")


def build_args(task: Task) -> dict:
    """Docker build args for this task: none for a local repo (COPY'd from the build
    context), REPO/COMMIT for a remote clone."""
    return {} if is_local_repo(task.repo) else {"REPO": task.repo, "COMMIT": task.commit}


def resolve_build_env(
    bundle_dir: str | Path, task: Task, *, regenerate: bool = False
) -> BuildEnv:
    """Pick ONE Dockerfile by strict precedence; generate only when absent (no-clobber)."""
    df = dockerfile_path(bundle_dir)
    stack = detect_stack(Path(bundle_dir) / task.repo)
    install = task.install_cmd or stack.install_cmd
    test = task.test_cmd or stack.test_cmd
    smoke = task.smoke_cmd or stack.smoke_cmd

    # tier 1 — an existing Dockerfile is authoritative; never overwrite it
    if df.is_file() and not regenerate:
        return BuildEnv(TIER_EXISTING, stack.name, df, False, False, install, test, smoke)

    # tier 2 — explicit task.json overrides drive a generated Dockerfile
    # Yatharth Note : similar to vercel way of installation, I got motivation from there.
    if task.install_cmd or task.build_cmd:
        df.write_text(_render_dockerfile(task, stack, install=install, build=task.build_cmd))
        return BuildEnv(TIER_OVERRIDE, stack.name, df, True, False, install, test, smoke)

    # tier 3 — auto-detect; fail soft to an editable starter if the stack is unknown
    df.write_text(_render_dockerfile(task, stack, install=stack.install_cmd, build=""))
    return BuildEnv(TIER_AUTODETECT, stack.name, df, True, stack.name == "generic", install, test, smoke)


def _render_dockerfile(task: Task, stack: Stack, *, install: str, build: str) -> str:
    """Generate a complete Dockerfile (fetch + install/build) for a known/unknown stack."""
    if is_local_repo(task.repo):
        fetch = f"WORKDIR /workspace\nCOPY {task.repo} /workspace/repo\nWORKDIR /workspace/repo"
    else:
        fetch = (
            'ARG REPO\nARG COMMIT\n'
            'WORKDIR /workspace\nRUN git clone "$REPO" repo\n'
            'WORKDIR /workspace/repo\nRUN git checkout "$COMMIT"'
        )
    steps = [f"RUN {c}" for c in (install, build) if c]
    body = "\n".join(steps) if steps else "# TODO: add install/build steps (e.g. RUN pip install -e .)"
    note = "" if stack.name != "generic" else "# Stack not auto-detected — set FROM + steps below.\n"
    return f"""# syntax=docker/dockerfile:1
# Generated by `task init` (stack: {stack.name}). This file owns the environment — edit freely.
{note}FROM {stack.base_image}

# system tools — `apt-get update` MUST share this RUN layer with install on slim images
RUN apt-get update && apt-get install -y --no-install-recommends git \\
    && rm -rf /var/lib/apt/lists/*

{fetch}

{body}
"""


# ----------------------------- Load -----------------------------
def load(bundle_dir: str | Path) -> Task:
    """Read + validate an existing bundle's task.json."""
    root = Path(bundle_dir)
    f = root / TASK_JSON
    if not f.is_file():
        raise BundleError(f"no {TASK_JSON} in {root}")
    try:
        data = json.loads(f.read_text())
    except json.JSONDecodeError as e:
        raise BundleError(f"{f}: invalid JSON ({e})")

    missing = [k for k in REQUIRED_FIELDS if not data.get(k)]
    if missing:
        raise BundleError(f"{f}: missing required field(s): {', '.join(missing)}")

    return Task(
        id=data["id"], repo=data["repo"], commit=data["commit"],
        install_cmd=data.get("install_cmd", ""),
        build_cmd=data.get("build_cmd", ""),
        test_cmd=data.get("test_cmd", ""),
        smoke_cmd=data.get("smoke_cmd", ""),
    )


# ----------------------------- Local-repo materialization -----------------------------
def _is_inside(root: Path, repo: str) -> bool:
    """True if `repo` resolves within the bundle (so a COPY can read it from the build context)."""
    try:
        (root / repo).resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _materialize_local_repo(root: Path, repo: str, commit: str) -> str:
    """Bring a local `--repo` that lives OUTSIDE the bundle IN as <bundle>/repo (the in-bundle mode
    COPY can build) and rewrite its path to "repo". Remote URLs and already-in-bundle paths pass
    through untouched. A missing/non-dir source fails EARLY here — never a late BuildKit dump."""
    if not is_local_repo(repo) or _is_inside(root, repo):
        return repo
    src = Path(repo)
    if not src.is_dir():
        raise BundleError(
            f"--repo local path not found (or not a directory): {repo} — "
            "pass a git URL, an existing directory, or a path inside the bundle")
    dest = root / "repo"
    if dest.exists():
        raise BundleError(f"cannot import {repo}: {dest} already exists (remove it, or use --repo repo)")
    if (src / ".git").is_dir():
        _clone_local(src, dest, commit)            # git repo → clone + pin the commit
    else:
        shutil.copytree(src, dest)                 # plain tree → copy as-is (commit is a label)
    return "repo"


def _clone_local(src: Path, dest: Path, commit: str) -> None:
    """Clone a local git repo, pin it at `commit`, then DROP its `.git`, surfacing a clean
    BundleError on failure. The checkout pins the tree; the `.git` must go because a leftover
    nested repo makes `git add <bundle>` record a gitlink (mode 160000) and silently drop the
    repo's files from the bundle commit."""
    try:
        subprocess.run(["git", "clone", "--local", str(src), str(dest)],
                       check=True, capture_output=True, text=True, errors="replace")
        subprocess.run(["git", "-C", str(dest), "checkout", commit],
                       check=True, capture_output=True, text=True, errors="replace")
    except FileNotFoundError:
        raise BundleError("git not found on PATH — needed to clone a local git repo")
    except subprocess.CalledProcessError as e:
        raise BundleError(f"could not clone {src} at {commit}: {(e.stderr or '').strip() or e}")
    # Yatharth Note : dropping .git on purpose - learnt the hard way that a leftover nested repo makes `git add` record a gitlink (mode 160000) and silently drops the repo files from the bundle commit.
    shutil.rmtree(dest / ".git", ignore_errors=True)   # leave a plain, commit-pinned tree


# ----------------------------- Scaffold -----------------------------
def scaffold(
    bundle_dir: str | Path,
    *,
    repo: str,
    commit: str,
    id: Optional[str] = None,
    install_cmd: Optional[str] = None,
    build_cmd: Optional[str] = None,
    test_cmd: Optional[str] = None,
    smoke_cmd: Optional[str] = None,
) -> Task:
    """Create the bundle skeleton; never clobbers existing author files.

    The Dockerfile is NOT created here — resolve_build_env() owns it (tiered).
    """
    root = Path(bundle_dir)
    root.mkdir(parents=True, exist_ok=True)
    repo = _materialize_local_repo(root, repo, commit)   # external local path → bring it in as <bundle>/repo
    task = Task(
        id=id or root.resolve().name,
        repo=repo, commit=commit,
        install_cmd=install_cmd or "", build_cmd=build_cmd or "",
        test_cmd=test_cmd or "", smoke_cmd=smoke_cmd or "",
    )
    _ensure(root / TASK_JSON, json.dumps(task_to_dict(task), indent=2) + "\n")
    _ensure(root / "description.md", "# Problem statement\n\n_TODO: describe the task the model must solve._\n")
    _ensure(root / "patch.diff", "")
    (root / "tests" / "pass2pass").mkdir(parents=True, exist_ok=True)
    (root / "tests" / "fail2pass").mkdir(parents=True, exist_ok=True)
    return task


# ----------------------------- Internal -----------------------------
def _ensure(path: Path, content: str) -> None:
    """Write content only if the file doesn't already exist."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
