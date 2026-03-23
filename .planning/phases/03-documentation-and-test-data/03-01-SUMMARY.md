---
phase: 03-documentation-and-test-data
plan: 01
subsystem: documentation
tags: [apache-2.0, changelog, versioning, python-docx, gitignore, synthetic-cv]

# Dependency graph
requires:
  - phase: 02-history-rewrite-and-tagging
    provides: clean git history with v1.0.0 tag
provides:
  - Apache 2.0 LICENSE file
  - CHANGELOG.md with v1.0.0 entry in Keep a Changelog format
  - __version__ = '1.0.0' importable from unified_pipeline
  - Synthetic sample CV (sample_vasquez_cv.docx) with 16 heading styles and 15+ sections
  - .gitignore negation chain for tracking sample CV inside ignored data/ directory
affects: [03-02, 03-03]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Layered gitignore negation: data/* + !dir/ + dir/* at each level to allow tracking specific files inside ignored directories"
    - "__version__ in __init__.py for package version accessibility"

key-files:
  created:
    - LICENSE
    - CHANGELOG.md
    - data/sample_cvs/word/sample_vasquez_cv.docx
  modified:
    - src/unified_pipeline/__init__.py
    - .gitignore

key-decisions:
  - "Changed data/ to data/* in .gitignore to enable negation (directory-level ignores prevent git from descending into the directory)"
  - "Used layered negation pattern (un-ignore dir, re-ignore dir/*, un-ignore next level) to track sample CV while keeping real CVs ignored"

patterns-established:
  - "Layered gitignore negation for deeply nested files inside ignored directories: data/* + !data/sample_cvs/ + data/sample_cvs/* + !data/sample_cvs/word/ + data/sample_cvs/word/* + !specific_file"

requirements-completed: [DOC-02, DOC-03, VER-03, TEST-01]

# Metrics
duration: 5min
completed: 2026-03-23
---

# Phase 3 Plan 1: Foundational Files Summary

**Apache 2.0 LICENSE, Keep a Changelog v1.0.0 entry, __version__ = '1.0.0', and synthetic 16-section sample CV with layered .gitignore negation for tracking**

## Performance

- **Duration:** 5 min
- **Started:** 2026-03-23T04:10:24Z
- **Completed:** 2026-03-23T04:15:30Z
- **Tasks:** 3
- **Files modified:** 5

## Accomplishments
- Apache 2.0 LICENSE file at repository root with official license text
- CHANGELOG.md with v1.0.0 entry (2026-03-22) covering all 12 pipeline stages, web interface, auth, and infrastructure
- __version__ = '1.0.0' importable from unified_pipeline package
- Synthetic sample CV (Elena M. Vasquez, MD, PhD) with 16 Word heading styles, 10 publications using real journal names, and comprehensive section coverage for all pipeline stages
- .gitignore negation chain enabling git tracking of sample CV while keeping real CVs (PII) properly ignored

## Task Commits

Each task was committed atomically:

1. **Task 1: Create LICENSE, CHANGELOG.md, and __version__** - `0654fb1` (feat)
2. **Task 3: Update .gitignore with negation rules** - `87a04f1` (chore)
3. **Task 2: Generate synthetic sample CV** - `8abd34c` (feat)

Note: Tasks 2 and 3 were committed in reversed order because the .gitignore negation rules (Task 3) must be in place before the sample CV (Task 2) can be tracked by git.

## Files Created/Modified
- `LICENSE` - Apache License 2.0 full text
- `CHANGELOG.md` - Keep a Changelog format with v1.0.0 entry listing all major features
- `src/unified_pipeline/__init__.py` - Added __version__ = '1.0.0' (was empty)
- `data/sample_cvs/word/sample_vasquez_cv.docx` - Synthetic sample CV for pipeline testing
- `.gitignore` - Layered negation rules for sample CV tracking

## Decisions Made
- Changed `data/` to `data/*` in .gitignore because directory-level ignores (trailing slash) prevent git from descending into the directory, making negation rules for nested files impossible. The wildcard pattern `data/*` ignores all contents while still allowing git to evaluate negation rules.
- Used layered negation pattern (un-ignore directory, re-ignore its children with wildcard, un-ignore next level) to ensure only the specific sample CV file is trackable while all other files in data/sample_cvs/word/ remain ignored.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Fixed .gitignore negation chain for directory-level ignores**
- **Found during:** Task 3 (Update .gitignore with negation rules)
- **Issue:** The plan specified adding `!data/sample_cvs/`, `!data/sample_cvs/word/`, and `!data/sample_cvs/word/sample_vasquez_cv.docx` after the existing `data/` rule. However, git's `data/` pattern ignores the directory itself, preventing git from descending into it. Negation rules for paths inside the directory are never evaluated. The existing `key_files/` + `!key_files/template.docx` pattern only appeared to work because the template file was already tracked before the .gitignore rule was added.
- **Fix:** Changed `data/` to `data/*` (wildcard ignores contents, not the directory) and used a layered negation pattern: `!data/sample_cvs/` + `data/sample_cvs/*` + `!data/sample_cvs/word/` + `data/sample_cvs/word/*` + `!data/sample_cvs/word/sample_vasquez_cv.docx`. This un-ignores each directory level while re-ignoring its children, ensuring only the specific sample file is trackable.
- **Files modified:** .gitignore
- **Verification:** `git add data/sample_cvs/word/sample_vasquez_cv.docx` succeeds without -f flag; `git add data/` only stages the sample CV; real CVs remain ignored
- **Committed in:** 87a04f1

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** Essential fix for correctness. The plan's original negation approach would have silently failed, leaving the sample CV untrackable. No scope creep.

## Issues Encountered
None beyond the deviation documented above.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- LICENSE, CHANGELOG.md, and __version__ are in place for README references (Plan 03)
- Sample CV is tracked and available for pipeline demonstration in README examples
- .gitignore negation pattern established for any future sample data files
- Plan 02 (docs reorganization) and Plan 03 (README creation) can proceed

## Self-Check: PASSED

All 5 files verified present. All 3 commit hashes verified in git log.

---
*Phase: 03-documentation-and-test-data*
*Completed: 2026-03-23*
