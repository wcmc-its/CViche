#!/usr/bin/env bash
# Run a batch of CVs through the pipeline ONE AT A TIME.
#
# Sequential on purpose: a single run_full_pipeline.py invocation already fans out
# several concurrent LLM calls internally, so running CVs one-at-a-time keeps total
# load gentle on the LLM APIs and the local machine. Do NOT parallelize this loop.
#
# Idempotent: skips any CV whose WCM output already exists in the results dir, so you
# can run in groups (25 now, 25 later) and re-invoke safely after an interruption.
#
# Usage:
#   scripts/run_corpus_batch.sh <input_dir> [count] [results_dir] [model]
#
#   input_dir    directory of .docx CVs to run          (required)
#   count        number of NEW CVs to run this call      (default 25)
#   results_dir  where outputs/logs/summary are written  (default <input_dir>/_batch_runs)
#   model        --model passed to run_full_pipeline.py   (default: pipeline default)
#
# MUST run on a checkout that has the latest merged pipeline (integration branch = dev).
# See docs/guides/running-cv-corpus-batches.md.
set -u

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT" || exit 1

INPUT_DIR="${1:?usage: run_corpus_batch.sh <input_dir> [count] [results_dir] [model]}"
COUNT="${2:-25}"
RESULTS="${3:-$INPUT_DIR/_batch_runs}"
MODEL="${4:-}"

OUTDIR="$RESULTS/outputs"; LOGDIR="$RESULTS/logs"; SUMMARY="$RESULTS/summary.tsv"
WCM_SRC="src/unified_pipeline/outputs/stage_6_wcm_documents"
mkdir -p "$OUTDIR" "$LOGDIR"
[ -f "$SUMMARY" ] || printf 'cv\texit\twcm_output\tkb\tsections\theaders\tentries\tclassified\n' > "$SUMMARY"

ran=0; skipped=0; failed=0
for f in "$INPUT_DIR"/*.docx; do
  [ -e "$f" ] || continue
  [ "$ran" -ge "$COUNT" ] && break
  stem="$(basename "$f" .docx)"
  if [ -f "$OUTDIR/${stem}_wcm.docx" ]; then skipped=$((skipped+1)); continue; fi

  ran=$((ran+1))
  log="$LOGDIR/${stem}.log"
  echo "[$(date '+%H:%M:%S')] ($ran/$COUNT) $stem ..."
  if [ -n "$MODEL" ]; then
    python3 run_full_pipeline.py "$f" --model "$MODEL" > "$log" 2>&1
  else
    python3 run_full_pipeline.py "$f" > "$log" 2>&1
  fi
  rc=$?

  wcm="$WCM_SRC/${stem}_wcm.docx"
  [ -f "$wcm" ] || wcm="$(ls "$WCM_SRC"/*"${stem}"*[wW][cC][mM]*.docx 2>/dev/null | head -1)"
  out="—"; kb=""
  if [ -n "$wcm" ] && [ -f "$wcm" ]; then cp "$wcm" "$OUTDIR/${stem}_wcm.docx"; out="${stem}_wcm.docx"; kb=$(( $(wc -c < "$wcm") / 1024 )); fi
  cp "src/unified_pipeline/outputs/quality_score.json" "$OUTDIR/${stem}_quality.json" 2>/dev/null

  sec=$(grep -oE 'Top-level sections: [0-9]+' "$log" | grep -oE '[0-9]+' | tail -1)
  hdr=$(grep -oE 'Total headers: [0-9]+'      "$log" | grep -oE '[0-9]+' | tail -1)
  ent=$(grep -oE 'Entries extracted: [0-9]+'  "$log" | grep -oE '[0-9]+' | tail -1)
  cls=$(grep -oE 'Entries classified: [0-9]+' "$log" | grep -oE '[0-9]+' | tail -1)
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$stem" "$rc" "$out" "${kb:-}" "${sec:-}" "${hdr:-}" "${ent:-}" "${cls:-}" >> "$SUMMARY"

  if [ "$rc" -ne 0 ] || [ "$out" = "—" ]; then failed=$((failed+1)); echo "   ! $stem rc=$rc output=$out (see $log)"; fi
  sleep 3
done

echo "DONE ran=$ran skipped=$skipped failed=$failed  (results in $RESULTS)"
