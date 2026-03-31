"""
Grants Parser - Phase 4.4

Extracts structured data from grant/funding entries using GPT-4o-mini with Structured Outputs.

Strategy:
- LLM extracts: title, agency, award_number, amount, PI/role, dates, status
- Amount normalization (handles $123K, $1.2M, etc.)
- Role classification (PI, Co-PI, Co-I, etc.)
- Status tracking (active, completed, pending, etc.)

Output: Structured grant records ready for database insertion
"""

import json
from pathlib import Path
from typing import Dict, List, Any, Optional

from unified_pipeline.llm_client import call_llm


# Grant schema for Structured Outputs
GRANT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
            "description": "Grant or project title"
        },
        "agency": {
            "type": "string",
            "description": "Funding agency or sponsor (e.g., NIH, NSF, foundation name)"
        },
        "award_number": {
            "type": "string",
            "description": "Grant/award number or identifier (empty string if not available)"
        },
        "amount": {
            "type": "string",
            "description": "Funding amount as string (e.g., '$500,000', '$1.2M') (empty string if not available)"
        },
        "role": {
            "type": "string",
            "enum": ["PI", "co-PI", "co-investigator", "collaborator", "consultant", "key_personnel", "other", "not_specified"],
            "description": "CV owner's role on the grant"
        },
        "start_year": {
            "type": "integer",
            "description": "Start year as integer, 0 if not available"
        },
        "end_year": {
            "type": "integer",
            "description": "End year as integer, 9999 for ongoing grants, 0 if not available"
        },
        "status": {
            "type": "string",
            "enum": ["active", "completed", "pending", "awarded", "not_funded", "in_preparation", "other"],
            "description": "Grant status"
        },
        "grant_type": {
            "type": "string",
            "enum": ["federal", "foundation", "industry", "institutional", "other"],
            "description": "Type of funding source"
        },
        "confidence": {
            "type": "number",
            "description": "Confidence score 0.0-1.0 for extraction quality"
        }
    },
    "required": ["title", "agency", "award_number", "amount", "role", "start_year", "end_year", "status", "grant_type", "confidence"],
    "additionalProperties": False
}


def parse_grant_entry(text: str, element_metadata: Optional[Dict] = None) -> Dict[str, Any]:
    """
    Parse a single grant/funding entry into structured fields.

    Args:
        text: Grant entry text
        element_metadata: Optional Word metadata (table structure, etc.)

    Returns:
        Structured grant record
    """
    system_prompt = """You are an academic CV grant parser. Extract structured data from grant and funding entries.

GUIDELINES:

1. TITLE EXTRACTION:
   - Extract complete grant/project title
   - Remove quotes if present

2. AGENCY:
   - Extract funding agency or sponsor name
   - Common agencies: NIH, NSF, AHRQ, DOD, American Heart Association, etc.
   - Include specific institute if mentioned (e.g., "NHLBI" vs "NIH")

3. AWARD NUMBER:
   - Extract grant/award number or identifier
   - Common formats: R01 HL123456, K08 CA234567, P01 AG345678
   - Use empty string "" if not available

4. AMOUNT:
   - Extract funding amount as string preserving original format
   - Common formats: "$500,000", "$1.2M", "$250K"
   - Include total amount or annual amount (whichever is given)
   - Use empty string "" if not available

5. ROLE:
   - PI: Principal Investigator
   - co-PI: Co-Principal Investigator
   - co-investigator: Co-Investigator
   - collaborator: Collaborator
   - consultant: Consultant
   - key_personnel: Key Personnel
   - other: Other roles
   - not_specified: Role not mentioned

6. DATES:
   - Extract start_year and end_year as integers
   - Use 9999 for current/ongoing/active grants
   - If only one year mentioned, use as start_year, end_year = 9999
   - If date range: "2010-2014" → start_year: 2010, end_year: 2014
   - Use 0 for missing dates

7. STATUS:
   - active: Currently active/ongoing
   - completed: Completed/ended
   - pending: Under review
   - awarded: Recently awarded but not started
   - not_funded: Not funded/rejected
   - in_preparation: In preparation
   - other: Other status

8. GRANT TYPE:
   - federal: Federal agencies (NIH, NSF, DOD, etc.)
   - foundation: Private foundations
   - industry: Industry/corporate funding
   - institutional: Internal institutional funding
   - other: Other sources

9. CONFIDENCE:
   - 0.9-1.0: Complete entry with title, agency, role, dates
   - 0.7-0.89: Most fields present, some ambiguity
   - 0.5-0.69: Some fields missing or ambiguous
   - 0.0-0.49: Very incomplete or unclear

Return structured JSON matching the schema."""

    user_prompt = f"""Parse this grant/funding entry:

{text}

Extract all available fields following the schema."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "grant_record",
            "strict": True,
            "schema": GRANT_SCHEMA
        }
    }

    result = call_llm(stage="parser_grants", messages=messages, response_format=response_format)

    # Parse response
    grant = json.loads(result["content"])

    # Add source metadata if available
    if element_metadata:
        grant["source_metadata"] = element_metadata

    return grant


def parse_grants_section(
    entries: List[Dict[str, Any]],
    section_metadata: Optional[Dict] = None
) -> List[Dict[str, Any]]:
    """
    Parse all entries in a grants/funding section.

    Args:
        entries: List of grant entries from segmentation
        section_metadata: Section-level metadata (taxonomy mapping, etc.)

    Returns:
        List of structured grant records
    """
    print(f"Parsing {len(entries)} grant entries...")

    parsed_grants = []

    for idx, entry in enumerate(entries, 1):
        text = entry.get("text_snippet", "")
        element_idx = entry.get("element_idx")

        if not text or len(text.strip()) < 10:
            # Skip empty or very short entries
            continue

        print(f"  [{idx}/{len(entries)}] Parsing entry...")

        try:
            # Parse entry
            grant = parse_grant_entry(
                text=text,
                element_metadata={"element_idx": element_idx} if element_idx else None
            )

            # Add original entry metadata
            grant["entry_id"] = entry.get("id")
            grant["order_index"] = entry.get("order_index")
            grant["original_text"] = text

            parsed_grants.append(grant)

            # Show progress
            confidence = grant.get("confidence", 0.0)
            conf_emoji = "✓" if confidence >= 0.8 else "⚠️" if confidence >= 0.6 else "✗"
            title = grant.get("title", "")
            agency = grant.get("agency", "")
            role = grant.get("role", "")
            status = grant.get("status", "")
            status_emoji = " (active)" if status == "active" else ""
            print(f"      {conf_emoji} {role} - {title[:50]}... | {agency}{status_emoji} (conf: {confidence:.2f})")

        except Exception as e:
            print(f"      ✗ Error: {e}")
            # Add unparsed entry with error info
            parsed_grants.append({
                "entry_id": entry.get("id"),
                "order_index": entry.get("order_index"),
                "original_text": text,
                "parse_error": str(e),
                "confidence": 0.0
            })

    return parsed_grants


def main():
    """
    Test grants parser on sample data.
    """
    import sys

    if len(sys.argv) < 2:
        print("Grants Parser - Phase 4.4")
        print()
        print("Usage: python grants_parser.py <segmented_cv_path>")
        print()
        print("Extracts structured grant data from segmented CV JSON.")
        print()
        sys.exit(1)

    segmented_cv_path = sys.argv[1]

    # Load segmented CV
    with open(segmented_cv_path, 'r') as f:
        segmented_cv = json.load(f)

    # Find grant-related sections
    print("="*80)
    print("GRANTS PARSER")
    print("="*80)
    print(f"Input: {segmented_cv_path}")
    print()

    all_parsed = []

    # Keywords for grant-related sections
    grant_keywords = [
        "grant", "funding", "support", "research support",
        "sponsored", "award", "fellowship"
    ]

    for group in segmented_cv.get("groups", []):
        label = group.get("label_inferred", "")

        # Check if this is a grant-related section
        if any(keyword in label.lower() for keyword in grant_keywords):
            print(f"Processing: {label}")
            print(f"Entries: {len(group.get('entries', []))}")
            print()

            parsed = parse_grants_section(
                entries=group.get("entries", []),
                section_metadata={"section_label": label}
            )

            all_parsed.extend(parsed)
            print()

        # Check subgroups
        for subgroup in group.get("subgroups", []):
            sub_label = subgroup.get("label_inferred", "")
            if any(keyword in sub_label.lower() for keyword in grant_keywords):
                print(f"Processing: {label} → {sub_label}")
                print(f"Entries: {len(subgroup.get('entries', []))}")
                print()

                parsed = parse_grants_section(
                    entries=subgroup.get("entries", []),
                    section_metadata={"section_label": label, "subsection_label": sub_label}
                )

                all_parsed.extend(parsed)
                print()

    # Calculate stats
    total_grants = len(all_parsed)
    active_grants = sum(1 for g in all_parsed if g.get("status") == "active")
    pi_grants = sum(1 for g in all_parsed if g.get("role") == "PI")
    federal_grants = sum(1 for g in all_parsed if g.get("grant_type") == "federal")

    # Write output
    output_path = Path(segmented_cv_path).parent / (Path(segmented_cv_path).stem.replace('_segmented', '') + '_grants_parsed.json')

    output_data = {
        "source_file": segmented_cv_path,
        "total_grants": total_grants,
        "active_grants": active_grants,
        "pi_grants": pi_grants,
        "federal_grants": federal_grants,
        "high_confidence": sum(1 for g in all_parsed if g.get("confidence", 0) >= 0.8),
        "medium_confidence": sum(1 for g in all_parsed if 0.6 <= g.get("confidence", 0) < 0.8),
        "low_confidence": sum(1 for g in all_parsed if g.get("confidence", 0) < 0.6),
        "grants": all_parsed
    }

    with open(output_path, 'w') as f:
        json.dump(output_data, f, indent=2)

    print("="*80)
    print("RESULTS")
    print("="*80)
    print(f"Total grants: {total_grants}")
    print(f"  Active grants: {active_grants}")
    print(f"  PI grants: {pi_grants}")
    print(f"  Federal grants: {federal_grants}")
    print()
    print(f"  High confidence (≥0.8): {output_data['high_confidence']}")
    print(f"  Medium confidence (0.6-0.79): {output_data['medium_confidence']}")
    print(f"  Low confidence (<0.6): {output_data['low_confidence']}")
    print()
    print(f"✓ Results saved to: {output_path}")
    print()


if __name__ == '__main__':
    main()
