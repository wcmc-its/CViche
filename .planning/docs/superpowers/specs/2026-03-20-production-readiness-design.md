# CViche Production Readiness — Design Spec

## Overview

Transform CViche from a single-user local dev tool into a multi-user production system with authentication, rate limiting, usage tracking, consent management, feedback collection, and an admin dashboard.

**Current state:** No auth, no users, SQLite DB, all endpoints open, single-tenant.
**Target state:** Multi-user with identity, access control, usage limits, admin oversight, and research data collection.

## Database

**Phase 1:** MariaDB. The system has write-heavy paths (per-request `last_active_at` updates, structured logging, run/step tracking) that benefit from proper concurrent write support. MariaDB is already used in the WCM ecosystem (ReCiterDB) and aligns with institutional infrastructure. SQLAlchemy + Alembic work identically with MariaDB — the migration from the current SQLite is a connection string change plus an Alembic migration.

**Connection:** Read from environment variable `CVICHE_DATABASE_URL`. Default for dev: `mysql+pymysql://root@localhost:3306/cviche`. Production: managed MariaDB instance.

## Phased Deployment

- **Phase 1 — Internal Pilot (immediate):** Internal team of admins processes CVs on behalf of faculty. Simple email-based auth. Users defined in `auth_config.yaml`. Deployed on WCM internal network only (not public internet).
- **Phase 2 — SAML Self-Service (future):** Faculty self-service via SAML SSO through WCM Enterprise Directory. Auth backend swapped; rest of app unchanged.

## Qualtrics Migration

Both Qualtrics forms are being replaced by native in-app equivalents:

- **SV_0kZg7KkZYZlBy4e** (pre-processing consent) → replaced by in-app consent page (section 3). Consent text derived from this form.
- **SV_9KO4A1TkMXGHdqK** (post-processing feedback) → replaced by native feedback form (section 5). Questions derived from this form, enhanced with run-aware context. Feedback data stored in the Feedback table, joined directly to run data for research export.

---

## 1. Authentication & Identity

### Phase 1 — Simple Email Auth

- User visits CViche → if no session, shown a login page asking for name and email.
- Backend checks email against `auth_config.yaml` allowed list. If not allowed, show "Access denied — contact administrator."
- On success, create/update User record in DB. Set httpOnly session cookie.
- Returning users (cookie valid, user still active) skip login, go straight to consent check → upload page.
- User can log out explicitly (clears cookie).

**Threat model:** Phase 1 is deployed on the WCM internal network only. Email-based login without a password is acceptable because:
- The application is not accessible from the public internet.
- The allowed user list is small and managed by the admin.
- The risk of impersonation is low within a trusted internal network.

If the application is ever exposed to the public internet before SAML is ready, email verification (send a code) must be added.

### Phase 2 — SAML SSO (future)

- Auth module is swappable. Same session cookie mechanism, different identity source.
- SAML assertion provides email, display name, group memberships.
- Group membership from WCM Enterprise Directory determines allowed users and admin status.
- User record created/updated on first SAML login.
- No changes needed to the rest of the app — User table is the single source of identity.

### Session Management

- **Library:** `itsdangerous` (already available via Starlette) for signed cookies. No server-side session store needed.
- **Cookie settings:** `httpOnly=True`, `SameSite=Lax`, cookie name: `cviche_session`. The `Secure` flag is controlled by environment variable `CVICHE_SECURE_COOKIES` (default: `true`). **Set to `false` for Phase 1 if the internal deployment does not have TLS** (no reverse proxy terminating HTTPS), otherwise the browser will silently refuse to set the cookie and login will fail. When a TLS-terminating proxy is in place, set to `true`.
- **Session TTL:** 7 days. After expiry, user must re-authenticate.
- **Session payload:** `{ user_id, email, role, issued_at }` signed with a secret key. The payload is signed but not encrypted — role is visible but tamper-evident. This is acceptable since the role is not secret information.
- **Secret key:** Read from environment variable `CVICHE_SESSION_SECRET`. If not set, generate a random key on startup and log a warning (acceptable for dev, not for production). **Note:** In dev without a stable secret, every server restart invalidates all sessions — users will need to re-login after each restart. This is expected behavior, not a bug.

### Session Revocation / Active Status Check

The signed cookie has no server-side revocation mechanism. To handle admin-disabled users and role changes:

- **On every authenticated request**, the auth middleware performs a lightweight DB check: query `User.status` and `User.role` for the `user_id` in the cookie.
- If `status != 'active'`, reject the request with 401 and clear the cookie.
- If `role` in the cookie doesn't match `role` in the DB (e.g., admin promoted/demoted a user), update the cookie with the current role.
- This DB check is trivially cheap for the small Phase 1 user pool (single indexed lookup). For Phase 2 at scale, this can be cached with a short TTL (e.g., 60 seconds) if needed.

This means disabling a user in the admin dashboard takes effect immediately on their next request, not after cookie expiry.

### CSRF Protection

- `SameSite=Lax` on the session cookie prevents CSRF from cross-origin POST requests in modern browsers.
- All state-changing API endpoints (`POST`, `PUT`, `DELETE`) also check the `Origin` or `Referer` header against the allowed origins list. Requests with mismatched origins are rejected with 403.
- This is sufficient for Phase 1 (internal network). Phase 2 (SAML) inherits the same protections.

### Login Rate Limiting

Login endpoint is rate-limited to **10 attempts per IP per minute**. This is the canonical definition — all other references to login rate limiting defer to this section.

### Access Control Config (`auth_config.yaml`)

Auth config lives in a dedicated file: `web_interface/backend/auth_config.yaml` (separate from the existing pipeline `config.yaml`). This file is **read-only bootstrap config** — it is read on startup and on each request, but is never written to by the application.

```yaml
auth:
  mode: simple  # "simple" or "saml"

allowed_users:
  - paa2013@med.cornell.edu
  - jsmith@med.cornell.edu

admin_users:
  - paa2013@med.cornell.edu

rate_limits:
  daily: 10
  monthly: 50

consent:
  version: "1.0"
```

When `mode: saml`, `allowed_users` and `admin_users` can reference Enterprise Directory group names instead of individual emails.

**Admin-editable settings** (rate limits, allowed users, consent version) are stored in the **database**, not in this YAML file. The YAML provides bootstrap defaults that seed the database on first startup. After that, the DB is authoritative and the admin dashboard edits the DB directly. See section 7 (Config tab) for details.

---

## 2. Data Model Changes

### New: User table

| Column | Type | Description |
|--------|------|-------------|
| id | Integer PK | Auto-increment |
| email | String, unique, indexed | Primary identifier |
| display_name | String | Full name |
| role | String, CHECK (user, admin) | System role |
| status | String, CHECK (active, disabled) | Account status |
| daily_limit | Integer, nullable | Per-user override (null = use system default) |
| monthly_limit | Integer, nullable | Per-user override (null = use system default) |
| default_submission_type | String, nullable | `own_cv` or `authorized_admin` (set during consent, overridable per run) |
| consent_version | String, nullable | Version of consent text they agreed to |
| consent_date | DateTime, nullable | When they last consented |
| created_at | DateTime | First login |
| last_active_at | DateTime | Last activity |

### New: Consent table

| Column | Type | Description |
|--------|------|-------------|
| id | Integer PK | Auto-increment |
| user_id | FK → users, indexed | Who consented |
| consent_version | String | Version of consent text |
| consent_text_hash | String | SHA-256 of the consent text shown |
| ip_address | String | For audit trail |
| user_agent | String | For audit trail |
| timestamp | DateTime | When they consented |

### New: SystemConfig table

| Column | Type | Description |
|--------|------|-------------|
| key | String PK | Config key (e.g., `rate_limit_daily`, `consent_version`) |
| value | Text | JSON-encoded value |
| updated_at | DateTime | Last modified |
| updated_by | FK → users, nullable | Who changed it |

Admin-editable settings live here, not in YAML. Seeded from `auth_config.yaml` on first startup. On subsequent startups, any keys present in YAML but absent from the DB are inserted (forward-compatible bootstrap for new config keys); existing keys are left untouched (preserves admin changes).

### Existing tables (already implemented, documented for completeness)

The following tables already exist and track cost, tokens, and step-level timing:

**Run table** (existing columns):
`id`, `filename`, `file_type`, `status`, `started_at`, `completed_at`, `total_cost`, `total_tokens`, `input_tokens`, `output_tokens`, `error_message`, `created_at`

**Step table** (existing columns):
`id`, `run_id`, `step_number`, `stage_id`, `step_name`, `status`, `started_at`, `completed_at`, `duration_seconds`, `cost`, `input_file`, `output_files`, `error_message`

**LLMUsage table** (existing columns — per-API-call granularity):
`id`, `run_id`, `step_number`, `model`, `prompt_tokens`, `completion_tokens`, `total_tokens`, `cost`, `latency_ms`, `timestamp`

**Log table** (existing):
`id`, `run_id`, `step_number`, `timestamp`, `level`, `message`

### Modified: Run table

Add these columns to the existing Run table:

| Column | Type | Description |
|--------|------|-------------|
| user_id | FK → users, nullable, indexed | Who initiated this run |
| submission_type | String, nullable | `own_cv` or `authorized_admin` |
| show_track_changes | Boolean, default true | Include track changes in output document |
| show_pipeline_comments | Boolean, default false | Include pipeline comments (classification details, confidence scores) in output document |

**Index:** Add index on `(user_id, started_at)` for the paginated user-scoped query.

Existing runs (pre-migration) get `user_id = NULL`, `submission_type = NULL`, `show_track_changes = true`, `show_pipeline_comments = false`.

### New: RunMetrics table — Input Characterization

Computed once during pipeline execution (after stage 1 parsing). One row per run. Essential for the research paper — correlates input complexity with output quality and cost.

| Column | Type | Description |
|--------|------|-------------|
| id | Integer PK | Auto-increment |
| run_id | FK → runs, unique, indexed | Which run |
| word_count | Integer, nullable | Total words in the input document |
| char_count | Integer, nullable | Total characters |
| publication_count | Integer, nullable | Number of publications detected (after stage 2) |
| sections_populated | Integer, nullable | Number of WCM sections that received content (after stage 6) |
| sections_total | Integer | Total WCM sections (71) |
| language | String, nullable | Detected language if non-English |
| computed_at | DateTime | When metrics were computed |

### Modified: Step table — Error Taxonomy

Add these columns to the existing Step table:

| Column | Type | Description |
|--------|------|-------------|
| error_type | String, nullable | Structured error category: `llm_timeout`, `token_limit`, `parse_error`, `invalid_response`, `api_error`, `file_error`, `unknown` |

The existing `error_message` column stores the human-readable detail. The new `error_type` enables aggregated reporting: "12% of failures were due to token limit truncation in stage 3."

### Modified: LLMUsage table — Extended Metadata

Add these columns to the existing LLMUsage table:

| Column | Type | Description |
|--------|------|-------------|
| model_version | String, nullable | Full model version (e.g., `gpt-4o-2024-08-06`, not just `gpt-4o`) |
| temperature | Float, nullable | Temperature setting used |
| finish_reason | String, nullable | `stop`, `length`, `content_filter`, etc. `length` = output was truncated — quality-relevant signal |
| retry_count | Integer, default 0 | Number of retries before this successful call |
| prompt_version | String, nullable | Hash or identifier of the prompt template used. Essential for longitudinal analysis when iterating on prompts during the pilot |

### New: Feedback table

Multiple feedback records per run are allowed — the CV subject and the admin who ran it may both provide feedback (inter-rater reliability data). Unique constraint on `(run_id, user_id)` — each user can give feedback once per run.

| Column | Type | Description |
|--------|------|-------------|
| id | Integer PK | Auto-increment |
| run_id | FK → runs, indexed | Which run this feedback is for |
| user_id | FK → users, indexed | Who submitted the feedback |
| reviewer_role | String | `cv_subject`, `departmental_staff`, `faculty_affairs`, or free-text other |
| overall_accuracy | Integer, nullable | 1–10 scale, or null if N/A |
| overall_completeness | Integer, nullable | 1–10 scale, or null if N/A |
| overall_usefulness | Integer | 1–5 scale |
| manual_conversion_effort | String | Time estimate (e.g., `< 5 minutes`, `1-2 hours`, `8+ hours`, `not_sure`) |
| correction_effort | String | Time estimate (e.g., `0 minutes`, `5-15 minutes`, `8+ hours`, `not_sure`) |
| enrichment_quality | Integer, nullable | 1–5 scale, or null if N/A (shown only if stage 5 ran) |
| summary_generated | Boolean, nullable | Auto-detected from run data (did stage 4.5 complete?), shown for confirmation |
| summary_quality | Integer, nullable | 1–5 scale, or null if N/A (shown only if summary was generated) |
| issue_missing_content | String, nullable | Severity: `not_noticed`, `minor`, `moderate`, `major` |
| issue_split_merged | String, nullable | Severity level |
| issue_wrong_section | String, nullable | Severity level |
| issue_inaccurate | String, nullable | Severity level |
| issue_ai_enrichment | String, nullable | Severity level |
| issue_formatting | String, nullable | Severity level |
| issue_locations | Text, nullable | JSON array of section names where issues were noticed |
| biggest_issue | Text, nullable | Free-text: the one issue most affecting confidence |
| likelihood_to_recommend | Integer | 1–5 scale |
| submitted_at | DateTime | When feedback was submitted |

**Unique constraint:** `(run_id, user_id)` — each user gives feedback once per run. Multiple users can give feedback on the same run.

### Feedback Design Notes

- **Run-aware context:** The feedback form auto-detects which pipeline stages ran for this specific run. If stage 5 (PubMed enrichment) didn't run, the enrichment quality question is hidden. If stage 4.5 (research summary) didn't run, the summary quality question is hidden. The issue location checkboxes are pre-populated with only the WCM sections that were actually populated in this run's output.
- **No run code question:** Eliminated — the form knows the `run_id`.
- **No email question:** Eliminated — the form knows the `user_id`.
- **Inter-rater reliability:** If both the admin and the faculty CV subject give feedback on the same run, this provides inter-rater data for the paper.
- **Research dataset:** Feedback is joined directly to run data (cost, duration, steps, errors, input metrics) in a single export.

### Automated Quality Signals

These metrics are computed automatically on every completed run and stored in RunMetrics (or derived from existing Step/LLMUsage data). They provide a richer dataset alongside human feedback ratings:

- **Publication match rate:** publications found in PubMed / total publications detected (from stage 5 data)
- **Section completeness:** WCM sections populated / total WCM sections (from stage 6 output)
- **Truncation rate:** LLM calls with `finish_reason = 'length'` / total LLM calls (from LLMUsage)
- **Retry rate:** LLM calls with `retry_count > 0` / total LLM calls (from LLMUsage)
- **Cost per section:** total cost / sections populated (derived)

These don't need their own table — they're computed at query time from existing data for the admin dashboard and CSV export.

### Cost Tracking Design

Cost is tracked at three levels of granularity (all already implemented in the existing schema):

1. **Per-LLM-call:** `LLMUsage` table stores `prompt_tokens`, `completion_tokens`, `model`, and computed `cost` per API call. Token counts + model identifier is future-proof — if pricing changes, costs can be recalculated historically.
2. **Per-step:** `Step.cost` is the sum of LLMUsage costs for that step.
3. **Per-run:** `Run.total_cost` is the sum of all step costs. `Run.input_tokens` and `Run.output_tokens` are the totals.

The admin dashboard's "Total Cost" stat card, per-user costs, and per-run costs all derive from `Run.total_cost`. The Feedback Insights tab can break down cost by pipeline stage using `Step.cost`.

### Database Migrations

Use Alembic (already in requirements.txt). Initialize with `alembic init` and create migration scripts for the new tables and columns.

### Required Indexes Summary

- `users.email` (unique index)
- `consent.user_id`
- `runs.user_id`
- `runs.(user_id, started_at)` (composite, for paginated queries)
- `run_metrics.run_id` (unique index)
- `feedback.(run_id, user_id)` (unique composite index)
- `feedback.run_id` (for joins)
- `llm_usage.run_id` (for aggregation queries)

---

## 3. Consent Flow

### Consent Text Storage

Consent text is stored as a markdown file: `web_interface/backend/consent_text.md`. The file contains the full consent text shown to users.

**Version integrity check:** On startup, the application computes the SHA-256 hash of `consent_text.md` and compares it to the last recorded hash for the current consent version (stored in the Consent table). If the hash has changed but the version string hasn't been bumped, the application logs a **warning**: "Consent text has changed but version is still X. Bump the version in config to require re-consent." This prevents silent consent text changes that bypass the re-consent mechanism.

### API

- `GET /api/consent` — returns current consent text (rendered from markdown), version, and whether the current user has consented to this version.
- `POST /api/consent` — submit consent. Request body: `{ "default_submission_type": "own_cv" | "authorized_admin" }`. Records consent event in Consent table, updates user's `consent_version` and `consent_date`, and stores the default submission type on the User record. The submission type can be overridden per-run on the upload page.

### Consent Text Content

Derived from the existing Qualtrics consent form (SV_0kZg7KkZYZlBy4e):

- **About this project:** The Samuel J. Wood Library is conducting a pilot of CViche, a prototype tool that uses AI to convert CVs into WCM format.
- **How your CV will be used:** Processed using AI, reviewed by project staff, feedback analyzed to improve the system.
- **Sensitive information notice:** Users may omit personal contact details before submission.
- **Fields collected:** Name, email, authorization role (own CV vs. authorized admin).
- **Consent checkbox:** "I have read the information above and consent to participate."

### Flow

1. **First visit (no consent on file):** Full-page consent form. Cannot proceed to upload without completing it.
2. **Returning visit (consent current):** Straight to upload page.
3. **Consent text updated (version bump):** User sees consent page again on next visit with updated text. Must re-consent.
4. **Authorization role:** Captured on the consent page as default. Can be changed per submission on the upload page (toggle between "own CV" and "authorized admin").

### Upload Page Options

The upload page shows three toggles below the file input (persisted per-run on the Run table):

- **Submission type:** "Submitting my own CV" / "Authorized on behalf of faculty" (default from consent page)
- **Show track changes:** On by default. Includes track changes in the output Word document showing what the pipeline modified from the original text.
- **Show pipeline comments:** Off by default. Includes classification details, confidence scores, and processing notes as comments in the output document. Useful for debugging pipeline behavior.

### Consent Timing

- Consent is checked at **upload time** (run creation), not just on page visit.
- If the consent version is bumped while a user has a run in-flight, the **in-flight run is not retroactively invalidated**. The run was authorized under the previous consent version, which is recorded in the Consent table.
- The user will be prompted to re-consent before their next upload.

### Storage

Each consent event logged to the Consent table with version, text hash, IP, user agent, and timestamp. Exportable for research paper.

---

## 4. Rate Limiting

### Defaults

- Regular users: **10 runs/day, 50 runs/month**
- Admin users: **Unlimited**

### What Counts as a Run

A run counts against the rate limit **only if it progresses past file upload** (i.e., a Run record is created with status `created` or `running`). Specifically:

- A run that **fails immediately** (bad file format rejected at upload validation, before a Run record is created) does **not** count.
- A run that is created but **fails during pipeline execution** (step 1 parsing error, API failure, etc.) **does** count. The user consumed LLM resources.
- A **cancelled** run **does** count (the user explicitly started it).
- A **restarted** run counts as a new run.

This is the pragmatic choice: once LLM API calls are made, the cost is real regardless of outcome.

### Per-user Overrides

Admins can adjust limits for individual users via the admin dashboard. Stored in the User table (`daily_limit`, `monthly_limit`). `NULL` means use system default from the SystemConfig table.

### Enforcement

- Backend middleware checks run count before accepting upload.
- If limit exceeded, return HTTP 429 with clear message: "Daily limit reached (10/10). Resets at midnight ET." or "Monthly limit reached (50/50). Resets on [date]."
- Frontend shows remaining quota on the upload page (e.g., "7 of 10 runs remaining today").
- When limit hit, upload button disabled with explanation text.

### Reset Schedule

- Daily limit resets at **midnight Eastern Time** (`America/New_York`). The server compares against this timezone explicitly, regardless of the server's own timezone (which may be UTC on AWS). Be aware of DST transitions causing a 23- or 25-hour "day" — this is acceptable and matches user expectations.
- Monthly limit resets at **midnight ET on the 1st of each month**.

---

## 5. Feedback Collection

### Goal

Collect structured feedback on every completed run through a native in-app form. Aggressively nudge users to complete feedback without blocking them.

### Native Feedback Form

Feedback is collected entirely within CViche — no external Qualtrics dependency. The form is derived from the existing Qualtrics feedback survey (SV_9KO4A1TkMXGHdqK) with run-aware enhancements.

**Questions** (see Feedback table in section 2 for field definitions):

1. **Reviewer role** — "What best describes your role?" (CV subject / departmental staff / faculty affairs / other)
2. **Overall accuracy** — 1–10 slider + N/A (null)
3. **Overall completeness** — 1–10 slider + N/A (null)
4. **Overall usefulness** — 1–5 scale ("How usable is this as a starting point?")
5. **Manual conversion effort** — "How long would manual reformatting take?" (time ranges)
6. **Correction effort** — "How long to correct the CViche output?" (time ranges)
7. **Publication enrichment quality** — 1–5 + N/A *(shown only if stage 5 ran for this run)*
8. **Research summary quality** — 1–5 + N/A *(shown only if stage 4.5 ran for this run; auto-detected)*
9. **Issues noticed** — matrix of 6 issue types × 4 severity levels (not noticed / minor / moderate / major)
10. **Issue locations** — checkboxes *(pre-populated with only the WCM sections that were actually populated in this run)*
11. **Biggest confidence issue** — free text
12. **Likelihood to recommend** — 1–5 scale

**Estimated completion time: ~3 minutes** (shown to user).

### API

- `GET /api/run/{run_id}/feedback` — get feedback for a run (returns existing feedback or null)
- `POST /api/run/{run_id}/feedback` — submit feedback. Returns 409 if this user has already submitted feedback for this run (unique on `run_id + user_id`). Multiple users can give feedback on the same run (inter-rater). Request body matches Feedback table fields with the following required/optional split:

**Required fields** (submission rejected without these — core metrics for the research paper):
- `reviewer_role`, `overall_usefulness`, `manual_conversion_effort`, `correction_effort`, `likelihood_to_recommend`

**Optional fields** (nullable — contextual or detail questions):
- `overall_accuracy`, `overall_completeness` (have N/A option)
- `enrichment_quality`, `summary_quality` (conditionally shown)
- All `issue_*` fields, `issue_locations`, `biggest_issue`

- `GET /api/admin/feedback?offset=0&limit=20` — all feedback across users (admin only, for export/analysis)

### Mechanism 1: Completion Interstitial

When a pipeline run completes (status → `complete`):

- Show a full-screen overlay with:
  - Success message: "Pipeline Complete! Your WCM-formatted CV is ready."
  - **Download** button (primary blue, prominent)
  - **Give Feedback** button (amber, equally prominent) — opens the feedback form inline (expands the interstitial or navigates to `/run/:runId/feedback`)
  - "I'll do this later" link (dismisses overlay, small gray text)
- Overlay shown once per run completion. Does not reappear if user navigates away and back.

### Mechanism 2: Run History Badges

On the upload page's run history section:

- **Persistent amber banner** at top: "You have N runs awaiting feedback — [Give Feedback]"
  - Banner cannot be permanently dismissed. Goes away only when all of the current user's completed runs have a Feedback record from that user.
  - "Give Feedback" opens the feedback form for the oldest un-reviewed run.
- **Per-run badge** next to each completed run:
  - Amber "Needs feedback" badge if no Feedback record from the current user exists for this run
  - Green "Feedback given" badge if the current user has submitted feedback for this run
- Clicking a run with no feedback shows a prompt to give feedback before viewing results (not blocking, just prominent).

### Feedback Form UX

- The form is a dedicated page/panel at `/run/:runId/feedback`, accessible from:
  - The completion interstitial
  - The "Needs feedback" badge in run history
  - A "Give Feedback" tab in the pipeline viewer (alongside Logs and Prompt Logs)
- The form shows the run context at the top: filename, run date, duration, cost, which stages completed.
- Questions that depend on pipeline stages are conditionally shown based on actual run data.
- Issue location checkboxes are populated with the WCM sections that were filled in this specific run (queried from run output data).
- Form submits to `POST /api/run/{run_id}/feedback`. On success, badge turns green, banner count decrements.

### Admin Dashboard Metrics

- **Feedback completion rate:** percentage of completed runs with a Feedback record (single, clean metric — no click-through/confirmed split needed)
- **Average scores:** overall accuracy, completeness, usefulness, likelihood to recommend — aggregated across all feedback
- **Time savings:** average manual conversion effort vs. correction effort — key metric for the research paper
- **Common issues:** aggregated issue severity matrix across all runs
- **Export:** all feedback joined to run data (cost, duration, steps, errors, user) as a single CSV

---

## 6. Run History & Pagination

### Pagination

- Show **20 runs** initially on the upload page.
- "Show more" button at bottom loads the next 20 (API supports `?offset=N&limit=20`).
- Runs ordered by `started_at` descending (most recent first).

**Breaking change:** The existing `GET /api/runs` endpoint (currently returns all runs, unpaginated, no user filtering) changes to user-scoped and paginated. The frontend `RunHistory.tsx` must be updated simultaneously to pass `offset` and `limit` parameters.

### User Isolation

- Regular users see **only their own runs**.
- Admin users see only their own runs on the upload page (admin dashboard shows all).
- All data endpoints (`/api/run/{run_id}/status`, `/api/run/{run_id}/data/*`, `/api/run/{run_id}/step/*`, `/api/run/{run_id}/prompt-logs`) check that the requesting user owns the run or is an admin. Returns 403 otherwise.

### WebSocket Auth

The WebSocket endpoint `/ws/run/{run_id}/stream` validates the session cookie on the upgrade request and checks run ownership before accepting the connection.

**Dev environment note:** In development, the frontend (port 3000) and backend (port 8000) are on different ports. Cookies are not sent on cross-origin WebSocket upgrades by default. The current code connects directly to `ws://localhost:8000` bypassing the Vite proxy. For auth to work, either:
- Route WebSocket through the Vite proxy (preferred, already partially configured), or
- Set `withCredentials` on the WebSocket connection and ensure CORS allows credentials.

### Incomplete Run Handling

When a user clicks a run with status `failed`, `cancelled`, or `created`:

- **Read-only pipeline viewer:** Shows whatever logs and output were produced before the run stopped. Status clearly displayed.
- **"Restart with this file" button:** Creates a brand-new run using the same uploaded file. The new run inherits the original `submission_type`, but the user can change it on the upload page before starting. If the original file has been deleted, shows "Original file no longer available — please upload again."
- Runs with status `running` show the live pipeline viewer as today.

### File Retention

- Uploaded CVs are retained **indefinitely**. CVs contain research data relevant to the academic paper and should not be auto-deleted.
- Admin dashboard Config tab shows total storage used for awareness.
- If file retention policy changes in the future, add a `file_deleted_at` timestamp on the Run record rather than leaving dangling file references.

---

## 7. Admin Dashboard

### Route

`/admin` — accessible only to users with `role = admin`. Regular users who navigate to `/admin` get redirected to `/`.

### Overview Panel (top of page)

Four stat cards:

- **Total Runs** — all-time count
- **Active Users** — users with at least one run in the last 30 days
- **Total Cost** — sum of all run costs
- **Feedback Rate** — percentage of completed runs with a Feedback record

### Tab 1: Users

Table columns: User name, Role (admin/user badge), Runs today (red if at limit), Total cost, Feedback rate (confirmed feedback / completed runs, red if low), Last active date, Status (active/disabled).

Actions per user:
- Enable/disable user (takes effect immediately via per-request status check)
- Adjust daily/monthly rate limits
- View user's run history

### Tab 2: All Submissions

Table of every run across all users. Columns: Run ID, User, Filename, Status, Duration, Cost, Date, Feedback (given / pending).

- Sortable by any column.
- Filterable by: user, status, date range.
- Click a run to view its pipeline results.
- Paginated (20 per page with Show more).

### Tab 3: Feedback Insights

Aggregated feedback analysis — the primary research paper data view.

- **Average scores:** Overall accuracy, completeness, usefulness, likelihood to recommend — with trend over time.
- **Time savings analysis:** Manual conversion effort vs. correction effort distributions. Key metric: median time saved per CV.
- **Issue heatmap:** Aggregated issue severity matrix (6 issue types × 4 severity levels) across all runs. Shows which pipeline areas need improvement.
- **Issue locations:** Which WCM sections most frequently have issues.
- **Per-question breakdown:** Distribution charts for each feedback question.
- **Individual feedback table:** All feedback responses with run data joined, sortable and filterable. Click to view the full feedback + run details.
- **Export:** CSV of all feedback joined to run data — the primary research dataset.

### Tab 4: Config

Admin-editable settings are stored in the **SystemConfig database table**, not in `auth_config.yaml`. The YAML file provides bootstrap defaults on first startup only.

Editable settings:
- **Allowed users:** View and edit the allow list. Add/remove emails. In SAML mode, manage Enterprise Directory group names.
- **Admin users:** Promote/demote users.
- **Default rate limits:** Edit daily and monthly defaults.
- **Consent version:** View current version, bump version (triggers re-consent for all users on next visit).
- **Export data:** Download CSV of all runs, all users, all consent records. Exports are logged with admin identity and timestamp.

**Config editing safety:**
- All changes are written to the SystemConfig DB table (not YAML), so standard DB transaction safety applies.
- At least one admin must remain in the admin list (cannot remove yourself if you're the last admin).
- All config changes are logged to structured application logs with admin identity.
- No YAML serialization from user input — eliminates injection risk.

---

## 8. Observability & Logging

### Structured Logging

All application logs use **JSON lines format** with consistent fields:

```json
{
  "timestamp": "2026-03-20T14:30:00Z",
  "level": "INFO",
  "event": "run_started",
  "user_id": 1,
  "user_email": "paa2013@med.cornell.edu",
  "run_id": "FIHL8A",
  "details": {}
}
```

### Logged Events

| Event | When | Extra Fields |
|-------|------|-------------|
| `user_login` | Successful login | `email`, `ip_address` |
| `user_login_denied` | Login denied (not on allow list) | `email`, `ip_address` |
| `user_login_rate_limited` | Login rate limit hit | `ip_address` |
| `consent_given` | User submits consent | `consent_version` |
| `run_started` | Pipeline run begins | `run_id`, `filename`, `submission_type` |
| `run_completed` | Pipeline run finishes | `run_id`, `status`, `duration`, `cost` |
| `run_cancelled` | User cancels a run | `run_id` |
| `rate_limit_hit` | Upload rejected due to rate limit | `limit_type` (daily/monthly), `count` |
| `feedback_submitted` | User submits native feedback form | `run_id`, `fields_completed` (count of non-null fields) |
| `admin_config_changed` | Admin changes a config setting | `key`, `old_value`, `new_value` |
| `admin_user_updated` | Admin changes user status/role/limits | `target_user_id`, `changes` |
| `admin_export` | Admin exports data | `export_type` |

### Log Output

Logs written to stdout (standard for containerized deployments). In development, also human-readable via a formatter flag.

---

## 9. Storage & EKS Deployment

### Storage Abstraction Layer

All file I/O (uploads, pipeline outputs, prompt logs) goes through a `RunStorage` interface. This abstracts the difference between local filesystem (dev) and S3 (production/EKS).

```python
class RunStorage:
    def put_file(self, run_id: str, key: str, data: bytes) -> None: ...
    def get_file(self, run_id: str, key: str) -> bytes: ...
    def list_files(self, run_id: str, prefix: str = "") -> list[str]: ...
    def get_download_url(self, run_id: str, key: str, expires_in: int = 300) -> str: ...
    def exists(self, run_id: str, key: str) -> bool: ...
```

Two implementations:
- `LocalRunStorage` — reads/writes to `web_interface/uploads/` and `web_interface/outputs/`. Used in dev.
- `S3RunStorage` — reads/writes to S3. Used in production (EKS).

Selected by environment variable `CVICHE_STORAGE_BACKEND` (`local` or `s3`).

### S3 Key Structure

```
{prefix}/runs/{run_id}/input/{original_filename}
{prefix}/runs/{run_id}/steps/{stage_id}/{output_filename}
{prefix}/runs/{run_id}/steps/{stage_id}/prompt_logs/{log_filename}
{prefix}/runs/{run_id}/output/{final_document}
```

The `run_id` as top-level prefix makes lifecycle management easy — deleting a run's artifacts is a single prefix delete.

### Pipeline Execution: Local-During-Execution, S3-at-Boundaries

The pipeline reads and writes to **local ephemeral storage** inside the pod while a run is active. No need to refactor every step to speak S3 natively. At the boundaries:

1. **Upload:** File lands in S3 first via the upload endpoint. At run start, pulled to local ephemeral storage.
2. **Per-step upload:** When each step completes, its outputs are pushed to S3 immediately. This gives:
   - **Crash resilience:** If the pod dies after step 3, steps 1–3 outputs are safe in S3.
   - **Live viewer:** The frontend can serve intermediate step outputs during execution (brief delay for S3 PUT, but S3 is strong read-after-write so it's effectively immediate).
3. **Final output:** Already in S3 from the step 6 upload. No separate finalization needed.

### Downloads: Presigned URLs

For file downloads (final CV, JSON outputs, prompt logs), generate **short-lived presigned S3 URLs** (5-minute expiry). The backend generates the URL, the frontend redirects to it.

- Presigned URLs bypass auth middleware — anyone with the URL can download until it expires.
- 5-minute expiry is acceptable for an internal tool. The URL is generated only after auth + ownership checks pass.
- This offloads download bandwidth from the backend pods.
- In dev (local storage), the backend proxies the file directly instead.

### Config Files on EKS

`consent_text.md` and `auth_config.yaml` are **config, not run data** — they can't live on ephemeral pod storage.

- **`auth_config.yaml`:** Mounted via Kubernetes ConfigMap. Read on startup for bootstrap seeding. Changed rarely.
- **`consent_text.md`:** Mounted via Kubernetes ConfigMap. Read on each consent request. Version integrity check runs on startup.
- **Secrets** (`CVICHE_SESSION_SECRET`, `CVICHE_DATABASE_URL`): Kubernetes Secrets, injected as environment variables.
- **S3 access:** Pod's service account gets an IAM role via IRSA (IAM Roles for Service Accounts). No access keys in config.

### Environment Variables Summary

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `CVICHE_DATABASE_URL` | Yes (prod) | `mysql+pymysql://root@localhost:3306/cviche` | MariaDB connection string |
| `CVICHE_SESSION_SECRET` | Yes (prod) | Random (dev only, logged warning) | Cookie signing key |
| `CVICHE_SECURE_COOKIES` | No | `true` | Set `false` for HTTP-only internal deployments |
| `CVICHE_ALLOWED_ORIGINS` | No | `http://localhost:3000,http://localhost:5173` | CORS allowed origins |
| `CVICHE_STORAGE_BACKEND` | No | `local` | `local` or `s3` |
| `CVICHE_S3_BUCKET` | If s3 | — | S3 bucket name |
| `CVICHE_S3_PREFIX` | No | `cviche` | S3 key prefix (for shared buckets) |
| `OPENAI_API_KEY_WORK` | Yes | — | OpenAI API key for LLM calls |

### `last_active_at` Write Frequency

The per-request `last_active_at` update on the User table is fine for Phase 1 scale with MariaDB. For Phase 2 with multiple pod replicas, debounce to **at most once per minute per user** — update only if the current `last_active_at` is more than 60 seconds old. This reduces write contention without meaningfully affecting the metric's usefulness.

---

## 10. API Error Response Contract

All API error responses use a consistent JSON format:

```json
{
  "error": "rate_limit_exceeded",
  "message": "Daily limit reached (10/10). Resets at midnight ET.",
  "details": {}
}
```

| Field | Type | Description |
|-------|------|-------------|
| `error` | String | Machine-readable error code (e.g., `rate_limit_exceeded`, `auth_required`, `forbidden`, `not_found`, `validation_error`, `server_error`) |
| `message` | String | Human-readable message for display in the UI |
| `details` | Object | Optional structured data (e.g., `{ "limit": 10, "used": 10, "resets_at": "..." }` for rate limits, or `{ "field": "email", "reason": "not on allow list" }` for validation) |

The frontend uses `error` for programmatic handling (e.g., redirect to login on `auth_required`, show banner on `rate_limit_exceeded`) and `message` for user-facing display. This eliminates per-endpoint error handling in the frontend.

### Standard Error Codes

| Code | HTTP Status | When |
|------|-------------|------|
| `auth_required` | 401 | No session cookie or expired session |
| `account_disabled` | 401 | User's status is `disabled` |
| `forbidden` | 403 | User lacks permission (e.g., non-admin accessing `/admin`) |
| `not_found` | 404 | Run, user, or resource not found |
| `validation_error` | 422 | Invalid request body or parameters |
| `rate_limit_exceeded` | 429 | Daily or monthly run limit hit |
| `login_rate_limited` | 429 | Too many login attempts from this IP |
| `consent_required` | 403 | User hasn't consented to current version (frontend redirects to `/consent`) |
| `file_not_found` | 404 | Original file deleted (for restart) |
| `server_error` | 500 | Unexpected server error |

---

## 11. Testing Strategy

Given that this is research-paper-grade software with an audit trail, consent compliance, and rate limiting, the following testing is required before production deployment:

### Integration Tests (automated)

- **Auth middleware:** Verify login creates session, invalid email rejected, disabled user blocked on next request, expired cookie forces re-login, role sync works after admin change.
- **Consent flow:** Verify first-visit redirect to consent, consent submission records audit trail, version bump triggers re-consent, in-flight runs unaffected by version bump, consent text hash integrity check on startup.
- **Rate limiting:** Verify daily/monthly limits enforced, admin unlimited, per-user overrides work, failed-at-validation runs don't count, limit resets at correct time, HTTP 429 returned with correct error format.
- **Run ownership:** Verify users can only see/access their own runs, admin can access all runs, 403 returned for unauthorized access to data/step/prompt-log endpoints.
- **Feedback:** Verify form submission creates Feedback record, 409 on duplicate, conditional questions hidden when stages didn't run, badges update correctly, banner count decrements on submit, admin export joins feedback to run data.

### Manual QA

- **Admin dashboard:** All three tabs render correctly, user enable/disable takes effect, config changes persist, CSV export downloads correctly.
- **Consent page:** Renders consent text, checkbox required, submission type captured.
- **Completion interstitial:** Appears once per run completion, feedback form accessible, dismiss works.
- **Feedback form:** Conditional questions show/hide based on run stages, issue location checkboxes populated from run output, submission works, form not shown for runs with existing feedback.
- **Run history:** Pagination works, feedback badges correct, restart button creates new run, incomplete runs are read-only.

### Smoke Test Checklist (deploy day)

- [ ] Login works (cookie set, redirect to consent or upload)
- [ ] Consent page appears for new users
- [ ] File upload → pipeline runs → completion interstitial shown
- [ ] Run appears in history with feedback badge
- [ ] Admin dashboard loads and shows correct stats
- [ ] Disabling a user blocks them immediately
- [ ] Rate limit message appears at limit

---

## Implementation Notes

### Database Migration

- Use Alembic to manage schema changes.
- Add new tables (User, Consent, SystemConfig) and new columns to Run.
- Seed SystemConfig from `auth_config.yaml` on first migration.
- Existing runs get `user_id = NULL`. They'll appear in the admin "All Submissions" tab but won't be associated with any user.

### Auth Middleware

- FastAPI dependency that extracts user from session cookie using `itsdangerous`.
- **On every request:** validates cookie signature, checks TTL, then queries DB to verify `User.status = 'active'` and sync `User.role`.
- All `/api/` endpoints (except `/api/auth/login`) require valid session.
- All `/ws/` endpoints validate session cookie on upgrade.
- Admin endpoints (`/api/admin/*`) additionally check `role = admin`.
- Auth backend is a Python module with a `get_current_user()` function. Phase 1: checks signed cookie + DB. Phase 2: validates SAML assertion.

### CORS Configuration

- Allowed origins configurable via environment variable `CVICHE_ALLOWED_ORIGINS` (comma-separated).
- Default for dev: `http://localhost:3000,http://localhost:5173`.
- Production: set to the actual deployment URL.

### Frontend Routes

- `/` — Upload page (requires auth + consent)
- `/login` — Login page (Phase 1: email entry; Phase 2: SAML redirect)
- `/consent` — Consent page (shown if user hasn't consented to current version)
- `/run/:runId` — Pipeline viewer (requires auth, user must own the run or be admin)
- `/admin` — Admin dashboard (requires admin role)

### File Structure (new components)

- `LoginPage.tsx` — email-based login form
- `ConsentPage.tsx` — consent form with project description, fields, checkbox
- `AdminDashboard.tsx` — admin overview + tabs
- `AdminUsers.tsx` — users table
- `AdminSubmissions.tsx` — all runs table
- `AdminFeedbackInsights.tsx` — aggregated feedback analysis (research paper data view)
- `AdminConfig.tsx` — config management
- `FeedbackForm.tsx` — native feedback form (run-aware, conditional questions)
- `CompletionInterstitial.tsx` — post-run overlay with download + feedback entry point
- `FeedbackBanner.tsx` — persistent amber banner for run history
- Updates to `RunHistory.tsx` — pagination, feedback badges, restart button
- Updates to `App.tsx` — new routes, auth guards

### API Endpoints (new)

- `POST /api/auth/login` — email-based login (Phase 1), rate-limited per section 1
- `POST /api/auth/logout` — clear session
- `GET /api/auth/me` — current user info
- `GET /api/consent` — get current consent text, version, user's consent status
- `POST /api/consent` — submit consent
- `GET /api/runs?offset=0&limit=20` — paginated runs for current user (breaking change)
- `GET /api/run/{run_id}/feedback` — get feedback for a run (null if none)
- `POST /api/run/{run_id}/feedback` — submit native feedback form (409 if already exists)
- `POST /api/run/{run_id}/restart` — create new run with same file, inherits `submission_type`
- `GET /api/admin/stats` — dashboard overview stats
- `GET /api/admin/users` — all users with stats
- `PUT /api/admin/users/{user_id}` — update user (role, status, limits)
- `GET /api/admin/runs?offset=0&limit=20&user=&status=` — all runs, filterable
- `GET /api/admin/config` — current config from SystemConfig table
- `PUT /api/admin/config` — update config (DB write, validated, logged)
- `GET /api/admin/feedback?offset=0&limit=20` — all feedback across users, with run data joined
- `GET /api/admin/export/{type}` — CSV export (runs, users, consent, feedback) with audit logging. The `feedback` export joins feedback responses to run data (cost, duration, steps, user) in one table.
