"""
Mentoring & Trainees Parser

Extracts structured data from mentoring entries including:
- Research trainees
- Clinical trainees
- Postdoctoral fellows
- Graduate students
- Medical students

Output: Structured mentoring records ready for table insertion
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


MENTORING_SCHEMA = {
    "type": "object",
    "properties": {
        "mentee_name": {
            "type": "string",
            "description": "Name of the trainee or mentee"
        },
        "current_position": {
            "type": "string",
            "description": "Current position or site/location"
        },
        "training_level": {
            "type": "string",
            "enum": ["postdoc", "graduate_student", "medical_student", "resident", "fellow", "undergraduate", "high_school", "other"],
            "description": "Level of trainee"
        },
        "start_year": {
            "type": "integer",
            "description": "Year mentoring began, 0 if not available"
        },
        "end_year": {
            "type": "integer",
            "description": "Year mentoring ended, 0 if ongoing or not available"
        },
        "project_description": {
            "type": "string",
            "description": "Brief description of project or training details"
        },
        "confidence": {
            "type": "number",
            "description": "Confidence score 0.0-1.0"
        }
    },
    "required": ["mentee_name", "current_position", "training_level", "start_year", "end_year", "project_description", "confidence"],
    "additionalProperties": False
}


def parse_mentoring_entry(text: str) -> Dict[str, Any]:
    """Parse a single mentoring entry."""

    system_prompt = """You are an academic mentoring parser. Extract structured data from mentoring/trainee entries.

GUIDELINES:
1. MENTEE_NAME: Extract the trainee's full name
2. CURRENT_POSITION: Extract current position, site, or location (e.g., "Assistant Professor at MIT")
3. TRAINING_LEVEL: Classify as:
   - postdoc: Postdoctoral fellow/researcher
   - graduate_student: PhD, MS, or other graduate student
   - medical_student: MD student
   - resident: Medical resident
   - fellow: Clinical fellow
   - undergraduate: Undergraduate student
   - high_school: High school student
   - other: Other training level
4. START_YEAR: Year mentoring began as integer, use 0 if missing
5. END_YEAR: Year ended, use 0 if ongoing or missing
6. PROJECT_DESCRIPTION: Brief description of research project or training (use "N/A" if not provided)
7. CONFIDENCE:
   - 0.9-1.0: Complete with name, position, dates
   - 0.7-0.89: Name and position, dates incomplete
   - 0.5-0.69: Name only
   - 0.0-0.49: Incomplete

Return structured JSON."""

    user_prompt = f"""Parse this mentoring entry:

{text}

Extract all fields."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "mentoring_record",
            "strict": True,
            "schema": MENTORING_SCHEMA
        }
    }

    log_id = log_prompt_before_call(
        messages=messages,
        model="gpt-4o-mini",
        temperature=0.1,
        max_tokens=400,
        response_format=response_format,
        purpose="mentoring_parsing",
        context={"text_length": len(text)},
        caller_file=get_caller_info()
    )

    start_time = time.time()
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        response_format=response_format,
        temperature=0.1,
        max_tokens=400
    )
    elapsed_time = time.time() - start_time
    log_prompt_response(log_id, response, "mentoring_parsing", elapsed_time)

    mentoring = json.loads(response.choices[0].message.content)
    mentoring['token_usage'] = {
        'prompt_tokens': response.usage.prompt_tokens,
        'completion_tokens': response.usage.completion_tokens,
        'total_tokens': response.usage.total_tokens
    }

    return mentoring


def parse_mentoring_section(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Parse all mentoring entries."""

    print(f"Parsing {len(entries)} mentoring entries...")
    parsed_mentoring = []

    for idx, entry in enumerate(entries, 1):
        text = entry.get("text_snippet", "")
        if not text or len(text.strip()) < 10:
            continue

        print(f"  [{idx}/{len(entries)}] Parsing entry...")

        try:
            mentoring = parse_mentoring_entry(text)
            mentoring["entry_id"] = entry.get("id")
            mentoring["order_index"] = entry.get("order_index")
            mentoring["original_text"] = text
            parsed_mentoring.append(mentoring)

            confidence = mentoring.get("confidence", 0.0)
            conf_emoji = "✓" if confidence >= 0.8 else "⚠️" if confidence >= 0.6 else "✗"
            name = mentoring.get("mentee_name", "")
            level = mentoring.get("training_level", "")
            print(f"      {conf_emoji} {name} ({level}) (conf: {confidence:.2f})")

        except Exception as e:
            print(f"      ✗ Error: {e}")
            parsed_mentoring.append({
                "entry_id": entry.get("id"),
                "original_text": text,
                "parse_error": str(e),
                "confidence": 0.0
            })

    return parsed_mentoring
