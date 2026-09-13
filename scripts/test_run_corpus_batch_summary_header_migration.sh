#!/usr/bin/env bash
# Smoke test for the summary.tsv header migration in run_corpus_batch.sh
# (#810 round-2 N6).
#
#   scripts/test_run_corpus_batch_summary_header_migration.sh
#
# Runs the real script with COUNT=0, the same trick
# test_run_corpus_batch_nondocx_skip.sh uses: the .docx loop breaks on its
# very first iteration (ran=0 >= COUNT=0) before python3 run_full_pipeline.py
# is ever invoked, so this exercises only the header-widening logic that
# runs before the loop.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/run_corpus_batch.sh"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

results="$tmp/_batch_runs"
mkdir -p "$results"
# An old, pre-#810 summary.tsv: 11 columns, no 'defaulted'.
printf 'date\tsha\tmodel\tcv\texit\twcm_output\tkb\tsections\theaders\tentries\tclassified\n' \
  > "$results/summary.tsv"
printf '2026-01-01T00:00:00Z\tabc1234\tdefault\told_cv\t0\told_cv_wcm.docx\t12\t3\t9\t20\t20\n' \
  >> "$results/summary.tsv"

: > "$tmp/one.docx"
"$SCRIPT" "$tmp" 0 "$results" > /dev/null 2>&1

header="$(head -1 "$results/summary.tsv")"
echo "$header" | grep -qE $'\tdefaulted$' \
  || { echo "FAIL: header was not widened with 'defaulted': $header"; exit 1; }
echo "old 11-column header gains 'defaulted'          ok"

first_col="$(head -1 "$results/summary.tsv" | cut -f1)"
[ "$first_col" = "date" ] \
  || { echo "FAIL: header rewrite corrupted the first column: $first_col"; exit 1; }
echo "header rewrite touches only the trailing column ok"

old_row="$(sed -n '2p' "$results/summary.tsv")"
echo "$old_row" | grep -qF "old_cv" \
  || { echo "FAIL: the old data row was altered or lost: $old_row"; exit 1; }
n_cells="$(echo "$old_row" | awk -F'\t' '{print NF}')"
[ "$n_cells" -eq 11 ] \
  || { echo "FAIL: old row must keep its original 11 cells (reads as an empty trailing 'defaulted' by position), got $n_cells"; exit 1; }
echo "old row is never rewritten (stays 11 cells)     ok"

n_header_cols="$(head -1 "$results/summary.tsv" | awk -F'\t' '{print NF}')"
[ "$n_header_cols" -eq 12 ] \
  || { echo "FAIL: widened header must have 12 columns, got $n_header_cols"; exit 1; }
echo "widened header has 12 columns                   ok"

# Idempotent: running again must not double-append 'defaulted'.
"$SCRIPT" "$tmp" 0 "$results" > /dev/null 2>&1
second_header="$(head -1 "$results/summary.tsv")"
[ "$second_header" = "$header" ] \
  || { echo "FAIL: a second run rewrote an already-widened header: $second_header"; exit 1; }
echo "already-widened header is left alone (idempotent) ok"

echo
echo "all run_corpus_batch summary.tsv header migration checks passed"
