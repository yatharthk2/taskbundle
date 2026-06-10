# Problem & solution — what each CLI command is for, and the design goals the implementation meets.

The CLI is built around four commands. Here's what each one is for.

## 1. `task init` — scaffold the task

Sets up a task's starting point from its bundle: it reads `task.json` and creates the initial
repository state — for example, cloning the repo at the specified commit — so the task is ready to
build and run.

## 2. `task validate` — check the baseline

Runs the hidden `pass2pass` and `fail2pass` tests on the untouched (baseline) repo — before any
solver edits it — to confirm the task is well-formed: `pass2pass` should pass and `fail2pass` should
fail.

## 3. `task run` — solve, then score

Asks a solver (e.g. an LLM) to fix the task, then pulls in the hidden `pass2pass` / `fail2pass` tests
to score whether the solution is correct — which tests it fixed and which it broke.

**The key rule:** the solver **never sees** the `pass2pass` / `fail2pass` tests before it solves —
they're injected only afterward, for scoring. It *does* get to see the repo's other (non-hidden) tests.

## 4. `task query` — inspect any run (the database)

Every command is logged to a lightweight SQLite ledger; `task query <id>` shows what happened — for a
run, which tests its solution passed and which it failed — so a collaborator can inspect a run by its
id without reproducing it.

---

## Beyond the core commands

### ✓ End-to-end on a real SWE-bench Pro task
Validated on `ansible/ansible` at a pinned commit ([`examples/ansible-combine-vars`](../examples/ansible-combine-vars/)):
`init` clones + builds it, and `validate`/`run` execute its real `combine_vars` tests (1 fail2pass, 15 pass2pass).

### ✓ Arbitrariness — works with any language, not just Python
The Dockerfile owns the environment (any stack builds via a hand-written or command-generated
Dockerfile), the **test runner is configurable** (`test_cmd` runs any framework that writes JUnit to
`$TASKBUNDLE_JUNIT`; the tests are mounted at `$TASKBUNDLE_BUCKET`), and grading reads **JUnit XML**, a
cross-language standard. So a non-Python task *runs* end-to-end, not just builds (verified with a
non-pytest runner). Only the stack *auto-detection* is Python-only today, one branch each.

### ✓ Observability & debuggability
Every run drops a JSON report **and** an artifacts dir (the captured patch, per-stage test logs, the
report) keyed by command id — so a failure is fully diagnosable from its id alone, with no re-run.

### ✓ Isolation & safety
Every step runs in a throwaway `--rm` container and never mutates the host; scoring is hermetic
(no network) on a clean image, with a fork-bomb cap, a host-side timeout-kill, and opt-in
`--memory` / `--cpus` caps so a bad solver can't escape, hang, or exhaust host RAM/CPU.

### ✓ Reproducibility & determinism
Deterministic image tags from id+commit, a pinned-commit checkout, and a UTC-stamped ledger make a
task behave the same on a given machine — one pinned base-image digest away from byte-exact across
machines.

### ✓ Performance — a fast authoring loop
The Docker layer cache front-loads the stable steps, the image is built once and reused by
`validate`/`run`, and `--no-build` lets you author a bundle without Docker at all.

### ✓ Clear UX + a fourth command (creativity)
Meaningful errors with distinct exit codes and readable verdicts, plus `query` beyond the required
three — and an LLM-agent solver seam (`--solver-env`) that forwards secrets by name, never to a log.

### ✓ Included artifacts
A validatable example bundle, the `run` JSON report artifact, and design notes
([DESIGN.md](../DESIGN.md)) — all included.
