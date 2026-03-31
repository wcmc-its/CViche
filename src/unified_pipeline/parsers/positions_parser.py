"""
Positions Parser - Phase 4.3

Extracts structured data from position entries using GPT-4o-mini with Structured Outputs.

Strategy:
- LLM extracts: title, institution, department, location, dates, position_type
- Date normalization for start/end years
- Position type classification (academic, clinical, administrative, etc.)
- Tracks rank progression for academic positions (Instructor → Assistant → Associate → Full)

Output: Structured position records ready for database insertion
"""

import json
from pathlib import Path
from typing import Dict, List, Any, Optional

from unified_pipeline.llm_client import call_llm


# Position schema for Structured Outputs
POSITION_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
            "description": "Position title or rank (e.g., Assistant Professor, Chief of Service)"
        },
        "institution": {
            "type": "string",
            "description": "Institution or organization name"
        },
        "department": {
            "type": "string",
            "description": "Department, division, or unit (empty string if not available)"
        },
        "location": {
            "type": "string",
            "description": "City, State or City, Country (empty string if not available)"
        },
        "start_year": {
            "type": "integer",
            "description": "Start year as integer, 0 if not available"
        },
        "end_year": {
            "type": "integer",
            "description": "End year as integer, 9999 for current/present positions, 0 if not available"
        },
        "position_type": {
            "type": "string",
            "enum": ["academic_faculty", "clinical", "administrative", "visiting", "adjunct", "emeritus", "postdoc", "research", "committee", "other"],
            "description": "Type of position or role"
        },
        "rank_level": {
            "type": "string",
            "enum": ["instructor", "assistant", "associate", "full", "endowed_chair", "other", "none"],
            "description": "Academic rank level (for faculty positions), 'none' if not applicable"
        },
        "is_current": {
            "type": "boolean",
            "description": "True if this is a current/present position"
        },
        "confidence": {
            "type": "number",
            "description": "Confidence score 0.0-1.0 for extraction quality"
        }
    },
    "required": ["title", "institution", "department", "location", "start_year", "end_year", "position_type", "rank_level", "is_current", "confidence"],
    "additionalProperties": False
}


def parse_position_entry(text: str, element_metadata: Optional[Dict] = None) -> Dict[str, Any]:
    """
    Parse a single position entry into structured fields.

    Args:
        text: Position entry text
        element_metadata: Optional Word metadata (table structure, etc.)

    Returns:
        Structured position record
    """
    system_prompt = """You are an academic CV position parser. Extract structured data from position/appointment entries.

GUIDELINES:

1. TITLE EXTRACTION:
   - Extract exact position title or rank
   - Common academic ranks: Instructor, Assistant Professor, Associate Professor, Professor
   - Clinical titles: Attending, Fellow, Director, Chief
   - Administrative: Committee Member, Chair, Director

2. INSTITUTION:
   - Full official name of institution or organization
   - Include affiliated hospital/center if mentioned

3. DEPARTMENT/DIVISION:
   - Department name (e.g., Pediatrics, Medicine)
   - Division or specialty (e.g., Cardiology, Oncology)
   - Use empty string "" if not specified

4. LOCATION:
   - Format: "City, State" or "City, Country"
   - Use standard state abbreviations (MO, CA, NY)
   - Use empty string "" if not specified

5. DATES:
   - Extract start_year and end_year as integers
   - Use 9999 for current/present positions
   - If only one year mentioned, use as start_year, end_year = 9999
   - If date range: "2010-2014" → start_year: 2010, end_year: 2014
   - Use 0 for missing dates

6. POSITION TYPE:
   - academic_faculty: Faculty positions (Professor, Instructor, etc.)
   - clinical: Clinical practice roles (Attending, Clinical Fellow)
   - administrative: Committee, director, leadership roles
   - visiting: Visiting appointments
   - adjunct: Adjunct or affiliate positions
   - emeritus: Emeritus positions
   - postdoc: Postdoctoral positions
   - research: Research positions (Research Scientist, etc.)
   - committee: Committee memberships, board positions
   - other: Other types

7. RANK LEVEL (for academic faculty only):
   - instructor: Instructor level
   - assistant: Assistant Professor
   - associate: Associate Professor
   - full: Full Professor
   - endowed_chair: Named/endowed chair positions
   - other: Other academic ranks
   - none: Not applicable (for non-faculty positions)

8. IS_CURRENT:
   - True if position is current/present (look for "Present", "Current", or no end date)
   - False if ended

9. CONFIDENCE:
   - 0.9-1.0: Complete entry with title, institution, dates
   - 0.7-0.89: Most fields present, some ambiguity
   - 0.5-0.69: Some fields missing or ambiguous
   - 0.0-0.49: Very incomplete or unclear

Return structured JSON matching the schema."""

    user_prompt = f"""Parse this position/appointment entry:

{text}

Extract all available fields following the schema."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "position_record",
            "strict": True,
            "schema": POSITION_SCHEMA
        }
    }

    result = call_llm(stage="parser_positions", messages=messages, response_format=response_format)

    # Parse response
    position = json.loads(result["content"])

    # Add source metadata if available
    if element_metadata:
        position["source_metadata"] = element_metadata

    return position


def parse_positions_section(
    entries: List[Dict[str, Any]],
    section_metadata: Optional[Dict] = None
) -> List[Dict[str, Any]]:
    """
    Parse all entries in a positions section.

    Args:
        entries: List of position entries from segmentation
        section_metadata: Section-level metadata (taxonomy mapping, etc.)

    Returns:
        List of structured position records
    """
    print(f"Parsing {len(entries)} position entries...")

    parsed_positions = []

    for idx, entry in enumerate(entries, 1):
        text = entry.get("text_snippet", "")
        element_idx = entry.get("element_idx")

        if not text or len(text.strip()) < 10:
            # Skip empty or very short entries
            continue

        print(f"  [{idx}/{len(entries)}] Parsing entry...")

        try:
            # Parse entry
            position = parse_position_entry(
                text=text,
                element_metadata={"element_idx": element_idx} if element_idx else None
            )

            # Add original entry metadata
            position["entry_id"] = entry.get("id")
            position["order_index"] = entry.get("order_index")
            position["original_text"] = text

            parsed_positions.append(position)

            # Show progress
            confidence = position.get("confidence", 0.0)
            conf_emoji = "✓" if confidence >= 0.8 else "⚠️" if confidence >= 0.6 else "✗"
            title = position.get("title", "")
            institution = position.get("institution", "")
            current = " (current)" if position.get("is_current", False) else ""
            print(f"      {conf_emoji} {title} - {institution[:40]}...{current} (conf: {confidence:.2f})")

        except Exception as e:
            print(f"      ✗ Error: {e}")
            # Add unparsed entry with error info
            parsed_positions.append({
                "entry_id": entry.get("id"),
                "order_index": entry.get("order_index"),
                "original_text": text,
                "parse_error": str(e),
                "confidence": 0.0
            })

    return parsed_positions


def main():
    """
    Test positions parser on sample data.
    """
    import sys

    if len(sys.argv) < 2:
        print("Positions Parser - Phase 4.3")
        print()
        print("Usage: python positions_parser.py <segmented_cv_path>")
        print()
        print("Extracts structured position data from segmented CV JSON.")
        print()
        sys.exit(1)

    segmented_cv_path = sys.argv[1]

    # Load segmented CV
    with open(segmented_cv_path, 'r') as f:
        segmented_cv = json.load(f)

    # Find position-related sections
    print("="*80)
    print("POSITIONS PARSER")
    print("="*80)
    print(f"Input: {segmented_cv_path}")
    print()

    all_parsed = []

    # Keywords for position-related sections
    position_keywords = [
        "position", "employment", "appointment", "committee",
        "clinical", "teaching", "administrative", "faculty"
    ]

    for group in segmented_cv.get("groups", []):
        label = group.get("label_inferred", "")

        # Check if this is a position-related section
        if any(keyword in label.lower() for keyword in position_keywords):
            print(f"Processing: {label}")
            print(f"Entries: {len(group.get('entries', []))}")
            print()

            parsed = parse_positions_section(
                entries=group.get("entries", []),
                section_metadata={"section_label": label}
            )

            all_parsed.extend(parsed)
            print()

        # Check subgroups
        for subgroup in group.get("subgroups", []):
            sub_label = subgroup.get("label_inferred", "")
            if any(keyword in sub_label.lower() for keyword in position_keywords):
                print(f"Processing: {label} → {sub_label}")
                print(f"Entries: {len(subgroup.get('entries', []))}")
                print()

                parsed = parse_positions_section(
                    entries=subgroup.get("entries", []),
                    section_metadata={"section_label": label, "subsection_label": sub_label}
                )

                all_parsed.extend(parsed)
                print()

    # Calculate stats
    total_positions = len(all_parsed)
    current_positions = sum(1 for p in all_parsed if p.get("is_current", False))
    academic_positions = sum(1 for p in all_parsed if p.get("position_type") == "academic_faculty")
    clinical_positions = sum(1 for p in all_parsed if p.get("position_type") == "clinical")
    administrative_positions = sum(1 for p in all_parsed if p.get("position_type") in ["administrative", "committee"])

    # Write output
    output_path = Path(segmented_cv_path).parent / (Path(segmented_cv_path).stem.replace('_segmented', '') + '_positions_parsed.json')

    output_data = {
        "source_file": segmented_cv_path,
        "total_positions": total_positions,
        "current_positions": current_positions,
        "academic_positions": academic_positions,
        "clinical_positions": clinical_positions,
        "administrative_positions": administrative_positions,
        "high_confidence": sum(1 for p in all_parsed if p.get("confidence", 0) >= 0.8),
        "medium_confidence": sum(1 for p in all_parsed if 0.6 <= p.get("confidence", 0) < 0.8),
        "low_confidence": sum(1 for p in all_parsed if p.get("confidence", 0) < 0.6),
        "positions": all_parsed
    }

    with open(output_path, 'w') as f:
        json.dump(output_data, f, indent=2)

    print("="*80)
    print("RESULTS")
    print("="*80)
    print(f"Total positions: {total_positions}")
    print(f"  Current positions: {current_positions}")
    print(f"  Academic faculty: {academic_positions}")
    print(f"  Clinical: {clinical_positions}")
    print(f"  Administrative/Committee: {administrative_positions}")
    print()
    print(f"  High confidence (≥0.8): {output_data['high_confidence']}")
    print(f"  Medium confidence (0.6-0.79): {output_data['medium_confidence']}")
    print(f"  Low confidence (<0.6): {output_data['low_confidence']}")
    print()
    print(f"✓ Results saved to: {output_path}")
    print()


if __name__ == '__main__':
    main()
