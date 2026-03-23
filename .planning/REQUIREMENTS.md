# Requirements: CViche Public Release

**Defined:** 2026-03-22
**Core Value:** No personally identifiable information ships to the public repository

## v1 Requirements

### Git Hygiene

- [x] **GIT-01**: Comprehensive .gitignore covering sample CVs, prompt logs, databases, node_modules, build artifacts, __pycache__, .env files, and OS artifacts
- [x] **GIT-02**: All files matching .gitignore patterns removed from git tracking via git rm --cached
- [x] **GIT-03**: Git history rewritten to purge PII (faculty CVs, prompt logs with CV text, SQLite databases with user data, auth config with real emails) using BFG Repo Cleaner or git filter-repo
- [x] **GIT-04**: auth_config.yaml replaced with auth_config.yaml.example containing placeholder values; real auth_config.yaml gitignored

### Documentation

- [ ] **DOC-01**: README.md with setup instructions — clone, install Python dependencies, configure OpenAI API key, run pipeline CLI, run web interface (both dev and Docker)
- [ ] **DOC-02**: Apache 2.0 LICENSE file at repository root
- [ ] **DOC-03**: CHANGELOG.md starting from v1.0.0 summarizing current capabilities
- [x] **DOC-04**: Top-level requirements.txt for pipeline Python dependencies (currently only web_interface/backend/ has one)

### Versioning

- [ ] **VER-01**: Semantic versioning policy documented — major = architecture changes, minor = model switches or new stages, patch = prompt tuning and bug fixes
- [x] **VER-02**: Git tag v1.0.0 applied to the clean public release commit
- [ ] **VER-03**: __version__ variable accessible in the package (e.g., src/unified_pipeline/__init__.py or similar)

### Test Data

- [ ] **TEST-01**: Synthetic sample CV (.docx) included in repo for pipeline testing — fabricated faculty data, not a real person

## v2 Requirements

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
| Pipeline architecture refactoring | Separate workstream; not gating public release |
| Docker/EKS deployment configs | Production deployment is a separate track with SDS |
| SAML/SSO integration | Production auth is separate from repo publication |
| Web interface feature work | Functional as-is for team testing |
| Automated test suite | Important but not blocking initial publication |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| GIT-01 | Phase 1 | Complete |
| GIT-02 | Phase 1 | Complete |
| GIT-03 | Phase 2 | Complete |
| GIT-04 | Phase 1 | Complete |
| DOC-01 | Phase 3 | Pending |
| DOC-02 | Phase 3 | Pending |
| DOC-03 | Phase 3 | Pending |
| DOC-04 | Phase 1 | Complete |
| VER-01 | Phase 3 | Pending |
| VER-02 | Phase 2 | Complete |
| VER-03 | Phase 3 | Pending |
| TEST-01 | Phase 3 | Pending |

**Coverage:**
- v1 requirements: 12 total
- Mapped to phases: 12
- Unmapped: 0

---
*Requirements defined: 2026-03-22*
*Last updated: 2026-03-22 after 02-03 completion (VER-02 complete)*
