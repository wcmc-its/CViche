---
phase: 02-history-rewrite-and-tagging
plan: 02
subsystem: infra
tags: [git, git-filter-repo, history-rewrite, pii-cleanup, gc, repack]

# Dependency graph
requires:
  - phase: 02-history-rewrite-and-tagging
    plan: 01
    provides: Clean HEAD (no src/logs/ files), BFG installed, mirror backup at ~/Dropbox/Projects/CViche - Planning/
provides:
  - Git history rewritten to remove all PII blobs (PDFs, CVs, databases, auth config, prompt logs)
  - .git directory reduced from 275 MB to 3.6 MB
  - Working tree intact with 375 tracked files including protected .docx templates
  - Exhaustive blob scan confirms zero surviving sensitive objects
affects: [02-03-PLAN (verification and v1.0.0 tagging)]

# Tech tracking
tech-stack:
  added: [git-filter-repo]
  patterns: [mirror-clone-and-replace for history rewriting, bare-to-non-bare conversion via git read-tree + checkout-index]

key-files:
  created: []
  modified: [.git]

key-decisions:
  - "Used git-filter-repo instead of BFG due to BFG LargeObjectException on large tree objects"
  - "Mirror-clone-and-replace strategy: cleaned /tmp/cviche-clean.git, then cp -a as new .git"
  - "Used git read-tree + checkout-index instead of git reset --hard for bare-to-non-bare sync"

patterns-established:
  - "git-filter-repo as preferred history rewriting tool (handles large trees that crash BFG)"
  - "Bare repo conversion: set core.bare=false, then git read-tree HEAD + git checkout-index -a -f"

requirements-completed: [GIT-03]

# Metrics
duration: 19min
completed: 2026-03-22
---

# Phase 2 Plan 02: BFG Execution and .git Replacement Summary

**Rewrote git history using git-filter-repo to purge all PII blobs, reducing .git from 275 MB to 3.6 MB with zero surviving sensitive objects across 23 commits**

## Performance

- **Duration:** 19 min (includes checkpoint wait for user approval)
- **Started:** 2026-03-22T23:15:00Z
- **Completed:** 2026-03-22T23:34:00Z
- **Tasks:** 3 (1 auto + 1 checkpoint + 1 auto)
- **Files modified:** 1 (.git directory replaced)

## Accomplishments
- Purged all targeted file types from every historical commit: *.pdf, *.docx (except 2 templates), *.sqlite, *.db, *.key, *.jsonl, *.tar.gz
- Purged all targeted folders from history: data/, archive/, node_modules/, uploads/, prompt_logs/, prompt_logs_debug/, dist/, outputs/, logs/
- Purged auth_config.yaml (contained real email addresses) from history
- Reduced .git from 275 MB to 3.6 MB (98.7% reduction)
- Preserved both protected .docx templates via HEAD protection
- Exhaustive blob scan confirms zero sensitive objects survive in any commit
- Working tree intact: 375 tracked files, all critical files verified present

## Task Commits

This plan's tasks operate on the .git directory itself (history rewriting), not on tracked files. No conventional commits are produced by these tasks -- the entire commit history was rewritten with new hashes.

1. **Task 1: Create mirror clone and execute filter-repo passes** - Operations on /tmp/cviche-clean.git (bare mirror)
2. **Task 2: Review results before replacing .git** - Checkpoint: user typed "approved"
3. **Task 3: Replace original .git with cleaned mirror** - .git directory replaced; HEAD is now `e42bb89`

## Files Created/Modified
- `.git/` - Entire directory replaced with cleaned mirror (275 MB -> 3.6 MB)

## Decisions Made
- **git-filter-repo instead of BFG:** BFG threw a LargeObjectException when processing this repository's large tree objects. git-filter-repo handled the same operations without issue. The replacement tool provides identical functionality for the targeted use case (extension-based and path-based blob deletion).
- **Bare-to-non-bare conversion approach:** After copying the bare mirror as .git, used `git config --local core.bare false` followed by `git read-tree HEAD` and `git checkout-index -a -f` to sync the working tree with the rewritten HEAD.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] BFG LargeObjectException -- switched to git-filter-repo**
- **Found during:** Task 1 (mirror clone and BFG execution)
- **Issue:** BFG threw `org.eclipse.jgit.errors.LargeObjectException` when processing this repository's tree objects, preventing any history rewriting
- **Fix:** Used git-filter-repo with equivalent --path and --invert-paths options to achieve the same purge of targeted extensions, folders, and specific files
- **Files modified:** None (operations on /tmp/cviche-clean.git mirror)
- **Verification:** Mirror size 3.6 MB; exhaustive blob scan shows zero surviving sensitive objects
- **Impact:** None -- git-filter-repo produces identical results for this use case

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** Tool substitution was necessary and transparent. All acceptance criteria met identically.

## Issues Encountered
- BFG Repo Cleaner (v1.15.0) failed with LargeObjectException on this repository's tree objects. This is a known limitation of BFG's underlying jgit library with repositories that have very large tree objects. git-filter-repo (Python-based, uses native git) handled the same operations without issue. The plan's locked decision for BFG was overridden out of necessity.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- History is fully clean: exhaustive blob scan confirms zero PII blobs in any historical commit
- .git is 3.6 MB (well under 50 MB target)
- 23 commits with rewritten hashes but identical messages
- 375 tracked files including both protected .docx templates
- Safety backup preserved at ~/Dropbox/Projects/CViche - Planning/repo-backup-pre-rewrite.git (275 MB)
- Ready for Plan 03 (verification, human approval, v1.0.0 tag)

## Self-Check: PASSED

All claims verified:
- 02-02-SUMMARY.md: FOUND
- .git directory: FOUND (3.6 MB)
- Tracked files: 375
- Commit count: 23
- HEAD: e42bb89
- Template .docx files tracked: 3 (2 templates + 1 Python file with docx in name)
- .git.bak: cleaned up (not present)
- /tmp/cviche-clean.git: cleaned up (not present)
- Blob scan: zero sensitive objects found

---
*Phase: 02-history-rewrite-and-tagging*
*Completed: 2026-03-22*
