---
phase: 3
slug: documentation-and-test-data
status: draft
shadcn_initialized: false
preset: none
created: 2026-03-22
---

# Phase 3 -- UI Design Contract

> Visual and content contract for a documentation-only phase. No interactive UI components are created or modified. The visual deliverables are README screenshots, README markdown layout, and a synthetic .docx CV document.

---

## Phase Classification

| Property | Value |
|----------|-------|
| Phase type | Documentation and content authoring |
| Frontend changes | None -- existing web interface is not modified |
| Visual deliverables | 3 screenshots for README, 1 synthetic .docx CV |
| Interactive deliverables | None |
| Design system impact | None |

**Why a reduced contract:** This phase creates markdown files (README, CHANGELOG, LICENSE), a python-docx generated Word document, and static screenshot images. The standard spacing/typography/color/registry sections apply to frontend component work and are not applicable here. The contract below covers the visual decisions that ARE needed: screenshot specifications, README image layout, and .docx document formatting.

---

## Design System

| Property | Value |
|----------|-------|
| Tool | none |
| Preset | not applicable |
| Component library | not applicable (no frontend work) |
| Icon library | not applicable |
| Font | not applicable (README uses GitHub's markdown renderer; .docx uses Word default styles) |

---

## Screenshot Specifications

Three screenshots are required for README.md (source: CONTEXT.md locked decisions).

### Image Dimensions and Format

| Property | Value |
|----------|-------|
| Format | PNG |
| Max width | 1200px (GitHub renders markdown images at max ~800px; 1200px source ensures Retina clarity) |
| Viewport for capture | 1280x800 (standard laptop viewport) |
| Color depth | 24-bit RGB |
| Compression | PNG default (lossless) |
| Storage path | `docs/images/` |

### Screenshot 1: Pipeline Viewer

| Property | Value |
|----------|-------|
| Filename | `pipeline-viewer.png` |
| Content | Web interface pipeline viewer showing real-time stage progress with multiple stages completed and one in-progress |
| Capture state | Pipeline mid-run (stages 1-6 complete, stage 7 in progress, stages 8-12 pending) -- shows the animated progress bar and status indicators |
| Viewport crop | Full page width, cropped to show the pipeline viewer component only (exclude browser chrome) |
| Expected height | 400-600px depending on stage list length |

### Screenshot 2: Upload Page

| Property | Value |
|----------|-------|
| Filename | `upload-page.png` |
| Content | Web interface upload page showing the file upload area and form controls |
| Capture state | Empty state -- no file selected, showing the upload prompt |
| Viewport crop | Full page width, cropped to show the upload area and header |
| Expected height | 300-500px |

### Screenshot 3: Before/After Comparison

| Property | Value |
|----------|-------|
| Filename | `before-after.png` |
| Content | Side-by-side comparison of page 1 of the sample input CV (left) and page 1 of the WCM-formatted output document (right) |
| Layout | Two images placed side-by-side with a vertical divider or labeled columns ("Input CV" / "WCM Output") |
| Source material | The synthetic sample CV (.docx) as input; the pipeline-generated WCM output as output |
| Capture method | Open both .docx files, screenshot page 1 of each, compose side-by-side |
| Expected dimensions | 1200px wide (600px per side), 700-900px tall |
| Label font | 16px semibold, dark gray (#374151), centered above each half |
| Label text left | "Input CV" |
| Label text right | "WCM Output" |

### Capture Methodology

Use Playwright MCP to capture web interface screenshots if the dev server can be started. For the before/after .docx comparison, use manual capture or a document preview tool. If screenshots cannot be captured during this phase (e.g., dev server issues), insert placeholder text in the README:

```markdown
<!-- Screenshot: [description] -- TODO: capture when dev server is running -->
```

---

## README Markdown Layout

The README is rendered by GitHub's markdown engine. These specifications ensure consistent visual presentation.

### Section Structure and Ordering

(Source: CONTEXT.md locked decisions + Claude's discretion for ordering)

| Order | Section | Heading Level | Content Type |
|-------|---------|---------------|--------------|
| 1 | Title + badges | H1 | Project name, one-line description |
| 2 | What It Does | H2 | Brief description (2-3 sentences) + example CLI command in fenced code block |
| 3 | Visual Overview | H2 | Before/after image + web interface screenshots |
| 4 | Pipeline Stages | H2 | Numbered list of 12 stages with one-line descriptions |
| 5 | Getting Started | H2 | Prerequisites, clone, install, configure |
| 6 | Running the Pipeline (CLI) | H2 | CLI commands in fenced code blocks |
| 7 | Web Interface | H2 | Docker Compose instructions, dev mode instructions |
| 8 | Sample CV | H2 | How to run the pipeline against the included sample |
| 9 | Versioning | H2 | Semantic versioning policy (major/minor/patch definitions) |
| 10 | License | H2 | One-line reference to Apache 2.0 LICENSE file |

### Code Block Language Tags

| Context | Language tag |
|---------|-------------|
| Shell commands | `bash` |
| Python code | `python` |
| YAML configuration | `yaml` |
| File paths in prose | Inline code (backticks) |

### Image Embedding Pattern

```markdown
![Pipeline Viewer](docs/images/pipeline-viewer.png)
```

Use relative paths from the repository root. Alt text must be descriptive (not empty).

---

## Synthetic CV Document Formatting (.docx)

The synthetic CV is generated via python-docx. These specifications ensure the pipeline processes it correctly.

### Document Structure

| Property | Value |
|----------|-------|
| Filename | `sample_vasquez_cv.docx` (source: RESEARCH.md recommendation) |
| Path | `data/sample_cvs/word/sample_vasquez_cv.docx` |
| Title style | Heading 0 ("Title" Word style) for "Curriculum Vitae" |
| Section headings | Heading 1 for major sections (Education, Bibliography, etc.) |
| Subsection headings | Heading 2 for subsections (Degrees, Peer-Reviewed Publications, etc.) |
| Body text | Normal style for all content paragraphs |
| Name formatting | Bold run within a Normal paragraph |

### Required Sections

(Source: RESEARCH.md WCM taxonomy mapping, CONTEXT.md locked decisions)

The CV must include content for all of the following to exercise all pipeline stages:

| Section | Heading Text | Min Entries | Why Required |
|---------|-------------|-------------|--------------|
| Personal Data | "Personal Data" | 1 block | Stage 1a segmentation, Stage 3 taxonomy |
| Education | "Education" > "Degrees" | 2 (MD + PhD) | Stage 2 extraction |
| Other Education | "Other Education" | 1 | Coverage breadth |
| Postdoctoral Training | "Postdoctoral Training" | 1 | Stage 2 extraction |
| Professional Positions | "Academic Appointments" | 3-4 | Stage 5b institution enrichment (ROR) |
| Licensure | "Licensure and Certification" | 1 | Coverage breadth |
| Board Certification | (subsection of above) | 1 | Coverage breadth |
| Honors | "Honors and Awards" | 3-4 | Stage 3 taxonomy |
| Professional Organizations | "Professional Memberships" | 2-3 | Coverage breadth |
| Teaching | "Teaching" | 3-4 | Stage 5c teaching formatter |
| Mentoring | "Mentoring and Advising" | 2-3 | Stage 5c |
| Service | "Institutional Service" | 2-3 | Coverage breadth |
| Speaking | "Invited Presentations" | 3-4 | Stage 3 taxonomy |
| Publications | "Bibliography" > "Peer-Reviewed Publications" | 8-12 | Stage 5 PubMed enrichment (must use real journal names) |
| Grants | "Research Funding" | 2-3 (current + completed) | Stage 3 taxonomy |

### Fabricated Identity

| Field | Value |
|-------|-------|
| Name | Elena M. Vasquez, MD, PhD |
| Title | Associate Professor of Medicine |
| Department | Department of Medicine, Division of Cardiology |
| Institution | Weill Cornell Medicine |
| Level | Mid-career physician-scientist |

(Source: RESEARCH.md code example; Claude's discretion for biographical details)

### Publication Formatting

Publications must use real journal names with fabricated article titles and co-authors (source: CONTEXT.md locked decision). Format:

```
N. Vasquez EM, [Co-author Last] [Initial], [Co-author Last] [Initial].
[Fabricated title]. [Real Journal Name]. YYYY;Vol(Issue):Pages.
```

Use journals such as: J Am Coll Cardiol, Circulation, JAMA Cardiology, Nature Medicine, Cell, PNAS.

---

## Copywriting Contract

These are the text elements that appear in user-facing documentation, not in a UI application.

| Element | Copy |
|---------|------|
| README title | "CViche" |
| README subtitle | "AI-powered CV parsing pipeline that transforms unstructured academic CVs into standardized WCM format" |
| Sample CV run instruction | `python3 run_full_pipeline.py sample_vasquez_cv` |
| Prerequisites list | Python 3.11+, poppler-utils (system package), OpenAI API key |
| OpenAI key instruction | `export OPENAI_API_KEY=your-key-here` with link to https://platform.openai.com/api-keys |
| CHANGELOG header | "All notable changes to this project will be documented in this file." |
| CHANGELOG format note | "The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/)." |
| License reference | "This project is licensed under the Apache License 2.0 -- see the [LICENSE](LICENSE) file for details." |
| Versioning policy -- major | "Architecture changes (e.g., new pipeline framework, database migration)" |
| Versioning policy -- minor | "Model switches or new processing stages" |
| Versioning policy -- patch | "Prompt tuning and bug fixes" |

---

## Spacing Scale

Not applicable -- this phase creates markdown documents (rendered by GitHub) and a .docx file (rendered by Word). No custom spacing tokens are needed.

---

## Typography

Not applicable -- GitHub's markdown renderer and Microsoft Word provide their own typography. No custom font declarations needed.

---

## Color

Not applicable -- no frontend UI components are created or modified. Screenshot images capture the existing web interface as-is.

---

## Registry Safety

| Registry | Blocks Used | Safety Gate |
|----------|-------------|-------------|
| not applicable | none | not applicable -- no frontend components |

---

## Checker Sign-Off

- [ ] Dimension 1 Copywriting: PENDING (README title, subtitle, instructions, changelog copy defined above)
- [ ] Dimension 2 Visuals: PENDING (3 screenshot specs with dimensions, content, and capture state defined)
- [ ] Dimension 3 Color: NOT APPLICABLE (documentation phase, no UI color decisions)
- [ ] Dimension 4 Typography: NOT APPLICABLE (documentation phase, markdown/Word rendering)
- [ ] Dimension 5 Spacing: NOT APPLICABLE (documentation phase, no layout spacing)
- [ ] Dimension 6 Registry Safety: NOT APPLICABLE (no component registry usage)

**Approval:** pending

---

## Sources

| Decision | Source |
|----------|--------|
| README sections and content | CONTEXT.md locked decisions |
| Screenshot list (3 images) | CONTEXT.md locked decisions |
| Screenshot storage path | CONTEXT.md: `docs/images/` |
| Before/after visual approach | CONTEXT.md locked decisions |
| Sample CV identity and sections | RESEARCH.md code examples + CONTEXT.md locked decisions |
| Sample CV filename | RESEARCH.md recommendation: `sample_vasquez_cv.docx` |
| CHANGELOG format | CONTEXT.md: Keep a Changelog |
| Versioning policy wording | CONTEXT.md + REQUIREMENTS.md VER-01 |
| Image dimensions (1200px) | Default: GitHub Retina-ready standard |
| Viewport for capture (1280x800) | Default: standard laptop viewport |
| README section ordering | Claude's discretion (per CONTEXT.md) |
| Fabricated person details | Claude's discretion (per CONTEXT.md) |
