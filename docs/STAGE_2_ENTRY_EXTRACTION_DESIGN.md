# Stage 2: Entry Extraction Design

**Status**: Design Phase
**Date**: 2025-01-22
**Previous Work**: `src/unified_pipeline/segmentation/word_delimited.py`

## Overview

Stage 2 extracts individual CV entries (publications, grants, positions, etc.) from sections identified in Stage 1. The design leverages existing `word_delimited.py` architecture which already implements the core principles we need.

## Design Principles

1. **Preserve full Word formatting**: No plain text conversion - send structured Word layout to LLM
2. **Minimize output costs**: Return only element indices/positions, not full content
3. **Use Chat Completions API**: GPT-5.1, same as Stage 1
4. **Leverage semantic + formatting signals**: LLM uses both content and Word formatting cues

---

## Previous Implementation: word_delimited.py

### Architecture Summary

The existing `word_delimited.py` demonstrates the approach:

```python
# Step 1: Extract Word structure (preserves ALL formatting)
structure = extract_docx_structure(docx_path)
layout = create_simplified_layout_json(structure)

# Step 2: Send layout JSON to GPT-4o
response = client.chat.completions.create(
    model="gpt-4o",
    messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": layout_json}
    ],
    response_format={"type": "json_schema", "json_schema": {...}}
)

# Step 3: Extract entries using element_idx references
# LLM returns positions, not full content
```

---

## Word Structure Extraction

**Module**: `src/unified_pipeline/core/docx_structure_extractor.py`

### What's Preserved

For each Word element (paragraph/table), the extractor captures:

**Paragraph metadata**:
- `text`: Full text content
- `style`: Word style name (e.g., "Heading 1", "Normal")
- `outline_level`: Heading level (1, 2, 3, etc.)
- `list_level`: List nesting level
- `num_fmt`: Numbering format (bullet, numbered, etc.)
- `indent_left`: Left indentation in inches
- `indent_first`: First line indentation
- `alignment`: left/center/right/justify
- `bold`, `italic`, `underline`: Font styling
- `font_size`: Font size in points
- `font_color`: Font color as hex

**Table metadata**:
- Row and cell structure
- Cell text content
- Per-cell formatting (bold, alignment, etc.)

### Example Layout JSON

```json
{
  "idx": 42,
  "type": "paragraph",
  "text": "Smith, J., et al. (2023). Novel approach to...",
  "style": "Normal",
  "list_level": 0,
  "indent_left": 0.5,
  "bold": false,
  "font_size": 11,
  "font_color": "#000000"
}
```

This rich metadata allows the LLM to use both semantic content AND formatting patterns to identify entry boundaries.

---

## Entry Identification Prompt

**Source**: `word_delimited.py` lines 129-182

### Complete System Prompt

```
You are an expert CV parser. Segment this CV into groups and entries following these rules:

1. HIERARCHY:
   - Level 1: Major sections (Education, Publications, Professional Experience, etc.)
   - Level 2: Subsections (Peer-Reviewed Publications, Book Chapters, Invited Publications, etc.)
   - Each entry = one distinct item OR multiple related items separated by "|"

2. DELIMITER PRESERVATION (NEW RULE):
   - When multiple list items should stay in same entry temporarily, separate them with " | "
   - Use " | " (space-pipe-space) between distinct items within an entry
   - This preserves structural information for downstream processing
   - Example: "2024 Award A | 2023 Award B | 2022 Award C"
   - Example: "Publication 1. Journal A. | Publication 2. Journal B."

3. WHEN TO USE "|" DELIMITER:
   - Multiple publications/presentations that are tightly related by date or subsection
   - List items that span multiple Word elements but form a logical group
   - When you're unsure if items should be split - use "|" to preserve the boundary
   - Awards/honors/grants within same time period or category

4. WHEN TO CREATE SEPARATE ENTRIES:
   - Clear section breaks or subsection headers
   - Different categories (e.g., "Education" vs "Training")
   - Distinct table rows with different contexts

5. WORD STRUCTURE EXPLOITATION:
   - EVERY list item (list_level ≥ 0) should be captured (may use "|" if grouping)
   - EVERY table row represents entries
   - Heading levels (role: "heading") indicate subsections
   - Bold text without heading role often marks section headers

6. COMPLETE ENUMERATION (MAXIMIZE RECALL):
   - Education: ALL degrees, fellowships, residencies
   - Licensure: ALL licenses and certifications
   - Appointments: ALL committee roles
   - Publications: ALL items (use "|" between items if needed)
   - Lectures: ALL invited talks (use "|" between talks if needed)
   - Grants: ALL funding sources (use "|" between grants if needed)

7. ENTRY IDs:
   - Use format: G{group_num}-E{entry_num}
   - For subgroups: G{group_num}-SG{subgroup_num}-E{entry_num}

8. INCLUDE element_idx:
   - Reference the original element index from the layout JSON

9. NORMALIZATION:
   - Map "Pres" → "Present"
   - Preserve all text content

IMPORTANT: The "|" delimiter will be used in Stage 2B1 to intelligently split entries.
It's better to include "|" when uncertain than to lump items with no delimiter.

TARGET: If you extract fewer than 50-60 entries from a typical academic CV, you are likely missing items.
```

### Key Edge Cases Handled

1. **Multi-element entries**: Publications spanning multiple Word paragraphs → Use "|" delimiter
2. **Table-based CVs**: Every table row is an entry candidate
3. **List items**: All bullets/numbered items captured using `list_level` metadata
4. **Unclear boundaries**: When uncertain, use "|" delimiter to preserve splitting option
5. **Nested lists**: Handled via `list_level` and `indent_left` values
6. **Complex formatting**: Bold headers within entries distinguished by context
7. **Date ranges**: "Pres" normalized to "Present"
8. **Incomplete recall**: Target 50-60+ entries to ensure comprehensive extraction

---

## Output Schema: Minimal for Cost Efficiency

**Source**: `word_delimited.py` lines 32-107

### JSON Structure

```json
{
  "document_uid": "cv_2048_lisanby",
  "meta": {
    "num_top_level_groups": 12,
    "total_entries": 87,
    "processing_notes": "Extracted all publications, grants, positions"
  },
  "groups": [
    {
      "id": "G1",
      "level": 1,
      "label_inferred": "PUBLICATIONS",
      "entries": [
        {
          "id": "G1-E1",
          "text_snippet": "Smith, J., et al. (2023)...",
          "element_idx": 42,              ← KEY: Points to Word element
          "entry_type": "peer_reviewed_article",
          "order_index": 1,
          "confidence": 0.95
        }
      ],
      "subgroups": [
        {
          "id": "G1-SG1",
          "level": 2,
          "label_inferred": "Peer-Reviewed Articles",
          "entries": [...]
        }
      ]
    }
  ]
}
```

### Output Design Rationale

**Minimal tokens returned**:
- `text_snippet`: Only first ~100 chars for verification (not full content)
- `element_idx`: Points to original Word element for later extraction
- `entry_type`: Classification (useful for downstream taxonomy mapping)
- `confidence`: LLM's confidence in the entry identification

**Full content extracted later**: Use `element_idx` to pull full text from original DOCX after LLM processing.

**Cost savings**: Returning only positions instead of full entries saves ~80% on output tokens.

---

## Adaptation for Stage 2

### Proposed Architecture

**Input**:
- Section from Stage 1 hierarchy (e.g., "PUBLICATIONS")
- Full Word document structure (layout JSON)

**Processing**:
1. Use Stage 1 hierarchy to identify section boundaries in the document
2. Extract section-specific layout JSON (only elements within section)
3. Send to GPT-5.1 with section-focused prompt
4. Receive element_idx array for entry boundaries
5. Extract full content using indices

### Section-by-Section Processing

Instead of processing entire CV at once:

```python
# Stage 1 output
hierarchy = [
    {'level': 'H1', 'text': 'PUBLICATIONS', 'start_element': 245, 'end_element': 612},
    {'level': 'H1', 'text': 'GRANTS', 'start_element': 613, 'end_element': 891},
    ...
]

# Stage 2: Process each section
for section in hierarchy:
    section_layout = extract_section_layout(
        full_layout,
        start=section['start_element'],
        end=section['end_element']
    )

    entries = extract_entries_from_section(
        section_layout,
        section_header=section['text'],
        model='gpt-5.1'
    )
```

### Cost Projection

**Per section** (typical Publications section with ~30 entries):
- Input: ~8k tokens (layout JSON for 30 entries)
- Output: ~500 tokens (30 entries × ~15 tokens per entry)
- Cost: (8k × $2.50/1M) + (500 × $10/1M) = $0.025

**Full CV** (12 sections):
- Total cost: ~$0.30 per CV

**Comparison**:
- Stage 1 (hierarchy): $0.05-$0.20
- Stage 2 (entries): $0.30
- **Total pipeline**: $0.35-$0.50 per CV

---

## Alternative: Line Numbers vs Character Positions

The user asked about character positions vs line numbers. Both are viable:

### Option A: Element Indices (Current Approach)

**Pros**:
- Direct reference to Word elements
- No ambiguity about boundaries
- Works with any CV structure

**Cons**:
- Requires structured Word extraction

### Option B: Line Numbers

**Pros**:
- Simple to implement
- Easy to debug/verify
- Human-readable

**Cons**:
- Assumes sequential text extraction preserves lines
- Tables may complicate line counting

### Option C: Character Positions

**Pros**:
- Precise boundaries
- Works with continuous text

**Cons**:
- Fragile to formatting changes
- Harder to debug
- DOCX character positions can shift with formatting

**Recommendation**: Stick with **element_idx** (Option A) - it's what `word_delimited.py` uses successfully, and it's the most robust.

---

## Next Steps

1. **Review this design** with user
2. **Adapt `word_delimited.py` for section-based processing** instead of full-CV
3. **Integrate with Stage 1 output**: Use hierarchy to define section boundaries
4. **Test on sample CVs**: Verify entry extraction quality
5. **Document Stage 2 in PIPELINE_ARCHITECTURE_V7.md** once validated

---

## Open Questions

1. Should we process sections in parallel (faster) or sequentially (cheaper)?
2. Do we need entry classification (entry_type) or just positions?
3. Should delimiter "|" splitting happen in Stage 2 or Stage 3?
4. How do we handle entries that span section boundaries?
