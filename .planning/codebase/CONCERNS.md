# Codebase Concerns

**Analysis Date:** 2026-03-22

---

## CRITICAL: Files/Directories That Must Be Excluded From Public Repository

**This repo has NO `.gitignore` file. As a result, 60,374 files are currently tracked by git, including personal CV documents, SQLite databases with user data, LLM prompt logs, node_modules, and compiled build artifacts. This must be addressed before any public push.**

### Personal CV Documents (PII — Real Faculty CVs)

**`data/sample_cvs/`** — 261 PDFs and 227 Word documents (134 MB total):
- `data/sample_cvs/pdf/` — 261 real faculty CV PDFs with full names visible in filenames (e.g., `1.-CV_Lulu-JIANG_2023-09-27.pdf`, `Aaron-Bowman-CV.pdf`, `Baumgartner_CV_1July2025_website.pdf`)
- `data/sample_cvs/word/` — 227 Word CVs, many with full names embedded (e.g., `2004_Almasri_Mahmoud.docx`, `2002_Holtz.docx`)

**`data/test_cvs/`** — 75 focused Word CV excerpts:
- `data/test_cvs/focused/` — Named section excerpts (e.g., `A_personal_data.docx`, `B1_academic_degrees.docx`)

**`web_interface/uploads/`** — 150 real user-uploaded CV files (7.7 MB):
- Files use a short random prefix (e.g., `038WKA_Jonathan Nahmias CV (Updated 1-26-26).docx`) — real names visible in filenames.

**`key_files/`** — Contains real CV samples used as reference:
- `key_files/Original_2100_Mocco.docx` — real CV with identifiable name
- `key_files/Output_2100_Mocco_wcm.docx` — processed output with same name

**`archive/backups_2025-10-29/backup_20251026_192630/`** — JSON extraction outputs with real names embedded in filenames:
- e.g., `section_B1_CV_2068_Yount_Kathryn_PhD_extracted.json`

**`archive/backups_2025-10-29/stage_1_segmentation_ARCHIVED_20251023/gold_standard/outputs/populated_cvs/`** — 30+ populated WCM-format Word CVs with real faculty names:
- e.g., `CV_2002_Holtz_Heidi_PhD_WCM.docx`, `CV_2022_Afifi_Rima_PhD_WCM.docx`

### Operational/Runtime Data

**`web_interface/backend/cviche.db`** (5 MB) and **`web_interface/backend/cviche_dev.db`** (94 KB):
- SQLite databases containing user records, session data, upload history, and run logs. Currently tracked by git.
- `web_interface/backend/pipeline.db` (0 bytes) also tracked.

**`web_interface/backend/auth_config.yaml`**:
- Contains an allowlist of real email addresses: `paa2013@med.cornell.edu`. This is a user identifier.
- Currently tracked by git.

### LLM Prompt Logs (Contain CV Content)

**`src/unified_pipeline/core/prompt_logs/`** — 40,549 files tracked by git:
- JSON and readable text files capturing full LLM prompt/response pairs.
- These include CV entry text sent to OpenAI and classified responses.
- Filenames contain timestamps and hash IDs but files contain CV content.

**`web_interface/backend/prompt_logs/`** — 1,464 files tracked by git:
- Same pattern — taxonomy mapping prompts with CV section content.

**`web_interface/outputs/`** — 1,930 tracked files:
- Pipeline output JSON/DOCX files from processed CVs, organized by run ID.

### Build Artifacts and Dependencies

**`web_interface/frontend/node_modules/`** — 8,044 files tracked by git (140 MB):
- The entire npm dependency tree is committed. Standard practice is to gitignore this.

**`web_interface/frontend/dist/`** — 4,539 files tracked by git (408 KB):
- Compiled frontend build artifacts. Should be built from source, not committed.

### Development/Operational Artifacts

**`archive/`** — 6,208 tracked files (448 MB total), includes:
- `archive/backups_2025-10-29/cv_pipeline_backup_20251026.tar.gz` — binary backup archive committed to git
- `archive/outputs/` — 193+ output directories with processed CV data
- `archive/cv_section_segmentation/` and other dev-era subdirectories

---

## Tech Debt

**No `.gitignore` file:**
- Issue: Nothing is excluded from git tracking. The repo tracks 60,374 files including node_modules, compiled artifacts, SQLite databases, log files, and real CV documents.
- Files: Root of repo (no `.gitignore` present)
- Impact: Bloated git history (`.git/` is already 284 MB); personal data exposure risk if repo is ever made public or shared; node_modules in version control makes cloning extremely slow.
- Fix approach: Create a comprehensive `.gitignore` and use `git rm --cached` to stop tracking excluded files. Consider `git filter-repo` or BFG Repo Cleaner to purge PII from git history.

**Legacy code intermixed with active pipeline:**
- Issue: `src/legacy/` contains an entire superseded pipeline implementation still in the repo. `src/unified_pipeline/stage_3_taxonomy_mapper_DEPRECATED.py` (1,048 lines) exists alongside the active `stage_3_taxonomy_mapper.py`. `src/unified_pipeline/cv_parser/cv_taxonomy_wcm_backup.py` (2,180 lines) is a backup copy of an active file.
- Files: `src/legacy/`, `src/unified_pipeline/stage_3_taxonomy_mapper_DEPRECATED.py`, `src/unified_pipeline/cv_parser/cv_taxonomy_wcm_backup.py`
- Impact: Confusion about which code is authoritative; risk of editing deprecated files accidentally.
- Fix approach: Move legacy code to `archive/` or remove from repo; delete `_DEPRECATED` and `_backup` files from `src/`.

**Log and documentation files mixed into source directories:**
- Issue: `src/unified_pipeline/core/` contains 17 `.log` files, 26 `.md` files, and 7,238 `.txt` files alongside Python source code. These are operational artifacts (batch run logs, analysis summaries) committed to the source tree.
- Files: `src/unified_pipeline/core/*.log`, `src/unified_pipeline/core/*.md`, `src/unified_pipeline/core/*.txt`
- Impact: Makes the source tree extremely hard to navigate; pollutes search results; inflates repo size.
- Fix approach: Move non-Python content to `docs/` or `archive/`; add `*.log` to `.gitignore`.

**No top-level `requirements.txt` or `pyproject.toml`:**
- Issue: The main pipeline (`run_full_pipeline.py`, `src/unified_pipeline/`) has no dependency manifest. Only `web_interface/backend/requirements.txt` exists.
- Files: Repo root
- Impact: New contributors or deployment environments cannot install dependencies without manual inspection.
- Fix approach: Create `requirements.txt` at repo root or `src/requirements.txt` for the pipeline code.

**Unpinned dependencies in `web_interface/backend/requirements.txt`:**
- Issue: All 17 packages in `web_interface/backend/requirements.txt` are listed without version pins (e.g., `fastapi`, `openai`, `sqlalchemy`).
- Files: `web_interface/backend/requirements.txt`
- Impact: Breaking changes in upstream packages can break the app without any code change.
- Fix approach: Pin all dependencies to exact versions or use a lockfile (`pip-compile` / `poetry.lock`).

**UPLOAD_DIR uses relative path resolution, not environment variable:**
- Issue: `web_interface/backend/app/api/upload.py` computes `UPLOAD_DIR` using `Path(__file__).parent.parent.parent.parent / "uploads"` — a fragile path walk, not an env var.
- Files: `web_interface/backend/app/api/upload.py`, `web_interface/backend/app/api/runs.py` (line 156–157 duplicates this logic)
- Impact: Breaks silently if the module is moved or the app is run from a different working directory.
- Fix approach: Replace with `os.environ.get("CVICHE_UPLOAD_DIR", ...)` with a sensible default.

**Deprecated output_manager methods not removed:**
- Issue: `src/unified_pipeline/core/output_manager.py` retains four deprecated methods (e.g., `get_stage1_json_path`, `get_stage2a_path`) with docstrings saying "DEPRECATED: Use get_stage1a_json_path()". The deprecated methods still exist and may be called by legacy code.
- Files: `src/unified_pipeline/core/output_manager.py` (lines 138, 142, 157, 162)
- Impact: Dead code confusion; risk of regression if callers are not all updated.
- Fix approach: Search for callers, migrate to new methods, delete deprecated stubs.

**Deprecated stage class kept in `cv_pipeline.py`:**
- Issue: `src/unified_pipeline/core/cv_pipeline.py` contains `_OLD_run_stage_4_template_generation_DEPRECATED` (line 1638), described as "KEPT FOR REFERENCE ONLY".
- Files: `src/unified_pipeline/core/cv_pipeline.py`
- Impact: 2,171-line file is harder to navigate with dead code present.
- Fix approach: Delete the method; the reference value is better served by git history.

**Hardcoded absolute paths in legacy and some active scripts:**
- Issue: Multiple scripts hard-code `/Users/paulalbert/Library/CloudStorage/...` paths. These are non-functional on any other machine.
- Files:
  - `src/legacy/stage_based_extraction/scripts/analysis/analyze_section_r.py` (line 9)
  - `src/legacy/stage_based_extraction/scripts/production/populate_cv.py` (line 673)
  - `src/legacy/stage_based_extraction/scripts/production/template_navigator.py` (line 642)
  - `src/unified_pipeline/core/batch_process_with_repair.py` (line 52)
  - `src/unified_pipeline/core/wcm_template_filler_v2.py` (line 440)
  - `src/unified_pipeline/core/process_already_segmented_cvs.py` (line 39)
  - `src/unified_pipeline/core/aggregate_chatgpt_feedback.py` (line 12)
- Impact: Scripts are non-portable; new team members or CI cannot run them.
- Fix approach: Replace hardcoded paths with paths relative to project root (using `Path(__file__).parents[N]`) or environment variables.

**OpenAI client instantiated with explicit `api_key=` in legacy scripts:**
- Issue: Several legacy scripts call `OpenAI(api_key=api_key)` where `api_key` is read from env. The recommended pattern per project CLAUDE.md is `client = OpenAI()`.
- Files:
  - `src/legacy/stage_based_extraction/scripts/tools/extract_unextracted_fallback.py` (line 804)
  - `src/legacy/stage_based_extraction/scripts/production/heal_unknown_groups.py` (line 33)
  - `src/legacy/stage_based_extraction/scripts/production/remediate_unknown_groups.py` (line 62)
  - `src/legacy/stage_based_extraction/scripts/production/analyze_unknown_groups.py` (line 33)
  - `src/legacy/stage_based_extraction/scripts/production/extract_section_t.py` (line 35)
- Impact: Minor — keys are read from env, not hardcoded — but violates project convention and passes key values through variables.
- Fix approach: Replace with `OpenAI()` and remove manual `api_key` variable passing.

**Pervasive `print()` debugging in production code:**
- Issue: 3,243 `print()` calls in `src/unified_pipeline/` (excluding test files). Many are clearly debug-era statements (e.g., `[DEBUG] Prompt preview`, `[DEBUG B1] WARNING: Empty row data!`).
- Files: Throughout `src/unified_pipeline/` especially `src/unified_pipeline/core/cv_pipeline.py`, `src/legacy/stage_based_extraction/scripts/production/wcm_formatter.py`
- Impact: Noisy stdout; cannot be controlled by log level; pollutes web interface log capture.
- Fix approach: Replace with structured `logging` calls at appropriate levels.

**Hardcoded rate limit fallback in `rate_limiter.py`:**
- Issue: `web_interface/backend/app/rate_limiter.py` falls back to hardcoded `daily = 10` and `monthly = 50` if config cannot be read (lines 28–32).
- Files: `web_interface/backend/app/rate_limiter.py`
- Impact: Comment notes this is a "hardcoded fallback" — silently applies wrong limits if DB config read fails.
- Fix approach: Raise a configuration error instead of silently using fallback values; ensure monitoring would catch a DB config failure.

**Unemitted retry logic in runs API:**
- Issue: `web_interface/backend/app/api/runs.py` line 320 contains `# TODO: Implement retry logic`.
- Files: `web_interface/backend/app/api/runs.py`
- Impact: Retry on failed runs is not implemented; users must re-upload.
- Fix approach: Implement retry by re-queuing the existing uploaded file through the orchestrator.

**Unimplemented TODOs in active pipeline:**
- Issue: Several active (non-legacy) modules have unimplemented placeholders:
  - `src/unified_pipeline/core/section_extraction_orchestrator.py` (line 568): `# TODO: Actually read the file and check entry count` — entry count validation is skipped.
  - `src/unified_pipeline/core/s7_validator.py` (line 325): `pass  # TODO: Implement based on actual data structure` — a validator method does nothing.
  - `src/unified_pipeline/core/legacy_enrichment_orchestrator.py` (line 169): `# TODO: Implement ROR enrichment when needed` — institution enrichment is a stub.
  - `src/unified_pipeline/core/cv_pipeline.py` (line 1975): `# TODO: Add explicit stage 2a/2b when entry delimitation logic is integrated`
- Files: As listed above
- Impact: Silent failures or missing functionality without error.
- Fix approach: Implement or raise `NotImplementedError` with a clear message.

---

## Security Considerations

**`web_interface/backend/auth_config.yaml` committed to git:**
- Risk: Contains real email addresses in the `allowed_users` and `admin_users` allowlists. If the repo is pushed publicly, these email addresses are exposed.
- Files: `web_interface/backend/auth_config.yaml`
- Current mitigation: Repo is currently private/local only.
- Recommendations: Add `auth_config.yaml` to `.gitignore` and provide an `auth_config.yaml.example` with placeholder values.

**Session secret falls back to randomly generated value:**
- Risk: If `CVICHE_SESSION_SECRET` is not set in production, a new random key is generated each startup. All sessions are invalidated on every server restart.
- Files: `web_interface/backend/app/auth.py` (lines 20–26)
- Current mitigation: A warning is logged. This is acceptable for development but problematic in production.
- Recommendations: Require this env var in production startup; fail fast with clear error if missing.

**SQLite databases committed to git:**
- Risk: `web_interface/backend/cviche.db` (5 MB) and `cviche_dev.db` (94 KB) are tracked by git. These databases contain user records, email addresses, upload history, session data, and run logs.
- Files: `web_interface/backend/cviche.db`, `web_interface/backend/cviche_dev.db`
- Current mitigation: None — they are tracked in git history.
- Recommendations: Add `*.db` to `.gitignore`; use `git rm --cached` to remove them; purge from git history using BFG or `git filter-repo`.

**Real faculty CVs tracked in git history:**
- Risk: 261 PDFs and 227 Word documents of real individual faculty CVs are tracked by git. These contain personal information (addresses, degrees, employment history, publications). If the repo were ever pushed to a public host, this would be a significant PII breach.
- Files: `data/sample_cvs/pdf/` (261 files), `data/sample_cvs/word/` (227 files)
- Current mitigation: Repo is not currently on a public host.
- Recommendations: Remove all CV files from git tracking immediately; add `data/sample_cvs/` and `web_interface/uploads/` to `.gitignore`; purge from git history.

---

## Performance Bottlenecks

**git operations are extremely slow:**
- Problem: With 60,374 tracked files (including 140 MB of node_modules, 448 MB archive, 134 MB sample CVs), basic git operations like `git status` and `git checkout` will be very slow.
- Files: Entire repo — no `.gitignore`
- Cause: node_modules (8,044 files), prompt_logs (40,549+ files), and archive (6,208 files) should never be in git.
- Improvement path: Create `.gitignore`, run `git rm --cached -r` on excluded paths, consider `git filter-repo` to clean history.

**`stage_6_word_template.py` is 8,221 lines:**
- Problem: The Word template population stage is in a single monolithic file.
- Files: `src/unified_pipeline/stage_6_word_template.py`
- Cause: Organic growth without refactoring.
- Improvement path: Split by section formatter type (academic degrees, publications, positions, etc.) into separate modules.

---

## Fragile Areas

**`run_full_pipeline.py` (45 KB, 1,576 lines at root level):**
- Files: `run_full_pipeline.py`
- Why fragile: Top-level orchestration script at repo root — not in `src/`. No unit tests. Imports from `src/unified_pipeline/` using relative path manipulation.
- Safe modification: Read fully before editing; all stage invocations flow through this file.
- Test coverage: None detected.

**`src/unified_pipeline/core/repair_segmentation.py` (4,924 lines):**
- Files: `src/unified_pipeline/core/repair_segmentation.py`
- Why fragile: Extremely large single file. Handles segmentation repair heuristics. No dedicated test file found.
- Safe modification: Each repair strategy is self-contained; test against specific CV inputs before changing heuristics.
- Test coverage: Not covered by any test file in active (non-archive) `tests/`.

**`src/unified_pipeline/core/taxonomy_mapper_v2.py` (4,222 lines):**
- Files: `src/unified_pipeline/core/taxonomy_mapper_v2.py`
- Why fragile: Core classification logic; changes affect all downstream stages. Coexists with `taxonomy_mapper.py` (original version) — unclear which is authoritative for the active pipeline.
- Safe modification: Confirm which mapper is called by `cv_pipeline.py` before editing either.
- Test coverage: `test_hierarchical_mapping.py` exists but it's in `core/`, not a dedicated test suite.

---

## Test Coverage Gaps

**No top-level `tests/` directory for active pipeline:**
- What's not tested: The entire `src/unified_pipeline/` pipeline stages 1–6, `run_full_pipeline.py`, and all web interface backend routes.
- Files: `src/unified_pipeline/stage_*.py`, `run_full_pipeline.py`, `web_interface/backend/app/api/`
- Risk: Regressions in core CV parsing logic will not be caught automatically.
- Priority: High

**Active test files are co-located with source in `src/unified_pipeline/core/`:**
- What's not tested: Test files (`test_segmentation_import.py`, `test_hierarchical_mapping.py`, etc.) are inside the source directory, not a separate `tests/` hierarchy. No `pytest` configuration file found at project root.
- Files: `src/unified_pipeline/core/test_*.py`, `src/unified_pipeline/test_async_pipeline.py`
- Risk: Test discovery is unreliable; tests may be skipped in CI.
- Priority: Medium

**Web interface backend has no test suite:**
- What's not tested: All FastAPI routes, authentication middleware, rate limiting, pipeline orchestration, storage backends.
- Files: `web_interface/backend/app/`
- Risk: Auth, upload, and run management regressions are undetected.
- Priority: High

---

## Dependencies at Risk

**`cv_taxonomy_wcm_backup.py` duplicates active taxonomy file:**
- Risk: `src/unified_pipeline/cv_parser/cv_taxonomy_wcm_backup.py` (2,180 lines) is identical in size to the active `cv_taxonomy_wcm.py`. Edits to the active file will not propagate to the backup, causing confusion.
- Impact: Developer confusion about which file to edit.
- Migration plan: Delete the backup file; rely on git history for rollback.

---

## Missing Critical Features

**No `.gitignore`:**
- Problem: The repo has no `.gitignore`, causing 60,374 files including PII, databases, node_modules, and build artifacts to be tracked.
- Blocks: Safe public sharing; clean CI/CD pipelines; reasonable `git status` output.

**No environment variable validation at startup:**
- Problem: Required env vars (`CVICHE_SESSION_SECRET`, `OPENAI_API_KEY_WORK`, `CVICHE_DATABASE_URL` for production) are not validated at startup with clear error messages.
- Files: `web_interface/backend/app/main.py`
- Blocks: Reliable production deployments; fast failure on misconfiguration.

---

*Concerns audit: 2026-03-22*
