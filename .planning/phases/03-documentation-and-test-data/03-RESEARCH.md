# Phase 3: Documentation and Test Data - Research

**Researched:** 2026-03-22
**Domain:** Project documentation, synthetic test data generation, versioning
**Confidence:** HIGH

## Summary

Phase 3 is a documentation and data authoring phase with no library installation or architectural changes. The work involves creating five new files (README.md, LICENSE, CHANGELOG.md, a synthetic .docx CV, and a `__version__` variable), reorganizing the docs/ directory to separate user-facing guides from internal design specs, adding a .gitignore negation rule for the sample CV, and creating a docs/images/ directory for README screenshots.

All technology required is already installed: python-docx 1.2.0 is in the project's requirements.txt and available locally. The Apache 2.0 license is a fixed legal text from apache.org. The CHANGELOG follows the Keep a Changelog specification (keepachangelog.com). The synthetic CV must be a .docx file that the pipeline can actually process, placed in `data/sample_cvs/word/` with a .gitignore negation rule.

**Primary recommendation:** Treat this as a content-authoring phase with one technical task (synthetic .docx generation via python-docx script). All other deliverables are straightforward file creation and git operations. The docs reorganization (moving ~46 files from docs/ to .planning/docs/) is the task with the highest risk of broken cross-references and should be verified after execution.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- Unified README.md covering both CLI pipeline and web interface in separate sections
- Primary audience: WCM team (colleagues); external users secondary
- Include "What it does" section with brief description + example CLI command
- Include before/after visual: side-by-side screenshots of sample input CV vs output WCM doc
- Include numbered stage list with brief one-line descriptions of each of the 12 stages
- Include screenshot of web interface (pipeline viewer + upload page)
- OpenAI API key setup: env var instruction only (`export OPENAI_API_KEY=your-key-here`) with link to OpenAI docs
- License section at bottom referencing Apache 2.0 LICENSE file (no contributing guide for initial release)
- Versioning policy section in README (major/minor/patch semantics -- architecture/model switches/prompt tuning)
- Reference both requirements.txt files (pipeline + web interface)
- Synthetic CV: mid-career physician-scientist profile (Associate Professor level)
- Comprehensive section coverage: education, positions, publications, grants, teaching, mentoring, service, honors, licensure, memberships, certifications (~15+ sections)
- Real journal names with fabricated article titles and co-authors (enables realistic PubMed enrichment testing)
- File format: .docx; Location: `data/sample_cvs/` with .gitignore negation rule
- Keep a Changelog format (keepachangelog.com) with Added/Changed/Fixed/Removed sections
- v1.0.0 entry: feature summary by category (12-stage pipeline, web interface, auth system, PubMed enrichment, etc.)
- `__version__ = '1.0.0'` in `src/unified_pipeline/__init__.py`
- Move to `.planning/docs/`: all design specs, research papers, evaluation docs, MULTI_INSTITUTION_SUPPORT.md, development/, superpowers/
- Keep in `docs/`: guides/, wcm_sections/, PIPELINE_README.md
- Store README images in `docs/images/`
- .gitignore update: add negation rule for sample CV in data/sample_cvs/

### Claude's Discretion
- Exact README section ordering and headings
- Sample CV fabricated person's name and biographical details
- Exact CHANGELOG wording for v1.0.0 feature categories
- Screenshot capture methodology and image dimensions
- Which docs/ files need renaming during reorganization

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| DOC-01 | README.md with setup instructions -- clone, install deps, configure OpenAI key, run pipeline CLI, run web interface | README structure, content sources (run_full_pipeline.py docstring, STACK.md env vars, docker-compose.yml), stage list from pipeline entry point |
| DOC-02 | Apache 2.0 LICENSE file at repository root | Official license text from apache.org/licenses/LICENSE-2.0.txt, standard filename convention |
| DOC-03 | CHANGELOG.md starting from v1.0.0 summarizing current capabilities | Keep a Changelog format spec, v1.0.0 tag date (2026-03-22), feature inventory from STACK.md and STRUCTURE.md |
| VER-01 | Semantic versioning policy documented -- major = architecture, minor = model/stages, patch = prompt/bugs | README versioning section, aligned with PROJECT.md key decisions |
| VER-03 | `__version__` variable accessible in the package | Empty `src/unified_pipeline/__init__.py` exists, standard Python `__version__` pattern |
| TEST-01 | Synthetic sample CV (.docx) included in repo for pipeline testing | python-docx 1.2.0 for generation, WCM taxonomy sections A-T for content design, .gitignore negation pattern, `data/sample_cvs/word/` as expected input location |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| python-docx | 1.2.0 | Generate synthetic .docx CV | Already in project requirements.txt; the pipeline reads .docx via this same library |

### Supporting
No additional libraries needed. This phase creates static files (markdown, legal text, .docx) using existing tools.

### Alternatives Considered
None. All tools are already in the project.

**Installation:**
No new packages required. `python-docx==1.2.0` is already in `requirements.txt`.

## Architecture Patterns

### File Creation Map
```
CViche/
├── README.md                          # NEW - project documentation
├── LICENSE                            # NEW - Apache 2.0 full text
├── CHANGELOG.md                       # NEW - Keep a Changelog format
├── src/unified_pipeline/__init__.py   # MODIFY - add __version__ = '1.0.0'
├── data/sample_cvs/word/
│   └── sample_cv.docx                 # NEW - synthetic CV (exact name TBD)
├── docs/
│   ├── images/                        # NEW directory for README screenshots
│   │   ├── pipeline-viewer.png        # Screenshot of web UI pipeline progress
│   │   ├── upload-page.png            # Screenshot of web UI upload page
│   │   └── before-after.png           # Input CV vs output WCM doc comparison
│   ├── PIPELINE_README.md             # STAYS
│   ├── guides/                        # STAYS (4 files)
│   └── wcm_sections/                  # STAYS (1 file)
├── .gitignore                         # MODIFY - add negation rule for sample CV
└── .planning/
    └── docs/                          # NEW directory
        ├── STAGE3_TAXONOMY_ARCHITECTURE.md      # MOVED from docs/
        ├── STAGE_2_ENTRY_EXTRACTION_DESIGN.md   # MOVED from docs/
        ├── STAGE_2_SYSTEM_PROMPT.md             # MOVED from docs/
        ├── ENTRY_EXTRACTION_PROMPT_SPEC.md      # MOVED from docs/
        ├── STAGE_EVALUATION_PROMPTS.md          # MOVED from docs/
        ├── EFFECTIVENESS_ASSESSMENT.md          # MOVED from docs/
        ├── MULTI_INSTITUTION_SUPPORT.md         # MOVED from docs/
        ├── research_*.md (3 files)              # MOVED from docs/
        ├── structured_output_research_*.md      # MOVED from docs/
        ├── development/                         # MOVED from docs/
        └── superpowers/                         # MOVED from docs/
```

### Pattern 1: .gitignore Negation Rule for Sample CV

**What:** The `data/` directory is gitignored (contains real CVs = PII). The synthetic sample CV must be tracked via a negation rule.
**When to use:** Any time a single file inside a gitignored directory must be tracked.
**Example:**
```gitignore
# Data and CVs (PII)
data/
!data/sample_cvs/
!data/sample_cvs/word/
!data/sample_cvs/word/sample_cv.docx
```
**Critical detail:** Git requires negating each parent directory in the path. Just `!data/sample_cvs/word/sample_cv.docx` alone will NOT work because `data/` is ignored and git never looks inside. You must negate `data/sample_cvs/` and `data/sample_cvs/word/` too. This follows the established pattern in the project where `key_files/` is ignored but `!key_files/wcm_cv_template_faculty_october_2022_final.docx` is negated (that pattern works because the negation is for a direct child of the ignored directory).

### Pattern 2: Synthetic CV Structure

**What:** A python-docx script generates a .docx with Word headings and paragraphs mimicking a real academic CV.
**When to use:** Creating the sample CV for TEST-01.
**Example:**
```python
from docx import Document

doc = Document()
doc.add_heading('Curriculum Vitae', level=0)
doc.add_heading('Personal Data', level=1)
doc.add_paragraph('Name: Dr. Elena M. Vasquez, MD, PhD')
doc.add_paragraph('Title: Associate Professor of Medicine')
# ... sections for Education, Positions, Publications, etc.
doc.save('data/sample_cvs/word/sample_cv.docx')
```

**Key structural requirement:** The synthetic CV must use Word heading styles (Heading 1, Heading 2) because Stage 1a extracts hierarchy from Word formatting. Plain text paragraphs that look like headings but lack heading styles will confuse the pipeline.

### Pattern 3: Keep a Changelog Format

**What:** CHANGELOG.md following keepachangelog.com specification.
**Structure:**
```markdown
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.0.0] - 2026-03-22

### Added
- 12-stage CV processing pipeline (segmentation through Word output)
- Web interface with real-time pipeline progress viewer
- ...
```

### Anti-Patterns to Avoid
- **Putting the sample CV in a non-standard location:** The pipeline's `resolve_cv_path()` function looks in `data/sample_cvs/word/` by default. Putting the file elsewhere breaks the simple UID-based invocation (`python3 run_full_pipeline.py sample_cv`).
- **Using plain text instead of Word heading styles:** The pipeline's segmentation stage (Stage 1a) relies on Word document structure (heading levels) to extract the hierarchy. A .docx with only normal paragraphs will produce poor segmentation results.
- **Incomplete .gitignore negation chain:** Negating only the leaf file without negating parent directories means git never descends into the ignored parent. Must negate each directory level.
- **Moving docs without checking for cross-references:** The file `docs/STAGE3_TAXONOMY_ARCHITECTURE.md` has a relative link to `PIPELINE_README.md`. After moving to `.planning/docs/`, this link breaks. All moved files must be checked for internal cross-references to staying files.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| .docx generation | Manual XML construction | python-docx `Document()` API | python-docx handles the complex OPC/XML packaging; already a project dependency |
| License text | Write your own license | Copy verbatim from apache.org/licenses/LICENSE-2.0.txt | Legal text must be exact; no modifications allowed |
| Changelog format | Invent a format | Keep a Changelog (keepachangelog.com) | Standardized, widely recognized, tooling-compatible |

**Key insight:** This phase has no complex engineering problems. The risk is in content accuracy (license text, CV realism, instruction correctness) and operational correctness (gitignore negation, cross-reference updates, file moves).

## Common Pitfalls

### Pitfall 1: .gitignore Negation Doesn't Work
**What goes wrong:** Sample CV added to `data/sample_cvs/word/` but `git add` refuses to track it.
**Why it happens:** The `data/` gitignore rule prevents git from descending into the directory. A negation of a deeply nested file requires negating every parent directory in the chain.
**How to avoid:** Add all three negation lines (data/sample_cvs/, data/sample_cvs/word/, and the specific file) immediately after the `data/` rule.
**Warning signs:** `git status` shows nothing when the file is clearly there; `git add data/sample_cvs/word/file.docx` silently does nothing.

### Pitfall 2: Synthetic CV Doesn't Exercise All Pipeline Stages
**What goes wrong:** The sample CV processes through some stages but fails or produces empty output on others (e.g., no publications means Stage 5 PubMed enrichment has nothing to do).
**Why it happens:** The CV omits sections that specific stages need as input.
**How to avoid:** Design the CV to include content for every major WCM taxonomy category, especially:
  - Publications with real journal names (for Stage 5 PubMed enrichment)
  - Multiple position entries (for Stage 5b institution enrichment via ROR)
  - Teaching/mentoring entries (for Stage 5c teaching formatter)
  - Non-enriched citations (for Stage 5d citation formatter)
**Warning signs:** Pipeline completes but Stage 5/5b/5c/5d produce empty or trivially small output.

### Pitfall 3: Broken Cross-References After Docs Reorganization
**What goes wrong:** Links between documentation files break after moving files from docs/ to .planning/docs/.
**Why it happens:** Relative markdown links (e.g., `[PIPELINE_README](PIPELINE_README.md)`) no longer resolve when the linking file moves.
**How to avoid:** Before moving files, grep for all internal cross-references. After moving, update any broken links. The known cross-reference is in `STAGE3_TAXONOMY_ARCHITECTURE.md` line 3 which links to `PIPELINE_README.md`.
**Warning signs:** Rendered markdown shows broken links or raw `[text](path)` instead of clickable links.

### Pitfall 4: README Instructions That Don't Actually Work
**What goes wrong:** A new user follows the README step by step and hits errors not mentioned in the docs.
**Why it happens:** The author knows implicit prerequisites (Python version, system deps like poppler-utils, environment variable names) that aren't documented.
**How to avoid:** Include all prerequisites explicitly:
  - Python 3.11+
  - `poppler-utils` system package (for pdf2image, used in PDF fallback)
  - OpenAI API key (with the env var name: `OPENAI_API_KEY`)
  - For web interface: Docker and Docker Compose
**Warning signs:** The README says "install dependencies" but doesn't mention system-level packages.

### Pitfall 5: Screenshot Images Not Committed
**What goes wrong:** README references images in `docs/images/` but the images aren't tracked in git.
**Why it happens:** The `docs/images/` directory is new and might be forgotten during staging, or image files might match a gitignore pattern.
**How to avoid:** Verify no gitignore patterns match PNG files in docs/images/. Explicitly `git add docs/images/` after creating screenshots. Check `git status` shows the images.
**Warning signs:** README renders with broken image placeholders on GitHub.

## Code Examples

### Synthetic CV Generation Script
```python
# Source: python-docx 1.2.0 official quickstart docs
from docx import Document
from docx.shared import Pt, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH

doc = Document()

# Title -- uses Heading 0 which maps to "Title" style
doc.add_heading('Curriculum Vitae', level=0)

# Personal Data section -- Heading 1 for major sections
doc.add_heading('Personal Data', level=1)
p = doc.add_paragraph()
p.add_run('Elena M. Vasquez, MD, PhD').bold = True
doc.add_paragraph('Associate Professor of Medicine')
doc.add_paragraph('Department of Medicine, Division of Cardiology')
doc.add_paragraph('Weill Cornell Medicine')
# ... more personal data

# Education section
doc.add_heading('Education', level=1)
doc.add_heading('Degrees', level=2)
doc.add_paragraph('MD, Universidad de Buenos Aires, Buenos Aires, Argentina, 2005')
doc.add_paragraph('PhD, Molecular Cardiology, Columbia University, New York, NY, 2010')

# Publications section -- critical for PubMed enrichment testing
doc.add_heading('Bibliography', level=1)
doc.add_heading('Peer-Reviewed Publications', level=2)
# Use real journal names with fabricated titles/authors
doc.add_paragraph('1. Vasquez EM, Chen R, Patel S. '
    'Cardiac fibroblast signaling in pressure overload models. '
    'J Am Coll Cardiol. 2018;71(4):412-423.')

doc.save('data/sample_cvs/word/sample_vasquez_cv.docx')
```

### __version__ in __init__.py
```python
# Source: Standard Python packaging convention (PEP 396)
# File: src/unified_pipeline/__init__.py
__version__ = '1.0.0'
```

### .gitignore Negation Pattern
```gitignore
# Data and CVs (PII)
data/
!data/sample_cvs/
!data/sample_cvs/word/
!data/sample_cvs/word/sample_vasquez_cv.docx
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| No README | Full README with setup instructions | Phase 3 | New users can onboard without external guidance |
| No license | Apache 2.0 | Phase 3 | Legal clarity for institutional adoption |
| No changelog | Keep a Changelog format | Phase 3 | Version history visible to users |
| No sample data | Synthetic CV in repo | Phase 3 | Pipeline can be demo'd without real PII data |
| All docs in docs/ | User-facing in docs/, internal in .planning/docs/ | Phase 3 | Cleaner separation for public repo consumers |

## Existing Content Inventory

### Content Sources for README
The README doesn't need to be written from scratch. These existing files contain the raw content:

| README Section | Source | Location |
|----------------|--------|----------|
| Stage list (12 stages) | Pipeline docstring | `run_full_pipeline.py` lines 6-18 |
| CLI usage examples | argparse help | `run_full_pipeline.py` lines 20-51 |
| Pipeline dependencies | requirements.txt | `requirements.txt` (8 pinned packages) |
| Web interface dependencies | requirements.txt | `web_interface/backend/requirements.txt` (17 packages) |
| Environment variables | Stack analysis | `.planning/codebase/STACK.md` Environment Variables section |
| Docker setup | compose file | `web_interface/docker-compose.yml` |
| Architecture overview | Pipeline README | `docs/PIPELINE_README.md` (ASCII diagram) |
| WCM taxonomy overview | Taxonomy guide | `docs/wcm_sections/WCM_STRUCTURE_GUIDE.md` |

### Files to Move from docs/ to .planning/docs/ (46 files)
Based on CONTEXT.md decisions and current git-tracked file inventory:

**Design specs (8 files):**
- `STAGE3_TAXONOMY_ARCHITECTURE.md` -- has cross-ref to PIPELINE_README.md (needs update)
- `STAGE_2_ENTRY_EXTRACTION_DESIGN.md`
- `STAGE_2_SYSTEM_PROMPT.md`
- `ENTRY_EXTRACTION_PROMPT_SPEC.md`
- `STAGE_EVALUATION_PROMPTS.md`
- `EFFECTIVENESS_ASSESSMENT.md`
- `MULTI_INSTITUTION_SUPPORT.md`
- `structured_output_research_2026-03-19.md`

**Research papers (3 files):**
- `research_agentic_document_processing_2026.md`
- `research_document_segmentation_classification_2026.md`
- `research_structured_extraction_landscape_2026.md`

**Development directory (all contents, ~30 files):**
- `development/README.md`
- `development/fixes/` (4 files)
- `development/history/` (~18 files)
- `development/testing/` (2 files)

**Superpowers directory (all contents, ~13 files):**
- `superpowers/plans/` (11 files)
- `superpowers/specs/` (2 files)

**Files staying in docs/ (6 files):**
- `PIPELINE_README.md`
- `guides/INTEGRATION_COMPLETE.md`
- `guides/WEB_APP_INTEGRATION.md`
- `guides/visual_enhancements.md`
- `guides/word_segmentation_production.md`
- `wcm_sections/WCM_STRUCTURE_GUIDE.md`

### Cross-References to Update After Move
Known internal cross-reference that will break:
- `docs/STAGE3_TAXONOMY_ARCHITECTURE.md` line 3: `[PIPELINE_README.md](PIPELINE_README.md)` -- after move to `.planning/docs/`, must become `../../docs/PIPELINE_README.md` or similar

### v1.0.0 Tag Context
- Tag exists: `v1.0.0` (annotated tag)
- Tag date: 2026-03-22
- Tag message: "v1.0.0 - Initial public release"
- CHANGELOG v1.0.0 entry should use date 2026-03-22

### WCM Taxonomy Sections for Synthetic CV Design
The WCM template defines 20 primary sections (A-T) with 66 subsections. For a comprehensive mid-career physician-scientist, the synthetic CV should include entries in at least these:

| Section | Code | Content Needed |
|---------|------|---------------|
| Personal Data | A | Name, title, department, institution, email, ORCID |
| Education/Degrees | B1 | MD + PhD with institutions, dates |
| Other Education | B2 | Certificate program |
| Postdoctoral Training | C | Research fellowship |
| Professional Positions | D1 | 3-4 positions showing career progression |
| Licensure | F1 | Medical license |
| Board Certification | F2 | Board certification |
| Honors & Awards | H | 3-4 awards |
| Professional Organizations | I | 2-3 memberships |
| Teaching | K1a-K1c | Courses taught, lectures given |
| Mentoring | N | Mentees supervised |
| Service/Committees | O, P, Q | Committee service entries |
| Speaking | R | Invited talks |
| Publications | S1a | 8-12 fabricated pubs with real journal names |
| Grants | M2a-M2b | 2-3 grants (current + completed) |

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | None configured (no pytest.ini, pyproject.toml, or conftest.py) |
| Config file | None -- Wave 0 gap |
| Quick run command | N/A for this phase |
| Full suite command | N/A for this phase |

### Phase Requirements to Test Map

This phase's requirements are documentation and file creation tasks. They are best validated by file existence checks and content verification rather than unit tests.

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| DOC-01 | README.md exists with required sections | smoke | `test -f README.md && grep -q "Setup" README.md && grep -q "Pipeline" README.md` | N/A (file check) |
| DOC-02 | LICENSE file exists with Apache 2.0 text | smoke | `test -f LICENSE && grep -q "Apache License" LICENSE` | N/A (file check) |
| DOC-03 | CHANGELOG.md exists with v1.0.0 entry | smoke | `test -f CHANGELOG.md && grep -q "1.0.0" CHANGELOG.md` | N/A (file check) |
| VER-01 | Versioning policy in README | smoke | `grep -q "versioning" README.md` (case-insensitive) | N/A (content check) |
| VER-03 | `__version__` accessible in package | unit | `python3 -c "import sys; sys.path.insert(0,'src'); from unified_pipeline import __version__; assert __version__ == '1.0.0'"` | Exists (empty) |
| TEST-01 | Synthetic CV processable by pipeline | integration | `python3 run_full_pipeline.py sample_vasquez_cv --stage 1a` (requires OpenAI key) | Not yet |

### Sampling Rate
- **Per task commit:** File existence checks (shell commands above)
- **Per wave merge:** All smoke checks pass
- **Phase gate:** All file checks pass + synthetic CV confirmed processable (manual with OpenAI key)

### Wave 0 Gaps
- [ ] No test framework configured -- but not needed for this phase (file existence checks suffice)
- [ ] Synthetic CV pipeline test requires OpenAI API key -- manual verification needed

## Open Questions

1. **Screenshot capture methodology**
   - What we know: README should include screenshots of web interface (pipeline viewer, upload page) and before/after CV comparison
   - What's unclear: Whether to use Playwright MCP to capture screenshots, manual capture, or placeholder images initially
   - Recommendation: Use Playwright MCP if the web interface can be started in the dev environment; otherwise use placeholder text noting "screenshot pending" and capture in a follow-up task

2. **Exact sample CV filename**
   - What we know: Goes in `data/sample_cvs/word/` as a .docx
   - What's unclear: The exact filename (affects the .gitignore negation rule and README examples)
   - Recommendation: Use a descriptive name like `sample_vasquez_cv.docx` that matches the fabricated person's name, following the existing `{uid}_*.docx` pattern

3. **Docs reorganization and git history**
   - What we know: Files move from docs/ to .planning/docs/
   - What's unclear: Whether `git mv` preserves file history through the move
   - Recommendation: Use `git mv` for each file to preserve history tracking. Git handles renames/moves via content similarity detection regardless, but `git mv` makes the intent clearer.

## Sources

### Primary (HIGH confidence)
- Project codebase analysis: `.planning/codebase/STRUCTURE.md`, `STACK.md`, `CONVENTIONS.md`
- Pipeline entry point: `run_full_pipeline.py` (stage list, CLI usage, file resolution logic)
- Existing .gitignore (negation pattern established for key_files/)
- v1.0.0 tag metadata (date, message confirmed via `git show v1.0.0`)
- python-docx 1.2.0 verified installed locally

### Secondary (MEDIUM confidence)
- [Keep a Changelog specification](https://keepachangelog.com/en/1.0.0/) -- format verified via official site
- [Apache License 2.0 official text](https://www.apache.org/licenses/LICENSE-2.0.txt) -- verbatim legal text
- [python-docx quickstart documentation](https://python-docx.readthedocs.io/en/latest/user/quickstart.html) -- API for Document/heading/paragraph creation

### Tertiary (LOW confidence)
- None. All findings verified from primary or official sources.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new libraries needed, python-docx already installed and version confirmed
- Architecture: HIGH -- file creation and git operations, patterns already established in project
- Pitfalls: HIGH -- gitignore negation, cross-references, and pipeline compatibility are well-understood from codebase analysis
- Content: MEDIUM -- synthetic CV design requires judgment about realism and section coverage; README instructions should be smoke-tested

**Research date:** 2026-03-22
**Valid until:** 2026-04-22 (stable -- documentation standards don't change rapidly)
