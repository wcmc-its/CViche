---
phase: 21-bedrock-provider-validation
plan: 01
subsystem: api
tags: [aws-bedrock, boto3, converse-api, llm-provider, multi-provider]

# Dependency graph
requires:
  - phase: 19-abstraction-foundation
    provides: call_llm() abstraction layer, PRICING dict, get_stage_config()
  - phase: 20-pipeline-migration
    provides: all 46 pipeline files using call_llm()
provides:
  - _call_bedrock() function using AWS Converse API
  - Bedrock pricing for 9 models (Claude, Llama, Mistral)
  - JSON validation with retry for Bedrock responses
  - Message translation from OpenAI format to Converse format
  - boto3 dependency in requirements.txt
affects: [21-02-PLAN, testing, deployment]

# Tech tracking
tech-stack:
  added: [boto3, botocore, aws-bedrock-converse-api]
  patterns: [lazy-boto3-import, guarded-botocore-import, prompt-injection-for-json, json-validation-retry]

key-files:
  created: []
  modified:
    - src/unified_pipeline/llm_client.py
    - src/unified_pipeline/config.py
    - requirements.txt
    - web_interface/backend/tests/test_llm_client.py

key-decisions:
  - "Lazy boto3 import inside _get_bedrock_client() to avoid ImportError when only OpenAI is used"
  - "Guarded botocore.ClientError import at module level with type(None) fallback for RETRYABLE_ERRORS tuple"
  - "JSON validation retry appends user message with stronger hint rather than modifying system prompt"

patterns-established:
  - "Lazy provider imports: provider SDKs imported inside getter functions, not at module level"
  - "Guarded imports for RETRYABLE_ERRORS: try/except with type(None) fallback keeps tuple valid when dependency missing"
  - "JSON validation retry: single retry with accumulated token usage on invalid JSON responses"

requirements-completed: [BED-01, BED-02]

# Metrics
duration: 3min
completed: 2026-04-03
---

# Phase 21 Plan 01: Bedrock Provider Implementation Summary

**AWS Bedrock provider with Converse API, 9-model pricing, lazy boto3 init, and JSON validation retry inside call_llm() abstraction**

## Performance

- **Duration:** 3 min
- **Started:** 2026-04-03T15:35:24Z
- **Completed:** 2026-04-03T15:38:30Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- Populated PRICING["bedrock"] with real on-demand pricing for 9 Bedrock models across 3 providers (Anthropic Claude, Meta Llama, Mistral)
- Implemented _call_bedrock() using AWS Converse API with message translation, JSON validation+retry, and response normalization to same 9-key dict as OpenAI
- Added lazy boto3 client initialization with default credential chain (env vars, credentials file, or IAM instance roles)
- All 27 existing tests pass without regression

## Task Commits

Each task was committed atomically:

1. **Task 1: Populate Bedrock pricing and add boto3 dependency** - `90c8649` (feat)
2. **Task 2: Implement _call_bedrock() with Converse API, JSON validation, and response normalization** - `d8e9468` (feat)

## Files Created/Modified
- `src/unified_pipeline/config.py` - Added 9 Bedrock model pricing entries to PRICING dict
- `src/unified_pipeline/llm_client.py` - Added _get_bedrock_client, _translate_messages, _validate_json_response, _call_bedrock, STOP_REASON_MAP, BEDROCK_RETRYABLE_CODES, and Bedrock dispatch in call_llm()
- `requirements.txt` - Added boto3>=1.35.0 dependency
- `web_interface/backend/tests/test_llm_client.py` - Updated unsupported provider test to use "azure" instead of "bedrock"

## Decisions Made
- Lazy boto3 import inside _get_bedrock_client() prevents ImportError when only OpenAI is used -- matching the plan's Pitfall 4 guidance
- Guarded botocore.ClientError import at module level with type(None) fallback keeps RETRYABLE_ERRORS tuple functional regardless of boto3 installation
- JSON validation retry appends a user-role message with stronger hint rather than modifying system prompt, accumulating token usage from both attempts

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated test_call_llm_unsupported_provider to use truly unsupported provider**
- **Found during:** Task 2 (verification)
- **Issue:** Existing test used "bedrock" as the unsupported provider, which now IS supported
- **Fix:** Changed test to use "azure" as the unsupported provider name
- **Files modified:** web_interface/backend/tests/test_llm_client.py
- **Verification:** All 27 tests pass
- **Committed in:** d8e9468 (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Necessary fix -- test was asserting behavior that we explicitly changed. No scope creep.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required. AWS credentials are only needed when provider is set to "bedrock" in deployment config.

## Next Phase Readiness
- Bedrock provider implementation complete, ready for Plan 02 (test coverage)
- _bedrock_client module variable needs reset in test fixtures (noted in Plan 02)
- All existing OpenAI-path tests pass, confirming no regression

## Self-Check: PASSED

All files exist, all commits verified.

---
*Phase: 21-bedrock-provider-validation*
*Completed: 2026-04-03*
