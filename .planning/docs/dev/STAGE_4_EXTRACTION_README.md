# Stage 4: Field Extraction

## Overview

Stage 4 extracts structured fields from Stage 3b classified entries. The goal is to transform free-text CV entries into structured data that can be:
1. Displayed in WCM template tables
2. Used for enrichment (e.g., PubMed lookup via DOI/PMID)
3. Reassembled into alternative formats (e.g., different citation styles)

---

## Larger Goals

### 1. Model Selection Strategy
- **Evaluate both**: gpt-4o-mini vs gpt-5.1 on representative sample
- **Choose one**: Based on performance AND cost tradeoffs
- **Stick with it**: No per-entry retry logic (simplifies pipeline, predictable costs)

Decision criteria:
- Extraction coverage percentage
- Field accuracy (spot-check sample)
- Cost per CV
- Latency

### 2. High Mapping Percentage
We aim to map a large proportion of the raw text to structured fields.

**Metric**: `extraction_coverage_percent` - percentage of content words from original text that appear in extracted fields.

### 3. Exclusions
We intentionally do NOT extract:
- **Stray punctuation** - artifacts like `:?` or trailing commas
- **Formatting labels** - "Featured:", "Submitted:", "In Review:" prefixes
- **Boilerplate text** - "Curriculum Vitae", page numbers, dates updated
- **Redundant hierarchy info** - section headers that duplicate the taxonomy code

---

## Field Extraction Philosophy

### When to Extract a Field

A field should be extracted if it satisfies ONE of these criteria:

| Criterion | Description | Examples |
|-----------|-------------|----------|
| **A. Table Column** | It's a separate column in a WCM template table | NPI, DEA number, license state |
| **B. Reassembly** | Needed to reassemble data in a different format | author, year, title, journal (for citations) |
| **C. Enrichment** | Can be used for downstream enrichment | DOI → PubMed, PMID → citation metrics |
| **D. Common Standalone** | Sufficiently common standalone attribute | ORCID, institution, degree |

### When NOT to Extract

If something doesn't satisfy the above criteria, we generally **do not extract it**.

However, **err on the side of inclusion** until we see how it looks in the output Word document. We can always remove fields later, but missing data is harder to recover.

| Consider Extracting | Reason |
|---------------------|--------|
| `hours_per_week` | May be useful for effort reporting - TBD based on Word output |
| `number_of_students` | May appear in teaching tables - TBD |

| Definitely Skip | Reason |
|-----------------|--------|
| `project_description` within a position | Use `narrative` field instead |
| Redundant hierarchy labels | Already captured in taxonomy |

### The Narrative Field

For entries with substantive prose that doesn't fit structured fields, we use a **single `narrative` attribute** across all taxonomy codes. The interpretation depends on the section/subsection context.

```json
{
  "taxonomy_code": "M1",
  "extracted_fields": {
    "research_area": "Cancer epidemiology",
    "narrative": "My research focuses on understanding the social determinants of cancer outcomes, with particular emphasis on rural-urban disparities..."
  }
}
```

**How `narrative` is interpreted by context**:

| Taxonomy | Narrative Contains |
|----------|-------------------|
| M1 (Research Activities) | Research statement - the narrative IS the content |
| D1 (Academic Position) | Embedded project descriptions, responsibilities |
| S1 (Publication) | Meta-statements: "co-authored with my mentee", "invited contribution" |
| K3 (Educational Leadership) | Program description, scope of responsibilities |
| H (Honors) | Context about the award significance |

The same `narrative` field serves all these purposes - the taxonomy code tells us how to interpret it.

---

## Reformatting Philosophy

### Target Style: Vancouver

All reformatting targets **Vancouver citation style**, the standard for biomedical publications.

### What We Reformat

We use a mix of **LLM + regex** to normalize specific field types:

| Field Type | Normalization | Method |
|------------|---------------|--------|
| **Years** | `2019-2024`, `2019-present` → `start_date`, `end_date` | Regex |
| **PMIDs** | Extract from text: `PMID: 12345678` → `12345678` | Regex |
| **PMCIDs** | Extract from text: `PMC7277806` → `PMC7277806` | Regex |
| **DOIs** | Normalize: `doi.org/10.1234/...` → `10.1234/...` | Regex |
| **Author names** | Normalize to Vancouver style (see below) | Regex |

### Vancouver Author Formatting

Authors are normalized to Vancouver style:
- **Format**: `LastName AB` (no comma between name and initials)
- **Initials**: No periods, no spaces (`JA` not `J.A.` or `J. A.`)
- **Separator**: Comma between authors
- **Et al**: Standardized to `et al.` with period

| Input | Output |
|-------|--------|
| `Smith, John A., Jones, Mary B.` | `Smith JA, Jones MB` |
| `Smith J.A., Jones M.B.` | `Smith JA, Jones MB` |
| `Smith, J. A. and Jones, M. B.` | `Smith JA, Jones MB` |
| `John A. Smith, Mary B. Jones` | `Smith JA, Jones MB` |
| `Smith JA, Jones MB, et al.,` | `Smith JA, Jones MB, et al.` |

### What We Do NOT Reformat

Most content should **preserve the CV author's original text**:

| Keep Original | Reason |
|---------------|--------|
| Award names | Author knows the correct name |
| Institution names | Variations are intentional |
| Course titles | May include special formatting |
| Committee names | Exact wording matters |
| Grant titles | Must match official records |

### Tracking Changes

When we reformat something, we log it in `reformatted_fields` for transparency:

```json
{
  "extracted_fields": {
    "authors": "Smith JA, Jones MB, et al.",
    "pmid": "12345678",
    "doi": "10.1234/example"
  },
  "reformatted_fields": {
    "authors": {
      "original": "Smith, John A., Jones, Mary B., et al.",
      "reformatted": "Smith JA, Jones MB, et al.",
      "reason": "Normalized author format"
    },
    "pmid": {
      "original": "PMID: 12345678",
      "reformatted": "12345678",
      "reason": "Extracted PMID number"
    }
  }
}
```

**Stage 5 (Word template generation)** will use this to:
1. Insert the reformatted version
2. Add a Word comment or tracked change showing the original
3. Let the user accept/reject the change

Example Word output:
```
Smith JA, Jones MB, et al. [Comment: "Reformatted from 'Smith, John A., Jones, Mary B., et al.'"]
```

---

## Field Schemas by Taxonomy Code

**Note**: All taxonomy codes include a `narrative` field for capturing prose content that doesn't fit structured fields. See "The Narrative Field" section above.

### Publications (S1-S9)

| Code | Description | Fields |
|------|-------------|--------|
| S0 | Researcher Profile | `orcid`, `google_scholar_url`, `scopus_id`, `ncbi_url`, `h_index`, `total_citations`, `narrative` |
| S1 | Peer-reviewed Articles | `authors`, `year`, `title`, `journal`, `volume`, `issue`, `pages`, `doi`, `pmid`, `pmcid`, `target_name`, `narrative` |
| S2 | Reviews/Editorials | Same as S1 |
| S3 | Books | `authors`, `editors`, `year`, `title`, `publisher`, `edition`, `isbn`, `narrative` |
| S4 | Book Chapters | `authors`, `year`, `chapter_title`, `book_title`, `editors`, `publisher`, `pages`, `doi`, `narrative` |
| S5 | Technical Reports | `authors`, `year`, `title`, `publication_venue`, `report_number`, `url`, `narrative` |
| S6 | Case Reports | Same as S1 |
| S7 | In Review/Submitted | `authors`, `year`, `title`, `status`, `target_journal`, `narrative` |
| S8 | Abstracts/Proceedings | `authors`, `year`, `title`, `conference_name`, `location`, `abstract_number`, `doi`, `narrative` |
| S9 | Other Media | `authors`, `year`, `title`, `media_type`, `venue`, `url`, `narrative` |

### Grants (M2A, M2B, M2C)

| Code | Description | Fields |
|------|-------------|--------|
| M2A | Active Grants | `grant_number`, `title`, `pi_role`, `agency`, `start_date`, `end_date`, `total_funding`, `percent_effort` |
| M2B | Completed Grants | Same as M2A (without `percent_effort`) |
| M2C | Pending Grants | `grant_number`, `title`, `pi_role`, `agency`, `total_funding_requested`, `submission_date` |

### Positions (D1, D2, D3)

| Code | Description | Fields |
|------|-------------|--------|
| D1 | Academic Appointments | `title`, `institution`, `department`, `start_date`, `end_date`, `track`, `tenure_status` |
| D2 | Hospital Appointments | `title`, `institution`, `department`, `start_date`, `end_date`, `appointment_type` |
| D3 | Other Professional | `title`, `organization`, `department`, `start_date`, `end_date`, `role_type` |

**Note**: For positions with embedded project descriptions, use `narrative` field.

### Teaching (K1-K5)

| Code | Description | Fields |
|------|-------------|--------|
| K1 | Didactic Teaching | `course_code`, `course_title`, `institution`, `role`, `level`, `start_date`, `end_date` |
| K2 | Clinical Teaching | `teaching_role`, `institution`, `setting`, `learner_level`, `start_date`, `end_date` |
| K3 | Educational Leadership | `program_name`, `role`, `institution`, `start_date`, `end_date`, `narrative` |
| K4 | CME/Professional Ed | `activity_title`, `institution`, `role`, `date`, `cme_credits` |
| K5 | Community Education | `activity_title`, `audience`, `location`, `date` |

---

## Post-Extraction Processing

### 1. Regex Enhancement Pass

After LLM extraction, apply regex to catch commonly missed patterns:

```python
REGEX_PATTERNS = {
    'pmid': r'PMID[:\s]*(\d{7,8})',
    'pmcid': r'PMC(\d+)',
    'doi': r'10\.\d{4,}/[^\s\]>]+',
    'orcid': r'\d{4}-\d{4}-\d{4}-\d{3}[\dX]',
    'year_range': r'(\d{4})\s*[-–—]\s*(\d{4}|present|current)',
}
```

### 2. Field Normalization Pass

Clean up extracted values:

```python
def normalize_authors(authors: str) -> str:
    """Remove trailing punctuation, normalize separators."""
    # "Smith, J.," → "Smith J"
    # "Smith, John A." → "Smith JA"
    ...

def normalize_title(title: str) -> str:
    """Remove leading labels and stray punctuation."""
    # "Featured: My Paper Title" → "My Paper Title"
    # "park use:?19(4)" → "park use"
    ...
```

### 3. Coverage Calculation

Track what was extracted vs. what remains:

```json
{
  "extraction_coverage": {
    "extraction_coverage_percent": 87.5,
    "total_original_words": 48,
    "total_extracted_words": 42,
    "unextracted_words": ["hours", "week", "approximately"]
  }
}
```

---

## Error Handling Strategy

### Low Coverage Entries

If `extraction_coverage_percent < 50%`:

1. **Check if narrative-heavy**: If taxonomy is M1, K3, or similar, low coverage is expected
2. **Retry with gpt-5.1**: May extract more fields with better reasoning
3. **Flag for review**: Add `extraction_quality: "low"` for manual inspection

### JSON Parse Errors

If LLM returns invalid JSON:

1. **Attempt repair**: Try `json.loads()` with lenient parsing
2. **Retry once**: Sometimes the model just needs another attempt
3. **Fall back**: Return empty `extracted_fields` with `extraction_error: true`

### Missing Expected Fields

If a publication is missing DOI/PMID:

1. **Regex scan**: Check original text for patterns
2. **Leave null**: Don't fabricate identifiers
3. **Downstream enrichment**: Stage 5 can attempt PubMed lookup by title+author

---

## Output Schema

```json
{
  "document_uid": "2015_Wende",
  "stage": "4",
  "stage_name": "Field Extraction",
  "source_stage": "3b",
  "total_entries": 248,
  "stats": {
    "extracted": 235,
    "skipped": 13,
    "fragments_skipped": 8,
    "duplicates_skipped": 5,
    "avg_coverage_percent": 82.3
  },
  "entries": [
    {
      "element_idx_start": 42,
      "taxonomy_code": "S1",
      "text": "Smith JA, Jones MB. Title of paper. Journal Name. 2023;45(3):123-456. doi:10.1234/example. PMID: 12345678.",
      "extracted_fields": {
        "authors": "Smith JA, Jones MB",
        "year": "2023",
        "title": "Title of paper",
        "journal": "Journal Name",
        "volume": "45",
        "issue": "3",
        "pages": "123-456",
        "doi": "10.1234/example",
        "pmid": "12345678",
        "target_name": "Smith"
      },
      "extraction_success": true,
      "extraction_coverage": {
        "extraction_coverage_percent": 95.2,
        "unextracted_words": []
      }
    }
  ]
}
```

---

## Usage

```bash
# Run Stage 4 only (requires Stage 3b output)
python3 run_full_pipeline.py 2015_Wende --stage 4

# Run with cheaper model
python3 run_full_pipeline.py 2015_Wende --stage 4 --model gpt-4o-mini

# Run full pipeline
python3 run_full_pipeline.py 2015_Wende
```

---

## Design Decisions

### Profile URLs - Where They Go

URLs are routed based on their **purpose**, not just their domain:

| URL Type | Taxonomy | Rationale |
|----------|----------|-----------|
| Personal website | A (Contact) | Contact/identity information |
| LinkedIn profile | A (Contact) | Professional identity, contact |
| ORCID | A (Contact) | Researcher identifier |
| Institutional faculty page | A (Contact) | Contact information |
| Google Scholar | S0 (Researcher Profile) | Publication list/metrics |
| ResearchGate | S0 (Researcher Profile) | Publication list |
| PubMed/NCBI bibliography | S0 (Researcher Profile) | Publication list |
| Scopus Author ID | S0 (Researcher Profile) | Publication metrics |

**Rule of thumb**: If the URL points to a **publication list or citation metrics**, it's S0. If it's **identity/contact**, it's A.

### Enrichment Strategy

For publications with identifiers (DOI, PMID, PMCID), we may query PubMed in a **separate enrichment step** (not in Stage 4):

| Has | Action |
|-----|--------|
| PMID | Query PubMed for full metadata |
| DOI | Query PubMed/CrossRef for metadata |
| PMCID | Query PubMed for PMID, then full metadata |
| Title + Author only | No automatic enrichment (too risky) |

Enrichment adds/validates: citation counts, MeSH terms, full author list, publication type.

**Important**: Enrichment only runs when we have a matching record. We never fabricate identifiers.

## Open Questions

1. **Narrative threshold**: How much prose justifies using `narrative` vs. trying to extract more fields?

2. **Funding in narratives**: Should we add `funding_source` to M1/research activities, or leave for enrichment?

3. **Parent-child relationships**: GRA position → nested projects. Is flat extraction sufficient, or do we need linkage?

---

## Field Schema Configuration

Field schemas are now externalized to versioned JSON config files in `config/`:

```
config/
├── field_schemas_v1.0.json   # Initial version
└── field_schemas_v1.1.json   # Current (minimal extraction)
```

### Schema v1.1 Features

- **`extract` flag**: Each field has `"extract": true/false` to control LLM extraction
- **`category`**: Fields categorized as `template`, `enrichment`, `reconstruction`, or `optional`
- **`extraction_mode`**: Schema-level setting (`minimal` or `comprehensive`)

### Extraction Modes

| Mode | Description | Codes |
|------|-------------|-------|
| `minimal` | Only WCM template columns | D1, D2, H, I, P, Q, R, etc. (40 codes) |
| `comprehensive` | Full extraction for reconstruction | S1-S9, M2A-C, N3A-B (15 codes) |
| `narrative_primary` | Content is narrative | M1 (Research Activities) |
| `enrichment` | Extract for downstream lookup | S0 (Researcher Profiles) |

### Stats (v1.1)

- 55 taxonomy codes
- 279/435 fields active (36% reduction)
- Reduces token usage for non-publication entries

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 3.0 | 2025-11-29 | Versioned field schemas in config/, minimal vs comprehensive extraction modes, target_name from raw text |
| 2.1 | 2025-11-29 | Added `narrative` field to all schemas, regex post-processing for PMIDs/DOIs, `reformatted_fields` tracking |
| 2.0 | 2024-11-29 | Complete schema overhaul, 60+ taxonomy codes, Stage 3b integration |
| 1.0 | 2024-11-24 | Initial field extractor with basic schemas |
