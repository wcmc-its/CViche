# Roadmap: CViche

## Milestones

- v1.0 Public Release - Phases 1-3 (shipped 2026-03-23)
- v1.1 Web Interface UX - Phases 4-6 (shipped 2026-03-24)
- v1.2 Auth & Access Control - Phases 7-11 (shipped 2026-03-26)
- v1.3 Code Quality & Security - Phases 12-15 (in progress)

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

<details>
<summary>v1.0 Public Release (Phases 1-3) - SHIPPED 2026-03-23</summary>

- [x] **Phase 1: Working Tree Sanitization** - Remove sensitive files from tracking, establish .gitignore, create config examples and dependency manifest
- [x] **Phase 2: History Rewrite and Tagging** - Purge all PII from git history, verify the repo is clean, apply v1.0.0 tag
- [x] **Phase 3: Documentation and Test Data** - Add README, LICENSE, CHANGELOG, versioning infrastructure, and synthetic sample CV

### Phase 1: Working Tree Sanitization
**Goal**: The git working tree contains no sensitive files and new sensitive files cannot be accidentally tracked
**Depends on**: Nothing (first phase)
**Requirements**: GIT-01, GIT-02, GIT-04, DOC-04
**Success Criteria** (what must be TRUE):
  1. Running `git ls-files` shows no faculty CVs, prompt logs, SQLite databases, node_modules, or auth config with real emails
  2. A comprehensive .gitignore exists and correctly ignores all categories of sensitive/generated content (CVs, logs, databases, node_modules, __pycache__, .env, OS artifacts)
  3. auth_config.yaml.example exists with placeholder values, and the real auth_config.yaml is gitignored
  4. A top-level requirements.txt exists listing all Python dependencies needed to run the pipeline
**Plans:** 2 plans

Plans:
- [x] 01-01-PLAN.md -- Create .gitignore, auth_config.yaml.example, and requirements.txt
- [x] 01-02-PLAN.md -- Execute git rm --cached, move legacy to archive, atomic commit, and audit

### Phase 2: History Rewrite and Tagging
**Goal**: The git history contains zero PII and the clean state is tagged as the v1.0.0 release point
**Depends on**: Phase 1
**Requirements**: GIT-03, VER-02
**Success Criteria** (what must be TRUE):
  1. Running BFG or git filter-repo confirms all targeted file types (PDFs, .docx CVs, prompt logs, .sqlite/.db files, auth_config.yaml) are purged from every commit in history
  2. The .git directory is under 50 MB after repacking
  3. A `git tag v1.0.0` exists on the final clean commit
**Plans:** 4/4 plans complete

Plans:
- [x] 02-01-PLAN.md -- Pre-BFG cleanup: fix HEAD, install BFG, create safety backup
- [x] 02-02-PLAN.md -- Execute BFG on mirror clone, gc/repack, replace .git
- [x] 02-03-PLAN.md -- Verify clean history, human approval, apply v1.0.0 tag
- [x] 02-04-PLAN.md -- Gap closure: re-purge .outputs/ hidden directory, re-apply v1.0.0 tag to HEAD

### Phase 3: Documentation and Test Data
**Goal**: A new user can clone the repo, understand what CViche does, set it up, and run the pipeline against a sample CV without any external guidance
**Depends on**: Phase 2
**Requirements**: DOC-01, DOC-02, DOC-03, VER-01, VER-03, TEST-01
**Success Criteria** (what must be TRUE):
  1. README.md contains clear setup instructions (clone, install deps, configure OpenAI key, run pipeline CLI, run web interface) and a new user can follow them end-to-end
  2. An Apache 2.0 LICENSE file exists at the repository root
  3. CHANGELOG.md exists starting from v1.0.0 with a summary of current capabilities
  4. The versioning policy (major/minor/patch definitions) is documented and a __version__ variable is accessible in the package
  5. A synthetic sample CV (.docx) is included in the repo and can be processed by the pipeline to demonstrate functionality
**Plans:** 3 plans

Plans:
- [x] 03-01-PLAN.md -- Create LICENSE, CHANGELOG.md, __version__, synthetic sample CV, and .gitignore negation
- [x] 03-02-PLAN.md -- Reorganize docs/ directory (move internal specs to .planning/docs/, fix cross-references)
- [x] 03-03-PLAN.md -- Create README.md with setup instructions, pipeline overview, and screenshots

</details>

<details>
<summary>v1.1 Web Interface UX (Phases 4-6) - SHIPPED 2026-03-24</summary>

- [x] **Phase 4: Feedback Form** - Build the frontend form that connects to the existing feedback API
- [x] **Phase 5: Run History Redesign** - Replace card layout with a scannable, sortable table
- [x] **Phase 6: End-User Help** - Add contextual help and support content for faculty/staff users

### Phase 4: Feedback Form
**Goal**: Users can submit structured feedback on their pipeline results directly from the web interface
**Depends on**: Phase 3 (v1.0 complete)
**Requirements**: FB-01, FB-02, FB-03
**Success Criteria** (what must be TRUE):
  1. User can open a feedback form from a completed run's page
  2. The form presents all backend schema fields in a user-friendly layout (accuracy rating, completeness rating, usefulness rating, effort estimates, issue checkboxes, NPS score, free-text comments)
  3. User can submit the form and sees a confirmation that feedback was recorded
  4. When a user returns to a run that already has feedback, they see an indicator that feedback was submitted (form is not offered again)
**Plans:** 2 plans

Plans:
- [x] 04-01-PLAN.md -- Create FeedbackForm.tsx: 4-step wizard with all schema fields, validation, API integration, and feedback-submitted card
- [x] 04-02-PLAN.md -- Integrate FeedbackForm into PipelineViewer and wire RunHistory badge navigation

### Phase 5: Run History Redesign
**Goal**: Users can quickly scan and find previous pipeline runs without parsing dense card layouts
**Depends on**: Phase 3 (v1.0 complete)
**Requirements**: RH-01, RH-02
**Success Criteria** (what must be TRUE):
  1. Previous runs display in a table layout with columns for filename, date, duration, cost, status, and feedback status
  2. Users can sort the table by any column to find specific runs
  3. The table is scannable at a glance -- a user with 20+ runs can locate a specific run in under 5 seconds
**Plans:** 1/1 plans complete

Plans:
- [x] 05-01-PLAN.md -- Rewrite RunHistory.tsx from cards to sortable paginated table with visual verification

### Phase 6: End-User Help
**Goal**: Faculty and staff users can understand what CViche does, what to expect from results, and how to get support -- without needing to ask someone
**Depends on**: Phase 3 (v1.0 complete)
**Requirements**: HELP-01, HELP-02
**Success Criteria** (what must be TRUE):
  1. Users can access help/support content from within the web interface (not hidden in a separate site or README)
  2. Help content explains what CViche does, what input formats are accepted, what the output looks like, and how to interpret pipeline results
  3. Help content is written in plain language appropriate for faculty/staff (no developer jargon, no references to API endpoints or pipeline internals)
**Plans:** 1/1 plans complete

Plans:
- [x] 06-01-PLAN.md -- Create HelpPage.tsx with all content sections, add /help route, and wire HelpCircle icon links on UploadPage and PipelineHeader

</details>

<details>
<summary>v1.2 Auth & Access Control (Phases 7-11) - SHIPPED 2026-03-26</summary>

- [x] **Phase 7: Config & Model Foundation** - Extend auth config for dual-mode switching and prepare the User model for SAML
- [x] **Phase 8: SAML SP Client & Endpoints** - Implement SAML 2.0 Service Provider with IdP redirect, assertion consumer, and metadata endpoints
- [x] **Phase 9: ED Group Authorization** - Gate user access by Enterprise Directory group membership via LDAP
- [x] **Phase 10: Frontend Auth Flow** - Adapt login UI to render SSO or email form based on configured auth mode
- [x] **Phase 11: Testing, Docs & Skill Extraction** - Validate full auth flow with mock IdP, document SP registration, extract reusable auth skill

### Phase 7: Config & Model Foundation
**Goal**: The auth system supports dual-mode configuration and the database is ready to track how users authenticate
**Depends on**: Phase 6 (v1.1 complete)
**Requirements**: MODE-01
**Success Criteria** (what must be TRUE):
  1. Setting `mode: saml` in auth_config.yaml switches auth behavior without any code changes; setting `mode: simple` preserves current behavior exactly
  2. The User table has an `auth_method` column that records whether each user authenticated via simple or SAML
  3. A public `GET /api/auth/config` endpoint returns the active auth mode so the frontend can adapt its UI
  4. The simple email login endpoint is disabled when mode is set to SAML (returns an error explaining SSO is required)
**Plans:** 1 plan

Plans:
- [x] 07-01-PLAN.md -- Extend auth_config.yaml schema, add Alembic migration for auth_method column, create config endpoint and mode guard

### Phase 8: SAML SP Client & Endpoints
**Goal**: Users can authenticate through WCM's SAML IdP and receive a CViche session identical to simple-mode login
**Depends on**: Phase 7
**Requirements**: SAML-01, SAML-02, SAML-03, SAML-04
**Success Criteria** (what must be TRUE):
  1. Visiting the login page in SAML mode redirects the user to the WCM IdP via login.weill.cornell.edu discovery service, and after IdP authentication the user lands back in CViche with an active session
  2. A User record is automatically created from SAML assertion attributes (eppn, displayName, mail) on first login -- no pre-registration needed
  3. `GET /api/saml/metadata` returns valid SAML 2.0 SP metadata XML suitable for IdP registration
  4. Logging out destroys the local session and the user must re-authenticate to access CViche
  5. The SAML session cookie is identical to the simple-mode cookie -- all downstream features (consent, runs, feedback) work without changes
**Plans:** 2 plans

Plans:
- [x] 08-01-PLAN.md -- Create saml_client.py factory (pysaml2 config, cert auto-gen, xmlsec1 detection, attribute extraction), test infrastructure
- [x] 08-02-PLAN.md -- Create saml_routes.py (login/ACS/metadata/logout endpoints), CSRF ACS exemption, complete tests, LoginPage error display

### Phase 9: ED Group Authorization
**Goal**: Only users who belong to a designated Enterprise Directory group can access CViche in SAML mode
**Depends on**: Phase 8
**Requirements**: ED-01, ED-02, ED-03
**Success Criteria** (what must be TRUE):
  1. A user who is not in the configured ED access group is denied login with a clear error message explaining they need group membership
  2. A user who is removed from the ED group loses access within 5 minutes (cached membership expires and re-check denies access)
  3. A user in the configured ED admin group is assigned the admin role in CViche
  4. If the Enterprise Directory is unreachable, login fails gracefully with a human-readable error (not a stack trace or timeout hang)
**Plans:** 2 plans

Plans:
- [x] 09-01-PLAN.md -- Create ed_group_lookup.py with LDAP membership check, TTL cache, stale-on-error fallback, config seeding, and test suite
- [x] 09-02-PLAN.md -- Wire ED check into ACS handler and get_current_user, add frontend error messages, integration tests

### Phase 10: Frontend Auth Flow
**Goal**: The login page adapts to the configured auth mode, showing either an SSO button or the existing email form
**Depends on**: Phase 7 (config endpoint), Phase 8 (SAML routes)
**Requirements**: MODE-02
**Success Criteria** (what must be TRUE):
  1. In SAML mode, the login page shows an SSO login button that initiates the SAML flow -- no email form is visible
  2. In simple mode, the login page shows the existing email form -- no SSO button is visible
  3. SAML authentication errors (IdP unreachable, assertion failed, not in group) display as user-friendly messages on the login page
**Plans:** 1/1 plans complete

Plans:
- [x] 10-01-PLAN.md -- Add mode detection to AuthContext, conditional SSO button/email form rendering to LoginPage

### Phase 11: Testing, Docs & Skill Extraction
**Goal**: The complete auth flow is validated against a mock IdP, SP registration is documented, and the auth pattern is captured as a reusable skill
**Depends on**: Phase 9, Phase 10
**Requirements**: TEST-01, DOC-01, SKILL-01
**Success Criteria** (what must be TRUE):
  1. A mock IdP (MockSAML or equivalent) enables end-to-end SAML flow testing locally without WCM infrastructure
  2. An SP registration guide documents the exact metadata URL, attribute requirements, and steps needed to register CViche with the WCM IdP
  3. A Claude Code skill file captures the SAML + ED group auth pattern in a form that can be applied to other FastAPI projects
**Plans:** 2/2 plans complete

Plans:
- [x] 11-01-PLAN.md -- Set up mock IdP Docker config, create integration test suite for SAML flow and error paths
- [x] 11-02-PLAN.md -- Write SP registration guide for WCM IT and extract reusable auth skill

</details>

### v1.3 Code Quality & Security (In Progress)

**Milestone Goal:** Fix critical security vulnerabilities, harden the web interface, and refactor backend/frontend architecture for proper separation of concerns.

- [x] **Phase 12: Critical Security Fixes** - Patch path traversal, SAML signature validation, and session secret vulnerabilities (completed 2026-03-26)
- [x] **Phase 13: Security Hardening** - Upload validation, error sanitization, HTTP security headers, CORS lockdown (completed 2026-03-26)
- [x] **Phase 14: Backend Service Layer** - Extract service functions from route handlers, deduplicate shared logic, centralize config (completed 2026-03-26)
- [ ] **Phase 15: Frontend Architecture** - Centralized API client, shared types, environment config, formatting utilities

## Phase Details

### Phase 12: Critical Security Fixes
**Goal**: The three highest-severity security vulnerabilities are eliminated before any other work proceeds
**Depends on**: Phase 11 (v1.2 complete)
**Requirements**: SEC-01, SEC-02, SEC-03
**Success Criteria** (what must be TRUE):
  1. Requesting a file download with `../` path components or an absolute path returns a 400 error and never accesses files outside the run output directory
  2. A SAML assertion with a missing or invalid IdP signature is rejected at the ACS endpoint -- the user is not logged in
  3. The application fails to start (raises an error at boot) if `CVICHE_SESSION_SECRET` is not set as an environment variable
**Plans:** 2/2 plans complete

Plans:
- [ ] 12-01-PLAN.md -- Enforce session secret requirement, enable SAML signature validation, add SEC-02/SEC-03 regression tests
- [ ] 12-02-PLAN.md -- Fix path traversal in file download endpoints with shared _resolve_safe_path utility and regression tests

### Phase 13: Security Hardening
**Goal**: The web interface follows defense-in-depth security practices for uploads, error handling, headers, and CORS
**Depends on**: Phase 12
**Requirements**: SEC-04, SEC-05, SEC-06, SEC-07
**Success Criteria** (what must be TRUE):
  1. Uploading a file with a spoofed extension (e.g., .docx extension but PDF magic bytes) is rejected; uploaded files are stored with randomized filenames, not the user-provided name
  2. Error responses from any endpoint contain no internal file paths, Python stack traces, or implementation details -- only a user-facing message and an error code
  3. Every HTTP response includes CSP, X-Frame-Options, Strict-Transport-Security, and X-Content-Type-Options headers
  4. CORS configuration explicitly lists allowed origins, methods, and headers instead of using wildcards
**Plans:** 2/2 plans complete

Plans:
- [ ] 13-01-PLAN.md -- Error sanitization (global exception handler), HTTP security headers middleware, CORS lockdown
- [ ] 13-02-PLAN.md -- Upload magic bytes validation, file size limit, filename randomization

### Phase 14: Backend Service Layer
**Goal**: Route handlers are thin dispatchers that delegate to testable service functions, with shared logic defined once
**Depends on**: Phase 13
**Requirements**: ARCH-01, ARCH-02, ARCH-03, ARCH-04, ARCH-05, ARCH-06
**Success Criteria** (what must be TRUE):
  1. No route handler file contains direct database queries -- all data access goes through service functions in a `services/` directory
  2. Run access control (checking if a user owns a run) is defined in one place and imported by all endpoints that need it -- grep finds zero duplicate implementations
  3. User provisioning from login (create-or-update) is a single shared function used by both simple auth and SAML ACS flows
  4. Hardcoded values (rate limits, cost-per-token rates, session TTL) live in config or environment variables, not scattered as literals in route handlers
  5. The admin user stats endpoint completes in O(1) queries (aggregation), not O(N) queries (one per user)
**Plans:** 3/3 plans complete

Plans:
- [ ] 14-01-PLAN.md -- Create service layer foundation: errors.py, config_service.py, run_service.py, user_service.py, and test suite
- [ ] 14-02-PLAN.md -- Wire services into non-admin route handlers, replace inline DB queries and plain-string errors
- [ ] 14-03-PLAN.md -- Create admin_service.py with O(1) aggregation queries, refactor admin_routes.py

### Phase 15: Frontend Architecture
**Goal**: The frontend has a single source of truth for API communication, types, environment config, and formatting utilities
**Depends on**: Phase 14
**Requirements**: FE-01, FE-02, FE-03, FE-04
**Success Criteria** (what must be TRUE):
  1. All API calls go through a centralized client module (`src/api/` or similar) with typed functions -- no component directly constructs fetch URLs
  2. The WebSocket URL is derived from an environment variable or the centralized config, not hardcoded to `localhost` or any specific host
  3. API response types are defined in `src/types/` and imported by components -- grep finds zero inline type definitions that duplicate the shared ones
  4. Formatting utilities (time duration, dates, status labels) are defined once in `src/utils/` and imported wherever needed
**Plans**: TBD

Plans:
- [ ] 15-01: TBD

## Progress

**Execution Order:**
Phases 12-15 execute sequentially. Phase 12 (critical security) is highest priority and must complete before any other v1.3 work.

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
| 14. Backend Service Layer | 3/3 | Complete   | 2026-03-26 | - |
| 15. Frontend Architecture | v1.3 | 0/TBD | Not started | - |
