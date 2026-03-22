# Codebase Structure

**Analysis Date:** 2026-03-22

## Directory Layout

```
CViche/
├── run_full_pipeline.py        # CLI entry point — runs all 12 stages
├── config.yaml                 # Pipeline configuration (taxonomy, PDF, logging)
├── taxonomy_reference.md       # Human-readable WCM taxonomy reference
├── src/
│   └── unified_pipeline/       # Core CV processing pipeline
│       ├── stage_1b_hierarchy_mapper.py
│       ├── stage_2_entry_extraction.py
│       ├── stage_3a_header_taxonomy_mapper.py
│       ├── stage_3b_entry_classifier.py
│       ├── stage_4_field_extractor.py
│       ├── stage_4_5_research_summary.py
│       ├── stage_5_pubmed_enrichment.py
│       ├── stage_5b_institution_enrichment.py
│       ├── stage_5c_teaching_formatter.py
│       ├── stage_5d_citation_formatter.py
│       ├── stage_6_word_template.py
│       ├── segmentation/       # Stage 1a — hierarchy extraction strategies
│       ├── parsers/            # Domain-specific field parsers (publications, grants, etc.)
│       ├── core/               # Shared utilities, validators, prompt logger
│       │   └── validators/     # 43 post-classification correction modules
│       ├── config/             # field_schemas_v1.1.json, institution_cache.json
│       ├── cv_parser/          # Legacy parser code, cv_taxonomy_wcm.py
│       ├── outputs/            # Stage output directories (created at runtime)
│       └── prompt_logs/        # LLM prompt/response JSON logs (created at runtime)
├── web_interface/
│   ├── docker-compose.yml      # Development Docker Compose
│   ├── docker-compose.prod.yml # Production Docker Compose
│   ├── backend/
│   │   ├── app/
│   │   │   ├── main.py         # FastAPI app, middleware, router registration
│   │   │   ├── models.py       # SQLAlchemy ORM models
│   │   │   ├── schemas.py      # Pydantic request/response schemas
│   │   │   ├── database.py     # DB engine, session factory, init_db()
│   │   │   ├── auth.py         # Session cookie auth, get_current_user dependency
│   │   │   ├── rate_limiter.py # Per-user daily/monthly run limits
│   │   │   ├── consent.py      # Consent text loading and integrity check
│   │   │   ├── config_loader.py# SystemConfig key-value access
│   │   │   ├── error_classifier.py # Map exceptions to error_type strings
│   │   │   ├── prompt_hasher.py    # SHA-256 prompt template versioning
│   │   │   ├── api/
│   │   │   │   ├── upload.py       # POST /api/upload
│   │   │   │   ├── runs.py         # GET/POST /api/runs, /api/runs/{id}
│   │   │   │   ├── steps.py        # GET /api/runs/{id}/steps
│   │   │   │   ├── websocket.py    # WS /ws/run/{id}/stream
│   │   │   │   ├── auth_routes.py  # POST /api/auth/login, logout, GET /api/auth/me
│   │   │   │   ├── consent_routes.py # GET/POST /api/consent
│   │   │   │   ├── feedback_routes.py # POST /api/feedback
│   │   │   │   └── admin_routes.py    # /api/admin/* (users, config, submissions)
│   │   │   ├── pipeline/
│   │   │   │   ├── orchestrator.py     # Runs 12 stages, captures stdout, emits events
│   │   │   │   ├── step_registry.py    # STEP_REGISTRY dataclass list
│   │   │   │   └── event_emitter.py    # WebSocket EventEmitter singleton
│   │   │   └── storage/
│   │   │       ├── base.py         # RunStorage ABC
│   │   │       ├── factory.py      # Singleton get_storage() factory
│   │   │       ├── local_storage.py # LocalRunStorage (dev)
│   │   │       └── s3_storage.py    # S3RunStorage (prod/EKS)
│   │   ├── alembic/            # Database migrations
│   │   │   └── versions/       # 3 migration scripts
│   │   ├── uploads/            # Uploaded CV files (local dev)
│   │   └── outputs/            # Pipeline output files (local dev)
│   └── frontend/
│       ├── src/
│       │   ├── main.tsx            # Vite entry point, mounts App
│       │   ├── App.tsx             # Router, auth guards (RequireAuth, RequireConsent, RequireAdmin)
│       │   ├── contexts/
│       │   │   └── AuthContext.tsx # User + consent state, login/logout
│       │   └── components/
│       │       ├── UploadPage.tsx      # CV file upload + history
│       │       ├── PipelineViewer.tsx  # Live stage progress, logs, outputs (largest component)
│       │       ├── LoginPage.tsx
│       │       ├── ConsentPage.tsx
│       │       ├── RunHistory.tsx
│       │       ├── StepSidebar.tsx
│       │       ├── OutputFiles.tsx
│       │       ├── PipelineHeader.tsx
│       │       ├── LogViewer.tsx
│       │       ├── PromptLogViewer.tsx
│       │       ├── JsonViewerModal.tsx
│       │       ├── ErrorBanner.tsx
│       │       ├── AdminDashboard.tsx
│       │       ├── AdminSubmissions.tsx
│       │       ├── AdminUsers.tsx
│       │       ├── AdminConfig.tsx
│       │       └── AdminFeedbackInsights.tsx
│       ├── package.json
│       ├── vite.config.ts
│       └── tailwind.config.js
├── data/                       # Input data (sample CVs, templates)
├── key_files/                  # Reference docs, sample CVs, taxonomy JSON
├── docs/                       # Design docs, research papers, guides
├── archive/                    # Old code archived (not active)
└── .planning/                  # GSD planning documents
    └── codebase/
```

## Directory Purposes

**`src/unified_pipeline/`:**
- Purpose: All 12 processing stage modules; the core of the system
- Contains: One Python file per stage (`stage_Xn_*.py`), sub-packages for segmentation, parsers, validators, config
- Key files: `stage_3b_entry_classifier.py` (104KB, most complex), `stage_6_word_template.py` (366KB, Word rendering), `stage_4_field_extractor.py` (99KB)

**`src/unified_pipeline/segmentation/`:**
- Purpose: Stage 1a — multiple strategies for extracting CV hierarchy from Word/PDF
- Key file: `chunked_chat_hierarchy_extractor.py` (active strategy), `signature_based_segmentation.py` (75KB, alternative rule-based approach)

**`src/unified_pipeline/parsers/`:**
- Purpose: Domain-specific parsers for individual section types
- Contains: `publications_parser.py`, `grants_parser.py`, `positions_parser.py`, `education_parser.py`, `certifications_parser.py`, `honors_parser.py`, `licensure_parser.py`, `memberships_parser.py`, `mentoring_parser.py`, `service_parser.py`

**`src/unified_pipeline/core/validators/`:**
- Purpose: Post-Stage-3b auto-correction rules to fix common LLM classification errors
- Contains: 43 validator modules, each targeting a specific taxonomy edge case
- Pattern: Each exports an `apply_*_corrections(entries)` function

**`src/unified_pipeline/config/`:**
- Purpose: Data files consumed by pipeline stages
- Key files: `field_schemas_v1.1.json` (field extraction schema for Stage 4), `institution_cache.json` (ROR lookup cache)

**`src/unified_pipeline/cv_parser/`:**
- Purpose: Legacy parser code predating the unified pipeline; `cv_taxonomy_wcm.py` is the authoritative WCM taxonomy definition
- Key file: `cv_taxonomy_wcm.py` (107KB — defines all taxonomy codes, descriptions, rules)

**`src/unified_pipeline/core/`:**
- Purpose: Shared utilities used across stages; also contains a large collection of batch processing and evaluation scripts
- Key file: `prompt_logger.py` (logs all LLM prompts/responses), `async_rate_limiter.py`

**`web_interface/backend/app/api/`:**
- Purpose: All FastAPI route modules, one file per domain
- Largest: `runs.py` (47KB), `admin_routes.py` (21KB)

**`web_interface/backend/app/pipeline/`:**
- Purpose: Bridge between web layer and core pipeline; orchestration, event streaming, step registry
- Key file: `orchestrator.py` (33KB) — the central coordinator for web-triggered runs

**`web_interface/backend/app/storage/`:**
- Purpose: File I/O abstraction for uploads and pipeline artifacts
- Pattern: ABC `RunStorage` → `LocalRunStorage` | `S3RunStorage` selected by `CVICHE_STORAGE_BACKEND` env var

**`web_interface/backend/alembic/`:**
- Purpose: Database schema migrations
- Contains: 3 migration scripts covering initial schema, research data tables, feedback table

**`web_interface/frontend/src/components/`:**
- Purpose: All React UI components
- Largest: `PipelineViewer.tsx` (30KB) — real-time progress display with WebSocket

**`web_interface/outputs/`:**
- Purpose: Pipeline output files stored by run ID (local dev)
- Not committed; created at runtime

**`web_interface/uploads/`:**
- Purpose: Uploaded CV files indexed by run ID (local dev)
- Not committed; created at runtime

**`src/unified_pipeline/prompt_logs/`:**
- Purpose: JSON logs of every LLM prompt and response, for debugging and evaluation
- Not committed; 17,000+ files generated during development runs

**`data/`:**
- Purpose: Input CV files for processing
- Sub-dirs: `sample_cvs/`, `templates/`, `test_cvs/`

**`key_files/`:**
- Purpose: Reference artifacts — sample CVs, WCM Word template (`cv_template_wcm.docx`), taxonomy JSON files

**`docs/`:**
- Purpose: Design documentation, research papers, evaluation guides, stage spec documents
- Key file: `STAGE3_TAXONOMY_ARCHITECTURE.md` (47KB)

**`archive/`:**
- Purpose: Superseded code from before the unified pipeline (Oct 2025)
- Not active; kept for reference

## Key File Locations

**Entry Points:**
- `run_full_pipeline.py`: CLI pipeline runner (project root)
- `web_interface/backend/app/main.py`: FastAPI application factory
- `web_interface/frontend/src/main.tsx`: React SPA entry

**Configuration:**
- `config.yaml`: Taxonomy fuzzy match settings, PDF processing, logging levels
- `web_interface/backend/auth_config.yaml`: Auth settings
- `web_interface/backend/consent_text.md`: Versioned consent text
- `src/unified_pipeline/config/field_schemas_v1.1.json`: Stage 4 field extraction schema
- `src/unified_pipeline/cv_parser/cv_taxonomy_wcm.py`: WCM taxonomy definition

**Core Logic:**
- `src/unified_pipeline/stage_3b_entry_classifier.py`: Entry classification with validators
- `src/unified_pipeline/stage_4_field_extractor.py`: Structured field extraction
- `src/unified_pipeline/stage_6_word_template.py`: Word document generation
- `web_interface/backend/app/pipeline/orchestrator.py`: Web pipeline coordinator

**Database:**
- `web_interface/backend/app/models.py`: SQLAlchemy ORM models
- `web_interface/backend/app/database.py`: Engine and session factory
- `web_interface/backend/alembic/versions/`: Migration scripts

**Testing:**
- `src/unified_pipeline/test_async_pipeline.py`: Pipeline integration tests
- `src/unified_pipeline/test_pipeline_stages.py`: Stage unit tests
- `src/unified_pipeline/core/test_*.py`: Core utility tests

## Naming Conventions

**Files:**
- Stage modules: `stage_{number}[letter]_{description}.py` (e.g., `stage_3b_entry_classifier.py`)
- API routes: `{domain}_routes.py` or `{domain}.py` (e.g., `auth_routes.py`, `runs.py`)
- Validators: `{domain}_corrector.py` or `{domain}_validator.py`
- React components: `PascalCase.tsx` (e.g., `PipelineViewer.tsx`)

**Directories:**
- Snake case for Python packages
- Stage output directories: `stage_{number}[letter]_{description}/`

**Run IDs:**
- 6-character uppercase alphanumeric (e.g., `A1B2C3`), generated via `secrets.token_urlsafe`

**CV artifact file names:**
- `{uid}_segmented.json`, `{uid}_entries.json`, `{uid}_classified.json`, `{uid}_wcm.docx`

## Where to Add New Code

**New pipeline stage:**
- Implementation: `src/unified_pipeline/stage_{N}_{description}.py`
- Register it: Add `StepDefinition` to `STEP_REGISTRY` in `web_interface/backend/app/pipeline/step_registry.py`
- Wire it in CLI: `run_full_pipeline.py` stage dispatch block
- Wire it in orchestrator: `web_interface/backend/app/pipeline/orchestrator.py`
- Add output directory: `src/unified_pipeline/outputs/stage_{N}_{description}/`

**New post-classification validator:**
- Implementation: `src/unified_pipeline/core/validators/{domain}_corrector.py`
- Export an `apply_{domain}_corrections(entries)` function
- Import and call it in `src/unified_pipeline/stage_3b_entry_classifier.py`

**New API endpoint:**
- Add route to the appropriate file in `web_interface/backend/app/api/`
- Register router in `web_interface/backend/app/main.py` if new file
- Add Pydantic schemas to `web_interface/backend/app/schemas.py`

**New React page or major component:**
- Implementation: `web_interface/frontend/src/components/{ComponentName}.tsx`
- Add route in `web_interface/frontend/src/App.tsx` with appropriate auth guards

**New domain-specific field parser:**
- Implementation: `src/unified_pipeline/parsers/{domain}_parser.py`
- Import and invoke from `src/unified_pipeline/stage_4_field_extractor.py`

**Shared utilities:**
- Python: `src/unified_pipeline/core/{utility}.py`
- Frontend: `web_interface/frontend/src/hooks/` or `web_interface/frontend/src/store/`

**Database schema changes:**
- Add/modify models in `web_interface/backend/app/models.py`
- Generate migration: `alembic revision --autogenerate -m "description"` from `web_interface/backend/`
- Migration file lands in `web_interface/backend/alembic/versions/`

## Special Directories

**`src/unified_pipeline/prompt_logs/`:**
- Purpose: LLM prompt/response logs for debugging and evaluation
- Generated: Yes (at runtime by `core/prompt_logger.py`)
- Committed: No (17,000+ files)

**`web_interface/uploads/`:**
- Purpose: Uploaded CV files keyed by run ID
- Generated: Yes
- Committed: No

**`web_interface/outputs/`:**
- Purpose: Pipeline stage outputs keyed by run ID (local dev; S3 in prod)
- Generated: Yes
- Committed: No

**`src/unified_pipeline/outputs/`:**
- Purpose: Stage output files written by CLI pipeline runs
- Generated: Yes
- Committed: No

**`archive/`:**
- Purpose: Archived code from before unified pipeline (Oct 2025)
- Generated: No
- Committed: Yes (historical reference)

**`.planning/codebase/`:**
- Purpose: GSD codebase analysis documents
- Generated: Yes (by GSD map-codebase)
- Committed: Yes

---

*Structure analysis: 2026-03-22*
