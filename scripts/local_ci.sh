#!/usr/bin/env bash
# Local stand-in for .github/workflows/ci.yml while Actions cannot start jobs.
# usage: scripts/local_ci.sh <pr-head-sha> <label>
# Test-merges <pr-head-sha> onto a freshly fetched origin/dev in its own
# worktree, then runs every ci.yml job with CI's commands and pinned tools.
#
# Environment (all optional, sensible defaults below):
#   LOCAL_CI_DIR  scratch/output root. Default: ${TMPDIR:-/tmp}/cviche-local-ci
#   TRUFFLEHOG    path to (or name on PATH of) the trufflehog binary (ci.yml
#                 pins v3.97.4). Default: `trufflehog` on PATH, else
#                 $LOCAL_CI_DIR/tools/trufflehog.
#   NODE20_BIN    directory containing a node 20 binary (ci.yml pins node 20).
#                 Default: /opt/homebrew/opt/node@20/bin.
set -uo pipefail
HEAD_SHA=$1; LABEL=$2

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null)"
if [ -z "$REPO" ]; then
  echo "local_ci.sh: not inside a git repo (looked from $SCRIPT_DIR)" >&2
  exit 1
fi

S="${LOCAL_CI_DIR:-${TMPDIR:-/tmp}/cviche-local-ci}"
mkdir -p "$S"
WT=$HOME/worktrees/ci-$LABEL
OUT=$S/ci-$LABEL; rm -rf "$OUT"; mkdir -p "$OUT"
VENV=$S/venv-ci

NODE20="${NODE20_BIN:-/opt/homebrew/opt/node@20/bin}"
if [ ! -x "$NODE20/node" ]; then
  echo "local_ci.sh: node 20 not found at '$NODE20/node'. Set NODE20_BIN=/path/to/node20/bin (ci.yml pins node 20)." >&2
  exit 1
fi

# Precedence: an explicit TRUFFLEHOG (or the default bare name `trufflehog`)
# resolved via PATH wins whenever it resolves at all -- the
# $LOCAL_CI_DIR/tools/trufflehog fallback is only consulted when that lookup
# comes back empty, never the other way around.
TH="$(command -v "${TRUFFLEHOG:-trufflehog}" 2>/dev/null || true)"
if [ -z "$TH" ] && [ -x "$S/tools/trufflehog" ]; then
  TH="$S/tools/trufflehog"
fi
if [ -z "$TH" ] || [ ! -x "$TH" ]; then
  echo "local_ci.sh: trufflehog not found. Set TRUFFLEHOG=/path/to/trufflehog, put it on PATH, or place it at $S/tools/trufflehog (ci.yml pins v3.97.4)." >&2
  exit 1
fi
echo "local_ci.sh: using trufflehog at $TH" >&2

git -C "$REPO" fetch -q origin
DEV=$(git -C "$REPO" rev-parse origin/dev)
[ -d "$WT" ] && git -C "$REPO" worktree remove --force "$WT"
git -C "$REPO" worktree add -q --detach "$WT" "$DEV"
git -C "$WT" -c user.name=ci -c user.email=ci@local merge -q --no-edit "$HEAD_SHA" || { echo "MERGE CONFLICT"; exit 2; }
MERGE=$(git -C "$WT" rev-parse HEAD)
{ echo "dev=$DEV"; echo "pr_head=$HEAD_SHA"; echo "test_merge=$MERGE"; } > "$OUT/refs.txt"

if [ ! -x "$VENV/bin/python" ]; then
  python3.14 -m venv "$VENV"
  "$VENV/bin/python" -m pip install -q --upgrade pip
fi
"$VENV/bin/pip" install -q -r "$WT/web_interface/backend/requirements-dev.txt" -r "$WT/requirements.txt" mypy ruff==0.16.6 > "$OUT/pip.log" 2>&1
PY="$VENV/bin/python"; export PATH="$VENV/bin:$PATH"

run() {  # run <job> <cmd...>; records rc + last line
  local job=$1; shift
  ( cd "$WT" && "$@" ) > "$OUT/$job.log" 2>&1; local rc=$?
  printf '%-28s rc=%s  %s\n' "$job" "$rc" "$(grep -v '^\s*$' "$OUT/$job.log" | tail -1 | cut -c1-110)" | tee -a "$OUT/summary.txt"
}

run backend-tests env -u DB_HOST -u DB_PORT -u DB_NAME -u DB_USER -u DB_PASSWORD \
  DB_HOST=localhost DB_PORT=3306 DB_NAME=test DB_USER=test CVICHE_SESSION_SECRET=test-secret-not-for-production \
  bash -c 'cd web_interface/backend && python -m pytest -q -p no:cacheprovider'
run pipeline-tests python -m pytest src/unified_pipeline/tests/ -q -p no:cacheprovider
run pipeline:render-doctor-gates python3 scripts/test_render_doctor_gates.py
run pipeline:render-gate-integ python3 scripts/test_render_gate_integration.py
run pipeline:corpus-batch-sh bash -c 'for t in scripts/test_run_corpus_batch_*.sh; do echo "=== $t"; bash "$t" || exit 1; done'
run pipeline:local-ci-selftest bash scripts/test_local_ci_trufflehog_resolution.sh
# function-size: CI checks out depth 1, where check_standards' local-only
# "commits behind" probe returns None -- run it on a .git-less copy.
FS=$S/ci-$LABEL-fs; rm -rf "$FS"; rsync -a --exclude .git "$WT/" "$FS/"
( cd "$FS" && python3 scripts/check_function_size.py ) > "$OUT/function-size.log" 2>&1; rc=$?
printf '%-28s rc=%s  %s\n' function-size "$rc" "$(tail -1 "$OUT/function-size.log")" | tee -a "$OUT/summary.txt"
for c in test_check_function_size check_standards test_check_standards; do
  ( cd "$FS" && python3 scripts/$c.py ) > "$OUT/fs-$c.log" 2>&1; rc=$?
  printf '%-28s rc=%s  %s\n' "function-size:$c" "$rc" "$(grep -v '^\s*$' "$OUT/fs-$c.log" | tail -1 | cut -c1-110)" | tee -a "$OUT/summary.txt"
done
run type-check mypy --config-file mypy.ini
# frontend: node 20 as in CI; npm ci into the merge worktree
run frontend-typecheck bash -c "export PATH=$NODE20:\$PATH; cd web_interface/frontend && node --version && npm ci --no-audit --no-fund >/dev/null && npx tsc --noEmit && npm run build 2>&1 | tail -3"
MB=$(git -C "$WT" merge-base "$DEV" "$HEAD_SHA"); git -C "$WT" branch -f "ci-$LABEL-head" "$HEAD_SHA"
run secret-scan "$TH" git "file://$WT" --since-commit "$MB" --branch "ci-$LABEL-head" --no-update --fail --results=verified,unknown --exclude-paths=.trufflehog-exclude.txt
{ echo "tools: $("$PY" --version) | ruff $("$VENV/bin/ruff" --version | cut -d' ' -f2) | mypy $("$VENV/bin/mypy" --version | cut -d' ' -f2) | node $($NODE20/node --version) | trufflehog 3.97.4"; } | tee -a "$OUT/summary.txt"
echo DONE
