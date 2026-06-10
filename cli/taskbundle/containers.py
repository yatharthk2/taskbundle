"""Thin wrappers over a docker-compatible runtime CLI (subprocess) — no SDK dependency.

Runtime-agnostic: works with docker, podman, nerdctl, colima, … Resolved once per command;
build/run take the resolved runtime binary as their first argument.
"""
from __future__ import annotations

# ----------------------------- Imports -----------------------------
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

# docker-compatible CLIs, in auto-detect preference order
KNOWN_RUNTIMES = ("docker", "podman", "nerdctl")


class ContainerRuntimeError(RuntimeError):
    """No usable container runtime (none installed, or its engine isn't reachable)."""


# ----------------------------- Result -----------------------------
@dataclass
class ExecResult:
    exit_code: int
    output: str  # merged stdout + stderr

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


@dataclass
class BuildOutcome:
    ok: bool
    output: str = ""   # build log, when the build failed
    digest: str = ""   # image id, once the image is present


# ----------------------------- Runtime resolution -----------------------------
def resolve_runtime(preferred: Optional[str] = None) -> str:
    """Return a usable runtime binary, or raise ContainerRuntimeError with guidance.

    `preferred` (from --runtime / TASKBUNDLE_RUNTIME) pins the choice; otherwise we
    auto-detect the first docker-compatible CLI on PATH whose engine is reachable.
    """
    candidates = [preferred] if preferred else list(KNOWN_RUNTIMES)
    found = [c for c in candidates if c and shutil.which(c)]
    if not found:
        if preferred:
            raise ContainerRuntimeError(f"container runtime '{preferred}' not found on PATH")
        raise ContainerRuntimeError(
            "no container runtime found on PATH (looked for: "
            + ", ".join(KNOWN_RUNTIMES)
            + "). Install Docker, Podman, or colima, then retry — "
            "or use --no-build to scaffold without building"
        )
    # installed; pick the first whose engine actually answers
    for name in found:
        if _run([name, "info"]).ok:
            return name
    raise ContainerRuntimeError(
        f"{found[0]} is installed but its engine isn't reachable — start it "
        "(e.g. Docker Desktop / `colima start` / `podman machine start`), then retry"
    )


# ----------------------------- Build & run -----------------------------
def build_image(
    runtime: str,
    tag: str,
    context_dir: str | Path,
    dockerfile: str | Path,
    build_args: dict,
    *,
    no_cache: bool = False,
) -> ExecResult:
    cmd = [runtime, "build", "-t", tag, "-f", str(dockerfile)]
    for k, v in build_args.items():
        cmd += ["--build-arg", f"{k}={v}"]
    if no_cache:
        cmd.append("--no-cache")
    cmd.append(str(context_dir))
    return _run(cmd)


_run_seq = 0  # unique-suffix counter so a timed-out run container can be killed by name


def run_in_image(
    runtime: str,
    tag: str,
    command: str,
    *,
    network: Optional[str] = None,
    workdir: Optional[str] = None,
    volumes: Optional[list] = None,
    env: Optional[list] = None,
    timeout: Optional[int] = None,
    memory: Optional[str] = None,
    cpus: Optional[str] = None,
) -> ExecResult:
    """Run a shell command in a fresh, auto-removed container.

    `volumes` are (host, container, mode) tuples (mode e.g. "ro"). `env` is a list of env-var
    NAMES forwarded into the container as `-e NAME` (no value): the runtime reads each value from
    its OWN environment, so a secret like an API key never appears on the constructed argv, in a
    log, or in the ledger. `timeout` caps the run so a hung test can't block the CLI; `memory`
    (e.g. "2g") and `cpus` (e.g. "1.5"), when set, add `--memory`/`--cpus` to bound the host RAM/CPU
    blast radius of an untrusted solver. The container gets a unique `--name` and a `--pids-limit`
    (fork-bomb cap), and on a host-side timeout we `docker kill` it — otherwise killing the client
    orphans the container (`--rm` only removes it once it exits).
    """
    global _run_seq
    _run_seq += 1
    name = f"taskbundle-run-{os.getpid()}-{_run_seq}"
    cmd = [runtime, "run", "--rm", "--name", name, "--pids-limit", "1024"]
    if memory is not None:
        cmd += ["--memory", memory]   # host RAM cap — container OOM-killed instead of the host
    if cpus is not None:
        cmd += ["--cpus", cpus]       # host CPU cap — a runaway solver can't peg every core
    if network is not None:
        cmd += ["--network", network]
    if workdir is not None:
        cmd += ["-w", workdir]
    for host, cont, mode in (volumes or []):
        cmd += ["-v", f"{host}:{cont}" + (f":{mode}" if mode else "")]
    for var in (env or []):
        cmd += ["-e", var]   # forward by NAME — value read from this process's env, never on the argv
    cmd += [tag, "sh", "-c", command]
    res = _run(cmd, timeout=timeout)
    # Yatharth Note : have to kill it by name on a timeout - --rm only removes the container once it EXITS, so killing the client alone leaves it orphaned and still running.
    if res.exit_code == 124:                 # host-side timeout — kill the now-orphaned container
        _run([runtime, "kill", name], timeout=15)
    return res


def image_exists(runtime: str, tag: str) -> bool:
    """True if the image tag is already built locally (cheap inspect)."""
    return _run([runtime, "image", "inspect", tag]).ok


def image_digest(runtime: str, tag: str) -> str:
    """Local image id (sha256:…) — pins exactly what was built, for the ledger."""
    r = _run([runtime, "image", "inspect", tag, "--format", "{{.Id}}"])
    return r.output.strip() if r.ok else ""


def ensure_image(
    runtime: str,
    tag: str,
    context_dir: str | Path,
    dockerfile: str | Path,
    build_args: dict,
    *,
    force: bool = False,
    no_cache: bool = False,
    notify: Optional[Callable[[str], None]] = None,
) -> BuildOutcome:
    """Build the image when forced or absent (else reuse the local one); return
    ok + (failure) build log + image digest. Shared by `init` and `validate`."""
    if force or not image_exists(runtime, tag):
        if notify:
            notify(f"building image {tag} ({runtime}); first build can take a while")
        res = build_image(runtime, tag, context_dir, dockerfile, build_args, no_cache=no_cache)
        if not res.ok:
            return BuildOutcome(False, output=res.output)
    return BuildOutcome(True, digest=image_digest(runtime, tag))


# ----------------------------- Internal -----------------------------
def _run(cmd: list, *, timeout: Optional[int] = None) -> ExecResult:
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace", timeout=timeout,   # a test/solver printing non-UTF-8 bytes must not crash the runner
        )
    except subprocess.TimeoutExpired as e:
        out = e.output or ""
        if isinstance(out, bytes):
            out = out.decode(errors="replace")
        return ExecResult(124, f"{out}\n[timed out after {timeout}s]")
    return ExecResult(proc.returncode, proc.stdout or "")
