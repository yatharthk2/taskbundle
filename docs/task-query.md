# `task query`

`task query` is **read-only inspection of the SQLite ledger**. It looks up one command by id, or
lists the most recent invocations. It needs **no container runtime**, and it deliberately **writes
no row for itself** — reads must never pollute the audit log (it won't even create the db file if
it's missing).

```bash
task query [<id>] [--json] [--db PATH]                              # look up an id, or list recent
task query [--command run] [--task <id>] [--status ok|error] [--outcome resolved] [--limit N]
task query --stats [--command/--task/...]                           # aggregate scoreboard
```

- **No id** → list recent invocations (newest first), optionally **filtered**.
- **`<id>`** → full, type-aware detail for that one command.
- **`--stats`** → an aggregate scoreboard instead of a list (respects the filters).
- **`--json`** → the raw stored record(s) / stats instead of the formatted view.

**Filters** (`--command`, `--task`, `--status` are columns; `--outcome` is matched inside the run's
JSON details) and **`--stats`** turn the ledger from a log into a queryable scoreboard:

```bash
task query --command run --outcome solver_error   # every run that crashed
task query --task hello-task --stats              # how are solvers doing on this task?
# ledger: 3 command(s) across 1 task(s)
#   run: 2 · validate: 1
# runs: 2 — resolved 1 · solver_error 1
# per task (runs resolved/total):
#   hello-task  1/2  (50%)
```

---

## How it works

`cmd_query` branches on: whether the ledger exists, whether you passed an id, and whether you asked
for `--stats`.

1. **Ledger missing** (`--db` path doesn't exist) — nothing has been recorded:
   - listing → prints `no commands recorded yet` → **exit 0**.
   - lookup `<id>` → `no command with id <id>` → **exit 10**.
   (It does *not* create the db — staying truly read-only.)
2. **Lookup `<id>`** — fetch the row. Not found → **exit 10**. Found → render it **type-aware**
   (init / validate / run) or, with `--json`, dump the raw record → **exit 0**.
3. **List** (no id) — the `--limit` most recent rows, newest first, optionally filtered by
   `--command` / `--task` / `--status` (SQL `WHERE`) and `--outcome` (matched in the JSON details).
   Empty → `no commands recorded yet` (or `no commands match those filters`). → **exit 0**.
4. **`--stats`** — aggregate the (optionally filtered) ledger into a scoreboard: totals by command,
   run outcomes, and per-task resolved/total. → **exit 0**.

**Read-only by construction:** unlike every other command, `query` never calls `DB.log_command`, so
inspecting the ledger doesn't add to it. It also needs no Docker.

### What a lookup renders, per command type

The detail view (`_render_record`) only prints fields that are present, keyed by command type:

- **init** → tier (+ stack), runtime, image, digest.
- **validate** → guardrail held|violated, per-bucket baseline counts (+ patched counts if `--check-patch`).
- **run** → solver kind/command, outcome (+ `resolved=`), baseline ok, `fail2pass`/`pass2pass`
  counts, the resolved / still-failing / regression test names, and the report + artifacts paths.

---

## Exit codes (quick reference)

| Code | Meaning |
|---|---|
| `0` | a hit, a listing, or an empty/missing ledger (`no commands recorded yet`) |
| `2` | usage error — a malformed argument (e.g. a non-integer id) |
| `10` | a **well-formed but unknown** id (`no command with id N`) |
| `70` | unexpected internal error (reported cleanly; **no** ledger row, since `query` is read-only) |

> `10` is kept distinct from `2` on purpose: a caller can tell **"not found"** (valid id, no such
> row) from **"bad input"** (the id wasn't even a number).

---

## Scenarios

| # | Input | Exit | Result |
|---|---|---|---|
| 1 | `task query` (list) | `0` | table of recent invocations |
| 2 | `task query <id>` (init row) | `0` | init detail |
| 3 | `task query <id>` (validate row) | `0` | validate detail |
| 4 | `task query <id>` (run row) | `0` | run detail |
| 5 | `task query <id> --json` | `0` | raw stored record |
| 6 | `task query <unknown-id>` | `10` | not found |
| 7 | `task query` on an empty/missing ledger | `0` | `no commands recorded yet` |

### 1. List recent invocations

**Input**
```bash
task query
```
**Output**
```
id  when                 command   summary
 3  2026-06-04 02:23:15  run       RESOLVED by golden solver
 2  2026-06-04 02:23:14  validate  baseline guardrail holds
 1  2026-06-04 02:23:13  init      scaffolded hello-task (tier: existing, no build)
```
**Behavior**
A compact `id │ when │ command │ summary` table, newest first — the quick scan of what's happened.

### 2. Look up an `init` command

**Input**
```bash
task query 1
```
**Output**
```
command #1  [init]  ok
  when:    2026-06-04T02:23:13+00:00
  task:    hello-task
  summary: scaffolded hello-task (tier: existing, no build)
  tier:    existing  (stack: python)
```
**Behavior**
Renders the init-specific fields (tier/stack, and runtime/image/digest when it actually built).

### 3. Look up a `validate` command

**Input**
```bash
task query 2
```
**Output**
```
command #2  [validate]  ok
  when:    2026-06-04T02:23:14+00:00
  task:    hello-task
  summary: baseline guardrail holds
  guardrail: held
    baseline pass2pass: 2 passed
    baseline fail2pass: 2 failed
```
**Behavior**
Shows whether the guardrail held and the per-bucket baseline counts (plus patched counts if it was a `--check-patch` run).

### 4. Look up a `run` command

**Input**
```bash
task query 3
```
**Output**
```
command #3  [run]  ok
  when:    2026-06-04T02:23:15+00:00
  task:    hello-task
  summary: RESOLVED by golden solver
  solver:  golden  (apply patch.diff)
  outcome: resolved  (resolved=True)
  baseline: ok
    fail2pass: 2 passed
    pass2pass: 2 passed
    resolved: test_factorial_five, test_factorial_six
  report:  /tmp/qr.json
  artifacts: /tmp/qart/3
```
**Behavior**
The full run verdict: solver, outcome, which tests resolved/failed/regressed, and where the report + artifacts live — so you can debug a run from its id alone.

### 5. Raw JSON record (`--json`)

**Input**
```bash
task query 3 --json
```
**Output**
```json
{
  "id": 3,
  "ts": "2026-06-04T02:23:15+00:00",
  "command": "run",
  "task_id": "hello-task",
  "status": "ok",
  "summary": "RESOLVED by golden solver",
  "details": { "solver": { "kind": "golden", ... }, "outcome": "resolved", ... }
}
```
**Behavior**
The stored record verbatim (with `details` decoded back to an object) — for scripting / piping into `jq`. Works for the list too (`task query --json`).

### 6. Unknown id

**Input**
```bash
task query 99
```
**Output**
```
no command with id 99
exit 10
```
**Behavior**
The id is a valid number but no such row exists → exit `10` (distinct from a malformed-argument `2`).

### 7. Empty or missing ledger

**Input**
```bash
task query                       # nothing recorded yet (or a fresh --db path)
```
**Output**
```
no commands recorded yet
exit 0
```
**Behavior**
No ledger (or an empty one) is a clean, expected state — not an error. The db file isn't created just to read it.
