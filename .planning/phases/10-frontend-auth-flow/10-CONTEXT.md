# Phase 10: Frontend Auth Flow - Context

**Gathered:** 2026-03-25
**Status:** Ready for planning

<domain>
## Phase Boundary

Adapt the login page to render either an SSO button or the existing email form based on the active auth mode returned by `GET /api/auth/config`. SAML error display is already implemented (Phase 8/9). This phase wires up mode detection and conditional rendering.

</domain>

<decisions>
## Implementation Decisions

### Claude's Discretion
User deferred all decisions. The following approach is based on established codebase patterns and prior phase decisions:

**Mode detection:**
- AuthContext.tsx fetches `GET /api/auth/config` on mount alongside existing `/api/auth/me` call
- Auth config stored in context state (`authMode: "simple" | "saml"`, `discoveryUrl: string | null`)
- While config is loading, LoginPage shows a minimal loading state (spinner inside the existing card — no flash of wrong form)
- If config fetch fails, default to `"simple"` mode (safe fallback — email form still works)

**SSO button design:**
- Single "Sign in with WCM SSO" button replacing the email form when `mode === "saml"`
- Uses existing `primary-600` styling for visual consistency with the current "Sign In" button
- Shield/lock icon from lucide-react (consistent with existing icon usage: LogIn, Loader2)
- Same card layout, logo, and background — only the form content changes
- Subtext below button: "You will be redirected to Weill Cornell Medicine sign-in"

**Transition behavior:**
- SSO button click: full-page redirect to `discovery_url` (standard SAML redirect, same tab)
- Show brief "Redirecting to WCM sign-in..." with spinner after click (covers any redirect delay)
- No new tab — SAML POST-back to ACS won't work cross-tab

**Conditional rendering:**
- `mode === "simple"`: show existing email form exactly as-is (zero changes to simple mode)
- `mode === "saml"`: show SSO button, hide email form completely
- Error banner display works in both modes (already implemented — `samlError` state + URL param parsing)

**Mode guard UX:**
- No auto-refresh on mode change — config only fetched on mount
- If someone bookmarks `/login` and mode changes, they see the new mode on next visit
- This matches how the backend mode guard works (checked per-request, not pushed)

**Simple mode subtitle update:**
- In simple mode, keep existing subtitle: "Enter your name and WCM email to get started."
- In SAML mode, replace with: "Use your Weill Cornell Medicine credentials to sign in."

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Frontend auth (current implementation)
- `web_interface/frontend/src/contexts/AuthContext.tsx` — Auth state management, `/api/auth/me` call, login/logout functions. Add config fetch and mode state here.
- `web_interface/frontend/src/components/LoginPage.tsx` — Login form, SAML error display (already implemented). Conditional rendering target.
- `web_interface/frontend/src/components/ErrorBanner.tsx` — Reusable error banner (already used by LoginPage for SAML errors)

### Backend config endpoint (Phase 7)
- `web_interface/backend/app/api/auth_routes.py` — `GET /api/auth/config` returns `{ "mode": "simple" }` or `{ "mode": "saml", "discovery_url": "..." }`

### SAML routes (Phase 8)
- `web_interface/backend/app/api/saml_routes.py` — SAML login redirect endpoint (`GET /api/saml/login`), ACS, logout. SSO button triggers the SAML login redirect.

### Prior phase context
- `.planning/phases/07-config-model-foundation/07-CONTEXT.md` — Config endpoint shape decision
- `.planning/phases/08-saml-sp-client-endpoints/08-CONTEXT.md` — Error experience decisions, SAML flow
- `.planning/phases/09-ed-group-authorization/09-CONTEXT.md` — ED error messages already wired into LoginPage

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `LoginPage.tsx`: Full login UI with card layout, WCM branding, form validation, SAML error display — add conditional rendering
- `AuthContext.tsx`: Auth state provider with `refreshUser` pattern — extend with config fetch
- `ErrorBanner.tsx`: Already used for SAML/ED errors — no changes needed
- lucide-react icons: `LogIn`, `Loader2` already imported — add `Shield` or `Lock` for SSO button

### Established Patterns
- Auth state managed in `AuthContext` via `useState` + `useEffect` on mount
- API calls use `fetch()` with relative URLs (Vite proxy to backend)
- Loading states use `Loader2` spinner from lucide-react
- Button styling: `bg-primary-600 text-white py-3 px-4 rounded-lg font-semibold`
- Error display: `ErrorBanner` component with dismiss callback

### Integration Points
- `AuthContext.tsx`: Add `authConfig` state, fetch on mount, expose to consumers
- `LoginPage.tsx`: Read `authConfig` from `useAuth()`, conditionally render SSO button vs email form
- No new files needed — this is purely modifying two existing files
- No new routes needed — SSO button links to existing `/api/saml/login` endpoint

</code_context>

<specifics>
## Specific Ideas

No specific requirements — user deferred all decisions to Claude's judgment. Decisions above are based on:
- Existing LoginPage.tsx patterns (card layout, button styling, error handling)
- Phase 7 config endpoint contract (`mode` + `discovery_url`)
- Phase 8 SAML login endpoint (`GET /api/saml/login` handles redirect)
- Standard SAML UX patterns (full-page redirect, no popup/new tab)

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 10-frontend-auth-flow*
*Context gathered: 2026-03-25*
