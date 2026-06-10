#!/usr/bin/env bash
# Reproduce all THREE build-env tiers end-to-end with REAL docker builds.
#
# Same toy repo (mathx) each time — only how the Dockerfile is resolved differs:
#   auto-detect → generated from the detected stack (Python)
#   override    → generated from task.json install_cmd
#   existing    → a hand-written Dockerfile, used verbatim
# Every run does a real `docker build` + an in-container smoke check (not a stub).
#
# Usage:  bash cli/examples/reproduce_tiers.sh        (run from the repo root)
set -uo pipefail

# ----------------------------- setup -----------------------------
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
TASK="python3 $ROOT/cli/task.py"
SRC="$ROOT/cli/examples/hello-task"
WORK=$(mktemp -d)
DB="$WORK/repro.db"

cleanup() {
  rm -rf "$WORK"
  for t in autodetect override existing; do
    docker rmi -f "taskbundle-$t:v1" >/dev/null 2>&1 || true   # remove only the images we built
  done
}
trap cleanup EXIT

make_bundle() {                       # $1=name → prints a fresh bundle dir with the shared repo
  local b="$WORK/$1"
  mkdir -p "$b"
  cp -R "$SRC/repo" "$b/repo"
  echo "$b"
}

pass=0; fail=0
check() {                             # $1=label  $2=expected_tier  $3=bundle_dir
  echo
  echo "── $1  (expect tier: $2) ──────────────────────────────────"
  local out rc
  out=$($TASK init "$3" --db "$DB" 2>&1); rc=$?
  echo "$out" | sed 's/^/    /'
  if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q "tier: $2"; then
    pass=$((pass + 1)); echo "    => PASS (real build + smoke ok, tier=$2)"
  else
    fail=$((fail + 1)); echo "    => FAIL (rc=$rc)"
  fi
}

# ----------------------------- tier 3: auto-detect -----------------------------
B=$(make_bundle autodetect)
cat > "$B/task.json" <<'EOF'
{
  "id": "autodetect",
  "repo": "repo",
  "commit": "v1"
}
EOF
check "auto-detect — Dockerfile generated from the detected Python stack" "auto-detect" "$B"

# ----------------------------- tier 2: override -----------------------------
B=$(make_bundle override)
cat > "$B/task.json" <<'EOF'
{
  "id": "override",
  "repo": "repo",
  "commit": "v1",
  "install_cmd": "pip install -e . && pip install pytest"
}
EOF
check "override — Dockerfile generated from task.json install_cmd" "override" "$B"

# ----------------------------- tier 1: existing -----------------------------
B=$(make_bundle existing)
cat > "$B/task.json" <<'EOF'
{
  "id": "existing",
  "repo": "repo",
  "commit": "v1"
}
EOF
cat > "$B/Dockerfile" <<'EOF'
# Hand-written — tier 1 "existing" uses this verbatim (never regenerated).
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
COPY repo /workspace/repo
WORKDIR /workspace/repo
RUN pip install -e . && pip install pytest
EOF
check "existing — hand-written Dockerfile used verbatim" "existing" "$B"

# ----------------------------- summary -----------------------------
echo
echo "════════════════════════════════════════════════════════════"
echo "tiers passed: $pass / 3"
[ "$fail" -eq 0 ]
