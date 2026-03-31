---
phase: 19-abstraction-foundation
plan: 03
subsystem: llm-client
tags: [openai, llm, retry, cost-tracking, provider-abstraction, centralized-client]

# Dependency graph
requires:
  - phase: 19-abstraction-foundation
    provides: YAML config system with get_stage_config() and calculate_cost() (Plan 01)
  - phase: 19-abstraction-foundation
    provides: LLMUsage provider column for cost tracking (Plan 02)
provides:
  - Centralized call_llm() function as single entry point for all LLM API calls
  - Normalized 9-key response dict (content, tokens, cost, model, provider, finish_reason, latency)
  - Built-in retry with exponential backoff on transient OpenAI errors
  - Lazy OpenAI client initialization (no import-time side effects)
  - Provider dispatch pattern (openai supported, bedrock placeholder for Phase 21)
affects: [20-pipeline-migration, 21-bedrock-provider]

# Tech tracking
tech-stack:
  added: []
  patterns: [lazy-client-init, exponential-backoff-retry, normalized-response-dict, kwargs-override-config]

key-files:
  created:
    - src/unified_pipeline/llm_client.py
  modified:
    - web_interface/backend/tests/test_llm_client.py

key-decisions:
  - "kwargs override config values (model, temperature, etc.) for per-call flexibility without changing YAML"
  - "Exponential backoff capped at 30s with retry_count from config (default 3, so 4 total attempts)"
  - "Extra kwargs filtered before forwarding to _call_openai to prevent duplicate argument errors"

patterns-established:
  - "call_llm(stage, messages, response_format, **kwargs) is the canonical LLM call interface"
  - "Lazy client init: _openai_client = None at module level, created on first call_llm()"
  - "Normalized response: 9-key dict (content, prompt_tokens, completion_tokens, total_tokens, cost, model, provider, finish_reason, latency_ms)"
  - "Provider dispatch: if/else on provider string, ValueError for unsupported providers"

requirements-completed: [LLM-01, LLM-02, LLM-04]

# Metrics
duration: 25min
completed: 2026-03-31
---

# Phase 19 Plan 03: Centralized LLM Client Summary

**call_llm() function with config-driven OpenAI dispatch, exponential backoff retry, and normalized 9-key response dict including cost and latency**

## Performance

- **Duration:** 25 min
- **Started:** 2026-03-31T17:36:47Z
- **Completed:** 2026-03-31T18:01:52Z
- **Tasks:** 2 (TDD: RED + GREEN)
- **Files modified:** 2

## Accomplishments
- Created centralized call_llm() function that resolves config from get_stage_config(), dispatches to OpenAI, retries on transient errors, and returns normalized response dict
- Lazy OpenAI client initialization prevents import-time auth errors (critical for test isolation and non-API contexts)
- 14 unit tests covering all behaviors: basic call, params, config override, normalized response, retry on/off, provider dispatch, lazy init, response_format passthrough, Plan 02 integration
- All 27 Phase 19 tests pass (13 config + 14 client)

## Task Commits

Each task was committed atomically:

1. **Task 1: Create test scaffold for LLM client (TDD RED)** - `26823de` (test)
2. **Task 2: Implement llm_client.py with call_llm() function (GREEN)** - `6e7da09` (feat)

_TDD flow: RED phase wrote 14 failing tests, GREEN phase implemented production code and fixed kwargs duplication bug to pass all tests._

## Files Created/Modified
- `src/unified_pipeline/llm_client.py` - Centralized LLM client with call_llm(), _call_with_retry(), _call_openai(), lazy _get_openai_client()
- `web_interface/backend/tests/test_llm_client.py` - 14 unit tests for call_llm function with mocked OpenAI

## Decisions Made
- kwargs override config values for per-call flexibility (e.g., call_llm("stage_2", msgs, model="gpt-4o") overrides config's gpt-4o-mini)
- Exponential backoff capped at 30s with retry_count from stage config (default 3 retries = 4 total attempts)
- Extra kwargs are filtered before forwarding to _call_openai to prevent Python "multiple values for argument" errors

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed kwargs duplication in _call_openai forwarding**
- **Found during:** Task 2 (GREEN phase)
- **Issue:** When kwargs contained model/temperature overrides, they were passed both as positional args and via **kwargs to _call_openai, causing TypeError "got multiple values for argument 'model'"
- **Fix:** Added extra_kwargs filtering that strips handled keys (provider, model, temperature, max_tokens, retry_count, stage) before forwarding to _call_openai
- **Files modified:** src/unified_pipeline/llm_client.py
- **Verification:** test_call_llm_kwargs_override passes
- **Committed in:** 6e7da09 (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Essential fix for correctness. The plan's implementation sketch had this latent bug; the TDD approach caught it immediately.

## Issues Encountered
None beyond the auto-fixed kwargs duplication bug.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- call_llm() is ready for Phase 20 pipeline migration (all 12 stages can switch from direct OpenAI calls to call_llm)
- Provider dispatch pattern is ready for Phase 21 Bedrock implementation (add "bedrock" branch in call_llm)
- All Phase 19 artifacts complete: YAML config (Plan 01), LLMUsage provider column (Plan 02), centralized client (Plan 03)
- 27 total Phase 19 tests provide regression safety for migration work

## Self-Check: PASSED

All files exist, all commits verified.

---
*Phase: 19-abstraction-foundation*
*Completed: 2026-03-31*
