# CViche Technical README

## Architecture Overview

CViche is a web application for converting CVs into the WCM (Weill Cornell Medicine) CV format using AI. The system consists of:

```
                                  +------------------+
                                  |    Browser       |
                                  |  (React SPA)     |
                                  +--------+---------+
                                           |
                                  HTTP / WebSocket
                                           |
                        +------------------v------------------+
                        |          nginx (production)         |
                        |    or Vite dev server (dev)         |
                        |  - Serves static React build        |
                        |  - Proxies /api/* and /ws/* to      |
                        |    the backend                      |
                        +------------------+------------------+
                                           |
                              /api/*       |       /ws/*
                                           |
                        +------------------v------------------+
                        |        FastAPI Backend               |
                        |  - Auth middleware (signed cookies)   |
                        |  - Pipeline orchestrator             |
                        |  - REST API + WebSocket streaming    |
                        |  - Alembic DB migrations             |
                        +-----+---------------------+----------+
                              |                     |
                     +--------v--------+   +--------v--------+
                     |    MariaDB      |   |   File Storage   |
                     |  - Users        |   |  - Local (dev)   |
                     |  - Runs/Steps   |   |  - S3 (prod)     |
                     |  - Feedback     |   |                  |
                     |  - Consent      |   |                  |
                     |  - LLM Usage    |   |                  |
                     |  - SystemConfig |   |                  |
                     +-----------------+   +-----------------+
```

**Frontend:** React 18 + TypeScript + Vite + Tailwind CSS. Single-page application with client-side routing.

**Backend:** Python 3.14 + FastAPI + SQLAlchemy + Alembic. Runs the pipeline orchestrator, exposes REST and WebSocket APIs, manages auth and sessions.

**Database:** MariaDB (utf8mb4). Manages users, runs, steps, feedback, consent audit trail, LLM usage tracking, and system configuration. Alembic handles all schema migrations.

**Storage:** Abstracted via a `RunStorage` interface with two backends -- `LocalRunStorage` (filesystem, for development) and `S3RunStorage` (for production/EKS). Uploads, pipeline outputs, and prompt logs are stored through this layer.

**Auth:** Phase 1 uses email-based login with `itsdangerous` signed cookies. Phase 2 will swap in SAML SSO. Session state is in the cookie (signed, not encrypted); a per-request DB check validates user status and role.

---

## Prerequisites

| Component | Version | Notes |
|-----------|---------|-------|
| Python | 3.11+ | Backend runtime |
| Node.js | 18+ | Frontend build toolchain |
| npm | 9+ | Comes with Node.js |
| MariaDB | 11+ | Database (MySQL-compatible) |
| Docker | 24+ | Optional, for containerized deployment |
| docker compose | 2.20+ | Optional, for local stack orchestration |

---

## Quick Start with Docker Compose

The fastest way to run the full stack locally:

```bash
cd web_interface

# Start MariaDB + backend + frontend
docker compose up --build

# Application is available at:
#   Frontend: http://localhost:3000
#   Backend API: http://localhost:8000
#   API Docs: http://localhost:8000/docs
```

This starts three services:

- **db:** MariaDB 11 on port 3306
- **backend:** FastAPI on port 8000 (with hot-reload, runs Alembic migrations on startup)
- **frontend:** Vite dev server on port 3000 (with hot-reload)

The `OPENAI_API_KEY` environment variable must be set in your host shell before running `docker compose up`. All other environment variables have development defaults.

To stop: `docker compose down`

To reset the database: `docker compose down -v` (destroys the MariaDB volume)

---

## Manual Local Development Setup

### 1. Create the MariaDB Database

```bash
mysql -u root -e "CREATE DATABASE IF NOT EXISTS cviche CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
```

### 2. Backend Setup

```bash
cd web_interface/backend

# Install Python dependencies
pip install -r requirements.txt

# Run Alembic migrations
alembic upgrade head

# Start the backend with hot-reload
uvicorn app.main:app --reload --port 8000
```

The backend resolves its connection from four separate settings -- `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER` -- looked up by `get_config` (environment variable first, then the `db:` section of `auth_config.yaml`). There is no `DATABASE_URL` setting; all four are required and `create_cviche_engine` raises at boot if any is empty.

`DB_AUTH_MODE` selects the credential, and defaults to `iam`: an RDS IAM token over TLS, with the server certificate verified against the CA bundle vendored at `web_interface/backend/rds_ca/`. Setting `DB_AUTH_MODE=password` switches to password auth without TLS, which is how the local compose stack reaches its MariaDB container; that mode requires `DB_PASSWORD` and refuses to start without it. Any other value fails at boot. `DB_PASSWORD` on its own no longer changes the mode -- a stray password in a deployed environment must not be able to downgrade IAM + verified TLS to an unauthenticated connection.

### 3. Frontend Setup

```bash
cd web_interface/frontend

# Install dependencies
npm install

# Start the Vite dev server
npm run dev
```

The frontend runs on port 3000 by default and proxies `/api` and `/ws` requests to the backend on port 8000 (configured in `vite.config.ts`).

### 4. Verify

- Frontend: http://localhost:3000
- Backend API: http://localhost:8000
- API docs (Swagger): http://localhost:8000/docs
- Liveness probe: http://localhost:8000/livez
- Readiness probe: http://localhost:8000/readyz (returns 503 if DB/S3 not reachable)

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER` | Yes | none | MariaDB connection parameters. Resolved individually by `get_config` (env, then `auth_config.yaml`'s `db:` section) and passed to `create_cviche_engine`. Boot fails if any is empty. |
| `MIGRATE_USER` | Yes | none | DB user Alembic connects as (`alembic/env.py`). Distinct from `DB_USER`; both must be set. |
| `DB_AUTH_MODE` | No | `iam` | Which database credential the engine uses: `iam` (RDS IAM token over certificate-verified TLS) or `password` (local compose only). Any other value, including an explicitly empty one, raises at boot. |
| `DB_PASSWORD` | If `password` | unset | Password for `DB_AUTH_MODE=password`. Local dev only. Ignored entirely under `iam`, so it cannot silently downgrade a deployed instance; `password` mode with this unset or empty refuses to start. |
| `CVICHE_SESSION_SECRET` | Yes (prod) | Random (dev only) | Secret key for signing session cookies with `itsdangerous`. If not set, a random key is generated on startup and a warning is logged. Sessions will not survive server restarts in dev. |
| `CVICHE_SECURE_COOKIES` | No | `true` | Set to `false` for HTTP-only deployments without TLS. When `true`, the browser requires HTTPS to set the session cookie. If the cookie silently fails to set and login does not work, this is the most likely cause. |
| `CVICHE_ALLOWED_ORIGINS` | No | `http://localhost:3000,http://localhost:5173` | Comma-separated list of allowed CORS origins. Also used for Origin/Referer CSRF checking on state-changing requests. |
| `CVICHE_STORAGE_BACKEND` | No | `local` | `local` for filesystem storage (dev), `s3` for S3 storage (production). |
| `CVICHE_S3_BUCKET` | If `s3` | -- | S3 bucket name. Required when `CVICHE_STORAGE_BACKEND=s3`. |
| `CVICHE_S3_PREFIX` | No | `cviche` | Key prefix within the S3 bucket. Allows sharing a bucket across environments. |
| `OPENAI_API_KEY` | Yes | -- | OpenAI API key for all LLM calls in the pipeline. |
| `CVICHE_LOCAL_STORAGE_DIR` | No | `web_interface/uploads/` | Override the default local storage directory for uploads and outputs. Only applies when `CVICHE_STORAGE_BACKEND=local`. |

### TLS termination in production

The container terminates HTTP, not TLS. It runs behind a TLS-terminating load balancer (ALB+ACM at WCM). uvicorn is invoked with `--proxy-headers --forwarded-allow-ips='*'` (see `web_interface/backend/docker-entrypoint.sh`), so `X-Forwarded-Proto` from the LB is honored and `request.url.scheme` reflects the upstream scheme. This matters for SAML metadata / OIDC redirects (which embed absolute URLs).

The container's network exposure must be restricted to the LB only (security-group rules on EKS) — `--forwarded-allow-ips='*'` trusts every forwarded header it sees, so direct access from outside the cluster would let a client spoof `X-Forwarded-Proto`.

For the full contract the LB must honor and a go-live checklist, see [docs/PRODUCTION_TLS.md](../docs/PRODUCTION_TLS.md).
### Secrets in production

The variables above marked "Yes (prod)" plus `OPENAI_API_KEY` are secrets and must not be committed, baked into the image, or passed on the command line. AWS credentials should come from IRSA, not static `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`.

For the provisioning pattern (External Secrets Operator + AWS Secrets Manager on EKS, `.env` on a VM), the IRSA trust policy and IAM policy templates, the bucket policy, and a verification checklist, see [docs/PRODUCTION_SECRETS.md](../docs/PRODUCTION_SECRETS.md). `auth_config.yaml` provisioning is documented separately in the root [README](../README.md).

---

## Database Setup

### MariaDB

CViche uses MariaDB with the `utf8mb4` character set. Create the database:

```bash
mysql -u root -e "CREATE DATABASE IF NOT EXISTS cviche CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
```

For production, use a managed MariaDB instance and set `CVICHE_DATABASE_URL` accordingly:

```
mysql+pymysql://cviche_user:PASSWORD@mariadb-host:3306/cviche
```

For backup configuration, snapshot retention, point-in-time recovery, and the restore runbook, see [docs/PRODUCTION_BACKUPS.md](../docs/PRODUCTION_BACKUPS.md). That doc also covers S3 versioning and the (currently fragile) prompt-log persistence story.

### Alembic Migrations

All schema changes are managed by Alembic. Migration files live in `web_interface/backend/alembic/versions/`.

```bash
cd web_interface/backend

# Apply all pending migrations
alembic upgrade head

# Check current migration version
alembic current

# Generate a new migration after model changes
alembic revision --autogenerate -m "description of changes"
```

In Docker, migrations run automatically on container startup via `docker-entrypoint.sh`.

Alembic builds its engine in `alembic/env.py` from `DB_HOST`, `DB_PORT`, `DB_NAME` and `MIGRATE_USER` -- note the last one, which is a different key from the app's `DB_USER`.

### Database Tables

| Table | Description |
|-------|-------------|
| `users` | User accounts (email, role, status, rate limits, consent state) |
| `consent` | Consent audit trail (version, text hash, IP, user agent, timestamp) |
| `system_config` | Admin-editable settings (rate limits, allowed users, consent version) |
| `runs` | Pipeline runs (status, cost, tokens, user, submission type, output settings) |
| `steps` | Per-stage execution data (status, duration, cost, error type) |
| `logs` | Execution log entries per step |
| `llm_usage` | Per-API-call LLM metrics (tokens, cost, model version, finish reason) |
| `run_metrics` | Input characterization (word count, publication count, section completeness) |
| `feedback` | User feedback per run (ratings, issue severity, effort estimates) |

---

## Auth Configuration

### auth_config.yaml

The auth configuration file lives at `web_interface/backend/auth_config.yaml`. It is read-only bootstrap config -- the application reads it on startup to seed the `system_config` database table but never writes to it. After initial seeding, the database is authoritative and the admin dashboard edits the database directly.

```yaml
auth:
  mode: simple  # "simple" (email login) or "saml" (future)

allowed_users:
  - paa2013@med.cornell.edu

admin_users:
  - paa2013@med.cornell.edu

rate_limits:
  daily: 10
  monthly: 50

consent:
  version: "1.0"
```

### Adding Users

**Phase 1 (email auth):**

1. Add the user's email to the `allowed_users` list in `auth_config.yaml` (for bootstrap).
2. Or add them via the admin dashboard Config tab (takes effect immediately, stored in DB).

**Phase 2 (SAML):** Users are admitted based on Enterprise Directory group membership. The `allowed_users` field references group names instead of individual emails.

### Promoting Admins

1. Add the user's email to the `admin_users` list in `auth_config.yaml` (for bootstrap).
2. Or promote them via the admin dashboard Users tab (takes effect immediately on their next request).

At least one admin must always exist -- the system prevents removing the last admin.

### Session Details

- **Cookie name:** `cviche_session`
- **Cookie flags:** `httpOnly=True`, `SameSite=Lax`, `Secure` controlled by `CVICHE_SECURE_COOKIES`
- **TTL:** 7 days
- **Payload:** `{ user_id, email, role, issued_at }` -- signed with `itsdangerous`, tamper-evident
- **Per-request DB check:** Every authenticated request verifies user status (active/disabled) and syncs role. Disabling a user in the admin dashboard takes effect immediately.

---

## Consent Text Management

### Editing Consent Text

The consent text shown to users lives in `web_interface/backend/consent_text.md`. To update it:

1. Edit the markdown file with the new consent language.
2. Bump the consent version in the admin dashboard Config tab (or in `auth_config.yaml` for bootstrap).
3. On their next visit, all users will be shown the updated consent page and must re-consent before uploading.

### Version Integrity Check

On startup, the application computes the SHA-256 hash of `consent_text.md` and compares it to the last recorded hash. If the file content has changed but the version string has not been bumped, the application logs a warning:

```
WARNING: Consent text has changed but version is still 1.0. Bump the version to require re-consent.
```

This prevents accidental silent changes to consent text.

### Consent Audit Trail

Every consent event is recorded in the `consent` table with:

- User ID
- Consent version
- SHA-256 hash of the consent text that was displayed
- IP address
- User agent
- Timestamp

This data is exportable from the admin dashboard for compliance and research purposes.

---

## Admin Dashboard

The admin dashboard is available at `/admin` and is restricted to users with the `admin` role. Non-admin users are redirected to the home page.

### Overview Panel

Four stat cards at the top:

- **Total Runs** -- all-time count across all users
- **Active Users** -- users with at least one run in the last 30 days
- **Total Cost** -- sum of all run costs (LLM API charges)
- **Feedback Rate** -- percentage of completed runs with at least one feedback submission

### Users Tab

Manage the user base:

- View all users with their role, run count, cost, feedback rate, and last active date
- Enable or disable users (takes effect immediately on their next request)
- Adjust per-user daily and monthly rate limits
- View a user's run history

### All Submissions Tab

Browse every pipeline run across all users:

- Sort by any column (user, status, duration, cost, date)
- Filter by user, status, or date range
- Click a run to view its pipeline results and logs
- Paginated (20 per page)

### Feedback Insights Tab

The research data view:

- Average scores for accuracy, completeness, usefulness, and likelihood to recommend
- Time savings analysis (manual effort vs. correction effort distributions)
- Issue heatmap (6 issue types x 4 severity levels, aggregated)
- Per-question distribution charts
- Individual feedback table with run data joined
- **CSV Export:** Download all feedback joined to run data as a single CSV file -- the primary research dataset

### Config Tab

Edit system settings (stored in the `system_config` database table):

- **Allowed users:** Add/remove email addresses
- **Admin users:** Promote/demote users
- **Default rate limits:** Edit daily and monthly maximums
- **Consent version:** View and bump (triggers re-consent for all users)
- **Data export:** Download CSV files of all runs, users, consent records, or feedback

---

## Storage Configuration

### Local Storage (Development)

Default when `CVICHE_STORAGE_BACKEND=local` (or unset).

Files are stored in:

- `web_interface/uploads/` -- uploaded CV files (overridable with `CVICHE_LOCAL_STORAGE_DIR`)
- `web_interface/outputs/{run_id}/` -- pipeline outputs per run

Downloads are served directly by the backend.

### S3 Storage (Production)

Active when `CVICHE_STORAGE_BACKEND=s3`.

S3 key structure:

```
{prefix}/runs/{run_id}/input/{original_filename}
{prefix}/runs/{run_id}/steps/{stage_id}/{output_filename}
{prefix}/runs/{run_id}/steps/{stage_id}/prompt_logs/{log_filename}
{prefix}/runs/{run_id}/output/{final_document}
```

The pipeline runs on local ephemeral storage within the pod. At step boundaries:

1. **Upload:** File lands in S3 first; pulled to local ephemeral storage at run start.
2. **Per-step:** After each step completes, outputs are pushed to S3 immediately (crash resilience).
3. **Downloads:** Backend generates short-lived presigned S3 URLs (5-minute expiry) after auth and ownership checks pass.

Required environment variables: `CVICHE_S3_BUCKET` and optionally `CVICHE_S3_PREFIX`.

---

## API Reference

Full Swagger documentation is available at `http://localhost:8000/docs` when the backend is running.

### Authentication Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/auth/login` | No | Email-based login. Rate-limited: 10 attempts per IP per minute. |
| `POST` | `/api/auth/logout` | Yes | Clear session cookie. |
| `GET` | `/api/auth/me` | Yes | Current user info, role, consent status, and usage quota. |

### Consent Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/consent` | Yes | Get consent text (markdown), version, and whether the current user has consented. |
| `POST` | `/api/consent` | Yes | Submit consent with default submission type (`own_cv` or `authorized_admin`). |

### Upload and Run Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/upload` | Yes | Upload a CV file (.docx or .pdf). Checks consent and rate limits. Creates a new run. |
| `POST` | `/api/estimate` | Yes | Estimate processing cost and time for a file without saving it. |
| `GET` | `/api/runs?offset=0&limit=20` | Yes | Paginated list of runs for the current user, most recent first. |
| `GET` | `/api/run/{run_id}/status` | Yes | Run status with all step details (status, duration, cost, output files). |
| `POST` | `/api/run/{run_id}/start` | Yes | Start pipeline execution for a created run. |
| `POST` | `/api/run/{run_id}/cancel` | Yes | Cancel a running pipeline. |
| `POST` | `/api/run/{run_id}/restart` | Yes | Create a new run using the same uploaded file. Inherits submission type. |
| `POST` | `/api/run/{run_id}/retry/{step_number}` | Yes | Retry a failed step. |
| `GET` | `/api/run/{run_id}/quality?step=N` | Yes | Data quality report for a specific pipeline step. |

### Step and Data Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/run/{run_id}/step/{step_number}` | Yes | Step details with logs and output preview. |
| `GET` | `/api/run/{run_id}/data/{filename}` | Yes | Download or preview an output file (JSON preview with `?preview=true`). |
| `GET` | `/api/run/{run_id}/data/{filename}/json` | Yes | Get raw JSON content for viewer display. |
| `GET` | `/api/run/{run_id}/prompt-logs?step=N` | Yes | Prompt logs for a specific step (LLM interaction details). |

### Feedback Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/run/{run_id}/feedback` | Yes | Get existing feedback for this user on this run, plus run context (completed stages, populated WCM sections). |
| `POST` | `/api/run/{run_id}/feedback` | Yes | Submit feedback. Returns 409 if the user has already submitted feedback for this run. |
| `GET` | `/api/runs/feedback-status` | Yes | Feedback status for all of the current user's completed runs (for badges and banner). |

### Admin Endpoints

All admin endpoints require the `admin` role. Non-admin users receive a 403 response.

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/admin/stats` | Admin | Dashboard overview: total runs, active users, total cost, feedback rate. |
| `GET` | `/api/admin/users` | Admin | All users with per-user stats (runs today, total cost, feedback count). |
| `PUT` | `/api/admin/users/{user_id}` | Admin | Update user role, status, or rate limits. |
| `GET` | `/api/admin/runs?offset=0&limit=20&user=&status=` | Admin | All runs, filterable by user email and status, paginated. |
| `GET` | `/api/admin/config` | Admin | Current system config (allowed users, admins, rate limits, consent version). |
| `PUT` | `/api/admin/config` | Admin | Update system config. Validates constraints (e.g., at least one admin). |
| `GET` | `/api/admin/export/{type}` | Admin | CSV export. Types: `runs`, `users`, `consent`, `feedback`. Logged with admin identity. |

### WebSocket Endpoint

| Protocol | Path | Auth | Description |
|----------|------|------|-------------|
| `WS` | `/ws/run/{run_id}/stream` | Yes | Real-time pipeline events. Validates session cookie on upgrade and checks run ownership. |

### Other Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/livez` | No | Liveness probe. 200 if the process is up. Does not touch the DB or S3. |
| `GET` | `/readyz` | No | Readiness probe. 200 if DB and (when storage backend is `s3`) S3 are reachable. 503 with a JSON `checks` body otherwise. |
| `GET` | `/health` | No | Deprecated alias for `/livez`. Kept for backward compatibility. |
| `GET` | `/` | No | API info with version and docs link. |

---

## Error Response Format

All API errors use a consistent JSON format:

```json
{
  "error": "rate_limit_exceeded",
  "message": "Daily limit reached (10/10). Resets at midnight ET.",
  "details": {}
}
```

The frontend uses `error` for programmatic handling (e.g., redirect to login on `auth_required`, show banner on `rate_limit_exceeded`) and `message` for user-facing display.

### Standard Error Codes

| Error Code | HTTP Status | When |
|------------|-------------|------|
| `auth_required` | 401 | No session or expired |
| `account_disabled` | 401 | User disabled by admin |
| `forbidden` | 403 | Insufficient permissions or cross-origin request rejected |
| `consent_required` | 403 | Consent not given for current version |
| `not_found` | 404 | Resource not found |
| `file_not_found` | 404 | Original file deleted (for restart) |
| `duplicate_feedback` | 409 | User already submitted feedback for this run |
| `validation_error` | 422 | Invalid request data |
| `rate_limit_exceeded` | 429 | Daily or monthly run limit hit |
| `login_rate_limited` | 429 | Too many login attempts from one IP |
| `server_error` | 500 | Unexpected error |

---

## Monitoring and Logging

### Structured JSON Logs

All application logs use JSON Lines format, written to stdout (standard for containerized deployments):

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

| Event | When |
|-------|------|
| `user_login` | Successful login |
| `user_login_denied` | Email not on allow list |
| `user_login_rate_limited` | Too many login attempts from one IP |
| `consent_given` | User submits consent |
| `run_started` | Pipeline run begins |
| `run_completed` | Pipeline run finishes (includes status, duration, cost) |
| `run_cancelled` | User cancels a run |
| `rate_limit_hit` | Upload rejected due to limit |
| `feedback_submitted` | User submits feedback form |
| `admin_config_changed` | Admin changes a setting |
| `admin_user_updated` | Admin changes user status/role/limits |
| `admin_export` | Admin exports data |

### Health Probes

The backend exposes two probes:

- `GET /livez` — liveness. Returns `{"status": "ok"}` immediately. Wire this to container HEALTHCHECK and Kubernetes `livenessProbe`. A failure here means "restart the container."
- `GET /readyz` — readiness. Runs `SELECT 1` against the DB and (when `CVICHE_STORAGE_BACKEND=s3`) `head_bucket` against `CVICHE_S3_BUCKET`. Returns 200 with `{"status": "ready", "checks": {...}}` on success or 503 with `{"status": "not_ready", "checks": {...}}` naming the failing check(s). Wire this to Kubernetes `readinessProbe` and any LB target-group health check. A failure here means "de-list this replica."

`GET /health` is preserved as a deprecated alias for `/livez`.

---

## Project Structure

```
web_interface/
├── backend/
│   ├── app/
│   │   ├── api/                    # API route modules
│   │   │   ├── auth_routes.py      # Login, logout, /auth/me
│   │   │   ├── upload.py           # File upload and cost estimation
│   │   │   ├── runs.py             # Run status, start, cancel, restart, quality
│   │   │   ├── steps.py            # Step details, output downloads, prompt logs
│   │   │   ├── feedback_routes.py  # Feedback GET/POST, feedback status
│   │   │   ├── consent_routes.py   # Consent GET/POST
│   │   │   ├── admin_routes.py     # Admin dashboard APIs and CSV export
│   │   │   └── websocket.py        # Real-time pipeline streaming
│   │   ├── pipeline/               # Pipeline orchestration
│   │   │   ├── orchestrator.py     # Main executor
│   │   │   ├── step_registry.py    # 12-step definitions with weights and timing
│   │   │   └── event_emitter.py    # WebSocket events
│   │   ├── storage/                # File storage abstraction
│   │   │   ├── base.py             # RunStorage abstract interface
│   │   │   ├── local_storage.py    # LocalRunStorage (filesystem)
│   │   │   └── s3.py               # S3RunStorage (production)
│   │   ├── auth.py                 # Session management, middleware, cookie handling
│   │   ├── config_loader.py        # YAML config loading, DB seeding
│   │   ├── consent.py              # Consent text loading, hash integrity check
│   │   ├── rate_limiter.py         # Daily/monthly rate limit checks
│   │   ├── database.py             # SQLAlchemy engine + session
│   │   ├── models.py               # All SQLAlchemy models
│   │   ├── schemas.py              # Pydantic request/response schemas
│   │   └── main.py                 # FastAPI app, CORS, CSRF middleware, routes
│   ├── alembic/                    # Alembic migrations
│   │   ├── env.py                  # Migration environment config
│   │   └── versions/               # Migration scripts
│   ├── alembic.ini                 # Alembic configuration
│   ├── auth_config.yaml            # Bootstrap auth config (read-only)
│   ├── consent_text.md             # Consent text shown to users
│   └── requirements.txt            # Python dependencies
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── LoginPage.tsx       # Email login form
│   │   │   ├── ConsentPage.tsx     # Consent form with checkbox
│   │   │   ├── UploadPage.tsx      # File upload + options + run history
│   │   │   ├── PipelineViewer.tsx  # Real-time pipeline progress
│   │   │   ├── RunHistory.tsx      # Paginated run list with badges
│   │   │   ├── FeedbackForm.tsx    # 12-question feedback form
│   │   │   ├── FeedbackBanner.tsx  # Amber nudge banner
│   │   │   ├── CompletionInterstitial.tsx  # Post-run overlay
│   │   │   ├── AdminDashboard.tsx  # Admin shell with tabs
│   │   │   ├── AdminUsers.tsx      # User management table
│   │   │   ├── AdminSubmissions.tsx  # All runs browser
│   │   │   ├── AdminFeedbackInsights.tsx  # Research data view
│   │   │   └── AdminConfig.tsx     # Config management
│   │   ├── contexts/
│   │   │   └── AuthContext.tsx      # Auth state, login/logout, consent check
│   │   ├── App.tsx                 # Routes, auth guards (RequireAuth, RequireConsent, RequireAdmin)
│   │   ├── main.tsx                # Entry point
│   │   └── index.css               # Tailwind base styles
│   ├── public/                     # Static assets
│   ├── package.json
│   ├── vite.config.ts              # Vite config with API proxy
│   ├── tailwind.config.js
│   └── tsconfig.json
├── uploads/                        # Uploaded CVs (local storage)
├── outputs/                        # Pipeline outputs (local storage)
├── USER_GUIDE.md                   # End-user documentation
└── TECHNICAL_README.md             # This file
```

---

## Troubleshooting

### Login fails silently (no error, but not logged in)

**Cause:** The session cookie is not being set by the browser.

**Fix:** Check `CVICHE_SECURE_COOKIES`. If the deployment does not have TLS (no HTTPS), set `CVICHE_SECURE_COOKIES=false`. When `Secure=true`, browsers silently refuse to set cookies over HTTP. This is the most common Phase 1 deployment issue.

### CORS errors in browser console

**Cause:** The frontend origin is not in the allowed origins list.

**Fix:** Set `CVICHE_ALLOWED_ORIGINS` to include the exact origin of the frontend (e.g., `http://localhost:3000`). No trailing slashes. The value is comma-separated for multiple origins. The default includes `http://localhost:3000`, `http://localhost:5173`, and their `127.0.0.1` equivalents.

### Database connection refused

**Cause:** MariaDB is not running, or the connection string is wrong.

**Fix:**
1. Verify MariaDB is running: `mysql -u root -e "SELECT 1"`
2. Verify the database exists: `mysql -u root -e "SHOW DATABASES LIKE 'cviche'"`
3. Check `CVICHE_DATABASE_URL` is correct. The format is: `mysql+pymysql://user:password@host:port/database`
4. In Docker, ensure the `db` service is healthy before the backend starts (docker-compose handles this via `depends_on` with health check).

### Alembic migration fails

**Cause:** Schema drift between the migration files and the actual database state.

**Fix:**
1. Check current migration state: `alembic current`
2. Check migration history: `alembic history`
3. If the database was modified outside Alembic, you may need to stamp the current state: `alembic stamp head`
4. Never manually modify tables that Alembic manages.

### WebSocket connection fails

**Cause:** The WebSocket upgrade request is not reaching the backend, or auth is failing on the upgrade.

**Fix:**
1. In development, verify the Vite proxy is configured in `vite.config.ts` to forward `/ws` to the backend.
2. In production (nginx), verify the WebSocket proxy configuration includes `proxy_http_version 1.1` and the `Upgrade`/`Connection` headers.
3. Check that session cookies are being sent on the WebSocket upgrade. The backend validates the `cviche_session` cookie on every WebSocket connection and checks run ownership before accepting.

### Pipeline fails at a specific stage

**Cause:** Varies by stage. Common issues:

- **Stage 1a (Hierarchy Extraction):** OpenAI API timeout or rate limit. Check `OPENAI_API_KEY` is valid and has sufficient quota.
- **Stage 2 (Entry Extraction):** Corrupt or password-protected document. Try converting to .docx first.
- **Stages 3a/3b (Taxonomy Mapping):** LLM response parsing error. Check step logs for the raw LLM response.
- **Stage 4 (Field Extraction):** Token limit exceeded for very large CVs. The step logs will show a `finish_reason: length` warning.
- **Stage 5 (PubMed Enrichment):** Network connectivity to PubMed E-utilities API. Check firewall rules.
- **Stage 5b (Institution Enrichment):** LLM connectivity. Less common than PubMed failures.
- **Stage 6 (WCM Word Template):** Template rendering error. Check the step logs for details.

### Rate limit message appears unexpectedly

**Cause:** The user has hit their daily (10) or monthly (50) run limit.

**Fix:**
1. Check the user's current usage in the admin dashboard Users tab.
2. Adjust per-user limits via the admin dashboard if needed (set `daily_limit` or `monthly_limit` on the User record). Setting a value of 0 clears the override and falls back to system defaults.
3. Daily limits reset at midnight Eastern Time. Monthly limits reset on the 1st.

### Admin dashboard shows "Access denied"

**Cause:** The user does not have the `admin` role.

**Fix:** Promote the user to admin via `auth_config.yaml` (add to `admin_users` list and restart) or via the admin dashboard Users tab (if another admin is available). The system prevents demoting the last remaining admin.

### Consent page keeps reappearing

**Cause:** The consent version was bumped in the config but the user has not yet re-consented to the new version.

**Fix:** This is expected behavior. When the consent version is bumped (via admin dashboard Config tab or `auth_config.yaml`), all users must re-consent before they can upload. If this was unintentional, revert the consent version in the admin dashboard.

---

## License

Internal use only -- Weill Cornell Medicine, Samuel J. Wood Library.
