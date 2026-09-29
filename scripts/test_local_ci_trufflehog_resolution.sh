#!/usr/bin/env bash
# Regression test for local_ci.sh's preflight checks (rework round 1 on the
# CI-outage stand-in script, #829 context): the header documents TRUFFLEHOG
# as "path to (or name on PATH of) the trufflehog binary", but the
# preflight check used to test `[ -x "$TRUFFLEHOG" ]` directly on the raw
# value, which never resolves a bare name through PATH -- so the documented
# interface (a name on PATH) failed even with that name resolvable via
# PATH. Rework round 3 added coverage for the script's other two preflight
# guards (node 20 not found; not run from inside a git repo), which this
# file's original six cases never exercised. Rework round 4 added two more
# cases that the round-3 cases still let two mutants survive on: a `node`
# file that exists but isn't executable (round 3's node20-missing case only
# tried an empty directory, so it never exercised the `-x` bit itself), and
# a TRUFFLEHOG set to a shell builtin name (`command -v` resolves a builtin
# to a non-empty string that isn't an executable file, which the round-3
# cases never produced).
#
#   scripts/test_local_ci_trufflehog_resolution.sh
#
# Builds a throwaway git repo (its own bare "origin", with HOME sandboxed
# to a temp dir so the script's hardcoded $HOME/worktrees/ci-<label> never
# touches the real machine) holding a copy of local_ci.sh, then runs the
# real script's preflight against it with a deliberately bad head SHA --
# far enough to prove preflight passed (merge fails fast with
# "MERGE CONFLICT"), without installing anything or touching the real dev
# remote.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# --- throwaway repo carrying the script under test ---
repo="$tmp/repo"
mkdir -p "$repo/scripts"
cp "$REPO_ROOT/scripts/local_ci.sh" "$repo/scripts/local_ci.sh"
(
  cd "$repo" && git init -q -b main .
  git -c user.name=t -c user.email=t@example.com add -A
  git -c user.name=t -c user.email=t@example.com commit -q -m init
) > /dev/null

# --- bare "origin" with a dev branch, so `git fetch origin` and
# `rev-parse origin/dev` succeed without the network or the real repo ---
origin="$tmp/origin.git"
git init -q --bare -b dev "$origin" > /dev/null
(
  cd "$tmp" && git clone -q "$origin" seed
  cd seed
  git -c user.name=t -c user.email=t@example.com commit -q --allow-empty -m seed
  git push -q origin dev
) > /dev/null 2>&1
git -C "$repo" remote add origin "$origin"

# --- fake node20: preflight checks for an executable node before it ever
# reaches the trufflehog check ---
node_dir="$tmp/node20"
mkdir -p "$node_dir"
printf '#!/usr/bin/env bash\necho v20.0.0\n' > "$node_dir/node"
chmod +x "$node_dir/node"

# --- fake trufflehog binary, used both on and off PATH below ---
fake_th_dir="$tmp/fake-th"
mkdir -p "$fake_th_dir"
printf '#!/usr/bin/env bash\necho fake-trufflehog\n' > "$fake_th_dir/trufflehog"
chmod +x "$fake_th_dir/trufflehog"

# --- sandbox HOME so $HOME/worktrees/ci-<label> lands in $tmp, not on the
# real machine ---
fakehome="$tmp/home"
mkdir -p "$fakehome"

BAD_SHA=deadbeefdeadbeefdeadbeefdeadbeefdeadbeef
fail=0

check_passes_preflight() {  # check_passes_preflight <case-label> <env-assignment...>
  local case=$1; shift
  local out rc=0
  # `if out=$(...)` (not a bare assignment) so a non-zero exit from
  # local_ci.sh -- expected here, it always fails past preflight since
  # $BAD_SHA never merges -- doesn't trip this script's own `set -e`.
  if out="$(env HOME="$fakehome" NODE20_BIN="$node_dir" LOCAL_CI_DIR="$tmp/ci-out-$case" "$@" \
    bash "$repo/scripts/local_ci.sh" "$BAD_SHA" "$case" 2>&1)"; then
    rc=0
  else
    rc=$?
  fi
  # A bad, unresolvable HEAD_SHA makes the real merge step fail fast, well
  # after the trufflehog preflight -- that's the signal preflight passed.
  if [ "$rc" -eq 2 ] && printf '%s' "$out" | grep -qF "MERGE CONFLICT"; then
    echo "$case: preflight passed (reached the merge step)      ok"
  else
    echo "FAIL ($case): expected preflight to pass and fail later at MERGE CONFLICT (rc=2); got rc=$rc"
    echo "$out"
    fail=1
  fi
}

check_fails_preflight() {  # check_fails_preflight <case-label> <env-assignment...>
  local case=$1; shift
  local out rc=0
  if out="$(env HOME="$fakehome" NODE20_BIN="$node_dir" LOCAL_CI_DIR="$tmp/ci-out-$case" "$@" \
    bash "$repo/scripts/local_ci.sh" "$BAD_SHA" "$case" 2>&1)"; then
    rc=0
  else
    rc=$?
  fi
  if [ "$rc" -eq 1 ] && printf '%s' "$out" | grep -qF "trufflehog not found"; then
    echo "$case: preflight correctly rejects it              ok"
  else
    echo "FAIL ($case): expected the documented 'trufflehog not found' error (rc=1); got rc=$rc"
    echo "$out"
    fail=1
  fi
}

# check_prefers_path_over_fallback <case-label> <env-assignment...>: like
# check_passes_preflight, but also asserts (via local_ci.sh's own
# "using trufflehog at ..." stderr line) which of two present binaries was
# actually picked -- the resolution order itself, not just pass/fail.
check_prefers_path_over_fallback() {
  local case=$1; shift
  local out rc=0
  if out="$(env HOME="$fakehome" NODE20_BIN="$node_dir" LOCAL_CI_DIR="$tmp/ci-out-$case" "$@" \
    bash "$repo/scripts/local_ci.sh" "$BAD_SHA" "$case" 2>&1)"; then
    rc=0
  else
    rc=$?
  fi
  local path_th="$fake_th_dir/trufflehog"
  local fallback_th="$tmp/ci-out-$case/tools/trufflehog"
  if [ "$rc" -eq 2 ] && printf '%s' "$out" | grep -qF "using trufflehog at $path_th" \
    && ! printf '%s' "$out" | grep -qF "using trufflehog at $fallback_th"; then
    echo "$case: PATH-resolved trufflehog wins over the fallback     ok"
  else
    echo "FAIL ($case): expected '$path_th' (PATH) to win over the fallback '$fallback_th'; got rc=$rc"
    echo "$out"
    fail=1
  fi
}

# A bare name that only resolves via PATH -- this is the reported bug.
check_passes_preflight bare-name-on-path PATH="$fake_th_dir:$PATH" TRUFFLEHOG=trufflehog

# An explicit path, not on PATH at all -- must keep working.
check_passes_preflight explicit-path PATH=/usr/bin:/bin TRUFFLEHOG="$fake_th_dir/trufflehog"

# TRUFFLEHOG unset (empty is equivalent under bash's ${VAR:-default}), bare
# `trufflehog` resolvable on PATH -- the pre-existing default-PATH behaviour
# must be unaffected by the fix.
check_passes_preflight unset-on-path PATH="$fake_th_dir:$PATH" TRUFFLEHOG=

# A name that resolves nowhere must still fail, with the documented message.
check_fails_preflight not-found PATH=/usr/bin:/bin TRUFFLEHOG=definitely-not-a-real-binary

# A shell builtin name also resolves via `command -v` (verified: `command -v
# cd` prints `cd`), but the result isn't an executable FILE -- `[ -x "$TH" ]`
# on a bare word like "cd" tests for a file literally named `cd` in the
# current directory, which doesn't exist here. Preflight must still reject
# it with the documented message. Catches a mutant that drops the
# `[ ! -x "$TH" ]` half of the guard (`if [ -z "$TH" ] || [ ! -x "$TH" ];
# then` -> `if [ -z "$TH" ]; then`), since a resolved-but-non-executable
# builtin name would then sail past preflight.
check_fails_preflight builtin-name PATH=/usr/bin:/bin TRUFFLEHOG=cd

# PATH has no trufflehog at all, but an executable sits at the documented
# fallback location ($LOCAL_CI_DIR/tools/trufflehog) -- preflight must still
# pass. Catches a mutant that disables the fallback branch outright.
fallback_only_case=fallback-only
mkdir -p "$tmp/ci-out-$fallback_only_case/tools"
cp "$fake_th_dir/trufflehog" "$tmp/ci-out-$fallback_only_case/tools/trufflehog"
chmod +x "$tmp/ci-out-$fallback_only_case/tools/trufflehog"
check_passes_preflight "$fallback_only_case" PATH=/usr/bin:/bin TRUFFLEHOG=

# Both a PATH-resolvable trufflehog AND an executable at the fallback
# location exist -- the PATH one must win. Catches a mutant that drops the
# `[ -z "$TH" ]` guard so the fallback unconditionally overrides an
# already-resolved TRUFFLEHOG/PATH hit.
both_present_case=both-present
mkdir -p "$tmp/ci-out-$both_present_case/tools"
cp "$fake_th_dir/trufflehog" "$tmp/ci-out-$both_present_case/tools/trufflehog"
chmod +x "$tmp/ci-out-$both_present_case/tools/trufflehog"
check_prefers_path_over_fallback "$both_present_case" PATH="$fake_th_dir:$PATH" TRUFFLEHOG=trufflehog

# check_fails_on_missing_node20 <case-label> <node20-dir> <env-assignment...>:
# like check_fails_preflight, but for the node-20 guard, which runs before
# the TRUFFLEHOG check -- TRUFFLEHOG's value is irrelevant here since the
# script must never reach that check.
check_fails_on_missing_node20() {
  local case=$1; shift
  local node20_dir=$1; shift
  local out rc=0
  if out="$(env HOME="$fakehome" NODE20_BIN="$node20_dir" LOCAL_CI_DIR="$tmp/ci-out-$case" "$@" \
    bash "$repo/scripts/local_ci.sh" "$BAD_SHA" "$case" 2>&1)"; then
    rc=0
  else
    rc=$?
  fi
  if [ "$rc" -eq 1 ] && printf '%s' "$out" | grep -qF "node 20 not found"; then
    echo "$case: preflight correctly rejects missing node 20        ok"
  else
    echo "FAIL ($case): expected the documented 'node 20 not found' error (rc=1); got rc=$rc"
    echo "$out"
    fail=1
  fi
}

# NODE20_BIN pointing at a directory with no executable `node` -- preflight
# must fail before ever reaching the TRUFFLEHOG check. Catches a mutant
# that disables the node-20 guard (`if [ ! -x "$NODE20/node" ]; then` ->
# `if false; then`).
empty_node20_dir="$tmp/no-node20"
mkdir -p "$empty_node20_dir"
check_fails_on_missing_node20 node20-missing "$empty_node20_dir" PATH=/usr/bin:/bin TRUFFLEHOG=

# NODE20_BIN pointing at a directory whose `node` file EXISTS but is not
# executable (touched, never chmod +x) -- preflight must still fail. The
# empty-directory case above only proves the guard reacts to a missing
# path; it never exercises the executable-bit test itself, so a mutant
# that weakens `[ ! -x "$NODE20/node" ]` to `[ ! -e "$NODE20/node" ]`
# would pass it unnoticed. This case's `node` file exists, so only the
# real `-x` check (not a mutated `-e` check) can still reject it.
non_exec_node20_dir="$tmp/non-exec-node20"
mkdir -p "$non_exec_node20_dir"
touch "$non_exec_node20_dir/node"
check_fails_on_missing_node20 node20-not-executable "$non_exec_node20_dir" PATH=/usr/bin:/bin TRUFFLEHOG=

# check_fails_outside_git_repo: runs a COPY of local_ci.sh from a directory
# that is not inside any git repo (not just a different repo) -- the
# not-inside-a-git-repo guard is the very first thing the script checks, so
# NODE20_BIN/TRUFFLEHOG are never reached and don't matter here. Catches
# mutants on that guard: `if [ -z "$REPO" ]; then` -> `if false; then`, and
# its `exit 1` -> `exit 0`.
check_fails_outside_git_repo() {
  local case=not-a-git-repo
  local nogit_dir="$tmp/nogit"
  mkdir -p "$nogit_dir/scripts"
  cp "$repo/scripts/local_ci.sh" "$nogit_dir/scripts/local_ci.sh"
  local out rc=0
  if out="$(env HOME="$fakehome" LOCAL_CI_DIR="$tmp/ci-out-$case" PATH=/usr/bin:/bin \
    bash "$nogit_dir/scripts/local_ci.sh" "$BAD_SHA" "$case" 2>&1)"; then
    rc=0
  else
    rc=$?
  fi
  if [ "$rc" -eq 1 ] && printf '%s' "$out" | grep -qF "not inside a git repo"; then
    echo "$case: preflight correctly rejects a non-git checkout     ok"
  else
    echo "FAIL ($case): expected the documented 'not inside a git repo' error (rc=1); got rc=$rc"
    echo "$out"
    fail=1
  fi
}
check_fails_outside_git_repo

if [ "$fail" -ne 0 ]; then
  exit 1
fi

echo
echo "all local_ci.sh preflight checks passed"
