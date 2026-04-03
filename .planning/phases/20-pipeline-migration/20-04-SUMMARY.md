---
phase: 20-pipeline-migration
plan: 04
subsystem: pipeline
tags: [llm-client, call_llm, migration, openai-abstraction]

# Dependency graph
requires:
  - phase: 20-03
    provides: Core files migrated, prompt_logger dict response support
  - phase: 19-03
    provides: call_llm() centralized client with retries and normalized response
provides:
  - All 46 pipeline files using call_llm() (excl. 3 documented exceptions)
  - Zero direct OpenAI imports in pipeline code
  - LLM-03 requirement complete
affects: [21-bedrock-provider]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "call_llm(stage=..., messages=...) replaces all direct OpenAI SDK calls"
    - "result['content'] replaces response.choices[0].message.content"
    - "result['cost'] replaces per-file calculate_cost() functions"

key-files:
  created: []
  modified:
    - src/unified_pipeline/stage_2_entry_extraction.py
    - src/unified_pipeline/stage_2a_delimiter_detector.py
    - src/unified_pipeline/stage_3a_header_taxonomy_mapper.py
    - src/unified_pipeline/stage_3b_entry_classifier.py
    - src/unified_pipeline/stage_4_field_extractor.py
    - src/unified_pipeline/stage_4_5_research_summary.py
    - src/unified_pipeline/stage_5b_institution_enrichment.py
    - src/unified_pipeline/stage_5c_teaching_formatter.py
    - src/unified_pipeline/stage_5d_citation_formatter.py
    - src/unified_pipeline/stage_6_word_template.py
    - src/unified_pipeline/cv_parser/data_structurer.py
    - src/unified_pipeline/cv_parser/llm_section_evaluator.py
    - src/unified_pipeline/cv_parser/phased_extractor.py
    - src/unified_pipeline/cv_parser/section_classifier.py
    - src/unified_pipeline/validators/llm_validator.py

key-decisions:
  - "Removed local MODEL_PRICING dict and calculate_cost() from stage_4_field_extractor.py -- call_llm returns cost"
  - "Removed try/except OpenAI import guards from stage_5b/5c/5d -- call_llm always available"
  - "Removed prompt_logger calls from all migrated files -- call_llm handles logging internally"
  - "llm_section_evaluator.py keeps client parameter in signature (deprecated, ignored) for backward compat"
  - "Removed _parse_validation_response, _parse_classification_response, _calculate_cost from llm_validator -- inline JSON parsing of call_llm response"

patterns-established:
  - "All pipeline LLM calls go through call_llm() with stage name for config resolution"
  - "Token/cost tracking comes from call_llm response dict, not per-file calculation"

requirements-completed: [LLM-03]

# Metrics
duration: 26min
completed: 2026-04-03
---

# Phase 20 Plan 04: Final Batch Migration + Audit Summary

**Migrated final 15 files to call_llm() and confirmed zero remaining direct OpenAI imports across the entire pipeline**

## Performance

- **Duration:** 26 min
- **Started:** 2026-04-03T13:53:36Z
- **Completed:** 2026-04-03T14:20:26Z
- **Tasks:** 2
- **Files modified:** 15

## Accomplishments
- Migrated 15 files (11 stage files, 4 cv_parser/validator files) to call_llm()
- Removed 580+ lines of redundant code (MODEL_PRICING dicts, local calculate_cost(), prompt_logger calls, try/except import guards, self.client initialization)
- Full migration audit confirms zero remaining direct OpenAI imports (excluding 3 documented exceptions + llm_client.py)
- 53 core tests pass with zero regressions

## Task Commits

Each task was committed atomically:

1. **Task 1: Migrate 15 files to call_llm()** - `e138694` (feat)
2. **Task 2: Migration audit** - verification only, no code changes

## Files Modified
- `src/unified_pipeline/stage_2_entry_extraction.py` - Replaced function-local OpenAI client, removed prompt_logger calls
- `src/unified_pipeline/stage_2a_delimiter_detector.py` - Replaced function-local client, removed prompt_logger and cost calc
- `src/unified_pipeline/stage_3a_header_taxonomy_mapper.py` - Replaced client + prompt_logger, removed hardcoded model params
- `src/unified_pipeline/stage_3b_entry_classifier.py` - Migrated 3 separate LLM call sites (classification, T-validation, fragment reconnection)
- `src/unified_pipeline/stage_4_field_extractor.py` - Removed MODULE_PRICING dict, calculate_cost(), module-level client, migrated 4 call sites
- `src/unified_pipeline/stage_4_5_research_summary.py` - Removed get_openai_client() helper, migrated 2 LLM calls
- `src/unified_pipeline/stage_5b_institution_enrichment.py` - Removed try/except import guard, local pricing dict, OPENAI_AVAILABLE flag
- `src/unified_pipeline/stage_5c_teaching_formatter.py` - Removed try/except guard, OPENAI_AVAILABLE flag, simplified call_llm_formatter
- `src/unified_pipeline/stage_5d_citation_formatter.py` - Removed try/except guard, OPENAI_AVAILABLE flag, simplified call_llm_formatter
- `src/unified_pipeline/stage_6_word_template.py` - Migrated 2 function-local imports in 7000+ line file
- `src/unified_pipeline/cv_parser/data_structurer.py` - Removed self.client, migrated 2 LLM calls
- `src/unified_pipeline/cv_parser/llm_section_evaluator.py` - Removed client parameter usage, migrated evaluate_section_header + evaluate_batch
- `src/unified_pipeline/cv_parser/phased_extractor.py` - Removed function-local OpenAI import deep in 1600+ line file
- `src/unified_pipeline/cv_parser/section_classifier.py` - Removed self.client, migrated _llm_classification
- `src/unified_pipeline/validators/llm_validator.py` - Removed openai import, self.client, _calculate_cost, dual API parsing methods

## Decisions Made
- Removed MODEL_PRICING and calculate_cost() from stage_4 per D-13 (call_llm returns cost)
- Removed OPENAI_AVAILABLE guards from stage_5b/5c/5d -- call_llm is always available in this codebase
- llm_section_evaluator.py keeps `client` parameter in signature but ignores it, for backward compat with callers
- llm_validator.py: Removed GPT-5 Responses API / Chat Completions API dual dispatch -- call_llm abstracts this

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Migration Audit Results (D-09)

```
Direct OpenAI imports remaining (excl. exceptions): 0 files -- PASS
Remaining client.chat.completions usage (excl. exceptions): 0 files -- PASS
Local MODEL_PRICING dicts remaining: 0 files -- PASS
call_llm adoption count: 45 files

Documented exceptions (expected):
  - llm_client.py (the centralized client itself)
  - async_rate_limiter.py (AsyncOpenAI for concurrency)
  - assistants_hierarchy_extractor.py (Assistants API)
  - direct_file_hierarchy_extractor.py (Assistants API)

Test suite: 53 passed (LLM client: 14, LLM config: 11, service layer: 26+)
```

## Next Phase Readiness
- LLM-03 requirement complete: all pipeline files use call_llm()
- Phase 20 complete: all 4 plans executed
- Ready for Phase 21: Bedrock Provider & Validation

## Self-Check: PASSED

---
*Phase: 20-pipeline-migration*
*Completed: 2026-04-03*
