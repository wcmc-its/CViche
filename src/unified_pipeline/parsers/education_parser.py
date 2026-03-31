"""
Education Parser - Phase 4.2

Extracts structured data from education entries using GPT-4o-mini with Structured Outputs.

Strategy:
- LLM extracts: degree, major/field, institution, location, dates
- Date normalization for start/end years
- Degree type classification (BA, BS, MD, PhD, etc.)

Output: Structured education records ready for database insertion
"""

import json
from pathlib import Path
from typing import Dict, List, Any, Optional

from unified_pipeline.llm_client import call_llm


# Education schema for Structured Outputs
EDUCATION_SCHEMA = {
    "type": "object",
    "properties": {
        "degree": {
            "type": "string",
            "description": "Degree earned (e.g., BA, BS, MD, PhD, Residency, Fellowship)"
        },
        "major_field": {
            "type": "string",
            "description": "Major, field of study, or specialty (e.g., Biology, Pediatrics)"
        },
        "institution": {
            "type": "string",
            "description": "Institution name"
        },
        "location": {
            "type": "string",
            "description": "City, State or City, Country"
        },
        "start_year": {
            "type": "integer",
            "description": "Start year as integer, 0 if not available"
        },
        "end_year": {
            "type": "integer",
            "description": "End year or graduation year as integer, 0 if not available"
        },
        "degree_type": {
            "type": "string",
            "enum": ["undergraduate", "graduate", "medical_school", "residency", "fellowship", "postdoc", "certificate", "other"],
            "description": "Type of degree or training"
        },
        "honors": {
            "type": "string",
            "description": "Honors, awards, or distinctions (e.g., 'cum laude', 'with honors'), empty string if none"
        },
        "confidence": {
            "type": "number",
            "description": "Confidence score 0.0-1.0 for extraction quality"
        }
    },
    "required": ["degree", "major_field", "institution", "location", "start_year", "end_year", "degree_type", "honors", "confidence"],
    "additionalProperties": False
}


def parse_education_entry(text: str, element_metadata: Optional[Dict] = None) -> Dict[str, Any]:
    """
    Parse a single education entry into structured fields.

    Args:
        text: Education entry text
        element_metadata: Optional Word metadata (table structure, etc.)

    Returns:
        Structured education record
    """
    system_prompt = """You are an academic CV education parser. Extract structured data from education entries.

GUIDELINES:

1. DEGREE EXTRACTION:
   - Extract exact degree abbreviation (BA, BS, MD, PhD, etc.)
   - For training: Residency, Fellowship, Postdoctoral Fellowship
   - Include full degree name if no abbreviation

2. MAJOR/FIELD:
   - Undergraduate: Major (e.g., Biology, Chemistry)
   - Graduate: Field (e.g., Molecular Biology, Neuroscience)
   - Medical: N/A or "Medicine"
   - Residency/Fellowship: Specialty (e.g., Pediatrics, Cardiology)

3. INSTITUTION:
   - Full official name
   - Include affiliated hospital if mentioned for medical training

4. LOCATION:
   - Format: "City, State" or "City, Country"
   - Use standard state abbreviations (MO, CA, NY)

5. DATES:
   - Extract start_year and end_year as integers
   - If only one year mentioned, use as end_year (graduation), start_year = 0
   - If date range: "2010-2014" → start_year: 2010, end_year: 2014
   - Use 0 for missing dates

6. DEGREE TYPE:
   - undergraduate: BA, BS, AB, etc.
   - graduate: MA, MS, PhD, etc.
   - medical_school: MD, DO, MBBS, etc.
   - residency: Any residency training
   - fellowship: Any fellowship training
   - postdoc: Postdoctoral positions
   - certificate: Certificate programs
   - other: Other training

7. HONORS:
   - Extract any honors/awards mentioned (e.g., "cum laude", "with honors", "Phi Beta Kappa")
   - Use empty string "" if none

8. CONFIDENCE:
   - 0.9-1.0: Complete entry with all key fields
   - 0.7-0.89: Most fields present, minor ambiguity
   - 0.5-0.69: Some fields missing or ambiguous
   - 0.0-0.49: Very incomplete or unclear

Return structured JSON matching the schema."""

    user_prompt = f"""Parse this education entry:

{text}

Extract all available fields following the schema."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "education_record",
            "strict": True,
            "schema": EDUCATION_SCHEMA
        }
    }

    result = call_llm(stage="parser_education", messages=messages, response_format=response_format)

    # Parse response
    education = json.loads(result["content"])

    education['token_usage'] = {
        'prompt_tokens': result["prompt_tokens"],
        'completion_tokens': result["completion_tokens"],
        'total_tokens': result["total_tokens"]
    }

    # Add source metadata if available
    if element_metadata:
        education["source_metadata"] = element_metadata

    return education


def parse_education_section(
    entries: List[Dict[str, Any]],
    section_metadata: Optional[Dict] = None
) -> List[Dict[str, Any]]:
    """
    Parse all entries in an education section.

    Args:
        entries: List of education entries from segmentation
        section_metadata: Section-level metadata (taxonomy mapping, etc.)

    Returns:
        List of structured education records
    """
    print(f"Parsing {len(entries)} education entries...")

    parsed_education = []

    for idx, entry in enumerate(entries, 1):
        text = entry.get("text_snippet", "")
        element_idx = entry.get("element_idx")

        if not text or len(text.strip()) < 10:
            # Skip empty or very short entries
            continue

        print(f"  [{idx}/{len(entries)}] Parsing entry...")

        try:
            # Parse entry
            education = parse_education_entry(
                text=text,
                element_metadata={"element_idx": element_idx} if element_idx else None
            )

            # Add original entry metadata
            education["entry_id"] = entry.get("id")
            education["order_index"] = entry.get("order_index")
            education["original_text"] = text

            parsed_education.append(education)

            # Show progress
            confidence = education.get("confidence", 0.0)
            conf_emoji = "✓" if confidence >= 0.8 else "⚠️" if confidence >= 0.6 else "✗"
            degree = education.get("degree", "")
            institution = education.get("institution", "")
            print(f"      {conf_emoji} {degree} - {institution[:50]}... (conf: {confidence:.2f})")

        except Exception as e:
            print(f"      ✗ Error: {e}")
            # Add unparsed entry with error info
            parsed_education.append({
                "entry_id": entry.get("id"),
                "order_index": entry.get("order_index"),
                "original_text": text,
                "parse_error": str(e),
                "confidence": 0.0
            })

    return parsed_education


def main():
    """
    Test education parser on sample data.
    """
    import sys

    if len(sys.argv) < 2:
        print("Education Parser - Phase 4.2")
        print()
        print("Usage: python education_parser.py <segmented_cv_path>")
        print()
        print("Extracts structured education data from segmented CV JSON.")
        print()
        sys.exit(1)

    segmented_cv_path = sys.argv[1]

    # Load segmented CV
    with open(segmented_cv_path, 'r') as f:
        segmented_cv = json.load(f)

    # Find education sections
    print("="*80)
    print("EDUCATION PARSER")
    print("="*80)
    print(f"Input: {segmented_cv_path}")
    print()

    all_parsed = []

    for group in segmented_cv.get("groups", []):
        label = group.get("label_inferred", "")

        # Check if this is an education-related section
        if any(keyword in label.lower() for keyword in ["education", "training", "degree"]):
            print(f"Processing: {label}")
            print(f"Entries: {len(group.get('entries', []))}")
            print()

            parsed = parse_education_section(
                entries=group.get("entries", []),
                section_metadata={"section_label": label}
            )

            all_parsed.extend(parsed)
            print()

        # Check subgroups
        for subgroup in group.get("subgroups", []):
            sub_label = subgroup.get("label_inferred", "")
            if any(keyword in sub_label.lower() for keyword in ["education", "training", "degree"]):
                print(f"Processing: {label} → {sub_label}")
                print(f"Entries: {len(subgroup.get('entries', []))}")
                print()

                parsed = parse_education_section(
                    entries=subgroup.get("entries", []),
                    section_metadata={"section_label": label, "subsection_label": sub_label}
                )

                all_parsed.extend(parsed)
                print()

    # Write output
    output_path = Path(segmented_cv_path).parent / (Path(segmented_cv_path).stem.replace('_segmented', '') + '_education_parsed.json')

    output_data = {
        "source_file": segmented_cv_path,
        "total_education": len(all_parsed),
        "high_confidence": sum(1 for e in all_parsed if e.get("confidence", 0) >= 0.8),
        "medium_confidence": sum(1 for e in all_parsed if 0.6 <= e.get("confidence", 0) < 0.8),
        "low_confidence": sum(1 for e in all_parsed if e.get("confidence", 0) < 0.6),
        "education": all_parsed
    }

    with open(output_path, 'w') as f:
        json.dump(output_data, f, indent=2)

    print("="*80)
    print("RESULTS")
    print("="*80)
    print(f"Total education entries: {len(all_parsed)}")
    print(f"  High confidence (≥0.8): {output_data['high_confidence']}")
    print(f"  Medium confidence (0.6-0.79): {output_data['medium_confidence']}")
    print(f"  Low confidence (<0.6): {output_data['low_confidence']}")
    print()
    print(f"✓ Results saved to: {output_path}")
    print()


if __name__ == '__main__':
    main()
