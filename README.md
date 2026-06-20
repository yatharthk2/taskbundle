# Taskbundle

[![PyPI version](https://img.shields.io/pypi/v/taskbundle)](https://pypi.org/project/taskbundle/)
[![Python versions](https://img.shields.io/pypi/pyversions/taskbundle)](https://pypi.org/project/taskbundle/)

Author, validate, and run **SWE-bench-style coding tasks** in reproducible containers.

A task is a **bundle**: a directory with a repo at a commit, a problem statement, a golden patch, and hidden `pass2pass` / `fail2pass` tests. Taskbundle packages it into a Docker image and runs those hidden tests **before and after** a solver (an LLM agent, a script, anything that edits files), so you know exactly which tests it fixed and which it broke.

**The loop:** `init` (build the image) → `validate` (assert the baseline guardrail) → `run` (solve in isolation, then score) → `query` (inspect any past run).

Design rationale & tradeoffs: [DESIGN.md](DESIGN.md).

## How it works

```
   ┌────────────────────────────────────────┐
   │                 BUNDLE                 │
   │     repo · problem · patch · tests     │
   └────────────────────────────────────────┘
                       │  init
                       ▼
   ┌────────────────────────────────────────┐
   │              Docker image              │
   └────────────────────────────────────────┘
                       │  run
                       ▼
   ┌────────────────────────────────────────┐
   │              1) SOLVE box              │
   │          sees repo + problem           │
   │      NEVER sees the hidden tests       │
   └────────────────────────────────────────┘
                       │  captured patch (git diff)
                       ▼
   ┌────────────────────────────────────────┐        ┌──────────────┐
   │              2) SCORE box              │        │ hidden tests │
   │         apply patch, run tests         │ ◀───── │  p2p / f2p   │
   │          hermetic, no network          │  only  └──────────────┘
   └────────────────────────────────────────┘  here
                       │
                       ▼
                   resolved?
```

Two **isolated** containers: **SOLVE** edits the repo but never sees the hidden tests; they're injected only into the separate, hermetic **SCORE** box that grades the captured patch, so a solver can't peek at or game them.

## Install

Python ≥ 3.9. Built with [Typer](https://typer.tiangolo.com/); everything else is stdlib.

```bash
pip install taskbundle      # or: uv pip install taskbundle
task --help
```

Docker (or Podman / colima / nerdctl) is needed to build & run images. `task query` and `task init --no-build` work without it.

The example bundles in the quickstart below ship in this repo (not the wheel), so clone the repo to follow along. To hack on taskbundle itself, install from the checkout instead: `pip install -e ./cli` (or `uv pip install ./cli`).

## Quickstart

The whole loop on a tiny, self-contained bundle ([`hello-task`](cli/examples/hello-task/), no external repo, ~1 min):

```bash
task init     cli/examples/hello-task                 # build the image + smoke-check
task validate cli/examples/hello-task --check-patch   # baseline: p2p pass / f2p fail; golden flips f2p
task run      cli/examples/hello-task                 # golden solver → RESOLVED (exit 0)
task run      cli/examples/hello-task --solver noop   # makes no edits → not resolved (exit 9)
task query                                            # the ledger, every run newest first
task query 1                                          # full detail for command #1
```

## Commands

| Command | What it does | Exit |
|---|---|---|
| **`init`** | Resolve a Dockerfile + build the image (clone/copy the repo at the commit) + smoke-check | `0` ok · `3/4/5/6/11` setup |
| **`validate`** | Run the hidden buckets on the baseline → assert **p2p pass, f2p fail** | `0` holds · `7` violated · `8` no tests |
| **`run`** | Run a solver (it never sees the buckets), capture its diff, score it in a hermetic box | `0` resolved · `9` not · `8/12` setup |
| **`query`** | Read-only ledger inspection: by id, filtered list, or `--stats` scoreboard | `0` · `10` unknown id |

Any *unexpected* error is caught, logged to the ledger, and shown as one line with exit `70` (`TASKBUNDLE_DEBUG=1` for the traceback). Step-by-step docs: [init](docs/task-init.md) · [validate](docs/task-validate.md) · [run](docs/task-run.md) · [query](docs/task-query.md).

### `init` — package the repo

Builds the image and smoke-checks that deps resolve and the test runner works. The Dockerfile is resolved by **strict precedence** (never merged):

| Tier | When | Dockerfile |
|---|---|---|
| **existing** | one is already in the bundle | used verbatim (`--regenerate` to replace) |
| **override** | `task.json` has `install_cmd` / `build_cmd` | generated from those |
| **auto-detect** | neither | detected stack (Python today) with sane defaults; unknown stack → editable starter, never a wrong guess |

```bash
task init <bundle>                              # build an in-bundle repo (stack auto-detected)
task init <bundle> --repo <url> --commit <sha>  # scaffold from a remote repo (cloned at the commit)
task init <bundle> --no-build                   # scaffold + write the Dockerfile only, no Docker
```

Logged to `.taskbundle/db.sqlite` (override with `--db`).

### `validate` — baseline guardrail

Runs the hidden buckets **read-only** and **hermetic** (`--network none`), asserting the SWE-bench invariant: every `pass2pass` passes and every `fail2pass` fails on the untouched baseline. `--check-patch` also applies the golden patch and confirms `fail2pass` flips while `pass2pass` stays green. Results are parsed from JUnit XML, so a real failure is told apart from an import error.

```bash
task validate <bundle>                # guardrail: exit 0 holds, 7 violated (offenders named)
task validate <bundle> --check-patch  # also confirm the golden patch flips fail2pass
```

`--rebuild` / `--no-cache` force a fresh image (only needed for a local repo edited under a fixed commit label); `--timeout` caps each bucket.

### `run` — solve & score

Two **isolated** containers:

1. **Solve**: the solver runs with the repo + `description.md` (`$TASKBUNDLE_PROBLEM`) but **never the hidden buckets**; its edits are captured via `git diff`. Network is on by default (`--solver-network none` to forbid).
2. **Score**: the captured patch is applied to a clean baseline in a hermetic box, and the buckets run. **Resolved** = every `fail2pass` passes, no `pass2pass` regression.

```bash
task run <bundle>                       # golden (apply patch.diff, a self-test)
task run <bundle> --solver noop         # no edits, the lower bound
task run <bundle> --solver 'sed -i "s/range(1, n)/range(1, n + 1)/" mathx/core.py'   # any file-editing command
```

- **Outcome** (in the report): `resolved` · `unresolved` · `patch_failed` · `no_edits` · `solver_error` · `solver_timeout`.
- **Containment**: a fork-bomb `--pids-limit` is always on; opt-in `--memory 2g` / `--cpus 1.5` cap an untrusted solver's host RAM/CPU.
- Each run writes a JSON report (`--report`) **and** an artifacts dir (`--artifacts`, default `.taskbundle/artifacts/<id>/`): the captured `solver.patch`, per-stage logs, and the report, so a command id resolves to everything you need to debug it. `query <id>` prints the path.

### `query` — inspect the ledger

Read-only: needs no runtime and **writes no row for itself**.

```bash
task query                          # last 10 invocations, newest first (--limit N)
task query 7                        # type-aware detail for #7 (tier/digest · guardrail · outcome…)
task query 7 --json                 # raw record, for scripting
task query --command run --outcome solver_error   # filter: --command / --task / --status / --outcome
task query --task hello-task --stats              # scoreboard: runs resolved/total per task
```

`--stats` aggregates the (optionally filtered) ledger (totals by command, run outcomes, and per-task
resolved/total), turning the log into a lightweight benchmark dashboard.

## Use an LLM as the solver

`--solver` is just a command that edits files, so an LLM agent is one too. The repo ships a **stdlib-only** OpenAI agent ([`openai_agent.py`](cli/solvers/openai_agent.py)); the `openai-demo` bundle vendors it and its Dockerfile `COPY`s it to `/opt`, so you run it as an ordinary solver command:

```bash
export OPENAI_API_KEY=sk-...           # in your shell, never an arg, never committed
task run cli/examples/openai-demo \
  --solver 'python /opt/openai_agent.py' \
  --solver-env OPENAI_API_KEY           # forward the key BY NAME (-e NAME)
```

- The key is forwarded **by name**: its value never hits the argv, a log, or the ledger (only the name is recorded).
- **Isolation is unchanged**: it runs in the same solve box and never sees the hidden buckets.
- **Model**: `gpt-4o-mini` by default; `OPENAI_MODEL` / `OPENAI_BASE_URL` override (forward them the same way, e.g. `--solver-env OPENAI_MODEL`).
- **Your own bundle**: vendor `cli/solvers/openai_agent.py` in and `COPY openai_agent.py /opt/openai_agent.py` in its Dockerfile, exactly what `openai-demo` does.

Under the hood it reads the problem + repo, asks the model for the updated file contents, and writes them back, captured and scored like any other solver.

> **Scope:** the bundled agent is a *minimal demonstration* of the solver interface: it sends ~120 KB of repo context in one shot, so it suits small bundles. On a large repo (e.g. the ansible instance) the file needing the fix can fall outside that window, so it won't resolve it. This is out of scope for now, but I was just curious to see and implement - maybe something for the future :) 
## A real SWE-bench Pro example

[`ansible-combine-vars`](examples/ansible-combine-vars/) is a real [SWE-bench Pro](https://huggingface.co/datasets/ScaleAI/SWE-bench_Pro) instance (`ansible/ansible` at a pinned commit) exercising the **remote-clone** path end-to-end with the instance's real `combine_vars` tests (1 fail2pass, 15 pass2pass):

```bash
task init     examples/ansible-combine-vars                # clone at the commit + build
task validate examples/ansible-combine-vars --check-patch  # baseline holds; golden flips fail2pass
task run      examples/ansible-combine-vars                 # golden → RESOLVED
```

See its [README](examples/ansible-combine-vars/README.md) for provenance.

## Bundle layout

```
my-task/
  task.json       # which code: repo, commit, id  (+ optional install/build/test/smoke overrides)
  description.md  # the problem statement shown to the solver
  patch.diff      # golden solution (unified diff)
  Dockerfile      # OPTIONAL — owns the environment; auto-generated if absent
  tests/
    pass2pass/    # must pass before AND after the golden patch
    fail2pass/    # must fail on baseline, pass after the golden patch
```

`task.json` says *which code*; the **Dockerfile owns the environment** (base image included). Never split across both. Its optional `test_cmd` sets how the buckets run — any framework that writes JUnit to `$TASKBUNDLE_JUNIT` (tests at `$TASKBUNDLE_BUCKET`), default pytest — so non-Python tasks run too.

## Tests & troubleshooting

```bash
python3 -m unittest discover -s cli/tests -t cli            # stdlib unittest (no test dep)
uv pip install './cli[test]' && python3 -m pytest cli/tests # optional, under pytest (quote for zsh)
```

No container runtime? `init --no-build` and `query` still work; pick one with `--runtime` / `TASKBUNDLE_RUNTIME`.

## References

The task structure and evaluation methodology (hidden `fail2pass` / `pass2pass` tests, golden patches,
containerized per-instance environments) take inspiration from:

- Jimenez et al., **"SWE-bench: Can Language Models Resolve Real-World GitHub Issues?"** ICLR 2024 —
  [arXiv:2310.06770](https://arxiv.org/abs/2310.06770). The original benchmark defining the
  repo-at-a-commit + problem statement + FAIL_TO_PASS / PASS_TO_PASS task format.
- Deng et al. (Scale AI), **"SWE-Bench Pro: Can AI Agents Solve Long-Horizon Software Engineering
  Tasks?"** — [arXiv:2509.16941](https://arxiv.org/abs/2509.16941). The harder variant with
  human-verified, Docker-based per-instance environments; the bundled
  [`ansible-combine-vars`](examples/ansible-combine-vars/) example is an instance from its
  [public dataset](https://huggingface.co/datasets/ScaleAI/SWE-bench_Pro).
