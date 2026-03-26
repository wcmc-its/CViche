# Requirements: CViche

**Defined:** 2026-03-24
**Core Value:** Faculty can upload a CV and get back a correctly formatted WCM document without manual reformatting

## v1.2 Requirements

### SAML SSO

- [x] **SAML-01**: App redirects to SAML IdP via login.weill.cornell.edu discovery service and processes assertion at ACS endpoint
- [x] **SAML-02**: SP metadata endpoint generates valid SAML 2.0 metadata for IdP registration
- [x] **SAML-03**: User record auto-created from SAML assertion attributes (eppn, displayName) on first login
- [x] **SAML-04**: Single logout destroys local session when initiated by user or IdP

### ED Authorization

- [x] **ED-01**: User access gated by Enterprise Directory group membership checked via LDAP at login
- [x] **ED-02**: Group membership re-checked per request with 5-minute TTL cache; removal locks user out within 5 minutes
- [x] **ED-03**: Admin role assigned based on separate ED admin group membership

### Dual Mode

- [x] **MODE-01**: Auth mode (simple vs SAML) switchable via config/environment without code changes
- [x] **MODE-02**: Frontend login page renders SSO button or email form based on active auth mode

### Testing & Docs

- [x] **TEST-01**: Mock IdP enables full SAML flow testing without live WCM infrastructure
- [x] **DOC-01**: SP registration guide documents what's needed to register CViche with WCM IdP

### Skill

- [x] **SKILL-01**: Reusable Claude Code auth skill captures SAML + ED group auth patterns for other projects

## v1.1 Requirements (Complete)

### Feedback

- [x] **FB-01**: User can submit feedback on a completed pipeline run via a form accessible from the run page
- [x] **FB-02**: Feedback form collects all fields defined in the existing backend schema (accuracy, completeness, usefulness, effort estimates, issue checkboxes, NPS)
- [x] **FB-03**: User can see that feedback was already submitted for a run (prevents duplicate submission)

### Run History

- [x] **RH-01**: Previous runs display as a table with sortable columns (filename, date, duration, cost, status, feedback)
- [x] **RH-02**: Run history table is scannable at a glance -- no need to parse card layouts

### End-User Help

- [x] **HELP-01**: End users can access contextual help/support content within the web interface
- [x] **HELP-02**: Help content is written for faculty/staff audience (not developers) -- explains what CViche does, what to expect, how to interpret results

## v1.3 Requirements

### Security — Critical

- [x] **SEC-01**: File download endpoint rejects absolute paths and restricts access to run output directories only
- [x] **SEC-02**: SAML assertions require valid IdP signatures (`want_assertions_signed: True`)
- [x] **SEC-03**: Application refuses to start without explicit `CVICHE_SESSION_SECRET` environment variable

### Security — Hardening

- [x] **SEC-04**: File uploads validated by MIME type and magic bytes; filenames sanitized to random IDs
- [x] **SEC-05**: Error responses contain no internal file paths, stack traces, or implementation details
- [x] **SEC-06**: HTTP security headers set on all responses (CSP, X-Frame-Options, HSTS, X-Content-Type-Options)
- [x] **SEC-07**: CORS explicitly lists allowed methods and headers instead of wildcards

### Backend Architecture

- [ ] **ARCH-01**: Route handlers delegate to service functions; no direct DB queries in route files
- [x] **ARCH-02**: Run access control (`_check_run_access`) defined once in a shared module, not duplicated across 3 files
- [x] **ARCH-03**: User provisioning logic (create/update from login) defined once, used by both simple and SAML auth flows
- [x] **ARCH-04**: Hardcoded values (rate limits, cost rates, session TTL) moved to config or environment variables
- [ ] **ARCH-05**: Admin user stats endpoint uses aggregation queries instead of N+1 per-user loop
- [x] **ARCH-06**: Error responses follow a single consistent format across all endpoints

### Frontend Architecture

- [ ] **FE-01**: All API calls go through a centralized client with typed functions, base URL from environment
- [ ] **FE-02**: WebSocket URL derived from environment config, not hardcoded to localhost
- [ ] **FE-03**: API response types defined in shared `src/types/` directory, not duplicated per component
- [ ] **FE-04**: Shared formatting utilities (time, date, status) defined once in `src/utils/`

## v2+ Requirements

### Code Quality

- **QUAL-01**: Replace print() debugging with structured logging (3,243 print calls in src/unified_pipeline/)
- **QUAL-02**: Remove deprecated files from src/ (_DEPRECATED, _backup suffixes)
- **QUAL-03**: Pin dependency versions in web_interface/backend/requirements.txt
- **QUAL-04**: Replace hardcoded absolute paths with relative paths or environment variables

### Testing

- **TEST-02**: pytest configuration and test directory structure
- **TEST-03**: Gold standard regression testing framework
- **TEST-04**: CI/CD pipeline (GitHub Actions)

## Out of Scope

| Feature | Reason |
|---------|--------|
| Live SSO deployment | CViche not yet approved for SAML integration |
| CSRF token implementation | Origin-header CSRF is sufficient for same-origin SPA; tokens add complexity without benefit for this architecture |
| Thread-safe module-level caches | FastAPI runs single-threaded per worker; asyncio concurrency doesn't cause race conditions on dict mutation |
| PipelineViewer refactor (useReducer) | Large but functional; cosmetic refactor deferred to avoid risk |
| API versioning (/api/v1/) | Single-deployment app with coordinated frontend/backend; versioning adds overhead without benefit |
| Pipeline architecture refactoring | Separate workstream |
| Admin dashboard improvements | Separate milestone |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| FB-01 | Phase 4 | Complete |
| FB-02 | Phase 4 | Complete |
| FB-03 | Phase 4 | Complete |
| RH-01 | Phase 5 | Complete |
| RH-02 | Phase 5 | Complete |
| HELP-01 | Phase 6 | Complete |
| HELP-02 | Phase 6 | Complete |
| MODE-01 | Phase 7 | Complete |
| SAML-01 | Phase 8 | Complete |
| SAML-02 | Phase 8 | Complete |
| SAML-03 | Phase 8 | Complete |
| SAML-04 | Phase 8 | Complete |
| ED-01 | Phase 9 | Complete |
| ED-02 | Phase 9 | Complete |
| ED-03 | Phase 9 | Complete |
| MODE-02 | Phase 10 | Complete |
| TEST-01 | Phase 11 | Complete |
| DOC-01 | Phase 11 | Complete |
| SKILL-01 | Phase 11 | Complete |
| SEC-01 | Phase 12 | Complete |
| SEC-02 | Phase 12 | Complete |
| SEC-03 | Phase 12 | Complete |
| SEC-04 | Phase 13 | Complete |
| SEC-05 | Phase 13 | Complete |
| SEC-06 | Phase 13 | Complete |
| SEC-07 | Phase 13 | Complete |
| ARCH-01 | Phase 14 | Pending |
| ARCH-02 | Phase 14 | Complete |
| ARCH-03 | Phase 14 | Complete |
| ARCH-04 | Phase 14 | Complete |
| ARCH-05 | Phase 14 | Pending |
| ARCH-06 | Phase 14 | Complete |
| FE-01 | Phase 15 | Pending |
| FE-02 | Phase 15 | Pending |
| FE-03 | Phase 15 | Pending |
| FE-04 | Phase 15 | Pending |

**Coverage:**
- v1.3 requirements: 17 total
- Mapped to phases: 17/17
- Unmapped: 0

---
*Requirements defined: 2026-03-24*
*Last updated: 2026-03-26 after v1.3 roadmap creation*
