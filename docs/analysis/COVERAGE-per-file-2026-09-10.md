# CViche per-file code coverage

Date: 2026-09-10

Tree: `origin/dev` at commit `c9425e4caf4d3245a8cf7eb9a6e370e6ea19f991`, measured from a pristine checkout of that commit (`git archive c9425e4caf4d3245a8cf7eb9a6e370e6ea19f991 | tar -x`) in an isolated directory. Nothing in the developer's own working copy of the repository (which carries unrelated uncommitted changes and sits on a different branch) was read, touched, or copied to produce any number in this document.

## 1. Overview

### The ask

Mahender (verbatim): "provide the code coverage on a per-file basis. This will help us identify which files are missing from code coverage, understand what those files are, and assess how important it is to include them in the coverage now or at a later stage, such as after deployment to production."

Paul: "I want the code coverage percentage for each file."

This document answers both: a full per-file table (section 4), a per-category rollup (section 5), and a mechanical tier assignment (1 = cover now, 2 = after production or as touched, 3 = not worth covering as-is) for the team to finalize.

### Commands run

Pipeline suite:

```
PY=<coverage-enabled python>
PYTHONHASHSEED=0 $PY -m coverage run -p --branch --source=src/unified_pipeline,scripts,run_full_pipeline -m pytest src/unified_pipeline/tests/ -q -p no:cacheprovider
for t in scripts/test_render_doctor_gates.py scripts/test_render_gate_integration.py scripts/test_check_function_size.py scripts/test_check_standards.py; do [ -f $t ] && PYTHONHASHSEED=0 $PY -m coverage run -p --branch --source=src/unified_pipeline,scripts,run_full_pipeline $t; done
$PY -m coverage combine
$PY -m coverage json -o out/coverage/pipeline.json --pretty-print
$PY -m coverage report --precision=1 > out/coverage/pipeline-report.txt
```

`--source` entries must be directories or importable module names, never file paths: `run_full_pipeline.py` (with the extension) silently matched nothing, so an earlier pass of this measurement dropped the CLI driver with no error, only a `Module run_full_pipeline.py was never imported` warning; `run_full_pipeline` (no extension) is the corrected, importable form and is what every command above now uses. With the corrected form, the three script self-test invocations still print `CoverageWarning: Module run_full_pipeline was never imported`, because those scripts do not import the CLI; that warning is expected there, and the combined data still carries the CLI's row from the pytest run.

Every `coverage run` above pins `PYTHONHASHSEED=0`: `src/unified_pipeline/core/validators/hierarchy_mismatch_flagger.py`'s coverage depends on the iteration order of a `set()` built at lines 145/155 and consumed by the loop at line 173, so an unpinned run non-deterministically reports one of two totals for that file, and for the pipeline-wide total (see section 6).

Backend suite, run from `web_interface/backend` (note `--rcfile=../../.coveragerc`, not `--rcfile=.coveragerc`: the config file lives at the repository root, two directories up from `web_interface/backend`, and coverage.py silently proceeds with no config -- no error, no omit rules applied -- if you point it at a path that does not exist):

```
env -u DB_PASSWORD -u CVICHE_TEAMS_WEBHOOK_URL -u DB_USERNAME DB_HOST=localhost DB_PORT=3306 DB_NAME=test DB_USER=test CVICHE_SESSION_SECRET=test-secret-not-for-production PYTHONHASHSEED=0 $PY -m coverage run --branch --source=app --rcfile=../../.coveragerc -m pytest -q -p no:cacheprovider
$PY -m coverage json -o out/coverage/backend.json --pretty-print --rcfile=../../.coveragerc
$PY -m coverage report --precision=1 --rcfile=../../.coveragerc > out/coverage/backend-report.txt
```

Verified the corrected path actually loads the config, from `web_interface/backend`: `python -c "import coverage; c = coverage.Coverage(config_file='../../.coveragerc'); print(c.config.config_files_read, c.config.run_omit)"` prints a real path in `config_files_read` and `run_omit=['*/tests/*', '*/__pycache__/*']`; the same call with `config_file='.coveragerc'` prints `config_files_read=[]` and `run_omit=[]`.

### Tool versions

- `python3.14.2` (system)
- `coverage.py 7.16.0` (with C extension)
- `pytest 8.4.2`
- `ruff 0.16.6` (not used to produce these numbers)

### How to read this

Coverage here is a reported number, not a merge gate: no CI job enforces a minimum and nothing fails a build on it (see docs/CODING_STANDARDS.md 6.10). Two different percentages are quoted below for the same tree and they are not interchangeable: statement percent counts only executed statements; blended percent is coverage.py's headline, which additionally folds in branch-partial coverage and is always the lower of the two. The number that carries the most weight is neither of those taken over the raw repository total, but statement coverage of only the files reachable from a live entry point (`run_full_pipeline.py` for the CLI, `web_interface/backend/app/pipeline/orchestrator.py` for the web pipeline path) -- the "reachable-only" rows in the totals table below. A file that is 0% covered but unreachable from every entry point is not silently missing test effort worth commissioning; it is dead code parked for deletion per #704, and the rubric below (tier 3) treats it that way.

Reachability was computed by statically parsing every non-test Python file's import statements (at module level and inside functions or classes) and resolving each one to a file on disk, following the same module search order Python itself would use from within this tree: package-qualified imports under `src/`, package-qualified imports under `web_interface/backend/` written as `app.x.y`, relative imports, and bare-name imports checked against every directory the code actually puts on `sys.path`. Three closures were built this way, one from each live entry point: `run_full_pipeline.py` (the standalone CLI), `web_interface/backend/app/pipeline/orchestrator.py` (the web pipeline driver), and `web_interface/backend/app/main.py` (the web application, which pulls in the orchestrator's closure plus the full API/service/storage/auth surface). Two shapes of edge that are easy to miss are both modeled: importing a submodule runs every ancestor package's `__init__.py` first, and a single `from <package> import <name1>, <name2>, ...` statement can pull in several submodules at once, not only symbols defined in `<package>` itself -- each name that resolves to an actual file (`<package>/<name>.py` or `<package>/<name>/__init__.py`) counts as its own edge. Known limits of this method: an import assembled from a non-literal, computed module name is invisible to it, and a file this analysis marks unreached could still run in production if its only caller invokes it as a subprocess rather than importing it (see the `check_function_size.py` / `check_standards.py` note in section 3).

## 2. Totals

### Raw

| Tree | Statements | Missed | Statement % | Blended % | Files at 0% |
|---|---|---|---|---|---|
| pipeline (all of `src/unified_pipeline` + `scripts` + `run_full_pipeline.py`) | 20,499 | 9,400 | 54.1 | 51.0 | 21 |
| backend (all of `web_interface/backend/app`) | 4,992 | 1,221 | 75.5 | 71.4 | 0 |

### Reachable-only

Statement coverage summed only over files reachable from a live entry point. Blended % is not recomputed for this cut (coverage.py's blend formula is not reproduced here); only statement % is quoted, consistent with 6.10's preference for the statement figure once a reachable set has been isolated by hand.

| Tree | Reachable files (measured) | Statements | Missed | Statement % |
|---|---|---|---|---|
| pipeline | 155 | 18,310 | 7,654 | 58.2 |
| backend | 55 | 4,992 | 1,221 | 75.5 |

Files at 0% statement coverage: 6 reachable, 15 unreachable (21 total, matching the raw pipeline count of 21 plus backend's 0).

### Comparison to the 2026-08-31 figures (docs/CODING_STANDARDS.md 6.10: "27.1% raw... 55.7% of statements... 102 modules reachable from a live driver", pipeline-only, `src/unified_pipeline`)

| Date | Raw stmt % | Reachable-only stmt % | Reachable module count |
|---|---|---|---|
| 2026-08-31 (6.10, hand-counted) | 27.1 | 55.7 | 102 |
| 2026-09-10 (this report, `src/unified_pipeline` only, for direct comparison) | 54.1 | 60.0 | 142 |

Two different things moved between these dates, and the evidence supports only one of them cleanly:

- **Raw % rose, and PR #771 explains it.** `git diff-tree -r --name-status` against #771's merge commit and its first parent shows it deleted 98 non-test-and-test files with no live importer: 94 under `src/unified_pipeline/` and 4 under `web_interface/backend/`, of which 7 total sat under a `tests/` directory. Those files were confirmed dead before deletion, so removing them shrinks the denominator without removing any tested code -- exactly what raises a raw percentage.
- **Reachable-module count did not shrink the way a pure dead-code deletion would predict -- it rose, from 102 to 142.** PR #771 does not explain that by itself (it deleted unreachable files, which were never in the 102). The likelier cause is that the 2026-08-31 figure was produced by hand (6.10 says so directly) and undercounted reachability that a systematic import-closure analysis now catches, including the submodule-import shape described in section 1. This document does not have the 2026-08-31 worksheet to prove which files it missed, so this is offered as the better-supported explanation, not a proven one.

## 3. Missing entirely (0% statement coverage, or not measured at all)

### 0% by measurement artifact, not by absence of use

These files run in CI but coverage.py cannot see the execution: `scripts/check_function_size.py` (0%) is invoked directly by `.github/workflows/ci.yml` as a fresh `python` subprocess, and both it and `scripts/check_standards.py` (17.8%, appears in the main table, not 0%) are also exercised by their own self-tests through `subprocess.run([sys.executable, ...])`. coverage.py cannot see code that runs in a separate subprocess without `COVERAGE_PROCESS_START` wired in, so both numbers understate real exercise. `scripts/render_gate.py` and `scripts/doctor_gate.py`, by contrast, are genuinely unexecuted on this tree (their own test file imports only the `*_compare` half of each pair) -- not a measurement artifact. A pull request adding an integration test for `render_gate.py` is open at the time of this report (#740).

| File | Category | What it is | Tier |
|---|---|---|---|
| `scripts/check_function_size.py` | tooling | Ratchet on oversized functions: the number may fall, never rise. | 2 |

### Other reachable files sitting at 0% (live code paths, not a measurement artifact)

| File | Category | What it is | Tier |
|---|---|---|---|
| `scripts/corpus_distinct_cvs.py` | tooling | Collapse a corpus of stage-4 fields.json to DISTINCT CVs before you quote a prevalence. | 2 |
| `scripts/cviche_trace.py` | tooling | Trace one CV record across pipeline stages 2 -> 3b -> 4 -> 6. | 2 |
| `scripts/doctor_gate.py` | gate | Run run_doctor over every corpus CV from one code arm, into a findings JSON. | 2 |
| `scripts/gen_template_boilerplate.py` | tooling | Generate template_boilerplate_phrases.json from the WCM faculty CV template. | 2 |
| `scripts/render_gate.py` | gate | Render every corpus CV through stage 6 from one code arm, into a fresh out_dir. (an integration test is proposed in open PR #740) | 2 |

### Unreachable (tier 3 -- dead code candidates, deletion parked per #704)

| File | Category | What it is |
|---|---|---|
| `src/unified_pipeline/core/bulk_pubmed_fetcher.py` | other | Bulk PubMed Record Fetcher with Caching |
| `src/unified_pipeline/core/candidate_surfacer.py` | other | LLM-Based Candidate Surfacing for Taxonomy Classification |
| `src/unified_pipeline/core/hierarchy_overrides.py` | other | Hierarchy-based override logic for taxonomy mapping. |
| `src/unified_pipeline/core/legacy_enrichment_orchestrator.py` | other | Legacy Enrichment Orchestrator |
| `src/unified_pipeline/core/legacy_extractor_orchestrator.py` | other | Legacy Extractor Orchestrator |
| `src/unified_pipeline/core/personal_info_extractor.py` | other | Personal Information Extractor - First Page Only |
| `src/unified_pipeline/core/prompt_analyzer.py` | other | Prompt Analyzer - Find patterns in LLM prompt successes and failures |
| `src/unified_pipeline/core/publication_classification_service.py` | other | Publication Classification Service |
| `src/unified_pipeline/core/s7_validator.py` | other | S7 Validation Rules - Prevent Published Articles from Being Labeled as Unpublished |
| `src/unified_pipeline/core/unified_publication_classifier.py` | other | Unified Publication Classifier |
| `src/unified_pipeline/core/unified_to_classified_converter.py` | other | Unified-to-Classified Converter |
| `src/unified_pipeline/core/valid_taxonomy_codes.py` | other | Valid WCM Taxonomy Codes for Enum Validation |
| `src/unified_pipeline/core/validators/calibration_logger.py` | other | Calibration Logger |
| `src/unified_pipeline/core/validators/validation_checker.py` | other | Validation Checker - Post-Validation Safety Net |
| `src/unified_pipeline/core/wcm_section_extractors.py` | other | WCM Section Extractors - Complete Integration |

### Not measured at all (structurally invisible to coverage.py's file discovery, not a 0% row; all confirmed genuinely 0% covered by CI)

| File | Reachable | Category | Reason not measured |
|---|---|---|---|
| `src/unified_pipeline/examples/classify_publications_example.py` | no | other | never imported by the test suite; lives in src/unified_pipeline/examples/, which has no __init__.py, so coverage's unexecuted-file discovery (which requires a real package under --source) never adds it as a 0% row. Directories with __init__.py under src/unified_pipeline/ (e.g. core/) do get 0% rows for their unexecuted files. |
| `src/unified_pipeline/scripts/backfill_pubmed_cache.py` | no | other | same cause: src/unified_pipeline/scripts/ has no __init__.py, and the module is never imported. |
| `src/unified_pipeline/tools/verify_word_document.py` | no | other | same cause: src/unified_pipeline/tools/ has no __init__.py, and the module is never imported. |
| `web_interface/backend/app/config/pipeline_config.py` | no | config | never imported by the backend test suite; app/config/ has no __init__.py, so it is invisible to coverage's unexecuted-file discovery under --source=app for the same reason as the pipeline/scripts,examples,tools cases above. |
## 4. Full per-file table

All 234 files across both trees (backend paths start `web_interface/backend/`; everything else is the pipeline tree). Sorted by tier, then statement % ascending, then file path. `n/a` = not measured (see section 3); `-` = no import edge found / not applicable.

| File | Stmts | Miss | Stmt % | Blended % | Reachable from | What it is | Tier |
|---|---|---|---|---|---|---|---|
| `src/unified_pipeline/segmentation/signature_based_segmentation.py` | 668 | 635 | 4.9 | 3.4 | cli, orchestrator, backend | Signature-Based CV Segmentation with LLM Classification | 1 |
| `src/unified_pipeline/stage_1b_hierarchy_mapper.py` | 281 | 266 | 5.3 | 3.7 | cli, orchestrator, backend | Stage 1b: Map Hierarchy to Element Indices (Python only, no LLM) | 1 |
| `src/unified_pipeline/stage_4_5_research_summary.py` | 268 | 243 | 9.3 | 6.7 | cli, orchestrator, backend | Stage 4.5: Research Summary Generation | 1 |
| `src/unified_pipeline/stage_3a_header_taxonomy_mapper.py` | 118 | 104 | 11.9 | 10.3 | cli, orchestrator, backend | Stage 3a: Header → Taxonomy Mapping | 1 |
| `src/unified_pipeline/segmentation/chunked_chat_hierarchy_extractor.py` | 159 | 138 | 13.2 | 11.5 | cli, orchestrator, backend | Chunked Chat Completions API hierarchy extraction with existing normalization. | 1 |
| `src/unified_pipeline/llm/bedrock.py` | 133 | 112 | 15.8 | 11.5 | cli, orchestrator, backend | Bedrock provider adapter: client, Converse API call, message translation, schema enforcement, and response normalization. | 1 |
| `src/unified_pipeline/stage_5d_citation_formatter.py` | 157 | 132 | 15.9 | 12.1 | cli, orchestrator, backend | Stage 5d: Citation Formatter for Non-Enriched Publications | 1 |
| `src/unified_pipeline/stage_5c_teaching_formatter.py` | 152 | 127 | 16.4 | 12.2 | cli, orchestrator, backend | Stage 5c: Teaching/Educational Contributions Formatter | 1 |
| `src/unified_pipeline/stage_4_field_extractor.py` | 95 | 79 | 16.8 | 15.6 | cli, orchestrator, backend | Stage 4: Intra-Entry Field Extraction (v2.0) | 1 |
| `src/unified_pipeline/stage_2_entry_extraction.py` | 587 | 463 | 21.1 | 19.6 | cli, orchestrator, backend | Stage 2: Entry Extraction (Combined Delimiter Detection + Text Extraction) | 1 |
| `src/unified_pipeline/stage6/sorting/document_order.py` | 19 | 14 | 26.3 | 25.9 | cli, orchestrator, backend | Ordering records the way the source CV had them (#398). | 1 |
| `src/unified_pipeline/llm/openai.py` | 26 | 19 | 26.9 | 18.4 | cli, orchestrator, backend | OpenAI provider adapter: client, raw SDK call, and response normalization. | 1 |
| `src/unified_pipeline/stage6/sections/researcher_profiles.py` | 22 | 16 | 27.3 | 21.9 | cli, orchestrator, backend | Section S0: researcher profile identifiers (#398). | 1 |
| `web_interface/backend/app/api/runs.py` | 466 | 321 | 31.1 | 26.6 | backend | Run status and management API endpoints. | 1 |
| `src/unified_pipeline/stage4/extraction.py` | 325 | 223 | 31.4 | 27.6 | cli, orchestrator, backend | LLM field extraction for stage 4: prompts, batch dispatch, recovery. | 1 |
| `src/unified_pipeline/stage6/sections/research_summary.py` | 42 | 28 | 33.3 | 33.9 | cli, orchestrator, backend | Section M1: the research summary (#398). | 1 |
| `web_interface/backend/app/api/steps.py` | 289 | 177 | 38.8 | 34.0 | backend | Step details and data API endpoints. | 1 |
| `web_interface/backend/app/api/feedback_routes.py` | 98 | 53 | 45.9 | 38.4 | backend | Feedback API endpoints for collecting user feedback on pipeline runs. | 1 |
| `web_interface/backend/app/api/admin_routes.py` | 233 | 119 | 48.9 | 40.7 | backend | Admin dashboard API endpoints. | 1 |
| `src/unified_pipeline/stage_5_pubmed_enrichment.py` | 407 | 207 | 49.1 | 44.6 | cli, orchestrator, backend | Stage 5: PubMed Enrichment | 1 |
| `src/unified_pipeline/llm/retry.py` | 66 | 33 | 50.0 | 42.5 | cli, orchestrator, backend | Shared call infrastructure for the OpenAI and Bedrock provider adapters. | 1 |
| `web_interface/backend/app/storage/s3_storage.py` | 92 | 46 | 50.0 | 43.2 | orchestrator, backend | S3 implementation of RunStorage. | 1 |
| `scripts/check_function_size.py` | 78 | 78 | 0.0 | 0.0 | entry point (script) | Ratchet on oversized functions: the number may fall, never rise. | 2 |
| `scripts/corpus_distinct_cvs.py` | 120 | 120 | 0.0 | 0.0 | entry point (script) | Collapse a corpus of stage-4 fields.json to DISTINCT CVs before you quote a prevalence. | 2 |
| `scripts/cviche_trace.py` | 187 | 187 | 0.0 | 0.0 | entry point (script) | Trace one CV record across pipeline stages 2 -> 3b -> 4 -> 6. | 2 |
| `scripts/doctor_gate.py` | 68 | 68 | 0.0 | 0.0 | entry point (script) | Run run_doctor over every corpus CV from one code arm, into a findings JSON. | 2 |
| `scripts/gen_template_boilerplate.py` | 78 | 78 | 0.0 | 0.0 | entry point (script) | Generate template_boilerplate_phrases.json from the WCM faculty CV template. | 2 |
| `scripts/render_gate.py` | 73 | 73 | 0.0 | 0.0 | entry point (script) | Render every corpus CV through stage 6 from one code arm, into a fresh out_dir. | 2 |
| `scripts/check_standards.py` | 366 | 301 | 17.8 | 12.3 | entry point (script) | Regenerate CODING_STANDARDS.md's mechanically-checkable §9 rows. | 2 |
| `src/unified_pipeline/core/validators/leadership_committee.py` | 43 | 35 | 18.6 | 15.7 | cli, orchestrator, backend | Leadership vs Committee Membership Validator | 2 |
| `src/unified_pipeline/core/prompt_logger.py` | 118 | 95 | 19.5 | 17.1 | cli, orchestrator, backend | Prompt Logger - Captures EXACT prompts sent to LLMs | 2 |
| `src/unified_pipeline/core/validators/mentoring_indicators.py` | 44 | 35 | 20.5 | 18.0 | cli, orchestrator, backend | Mentoring Indicators Validator | 2 |
| `src/unified_pipeline/core/validators/contact_section.py` | 106 | 84 | 20.8 | 14.5 | cli, orchestrator, backend | Contact/Personal Data Section Validator | 2 |
| `src/unified_pipeline/core/output_manager.py` | 97 | 76 | 21.6 | 16.0 | cli, orchestrator, backend | Output Manager for CV Processing Pipeline | 2 |
| `src/unified_pipeline/core/validators/awards_grants.py` | 99 | 77 | 22.2 | 14.4 | cli, orchestrator, backend | Validator for disambiguating Awards between Grants (Research Support) and Honors. | 2 |
| `src/unified_pipeline/core/validators/url_domain.py` | 112 | 87 | 22.3 | 15.7 | cli, orchestrator, backend | URL Domain Validator | 2 |
| `src/unified_pipeline/core/validators/temporal_patterns.py` | 76 | 59 | 22.4 | 14.4 | cli, orchestrator, backend | Temporal Pattern Validator | 2 |
| `src/unified_pipeline/core/validators/fellowship_classifier.py` | 84 | 65 | 22.6 | 14.8 | cli, orchestrator, backend | Fellowship Classifier Validator | 2 |
| `src/unified_pipeline/core/validators/education_postdoc.py` | 75 | 58 | 22.7 | 13.6 | cli, orchestrator, backend | Education vs Postdoc Position Validator | 2 |
| `src/unified_pipeline/core/validators/s7_unpublished.py` | 118 | 89 | 24.6 | 15.8 | cli, orchestrator, backend | S7 Unpublished Validator | 2 |
| `src/unified_pipeline/core/validators/professional_service.py` | 29 | 21 | 27.6 | 24.2 | cli, orchestrator, backend | Professional Service Validator | 2 |
| `src/unified_pipeline/core/validators/dataset_cohort.py` | 54 | 39 | 27.8 | 19.7 | cli, orchestrator, backend | Dataset/Cohort Validator | 2 |
| `src/unified_pipeline/core/validators/book_chapter.py` | 63 | 45 | 28.6 | 20.2 | cli, orchestrator, backend | Book/Chapter Validator | 2 |
| `src/unified_pipeline/core/validators/honors_membership.py` | 56 | 40 | 28.6 | 20.5 | cli, orchestrator, backend | Honors/Membership Validator | 2 |
| `src/unified_pipeline/core/validators/reasoning_consistency_checker.py` | 78 | 55 | 29.5 | 25.0 | cli, orchestrator, backend | Reasoning-Code Consistency Checker | 2 |
| `src/unified_pipeline/core/validators/grant_structure.py` | 54 | 38 | 29.6 | 20.5 | cli, orchestrator, backend | Grant Structure Validator | 2 |
| `src/unified_pipeline/core/validators/publication_case_report.py` | 50 | 35 | 30.0 | 22.1 | cli, orchestrator, backend | S6 Case Reports Validator | 2 |
| `src/unified_pipeline/core/validators/mentee_outcomes.py` | 46 | 32 | 30.4 | 21.9 | cli, orchestrator, backend | Mentee Outcomes Validator | 2 |
| `src/unified_pipeline/core/validators/postdoc_position.py` | 46 | 32 | 30.4 | 19.4 | cli, orchestrator, backend | Postdoc Position Validator | 2 |
| `src/unified_pipeline/core/validators/grant_status_corrector.py` | 82 | 57 | 30.5 | 24.6 | cli, orchestrator, backend | Grant Status Corrector | 2 |
| `src/unified_pipeline/core/validators/guidance_engine.py` | 68 | 47 | 30.9 | 25.5 | cli, orchestrator, backend | Guidance Engine | 2 |
| `src/unified_pipeline/config.py` | 200 | 135 | 32.5 | 26.0 | cli, orchestrator, backend | Centralized configuration for CV parsing pipeline. | 2 |
| `src/unified_pipeline/core/validators/publication_manuscript_in_prep.py` | 41 | 27 | 34.1 | 26.4 | cli, orchestrator, backend | S7 Manuscripts in Preparation Validator | 2 |
| `src/unified_pipeline/core/validators/grant_position_corrector.py` | 68 | 44 | 35.3 | 29.4 | cli, orchestrator, backend | Grant Position Corrector | 2 |
| `src/unified_pipeline/core/validators/label_content_conflict.py` | 53 | 34 | 35.8 | 27.5 | cli, orchestrator, backend | Label-Content Conflict Validator | 2 |
| `src/unified_pipeline/core/validators/service_vs_publication.py` | 39 | 25 | 35.9 | 25.5 | cli, orchestrator, backend | Service vs Publication Validator | 2 |
| `src/unified_pipeline/core/validators/committee_position_corrector.py` | 87 | 54 | 37.9 | 33.6 | cli, orchestrator, backend | Committee vs Position Auto-Corrector | 2 |
| `src/unified_pipeline/core/docx_structure_extractor.py` | 461 | 278 | 39.7 | 36.5 | cli, orchestrator, backend | Word Document Structure Extractor | 2 |
| `src/unified_pipeline/core/validators/publication_preprint.py` | 37 | 22 | 40.5 | 31.9 | cli, orchestrator, backend | S10 Preprint Validator | 2 |
| `scripts/render_gate_compare.py` | 129 | 76 | 41.1 | 39.4 | entry point (script) | Compare two render_gate.py output dirs. | 2 |
| `src/unified_pipeline/stage5b/cache.py` | 62 | 35 | 43.5 | 39.2 | cli, orchestrator, backend | The on-disk institution lookup cache and its key scheme (#523). | 2 |
| `src/unified_pipeline/core/validators/section_header_context.py` | 25 | 14 | 44.0 | 33.3 | cli, orchestrator, backend | Section Header Context Validator | 2 |
| `src/unified_pipeline/core/validators/publication_abstract.py` | 32 | 17 | 46.9 | 37.5 | cli, orchestrator, backend | S8 Conference Abstract Validator | 2 |
| `scripts/doctor_gate_compare.py` | 57 | 30 | 47.4 | 48.1 | entry point (script) | Compare two doctor_gate.py runs. | 2 |
| `src/unified_pipeline/core/validators/adjunct_position_corrector.py` | 45 | 23 | 48.9 | 41.5 | cli, orchestrator, backend | Adjunct/Instructor Position Corrector | 2 |
| `src/unified_pipeline/core/validators/leadership_level_corrector.py` | 45 | 23 | 48.9 | 41.5 | cli, orchestrator, backend | Leadership Level Corrector | 2 |
| `src/unified_pipeline/core/validators/prose_mentee_corrector.py` | 47 | 24 | 48.9 | 42.9 | cli, orchestrator, backend | Prose mentee corrector. | 2 |
| `src/unified_pipeline/core/validators/training_compliance_corrector.py` | 43 | 21 | 51.2 | 42.9 | cli, orchestrator, backend | Training/Compliance Corrector | 2 |
| `src/unified_pipeline/core/validators/invited_talk_corrector.py` | 44 | 21 | 52.3 | 43.8 | cli, orchestrator, backend | Invited Talk Corrector | 2 |
| `src/unified_pipeline/core/validators/hierarchy_mismatch_flagger.py` | 60 | 28 | 53.3 | 50.0 | cli, orchestrator, backend | Hierarchy-Taxonomy Mismatch Flagger | 2 |
| `src/unified_pipeline/stage5b/lookup.py` | 94 | 43 | 54.3 | 48.6 | cli, orchestrator, backend | LLM institution lookup: prompt construction and the batched call (#523). | 2 |
| `src/unified_pipeline/core/validators/teaching_leadership_corrector.py` | 36 | 16 | 55.6 | 50.0 | cli, orchestrator, backend | Teaching Leadership Corrector | 2 |
| `src/unified_pipeline/core/validators/structural_header.py` | 109 | 46 | 57.8 | 50.9 | cli, orchestrator, backend | Structural Header Validator | 2 |
| `src/unified_pipeline/stage6/sections/passthrough.py` | 123 | 49 | 60.2 | 54.2 | cli, orchestrator, backend | Sections E and G: the passthrough sections (#398). | 2 |
| `src/unified_pipeline/stage6/sections/personal_data.py` | 223 | 85 | 61.9 | 55.2 | cli, orchestrator, backend | Section A: personal data -- name, addresses, phones, emails (#398). | 2 |
| `src/unified_pipeline/core/validators/wcm_table_corrector.py` | 40 | 15 | 62.5 | 55.4 | cli, orchestrator, backend | WCM structured-table corrector. | 2 |
| `src/unified_pipeline/stage4/owner_name.py` | 244 | 91 | 62.7 | 56.1 | cli, orchestrator, backend | CV owner-name extraction and owner-location inference (stage 4). | 2 |
| `src/unified_pipeline/llm_client.py` | 34 | 12 | 64.7 | 63.2 | cli, orchestrator, backend | Centralized LLM client for CV parsing pipeline. | 2 |
| `web_interface/backend/app/errors.py` | 20 | 7 | 65.0 | 59.1 | backend | Consistent HTTPException factory functions. | 2 |
| `web_interface/backend/app/pipeline/orchestrator.py` | 631 | 219 | 65.3 | 61.8 | orchestrator, backend | Pipeline orchestrator - coordinates execution of all 12 pipeline stages. | 2 |
| `src/unified_pipeline/stage6/sections/honors.py` | 178 | 61 | 65.7 | 62.3 | cli, orchestrator, backend | Section H: honors and awards (#398). | 2 |
| `src/unified_pipeline/stage_3b_entry_classifier.py` | 355 | 118 | 66.8 | 59.4 | cli, orchestrator, backend | Stage 3b: Entry Classification | 2 |
| `web_interface/backend/app/api/upload.py` | 212 | 69 | 67.5 | 62.0 | backend | File upload API endpoint. | 2 |
| `src/unified_pipeline/stage6/sections/teaching.py` | 102 | 32 | 68.6 | 63.9 | cli, orchestrator, backend | Section K: teaching activities, K1 through K5 (#398). | 2 |
| `src/unified_pipeline/stage6/sections/research_support.py` | 212 | 64 | 69.8 | 65.0 | cli, orchestrator, backend | Section M2: research support -- current, past and pending funding (#398). | 2 |
| `src/unified_pipeline/core/validators/template_scaffold.py` | 47 | 14 | 70.2 | 69.2 | cli, orchestrator, backend | WCM template-scaffold corrector. | 2 |
| `src/unified_pipeline/stage4/coercion.py` | 202 | 59 | 70.8 | 67.0 | cli, orchestrator, backend | Value coercion and normalisation for stage 4 extracted fields. | 2 |
| `src/unified_pipeline/stage_6_word_template.py` | 1,161 | 320 | 72.4 | 68.6 | cli, orchestrator, backend | Stage 6: WCM Word Template Generation | 2 |
| `src/unified_pipeline/stage_5b_institution_enrichment.py` | 212 | 58 | 72.6 | 67.1 | cli, orchestrator, backend | Stage 5b: Institution Enrichment via LLM | 2 |
| `web_interface/backend/app/pipeline/redis_broker.py` | 74 | 19 | 74.3 | 75.0 | orchestrator, backend | Optional Redis broker for cross-worker real-time fan-out and cancellation. | 2 |
| `web_interface/backend/app/services/quality_score_service.py` | 35 | 9 | 74.3 | 68.3 | orchestrator, backend | Per-run quality score: compute from persisted outputs, cache, and read. | 2 |
| `src/unified_pipeline/stage6/resolution/owner.py` | 16 | 4 | 75.0 | 76.9 | cli, orchestrator, backend | Deciding whose CV this is (#398). | 2 |
| `src/unified_pipeline/core/validators/block_coherence_corrector.py` | 164 | 40 | 75.6 | 75.5 | cli, orchestrator, backend | Block-coherence corrector (issue #198). | 2 |
| `src/unified_pipeline/stage6/sections/presentations.py` | 82 | 20 | 75.6 | 71.4 | cli, orchestrator, backend | Section R: invitations to speak and present (#398). | 2 |
| `web_interface/backend/app/services/run_service.py` | 169 | 41 | 75.7 | 77.4 | backend | Run-related service functions. | 2 |
| `src/unified_pipeline/stage6/normalization/text.py` | 128 | 31 | 75.8 | 72.9 | cli, orchestrator, backend | Value normalization lifted out of stage_6_word_template (#398). | 2 |
| `src/unified_pipeline/stage6/parsing/text.py` | 174 | 41 | 76.4 | 72.3 | cli, orchestrator, backend | Reading structure out of raw CV text (#398). | 2 |
| `run_full_pipeline.py` | 694 | 160 | 76.9 | 73.6 | cli | CViche - Full CV Processing Pipeline (V15) | 2 |
| `web_interface/backend/app/storage/factory.py` | 18 | 4 | 77.8 | 77.3 | orchestrator, backend | Factory for creating the configured RunStorage backend. | 2 |
| `src/unified_pipeline/stage3b/context.py` | 82 | 18 | 78.0 | 76.9 | cli, orchestrator, backend | Hierarchy context for stage 3b classification (#522). | 2 |
| `src/unified_pipeline/stage6/sections/service.py` | 445 | 98 | 78.0 | 72.9 | cli, orchestrator, backend | Section Q: extramural professional responsibilities (#398). | 2 |
| `web_interface/backend/app/pipeline/step_registry.py` | 46 | 10 | 78.3 | 79.0 | orchestrator, backend | Registry of all pipeline steps - aligned with run_full_pipeline.py (V15). | 2 |
| `src/unified_pipeline/stage6/sections/memberships.py` | 80 | 17 | 78.8 | 71.9 | cli, orchestrator, backend | Section I: professional organizations and society memberships (#398). | 2 |
| `src/unified_pipeline/core/validators/base_validator.py` | 44 | 9 | 79.5 | 79.5 | cli, orchestrator, backend | Base Validator Interface | 2 |
| `web_interface/backend/app/storage/local_storage.py` | 66 | 12 | 81.8 | 76.1 | orchestrator, backend | Local filesystem implementation of RunStorage. | 2 |
| `src/unified_pipeline/stage6/sections/positions.py` | 242 | 42 | 82.6 | 77.3 | cli, orchestrator, backend | Section D: academic, hospital and other professional appointments (#398). | 2 |
| `web_interface/backend/app/database.py` | 24 | 4 | 83.3 | 83.3 | backend | Database configuration and session management. | 2 |
| `web_interface/backend/app/services/template_warning.py` | 30 | 5 | 83.3 | 75.0 | backend | Cheap, no-LLM detection of blank/near-blank WCM template uploads. | 2 |
| `src/unified_pipeline/stage6/resolution/institution.py` | 55 | 9 | 83.6 | 81.5 | cli, orchestrator, backend | Deciding which institution and location a record actually refers to (#398). | 2 |
| `web_interface/backend/app/consent.py` | 43 | 7 | 83.7 | 75.5 | backend | Consent text management and version integrity checking. | 2 |
| `web_interface/backend/app/api/consent_routes.py` | 19 | 3 | 84.2 | 84.2 | backend | Consent API endpoints. | 2 |
| `web_interface/backend/app/pipeline/event_emitter.py` | 139 | 21 | 84.9 | 82.1 | orchestrator, backend | Event emitter for WebSocket communication. | 2 |
| `web_interface/backend/app/services/config_service.py` | 27 | 4 | 85.2 | 82.8 | backend | Centralized configuration constants with environment variable overrides. | 2 |
| `web_interface/backend/app/rate_limiter.py` | 57 | 8 | 86.0 | 80.5 | backend | Rate limiting for pipeline runs based on per-user and system-wide limits. | 2 |
| `src/unified_pipeline/stage6/formatting/dates.py` | 58 | 8 | 86.2 | 87.0 | cli, orchestrator, backend | Rendering a date the way one WCM section wants to see it (#398). | 2 |
| `src/unified_pipeline/stage6/sections/board_certification.py` | 197 | 23 | 88.3 | 85.3 | cli, orchestrator, backend | Section F2: board certification (#398). | 2 |
| `src/unified_pipeline/stage6/sections/clinical_practice.py` | 207 | 23 | 88.9 | 82.5 | cli, orchestrator, backend | Section L: clinical practice, innovation and leadership (#398). | 2 |
| `src/unified_pipeline/stage6/sections/administrative_activities.py` | 131 | 14 | 89.3 | 86.4 | cli, orchestrator, backend | Section P: institutional administrative activities (#398). | 2 |
| `src/unified_pipeline/stage4/schemas.py` | 66 | 7 | 89.4 | 85.9 | cli, orchestrator, backend | Field schemas, descriptions and taxonomy labels for stage 4 extraction. | 2 |
| `src/unified_pipeline/stage6/dedup.py` | 94 | 10 | 89.4 | 86.0 | cli, orchestrator, backend | Near-duplicate removal within a taxonomy-code group (#398). | 2 |
| `scripts/doctor_one.py` | 42 | 4 | 90.5 | 88.5 | entry point (script) | Run run_doctor on a single local pipeline run; print a one-line TSV summary. | 2 |
| `src/unified_pipeline/stage3b/io.py` | 85 | 8 | 90.6 | 85.6 | cli, orchestrator, backend | Stage 3b artifact I/O (#522). | 2 |
| `web_interface/backend/app/api/websocket.py` | 126 | 11 | 91.3 | 89.9 | backend | WebSocket endpoint for real-time pipeline updates. | 2 |
| `src/unified_pipeline/stage6/sections/leadership.py` | 84 | 7 | 91.7 | 89.8 | cli, orchestrator, backend | Section O: institutional leadership activities (#398). | 2 |
| `scripts/score_one.py` | 58 | 4 | 93.1 | 92.1 | entry point (script) | Score a single local pipeline run; print a one-line TSV summary. | 2 |
| `web_interface/backend/app/pipeline/concurrency.py` | 29 | 2 | 93.1 | 93.9 | backend | Per-pod admission control for pipeline runs. | 2 |
| `web_interface/backend/app/login_throttle.py` | 61 | 4 | 93.4 | 89.6 | backend | Login rate limiting, distributed across pods via Valkey/Redis. | 2 |
| `web_interface/backend/app/config_loader.py` | 46 | 3 | 93.5 | 91.7 | cli, orchestrator, backend | Load auth config from YAML and seed SystemConfig DB table. | 2 |
| `src/unified_pipeline/quality_score.py` | 369 | 22 | 94.0 | 93.5 | orchestrator, backend | CViche run quality scorer. | 2 |
| `web_interface/backend/app/main.py` | 196 | 11 | 94.4 | 92.2 | backend | Main FastAPI application. | 2 |
| `src/unified_pipeline/segmentation_regression.py` | 343 | 15 | 95.6 | 93.8 | orchestrator, backend | Gold-set segmentation regression harness (precondition for #208/#212). | 2 |
| `src/unified_pipeline/doctor/lints/render.py` | 361 | 15 | 95.8 | 94.1 | orchestrator, backend | Lints for defects visible in the rendered WCM document (#493). | 2 |
| `src/unified_pipeline/stage6/formatting/docx.py` | 97 | 4 | 95.9 | 93.4 | cli, orchestrator, backend | python-docx formatting primitives lifted out of WCMTemplateGenerator (#398). | 2 |
| `src/unified_pipeline/core/validators/position_subcode_reconciler.py` | 50 | 2 | 96.0 | 93.2 | cli, orchestrator, backend | Position Subcode Reconciler | 2 |
| `web_interface/backend/app/api/saml_routes.py` | 177 | 7 | 96.0 | 95.3 | backend | SAML 2.0 Service Provider endpoints. | 2 |
| `web_interface/backend/app/ed_group_lookup.py` | 233 | 9 | 96.1 | 95.7 | backend | Enterprise Directory group membership check via LDAP with TTL cache. | 2 |
| `src/unified_pipeline/stage6/sections/education.py` | 105 | 4 | 96.2 | 95.0 | cli, orchestrator, backend | Section B1: education -- conferred degrees (#398). | 2 |
| `src/unified_pipeline/stage6/sections/licensure.py` | 151 | 5 | 96.7 | 93.6 | cli, orchestrator, backend | Section F1: licensure, plus the DEA and NPI numbers (#398). | 2 |
| `web_interface/backend/app/api/auth_routes.py` | 93 | 3 | 96.8 | 96.3 | backend | Authentication API endpoints. | 2 |
| `src/unified_pipeline/core/template_boilerplate.py` | 65 | 2 | 96.9 | 96.8 | cli, orchestrator, backend | Deterministic, precision-biased detector for WCM-template instruction boilerplate. | 2 |
| `web_interface/backend/app/services/auto_retry.py` | 36 | 1 | 97.2 | 95.7 | backend | Auto-retry classification and configuration for interrupted runs (issue #145). | 2 |
| `src/unified_pipeline/doctor/lints/segmentation.py` | 38 | 1 | 97.4 | 94.2 | orchestrator, backend | Lints for how the source document was cut into a hierarchy (#493). | 2 |
| `src/unified_pipeline/stage6/sections/mentoring.py` | 212 | 5 | 97.6 | 95.8 | cli, orchestrator, backend | Section N: mentoring -- current mentees, past mentees, outcomes (#398). | 2 |
| `web_interface/backend/app/saml_client.py` | 84 | 2 | 97.6 | 96.4 | backend | pysaml2 SP client factory, certificate generation, and SAML attribute extraction. | 2 |
| `src/unified_pipeline/stage6/sections/postdoc_training.py` | 87 | 2 | 97.7 | 96.6 | cli, orchestrator, backend | Section C: postdoctoral training, including C1, C2 and C3 (#398). | 2 |
| `web_interface/backend/app/saml_replay.py` | 88 | 2 | 97.7 | 96.7 | backend | SAML assertion replay cache backed by Valkey/Redis. | 2 |
| `web_interface/backend/app/auth.py` | 230 | 5 | 97.8 | 96.6 | backend | Authentication middleware using itsdangerous signed cookies. | 2 |
| `src/unified_pipeline/doctor/lints/extraction.py` | 142 | 3 | 97.9 | 97.1 | orchestrator, backend | Lints for what the extraction stages pulled out, and what became of it (#493). | 2 |
| `src/unified_pipeline/stage6/sections/patents.py` | 145 | 3 | 97.9 | 96.4 | cli, orchestrator, backend | Section M2D: patents and inventions (#398). | 2 |
| `scripts/corpus_doctor_sweep.py` | 197 | 4 | 98.0 | 97.5 | entry point (script) | Stage flat S3 run outputs into the stage_* layout run_doctor expects, run the doctor over a set of representative runs, and aggregate... | 2 |
| `src/unified_pipeline/stage6/sections/appendix.py` | 98 | 2 | 98.0 | 97.0 | cli, orchestrator, backend | Section T: the appendix -- content that reached no other section (#398). | 2 |
| `src/unified_pipeline/run_doctor.py` | 271 | 5 | 98.2 | 96.4 | orchestrator, backend | Cross-stage run doctor: offline lints over one run's stage artifacts. | 2 |
| `web_interface/backend/app/services/notifications.py` | 112 | 2 | 98.2 | 98.6 | orchestrator, backend | Best-effort outbound notifications for run lifecycle events. | 2 |
| `scripts/test_render_doctor_gates.py` | 130 | 2 | 98.5 | 97.1 | n/a | Self-tests for the pure logic in render_gate_compare.py and doctor_gate_compare.py -- the parts cheap enough to pin without a corpus. | 2 |
| `web_interface/backend/app/database_factory.py` | 72 | 1 | 98.6 | 96.8 | backend | (no docstring) builds the SQLAlchemy engine, choosing RDS IAM or password auth by DB_AUTH_MODE. | 2 |
| `src/unified_pipeline/stage6/sections/other_education.py` | 87 | 1 | 98.9 | 98.3 | cli, orchestrator, backend | Section B2: other educational experiences (#398). | 2 |
| `src/unified_pipeline/stage3b/classify.py` | 325 | 2 | 99.4 | 98.6 | cli, orchestrator, backend | Stage 3b LLM classification passes (#522). | 2 |
| `src/unified_pipeline/stage6/sections/bibliography.py` | 173 | 1 | 99.4 | 97.4 | cli, orchestrator, backend | Section S: bibliography -- publications S1 through S9 (#398). | 2 |
| `scripts/test_check_function_size.py` | 65 | 0 | 100.0 | 98.5 | n/a | Self-test for the oversized-function ratchet. | 2 |
| `scripts/test_check_standards.py` | 250 | 0 | 100.0 | 99.6 | n/a | Self-test for check_standards.py. | 2 |
| `src/unified_pipeline/__init__.py` | 1 | 0 | 100.0 | 100.0 | cli, orchestrator, backend | (no docstring) package marker; sets __version__. | 2 |
| `src/unified_pipeline/core/render_check.py` | 4 | 0 | 100.0 | 100.0 | cli, orchestrator, backend | Render-presence primitives shared between the offline run doctor (unified_pipeline.run_doctor) and stage 6's own #221 recovery pass... | 2 |
| `src/unified_pipeline/core/validators/__init__.py` | 75 | 0 | 100.0 | 100.0 | cli, orchestrator, backend | Validators Package | 2 |
| `src/unified_pipeline/doctor/lints/enrichment.py` | 27 | 0 | 100.0 | 100.0 | orchestrator, backend | Lints for the two gates that decide whether a run is deliverable at all (#493). | 2 |
| `src/unified_pipeline/doctor/lints/runtime.py` | 11 | 0 | 100.0 | 100.0 | orchestrator, backend | Lints for the run itself rather than anything it produced (#493). | 2 |
| `src/unified_pipeline/doctor/shared.py` | 41 | 0 | 100.0 | 98.2 | orchestrator, backend | Primitives every lint needs, regardless of which stage it inspects (#493). | 2 |
| `src/unified_pipeline/segmentation/locked_headers_v6.py` | 1 | 0 | 100.0 | 100.0 | cli, orchestrator, backend | V6: Comprehensive Locked CV Section Headers | 2 |
| `src/unified_pipeline/stage3b/prompt.py` | 37 | 0 | 100.0 | 100.0 | cli, orchestrator, backend | The stage 3b classification prompt (#522). | 2 |
| `src/unified_pipeline/stage5b/normalize.py` | 33 | 0 | 100.0 | 94.1 | cli, orchestrator, backend | Institution-name normalization and location formatting (#523). | 2 |
| `src/unified_pipeline/stage6/formatting/__init__.py` | 3 | 0 | 100.0 | 100.0 | cli, orchestrator, backend | Formatting: how content is made to *look* right in the Word document. | 2 |
| `src/unified_pipeline/stage6/formatting/values.py` | 91 | 0 | 100.0 | 99.3 | cli, orchestrator, backend | Rendering a value as the string that appears on the page (#398). | 2 |
| `src/unified_pipeline/stage6/normalization/__init__.py` | 5 | 0 | 100.0 | 100.0 | cli, orchestrator, backend | Normalization: canonicalising an extracted value before it is rendered. | 2 |
| `src/unified_pipeline/stage6/normalization/citation_matching.py` | 23 | 0 | 100.0 | 100.0 | cli, orchestrator, backend | Is a value already present in a citation's text? (#481) | 2 |
| `src/unified_pipeline/stage6/normalization/fields.py` | 65 | 0 | 100.0 | 100.0 | cli, orchestrator, backend | Coercing a stage-4 field of unpredictable shape to plain cell text (#398). | 2 |
| `src/unified_pipeline/stage6/normalization/publication.py` | 54 | 0 | 100.0 | 100.0 | cli, orchestrator, backend | A raw publication entry in, one resolved record out (#481, #659, PR #737). | 2 |
| `src/unified_pipeline/stage6/normalization/records.py` | 35 | 0 | 100.0 | 100.0 | cli, orchestrator, backend | Correcting a stage-4 record that is not yet in canonical form (#398). | 2 |
| `src/unified_pipeline/stage6/parsing/__init__.py` | 3 | 0 | 100.0 | 100.0 | cli, orchestrator, backend | Parsing: reading structure out of raw text and extracted records. | 2 |
| `src/unified_pipeline/stage6/parsing/dates.py` | 78 | 0 | 100.0 | 100.0 | cli, orchestrator, backend | Reading dates off raw text and off an extracted record (#398). | 2 |
| `src/unified_pipeline/stage6/parsing/records.py` | 12 | 0 | 100.0 | 100.0 | cli, orchestrator, backend | Classifying an extracted record by kind (#398). | 2 |
| `src/unified_pipeline/stage6/render_check.py` | 82 | 0 | 100.0 | 100.0 | cli, orchestrator, backend | Render-check: has this content already reached the rendered output? (#398) | 2 |
| `src/unified_pipeline/stage6/resolution/__init__.py` | 2 | 0 | 100.0 | 100.0 | cli, orchestrator, backend | Resolution: deciding what an incomplete or ambiguous record should say. | 2 |
| `src/unified_pipeline/stage6/sections/__init__.py` | 23 | 0 | 100.0 | 100.0 | cli, orchestrator, backend | One module per WCM template section (#398). | 2 |
| `src/unified_pipeline/stage6/sorting/__init__.py` | 2 | 0 | 100.0 | 100.0 | cli, orchestrator, backend | Sorting: the order records appear in within a section (#398). | 2 |
| `src/unified_pipeline/stage6/sorting/chronological.py` | 18 | 0 | 100.0 | 100.0 | cli, orchestrator, backend | Reverse-chronological ordering, the default for a dated WCM section (#398). | 2 |
| `web_interface/backend/app/audit_events.py` | 8 | 0 | 100.0 | 100.0 | backend | Structured audit event names shared across the auth module (#373). | 2 |
| `web_interface/backend/app/base_class.py` | 2 | 0 | 100.0 | 100.0 | cli, orchestrator, backend | This file does absolutely nothing except hold the Base identity. | 2 |
| `web_interface/backend/app/client_ip.py` | 15 | 0 | 100.0 | 100.0 | backend | Resolve the real client IP from X-Forwarded-For, not uvicorn's leftmost pick. | 2 |
| `web_interface/backend/app/logging_config.py` | 44 | 0 | 100.0 | 100.0 | backend | Application logging configuration. | 2 |
| `web_interface/backend/app/middleware/request_id.py` | 16 | 0 | 100.0 | 100.0 | backend | Request ID middleware. | 2 |
| `web_interface/backend/app/models.py` | 154 | 0 | 100.0 | 100.0 | cli, orchestrator, backend | SQLAlchemy database models. | 2 |
| `web_interface/backend/app/origins.py` | 27 | 0 | 100.0 | 97.1 | backend | The allowed-origin allowlist, and the one predicate that answers it. | 2 |
| `web_interface/backend/app/redirect_safety.py` | 15 | 0 | 100.0 | 100.0 | backend | Open-redirect (CWE-601) safe-target validation for post-auth redirects. | 2 |
| `web_interface/backend/app/schemas.py` | 147 | 0 | 100.0 | 100.0 | backend | Pydantic schemas for API request/response validation. | 2 |
| `web_interface/backend/app/services/admin_service.py` | 19 | 0 | 100.0 | 100.0 | backend | Admin service functions with O(1) aggregation queries. | 2 |
| `web_interface/backend/app/services/consent_service.py` | 20 | 0 | 100.0 | 100.0 | backend | Consent recording service. | 2 |
| `web_interface/backend/app/services/user_service.py` | 37 | 0 | 100.0 | 98.1 | backend | User-related service functions. | 2 |
| `web_interface/backend/app/session_idle.py` | 97 | 0 | 100.0 | 98.3 | backend | Server-side idle session enforcement backed by Valkey/Redis. | 2 |
| `web_interface/backend/app/storage/__init__.py` | 2 | 0 | 100.0 | 100.0 | orchestrator, backend | Storage abstraction layer for CViche run artifacts. | 2 |
| `web_interface/backend/app/storage/base.py` | 18 | 0 | 100.0 | 100.0 | orchestrator, backend | Abstract base class for run storage backends. | 2 |
| `src/unified_pipeline/examples/classify_publications_example.py` | n/a | n/a | n/a | n/a | - | Example: Classify Publications Using Unified System | 3 |
| `src/unified_pipeline/scripts/backfill_pubmed_cache.py` | n/a | n/a | n/a | n/a | - | Backfill PubMed Cache | 3 |
| `src/unified_pipeline/tools/verify_word_document.py` | n/a | n/a | n/a | n/a | - | Word Document Verification Tool | 3 |
| `web_interface/backend/app/config/pipeline_config.py` | n/a | n/a | n/a | n/a | - | Pipeline Configuration Manages feature flags for pipeline execution strategy. | 3 |
| `src/unified_pipeline/core/bulk_pubmed_fetcher.py` | 227 | 227 | 0.0 | 0.0 | - | Bulk PubMed Record Fetcher with Caching | 3 |
| `src/unified_pipeline/core/candidate_surfacer.py` | 67 | 67 | 0.0 | 0.0 | - | LLM-Based Candidate Surfacing for Taxonomy Classification | 3 |
| `src/unified_pipeline/core/hierarchy_overrides.py` | 54 | 54 | 0.0 | 0.0 | - | Hierarchy-based override logic for taxonomy mapping. | 3 |
| `src/unified_pipeline/core/legacy_enrichment_orchestrator.py` | 124 | 124 | 0.0 | 0.0 | - | Legacy Enrichment Orchestrator | 3 |
| `src/unified_pipeline/core/legacy_extractor_orchestrator.py` | 177 | 177 | 0.0 | 0.0 | - | Legacy Extractor Orchestrator | 3 |
| `src/unified_pipeline/core/personal_info_extractor.py` | 131 | 131 | 0.0 | 0.0 | - | Personal Information Extractor - First Page Only | 3 |
| `src/unified_pipeline/core/prompt_analyzer.py` | 230 | 230 | 0.0 | 0.0 | - | Prompt Analyzer - Find patterns in LLM prompt successes and failures | 3 |
| `src/unified_pipeline/core/publication_classification_service.py` | 122 | 122 | 0.0 | 0.0 | - | Publication Classification Service | 3 |
| `src/unified_pipeline/core/s7_validator.py` | 114 | 114 | 0.0 | 0.0 | - | S7 Validation Rules - Prevent Published Articles from Being Labeled as Unpublished | 3 |
| `src/unified_pipeline/core/unified_publication_classifier.py` | 263 | 263 | 0.0 | 0.0 | - | Unified Publication Classifier | 3 |
| `src/unified_pipeline/core/unified_to_classified_converter.py` | 103 | 103 | 0.0 | 0.0 | - | Unified-to-Classified Converter | 3 |
| `src/unified_pipeline/core/valid_taxonomy_codes.py` | 15 | 15 | 0.0 | 0.0 | - | Valid WCM Taxonomy Codes for Enum Validation | 3 |
| `src/unified_pipeline/core/validators/calibration_logger.py` | 34 | 34 | 0.0 | 0.0 | - | Calibration Logger | 3 |
| `src/unified_pipeline/core/validators/validation_checker.py` | 31 | 31 | 0.0 | 0.0 | - | Validation Checker - Post-Validation Safety Net | 3 |
| `src/unified_pipeline/core/wcm_section_extractors.py` | 52 | 52 | 0.0 | 0.0 | - | WCM Section Extractors - Complete Integration | 3 |
| `src/unified_pipeline/core/__init__.py` | 0 | 0 | 100.0 | 100.0 | cli, orchestrator, backend | (no docstring) empty package marker. | 3 |
| `src/unified_pipeline/doctor/__init__.py` | 0 | 0 | 100.0 | 100.0 | orchestrator, backend | run_doctor internals, split by what each lint inspects (#493). | 3 |
| `src/unified_pipeline/doctor/lints/__init__.py` | 0 | 0 | 100.0 | 100.0 | orchestrator, backend | One module per domain a lint inspects. | 3 |
| `src/unified_pipeline/llm/__init__.py` | 0 | 0 | 100.0 | 100.0 | cli, orchestrator, backend | Provider adapters and shared call infrastructure for llm_client.py. | 3 |
| `src/unified_pipeline/segmentation/__init__.py` | 0 | 0 | 100.0 | 100.0 | cli, orchestrator, backend | (no docstring) empty package marker. | 3 |
| `src/unified_pipeline/stage3b/__init__.py` | 0 | 0 | 100.0 | 100.0 | cli, orchestrator, backend | Stage 3b submodules, split by separation of concerns (#522). | 3 |
| `src/unified_pipeline/stage4/__init__.py` | 0 | 0 | 100.0 | 100.0 | cli, orchestrator, backend | Stage 4 submodules, split by separation of concerns (#498). | 3 |
| `src/unified_pipeline/stage5b/__init__.py` | 0 | 0 | 100.0 | 100.0 | cli, orchestrator, backend | Stage 5b submodules, split by separation of concerns (#523). | 3 |
| `src/unified_pipeline/stage6/__init__.py` | 0 | 0 | 100.0 | 100.0 | cli, orchestrator, backend | Stage 6 submodules, split by separation of concerns (#398). | 3 |
| `src/unified_pipeline/utils/__init__.py` | 0 | 0 | 100.0 | 100.0 | - | (no docstring) empty package marker. | 3 |
| `web_interface/backend/app/__init__.py` | 0 | 0 | 100.0 | 100.0 | cli, orchestrator, backend | (no docstring) empty package marker. | 3 |
| `web_interface/backend/app/api/__init__.py` | 0 | 0 | 100.0 | 100.0 | backend | (no docstring) empty package marker. | 3 |
| `web_interface/backend/app/middleware/__init__.py` | 0 | 0 | 100.0 | 100.0 | backend | (no docstring) empty package marker. | 3 |
| `web_interface/backend/app/pipeline/__init__.py` | 0 | 0 | 100.0 | 100.0 | orchestrator, backend | (no docstring) empty package marker. | 3 |
| `web_interface/backend/app/services/__init__.py` | 0 | 0 | 100.0 | 100.0 | orchestrator, backend | Service layer: business logic and data access functions. | 3 |
| `web_interface/backend/app/utils/__init__.py` | 0 | 0 | 100.0 | 100.0 | - | (no docstring) empty package marker. | 3 |
## 5. Per-category summary

Files counts every row in the category, including files not measured by coverage.py (4 files total, listed in section 3); Statements and Stmt % are computed over the measured files only.

| Category | Files | Statements | Stmt % | Files at 0% |
|---|---|---|---|---|
| (unclassified: self-test script, no category) | 3 | 445 | 99.6 | 0 |
| api | 10 | 1,909 | 59.5 | 0 |
| auth | 8 | 844 | 96.6 | 0 |
| config | 4 | 290 | 52.4 | 0 |
| doctor | 7 | 891 | 97.3 | 0 |
| driver | 2 | 1,325 | 71.4 | 0 |
| extraction | 4 | 837 | 54.6 | 0 |
| gate | 4 | 327 | 24.5 | 2 |
| init | 25 | 116 | 100.0 | 0 |
| llm_client | 4 | 259 | 32.0 | 0 |
| model | 2 | 301 | 100.0 | 0 |
| normalization | 1 | 33 | 100.0 | 0 |
| other | 83 | 6,865 | 42.4 | 15 |
| renderer | 40 | 4,527 | 84.0 | 0 |
| segmentation | 3 | 828 | 6.6 | 0 |
| service | 9 | 485 | 87.2 | 0 |
| stage | 11 | 3,793 | 44.2 | 0 |
| storage | 6 | 290 | 76.9 | 0 |
| tooling | 8 | 1,126 | 31.1 | 4 |

## 6. How to reproduce, and caveats

### How to reproduce

```
git fetch origin
TIP=$(git rev-parse origin/dev)
SCR=$(mktemp -d)
git archive $TIP | tar -x -C $SCR
# then run the pipeline-suite and backend-suite command blocks from section 1 inside $SCR
```

The reachability analysis (section 1) is a static import-closure script over the same checkout; it is not checked into the repository. Reproducing it means re-parsing every non-test `.py` file under `src/unified_pipeline/`, `scripts/`, `web_interface/backend/app/`, and `run_full_pipeline.py` for `import`/`from ... import ...` statements, resolving each to a file using the module search order described in section 1, and building a breadth-first closure from each of the three entry points.

### Caveats (what these numbers do not say)

- **Executed is not asserted.** A statement counted as "covered" only means a test ran that line, not that a test checked the line produced the right output. #643 is a real example of this failing silently: an integration test passed while the code path it existed to cover was completely broken, because the assertion was satisfied by an unrelated fallback route. A high statement percentage in the table above is not evidence the tested behavior is correct.
- **Statement % and blended % are not interchangeable and both are quoted for a reason.** Blended folds in branch-partial coverage and is always the more conservative (lower) of the two; cite one and say which.
- **The pipeline-suite determinism check, with `PYTHONHASHSEED=0` pinned as the commands in section 1 now show**, found 0 files differing (statements/missing/branches) between two independent runs; the backend suite was not re-run a second time to check the same, so its number reflects a single run. Without the seed pinned, `src/unified_pipeline/core/validators/hierarchy_mismatch_flagger.py` lines 178-190 execute under some interpreter hash seeds and not others (5 statements: 178, 181, 184, 187, 190), because the module iterates a `set()` whose element order the hash seed controls -- one of two totals results for that file (28 or 23 missing) and for the pipeline-wide raw total (9,395 or 9,400 missed), a set-ordering dependence in that module worth a look on its own, separate from this report's numbers. A second, smaller effect in the same family: `src/unified_pipeline/llm/retry.py`'s blended percent depends on import order (43.8 or 42.5), because of the `sys.path` guard at line 34 (`if str(BACKEND_ROOT) not in sys.path:`) -- statement coverage for that file is unaffected either way.
- **4 files are structurally invisible to coverage.py**, because their directory has no `__init__.py` and coverage.py's unexecuted-file scan only walks real packages. They carry no measured statements/missed counts, are excluded from every total and reachable-only sum in section 2, and are listed by name in section 3 instead. (`run_full_pipeline.py` was previously in this set on the strength of a `--source` argument that named it by file path rather than module name; corrected, it is measured like any other file and appears in the main table with real statement/missed/branch counts, not a static-parse estimate.)
- **3 files sit outside the reachability classification by design**: `scripts/test_check_function_size.py`, `scripts/test_check_standards.py`, and `scripts/test_render_doctor_gates.py` are the pipeline suite's own self-test scripts, measured because they were run directly under `coverage run`, but excluded from the import-closure inventory as test files rather than source modules. They carry no category and no reachable-from list; each is treated as tier 2 ("entry point, run by CI") on that basis alone, and the per-category table above groups them as "(unclassified: self-test script, no category)".
- **Two `core/validators/*` files are dead by two independent methods**: no import edge from the closure analysis, and a literal text search across every non-test file for their module name also finds nothing -- `calibration_logger.py` and `validation_checker.py`. This is the strongest unreachable finding in this report; every other tier-3 file rests on the import-graph analysis alone (each was re-checked with the same literal text search after the reachability fix described in section 1, and none produced a hit outside a small, already-dead cluster of publication-classification files that only import each other).
- **This analysis is static, not runtime-traced.** An import built from a computed or dynamically-assembled module name, rather than a literal string, will not be modeled and could misclassify a file as unreachable. No such import was found in scope during this pass, which is evidence against it happening here, but is not a runtime-verified guarantee.
- **Tiers are mechanical, not final.** They follow the stated rubric row by row; a person closing out specific files should still sanity-check the category and reachability columns against the rubric before acting on tier 1.
- **No PII**: file paths only. No corpus identifiers or CV content were read or needed to produce this document.
