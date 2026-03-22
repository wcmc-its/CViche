# Technology Stack

**Analysis Date:** 2026-03-22

## Languages

**Primary:**
- Python 3.11 (backend, pipeline) - All pipeline stages, FastAPI backend, CV parsing logic
- TypeScript 5.3 - React frontend (`web_interface/frontend/`)

**Secondary:**
- JavaScript - Vite/PostCSS config files

## Runtime

**Environment:**
- Python 3.11 (pinned in Dockerfile: `python:3.11-slim`)
- Node.js 22 (host dev; not pinned in project)

**Package Manager:**
- Python: pip (no lockfile — `requirements.txt` only, no pinned versions)
- Node: npm 10 (`web_interface/frontend/package-lock.json` present)

## Frameworks

**Core (Backend):**
- FastAPI (latest) - REST API and WebSocket server (`web_interface/backend/app/main.py`)
- Uvicorn - ASGI server, 4 workers in production
- SQLAlchemy (latest) - ORM, declarative base (`web_interface/backend/app/database.py`)
- Alembic (latest) - Database migrations (`web_interface/backend/alembic/`)
- Pydantic (latest) - Request/response schemas (`web_interface/backend/app/schemas.py`)

**Core (Frontend):**
- React 18.2 - UI framework (`web_interface/frontend/src/`)
- React Router DOM 6.20 - Client-side routing (`web_interface/frontend/src/App.tsx`)
- Vite 5.0 - Build tool and dev server (`web_interface/frontend/vite.config.ts`)
- Tailwind CSS 3.3 - Utility-first CSS (`web_interface/frontend/tailwind.config.js`)
- Lucide React 0.577 - Icon library

**Build/Dev:**
- Vite 5.0 with `@vitejs/plugin-react` - Frontend build
- PostCSS 8 + Autoprefixer - CSS processing
- Docker + Docker Compose - Container orchestration (`web_interface/docker-compose.yml`)
- nginx - Production static asset server (`web_interface/frontend/nginx.conf`)

## Key Dependencies

**Critical (Backend):**
- `openai` (latest) - OpenAI API client; used across all LLM pipeline stages via `OpenAI()` (reads `OPENAI_API_KEY` or `OPENAI_API_KEY_WORK` from env)
- `pdfplumber` - PDF text extraction with layout analysis
- `pdf2image` - PDF-to-image conversion (requires `poppler-utils` system dep)
- `python-docx` - `.docx` file parsing
- `tiktoken` - Token counting for LLM prompt management
- `itsdangerous` - Signed session cookies (`URLSafeTimedSerializer`)
- `boto3` - AWS SDK for S3 storage backend (production only)
- `pymysql` - MariaDB/MySQL driver for production
- `python-multipart` - File upload support
- `websockets` - WebSocket support

**Infrastructure:**
- `sqlalchemy` - ORM (supports SQLite dev / MariaDB prod)
- `alembic` - Schema migrations run at container startup via `docker-entrypoint.sh`
- `pyyaml` - Config parsing (`config.yaml`, `auth_config.yaml`)
- `requests` - HTTP client used in PubMed (NCBI E-utilities) and ROR API calls

## Configuration

**Environment Variables:**
- `CVICHE_DATABASE_URL` - Full SQLAlchemy URL (defaults to `sqlite:///./cviche_dev.db`)
- `CVICHE_SESSION_SECRET` - Cookie signing key (random fallback if unset — sessions lost on restart)
- `CVICHE_SECURE_COOKIES` - `"true"/"false"` (default true; set false in local dev)
- `CVICHE_ALLOWED_ORIGINS` - Comma-separated CORS + CSRF allowed origins
- `CVICHE_STORAGE_BACKEND` - `"local"` (default) or `"s3"`
- `CVICHE_S3_BUCKET` - S3 bucket name (production only)
- `CVICHE_S3_PREFIX` - S3 key prefix (default `"cviche"`)
- `OPENAI_API_KEY_WORK` - OpenAI API key (passed as `OPENAI_API_KEY` inside pipeline modules)
- `NCBI_API_KEY` / `PUBMED_API_KEY` - Optional NCBI API key (3 req/s without, 10 req/s with)

**Config Files:**
- `config.yaml` - Pipeline extraction parameters, taxonomy settings, PDF processing knobs
- `web_interface/backend/auth_config.yaml` - Auth mode (`simple` or `saml`), allowed users, rate limits, consent version
- `web_interface/frontend/vite.config.ts` - Dev server port 3001, API proxy to `:8000`
- `web_interface/frontend/tailwind.config.js` - Custom color palette (primary, surface, success, error, warning)

**Build:**
- `web_interface/backend/Dockerfile` - Python 3.11-slim, installs `poppler-utils` + `libmagic1`
- `web_interface/frontend/Dockerfile` - Multi-stage (build target for dev, production target serves via nginx)
- `web_interface/docker-compose.yml` - Local dev (MariaDB 11, backend :8000, frontend :3000)
- `web_interface/docker-compose.prod.yml` - Production overrides (S3 storage, 4 uvicorn workers, nginx on :80, external managed DB)

## Platform Requirements

**Development:**
- Docker and Docker Compose for full-stack local dev
- `poppler-utils` system package required for `pdf2image`
- OpenAI API key required for LLM-dependent pipeline stages

**Production:**
- Docker containers on EKS (Kubernetes)
- Managed MariaDB instance (not the compose `db` service)
- AWS S3 bucket for run artifact storage
- IAM/IRSA for boto3 authentication (no hardcoded AWS keys)

---

*Stack analysis: 2026-03-22*
