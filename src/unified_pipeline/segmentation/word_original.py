"""
Word CV Segmentation using Text Model with Structured Outputs

Leverages Word's native structure (extracted via docx_structure_extractor.py)
and uses GPT-4o with Structured Outputs for reliable JSON segmentation.

This is significantly cheaper and faster than vision-based approaches:
- No image conversion required
- Uses text model (cheaper than vision)
- Structured Outputs eliminates JSON parsing failures
- Exploits Word's heading/list metadata directly

Cost: ~$0.05-0.10 per CV (vs $0.30-0.58 for PDF vision approach)
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


# Define JSON schema for Structured Outputs
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


def segment_word_cv(docx_path: str, output_dir: str = None) -> Dict[str, Any]:
    """
    Segment Word CV using text model with Structured Outputs.

    Strategy:
    1. Extract Word structure to layout JSON
    2. Send layout JSON to GPT-4o with JSON schema
    3. Model returns structured segmentation (guaranteed valid JSON)

    Args:
        docx_path: Path to .docx file
        output_dir: Where to save output (default: same as script)

    Returns:
        Segmented CV structure matching three-pass output format
    """
    print("="*80)
    print("WORD CV SEGMENTATION (Text Model + Structured Outputs)")
    print("="*80)
    print(f"DOCX: {docx_path}")
    print()

    # Step 1: Extract structure
    print("Step 1: Extracting Word document structure...")
    structure = extract_docx_structure(docx_path)

    # Use FULL structure instead of simplified - tables need complete data
    layout = structure['elements']

    print(f"  ✓ Extracted {len(layout)} elements")
    print(f"    Paragraphs: {structure['meta']['num_paragraphs']}")
    print(f"    Tables: {structure['meta']['num_tables']}")

    # Step 2: Prepare prompt
    print("\nStep 2: Preparing segmentation prompt...")

    system_prompt = """You are analyzing an academic CV that has been extracted from a Word document with rich structural metadata.

Your task: Segment the CV into hierarchical groups representing major sections (Education, Publications, etc.) and their entries with MAXIMUM RECALL.

CRITICAL GUIDELINES:

1. HIERARCHY:
   - Level 1: Major sections (Education, Publications, Professional Experience, etc.)
   - Level 2: Subsections (Peer-Reviewed Publications, Book Chapters, Invited Publications, etc.)
   - Each entry = one distinct item (one publication, one degree, one position, one license, etc.)

2. WORD STRUCTURE EXPLOITATION:
   - EVERY list item (list_level ≥ 0) is a separate entry unless clearly continuation text
   - EVERY table row represents entries (skip headers)
   - Heading levels (role: "heading", level: 2/3) indicate subsections
   - Bold text without heading role often marks section headers

3. LIST CONTINUATION (CRITICAL FOR RECALL):
   - Continue lists across elements when indent and list_level match
   - Lists spanning multiple paragraphs/elements belong to same section
   - Example: 10 list items with same indent → 10 separate entries

4. PUBLICATIONS SUBSECTIONS (COMMON PATTERN):
   - "Invited Publications" → separate level 2 subgroup
   - "Book Chapters" → separate level 2 subgroup
   - "Research Abstract Presentations" → separate level 2 subgroup
   - "Peer-Reviewed Publications" → separate level 2 subgroup
   - Enumerate ALL items in each subsection

5. COMPLETE ENUMERATION (MAXIMIZE RECALL):
   - Education: ALL degrees, fellowships, residencies (5+ entries typical)
   - Licensure: ALL licenses and certifications (4+ entries typical)
   - Appointments: ALL committee roles (5-10+ entries typical)
   - Societies: ALL professional organizations (5+ entries typical)
   - Lectures: ALL invited talks (10+ entries typical)
   - Research Support: ALL grants with dates/agencies
   - Publications: 20-50+ entries typical for academic CVs

6. TABLE PARSING:
   - Each table row = potential entry or set of entries
   - Tables with date ranges in column 1 → multiple entries per table
   - Parse tables cell-by-cell

7. ENTRY IDs:
   - Use format: G{group_num}-E{entry_num}
   - For subgroups: G{group_num}-SG{subgroup_num}-E{entry_num}

8. INCLUDE element_idx:
   - Reference the original element index from the layout JSON
   - This links back to source document

9. NORMALIZATION:
   - Map "Pres" → "Present"
   - Preserve all text content for each entry

TARGET: If you extract fewer than 50-60 entries from a typical academic CV, you are likely missing items.

Output the segmentation as hierarchical JSON matching the provided schema."""

    # Convert layout to compact string
    layout_str = json.dumps(layout, separators=(',', ':'))

    user_prompt = f"""Segment this CV layout into hierarchical groups and entries with MAXIMUM RECALL.

LAYOUT JSON:
{layout_str}

KEY INSTRUCTIONS:
1. Extract EVERY list item as a separate entry (check for list_level ≥ 0 and indent values)
2. Parse ALL table rows as entries (tables marked with "type": "table")
3. Recognize subsections under Publications (Invited, Book Chapters, Abstracts)
4. Continue lists across multiple elements (same indent + list_level)
5. Aim for 50-60+ total entries for a typical academic CV

Return complete hierarchical segmentation following the JSON schema."""

    print(f"  Layout size: {len(layout_str):,} chars")

    # Step 3: Call GPT-4o with Structured Outputs
    print("\nStep 3: Calling GPT-4o with Structured Outputs...")

    result = call_llm(
        stage="segmentation_word_original",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "cv_segmentation",
                "strict": True,
                "schema": CV_SEGMENTATION_SCHEMA
            }
        },
        temperature=0.1,
        max_tokens=16000
    )

    # Parse response - guaranteed to be valid JSON matching schema
    segmentation = json.loads(result["content"])

    # Add document_uid if not present
    if "document_uid" not in segmentation or not segmentation["document_uid"]:
        segmentation["document_uid"] = Path(docx_path).stem

    # Calculate stats
    total_entries = 0
    for group in segmentation.get("groups", []):
        total_entries += len(group.get("entries", []))
        for subgroup in group.get("subgroups", []):
            total_entries += len(subgroup.get("entries", []))

    # Update meta
    segmentation["meta"]["num_top_level_groups"] = len(segmentation.get("groups", []))
    segmentation["meta"]["total_entries"] = total_entries
    if "processing_notes" not in segmentation["meta"]:
        segmentation["meta"]["processing_notes"] = "Word native structure + text model + Structured Outputs"

    print(f"  ✓ Segmentation complete")
    print(f"    Sections: {segmentation['meta']['num_top_level_groups']}")
    print(f"    Total entries: {segmentation['meta']['total_entries']}")

    # Step 4: Write output
    if output_dir is None:
        output_dir = Path(__file__).parent
    else:
        output_dir = Path(output_dir)

    output_file_path = output_dir / (Path(docx_path).stem + "_word_segmented.json")

    with open(output_file_path, 'w') as f:
        json.dump(segmentation, f, indent=2)

    print(f"\n{'='*80}")
    print("RESULTS")
    print("="*80)
    print(f"Total sections: {segmentation['meta']['num_top_level_groups']}")
    print(f"Total entries: {segmentation['meta']['total_entries']}")
    print(f"\n✓ Results saved to: {output_file_path}")

    file_size_kb = output_file_path.stat().st_size / 1024
    print(f"File size: {file_size_kb:.1f} KB")

    return {
        'num_sections': segmentation['meta']['num_top_level_groups'],
        'total_entries': segmentation['meta']['total_entries'],
        'output_file': str(output_file_path)
    }


def main():
    import sys

    if len(sys.argv) < 2:
        print("Usage: python word_cv_segmentation.py <docx_path>")
        sys.exit(1)

    docx_path = sys.argv[1]

    if not os.path.exists(docx_path):
        print(f"Error: File not found: {docx_path}")
        sys.exit(1)

    if not docx_path.endswith('.docx'):
        print(f"Error: File must be .docx format: {docx_path}")
        sys.exit(1)

    segment_word_cv(docx_path)


if __name__ == '__main__':
    main()
