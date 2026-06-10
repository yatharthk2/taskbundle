# `task run`

`task run <bundle>` runs a **solver** against the task, then scores whether its patch resolves it.
The defining property: the solver **never sees the hidden `pass2pass` / `fail2pass` buckets** — they
live in the bundle and are injected only afterward, for scoring.

It works in two **isolated** containers:

1. **Solve** — the solver runs at `/workspace/repo`, seeing the repo, its *own* visible tests, and
   `description.md` (at `$TASKBUNDLE_PROBLEM`) — but **not** the buckets. Its edits are captured as a
   patch via `git diff` (so *any* file-editing command works — a stub, a shell one-liner, or an LLM
   agent). The solve box has **network on** by default (LLM agents need it).
2. **Score** — in a separate **hermetic** container (`--network none`), the captured patch is applied
   to a **clean baseline** image and the hidden buckets are run. **Resolved** = every `fail2pass`
   passes **and** no `pass2pass` regresses.

```bash
task run <bundle> [--solver golden|noop|"<shell cmd>"] [--solver-network none] [--solver-timeout 900]
                  [--solver-env NAME ...] [--network none] [--timeout 300] [--memory 2g] [--cpus 1.5]
                  [--report PATH] [--artifacts DIR] [--rebuild] [--no-cache] [--runtime docker] [--db PATH]
```

---

## The steps

`run` runs these in order (`cmd_run` in `cli/task.py`). Each can short-circuit with its own exit code.

### 1. Load config
- `B.load` reads + validates `task.json`. Missing/invalid → `BundleError` → **exit 2**.

### 2. Check preconditions
- `tests/fail2pass/` must contain test files — `run` needs them to score resolution. Else → **exit 8**.

### 3. Resolve the build environment
- `resolve_build_env` (same tiers as `init`). Generic starter (`needs_edit`) → **exit 6** (run `init` first).

### 4. Resolve runtime + ensure the image
- Resolve docker/podman/nerdctl. None reachable → **exit 3**.
- Reuse `init`'s image, or build it (forced fresh by `--rebuild` / `--no-cache`). Build failure → **exit 4**.

### 5. Baseline (before the solver)
- Run the buckets **unpatched** and `judge_baseline`: `pass2pass` should pass, `fail2pass` should fail.
- If the baseline is invalid, it **warns and continues**, but the result can't certify as resolved
  (`outcome = invalid_baseline`). `run` checks this itself — it doesn't trust that you ran `validate`.

### 6. Solve (isolated — the solver never sees the buckets)
- Built-ins: `golden` (apply `patch.diff`), `noop` (no edits). Anything else is a shell command run in
  a fresh container.
- Only `description.md` is mounted into the solve box; the buckets are **never** mounted. The repo is
  git-snapshotted, the solver runs, then `git diff` (vs the base commit's SHA) captures exactly its edits.
- `--solver-env NAME` forwards a host env var **by name** (`-e NAME`, value from your shell) — so a
  secret like an API key never hits the argv, a log, or the ledger.
- **Containment:** a fork-bomb `--pids-limit` is always on; `--memory 2g` / `--cpus 1.5` (opt-in, applied
  to the solve **and** score boxes) bound an untrusted solver's host RAM/CPU blast radius. Left opt-in so a
  cap can't silently OOM-kill a heavy-but-legit test suite and mislabel it. Recorded in the report.
- A setup error (e.g. `--solver golden` with an empty `patch.diff`) → **exit 12**.

### 7. Score (hermetic)
- In a clean container with `--network none`, the captured patch is `git apply`-ed onto the baseline
  image and the buckets are re-run. `judge_patched`: `pass2pass` still clean **and** `fail2pass` all pass.

### 8. Classify + persist
- `resolved = baseline_ok AND judge_patched(...)`. `_run_outcome` labels the run (see the table below).
- Writes a JSON **report** (`--report`, default `<bundle>/run-report.json`) and a per-run **artifacts
  dir** (`--artifacts`, default `.taskbundle/artifacts/<id>/`: `solver.patch`, `solver.log`,
  `pass2pass.log`, `fail2pass.log`, `run-report.json`), and a ledger row as `command #<id>`.

### 9. Verdict
- ✅ **exit 0** if resolved, ⚠️ **exit 9** if not (the report is still written either way).

---

## Exit codes (quick reference)

| Code | Meaning |
|---|---|
| `0` | **resolved** — every `fail2pass` passes, no `pass2pass` regression |
| `2` | bundle error (missing/invalid `task.json`) |
| `3` | no container runtime available |
| `4` | image build failed |
| `6` | stack not auto-detected — run `task init` first |
| `8` | no `fail2pass` tests to score against |
| `9` | **not resolved** (any non-resolved outcome below) |
| `12` | solver setup error (e.g. `--solver golden` with an empty `patch.diff`) |
| `70` | unexpected internal error (audited; `TASKBUNDLE_DEBUG=1` for the traceback) |

## Outcomes (the report's `outcome` field)

`run` classifies *why* a run did or didn't resolve, so a collaborator can tell the modes apart:

| `outcome` | Meaning | Exit |
|---|---|---|
| `resolved` | patch applied, `fail2pass` all green, no regression | `0` |
| `unresolved` | patch applied + tests ran, but `fail2pass` not fully green | `9` |
| `patch_failed` | the captured patch didn't apply → no tests ran | `9` |
| `no_edits` | solver made no changes (clean exit) | `9` |
| `solver_error` | solver crashed (non-zero exit) with no patch | `9` |
| `solver_timeout` | solver hit `--solver-timeout` | `9` |
| `invalid_baseline` | task wouldn't pass `validate` (e.g. `fail2pass` already green) | `9` |

---

## Scenarios

| # | Solver / situation | Outcome | Exit |
|---|---|---|---|
| 1 | `golden` (apply `patch.diff`) | `resolved` | `0` |
| 2 | `noop` (no edits) | `no_edits` | `9` |
| 3 | shell command that fixes the bug | `resolved` | `0` |
| 4 | solver edits files but doesn't fix it | `unresolved` | `9` |
| 5 | LLM agent (`openai_agent.py`) | `resolved`/`unresolved` | `0`/`9` |
| 6 | solver crashes (non-zero, no edits) | `solver_error` | `9` |
| 7 | `golden` with an empty `patch.diff` | — (setup error) | `12` |
| 8 | no `fail2pass` tests | — (precondition) | `8` |
| 9 | task has an invalid baseline | `invalid_baseline` | `9` |

### 1. Golden solver (the self-test)

**Input**
```bash
task run cli/examples/hello-task
```
**Output**
```
• loaded cli/examples/hello-task/task.json  (id=hello-task)
• baseline (before solver):
    ✓ pass2pass: 2 passed  (want: all pass)
    ✓ fail2pass: 2 failed  (want: all fail)
• solving (solver: golden)
• solver [golden] produced a 13-line patch (exit 0)
• after solver:
    ✓ pass2pass: 2 passed  (want: all pass)
    ✓ fail2pass: 2 passed  (want: now pass)
• report → cli/examples/hello-task/run-report.json
• artifacts → .taskbundle/artifacts/1
✅ RESOLVED by golden solver. command #1
exit 0
```
**Behavior**
Applies the golden `patch.diff`; `fail2pass` flips to passing with no regression — confirms the task + solution are sound.

### 2. Noop solver (the lower bound)

**Input**
```bash
task run cli/examples/hello-task --solver noop
```
**Output**
```
• baseline (before solver):
    ✓ pass2pass: 2 passed  (want: all pass)
    ✓ fail2pass: 2 failed  (want: all fail)
• solving (solver: noop)
• solver [noop] produced a 0-line patch (exit 0)
• after solver:
    ✓ pass2pass: 2 passed  (want: all pass)
    ✗ fail2pass: 2 failed  (want: now pass)
        offending: test_factorial_five, test_factorial_six
⚠️  NOT resolved [no_edits] by noop solver — regressions: 0, still-failing: 2. command #N
exit 9
```
**Behavior**
The solver changes nothing, so `fail2pass` stays red — the sanity floor that proves the buckets actually gate on a fix.

### 3. A shell command as the solver

**Input**
```bash
task run cli/examples/hello-task \
  --solver 'sed -i "s/range(1, n)/range(1, n + 1)/" mathx/core.py'
```
**Output**
```
• solving (solver: sed -i "s/range(1, n)/range(1, n + 1)/" mathx/core.py)
• solver [command] produced a 13-line patch (exit 0)
• after solver:
    ✓ pass2pass: 2 passed  (want: all pass)
    ✓ fail2pass: 2 passed  (want: now pass)
✅ RESOLVED by command solver. command #N
exit 0
```
**Behavior**
Any file-editing command is a valid solver — the edit is captured by `git diff` and scored exactly like the golden patch.

### 4. Solver edits files but doesn't fix it

**Input**
```bash
task run cli/examples/hello-task --solver 'echo "# noise" >> mathx/core.py'
```
**Output**
```
• solver [command] produced a 9-line patch (exit 0)
• after solver:
    ✓ pass2pass: 2 passed  (want: all pass)
    ✗ fail2pass: 2 failed  (want: now pass)
        offending: test_factorial_five, test_factorial_six
⚠️  NOT resolved [unresolved] by command solver — regressions: 0, still-failing: 2. command #N
exit 9
```
**Behavior**
The patch applied and tests ran, but `fail2pass` is still red — a real but *wrong* attempt (`unresolved`, distinct from `no_edits`).

### 5. An LLM agent as the solver

**Input**
```bash
export OPENAI_API_KEY=sk-...
task run cli/examples/openai-demo \
  --solver 'python /opt/openai_agent.py' \
  --solver-env OPENAI_API_KEY
```
**Output**
```
• solving (solver: python /opt/openai_agent.py)
• solver [command] produced an N-line patch (exit 0)
• after solver:
    ✓ pass2pass: … (want: all pass)
    ✓ fail2pass: … (want: now pass)
✅ RESOLVED by command solver. command #N
exit 0      # or exit 9 [unresolved] if the model's edit doesn't fix it
```
**Behavior**
The agent runs in the *same* isolated solve box (still can't see the buckets); the key is forwarded by name via `--solver-env`, so it never touches the argv or ledger.

### 6. Solver crashes

**Input**
```bash
task run cli/examples/hello-task --solver 'exit 1'
```
**Output**
```
• solver [command] produced a 0-line patch (exit 1)
• after solver:
    ✗ fail2pass: 2 failed  (want: now pass)
⚠️  NOT resolved [solver_error] by command solver — regressions: 0, still-failing: 2. command #N
exit 9
```
**Behavior**
A non-zero exit with no patch is a crash, not a clean noop — labelled `solver_error` so you can tell a broken solver from one that simply did nothing.

### 7. `golden` with an empty `patch.diff`

**Input**
```bash
task run my-task --solver golden        # patch.diff is empty
```
**Output**
```
error: --solver golden needs a non-empty my-task/patch.diff
exit 12
```
**Behavior**
The built-in golden self-test has no patch to apply — a setup error, caught before scoring.

### 8. No `fail2pass` tests

**Input**
```bash
task run my-task                        # tests/fail2pass/ is empty
```
**Output**
```
error: run needs fail2pass tests to score resolution (my-task/tests/fail2pass)
exit 8
```
**Behavior**
Without `fail2pass` there's nothing to resolve, so `run` stops before doing any work.

### 9. Invalid baseline

**Input**
```bash
task run my-broken-task                 # fail2pass already passes unpatched
```
**Output**
```
• baseline (before solver):
    ✓ pass2pass: … (want: all pass)
    ✗ fail2pass: … (want: all fail)
  ⚠️  baseline invariant does NOT hold (pass2pass should pass + fail2pass should fail unpatched) …
…
⚠️  NOT resolved [invalid_baseline] … command #N
exit 9
```
**Behavior**
The task wouldn't pass `validate` (its `fail2pass` is already green), so resolution can't be certified — even a "correct" solver can't score `resolved` on a malformed task.
