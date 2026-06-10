"""Run a solver inside the task image and capture its code patch (a unified diff).

The solver edits the repo in a fresh, isolated container — it sees the repo and the
problem statement, but NEVER the pass2pass/fail2pass buckets (those live in the bundle
and are injected later, only for scoring). We snapshot the repo with git, run the
solver, then diff to recover exactly what it changed — so any file-editing command (a
stub, a script, or a real LLM agent) works without having to emit a diff itself.
"""
from __future__ import annotations

# ----------------------------- Imports -----------------------------
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import containers as C

# ----------------------------- Constants -----------------------------
_PROBLEM_MOUNT = "/taskbundle/description.md"   # the only thing mounted into the solve box
_REPO_WORKDIR = "/workspace/repo"
_DELIM = f"===TASKBUNDLE-SOLVER-DIFF-{secrets.token_hex(6)}==="   # random per run so solver output can't forge it
_GIT = "git -c user.email=solver@taskbundle -c user.name=solver"


@dataclass
class SolverResult:
    patch: str       # unified diff of the solver's edits ("" if it changed nothing)
    exit_code: int
    log: str         # solver stdout/stderr (for the ledger / debugging)


# ----------------------------- Run a solver -----------------------------
def run_solver(
    runtime: str,
    tag: str,
    command: str,
    *,
    problem: Optional[str | Path] = None,
    network: Optional[str] = None,
    env: Optional[list] = None,
    timeout: Optional[int] = None,
    memory: Optional[str] = None,
    cpus: Optional[str] = None,
) -> SolverResult:
    """Run `command` in the image at the repo workdir; return the diff it produced.

    The repo is snapshotted with git (an empty base commit, so it works whether or not
    the repo already had history) before the solver runs. We diff the staged tree against
    that base commit's SHA — not HEAD — so a solver that makes its own commits still
    yields its full diff instead of an empty one. No bundle tests are mounted here; `env` forwards
    named host env vars (e.g. an LLM API key) into the box by name — values never hit the argv.
    """
    volumes = []
    pre = ""
    if problem is not None:
        volumes.append((str(Path(problem).resolve()), _PROBLEM_MOUNT, "ro"))
# Yatharth Note : Isolation invariant holds here: volumes starts as [] and the only thing ever appended is the problem statement. There is no code path here that mounts a bucket. So the solver's container has just the repo (baked into the image) + description.md
# yatharth Developer note : having structural guradrail is always better for the surity that llm does not have any context.
# Yatharth Note : This is the first guardrail, 2nd one is on higher surface level - task.py SV.run_solver() definition. :).
        pre = f"export TASKBUNDLE_PROBLEM={_PROBLEM_MOUNT}; "
    script = (
        f"{_GIT} init -q . >/dev/null 2>&1; {_GIT} add -A >/dev/null 2>&1; "
        f"{_GIT} commit -qm __base__ --allow-empty >/dev/null 2>&1; "
        f"base=$({_GIT} rev-parse HEAD); "                 # pin the base before the solver runs
        f"{pre}( {command} ); rc=$?; "
        f"{_GIT} add -A >/dev/null 2>&1; "
        # --binary keeps binary edits applicable; diff vs base SHA survives solver commits. guard a
        # non-empty base + drop git's stderr so a broken-git capture yields an EMPTY patch, never
        # git's error/usage text masquerading as one (which would mis-report as patch_failed).
        f'echo "{_DELIM}"; [ -n "$base" ] && {_GIT} diff --binary --cached "$base" 2>/dev/null; exit $rc'
    )
    res = C.run_in_image(
        runtime, tag, script,
        network=network, workdir=_REPO_WORKDIR, volumes=volumes, env=env, timeout=timeout,
        memory=memory, cpus=cpus,
    )
    log, _, diff = res.output.partition(_DELIM)
    diff = diff.lstrip("\n")   # drop only the newline after the delimiter; keep the patch bytes verbatim
    return SolverResult(diff if diff.strip() else "", res.exit_code, log.strip())
