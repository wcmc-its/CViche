# Running CV corpus batches

How to harvest CVs, run a group through the pipeline, and track it so the whole thing is
repeatable and outputs stay attributable to a pipeline version. Companion to issue #272.

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
bounded and gentle on Bedrock's rate limits and the local machine. **Do not
parallelize the loop.** Cost is ~$2–3 per CV; 25 ≈ ~$50–75. Wall-clock scales with CV
size — a 900–1,600-entry CV takes ~25–30 min, so a 25-CV batch can run several hours.

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
- **Credentials** in the shell env (`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`, or
  `AWS_BEARER_TOKEN_BEDROCK` -- CViche is Bedrock-only). No repo `.env` — the pipeline
  reads `os.environ`.
- **`python3`** (not `python`) with pipeline deps installed globally.
- **poppler** (`pdftoppm`) only if running `.pdf` inputs (vision segmentation).

## Long batches: launch detached

Plain background jobs get reaped mid-run in some harnesses (observed: a batch killed at
~33 min). For anything multi-hour, detach it so it survives:

```bash
nohup scripts/run_corpus_batch.sh data/sample_cvs/word/web_harvest 25 \
  </dev/null >/tmp/cviche-batch.log 2>&1 & disown
tail -f /tmp/cviche-batch.log     # watch progress; safe to detach and re-attach
```

The runner is idempotent, so if it dies partway you just re-run the same command — done
CVs are skipped and it continues.

## The corpus

- `data/sample_cvs/word/web_harvest/` — real biomedical `.docx` CVs harvested from the
  web (see issue #272). This path is **gitignored**: the CVs contain real-people PII and
  **must stay local — never commit them.** `allurls.txt` there records provenance URLs.

## Harvesting more CVs

Real completed CVs on the open web are almost all PDF; real `.docx` CVs essentially only
exist as faculty-database-hosted files. Two steps:

1. **Find URLs (search-driven).** The pattern that cuts through templates is bare `CV` +
   the specialty + a docx filter — vary the specialty for a random assortment:
   ```
   CV oncology filetype:docx
   CV cardiology filetype:docx
   CV neurology filetype:docx        # ... radiology, psychiatry, pediatrics, surgery, etc.
   ```
   Use `CV`, not `"curriculum vitae"` (the latter returns mostly templates). Rich veins of
   real faculty `.docx`: `medschool.umaryland.edu/profiles/`,
   `som.cuanschutz.edu/FIMS/Content/faculty/<id>/`, `medschool.lsuhsc.edu/.../docs/`,
   `pediatrics.pitt.edu`, `med.uth.edu`. Keep only direct `.docx` links to a specific
   person's CV; reject anything whose name/URL says template/format/guide/example/sample/
   instructions/supplemental/posting/policy/syllabus/registration/form. Collect the
   survivors into a `urls.txt`, one per line.

2. **Download + validate + dedupe (scripted).**
   ```bash
   scripts/harvest_download.sh urls.txt data/sample_cvs/word/web_harvest web
   ```
   Keeps only real Word docs (ZIP magic + `word/document.xml`), skips HTTP failures /
   non-docx / exact content-hash dupes, and names survivors `webNNN.docx` continuing from
   what's already there. Re-running with the same URLs is a no-op (all dedupe). The dest
   dir must be gitignored (PII).

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
  - `_batch_runs/outputs/<cv>_quality.json` — the full quality-score breakdown
  - `_batch_runs/logs/<cv>.log` — full stdout/stderr
  - one row in `_batch_runs/summary.tsv`
  - one row in `_batch_runs/scores.tsv` — score, band, and the worst three penalties

Scoring is deterministic and costs nothing (no LLM calls), so it runs for every CV
rather than behind a flag. It reads the stage artifacts directly; the local CLI
pipeline never writes a `quality_score.json` of its own, which is why earlier batches
captured no scores at all (#435).

Treat a row whose run produced no WCM docx as an **upper bound**: with no docx to
inspect, both render dimensions award a flat half credit instead of penalising, so a
run that rendered nothing can out-score one that rendered something genuinely sparse.

To (re)score any local run whose stage artifacts are still on disk, without re-running
the pipeline:

```bash
PYTHONPATH=src python3 scripts/score_one.py src/unified_pipeline/outputs <cv> \
    _batch_runs/outputs/<cv>_wcm.docx --source <cv>.docx
```

`--source` is the original CV: it lets the lost-source-table cap run (the batch runner
passes it). Without it that one cap is skipped and the score can sit above the web app's.

Run the next group later with the same command — already-done CVs are skipped automatically.
To run a specific model: pass it as the 4th arg (a Bedrock model id, e.g. `us.anthropic.claude-haiku-4-5-20251001-v1:0`).

### Running the doctor alongside the batch

Add `--doctor` to also run the deterministic `run_doctor` over each run's stage artifacts:

```bash
scripts/run_corpus_batch.sh --doctor data/sample_cvs/word/web_harvest 25
```

Per CV it writes a row to `_batch_runs/doctor.tsv` (`date sha cv worst ERROR WARN INFO top_lints`)
and the full findings to `_batch_runs/doctor/<cv>.json`. Each `top_lints` item reads `lint:count (p~0.50 n=8)`:
the lint's hand-checked precision and sample size from `src/unified_pipeline/doctor/PRECISION.md`, or
`(p unmeasured)` (#819). No LLM cost — the doctor is deterministic
lints over the stage_* artifacts. Findings are WARN/INFO detection lints (missed_headers,
classified_unrendered, output_hygiene, dedup_drops, …); an ERROR means a crash- or critical-loss
class worth stopping for. To doctor a run outside the batch loop:

```bash
PYTHONPATH=src python3 scripts/doctor_one.py src/unified_pipeline/outputs <cv> <source.docx>
```

## Provenance & what gets tracked

So an output can always be traced to the code and model that made it:

- **`summary.tsv`** — one row per CV, columns:
  `date  sha  model  cv  exit  wcm_output  kb  sections  headers  entries  classified`.
  The `date`/`sha`/`model` stamp means a re-run on a newer `dev` is distinguishable from
  the old one.
- **`run_meta.jsonl`** — one line per invocation: started/finished, `sha`, `branch`,
  `model`, `input_dir`, `count`, `ran`, `skipped`, `failed`. The batch-level audit record.

If you're comparing runs across pipeline versions, group by `sha` — never mix outputs from
different SHAs into one gold/eval judgment without noting it.

## Batch ledger (issue #272)

Keep #272 as the running ledger: **one comment per batch** with date · CV range · count ·
`sha` · model · failures · overall coverage. That's the human-readable trail of what's been
run against what, alongside the machine-readable `run_meta.jsonl`.

## Inspecting results

```bash
column -t -s$'\t' <results_dir>/summary.tsv     # scan exit codes + entry counts
# rows with exit≠0 or wcm_output=— are failures; read the matching logs/<cv>.log
```
Spot-check a few `_wcm.docx` outputs in Word. Compare `entries` vs `classified` for large
drops (but note: headers/boilerplate aren't meant to classify, so <100% coverage isn't loss
by itself). For deeper diagnosis of a single run, use the `run-autopsy` skill on its uid.

## From run to gold

Running produces parse **candidates**, not verified gold. To grow the gold set, hand-verify
selected outputs and promote them into `src/unified_pipeline/outputs/gold_set/` (the
`segsnap_*` snapshots are the existing regression references). Verification is the separate,
expensive step — don't treat raw batch outputs as ground truth.
