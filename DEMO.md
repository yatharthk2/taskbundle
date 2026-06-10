# Taskbundle CLI — Demo Cheat Sheet

A task is a **bundle** (repo + commit + problem + golden patch + hidden tests). The CLI builds it
into a reproducible Docker image and runs the hidden tests before/after a solver.

**Loop:** `init → validate → run → query`

```bash
cd cli
```

---

## 1. init — build the image (Tier 1: bundle ships a Dockerfile)

```bash
task init examples/hello-task
```
→ `tier: existing` · builds the image · smoke-checks it · logs `command #N`

## 2. query — show it's logged

```bash
task query 4        # detail of that init (tier, image, digest)
task query          # recent list
```
→ read-only ledger; every command is queryable by id

## 3. validate — baseline guardrail

```bash
task validate examples/hello-task --check-patch
```
→ baseline: pass2pass pass, fail2pass fail · golden patch flips fail2pass

## 4. run — solve + score

```bash
task run examples/hello-task              # golden solver → ✅ RESOLVED
task run examples/hello-task --solver noop   # no edits → not resolved
```
→ solver runs isolated (never sees hidden tests); scored in a hermetic box

## 5. query --stats — the scoreboard

```bash
task query --stats
```
→ totals by command, run outcomes, per-task resolved/total

---

## The 3 build tiers (how the Dockerfile is resolved)

```bash
task init examples/hello-task            # tier: existing  (Dockerfile present)
rm examples/hello-task/Dockerfile
task init examples/hello-task            # tier: auto-detect  (pyproject.toml → python)
# add "install_cmd" to task.json, delete Dockerfile:
task init examples/hello-task            # tier: override
```

Reset: `git checkout -- examples/hello-task && docker rmi -f $(docker images -q 'taskbundle-*')`
