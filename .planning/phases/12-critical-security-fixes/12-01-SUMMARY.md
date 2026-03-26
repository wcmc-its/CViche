---
phase: 12-critical-security-fixes
plan: 01
subsystem: auth
tags: [saml, pysaml2, security, session-management, itsdangerous]

# Dependency graph
requires: []
provides:
  - Session secret enforcement with RuntimeError on missing CVICHE_SESSION_SECRET
  - SAML assertion signature validation via want_assertions_signed=True
  - "[SECURITY] prefixed WARNING logging for SAML signature failures at ACS"
  - Security regression tests for SEC-02 and SEC-03
affects: [12-critical-security-fixes]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Module-level RuntimeError for required env vars (fail-fast at import time)"
    - "[SECURITY] log prefix for security-relevant events at WARNING level"

key-files:
  created:
    - web_interface/backend/tests/test_security.py
  modified:
    - web_interface/backend/app/auth.py
    - web_interface/backend/app/saml_client.py
    - web_interface/backend/app/api/saml_routes.py
    - web_interface/backend/tests/conftest.py

key-decisions:
  - "Removed secrets import from auth.py since token_hex is only referenced in error message string"
  - "Used direct os.environ manipulation in tests instead of patch.dict(clear=True) due to reload interaction issues"
  - "Added extra test for non-signature errors to verify they still log at ERROR level (not WARNING)"

patterns-established:
  - "conftest.py sets CVICHE_SESSION_SECRET before any app imports to prevent RuntimeError in test environment"
  - "[SECURITY] prefix on WARNING logs for security-relevant failures (signature, auth)"

requirements-completed: [SEC-02, SEC-03]

# Metrics
duration: 3min
completed: 2026-03-26
---

# Phase 12 Plan 01: Critical Security Fixes Summary

**Session secret enforcement via RuntimeError and SAML signature validation via want_assertions_signed=True with [SECURITY] logging**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-26T12:42:03Z
- **Completed:** 2026-03-26T12:45:31Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments
- Application now refuses to start when CVICHE_SESSION_SECRET is not set, with actionable error message including generation command
- SAML assertions without valid IdP signature are rejected (want_assertions_signed=True)
- Signature validation failures at ACS are logged at WARNING with [SECURITY] prefix for operational alerting
- 6 new regression tests guard both SEC-02 and SEC-03 fixes
- Full test suite passes: 73 tests (67 existing + 6 new), 5 skipped

## Task Commits

Each task was committed atomically:

1. **Task 1: Enforce CVICHE_SESSION_SECRET and update test environment** - `7f37d3c` (fix)
2. **Task 2: Enable SAML signature validation, add [SECURITY] ACS logging, and add SEC-02/SEC-03 regression tests** - `5d98c27` (fix)

## Files Created/Modified
- `web_interface/backend/app/auth.py` - Replaced random-key fallback with RuntimeError, removed unused secrets import
- `web_interface/backend/app/saml_client.py` - Set want_assertions_signed=True in pysaml2 SP config
- `web_interface/backend/app/api/saml_routes.py` - Added [SECURITY] prefixed WARNING logging for signature failures in ACS handler
- `web_interface/backend/tests/conftest.py` - Added os.environ.setdefault for CVICHE_SESSION_SECRET before app imports
- `web_interface/backend/tests/test_security.py` - New file with TestSessionSecret (3 tests) and TestSamlSignature (3 tests)

## Decisions Made
- Removed `import secrets` from auth.py since `secrets.token_hex(32)` is only referenced in the error message string, not called
- Used direct `os.environ.pop`/restore pattern in tests instead of `patch.dict(clear=True)` which interfered with `importlib.reload` across the test process boundary
- Added an extra test (`test_non_signature_error_logs_at_error_level`) beyond plan spec to verify that the bifurcated logging correctly keeps ERROR level for non-signature exceptions

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed test_missing_secret_raises_runtime_error using direct env manipulation**
- **Found during:** Task 2 (test_security.py creation)
- **Issue:** Plan's `patch.dict(os.environ, env, clear=True)` pattern caused RuntimeError to escape `pytest.raises` context during `importlib.reload` due to how module-level code re-executes
- **Fix:** Switched to direct `os.environ.pop` with try/finally restore pattern
- **Files modified:** web_interface/backend/tests/test_security.py
- **Verification:** All 6 security tests pass
- **Committed in:** 5d98c27 (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Test implementation detail adjusted for correctness. No scope creep.

## Issues Encountered
None beyond the test pattern fix documented above.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- SEC-02 and SEC-03 are now enforced with regression tests
- Phase 12 Plan 02 (path traversal fix, SEC-01) can proceed independently
- conftest.py session secret is in place for all future test runs

## Self-Check: PASSED

All 5 created/modified files verified present. Both task commits (7f37d3c, 5d98c27) verified in git log.

---
*Phase: 12-critical-security-fixes*
*Completed: 2026-03-26*
