# Changelog

## Unreleased

- **LLM solver support**: a stdlib-only OpenAI agent (`cli/solvers/openai_agent.py`) runnable as a solver command, plus `--solver-env NAME` to forward a host env var (e.g. an API key) into the box by name; isolation and secret hygiene preserved. Demo bundle: `cli/examples/openai-demo/`.
- **Audited errors**: unexpected exceptions are caught, logged to the ledger, and shown as a clean one-line message with exit `70` (set `TASKBUNDLE_DEBUG=1` for the traceback) instead of a bare crash.
- **Per-run artifacts**: `run` writes `.taskbundle/artifacts/<id>/` with the captured patch, per-stage logs, and a report copy, so a command id resolves to everything needed to debug it.
- **CLI on Typer**: replaced the hand-rolled argparse parser with a Typer app (typed commands, generated `--help`, consistent usage errors); the exit-code contract is unchanged.
- **`task query`**: read-only ledger inspection by id or recent list, with filters (command / task / status / outcome), a `--stats` scoreboard, and `--json`; never writes a row of its own.
- **`task run`**: run a solver in isolation (it never sees the hidden buckets), capture its diff, and score it in a hermetic box; structured JSON report with an `outcome` label.
- **`task validate`**: assert the baseline guardrail (pass2pass pass, fail2pass fail), and with `--check-patch` that the golden patch flips fail2pass; grades from JUnit XML.
- **`task init`**: scaffold a bundle, resolve one Dockerfile (existing > override > auto-detect), build and smoke-check it, and log the run.
