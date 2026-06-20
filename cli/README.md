# Taskbundle

Author, validate, and run **SWE-bench-style coding tasks** in reproducible containers.

A task is a **bundle**: a directory with a repo at a commit, a problem statement, a golden patch, and hidden `pass2pass` / `fail2pass` tests. Taskbundle packages it into a Docker image and runs those hidden tests **before and after** a solver (an LLM agent, a script, anything that edits files), so you know exactly which tests it fixed and which it broke.

**The loop:** `init` (build the image) → `validate` (assert the baseline guardrail) → `run` (solve in isolation, then score) → `query` (inspect any past run).

> Full documentation, design rationale, and examples live in the GitHub repo:
> **https://github.com/yatharthk2/taskbundle**

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
pip install taskbundle
task --help
```

Docker (or Podman / colima / nerdctl) is needed to build & run images. `task query` and `task init --no-build` work without it.

## Quickstart

The whole loop on a tiny, self-contained bundle ([`hello-task`](https://github.com/yatharthk2/taskbundle/tree/main/cli/examples/hello-task), no external repo, ~1 min):

```bash
task init     cli/examples/hello-task                 # build the image + smoke-check
task validate cli/examples/hello-task --check-patch   # baseline: p2p pass / f2p fail; golden flips f2p
task run      cli/examples/hello-task                 # golden solver → RESOLVED (exit 0)
task run      cli/examples/hello-task --solver noop   # makes no edits → not resolved (exit 9)
task query                                            # the ledger, every run newest first
task query 1                                          # full detail for command #1
```

(Clone the [repo](https://github.com/yatharthk2/taskbundle) to get the example bundles.)

## Commands

| Command | What it does | Exit |
|---|---|---|
| **`init`** | Resolve a Dockerfile + build the image (clone/copy the repo at the commit) + smoke-check | `0` ok · `3/4/5/6/11` setup |
| **`validate`** | Run the hidden buckets on the baseline → assert **p2p pass, f2p fail** | `0` holds · `7` violated · `8` no tests |
| **`run`** | Run a solver (it never sees the buckets), capture its diff, score it in a hermetic box | `0` resolved · `9` not · `8/12` setup |
| **`query`** | Read-only ledger inspection: by id, filtered list, or `--stats` scoreboard | `0` · `10` unknown id |

Any *unexpected* error is caught, logged to the ledger, and shown as one line with exit `70` (`TASKBUNDLE_DEBUG=1` for the traceback). Step-by-step docs:
[init](https://github.com/yatharthk2/taskbundle/blob/main/docs/task-init.md) ·
[validate](https://github.com/yatharthk2/taskbundle/blob/main/docs/task-validate.md) ·
[run](https://github.com/yatharthk2/taskbundle/blob/main/docs/task-run.md) ·
[query](https://github.com/yatharthk2/taskbundle/blob/main/docs/task-query.md).

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

`task.json` says *which code*; the **Dockerfile owns the environment** (base image included). Its optional `test_cmd` sets how the buckets run — any framework that writes JUnit to `$TASKBUNDLE_JUNIT` (tests at `$TASKBUNDLE_BUCKET`), default pytest — so non-Python tasks run too.

## Use an LLM as the solver

`--solver` is just a command that edits files, so an LLM agent is one too. The repo ships a **stdlib-only** OpenAI agent ([`openai_agent.py`](https://github.com/yatharthk2/taskbundle/blob/main/cli/solvers/openai_agent.py)); the `openai-demo` bundle vendors it and `COPY`s it into the image, so you run it as an ordinary solver command:

```bash
export OPENAI_API_KEY=sk-...           # in your shell, never an arg, never committed
task run cli/examples/openai-demo \
  --solver 'python /opt/openai_agent.py' \
  --solver-env OPENAI_API_KEY           # forward the key BY NAME (-e NAME)
```

The key is forwarded **by name**: its value never hits the argv, a log, or the ledger. Isolation is unchanged — it runs in the same solve box and never sees the hidden buckets.

## References

The task structure and evaluation methodology (hidden `fail2pass` / `pass2pass` tests, golden patches, containerized per-instance environments) take inspiration from:

- Jimenez et al., **"SWE-bench: Can Language Models Resolve Real-World GitHub Issues?"** ICLR 2024 — [arXiv:2310.06770](https://arxiv.org/abs/2310.06770).
- Deng et al. (Scale AI), **"SWE-Bench Pro: Can AI Agents Solve Long-Horizon Software Engineering Tasks?"** — [arXiv:2509.16941](https://arxiv.org/abs/2509.16941).

## License

[MIT](https://github.com/yatharthk2/taskbundle/blob/main/LICENSE) © Yatharth Kapadia
