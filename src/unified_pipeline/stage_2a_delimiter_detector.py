#!/usr/bin/env python3
"""
Stage 2a: Entry Delimiter Detection

Uses LLM to identify start/end delimiters for entries within each section.
Outputs full hierarchical context (parent > grandparent > ...) for each delimiter.

Input: Stage 1 hierarchy JSON + Word document
Output: JSON with entry delimiters including element indices and hierarchy
"""

import os
import sys
import json
import time
from pathlib import Path
from typing import List, Dict, Any, Tuple
from docx import Document

# Add to path
sys.path.insert(0, str(Path(__file__).parent))

from unified_pipeline.llm_client import call_llm
from core.output_manager import OutputManager


def get_hierarchy_path(node: Dict, current_path: List[str] = None) -> List[str]:
    """Build full hierarchy path for a node"""
    if current_path is None:
        current_path = []

    text = node.get("text", "").strip()
    if text:
        current_path.append(text)

    return current_path


def extract_leaf_sections_with_boundaries(
    section_boundaries: List[Dict],
    hierarchy: List[Dict]
) -> List[Tuple[List[str], int, int]]:
    """
    Extract all leaf sections with their boundaries from Stage 1b output.

    Args:
        section_boundaries: List of section boundary dicts from Stage 1b
        hierarchy: Hierarchy structure (for reference)

    Returns:
        List of (hierarchy_path, element_idx_start, element_idx_end) tuples
    """
    leaf_sections = []

    for boundary in section_boundaries:
        # Only process leaf sections (sections without children)
        if not boundary.get("has_children", False):
            hierarchy_path = boundary.get("hierarchy", [])
            start_idx = boundary.get("element_idx_start")
            end_idx = boundary.get("element_idx_end")

            # Only include if we have valid boundaries
            if start_idx is not None and end_idx is not None:
                leaf_sections.append((hierarchy_path, start_idx, end_idx))

    return leaf_sections


def collect_header_indices(hierarchy_with_indices: List[Dict]) -> set:
    """
    Recursively collect all element indices that are section/subsection headers.

    Args:
        hierarchy_with_indices: Hierarchy structure from Stage 1b with element_idx

    Returns:
        Set of element indices that are headers
    """
    header_indices = set()

    def walk_hierarchy(items):
        for item in items:
            if "element_idx" in item and item["element_idx"] is not None:
                header_indices.add(item["element_idx"])
            if "children" in item:
                walk_hierarchy(item["children"])

    walk_hierarchy(hierarchy_with_indices)
    return header_indices


def detect_delimiters_for_section(
    section_hierarchy: List[str],
    doc: Document,
    start_para_idx: int,
    end_para_idx: int,
    document_uid: str = None,
    header_indices: set = None
) -> Tuple[List[Dict], Dict]:
    """
    Use LLM to detect entry delimiters within a section.

    Args:
        section_hierarchy: Full path (e.g., ["Research Experience", "Publications", "Peer-Reviewed"])
        doc: Word document
        start_para_idx: Starting paragraph index for this section
        end_para_idx: Ending paragraph index for this section (before next section)
        client: OpenAI client
        document_uid: Document identifier for logging
        header_indices: Set of element indices that are section/subsection headers (to exclude)

    Returns:
        Tuple of (delimiters_list, cost_info_dict)
    """
    if header_indices is None:
        header_indices = set()

    # Extract paragraphs for this section, excluding headers
    section_paragraphs = []
    skipped_headers = 0
    for i in range(start_para_idx, min(end_para_idx, len(doc.paragraphs))):
        # Skip if this index is a header
        if i in header_indices:
            skipped_headers += 1
            continue

        text = doc.paragraphs[i].text.strip()
        if text:  # Only include non-empty paragraphs
            section_paragraphs.append({
                "idx": i,
                "text": text
            })

    if not section_paragraphs:
        return [], {"cost": 0, "tokens": 0}

    # Build context for LLM
    section_name = section_hierarchy[-1] if section_hierarchy else "Unknown Section"
    full_hierarchy = " > ".join(section_hierarchy)

    # Create paragraph list for LLM
    paragraph_list = "\n".join([
        f"[{p['idx']}] {p['text'][:150]}{'...' if len(p['text']) > 150 else ''}"
        for p in section_paragraphs[:50]  # Limit to 50 paragraphs to avoid token limits
    ])

    system_prompt = """You are an intelligent parser analyzing a CV section. Your goal is to identify individual **logical entries** (e.g., a single publication, position, award, or course) from a list of raw paragraphs or table rows. Always respond with valid JSON only."""

    user_prompt = f"""## Input Data

**CV Section Header:** `{full_hierarchy}`

**Raw Data (Paragraphs):**
{paragraph_list}

-----

## Instructions

### 1. Grouping Logic (Detecting Multi-Part Entries)

A single logical entry may span multiple lines (paragraphs or table rows). You must determine if a line starts a **new entry** or is a **continuation** of the previous one.

**Merge consecutive lines into a SINGLE entry when:**

* **The First Line (Start):** Introduces the main item (contains the date, role, title, course, or event).
* **The Following Line (Continuation):** Does **NOT** introduce a new date, role, or top-level identifier. Instead, it provides dependent details (descriptions, notes, durations, session counts).
    * *Rule of thumb:* If the second line would be confusing or incomplete when read alone, but clearly belongs to the line above, group them.

**Set Indices Accordingly:**

* `element_idx_start`: Index of the line introducing the item.
* `element_idx_end`: Index of the last line containing details for that same item.
* If an entry is a single line, start and end indices are identical.

### 2. Handling Tables

* **Whole Table as Entry:** If a table describes a *single* summary item (e.g., a summary of one grant), treat the entire table as one entry (`element_type: "table"`).
* **Rows as Entries:** If a table lists *multiple* items (e.g., a list of courses), treat each row (or group of rows based on the logic above) as a separate entry (`element_type: "table_row"`).

### 3. Output Format

Respond **only** with a JSON array containing the identified entries. If no entries are found, return `[]`.

**JSON Structure:**

```json
[
  {{
    "element_idx_start": 120,
    "element_idx_end": 121,
    "element_type": "paragraph",
    "confidence": 0.95,
    "reasoning": "Details in 121 belong to item in 120"
  }}
]
```

*Note: `element_type` must be "paragraph", "table", or "table_row".*

**IMPORTANT:**
- Use the exact paragraph indices shown in brackets [idx]
- Confidence should be 0.0 to 1.0
- Keep reasoning brief (<60 chars), especially for multi-line groupings
"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    try:
        llm_result = call_llm(
            stage="stage_2a",
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.1
        )

        result_text = llm_result["content"]
        result = json.loads(result_text)

        total_cost = llm_result["cost"]

        # Extract delimiters array - LLM may return in different keys
        if isinstance(result, dict):
            # Try common keys the LLM might use
            delimiters = (
                result.get("delimiters") or
                result.get("entries") or
                result.get("result") or
                result.get("data") or
                []
            )
        else:
            delimiters = result

        # Validate and clean delimiters
        validated_delimiters = []
        for delim in delimiters:
            if "element_idx_start" in delim and "element_idx_end" in delim:
                # Ensure indices are within bounds
                start_idx = delim["element_idx_start"]
                end_idx = delim["element_idx_end"]

                if start_idx >= start_para_idx and end_idx < end_para_idx:
                    # Add full verbose text for all paragraphs in the entry range
                    full_text_parts = []
                    for idx in range(start_idx, end_idx + 1):
                        if idx < len(doc.paragraphs):
                            para_text = doc.paragraphs[idx].text.strip()
                            if para_text:
                                full_text_parts.append(para_text)
                    delim["full_text"] = "\n".join(full_text_parts)

                    validated_delimiters.append(delim)

        # Return delimiters and cost info
        cost_info = {
            "cost": total_cost,
            "tokens": llm_result["total_tokens"],
            "prompt_tokens": llm_result["prompt_tokens"],
            "completion_tokens": llm_result["completion_tokens"],
        }
        return validated_delimiters, cost_info

    except Exception as e:
        print(f"  ⚠ Error detecting delimiters for '{section_name}': {e}")
        return [], {"cost": 0, "tokens": 0}


def run_stage_2a(docx_path: str, hierarchy_json_path: str = None):
    """
    Main Stage 2a: Detect entry delimiters using LLM

    Args:
        docx_path: Path to Word document
        hierarchy_json_path: Optional path to Stage 1b hierarchy JSON with boundaries
                            (if not provided, will use OutputManager to find it)
    """

    print("="*80)
    print("STAGE 2A: ENTRY DELIMITER DETECTION (LLM)")
    print("="*80)
    print(f"Input: {docx_path}")
    print()

    # Setup
    om = OutputManager(docx_path)

    # Load Stage 1b hierarchy with boundaries
    if hierarchy_json_path is None:
        hierarchy_json_path = om.get_stage1b_path()

    print(f"Loading hierarchy with boundaries: {hierarchy_json_path}")
    with open(hierarchy_json_path) as f:
        hierarchy_data = json.load(f)

    # Load Word document
    doc = Document(docx_path)

    # Extract leaf sections with boundaries from Stage 1b
    section_boundaries = hierarchy_data.get("section_boundaries", [])
    hierarchy = hierarchy_data.get("hierarchy_with_indices", [])

    leaf_sections = extract_leaf_sections_with_boundaries(section_boundaries, hierarchy)

    # Collect all header indices to exclude from paragraph lists
    header_indices = collect_header_indices(hierarchy)
    print(f"Collected {len(header_indices)} header indices to exclude from entry detection")

    # Find the first section start index to process "Personal Data" section
    first_section_idx = min([start for _, start, _ in leaf_sections]) if leaf_sections else 0

    # Add "Personal Data" section for elements before the first section
    sections_to_process = []
    if first_section_idx > 0:
        sections_to_process.append((["Personal Data"], 0, first_section_idx - 1))

    sections_to_process.extend(leaf_sections)

    print(f"Found {len(leaf_sections)} leaf sections + 1 Personal Data section to analyze")
    print()

    # Process each section
    all_delimiters = []
    total_entries = 0
    total_cost = 0.0
    total_tokens = 0
    document_uid = hierarchy_data.get("document_uid")

    for i, (hierarchy_path, start_idx, end_idx) in enumerate(sections_to_process, 1):
        section_name = hierarchy_path[-1] if hierarchy_path else "Unknown"
        print(f"[{i}/{len(sections_to_process)}] Processing: {' > '.join(hierarchy_path)}")
        print(f"  Elements: {start_idx} to {end_idx}")

        # Detect delimiters using actual section boundaries from Stage 1b
        delimiters, cost_info = detect_delimiters_for_section(
            hierarchy_path,
            doc,
            start_idx,
            end_idx + 1,  # end_idx is inclusive, so add 1 for range
            document_uid=document_uid,
            header_indices=header_indices
        )

        # Track costs
        total_cost += cost_info.get("cost", 0)
        total_tokens += cost_info.get("tokens", 0)

        if delimiters:
            all_delimiters.append({
                "hierarchy": hierarchy_path,
                "element_idx_start": start_idx,
                "element_idx_end": end_idx,
                "entry_delimiters": delimiters,
                "cost": cost_info.get("cost", 0),
                "tokens": cost_info.get("tokens", 0),
                "log_id": cost_info.get("log_id")
            })
            total_entries += len(delimiters)
            print(f"  ✓ Found {len(delimiters)} entries (${cost_info.get('cost', 0):.4f})")
        else:
            print(f"  - No entries found")
        print()

    # Save output
    output_path = om.get_stage2a_path()

    output_data = {
        "document_uid": hierarchy_data.get("document_uid"),
        "total_sections_analyzed": len(sections_to_process),
        "total_entries_found": total_entries,
        "total_cost": total_cost,
        "total_tokens": total_tokens,
        "delimiters": all_delimiters
    }

    with open(output_path, 'w') as f:
        json.dump(output_data, f, indent=2)

    print("="*80)
    print("STAGE 2A COMPLETE")
    print("="*80)
    print(f"Output: {output_path}")
    print(f"Sections analyzed: {len(sections_to_process)}")
    print(f"Total entries found: {total_entries}")
    print(f"Total cost: ${total_cost:.4f}")
    print(f"Total tokens: {total_tokens:,}")
    print("="*80)

    return output_data, output_path


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python stage_2a_delimiter_detector.py <docx_path> [hierarchy_json]")
        print()
        print("Example:")
        print("  python stage_2a_delimiter_detector.py data/sample_cvs/word/2071_Zuschlag_Cv.docx")
        sys.exit(1)

    docx_path = sys.argv[1]
    hierarchy_json = sys.argv[2] if len(sys.argv) > 2 else None

    run_stage_2a(docx_path, hierarchy_json)
