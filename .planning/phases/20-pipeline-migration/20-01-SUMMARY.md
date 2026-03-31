---
phase: 20-pipeline-migration
plan: 01
subsystem: pipeline
tags: [llm-abstraction, openai, call_llm, parsers, migration]

# Dependency graph
requires:
  - phase: 19-abstraction-foundation
    provides: "call_llm() centralized client, get_stage_config(), YAML config system"
provides:
  - "10 parser files migrated to call_llm() (zero direct OpenAI imports)"
  - "Uniform parser_* stage naming convention for config resolution"
affects: [20-pipeline-migration plans 02-04, stage config YAML additions]

# Tech tracking
tech-stack:
  added: []
  patterns: ["call_llm(stage='parser_*') for all field extraction parsers"]

key-files:
  created: []
  modified:
    - src/unified_pipeline/parsers/certifications_parser.py
    - src/unified_pipeline/parsers/education_parser.py
    - src/unified_pipeline/parsers/grants_parser.py
    - src/unified_pipeline/parsers/honors_parser.py
    - src/unified_pipeline/parsers/licensure_parser.py
    - src/unified_pipeline/parsers/memberships_parser.py
    - src/unified_pipeline/parsers/mentoring_parser.py
    - src/unified_pipeline/parsers/positions_parser.py
    - src/unified_pipeline/parsers/publications_parser.py
    - src/unified_pipeline/parsers/service_parser.py

key-decisions:
  - "Kept module-level docstring references to GPT-4o-mini (describes purpose, not migration artifact)"
  - "Preserved publications_parser inline Step 2 comment (describes code structure, not migration)"
  - "Removed stale 'Log the EXACT prompt before API call' comments from 3 files (orphaned by migration)"

patterns-established:
  - "Pattern: call_llm(stage='parser_{type}') for all parser LLM calls"
  - "Pattern: result['content'] for response text, result['prompt_tokens'] etc. for usage"

requirements-completed: [LLM-03]

# Metrics
duration: 18min
completed: 2026-03-31
---

# Phase 20 Plan 01: Parser Migration Summary

**Migrated all 10 parser files from direct OpenAI SDK calls to centralized call_llm(), removing 325 lines of boilerplate (prompt logging, manual timing, hardcoded model strings)**

## Performance

- **Duration:** 18 min
- **Started:** 2026-03-31T22:02:07Z
- **Completed:** 2026-03-31T22:20:41Z
- **Tasks:** 1
- **Files modified:** 10

## Accomplishments
- All 10 parser files now use `call_llm()` exclusively with zero direct OpenAI imports
- Removed all `prompt_logger` imports and `log_prompt_before_call`/`log_prompt_response` calls
- Removed all hardcoded model strings (`gpt-4o-mini`) from parser code
- Established consistent `parser_*` stage naming convention for config resolution
- Net reduction of 325 lines (384 removed, 59 added) across 10 files

## Task Commits

Each task was committed atomically:

1. **Task 1: Migrate all 10 parser files from OpenAI SDK to call_llm()** - `43eb062` (feat)

## Files Created/Modified
- `src/unified_pipeline/parsers/certifications_parser.py` - Migrated to call_llm(stage="parser_certifications")
- `src/unified_pipeline/parsers/education_parser.py` - Migrated to call_llm(stage="parser_education")
- `src/unified_pipeline/parsers/grants_parser.py` - Migrated to call_llm(stage="parser_grants")
- `src/unified_pipeline/parsers/honors_parser.py` - Migrated to call_llm(stage="parser_honors")
- `src/unified_pipeline/parsers/licensure_parser.py` - Migrated to call_llm(stage="parser_licensure")
- `src/unified_pipeline/parsers/memberships_parser.py` - Migrated to call_llm(stage="parser_memberships")
- `src/unified_pipeline/parsers/mentoring_parser.py` - Migrated to call_llm(stage="parser_mentoring")
- `src/unified_pipeline/parsers/positions_parser.py` - Migrated to call_llm(stage="parser_positions")
- `src/unified_pipeline/parsers/publications_parser.py` - Migrated to call_llm(stage="parser_publications")
- `src/unified_pipeline/parsers/service_parser.py` - Migrated to call_llm(stage="parser_service")

## Decisions Made
- Kept module-level docstring references to GPT-4o-mini (describes historical purpose, not migration artifact -- plan says "Keep everything else unchanged")
- Preserved publications_parser "Step 2" inline comment (describes code structure)
- Removed 3 orphaned "Log the EXACT prompt" comments that referenced deleted code

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- Test suite shows 78 passed + 81 errors (database lifespan failures due to no MariaDB in worktree). This is pre-existing -- all 81 errors are in `conftest.py:73` TestClient setup, unrelated to parser changes. LLM abstraction tests (27/27) pass cleanly.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- All 10 parsers ready for config-driven model selection via llm_config.yaml
- Stage names (`parser_*`) established for plans 02-04 to reference when adding stage-specific config
- Pattern validated: identical mechanical migration can be applied to remaining pipeline stages

## Self-Check: PASSED

- All 10 parser files exist and are modified
- Commit 43eb062 verified in git log
- SUMMARY.md created at correct path

---
*Phase: 20-pipeline-migration*
*Completed: 2026-03-31*
