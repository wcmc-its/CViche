"""
Board Certifications Parser

Extracts structured data from board certification entries using GPT-4o-mini with Structured Outputs.

Strategy:
- LLM extracts: board name, certificate number, start/end dates
- Date normalization for certification periods
- Board eligibility vs. certified status

Output: Structured certification records ready for table insertion
"""

import json
from typing import Dict, List, Any, Optional

from unified_pipeline.llm_client import call_llm


# Certification schema for Structured Outputs
CERTIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "board_name": {
            "type": "string",
            "description": "Full name of the certifying board"
        },
        "certificate_number": {
            "type": "string",
            "description": "Certificate number or 'board eligible' if not yet certified"
        },
        "start_year": {
            "type": "integer",
            "description": "Year of initial certification, 0 if not available"
        },
        "end_year": {
            "type": "integer",
            "description": "Year certification expires or last renewed, 0 if ongoing or not available"
        },
        "status": {
            "type": "string",
            "enum": ["certified", "board_eligible", "expired", "inactive"],
            "description": "Current certification status"
        },
        "confidence": {
            "type": "number",
            "description": "Confidence score 0.0-1.0 for extraction quality"
        }
    },
    "required": ["board_name", "certificate_number", "start_year", "end_year", "status", "confidence"],
    "additionalProperties": False
}


def parse_certification_entry(text: str) -> Dict[str, Any]:
    """Parse a single board certification entry into structured fields."""

    system_prompt = """You are a medical CV board certification parser. Extract structured data from certification entries.

GUIDELINES:

1. BOARD NAME:
   - Extract full official board name
   - Include specialty (e.g., "American Board of Internal Medicine")

2. CERTIFICATE NUMBER:
   - Extract certificate/license number if present
   - Use "board eligible" if mentioned
   - Use "N/A" if no number provided

3. DATES:
   - Extract start_year (initial certification)
   - Extract end_year (expiration or last renewal)
   - Use 0 for missing dates
   - If ongoing/active with no end date, use 0 for end_year

4. STATUS:
   - certified: Has valid certification with number
   - board_eligible: Eligible but not yet certified
   - expired: Certification has lapsed
   - inactive: No longer maintaining certification

5. CONFIDENCE:
   - 0.9-1.0: Complete with board name, number, and dates
   - 0.7-0.89: Board name and number, dates unclear
   - 0.5-0.69: Board name only, missing other details
   - 0.0-0.49: Very incomplete or unclear

Return structured JSON matching the schema."""

    user_prompt = f"""Parse this board certification entry:

{text}

Extract all available fields following the schema."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "certification_record",
            "strict": True,
            "schema": CERTIFICATION_SCHEMA
        }
    }

    result = call_llm(stage="parser_certifications", messages=messages, response_format=response_format)

    # Parse response
    certification = json.loads(result["content"])

    certification['token_usage'] = {
        'prompt_tokens': result["prompt_tokens"],
        'completion_tokens': result["completion_tokens"],
        'total_tokens': result["total_tokens"]
    }

    return certification


def parse_certifications_section(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Parse all entries in a certifications section.

    Args:
        entries: List of certification entries from extraction

    Returns:
        List of structured certification records
    """
    print(f"Parsing {len(entries)} certification entries...")

    parsed_certifications = []

    for idx, entry in enumerate(entries, 1):
        text = entry.get("text_snippet", "")

        if not text or len(text.strip()) < 10:
            continue

        print(f"  [{idx}/{len(entries)}] Parsing entry...")

        try:
            certification = parse_certification_entry(text)

            # Add original entry metadata
            certification["entry_id"] = entry.get("id")
            certification["order_index"] = entry.get("order_index")
            certification["original_text"] = text

            parsed_certifications.append(certification)

            # Show progress
            confidence = certification.get("confidence", 0.0)
            conf_emoji = "✓" if confidence >= 0.8 else "⚠️" if confidence >= 0.6 else "✗"
            board = certification.get("board_name", "")
            status = certification.get("status", "")
            print(f"      {conf_emoji} {board} - {status} (conf: {confidence:.2f})")

        except Exception as e:
            print(f"      ✗ Error: {e}")
            parsed_certifications.append({
                "entry_id": entry.get("id"),
                "order_index": entry.get("order_index"),
                "original_text": text,
                "parse_error": str(e),
                "confidence": 0.0
            })

    return parsed_certifications
