---
phase: 15-frontend-architecture
plan: 01
subsystem: ui
tags: [typescript, react, vite, api-client, websocket, lucide-react]

# Dependency graph
requires:
  - phase: 14-backend-service-layer
    provides: "Backend service layer with typed API routes"
provides:
  - "Shared TypeScript type definitions for all API responses/requests in src/types/"
  - "Typed API client module with 28 endpoint functions in src/api/"
  - "WebSocket URL builder deriving from env or window.location in src/api/websocket.ts"
  - "Formatting utilities (formatDate, formatDateShort, formatDuration, formatCost) in src/utils/"
  - "Status utilities (statusLabel, statusLabelColor) in src/utils/"
  - "Shared StatusIcon component in src/components/shared/"
affects: [15-02-PLAN]

# Tech tracking
tech-stack:
  added: []
  patterns: [api-client-module, barrel-re-exports, typed-fetch-wrapper, raw-response-pattern]

key-files:
  created:
    - web_interface/frontend/src/vite-env.d.ts
    - web_interface/frontend/src/types/index.ts
    - web_interface/frontend/src/api/client.ts
    - web_interface/frontend/src/api/index.ts
    - web_interface/frontend/src/api/websocket.ts
    - web_interface/frontend/src/utils/format.ts
    - web_interface/frontend/src/utils/status.ts
    - web_interface/frontend/src/components/shared/StatusIcon.tsx
  modified: []

key-decisions:
  - "API client defaults to empty string for VITE_API_URL to work with Vite proxy in dev"
  - "getRuns uses getRaw + array normalization for backward compat with legacy array response"
  - "submitFeedback returns raw Response for status-specific handling (201/409/422/403/404)"
  - "statusLabelColor default normalized to text-gray-600 per UI-SPEC contract"

patterns-established:
  - "api module pattern: typed functions import from ./client and ../types, barrel re-exported via index.ts"
  - "postRaw/getRaw for endpoints needing raw Response (feedback submit, CSV export)"
  - "FormData guard: body instanceof FormData ? undefined : Content-Type header"
  - "WebSocket URL derivation: VITE_API_URL env var first, window.location fallback"

requirements-completed: [FE-01, FE-02, FE-03, FE-04]

# Metrics
duration: 4min
completed: 2026-03-27
---

# Phase 15 Plan 01: Shared Foundation Summary

**20 new foundation modules: TypeScript types for all API shapes, typed fetch client with 28 endpoints + WebSocket builder, formatting/status utilities, and shared StatusIcon component**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-27T00:26:33Z
- **Completed:** 2026-03-27T00:30:39Z
- **Tasks:** 3
- **Files created:** 20

## Accomplishments
- 7 type files in src/types/ export all API response/request interfaces matching existing component shapes
- 9 files in src/api/ provide typed functions for all fetch endpoints plus environment-driven WebSocket URL builder
- 4 files in src/utils/ and src/components/shared/ consolidate formatting, status, and StatusIcon logic
- TypeScript compilation and Vite production build both pass with zero errors
- No existing component files were modified

## Task Commits

Each task was committed atomically:

1. **Task 1: Create shared type definitions and environment config** - `4fb1744` (feat)
2. **Task 2: Create API client module with typed endpoint functions and WebSocket helper** - `0d69a73` (feat)
3. **Task 3: Create formatting utilities and shared StatusIcon component** - `0b42dd5` (feat)

## Files Created
- `src/vite-env.d.ts` - Vite environment type declaration with VITE_API_URL
- `src/types/auth.ts` - User, ConsentStatus, AuthConfig interfaces
- `src/types/runs.ts` - RunStatus, RunSummary, StepSummary, FeedbackStatus, PaginatedRuns
- `src/types/feedback.ts` - FeedbackFormData, WcmSection interfaces
- `src/types/admin.ts` - Stats, AdminUser, AdminRun, AdminRunsResponse, SystemConfig, FeedbackData, AggregatedScores
- `src/types/upload.ts` - Estimate interface
- `src/types/index.ts` - Barrel re-export of all types
- `src/api/client.ts` - Base fetch wrapper with error handling, FormData guard, postRaw/getRaw
- `src/api/auth.ts` - getAuthConfig, getCurrentUser, login, logout
- `src/api/runs.ts` - getRunStatus, cancelRun, restartRun, getRuns, getFeedbackStatuses, startRun
- `src/api/feedback.ts` - getFeedback, submitFeedback (raw Response)
- `src/api/admin.ts` - getAdminStats, getAdminUsers, updateAdminUser, getAdminRuns, getAdminConfig, updateAdminConfig, exportFeedbackCsv
- `src/api/consent.ts` - getConsentStatus, submitConsent
- `src/api/upload.ts` - getEstimate, uploadFile
- `src/api/websocket.ts` - getWebSocketUrl from env or window.location
- `src/api/index.ts` - Barrel re-export of all API functions
- `src/utils/format.ts` - formatDate, formatDateShort, formatDuration, formatCost
- `src/utils/status.ts` - statusLabel, statusLabelColor
- `src/utils/index.ts` - Barrel re-export of all utilities
- `src/components/shared/StatusIcon.tsx` - Shared StatusIcon with lucide-react icons

## Decisions Made
- API client base URL defaults to empty string (not localhost) so requests go through Vite proxy in dev
- getRuns uses getRaw + manual JSON parsing to normalize both array and paginated response formats
- submitFeedback returns raw Response because FeedbackForm needs status-specific handling (201/409/422/403/404)
- exportFeedbackCsv returns raw Response for non-JSON CSV content
- statusLabelColor default changed from text-gray-500 to text-gray-600 per UI-SPEC contract

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- All shared foundation modules ready for Plan 02 component migration
- Plan 02 will import from src/types/, src/api/, src/utils/, and src/components/shared/
- Existing components remain unchanged and functional

## Self-Check: PASSED

All 20 created files verified present. All 3 task commits verified in git log. TypeScript and Vite build both pass.

---
*Phase: 15-frontend-architecture*
*Completed: 2026-03-27*
