"""
Service & Committees Parser

Extracts structured data from service entries including:
- Institutional service
- Committee memberships
- Professional organization leadership
- Editorial roles

Output: Structured service records ready for table insertion
"""

import json
from typing import Dict, List, Any

from unified_pipeline.llm_client import call_llm


SERVICE_SCHEMA = {
    "type": "object",
    "properties": {
        "role": {
            "type": "string",
            "description": "Role or position (e.g., Chair, Member, Reviewer)"
        },
        "committee_or_activity": {
            "type": "string",
            "description": "Name of committee, journal, or activity"
        },
        "organization": {
            "type": "string",
            "description": "Institution or organization"
        },
        "start_year": {
            "type": "integer",
            "description": "Year started, 0 if not available"
        },
        "end_year": {
            "type": "integer",
            "description": "Year ended, 0 if ongoing or not available"
        },
        "service_type": {
            "type": "string",
            "enum": ["institutional", "committee", "professional_org", "editorial", "reviewer", "other"],
            "description": "Type of service"
        },
        "confidence": {
            "type": "number",
            "description": "Confidence score 0.0-1.0"
        }
    },
    "required": ["role", "committee_or_activity", "organization", "start_year", "end_year", "service_type", "confidence"],
    "additionalProperties": False
}


def normalize_to_wcm_format(service: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert unified pipeline field names to WCM field names.

    WCM Section O expects:
    - Role/Position
    - Department/Division
    - Institution
    - City
    - State/Province
    - Country
    - Dates
    - Scope/Description
    """
    wcm_entry = {}

    # Map role to Role/Position
    wcm_entry["Role/Position"] = service.get("role", "")

    # Map committee_or_activity to Department/Division
    wcm_entry["Department/Division"] = service.get("committee_or_activity", "")

    # Map organization to Institution
    wcm_entry["Institution"] = service.get("organization", "")

    # Empty placeholders for fields we don't have
    wcm_entry["City"] = ""
    wcm_entry["State/Province"] = ""
    wcm_entry["Country"] = ""

    # Format dates from start_year and end_year
    start_year = service.get("start_year", 0)
    end_year = service.get("end_year", 0)

    if start_year and start_year > 0:
        if end_year and end_year > 0:
            wcm_entry["Dates"] = f"{start_year}–{end_year}"
        else:
            wcm_entry["Dates"] = f"{start_year}–Present"
    else:
        wcm_entry["Dates"] = ""

    # Map service_type to Scope/Description if useful
    service_type = service.get("service_type", "")
    wcm_entry["Scope/Description"] = service_type.replace("_", " ").title() if service_type and service_type != "other" else ""

    # Keep metadata fields
    wcm_entry["confidence"] = service.get("confidence", 0.0)
    wcm_entry["token_usage"] = service.get("token_usage", {})

    return wcm_entry


def parse_service_entry(text: str) -> Dict[str, Any]:
    """Parse a single service entry."""

    system_prompt = """You are an academic service and committee parser. Extract structured data from service entries.

GUIDELINES:
1. ROLE: Extract the role (Chair, Member, Secretary, Reviewer, Editor, etc.)
2. COMMITTEE/ACTIVITY: Name of committee, journal, board, or activity
3. ORGANIZATION: Institution or organization name
4. START_YEAR: Year service began as integer, use 0 if missing
5. END_YEAR: Year ended, use 0 if ongoing or missing
6. SERVICE_TYPE:
   - institutional: University/hospital committees, internal service
   - committee: National/professional committees
   - professional_org: Leadership in professional societies
   - editorial: Journal editor, editorial board
   - reviewer: Manuscript or grant reviewer
   - other: Other service activities
7. CONFIDENCE:
   - 0.9-1.0: Complete with role, committee, organization, dates
   - 0.7-0.89: Role and committee/organization, dates incomplete
   - 0.5-0.69: Role and committee only
   - 0.0-0.49: Incomplete

Return structured JSON."""

    user_prompt = f"""Parse this service entry:

{text}

Extract all fields."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "service_record",
            "strict": True,
            "schema": SERVICE_SCHEMA
        }
    }

    result = call_llm(stage="parser_service", messages=messages, response_format=response_format)

    service = json.loads(result["content"])
    service['token_usage'] = {
        'prompt_tokens': result["prompt_tokens"],
        'completion_tokens': result["completion_tokens"],
        'total_tokens': result["total_tokens"]
    }

    # Convert to WCM format for compatibility with legacy template populator
    wcm_service = normalize_to_wcm_format(service)

    return wcm_service


def parse_service_section(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Parse all service entries."""

    print(f"Parsing {len(entries)} service entries...")
    parsed_services = []

    for idx, entry in enumerate(entries, 1):
        text = entry.get("text_snippet", "")
        if not text or len(text.strip()) < 10:
            continue

        print(f"  [{idx}/{len(entries)}] Parsing entry...")

        try:
            service = parse_service_entry(text)
            service["entry_id"] = entry.get("id")
            service["order_index"] = entry.get("order_index")
            service["original_text"] = text
            parsed_services.append(service)

            confidence = service.get("confidence", 0.0)
            conf_emoji = "✓" if confidence >= 0.8 else "⚠️" if confidence >= 0.6 else "✗"
            role = service.get("role", "")
            activity = service.get("committee_or_activity", "")
            print(f"      {conf_emoji} {role} - {activity[:40]}... (conf: {confidence:.2f})")

        except Exception as e:
            print(f"      ✗ Error: {e}")
            parsed_services.append({
                "entry_id": entry.get("id"),
                "original_text": text,
                "parse_error": str(e),
                "confidence": 0.0
            })

    return parsed_services
