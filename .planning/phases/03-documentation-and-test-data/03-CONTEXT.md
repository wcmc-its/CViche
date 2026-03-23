# Phase 3: Documentation and Test Data - Context

**Gathered:** 2026-03-22
**Status:** Ready for planning

<domain>
## Phase Boundary

Create all documentation files (README.md, LICENSE, CHANGELOG.md) and a synthetic sample CV so a new user can clone the repo, understand what CViche does, set it up, and run the pipeline against a sample CV without external guidance. Also establish the `__version__` variable and versioning policy. Reorganize docs/ to separate user-facing guides from internal design specs.

</domain>

<decisions>
## Implementation Decisions

### README structure
- Unified README.md covering both CLI pipeline and web interface in separate sections
- Primary audience: WCM team (colleagues who need to set up and run CViche locally); external users are secondary
- Include a "What it does" section with brief description + example CLI command
- Include a before/after visual: side-by-side screenshots of sample input CV vs output WCM doc
- Include a numbered stage list with brief one-line descriptions of each of the 12 stages
- Include screenshot of web interface (pipeline viewer + upload page)
- OpenAI API key setup: env var instruction only (`export OPENAI_API_KEY=your-key-here`) with link to OpenAI docs
- License section at bottom referencing Apache 2.0 LICENSE file (no contributing guide for initial release)
- Versioning policy section in README (major/minor/patch semantics — architecture/model switches/prompt tuning)
- Reference both requirements.txt files (pipeline + web interface)

### Synthetic CV design
- Mid-career physician-scientist profile (Associate Professor level)
- Comprehensive section coverage: education, positions, publications, grants, teaching, mentoring, service, honors, licensure, memberships, certifications (~15+ sections)
- Real journal names with fabricated article titles and co-authors (enables realistic PubMed enrichment testing)
- File format: .docx
- Location: `data/sample_cvs/` with .gitignore negation rule to track it (same pattern as key_files/ negation)

### CHANGELOG and versioning
- Keep a Changelog format (keepachangelog.com) with Added/Changed/Fixed/Removed sections
- v1.0.0 entry: feature summary by category (12-stage pipeline, web interface, auth system, PubMed enrichment, etc.)
- `__version__ = '1.0.0'` in `src/unified_pipeline/__init__.py` (standard Python pattern)

### Documentation reorganization
- Move to `.planning/docs/`: all design specs (STAGE3_TAXONOMY_ARCHITECTURE.md, STAGE_2_*.md, ENTRY_EXTRACTION_PROMPT_SPEC.md), research papers (research_*.md, structured_output_research_*.md), evaluation docs (STAGE_EVALUATION_PROMPTS.md, EFFECTIVENESS_ASSESSMENT.md), MULTI_INSTITUTION_SUPPORT.md, development/, superpowers/
- Keep in `docs/`: guides/, wcm_sections/, PIPELINE_README.md
- .gitignore update: add negation rule for sample CV in data/sample_cvs/

### Screenshots and visuals
- Store all README images in `docs/images/`
- Web interface screenshots: pipeline viewer (real-time stage progress) + upload page
- Before/after: side-by-side screenshots of sample input CV page 1 vs output WCM doc page 1
- Reference images with relative paths from README

### Claude's Discretion
- Exact README section ordering and headings
- Sample CV fabricated person's name and biographical details
- Exact CHANGELOG wording for v1.0.0 feature categories
- Screenshot capture methodology and image dimensions
- Which docs/ files need renaming during reorganization

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project context
- `.planning/PROJECT.md` — Core value (no PII), constraints, key decisions (Apache 2.0, semantic versioning, synthetic CV)
- `.planning/REQUIREMENTS.md` — DOC-01, DOC-02, DOC-03, VER-01, VER-03, TEST-01 are Phase 3 requirements

### Prior phase context
- `.planning/phases/01-working-tree-sanitization/01-CONTEXT.md` — .gitignore structure, requirements.txt strategy, auth_config.yaml.example pattern
- `.planning/phases/02-history-rewrite-and-tagging/02-CONTEXT.md` — v1.0.0 tag decision, history rewrite details

### Codebase analysis
- `.planning/codebase/STRUCTURE.md` — Directory layout, file purposes, entry points
- `.planning/codebase/STACK.md` — Technology stack details for README content
- `.planning/codebase/CONVENTIONS.md` — Naming conventions, patterns to document

### Pipeline reference
- `run_full_pipeline.py` — CLI entry point, stage names and descriptions (source of truth for README stage list)
- `docs/PIPELINE_README.md` — Existing stage guide to cross-reference

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `run_full_pipeline.py` docstring: Contains complete stage list with descriptions (lines 3-28) — can be adapted for README stage overview
- `requirements.txt`: Already exists with 8 pinned dependencies — README references this
- `web_interface/backend/requirements.txt`: 17 packages (unpinned) — README references this
- `docs/PIPELINE_README.md`: Existing pipeline documentation that can inform README content

### Established Patterns
- `src/unified_pipeline/__init__.py` exists but is empty — natural place for `__version__`
- `.gitignore` uses negation rules (e.g., `!key_files/cv_template_wcm.docx`) — same pattern for sample CV
- `data/sample_cvs/` directory structure exists with `pdf/` and `word/` subdirectories

### Integration Points
- `.gitignore` needs negation rule added for the synthetic sample CV
- `v1.0.0` tag already exists — CHANGELOG references this tag
- `docs/` directory reorganization requires updating any internal cross-references between docs

</code_context>

<specifics>
## Specific Ideas

- Before/after visual in README: side-by-side screenshot comparison showing the transformation from unstructured CV to WCM-formatted output
- Web interface screenshots: capture pipeline viewer showing real-time stage progress, and the upload page as entry point
- Sample CV should exercise all pipeline stages to serve as a comprehensive demo
- README should feel professional enough for eventual adoption by other institutions

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 03-documentation-and-test-data*
*Context gathered: 2026-03-22*
