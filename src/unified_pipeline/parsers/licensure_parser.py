"""
Medical Licensure Parser

Extracts structured data from medical licensure entries using GPT-4o-mini.

Output: Structured licensure records ready for table insertion
"""

import json
from typing import Dict, List, Any

from unified_pipeline.llm_client import call_llm


LICENSURE_SCHEMA = {
    "type": "object",
    "properties": {
        "state": {
            "type": "string",
            "description": "State or jurisdiction where licensed"
        },
        "license_number": {
            "type": "string",
            "description": "License number or registration ID"
        },
        "date_issued": {
            "type": "integer",
            "description": "Year license was issued, 0 if not available"
        },
        "start_year": {
            "type": "integer",
            "description": "Year registration began, 0 if not available"
        },
        "end_year": {
            "type": "integer",
            "description": "Year registration ended, 0 if ongoing or not available"
        },
        "license_type": {
            "type": "string",
            "enum": ["medical", "specialist", "temporary", "training", "other"],
            "description": "Type of medical license"
        },
        "status": {
            "type": "string",
            "enum": ["active", "expired", "inactive", "suspended", "revoked"],
            "description": "Current license status"
        },
        "confidence": {
            "type": "number",
            "description": "Confidence score 0.0-1.0"
        }
    },
    "required": ["state", "license_number", "date_issued", "start_year", "end_year", "license_type", "status", "confidence"],
    "additionalProperties": False
}


def parse_licensure_entry(text: str) -> Dict[str, Any]:
    """Parse a single medical licensure entry."""

    system_prompt = """You are a medical licensure parser. Extract structured data from medical license entries.

GUIDELINES:
1. STATE: Extract state/jurisdiction (e.g., "New York", "NY", "California")
2. LICENSE_NUMBER: Extract license or registration number
3. DATE_ISSUED: Year license was issued as integer, use 0 if missing
4. START_YEAR: Year registration began, use 0 if missing
5. END_YEAR: Year ended, use 0 if ongoing or missing
6. LICENSE_TYPE: Classify as medical, specialist, temporary, training, or other
7. STATUS:
   - active: Currently valid
   - expired: License has expired
   - inactive: Not currently practicing
   - suspended: Temporarily suspended
   - revoked: License revoked
8. CONFIDENCE:
   - 0.9-1.0: Complete with state, number, and dates
   - 0.7-0.89: State and number, dates incomplete
   - 0.5-0.69: State only
   - 0.0-0.49: Incomplete

Return structured JSON."""

    user_prompt = f"""Parse this medical licensure entry:

{text}

Extract all fields."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "licensure_record",
            "strict": True,
            "schema": LICENSURE_SCHEMA
        }
    }

    result = call_llm(stage="parser_licensure", messages=messages, response_format=response_format)

    licensure = json.loads(result["content"])
    licensure['token_usage'] = {
        'prompt_tokens': result["prompt_tokens"],
        'completion_tokens': result["completion_tokens"],
        'total_tokens': result["total_tokens"]
    }

    return licensure


def parse_licensure_section(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Parse all licensure entries."""

    print(f"Parsing {len(entries)} licensure entries...")
    parsed_licensures = []

    for idx, entry in enumerate(entries, 1):
        text = entry.get("text_snippet", "")
        if not text or len(text.strip()) < 10:
            continue

        print(f"  [{idx}/{len(entries)}] Parsing entry...")

        try:
            licensure = parse_licensure_entry(text)
            licensure["entry_id"] = entry.get("id")
            licensure["order_index"] = entry.get("order_index")
            licensure["original_text"] = text
            parsed_licensures.append(licensure)

            confidence = licensure.get("confidence", 0.0)
            conf_emoji = "✓" if confidence >= 0.8 else "⚠️" if confidence >= 0.6 else "✗"
            state = licensure.get("state", "")
            status = licensure.get("status", "")
            print(f"      {conf_emoji} {state} - {status} (conf: {confidence:.2f})")

        except Exception as e:
            print(f"      ✗ Error: {e}")
            parsed_licensures.append({
                "entry_id": entry.get("id"),
                "original_text": text,
                "parse_error": str(e),
                "confidence": 0.0
            })

    return parsed_licensures
