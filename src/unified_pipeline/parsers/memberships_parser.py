"""
Professional Memberships Parser

Extracts structured data from professional membership entries using GPT-4o-mini.

Output: Structured membership records ready for table insertion
"""

import os
import json
import time
from typing import Dict, List, Any
from openai import OpenAI
from pathlib import Path

# Import prompt logger
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "core"))
from prompt_logger import log_prompt_before_call, log_prompt_response, get_caller_info

# Use default environment context to avoid expensive SKU mapping
client = OpenAI()


MEMBERSHIP_SCHEMA = {
    "type": "object",
    "properties": {
        "organization": {
            "type": "string",
            "description": "Name of the professional organization or society"
        },
        "start_year": {
            "type": "integer",
            "description": "Year membership began, 0 if not available"
        },
        "end_year": {
            "type": "integer",
            "description": "Year membership ended, 0 if ongoing or not available"
        },
        "membership_type": {
            "type": "string",
            "enum": ["member", "fellow", "associate", "honorary", "student", "emeritus", "other"],
            "description": "Type of membership"
        },
        "confidence": {
            "type": "number",
            "description": "Confidence score 0.0-1.0"
        }
    },
    "required": ["organization", "start_year", "end_year", "membership_type", "confidence"],
    "additionalProperties": False
}


def parse_membership_entry(text: str) -> Dict[str, Any]:
    """Parse a single membership entry."""

    system_prompt = """You are a professional membership parser. Extract structured data from membership entries.

GUIDELINES:
1. ORGANIZATION: Extract full organization name
2. START_YEAR: Extract year membership began as integer, use 0 if missing
3. END_YEAR: Extract year ended, use 0 if ongoing or missing
4. TYPE: Classify as member, fellow, associate, honorary, student, emeritus, or other
5. CONFIDENCE:
   - 0.9-1.0: Complete with organization and dates
   - 0.7-0.89: Organization and start date
   - 0.5-0.69: Organization only
   - 0.0-0.49: Incomplete

Return structured JSON."""

    user_prompt = f"""Parse this membership entry:

{text}

Extract all fields."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "membership_record",
            "strict": True,
            "schema": MEMBERSHIP_SCHEMA
        }
    }

    log_id = log_prompt_before_call(
        messages=messages,
        model="gpt-4o-mini",
        temperature=0.1,
        max_tokens=300,
        response_format=response_format,
        purpose="membership_parsing",
        context={"text_length": len(text)},
        caller_file=get_caller_info()
    )

    start_time = time.time()
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        response_format=response_format,
        temperature=0.1,
        max_tokens=300
    )
    elapsed_time = time.time() - start_time
    log_prompt_response(log_id, response, "membership_parsing", elapsed_time)

    membership = json.loads(response.choices[0].message.content)
    membership['token_usage'] = {
        'prompt_tokens': response.usage.prompt_tokens,
        'completion_tokens': response.usage.completion_tokens,
        'total_tokens': response.usage.total_tokens
    }

    return membership


def parse_memberships_section(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Parse all membership entries."""

    print(f"Parsing {len(entries)} membership entries...")
    parsed_memberships = []

    for idx, entry in enumerate(entries, 1):
        text = entry.get("text_snippet", "")
        if not text or len(text.strip()) < 10:
            continue

        print(f"  [{idx}/{len(entries)}] Parsing entry...")

        try:
            membership = parse_membership_entry(text)
            membership["entry_id"] = entry.get("id")
            membership["order_index"] = entry.get("order_index")
            membership["original_text"] = text
            parsed_memberships.append(membership)

            confidence = membership.get("confidence", 0.0)
            conf_emoji = "✓" if confidence >= 0.8 else "⚠️" if confidence >= 0.6 else "✗"
            org = membership.get("organization", "")
            print(f"      {conf_emoji} {org[:50]}... (conf: {confidence:.2f})")

        except Exception as e:
            print(f"      ✗ Error: {e}")
            parsed_memberships.append({
                "entry_id": entry.get("id"),
                "original_text": text,
                "parse_error": str(e),
                "confidence": 0.0
            })

    return parsed_memberships
