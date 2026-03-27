# Roadmap: CViche

## Milestones

- v1.0 Public Release - Phases 1-3 (shipped 2026-03-23)
- v1.1 Web Interface UX - Phases 4-6 (shipped 2026-03-24)
- v1.2 Auth & Access Control - Phases 7-11 (shipped 2026-03-26)
- v1.3 Code Quality & Security - Phases 12-15 (shipped 2026-03-27)
- v1.4 Docs & UX Polish - Phases 16-18 (in progress)

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

<details>
<summary>v1.0 Public Release (Phases 1-3) - SHIPPED 2026-03-23</summary>

- [x] **Phase 1: Working Tree Sanitization** (2/2 plans) - completed 2026-03-22
- [x] **Phase 2: History Rewrite and Tagging** (4/4 plans) - completed 2026-03-23
- [x] **Phase 3: Documentation and Test Data** (3/3 plans) - completed 2026-03-23

</details>

<details>
<summary>v1.1 Web Interface UX (Phases 4-6) - SHIPPED 2026-03-24</summary>

- [x] **Phase 4: Feedback Form** (2/2 plans) - completed 2026-03-23
- [x] **Phase 5: Run History Redesign** (1/1 plan) - completed 2026-03-24
- [x] **Phase 6: End-User Help** (1/1 plan) - completed 2026-03-24

</details>

<details>
<summary>v1.2 Auth & Access Control (Phases 7-11) - SHIPPED 2026-03-26</summary>

- [x] **Phase 7: Config & Model Foundation** (1/1 plan) - completed 2026-03-25
- [x] **Phase 8: SAML SP Client & Endpoints** (2/2 plans) - completed 2026-03-25
- [x] **Phase 9: ED Group Authorization** (2/2 plans) - completed 2026-03-25
- [x] **Phase 10: Frontend Auth Flow** (1/1 plan) - completed 2026-03-25
- [x] **Phase 11: Testing, Docs & Skill Extraction** (2/2 plans) - completed 2026-03-26

</details>

<details>
<summary>v1.3 Code Quality & Security (Phases 12-15) - SHIPPED 2026-03-27</summary>

- [x] **Phase 12: Critical Security Fixes** (2/2 plans) - completed 2026-03-26
- [x] **Phase 13: Security Hardening** (2/2 plans) - completed 2026-03-26
- [x] **Phase 14: Backend Service Layer** (3/3 plans) - completed 2026-03-26
- [x] **Phase 15: Frontend Architecture** (2/2 plans) - completed 2026-03-27

</details>

### v1.4 Docs & UX Polish

- [x] **Phase 16: Environment Fixes** - Commit CORS and Vite proxy fixes discovered during v1.3 verification (completed 2026-03-27)
- [ ] **Phase 17: Run History UX** - Feedback indicator, relative dates, and column width fix on Previous Runs table
- [ ] **Phase 18: Documentation** - README architecture update and developer handoff document

## Phase Details

### Phase 16: Environment Fixes
**Goal**: Dev environment works correctly with Vite dev server and backend CORS
**Depends on**: Phase 15
**Requirements**: ENV-01, ENV-02
**Success Criteria** (what must be TRUE):
  1. Vite dev server on port 3001 can make API calls to the backend without CORS errors
  2. Vite proxy forwards API requests to the correct backend port (5002)
  3. Both fixes are committed to the repository
**Plans**: 1 plan
Plans:
- [x] 16-01-PLAN.md -- Commit and verify CORS + Vite proxy fixes

**Note:** ENV-01 and ENV-02 are already implemented in the working tree. This phase commits the existing changes and verifies they work.

### Phase 17: Run History UX
**Goal**: Previous Runs table on the upload page gives users clear, glanceable run context
**Depends on**: Phase 16
**Requirements**: UX-01, UX-02, UX-03
**Success Criteria** (what must be TRUE):
  1. A run that has submitted feedback shows a visible indicator (icon or badge) in the Previous Runs table
  2. Runs less than 24 hours old display relative time (e.g., "5 minutes ago", "3 hours ago") instead of a full date
  3. Runs older than 24 hours display the full date
  4. The date column is wide enough that no date text is clipped or truncated
**Plans**: TBD

### Phase 18: Documentation
**Goal**: A new developer can understand the codebase architecture, security model, and deployment path
**Depends on**: Phase 17
**Requirements**: DOC-05, DOC-06
**Success Criteria** (what must be TRUE):
  1. README.md describes the current architecture: pipeline stages, web stack (React + Vite + Tailwind / FastAPI + MariaDB), service layer modules, security measures, and auth modes
  2. README.md is committed to the repository and visible on GitHub
  3. A developer handoff document exists covering SAML activation steps, ED group setup, deployment checklist, and known pending items
**Plans**: TBD

**Note:** DOC-06 (developer handoff document) is NOT committed to the repository -- it contains sensitive operational details. It will be created as a local-only file.

## Progress

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Working Tree Sanitization | v1.0 | 2/2 | Complete | 2026-03-22 |
| 2. History Rewrite and Tagging | v1.0 | 4/4 | Complete | 2026-03-23 |
| 3. Documentation and Test Data | v1.0 | 3/3 | Complete | 2026-03-23 |
| 4. Feedback Form | v1.1 | 2/2 | Complete | 2026-03-23 |
| 5. Run History Redesign | v1.1 | 1/1 | Complete | 2026-03-24 |
| 6. End-User Help | v1.1 | 1/1 | Complete | 2026-03-24 |
| 7. Config & Model Foundation | v1.2 | 1/1 | Complete | 2026-03-25 |
| 8. SAML SP Client & Endpoints | v1.2 | 2/2 | Complete | 2026-03-25 |
| 9. ED Group Authorization | v1.2 | 2/2 | Complete | 2026-03-25 |
| 10. Frontend Auth Flow | v1.2 | 1/1 | Complete | 2026-03-25 |
| 11. Testing, Docs & Skill Extraction | v1.2 | 2/2 | Complete | 2026-03-26 |
| 12. Critical Security Fixes | v1.3 | 2/2 | Complete | 2026-03-26 |
| 13. Security Hardening | v1.3 | 2/2 | Complete | 2026-03-26 |
| 14. Backend Service Layer | v1.3 | 3/3 | Complete | 2026-03-26 |
| 15. Frontend Architecture | v1.3 | 2/2 | Complete | 2026-03-27 |
| 16. Environment Fixes | v1.4 | 1/1 | Complete   | 2026-03-27 |
| 17. Run History UX | v1.4 | 0/0 | Not started | - |
| 18. Documentation | v1.4 | 0/0 | Not started | - |
