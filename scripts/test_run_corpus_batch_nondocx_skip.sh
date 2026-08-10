#!/usr/bin/env bash
# Smoke test for the non-.docx skip reporting in run_corpus_batch.sh (#528).
#
#   scripts/test_run_corpus_batch_nondocx_skip.sh
#
# Runs the real script against a temp dir holding one .docx and one .pdf,
# with COUNT=0 -- that makes the .docx loop break on its very first
# iteration (ran=0 >= COUNT=0), before python3 run_full_pipeline.py is ever
# invoked, so this exercises the actual glob/count logic without running
# the pipeline.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/run_corpus_batch.sh"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

: > "$tmp/one.docx"
: > "$tmp/two.pdf"

out="$("$SCRIPT" "$tmp" 0 2>&1)"

echo "$out" | grep -qF "Skipped 1 non-.docx file(s) in $tmp: two.pdf" \
  || { echo "FAIL: expected a skip line naming two.pdf"; echo "$out"; exit 1; }
echo "skip line names the non-.docx file      ok"

echo "$out" | grep -qE "^DONE ran=0 skipped=0 skipped_nondocx=1 failed=0" \
  || { echo "FAIL: expected skipped_nondocx=1 in the DONE line"; echo "$out"; exit 1; }
echo "DONE line reports skipped_nondocx separately   ok"

echo "$out" | grep -qF "WARNING: ran=0" \
  || { echo "FAIL: expected a ran=0/skipped_nondocx warning"; echo "$out"; exit 1; }
echo "ran=0 with non-.docx files present warns        ok"

echo
echo "all run_corpus_batch non-.docx skip checks passed"
