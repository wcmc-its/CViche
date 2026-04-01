---
phase: 20-pipeline-migration
plan: 02
subsystem: pipeline
tags: [llm-abstraction, openai, call_llm, segmentation, migration, retry-removal]

# Dependency graph
requires:
  - phase: 19-abstraction-foundation
    provides: "call_llm() centralized client, get_stage_config(), YAML config system"
  - phase: 20-pipeline-migration
    plan: 01
    provides: "Parser migration pattern validated, call_llm(stage='parser_*') established"
provides:
  - "10 segmentation files migrated to call_llm() (zero direct OpenAI imports)"
  - "Hand-rolled retry loop removed from chunked_chat_hierarchy_extractor.py"
  - "Uniform segmentation_* stage naming convention for config resolution"
affects: [20-pipeline-migration plans 03-04, stage config YAML additions]

# Tech tracking
tech-stack:
  added: []
  patterns: ["call_llm(stage='segmentation_*') for all segmentation LLM calls"]

key-files:
  created: []
  modified:
    - src/unified_pipeline/segmentation/chat_completions_hierarchy_extractor.py
    - src/unified_pipeline/segmentation/chunked_chat_hierarchy_extractor.py
    - src/unified_pipeline/segmentation/entry_validator.py
    - src/unified_pipeline/segmentation/header_validator.py
    - src/unified_pipeline/segmentation/pdf_vision.py
    - src/unified_pipeline/segmentation/signature_based_segmentation.py
    - src/unified_pipeline/segmentation/stage2_entry_extractor.py
    - src/unified_pipeline/segmentation/word_chunked.py
    - src/unified_pipeline/segmentation/word_delimited.py
    - src/unified_pipeline/segmentation/word_original.py

key-decisions:
  - "Removed model parameters from function signatures since call_llm() resolves model from config"
  - "Replaced tiktoken encoding_for_model('gpt-4o') with get_encoding('o200k_base') to eliminate hardcoded model strings"
  - "Changed model-specific print statements to generic 'LLM' references"

patterns-established:
  - "Pattern: call_llm(stage='segmentation_{type}') for all segmentation LLM calls"
  - "Pattern: result['content'] for response text, result['cost'] for cost (replaces manual cost calculation)"

requirements-completed: [LLM-03]

# Metrics
duration: 15min
completed: 2026-04-01
---

# Phase 20 Plan 02: Segmentation Migration Summary

**Migrated all 10 segmentation files from direct OpenAI SDK calls to centralized call_llm(), removing 169 lines of boilerplate (retry loops, manual cost calculation, model-specific parameter handling)**

## Performance

- **Duration:** 15 min
- **Started:** 2026-04-01T07:17:00Z
- **Completed:** 2026-04-01T07:32:37Z
- **Tasks:** 1
- **Files modified:** 10

## Accomplishments
- All 10 segmentation files now use `call_llm()` exclusively with zero direct OpenAI imports
- Removed hand-rolled retry loop (5 retries + RateLimitError catch + exponential backoff) from chunked_chat_hierarchy_extractor.py -- call_llm() handles retries internally
- Removed all hardcoded model strings from migrated files (gpt-4o, gpt-4o-mini, gpt-5.1)
- Removed manual cost calculation code from 4 files (header_validator, entry_validator, stage2_entry_extractor, word_chunked) -- call_llm() handles cost tracking
- Removed model-specific parameter logic from word_chunked.py (gpt-5 max_completion_tokens vs max_tokens branching)
- Net reduction of 169 lines (301 removed, 132 added) across 10 files

## Task Commits

Each task was committed atomically:

1. **Task 1: Migrate 10 segmentation files from OpenAI SDK to call_llm()** - `ee23796` (feat)

## Files Created/Modified
- `src/unified_pipeline/segmentation/chat_completions_hierarchy_extractor.py` - Migrated to call_llm(stage="segmentation_chat_hierarchy")
- `src/unified_pipeline/segmentation/chunked_chat_hierarchy_extractor.py` - Migrated to call_llm(stage="segmentation_chunked_hierarchy"), retry loop removed
- `src/unified_pipeline/segmentation/entry_validator.py` - Migrated to call_llm(stage="segmentation_entry_validator")
- `src/unified_pipeline/segmentation/header_validator.py` - Migrated to call_llm(stage="segmentation_header_validator")
- `src/unified_pipeline/segmentation/pdf_vision.py` - Migrated to call_llm(stage="segmentation_pdf_vision"), 3 call sites
- `src/unified_pipeline/segmentation/signature_based_segmentation.py` - Migrated to call_llm(stage="segmentation_signature"), 3 call sites
- `src/unified_pipeline/segmentation/stage2_entry_extractor.py` - Migrated to call_llm(stage="segmentation_entry_extractor")
- `src/unified_pipeline/segmentation/word_chunked.py` - Migrated to call_llm(stage="segmentation_word_chunked"), complex model logic removed
- `src/unified_pipeline/segmentation/word_delimited.py` - Migrated to call_llm(stage="segmentation_word_delimited")
- `src/unified_pipeline/segmentation/word_original.py` - Migrated to call_llm(stage="segmentation_word_original")

## Decisions Made
- Removed `model` parameters from function signatures (get_hierarchy_from_text, get_cv_hierarchy, etc.) since call_llm() resolves model from YAML config. Callers no longer need to specify model.
- Replaced `tiktoken.encoding_for_model("gpt-4o")` with `tiktoken.get_encoding("o200k_base")` -- equivalent tokenizer without hardcoded model name.
- Replaced model-specific print statements ("Sending to GPT-4o", "GPT-5.1") with generic "LLM" references.
- In word_chunked.py, removed the complex model-specific parameter branching (gpt-5 uses max_completion_tokens, gpt-5-mini doesn't support temperature, etc.) -- call_llm() and its config handle all of this.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- Test suite shows 78 passed + 81 errors (database lifespan failures due to no MariaDB in worktree). This is pre-existing and identical to plan 01 results -- all 81 errors are in conftest.py TestClient setup, unrelated to segmentation changes.
- word_chunked_with_validation.py (not in the 10 migration files) still contains hardcoded model strings in its function parameter defaults. This file does not import openai directly; it delegates to word_chunked.py and entry_validator.py which are now migrated. It will be addressed in plan 03 or 04.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- All 10 segmentation files ready for config-driven model selection via llm_config.yaml
- Stage names (`segmentation_*`) established for plans 03-04 to reference when adding stage-specific config
- Two Assistants API exception files remain untouched as documented (D-05)
- Pattern validated for remaining pipeline migration (plans 03-04)

## Self-Check: PASSED

- All 10 modified segmentation files exist
- Commit ee23796 verified in git log
- SUMMARY.md created at correct path

---
*Phase: 20-pipeline-migration*
*Completed: 2026-04-01*
