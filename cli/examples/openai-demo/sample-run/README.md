# Captured proof — a real OpenAI-solver run

Verbatim artifact from a real `task run` of this bundle with the OpenAI solver
([`cli/solvers/openai_agent.py`](../../../solvers/openai_agent.py), model `gpt-4o-mini`,
temperature 0). The model read the problem + repo, fixed `calc/core.py`, and the hidden
fail2pass tests flipped to passing — `outcome: resolved`.

- **`solver.patch`** — the model's actual edit, captured by `git diff`: `return a - b` → `return a + b`.
- **`solver.log`** — the agent's output (model + the one file it wrote). No API key appears.
- **`run-report.json`** — the structured result: `outcome: resolved`, both fail2pass tests passing,
  and `solver_env: ["OPENAI_API_KEY"]` — the env-var **name** only, never the value.

Reproduce with your own key (`export OPENAI_API_KEY=...`) via the command in [../README.md](../README.md).
