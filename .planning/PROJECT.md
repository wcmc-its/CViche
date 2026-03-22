# CViche — Public Release Preparation

## What This Is

Prepare the CViche repository for public release on GitHub (paulalbert1/CViche). CViche is an AI-powered pipeline that converts faculty CVs into WCM's standard template format. The repo is currently a private development workspace with 60,374 tracked files including real faculty CVs, PII-containing prompt logs, SQLite databases, and node_modules. This project transforms it into a clean, publishable open-source repository that the team can clone, set up, and run.

## Core Value

No personally identifiable information — faculty CVs, email addresses, prompt logs containing CV text, or database files — ships to the public repository. Everything else is secondary.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] Comprehensive .gitignore covering all sensitive and generated content
- [ ] Git history cleaned of PII (faculty CVs, prompt logs, databases, email addresses)
- [ ] Apache 2.0 license file added
- [ ] README with setup instructions (clone, install deps, configure OpenAI key, run pipeline, run web interface)
- [ ] Top-level requirements.txt for the pipeline (currently only web_interface/backend has one)
- [ ] auth_config.yaml.example with placeholder values (real email addresses removed from tracking)
- [ ] Versioning strategy documented (semantic versioning, CHANGELOG, git tags)
- [ ] Sample/synthetic CV included for testing (not a real faculty CV)
- [ ] Planning docs (.planning/) included in public repo
- [ ] Sensitive files removed from git tracking (git rm --cached)

### Out of Scope

- Refactoring the pipeline architecture — separate effort, not gating public release
- Removing legacy/deprecated code from src/ — cleanup, not a release blocker
- Adding automated tests — important but not blocking initial publication
- Replacing print() with logging — quality improvement, not release-critical
- Docker/EKS deployment configuration — production deployment is a separate track

## Context

- The repo has been a single-developer workspace since inception. No .gitignore has ever existed.
- 60,374 files are currently tracked including: 488 real faculty CVs (PDF + Word), 42,013 prompt log files, 8,044 node_modules files, 6,208 archive files, SQLite databases with user data, and auth config with real email addresses.
- The .git directory is 284 MB due to tracked binary/data files.
- Git history contains all of the above — a .gitignore alone is insufficient. History must be rewritten.
- The team needs to clone the repo, set up a local dev environment (Python, OpenAI API key, optionally Node.js for web interface), and run the pipeline against their own CVs.
- Eventually, users from other institutions may adopt CViche. The repo should be professional enough for that audience.
- Codebase map exists at .planning/codebase/ with 7 documents covering stack, architecture, structure, conventions, testing, integrations, and concerns.

## Constraints

- **PII**: No real faculty data in the public repo or its git history. This is non-negotiable.
- **History rewrite**: Using git filter-repo or BFG Repo Cleaner will rewrite commit hashes. Since there are no external forks or CI pipelines pointing at the current history, this is safe.
- **Repo size**: After cleaning, the repo should be under 50 MB (down from ~700+ MB with .git).
- **Working state**: The public repo must be runnable — someone should be able to clone it and process a CV within 30 minutes of setup.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Apache 2.0 license | Patent grant important for institutional users; standard for academic tools | -- Pending |
| Rewrite git history (not just .gitignore) | PII in history is as bad as PII in working tree; .gitignore only prevents future tracking | -- Pending |
| Include .planning/ in public repo | Planning docs provide context for contributors and show the thought process | -- Pending |
| Semantic versioning with model changes as minor bumps | Architecture changes = major; model switches = minor; prompt tuning = patch | -- Pending |
| Provide synthetic sample CV, not real ones | Real CVs contain PII; a synthetic CV demonstrates the pipeline without risk | -- Pending |

---
*Last updated: 2026-03-22 after initialization*
