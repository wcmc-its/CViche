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
# Provenance: every row is stamped with the pipeline git SHA, model, and UTC timestamp,
# and each invocation appends a summary line to run_meta.jsonl. This is what lets you
# tell which code version produced an output and re-run/compare later. Do not drop it.
#
# Scoring: every CV is scored from its stage artifacts into <results_dir>/scores.tsv
# and <results_dir>/outputs/<cv>_quality.json. Deterministic, no LLM cost. Rows whose
# run produced no WCM docx are an upper bound, not a measurement -- the two render
# dimensions award flat half credit when there is no docx to inspect (#435).
#
# Usage:
#   scripts/run_corpus_batch.sh [--doctor] <input_dir> [count] [results_dir] [model]
#
#   --doctor     also run the deterministic run_doctor over each run's stage artifacts,
#                writing a row to <results_dir>/doctor.tsv and full findings to
#                <results_dir>/doctor/<cv>.json (no LLM cost; findings are WARN/INFO lints)
#   input_dir    directory of .docx CVs to run          (required)
#   count        number of NEW CVs to run this call      (default 25)
#   results_dir  where outputs/logs/summary are written  (default <input_dir>/_batch_runs)
#   model        --model passed to run_full_pipeline.py   (default: pipeline default)
#
# MUST run on a checkout that has the latest merged pipeline (integration branch = dev).
# For a long batch, launch detached so it survives turn-end reaping:
#   nohup scripts/run_corpus_batch.sh <input_dir> 25 </dev/null >batch.log 2>&1 & disown
# See docs/guides/running-cv-corpus-batches.md.
set -u

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT" || exit 1

# optional flags (currently just --doctor) may appear anywhere; strip them to positionals
DOCTOR=0; POS=()
for a in "$@"; do
  case "$a" in
    --doctor) DOCTOR=1 ;;
    *) POS+=("$a") ;;
  esac
done
set -- ${POS[@]+"${POS[@]}"}

INPUT_DIR="${1:?usage: run_corpus_batch.sh [--doctor] <input_dir> [count] [results_dir] [model]}"
COUNT="${2:-25}"
RESULTS="${3:-$INPUT_DIR/_batch_runs}"
MODEL="${4:-}"

OUTDIR="$RESULTS/outputs"; LOGDIR="$RESULTS/logs"; SUMMARY="$RESULTS/summary.tsv"; META="$RESULTS/run_meta.jsonl"
WCM_SRC="src/unified_pipeline/outputs/stage_6_wcm_documents"
OUTPUTS_ROOT="src/unified_pipeline/outputs"
DOCTOR_TSV="$RESULTS/doctor.tsv"; DOCTOR_DIR="$RESULTS/doctor"
SCORES_TSV="$RESULTS/scores.tsv"
mkdir -p "$OUTDIR" "$LOGDIR"
[ -f "$SCORES_TSV" ] || printf 'date\tsha\tcv\tscore\tband\traw_before_caps\ttop_penalties\n' > "$SCORES_TSV"
if [ "$DOCTOR" = "1" ]; then
  mkdir -p "$DOCTOR_DIR"
  [ -f "$DOCTOR_TSV" ] || printf 'date\tsha\tcv\tworst\tERROR\tWARN\tINFO\ttop_lints\n' > "$DOCTOR_TSV"
fi

# provenance for this invocation
SHA="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
STARTED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
MODEL_LABEL="${MODEL:-default}"

HEADER=$'date\tsha\tmodel\tcv\texit\twcm_output\tkb\tsections\theaders\tentries\tclassified'
[ -f "$SUMMARY" ] || printf '%s\n' "$HEADER" > "$SUMMARY"

ran=0; skipped=0; failed=0
for f in "$INPUT_DIR"/*.docx; do
  [ -e "$f" ] || continue
  [ "$ran" -ge "$COUNT" ] && break
  stem="$(basename "$f" .docx)"
  if [ -f "$OUTDIR/${stem}_wcm.docx" ]; then skipped=$((skipped+1)); continue; fi

  ran=$((ran+1))
  log="$LOGDIR/${stem}.log"
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
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
  # Score from the stage artifacts. This used to `cp` a quality_score.json out of
  # the outputs dir, but nothing in the local CLI pipeline writes that file --
  # only the web backend does, into storage -- so with stderr suppressed it was a
  # silent no-op and no batch ever captured a score (#435).
  sline=$(PYTHONPATH=src python3 scripts/score_one.py "$OUTPUTS_ROOT" "$stem" \
            "$OUTDIR/${stem}_wcm.docx" "$OUTDIR/${stem}_quality.json" 2>>"$log") \
    || sline=$'error\t\t\t'
  printf '%s\t%s\t%s\t%s\n' "$ts" "$SHA" "$stem" "$sline" >> "$SCORES_TSV"
  echo "   score: $(printf '%s' "$sline" | cut -f1) $(printf '%s' "$sline" | cut -f2)"

  sec=$(grep -oE 'Top-level sections: [0-9]+' "$log" | grep -oE '[0-9]+' | tail -1)
  hdr=$(grep -oE 'Total headers: [0-9]+'      "$log" | grep -oE '[0-9]+' | tail -1)
  ent=$(grep -oE 'Entries extracted: [0-9]+'  "$log" | grep -oE '[0-9]+' | tail -1)
  cls=$(grep -oE 'Entries classified: [0-9]+' "$log" | grep -oE '[0-9]+' | tail -1)
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$ts" "$SHA" "$MODEL_LABEL" "$stem" "$rc" "$out" "${kb:-}" "${sec:-}" "${hdr:-}" "${ent:-}" "${cls:-}" >> "$SUMMARY"

  # optional: run the deterministic doctor over this run's stage artifacts and record findings
  if [ "$DOCTOR" = "1" ]; then
    dline=$(PYTHONPATH=src python3 scripts/doctor_one.py "$OUTPUTS_ROOT" "$stem" "$f" "$DOCTOR_DIR/${stem}.json" 2>>"$log") || dline=$'error\t\t\t\t'
    printf '%s\t%s\t%s\t%s\n' "$ts" "$SHA" "$stem" "$dline" >> "$DOCTOR_TSV"
    echo "   doctor: $(printf '%s' "$dline" | cut -f1) (E/W/I $(printf '%s' "$dline" | cut -f2-4 | tr '\t' '/'))"
  fi

  if [ "$rc" -ne 0 ] || [ "$out" = "—" ]; then failed=$((failed+1)); echo "   ! $stem rc=$rc output=$out (see $log)"; fi
  sleep 3
done

FINISHED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf '{"started":"%s","finished":"%s","sha":"%s","branch":"%s","model":"%s","input_dir":"%s","count":%s,"ran":%s,"skipped":%s,"failed":%s}\n' \
  "$STARTED" "$FINISHED" "$SHA" "$BRANCH" "$MODEL_LABEL" "$INPUT_DIR" "$COUNT" "$ran" "$skipped" "$failed" >> "$META"

echo "DONE ran=$ran skipped=$skipped failed=$failed sha=$SHA doctor=$DOCTOR  (results in $RESULTS)"
