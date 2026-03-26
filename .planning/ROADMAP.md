# Roadmap: CViche

## Milestones

- v1.0 Public Release - Phases 1-3 (shipped 2026-03-23)
- v1.1 Web Interface UX - Phases 4-6 (shipped 2026-03-24)
- v1.2 Auth & Access Control - Phases 7-11 (in progress)

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

### v1.2 Auth & Access Control (In Progress)

**Milestone Goal:** Build dual-mode authentication (simple email + SAML SSO) and Enterprise Directory group-based authorization into CViche, ready to activate when approved. Extract a reusable auth skill for other projects.

- [ ] **Phase 7: Config & Model Foundation** - Extend auth config for dual-mode switching and prepare the User model for SAML
- [ ] **Phase 8: SAML SP Client & Endpoints** - Implement SAML 2.0 Service Provider with IdP redirect, assertion consumer, and metadata endpoints
- [ ] **Phase 9: ED Group Authorization** - Gate user access by Enterprise Directory group membership via LDAP
- [x] **Phase 10: Frontend Auth Flow** - Adapt login UI to render SSO or email form based on configured auth mode (completed 2026-03-25)
- [x] **Phase 11: Testing, Docs & Skill Extraction** - Validate full auth flow with mock IdP, document SP registration, extract reusable auth skill (completed 2026-03-26)

## Phase Details

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
- [ ] 07-01-PLAN.md -- Extend auth_config.yaml schema, add Alembic migration for auth_method column, create config endpoint and mode guard

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
- [ ] 08-01-PLAN.md -- Create saml_client.py factory (pysaml2 config, cert auto-gen, xmlsec1 detection, attribute extraction), test infrastructure
- [ ] 08-02-PLAN.md -- Create saml_routes.py (login/ACS/metadata/logout endpoints), CSRF ACS exemption, complete tests, LoginPage error display

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
- [ ] 09-01-PLAN.md -- Create ed_group_lookup.py with LDAP membership check, TTL cache, stale-on-error fallback, config seeding, and test suite
- [ ] 09-02-PLAN.md -- Wire ED check into ACS handler and get_current_user, add frontend error messages, integration tests

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
- [ ] 10-01-PLAN.md -- Add mode detection to AuthContext, conditional SSO button/email form rendering to LoginPage

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
- [ ] 11-01-PLAN.md -- Set up mock IdP Docker config, create integration test suite for SAML flow and error paths
- [ ] 11-02-PLAN.md -- Write SP registration guide for WCM IT and extract reusable auth skill

## Progress

**Execution Order:**
Phases 7-11 execute sequentially. Phase 10 depends on Phases 7 and 8 (not 9), so it could theoretically overlap with Phase 9, but sequential execution is simpler.

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Working Tree Sanitization | v1.0 | 2/2 | Complete | 2026-03-22 |
| 2. History Rewrite and Tagging | v1.0 | 4/4 | Complete | 2026-03-23 |
| 3. Documentation and Test Data | v1.0 | 3/3 | Complete | 2026-03-23 |
| 4. Feedback Form | v1.1 | 2/2 | Complete | 2026-03-23 |
| 5. Run History Redesign | v1.1 | 1/1 | Complete | 2026-03-24 |
| 6. End-User Help | v1.1 | 1/1 | Complete | 2026-03-24 |
| 7. Config & Model Foundation | v1.2 | 0/1 | Not started | - |
| 8. SAML SP Client & Endpoints | v1.2 | 0/2 | Not started | - |
| 9. ED Group Authorization | v1.2 | 0/2 | Not started | - |
| 10. Frontend Auth Flow | 1/1 | Complete    | 2026-03-25 | - |
| 11. Testing, Docs & Skill Extraction | 2/2 | Complete   | 2026-03-26 | - |
