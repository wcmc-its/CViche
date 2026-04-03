---
plan: 20-03
status: complete
started: 2025-03-31
completed: 2025-03-31
---

## Summary

Migrated 8 core files from direct OpenAI SDK calls to `call_llm()` and updated `prompt_logger.py` to handle dict responses (9 files modified total).

## What was built

- **prompt_logger.py**: Added `isinstance(response, dict)` branch to `log_prompt_response()` for dual compatibility (dict from `call_llm()` + OpenAI objects from exception files)
- **4 simpler core files**: `candidate_surfacer.py`, `extraction_recovery.py`, `personal_info_extractor.py`, `taxonomy_mapper.py` — Pattern 1 migration
- **taxonomy_mapper_v2.py**: Most complex file — 4 call sites migrated, local `MODEL_PRICING` dict and `calculate_cost()` function removed entirely
- **prompt_ab_tester.py**: Tooling file migrated with kwargs passthrough for parameter replay
- **repair_segmentation.py**: Pattern 2 (function-local import) migrated
- **section_extraction_orchestrator.py**: Pattern 2 (function-local import in class method) migrated

## Decisions

- D-01: `prompt_logger.py` keeps backward compat for exception files that still pass OpenAI objects
- D-02: `taxonomy_mapper_v2.py` local pricing removed — `call_llm()` returns cost directly

## Verification

- Zero OpenAI imports in core files (excluding `async_rate_limiter.py`)
- All 9 core files contain `from unified_pipeline.llm_client import call_llm`
- `prompt_logger.py` has `isinstance(response, dict)` check at line 196
- LLM client tests: 14 passed

## Self-Check: PASSED

## Key files

### key-files.modified
- src/unified_pipeline/core/prompt_logger.py
- src/unified_pipeline/core/candidate_surfacer.py
- src/unified_pipeline/core/extraction_recovery.py
- src/unified_pipeline/core/personal_info_extractor.py
- src/unified_pipeline/core/taxonomy_mapper.py
- src/unified_pipeline/core/taxonomy_mapper_v2.py
- src/unified_pipeline/core/prompt_ab_tester.py
- src/unified_pipeline/core/repair_segmentation.py
- src/unified_pipeline/core/section_extraction_orchestrator.py
