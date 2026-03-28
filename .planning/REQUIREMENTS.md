# Requirements: CViche

**Defined:** 2026-03-27
**Core Value:** Faculty can upload a CV and get back a correctly formatted WCM document without manual reformatting

## v1.4 Requirements

### Documentation

- [x] **DOC-05**: Update existing README.md with current architecture (pipeline stages, web stack, service layer, security measures, auth modes)
- [x] **DOC-06**: Create a developer handoff document (NOT committed) covering SAML activation steps, ED group setup, deployment checklist, and known pending items

### Run History UX

- [ ] **UX-01**: Previous Runs table on upload page shows a feedback indicator for runs that have submitted feedback
- [ ] **UX-02**: Date column shows relative time ("5 minutes ago", "3 hours ago") for runs less than 24 hours old, full date for older runs
- [ ] **UX-03**: Date column is wide enough to display the full date without clipping

### Environment Fixes

- [x] **ENV-01**: CORS allowed origins include localhost:3001 for Vite dev server
- [x] **ENV-02**: Vite proxy targets the correct backend port (5002)

## Out of Scope

| Feature | Reason |
|---------|--------|
| Pipeline architecture refactoring | Separate effort, large scope |
| Replacing print() with structured logging | Quality improvement, separate milestone |
| Docker/EKS deployment | Production ops track |
| Admin dashboard improvements | Separate milestone |
| Going live with SSO | Pending WCM approval |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| DOC-05 | Phase 18 | Complete |
| DOC-06 | Phase 18 | Complete |
| UX-01 | Phase 17 | Pending |
| UX-02 | Phase 17 | Pending |
| UX-03 | Phase 17 | Pending |
| ENV-01 | Phase 16 | Pending (implemented, needs commit) |
| ENV-02 | Phase 16 | Pending (implemented, needs commit) |

**Coverage:**
- v1.4 requirements: 7 total
- Mapped to phases: 7/7
- Unmapped: 0

---
*Requirements defined: 2026-03-27*
