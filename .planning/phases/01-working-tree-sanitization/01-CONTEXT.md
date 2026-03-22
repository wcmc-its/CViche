# Phase 1: Working Tree Sanitization - Context

**Gathered:** 2026-03-22
**Status:** Ready for planning

<domain>
## Phase Boundary

Remove all sensitive files from git tracking, establish a comprehensive .gitignore, create an auth_config.yaml.example with placeholder values, and generate a top-level requirements.txt for the pipeline. After this phase, `git ls-files` shows no PII, no build artifacts, and no runtime data.

</domain>

<decisions>
## Implementation Decisions

### .gitignore Scope
- Gitignore `archive/` entirely (6,208 files, 448 MB) — old code, backups, processed CV outputs
- Move `src/legacy/` to `archive/` then gitignore both — keeps src/ clean, legacy code preserved locally
- Broad IDE/OS coverage: macOS (.DS_Store), Windows (Thumbs.db), Linux, VS Code (.vscode/), JetBrains (.idea/), Vim swap files
- Keep `docs/` tracked — technical design docs, no PII risk, valuable for contributors
- Gitignore all prompt_logs/ directories (40,549+ files in core, 1,464 in backend)
- Gitignore `node_modules/` and `dist/` under web_interface/frontend/
- Gitignore `*.db` and `*.sqlite` files
- Gitignore `__pycache__/`, `*.pyc`, `.env` files
- Gitignore all `outputs/` directories (runtime artifacts)

### auth_config.yaml.example
- Generic placeholder emails: `user@example.com` for allowed_users, `admin@example.com` for admin_users
- Keep current rate_limits values: daily: 10, monthly: 50
- Keep consent version: "1.0"
- Keep auth mode: simple
- Real `auth_config.yaml` gets gitignored

### requirements.txt Strategy
- Top-level requirements.txt covers pipeline only (src/unified_pipeline + run_full_pipeline.py)
- Web interface keeps its own at web_interface/backend/requirements.txt
- Pin to exact versions for reproducibility
- README should cross-reference both requirements files and cover both use cases (Phase 3 scope, but noted here)

### File Removal Decisions
- `data/` — Gitignore entirely (488 real CVs = primary PII risk)
- `key_files/` — Gitignore entirely EXCEPT keep `cv_template_wcm.docx` tracked (negation rule in .gitignore)
- `web_interface/uploads/` — Gitignore (150 real user CVs with names in filenames)
- `_DEPRECATED` and `_backup` files in src/ — Remove from tracking now, not deferred
- `src/unified_pipeline/core/` non-Python files — Remove .log, .txt files from tracking (7,238 .txt, 17 .log are runtime artifacts, not source)
- Single atomic commit for all removals (.gitignore creation + git rm --cached in one commit)
- Post-removal audit: run `git ls-files` and verify against success criteria before moving to Phase 2

### Claude's Discretion
- Exact .gitignore ordering and section comments
- How to structure the negation rule for cv_template_wcm.docx in key_files/
- Which specific .md files in core/ are documentation vs. operational logs
- Exact dependency versions to pin in requirements.txt (based on current environment)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project context
- `.planning/PROJECT.md` — Core value (no PII in public repo), constraints, key decisions
- `.planning/REQUIREMENTS.md` — GIT-01, GIT-02, GIT-04, DOC-04 are Phase 1 requirements

### Codebase analysis
- `.planning/codebase/CONCERNS.md` — Full inventory of PII files, tracked artifacts, and security considerations
- `.planning/codebase/STRUCTURE.md` — Directory layout, file purposes, which directories are generated vs. committed

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `web_interface/backend/requirements.txt` — Existing dependency list (17 packages, unpinned) to cross-reference when building top-level requirements.txt
- `web_interface/backend/auth_config.yaml` — Template for .example file (structure: auth mode, allowed_users, admin_users, rate_limits, consent)

### Established Patterns
- No .gitignore exists — this is a greenfield creation
- No existing requirements.txt for the pipeline — must be built by scanning imports
- `git rm --cached` is the correct approach since files should remain on disk for local use

### Integration Points
- `.gitignore` must not break the existing dev workflow — files stay on disk, just untracked
- `auth_config.yaml.example` must be placed alongside the real `auth_config.yaml` in `web_interface/backend/`
- Top-level `requirements.txt` must cover all imports used by `run_full_pipeline.py` and `src/unified_pipeline/`

</code_context>

<specifics>
## Specific Ideas

- Keep cv_template_wcm.docx from key_files/ tracked as a reference — it's the WCM Word template the pipeline fills
- Top-level README should reference both requirements files and explain both CLI and web interface setup (Phase 3, but captured here as intent)
- The single-commit approach is justified because Phase 2 rewrites history anyway — granular removal commits add no value

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 01-working-tree-sanitization*
*Context gathered: 2026-03-22*
