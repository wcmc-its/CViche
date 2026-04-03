---
phase: 21-bedrock-provider-validation
plan: 02
subsystem: testing
tags: [pytest, bedrock-tests, e2e-tests, json-validation, mock-boto3, unit-tests]

# Dependency graph
requires:
  - phase: 21-bedrock-provider-validation
    plan: 01
    provides: _call_bedrock(), _get_bedrock_client(), STOP_REASON_MAP, BEDROCK_RETRYABLE_CODES, _validate_json_response()
  - phase: 19-abstraction-foundation
    provides: call_llm() abstraction, test_llm_client.py test suite
provides:
  - 14 Bedrock unit tests covering dispatch, params, retry, stop reason mapping
  - 3 JSON validation tests covering passthrough, retry, and fallback
  - 1 E2E pipeline smoke test with pytest.mark.e2e marker
  - pytest e2e marker registration in conftest.py
  - Fix for _call_with_retry non-retryable ClientError handling
affects: [deployment, documentation]

# Tech tracking
tech-stack:
  added: []
  patterns: [e2e-marker-deselection, bedrock-mock-pattern, lazy-init-testing]

key-files:
  created: []
  modified:
    - web_interface/backend/tests/test_llm_client.py
    - web_interface/backend/tests/conftest.py
    - src/unified_pipeline/llm_client.py

key-decisions:
  - "Patch boto3.client at package level for lazy init test (not unified_pipeline.llm_client.boto3 which does not exist)"
  - "E2E test uses pytest.skip for missing API key rather than skipIf decorator for clearer skip messages"
  - "Fixed _call_with_retry to check ClientError error codes before retrying -- AccessDeniedException was being incorrectly retried"

patterns-established:
  - "Bedrock mock pattern: patch _get_bedrock_client and return MagicMock with converse.return_value set to _make_bedrock_response()"
  - "E2E marker pattern: @pytest.mark.e2e for tests that require API keys, deselected by default"
  - "Lazy init testing: patch boto3.client at package level for import-lazy modules"

requirements-completed: [TEST-01, TEST-02]

# Metrics
duration: 4min
completed: 2026-04-03
---

# Phase 21 Plan 02: Bedrock Test Coverage Summary

**14 Bedrock unit tests + 3 JSON validation tests + E2E pipeline smoke test with e2e marker, plus non-retryable ClientError bug fix**

## Performance

- **Duration:** 4 min
- **Started:** 2026-04-03T15:42:41Z
- **Completed:** 2026-04-03T15:46:50Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments
- Added 14 new Bedrock/JSON unit tests covering: dispatch, params, temperature, kwargs override, system message separation, stop reason mapping, throttle retry, retry exhaustion, access denied no-retry, JSON passthrough, JSON retry, JSON fallback, lazy init, and normalized response
- Added E2E pipeline smoke test gated behind @pytest.mark.e2e marker, deselected from regular test runs
- Fixed bug where _call_with_retry retried non-retryable Bedrock ClientErrors (AccessDeniedException)
- All 41 LLM tests pass (28 llm_client + 13 llm_config), E2E deselected by default

## Task Commits

Each task was committed atomically:

1. **Task 1: Add Bedrock unit tests and JSON validation tests** - `fd6126a` (test)
2. **Task 2: Add E2E pipeline smoke test with pytest.mark.e2e marker** - `13b4541` (test)

## Files Created/Modified
- `web_interface/backend/tests/test_llm_client.py` - Added 14 Bedrock tests, 1 E2E test, _make_bedrock_response helper, updated _bedrock_config and reset_state fixture
- `web_interface/backend/tests/conftest.py` - Added pytest_configure with e2e marker registration
- `src/unified_pipeline/llm_client.py` - Fixed _call_with_retry to check ClientError error codes before retrying

## Decisions Made
- Patched `boto3.client` at package level for lazy init test because boto3 is imported lazily inside `_get_bedrock_client()` and does not exist as an attribute on the llm_client module
- E2E test uses `pytest.skip()` with descriptive message when OPENAI_API_KEY is not set, rather than a decorator, for clearer output
- Fixed the retry logic bug inline (Rule 1) rather than deferring, since the access-denied-no-retry test was directly affected

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed _call_with_retry retrying non-retryable Bedrock ClientErrors**
- **Found during:** Task 1 (test_call_llm_bedrock_no_retry_on_access_denied)
- **Issue:** `_BotoClientError` is in `RETRYABLE_ERRORS` tuple, so ALL `ClientError` exceptions were retried by `_call_with_retry`, including non-retryable ones like `AccessDeniedException`. The `_call_bedrock` function re-raises both retryable and non-retryable errors identically.
- **Fix:** Added check in `_call_with_retry` to inspect `ClientError.response["Error"]["Code"]` against `BEDROCK_RETRYABLE_CODES` before retrying. Non-retryable codes are re-raised immediately.
- **Files modified:** `src/unified_pipeline/llm_client.py`
- **Verification:** `test_call_llm_bedrock_no_retry_on_access_denied` passes with `converse.call_count == 1`
- **Committed in:** `fd6126a` (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Necessary fix -- the retry logic had a correctness bug where non-retryable errors would be retried. No scope creep.

## Issues Encountered
None

## User Setup Required
None - all tests use mocks. E2E test requires OPENAI_API_KEY but gracefully skips when absent.

## Next Phase Readiness
- Phase 21 complete: Bedrock provider implementation and test coverage both done
- v1.5 milestone complete: All 3 phases (19-21) delivered
- Ready for deployment: OpenAI path unchanged, Bedrock available via config switch

---
*Phase: 21-bedrock-provider-validation*
*Completed: 2026-04-03*
