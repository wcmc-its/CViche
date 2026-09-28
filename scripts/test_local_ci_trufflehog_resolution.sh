#!/usr/bin/env bash
# Regression test for local_ci.sh's TRUFFLEHOG resolution (rework round 1
# on the CI-outage stand-in script, #829 context): the header documents
# TRUFFLEHOG as "path to (or name on PATH of) the trufflehog binary", but
# the preflight check used to test `[ -x "$TRUFFLEHOG" ]` directly on the
# raw value, which never resolves a bare name through PATH -- so the
# documented interface (a name on PATH) failed even with that name
# resolvable via PATH.
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

if [ "$fail" -ne 0 ]; then
  exit 1
fi

echo
echo "all local_ci.sh TRUFFLEHOG resolution checks passed"
