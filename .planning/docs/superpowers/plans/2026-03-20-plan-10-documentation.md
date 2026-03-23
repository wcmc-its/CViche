# Plan 10: Documentation (User Guide + Technical README)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce two complete documentation files for CViche's two audiences: (1) a plain-language user guide for non-technical faculty and staff who use the web interface to convert CVs, and (2) a technical README for developers and admins who deploy, configure, and maintain the system.

**Architecture:** Two standalone markdown files at the top level of the repository. `USER_GUIDE.md` covers the end-to-end user experience from login through download and feedback. `TECHNICAL_README.md` covers architecture, setup, configuration, deployment, and troubleshooting. Both reference the production-readiness spec as the source of truth for feature behavior.

**Tech Stack:** Markdown

**Spec:** `docs/superpowers/specs/2026-03-20-production-readiness-design.md` -- all sections (the documentation covers the complete system).

**Depends on:** All other plans (1-9) define the features being documented. This plan can be implemented at any time -- it documents the target state regardless of which plans have been completed.

---

### Task 1: USER_GUIDE.md

**Files:**
- Create: `web_interface/USER_GUIDE.md`

- [ ] **Step 1: Create the user guide**

Create `web_interface/USER_GUIDE.md` with the following complete content:

```markdown
# CViche User Guide

## What Is CViche?

CViche is a tool built by the Samuel J. Wood Library at Weill Cornell Medicine that converts your existing CV into the official WCM CV format. It uses AI to read your CV, identify each section (education, publications, grants, etc.), and reorganize everything into the correct WCM template -- saving you hours of manual reformatting.

**What CViche does:**

- Reads your CV in Word (.docx) or PDF format
- Identifies and classifies every entry (degrees, positions, publications, grants, honors, and more)
- Maps each entry to the correct section of the WCM CV template (71 possible sections)
- Enriches publication data using PubMed (adds author lists, journal names, MeSH terms)
- Generates a formatted Word document in the official WCM CV layout
- Optionally generates a research narrative summary

**What CViche does NOT do:**

- CViche does not replace human review. The output is a starting point that should be reviewed and corrected before official use.
- CViche does not access any external systems on your behalf. It only processes the document you upload.
- CViche does not store or share your CV outside the project team.

---

## Getting Started

### Logging In

1. Navigate to the CViche web application in your browser. (Your administrator will provide the URL.)
2. On the login screen, enter your **full name** and your **WCM email address** (e.g., `abc1234@med.cornell.edu`).
3. Click **Sign In**.

Your email must be on the approved access list. If you see "Access denied," contact your administrator to request access.

Once signed in, your session lasts 7 days. You do not need to log in again during that period unless you explicitly log out or your browser clears cookies.

> **Note:** In a future update, CViche will support single sign-on through WCM's Enterprise Directory (SAML SSO). When that is available, you will log in with your standard WCM credentials.

### The Consent Process

The first time you use CViche, you will see a consent page before you can upload a CV. This is a one-time step (unless the consent text is updated, in which case you will be asked to re-consent).

**What you are agreeing to:**

- The Samuel J. Wood Library is conducting a pilot of CViche, a prototype tool that uses AI to convert CVs into WCM format.
- Your CV will be processed using AI. The output will be reviewed by project staff, and your feedback will be analyzed to improve the system.
- You may omit personal contact details from your CV before uploading if you prefer.
- Your name, email, and authorization role are collected as part of the pilot.

**Authorization role:** On the consent page, you will be asked to select one of two roles:

- **Submitting my own CV** -- You are uploading your own CV for conversion.
- **Authorized on behalf of faculty** -- You are an authorized staff member uploading a CV on behalf of a faculty member.

This selection becomes your default for future uploads but can be changed on a per-upload basis.

After reading the consent text, check the consent checkbox and click **Agree and Continue**.

---

## Uploading a CV

### Supported File Formats

CViche accepts the following file types:

- **Word documents** (.docx) -- recommended for best results
- **PDF files** (.pdf)

For best results, use Word format. PDFs sometimes lose structural information (headers, tables, lists) during conversion, which can affect the quality of the output.

### Upload Steps

1. On the main page, click **Select a file** or drag and drop your CV file.
2. Configure the three options below the file input:

   - **Submission type:** Defaults to the role you selected during consent. Toggle between "Submitting my own CV" and "Authorized on behalf of faculty" if needed for this specific upload.
   - **Show track changes:** On by default. When enabled, the output Word document includes track changes marks showing what the pipeline modified from the original text. This makes it easy to see exactly what was changed.
   - **Show pipeline comments:** Off by default. When enabled, the output document includes comments in the margins with classification details, confidence scores, and processing notes. This is useful if you want to understand why the pipeline made specific decisions.

3. Click **Start Pipeline**.

### Usage Limits

To manage system resources during the pilot, each user is limited to:

- **10 runs per day** (resets at midnight Eastern Time)
- **50 runs per month** (resets on the 1st of each month)

Your remaining quota is displayed on the upload page (e.g., "7 of 10 runs remaining today"). When the limit is reached, the upload button is disabled with an explanation of when the limit resets.

Administrators are not subject to usage limits.

---

## What Happens During Processing

After you click **Start Pipeline**, CViche processes your CV through a series of stages. This typically takes **2 to 8 minutes** depending on the length of your CV and the number of publications.

### The Progress View

The pipeline viewer shows:

- **Left sidebar:** All processing stages with status icons (pending, running, complete, or error).
- **Top bar:** Your run ID, total processing cost, token usage, and overall status.
- **Main panel:** Details for the currently selected stage, including logs and output files.

### Processing Stages

1. **Identify Sections** -- Detects the major sections of your CV (education, publications, etc.)
2. **Preserve Formatting** -- Retains paragraph structure and formatting from the original
3. **Break Into Items** -- Splits each section into individual entries (one per degree, one per publication, etc.)
4. **Categorize Entries** -- Classifies each entry into the correct WCM taxonomy category
5. **Fix Unknowns** -- Re-examines any entries that could not be classified on the first pass
6. **Extract Structured Data** -- Pulls out specific fields (dates, titles, institutions, etc.)
7. **AI Assist (LLM Fallback)** -- Handles complex or ambiguous entries using additional AI analysis
8. **Enrich with PubMed** -- Matches publications against PubMed and adds metadata (author lists, journals, MeSH terms). This stage is optional and only runs when publications are detected.
9. **Generate Final Document** -- Produces the formatted Word document in the WCM CV template

You can watch each stage complete in real time. If a stage encounters an error, it will be flagged and you can view the error details in the logs.

---

## Downloading Your Output

When processing is complete, a success overlay appears with:

- **Download WCM CV** button -- downloads the formatted Word document
- **Give Feedback** button -- opens the feedback form (more on this below)
- **"I'll do this later"** link -- dismisses the overlay

You can also download the output at any time by:

1. Finding the run in your **Previous Runs** list on the upload page.
2. Clicking the run to open the pipeline viewer.
3. Navigating to the final stage and downloading the output file.

### Understanding the Output Document

The output is a Word (.docx) file organized into the WCM CV sections. Here is what you may see:

- **Track changes** (if enabled): Insertions and deletions are marked using Word's Track Changes feature. This shows you exactly what text was added, removed, or moved during conversion. You can accept or reject changes individually in Word.
- **Comments** (if pipeline comments are enabled): Margin comments from the pipeline explain classification decisions, show confidence scores, and note any entries that required special handling. These can be deleted in bulk in Word when you no longer need them.
- **Empty sections:** WCM sections that had no matching content in your CV are included as empty placeholders. You can fill these in manually if needed, or delete them.

---

## Giving Feedback

Your feedback is critical to improving CViche. It takes approximately **3 minutes** and directly informs the research behind this project.

### When You Will Be Asked

- **Right after processing completes:** The success overlay includes a prominent "Give Feedback" button.
- **On the upload page:** An amber banner shows how many of your runs are awaiting feedback (e.g., "You have 3 runs awaiting feedback"). This banner remains visible until all completed runs have feedback.
- **In run history:** Each completed run shows either an amber "Needs feedback" badge or a green "Feedback given" badge.
- **In the pipeline viewer:** Completed runs have a "Feedback" tab alongside the Logs and Prompt Logs tabs.

### What the Feedback Form Asks

The form is tailored to the specific run you are reviewing. Questions include:

1. **Your role** -- Are you the CV subject, departmental staff, or library/faculty affairs staff?
2. **Accuracy** (1-10) -- Were names, dates, and content faithfully preserved?
3. **Completeness** (1-10) -- Was all content from the original captured?
4. **Usefulness** (1-5) -- How usable is this as a starting point?
5. **Manual effort estimate** -- How long would it take to reformat this CV manually?
6. **Correction effort** -- How long to fix the CViche output?
7. **Publication enrichment quality** (1-5) -- Shown only if PubMed enrichment ran
8. **Research summary quality** (1-5) -- Shown only if a research summary was generated
9. **Issues noticed** -- Rate the severity of six common issue types (missing content, wrong section, etc.)
10. **Issue locations** -- Check which WCM sections had problems (only sections that were populated in your run are shown)
11. **Biggest concern** -- Free text describing the single most important issue
12. **Likelihood to recommend** (1-5) -- Would you recommend CViche to a colleague?

You can only submit feedback once per run. If both the CV subject and an administrator review the same run, both can submit separate feedback.

### Why Feedback Matters

Feedback data is the primary research dataset for evaluating CViche's effectiveness. Your responses help us:

- Identify which pipeline stages need improvement
- Measure time savings compared to manual conversion
- Understand which WCM sections are most problematic
- Prioritize bug fixes and feature improvements

---

## Viewing Previous Runs

Your **Previous Runs** list appears on the upload page below the file upload area. It shows your 20 most recent runs, with the option to load more.

For each run, you can see:

- **Filename** and **date**
- **Status** (complete, running, failed, or cancelled)
- **Duration** and **cost**
- **Feedback badge** (amber "Needs feedback" or green "Feedback given")

Click any run to open it in the pipeline viewer, where you can:

- Review logs for each stage
- Download output files
- Give feedback (for completed runs)
- View prompt logs (detailed AI interaction data)

### Re-running a CV

If a run failed or you want to try again with different settings:

1. Click the failed or completed run in your history.
2. Click **Restart with this file** to create a new run using the same uploaded document.
3. Adjust settings (track changes, pipeline comments, submission type) if desired.
4. Click **Start Pipeline** to begin.

Each restart counts as a new run against your usage limits.

If the original file is no longer available, you will be prompted to upload the file again.

---

## Frequently Asked Questions

### Why was something classified in the wrong section?

CViche uses AI to determine which WCM section each CV entry belongs to. With 71 possible sections, the AI sometimes makes mistakes, especially with:

- Entries that could fit multiple categories (e.g., a teaching award could go under Honors or Teaching)
- Unusual formatting or non-standard CV structures
- Entries in languages other than English

If you notice misclassifications, please report them in the feedback form (question 9, "Wrong section placement"). This data helps us improve the classification model.

### Can I re-run a CV?

Yes. Click the run in your Previous Runs list and use the "Restart with this file" button. Each re-run counts toward your daily and monthly usage limits.

### Who sees my data?

Your CV and the converted output are accessible only to:

- **You** (the person who uploaded it)
- **CViche administrators** (project team members with admin access)

Your data is used solely for the CViche pilot project. Feedback responses are anonymized in aggregate reporting. Your CV is stored securely and is not shared outside the project team.

### What happens if processing fails partway through?

If the pipeline fails at any stage, you can:

1. View the partial results and logs up to the point of failure.
2. Use "Restart with this file" to try again.

Common causes of failure include very long CVs that exceed processing limits, or unusual document formatting that the parser cannot interpret.

### Can I use CViche for someone else's CV?

Yes, if you are authorized to do so. Select "Authorized on behalf of faculty" as your submission type. This is intended for departmental staff and faculty affairs personnel who manage CV submissions.

### How long are my files kept?

Uploaded CVs and output documents are retained for the duration of the pilot. They are not automatically deleted. If the retention policy changes, you will be notified.

### The page says my session expired. What happened?

Your login session lasts 7 days. After that, you need to sign in again. In development environments, sessions may also expire when the server restarts. This is normal.

---

## Getting Help

If you encounter issues or have questions about CViche:

- **Technical issues** (errors, failed runs, login problems): Contact your CViche administrator.
- **Questions about the WCM CV format**: Contact the Office of Faculty Affairs.
- **Feedback about CViche itself**: Use the in-app feedback form after each run.

Your administrator can adjust your usage limits, troubleshoot access issues, and escalate technical problems to the development team.
```

- [ ] **Step 2: Verify the file renders correctly**

Open `web_interface/USER_GUIDE.md` in a markdown viewer and confirm:
- All headings are properly nested (H1 > H2 > H3)
- Lists are properly formatted
- No broken links or formatting artifacts
- Content flows logically from start to finish

- [ ] **Step 3: Commit**

```bash
git add web_interface/USER_GUIDE.md
git commit -m "docs: add USER_GUIDE.md for non-technical faculty and staff"
```

---

### Task 2: TECHNICAL_README.md

**Files:**
- Create: `web_interface/TECHNICAL_README.md`

- [ ] **Step 1: Create the technical README**

Create `web_interface/TECHNICAL_README.md` with the following complete content:

```markdown
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

**Backend:** Python 3.11 + FastAPI + SQLAlchemy + Alembic. Runs the pipeline orchestrator, exposes REST and WebSocket APIs, manages auth and sessions.

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

The `OPENAI_API_KEY_WORK` environment variable must be set in your host shell before running `docker compose up`. All other environment variables have development defaults.

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

The backend reads `CVICHE_DATABASE_URL` from the environment. If not set, it defaults to `mysql+pymysql://root@localhost:3306/cviche`.

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
- Health check: http://localhost:8000/health

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `CVICHE_DATABASE_URL` | Yes (prod) | `mysql+pymysql://root@localhost:3306/cviche` | MariaDB connection string. Used by SQLAlchemy and Alembic. |
| `CVICHE_SESSION_SECRET` | Yes (prod) | Random (dev only) | Secret key for signing session cookies with `itsdangerous`. If not set, a random key is generated on startup and a warning is logged. Sessions will not survive server restarts in dev. |
| `CVICHE_SECURE_COOKIES` | No | `true` | Set to `false` for HTTP-only deployments without TLS. When `true`, the browser requires HTTPS to set the session cookie. If the cookie silently fails to set and login does not work, this is the most likely cause. |
| `CVICHE_ALLOWED_ORIGINS` | No | `http://localhost:3000,http://localhost:5173` | Comma-separated list of allowed CORS origins. Also used for Origin/Referer CSRF checking on state-changing requests. |
| `CVICHE_STORAGE_BACKEND` | No | `local` | `local` for filesystem storage (dev), `s3` for S3 storage (production). |
| `CVICHE_S3_BUCKET` | If `s3` | -- | S3 bucket name. Required when `CVICHE_STORAGE_BACKEND=s3`. |
| `CVICHE_S3_PREFIX` | No | `cviche` | Key prefix within the S3 bucket. Allows sharing a bucket across environments. |
| `OPENAI_API_KEY_WORK` | Yes | -- | OpenAI API key for all LLM calls in the pipeline. |

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

Alembic reads the database URL from the `CVICHE_DATABASE_URL` environment variable (configured in `alembic/env.py`). The fallback in `alembic.ini` is for local dev only.

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
  - jsmith@med.cornell.edu

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

## Deployment

### Docker Compose (Production)

For production deployment with Docker Compose:

```bash
cd web_interface

# Copy and fill in production environment variables
cp .env.example .env
# Edit .env with production values

# Start with production overrides
docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build -d
```

The production compose file:

- Removes the local MariaDB service (use a managed instance)
- Disables hot-reload volume mounts
- Sets `CVICHE_STORAGE_BACKEND=s3`
- Sets `CVICHE_SECURE_COOKIES=true`
- Runs uvicorn with 4 workers
- Builds the frontend with nginx (serves static assets, proxies API/WebSocket)

### Deployment to EKS

Kubernetes manifests are in `web_interface/k8s/`. The deployment uses:

- **Namespace:** `cviche`
- **Backend Deployment:** 2 replicas, FastAPI container, ConfigMap-mounted config files, Secret-injected credentials
- **Frontend Deployment:** 2 replicas, nginx container serving the React build
- **Services:** ClusterIP for both backend and frontend
- **Ingress:** AWS ALB with TLS termination, sticky sessions for WebSocket

Key configuration:

- **Config files** (`auth_config.yaml`, `consent_text.md`): Mounted via Kubernetes ConfigMap. See `k8s/configmap.yaml`.
- **Secrets** (`CVICHE_DATABASE_URL`, `CVICHE_SESSION_SECRET`, `OPENAI_API_KEY_WORK`): Kubernetes Secrets, injected as environment variables. See `k8s/secrets.yaml` (template only -- never commit actual values).
- **S3 access:** Pod's service account uses IRSA (IAM Roles for Service Accounts). No access keys in config.
- **Migrations:** Run automatically on container startup by the backend entrypoint script.

Apply manifests:

```bash
kubectl apply -f web_interface/k8s/namespace.yaml
kubectl apply -f web_interface/k8s/configmap.yaml
# Create secrets via kubectl (do not apply the template file with placeholder values)
kubectl create secret generic cviche-secrets \
  --namespace=cviche \
  --from-literal=CVICHE_DATABASE_URL='mysql+pymysql://...' \
  --from-literal=CVICHE_SESSION_SECRET='...' \
  --from-literal=OPENAI_API_KEY_WORK='...'
kubectl apply -f web_interface/k8s/
```

See the individual manifest files for full configuration details and resource limits.

---

## Storage Configuration

### Local Storage (Development)

Default when `CVICHE_STORAGE_BACKEND=local` (or unset).

Files are stored in:

- `web_interface/uploads/` -- uploaded CV files
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

### Health Check

`GET /health` returns database connectivity status:

```json
{"status": "healthy", "database": "connected"}
```

Returns HTTP 503 if the database is unreachable.

---

## Troubleshooting

### Login fails silently (no error, but not logged in)

**Cause:** The session cookie is not being set by the browser.

**Fix:** Check `CVICHE_SECURE_COOKIES`. If the deployment does not have TLS (no HTTPS), set `CVICHE_SECURE_COOKIES=false`. When `Secure=true`, browsers silently refuse to set cookies over HTTP. This is the most common Phase 1 deployment issue.

### CORS errors in browser console

**Cause:** The frontend origin is not in the allowed origins list.

**Fix:** Set `CVICHE_ALLOWED_ORIGINS` to include the exact origin of the frontend (e.g., `http://localhost:3000`). No trailing slashes. The value is comma-separated for multiple origins.

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

**Cause:** The WebSocket upgrade request is not reaching the backend.

**Fix:**
1. In development, verify the Vite proxy is configured in `vite.config.ts` to forward `/ws` to the backend.
2. In production (nginx), verify the WebSocket proxy configuration includes `proxy_http_version 1.1` and the `Upgrade`/`Connection` headers.
3. Check that session cookies are being sent on the WebSocket upgrade (requires same-origin or correct CORS credentials config).

### Pipeline fails at a specific stage

**Cause:** Varies by stage. Common issues:

- **Stage 1 (parsing):** Corrupt or password-protected document. Try converting to .docx first.
- **Stage 5-7 (AI stages):** OpenAI API timeout or rate limit. Check `OPENAI_API_KEY_WORK` is valid and has sufficient quota.
- **Stage 8 (PubMed):** Network connectivity to PubMed E-utilities API. Check firewall rules.
- **Stage 9 (document generation):** Template rendering error. Check the step logs for details.

### Rate limit message appears unexpectedly

**Cause:** The user has hit their daily (10) or monthly (50) run limit.

**Fix:**
1. Check the user's current usage in the admin dashboard Users tab.
2. Adjust per-user limits via the admin dashboard if needed (set `daily_limit` or `monthly_limit` on the User record).
3. Daily limits reset at midnight Eastern Time. Monthly limits reset on the 1st.

### Admin dashboard shows "Access denied"

**Cause:** The user does not have the `admin` role.

**Fix:** Promote the user to admin via `auth_config.yaml` (add to `admin_users` list and restart) or via the admin dashboard Config tab (if another admin is available).

---

## API Reference

Full Swagger documentation is available at `http://localhost:8000/docs` when the backend is running.

### Key Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/auth/login` | No | Email-based login (rate-limited: 10/min/IP) |
| `POST` | `/api/auth/logout` | Yes | Clear session |
| `GET` | `/api/auth/me` | Yes | Current user info + quota |
| `GET` | `/api/consent` | Yes | Consent text, version, user consent status |
| `POST` | `/api/consent` | Yes | Submit consent |
| `POST` | `/api/upload` | Yes | Upload a CV file |
| `GET` | `/api/runs?offset=0&limit=20` | Yes | Paginated runs for current user |
| `GET` | `/api/run/{run_id}/status` | Yes | Run status + steps |
| `POST` | `/api/run/{run_id}/start` | Yes | Start pipeline |
| `GET` | `/api/run/{run_id}/data/{filename}` | Yes | Download output file |
| `GET` | `/api/run/{run_id}/feedback` | Yes | Get feedback + run context |
| `POST` | `/api/run/{run_id}/feedback` | Yes | Submit feedback (409 if duplicate) |
| `POST` | `/api/run/{run_id}/restart` | Yes | Restart run with same file |
| `WS` | `/ws/run/{run_id}/stream` | Yes | Real-time pipeline events |
| `GET` | `/api/admin/stats` | Admin | Dashboard overview stats |
| `GET` | `/api/admin/users` | Admin | All users with stats |
| `PUT` | `/api/admin/users/{user_id}` | Admin | Update user role/status/limits |
| `GET` | `/api/admin/runs` | Admin | All runs (filterable, paginated) |
| `GET` | `/api/admin/config` | Admin | System config |
| `PUT` | `/api/admin/config` | Admin | Update config |
| `GET` | `/api/admin/feedback` | Admin | All feedback (paginated) |
| `GET` | `/api/admin/export/{type}` | Admin | CSV export (runs, users, consent, feedback) |
| `GET` | `/health` | No | Health check with DB connectivity |

### Error Response Format

All API errors use a consistent JSON format:

```json
{
  "error": "rate_limit_exceeded",
  "message": "Daily limit reached (10/10). Resets at midnight ET.",
  "details": {}
}
```

| Error Code | HTTP Status | When |
|------------|-------------|------|
| `auth_required` | 401 | No session or expired |
| `account_disabled` | 401 | User disabled by admin |
| `forbidden` | 403 | Insufficient permissions |
| `consent_required` | 403 | Consent not given for current version |
| `not_found` | 404 | Resource not found |
| `validation_error` | 422 | Invalid request data |
| `rate_limit_exceeded` | 429 | Daily or monthly limit hit |
| `login_rate_limited` | 429 | Too many login attempts |
| `server_error` | 500 | Unexpected error |

---

## Project Structure

```
web_interface/
├── backend/
│   ├── app/
│   │   ├── api/                    # API route modules
│   │   │   ├── auth_routes.py      # Login, logout, /auth/me
│   │   │   ├── upload.py           # File upload
│   │   │   ├── runs.py             # Run status, start, cancel, restart
│   │   │   ├── steps.py            # Step details, output downloads
│   │   │   ├── feedback.py         # Feedback GET/POST
│   │   │   ├── consent_routes.py   # Consent GET/POST
│   │   │   ├── admin_routes.py     # Admin dashboard APIs
│   │   │   └── websocket.py        # Real-time pipeline streaming
│   │   ├── pipeline/               # Pipeline orchestration
│   │   │   ├── orchestrator.py     # Main executor
│   │   │   ├── step_registry.py    # Step definitions
│   │   │   └── event_emitter.py    # WebSocket events
│   │   ├── storage/                # File storage abstraction
│   │   │   ├── base.py             # RunStorage abstract interface
│   │   │   ├── local.py            # LocalRunStorage (filesystem)
│   │   │   └── s3.py               # S3RunStorage (production)
│   │   ├── auth.py                 # Session management, middleware
│   │   ├── config_loader.py        # YAML config loading, DB seeding
│   │   ├── rate_limiter.py         # Daily/monthly rate limit checks
│   │   ├── database.py             # SQLAlchemy engine + session
│   │   ├── models.py               # All SQLAlchemy models
│   │   ├── schemas.py              # Pydantic request/response schemas
│   │   └── main.py                 # FastAPI app, middleware, routes
│   ├── alembic/                    # Alembic migrations
│   │   ├── env.py                  # Migration environment config
│   │   └── versions/               # Migration scripts
│   ├── alembic.ini                 # Alembic configuration
│   ├── auth_config.yaml            # Bootstrap auth config (read-only)
│   ├── consent_text.md             # Consent text shown to users
│   ├── requirements.txt            # Python dependencies
│   ├── Dockerfile                  # Backend container
│   └── docker-entrypoint.sh        # Runs migrations + starts uvicorn
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
│   │   │   └── AuthContext.tsx      # Auth state, login/logout
│   │   ├── App.tsx                 # Routes, auth guards
│   │   ├── main.tsx                # Entry point
│   │   └── index.css               # Tailwind base styles
│   ├── public/                     # Static assets (logos, backgrounds)
│   ├── package.json
│   ├── vite.config.ts              # Vite config with API proxy
│   ├── tailwind.config.js
│   ├── tsconfig.json
│   ├── Dockerfile                  # Multi-stage: Node build + nginx
│   └── nginx.conf                  # nginx config for production
├── k8s/                            # Kubernetes manifests
│   ├── namespace.yaml
│   ├── configmap.yaml              # auth_config.yaml + consent_text.md
│   ├── secrets.yaml                # Template (never commit real values)
│   ├── backend-deployment.yaml
│   ├── backend-service.yaml
│   ├── frontend-deployment.yaml
│   ├── frontend-service.yaml
│   └── ingress.yaml                # AWS ALB ingress
├── docker-compose.yml              # Local dev stack
├── docker-compose.prod.yml         # Production overrides
├── .env.example                    # Production env var template
├── uploads/                        # Uploaded CVs (local storage)
├── outputs/                        # Pipeline outputs (local storage)
├── USER_GUIDE.md                   # End-user documentation
├── TECHNICAL_README.md             # This file
└── README.md                       # Legacy overview
```

---

## License

Internal use only -- Weill Cornell Medicine, Samuel J. Wood Library.
```

- [ ] **Step 2: Verify the file renders correctly**

Open `web_interface/TECHNICAL_README.md` in a markdown viewer and confirm:
- The architecture diagram renders in a code block
- All tables are properly formatted
- The project structure tree is readable
- Environment variable table is complete (all 8 variables)
- No broken links or formatting artifacts

- [ ] **Step 3: Commit**

```bash
git add web_interface/TECHNICAL_README.md
git commit -m "docs: add TECHNICAL_README.md for developers and admins"
```

---

### Post-Implementation Verification Checklist

- [ ] `USER_GUIDE.md` exists at `web_interface/USER_GUIDE.md` and contains all sections: login, consent, upload, processing, download, feedback, run history, FAQ, contact.
- [ ] `TECHNICAL_README.md` exists at `web_interface/TECHNICAL_README.md` and contains all sections: architecture, prerequisites, quick start, manual setup, env vars, database, auth config, consent management, admin dashboard, deployment, storage, monitoring, troubleshooting.
- [ ] The environment variables table in `TECHNICAL_README.md` lists all 8 variables from the spec.
- [ ] The architecture diagram in `TECHNICAL_README.md` accurately reflects the system components.
- [ ] The project structure tree matches the actual file layout (accounting for files from all plans).
- [ ] The API reference table lists all endpoints from the spec.
- [ ] Both documents render correctly in GitHub's markdown viewer.
- [ ] Neither document contains credentials, API keys, or sensitive information.
- [ ] The user guide uses plain language throughout -- no technical jargon, no code snippets, no references to API endpoints.
- [ ] The technical README is self-contained -- a new developer can set up the system from scratch using only this document.

---

### Summary

| Task | Description | Files | Estimated Time |
|------|-------------|-------|----------------|
| 1 | USER_GUIDE.md (end-user documentation) | `web_interface/USER_GUIDE.md` | 15 min |
| 2 | TECHNICAL_README.md (developer/admin documentation) | `web_interface/TECHNICAL_README.md` | 20 min |

**Total: ~35 minutes (2 tasks, ~6 steps)**

### Files Created

- `web_interface/USER_GUIDE.md`
- `web_interface/TECHNICAL_README.md`
