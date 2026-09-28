# CViche Output Quality Evaluation Guide

This document defines the systematic procedure for evaluating and improving CViche output quality. It captures the methodology proven effective during hands-on debugging sessions and serves as a reference for future optimization work.

## How to Use This Guide

When optimizing CViche for a specific CV, follow the phases in order. Each phase builds on findings from the previous one:

1. **Audit** the output document to catalog every defect
2. **Trace** each defect backward through pipeline stages to find the root cause
3. **Classify** defects by root cause to prioritize fixes
4. **Fix** starting with Stage 6 (cheapest to fix) and working upstream
5. **Verify** each fix, then do a full regression check

---

## Phase 1: Full Document Audit

### 1.1 Setup

Provide the specific output file path:
```
/src/unified_pipeline/outputs/stage_6_wcm_documents/{UID}_{name}_wcm.docx
```

### 1.2 Structural Audit

Inspect every table in the output document using python-docx. For each table:

| Check | What to look for | Example defect |
|-------|-----------------|----------------|
| **Row count** | 0 data rows = section not populated | Education table with only header row |
| **Empty columns** | Columns that should have data but are blank across all rows | Organization column in Honors all empty |
| **Placeholder text** | WCM template placeholder text still present | `[List degrees...]` not replaced |
| **Column header leakage** | Source CV table headers appearing as data rows | "Name of award" appearing as an award entry |

### 1.3 Data Quality Checks by Section

Run these checks against every populated section:

#### Date Format Compliance

Each WCM section requires a specific date format. Verify dates match:

| Taxonomy Code | Section | Required Format | Example |
|--------------|---------|----------------|---------|
| B1, B2 | Education | mm/yyyy | 08/1989 |
| C, C1, C2, C3 | Postdoc Training | mm/yy | 01/94 |
| D1, D2, D3 | Positions | mm/yy | 07/10 |
| F1 | Licensure | mm/dd/yyyy | 07/01/1994 |
| F2 | Board Certification | yyyy | 2010 |
| H | Honors & Awards | yyyy | 2019 |
| I | Memberships | yyyy | 2005 |
| K1-K5 | Teaching | yyyy | 2015 |
| M2A-D | Grants | mm/yy | 07/10 |
| N3A, N3B | Mentees | yyyy | 2020 |
| O | Leadership | yyyy | 1999 |
| P | Committees | yyyy | 2005 |
| Q1-Q4 | Service | yyyy | 2010 |
| R | Presentations | yyyy | 2019 |
| S1-S9 | Bibliography | yyyy | 2018 |

**Common date defects:**
- Raw ISO format leaking through: `2019-09-01` instead of `2019`
- Month abbreviation with period not parsed: `Sept. 1985` not converted
- String `"None"` from LLM field extraction treated as literal text
- Empty dates when field name mismatch between extraction and rendering (e.g., `dates_attended_start_date` vs `start_date`)

#### Content Completeness

For each section, compare entry counts against source CV:

```python
# Count entries per taxonomy code in pipeline data
import json
with open('outputs/stage_5d_citation_formatted/{UID}_citation_formatted.json') as f:
    data = json.load(f)
counts = {}
for entry in data['entries']:
    code = entry.get('taxonomy_code', 'unknown')
    counts[code] = counts.get(code, 0) + 1
```

Compare against:
- Number of rows in output document tables
- Visual count of items in the source CV

**Red flags:**
- Pipeline has N entries but output shows significantly fewer rows
- Pipeline has 1 entry but source CV clearly has many items (mega-block)
- Section has 0 entries but source CV has content (classification gap)

#### Field Extraction Quality

For each entry, check `extraction_coverage_percent` in the stage 4 output:

| Coverage | Interpretation | Action |
|----------|---------------|--------|
| 80-100% | Good extraction | Verify field values are correct |
| 50-79% | Partial extraction | Check which fields are missing |
| 20-49% | Poor extraction | Likely a mega-block or unusual format |
| < 20% | Failed extraction | Entry needs structural investigation |

### 1.4 Audit Script Template

Use this script to run a comprehensive audit:

```python
from docx import Document
import json

doc = Document('path/to/output.docx')

# 1. Table inventory
for i, table in enumerate(doc.tables):
    header = [c.text.strip()[:50] for c in table.rows[0].cells] if table.rows else []
    data_rows = len(table.rows) - 1
    empty_cols = []
    for col_idx in range(len(header)):
        col_values = [table.rows[r].cells[col_idx].text.strip()
                      for r in range(1, len(table.rows))]
        if all(not v for v in col_values):
            empty_cols.append(header[col_idx] if col_idx < len(header) else f'col{col_idx}')

    issues = []
    if data_rows == 0:
        issues.append('NO DATA')
    if empty_cols:
        issues.append(f'empty columns: {empty_cols}')

    # Check for date format issues
    for r in range(1, len(table.rows)):
        for c in table.rows[r].cells:
            text = c.text.strip()
            if re.match(r'\d{4}-\d{2}-\d{2}', text):
                issues.append(f'raw ISO date: {text}')
            if text.lower() == 'none':
                issues.append(f'literal None')

    if issues:
        print(f'Table {i}: {header}')
        for issue in issues:
            print(f'  - {issue}')
```

---

## Phase 2: Pipeline Data Tracing

Once defects are cataloged, trace each one backward through the pipeline to find where data is lost or malformed.

### 2.1 The Pipeline Stages (in order)

```
Source .docx
    |
    v
Stage 1a: Hierarchy Extraction (LLM segments CV into sections)
    |
    v
Stage 1b: Hierarchy Mapping (maps headers to element indices)
    |
    v
Stage 2: Entry Extraction (LLM detects individual CV entries)
    |
    v
Stage 3a: Header Taxonomy (LLM maps CV headers to WCM codes A-T)
    |
    v
Stage 3b: Entry Classification (LLM assigns taxonomy codes to entries)
    |
    v
Stage 4: Field Extraction (LLM extracts structured fields per entry)
    |
    v
Stage 4.5: Research Summary (generates M1 biosketch summary)
    |
    v
Stage 5: PubMed Enrichment (matches publications to PubMed)
    |
    v
Stage 5b: Institution Enrichment (LLM lookup for city/state)
    |
    v
Stage 5c: Teaching Formatter (reformats K-code entries)
    |
    v
Stage 5d: Citation Formatter (reformats citations to Vancouver)
    |
    v
Stage 6: WCM Word Template (generates final .docx)
```

### 2.2 Stage Output Files

All intermediate outputs are in `src/unified_pipeline/outputs/`:

| Stage | Output Directory | File Pattern |
|-------|-----------------|-------------|
| 1a | `stage_1a_hierarchy/` | `{UID}_segmented.json` |
| 1b | `stage_1b_hierarchy_mapped/` | `{UID}_hierarchy_mapped.json` |
| 2 | `stage_2_entry_extraction/` | `{UID}_entries.json` |
| 3a | `stage_3a_header_taxonomy/` | `{UID}_header_taxonomy.json` |
| 3b | `stage_3b_classified_entries/` | `{UID}_classified.json` |
| 4 | `stage_4_field_extraction/` | `{UID}_fields.json` |
| 5d | `stage_5d_citation_formatted/` | `{UID}_citation_formatted.json` |
| 6 | `stage_6_wcm_documents/` | `{UID}_wcm.docx` |

### 2.3 Tracing Procedure

For a defect like "Honors Organization column is empty":

1. **Start at Stage 6 output**: Confirm the column is empty in the .docx
2. **Check Stage 5d input**: Does the JSON have `granting_body` populated in `extracted_fields`?
3. **Check Stage 4 output**: Did field extraction capture the organization?
4. **Check Stage 3b**: Was the entry correctly classified as `H`?
5. **Check Stage 2**: Was the entry detected at all? Is it a mega-block?

```python
# Quick trace: find all entries for a given taxonomy code
import json
with open('outputs/stage_5d_citation_formatted/{UID}_citation_formatted.json') as f:
    data = json.load(f)

for entry in data['entries']:
    if entry.get('taxonomy_code') == 'H':
        print(f"Text: {entry.get('text', '')[:100]}")
        print(f"Fields: {entry.get('extracted_fields', {})}")
        print(f"Coverage: {entry.get('extraction_coverage', {}).get('extraction_coverage_percent', 0)}%")
        print(f"Lines in text: {len(entry.get('text', '').split(chr(10)))}")
        print()
```

### 2.4 What to Look For at Each Stage

| Stage | Common Issues |
|-------|--------------|
| **Stage 2** | Mega-blocks: 20+ items merged into one entry. Check line count in `text` field. |
| **Stage 3b** | Misclassification: entry assigned wrong taxonomy code. Check `classification_reasoning`. |
| **Stage 4** | Field name mismatches: extraction uses `dates_attended_start_date` but Stage 6 expects `start_date`. Low `extraction_coverage_percent` on mega-blocks. |
| **Stage 5b** | Institution not enriched: LLM lookup didn't match. Check `institution_enrichment` field. |
| **Stage 6** | Rendering bugs: correct data in JSON but wrong formatting in .docx. |

---

## Phase 3: Defect Classification

### 3.1 Bug Taxonomy

Every defect falls into one of these categories:

#### Category A: Stage 6 Rendering Bugs
Data is correct in pipeline JSON but rendered incorrectly in the output document.

**Examples:**
- Date not formatted (raw ISO passes through)
- Field name mismatch (code looks for `start_date` but data has `dates_attended_start_date`)
- Placeholder column headers treated as data ("Title", "Name of award")
- Regex doesn't handle format variant ("Sept." with trailing period)
- Year alignment wrong (reversed when it should be forward)
- Multi-line parsing not triggered (gated on field being empty, but extractor returned partial data)

**Fix cost: Low** -- change Stage 6 code, re-run `--stage 6` only (~25 seconds)

#### Category B: Stage 6 Missing Logic
Stage 6 lacks a feature needed to handle the data correctly.

**Examples:**
- No organization extraction for honors (only award name and date were parsed)
- No pipe separator handling in mega-block parser
- No parenthetical date extraction (`(Chair 1999-2010)` not parsed into role + date)
- No tab-separated value handling in honors

**Fix cost: Medium** -- new logic in Stage 6, but no upstream re-runs needed

#### Category C: Mega-Block Upstream Issues
Stage 2 merges many items into one entry, causing Stage 4 to extract only the first item.

**Examples:**
- 28 committee names in one O entry, field extractor captures 1
- 12 honors in one H entry, field extractor captures 1
- Column headers mixed in with data text

**Fix approach:** Stage 6 multi-line parsers compensate. Trigger on `len(lines) >= 3` rather than checking if fields are empty. The multi-line parsers should handle: pipe separators, parenthetical dates, tab-separated values, date-only lines, and header-like lines.

**Fix cost: Medium** -- Stage 6 compensatory parsing, but root cause is in Stage 2

#### Category D: Field Extraction Gaps
Stage 4 LLM doesn't extract a field, or uses an unexpected field name.

**Examples:**
- `granting_body` extracted for first honor but not subsequent ones
- `dates_attended_start_date` instead of `start_date`
- `date` field contains `"None"` string instead of empty

**Fix approach:** Stage 6 defensive coding (check multiple field name variants, filter `"None"` strings)

**Fix cost: Low** -- Stage 6 defensive code

#### Category E: Classification Errors
Stage 3b assigns the wrong taxonomy code.

**Examples:**
- Leadership activity classified as administrative committee (O vs P)
- Teaching activity classified as presentation (K vs R)

**Fix approach:** Requires investigating Stage 3b prompts and classification logic

**Fix cost: High** -- upstream LLM prompt changes, full re-run

#### Category F: Missing Data in Source
The source CV genuinely doesn't contain certain information.

**Examples:**
- No mentee data in source CV (N3A/N3B correctly 0)
- No organization column in source CV's honors table

**Fix approach:** None needed for missing data. For inferred data (like organization from award text), add extraction heuristics in Stage 6.

### 3.2 Priority Matrix

Fix defects in this order (highest impact per effort first):

| Priority | Category | Why |
|----------|----------|-----|
| 1 | A: Rendering bugs | Quick fixes, re-run Stage 6 only |
| 2 | D: Field name mismatches | Defensive code in Stage 6, no upstream changes |
| 3 | B: Missing Stage 6 logic | New features but confined to Stage 6 |
| 4 | C: Mega-block parsing | Complex but high-impact (recovers 10x more data) |
| 5 | E: Classification errors | Requires upstream investigation and LLM prompt tuning |

### 3.3 Regex vs LLM: Choosing the Right Fix Approach

A core design decision for every fix is whether to solve it with deterministic code (regex, string parsing, heuristics) or with an LLM call. LLM inference is cheap — a Claude Haiku 4.5 call to parse a single entry costs fractions of a cent — so the decision should be driven by brittleness, not cost.

#### When to Use Regex/Heuristics

- **Predictable structure:** Date formatting, field name lookups, pipe/tab splitting. The input follows a known pattern with limited variation.
- **Exact correctness required:** Date format conversion (`2019-09-01` → `2019`) must be deterministic. An LLM might hallucinate a wrong year.
- **Hot path / every entry:** Logic that runs on every single entry in a section (e.g., `format_date_for_section`). Even cheap LLM calls add up at scale across hundreds of entries and dozens of CVs.
- **Simple transformations:** Filtering `"None"` strings, checking placeholder column headers, field name fallback chains.

#### When to Prefer an LLM Call

- **Regex would require 5+ special cases and still miss edge cases.** The organization extraction from award text (Section 4.5) is an example that straddles this line — we used heuristics with 4 strategies and stop-word lists, but an LLM call like _"Extract the granting organization from this award description, or return empty if none is identifiable"_ would handle it with less code and better generalization to unseen CVs.
- **Natural language understanding is needed.** Distinguishing "Elected Member, American Pediatric Society" (org = American Pediatric Society) from "Pediatric Housestaff Award" (no org) requires understanding what constitutes an organization name vs. a descriptor.
- **The data is highly variable across CVs.** A regex tuned to one CV's formatting may break on another. LLMs generalize better to formats they haven't seen before.
- **Classification or categorization tasks.** Deciding if "Senior List for Excellence in Teaching" is an honor or a teaching activity is inherently a judgment call.
- **The section has few entries.** A section with 5-15 entries (like honors or licensure) can afford per-entry LLM calls without meaningful cost impact.

#### Decision Framework

```
Is the transformation deterministic with < 3 format variants?
  YES → Regex/heuristic
  NO  →
    Would regex require > 4 special cases or a stop-word list?
      YES → Strong candidate for LLM
      NO  →
        Does correctness require exact string manipulation (dates, numbers)?
          YES → Regex/heuristic
          NO  → LLM is probably simpler and more robust
```

#### Hybrid Approach

Often the best solution combines both. Use regex for the structured parts and LLM for the ambiguous parts:

```python
# Regex handles the predictable structure
raw_date = fields.get('year') or fields.get('date') or ''
formatted_date = format_date_for_section(raw_date, 'R')  # Always regex

# LLM handles the ambiguous extraction
if not granting_body:
    granting_body = self._llm_extract_organization(award_text)  # LLM for hard cases
```

#### Cost Reference

For perspective on LLM costs in this pipeline (illustrative OpenAI-era figures; current per-model rates are in `docs/LLM_MODELS.md`):

| Model | Typical call | Cost per entry | 100 entries |
|-------|-------------|---------------|-------------|
| gpt-5.1-mini | Field extraction | ~$0.001 | ~$0.10 |
| gpt-5.1-nano | Simple classification | ~$0.0002 | ~$0.02 |
| gpt-5.1 | Complex reasoning | ~$0.005 | ~$0.50 |

A full CV with ~200 entries processed through all stages typically costs $0.05-$0.15 total. Adding a targeted LLM call for a specific section (e.g., 12 honors entries) adds negligible cost. Models are configured per stage in `src/unified_pipeline/config/llm_config.yaml` (see `docs/LLM_MODELS.md`).

#### When Refactoring Regex to LLM

If an existing regex-based solution is accumulating special cases, edge case patches, or stop-word lists across multiple CVs, that's a signal to consider replacing it with an LLM call. Signs:

- The regex has been patched 3+ times for different CVs
- There's a growing list of stop words or exception patterns
- New CVs regularly produce incorrect results from the heuristic
- The code comment starts with "This handles the case where..."

---

## Phase 4: Fix Patterns

### 4.1 Date Formatting Fixes

**Pattern:** Any `_fill_*` method that writes dates should use the centralized formatter.

```python
# WRONG: Raw date passthrough
row.cells[2].text = str(year) if year else ''

# RIGHT: Format through centralized function
formatted = format_date_for_section(raw_date, taxonomy_code) if raw_date else ''
row.cells[2].text = formatted
```

**Checklist for date fixes:**
- [ ] Call `format_date_for_section(date_str, taxonomy_code)` for single dates
- [ ] Call `format_date_range(start, end, taxonomy_code)` for date ranges
- [ ] Handle `"None"` string: `if str(val).strip().lower() == 'none': val = ''`
- [ ] Fall back to alternative field names: `fields.get('year') or fields.get('date') or ''`
- [ ] Fall back to start_date when primary date field is empty

### 4.2 Field Name Mismatch Fixes

**Pattern:** Always check multiple possible field names with fallback chain.

```python
# WRONG: Single field name
start = fields.get('start_date', '')

# RIGHT: Fallback chain
start = fields.get('dates_attended_start_date', '') or fields.get('start_date', '')
```

**Known field name variants:**

| Expected | Variant(s) |
|----------|-----------|
| `start_date` | `dates_attended_start_date`, `date_start` |
| `end_date` | `dates_attended_end_date`, `date_end` |
| `institution` | `organization`, `location`, `venue` |
| `title` | `presentation_title`, `activity_title`, `award_name` |
| `date` | `year`, `start_date` |
| `granting_body` | `organization`, `awarding_body` |

### 4.3 Placeholder/Header Detection

**Pattern:** Filter out values that are actually column headers from the source CV.

```python
# Detect generic placeholder values
placeholder_values = {'title', 'position', 'role', 'name', 'description',
                      'activity', 'institution', 'organization', 'date', 'year'}
if value.strip().lower() in placeholder_values:
    value = ''

# Detect multi-word header lines
header_keywords = {'name of award', 'date awarded', 'organization', 'granting body'}
line_lower = line.lower().replace('\t', ' ')
if sum(1 for kw in header_keywords if kw in line_lower) >= 2:
    continue  # Skip this line
```

### 4.4 Mega-Block Multi-Line Parsing

**Pattern:** When an entry has 3+ lines, switch from field-based rendering to text-based parsing.

```python
lines = [l.strip() for l in original_text.split('\n') if l.strip()]

# Trigger multi-line parsing on line count, NOT on field emptiness
if len(lines) >= 3:
    self._add_multiline_rows(table, lines)
elif len(lines) > 1 and not extracted_field:
    self._add_multiline_rows(table, lines)
else:
    # Normal single-entry rendering using extracted fields
    ...
```

**Multi-line parser must handle:**

1. **Date-only lines:** `re.match(r'^\d{4}(?:\s*[-\u2013]\s*(?:\d{4}|present))?$', line)`
2. **Pipe separators:** `"Committee (Chair) | 1996-Present"` -- split on `|`, last part is date
3. **Parenthetical role+date:** `"Committee (Chair 1999-2010)"` -- extract role and date
4. **Multiple parentheticals:** `"(Vice Chair 2006-2008) (Chair 2008-2010)"` -- take latest date
5. **Trailing dates:** `"Committee Name 1999-2010"` -- date at end of line
6. **Tab-separated values:** `"Award Name\t2019"` -- split on tab
7. **Header lines:** Skip lines matching column header patterns
8. **Trailing whitespace in parentheses:** `"(Chair 1999-2010 )"` -- add `\s*` before `\)` in regex

**Date pool assignment:** When dates are extracted separately from items (e.g., in separate lines or columns), assign them in **forward order** (first date to first item). Only reverse if the dates are clearly in ascending order while items are in descending order:

```python
if len(year_lines) >= 2:
    first_y = extract_year(year_lines[0])
    last_y = extract_year(year_lines[-1])
    ordered = year_lines if first_y >= last_y else list(reversed(year_lines))
```

### 4.5 Organization Extraction from Award Text

**Pattern:** When the source CV doesn't have a separate organization column, extract it from the award description using institutional keyword patterns.

Strategy cascade (in order):
1. **"from [Organization]"** -- explicit delimiter
2. **Comma-separated last segment** -- with institutional keyword or short proper-noun phrase
3. **"Association/Society of X" at start** -- organization name precedes award description
4. **Institutional keyword anywhere** -- find last keyword, walk backwards through proper nouns

**Institutional keywords:** University, College, Hospital, Medical Center, Society, Association, Institute, Academy, Foundation, Program Directors, Center

**Stop words** (not part of org names when walking backwards): award, excellence, teaching, list, recognition, member, elected, senior, certificate, mentoring, director, subinternship, housestaff, faculty, resident, scholarship, honor, clinical, student

**Special handling:**
- Dash-connected names: "The New York Hospital \u2013 Cornell Medical Center" -- skip dash tokens, keep walking
- Use the **last** keyword match to capture multi-keyword org names: "New York Presbyterian Hospital Weill Cornell Center" (both Hospital and Center are keywords)

### 4.6 Regex Edge Cases

Common regex patterns that need defensive variants:

```python
# Month abbreviation with optional period
r'([a-zA-Z]+)\.?\s*(\d{4})'          # Matches: "Sept. 1985", "Aug 1989"

# Parenthetical with optional trailing whitespace
r'\(([^)]*?)(\d{4})\s*[-\u2013]\s*(\d{4}|present)\s*\)'  # Matches: "(Chair 1999-2010 )"

# Date range with en-dash or hyphen
r'(\d{4})\s*[-\u2013]\s*(\d{4}|[Pp]resent)'  # Matches: "1999-2010", "1999\u20132010"
```

---

## Phase 5: Verification Procedures

### 5.1 After Each Fix

1. **Compile check:** `python3 -c "import py_compile; py_compile.compile('stage_6_word_template.py')"`
2. **Re-run Stage 6 only:** `python3 run_full_pipeline.py {UID} --stage 6`
3. **Inspect the specific table/section** that was fixed
4. **Check for regressions** in nearby sections

### 5.2 Full Regression Check

After all fixes, run the comprehensive audit script from Phase 1 again. Compare before/after:

```python
# Key metrics to track
metrics = {
    'total_data_rows': 0,          # Sum of all data rows across all tables
    'empty_column_count': 0,       # Columns that should have data but don't
    'raw_iso_dates': 0,            # Dates in YYYY-MM-DD format (should be 0)
    'literal_none_values': 0,      # Cells containing "None" string
    'placeholder_values': 0,       # Cells containing column header text as data
    'entries_in_pipeline': 0,      # Total entries in Stage 5d JSON
    'entries_in_output': 0,        # Total data rows in output .docx
}
```

### 5.3 Cross-CV Validation

After fixing for one CV, test against other CVs to ensure fixes don't break them:

```bash
# Re-run Stage 6 for all available CVs
for uid in $(ls src/unified_pipeline/outputs/stage_5d_citation_formatted/ | sed 's/_.*//'); do
    python3 run_full_pipeline.py "$uid" --stage 6
done
```

---

## Phase 6: Continuous Improvement

### 6.1 New CV Onboarding Checklist

When processing a new CV for the first time:

- [ ] Run full pipeline (all stages)
- [ ] Run Phase 1 audit on output
- [ ] Check for any new defect patterns not in this guide
- [ ] Document new patterns and add to this guide

### 6.2 Known Limitations

These are structural limitations that require upstream changes:

| Limitation | Root Cause | Potential Fix |
|-----------|-----------|--------------|
| Mega-blocks (20+ items per entry) | Stage 2 entry extraction merges table columns | Improve Stage 2 table parsing to split by rows |
| Low field extraction on mega-blocks | Stage 4 LLM extracts first item only | Stage 4 prompt to handle multi-item entries, or pre-split in Stage 3.5 |
| Organization not in source CV | Source CV lacks separate organization column | Stage 6 heuristic extraction (implemented) |
| Fragment entries in date columns | Stage 2 extracts date column as separate entries | Stage 3b fragment detection and merging |

### 6.3 Systemic Patterns to Watch For

Patterns that indicate a class of bugs rather than one-off issues:

1. **Inconsistent formatter usage:** Any `_fill_*` method that writes dates without calling `format_date_for_section()` is a bug. New sections should always use the centralized formatter.

2. **Field name assumptions:** Any `fields.get('field_name')` with a single field name is fragile. Always use a fallback chain.

3. **Gating on field emptiness:** Code like `if not role:` to trigger multi-line parsing breaks when Stage 4 returns partial results. Gate on line count instead.

4. **Year ordering assumptions:** Never assume years are in ascending or descending order. Use adaptive ordering that checks the actual data.

5. **Regex without whitespace tolerance:** Any regex matching structured text (parentheses, brackets, separators) should include `\s*` at boundaries to handle OCR artifacts and copy-paste whitespace.

---

## Appendix A: Quick Reference -- Taxonomy Codes

| Code | Section | Description |
|------|---------|-------------|
| A | Personal Data | Name, contact, NPI, DEA |
| B1 | Education | Academic degrees |
| B2 | Other Education | Continuing education, additional training |
| C/C1/C2 | Postdoc Training | Residency, fellowship, postdoc |
| D1 | Academic Appointments | Faculty positions |
| D2 | Hospital Appointments | Clinical appointments |
| D3 | Other Positions | Non-academic/non-hospital roles |
| F1 | Licensure | Medical licenses |
| F2 | Board Certification | Board certifications |
| H | Honors & Awards | Awards, elected memberships |
| I | Memberships | Professional society memberships |
| K1 | Didactic Teaching | Classroom/lecture teaching |
| K2 | Clinical Teaching | Bedside/clinical teaching |
| K3 | Teaching Leadership | Course director, program director roles |
| K4 | CME | Continuing medical education |
| K5 | Community Education | Public outreach |
| L1-L3 | Clinical Practice | Patient care activities |
| M1 | Research Summary | Biosketch-style research narrative |
| M2A | Current Grants | Active funding |
| M2B | Completed Grants | Past funding |
| M2C | Pending Grants | Submitted/pending funding |
| M2D | Patents | Intellectual property |
| N3A | Current Mentees | Active mentoring |
| N3B | Past Mentees | Completed mentoring |
| O | Institutional Leadership | Leadership roles within institution |
| P | Administrative Committees | Committee service within institution |
| Q1 | Editorial Activities | Journal editorial boards |
| Q2 | Journal Reviewing | Peer review service |
| Q3 | Extramural Committees | Committees outside institution |
| Q4/Q4A-D | Professional Service | National/regional service |
| R | Invited Presentations | Talks, workshops, grand rounds |
| S0 | Researcher Profile | ORCID, Google Scholar links |
| S1 | Peer-Reviewed Publications | Journal articles |
| S2 | Reviews & Editorials | Review articles, invited editorials |
| S3 | Books | Authored or edited books |
| S4 | Book Chapters | Chapters in edited volumes |
| S5 | Technical Reports | Reports, white papers |
| S6 | Case Reports | Clinical case reports |
| S7 | In Review | Submitted manuscripts |
| S8 | Abstracts & Posters | Conference abstracts |
| S9 | Other Media | Non-traditional publications |
| T | Appendix | Uncategorized content |

## Appendix B: Initiating an Optimization Session

When starting a new session with Claude, provide:

```
Optimize CViche output for '{path_to_output.docx}'.
Reference: EVALUATION_GUIDE.md in the project root.

Follow the evaluation phases:
1. Audit the output document (Phase 1)
2. Trace defects through pipeline stages (Phase 2)
3. Classify and prioritize (Phase 3)
4. Fix highest-priority issues first (Phase 4)
5. Verify after each fix (Phase 5)
```

For continuing work on a known issue:

```
Continue CViche optimization for {UID}.
Previous findings: [describe specific defects].
Fix priority: [specific category from Phase 3].
Reference: EVALUATION_GUIDE.md
```

---

## Appendix C: Test History & Changelog

### CVs Tested

| UID | Name | Entries | Date Tested | Key Characteristics |
|-----|------|---------|-------------|---------------------|
| 4N14RQ | Jonathan Nahmias | 107 | 2026-01-31 | Junior faculty, psychiatry. Heavy teaching (K-codes), clinical practice (L1), mentoring (N3B). Few publications (S8 only). |
| W0MTVW | Sue Bostwick | 176 | 2026-01-31 | Senior faculty, pediatrics. Large bibliography (S1/S2/S3/S5/S8), many presentations (R), grants (M2B), heavy committee service (Q). |
| MNZ7IA | J. Scott Bomann | 98 | 2026-02-01 | Mid-career, emergency medicine. International career (US/NZ/Australia). Many case reports (S6), conference abstracts (S8), multiple positions at same institutions (D2 sub-entries). |

### Stage 6 Fixes Applied (V13.0)

#### Date Formatting
- **Present-Present fix**: `format_date_range()` returns `"Present"` instead of `"Present-Present"` when start resolves to Present
- **Same-year dedup**: `format_date_range()` returns `"2024"` instead of `"2024-2024"` when start and end resolve identically
- **ISO date normalization**: `normalize_iso_dates_in_text()` converts `2021-03-01` → `March 2021` in teaching entry free text
- **Zero-padded day**: Changed `strftime("%B %d, %Y")` to `strftime("%B %-d, %Y")` for preparation date

#### Deduplication System
- **Core dedup**: Three similarity metrics — Jaccard (≥0.6), containment (≥0.75), title containment (≥0.8, min 4 words)
- **Date-aware mode**: Career progression codes (D1/D2/D3/C/B1) only dedup when date ranges overlap or match, preventing false positives on rank changes (e.g., Assistant Prof → Associate Prof)
- **Title dissimilarity safety check**: When full-text metrics trigger but title Jaccard ≤0.25 (min 3 words), dedup is skipped — prevents merging different presentations at the same venue
- **Improved title extraction**: Uses quoted text and extracted fields instead of naive `text[:100]`, correctly distinguishing presentation titles from venue names
- Found by: Nahmias (true duplicates across awards, committees), Bostwick (12 duplicates in presentations, publications), Borman (duplicate Yale fellowship, 3 different ACEM talks preserved)

#### Table Routing & Fallbacks
- **L1/L2/L3 table validation**: Checks table headers before writing clinical practice data — rejects grant tables (containing "award source" or "funding") and falls back to bullet points
- **L1 full-text bullets**: Uses complete `original_text` instead of truncated `clinical_role` field for clinical practice bullet points

#### Institution Handling
- **Institution inheritance**: `_propagate_institution_to_subentries()` forward-propagates institution from parent D2 entries to blank sub-entries in document order. Found by: Borman (Lincoln Hospital sub-positions)
- **Committee field splitting**: When `committee_name` is empty and `role` contains comma-separated "Chair, Committee Name", splits into separate fields using a whitelist of recognized role titles. Found by: Borman (ACEM committees)

#### Content Filtering
- **Structural label filtering**: `_is_structural_label()` removes source CV section headers that were extracted as data entries
- **Teaching orphan filter**: Skips K-code entries with no date, no audience, no location, and text < 80 chars

### Issues Deferred to Upstream Stages

These issues were identified during Stage 6 review but require fixes in Stage 3b or Stage 4:

| Issue | CV | Root Cause | Upstream Fix Needed |
|-------|-----|-----------|---------------------|
| Q2 committee names merged into role field | Borman | Stage 4 doesn't separate `role` from `committee_name` | Stage 4 extraction prompt for Q-codes |
| K4 title fields all empty | Borman | Stage 4 doesn't populate `title` for teaching entries | Stage 4 extraction prompt for K-codes |
| D2 sub-entries lose parent institution | Borman | Stage 4 per-entry extraction can't see CV hierarchy | Stage 4 context propagation (compensated in Stage 6) |
| Redundant description paragraph as K4 entry | Borman | Stage 3b classifies course description as separate entry | Stage 3b structural detection |
| Missing professional membership dates | Borman | Source CV lacks dates; Stage 4 can't extract what's absent | Source data limitation |
| Empty L1 clinical practice section | Borman | Source CV uses position titles (D2), not practice narratives | Classification is correct; source data limitation |
