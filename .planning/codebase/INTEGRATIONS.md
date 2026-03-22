# External Integrations

**Analysis Date:** 2026-03-22

## APIs & External Services

**AI / LLM:**
- OpenAI Chat Completions API - Powers all LLM pipeline stages (1a, 2, 3a, 3b, 4, 4.5, 5c, 5d)
  - SDK/Client: `openai` Python package; initialized as `OpenAI()` (reads env automatically)
  - Auth: `OPENAI_API_KEY_WORK` env var (mapped to `OPENAI_API_KEY` inside docker-compose)
  - Default model: `gpt-5.1` (set in `web_interface/backend/app/pipeline/orchestrator.py`)
  - Cost tracking: per-run token counts stored in `llm_usage` and `runs` DB tables
  - Used in: `src/unified_pipeline/segmentation/chunked_chat_hierarchy_extractor.py`, `src/unified_pipeline/stage_3a_header_taxonomy_mapper.py`, `src/unified_pipeline/stage_3b_entry_classifier.py`, `src/unified_pipeline/stage_4_field_extractor.py`, `src/unified_pipeline/stage_4_5_research_summary.py`, `src/unified_pipeline/stage_5c_teaching_formatter.py`, `src/unified_pipeline/stage_5d_citation_formatter.py`

**Biomedical Literature:**
- NCBI PubMed E-utilities API - Stage 5: publication enrichment (adds PMIDs, citation counts, abstracts)
  - Endpoints: `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi`, `efetch.fcgi`, `elink.fcgi`
  - Also: `https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/` for PMC ID conversion
  - SDK/Client: `requests` (direct HTTP)
  - Auth: `NCBI_API_KEY` or `PUBMED_API_KEY` env var (optional; without key: 3 req/s; with key: 10 req/s)
  - Used in: `src/unified_pipeline/stage_5_pubmed_enrichment.py`

**Research Organization Registry:**
- ROR API - Stage 5b: institution enrichment (adds geographic location, ROR IDs to education/position entries)
  - Endpoint: `api.ror.org` (Research Organization Registry, successor to GRID)
  - SDK/Client: `requests` (direct HTTP)
  - Auth: None (public API)
  - Cache: local JSON file at `src/unified_pipeline/config/institution_cache.json` (migrated from `ror_cache.json`)
  - Used in: `src/unified_pipeline/stage_5b_institution_enrichment.py`

## Data Storage

**Databases:**
- SQLite (development default)
  - Connection: `CVICHE_DATABASE_URL` defaults to `sqlite:///./cviche_dev.db`
  - Client: SQLAlchemy with `check_same_thread: False`
  - Location: `web_interface/backend/cviche_dev.db` (gitignored)

- MariaDB 11 (production / Docker Compose full dev stack)
  - Connection: `CVICHE_DATABASE_URL` env var (`mysql+pymysql://...`)
  - Client: SQLAlchemy + PyMySQL driver
  - Schema managed by Alembic; migrations run automatically at container startup via `web_interface/backend/docker-entrypoint.sh`
  - Tables: `users`, `runs`, `steps`, `logs`, `llm_usage`, `run_metrics`, `consent`, `feedback`, `system_config`
  - Models: `web_interface/backend/app/models.py`

**File Storage:**

- Local filesystem (development)
  - Backend: `CVICHE_STORAGE_BACKEND=local`
  - Upload directory: `/app/uploads`
  - Output directory: `/app/outputs/{run_id}/`
  - Pipeline output: `src/unified_pipeline/outputs/stage_*/`
  - Implementation: `web_interface/backend/app/storage/local_storage.py`

- Amazon S3 (production)
  - Backend: `CVICHE_STORAGE_BACKEND=s3`
  - Bucket: `CVICHE_S3_BUCKET` env var
  - Key prefix: `CVICHE_S3_PREFIX` (default `"cviche"`)
  - Key layout: `{prefix}/runs/{run_id}/input/cv.docx`, `{prefix}/runs/{run_id}/steps/{stage_id}/output.json`
  - Auth: boto3 default credential chain (IRSA on EKS — no hardcoded keys)
  - Presigned URLs: 5-minute expiry for download links
  - Implementation: `web_interface/backend/app/storage/s3_storage.py`

**Caching:**
- Institution lookup cache: local JSON file (`src/unified_pipeline/config/institution_cache.json`) — persisted between runs, not Redis

## Authentication & Identity

**Auth Provider:**
- Custom email-allowlist authentication (no OAuth, no external IdP in current config)
  - Mode configured via `web_interface/backend/auth_config.yaml`: `auth.mode: simple`
  - SAML mode exists in config schema (`auth.mode: saml`) but is not the active configuration
  - Allowed users and admin users lists stored in `system_config` DB table (seeded from `auth_config.yaml` at startup)
  - Implementation: `web_interface/backend/app/auth.py`

- Session cookies signed with `itsdangerous.URLSafeTimedSerializer`
  - Cookie name: `cviche_session`
  - TTL: 7 days
  - Secure + HttpOnly + SameSite=lax
  - Secret key: `CVICHE_SESSION_SECRET` env var

- WebSocket authentication: session cookie extracted from upgrade request headers and validated before allowing stream connection (`web_interface/backend/app/api/websocket.py`)

## Monitoring & Observability

**Error Tracking:**
- None detected (no Sentry, Datadog, or similar)

**Logs:**
- Pipeline stdout captured in real-time via `StreamingStdoutCapture` and stored in `logs` DB table per run/step (`web_interface/backend/app/pipeline/orchestrator.py`)
- LLM prompt logs written to disk: `web_interface/backend/prompt_logs/` (timestamped, human-readable `.txt` files)
- Standard Python `logging` module used in backend; level configurable via `config.yaml` (`logging.level`)
- Uvicorn access logs at `info` level

## CI/CD & Deployment

**Hosting:**
- Production: AWS EKS (Kubernetes) — referenced in `s3_storage.py` (IRSA) and pipeline code comments
- Local dev: Docker Compose (`web_interface/docker-compose.yml`)

**CI Pipeline:**
- None detected (no GitHub Actions, CircleCI, or similar config files present)

## WebSocket

**Real-time Pipeline Events:**
- Endpoint: `ws://{host}/ws/run/{run_id}/stream`
- Direction: server → client (server-sent events only)
- Auth: session cookie validated on WebSocket upgrade
- Event types: `RUN_START`, `STEP_START`, `STEP_COMPLETE`, `STEP_ERROR`, `RUN_COMPLETE`, `RUN_CANCELLED`, `LOG`, `PROGRESS`, `COST_UPDATE`
- Implementation: `web_interface/backend/app/api/websocket.py`, `web_interface/backend/app/pipeline/event_emitter.py`

## Webhooks & Callbacks

**Incoming:**
- None

**Outgoing:**
- None

## Environment Configuration

**Required env vars for production:**
- `CVICHE_DATABASE_URL` - MariaDB connection string
- `CVICHE_SESSION_SECRET` - Cookie signing secret (must be stable across restarts)
- `CVICHE_ALLOWED_ORIGINS` - Comma-separated allowed origins for CORS/CSRF
- `CVICHE_STORAGE_BACKEND` - `"s3"` for production
- `CVICHE_S3_BUCKET` - S3 bucket name
- `OPENAI_API_KEY_WORK` - OpenAI API key

**Optional env vars:**
- `CVICHE_S3_PREFIX` - S3 key prefix (default `"cviche"`)
- `CVICHE_SECURE_COOKIES` - Force `"false"` only in local HTTP dev
- `NCBI_API_KEY` / `PUBMED_API_KEY` - Increases PubMed rate limit from 3/s to 10/s

**Secrets location:**
- All secrets passed via environment variables (Docker Compose env or EKS Secrets)
- Never hardcoded in source

---

*Integration audit: 2026-03-22*
