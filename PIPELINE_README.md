# CViche Pipeline

## Purpose & Scope

CViche parses academic/faculty CVs (Word documents) and produces structured JSON with taxonomy-classified entries. The output maps CV content to the WCM (Weill Cornell Medicine) standard taxonomy schema for downstream systems.

**What it does:**
- Extracts hierarchical section structure from Word CVs
- Identifies individual entries (publications, grants, positions, etc.)
- Classifies entries to a 20-category taxonomy with 59 codes

**What it doesn't do:**
- Parse PDFs (Word .docx only)
- Handle non-English CVs
- Process job descriptions or resumes (academic CVs only)

---

## Architecture Overview

```
┌───────────────────────────────────────────────────────────────────────────────┐
│                              CVICHE PIPELINE                                   │
│                                                                               │
│  ┌─────────┐                                                                  │
│  │  .docx  │                                                                  │
│  │  Input  │                                                                  │
│  └────┬────┘                                                                  │
│       │                                                                       │
│       ▼                                                                       │
│  ┌────────────────────────────────────────────────────────────────────────┐   │
│  │              CLASSIFICATION STAGES (Stages 1a - 3b)                     │   │
│  │                                                                        │   │
│  │  ┌───────────┐   ┌───────────┐   ┌───────────┐   ┌───────────┐        │   │
│  │  │ Stage 1a  │──▶│ Stage 1b  │──▶│  Stage 2  │──▶│ Stage 3a  │        │   │
│  │  │ Hierarchy │   │ Index Map │   │  Entries  │   │  Header   │        │   │
│  │  │ (LLM)     │   │ (No LLM)  │   │ (No LLM)  │   │ Taxonomy  │        │   │
│  │  └───────────┘   └───────────┘   └───────────┘   │  (LLM)    │        │   │
│  │                                        │         └─────┬─────┘        │   │
│  │                                        │               │              │   │
│  │                                        ▼               ▼              │   │
│  │                                  ┌───────────────────────────┐        │   │
│  │                                  │       Stage 3b            │        │   │
│  │                                  │  Entry Classification     │        │   │
│  │                                  │       (LLM)               │        │   │
│  │                                  └─────────────┬─────────────┘        │   │
│  └────────────────────────────────────────────────│──────────────────────┘   │
│                                                   │                          │
│                                                   ▼                          │
│  ┌────────────────────────────────────────────────────────────────────────┐   │
│  │              ENRICHMENT STAGES (Stages 4 - 5b)                         │   │
│  │                                                                        │   │
│  │  ┌───────────┐   ┌───────────┐   ┌───────────┐   ┌───────────┐        │   │
│  │  │  Stage 4  │──▶│ Stage 4.5 │──▶│  Stage 5  │──▶│ Stage 5b  │        │   │
│  │  │  Field    │   │  Research │   │  PubMed   │   │Institution│        │   │
│  │  │ Extract   │   │  Summary  │   │  Enrich   │   │  Enrich   │        │   │
│  │  │  (LLM)    │   │  (LLM)    │   │ (API)     │   │  (LLM)    │        │   │
│  │  └───────────┘   └───────────┘   └───────────┘   └─────┬─────┘        │   │
│  │                                                         │              │   │
│  │  ┌───────────┐   ┌───────────┐                          │              │   │
│  │  │ Stage 5c  │──▶│ Stage 5d  │◀─────────────────────────┘              │   │
│  │  │ Teaching  │   │ Citation  │                                         │   │
│  │  │ Formatter │   │ Formatter │                                         │   │
│  │  │  (LLM)    │   │  (LLM)    │                                         │   │
│  │  └───────────┘   └─────┬─────┘                                         │   │
│  └────────────────────────│────────────────────────────────────────────┘   │
│                                                           │                  │
│                                                           ▼                  │
│  ┌────────────────────────────────────────────────────────────────────────┐   │
│  │              OUTPUT GENERATION (Stage 6)                               │   │
│  │                                                                        │   │
│  │  ┌───────────────────────────────────────────────────────────────┐    │   │
│  │  │  Stage 6: WCM Word Template Generation                         │    │   │
│  │  │  - Fills WCM template sections by taxonomy code                │    │   │
│  │  │  - Bolds CV owner name in publications                         │    │   │
│  │  │  - Track changes for enriched/reformatted content              │    │   │
│  │  │  - Word comments explaining each change                        │    │   │
│  │  │  - Near-duplicate entry detection and removal                  │    │   │
│  │  │  - Geographic scope classification (Regional/National/Intl)    │    │   │
│  │  └───────────────────────────────────────────────────────────────┘    │   │
│  └────────────────────────────────────────────────────────────────────────┘   │
│                                                                               │
│  OUTPUT FILES:                                                                │
│  ├── stage_1a_segmentation/{uid}_segmented.json                              │
│  ├── stage_1b_hierarchy_mapping/{uid}_hierarchy_mapped.json                  │
│  ├── stage_2_entry_extraction/{uid}_entries.json                             │
│  ├── stage_3a_header_mappings/{uid}_header_taxonomy.json                     │
│  ├── stage_3b_classified_entries/{uid}_classified.json                       │
│  ├── stage_4_field_extraction/{uid}_fields.json                              │
│  ├── stage_4_5_research_summary/{uid}_research_summary.json                  │
│  ├── stage_5_enrichment/{uid}_enriched.json                                  │
│  ├── stage_5b_institution_enrichment/{uid}_institution_enriched.json         │
│  ├── stage_5c_teaching_formatted/{uid}_teaching_formatted.json               │
│  ├── stage_5d_citation_formatted/{uid}_citation_formatted.json               │
│  └── stage_6_wcm_documents/{uid}_wcm.docx  ← FINAL OUTPUT                    │
│                                                                               │
│  ORCHESTRATOR: run_full_pipeline.py                                           │
│  CONFIG: Model settings in each stage script (no central config file)         │
│  CACHING: None (re-runs overwrite previous outputs)                           │
└───────────────────────────────────────────────────────────────────────────────┘
```

**Key architectural decisions:**
- LLM stages alternate with deterministic/API stages for cost efficiency
- Each stage writes a complete atomic output file before the next stage runs
- Pipeline aborts on stage failure; no partial files are written
- No caching—re-running overwrites previous results
- Stage 2 achieves 100% coverage (all document indices accounted for)
- Stages 4-6 are optional enrichment/output stages (core classification ends at 3b)

### Simple Text Reference (LLM-friendly)

```
Stage order: 1a → 1b → 2 → 3a → 3b → 4 → 4.5 → 5 → 5b → 5c → 5d → 6

LLM stages:         1a, 3a, 3b, 4, 4.5, 5b, 5c, 5d  (require OpenAI API)
Deterministic:      1b, 2                             (no API calls)
External API:       5                                 (PubMed/NCBI)
Document Gen:       6                                 (python-docx)

Inputs/Outputs:
  .docx → Stage 1a → _segmented.json
                   → Stage 1b → _hierarchy_mapped.json
                              → Stage 2 → _entries.json
                                        → Stage 3a → _header_taxonomy.json
                                                   → Stage 3b → _classified.json
                                                              → Stage 4 → _fields.json
                                                                        → Stage 4.5 → _research_summary.json
                                                                                    → Stage 5 → _enriched.json
                                                                                              → Stage 5b → _institution_enriched.json
                                                                                                         → Stage 5c → _teaching_formatted.json
                                                                                                                    → Stage 5d → _citation_formatted.json
                                                                                                                               → Stage 6 → _wcm.docx (FINAL)
```

---

## Pipeline Stages (Detailed)

```
Word Document (.docx)
    │
    ▼
┌─────────────────────────────────────┐
│  Stage 1a: Hierarchy Extraction     │  LLM (gpt-5.1)
│  Extract section headers & structure │
│  Two-pass normalization prompts     │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│  Stage 1b: Element Index Mapping    │  No LLM
│  Map headers to paragraph indices   │
│  Handle synthetic headers           │
│  Create "Personal Data" preamble    │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│  Stage 2: Entry Extraction          │  No LLM
│  Extract all entries with 100%      │
│  coverage of document indices       │
│  Entry types: paragraph, table,     │
│  table_row, header, break           │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│  Stage 3a: Header Taxonomy Mapping  │  LLM (gpt-5.1)
│  Map CV section headers to taxonomy │
│  codes with confidence weights      │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│  Stage 3b: Entry Classification     │  LLM (gpt-5.1)
│  Classify entries using header      │
│  taxonomy context from Stage 3a     │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│  Stage 4: Field Extraction          │  LLM (gpt-5.1)
│  Extract structured fields from     │
│  classified entries (authors, dates,│
│  titles, grant numbers, etc.)       │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│  Stage 4.5: Research Summary        │  LLM (gpt-5.1)
│  Generate biosketch-style M1        │
│  research summary statement         │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│  Stage 5: PubMed Enrichment         │  External API (NCBI)
│  Enrich publications with PubMed    │
│  metadata (authors, MeSH, etc.)     │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│  Stage 5b: Institution Enrichment   │  LLM (gpt-5.1)
│  Add institution location data      │
│  (city, state, country) via LLM     │
│  with CV owner context for disambig │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│  Stage 5c: Teaching Formatter       │  LLM (gpt-5.1)
│  Reformat K-code teaching entries   │
│  into standardized display format   │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│  Stage 5d: Citation Formatter       │  LLM (gpt-5.1)
│  Reformat S-code citations to       │
│  Vancouver style with numbered refs │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│  Stage 6: WCM Word Template         │  Document Generation
│  Generate WCM-formatted Word doc    │
│  with all sections filled           │
└─────────────────────────────────────┘
    │
    ▼
WCM Word Document (.docx)
```

| Stage | Purpose | LLM/API | Input | Output |
|-------|---------|---------|-------|--------|
| **1a** | Extract section hierarchy | LLM | .docx file | `_segmented.json` |
| **1b** | Map headers to paragraph indices | No | Stage 1a JSON + .docx | `_hierarchy_mapped.json` |
| **2** | Extract entries (100% coverage) | No | Stage 1b JSON + .docx | `_entries.json` |
| **3a** | Map headers to taxonomy codes | LLM | Stage 1a JSON | `_header_taxonomy.json` |
| **3b** | Classify entries to taxonomy | LLM | Stage 2 JSON + Stage 3a JSON | `_classified.json` |
| **4** | Extract structured fields | LLM | Stage 3b JSON | `_fields.json` |
| **4.5** | Generate research summary (M1) | LLM | Stage 4 JSON | `_research_summary.json` |
| **5** | Enrich publications with PubMed | NCBI API | Stage 4 JSON | `_enriched.json` |
| **5b** | Enrich institutions with location data | LLM | Stage 5 JSON | `_institution_enriched.json` |
| **5c** | Reformat teaching entries | LLM | Stage 5b JSON | `_teaching_formatted.json` |
| **5d** | Reformat citations to Vancouver | LLM | Stage 5c JSON | `_citation_formatted.json` |
| **6** | Generate WCM Word template | None | Best available JSON | `_wcm.docx` |

---

## Directory Layout

```
CV parsing - AI project/
├── run_full_pipeline.py              # Main entry point - runs all stages
├── prompt_logs/                      # LLM call logs (JSON + readable text)
├── data/
│   └── sample_cvs/
│       └── word/                     # Input Word documents go here
│           └── *.docx
├── src/unified_pipeline/
│   ├── segmentation/
│   │   ├── signature_based_segmentation.py       # Stage 1a (LLM hierarchy + normalization)
│   │   ├── chunked_chat_hierarchy_extractor.py   # Alternative Stage 1a approach
│   │   ├── header_validator.py                   # Header validation (LLM + regex)
│   │   └── locked_headers_v6.py                  # Known CV headers (bypass validation)
│   ├── stage_1b_hierarchy_mapper.py              # Stage 1b (NO LLM - index mapping)
│   ├── stage_2_entry_extraction.py               # Stage 2 (NO LLM - 100% coverage)
│   ├── stage_3a_header_taxonomy_mapper.py        # Stage 3a (LLM - header taxonomy mapping)
│   ├── stage_3b_entry_classifier.py              # Stage 3b (LLM - entry classification)
│   ├── stage_4_field_extractor.py                # Stage 4 (LLM - structured field extraction)
│   ├── stage_4_smart_extractor.py                # Stage 4 (two-tier: cheap model + retry)
│   ├── stage_4_5_research_summary.py             # Stage 4.5 (LLM - M1 biosketch summary)
│   ├── stage_5_pubmed_enrichment.py              # Stage 5 (NCBI API - PubMed metadata)
│   ├── stage_5b_institution_enrichment.py        # Stage 5b (LLM - institution location)
│   ├── stage_5c_teaching_formatter.py            # Stage 5c (LLM - teaching entry reformatting)
│   ├── stage_5d_citation_formatter.py            # Stage 5d (LLM - citation reformatting)
│   ├── stage_6_word_template.py                  # Stage 6 (Word doc generation)
│   ├── core/
│   │   ├── taxonomy_v7.json                      # Definitive taxonomy (59 codes)
│   │   ├── taxonomy_mapper_v2.py                 # Core classification logic
│   │   ├── candidate_surfacer.py                 # Candidate surfacing logic
│   │   ├── taxonomy_contexts.py                  # WCM taxonomy definitions (codes + labels)
│   │   ├── confusion_matrix.py                   # Section confusion definitions & routing rules
│   │   ├── confusion_detector.py                 # Keyword-based confusion trigger detection
│   │   ├── disambiguation_validators.py          # Validation flag definitions
│   │   ├── repair_segmentation.py                # Entry filtering and noise removal
│   │   ├── output_manager.py                     # Path management utilities
│   │   └── validators/                           # Modular validation framework
│   │       ├── base_validator.py                 # BaseValidator class
│   │       ├── guidance_engine.py                # Validator orchestration
│   │       ├── label_content_conflict.py         # Generic label + specific content
│   │       ├── fellowship_classifier.py          # Fellowship disambiguation
│   │       ├── s7_unpublished.py                 # Unpublished work detection
│   │       └── ...                               # Other validators
│   ├── config/
│   │   ├── field_schemas_v1.json                 # Field extraction schemas by taxonomy code
│   │   └── ror_cache.json                        # Cached ROR lookups (auto-generated)
│   └── outputs/                                  # All stage outputs (auto-created)
│       ├── stage_1a_segmentation/
│       ├── stage_1b_hierarchy_mapping/
│       ├── stage_2_entry_extraction/
│       ├── stage_3a_header_mappings/
│       ├── stage_3b_classified_entries/
│       ├── stage_4_field_extraction/
│       ├── stage_4_5_research_summary/
│       ├── stage_5_enrichment/
│       ├── stage_5b_institution_enrichment/
│       ├── stage_5c_teaching_formatted/
│       ├── stage_5d_citation_formatted/
│       ├── stage_6_wcm_documents/
│       └── archive/                              # Archived outputs before reprocessing
└── docs/
    ├── PIPELINE_README.md                        # This file (start here)
    ├── STAGE3_TAXONOMY_ARCHITECTURE.md           # Stage 3 deep dive
    └── PIPELINE_ARCHITECTURE_V9.md               # Historical reference
```

---

## How to Run

### Prerequisites
- Python 3.9+
- OpenAI API key set as `OPENAI_API_KEY` environment variable
- Dependencies: `pip install openai python-docx tiktoken requests lxml`
- Optional: `NCBI_API_KEY` for faster PubMed lookups in Stage 5

### Full Pipeline (Recommended)

```bash
cd "CV parsing - AI project"

python3 run_full_pipeline.py <cv_path_or_uid> [--stage STAGE] [--model MODEL]

# Example - run full pipeline:
python3 run_full_pipeline.py 2071_Zuschlag_Cv

# Or with full path:
python3 run_full_pipeline.py 'data/sample_cvs/word/2071_Zuschlag_Cv.docx'

# With specific model:
python3 run_full_pipeline.py 2071_Zuschlag_Cv --model gpt-4o
```

**Arguments:**
- `cv_path_or_uid`: Path to Word document OR just the document UID (if UID only, looks in `data/sample_cvs/word/`)
- `--stage`: Run ONLY this stage: `1a`, `1b`, `2`, `3a`, `3b`, `3`, `4`, `4.5`, `5`, `5b`, or `6` (omit for full pipeline)
- `--model`: OpenAI model to use (default: `gpt-5.1`)

### Single Stage Runs

Run individual stages when you want to re-process just one step:

```bash
# Run only Stage 1a (hierarchy extraction)
python3 run_full_pipeline.py 2071_Zuschlag_Cv --stage 1a

# Run only Stage 1b (index mapping)
python3 run_full_pipeline.py 2071_Zuschlag_Cv --stage 1b

# Run only Stage 2 (entry extraction)
python3 run_full_pipeline.py 2071_Zuschlag_Cv --stage 2

# Run only Stage 3a (header taxonomy mapping)
python3 run_full_pipeline.py 2071_Zuschlag_Cv --stage 3a

# Run only Stage 3b (entry classification)
python3 run_full_pipeline.py 2071_Zuschlag_Cv --stage 3b

# Run both Stage 3a and 3b together
python3 run_full_pipeline.py 2071_Zuschlag_Cv --stage 3

# Run only Stage 4 (field extraction)
python3 run_full_pipeline.py 2071_Zuschlag_Cv --stage 4

# Run only Stage 4.5 (research summary generation)
python3 run_full_pipeline.py 2071_Zuschlag_Cv --stage 4.5

# Run only Stage 5 (PubMed enrichment)
python3 run_full_pipeline.py 2071_Zuschlag_Cv --stage 5

# Run only Stage 5b (institution enrichment)
python3 run_full_pipeline.py 2071_Zuschlag_Cv --stage 5b

# Run only Stage 6 (WCM Word template generation)
python3 run_full_pipeline.py 2071_Zuschlag_Cv --stage 6
```

**Stage dependencies:**
| Stage | Requires | LLM/API |
|-------|----------|---------|
| `1a` | None (starts fresh) | LLM |
| `1b` | Stage 1a output (`_segmented.json`) | No |
| `2` | Stage 1b output (`_hierarchy_mapped.json`) | No |
| `3a` | Stage 1a output (`_segmented.json`) | LLM |
| `3b` | Stage 2 (`_entries.json`) + Stage 3a (`_header_taxonomy.json`) | LLM |
| `3` | Stage 1a + Stage 2 (runs 3a then 3b) | LLM |
| `4` | Stage 3b output (`_classified.json`) | LLM |
| `4.5` | Stage 4 output (`_fields.json`) | LLM |
| `5` | Stage 4 output (`_fields.json`) | NCBI API |
| `5b` | Stage 5 output (`_enriched.json`) | LLM |
| `5c` | Stage 5b output (`_institution_enriched.json`) | LLM |
| `5d` | Stage 5c output (`_teaching_formatted.json`) | LLM |
| `6` | Best available: 5d → 5c → 5b → 5 → 4 → 3b | No |

**Behavior:**
- With `--stage`: Runs ONLY that stage (prerequisites must exist)
- Without `--stage`: Runs full pipeline (all stages)
- Missing prerequisites produce a clear error message
- Costs only reflect stages that were actually run
- Stage 6 uses the most enriched available output

### Individual Stage Scripts

You can also run stage scripts directly (useful for debugging):

```bash
# Stage 1a only
python3 src/unified_pipeline/segmentation/signature_based_segmentation.py <cv.docx>

# Stage 1b only (requires Stage 1a output)
python3 src/unified_pipeline/stage_1b_hierarchy_mapper.py <cv.docx>

# Stage 2 only (requires Stage 1b output)
python3 src/unified_pipeline/stage_2_entry_extraction.py <cv.docx>

# Stage 3a only (requires Stage 1a output)
python3 src/unified_pipeline/stage_3a_header_taxonomy_mapper.py <document_uid> [model]

# Stage 3b only (requires Stage 2 + Stage 3a outputs)
python3 src/unified_pipeline/stage_3b_entry_classifier.py <document_uid> [model]

# Stage 4 only (requires Stage 3b output)
python3 src/unified_pipeline/stage_4_field_extractor.py <cv.docx> [model]

# Stage 4.5 only (requires Stage 4 output)
python3 src/unified_pipeline/stage_4_5_research_summary.py <stage4_output.json>

# Stage 5 only (requires Stage 4 output)
python3 src/unified_pipeline/stage_5_pubmed_enrichment.py <stage4_output.json>

# Stage 5b only (requires Stage 5 output)
python3 src/unified_pipeline/stage_5b_institution_enrichment.py <stage5_output.json>

# Stage 6 only (requires best available enriched output)
python3 src/unified_pipeline/stage_6_word_template.py <enriched_output.json>
```

---

## Output Locations

All outputs are saved to `src/unified_pipeline/outputs/`. Output folders are auto-created. Re-running with the same UID **overwrites** previous outputs.

```
outputs/
├── stage_1a_segmentation/
│   ├── {uid}_segmented.json           # Hierarchical structure (machine-readable)
│   └── {uid}_segmented.txt            # Hierarchical structure (human-readable)
├── stage_1b_hierarchy_mapping/
│   └── {uid}_hierarchy_mapped.json
├── stage_2_entry_extraction/
│   └── {uid}_entries.json             # Key intermediate (Stage 2 → 3)
├── stage_3a_header_mappings/
│   └── {uid}_header_taxonomy.json     # Header-to-taxonomy mappings
├── stage_3b_classified_entries/
│   └── {uid}_classified.json          # Classification output (Stage 3 → 4)
├── stage_4_field_extraction/
│   └── {uid}_fields.json              # Structured fields extracted
├── stage_4_5_research_summary/
│   └── {uid}_research_summary.json    # M1 biosketch summary
├── stage_5_enrichment/
│   └── {uid}_enriched.json            # PubMed-enriched output
├── stage_5b_institution_enrichment/
│   └── {uid}_institution_enriched.json # Institution location data
├── stage_6_wcm_documents/
│   └── {uid}_wcm.docx                 # ← FINAL OUTPUT (WCM Word template)
└── archive/
    └── {uid}_{timestamp}/             # Archived outputs before reprocessing
```

---

## Key Data Schemas

### Stage 1a Output (`_segmented.json`)
```json
{
  "document_uid": "2071_Zuschlag_Cv",
  "hierarchy": [
    {
      "text": "Education",
      "level": "H1",
      "children": [
        {"text": "Graduate", "level": "H2", "children": []},
        {"text": "Undergraduate", "level": "H2", "children": []}
      ]
    }
  ],
  "meta": {
    "total_headers": 45,
    "extraction_cost": 0.08
  }
}
```

### Stage 2 Output (`_entries.json`) — Key Internal Schema
This is the contract between Stage 2 and Stage 3:

```json
{
  "document_uid": "2071_Zuschlag_Cv",
  "total_entries": 155,
  "entries": [
    {
      "entry_id": 1,
      "hierarchy": ["Research Experience", "Publications"],
      "hierarchy_path": "Research Experience > Publications",
      "paragraph_range": [45, 47],
      "text": "Smith J, Doe A. Title of Paper. Journal Name. 2024;42:123-145.",
      "section_header": "Publications",
      "parent_header": "Research Experience"
    }
  ]
}
```

**Fields used by Stage 3a/3b:**
- `hierarchy`: Array of section/subsection headers (used for candidate surfacing)
- `text`: The actual entry content to classify
- `section_header`: Immediate parent header (used in prompts)

### Stage 3a Output (`_header_taxonomy.json`) — Header Mappings
```json
{
  "document_uid": "2071_Zuschlag_Cv",
  "stage": "3a",
  "stage_name": "Header Taxonomy Mapping",
  "mappings": [
    {
      "title": "Education",
      "level": "H1",
      "taxonomy_options": [
        { "code": "B1", "confidence": 0.85 },
        { "code": "B2", "confidence": 0.15 }
      ],
      "children": [
        {
          "title": "Graduate",
          "level": "H2",
          "taxonomy_options": [{ "code": "B1", "confidence": 1.0 }]
        }
      ]
    }
  ],
  "meta": {
    "model": "gpt-5.1",
    "taxonomy_version": "7.2",
    "node_count": 45
  }
}
```

### Stage 3b Output (`_classified.json`) — Classification Output
```json
{
  "document_uid": "2071_Zuschlag_Cv",
  "total_entries": 155,
  "entries_classified": 155,
  "total_cost": 0.042,
  "entries": [
    {
      "entry_id": 1,
      "hierarchy": ["Research Experience", "Publications"],
      "text": "Smith J, Doe A. Title of Paper. Journal. 2024;42:123-145.",
      "taxonomy_code": "S1",
      "taxonomy_label": "Peer-Reviewed Research Articles",
      "confidence": 0.95,
      "reasoning": "Standard journal article format with authors, title, journal, year, volume, pages"
    }
  ],
  "meta": {
    "post_correction_summary": {
      "total_corrections": 8,
      "structural_corrections": 2,
      "committee_corrections": 1,
      "reasoning_corrections": 0,
      "grant_status_corrections": 2,
      "teaching_leadership_corrections": 1,
      "leadership_level_corrections": 1,
      "adjunct_position_corrections": 0,
      "training_compliance_corrections": 1,
      "invited_talk_corrections": 0
    },
    "qa_flags": {
      "hierarchy_mismatches": 3,
      "mismatch_summary": {
        "total_mismatches": 3,
        "by_code": {"R": 2, "M2": 1},
        "by_hierarchy": {"Honors": 2, "Publications": 1}
      }
    }
  }
}
```

### Stage 4 Output (`_fields.json`) — Structured Field Extraction
```json
{
  "document_uid": "2071_Zuschlag_Cv",
  "stage": "4",
  "stage_name": "Field Extraction",
  "total_entries": 155,
  "total_cost": 0.082,
  "cv_owner": {
    "name": "John A. Smith",
    "degrees": "MD, PhD",
    "institution": "Weill Cornell Medicine"
  },
  "cv_owner_location": {
    "locations": [
      {
        "institution": "Weill Cornell Medicine",
        "city": "New York",
        "state": "NY",
        "country": "USA",
        "confidence": 0.95
      }
    ],
    "metro_area": "New York City",
    "primary_location": {
      "institution": "Weill Cornell Medicine",
      "city": "New York",
      "state": "NY",
      "country": "USA",
      "confidence": 0.95
    },
    "inference_success": true,
    "cost": 0.0012,
    "tokens": 450
  },
  "entries": [
    {
      "entry_id": 1,
      "taxonomy_code": "S1",
      "taxonomy_label": "Peer-Reviewed Research Articles",
      "text": "Smith JA, Doe B, Jones C. Effects of treatment on outcomes. JAMA. 2024;331(5):423-431.",
      "extracted_fields": {
        "authors": "Smith JA, Doe B, Jones C",
        "title": "Effects of treatment on outcomes",
        "journal": "JAMA",
        "year": "2024",
        "volume": "331",
        "issue": "5",
        "pages": "423-431",
        "pmid": "38123456",
        "doi": "10.1001/jama.2024.12345"
      },
      "extraction_confidence": 0.95
    },
    {
      "entry_id": 42,
      "taxonomy_code": "M2A",
      "taxonomy_label": "Currently Active Research Grants",
      "text": "R01 CA123456 (PI: Smith) 07/2022-06/2027 NIH/NCI $2,500,000 Cancer treatment innovations",
      "extracted_fields": {
        "grant_number": "R01 CA123456",
        "pi_role": "PI",
        "agency": "NIH/NCI",
        "start_date": "07/2022",
        "end_date": "06/2027",
        "total_funding": "$2,500,000",
        "title": "Cancer treatment innovations"
      }
    }
  ],
  "stats": {
    "extracted": 148,
    "skipped": 7,
    "by_code": {"S1": 45, "M2A": 8, "D1": 5, "...": "..."}
  }
}
```

**Field schemas vary by taxonomy code:**
- S1/S2 (Publications): `authors`, `title`, `journal`, `year`, `volume`, `pages`, `doi`, `pmid`
- M2A/M2B/M2C (Grants): `grant_number`, `pi_role`, `agency`, `start_date`, `end_date`, `total_funding`, `title`
- D1/D2/D3 (Positions): `title`, `institution`, `department`, `start_date`, `end_date`
- B1 (Education): `degree`, `institution`, `discipline`, `year`, `thesis_title`, `advisor`
- H (Honors): `award_name`, `granting_body`, `date`, `amount`

See `config/field_schemas_v1.json` for complete field definitions.

### CV Owner Location Inference (Stage 4)

Stage 4 includes an LLM-powered location inference feature that analyzes the CV owner's employment, education, and training history to determine their current primary location. This information is used downstream in Stage 6 to automatically classify activities as Regional, National, or International.

**How it works:**

1. **Data Collection**: Collects entries with location-relevant taxonomy codes:
   - `A` (Personal/Contact Information)
   - `B1`, `B2` (Education)
   - `C` (Postdoctoral Training)
   - `D1`, `D2`, `D3` (Professional Positions)

2. **Prioritization**: Sorts entries by date, prioritizing current positions (end_date="present") over past positions.

3. **LLM Analysis**: Sends formatted employment/education history to `gpt-5.1` with a prompt to:
   - Identify current affiliations with confidence scores
   - Infer the metropolitan area (e.g., "New York City", "Boston", "San Francisco Bay Area")
   - Determine the primary (most likely) current work location

**Output fields:**
- `locations`: Array of current affiliations with institution, city, state, country, confidence
- `metro_area`: Inferred metropolitan area name
- `primary_location`: Single most likely current location
- `inference_success`: Boolean indicating if inference succeeded
- `cost`: LLM cost for location inference (~$0.002-0.005 per CV)
- `tokens`: Token count for the inference call

### Geographic Scope Classification (Stage 6)

Stage 6 uses the `cv_owner_location` from Stage 4 to automatically classify certain activities into geographic scope categories: **Regional**, **National**, or **International**. This applies to:

- **Q2 (Service on Boards/Committees)**: Classified into Regional/National/International tables
- **R (Invited Presentations)**: Classified into Regional/National/International tables

**Classification Logic:**

1. **International** (checked first):
   - Keywords: "international", "european", "asian", "global", "world"
   - Countries: Canada, UK, Australia, Germany, France, Japan, China, India, Brazil, Mexico, Italy, Spain, Switzerland, Netherlands, Sweden, Denmark, Norway, Qatar, Singapore, Hong Kong
   - If any of these appear in the activity text or location → **International**

2. **Regional** (same metro area or state as CV owner):
   - Matches against CV owner's city, state, and metro area
   - Metro-specific keywords by area:
     - **New York City**: NY, N.Y., NYC, Manhattan, Brooklyn, Queens, Bronx, Long Island, Westchester, New Jersey
     - **Boston**: Massachusetts, MA, Cambridge
     - **Los Angeles**: LA, Southern California
     - **Chicago**: Illinois, IL
     - **San Francisco**: Bay Area, California
   - If activity location matches regional indicators → **Regional**

3. **National** (default):
   - Same country but different region
   - Applied when no international or regional indicators found

**Example output in Stage 6:**
```
  Service on Boards: 3 Regional, 8 National, 2 International
  Presentations: 5 Regional, 12 National, 4 International
```

**Fallback behavior**: If `cv_owner_location` inference fails or is unavailable, all activities default to **National**.

### Stage 4.5 Output (`_research_summary.json`) — Research Summary
```json
{
  "document_uid": "2071_Zuschlag_Cv",
  "stage": "4.5",
  "stage_name": "Research Summary Generation",
  "cv_owner": {
    "name": "John A. Smith",
    "degrees": "MD, PhD"
  },
  "research_summary": {
    "existing_m1_score": 0.45,
    "used_existing": false,
    "generated_summary": "Dr. Smith's research program focuses on precision oncology and translational cancer therapeutics. His laboratory has pioneered novel approaches to understanding drug resistance mechanisms in solid tumors, with particular emphasis on breast and lung cancers. Key achievements include: (1) identification of biomarkers predictive of immunotherapy response (Nature Medicine, 2023), (2) development of combination therapies now in Phase II clinical trials, and (3) training of 12 postdoctoral fellows who have established independent research careers. His work is supported by R01 grants from NCI totaling $4.5M in active funding.",
    "word_count": 89,
    "generation_cost": 0.012
  },
  "entries": [...]  // All entries passed through from Stage 4
}
```

**M1 scoring criteria (0-1 scale):**
- Score ≥ 0.8: Use existing M1 content from CV
- Score < 0.8: Generate new biosketch-style summary from CV content

### Stage 5 Output (`_enriched.json`) — PubMed Enrichment
```json
{
  "document_uid": "2071_Zuschlag_Cv",
  "stage": "5",
  "stage_name": "PubMed Enrichment",
  "source_stage": "4",
  "enrichment_timestamp": "2025-11-29T14:32:00.000Z",
  "stats": {
    "total_publications": 45,
    "with_pmid": 38,
    "with_pmcid_only": 3,
    "with_doi_only": 2,
    "no_identifier": 2,
    "enriched": 41,
    "failed_lookups": 2
  },
  "entries": [
    {
      "entry_id": 1,
      "taxonomy_code": "S1",
      "extracted_fields": {
        "authors": "Smith JA, Doe B, Jones C",
        "title": "Effects of treatment on outcomes",
        "journal": "JAMA",
        "pmid": "38123456"
      },
      "enrichment_status": "enriched",
      "pubmed_data": {
        "pmid": "38123456",
        "pmcid": "PMC10234567",
        "full_authors": "Smith JA, Doe BC, Jones CD, Williams EF, Brown GH",
        "journal_iso": "JAMA",
        "pub_date": "2024 Feb 6",
        "pub_types": ["Journal Article", "Research Support, N.I.H., Extramural"],
        "mesh_terms": ["Neoplasms/drug therapy", "Precision Medicine"],
        "abstract": "Background: Treatment outcomes vary significantly..."
      }
    }
  ]
}
```

**Enrichment strategies by identifier type:**
1. PMID: Direct efetch lookup
2. PMCID only: Convert via ID Converter API → efetch
3. DOI only: Search PubMed → efetch

### Stage 5b Output (`_institution_enriched.json`) — Institution Enrichment
```json
{
  "document_uid": "2071_Zuschlag_Cv",
  "stage": "5b",
  "stage_name": "Institution Enrichment",
  "source_stage": "5",
  "enrichment_timestamp": "2025-11-29T14:35:00.000Z",
  "stats": {
    "total_institution_entries": 15,
    "enriched": 12,
    "not_found": 2,
    "skipped_internal_unit": 1
  },
  "entries": [
    {
      "entry_id": 5,
      "taxonomy_code": "B1",
      "extracted_fields": {
        "degree": "MD",
        "institution": "Johns Hopkins University School of Medicine",
        "year": "2005"
      },
      "institution_enrichment": {
        "cleaned_name": "Johns Hopkins University School of Medicine",
        "official_name": "Johns Hopkins University",
        "city": "Baltimore",
        "state": "Maryland",
        "country": "United States",
        "country_code": "US",
        "source": "llm"
      }
    }
  ]
}
```

**Enrichment method:** LLM-based (gpt-5.1). Uses CV owner location context for disambiguation (e.g., "OU College of Medicine" + Oklahoma context → University of Oklahoma, not Oglethorpe). Returns `cleaned_name` (institution without embedded location) to prevent duplication in Stage 6 output. Results are cached in `config/ror_cache.json`.

**Applies to taxonomy codes:** B1, B2 (Education), C/C1/C2 (Training), D1/D2/D3 (Positions)

### Stage 6 Output (`_wcm.docx`) — WCM Word Template
Stage 6 generates a Microsoft Word document (`.docx`) formatted according to WCM (Weill Cornell Medicine) CV template standards.

**Features:**
- Fills all WCM template sections organized by taxonomy code
- Bolds the CV owner's name in publication author lists
- Formats dates according to section-specific requirements (mm/yy, yyyy, mm/dd/yyyy)
- Normalizes ISO dates in free text (e.g., `2021-03-01` → `March 2021`)
- Uses Word track changes for enriched/reformatted content (institution locations, teaching reformats)
- Adds Word comments explaining each change made
- Sorts entries in reverse chronological order within sections
- **Near-duplicate detection**: Removes entries duplicated in source CVs using Jaccard similarity, containment, and title-based metrics. Date-aware mode for career progression codes (D1/D2/D3/C/B1) prevents false positives on rank changes. Title dissimilarity check prevents false positives on different presentations at the same venue.
- **Institution inheritance**: Propagates institution names from parent entries to blank sub-entries (e.g., indented bullet positions under a hospital)
- **Committee field splitting**: When Stage 4 merges committee name into role field (e.g., "Chair, Ultrasound Committee"), Stage 6 splits them into separate columns
- **Geographic scope classification**: Automatically routes Q2 (Service on Boards) and R (Presentations) entries to Regional/National/International tables based on CV owner's inferred location (see "CV Owner Location Inference" above)
- **Structural label filtering**: Removes source CV section headers that were incorrectly extracted as data entries

**Section mapping:**
| Taxonomy Code | WCM Section Title |
|---------------|-------------------|
| A | Personal Information |
| B1 | Education - Degrees |
| B2 | Education - Other |
| C | Postdoctoral Training |
| D1 | Academic Appointments |
| D2 | Hospital Appointments |
| M2A | Active Grants |
| M2B | Completed Grants |
| S1 | Peer-Reviewed Publications |
| ... | ... |

---

## WCM Taxonomy Overview

The pipeline classifies entries into 20 parent categories (A-T):

| Code | Category |
|------|----------|
| A | Personal Data / Contact Information |
| B | Education |
| C | Postdoctoral Training |
| D | Professional Positions & Employment |
| E | Licensure and Certification |
| G | Institutional / Hospital Affiliation |
| H | Honors and Awards |
| I | Professional Organizations |
| J | Percent Effort & Institutional Responsibilities |
| K | Educational Contributions |
| M | Research |
| N | Mentoring |
| O | Institutional Administrative Activities |
| P | Clinical Practice |
| Q | Extramural Professional Responsibilities |
| R | Invitations to Speak/Present |
| S | Bibliography |
| T | Appendix / Other |

Many categories have subcategories (e.g., S1-S8 for different publication types). See `core/taxonomy_contexts.py` for full definitions.

---

## Classification Architecture

### Stage 3a: Header Taxonomy Mapping
1. **Input**: CV section hierarchy from Stage 1a
2. **Process**: LLM maps each header node to taxonomy code(s) with confidence weights
3. **Output**: Tree of header mappings with `taxonomy_options` arrays

Key features:
- Preserves full hierarchy structure (H1/H2/H3 levels)
- Supports multi-code mappings for ambiguous headers (e.g., "Activities" → P 70%, Q2 30%)
- Confidences sum to 1.0 for each node
- Includes disambiguation notes for ambiguous cases

### Stage 3b: Entry Classification
1. **Input**: Entries from Stage 2 + header mappings from Stage 3a
2. **Process**: LLM classifies each entry using header taxonomy context as guidance
3. **Output**: Classified entries with codes, labels, confidence, and reasoning

Key features:
- Uses header mappings as context (not hard constraint)
- Can override header mapping when entry content is more specific
- Batched processing for efficiency
- Deterministic validators for common confusion patterns

### Stage 3b: Post-Classification Pipeline (10 Steps)

After initial LLM classification, Stage 3b runs a 10-step post-classification pipeline to correct systematic errors and flag entries for QA review. This pipeline was developed based on expert evaluation feedback achieving **86-91/100 accuracy scores**.

```
Stage 3b Processing Flow:
┌─────────────────────────────────────────────────────────────────┐
│  Entries ──→ [Initial Classification] ──→ T entries exist?     │
│                                                │                │
│                                           YES  │                │
│                                                ▼                │
│                                    [T-Validation Gate]          │
│                                    Reclassify T → specific      │
│                                                │                │
│                                                ▼                │
│                                    [Fragment Reconnection]      │
│                                    Link orphans to parents      │
│                                                │                │
│                                                ▼                │
│                                    [Duplicate Detection]        │
│                                                │                │
│                                                ▼                │
│                          [POST-CLASSIFICATION CORRECTIONS]      │
│                          10-step deterministic pipeline         │
│                                                │                │
│                                                ▼                │
│                                         Final Output            │
└─────────────────────────────────────────────────────────────────┘
```

#### 10-Step Post-Classification Correction Pipeline

| Step | Validator | From → To | Pattern Detected |
|------|-----------|-----------|------------------|
| 1 | StructuralHeader | A → T | CV titles, page markers, timestamps |
| 2 | CommitteePosition | D → P/Q2 | Committee service vs positions |
| 3 | ReasoningConsistency | X → Y | LLM reasoning contradicts assigned code |
| 4 | **GrantStatus** | M2B/C → M2A | Date-based grant status (active vs completed) |
| 5 | **TeachingLeadership** | K1 → K3 | Course Director, Program Director roles |
| 6 | **LeadershipLevel** | O → P | Non-executive admin roles (Committee Chair, etc.) |
| 7 | **AdjunctPosition** | D1 → D3 | Community college instructors, Lab Managers |
| 8 | **TrainingCompliance** | P → B2 | DEI/Title IX/HIPAA trainings received |
| 9 | **InvitedTalk** | S8 → R | Keynotes, invited conference talks |
| 10 | **HierarchyMismatch** | (QA flag) | Content-hierarchy mismatch for review |

**Steps 4-10 are new in V12.0** (based on expert evaluation feedback).

#### Step Details

**4. GrantStatusCorrector** - Corrects grant codes based on date analysis:
- End year < current year → M2B (completed)
- End year ≥ current year → M2A (active)
- Patterns: `2019-2024`, `2020-present`, `01/2019-12/2024`

**5. TeachingLeadershipCorrector** - Detects program leadership roles:
- Triggers: "Course Director", "Program Director", "Co-Director", "Clerkship Director"
- K1 (didactic teaching) → K3 (educational program leadership)

**6. LeadershipLevelCorrector** - Distinguishes executive vs admin roles:
- Executive (keep O): Department Chair, Center Director, Associate Dean
- Administrative (→ P): Committee Chair, Track Director, Secretary/Treasurer

**7. AdjunctPositionCorrector** - Corrects non-faculty positions:
- Triggers: "Community College", "Adjunct Instructor", "Lab Manager"
- D1 (faculty) → D3 (other professional positions)

**8. TrainingComplianceCorrector** - Identifies trainings received:
- Triggers: "Title IX", "DEI", "Bias Training", "HIPAA", "CITI"
- P (service) → B2 (non-degree educational experiences)

**9. InvitedTalkCorrector** - Detects invited presentations:
- Triggers: "Invited Talk", "Keynote", "Grand Rounds", "Federal Interagency"
- S8 (abstract) → R (invited presentation)

**10. HierarchyMismatchFlagger** - QA flags only (no auto-correction):
- Flags entries where taxonomy code doesn't match hierarchy section
- Accepted mismatches (not flagged): R under "Honors", M3 under "Patent Applications"

#### T-Validation Gate
- **Purpose**: Re-evaluate entries classified as "T" with full taxonomy context
- **Trigger**: Any entry with exactly code "T" (not T-family codes like T1)
- **Process**:
  1. Collects all T-classified entries
  2. Sends to LLM with FULL taxonomy and guidance that T should be <2% of entries
  3. Provides 10 common T misclassification patterns (Q2, R, M2, K1, etc.)
  4. Reclassifies or confirms each T entry with reasoning
- **Result**: In testing, reclassifies 80-100% of T entries to more specific codes

#### Fragment Reconnection
- **Purpose**: Link orphaned fragment entries back to adjacent parent entries
- **Trigger**: T entries that look like fragments (short, low confidence, pattern matches)
- **Process**:
  1. Identifies T entries that are likely fragments:
     - Location-only lines: "University, Columbus, OH"
     - Budget-only lines: "$1,326,480, Ohio Department of Medicaid"
     - Partial institution names without roles
  2. For each fragment, shows previous and next entries to LLM
  3. LLM decides: belongs to PREVIOUS, NEXT, or STANDALONE
  4. Fragments inherit taxonomy code from parent with low confidence (0.3)
- **Output fields**:
  - `is_fragment: true` - marks entry as a fragment
  - `fragment_of: N` - index of parent entry
  - `fragment_reasoning: "..."` - explanation of reconnection

---

## Validation & Disambiguation System

Stage 3 includes a deterministic validation layer that runs alongside LLM classification to catch common confusion patterns and raise flags for human review.

### Architecture Overview

```
Entry Classification Flow:
┌─────────────────────────────────────────────────────────────────┐
│                         STAGE 3                                  │
│                                                                 │
│  Entry ──┬──→ [Validators] ──→ Guidance/Exclusions              │
│          │                           │                          │
│          │                           ▼                          │
│          └──→ [LLM Classification] ←─┤                          │
│                      │               │                          │
│                      ▼               │                          │
│              [Confusion Detector] ───┘                          │
│                      │                                          │
│                      ▼                                          │
│              Taxonomy Code + Validation Flags                   │
└─────────────────────────────────────────────────────────────────┘
```

### Core Components

| Component | File | Purpose |
|-----------|------|---------|
| **Confusion Matrix** | `core/confusion_matrix.py` | Defines which taxonomy codes are commonly confused and disambiguation rules |
| **Confusion Detector** | `core/confusion_detector.py` | Detects keywords/patterns that trigger confusion risk |
| **Disambiguation Validators** | `core/disambiguation_validators.py` | Flags potential misclassifications with `ValidationFlag` objects |
| **Validator Framework** | `core/validators/` | Modular validators for specific confusion patterns |
| **Guidance Engine** | `core/validators/guidance_engine.py` | Orchestrates all validators during classification |

### Validation Flags

When validators detect potential issues, they raise `ValidationFlag` objects:

```python
@dataclass
class ValidationFlag:
    severity: str           # 'info', 'warning', 'error'
    confusion_type: str     # e.g., 'H_I_FELLOWSHIP', 'S1_S8_ABSTRACT'
    message: str            # Human-readable description
    suggestion: str         # Recommended action
    alternative_sections: List[str]  # Other possible codes
    key_question: str       # Question to resolve ambiguity
```

### Validation Severity Behavior

| Severity | Recorded? | Override LLM? | Downstream Action |
|----------|-----------|---------------|-------------------|
| `info` | Yes, in `_mapped.json` | No | None required |
| `warning` | Yes, in `_mapped.json` | No | Human review recommended |
| `error` | Yes, in `_mapped.json` | **Yes, if confidence ≥80%** | Forces review state |

**Override threshold**: Validators with `confidence ≥ 0.80` can override the LLM's classification. This is logged as `[OVERRIDE]` in console output.

**Where flags appear**:
- In each entry's `validation_flags` array in `_mapped.json`
- In console output during processing
- Not in any separate log file (prompt logs don't include validation)

### Current Validators

#### Pre-Classification Validators (Run before LLM)

| Validator | Priority | Detects |
|-----------|----------|---------|
| `LabelContentConflictValidator` | 5 | Generic labels with specific content (e.g., "Contact Info" containing publications) |
| `FellowshipClassifier` | 12 | Fellowship vs honor society vs training position |
| `EducationPostdocValidator` | 15 | Postdoc training vs education vs employment |
| `AwardsGrantsValidator` | 15 | Honors (H) vs research grants (M2) |
| `S7UnpublishedValidator` | 20 | Unpublished/in-prep work patterns |
| `URLDomainValidator` | 20 | Preprint URLs (bioRxiv, medRxiv) → S10 |
| `ContactSectionValidator` | 10 | Personal data subsection routing |

#### Post-Classification Correctors (Run after LLM) - V12.0

| Validator | Correction | Triggers |
|-----------|------------|----------|
| `StructuralHeaderValidator` | A → T | CV titles, page markers, timestamps |
| `CommitteePositionCorrector` | D → P/Q2 | Committee service patterns |
| `ReasoningConsistencyChecker` | X → Y | Reasoning contradicts code |
| `GrantStatusCorrector` | M2B/C ↔ M2A | Date ranges in grant text |
| `TeachingLeadershipCorrector` | K1 → K3 | "Course Director", "Program Director" |
| `LeadershipLevelCorrector` | O → P | "Committee Chair", "Track Director" |
| `AdjunctPositionCorrector` | D1 → D3 | "Community College", "Lab Manager" |
| `TrainingComplianceCorrector` | P → B2 | "Title IX", "DEI", "HIPAA Training" |
| `InvitedTalkCorrector` | S8 → R | "Invited Talk", "Keynote", "Grand Rounds" |
| `HierarchyMismatchFlagger` | (QA flag) | Content-hierarchy mismatch |

### Confusion Matrix

The confusion matrix (`confusion_matrix.py`) defines:
- **Common confusions**: Which codes are frequently mixed up (e.g., H ↔ I for fellowships)
- **Disambiguation rules**: Deterministic signals to distinguish codes
- **Routing rules**: Decision trees for ambiguous cases

Example entry:
```python
'bibliography': {
    'primary_parent': 'S',
    'confusion_risk': 'high',
    'decision_order': [
        '1) PRE-PUBLICATION? → S7 (in prep) or S10 (preprint)',
        '2) JOURNAL ARTICLE? Original data → S1; review/editorial → S2',
        '3) ABSTRACT ONLY? → S8',
        ...
    ]
}
```

### How Validators Integrate

1. **Pre-classification**: Validators analyze entry before LLM call
2. **Guidance injection**: Validator hints added to LLM prompt
3. **Override logic**: High-confidence validators (≥80%) can override LLM result
4. **Flag collection**: All flags collected in output for review

### Output with Validation Flags

When validation flags are raised, they appear in `_mapped.json`:

```json
{
  "entry_id": 42,
  "text": "Fellow, American College of Physicians (FACP), 2019",
  "taxonomy_code": "I",
  "validation_flags": [
    {
      "severity": "warning",
      "confusion_type": "H_I_FELLOWSHIP",
      "message": "FACP fellowship abbreviation suggests professional society (I), not honor (H)",
      "key_question": "Is this an ongoing membership or a one-time honor?"
    }
  ]
}
```

### Adding New Validators

See `core/validators/README.md` for the full guide. Quick steps:

1. Create `validators/your_validator.py` extending `BaseValidator`
2. Implement `applies_to()`, `priority()`, and `analyze()` methods
3. Register in `validators/__init__.py`
4. Test with: `grep -E "(VALIDATOR|OVERRIDE)" < pipeline_output.log`

---

## Configuration

### Model Selection
All LLM calls use `gpt-5.1`. Model is configured directly in each stage script (no central config file):
- `signature_based_segmentation.py` (Stage 1a) — `model='gpt-5.1'`
- `stage_3_taxonomy_mapper.py` (Stage 3) — `model='gpt-5.1'`
- `header_validator.py` — `model='gpt-5.1'`

Note: Stage 1b and Stage 2 are deterministic (NO LLM calls).

### Environment Variables
| Variable | Required | Purpose |
|----------|----------|---------|
| `OPENAI_API_KEY` | Yes | OpenAI API authentication |
| `NCBI_API_KEY` | No | NCBI/PubMed API key (10 req/s vs 3 req/s without) |

### Tuning Parameters (in code)
- **Batch size**: Stage 3 processes entries in batches of 10-15 (configurable in `taxonomy_mapper_v2.py`)
- **Temperature**: 0.1 for classification, 0.2 for hierarchy extraction
- **Chunk size**: Stage 1 processes ~50 paragraphs per LLM call

### Cost Estimates
Typical CV (150 entries):
- Stage 1a: ~$0.05-0.15 (LLM)
- Stage 1b: $0 (no LLM)
- Stage 2: $0 (no LLM)
- Stage 3a: ~$0.01-0.02 (LLM)
- Stage 3b: ~$0.02-0.04 (LLM)
- Stage 4: ~$0.02-0.08 (LLM, varies by two-tier strategy)
- Stage 4.5: ~$0.01-0.02 (LLM)
- Stage 5: $0 (PubMed API, free)
- Stage 5b: ~$0.001-0.003 (LLM, batched institution lookup)
- Stage 5c: ~$0.005-0.01 (LLM, teaching entry reformatting)
- Stage 5d: ~$0.005-0.01 (LLM, citation reformatting)
- Stage 6: $0 (local document generation)
- **Total: ~$0.12-0.34 per CV (full pipeline)**

**Cost optimization notes:**
- Stage 4 uses a two-tier strategy: cheap model first (gpt-4o-mini), premium retry for failures
- Stages 5/5b/6 are optional enrichment stages; core classification cost is ~$0.08-0.21

---

## Cross-Cutting Concerns

### Error Handling & Output Atomicity

**Core principle**: Each stage writes a complete atomic output file only on success. No stage writes partial files. Failure at any stage stops further processing.

| Scenario | Behavior |
|----------|----------|
| Stage 1a fails | No `_segmented.json` written; pipeline stops |
| Stage 1b fails | `_segmented.json` exists (from Stage 1a); no `_hierarchy_mapped.json`; pipeline stops |
| Stage 2 fails | `_hierarchy_mapped.json` exists (from Stage 1b); no `_entries.json`; pipeline stops |
| Stage 3a fails | `_entries.json` exists (from Stage 2); no `_header_taxonomy.json`; pipeline stops |
| Stage 3b fails on entry 50/100 | `_header_taxonomy.json` exists (from Stage 3a); no `_classified.json`; pipeline stops |
| Re-run after failure | Previous successful stage outputs remain; failed stage re-executes |

**Exception types:**
- **Retried**: OpenAI rate limits (exponential backoff)
- **Fatal**: Missing input file, invalid JSON from previous stage, missing API key, malformed .docx

### Logging
- **Console output**: Each stage prints progress to stdout (section names, entry counts, costs)
- **Prompt logs**: All LLM calls are logged to `prompt_logs/` directory
- **Debug mode**: Not implemented; inspect intermediate JSON files and prompt logs for debugging

### Prompt Logging
All LLM API calls are logged to the `prompt_logs/` directory with three file types per call:

```
prompt_logs/
├── {timestamp}_{operation}_{hash}.json           # Full request payload (messages, model, params)
├── {timestamp}_{operation}_{hash}_READABLE.txt   # Human-readable prompt text
└── {timestamp}_{operation}_{hash}_RESPONSE.json  # Full API response
```

**File naming convention:**
- `timestamp`: `YYYY-MM-DD_HH-MM-SS`
- `operation`: `taxonomy_mapping`, `hierarchy_extraction`, `header_validation`, etc.
- `hash`: Short unique identifier for the request

**Use cases:**
- Debug classification errors by reviewing exact prompts sent
- Analyze LLM responses for unexpected behavior
- Audit API costs and token usage
- Reproduce issues by replaying logged prompts

**⚠️ SECURITY WARNING**: Prompt logs contain **full CV text** including personal information (names, addresses, employment history, publications). Treat `prompt_logs/` as sensitive data:
- Do not commit to version control
- Do not share without redaction
- Delete after debugging if not needed for audit

### Caching & Re-run Behavior
- **No LLM caching**: Each run makes fresh API calls (no result caching)
- **Output overwrite**: Re-running with same UID overwrites all stage outputs
- **Idempotency**: Safe to re-run; same input produces same output (deterministic prompts, low temperature)
- **Partial re-runs**: Use `--stage` to start from a specific stage (e.g., `--stage 3` to re-run only taxonomy mapping)

### Performance
- **Runtime**: ~30-90 seconds per CV (depends on size and API latency)
- **Parallelization**: Not supported; process one CV at a time
- **Max CV size**: No hard limit; Stage 1 processes in chunks (~50 paragraphs per LLM call)
- **Throughput**: ~40-120 CVs/hour at current API limits

---

## Prompt Management

### Prompt Locations
Prompts are embedded in the stage scripts (not external files):

| Stage | File | Prompt Location |
|-------|------|-----------------|
| 1a | `signature_based_segmentation.py` | Pass 1 and Pass 2 normalization prompts |
| 1a (validation) | `header_validator.py` | `system_prompt` in `validate_headers_batch()` |
| 3a | `stage_3a_header_taxonomy_mapper.py` | `map_headers_to_taxonomy()` system prompt |
| 3b | `stage_3b_entry_classifier.py` | Entry classification prompts |

Note: Stage 1b and Stage 2 have no prompts (deterministic, no LLM calls).

### Prompt Conventions
- **JSON schema enforcement**: Stage 3 uses OpenAI's `response_format` with strict JSON schemas
- **Temperature**: 0.1-0.2 for deterministic outputs
- **System/User split**: System prompt contains instructions; user prompt contains CV content

### Updating Prompts
1. Edit the prompt directly in the relevant Python file
2. Test on 2-3 sample CVs with known good outputs
3. Compare `_mapped.json` results before/after

---

## Limitations & Assumptions

### Document Format Assumptions
- **Word styles**: Works best with CVs using Heading 1/2/3 styles; plain bold text also detected
- **Section ordering**: Assumes CV has recognizable sections (Education, Experience, Publications, etc.)
- **Language**: English only; non-English CVs will produce poor results

### Known Limitations
- **Tables**: Table content is extracted but may lose structure
- **Multi-column layouts**: Parsed as sequential paragraphs; column order may be wrong
- **Embedded images**: Ignored
- **Very large CVs**: Stage 1 processes large CVs in chunks (~50 paragraphs per LLM call), so size is not a hard limit

### Common Failure Patterns
| Pattern | Symptom | Mitigation |
|---------|---------|------------|
| No Word styles | Flat hierarchy (all H1) | Check `_segmented.txt`; may need manual structure |
| Dense publication lists | Entries merged together | Check `_delimiters.json` for missed boundaries |
| Unusual section names | Wrong taxonomy codes | Use `--mode guided` for better semantic analysis |
| Very long entries | Truncated text | Check `_entries.json` for full text |

---

## Troubleshooting

### Common Issues

| Problem | Likely Cause | Solution |
|---------|--------------|----------|
| "Headers not detected" | CV has no Word styles | Check `_segmented.txt`; consider manual preprocessing |
| "Empty entries" | Entry extraction failed | Check `_entries.json`; entries may be merged |
| "Wrong taxonomy code" | Ambiguous section header | Check `_header_taxonomy.json` for header mappings |
| "Rate limit error" | Too many API calls | Wait and retry; consider smaller batch size |
| "Stage 2 empty output" | Stage 1b failed silently | Check `_hierarchy_mapped.json` exists |
| "Unaccounted indices" | Coverage issue in Stage 2 | Check for synthetic headers in Stage 1b |
| "Stage 3b missing input" | Stage 3a not run | Run Stage 3a first or run full pipeline |

### Debug Workflow
1. **Check Stage 1a output**: Open `_segmented.txt` for human-readable hierarchy
2. **Check Stage 1b output**: Look at `_hierarchy_mapped.json` for coverage percentage
3. **Verify entry detection**: Count entries in `_entries.json` vs expected
4. **Check header mappings**: Review `_header_taxonomy.json` for taxonomy assignments
5. **Inspect classifications**: Look at `taxonomy_code` and `reasoning` in `_classified.json`
6. **Run individual stages**: Isolate which stage is failing

### Inspecting Intermediate Files
```bash
# View hierarchy structure
cat outputs/stage_1a_segmentation/{uid}_segmented.txt

# Check coverage in Stage 1b
python3 -c "import json; d=json.load(open('outputs/stage_1b_hierarchy_mapping/{uid}_hierarchy_mapped.json')); print(d['meta']['coverage'])"

# Count detected entries
python3 -c "import json; d=json.load(open('outputs/stage_2_entry_extraction/{uid}_entries.json')); print(len(d['entries']))"

# View header taxonomy mappings
python3 -c "import json; d=json.load(open('outputs/stage_3a_header_mappings/{uid}_header_taxonomy.json')); print(json.dumps(d['mappings'][:3], indent=2))"

# Check taxonomy distribution
python3 -c "import json; from collections import Counter; d=json.load(open('outputs/stage_3b_entry_classification/{uid}_classified.json')); print(Counter(e['taxonomy_code'] for e in d['classified_entries']))"
```

---

## Security & Privacy

### Data Handling
- **PII/PHI**: CVs contain personal information; outputs also contain this data
- **No encryption**: All files written as plain JSON
- **No automatic deletion**: Intermediate files persist until manually removed

### Recommendations
- Store input/output directories outside of version control
- Delete intermediate files after processing if not needed
- Do not commit CVs or outputs to git

### API Key Safety
- Set `OPENAI_API_KEY` as environment variable, not in code
- Do not log or print the API key

---

## Reproducibility & Versioning

### Output Reproducibility

| Factor | Value | Impact |
|--------|-------|--------|
| Temperature (Stage 1) | 0.2 | Low variation in hierarchy extraction |
| Temperature (Stage 2a) | 0.1 | Very stable delimiter detection |
| Temperature (Stage 3) | 0.1 | Very stable classification |

**Same input + same prompts = stable outputs?** Yes, with caveats:
- LLM outputs are deterministic at temperature ≤0.1
- Minor variations possible across API versions
- Validator overrides are fully deterministic

### Versioning

**Current Version**: V12.0

Version scope: Whole pipeline (not per-stage)

| Version | Change Type | Key Changes |
|---------|-------------|-------------|
| V9→V10 | Architecture | Two-pass → Guided classification |
| V10→V11 | Architecture | Stage 3a/3b split (header taxonomy + entry classification) |
| V11→V12 | Quality | 10-step post-classification correction pipeline |

**V12.0 Changes** (2025-11-28):
- Taxonomy v7.5 with refined K1-K4 teaching code definitions
- 7 new post-classification correctors (steps 4-10)
- Hierarchy mismatch QA flagging
- Based on expert evaluation achieving 86-91/100 accuracy

Version-specific behaviors documented in:
- `core/validators/README.md` (post-classification correctors)
- `core/CV_PIPELINE_ARCHITECTURE_V3.md` (V6 additions)
- `STAGE3_TAXONOMY_ARCHITECTURE.md` (V9 vs V10 comparison)

### Testing

**Current state**: No automated test suite. Testing is manual.

**Manual testing workflow**:
```bash
# Run on sample CV
python3 run_full_pipeline.py 'data/sample_cvs/word/2071_Zuschlag_Cv.docx' '2071_Zuschlag_Cv'

# Compare output to known-good result
diff outputs/stage_3_taxonomy_mapping/2071_Zuschlag_Cv_mapped.json expected/2071_Zuschlag_Cv_mapped.json
```

**Canonical test CV**: `2071_Zuschlag_Cv.docx` — used for regression testing

---

## Upstream & Downstream Integration

### What Consumes the Output?

The `_mapped.json` output is consumed by:
- **WCM Faculty Profile System**: Imports structured CV data
- **Bibliometric Analysis Tools**: Publication classification statistics
- **Manual Review Interface**: Human validation of classifications

### Integration Points

```
                    ┌─────────────────────┐
                    │   CV Parsing        │
   .docx input ───→ │   Pipeline          │ ───→ _mapped.json
                    │   (this system)     │
                    └─────────────────────┘
                              │
                              ▼
                    ┌─────────────────────┐
                    │   Downstream        │
                    │   - WCM profiles    │
                    │   - Analytics       │
                    │   - Review UI       │
                    └─────────────────────┘
```

### Operational Mode

- **Offline only**: No automatic upload; outputs written to local filesystem
- **Batch processing**: Run via CLI; no API server
- **Timestamps**: Each output includes processing timestamp in metadata

---

## Scale & Memory Limitations

| Constraint | Limit | Notes |
|------------|-------|-------|
| Max paragraphs per CV | ~2000 | Stage 1 chunks at ~50 paragraphs; no hard limit |
| Max Word doc size | ~10 MB | python-docx performance degrades above this |
| Memory footprint | ~500 MB | Peak during Stage 1 with large CVs |
| Concurrent CVs | 1 | No parallelization; process sequentially |
| Max entries per section | ~500 | Stage 3 batches at 10-15; larger sections take longer |

**Performance expectations**:
- Small CV (50 entries): ~20-30 seconds
- Medium CV (150 entries): ~45-90 seconds
- Large CV (300+ entries): ~2-3 minutes

---

## Project Structure

### CLI Tool, Not Library

This project is a **CLI tool**, not an importable Python library.

**Intended usage**:
```bash
# Run full pipeline
python3 run_full_pipeline.py 2097_Upton_Cv

# Run single stage
python3 run_full_pipeline.py 2097_Upton_Cv --stage 2a
```

**Not intended for**:
```python
# Don't do this - internal APIs may change
from unified_pipeline.stage_3_taxonomy_mapper import run_stage_3
```

### Directory Purpose

| Directory | Purpose |
|-----------|---------|
| `run_full_pipeline.py` | Main entry point (CLI) |
| `src/unified_pipeline/` | Internal implementation (not public API) |
| `data/sample_cvs/` | Input documents |
| `src/unified_pipeline/outputs/` | Output files |
| `prompt_logs/` | LLM call logs (for debugging) |
| `docs/` | Documentation |

---

## Deep Dive Documentation

- **Stage 3 Architecture**: [`STAGE3_TAXONOMY_ARCHITECTURE.md`](STAGE3_TAXONOMY_ARCHITECTURE.md)
  - Detailed explanation of candidate surfacing and guided classification
  - V9 vs V10 comparison
  - Cost analysis and optimization

- **Validators Guide**: `core/validators/README.md`
  - How to create new validators
  - Validator interface and best practices
  - Confidence levels and override thresholds

- **Historical Reference**: [`PIPELINE_ARCHITECTURE_V9.md`](PIPELINE_ARCHITECTURE_V9.md)
  - Original multi-stage pipeline design
  - Historical context for earlier versions

---

## Glossary

| Term | Definition |
|------|------------|
| **Hierarchy** | The section/subsection structure extracted from a CV (H1/H2/H3 levels) |
| **Entry** | A single item within a section (one publication, one grant, one position) |
| **Taxonomy Code** | WCM classification code (e.g., S1 = Peer-Reviewed Articles) |
| **Element Index** | Paragraph number in the Word document (0-indexed) |
| **Synthetic Header** | LLM-generated grouping header (e.g., "PUBLICATIONS") not in original document |
| **Coverage** | Percentage of document indices accounted for in Stage 2 (should be 100%) |
| **Header Taxonomy Mapping** | Stage 3a process: map CV section headers to taxonomy codes with confidence |
| **Entry Classification** | Stage 3b process: classify individual entries using header context |
| **Taxonomy Options** | Array of possible codes with confidence weights for ambiguous mappings |
| **Parent Code** | Top-level taxonomy category (A-T) |
| **Child Code** | Specific subcategory (e.g., S1, S2, K3) |
| **Validation Flag** | Warning/error raised by validators for potential misclassification |
| **Confusion Matrix** | Definitions of which taxonomy codes are commonly confused |
| **Validator** | Deterministic rule that detects specific confusion patterns |
| **Disambiguation** | Process of resolving ambiguous classifications (e.g., H vs I for fellowships) |
| **Field Extraction** | Stage 4 process: extract structured fields (authors, dates, etc.) from entry text |
| **Extracted Fields** | Structured key-value pairs extracted from entry text (e.g., `{"authors": "...", "title": "..."}`) |
| **Research Summary (M1)** | Biosketch-style narrative summarizing research activities and contributions |
| **PubMed Enrichment** | Stage 5 process: add authoritative metadata from NCBI PubMed to publications |
| **ROR** | Research Organization Registry - historical reference; Stage 5b now uses LLM-based enrichment |
| **Institution Enrichment** | Stage 5b process: add city/state/country data to education and position entries |
| **WCM Template** | Weill Cornell Medicine standard CV format with predefined sections |
| **CV Owner** | The person whose CV is being processed (used for name highlighting) |
| **CV Owner Location** | LLM-inferred current location of the CV owner based on employment/education history |
| **Metro Area** | Metropolitan area inferred from CV owner's affiliations (e.g., "New York City", "Boston") |
| **Geographic Scope** | Classification of activities as Regional, National, or International based on location |
| **Location Inference** | Stage 4 process: use LLM to determine CV owner's current location from position history |

---

**Last Updated**: 2026-02-01
**Current Version**: V13.0 (full pipeline with enrichment stages 4-6, LLM institution enrichment, teaching/citation formatting, deduplication)
