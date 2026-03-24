# Phase 5: Run History Redesign - Context

**Gathered:** 2026-03-23
**Status:** Ready for planning

<domain>
## Phase Boundary

Replace the current card-based run history list with a scannable, sortable table layout. Users can quickly find previous pipeline runs by sorting any column and scanning rows at a glance. This is a frontend-only redesign of RunHistory.tsx — no backend API changes.

</domain>

<decisions>
## Implementation Decisions

### Table columns & data density
- Six columns: Status | Filename | Date | Duration | Cost | Feedback
- All six columns match ROADMAP success criteria (filename, date, duration, cost, status, feedback status)
- Long filenames wrap to multiple lines (no truncation)
- Status column shows icon + text label (e.g., green checkmark + "Complete", spinning blue + "Running", red X + "Failed")
- Date column uses absolute time format: "Mar 23, 2:15 PM" (current format, no relative time)
- Cost column formatted to 2 decimal places: "$0.12"
- Duration column: "3m 42s" format (current behavior)
- Feedback column: keep current badge design — green "Feedback given" pill and amber clickable "Needs feedback" pill
- Non-complete runs show available data (cost so far, duration if available); dash for missing values
- Sticky header row so column labels stay visible when scrolling through many runs

### Sort behavior
- Default sort: date descending (most recent first)
- All six columns sortable
- Active sort column indicated by up/down chevron arrow icon; other columns show subtle neutral icon on hover to indicate they're sortable
- Client-side sorting (sort within loaded data, no API changes)
- Changing sort column resets to page 1

### Row interaction & navigation
- Clicking anywhere on a row navigates to /run/{id} (PipelineViewer) — same as current card behavior
- "Needs feedback" badge still navigates to /run/{id}#feedback (Phase 4 behavior preserved)
- Hover state: subtle gray background highlight (consistent with current hover:bg-gray-50 pattern), pointer cursor
- Alternating row backgrounds (zebra stripes) for easier horizontal scanning
- Running rows get a faint blue-tinted background for emphasis (in addition to the spinning status icon)

### Pagination
- Page numbers at bottom of table (classic table pagination: < 1 2 3 ... N >)
- 100 rows per page (effectively single-page for most users, pagination only for heavy users)
- "Showing X-Y of Z runs" summary displayed next to page numbers
- Sort changes reset to page 1

### Claude's Discretion
- Exact zebra stripe and running-row tint colors
- Page number styling (active page indicator)
- Column width distribution and responsive behavior
- Empty state when no runs exist (currently returns null — table version may want a message)
- Mobile/narrow viewport handling
- Keyboard navigation between rows (tab/arrow keys)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Current implementation (replace this)
- `web_interface/frontend/src/components/RunHistory.tsx` — Current card-based layout to be replaced with table. Contains StatusIcon helper, data fetching, pagination, feedback badge logic.
- `web_interface/frontend/src/components/UploadPage.tsx` — Parent component that renders RunHistory with `onSelectRun` prop

### Backend API (no changes needed)
- `web_interface/backend/app/api/runs.py` — GET /api/runs?offset=&limit= returns paginated RunSummary list
- `web_interface/backend/app/api/feedback_routes.py` — GET /api/runs/feedback-status returns feedback status per run
- `web_interface/backend/app/schemas.py` (RunSummary, PaginatedRuns, RunFeedbackStatus) — Response schemas with all field names

### Design system
- `web_interface/frontend/tailwind.config.js` — Color tokens, typography, spacing
- `web_interface/frontend/src/index.css` — Custom CSS classes

### Phase 4 integration
- `web_interface/frontend/src/components/FeedbackForm.tsx` — Feedback form lives in PipelineViewer; RunHistory "Needs feedback" badge links to /run/{id}#feedback

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- **StatusIcon component**: Already defined in RunHistory.tsx — maps status string to colored Lucide icon. Reuse directly, add text labels alongside.
- **Lucide icons**: CheckCircle2, Loader2, XCircle, AlertCircle, Clock, DollarSign, FileText, MessageSquare, ChevronDown already imported
- **ErrorBanner component**: Available for error states
- **Tailwind design tokens**: primary-600/700 for interactive elements, success-600 for confirmations, consistent rounded-lg/shadow-lg card container pattern

### Established Patterns
- **Data fetching**: Native fetch() with useState for loading/error states. Current RunHistory fetches runs and feedback status in parallel with Promise.all.
- **Card container**: `bg-white/95 backdrop-blur-sm rounded-lg shadow-lg p-6` — table should live inside similar container for visual consistency with rest of UI
- **Pagination API**: Backend already supports `?offset=&limit=` parameters. Currently uses limit=20, will change to limit=100.

### Integration Points
- **UploadPage.tsx**: RunHistory is rendered via `<RunHistory onSelectRun={(runId) => navigate(`/run/${runId}`)} />` — interface stays the same
- **React Router navigate**: Used for row clicks and feedback badge navigation
- **Feedback status map**: Already fetched as Record<string, boolean> keyed by run_id

</code_context>

<specifics>
## Specific Ideas

- The table should feel like a natural data table (think admin dashboards, not spreadsheets) — clean, well-spaced rows, not cramped
- Keep the existing card container (bg-white/95 backdrop-blur-sm rounded-lg shadow-lg) as the wrapper for the table to maintain visual consistency with UploadPage
- The "Needs feedback" badge click → /run/{id}#feedback navigation from Phase 4 must be preserved exactly

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 05-run-history-redesign*
*Context gathered: 2026-03-23*
