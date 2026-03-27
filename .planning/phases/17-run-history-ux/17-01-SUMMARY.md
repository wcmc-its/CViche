---
phase: 17-run-history-ux
plan: 01
subsystem: ui
tags: [react, typescript, date-formatting, tailwind, relative-time]

# Dependency graph
requires:
  - phase: 05-run-history-redesign
    provides: RunHistory table component with date column and feedback badges
provides:
  - formatRelativeDate() utility returning { display, tooltip } for glanceable relative time
  - formatFullDateTime() utility for tooltip date+time display
  - Updated formatDate() with conditional year inclusion for non-current-year dates
  - Widened date column (min-w-[170px]) preventing date text clipping
affects: [run-history, admin-submissions, date-formatting]

# Tech tracking
tech-stack:
  added: []
  patterns: [return-object-from-formatter, html-title-tooltip, iife-in-jsx-for-destructuring]

key-files:
  created:
    - web_interface/frontend/src/utils/format.ts
    - web_interface/frontend/src/utils/index.ts
  modified:
    - web_interface/frontend/src/components/RunHistory.tsx

key-decisions:
  - "Created utils/format.ts and utils/index.ts in worktree (did not exist yet) with all format functions plus new formatRelativeDate and formatFullDateTime"
  - "formatRelativeDate returns { display, tooltip } object instead of string for co-located tooltip data"
  - "Used IIFE pattern in JSX for destructuring formatRelativeDate return value"
  - "Kept inline formatDuration in RunHistory.tsx unchanged (out of plan scope)"

patterns-established:
  - "Return object from formatter: formatRelativeDate returns { display, tooltip } to co-locate display text and tooltip"
  - "HTML title attribute for native browser tooltip on relative-time date cells"

requirements-completed: [UX-01, UX-02, UX-03]

# Metrics
duration: 3min
completed: 2026-03-27
---

# Phase 17 Plan 01: Run History UX Summary

**Relative time formatting for Recent Runs (< 24h shows 'N minutes ago' with tooltip) and date column widened to 170px**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-27T18:12:32Z
- **Completed:** 2026-03-27T18:15:39Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments
- Created formatRelativeDate() with four time thresholds (< 60s, < 60m, < 24h, >= 24h) returning display text and optional tooltip
- Created formatFullDateTime() for full date+time tooltip display
- Updated formatDate() with conditional year inclusion for non-current-year dates (D-05)
- Wired formatRelativeDate into RunHistory date cell with HTML title tooltip
- Widened date column from min-w-[140px] to min-w-[170px] to prevent clipping

## Task Commits

Each task was committed atomically:

1. **Task 1: Add formatRelativeDate and update formatDate year handling in format.ts** - `44897f2` (feat)
2. **Task 2: Wire formatRelativeDate into RunHistory date cell and widen column** - `1221562` (feat)

## Files Created/Modified
- `web_interface/frontend/src/utils/format.ts` - New file with formatDate (conditional year), formatDateShort, formatDuration, formatCost, formatFullDateTime, and formatRelativeDate
- `web_interface/frontend/src/utils/index.ts` - New file re-exporting all format functions including formatRelativeDate
- `web_interface/frontend/src/components/RunHistory.tsx` - Import formatRelativeDate from utils, replace date cell rendering with tooltip support, widen date column

## Decisions Made
- Created utils/format.ts and utils/index.ts as new files (worktree was behind main and did not have the utils directory yet) -- this is equivalent to what the plan intended
- formatRelativeDate returns an object { display, tooltip } rather than a plain string, co-locating the tooltip data with the display text per D-04
- Kept inline formatDuration in RunHistory.tsx unchanged since the plan only targeted the date formatting
- Used IIFE pattern in JSX `{(() => { ... })()}` for clean destructuring of formatRelativeDate return value

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Created utils/format.ts and utils/index.ts (files did not exist in worktree)**
- **Found during:** Task 1 (format.ts modification)
- **Issue:** Plan referenced modifying existing utils/format.ts and utils/index.ts, but these files did not exist in this worktree branch (created in a later phase on main)
- **Fix:** Created both files from scratch with content matching main repo plus new functions (formatRelativeDate, formatFullDateTime, updated formatDate)
- **Files modified:** web_interface/frontend/src/utils/format.ts (created), web_interface/frontend/src/utils/index.ts (created)
- **Verification:** Vite build passes, all imports resolve
- **Committed in:** 44897f2 (Task 1 commit)

**2. [Rule 3 - Blocking] Installed npm dependencies (node_modules missing in worktree)**
- **Found during:** Task 1 verification (Vite build)
- **Issue:** Worktree had no node_modules directory, causing Vite build to fail with ERR_MODULE_NOT_FOUND
- **Fix:** Ran npm install in the frontend directory
- **Files modified:** None committed (node_modules is gitignored)
- **Verification:** Vite build succeeds after npm install
- **Committed in:** N/A (runtime dependency, not committed)

---

**Total deviations:** 2 auto-fixed (2 blocking)
**Impact on plan:** Both auto-fixes necessary for worktree environment setup. No scope creep. Final code matches plan intent exactly.

## Issues Encountered
- RunHistory.tsx in this worktree had inline formatDate/formatDuration functions rather than importing from utils. Adapted by creating the utils module and only importing formatRelativeDate (the new function), leaving inline formatDuration untouched.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Relative time formatting complete and building cleanly
- All three UX requirements (UX-01 feedback badges, UX-02 relative dates, UX-03 column width) are addressed
- Ready for visual verification via browser testing

## Self-Check: PASSED

- All 4 files exist (format.ts, index.ts, RunHistory.tsx, 17-01-SUMMARY.md)
- Both commits verified (44897f2, 1221562)

---
*Phase: 17-run-history-ux*
*Completed: 2026-03-27*
