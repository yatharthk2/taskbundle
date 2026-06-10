# `task validate`

`task validate <bundle>` runs the hidden `pass2pass` / `fail2pass` test buckets on the
**baseline** image and asserts the SWE-bench guardrail: every `pass2pass` test passes and every
`fail2pass` test fails *before* any fix. With `--check-patch` it also applies the golden
`patch.diff` and confirms `fail2pass` flips to passing while `pass2pass` stays green.

It builds the image first if `init` hasn't already (reusing the same Dockerfile). Test runs are
**hermetic** by default (`--network none`), each bucket is mounted **read-only**, and results are
read from **JUnit XML** (so an assertion *failure* is told apart from a collection/import *error*).

```bash
task validate <bundle> [--check-patch] [--network none] [--timeout 300]
                       [--rebuild] [--no-cache] [--runtime docker] [--db PATH]
```

---

## The steps

`validate` runs these in order (`cmd_validate` in `cli/task.py`). Each can short-circuit with its
own exit code.

### 1. Load config
- `B.load` reads + validates `task.json`. Missing/invalid → `BundleError` → **exit 2**.

### 2. Check preconditions
- At least one bucket (`tests/pass2pass/` or `tests/fail2pass/`) must contain test files
  (`test_*.py` / `*_test.py`), else ❌ **exit 8** (`no tests found …`).
- If `--check-patch`, `patch.diff` must exist and be non-empty, else ❌ **exit 8**.

### 3. Resolve the build environment
- `resolve_build_env` picks the Dockerfile (same tiers as `init`). If it's only a generic
  **starter** (`needs_edit`) → ❌ **exit 6** (`edit … then run \`task init\``).
- `validate` does not regenerate — it reuses whatever `init` would build.

### 4. Resolve runtime + ensure the image
- Resolve docker/podman/nerdctl (or `--runtime`/`TASKBUNDLE_RUNTIME`). None reachable → ❌ **exit 3**.
- Reuse the image `init` built; if it's missing, **build it now** (forced fresh by `--rebuild` /
  `--no-cache`). Build failure → ❌ **exit 4**.

### 5. Baseline guardrail
- Run `pass2pass` and `fail2pass` in the image — mounted **read-only**, `--network none`,
  per-bucket `--timeout` (300s). Results parsed from JUnit XML.
- `judge_baseline` holds when: **pass2pass is clean** (no fail/error, and expected tests actually
  ran) **and fail2pass genuinely fails** (≥1 fail/error and nothing passing). An empty or
  all-*skipped* `fail2pass` does **not** count — an inert task can't certify.

### 6. Patch check (optional, `--check-patch`)
- `git apply` the golden `patch.diff` inside a throwaway container, then re-run both buckets.
- `judge_patched` holds when: **pass2pass still clean** **and fail2pass now all passes** (flips).

### 7. Verdict + log
- `invariant_ok = baseline_ok` (and `patched_ok` too, when `--check-patch`).
- Writes the full per-test breakdown to the ledger as `command #<id>`.
- ✅ **exit 0** if the invariant holds, ❌ **exit 7** if it's violated.

---

## Exit codes (quick reference)

| Code | Meaning |
|---|---|
| `0` | guardrail holds (and patch flips `fail2pass`, if `--check-patch`) |
| `2` | bundle error (missing/invalid `task.json`) |
| `3` | no container runtime available |
| `4` | image build failed |
| `6` | stack not auto-detected — run `task init` (edit the Dockerfile) first |
| `7` | guardrail **violated** (baseline wrong, or the patch didn't flip `fail2pass`) |
| `8` | no tests in the buckets, or `--check-patch` with an empty `patch.diff` |
| `70` | unexpected internal error (audited; `TASKBUNDLE_DEBUG=1` for the traceback) |

---

## Scenarios

| # | Scenario | Exit | Result |
|---|---|---|---|
| 1 | Baseline guardrail holds | `0` | pass2pass pass, fail2pass fail |
| 2 | `--check-patch`, patch flips `fail2pass` | `0` | baseline holds **and** patch fixes it |
| 3 | `fail2pass` already passes on baseline | `7` | guardrail violated |
| 4 | `--check-patch`, patch does **not** flip `fail2pass` | `7` | baseline ok, patch insufficient |
| 5 | No test files in either bucket | `8` | nothing to validate |
| 6 | `--check-patch` with an empty `patch.diff` | `8` | nothing to apply |
| 7 | Stack not auto-detected (no Dockerfile yet) | `6` | run `task init` first |
| 8 | No Docker running | `3` | no runtime |
| 9 | Image not built yet | `0` | builds it first, then validates |

### 1. Baseline guardrail holds

**Input**
```bash
task validate cli/examples/hello-task
```
**Output**
```
• loaded cli/examples/hello-task/task.json  (id=hello-task)
• baseline image taskbundle-hello-task:v1  (runtime: docker, network: none)
• baseline:
    ✓ pass2pass: 2 passed  (want: all pass)
    ✓ fail2pass: 2 failed  (want: all fail)
✅ baseline guardrail holds. command #N
exit 0
```
**Behavior**
The untouched repo behaves as a valid task should — pass2pass green, fail2pass red — so the task is well-formed.

### 2. Confirm the golden patch fixes it (`--check-patch`)

**Input**
```bash
task validate cli/examples/hello-task --check-patch
```
**Output**
```
• loaded cli/examples/hello-task/task.json  (id=hello-task)
• baseline image taskbundle-hello-task:v1  (runtime: docker, network: none)
• baseline:
    ✓ pass2pass: 2 passed  (want: all pass)
    ✓ fail2pass: 2 failed  (want: all fail)
• after golden patch:
    ✓ pass2pass: 2 passed  (want: all pass)
    ✓ fail2pass: 2 passed  (want: now pass)
✅ baseline guardrail holds; patch flips fail2pass. command #N
exit 0
```
**Behavior**
Baseline holds, and applying `patch.diff` flips fail2pass to passing with no pass2pass regression — the task + golden solution are both sound.

### 3. Guardrail violated — `fail2pass` passes on baseline

**Input**
```bash
task validate cli/examples/hello-task
```
**Output**
```
• baseline:
    ✓ pass2pass: 2 passed  (want: all pass)
    ✗ fail2pass: 2 passed  (want: all fail)
        offending: test_factorial_five, test_factorial_six
❌ baseline guardrail VIOLATED. command #N
exit 7
```
**Behavior**
A fail2pass test passes before any fix, so the task isn't actually demonstrating a bug — the offending tests are named.

### 4. `--check-patch`, but the patch doesn't flip `fail2pass`

**Input**
```bash
task validate cli/examples/hello-task --check-patch
```
**Output**
```
• baseline:
    ✓ pass2pass: 2 passed  (want: all pass)
    ✓ fail2pass: 2 failed  (want: all fail)
• after golden patch:
    ✓ pass2pass: 2 passed  (want: all pass)
    ✗ fail2pass: 2 failed  (want: now pass)
        offending: test_factorial_five, test_factorial_six
❌ baseline guardrail holds; patch does NOT flip fail2pass. command #N
exit 7
```
**Behavior**
The baseline is fine, but the golden `patch.diff` fails to make fail2pass pass — the solution is incomplete.

### 5. No tests in the buckets

**Input**
```bash
task validate my-empty-task
```
**Output**
```
error: no tests found under my-empty-task/tests/pass2pass or my-empty-task/tests/fail2pass
exit 8
```
**Behavior**
Both buckets are empty, so there's nothing to assert — it stops before touching Docker.

### 6. `--check-patch` with an empty `patch.diff`

**Input**
```bash
task validate my-task --check-patch
```
**Output**
```
error: --check-patch needs a non-empty my-task/patch.diff
exit 8
```
**Behavior**
You asked to verify the patch, but there's no patch to apply.

### 7. Stack not auto-detected

**Input**
```bash
task validate my-remote-task        # remote repo, no Dockerfile built yet
```
**Output**
```
• loaded my-remote-task/task.json  (id=my-remote-task)
error: stack not auto-detected — edit my-remote-task/Dockerfile, then run `task init` to build it
exit 6
```
**Behavior**
The environment is only a generic starter; `validate` won't run against an unbuilt guess — finish `init` first.

### 8. No container runtime

**Input**
```bash
task validate cli/examples/hello-task        # Docker not running
```
**Output**
```
• loaded cli/examples/hello-task/task.json  (id=hello-task)
error: docker is installed but its engine isn't reachable — start it
       (e.g. Docker Desktop / `colima start`), then retry
exit 3
```
**Behavior**
Config and environment resolve, but there's no usable engine to run the buckets in.

### 9. Image not built yet

**Input**
```bash
task validate cli/examples/hello-task        # `init` not run beforehand
```
**Output**
```
• loaded cli/examples/hello-task/task.json  (id=hello-task)
building image taskbundle-hello-task:v1 (docker); first build can take a while
• baseline image taskbundle-hello-task:v1  (runtime: docker, network: none)
• baseline:
    ✓ pass2pass: 2 passed  (want: all pass)
    ✓ fail2pass: 2 failed  (want: all fail)
✅ baseline guardrail holds. command #N
exit 0
```
**Behavior**
No prior image, so `validate` builds it first (reusing the Dockerfile), then runs the guardrail. Use `--rebuild` to force a fresh image when a local repo changed.
