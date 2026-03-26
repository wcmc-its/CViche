---
phase: 14-backend-service-layer
plan: 01
subsystem: api
tags: [fastapi, service-layer, error-handling, config, sqlalchemy]

# Dependency graph
requires:
  - phase: 13-security-hardening
    provides: "Security middleware, upload validation, error sanitization"
provides:
  - "app/errors.py: HTTPException factory functions with consistent {error, message} format"
  - "app/services/config_service.py: Centralized config constants with env var overrides"
  - "app/services/run_service.py: check_run_access single-point-of-truth"
  - "app/services/user_service.py: provision_user create-or-update for both auth methods"
  - "tests/test_service_layer.py: 21 regression tests covering ARCH-02 through ARCH-06"
affects: [14-02, 14-03]

# Tech tracking
tech-stack:
  added: []
  patterns: [service-layer-extraction, error-factory-pattern, env-var-config-override]

key-files:
  created:
    - web_interface/backend/app/errors.py
    - web_interface/backend/app/services/__init__.py
    - web_interface/backend/app/services/config_service.py
    - web_interface/backend/app/services/run_service.py
    - web_interface/backend/app/services/user_service.py
    - web_interface/backend/tests/test_service_layer.py
  modified: []

key-decisions:
  - "Error helpers return HTTPException (raise at call site) rather than raising internally, matching existing codebase convention"
  - "Config constants use int/float conversion at module load for fail-fast on invalid env vars"

patterns-established:
  - "Error factory pattern: all error responses use {error: code, message: text} envelope via app.errors"
  - "Service layer dependency arrow: api/ -> services/ -> models/ (services never import from api/)"
  - "Config override pattern: sensible defaults with CVICHE_* env var overrides"

requirements-completed: [ARCH-02, ARCH-03, ARCH-04, ARCH-06]

# Metrics
duration: 2min
completed: 2026-03-26
---

# Phase 14 Plan 01: Service Layer Foundation Summary

**Error helpers, config constants, run access check, and user provisioning extracted into standalone service modules with 21 regression tests**

## Performance

- **Duration:** 2 min
- **Started:** 2026-03-26T20:46:56Z
- **Completed:** 2026-03-26T20:49:37Z
- **Tasks:** 2
- **Files created:** 6

## Accomplishments
- Created errors.py with 8 HTTPException factory functions producing consistent structured format
- Centralized 7 hardcoded config values (session TTL, rate limits, upload size, cost estimation) with environment variable overrides
- Extracted check_run_access (duplicated in 3 route files) into single-source-of-truth service function
- Extracted user provisioning logic (duplicated between simple and SAML auth) into unified provision_user function
- 21 regression tests all passing, full existing suite (127 tests) unaffected

## Task Commits

Each task was committed atomically:

1. **Task 1: Create errors.py, config_service.py, and services package** - `e354d2d` (feat)
2. **Task 2: Create run_service.py, user_service.py, and test suite** - `41efac8` (feat)

## Files Created/Modified
- `web_interface/backend/app/errors.py` - HTTPException factories: not_found, bad_request, forbidden, rate_limited, validation_error, internal_error, conflict, unauthorized
- `web_interface/backend/app/services/__init__.py` - Service layer package marker with dependency-arrow docstring
- `web_interface/backend/app/services/config_service.py` - 7 centralized constants with CVICHE_* env var overrides
- `web_interface/backend/app/services/run_service.py` - check_run_access using error helpers instead of inline HTTPException
- `web_interface/backend/app/services/user_service.py` - provision_user with create/update/role-preservation logic
- `web_interface/backend/tests/test_service_layer.py` - 21 tests: 5 run access, 6 user provisioning, 4 config defaults, 6 error format

## Decisions Made
- Error helpers return HTTPException instances (caller raises) rather than raising internally -- this matches the existing pattern in the codebase where `_check_run_access` uses `raise HTTPException(...)` and keeps the service functions composable
- Config constants perform type conversion at module load time (not lazily) so invalid env vars fail fast at startup

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Service functions ready for Plan 02 (route handler consolidation: replace inline _check_run_access with service import)
- Service functions ready for Plan 03 (wire config_service constants into existing hardcoded locations)
- All 6 new files are importable and tested; no existing files were modified

## Self-Check: PASSED

All 7 files verified present. Both task commits (e354d2d, 41efac8) verified in git log.

---
*Phase: 14-backend-service-layer*
*Completed: 2026-03-26*
