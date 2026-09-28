# Web App Integration Guide

**Last updated:** 2026-03-27. For a quick start, see the [README](../../README.md#web-interface). For deep architectural details, see the [Technical Documentation](../TECHNICAL_README.md).

## Overview

The CViche web app is a browser-based interface that wraps the same 12-stage CV parsing pipeline used by the CLI, adding real-time progress streaming, user authentication, administrative features, and a polished upload experience.

**Key Principle**: The web backend imports and wraps the same stage functions used by the CLI entry point (`run_full_pipeline.py`) -- no duplicate pipeline logic.

---

## Architecture

```
+---------------------------------------------+
| React Frontend (Vite dev: 3001 / Docker: 3000)
| - 8 typed API client modules               |
| - AuthContext for session state             |
| - WebSocket for real-time pipeline updates  |
+------------------+--------------------------+
                   | HTTP + WebSocket
                   v
+---------------------------------------------+
| FastAPI Backend (local dev: 5002 / Docker: 8000)
| - Service layer (config, run, admin, user)  |
| - Dual-mode auth (email + SAML)            |
| - Security middleware stack                 |
| - Pipeline orchestrator                     |
| - Storage abstraction (local / S3)         |
+------------------+--------------------------+
                   | Direct import
                   v
+---------------------------------------------+
| CV Parsing Pipeline (src/unified_pipeline/) |
| - 12 stages (1a through 6)                 |
| - Same code as CLI                         |
+---------------------------------------------+
```

### Port Reference

| Environment | Backend | Frontend | Database |
|-------------|---------|----------|----------|
| **Local dev** (no Docker) | 5002 | 3001 (Vite) | MariaDB (`DB_HOST` etc.) |
| **Docker Compose** | 8000 | 3000 | MariaDB 11 (container) |
| **Production** | 8000 (4 workers) | 80 (nginx) | Managed MariaDB |

---

## How It Works

### 1. User Uploads CV

The frontend uses a typed API client (`web_interface/frontend/src/api/upload.ts`) to send the file to `POST /api/upload`. The upload includes authentication credentials (session cookie) automatically.

### 2. Backend Creates Run

The upload route handler in `web_interface/backend/app/api/upload.py`:
- Validates the file (magic bytes check, extension allowlist, size limit)
- Checks rate limits via `rate_limiter.py` (per-user daily/monthly caps)
- Creates a `Run` record in the database
- Stores the uploaded file via the storage abstraction (`RunStorage`)
- Launches the pipeline orchestrator in a background thread

### 3. Orchestrator Executes Pipeline

`web_interface/backend/app/pipeline/orchestrator.py` (the `PipelineOrchestrator`):
- Runs each of the 12 pipeline stages sequentially in a background thread
- Captures `stdout`/`stderr` via Python's `redirect_stdout`/`redirect_stderr`
- Parses captured output with regex to detect progress messages
- Converts progress into structured WebSocket events (`STEP_START`, `LOG`, `PROGRESS`, `COST_UPDATE`, `STEP_COMPLETE`, `STEP_ERROR`, `RUN_COMPLETE`)
- Persists step state to the database (`Step` model) at each transition

### 4. Real-time Progress Streaming

The `EventEmitter` (singleton in `event_emitter.py`) broadcasts events to all WebSocket connections registered for a `run_id`. The frontend connects to `ws://.../ws/run/{run_id}/stream` immediately after upload.

### 5. Frontend Displays Progress

The pipeline viewer component receives WebSocket events and updates:
- Step status icons (pending, running, complete, error)
- Progress bars for long-running stages
- Real-time log output per step
- Running cost and token counts
- Output file download links upon completion

---

## Backend Architecture

### Route Organization

| Router | Prefix | Key Endpoints |
|--------|--------|---------------|
| `auth_routes` | `/api` | `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me` |
| `upload` | `/api` | `POST /api/upload` |
| `runs` | `/api` | `GET /api/runs`, `GET /api/run/{id}/status` |
| `steps` | `/api` | `GET /api/run/{id}/step/{n}`, `GET /api/run/{id}/data/{file}` |
| `feedback_routes` | `/api` | `POST /api/feedback`, `GET /api/feedback/{run_id}` |
| `admin_routes` | `/api` | `GET /api/admin/users`, `PUT /api/admin/users/{id}` |
| `saml_routes` | `/api` | `GET /api/saml/login`, `POST /api/saml/acs`, `GET /api/saml/metadata` |
| `websocket` | `/ws` | `WS /ws/run/{run_id}/stream` |

### Service Layer

Route handlers are thin, delegating to service modules in `web_interface/backend/app/services/`:

- **`config_service.py`** -- Centralized `CVICHE_*` environment variable config with defaults
- **`run_service.py`** -- Run access control (owner or admin check)
- **`admin_service.py`** -- O(1) aggregation queries for the admin dashboard
- **`user_service.py`** -- User create-or-update with role resolution

### Middleware Stack

Applied in order (outermost first):
1. **CORS** -- `CVICHE_ALLOWED_ORIGINS` env var, credentials enabled
2. **Security Headers** -- CSP, X-Frame-Options, HSTS, X-Content-Type-Options, Referrer-Policy
3. **CSRF** -- Origin header validation on state-changing methods (SAML ACS exempt)

### Database

- **Dev and production:** MariaDB via `DB_HOST`/`DB_PORT`/`DB_NAME`/`DB_USER` (RDS IAM auth by default; local docker-compose uses `DB_AUTH_MODE=password` against the compose `db` service — see `app/database_factory.py:create_cviche_engine`). There is no SQLite path: `app/database.py` raises if any of the four is missing.
- **ORM:** SQLAlchemy with Alembic migrations
- **Models:** User, Run, Step, Log, LLMUsage, Consent, Feedback, SystemConfig, RunMetrics

### Storage Abstraction

- `RunStorage` ABC in `web_interface/backend/app/storage/base.py`
- `LocalRunStorage` for development (filesystem)
- `S3RunStorage` for production (AWS S3 via boto3)
- Factory in `storage/factory.py` driven by `CVICHE_STORAGE_BACKEND` env var

---

## Frontend Architecture

### Tech Stack

React 18 + TypeScript + Vite + Tailwind CSS + Lucide React icons.

### API Client

Eight typed modules in `web_interface/frontend/src/api/`:
- `client.ts` (shared fetch wrapper), `auth.ts`, `upload.ts`, `runs.ts`, `admin.ts`, `consent.ts`, `feedback.ts`, `websocket.ts`

All functions are fully typed with shared TypeScript interfaces from `src/types/`.

### Key Components

- **UploadPage** -- CV upload (drag-and-drop), pipeline viewer (real-time progress), run history, output downloads
- **AdminDashboard** -- User management, system config, usage stats
- **HelpPage** -- FAQ, getting started, support info
- **LoginPage** -- Email login (simple mode) or SSO redirect (SAML mode)
- **AuthContext** (`src/contexts/AuthContext.tsx`) -- Session state, login/logout, consent tracking

### Environment Configuration

- `VITE_API_URL` -- API base URL (defaults to empty string for Vite proxy in dev, explicit URL in production)
- Vite proxy in `vite.config.ts` forwards `/api` and `/ws` to the backend during development

---

## Development Workflow

### Local Development (Without Docker)

```bash
# Terminal 1: Start backend on port 5002
cd web_interface/backend
uvicorn app.main:app --reload --port 5002

# Terminal 2: Start frontend on port 3001
cd web_interface/frontend
npm run dev

# Open http://localhost:3001
```

### Docker Development

```bash
cd web_interface
docker compose up

# Backend: http://localhost:8000
# Frontend: http://localhost:3000
```

### Testing

```bash
# Backend tests (132+ tests)
cd web_interface/backend
pytest tests/

# Test the CLI pipeline directly
python3 run_full_pipeline.py data/sample_cvs/word/2097_Upton_Cv.docx
```

---

## WebSocket Event Protocol

### Event Types (Server to Client)

| Event | Fields | Purpose |
|-------|--------|---------|
| `STEP_START` | `run_id`, `step_number`, `step_name` | Stage is beginning |
| `LOG` | `run_id`, `step_number`, `message`, `level` | Log output from stage |
| `PROGRESS` | `run_id`, `step_number`, `current`, `total` | Numerical progress |
| `COST_UPDATE` | `run_id`, `cost`, `tokens` | Running LLM cost total |
| `STEP_COMPLETE` | `run_id`, `step_number`, `duration`, `cost`, `output_files` | Stage finished |
| `STEP_ERROR` | `run_id`, `step_number`, `error_message`, `error_type` | Stage failed |
| `RUN_COMPLETE` | `run_id`, `total_cost`, `total_tokens`, `duration` | Pipeline finished |

---

## Error Handling

- Pipeline errors are caught by the orchestrator, persisted to `Step.error_message` and `Step.error_type`, and broadcast via `STEP_ERROR` WebSocket event
- HTTP errors use structured JSON responses via `errors.py` factory functions: `{"error": "<code>", "message": "<text>"}`
- Global exception handler catches unhandled exceptions, logs full tracebacks server-side, returns sanitized responses to the client
- Debug mode (`CVICHE_DEBUG=true`) optionally includes tracebacks in error responses

---

## Authentication

Dual-mode authentication switched via `auth.mode` in `web_interface/backend/auth_config.yaml`:

- **Simple mode** (default): Email-based login against an allowed-users list
- **SAML mode**: Full SAML 2.0 SP via pysaml2 with ED group-based authorization

Session cookies (`cviche_session`) are signed with `itsdangerous.URLSafeTimedSerializer`. WebSocket auth uses the same cookie extracted from upgrade request headers.

For detailed auth architecture, see the [Technical Documentation](../TECHNICAL_README.md#authentication--authorization).
