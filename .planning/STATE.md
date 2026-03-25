---
gsd_state_version: 1.0
milestone: v1.2
milestone_name: Auth & Access Control
status: completed
stopped_at: Phase 10 context gathered
last_updated: "2026-03-25T12:49:47.759Z"
last_activity: 2026-03-25 -- Executed 09-02 ED group authorization wiring
progress:
  total_phases: 5
  completed_phases: 3
  total_plans: 5
  completed_plans: 5
  percent: 100
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-24)

**Core value:** Faculty can upload a CV and get back a correctly formatted WCM document without manual reformatting
**Current focus:** Phase 9 complete -- ED Group Authorization fully wired; ready for Phase 10

## Current Position

Phase: 9 of 11 (ED Group Authorization) -- COMPLETE
Plan: 2 of 2 in current phase (All plans complete)
Status: Phase 9 complete, ready for Phase 10
Last activity: 2026-03-25 -- Executed 09-02 ED group authorization wiring

Progress: [██████████] 100%

## Performance Metrics

**Velocity:**
- Total plans completed: 18 (v1.0: 9, v1.1: 4, v1.2: 5)
- Average duration: --
- Total execution time: --

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1 | 2 | -- | -- |
| 2 | 4 | -- | -- |
| 3 | 3 | -- | -- |
| Phase 04 P01 | 3min | 1 tasks | 1 files |
| Phase 04 P02 | 35min | 3 tasks | 2 files |
| Phase 05 P01 | N/A | 2 tasks | 1 files |
| Phase 06 P01 | 8min | 3 tasks | 4 files |
| Phase 07 P01 | 6min | 3 tasks | 9 files |
| Phase 08 P01 | 4min | 2 tasks | 6 files |
| Phase 08 P02 | 4min | 3 tasks | 4 files |
| Phase 09 P01 | 3min | 2 tasks | 6 files |
| Phase 09 P02 | 4min | 2 tasks | 4 files |

## Accumulated Context

### Decisions

- v1.0 completed: 3 phases, 9 plans, repo sanitized and published to GitHub
- v1.1 completed: 3 phases, 4 plans, feedback form, run history table, help page
- Dual-mode auth: simple (default) vs saml, switchable via config without code changes
- pysaml2 for SAML SP, ldap3 for ED group check -- same as Janus-Auth-Manager
- Both auth modes produce identical session cookies -- downstream code unchanged
- CSRF middleware must exempt SAML ACS endpoint (IdP POSTs from external origin)
- ePPN is not email -- use mail attribute for user lookup, not eppn
- Build ready but don't deploy -- simple mode stays default until SAML approval
- [Phase 07]: Used StaticPool for test DB to ensure lifespan and fixtures share same in-memory SQLite
- [Phase 07]: Config endpoint uses response_model_exclude_none to return clean JSON in simple mode
- [Phase 08]: Self-signed SP cert RSA 2048-bit, TraditionalOpenSSL PEM, 10-year validity
- [Phase 08]: extract_user_attrs checks OID key first, then friendly name for dual IdP format support
- [Phase 08]: Email normalized (strip+lower) in extract_user_attrs to prevent duplicate user records
- [Phase 08]: SAML binding URIs as string literals in saml_routes.py to avoid saml2 import coupling
- [Phase 08]: Separate samlError state from form error in LoginPage for simultaneous banner display
- [Phase 08]: Both GET and POST on /saml/logout for browser convenience (no IdP SLO round-trip)
- [Phase 09]: TTLCache (cachetools) with 300s TTL + stale dict fallback for ED outage resilience
- [Phase 09]: LDAP memberOf check primary path, direct group member query as fallback
- [Phase 09]: Admin group only checked if user is in access group (admin does NOT imply access)
- [Phase 09]: ED check in ACS uses redirect-on-denial pattern matching existing SAML error flow
- [Phase 09]: Per-request ED revalidation: cache-first, LDAP on miss, stale on outage, 401 on removal
- [Phase 09]: user_role=None when ED disabled preserves existing roles; simple mode bypasses ED entirely

### Pending Todos

None yet.

### Blockers/Concerns

- CViche SAML approval pending -- implementation is config-gated, no blocker for code work

## Session Continuity

Last session: 2026-03-25T12:49:47.754Z
Stopped at: Phase 10 context gathered
Resume file: .planning/phases/10-frontend-auth-flow/10-CONTEXT.md
