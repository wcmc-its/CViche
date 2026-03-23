# Plan: Durable Fix for Table-Heavy CV Processing

## Problem Statement

CVs like `2012_Ncebner_Nov` have most content in Word tables rather than paragraphs. The current pipeline fails because:

1. **Stage 1a** only detects headers in paragraphs, missing headers inside tables
2. **Stage 1b** only searches for headers in paragraph elements
3. **Stage 2** receives the entire document as one "Personal Data" section
4. The LLM gets overwhelmed with 23 tables of content in a single prompt

### Document Structure Example (2012_Ncebner_Nov)
```
Paragraphs 0-9: Contact info (name, address, email, website)
Table 0: "Research Statement" (21 rows)
Table 1: Education/Degrees (44 rows)
Table 2: Grants (19 rows)
Table 3: "Publications: Journal Articles and Book Chapters" (54 rows)
...
Table 22: "Workshop Participation" (19 rows)
```

Each table contains a **section header in its first cell**, followed by content rows.

---

## Solution Architecture

### Core Insight

Tables in academic CVs follow predictable patterns:
1. **Section-header tables**: First row/cell contains a header like "PUBLICATIONS", "GRANTS"
2. **Content tables**: Rows contain individual entries (publications, positions, etc.)
3. **Mixed tables**: Header row + content rows in same table

### Proposed Fix: Unified Element Processing

Create a **unified element stream** that treats table header cells as if they were paragraphs, enabling the existing header detection logic to work on tables too.

---

## Implementation Plan

### Phase 1: Enhance Document Structure Extraction

**File: `core/docx_structure_extractor.py`**

Add a new function `extract_unified_elements()` that:

1. Iterates through document body in order (paragraphs AND tables)
2. For each table, extracts the **first cell of first row** as a potential header
3. Returns a unified list where:
   - Regular paragraphs keep their current format
   - Tables are represented as:
     - A "table_header" element (first cell text, with table metadata)
     - A "table_content" element (remaining rows, flattened)

```python
def extract_unified_elements(docx_path: str) -> Dict[str, Any]:
    """
    Extract document elements with table-awareness.

    Returns unified element list where table headers are treated
    like paragraphs for header detection purposes.
    """
    elements = []
    unified_idx = 0

    for element in doc.element.body:
        if isinstance(element, CT_P):
            # Standard paragraph handling
            elements.append({
                "unified_idx": unified_idx,
                "type": "paragraph",
                "text": para.text.strip(),
                # ... formatting metadata
            })
            unified_idx += 1

        elif isinstance(element, CT_Tbl):
            table = Table(element, doc)
            first_cell = get_first_cell_text(table)

            # Check if first cell looks like a header
            if looks_like_header(first_cell):
                # Emit table header as paragraph-like element
                elements.append({
                    "unified_idx": unified_idx,
                    "type": "table_header",
                    "text": first_cell,
                    "table_index": table_idx,
                    "is_header_candidate": True,
                    # ... formatting from table
                })
                unified_idx += 1

            # Emit table content as single element
            elements.append({
                "unified_idx": unified_idx,
                "type": "table_content",
                "table_index": table_idx,
                "rows": table_rows,
                "text": flatten_table_text(table),
            })
            unified_idx += 1

    return {"elements": elements, "meta": {...}}
```

**Helper function `looks_like_header()`:**
- Short text (< 100 chars)
- ALL CAPS or Title Case
- No dates, no citations
- Matches known CV section keywords
- Contains colon at end (optional)

---

### Phase 2: Update Stage 1a Header Detection

**File: `segmentation/word_chunked.py`**

Modify `detect_section_headers()` to:

1. Accept `table_header` elements alongside `paragraph` elements
2. Apply same 11 header signals to table headers
3. Track which headers came from tables (for Stage 1b mapping)

```python
def detect_section_headers(structure: Dict) -> List[Dict]:
    """Detect headers in paragraphs AND table first cells."""

    elements = structure.get("elements", [])
    header_candidates = []

    for elem in elements:
        # Process paragraphs AND table_header elements
        if elem['type'] not in ('paragraph', 'table_header'):
            continue

        text = elem.get('text', '').strip()
        if not text:
            continue

        # Apply header detection signals
        signals = calculate_header_signals(elem)

        if is_likely_header(signals):
            header_candidates.append({
                "text": text,
                "unified_idx": elem['unified_idx'],
                "source_type": elem['type'],  # 'paragraph' or 'table_header'
                "table_index": elem.get('table_index'),  # if from table
                "signals": signals
            })

    return header_candidates
```

---

### Phase 3: Update Stage 1b Hierarchy Mapping

**File: `stage_1b_hierarchy_mapper.py`**

Modify header-to-element mapping to:

1. Use `unified_idx` instead of paragraph-only indices
2. Search in both paragraphs and table headers
3. Compute section boundaries that properly span table content

```python
def map_header_to_element(header_text: str, elements: List[Dict],
                          start_idx: int = 0) -> Optional[int]:
    """Find header in unified element list (paragraphs + table headers)."""

    for i in range(start_idx, len(elements)):
        elem = elements[i]

        # Search in paragraphs AND table headers
        if elem['type'] not in ('paragraph', 'table_header'):
            continue

        if fuzzy_match(header_text, elem.get('text', '')):
            return elem['unified_idx']

    return None
```

**Section boundary computation:**

```python
def compute_section_boundaries(hierarchy: List[Dict],
                               elements: List[Dict]) -> List[Dict]:
    """Compute boundaries using unified indices."""

    boundaries = []

    for section in sections:
        start_idx = section['unified_idx']

        # Find end: next header OR end of document
        end_idx = find_next_header_idx(start_idx, elements) - 1

        # If section started from table_header, include the table_content
        if elements[start_idx]['type'] == 'table_header':
            # The table_content element follows immediately
            # Ensure we include it in this section
            pass

        boundaries.append({
            "hierarchy": section['hierarchy_path'],
            "unified_idx_start": start_idx,
            "unified_idx_end": end_idx,
            # ... other fields
        })

    return boundaries
```

---

### Phase 4: Update Stage 2 Entry Extraction

**File: `stage_2_entry_extraction.py`**

The current Stage 2 fix already handles tables. Additional changes:

1. Use `unified_idx` for section boundaries
2. For `table_content` elements, extract individual rows as entries
3. Improve prompts to handle table row extraction

```python
def detect_entries_for_section(section_elements: List[Dict], ...):
    """Extract entries from section elements (paragraphs + tables)."""

    for elem in section_elements:
        if elem['type'] == 'table_content':
            # Each row is potentially a separate entry
            for row_idx, row in enumerate(elem['rows']):
                row_text = flatten_row(row)
                element_list_parts.append(
                    f"[{elem['unified_idx']}.{row_idx}] (ROW) {row_text[:150]}"
                )
        elif elem['type'] == 'paragraph':
            element_list_parts.append(
                f"[{elem['unified_idx']}] {elem['text'][:150]}"
            )
```

---

## Implementation Order

### Step 1: Add `extract_unified_elements()` to docx_structure_extractor.py
- Create new function alongside existing `extract_docx_structure()`
- Add `looks_like_header()` helper
- Test with table-heavy CV

### Step 2: Create adapter for Stage 1a
- Add flag to use unified elements
- Modify header detection to accept table headers
- Maintain backward compatibility

### Step 3: Update Stage 1b mapping
- Switch to unified indices
- Update boundary computation
- Test hierarchy mapping on table-heavy CV

### Step 4: Update Stage 2 extraction
- Already mostly done
- Add row-level extraction for tables
- Improve prompts

### Step 5: End-to-end testing
- Test on 2012_Ncebner_Nov (table-heavy)
- Test on paragraph-heavy CVs (regression)
- Verify classification accuracy

---

## Alternative Approaches Considered

### Option A: Pre-process tables into paragraphs
- Convert each table row to a paragraph before processing
- Simpler but loses table structure information
- **Rejected**: Loses formatting cues that help classification

### Option B: Separate table processing pipeline
- Run table content through separate extraction
- Merge results with paragraph-based extraction
- **Rejected**: Complex merging logic, inconsistent handling

### Option C: LLM-based table understanding (current Stage 2 approach)
- Send tables to LLM for structure understanding
- Works but overwhelms LLM with large tables
- **Partially adopted**: Good for entry extraction, not for header detection

### Option D: Unified element stream (SELECTED)
- Treat table headers like paragraphs
- Leverage existing header detection logic
- Cleanest integration with current architecture

---

## Success Criteria

1. **2012_Ncebner_Nov** produces proper section hierarchy:
   - "Research Statement" section with table content
   - "Education" section with degree entries
   - "Publications" section with article entries
   - etc.

2. **Entry extraction** correctly classifies:
   - Publications as S1/S2/S3
   - Grants as M1/M2/M3
   - Positions as D1/D2/D3
   - etc.

3. **Backward compatibility**: Paragraph-heavy CVs continue to work

4. **Performance**: No significant increase in processing time or cost

---

## Risk Mitigation

1. **False positive headers**: Table cells that look like headers but aren't
   - Mitigation: Require multiple header signals, not just one
   - Use context (position in document, neighboring elements)

2. **Complex table structures**: Merged cells, nested tables
   - Mitigation: Start with simple first-cell extraction
   - Enhance later based on real-world edge cases

3. **Regression in paragraph CVs**: Changes might break working cases
   - Mitigation: Comprehensive test suite
   - Feature flag for gradual rollout
