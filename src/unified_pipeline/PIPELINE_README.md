# CV Parsing Pipeline

A multi-stage LLM pipeline for extracting structured data from faculty CVs and reformatting them to WCM (Weill Cornell Medicine) template format.

## Pipeline Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           CV PARSING PIPELINE                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  STAGE 1a: Document Segmentation                                            │
│  ├── Input: .docx file                                                      │
│  ├── Process: Parse document structure, identify headers                    │
│  └── Output: *_segmented.json                                               │
│                                                                             │
│  STAGE 1b: Hierarchy Mapping                                                │
│  ├── Input: Segmented document                                              │
│  ├── Process: Map section hierarchy, identify element boundaries            │
│  └── Output: *_hierarchy_mapped.json                                        │
│                                                                             │
│  STAGE 2: Entry Extraction                                                  │
│  ├── Input: Stage 1b hierarchy + .docx file                                 │
│  ├── Process: LLM identifies entry boundaries using blank line markers      │
│  ├── Features: Multi-line entry grouping, table row handling                │
│  └── Output: *_entries.json                                                 │
│                                                                             │
│  STAGE 3a: Header Taxonomy Mapping                                          │
│  ├── Input: Segmented document headers                                      │
│  ├── Process: Map CV section headers to WCM taxonomy codes                  │
│  └── Output: *_header_taxonomy.json                                         │
│                                                                             │
│  STAGE 3b: Entry Classification                                             │
│  ├── Input: Segmented entries + header mappings                             │
│  ├── Process: Classify each entry to specific taxonomy code (S1, M2A, etc.) │
│  ├── Post-processing: Validators, correctors, fragment reconnection         │
│  └── Output: *_classified.json                                              │
│                                                                             │
│  STAGE 4: Field Extraction                                                  │
│  ├── Input: Classified entries                                              │
│  ├── Process: Extract structured fields per taxonomy code                   │
│  ├── Features: target_name identification, Vancouver formatting             │
│  └── Output: *_fields.json                                                  │
│                                                                             │
│  STAGE 5: PubMed Enrichment                                                 │
│  ├── Input: Stage 4 field extraction output                                 │
│  ├── Process: Look up publications by PMID/PMCID/DOI in PubMed              │
│  ├── Features: ID conversion, batch fetching, authoritative metadata        │
│  └── Output: *_enriched.json                                                │
│                                                                             │
│  STAGE 5b: Institution Enrichment (ROR API)                                 │
│  ├── Input: Stage 5 enriched output                                         │
│  ├── Process: Look up institutions in ROR (Research Organization Registry)  │
│  ├── Features: City, state, country lookup for B, C, D entries              │
│  └── Output: *_institution_enriched.json                                    │
│                                                                             │
│  STAGE 5c: Teaching Formatter (LLM)                                         │
│  ├── Input: Stage 5b output (or earlier)                                    │
│  ├── Process: LLM reformats K-code entries for consistent, polished output  │
│  ├── Features: Date standardization, role formatting, sub-bullet handling   │
│  └── Output: *_teaching_formatted.json                                      │
│                                                                             │
│  STAGE 5d: Citation Formatter (LLM)                                         │
│  ├── Input: Stage 5c output (or earlier)                                    │
│  ├── Process: LLM reformats non-enriched citations to Vancouver format      │
│  ├── Features: Author name normalization, book chapter "In:" formatting     │
│  └── Output: *_citation_formatted.json                                      │
│                                                                             │
│  STAGE 6: WCM Word Template Generation                                      │
│  ├── Input: Stage 5d output (or 5c, 5b, 5, or 4)                            │
│  ├── Process: Fill WCM template with extracted/enriched data                │
│  ├── Features: Bold target author, Vancouver citations, table population    │
│  └── Output: *_wcm.docx                                                     │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Quick Start

```bash
# Run full pipeline on a CV (Stages 1-4)
python3 run_full_pipeline.py data/sample_cvs/word/2015_Wende.docx

# Run specific stage only
python3 run_full_pipeline.py 2015_Wende --stage 4

# Run with different model
python3 run_full_pipeline.py 2015_Wende --model gpt-4o-mini

# Run Stage 5: PubMed Enrichment
python3 stage_5_pubmed_enrichment.py 2015_Wende

# Run Stage 5b: Institution Enrichment (ROR lookup for locations)
python3 stage_5b_institution_enrichment.py 2015_Wende

# Run Stage 5c: Teaching Formatter (LLM-polish K-code entries)
python3 run_full_pipeline.py 2015_Wende --stage 5c

# Run Stage 6: Generate WCM Word document
python3 stage_6_word_template.py 2015_Wende
```

## Directory Structure

```
unified_pipeline/
├── run_full_pipeline.py          # Main entry point
├── PIPELINE_README.md            # This file
│
├── config/                       # Configuration files
│   ├── field_schemas_v1.1.json   # Field extraction schemas (versioned)
│   └── taxonomy_v7.json          # Taxonomy code definitions
│
├── core/                         # Core components
│   ├── taxonomy_mapper_v2.py     # Header taxonomy mapping
│   ├── classification_rules/     # LLM classification rules (versioned)
│   └── validators/               # Pre/post classification validators
│
├── segmentation/                 # Stage 1 document parsing
│   └── docx_parser.py
│
├── stage_3b_entry_classifier.py      # Entry classification
├── stage_4_field_extractor.py        # Field extraction
├── stage_5_pubmed_enrichment.py      # PubMed enrichment
├── stage_5b_institution_enrichment.py # Institution location enrichment (ROR)
├── stage_5c_teaching_formatter.py    # Teaching/K-code formatting (LLM)
├── stage_5d_citation_formatter.py    # Non-enriched citation formatting (LLM)
├── stage_6_word_template.py          # WCM document generation
│
├── outputs/                          # Pipeline outputs by stage
│   ├── stage_1a_segmentation/
│   ├── stage_3a_header_mappings/
│   ├── stage_3b_classified_entries/
│   ├── stage_4_field_extraction/
│   ├── stage_5_enrichment/
│   ├── stage_5b_institution_enrichment/
│   ├── stage_5c_teaching_formatted/
│   ├── stage_5d_citation_formatted/
│   └── stage_6_wcm_documents/
│
└── docs/                         # Documentation
    └── STAGE_4_EXTRACTION_README.md
```

## Taxonomy Codes

The pipeline uses a hierarchical taxonomy aligned with the WCM CV template. Key codes:

| Code | Description | WCM Section |
|------|-------------|-------------|
| **A** | Personal/Contact Information | PERSONAL DATA |
| **B1** | Academic Degrees | EDUCATION |
| **B2** | Other Educational Experiences | EDUCATION |
| **C** | Postdoctoral Training (AFTER terminal degree only) | POSTDOCTORAL TRAINING |
| **D1** | Academic Appointments | PROFESSIONAL POSITIONS |
| **D2** | Hospital Appointments | PROFESSIONAL POSITIONS |
| **D3** | Other Professional Positions (incl. pre-doctoral GRA/GTA) | PROFESSIONAL POSITIONS |
| **F1** | Licensure | LICENSURE |
| **F2** | Board Certification | BOARD CERTIFICATION |
| **H** | Honors and Awards | HONORS, AWARDS |
| **I** | Professional Memberships | PROFESSIONAL ORGANIZATIONS |
| **K1-K5** | Teaching Activities | EDUCATIONAL CONTRIBUTIONS |
| **M1** | Research Activities | RESEARCH |
| **M2A** | Current Grants | RESEARCH SUPPORT |
| **M2B** | Completed Grants | RESEARCH SUPPORT |
| **M2C** | Pending Grants | RESEARCH SUPPORT |
| **M3** | Patents | PATENTS & INVENTIONS |
| **N3A/N3B** | Mentees (Current/Past) | MENTORING |
| **O** | Institutional Leadership | LEADERSHIP |
| **P** | Institutional Committees | ADMINISTRATIVE |
| **Q1-Q4** | Extramural Service | EXTRAMURAL |
| **R** | Invited Presentations | INVITATIONS TO SPEAK |
| **S1** | Peer-reviewed Articles | BIBLIOGRAPHY |
| **S2** | Reviews/Editorials | BIBLIOGRAPHY |
| **S3** | Books | BIBLIOGRAPHY |
| **S4** | Book Chapters | BIBLIOGRAPHY |
| **S7** | Manuscripts in Review | BIBLIOGRAPHY |
| **S8** | Abstracts/Proceedings | BIBLIOGRAPHY |
| **T** | Miscellaneous | N/A |

## Stage 4: Field Extraction

### Field Schema Configuration

Field schemas are versioned and stored in `config/field_schemas_v1.1.json`. Each schema defines:

- **extraction_mode**: `minimal` (template fields only) or `comprehensive` (full extraction)
- **fields**: List of fields to extract, each with:
  - `extract`: Whether to include in LLM prompt (true/false)
  - `category`: `template`, `enrichment`, `reconstruction`, or `optional`
  - `wcm_column`: Corresponding WCM template column (if applicable)

### Extraction Philosophy

```
MINIMAL EXTRACTION (40 codes)
├── Extract only WCM template table columns
├── Reduces token usage and cost
└── Examples: D1, H, I, P, Q, R

COMPREHENSIVE EXTRACTION (15 codes)
├── Full extraction for reconstruction/enrichment
├── S-codes: Citation reconstruction in Vancouver format
├── M2-codes: Complete grant information
└── N3-codes: Full mentee details
```

### Current Stats (v1.1)

- **55 taxonomy codes** defined
- **279/435 fields active** (36% reduction from optional fields)
- **40 codes** use minimal extraction
- **15 codes** use comprehensive extraction

### target_name Extraction

For publications (S-codes) and presentations (R), the pipeline identifies the CV owner's name as it appears in author lists:

1. CV owner name extracted from document UID (e.g., "2015_Wende" → "Wende")
2. Handles filename patterns with initials (e.g., "2003_Albrechtjs" → "Albrecht")
3. Searches raw citation text for matching author
4. Supports author markers (*, †, ‡) for disambiguation

### Vancouver Citation Formatting

Publications are normalized to Vancouver citation style:
- Author format: `LastName AB` (no comma, no periods in initials)
- Multiple authors: `Smith JA, Jones MB, et al.`

## Validators

The pipeline includes pre- and post-classification validators:

### Pre-Classification (runs during mapping)
- `LabelContentConflictValidator`: Detects generic labels with specific content
- `S7UnpublishedValidator`: Identifies unpublished work patterns
- `URLDomainValidator`: URL-based classification hints

### Post-Classification (auto-corrects common errors)
- `StructuralHeaderValidator`: CV titles, page numbers → T
- `CommitteePositionCorrector`: Committee service (P/Q2) vs positions (D)
- `GrantStatusCorrector`: M2A/M2B/M2C based on dates
- `TeachingLeadershipCorrector`: Course Directors → K3
- `InvitedTalkCorrector`: Keynotes → R (not S8)

## Output Format

### Stage 4 Output (`*_fields.json`)

```json
{
  "document_uid": "2015_Wende",
  "stage": "4",
  "cv_owner": {"last_name": "Wende"},
  "schema_version": "1.1",
  "stats": {
    "total_entries": 248,
    "extracted": 235,
    "avg_coverage_percent": 82.3
  },
  "entries": [
    {
      "element_idx_start": 42,
      "taxonomy_code": "S1",
      "text": "Wende ME, Smith J. Title. Journal. 2023;45:123.",
      "extracted_fields": {
        "authors": "Wende ME, Smith J",
        "year": "2023",
        "title": "Title",
        "journal": "Journal",
        "volume": "45",
        "pages": "123",
        "target_name": "Wende ME"
      }
    }
  ]
}
```

## Cost Management

The pipeline logs estimated costs per LLM call:

```
[S1] 15 entries | 2,450 tokens | $0.0073
[M2A] 8 entries | 1,890 tokens | $0.0057
Batch 1/5 complete | Running total: $0.0130
```

### Cost Optimization
- Minimal extraction for non-publication codes reduces token usage
- Batch processing groups entries by taxonomy code
- Model selection: gpt-4o-mini for cost-sensitive runs

## Stage 5: PubMed Enrichment

Stage 5 enriches publication entries with authoritative PubMed metadata.

### Lookup Strategy

| Identifier Available | Strategy |
|---------------------|----------|
| PMID | Direct efetch lookup (fastest) |
| PMCID only | Convert via NCBI ID Converter → efetch |
| DOI only | esearch by DOI → efetch |
| None | Skip enrichment |

### What Gets Enriched

- **Authors**: Full author list in Vancouver format from PubMed
- **Journal**: Official journal name
- **Volume/Issue/Pages**: Fills in missing fields
- **PMID/PMCID**: Discovers missing identifiers
- **Publication Types**: Journal Article, Review, Case Report, etc.
- **MeSH Terms**: Medical Subject Headings for the article

### Output Structure

```json
{
  "enrichment_status": "enriched",
  "enrichment_source": "pmid",
  "enriched_fields": ["pmcid", "volume"],
  "enrichment_data": {
    "pubmed_authors": "Smith JA, Jones MB, et al",
    "pubmed_journal": "New England Journal of Medicine",
    "publication_types": ["Journal Article", "Randomized Controlled Trial"],
    "mesh_terms": ["COVID-19", "Vaccines"]
  }
}
```

### Usage

```bash
# Enrich Stage 4 output
python3 stage_5_pubmed_enrichment.py 2015_Wende

# With custom output path
python3 stage_5_pubmed_enrichment.py 2015_Wende -o custom_output.json
```

### Rate Limiting

- With NCBI API key: 10 requests/second
- Without API key: 3 requests/second
- Set `NCBI_API_KEY` or `PUBMED_API_KEY` environment variable

---

## Stage 5b: Institution Enrichment (ROR API)

Stage 5b enriches education and position entries with institution location data using the ROR (Research Organization Registry) API, which is the successor to GRID.

### What Gets Enriched

Entries with these taxonomy codes are eligible for institution enrichment:
- **B1, B2**: Education (university locations)
- **C, C1, C2**: Postdoctoral Training
- **D1, D2, D3**: Professional Positions

### Enrichment Data

For each institution, ROR provides:
- **City**: e.g., "Columbus"
- **State**: e.g., "Ohio" (converted to "OH" for US)
- **Country**: e.g., "United States"
- **ROR ID**: Unique identifier for the institution

### Output Structure

```json
{
  "institution_enrichment": {
    "ror_id": "https://ror.org/00rs6vg23",
    "official_name": "Ohio State University",
    "city": "Columbus",
    "state": "Ohio",
    "country": "United States",
    "country_code": "US",
    "source": "ror_api"
  }
}
```

### Usage

```bash
# Enrich Stage 5 output with institution locations
python3 stage_5b_institution_enrichment.py 2015_Wende

# Or specify full path
python3 stage_5b_institution_enrichment.py outputs/stage_5_enrichment/2015_Wende_enriched.json
```

### Caching

ROR lookups are cached to `config/ror_cache.json` to avoid redundant API calls and improve performance on subsequent runs.

---

## Stage 6: WCM Word Template Generation

Stage 6 generates a WCM-formatted Word document from enriched data.

### Features

- **Fills all WCM sections**: Personal Data, Education, Positions, Research Support, Bibliography
- **Bolds target author**: CV owner's name is bold in all citations
- **Vancouver citations**: Proper biomedical citation format
- **Table population**: Fills WCM template tables

### Section Mapping

| Taxonomy Codes | WCM Section |
|---------------|-------------|
| A | Personal Data |
| B1, B2 | Education |
| D1, D2, D3 | Professional Positions |
| M2A, M2B, M2C | Research Support |
| S1-S9 | Bibliography |

### Usage

```bash
# Generate from Stage 5 enriched output
python3 stage_6_word_template.py 2015_Wende

# Or from Stage 4 output (without enrichment)
python3 stage_6_word_template.py outputs/stage_4_field_extraction/2015_Wende_fields.json
```

### Output

Generated documents are saved to `outputs/stage_6_wcm_documents/`.

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.5 | 2025-12-01 | Taxonomy: Graduate Research/Teaching Assistants → D3 (not C); Consultant → D3 (not D1); C is ONLY for post-terminal-degree training |
| 1.4 | 2025-12-01 | Stage 2: Added blank line markers to improve multi-line entry grouping |
| 1.3 | 2025-11-29 | Added Stage 5b (Institution Enrichment via ROR API), postdoc training section |
| 1.2 | 2025-11-29 | Added Stage 5 (PubMed Enrichment) and Stage 6 (WCM Word Template) |
| 1.1 | 2025-11-29 | Added extract flags to field schemas, minimal vs comprehensive modes |
| 1.0 | 2025-11-29 | Initial versioned schemas aligned with WCM template |

## Related Documentation

- [Stage 4 Extraction Details](docs/STAGE_4_EXTRACTION_README.md)
- [Validators README](core/validators/README.md)
- [Classification Rules](core/classification_rules/README.md)
