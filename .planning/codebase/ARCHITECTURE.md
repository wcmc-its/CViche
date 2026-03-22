# Architecture

**Analysis Date:** 2026-03-22

## Pattern Overview

**Overall:** Multi-stage LLM pipeline with a web-based orchestration layer

**Key Characteristics:**
- Sequential stage pipeline: each stage consumes the JSON output of the prior stage
- Two execution modes: CLI (`run_full_pipeline.py`) and web UI (FastAPI + React)
- The web backend imports and wraps the same stage functions used by the CLI
- WebSocket-driven real-time progress streaming from pipeline to browser
- Storage is abstracted (local filesystem vs. S3) via a strategy pattern

## Layers

**CV Parsing Pipeline (Core Processing):**
- Purpose: Transform raw CV documents (DOCX/PDF) into structured, enriched JSON and formatted Word output
- Location: `src/unified_pipeline/`
- Contains: Stage modules (stage_1a through stage_6), segmentation strategies, field-specific parsers, validators, taxonomy definitions
- Depends on: OpenAI API, PubMed API, ROR API, python-docx, config YAML
- Used by: CLI entry point, web orchestrator

**Web Backend (API + Orchestration):**
- Purpose: Accept uploads, manage pipeline runs, stream progress, persist results, enforce auth/rate-limits
- Location: `web_interface/backend/app/`
- Contains: FastAPI routes, SQLAlchemy models, Pydantic schemas, pipeline orchestrator, storage abstraction, auth middleware
- Depends on: Core pipeline layer, SQLite (dev) / MariaDB (prod), SQLAlchemy/Alembic
- Used by: React frontend

**Web Frontend (React SPA):**
- Purpose: CV upload UI, real-time pipeline viewer, run history, admin dashboard
- Location: `web_interface/frontend/src/`
- Contains: React components, AuthContext, routing
- Depends on: Web backend REST API + WebSocket endpoint
- Used by: End users

**Taxonomy & Configuration:**
- Purpose: Define the WCM CV taxonomy (section codes like S1, B1, M2A) and field extraction schemas
- Location: `src/unified_pipeline/cv_parser/cv_taxonomy_wcm.py`, `src/unified_pipeline/config/field_schemas_v1.1.json`
- Used by: Stage 3a, 3b, 4

## Data Flow

**Primary Pipeline Flow (12 stages):**

1. User uploads DOCX/PDF via `POST /api/upload` → run ID (e.g., `A1B2C3`) created in DB
2. `PipelineOrchestrator` launches pipeline in a background thread
3. **Stage 1a** (`segmentation/chunked_chat_hierarchy_extractor.py`): LLM extracts hierarchical section structure → `{uid}_segmented.json`
4. **Stage 1b** (`stage_1b_hierarchy_mapper.py`): Maps extracted headers to Word paragraph indices (no LLM) → `{uid}_hierarchy_mapped.json`
5. **Stage 2** (`stage_2_entry_extraction.py`): Detects individual entry boundaries within each section → `{uid}_entries.json`
6. **Stage 3a** (`stage_3a_header_taxonomy_mapper.py`): LLM maps CV section headers to WCM taxonomy codes → `{uid}_header_taxonomy.json`
7. **Stage 3b** (`stage_3b_entry_classifier.py`): LLM classifies each entry to a taxonomy code using header context + validators → `{uid}_classified.json`
8. **Stage 4** (`stage_4_field_extractor.py`): LLM extracts structured fields per entry type → `{uid}_fields.json`
9. **Stage 4.5** (`stage_4_5_research_summary.py`): LLM generates biosketch-style research summary → `{uid}_research_summary.json`
10. **Stage 5** (`stage_5_pubmed_enrichment.py`): Enriches publications with PubMed metadata → `{uid}_enriched.json`
11. **Stage 5b/5c/5d**: Institution enrichment (ROR API), teaching formatter, citation formatter → separate enriched JSON files
12. **Stage 6** (`stage_6_word_template.py`): Renders WCM Word template from structured data → `{uid}_wcm.docx`

**Real-time Progress:**
- Each stage emits `STEP_START`, `LOG`, `PROGRESS`, `COST_UPDATE`, `STEP_COMPLETE`, `STEP_ERROR`, `RUN_COMPLETE` events
- `event_emitter` (singleton `EventEmitter`) broadcasts to all WebSocket connections registered for a `run_id`
- Frontend connects to `ws://.../ws/run/{run_id}/stream` immediately after upload and receives live updates

**State Management (Frontend):**
- `AuthContext` (`src/contexts/AuthContext.tsx`): user identity, consent status, login/logout
- No Redux/Zustand — component-local state via `useState` + React Router for page navigation

## Key Abstractions

**StepDefinition / STEP_REGISTRY:**
- Purpose: Canonical list of all 12 pipeline steps with metadata (name, LLM usage, weight, estimated seconds)
- Location: `web_interface/backend/app/pipeline/step_registry.py`
- Used by: Orchestrator, upload initialization, frontend display

**RunStorage (Strategy Pattern):**
- Purpose: Abstract file I/O for uploads, pipeline outputs, prompt logs
- Interface: `web_interface/backend/app/storage/base.py` (`RunStorage` ABC)
- Implementations: `local_storage.py` (dev), `s3_storage.py` (prod)
- Factory: `web_interface/backend/app/storage/factory.py` (singleton, env-driven)

**PipelineOrchestrator:**
- Purpose: Wraps all 12 stage functions, captures stdout/stderr, emits WebSocket events, persists step state to DB, handles cancellation
- Location: `web_interface/backend/app/pipeline/orchestrator.py`
- Key mechanism: Regex patterns on stdout intercept progress like "Processing 5 of 10 sections" and convert to `PROGRESS` events

**Post-Classification Validators:**
- Purpose: Auto-correct common LLM classification errors after Stage 3b
- Location: `src/unified_pipeline/core/validators/` (43 validator modules)
- Pattern: Each validator takes classified entries and applies domain-specific rules (e.g., `grant_status_corrector.py`, `committee_position_corrector.py`)

**SQLAlchemy Models:**
- `Run`: Top-level pipeline run with status and cost tracking
- `Step`: Individual stage execution record (status, duration, cost, output files)
- `Log`: Captured stdout/stderr per step
- `LLMUsage`: Per-call token and cost tracking with prompt version hash
- `User`, `Consent`, `Feedback`, `SystemConfig`, `RunMetrics`
- Location: `web_interface/backend/app/models.py`

## Entry Points

**CLI Pipeline Runner:**
- Location: `run_full_pipeline.py` (project root)
- Triggers: `python3 run_full_pipeline.py <cv_path> [--stage STAGE] [--model MODEL]`
- Responsibilities: Argument parsing, stage dispatch, file I/O, timing output

**FastAPI Web Backend:**
- Location: `web_interface/backend/app/main.py`
- Triggers: `uvicorn app.main:app` from `web_interface/backend/`
- Responsibilities: Route registration, DB init, CORS + CSRF middleware, lifespan events (seed config, load consent)

**React Frontend:**
- Location: `web_interface/frontend/src/main.tsx`
- Entry component: `web_interface/frontend/src/App.tsx`
- Build: Vite; served via nginx in production Docker container

**Docker:**
- `web_interface/docker-compose.yml` — development
- `web_interface/docker-compose.prod.yml` — production
- Backend Dockerfile: `web_interface/backend/Dockerfile`
- Frontend Dockerfile: `web_interface/frontend/Dockerfile` (nginx static)

## Error Handling

**Strategy:** Stage errors are caught by the orchestrator, persisted to `Step.error_message` and `Step.error_type`, and broadcast via `STEP_ERROR` WebSocket event. The run status is set to `"failed"`.

**Error Types:**
- `llm_timeout`, `token_limit`, `parse_error`, `invalid_response`, `api_error`, `file_error`, `unknown` — stored in `Step.error_type`
- Classification logic in `web_interface/backend/app/error_classifier.py`

**HTTP Error Pattern:**
- FastAPI routes raise `HTTPException` with structured `detail` dicts: `{"error": "error_code", "message": "human message"}`
- Auth errors: 401 with `auth_required` or `account_disabled` codes
- Access errors: 403 with `forbidden` code

## Cross-Cutting Concerns

**Logging:** Stage functions print to stdout; the orchestrator captures via `redirect_stdout`/`redirect_stderr` and writes to `Log` table and emits `LOG` WebSocket events. Prompt inputs/outputs are separately written to `src/unified_pipeline/prompt_logs/` JSON files.

**Validation:** Post-classification validators in `src/unified_pipeline/core/validators/` (43 modules); request/response validation via Pydantic schemas in `web_interface/backend/app/schemas.py`.

**Authentication:** Session cookie (`cviche_session`) signed with `itsdangerous.URLSafeTimedSerializer`; 7-day TTL; `get_current_user` FastAPI dependency performs per-request DB lookup. WebSocket auth uses same cookie extracted from upgrade headers.

**Rate Limiting:** Per-user daily and monthly run limits stored in `User` model and `SystemConfig`; enforced at upload time in `web_interface/backend/app/rate_limiter.py`.

**Consent:** Versioned consent text in `web_interface/backend/consent_text.md`; hash tracked in `Consent` table and checked at upload; `RequireConsent` route guard in React.

---

*Architecture analysis: 2026-03-22*
