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
#                <results_dir>/doctor/<cv>.json (no LLM cost; an ERROR row means
#                the run tripped a quality-score hard-fail gate -- do not deliver)
#   input_dir    directory of .docx CVs to run          (required)
#   count        number of NEW CVs to run this call      (default 25)
#   results_dir  where outputs/logs/summary are written  (default <input_dir>/_batch_runs)
#   model        value for CVICHE_LLM_MODEL, overriding the default model block
#                only -- explicit per-stage entries in llm_config.yaml still win
#                (stage_3b stays on Haiku). Default: whatever the config resolves.
#                The 'model' column is SCRAPED from each run's own output, so it
#                reports what actually served the calls rather than what was asked
#                for (#444).
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
METRICS_TSV="$RESULTS/metrics.tsv"
SCORES_TSV="$RESULTS/scores.tsv"
mkdir -p "$OUTDIR" "$LOGDIR"
[ -f "$SCORES_TSV" ] || printf 'date\tsha\tcv\tscore\tband\traw_before_caps\ttop_penalties\n' > "$SCORES_TSV"
if [ "$DOCTOR" = "1" ]; then
  mkdir -p "$DOCTOR_DIR"
  [ -f "$DOCTOR_TSV" ] || printf 'date\tsha\tcv\tworst\tERROR\tWARN\tINFO\ttop_lints\n' > "$DOCTOR_TSV"
  # #816: batch-trend numbers (appendix share, honors malformed-row rate,
  # unrouted taxonomy codes, stage-3b fallback ratio, source coverage %,
  # T-validation/fragment-reconnection yields), one row per CV, distinct
  # from doctor.tsv's per-run findings. Header created here, same as the
  # two TSVs above; doctor_one.py --metrics-tsv only ever appends.
  [ -f "$METRICS_TSV" ] || printf 'uid\tappendix_entries\tappendix_share\thonors_malformed_rows\thonors_rows\tunrouted_code_entries\tsource_coverage_pct\tstage3b_fallback_ratio\tt_validation_yield\tfragment_reconnection_yield\ttotal_post_corrections\n' > "$METRICS_TSV"
fi

# provenance for this invocation
SHA="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
STARTED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
MODEL_LABEL="${MODEL:-default}"

# 'defaulted' is APPENDED at the end (#810), never inserted -- existing
# columns keep their positions, since the merge scripts and scores.tsv joins
# read summary.tsv by index.
HEADER=$'date\tsha\tmodel\tcv\texit\twcm_output\tkb\tsections\theaders\tentries\tclassified\tdefaulted'
if [ -f "$SUMMARY" ]; then
  # round-2 N6: a summary.tsv from before #810 has the old 11-column header
  # (no 'defaulted') -- appending this run's 12-cell rows under it silently
  # would leave every reader that keys columns off the header line
  # misaligned. Cheapest honest fix: widen the header in place, once. The
  # OLD rows are never rewritten, so under the new header they simply read
  # as an empty 'defaulted' cell by position -- the same as any short
  # trailing TSV row already reads, never a fabricated 0.
  if ! head -1 "$SUMMARY" | grep -q $'\tdefaulted$'; then
    sed -i.bak '1s/$/\tdefaulted/' "$SUMMARY" && rm -f "$SUMMARY.bak"
  fi
else
  printf '%s\n' "$HEADER" > "$SUMMARY"
fi

# The loop below only ever sees *.docx -- the pipeline's readers don't handle
# other formats yet (#524 is the separate initiative for that). A directory
# of PDFs or anything else non-.docx would otherwise glob to zero iterations
# and exit 0 with no rows, indistinguishable from a genuinely empty or
# already-fully-run directory (#528). Count and name what got skipped here,
# under its own counter -- distinct from the "already had output" $skipped
# below -- so that silent zero-docx case is visible.
NAMED_SKIP_LIMIT=5
skipped_nondocx=0; nondocx_names=()
for f in "$INPUT_DIR"/*; do
  [ -e "$f" ] || continue
  [ -d "$f" ] && continue
  base="$(basename "$f")"
  case "$base" in
    .*|*.docx) continue ;;
  esac
  skipped_nondocx=$((skipped_nondocx+1))
  nondocx_names+=("$base")
done
if [ "$skipped_nondocx" -gt 0 ]; then
  if [ "$skipped_nondocx" -le "$NAMED_SKIP_LIMIT" ]; then
    shown="$(IFS=,; echo "${nondocx_names[*]}")"
  else
    shown="$skipped_nondocx files"
  fi
  echo "Skipped $skipped_nondocx non-.docx file(s) in $INPUT_DIR: $shown"
fi

ran=0; skipped=0; failed=0
for f in "$INPUT_DIR"/*.docx; do
  [ -e "$f" ] || continue
  [ "$ran" -ge "$COUNT" ] && break
  stem="$(basename "$f" .docx)"
  log="$LOGDIR/${stem}.log"
  if [ -f "$OUTDIR/${stem}_wcm.docx" ]; then
    # #810: a docx existing is no longer proof the run is done -- a run that
    # hit the stage3b_fallback_ratio hard-fail gate (doctor ERROR) or an
    # outage-class LLM failure still writes one, and re-invoking the batch
    # used to treat it as finished forever.
    if [ "$DOCTOR" = "1" ]; then
      # Skip only on a CLEAN doctor row: the latest row for this cv whose
      # `worst` column is not ERROR. No row yet (never doctored) or worst=
      # ERROR both fall through to re-run.
      worst="$(awk -F'\t' -v cv="$stem" '$3==cv{w=$4} END{print w}' "$DOCTOR_TSV" 2>/dev/null)"
      if [ -n "$worst" ] && [ "$worst" != "ERROR" ]; then
        skipped=$((skipped+1)); continue
      fi
    elif ! grep -qE 'Stage 3b batch classification failed|LLMOutageError' "$log" 2>/dev/null; then
      # --doctor is off: keep the old docx-existence check, but only when the
      # run's own log shows no outage-driven failure -- a log naming one
      # means this docx is exactly the case above and must be re-run.
      skipped=$((skipped+1)); continue
    fi
  fi

  ran=$((ran+1))
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "[$(date '+%H:%M:%S')] ($ran/$COUNT) $stem ..."
  if [ -n "$MODEL" ]; then
    CVICHE_LLM_MODEL="$MODEL" python3 run_full_pipeline.py "$f" > "$log" 2>&1
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
            "$OUTDIR/${stem}_wcm.docx" "$OUTDIR/${stem}_quality.json" --source "$f" 2>>"$log") \
    || sline=$'error\t\t\t'
  printf '%s\t%s\t%s\t%s\n' "$ts" "$SHA" "$stem" "$sline" >> "$SCORES_TSV"
  echo "   score: $(printf '%s' "$sline" | cut -f1) $(printf '%s' "$sline" | cut -f2)"

  sec=$(grep -oE 'Top-level sections: [0-9]+' "$log" | grep -oE '[0-9]+' | tail -1)
  hdr=$(grep -oE 'Total headers: [0-9]+'      "$log" | grep -oE '[0-9]+' | tail -1)
  ent=$(grep -oE 'Entries extracted: [0-9]+'  "$log" | grep -oE '[0-9]+' | tail -1)
  # #810: 'classified' is now entries that received a REAL taxonomy code
  # (run_full_pipeline.py logs llm_classified under this same literal, so the
  # column header is unchanged); 'defaulted' is the new, appended column for
  # entries that fell back to a default code on an LLM failure.
  cls=$(grep -oE 'Entries classified: [0-9]+' "$log" | grep -oE '[0-9]+' | tail -1)
  dft=$(grep -oE 'Entries defaulted: [0-9]+'  "$log" | grep -oE '[0-9]+' | tail -1)
  # Scraped, not echoed: the old column repeated whatever was passed in, so it
  # could not tell two model configurations apart and said 'default' either way.
  mdl=$(grep -oE '^Models: .*' "$log" | tail -1 | sed 's/^Models: //' | tr '\t' ' ')
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$ts" "$SHA" "${mdl:-$MODEL_LABEL}" "$stem" "$rc" "$out" "${kb:-}" "${sec:-}" "${hdr:-}" "${ent:-}" "${cls:-}" "${dft:-}" >> "$SUMMARY"

  # optional: run the deterministic doctor over this run's stage artifacts and record findings
  if [ "$DOCTOR" = "1" ]; then
    dline=$(PYTHONPATH=src python3 scripts/doctor_one.py "$OUTPUTS_ROOT" "$stem" "$f" "$DOCTOR_DIR/${stem}.json" --metrics-tsv "$METRICS_TSV" 2>>"$log") || dline=$'error\t\t\t\t'
    printf '%s\t%s\t%s\t%s\n' "$ts" "$SHA" "$stem" "$dline" >> "$DOCTOR_TSV"
    echo "   doctor: $(printf '%s' "$dline" | cut -f1) (E/W/I $(printf '%s' "$dline" | cut -f2-4 | tr '\t' '/'))"
  fi

  if [ "$rc" -ne 0 ] || [ "$out" = "—" ]; then failed=$((failed+1)); echo "   ! $stem rc=$rc output=$out (see $log)"; fi
  sleep 3
done

FINISHED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf '{"started":"%s","finished":"%s","sha":"%s","branch":"%s","model":"%s","input_dir":"%s","count":%s,"ran":%s,"skipped":%s,"failed":%s}\n' \
  "$STARTED" "$FINISHED" "$SHA" "$BRANCH" "$MODEL_LABEL" "$INPUT_DIR" "$COUNT" "$ran" "$skipped" "$failed" >> "$META"

if [ "$ran" -eq 0 ] && [ "$skipped_nondocx" -gt 0 ]; then
  echo "WARNING: ran=0 -- $skipped_nondocx non-.docx file(s) in $INPUT_DIR were skipped (see above); confirm $INPUT_DIR actually contains .docx CVs to run"
fi
echo "DONE ran=$ran skipped=$skipped skipped_nondocx=$skipped_nondocx failed=$failed sha=$SHA doctor=$DOCTOR  (results in $RESULTS)"
