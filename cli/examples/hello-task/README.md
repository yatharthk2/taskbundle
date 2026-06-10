# Getting started: the `hello-task` bundle

The smallest possible task bundle — a self-contained walkthrough of how the CLI packages a
coding task into a reproducible container. No external repo to clone: the code ships right
here. (Building the image still pulls a base image + `pytest`, like any Docker build.)

## Anatomy

```
hello-task/
  task.json          # just id, repo (local), commit label — that's it
  description.md      # the problem statement the solver sees
  patch.diff          # the golden fix (one line)
  repo/               # the project being fixed: a tiny Python package, `mathx`
    mathx/core.py      #   factorial() has an off-by-one bug; gcd() is fine
    pyproject.toml     #   ← lets `init` auto-detect "python" and generate the Dockerfile
    tests/test_gcd.py  #   a VISIBLE test — the solver sees this one
  tests/
    pass2pass/         # hidden: must pass BEFORE and AFTER the patch
    fail2pass/         # hidden: must FAIL on baseline, pass AFTER the patch
```

**No Dockerfile is shipped on purpose** — `init` auto-detects the Python stack (from
`repo/pyproject.toml`) and generates one for you (`COPY repo` + `pip install -e .`). It appears
in the bundle on first run and is never overwritten afterward.

Because the repo lives inside the bundle, `commit` in `task.json` is just a version label —
the files on disk *are* the pinned baseline state.

## The invariant this task encodes

| | pass2pass (`factorial(0)`, `factorial(1)`) | fail2pass (`factorial(5)`, `factorial(6)`) |
|---|---|---|
| **Baseline** (no patch) | ✅ pass | ❌ fail (`factorial(5)` → 24) |
| **After `patch.diff`** | ✅ pass | ✅ pass (`factorial(5)` → 120) |

The solver never sees `tests/pass2pass` or `tests/fail2pass` — only `repo/tests/test_gcd.py`.

## Run it

```bash
# build the reproducible image + smoke-check it (needs a container runtime)
python3 cli/task.py init cli/examples/hello-task

# no Docker handy? just confirm the bundle is well-formed:
python3 cli/task.py init cli/examples/hello-task --no-build
```

(Run from the repo root. After `pip install -e ./cli` you can use `task init …` instead.)

`init` builds an image that COPYs `repo/` in and installs it, then smoke-checks
(`pytest --collect-only`) that dependencies resolve and the runner works. Every invocation is
recorded in the SQLite ledger with a command id.

> **Coming next:** `task validate` asserts the invariant above (baseline vs. golden-patched),
> and `task run` hides the bucket tests, runs a solver, re-injects the tests, and scores the result.
