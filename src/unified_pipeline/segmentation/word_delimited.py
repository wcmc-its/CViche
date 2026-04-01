"""
Word CV Segmentation with Delimiter Preservation

MODIFICATION: Preserves list item boundaries using "|" delimiter for better downstream splitting.

Changes from original word_cv_segmentation.py:
1. Instructs LLM to use "|" between distinct list items when they're in same entry
2. Preserves structural information for Stage 2B1 splitting
3. Delimiters can be removed in final Stage 3 output

Usage:
    python3 word_cv_segmentation_with_delimiters.py /path/to/cv.docx
"""

import os
import json
from pathlib import Path
from typing import List, Dict, Any
from unified_pipeline.llm_client import call_llm

# Handle both relative and absolute imports for flexible usage
try:
    from ..core.docx_structure_extractor import extract_docx_structure, create_simplified_layout_json
except (ImportError, ValueError):
    from core.docx_structure_extractor import extract_docx_structure, create_simplified_layout_json


# Same schema as original
CV_SEGMENTATION_SCHEMA = {
    "type": "object",
    "properties": {
        "document_uid": {"type": "string"},
        "meta": {
            "type": "object",
            "properties": {
                "num_top_level_groups": {"type": "integer"},
                "total_entries": {"type": "integer"},
                "processing_notes": {"type": "string"}
            },
            "required": ["num_top_level_groups", "total_entries", "processing_notes"],
            "additionalProperties": False
        },
        "groups": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "level": {"type": "integer"},
                    "label_inferred": {"type": "string"},
                    "entries": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "text_snippet": {"type": "string"},
                                "element_idx": {"type": "integer"},
                                "entry_type": {"type": "string"},
                                "order_index": {"type": "integer"},
                                "confidence": {"type": "number"}
                            },
                            "required": ["id", "text_snippet", "element_idx", "entry_type", "order_index", "confidence"],
                            "additionalProperties": False
                        }
                    },
                    "subgroups": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "level": {"type": "integer"},
                                "label_inferred": {"type": "string"},
                                "entries": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "string"},
                                            "text_snippet": {"type": "string"},
                                            "element_idx": {"type": "integer"},
                                            "entry_type": {"type": "string"},
                                            "order_index": {"type": "integer"},
                                            "confidence": {"type": "number"}
                                        },
                                        "required": ["id", "text_snippet", "element_idx", "entry_type", "order_index", "confidence"],
                                        "additionalProperties": False
                                    }
                                }
                            },
                            "required": ["id", "level", "label_inferred", "entries"],
                            "additionalProperties": False
                        }
                    }
                },
                "required": ["id", "level", "label_inferred", "entries", "subgroups"],
                "additionalProperties": False
            }
        }
    },
    "required": ["document_uid", "meta", "groups"],
    "additionalProperties": False
}


def segment_cv_with_delimiters(docx_path: str, output_path: str = None) -> Dict[str, Any]:
    """
    Segment Word CV into structured JSON with delimiter preservation.

    MODIFICATION: Instructs LLM to use "|" between list items within same entry.
    """
    print(f"\n{'='*80}")
    print(f"CV SEGMENTATION WITH DELIMITER PRESERVATION")
    print(f"{'='*80}")
    print(f"Source: {docx_path}")

    # Step 1: Extract Word structure
    print("\nStep 1: Extracting Word document structure...")
    structure = extract_docx_structure(docx_path)
    layout = create_simplified_layout_json(structure)

    # Step 2: Create prompt with delimiter instructions
    print("\nStep 2: Creating segmentation prompt...")

    system_prompt = """You are an expert CV parser. Segment this CV into groups and entries following these rules:

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

TARGET: If you extract fewer than 50-60 entries from a typical academic CV, you are likely missing items."""

    # Convert layout to compact string
    layout_str = json.dumps(layout, separators=(',', ':'))

    user_prompt = f"""Segment this CV layout into hierarchical groups and entries with MAXIMUM RECALL and DELIMITER PRESERVATION.

LAYOUT JSON:
{layout_str}

KEY INSTRUCTIONS:
1. Extract EVERY list item (check for list_level ≥ 0 and indent values)
2. Use " | " (space-pipe-space) between distinct items when they're in same entry
3. Parse ALL table rows as entries
4. Recognize subsections under Publications (Invited, Book Chapters, Abstracts)
5. Continue lists across multiple elements (same indent + list_level)
6. Aim for 50-60+ total entries for a typical academic CV
7. When grouping items in one entry, separate them with " | " for downstream splitting

DELIMITER EXAMPLES:
- "2024 Award | 2023 Honor | 2022 Grant"
- "Pub 1. Journal A. | Pub 2. Journal B. | Pub 3. Journal C."
- "Position A, 2020-Present | Position B, 2018-2020"

Return complete hierarchical segmentation following the JSON schema."""

    print(f"  Layout size: {len(layout_str):,} chars")

    # Step 3: Call GPT-4o with Structured Outputs
    print("\nStep 3: Calling GPT-4o with Structured Outputs (delimiter-aware)...")

    result = call_llm(
        stage="segmentation_word_delimited",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "cv_segmentation_with_delimiters",
                "strict": True,
                "schema": CV_SEGMENTATION_SCHEMA
            }
        },
        temperature=0.1,
        max_tokens=16000
    )

    # Parse response
    segmentation = json.loads(result["content"])

    # Add document_uid if not present
    if "document_uid" not in segmentation or not segmentation["document_uid"]:
        segmentation["document_uid"] = Path(docx_path).stem

    # Calculate stats
    total_entries = segmentation["meta"]["total_entries"]
    total_groups = segmentation["meta"]["num_top_level_groups"]

    # Count entries with delimiters
    entries_with_delimiters = 0
    total_delimiter_count = 0

    for group in segmentation.get("groups", []):
        for entry in group.get("entries", []):
            if " | " in entry.get("text_snippet", ""):
                entries_with_delimiters += 1
                total_delimiter_count += entry["text_snippet"].count(" | ")

        for subgroup in group.get("subgroups", []):
            for entry in subgroup.get("entries", []):
                if " | " in entry.get("text_snippet", ""):
                    entries_with_delimiters += 1
                    total_delimiter_count += entry["text_snippet"].count(" | ")

    print(f"\n{'='*80}")
    print(f"SEGMENTATION COMPLETE WITH DELIMITERS")
    print(f"{'='*80}")
    print(f"Total Groups: {total_groups}")
    print(f"Total Entries: {total_entries}")
    print(f"Entries with '|' delimiters: {entries_with_delimiters}")
    print(f"Total '|' delimiters: {total_delimiter_count}")
    print(f"Average items per delimited entry: {(total_delimiter_count / entries_with_delimiters + 1):.1f}" if entries_with_delimiters > 0 else "N/A")

    # Save output
    if output_path is None:
        output_path = Path(docx_path).stem + "_segmented_with_delimiters.json"

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(segmentation, f, indent=2, ensure_ascii=False)

    print(f"\nSaved to: {output_path}")
    print(f"{'='*80}\n")

    return segmentation


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python3 word_cv_segmentation_with_delimiters.py <path_to_docx> [output_path]")
        sys.exit(1)

    docx_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else None

    segment_cv_with_delimiters(docx_path, output_path)
