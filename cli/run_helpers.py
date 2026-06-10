"""Run-command helpers, split out of the `task` entry point: produce the solver's patch
(`_solve`), classify the outcome (`_run_outcome`), and persist per-run artifacts
(`_write_run_artifacts`). `cmd_run` in task.py orchestrates these."""
# ----------------------------- Imports -----------------------------
import difflib
import json
import os
from pathlib import Path

from taskbundle import solver as SV

from cli_helpers import _err, _tail


# ----------------------------- Solve -----------------------------
def _solve(args, runtime, tag, bundle_dir):
    """Produce the solver's patch. Built-ins: `golden` (apply patch.diff — a self-test)
    and `noop` (no edits). Anything else is a shell command run in an isolated container.
    Returns (patch_text, meta) or (None, None) on a setup error."""
    if args.solver == "golden": # yatharth note : Just for the purpose of test
        patch = bundle_dir / "patch.diff"
        if not (patch.is_file() and patch.read_text().strip()):
            _err(f"error: --solver golden needs a non-empty {patch}")
            return None, None
        return patch.read_text(), {"kind": "golden", "command": "apply patch.diff", "exit": 0}
    if args.solver == "noop": # yatharth note : Just for the purpose of test
        return "", {"kind": "noop", "command": "(no edits)", "exit": 0}

    #yatharth note : If we are not testing, then run the LLM flow - this is not required as per the expectation but I was curious to see how it would work in practice.
    desc = bundle_dir / "description.md"
    env_names = list(getattr(args, "solver_env", None) or [])   # NAMES only; values come from the host env
    missing = [n for n in env_names if n not in os.environ]
    if missing:
        # name set but not forwarded is almost always a typo — suggest the closest var that IS set
        hints = []
        for n in missing:
            near = difflib.get_close_matches(n, list(os.environ), n=1, cutoff=0.6)
            hints.append(n + (f" (did you mean {near[0]}?)" if near else ""))
        _err(f"warning: --solver-env name(s) not set in your shell — not forwarded: {', '.join(hints)}")
        _err("         the solver will run WITHOUT them; set with `export NAME=value` (no spaces) and re-run")
    # Yatharth Note : Isolation invariant: cmd_run never hands the buckets to the solver
    # Yatharth dev note : the first guard rail is in the run_solver definition itself - the volume definition only allows mounting the problem statement, no buckets.
    res = SV.run_solver(runtime, tag, args.solver,
                        problem=desc if desc.is_file() else None,
                        network=args.solver_network, env=env_names, timeout=args.solver_timeout,
                        memory=args.memory, cpus=args.cpus)
    return res.patch, {"kind": "command", "command": args.solver, "exit": res.exit_code,
                       "log": _tail(res.log), "solver_env": env_names}


# ----------------------------- Outcome classification -----------------------------
def _run_outcome(resolved: bool, solver_exit: int, *, made_edits: bool, scored: bool,
                 baseline_ok: bool = True) -> str:
    """Classify a run for the report so a collaborator can tell the modes apart without re-running.
    `invalid_baseline` and `no_edits` are checked BEFORE `resolved` so a no-op on a malformed task
    (fail2pass already green unpatched) can't read as resolved: a broken task vs a broken solver
    (timed out / errored / patch didn't apply) vs one that changed nothing vs a wrong one (applied, still red)."""
    if not baseline_ok:
        return "invalid_baseline"          # task wouldn't pass `validate` — resolution can't be certified
    if solver_exit == 124:                 # _run's timeout sentinel
        return "solver_timeout"
    if not made_edits:
        return "solver_error" if solver_exit else "no_edits"  # errored (exit≠0) vs a clean noop
    if resolved:
        return "resolved"
    if not scored:
        return "patch_failed"              # the captured patch didn't apply → no tests ran
    return "unresolved"                    # patch applied + tests ran, but fail2pass not fully green


# ----------------------------- Artifacts -----------------------------
def _write_run_artifacts(out_dir, *, patch_text, solver_log, p2p_log, f2p_log, report) -> Path:
    """Persist the whole run under <out_dir> — the solver's captured patch, the per-stage logs,
    and a copy of the report — so a collaborator can see exactly what the solver did and why it
    scored as it did from the command id alone, without reproducing the run. Empty patch/logs are
    skipped (a noop solver has neither); the report copy is always written."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name, text in (("solver.patch", patch_text), ("solver.log", solver_log),
                       ("pass2pass.log", p2p_log), ("fail2pass.log", f2p_log)):
        if text and text.strip():
            out.joinpath(name).write_text(text if text.endswith("\n") else text + "\n")
    out.joinpath("run-report.json").write_text(json.dumps(report, indent=2) + "\n")
    return out
