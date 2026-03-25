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

- [ ] **TEST-01**: Mock IdP enables full SAML flow testing without live WCM infrastructure
- [ ] **DOC-01**: SP registration guide documents what's needed to register CViche with WCM IdP

### Skill

- [ ] **SKILL-01**: Reusable Claude Code auth skill captures SAML + ED group auth patterns for other projects

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
| Creating ED access group | Pending approval; code handles any group name via config |
| SP registration with IdP | Pending approval; docs cover what's needed |
| OIDC/OAuth support | SAML is the WCM standard; no need for OIDC |
| Password-based auth | WCM uses SSO; simple mode uses email allow-list |
| Multi-IdP support | CViche is WCM-only; discovery service handles federation |
| SCIM user provisioning | JIT from SAML assertions is sufficient |
| Admin dashboard improvements | Separate milestone |
| Pipeline architecture refactoring | Separate workstream |

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
| TEST-01 | Phase 11 | Pending |
| DOC-01 | Phase 11 | Pending |
| SKILL-01 | Phase 11 | Pending |

**Coverage:**
- v1.2 requirements: 12 total
- Mapped to phases: 12
- Unmapped: 0

---
*Requirements defined: 2026-03-24*
