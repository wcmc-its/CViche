---
phase: 19-abstraction-foundation
plan: 01
subsystem: config
tags: [yaml, llm, multi-provider, pricing, config-resolution]

# Dependency graph
requires:
  - phase: 18-documentation
    provides: stable codebase with documented architecture
provides:
  - YAML-based LLM deployment config with global defaults and per-stage overrides
  - get_stage_config() 4-layer resolution function (defaults -> YAML -> stage overrides -> env vars)
  - Nested PRICING dict by provider with PRICING_FLAT backward compat alias
  - calculate_cost() with provider parameter (backward compatible)
  - reload_config() for cache clearing
affects: [19-02, 19-03, 20-pipeline-migration]

# Tech tracking
tech-stack:
  added: [pyyaml]
  patterns: [yaml-config-with-env-override, nested-pricing-with-flat-alias, cached-config-loading]

key-files:
  created:
    - src/unified_pipeline/config/llm_config.yaml
    - web_interface/backend/tests/test_llm_config.py
  modified:
    - src/unified_pipeline/config.py

key-decisions:
  - "Env vars override global defaults but NOT stage-specific YAML overrides -- preserves deployment intent"
  - "PRICING_FLAT = PRICING['openai'] provides zero-change backward compat for existing pipeline code"
  - "Config cached at module level with reload_config() for test isolation"

patterns-established:
  - "4-layer config resolution: hardcoded defaults -> YAML defaults -> YAML stage overrides -> env vars (non-stage keys only)"
  - "CVICHE_LLM_PROVIDER and CVICHE_LLM_MODEL env var naming convention"
  - "Nested PRICING[provider][model] with flat alias for backward compat"

requirements-completed: [CFG-01, CFG-02, CFG-03]

# Metrics
duration: 9min
completed: 2026-03-31
---

# Phase 19 Plan 01: YAML Config System Summary

**YAML-based LLM deployment config with 4-layer resolution (defaults, YAML globals, stage overrides, env vars) and nested multi-provider PRICING dict**

## Performance

- **Duration:** 9 min
- **Started:** 2026-03-31T17:04:11Z
- **Completed:** 2026-03-31T17:13:34Z
- **Tasks:** 2 (TDD: RED + GREEN)
- **Files modified:** 3

## Accomplishments
- Created llm_config.yaml with default provider=openai, model=gpt-4o-mini and stage_4/stage_4_5 overrides to gpt-4o
- Implemented get_stage_config() resolving 4-layer config correctly (hardcoded defaults, YAML defaults, YAML stage overrides, env vars for non-stage-overridden keys)
- Restructured PRICING to nested provider->model format with PRICING_FLAT backward-compatible alias
- Updated calculate_cost() to accept provider parameter while remaining backward compatible
- 13 unit tests covering all CFG requirements pass

## Task Commits

Each task was committed atomically:

1. **Task 1: Create test scaffold for config system (TDD RED)** - `6ddcbbe` (test)
2. **Task 2: Implement config system and PRICING restructure (TDD GREEN)** - `e8654ed` (feat)

_TDD flow: RED phase wrote 13 failing tests, GREEN phase implemented all production code to pass them._

## Files Created/Modified
- `src/unified_pipeline/config/llm_config.yaml` - Deployment config with default block and stage overrides
- `src/unified_pipeline/config.py` - Restructured PRICING, added get_stage_config(), reload_config(), _load_yaml_config()
- `web_interface/backend/tests/test_llm_config.py` - 13 unit tests for config loading, stage overrides, fallback, env vars, pricing, calculate_cost

## Decisions Made
- Env vars (CVICHE_LLM_PROVIDER, CVICHE_LLM_MODEL) override global defaults but NOT stage-specific YAML overrides -- this preserves deployment intent when an admin explicitly configures a stage
- PRICING_FLAT = PRICING["openai"] provides zero-change backward compatibility for all 46+ existing pipeline files that reference the flat PRICING dict
- Config is cached at module level with reload_config() for test isolation; avoids re-reading YAML on every pipeline stage call

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Config system ready for Plan 02 (LLMUsage provider column, Alembic migration) and Plan 03 (centralized call_llm())
- get_stage_config() provides the provider/model/temperature that call_llm() will consume
- PRICING nested structure ready for Bedrock pricing entries in Phase 21
- All existing pipeline constants preserved (DEFAULT_MODEL, SEGMENTATION_MODEL, etc.) -- Phase 20 will migrate files

## Self-Check: PASSED

All files exist, all commits verified.

---
*Phase: 19-abstraction-foundation*
*Completed: 2026-03-31*
