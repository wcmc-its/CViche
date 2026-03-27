---
phase: 17-run-history-ux
verified: 2026-03-27T18:30:00Z
status: passed
score: 5/5 must-haves verified
re_verification: false
---

# Phase 17: Run History UX Verification Report

**Phase Goal:** Previous Runs table on the upload page gives users clear, glanceable run context
**Verified:** 2026-03-27T18:30:00Z
**Status:** passed
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Runs less than 24 hours old display relative time ('just now', 'N minutes ago', 'N hours ago') | VERIFIED | `formatRelativeDate` in format.ts lines 88-101 implements all three sub-thresholds: `diffSec < 60` → 'just now', `diffMin < 60` → 'N minutes ago', `diffHr < 24` → 'N hours ago' |
| 2 | Runs 24+ hours old display absolute date ('Mar 26, 2:30 PM' for current year, 'Mar 26, 2025, 2:30 PM' for previous year) | VERIFIED | `formatRelativeDate` falls through to `formatDate(dateStr)` at line 105 for `>= 24h`; `formatDate` conditionally includes year via `d.getFullYear() !== now.getFullYear()` (format.ts line 10) |
| 3 | Hovering over a relative-time date shows a tooltip with the full date+time | VERIFIED | RunHistory.tsx line 358: `<span title={tooltip}>{display}</span>`; tooltip is set for all relative-time branches in `formatRelativeDate`, `undefined` for absolute dates (no attribute rendered) |
| 4 | The date column is wide enough that no date text is clipped or truncated | VERIFIED | RunHistory.tsx line 290: `min-w-[170px]` (widened from 140px per plan) |
| 5 | Runs with submitted feedback show a visible green 'Feedback given' badge | VERIFIED | RunHistory.tsx lines 368-373: `feedbackMap[run.run_id] === true` renders green badge with `MessageSquare` icon and "Feedback given" text |

**Score:** 5/5 truths verified

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `web_interface/frontend/src/utils/format.ts` | formatRelativeDate() and formatFullDateTime() functions | VERIFIED | File exists, substantive (107 lines), contains both function definitions |
| `web_interface/frontend/src/utils/index.ts` | Re-exports formatRelativeDate | VERIFIED | Line 1 exports `formatRelativeDate` from './format' |
| `web_interface/frontend/src/components/RunHistory.tsx` | Date cell using formatRelativeDate with tooltip, wider column | VERIFIED | Imports `formatRelativeDate`, uses it in date cell with IIFE+tooltip, column widened to 170px |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `RunHistory.tsx` | `utils/format.ts` | `import { formatRelativeDate } from '../utils'` | WIRED | Line 4 of RunHistory.tsx imports `formatRelativeDate`; used at lines 357-358 with `run.started_at` |
| `formatRelativeDate` | `formatDate` (existing) | internal call for >= 24h fallback | WIRED | format.ts line 105: `return { display: formatDate(dateStr) }` — falls through to absolute formatter for old runs |

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| UX-01 | 17-01-PLAN.md | Previous Runs table shows a feedback indicator for runs that have submitted feedback | SATISFIED | Green "Feedback given" badge (lines 368-373), amber "Needs feedback" button (lines 374-384) already present; SUMMARY confirms these were untouched per plan |
| UX-02 | 17-01-PLAN.md | Date column shows relative time for runs < 24h old, full date for older runs | SATISFIED | `formatRelativeDate` implements all thresholds; wired into RunHistory date cell |
| UX-03 | 17-01-PLAN.md | Date column wide enough to display full date without clipping | SATISFIED | `min-w-[170px]` on date column header (RunHistory.tsx line 290) |

No orphaned requirements: REQUIREMENTS.md maps UX-01, UX-02, UX-03 to Phase 17; all three are claimed in 17-01-PLAN.md frontmatter.

---

### Anti-Patterns Found

None. Scanned `format.ts`, `index.ts`, and `RunHistory.tsx` for TODO/FIXME/placeholder, empty returns, and stub handlers. All clear.

---

### Human Verification Required

#### 1. Relative time display in browser

**Test:** Open the upload page in a browser while at least one run exists that was started within the last hour.
**Expected:** The date cell for that run shows text like "5 minutes ago" rather than a formatted date string.
**Why human:** Time-based rendering cannot be verified statically; requires a live browser session with real run data.

#### 2. Tooltip on hover for recent runs

**Test:** Hover the mouse over a relative-time date cell (e.g., "5 minutes ago").
**Expected:** A browser native tooltip appears showing the full date and time (e.g., "Mar 27, 2026, 2:18 PM").
**Why human:** HTML `title` tooltip behavior is a browser interaction that cannot be verified from code inspection alone.

#### 3. No clipping on older date strings

**Test:** View the table with a run that has a previous-year date (e.g., "Mar 26, 2025, 2:30 PM").
**Expected:** The full date string is visible without truncation or ellipsis.
**Why human:** Visual column width behavior requires a rendered browser viewport to confirm no text overflow.

---

### Commits Verified

| Hash | Message |
|------|---------|
| `44897f2` | feat(17-01): add formatRelativeDate and formatFullDateTime in utils/format.ts |
| `1221562` | feat(17-01): wire formatRelativeDate into RunHistory date cell and widen column |

Both commits are present in the main branch git log (merged via `a13d292`).

---

### Summary

All five must-have truths are verified in the actual codebase with no stubs, placeholders, or orphaned artifacts. The implementation matches the plan specification exactly:

- `format.ts` contains a complete, non-stub `formatRelativeDate` with all four time thresholds and correct singular handling.
- `index.ts` re-exports `formatRelativeDate` cleanly.
- `RunHistory.tsx` imports and uses `formatRelativeDate`, renders the date cell with IIFE destructuring and `title={tooltip}`, has the date column widened to `min-w-[170px]`, and preserves both feedback badge variants untouched.
- All three requirement IDs (UX-01, UX-02, UX-03) are satisfied with direct code evidence.

Three items are flagged for human browser verification (live time rendering, tooltip behavior, column width visual confirmation) — these cannot be verified statically but are low-risk given the direct code evidence.

---

_Verified: 2026-03-27T18:30:00Z_
_Verifier: Claude (gsd-verifier)_
