# Phase 13: Security Hardening - Context

**Gathered:** 2026-03-26
**Status:** Ready for planning

<domain>
## Phase Boundary

Harden the web interface with defense-in-depth security practices: validate uploads by content (not just extension), sanitize all error responses, add HTTP security headers, and lock down CORS to explicit allowlists. No new features — these are security controls applied to existing endpoints.

</domain>

<decisions>
## Implementation Decisions

### Upload Validation (SEC-04)
- Verify uploaded files using magic bytes (file signature) — reject files where the actual format doesn't match the claimed extension
- Use built-in signature checking (zipfile for .docx, PDF header bytes) — no external dependency like python-magic needed
- Store uploaded files with randomized filenames: `{run_id}.{ext}` — no user-provided text touches the filesystem
- Original filename preserved in the database `Run.filename` column only (already the case)
- Enforce a 50 MB file size limit on uploads — reject larger files with a clear error message
- Log rejected uploads at WARNING with `[SECURITY]` prefix (consistent with Phase 12 pattern)

### Error Sanitization (SEC-05)
- Add a global FastAPI exception handler that catches all unhandled exceptions
- Server-side: log the full traceback for debugging
- Client-side: return generic `{"error": "internal_error", "message": "An unexpected error occurred"}` — no file paths, stack traces, or implementation details
- Validation errors (HTTPException with 4xx status) MAY echo user input back (e.g., "Unsupported file type: .exe") since this is the user's own input, not server internals
- Debug mode via `CVICHE_DEBUG=true` env var: when enabled, error responses include the full traceback. Default is off (production-safe).
- Audit existing HTTPException `detail` values across all endpoints to strip any that leak internal paths or implementation details

### HTTP Security Headers (SEC-06)
- Add a security headers middleware that sets headers on ALL responses
- Content-Security-Policy: moderate strictness — `'self'` for scripts/styles plus `'unsafe-inline'` for styles only (React/Vite injects inline styles). Block all other sources.
- X-Frame-Options: DENY (CViche should never be framed)
- X-Content-Type-Options: nosniff
- Strict-Transport-Security: max-age with NO preload directive (app not yet in production; preload is permanent and premature)
- Referrer-Policy: strict-origin-when-cross-origin

### CORS Lockdown (SEC-07)
- Replace `allow_methods=["*"]` with explicit list: `["GET", "POST", "PUT", "DELETE"]`
- Replace `allow_headers=["*"]` with explicit list: `["Content-Type", "Authorization", "X-Requested-With"]`
- Keep existing env-configurable origins (`CVICHE_ALLOWED_ORIGINS`) — already properly scoped
- Keep `allow_credentials=True` — needed for session cookies

### Claude's Discretion
- Exact magic bytes signatures to check for .docx (ZIP PK header) and .pdf (PDF header)
- Whether to implement the security headers as Starlette BaseHTTPMiddleware or use a response hook
- Exact CSP directive values (connect-src, img-src, font-src details)
- Error response format for the global handler (error code, request ID, etc.)
- Test structure for new security controls

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Vulnerable/target files
- `web_interface/backend/app/api/upload.py` — Upload endpoint with extension-only validation and user-provided filenames (lines 43-117)
- `web_interface/backend/app/main.py` — CORS middleware config with wildcard methods/headers (lines 81-91), no security headers middleware

### Error handling across endpoints
- `web_interface/backend/app/api/steps.py` — File download endpoints (already hardened in Phase 12 with `_resolve_safe_path`)
- `web_interface/backend/app/api/runs.py` — Run management endpoints with HTTPException usage
- `web_interface/backend/app/api/admin_routes.py` — Admin endpoints with HTTPException usage
- `web_interface/backend/app/api/consent_routes.py` — Consent endpoints
- `web_interface/backend/app/api/feedback_routes.py` — Feedback endpoints
- `web_interface/backend/app/auth.py` — Auth module (session secret enforcement already done in Phase 12)

### Test infrastructure
- `web_interface/backend/tests/conftest.py` — Pytest fixtures, test DB, FastAPI client
- `web_interface/backend/tests/test_security.py` — Phase 12 security regression tests (follow pattern)

### Requirements
- `.planning/REQUIREMENTS.md` — SEC-04, SEC-05, SEC-06, SEC-07 definitions

### Phase 12 context (prior decisions)
- `.planning/phases/12-critical-security-fixes/12-CONTEXT.md` — Error response philosophy, `[SECURITY]` logging pattern

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `_resolve_safe_path()` in `steps.py`: Phase 12 path validation utility — pattern for security-check-then-proceed
- `CSRFMiddleware` in `main.py`: Existing custom middleware using `BaseHTTPMiddleware` — pattern for adding security headers middleware
- `_allowed_origins` in `main.py`: Env-configurable origin list — already properly scoped, just needs method/header tightening
- `tests/test_security.py`: Phase 12 security regression tests — extend with Phase 13 tests

### Established Patterns
- Security logging: `logger.warning("[SECURITY] ...")` at WARNING level (Phase 12)
- Error responses: generic client message, detailed server log (Phase 12 philosophy)
- Middleware ordering: CORS first, then CSRF — security headers middleware fits in this chain

### Integration Points
- `main.py` middleware stack: add security headers middleware after CORS, update CORS config
- `upload.py` upload handler: add magic bytes check before saving, change filename to randomized
- Global exception handler registered on the FastAPI `app` object in `main.py`

</code_context>

<specifics>
## Specific Ideas

No specific requirements — these are well-defined security hardening controls with clear success criteria from the roadmap and requirements.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 13-security-hardening*
*Context gathered: 2026-03-26*
