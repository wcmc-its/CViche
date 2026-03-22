# Phase 2: History Rewrite and Tagging - Context

**Gathered:** 2026-03-22
**Status:** Ready for planning

<domain>
## Phase Boundary

Purge all PII and bloat from git history using BFG Repo Cleaner, verify the repository is clean, and tag the final clean commit as v1.0.0. After this phase, no faculty CVs, prompt logs, databases, or other sensitive files exist in any historical commit, the .git directory is under 50 MB, and a v1.0.0 annotated tag marks the release point.

</domain>

<decisions>
## Implementation Decisions

### Tool Choice
- Use BFG Repo Cleaner (installed via Homebrew: `brew install bfg`)
- BFG only — no git filter-repo second pass needed
- No PII exists in commit messages (all 16 commits are Phase 1/GSD planning work)
- BFG operates on a bare clone; we'll work on a mirror then replace

### Purge Targeting Strategy
- Target by path + extension globs (not size threshold)
- Paths to purge from history: `data/`, `archive/`, `web_interface/uploads/`, `web_interface/frontend/node_modules/`, `web_interface/frontend/dist/`, `src/unified_pipeline/core/prompt_logs/`, `web_interface/backend/prompt_logs/`, `web_interface/outputs/`, `src/logs/`, `key_files/` (except cv_template_wcm.docx)
- Extensions to purge: `*.pdf`, `*.docx` (except cv_template_wcm.docx), `*.sqlite`, `*.db`, `*.tar.gz`, `*.key`
- Specific files: `web_interface/backend/auth_config.yaml`, `web_interface/backend/cviche.db`, `web_interface/backend/cviche_dev.db`, `web_interface/backend/pipeline.db`
- `classifications.jsonl` (13MB, src/logs/) — purge (Claude's discretion: contains LLM outputs with potential CV content, PII risk)
- Logo file (CViche - Logo.key, 11MB) — purge from history, keep on disk, copy to Projects/ first

### Artifact Preservation
- Before rewrite: copy logo and large design/business artifacts to `~/Dropbox/Projects/CViche - Planning/`
- Operational data (prompt logs, classifications, databases) does NOT need external backup — can be regenerated
- Files remain on local disk regardless (Phase 1's git rm --cached preserved them)

### Backup & Safety Plan
- Full mirror clone before rewrite: `git clone --mirror` to `~/Dropbox/Projects/CViche - Planning/repo-backup-pre-rewrite.git`
- Preserves complete original history (~275MB) as a restore point
- Dry run BFG first (report mode) to review what would be purged before executing
- Only proceed to actual rewrite after reviewing dry run output

### Post-Rewrite Verification
- Automated blob scan: enumerate ALL blobs in rewritten history, verify none match targeted extensions/paths
- Verify .git directory size < 50 MB after gc/repack
- Verify current working tree is intact (all tracked files still present and functional)
- Manual sign-off required before tagging — show verification results, wait for explicit approval

### Tagging
- Annotated tag: `git tag -a v1.0.0 -m "..."` with release message
- Tag applied only after verification passes AND user approves
- Tag goes on the final clean commit (HEAD after rewrite + repack)

### Claude's Discretion
- Exact BFG command flags and ordering of operations
- Whether to run multiple BFG passes (one per category) or a single pass with combined glob
- Git gc/repack strategy (aggressive vs standard)
- Exact wording of the v1.0.0 annotated tag message
- Implementation of the automated blob scan verification script
- Whether `*.key` should be a blanket extension or specifically target the logo file path

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project context
- `.planning/PROJECT.md` -- Core value (no PII in public repo), constraints, key decisions
- `.planning/REQUIREMENTS.md` -- GIT-03 (history rewrite) and VER-02 (v1.0.0 tag) are Phase 2 requirements

### PII inventory
- `.planning/codebase/CONCERNS.md` -- Full inventory of PII files, tracked artifacts, security considerations, and file paths that must be purged
- `.planning/codebase/STRUCTURE.md` -- Directory layout showing which paths contain sensitive content

### Phase 1 context
- `.planning/phases/01-working-tree-sanitization/01-CONTEXT.md` -- Decisions about which files were removed from tracking, gitignore patterns, and file removal scope

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `.gitignore` (created in Phase 1) -- Defines all patterns that should be excluded; the same patterns inform BFG targeting
- Phase 1's `git rm --cached` results -- Files are untracked in HEAD but still in history; BFG cleans the history

### Established Patterns
- 16 commits in history, all from Phase 1/GSD planning work (no PII in commit messages)
- .git is 275 MB; largest blobs: backup tar.gz (20MB), classifications.jsonl (13MB), Keynote logo (11MB), real CV PDFs (10MB+), node_modules binaries (9MB+)

### Integration Points
- BFG requires a bare/mirror clone to operate on; result must be pushed back or replace the current .git
- After history rewrite, all commit hashes change -- no external references exist (no forks, no CI), so this is safe
- v1.0.0 tag is consumed by Phase 3 (CHANGELOG references it) and VER-02 requirement

</code_context>

<specifics>
## Specific Ideas

- User wants logo and design artifacts copied to `~/Dropbox/Projects/CViche - Planning/` before purge — follows the Projects/ directory convention for non-code artifacts
- Dry run before actual rewrite is non-negotiable — review what BFG would remove before executing
- The 16-commit history has value (Phase 1 work) and should be preserved, not squashed

</specifics>

<deferred>
## Deferred Ideas

None -- discussion stayed within phase scope

</deferred>

---

*Phase: 02-history-rewrite-and-tagging*
*Context gathered: 2026-03-22*
