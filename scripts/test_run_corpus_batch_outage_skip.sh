#!/usr/bin/env bash
# Smoke test for run_corpus_batch.sh's #810 skip-rule fix: a pre-existing
# <cv>_wcm.docx is no longer, by itself, proof a CV is done -- a run that
# tripped the stage3b_fallback_ratio hard-fail gate (doctor ERROR) or hit an
# outage-class LLM failure (no --doctor) must be re-run, not skipped forever.
#
#   scripts/test_run_corpus_batch_outage_skip.sh
#
# A fake `python3` shadows the real one on PATH for every CV this test lets
# fall through the skip check (exits 0 immediately, writes nothing) -- this
# test proves the skip DECISION, not a real pipeline run, so
# run_full_pipeline.py/score_one.py/doctor_one.py never have to do real work.
# A CV the script correctly SKIPS never reaches that fake python3 at all
# (the skip `continue` fires first), which is itself part of what each
# assertion below checks.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/run_corpus_batch.sh"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

mkdir -p "$tmp/bin"
cat > "$tmp/bin/python3" <<'PYEOF'
#!/usr/bin/env bash
exit 0
PYEOF
chmod +x "$tmp/bin/python3"
export PATH="$tmp/bin:$PATH"

# --- --doctor mode: skip keys off the LATEST doctor.tsv row's `worst` column ---

input="$tmp/input_doctor"; results="$tmp/results_doctor"
mkdir -p "$input" "$results/outputs"
: > "$input/cv_clean.docx";  : > "$results/outputs/cv_clean_wcm.docx"
: > "$input/cv_error.docx";  : > "$results/outputs/cv_error_wcm.docx"
: > "$input/cv_norow.docx";  : > "$results/outputs/cv_norow_wcm.docx"

printf 'date\tsha\tcv\tworst\tERROR\tWARN\tINFO\ttop_lints\n' > "$results/doctor.tsv"
printf '2026-01-01T00:00:00Z\tabc1234\tcv_clean\tWARN\t0\t2\t1\tsome_lint\n' >> "$results/doctor.tsv"
printf '2026-01-01T00:00:00Z\tabc1234\tcv_error\tERROR\t1\t0\t0\tpipeline_errors_present\n' >> "$results/doctor.tsv"
# cv_norow: no doctor.tsv row at all -- never doctored, must not be treated as done.

out="$("$SCRIPT" --doctor "$input" 3 "$results" 2>&1)"

echo "$out" | grep -qE '^DONE ran=2 skipped=1 ' \
  || { echo "FAIL: expected ran=2 skipped=1 (only cv_clean skipped)"; echo "$out"; exit 1; }
echo "clean doctor row (worst=WARN) is skipped                        ok"

echo "$out" | grep -qF '! cv_error' \
  || { echo "FAIL: expected cv_error to be RE-RUN (its ERROR row must not skip it)"; echo "$out"; exit 1; }
echo "ERROR doctor row forces a re-run, not a skip                    ok"

echo "$out" | grep -qF '! cv_norow' \
  || { echo "FAIL: expected cv_norow to be RE-RUN (no doctor row yet)"; echo "$out"; exit 1; }
echo "no doctor row yet forces a re-run, not a skip                   ok"

# --- without --doctor: skip also requires the log to show no outage marker ---

input2="$tmp/input_nodoctor"; results2="$tmp/results_nodoctor"
mkdir -p "$input2" "$results2/outputs" "$results2/logs"
: > "$input2/cv_nolog.docx";     : > "$results2/outputs/cv_nolog_wcm.docx"
: > "$input2/cv_outagelog.docx"; : > "$results2/outputs/cv_outagelog_wcm.docx"
# cv_nolog: docx exists, no log at all (e.g. migrated from before this fix) -- keep skipping it.
printf 'Stage 3b batch classification failed; 5 entries fall back to default codes\n' \
  > "$results2/logs/cv_outagelog.log"

out2="$("$SCRIPT" "$input2" 2 "$results2" 2>&1)"

echo "$out2" | grep -qE '^DONE ran=1 skipped=1 ' \
  || { echo "FAIL: expected ran=1 skipped=1 (only cv_nolog skipped)"; echo "$out2"; exit 1; }
echo "docx with no log (pre-fix run) is still skipped                 ok"

echo "$out2" | grep -qF '! cv_outagelog' \
  || { echo "FAIL: expected cv_outagelog to be RE-RUN (its log names a stage3b/outage failure)"; echo "$out2"; exit 1; }
echo "a log naming the stage3b/outage failure forces a re-run          ok"

echo
echo "all run_corpus_batch outage-skip checks passed"
