---
phase: 14-backend-service-layer
plan: 02
subsystem: api
tags: [fastapi, service-layer, refactoring, error-handling, config]

# Dependency graph
requires:
  - phase: 14-backend-service-layer/01
    provides: "Service layer foundation (run_service, user_service, config_service, errors.py)"
provides:
  - "Thin route handlers in all non-admin API files importing from services"
  - "Zero duplicated _check_run_access definitions"
  - "Consistent structured error format across all non-security routes"
  - "Single provision_user call for both simple and SAML auth flows"
  - "Environment-overridable config constants in all route files"
affects: [14-backend-service-layer/03, admin-routes]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Service import pattern: route handlers import from app.services.* and app.errors"
    - "Config alias pattern: auth.py imports SESSION_TTL with alias to preserve re-export name"
    - "WebSocket access check pattern: catch HTTPException and translate to WS close codes"

key-files:
  created: []
  modified:
    - "web_interface/backend/app/api/runs.py"
    - "web_interface/backend/app/api/feedback_routes.py"
    - "web_interface/backend/app/api/steps.py"
    - "web_interface/backend/app/api/websocket.py"
    - "web_interface/backend/app/api/auth_routes.py"
    - "web_interface/backend/app/api/saml_routes.py"
    - "web_interface/backend/app/api/upload.py"
    - "web_interface/backend/app/api/consent_routes.py"
    - "web_interface/backend/app/auth.py"
    - "web_interface/backend/tests/test_security.py"

key-decisions:
  - "Left _resolve_safe_path errors as plain strings (security opacity by design)"
  - "Used config_service alias import in auth.py to preserve SESSION_TTL re-export name"
  - "WebSocket access check catches HTTPException and translates to WS close codes"

patterns-established:
  - "Route handlers import check_run_access from app.services.run_service (not local copy)"
  - "User provisioning always via provision_user() from app.services.user_service"
  - "Error responses use app.errors factory functions (bad_request, not_found, etc.)"
  - "Config constants imported from app.services.config_service"

requirements-completed: [ARCH-01, ARCH-06]

# Metrics
duration: 14min
completed: 2026-03-26
---

# Phase 14 Plan 02: Route Handler Service Wiring Summary

**Wired all non-admin route handlers to use service layer: check_run_access, provision_user, config_service constants, and structured error factories**

## Performance

- **Duration:** 14 min
- **Started:** 2026-03-26T23:03:36Z
- **Completed:** 2026-03-26T23:18:29Z
- **Tasks:** 2
- **Files modified:** 10

## Accomplishments
- Eliminated 3 duplicated _check_run_access functions from runs.py, feedback_routes.py, and steps.py; all now import from run_service
- Replaced 2 inline user create-or-update blocks (auth_routes.py, saml_routes.py) with single provision_user() call
- Replaced 6 hardcoded constants (rate limits, upload size, cost/time rates, session TTL) with config_service imports
- Converted all plain-string HTTPException details to structured error format using error helpers (except security-opaque _resolve_safe_path errors)
- WebSocket access check now uses service function with HTTPException-to-WS-close-code translation

## Task Commits

Each task was committed atomically:

1. **Task 1: Wire run_service into runs.py, feedback_routes.py, steps.py, websocket.py** - `4fca346` (feat)
2. **Task 2: Wire user_service and config_service into auth, SAML, upload, and consent routes** - `9ca0b84` (feat)

## Files Created/Modified
- `web_interface/backend/app/api/runs.py` - Removed local _check_run_access, import from services, structured errors
- `web_interface/backend/app/api/feedback_routes.py` - Removed local _check_run_access, import from services
- `web_interface/backend/app/api/steps.py` - Removed local _check_run_access, structured errors for non-security paths
- `web_interface/backend/app/api/websocket.py` - Service-based access check with HTTPException catch
- `web_interface/backend/app/api/auth_routes.py` - provision_user, config_service rate limit constants
- `web_interface/backend/app/api/saml_routes.py` - provision_user for JIT provisioning
- `web_interface/backend/app/api/upload.py` - config_service constants, error helpers for all validation
- `web_interface/backend/app/api/consent_routes.py` - validation_error helper for submission type check
- `web_interface/backend/app/auth.py` - SESSION_TTL from config_service (alias pattern)
- `web_interface/backend/tests/test_security.py` - Updated assertions for structured error format

## Decisions Made
- Left _resolve_safe_path errors as plain strings deliberately -- these are security-opaque responses where the actual detail is logged with [SECURITY] prefix
- Used alias import pattern for SESSION_TTL in auth.py (`from config_service import SESSION_TTL as _CFG_SESSION_TTL`) to preserve the existing `SESSION_TTL` name that other modules import from `app.auth`
- WebSocket handler catches HTTPException from check_run_access and translates to appropriate WS close codes (1008 for not found, 4003 for access denied)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated test assertions for structured error format**
- **Found during:** Task 2 (upload.py error format changes)
- **Issue:** 4 test assertions in test_security.py checked `response.json()["detail"]` as a string, but errors are now structured dicts with `{"error": ..., "message": ...}`
- **Fix:** Changed assertions to check `response.json()["detail"]["message"]` instead
- **Files modified:** web_interface/backend/tests/test_security.py
- **Verification:** All 132 tests pass after fix
- **Committed in:** 9ca0b84 (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug fix)
**Impact on plan:** Test assertion update was necessary consequence of the error format change. No scope creep.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- All non-admin route handlers now use the service layer
- Plan 03 (admin routes wiring) can proceed -- same pattern applies to admin_routes.py
- Error format is now consistent across all non-admin, non-security endpoints

## Self-Check: PASSED

All 10 modified files verified present. Both task commits (4fca346, 9ca0b84) verified in git log. SUMMARY.md created.

---
*Phase: 14-backend-service-layer*
*Completed: 2026-03-26*
