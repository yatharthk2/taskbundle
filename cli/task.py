"""`task` CLI entry point — a Typer app over the taskbundle library.

Run: `python cli/task.py <cmd>` (no install) or `task <cmd>` (after `pip install -e ./cli`).
Implemented so far: init, validate, run, query.
"""
# ----------------------------- Imports -----------------------------
import functools
import json
import os
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, List, Optional

import click
import typer
from typer.main import get_command

from taskbundle import bundle as B
from taskbundle import containers as C
from taskbundle import db as DB
from taskbundle import evaluate as EV

# helpers split out of this entry point — presentation (cli_helpers) + run logic (run_helpers)
from cli_helpers import (_echo, _err, _tail, _has_tests, _print_bucket,
                         _render_list, _render_record, _compute_stats, _render_stats)
from run_helpers import _solve, _run_outcome, _write_run_artifacts


# ----------------------------- Error-logging helper -----------------------------
def _fail(command, task_id, summary, code, *, db_path, details=None, blob="", msg=None) -> int:
    """Log an error row, print an optional blob + message (with the command id), and
    return the exit code — collapses the repeated log/print/return fail-paths."""
    cid = DB.log_command(command, "error", task_id=task_id, summary=summary,
                         details=details or {}, db_path=db_path)
    if blob:
        _err(_tail(blob))
    _err(f"{msg or 'error: ' + summary}. command #{cid}")
    return code


# Yatharth Note: Decorator to catch unexpected errors and log them instead of crashing with a traceback;.
# ----------------------------- Audited dispatch -----------------------------
_EXIT_UNEXPECTED = 70   # sysexits EX_SOFTWARE — an unexpected internal error, distinct from the domain codes


def _audited(func):
    """Wrap a cmd_* so an UNEXPECTED exception is audited and reported cleanly instead of crashing with a
    bare traceback: log an error ledger row (right db, with the bundle + a traceback tail) and return a
    distinct code (70). Known errors (BundleError) still propagate to main() (→ exit 2). TASKBUNDLE_DEBUG=1
    re-raises the full traceback — so a normal run gets a clean message + an audit row, a debugger the stack."""
    @functools.wraps(func)
    def wrapper(args):
        try:
            return func(args)
        except B.BundleError:
            raise
        except Exception as e:
            if os.environ.get("TASKBUNDLE_DEBUG", "") not in ("", "0"):
                raise
            name = func.__name__.removeprefix("cmd_")
            _err(f"error: unexpected {type(e).__name__}: {e}")
            if name == "query":                 # query is read-only — report cleanly but never write a row
                _err("  (read-only command — no ledger row written); set TASKBUNDLE_DEBUG=1 for the full traceback")
                return _EXIT_UNEXPECTED
            cid = DB.log_command(
                name, "error",
                summary=f"unexpected error: {type(e).__name__}: {e}",
                details={"error": str(e), "type": type(e).__name__,
                         "bundle": getattr(args, "bundle", None),
                         "traceback_tail": _tail(traceback.format_exc())},
                db_path=getattr(args, "db", DB.DEFAULT_DB_PATH),
            )
            _err(f"  logged as command #{cid}; set TASKBUNDLE_DEBUG=1 for the full traceback")
            return _EXIT_UNEXPECTED
    return wrapper


# ----------------------------- init command -----------------------------
@_audited
def cmd_init(args) -> int:
    """Scaffold the bundle, resolve its build env (one Dockerfile, three tiers),
    build the image, smoke-check it, and log the run."""
    bundle_dir = Path(args.bundle)
    tj = bundle_dir / B.TASK_JSON

    # 1. config: load existing task.json, else scaffold from flags
    if tj.is_file():
        task = B.load(bundle_dir) # returns dataclass task
        if args.repo or args.commit:
            _echo("• note: task.json exists — ignoring --repo/--commit (delete it to re-scaffold)")
        _echo(f"• loaded {tj}")
    else:
        if not (args.repo and args.commit):
            _err("error: no task.json found — pass --repo and --commit to scaffold a new bundle")
            return 2
        task = B.scaffold(
            bundle_dir, repo=args.repo, commit=args.commit, id=args.id,
            install_cmd=args.install_cmd, build_cmd=args.build_cmd,
            test_cmd=args.test_cmd, smoke_cmd=args.smoke_cmd,
        ) # returns dataclass task
        _echo(f"• scaffolded bundle at {bundle_dir}/  (id={task.id})")

    # 2. resolve the build environment: 1. existing -> 2. override -> 3. auto-detect
    # Yatharth Note : The env building mech tries best to avoid manual edits from user but finally falls gracefully to starter kit.
    env = B.resolve_build_env(bundle_dir, task, regenerate=args.regenerate)
    _echo(f"• build env → tier: {env.tier}, stack: {env.stack}, Dockerfile: {env.dockerfile}")
    if env.needs_edit:
        _echo(f"• couldn't auto-detect the stack — wrote a starter Dockerfile at {env.dockerfile}, edit it")
    meta = {"tier": env.tier, "stack": env.stack, "dockerfile": str(env.dockerfile)}

    # 3. scaffold-only path (no runtime needed)
    # Yatharth Note : one might ask why is this required - so this is required because what if the user wants framework but wants the power to edit it in the process.
    if args.no_build:
        cid = DB.log_command(
            "init", "ok", task_id=task.id,
            summary=f"scaffolded {task.id} (tier: {env.tier}, no build)",
            details={**meta, "bundle": str(bundle_dir), "built": False}, db_path=args.db,
        )
        _echo(f"✓ bundle scaffolded (no build). command #{cid}")
        return 0

    # a generic starter can't be built confidently — stop and ask the user to edit it
    if env.needs_edit:
        return _fail("init", task.id, "stack not detected; starter written", 6,
                     db_path=args.db, details=meta,
                     msg=f"error: couldn't auto-detect the stack (expected for a remote-URL repo whose files aren't cloned yet). "
                         f"Edit {env.dockerfile} (set FROM + install steps), or add install_cmd to task.json, then re-run")

    # 4. resolve a container runtime
    try:
        runtime = C.resolve_runtime(args.runtime)
    except C.ContainerRuntimeError as e:
        return _fail("init", task.id, "no container runtime", 3, db_path=args.db,
                     details={**meta, "error": str(e)},
                     msg=f"error: {e}\nhint: re-run with --no-build to scaffold without building")
    _echo(f"• using container runtime: {runtime}")

    # 5. build the image (init always builds; relies on layer cache for speed)
    tag = task.image_tag()
    outcome = C.ensure_image(runtime, tag, bundle_dir, env.dockerfile, B.build_args(task),
                             force=True, no_cache=args.no_cache, notify=_echo)
    if not outcome.ok:
        return _fail("init", task.id, "image build failed", 4, db_path=args.db,
                     details={**meta, "image": tag, "build_log_tail": _tail(outcome.output)},
                     blob=outcome.output, msg=f"error: image build failed for {tag}")
    digest = outcome.digest

    # 6. smoke check: do deps resolve and does the test runner work?
    # exit 11 = built but NOTHING to verify with (no smoke_cmd); exit 5 = ran the smoke check and it FAILED.
    if not env.smoke_cmd:
        return _fail("init", task.id, "no smoke_cmd", 11, db_path=args.db,
                     details={**meta, "image": tag, "digest": digest},
                     msg=f'error: built {tag}, but no smoke_cmd to verify it (stack: {env.stack}). Set "smoke_cmd" in task.json')
    _echo(f"• smoke check: {env.smoke_cmd}")
    smoke = C.run_in_image(runtime, tag, env.smoke_cmd, workdir="/workspace/repo")
    cid = DB.log_command(
        "init", "ok" if smoke.ok else "error", task_id=task.id,
        summary="env reproducible" if smoke.ok else "smoke check failed",
        details={
            **meta, "image": tag, "runtime": runtime, "digest": digest,
            "smoke_cmd": env.smoke_cmd, "smoke_exit": smoke.exit_code,
            "smoke_log_tail": _tail(smoke.output),
        },
        db_path=args.db,
    )
    if not smoke.ok:
        _err(_tail(smoke.output))
        _err(f"error: smoke check failed (exit {smoke.exit_code}). command #{cid}")
        return 5

    _echo(f"✅ bundle ready, env reproducible — {tag} ({digest[:19]}…). command #{cid}")
    return 0


# ----------------------------- validate command -----------------------------
@_audited
def cmd_validate(args) -> int:
    """Run the pass2pass/fail2pass buckets on the baseline image and assert the guardrail:
    every pass2pass passes, every fail2pass fails. With --check-patch, also apply patch.diff
    and confirm fail2pass flips to passing while pass2pass stays green."""
    bundle_dir = Path(args.bundle)
    task = B.load(bundle_dir)
    _echo(f"• loaded {bundle_dir / B.TASK_JSON}  (id={task.id})")

    p2p_dir = bundle_dir / "tests" / "pass2pass"
    f2p_dir = bundle_dir / "tests" / "fail2pass"
    if not (_has_tests(p2p_dir) or _has_tests(f2p_dir)):
        _err(f"error: no tests found under {p2p_dir} or {f2p_dir}")
        return 8
    patch = bundle_dir / "patch.diff"
    if args.check_patch and not (patch.is_file() and patch.read_text().strip()):
        _err(f"error: --check-patch needs a non-empty {patch}")
        return 8

    env = B.resolve_build_env(bundle_dir, task)
    if env.needs_edit:
        _err(f"error: stack not auto-detected — edit {env.dockerfile}, then run `task init` to build it")
        return 6

    # runtime + baseline image (build it if `init` hasn't already; --rebuild to force)
    try:
        runtime = C.resolve_runtime(args.runtime)
    except C.ContainerRuntimeError as e:
        return _fail("validate", task.id, "no container runtime", 3, db_path=args.db,
                     details={"error": str(e)}, msg=f"error: {e}")
    tag = task.image_tag()
    outcome = C.ensure_image(runtime, tag, bundle_dir, env.dockerfile, B.build_args(task),
                             force=(args.rebuild or args.no_cache), no_cache=args.no_cache, notify=_echo)
    if not outcome.ok:
        return _fail("validate", task.id, "image build failed", 4, db_path=args.db,
                     details={"image": tag, "build_log_tail": _tail(outcome.output)},
                     blob=outcome.output, msg=f"error: image build failed for {tag}")
    digest = outcome.digest
    _echo(f"• baseline image {tag}  (runtime: {runtime}, network: {args.network})")

    # baseline guardrail
    base_p2p = EV.run_bucket(runtime, tag, p2p_dir, name="pass2pass", network=args.network, timeout=args.timeout, test_cmd=task.test_cmd)
    base_f2p = EV.run_bucket(runtime, tag, f2p_dir, name="fail2pass", network=args.network, timeout=args.timeout, test_cmd=task.test_cmd)
    baseline_ok = EV.judge_baseline(base_p2p, base_f2p)
    _echo("• baseline:")
    _print_bucket("pass2pass", base_p2p, "pass")
    _print_bucket("fail2pass", base_f2p, "fail")

    report = {
        "image": tag, "runtime": runtime, "digest": digest, "network": args.network,
        "baseline": {"ok": baseline_ok, "pass2pass": base_p2p.counts(), "fail2pass": base_f2p.counts()},
        "checked_patch": bool(args.check_patch), "patched": None,
    }
    invariant_ok = baseline_ok

    # optional: apply the golden patch and confirm fail2pass flips
    if args.check_patch:
        pat_p2p = EV.run_bucket(runtime, tag, p2p_dir, name="pass2pass", patch=patch, network=args.network, timeout=args.timeout, test_cmd=task.test_cmd)
        pat_f2p = EV.run_bucket(runtime, tag, f2p_dir, name="fail2pass", patch=patch, network=args.network, timeout=args.timeout, test_cmd=task.test_cmd)
        patched_ok = EV.judge_patched(pat_p2p, pat_f2p)
        _echo("• after golden patch:")
        _print_bucket("pass2pass", pat_p2p, "pass")
        _print_bucket("fail2pass", pat_f2p, "flip")
        report["patched"] = {"ok": patched_ok, "pass2pass": pat_p2p.counts(), "fail2pass": pat_f2p.counts()}
        invariant_ok = baseline_ok and patched_ok

    summary = "baseline guardrail holds" if baseline_ok else "baseline guardrail VIOLATED"
    if args.check_patch:
        summary += "; patch " + ("flips fail2pass" if report["patched"]["ok"] else "does NOT flip fail2pass")
    cid = DB.log_command("validate", "ok" if invariant_ok else "error",
                         task_id=task.id, summary=summary, details=report, db_path=args.db)
    if invariant_ok:
        _echo(f"✅ {summary}. command #{cid}")
        return 0
    _err(f"❌ {summary}. command #{cid}")
    return 7


# ----------------------------- run command -----------------------------
@_audited
def cmd_run(args) -> int:
    """Run a solver against the task — it never sees the hidden buckets — then inject
    pass2pass/fail2pass and score whether its patch resolves the task."""
    bundle_dir = Path(args.bundle)
    task = B.load(bundle_dir)
    _echo(f"• loaded {bundle_dir / B.TASK_JSON}  (id={task.id})")

    p2p_dir = bundle_dir / "tests" / "pass2pass"
    f2p_dir = bundle_dir / "tests" / "fail2pass"
    if not _has_tests(f2p_dir):
        _err(f"error: run needs fail2pass tests to score resolution ({f2p_dir})")
        return 8

    env = B.resolve_build_env(bundle_dir, task)
    if env.needs_edit:
        _err(f"error: stack not auto-detected — edit {env.dockerfile}, then run `task init`")
        return 6

    # runtime + image (reuse init's image, or build it)
    try:
        runtime = C.resolve_runtime(args.runtime)
    except C.ContainerRuntimeError as e:
        return _fail("run", task.id, "no container runtime", 3, db_path=args.db,
                     details={"error": str(e)}, msg=f"error: {e}")
    tag = task.image_tag()
    outcome = C.ensure_image(runtime, tag, bundle_dir, env.dockerfile, B.build_args(task),
                             force=(args.rebuild or args.no_cache), no_cache=args.no_cache, notify=_echo)
    if not outcome.ok:
        return _fail("run", task.id, "image build failed", 4, db_path=args.db,
                     details={"image": tag, "build_log_tail": _tail(outcome.output)},
                     blob=outcome.output, msg=f"error: image build failed for {tag}")
    digest = outcome.digest

    # 1. BASELINE — run the buckets UNPATCHED (the "before"): pass2pass must pass, fail2pass must
    #    fail. `run` certifies this itself rather than trusting that someone ran `validate` first.
    base_p2p = (EV.run_bucket(runtime, tag, p2p_dir, name="pass2pass",
                              network=args.network, timeout=args.timeout, test_cmd=task.test_cmd,
                              memory=args.memory, cpus=args.cpus)
                if _has_tests(p2p_dir) else EV.BucketResult("pass2pass", [], 0))
    base_f2p = EV.run_bucket(runtime, tag, f2p_dir, name="fail2pass",
                             network=args.network, timeout=args.timeout, test_cmd=task.test_cmd,
                             memory=args.memory, cpus=args.cpus)
    baseline_ok = EV.judge_baseline(base_p2p, base_f2p)
    _echo("• baseline (before solver):")
    _print_bucket("pass2pass", base_p2p, "pass")
    _print_bucket("fail2pass", base_f2p, "fail")
    if not baseline_ok:
        _err("  ⚠️  baseline invariant does NOT hold (pass2pass should pass + fail2pass should fail "
             "unpatched) — this task would not pass `validate`; resolution cannot be certified.")

    # 2. SOLVE — solver sees the repo + problem statement, never the hidden buckets
    _echo(f"• solving (solver: {args.solver})")
    patch_text, solver_meta = _solve(args, runtime, tag, bundle_dir)
    if patch_text is None:
        return 12  # solver setup error (e.g. --solver golden with an empty patch.diff); _solve already explained why
    solver_meta["patch_lines"] = patch_text.count("\n")
    _echo(f"• solver [{solver_meta['kind']}] produced a {solver_meta['patch_lines']}-line patch (exit {solver_meta['exit']})")

    # 3. SCORE — inject the hidden buckets in a hermetic box with the solver's patch applied (the "after")
    with tempfile.TemporaryDirectory() as td:
        patch_arg = None
        if patch_text.strip():
            patch_arg = str(Path(td) / "solver.patch")
            Path(patch_arg).write_text(patch_text)
        sol_p2p = (EV.run_bucket(runtime, tag, p2p_dir, name="pass2pass", patch=patch_arg,
                                 network=args.network, timeout=args.timeout, test_cmd=task.test_cmd,
                                 memory=args.memory, cpus=args.cpus)
                   if _has_tests(p2p_dir) else EV.BucketResult("pass2pass", [], 0))
        sol_f2p = EV.run_bucket(runtime, tag, f2p_dir, name="fail2pass", patch=patch_arg,
                                network=args.network, timeout=args.timeout, test_cmd=task.test_cmd,
                                memory=args.memory, cpus=args.cpus)

    # RESOLVED requires a VALID baseline (fail2pass was actually failing) AND the patch flipping it
    # with no pass2pass regression — so a no-op on a malformed task can't score resolved.
    resolved = baseline_ok and EV.judge_patched(sol_p2p, sol_f2p)
    outcome = _run_outcome(resolved, solver_meta.get("exit", 0),
                           made_edits=bool(patch_text.strip()), scored=sol_f2p.produced,
                           baseline_ok=baseline_ok)
    _echo("• after solver:")
    _print_bucket("pass2pass", sol_p2p, "pass")
    _print_bucket("fail2pass", sol_f2p, "flip")

    report = {
        "image": tag, "runtime": runtime, "digest": digest, "network": args.network,
        "memory": args.memory, "cpus": args.cpus,   # applied resource caps (null = no limit)
        "solver": solver_meta, "resolved": resolved, "outcome": outcome,
        "baseline": {"ok": baseline_ok, "pass2pass": base_p2p.counts(), "fail2pass": base_f2p.counts()},
        "pass2pass": sol_p2p.counts(), "fail2pass": sol_f2p.counts(),
        "regressions": sol_p2p.failed + sol_p2p.errored,
        "resolved_tests": sol_f2p.passed,
        "unresolved_tests": sol_f2p.failed + sol_f2p.errored,
    }
    if not sol_f2p.produced:                       # patch didn't apply — keep the apply error
        report["scoring_log"] = _tail(sol_f2p.log)
    if sol_p2p.unproduced:                         # had tests but reported nothing — not a regression
        report["pass2pass_note"] = "pass2pass produced no results; cannot confirm absence of regression"
    verdict = "RESOLVED" if resolved else f"NOT resolved [{outcome}]"
    summary = f"{verdict} by {solver_meta['kind']} solver"
    report_path = Path(args.report) if args.report else bundle_dir / "run-report.json"
    artifacts_root = Path(args.artifacts) if args.artifacts else Path(".taskbundle") / "artifacts"
    report["report_path"] = str(report_path)        # where the report + artifacts went, for `query`
    report["artifacts_root"] = str(artifacts_root)   # per-run dir is <root>/<id>, resolved once we have the id
    cid = DB.log_command("run", "ok", task_id=task.id, summary=summary, details=report, db_path=args.db)
    artifacts_dir = artifacts_root / str(cid)
    report["id"], report["task_id"], report["artifacts"] = cid, task.id, str(artifacts_dir)

    try:                                            # best-effort side outputs — a write failure here must
        report_path.parent.mkdir(parents=True, exist_ok=True)        # NOT mask the verdict (already logged
        report_path.write_text(json.dumps(report, indent=2) + "\n")  # as #cid); the exit code stays the score
        _echo(f"• report → {report_path}")
        _write_run_artifacts(                       # the full run keyed by command id: patch + logs + report copy
            artifacts_dir, patch_text=patch_text, solver_log=solver_meta.get("log", ""),
            p2p_log=sol_p2p.log, f2p_log=sol_f2p.log, report=report,
        )
        _echo(f"• artifacts → {artifacts_dir}")
    except OSError as e:
        _err(f"warning: could not write report/artifacts ({e}) — verdict still logged as command #{cid}")

    if not resolved and solver_meta.get("log", "").strip():   # show what a failing solver did
        _echo("• solver output (tail):")
        _echo("\n".join("    " + ln for ln in _tail(solver_meta["log"]).splitlines()[-15:]))

    if resolved:
        _echo(f"✅ {summary}. command #{cid}")
        return 0
    _echo(f"⚠️  {summary} — regressions: {len(report['regressions'])}, still-failing: {len(report['unresolved_tests'])}. command #{cid}")
    return 9


# ----------------------------- query command -----------------------------
@_audited
def cmd_query(args) -> int:
    """Read-only ledger inspection: look up one command by id, or list recent ones.
    Never writes a row (reads must not pollute the audit log) and needs no runtime."""
    db_path = args.db
    if not Path(db_path).exists():                 # truly read-only — don't even create the db
        if args.id is None:
            _echo("no commands recorded yet")
            return 0
        _err(f"no command with id {args.id}")
        return 10                                  # distinct from a usage error's exit 2 (a malformed id)

    if args.id is not None:
        rec = DB.get_command(args.id, db_path)
        if rec is None:
            _err(f"no command with id {args.id}")
            return 10
        _echo(json.dumps(rec, indent=2)) if args.json else _render_record(rec)
        return 0

    # --stats: an aggregate scoreboard (respects the filters) instead of a row list
    if getattr(args, "stats", False):
        records = DB.find_commands(command=args.command, task_id=args.task, status=args.status,
                                   outcome=args.outcome, limit=None, db_path=db_path)
        if not records:
            _echo("no commands recorded yet")
            return 0
        stats = _compute_stats(records)
        _echo(json.dumps(stats, indent=2)) if args.json else _render_stats(stats)
        return 0

    # list (newest first), optionally filtered by command / task / status / outcome
    filtered = any((args.command, args.task, args.status, args.outcome))
    rows = (DB.find_commands(command=args.command, task_id=args.task, status=args.status,
                             outcome=args.outcome, limit=args.limit, db_path=db_path)
            if filtered else DB.recent_commands(args.limit, db_path))
    if not rows:
        _echo("no commands match those filters" if filtered else "no commands recorded yet")
        return 0
    _echo(json.dumps(rows, indent=2)) if args.json else _render_list(rows)
    return 0


# ----------------------------- Typer app -----------------------------
# Typer owns parsing + UX (help, errors, completion-off); each command collects its options
# into a plain namespace and hands off to the cmd_* business logic above (kept parser-agnostic
# and unchanged), then surfaces that logic's int exit code via `typer.Exit`.
app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
    help="Containerized task-bundle CLI for LLM coding benchmarks (SWE-bench style).",
)

# shared argument/option types (init/validate/run take --db + --runtime; query takes only --db)
BundleArg = Annotated[str, typer.Argument(help="bundle directory (default: .)")]
DbOpt = Annotated[str, typer.Option(help="SQLite ledger path")]
RuntimeOpt = Annotated[Optional[str], typer.Option(
    envvar="TASKBUNDLE_RUNTIME", help="container runtime: docker/podman/nerdctl (default: auto-detect)")]


@app.command(help="scaffold a bundle and build its reproducible container image")
def init(
    bundle: BundleArg = ".",
    repo: Annotated[Optional[str], typer.Option(help="git URL, or in-bundle path (scaffolds a new bundle)")] = None,
    commit: Annotated[Optional[str], typer.Option(help="base commit SHA / version label")] = None,
    id: Annotated[Optional[str], typer.Option(help="task id (default: bundle directory name)")] = None,
    install_cmd: Annotated[Optional[str], typer.Option(help="override: dependency install command")] = None,
    build_cmd: Annotated[Optional[str], typer.Option(help="override: extra build command")] = None,
    test_cmd: Annotated[Optional[str], typer.Option(help="override: command to run tests")] = None,
    smoke_cmd: Annotated[Optional[str], typer.Option(help="override: quick env-check command")] = None,
    regenerate: Annotated[bool, typer.Option("--regenerate", help="regenerate the Dockerfile even if one exists")] = False,
    no_build: Annotated[bool, typer.Option("--no-build", help="scaffold + resolve env only; skip build + smoke")] = False,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="build without cache")] = False,
    db: DbOpt = DB.DEFAULT_DB_PATH,
    runtime: RuntimeOpt = None,
):
    raise typer.Exit(code=cmd_init(SimpleNamespace(
        bundle=bundle, repo=repo, commit=commit, id=id,
        install_cmd=install_cmd, build_cmd=build_cmd, test_cmd=test_cmd, smoke_cmd=smoke_cmd,
        regenerate=regenerate, no_build=no_build, no_cache=no_cache, db=db, runtime=runtime,
    )))


@app.command(help="run baseline buckets and assert pass2pass pass + fail2pass fail")
def validate(
    bundle: BundleArg = ".",
    check_patch: Annotated[bool, typer.Option("--check-patch", help="also apply patch.diff and confirm fail2pass flips to passing")] = False,
    network: Annotated[str, typer.Option(help="container network for test runs (default: none = hermetic)")] = "none",
    timeout: Annotated[int, typer.Option(help="per-bucket test timeout in seconds")] = 300,
    rebuild: Annotated[bool, typer.Option("--rebuild", help="rebuild the image even if it exists (picks up local-repo edits)")] = False,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="build without cache (implies --rebuild)")] = False,
    db: DbOpt = DB.DEFAULT_DB_PATH,
    runtime: RuntimeOpt = None,
):
    raise typer.Exit(code=cmd_validate(SimpleNamespace(
        bundle=bundle, check_patch=check_patch, network=network, timeout=timeout,
        rebuild=rebuild, no_cache=no_cache, db=db, runtime=runtime,
    )))


@app.command(help="run a solver, inject the hidden buckets, and score whether it resolves the task")
def run(
    bundle: BundleArg = ".",
    solver: Annotated[str, typer.Option(help='"golden" (apply patch.diff — self-test), "noop" (no edits), or any shell command run in the image (a script or LLM agent that edits files)')] = "golden",
    solver_network: Annotated[Optional[str], typer.Option(help="network for the solver box (default: on — LLM solvers need it; 'none' forbids)")] = None,
    solver_timeout: Annotated[int, typer.Option(help="solver timeout in seconds")] = 900,
    solver_env: Annotated[Optional[List[str]], typer.Option("--solver-env", help="host env var NAME to forward into the solver box (repeatable; e.g. OPENAI_API_KEY). The value comes from your shell env — never the CLI argv or the ledger")] = None,
    network: Annotated[str, typer.Option(help="network for the SCORING test runs (default: none = hermetic)")] = "none",
    timeout: Annotated[int, typer.Option(help="per-bucket scoring timeout in seconds")] = 300,
    memory: Annotated[Optional[str], typer.Option(help="cap container RAM for the solve + score boxes (e.g. 2g, 512m); bounds an untrusted solver's blast radius. Default: no limit")] = None,
    cpus: Annotated[Optional[str], typer.Option(help="cap container CPUs for the solve + score boxes (e.g. 1.5). Default: no limit")] = None,
    report: Annotated[Optional[str], typer.Option(help="write the JSON report here (default: <bundle>/run-report.json)")] = None,
    artifacts: Annotated[Optional[str], typer.Option(help="per-run artifacts dir, written as <dir>/<id>/ (default: .taskbundle/artifacts)")] = None,
    rebuild: Annotated[bool, typer.Option("--rebuild", help="rebuild the image even if it exists")] = False,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="build without cache (implies --rebuild)")] = False,
    db: DbOpt = DB.DEFAULT_DB_PATH,
    runtime: RuntimeOpt = None,
):
    raise typer.Exit(code=cmd_run(SimpleNamespace(
        bundle=bundle, solver=solver, solver_network=solver_network, solver_timeout=solver_timeout,
        solver_env=solver_env, network=network, timeout=timeout, memory=memory, cpus=cpus,
        report=report, artifacts=artifacts,
        rebuild=rebuild, no_cache=no_cache, db=db, runtime=runtime,
    )))


@app.command(help="inspect the ledger: look up an id, list (with filters), or --stats (read-only; no runtime)")
def query(
    id: Annotated[Optional[int], typer.Argument(help="command id to look up (omit to list / --stats)")] = None,
    db: DbOpt = DB.DEFAULT_DB_PATH,
    limit: Annotated[int, typer.Option(help="rows to show when listing")] = 10,
    command: Annotated[Optional[str], typer.Option("--command", help="filter: only this command type (init/validate/run/query)")] = None,
    task: Annotated[Optional[str], typer.Option("--task", help="filter: only this task id")] = None,
    status: Annotated[Optional[str], typer.Option("--status", help="filter: ok | error")] = None,
    outcome: Annotated[Optional[str], typer.Option("--outcome", help="filter: a run outcome (resolved/unresolved/no_edits/solver_error/…)")] = None,
    stats: Annotated[bool, typer.Option("--stats", help="show an aggregate scoreboard instead of a list (respects the filters)")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="emit the raw record(s) / stats as JSON instead of a table")] = False,
):
    raise typer.Exit(code=cmd_query(SimpleNamespace(
        id=id, db=db, limit=limit, command=command, task=task, status=status,
        outcome=outcome, stats=stats, json=as_json)))


# ----------------------------- Entry point -----------------------------
# Map usage errors to a clean exit 2 from WHICHEVER click Typer parses with. Typer ≥ 0.24 (on
# Python ≥ 3.10) vendors its own click as typer._click, whose exceptions are NOT subclasses of the
# standalone click's — so a bare `except click.exceptions.ClickException` misses them and a usage
# error escapes as a raw traceback. Keep the real click, and add the vendored lineage when present.
_CLICK_EXC = (click.exceptions.ClickException,)
_ABORT = (click.exceptions.Abort,)
try:
    import typer._click.exceptions as _tce
    _CLICK_EXC += (_tce.ClickException,)
    _ABORT += (_tce.Abort,)
except Exception:
    pass


def main(argv: Optional[list] = None) -> int:
    """Drive the Typer app in non-standalone mode so a command's int exit code propagates as
    main()'s return value (the suite calls main(argv) and asserts on it), usage errors map to
    exit 2, and BundleError stays exit 2 — the same contract the argparse version had."""
    command = get_command(app)
    try:
        return command.main(args=argv, prog_name="task", standalone_mode=False) or 0
    except _CLICK_EXC as e:                        # bad/missing args, unknown command → exit 2
        e.show()
        return e.exit_code
    except _ABORT:                                 # Ctrl-C / EOF
        _err("Aborted!")
        return 130
    except B.BundleError as e:                     # malformed/missing bundle inputs
        _err(f"error: {e}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
