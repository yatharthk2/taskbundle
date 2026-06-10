# `task init`

`task init <bundle>` turns a task bundle into a built, verified container image — and
records the run in the ledger. It either **loads** an existing `task.json` or **scaffolds**
a new bundle from `--repo` / `--commit`, then resolves one Dockerfile (by strict tier
precedence), builds the image, and smoke-checks it.

```bash
task init <bundle> [--repo URL|PATH] [--commit SHA] [--id NAME]
                   [--install-cmd ...] [--build-cmd ...] [--test-cmd ...] [--smoke-cmd ...]
                   [--no-build] [--regenerate] [--no-cache] [--runtime docker] [--db PATH]
```

---

## The six steps

`init` runs these in order (`cmd_init` in `cli/task.py`). Each step can short-circuit with
its own exit code.

### 1. Config — load or scaffold
- If `task.json` exists → **load** it (any `--repo`/`--commit` are ignored, with a note).
- If not → **scaffold** a new bundle. Requires `--repo` **and** `--commit`, else:
  - ❌ `error: no task.json found — pass --repo and --commit` → **exit 2**.
- Scaffolding writes the skeleton: `task.json`, a placeholder `description.md`, an empty
  `patch.diff`, and empty `tests/pass2pass/` + `tests/fail2pass/` dirs.

### 2. Resolve the build environment (one Dockerfile, three tiers)
`resolve_build_env` picks **one** Dockerfile by strict precedence (highest wins, never merged):
1. **existing** — a Dockerfile already in the bundle → used verbatim (unless `--regenerate`).
2. **override** — `task.json` has `install_cmd`/`build_cmd` → generate one from those.
3. **auto-detect** — recognize the stack and generate with defaults; if the stack is unknown,
   write a generic **starter** and flag `needs_edit`.
- Prints: `build env → tier: …, stack: …, Dockerfile: …`.

### 3. Scaffold-only path (`--no-build`)
- If `--no-build` was passed → log an `ok` row, print `✓ bundle scaffolded (no build)`,
  and **exit 0**. No runtime needed. (This is where authoring without Docker stops.)
- **Needs-edit guard** (only reached when *not* `--no-build`): if the Dockerfile is a generic
  starter (`needs_edit`), `init` refuses to build a guess:
  - ❌ `couldn't auto-detect the stack. Edit … Dockerfile, then re-run` → **exit 6**.

### 4. Resolve a container runtime
- Auto-detect docker/podman/nerdctl (or honor `--runtime` / `TASKBUNDLE_RUNTIME`).
- If none is installed/reachable:
  - ❌ `error: <reason>` (with a `--no-build` hint) → **exit 3**.

### 5. Build the image
- `init` **always builds** (`force=True`), relying on the Docker layer cache for speed.
- Tag is derived deterministically: `taskbundle-<id>:<commit[:12]>`.
- If the build fails:
  - ❌ `image build failed for <tag>` (build log tail printed + logged) → **exit 4**.

### 6. Smoke check — is the built image actually runnable?
- Runs `smoke_cmd` inside a throwaway container at `/workspace/repo`
  (Python default: `python -m pytest --collect-only -q` — confirms deps resolve and tests
  can be collected, without running them).
- No `smoke_cmd` to verify with → ❌ **exit 11** (built, but nothing to verify it; asks you to set `"smoke_cmd"`).
- Smoke command runs and fails → ❌ **exit 5** (output tail printed + logged).
- Smoke passes → ✅ `bundle ready, env reproducible — <tag> (<digest>)` → **exit 0**.

Every outcome (success or failure) is written to the SQLite ledger and tagged with a
`command #<id>` you can inspect via `task query <id>`.

---

## Exit codes (quick reference)

| Code | Meaning |
|---|---|
| `0` | success (built + smoke-checked, or scaffolded with `--no-build`) |
| `2` | usage / bundle error (e.g. no `task.json` and no `--repo`/`--commit`) |
| `3` | no container runtime available |
| `4` | image build failed |
| `5` | the smoke check ran and **failed** (env not reproducible) |
| `6` | stack not auto-detected — starter Dockerfile written, edit it |
| `11` | built, but **no `smoke_cmd`** to verify it with (set one in `task.json`) |
| `70` | unexpected internal error (audited; `TASKBUNDLE_DEBUG=1` for the traceback) |

---

## Scenarios

How different inputs flow through the steps above.

| # | Input | Tier | Builds? | Exit | Outcome |
|---|---|---|---|---|---|
| 1 | Existing `task.json`, runtime up | (resolved) | yes | `0` | image built + verified |
| 2 | No `task.json`, no `--repo`/`--commit` | — | no | `2` | usage error |
| 3 | Scaffold, **local** repo (has `pyproject.toml`) | auto-detect (python) | yes | `0` | working Dockerfile, built |
| 4 | Scaffold, **remote URL**, no overrides | auto-detect (generic) | no | `6` | starter written, must edit |
| 5 | Scenario 4 **+ `--no-build`** | auto-detect (generic) | no | `0` | scaffold + starter, no build |
| 6 | Scaffold + `--install-cmd`/`--build-cmd` | override | yes* | `0`/`4` | Dockerfile generated from cmds |
| 7 | Bundle already has a `Dockerfile` | existing | yes | `0` | Dockerfile used verbatim |
| 8 | Existing Dockerfile **+ `--regenerate`** | override/auto-detect | yes | `0` | Dockerfile replaced |
| 9 | Scaffold, **external local path** repo | auto-detect | yes | `0` | repo copied into `<bundle>/repo` |
| 10 | Any build path, **no Docker running** | (resolved) | no | `3` | no runtime |

\* it attempts to build; if the generated base image lacks your tooling (e.g. `npm` on the
generic base), the build fails with **exit 4** — see Scenario 6.

### 1. Existing bundle (the common case)

**Input**
```bash
task init examples/hello-task
```
**Output**
```
• loaded examples/hello-task/task.json
• build env → tier: existing, stack: python, Dockerfile: examples/hello-task/Dockerfile
• using container runtime: docker
• smoke check: python -m pytest --collect-only -q
✅ bundle ready, env reproducible — taskbundle-hello-task:v1 (sha256:ad33ccbf2825…). command #1
exit 0
```
**Behavior**
Loaded the existing config + Dockerfile, built the image, and smoke-checked that the env runs.

### 2. No `task.json`, nothing to scaffold from

**Input**
```bash
task init my-task
```
**Output**
```
error: no task.json found — pass --repo and --commit to scaffold a new bundle
exit 2
```
**Behavior**
No config and no `--repo`/`--commit` to scaffold from, so it errors before touching anything.

### 3. Scaffold from a local repo (auto-detect works)

**Input**
```bash
task init my-task --repo ./checkout --commit v1
```
**Output**
```
• scaffolded bundle at my-task/  (id=my-task)
• build env → tier: auto-detect, stack: python, Dockerfile: my-task/Dockerfile
• smoke check: python -m pytest --collect-only -q
✅ bundle ready, env reproducible — taskbundle-my-task:v1 (sha256:…). command #N
exit 0
```
**Behavior**
The local files revealed Python, so it generated a buildable Dockerfile and built + verified it.

### 4. Scaffold from a remote URL (the remote-clone gap)

**Input**
```bash
task init my-task --repo https://github.com/owner/repo --commit <sha>
```
**Output**
```
• scaffolded bundle at my-task/  (id=my-task)
• build env → tier: auto-detect, stack: generic, Dockerfile: my-task/Dockerfile
• couldn't auto-detect the stack — wrote a starter Dockerfile at my-task/Dockerfile, edit it
error: couldn't auto-detect the stack. Edit my-task/Dockerfile (FROM + install steps), then re-run. command #N
exit 6
```
**Behavior**
A remote repo isn't on disk yet, so the stack reads generic; it writes a starter and refuses to build a guess.

### 5. Same remote URL, scaffold only

**Input**
```bash
task init my-task --repo https://github.com/owner/repo --commit <sha> --no-build
```
**Output**
```
• scaffolded bundle at my-task/  (id=my-task)
• build env → tier: auto-detect, stack: generic, Dockerfile: my-task/Dockerfile
• couldn't auto-detect the stack — wrote a starter Dockerfile at my-task/Dockerfile, edit it
✓ bundle scaffolded (no build). command #N
exit 0
```
**Behavior**
Stops after scaffolding + writing the starter Dockerfile; no Docker required.

### 6. Provide install / build commands (tier 2)

**Input**
```bash
task init my-task --repo ./checkout --commit v1 \
  --install-cmd "npm install" --build-cmd "npm run build"
```
**Output**
```
• scaffolded bundle at my-task/  (id=my-task)
• build env → tier: override, stack: generic, Dockerfile: my-task/Dockerfile
• using container runtime: docker
error: image build failed for taskbundle-my-task:v1
exit 4        # npm isn't on the generic base; exit 0 once you fix FROM
```
**Behavior**
Your commands become `RUN` lines, but the generic base lacks `npm`, so the build fails — fix `FROM` (or use tier 1). (`--test-cmd` is never written into the Dockerfile.)

### 7. Bring your own Dockerfile (tier 1)

**Input**
```bash
task init my-task        # bundle already contains a Dockerfile
```
**Output**
```
• loaded my-task/task.json
• build env → tier: existing, stack: …, Dockerfile: my-task/Dockerfile
• smoke check: …
✅ bundle ready, env reproducible — … command #N
exit 0
```
**Behavior**
Used your Dockerfile verbatim (any `install_cmd`/`build_cmd` are ignored), then built + smoke-checked.

### 8. Force a regenerate

**Input**
```bash
task init my-task --regenerate
```
**Output**
```
• loaded my-task/task.json
• build env → tier: override, stack: …, Dockerfile: my-task/Dockerfile   (regenerated)
✅ bundle ready, env reproducible — … command #N
exit 0
```
**Behavior**
Skips the existing-Dockerfile tier and regenerates it from your overrides / auto-detect, then builds.

### 9. External local path is materialized in

**Input**
```bash
task init my-task --repo ~/code/foo --commit v1
```
**Output**
```
• scaffolded bundle at my-task/  (id=my-task)
• build env → tier: auto-detect, stack: python, Dockerfile: my-task/Dockerfile
✅ bundle ready, env reproducible — … command #N
exit 0
```
**Behavior**
Copied the external repo into `my-task/repo` (git → clone+pin then drop `.git`; else plain copy), then auto-detected + built.

### 10. No container runtime

**Input**
```bash
task init examples/hello-task        # Docker not running
```
**Output**
```
• loaded examples/hello-task/task.json
• build env → tier: existing, stack: python, Dockerfile: …
error: docker is installed but its engine isn't reachable — start it
       (e.g. Docker Desktop / `colima start`), then retry
exit 3
```
**Behavior**
Resolves the Dockerfile fine, but finds no usable container engine, so it stops before building.
