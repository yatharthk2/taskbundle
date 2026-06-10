# openai-demo — a real LLM solver, end-to-end

A tiny Python bundle (one buggy function: `calc.core.add` returns `a - b` instead of `a + b`)
whose entire repo fits an LLM context window. It exists to demonstrate `task run` with a **real
OpenAI solver** — the spec's headline verb, "ask an LLM."

The solver is [`cli/solvers/openai_agent.py`](../../solvers/openai_agent.py) (stdlib only, no
`openai` SDK), vendored here as `openai_agent.py` and COPYed into the image by the `Dockerfile`
(tier-1). Inside the solve box it reads the problem statement + the repo, asks a cheap model for
the full updated file contents, and writes them back; the harness captures the edit via `git diff`.

It runs in the **existing** solve box, so it sees only `description.md` + the repo — never the
hidden `pass2pass` / `fail2pass` buckets.

## Run it

```bash
export OPENAI_API_KEY=sk-...                       # your key — exported, never committed or typed as an arg
task run cli/examples/openai-demo \
  --solver 'python /opt/openai_agent.py' \
  --solver-env OPENAI_API_KEY \                    # forwarded into the box BY NAME (value stays in your env)
  --report /tmp/llm-report.json --artifacts /tmp/llm-artifacts
```

- **Model**: `gpt-4o-mini` by default (cheap); override with `OPENAI_MODEL` (also `--solver-env OPENAI_MODEL`).
- **Endpoint**: `OPENAI_BASE_URL` overrides `https://api.openai.com/v1` (Azure / a proxy / a local mock).
- **Secret hygiene**: the key flows only via the `OPENAI_API_KEY` env var, read in the container at
  runtime. It is never baked into a layer, written to a file/log, or stored in the ledger — only the
  env var *name* (`["OPENAI_API_KEY"]`) is recorded.
- **Baseline first**: `task validate cli/examples/openai-demo --check-patch` confirms the guardrail
  (pass2pass green, fail2pass red) and that the golden patch flips fail2pass.

The run writes `solver.patch` + `solver.log` + `run-report.json` under `--artifacts` (default
`.taskbundle/artifacts/<id>/`). A verbatim capture from a real `gpt-4o-mini` run — `outcome:
resolved` — is committed in [`sample-run/`](sample-run/) as proof.
