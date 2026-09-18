# CLAUDE.md

Project-level guidance for Claude Code sessions in this repo. Personal
preferences go in `CLAUDE.local.md` (gitignored), not here.

## Start here

- The default branch is `dev`. Base every PR on `origin/dev`, never on another
  feature branch. `main` is a release pointer and lags `dev` badly; its
  committed docs are stale.
- Read `docs/DEV_WORKFLOW.md` (branching, merging, corpus batches, PII),
  `docs/INVESTIGATING.md` (measurement landmines), and
  `docs/CODING_STANDARDS.md` (the code standard) before starting.
- Before opening a PR, answer the "What a PR description must contain"
  checklist in `docs/CODING_STANDARDS.md`. CI runs `scripts/check_standards.py`
  against `scripts/standards-baseline.json`; a ratchet row may fall, never rise.
- One test file per source module. New tests go into the existing
  `test_<module>.py`; a new test file is for a new source module only.

## Two pipeline drivers

`run_full_pipeline.py` (CLI) and
`web_interface/backend/app/pipeline/orchestrator.py` (web) are independent
drivers over the same `stage_*` modules. The orchestrator never imports the
CLI. Only a fix inside a `stage_*` module reaches both.

Their error handling is opposite: the CLI logs `Warning: Stage N failed` and
continues; the orchestrator raises and fails the run. A clean CLI or
corpus-batch run is not evidence the web path works.

## Stage stdout is a parsed contract

Two consumers regex the stages' progress output:

- `orchestrator.py` captures stdout under `redirect_stdout` and matches
  `(Processing|Extracting|Mapping|Classifying|Enriching) ... N of M` to drive
  the progress bar.
- `scripts/run_corpus_batch.sh` greps `Top-level sections:`,
  `Total headers:`, `Entries extracted:`, `Entries classified:` out of the CLI
  log to build `summary.tsv`.

`src/unified_pipeline/tests/test_run_full_pipeline_stdout_contract.py` pins
the four batch literals. The progress regexes have no test: rewording a stage
print silently blanks the web progress bar.

## `step_registry.py` metadata drifts

`web_interface/backend/app/pipeline/step_registry.py` is hand-maintained.
Stage 2 is `uses_llm=False` but `stage_2_entry_extraction.py` imports and
calls `call_llm`. Do not use the registry to reason about which stages cost
LLM calls; read the stage module.

## Stage 6 drops unnamed fields

Section renderers under `src/unified_pipeline/stage6/sections/` are fixed-slot
`fields.get(...)` enumerations. A stage-4 field the renderer does not name is
dropped with no warning. Before adding a stage-4 field, grep `stage6/` for it
and check what each hit actually does: a match inside
`stage6/render_check.py`'s `_IDENTIFYING_FIELDS` feeds dedup, not output.
