"""
Honors & Awards Parser

Extracts structured data from honors/awards entries using GPT-4o-mini with Structured Outputs.

Output: Structured honor/award records ready for table insertion
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


HONOR_SCHEMA = {
    "type": "object",
    "properties": {
        "award_name": {
            "type": "string",
            "description": "Name of the honor, award, or scholarship"
        },
        "organization": {
            "type": "string",
            "description": "Awarding organization or institution"
        },
        "year_awarded": {
            "type": "integer",
            "description": "Year awarded, 0 if not available"
        },
        "award_type": {
            "type": "string",
            "enum": ["honor", "award", "scholarship", "fellowship", "prize", "recognition", "other"],
            "description": "Type of recognition"
        },
        "confidence": {
            "type": "number",
            "description": "Confidence score 0.0-1.0"
        }
    },
    "required": ["award_name", "organization", "year_awarded", "award_type", "confidence"],
    "additionalProperties": False
}


def parse_honor_entry(text: str) -> Dict[str, Any]:
    """Parse a single honor/award entry."""

    system_prompt = """You are an academic honors and awards parser. Extract structured data from honor/award entries.

GUIDELINES:
1. AWARD NAME: Extract the full name of the honor/award
2. ORGANIZATION: Extract the awarding organization
3. YEAR: Extract the year awarded as integer, use 0 if missing
4. TYPE: Classify as honor, award, scholarship, fellowship, prize, recognition, or other
5. CONFIDENCE:
   - 0.9-1.0: Complete with name, organization, and year
   - 0.7-0.89: Name and organization, year missing
   - 0.5-0.69: Name only, missing other details
   - 0.0-0.49: Very incomplete

Return structured JSON."""

    user_prompt = f"""Parse this honor/award entry:

{text}

Extract all fields."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "honor_record",
            "strict": True,
            "schema": HONOR_SCHEMA
        }
    }

    log_id = log_prompt_before_call(
        messages=messages,
        model="gpt-4o-mini",
        temperature=0.1,
        max_tokens=300,
        response_format=response_format,
        purpose="honor_parsing",
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
    log_prompt_response(log_id, response, "honor_parsing", elapsed_time)

    honor = json.loads(response.choices[0].message.content)
    honor['token_usage'] = {
        'prompt_tokens': response.usage.prompt_tokens,
        'completion_tokens': response.usage.completion_tokens,
        'total_tokens': response.usage.total_tokens
    }

    return honor


def parse_honors_section(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Parse all honors/awards entries."""

    print(f"Parsing {len(entries)} honor/award entries...")
    parsed_honors = []

    for idx, entry in enumerate(entries, 1):
        text = entry.get("text_snippet", "")
        if not text or len(text.strip()) < 10:
            continue

        print(f"  [{idx}/{len(entries)}] Parsing entry...")

        try:
            honor = parse_honor_entry(text)
            honor["entry_id"] = entry.get("id")
            honor["order_index"] = entry.get("order_index")
            honor["original_text"] = text
            parsed_honors.append(honor)

            confidence = honor.get("confidence", 0.0)
            conf_emoji = "✓" if confidence >= 0.8 else "⚠️" if confidence >= 0.6 else "✗"
            award = honor.get("award_name", "")
            print(f"      {conf_emoji} {award[:50]}... (conf: {confidence:.2f})")

        except Exception as e:
            print(f"      ✗ Error: {e}")
            parsed_honors.append({
                "entry_id": entry.get("id"),
                "original_text": text,
                "parse_error": str(e),
                "confidence": 0.0
            })

    return parsed_honors
