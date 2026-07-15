# Running CV corpus batches

How to run a group of CVs through the pipeline to grow/evaluate the corpus, without
overloading the LLM APIs or reinventing the wheel each time. Companion to issue #272.

## TL;DR

```bash
# 1. Be on the latest merged pipeline (integration branch = dev)
git fetch origin && git checkout dev && git pull      # or use a worktree off origin/dev

# 2. Run a group of 25, one at a time
scripts/run_corpus_batch.sh data/sample_cvs/word/web_harvest 25

# 3. Inspect
column -t -s$'\t' data/sample_cvs/word/web_harvest/_batch_runs/summary.tsv
open data/sample_cvs/word/web_harvest/_batch_runs/outputs/   # the *_wcm.docx files
```

## Why one at a time

A single `run_full_pipeline.py` invocation already fans out several concurrent LLM
calls internally (per-stage batching). Running CVs sequentially keeps total concurrency
bounded and gentle on the OpenAI/Bedrock rate limits and the local machine. **Do not
parallelize the loop.** Cost is ~$2–3 per CV; 25 ≈ ~$50–75 and roughly 1–2 hours.

## Prerequisites

- **Run on the latest `dev`.** Pipeline output fidelity depends on merged stage-6 fixes.
  Running on a stale branch reproduces already-fixed bugs (dropped content, leaked
  separators/codes, mis-numbered citations, admin-committee crash). If your working
  checkout is on a feature branch, use a throwaway worktree so you don't disturb it:
  ```bash
  git worktree add --detach ~/worktrees/cviche-run origin/dev
  cd ~/worktrees/cviche-run
  # inputs can be an absolute path back to the main checkout's web_harvest dir
  git worktree remove ~/worktrees/cviche-run   # when done
  ```
- **Credentials** in the shell env (`OPENAI_API_KEY`, and `AWS_ACCESS_KEY_ID` /
  `BEDROCK_API_KEY` if using Bedrock). No repo `.env` — the pipeline reads `os.environ`.
- **`python3`** (not `python`) with pipeline deps installed globally.
- **poppler** (`pdftoppm`) only if running `.pdf` inputs (vision segmentation).

## The corpus

- `data/sample_cvs/word/web_harvest/` — 240 real biomedical `.docx` CVs harvested from the
  web (see issue #272). This path is **gitignored**: the CVs contain real-people PII and
  **must stay local — never commit them.** `allurls.txt` there records provenance.

## Inputs vs. runs

- Inputs (`.docx` CVs) are pipeline-version-independent — harvesting is never wasted.
- Runs (the LLM spend) bake in the current pipeline behavior — only run on merged code.

## The runner

`scripts/run_corpus_batch.sh <input_dir> [count] [results_dir] [model]`

- **Sequential**, one CV at a time, `sleep 3` between.
- **Idempotent** — skips any CV whose `_wcm.docx` already exists in the results dir, so you
  can run in groups (25 now, 25 later) and safely re-invoke after an interruption.
- Per CV it writes:
  - `_batch_runs/outputs/<cv>_wcm.docx` — the WCM output document
  - `_batch_runs/outputs/<cv>_quality.json` — quality score (if the run emits one)
  - `_batch_runs/logs/<cv>.log` — full stdout/stderr
  - one row in `_batch_runs/summary.tsv` — exit code, output, KB, sections/headers/entries/classified

Run the next group later with the same command — already-done CVs are skipped automatically.
To run a specific model: pass it as the 4th arg (e.g. `gpt-5.1`, or a Bedrock model id).

## Inspecting results

```bash
column -t -s$'\t' <results_dir>/summary.tsv     # scan exit codes + entry counts
# rows with exit≠0 or wcm_output=— are failures; read the matching logs/<cv>.log
```
Spot-check a few `_wcm.docx` outputs in Word. Compare `entries` vs `classified` for large
drops. For deeper diagnosis of a single run, use the `run-autopsy` skill on its uid.

## From run to gold

Running produces parse **candidates**, not verified gold. To grow the gold set, hand-verify
selected outputs and promote them into `src/unified_pipeline/outputs/gold_set/` (the
`segsnap_*` snapshots are the existing regression references). Verification is the separate,
expensive step — don't treat raw batch outputs as ground truth.
