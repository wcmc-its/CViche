---
phase: 09-ed-group-authorization
plan: 02
subsystem: auth
tags: [ldap, ed-group, saml-acs, per-request-auth, cache, role-sync, fastapi]

# Dependency graph
requires:
  - phase: 09-ed-group-authorization
    plan: 01
    provides: ed_group_lookup.py module with LDAP membership check, TTL cache, stale fallback
  - phase: 08-saml-sp-client-endpoints
    provides: SAML ACS handler, get_current_user dependency, LoginPage error map
provides:
  - ED group check wired into SAML ACS handler (login-time gating)
  - Per-request ED re-check in get_current_user with cache-first, stale fallback
  - Admin role sync from ED admin group at login and per-request
  - Frontend error messages for not_authorized and directory_unavailable
  - 7 integration tests for ACS and get_current_user ED wiring
affects: [phase-10-sso-button, admin-dashboard, deployment]

# Tech tracking
tech-stack:
  added: []
  patterns: [ACS-inline ED gating with redirect on denial, per-request cache-first membership revalidation, stale-on-error with 401 fallback, role sync on every request]

key-files:
  created: []
  modified:
    - web_interface/backend/app/api/saml_routes.py
    - web_interface/backend/app/auth.py
    - web_interface/frontend/src/components/LoginPage.tsx
    - web_interface/backend/tests/test_ed_group.py

key-decisions:
  - "ED check in ACS uses redirect-on-denial pattern (not_authorized, directory_unavailable) matching existing SAML error flow"
  - "Per-request check uses cache-first with live LDAP query on miss, stale fallback on ED outage"
  - "Role determined from ED membership: user_role is None when ED disabled (preserves existing role for non-ED users)"
  - "Simple mode users bypass ED check entirely in get_current_user (auth_method != saml)"

patterns-established:
  - "ED gating pattern: check after SAML assertion extraction, before JIT provisioning"
  - "Per-request revalidation: cache hit -> use cached, cache miss -> LDAP query, LDAP fail -> stale cache, no stale -> 401"
  - "Role sync: admin group membership checked at login and per-request, role updated on change"

requirements-completed: [ED-01, ED-02, ED-03]

# Metrics
duration: 4min
completed: 2026-03-25
---

# Phase 9 Plan 02: ED Group Authorization Wiring Summary

**ED group check wired into SAML ACS (login-time gating) and get_current_user (per-request cache-first revalidation) with admin role sync and frontend error messages**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-25T12:28:54Z
- **Completed:** 2026-03-25T12:33:03Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- Wired ED group membership check into SAML ACS handler: non-members denied with not_authorized redirect, ED failures with directory_unavailable redirect
- Added per-request ED revalidation in get_current_user with cache-first strategy, stale-on-error fallback, and 401 on removal
- Admin role set from ED admin group at login and synced on every request; simple mode users bypass ED check entirely
- Added 7 integration tests (4 ACS, 3 per-request) bringing test_ed_group.py to 18 tests and full suite to 51

## Task Commits

Each task was committed atomically:

1. **Task 1: Wire ED group check into SAML ACS handler and get_current_user dependency** - `a417bf5` (feat)
2. **Task 2: Add frontend error messages and integration tests for ED wiring** - `8553065` (feat)

## Files Created/Modified
- `web_interface/backend/app/api/saml_routes.py` - ED group check after SAML assertion, role determination from membership, modified JIT provisioning
- `web_interface/backend/app/auth.py` - Per-request ED re-check with cache/stale/LDAP fallback chain, role sync, not_authorized denial
- `web_interface/frontend/src/components/LoginPage.tsx` - Two new error messages in SAML_ERROR_MESSAGES map
- `web_interface/backend/tests/test_ed_group.py` - 7 integration tests: TestACSGroupCheck (4) and TestPerRequestCheck (3)

## Decisions Made
- ED check in ACS uses same redirect-on-denial pattern as existing SAML errors (consistent UX)
- When ED is disabled, user_role is None to preserve existing role rather than defaulting to "user" (avoids demoting existing admins)
- Per-request check only runs for auth_method="saml" users; simple mode users are completely unaffected
- ED LDAP credential check (ldap_url and bind_dn must be set) prevents cryptic LDAP errors when env vars are missing

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - ED LDAP credentials (ED_LDAP_URL, ED_LDAP_BIND_DN, ED_LDAP_BIND_PASSWORD) configured via environment variables when deployment is ready. No action needed for development.

## Next Phase Readiness
- Phase 9 complete: ED group authorization fully wired from SAML assertion through per-request validation
- Ready for Phase 10: SSO button and mode detection (LoginPage already handles all error states)
- Full test suite green: 51 tests, 0 failures
- All ED config keys seeded, all error paths tested, all cache strategies verified

## Self-Check: PASSED

All files exist. All commits verified.

---
*Phase: 09-ed-group-authorization*
*Completed: 2026-03-25*
