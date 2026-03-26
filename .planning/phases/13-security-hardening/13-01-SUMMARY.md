---
phase: 13-security-hardening
plan: 01
subsystem: security
tags: [fastapi, middleware, csp, cors, hsts, error-handling, security-headers]

# Dependency graph
requires:
  - phase: 12-critical-security-fixes
    provides: "CSRFMiddleware, _resolve_safe_path, [SECURITY] logging pattern"
provides:
  - "Global exception handler with sanitized error responses (SEC-05)"
  - "SecurityHeadersMiddleware with CSP, X-Frame-Options, HSTS, nosniff, Referrer-Policy (SEC-06)"
  - "CORS lockdown with explicit methods and headers lists (SEC-07)"
  - "_build_error_response() and _add_security_headers() helper functions"
affects: [13-02, frontend-csp-compliance]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "SecurityHeadersMiddleware pattern for response-level security headers"
    - "_build_error_response() shared between middleware and exception handler"
    - "CVICHE_DEBUG env var toggle for development traceback visibility"

key-files:
  created: []
  modified:
    - "web_interface/backend/app/main.py"
    - "web_interface/backend/app/api/runs.py"
    - "web_interface/backend/tests/test_security.py"

key-decisions:
  - "Exception handling in SecurityHeadersMiddleware dispatch instead of relying solely on @app.exception_handler -- Starlette 0.52+ BaseHTTPMiddleware.call_next() re-raises exceptions before FastAPI exception handlers"
  - "Extracted _build_error_response() and _add_security_headers() as module-level helpers for reuse across middleware and exception handler"

patterns-established:
  - "SecurityHeadersMiddleware: all responses get CSP, X-Frame-Options, HSTS, nosniff, Referrer-Policy"
  - "Error response sanitization: generic message in production, full traceback when CVICHE_DEBUG=true"

requirements-completed: [SEC-05, SEC-06, SEC-07]

# Metrics
duration: 4min
completed: 2026-03-26
---

# Phase 13 Plan 01: Error Sanitization, Security Headers & CORS Lockdown Summary

**Global exception handler with sanitized error responses, SecurityHeadersMiddleware adding 5 security headers to all responses, and CORS lockdown replacing wildcard methods/headers with explicit lists**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-26T19:38:34Z
- **Completed:** 2026-03-26T19:42:58Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments
- Unhandled exceptions return generic JSON with no stack traces or file paths; CVICHE_DEBUG=true enables traceback for development
- Every HTTP response includes Content-Security-Policy, X-Frame-Options (DENY), X-Content-Type-Options (nosniff), Strict-Transport-Security (no preload), and Referrer-Policy headers
- CORS allow_methods and allow_headers replaced with explicit lists -- no more wildcards
- HTTPException detail "Output directory not found" sanitized to "Run output not available"
- 17 new regression tests covering SEC-05, SEC-06, SEC-07 (31 total security tests, all passing)

## Task Commits

Each task was committed atomically:

1. **Task 1: Add global exception handler, SecurityHeadersMiddleware, and CORS lockdown** - `5e79bd8` (feat)
2. **Task 1 fix: Handle exceptions in SecurityHeadersMiddleware dispatch** - `2689054` (fix)
3. **Task 2: Add regression tests for SEC-05, SEC-06, SEC-07** - `46ebb43` (test)

## Files Created/Modified
- `web_interface/backend/app/main.py` - Global exception handler, SecurityHeadersMiddleware, CORS lockdown, helper functions
- `web_interface/backend/app/api/runs.py` - Sanitized HTTPException detail (line 351)
- `web_interface/backend/tests/test_security.py` - 17 new tests: TestErrorSanitization (4), TestSecurityHeaders (9), TestCorsLockdown (4)

## Decisions Made
- Exception handling placed in SecurityHeadersMiddleware.dispatch() to catch exceptions from call_next() -- Starlette 0.52+ re-raises exceptions through BaseHTTPMiddleware before FastAPI's @app.exception_handler can intercept them
- Extracted _build_error_response() and _add_security_headers() as module-level helpers for DRY reuse across middleware exception catch and the @app.exception_handler(Exception) registration
- CSP allows 'unsafe-inline' only for style-src (React/Vite inline styles) but NOT for script-src

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] BaseHTTPMiddleware.call_next() re-raises exceptions before FastAPI exception handler**
- **Found during:** Task 2 (regression test execution)
- **Issue:** In Starlette 0.52+, `BaseHTTPMiddleware.call_next()` re-raises exceptions from downstream routes. The `@app.exception_handler(Exception)` handler sits inside the middleware stack (at the ExceptionMiddleware layer) and never gets reached because the exception propagates outward through middleware dispatch before reaching it.
- **Fix:** Added try/except in `SecurityHeadersMiddleware.dispatch()` to catch exceptions from `call_next()` and produce sanitized error responses via `_build_error_response()`. Kept `@app.exception_handler(Exception)` as belt-and-suspenders. Extracted shared helpers to avoid code duplication.
- **Files modified:** `web_interface/backend/app/main.py`
- **Verification:** All 17 new tests pass including `test_unhandled_exception_returns_generic_error`
- **Committed in:** `2689054`

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Essential fix for correctness -- without it, unhandled exceptions would crash through middleware as 500 errors with full stack traces instead of returning sanitized JSON. No scope creep.

## Issues Encountered
None beyond the deviation documented above.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Security headers and error sanitization in place for all responses
- CORS locked down to explicit methods and headers
- Ready for Plan 02 (upload validation with magic bytes, SEC-04)
- All 31 security regression tests green

## Self-Check: PASSED

- All 3 modified files exist on disk
- All 3 task commits verified in git log (5e79bd8, 2689054, 46ebb43)
- 31/31 tests pass in test_security.py

---
*Phase: 13-security-hardening*
*Completed: 2026-03-26*
