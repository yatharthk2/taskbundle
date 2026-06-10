# ansible-combine-vars — a real SWE-bench Pro example

A task bundle built from a real **SWE-bench Pro** instance, to exercise the CLI end-to-end
on the **remote-clone** path (a real GitHub repo cloned at a commit) rather than the
self-contained `cli/examples/hello-task` toy.

## Provenance

- Dataset: [`ScaleAI/SWE-bench_Pro`](https://huggingface.co/datasets/ScaleAI/SWE-bench_Pro)
- Instance: `instance_ansible__ansible-0ea40e09…-v30a923fb…`
- Repo / commit: `github.com/ansible/ansible` @ `f7234968d241d7171aadb1e873a67510753f3163`
- The bug ([ansible#81659](https://github.com/ansible/ansible/issues/81659)): `combine_vars(a, b)`
  with a `dict` and a `VarsWithSources` in *replace* mode raised
  `TypeError: unsupported operand type(s) for |: 'dict' and 'VarsWithSources'`. The golden
  `patch.diff` gives `VarsWithSources` `__or__`/`__ror__`/`__ior__`.

## Bundle layout

- `task.json` — remote `repo` + `commit` (+ a `smoke_cmd`; `_source` records the dataset id).
- `description.md` — the instance's problem statement, verbatim.
- `patch.diff` — the instance's golden patch, verbatim.
- `Dockerfile` — **tier 1**, the intended path for a remote repo (see the gap note below).
- `tests/fail2pass/` — `test_combine_vars_replace`, the dataset's single FAIL_TO_PASS.
- `tests/pass2pass/` — all **15** of the instance's PASS_TO_PASS (`test_combine_vars_merge`,
  `test_combine_vars_improper_args`, and the 13 `test_merge_hash_*`), none of which depends on the fix.

## What was adapted (and why)

SWE-bench applies a *test patch* and selects tests by node id; this tool runs whole **bucket
directories** with `pytest`. So the instance's `FAIL_TO_PASS` and `PASS_TO_PASS` methods were
copied out of ansible's `test/units/utils/test_vars.py` (post-test-patch) into standalone files
in `tests/{fail2pass,pass2pass}/`. The **only** change vs upstream is swapping ansible's
`from units.compat import unittest` test-harness shim for the stdlib `unittest` so each file runs
on its own — every assertion and data row (including the `VarsWithSources` case the test patch
adds, and the `defaultdict` cases) is verbatim.

## The remote-clone `detect_stack` gap (confronted here)

Auto-detect reads the bundle's *local* repo files to pick a stack — but a remote-URL repo
isn't cloned until the image build, so it always detects "generic" and `init` would stop. This
bundle therefore ships a **tier-1 Dockerfile** (the documented remote-repo path: tier 1 or
tier 2 overrides). The Dockerfile shallow-fetches just the target commit and `pip install -e .`s
ansible-core, then the buckets run against the editable install.

## Run it

```bash
python3 cli/task.py init     examples/ansible-combine-vars      # clone + build + smoke
python3 cli/task.py validate examples/ansible-combine-vars --check-patch   # baseline + golden flip
python3 cli/task.py run       examples/ansible-combine-vars      # golden solver → RESOLVED
```
