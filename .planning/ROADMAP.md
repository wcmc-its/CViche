# Roadmap: CViche Public Release

## Overview

Transform the CViche repository from a private development workspace (60,374 tracked files, 284 MB .git, real faculty CVs and PII throughout) into a clean, professional open-source repository that anyone can clone, set up, and run within 30 minutes. Three phases: sanitize the working tree, rewrite history to purge PII, then add documentation and test data to make the repo self-sufficient.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Working Tree Sanitization** - Remove sensitive files from tracking, establish .gitignore, create config examples and dependency manifest
- [x] **Phase 2: History Rewrite and Tagging** - Purge all PII from git history, verify the repo is clean, apply v1.0.0 tag
- [ ] **Phase 3: Documentation and Test Data** - Add README, LICENSE, CHANGELOG, versioning infrastructure, and synthetic sample CV

## Phase Details

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
**Plans:** 3 plans

Plans:
- [x] 02-01-PLAN.md -- Pre-BFG cleanup: fix HEAD, install BFG, create safety backup
- [x] 02-02-PLAN.md -- Execute BFG on mirror clone, gc/repack, replace .git
- [x] 02-03-PLAN.md -- Verify clean history, human approval, apply v1.0.0 tag

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
**Plans**: TBD

Plans:
- [ ] 03-01: TBD
- [ ] 03-02: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 -> 2 -> 3

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Working Tree Sanitization | 2/2 | Complete | 2026-03-22 |
| 2. History Rewrite and Tagging | 3/3 | Complete | 2026-03-22 |
| 3. Documentation and Test Data | 0/? | Not started | - |
