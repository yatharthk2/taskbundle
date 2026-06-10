"""CLI presentation helpers, split out of the `task` entry point so it stays focused on command
logic. Pure output / formatting + the type-aware `query` renderers — no command logic, and no
taskbundle imports (it only formats data the commands hand it)."""
# ----------------------------- Imports -----------------------------
import sys
from pathlib import Path

_LOG_TAIL = 4000  # cap log text stored in the ledger / printed on failure


# ----------------------------- Output -----------------------------
def _echo(msg: str = "") -> None:
    print(msg, flush=True)


def _err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _tail(s: str) -> str:
    return s[-_LOG_TAIL:]


# ----------------------------- Test-bucket formatting -----------------------------
def _has_tests(d: Path) -> bool:
    # a non-empty bucket counts as having tests — language-agnostic, not just Python test_*.py
    return d.is_dir() and any(p.is_file() for p in d.rglob("*"))


def _fmt_counts(b) -> str:
    if not b.produced:
        return f"no tests reported (exit {b.exit_code})"
    segs = [f"{len(names)} {label}" for names, label in (
        (b.passed, "passed"), (b.failed, "failed"), (b.errored, "error"), (b.skipped, "skipped")
    ) if names]
    return ", ".join(segs) if segs else "0 tests"


def _print_bucket(label: str, b, expect: str) -> None:
    """expect: 'pass' (all clean), 'fail' (ran, none passed), or 'flip' (all now pass)."""
    if expect == "pass":
        ok, want, offenders = b.clean, "all pass", b.failed + b.errored
    elif expect == "flip":
        ok, want, offenders = b.all_passed, "now pass", b.failed + b.errored
    else:  # fail (baseline): need a genuine failure and nothing passing (a skip is not a fail)
        ok, want, offenders = (b.any_failed and not b.any_passed), "all fail", b.passed + b.skipped
    _echo(f"    {'✓' if ok else '✗'} {label}: {_fmt_counts(b)}  (want: {want})")
    if ok:
        return
    if offenders:
        _echo(f"        offending: {', '.join(offenders)}")
    if not b.produced:
        _echo(f"        ✗ no test results (exit {b.exit_code})")
        if b.log:
            _echo("\n".join("        " + ln for ln in _tail(b.log).splitlines()))


# ----------------------------- query rendering -----------------------------
def _short(s: str, n: int) -> str:
    s = (s or "").replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def _count_str(c) -> str:
    """One line from a stored bucket dict ({passed:[...], failed:[...], ...})."""
    c = c or {}
    segs = [f"{len(c.get(k) or [])} {k}" for k in ("passed", "failed", "error", "skipped") if c.get(k)]
    return ", ".join(segs) if segs else "0 tests"


def _render_list(rows) -> None:
    """Compact table of recent invocations, newest first."""
    idw = max([2] + [len(str(r.get("id", "?"))) for r in rows])
    _echo(f"{'id':>{idw}}  {'when':<19}  {'command':<8}  summary")
    for r in rows:
        when = (r.get("ts") or "")[:19].replace("T", " ")
        _echo(f"{str(r.get('id', '?')):>{idw}}  {when:<19}  {(r.get('command') or '?'):<8}  "
              f"{_short(r.get('summary') or r.get('status') or '', 56)}")


def _render_record(rec) -> None:
    """Detailed, type-aware view of one ledger row (renders only what's present)."""
    d = rec.get("details") or {}
    if rec.get("command") == "run" and d.get("artifacts_root") and not d.get("artifacts"):
        d = {**d, "artifacts": str(Path(d["artifacts_root"]) / str(rec.get("id")))}  # per-run dir = <root>/<id>
    _echo(f"command #{rec.get('id')}  [{rec.get('command', '?')}]  {rec.get('status', '?')}")
    _echo(f"  when:    {rec.get('ts', '?')}")
    if rec.get("task_id"):
        _echo(f"  task:    {rec['task_id']}")
    if rec.get("summary"):
        _echo(f"  summary: {rec['summary']}")
    renderer = {"init": _render_init, "validate": _render_validate, "run": _render_run}.get(rec.get("command"))
    if renderer:
        renderer(d)
    elif d:                                        # unknown/older shape — show what's there
        for k, v in d.items():
            _echo(f"  {k}: {_short(str(v), 80)}")


def _render_init(d) -> None:
    _echo(f"  tier:    {d.get('tier', '?')}  (stack: {d.get('stack', '?')})")
    if d.get("runtime"): _echo(f"  runtime: {d['runtime']}")
    if d.get("image"):   _echo(f"  image:   {d['image']}")
    if d.get("digest"):  _echo(f"  digest:  {d['digest']}")


def _render_validate(d) -> None:
    base = d.get("baseline") or {}
    _echo(f"  guardrail: {'held' if base.get('ok') else 'VIOLATED'}")
    _echo(f"    baseline pass2pass: {_count_str(base.get('pass2pass'))}")
    _echo(f"    baseline fail2pass: {_count_str(base.get('fail2pass'))}")
    pat = d.get("patched")
    if pat:
        _echo(f"  after patch: {'ok' if pat.get('ok') else 'NOT ok'}")
        _echo(f"    patched pass2pass: {_count_str(pat.get('pass2pass'))}")
        _echo(f"    patched fail2pass: {_count_str(pat.get('fail2pass'))}")


def _render_run(d) -> None:
    solver = d.get("solver") or {}
    _echo(f"  solver:  {solver.get('kind', '?')}  ({_short(str(solver.get('command', '')), 56)})")
    if solver.get("solver_env"):
        _echo(f"  solver env: {', '.join(solver['solver_env'])}  (forwarded by name)")
    _echo(f"  outcome: {d.get('outcome', '?')}  (resolved={d.get('resolved')})")
    base = d.get("baseline")
    if base is not None:
        _echo(f"  baseline: {'ok' if base.get('ok') else 'INVALID — would fail validate'}")
    _echo(f"    fail2pass: {_count_str(d.get('fail2pass'))}")
    _echo(f"    pass2pass: {_count_str(d.get('pass2pass'))}")
    if d.get("resolved_tests"):
        _echo(f"    resolved: {', '.join(d['resolved_tests'])}")
    if d.get("unresolved_tests"):
        _echo(f"    still failing: {', '.join(d['unresolved_tests'])}")
    if d.get("regressions"):
        _echo(f"    regressions: {', '.join(d['regressions'])}")
    if d.get("report_path"):
        _echo(f"  report:  {d['report_path']}")
    if d.get("artifacts"):
        _echo(f"  artifacts: {d['artifacts']}")
    if d.get("scoring_log"):
        _echo(f"  scoring: {_short(d['scoring_log'], 200)}")
    if solver.get("log"):
        _echo(f"  solver log: {_short(solver['log'], 200)}")


# ----------------------------- stats (scoreboard) -----------------------------
def _compute_stats(records) -> dict:
    """Aggregate ledger records into a scoreboard: totals by command type, run outcomes, and
    per-task resolved/total for runs. Pure — operates on the decoded record dicts."""
    by_command, by_outcome, per_task, tasks = {}, {}, {}, set()
    for r in records:
        cmd = r.get("command") or "?"
        by_command[cmd] = by_command.get(cmd, 0) + 1
        if r.get("task_id"):
            tasks.add(r["task_id"])
        if cmd == "run":
            d = r.get("details") or {}
            by_outcome[d.get("outcome") or "?"] = by_outcome.get(d.get("outcome") or "?", 0) + 1
            t = per_task.setdefault(r.get("task_id") or "?", {"resolved": 0, "runs": 0})
            t["runs"] += 1
            t["resolved"] += 1 if d.get("resolved") else 0
    return {"commands": len(records), "tasks": len(tasks), "by_command": by_command,
            "runs": by_command.get("run", 0), "by_outcome": by_outcome, "per_task": per_task}


def _render_stats(s) -> None:
    """Human scoreboard: ledger totals, run outcomes, and a per-task resolved/total table."""
    _echo(f"ledger: {s['commands']} command(s) across {s['tasks']} task(s)")
    if s.get("by_command"):
        _echo("  " + " · ".join(f"{k}: {v}" for k, v in sorted(s["by_command"].items())))
    if s.get("runs"):
        oc = sorted((s.get("by_outcome") or {}).items(), key=lambda kv: (-kv[1], kv[0]))
        _echo(f"runs: {s['runs']} — " + " · ".join(f"{k} {v}" for k, v in oc))
        pt = s.get("per_task") or {}
        if pt:
            _echo("per task (runs resolved/total):")
            width = max(len(t) for t in pt)
            for t, c in sorted(pt.items()):
                pct = round(100 * c["resolved"] / c["runs"]) if c["runs"] else 0
                _echo(f"  {t:<{width}}  {c['resolved']}/{c['runs']}  ({pct}%)")
