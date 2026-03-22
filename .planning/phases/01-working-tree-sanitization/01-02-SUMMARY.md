---
phase: 01-working-tree-sanitization
plan: 02
subsystem: infra
tags: [git-rm-cached, pii-removal, legacy-archive, working-tree-cleanup]

# Dependency graph
requires:
  - phase: 01-working-tree-sanitization plan 01
    provides: .gitignore with 14-category ignore rules, auth_config.yaml.example, requirements.txt
provides:
  - sanitized git index with 370 tracked files (down from 60,387)
  - src/legacy/ relocated to archive/legacy/ (gitignored)
  - zero PII files in git tracking (CVs, prompt logs, databases, auth config, node_modules)
  - WCM CV template preserved via .gitignore negation rule
affects: [phase-2 (history rewrite operates on clean working tree), phase-3 (documentation builds on clean repo)]

# Tech tracking
tech-stack:
  added: []
  patterns: [atomic commit for mass git rm --cached operations, git ls-files -i --exclude-standard for .gitignore-matched removal]

key-files:
  created:
    - archive/legacy/ (relocated from src/legacy/)
  modified:
    - .gitignore (staged with removals in atomic commit)

key-decisions:
  - "Single atomic commit for all git rm --cached operations plus new files, per user decision in CONTEXT.md"
  - "60,024 files removed from tracking in one commit (files remain on disk for local use)"
  - "src/legacy/ moved to archive/ before git rm to let git see it as a deletion"
  - "Explicit removal of _DEPRECATED/_backup files and runtime .txt/.md artifacts from src/unified_pipeline/core/"

patterns-established:
  - "Archive pattern: deprecated code moves to archive/ directory which is gitignored"
  - "Post-removal audit: 10-check verification matrix for working tree cleanliness"

requirements-completed: [GIT-02]

# Metrics
duration: 4min
completed: 2026-03-22
---

# Phase 1 Plan 2: Execute git rm --cached, Move Legacy to Archive, and Audit Summary

**Removed 60,024 files from git tracking via atomic git rm --cached, relocated src/legacy/ to archive/, verified zero PII remains tracked across 10 audit checks**

## Performance

- **Duration:** ~4 min (across checkpoint pause)
- **Started:** 2026-03-22T15:38:00Z
- **Completed:** 2026-03-22T15:55:00Z
- **Tasks:** 3 (1 execution + 1 audit + 1 human verification checkpoint)
- **Files modified:** 60,024 removed from tracking, 1 re-added (WCM template)

## Accomplishments
- Removed 60,024 files from git tracking in a single atomic commit (files remain on disk for local use)
- Relocated src/legacy/ (stage-based extraction code) to archive/legacy/ which is covered by .gitignore
- Eliminated all PII from git index: 488 faculty CVs in data/, 150 uploaded CVs, 42,013 prompt logs, 8,044 node_modules files, 4,539 dist/ files, SQLite databases, and auth config with real emails
- Preserved WCM CV template via .gitignore negation rule (verified tracked)
- Passed all 10 post-removal audit checks with zero failures
- Reduced tracked file count from 60,387 to 370

## Task Commits

Each task was committed atomically:

1. **Task 1: Move src/legacy/, remove deprecated/backup files, execute mass git rm --cached** - `852ae263` (chore)
2. **Task 2: Post-removal audit** - read-only verification, no commit
3. **Task 3: Verify sanitized working tree** - human checkpoint, approved

## Files Created/Modified
- `archive/legacy/` - Relocated from src/legacy/ (gitignored, stays on disk)
- `.gitignore` - Staged as part of atomic commit (created in Plan 01)
- `requirements.txt` - Staged as part of atomic commit (created in Plan 01)
- `web_interface/backend/auth_config.yaml.example` - Staged as part of atomic commit (created in Plan 01)
- `key_files/wcm_cv_template_faculty_october_2022_final.docx` - Re-added after git rm to honor negation rule

## Decisions Made
- Combined all git rm --cached operations and new file staging into a single atomic commit per CONTEXT.md decision
- Used `git ls-files -i --exclude-standard -z | xargs -0 git rm --cached` for bulk removal (macOS-compatible)
- Explicitly removed _DEPRECATED and _backup files not covered by .gitignore patterns
- Explicitly removed 7,238 .txt runtime artifacts and 22 operational .md files from src/unified_pipeline/core/

## Deviations from Plan
None - plan executed exactly as written.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Working tree is fully sanitized: 370 tracked files, zero PII in git index
- Phase 2 (History Rewrite and Tagging) can proceed: clean working tree is the prerequisite
- The .git directory still contains historical PII in commit objects -- Phase 2 will purge this with BFG or git filter-repo
- All Phase 1 success criteria from ROADMAP.md are met

## Issues Encountered
None - all operations completed as expected.

## Self-Check: PASSED

- FOUND: 01-02-SUMMARY.md on disk
- FOUND: commit 852ae263 in git log
- FOUND: archive/legacy/ directory on disk
- TRACKED FILES: 370 (target: under 3,000)
- PII FILES: 0 (target: 0)
- FOUND: WCM template tracked via negation rule

All claims verified.

---
*Phase: 01-working-tree-sanitization*
*Completed: 2026-03-22*
