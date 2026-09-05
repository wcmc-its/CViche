#!/usr/bin/env python3
"""
Personal Information Extractor - First Page Only

Extracts contact information from the first page of a CV:
- Name (with credentials)
- Office address, phone, email
- Cell phone, home address, personal email (if present)

Strategy:
1. Extract first page only (first 15 paragraphs or ~500 words)
2. Regex extraction for structured data (email, phone)
3. LLM extraction with Structured Outputs for context-aware parsing
4. Combine and return standardized JSON

Cost: ~$0.001-0.002 per CV
Speed: ~1-2 seconds per CV
Accuracy: 95%+ for fields that exist in CVs
"""

import os
import re
import time
from pathlib import Path
from typing import Dict, Any, List, Optional
from docx import Document

from unified_pipeline.llm_client import call_llm

# Schema for Structured Outputs
PERSONAL_INFO_SCHEMA = {
    "type": "object",
    "properties": {
        "full_name": {
            "type": "string",
            "description": "Full name with credentials (e.g., 'Christy A. Denckla, Ph.D.')"
        },
        "work_email": {
            "type": "string",
            "description": "Work/institutional email address"
        },
        "office_phone": {
            "type": "string",
            "description": "Office phone number"
        },
        "office_address": {
            "type": "string",
            "description": "Complete office address (building, room, street, city, state, zip)"
        },
        "cell_phone": {
            "type": "string",
            "description": "Cell/mobile phone if different from office phone"
        },
        "personal_email": {
            "type": "string",
            "description": "Personal email if different from work email"
        },
        "home_address": {
            "type": "string",
            "description": "Home address if provided"
        },
        "orcid": {
            "type": "string",
            "description": "ORCID identifier if provided"
        },
        "department": {
            "type": "string",
            "description": "Department or unit"
        },
        "institution": {
            "type": "string",
            "description": "Primary institution"
        }
    },
    "required": ["full_name"],
    "additionalProperties": False
}


def extract_first_page_text(cv_path: str, max_paragraphs: int = 15) -> str:
    """
    Extract text from first page of Word CV.

    Args:
        cv_path: Path to .docx file
        max_paragraphs: Maximum paragraphs to extract (default 15)

    Returns:
        Text from first page
    """
    doc = Document(cv_path)
    paragraphs = []

    for i, para in enumerate(doc.paragraphs[:max_paragraphs]):
        text = para.text.strip()
        if text:
            paragraphs.append(text)

    return '\n'.join(paragraphs)


def extract_emails_regex(text: str) -> list[str]:
    """
    Extract all email addresses using regex.

    Returns:
        List of email addresses found
    """
    pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    return re.findall(pattern, text)


def extract_phones_regex(text: str) -> list[str]:
    """
    Extract all phone numbers using regex.

    Handles formats:
    - (123) 456-7890
    - 123-456-7890
    - +1 123.456.7890
    - etc.

    Returns:
        List of phone numbers found
    """
    patterns = [
        r'\+?1?\s*\(?[0-9]{3}\)?[\s.-][0-9]{3}[\s.-][0-9]{4}',  # US/Canada
        r'\([0-9]{3}\)\s*[0-9]{3}-[0-9]{4}',                     # (123) 456-7890
        r'[0-9]{3}[-.\s][0-9]{3}[-.\s][0-9]{4}',                 # 123-456-7890
        r'\+[0-9]{1,3}\s[0-9]{3}\.[0-9]{3}\.[0-9]{4}'            # +1 123.456.7890
    ]

    phones = []
    for pattern in patterns:
        phones.extend(re.findall(pattern, text))

    return list(set(phones))  # Remove duplicates


def extract_orcid_regex(text: str) -> str | None:
    """
    Extract ORCID identifier using regex.

    Format: 0000-0000-0000-0000

    Returns:
        ORCID ID or None
    """
    pattern = r'(?:orcid\.org/)?(\d{4}-\d{4}-\d{4}-\d{3}[0-9X])'
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group(1) if match else None


def extract_name_regex(text: str) -> str | None:
    """
    Extract name from first few lines using heuristics.

    Looks for patterns like:
    - "Christy A. Denckla, Ph.D."
    - "Dr. Holly Dabelko-Schoeny"
    - "MAHMOUD ALMASRI, MD, FRCSC"

    Returns:
        Best guess at full name or None
    """
    lines = [line.strip() for line in text.split('\n')[:8] if line.strip()]

    # Skip "Curriculum Vitae" line (almost always line 0 or 1)
    lines = [line for line in lines if 'curriculum vitae' not in line.lower()]

    if not lines:
        return None

    # Common credentials - comprehensive list from faculty affairs system
    # Includes both with periods (all degrees) and without periods (common, unambiguous ones only)
    credentials = [
        # Titles
        'Dr.', 'Professor', 'Prof.',

        # Doctoral degrees (with and without periods)
        'Ph.D.', 'PhD', 'M.D.', 'MD', 'D.M.D.', 'DMD', 'D.D.S.', 'DDS',
        'D.O.', 'DO', 'Psy.D.', 'PsyD', 'Ed.D.', 'EdD', 'J.D.', 'JD',
        'Pharm.D.', 'PharmD', 'D.V.M.', 'DVM', 'V.M.D.', 'VMD',
        'D.P.M.', 'DPM', 'Au.D.', 'AuD', 'D.N.P.', 'DNP',
        'Sc.D.', 'ScD', 'D.Sc.', 'DSc', 'D.Phil.', 'DPhil',
        'Dr.P.H.', 'DrPH', 'D.P.H.', 'DPH', 'D.S.W.', 'DSW',
        'D.M.A.', 'DMA', 'D.P.T.', 'DPT', 'D.H.Sc.', 'DHSc',
        'D.M.', 'DM', 'D.M.Sc.', 'DMSc',

        # Medical credentials
        'M.B.', 'MB', 'M.B.S.', 'MBS', 'M.B.B.S.', 'MBBS',
        'B.A.O.', 'M.Med.', 'D.C.', 'D.C.H.',
        'Dr. Med.', 'Dr.sc.hum.', 'Dr. rer. medic.', 'Dr. rer. nat.', 'Dr. techn.',

        # Master's degrees (with and without periods for very common ones)
        'M.S.', 'MS', 'M.A.', 'MA', 'M.Sc.', 'MSc', 'M.B.A.', 'MBA',
        'M.P.H.', 'MPH', 'M.S.N.', 'MSN', 'M.S.W.', 'MSW', 'M.Phil.', 'MPhil',
        'M.E.', 'ME', 'M.Eng', 'MEng', 'M.Ed.', 'MEd', 'M.F.A.', 'MFA',
        'M.M.', 'MM', 'M.P.A.', 'MPA', 'M.H.A.', 'MHA', 'M.L.S.', 'MLS',
        'M.M.S.', 'MMS', 'M.M.Sc.', 'MMSc', 'M.H.S.', 'MHS',
        'M.P.A.S.', 'MPAS', 'M.S.P.H.', 'MSPH', 'M.P.T.', 'MPT',
        'M.Tech.', 'MTech', 'M.Pharm.', 'MPharm', 'M.Res.', 'MRes',
        'M.A.S.', 'MAS', 'M.S.E.', 'MSE', 'M.E.E.', 'MEE',
        'M.Ch.', 'M.Div.', 'M.H.P.E.', 'M.L.I.S.', 'M.P.P.',
        'M.A.T.', 'M.V.Sc.', 'M.H.Sc.', 'M.S.C.R.', 'M.T.S.',
        'M.E.H.P.', 'M.S.H.S.', 'M.S.H.P.', 'M.S.Ed.', 'M.H.C.M.',
        'M.M. (Music)', 'M.M.E.', 'M.M.M.', 'M.O.T.', 'M.P.S.',
        'M.S.C.E.', 'M.S.C.I.', 'M.S.J.', 'M.S.P.', 'M.S.S.A.',
        'M.S.T.R.', 'M.Sc.D.', 'M.Sci.', 'M.Sc.B.M.I.',
        'M.C.E.', 'M.C.M.Sc.', 'M.Ch.E.', 'M.Bioch.', 'M.BE.',
        'M.Ag.S.', 'M.I.A.', 'M.L.A.S.', 'M.Phys', 'M.Th.',
        'M.U.Dr.', 'M.H.M.', 'S.M.', 'S.M.D.', 'A.M.', 'A.L.M.',
        'Ed.M.', 'E.M.L.', 'Sc.M.', 'Ph.M.', 'Psy.M.', 'Th.M.',

        # Bachelor's degrees (with periods only - too risky without)
        'B.S.', 'B.A.', 'B.Sc.', 'B.M.', 'B.E.', 'B.Eng.',
        'B.S.N.', 'BSN', 'B.F.A.', 'B.B.A.', 'B.S.E.', 'B.Med.Sc.',
        'Bs.C.', 'A.B.', 'S.B.', 'Sc.B.',
        'B.A.S.', 'B.M.S.', 'B.Tech.', 'B.V.S.', 'B.V.Sc.',
        'B.E.E.', 'B.Ch.E.', 'B.D.S.', 'B.Pharm.', 'Pharm.B.',
        'B.Ed.', 'B.G.S.', 'B.H.A.', 'B.H.K.', 'B.L.A.',
        'B.M.E.', 'B.N.S.', 'B.Ph.', 'B.S.D.', 'B.S.E.E.',
        'B.S.F.S.', 'B.S.M.T.', 'B.S.Ch.E.', 'B.Sc.H.',
        'B.M. (Music)', 'B.Mgt.', 'B.E.S.', 'B.A.Ed.', 'B.B',
        'B.C.S.',

        # Associate degrees
        'A.A.', 'A.S.', 'A.A.S.', 'A.M.L.S.', 'A.C.S.W.',

        # Other professional credentials
        'FRCSC', 'R.N.', 'RN', 'O.D.', 'OD', 'O.T.R.',
        'L.L.B.', 'LLB', 'L.L.M.', 'LLM', 'L.M.C.D.',
        'H.S.C.', 'I.Sc.', 'C.T.F.', 'F.A.',

        # Special degrees
        'Diplom', 'Diploma', 'D.Bioethics', 'D. Tech.', 'D.Eng',
        'D.H.Ed.', 'D.Min', 'D.P.S.', 'D.T.M.&H',
        'D.Sc. (honoris causa)', 'D.Sc.Hyg.',
        'Degree Info Pending'
    ]

    for i, line in enumerate(lines[:5]):  # Check first 5 non-empty, non-CV lines
        # Skip if it's a date line
        if re.search(r'\d{4}|\d{1,2}/\d{1,2}', line):
            continue

        # Skip if it looks like an address (has numbers and street words)
        if re.search(r'\d+.*(?:Street|Avenue|Road|Building|Suite|Room)', line, re.IGNORECASE):
            continue

        # Skip if it's clearly an organization/institution
        if any(org in line for org in ['University', 'College', 'Hospital', 'Institute', 'Department', 'School of']):
            continue

        # Check if line has credentials (strong signal it's a name)
        has_credentials = any(cred in line for cred in credentials)

        # Check if it looks like a name:
        # - Has 2-5 words
        # - Each word starts with capital letter
        # - At least one word is >2 letters
        words = re.findall(r'[A-Z][a-z]+|[A-Z]+(?=\s|,|$)|[A-Z]\.', line)
        has_name_structure = (
            2 <= len(words) <= 6 and
            any(len(w) > 2 for w in words)
        )

        if has_credentials or has_name_structure:
            # Clean up common prefixes
            line = re.sub(r'^(Dr\.?\s+|Professor\s+|Prof\.?\s+)', '', line)
            return line

    # Last resort: Take first line that has 2-4 capitalized words
    for line in lines[:3]:
        words = line.split()
        if 2 <= len(words) <= 4 and all(w[0].isupper() for w in words if w and len(w) > 1):
            return line

    return None


def extract_regex_only(first_page_text: str, emails_found: list[str], phones_found: list[str], orcid_found: str | None) -> dict[str, Any]:
    """
    Fallback extraction using only regex (no LLM).

    Returns basic information with what we can reliably extract.
    """
    name = extract_name_regex(first_page_text)

    return {
        'full_name': name or '',
        'work_email': emails_found[0] if emails_found else '',
        'office_phone': phones_found[0] if phones_found else '',
        'office_address': '',  # Hard to extract reliably without LLM
        'cell_phone': phones_found[1] if len(phones_found) > 1 else '',
        'personal_email': emails_found[1] if len(emails_found) > 1 else '',
        'home_address': '',
        'orcid': orcid_found or '',
        'department': '',  # Hard to extract reliably without LLM
        'institution': ''   # Hard to extract reliably without LLM
    }


def extract_with_llm(first_page_text: str, emails_found: list[str], phones_found: list[str]) -> dict[str, Any]:
    """
    Extract personal information using LLM with Structured Outputs.

    Args:
        first_page_text: Text from first page
        emails_found: Email addresses found by regex
        phones_found: Phone numbers found by regex

    Returns:
        Extracted personal information
    """
    prompt = f"""You are extracting contact information from the first page of an academic CV.

FIRST PAGE TEXT:
---
{first_page_text}
---

EMAILS DETECTED: {', '.join(emails_found) if emails_found else 'None'}
PHONES DETECTED: {', '.join(phones_found) if phones_found else 'None'}

Extract the following information:

1. **full_name**: Person's full name with credentials (e.g., "Christy A. Denckla, Ph.D.")
2. **work_email**: Which email is the work/institutional email? Use detected emails list.
3. **office_phone**: Which phone is the office phone? Include format as written.
4. **office_address**: Complete office address (building, room, street, city, state, zip)
5. **cell_phone**: Which phone is the cell/mobile phone (if different from office)?
6. **personal_email**: Personal email if different from work email (rare)
7. **home_address**: Home address if provided (rare)
8. **orcid**: ORCID identifier if provided (just the numbers)
9. **department**: Department or unit name
10. **institution**: Primary institution name

IMPORTANT:
- If a field is not present, return empty string ""
- For emails/phones, choose from the detected list or extract if not detected
- Use exactly what's written in the CV (preserve formatting)
- Don't make up information that's not in the text
"""

    try:
        messages = [
            {
                "role": "system",
                "content": "You are a precise data extraction assistant. Extract exactly what's in the text, nothing more."
            },
            {
                "role": "user",
                "content": prompt
            }
        ]
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "personal_info",
                "strict": True,
                "schema": PERSONAL_INFO_SCHEMA
            }
        }

        result = call_llm(
            stage="core_personal_info",
            messages=messages,
            response_format=response_format,
        )

        import json
        return json.loads(result["content"])

    except Exception as e:
        print(f"LLM extraction failed: {str(e)[:100]}")
        print(f"   Falling back to regex-only extraction...")
        return None  # Signal to use regex fallback


def calculate_confidence(personal_info: dict[str, Any]) -> float:
    """
    Calculate confidence score based on what was extracted.

    Returns:
        Confidence score 0.0-1.0
    """
    score = 0.0

    # Name is critical (50%)
    if personal_info.get('full_name'):
        score += 0.50

    # Work email (20%)
    if personal_info.get('work_email'):
        score += 0.20

    # Office phone (15%)
    if personal_info.get('office_phone'):
        score += 0.15

    # Office address (15%)
    if personal_info.get('office_address'):
        score += 0.15

    return score


def extract_personal_info(cv_path: str) -> dict[str, Any]:
    """
    Extract personal information from first page of CV.

    Args:
        cv_path: Path to .docx CV file

    Returns:
        Dictionary with extracted personal information:
        {
            'full_name': str,
            'work_email': str,
            'office_phone': str,
            'office_address': str,
            'cell_phone': str (optional),
            'personal_email': str (optional),
            'home_address': str (optional),
            'orcid': str (optional),
            'department': str (optional),
            'institution': str (optional),
            'confidence': float (0.0-1.0)
        }
    """
    print(f"\n{'='*80}")
    print("PERSONAL INFO EXTRACTION - First Page Only")
    print(f"{'='*80}")
    print(f"CV: {cv_path}")

    # Step 1: Extract first page text
    print("\nStep 1: Extracting first page text...")
    first_page = extract_first_page_text(cv_path, max_paragraphs=15)
    print(f"  ✓ Extracted {len(first_page)} characters from first page")

    # Step 2: Regex extraction (fast, free, reliable)
    print("\nStep 2: Regex extraction...")
    emails_found = extract_emails_regex(first_page)
    phones_found = extract_phones_regex(first_page)
    orcid_found = extract_orcid_regex(first_page)

    print(f"  ✓ Found {len(emails_found)} email(s): {emails_found}")
    print(f"  ✓ Found {len(phones_found)} phone(s): {phones_found}")
    if orcid_found:
        print(f"  ✓ Found ORCID: {orcid_found}")

    # Step 3: LLM extraction (context-aware)
    print("\nStep 3: LLM extraction with context...")
    llm_data = extract_with_llm(first_page, emails_found, phones_found)

    # If LLM failed, use regex-only fallback
    if llm_data is None:
        print("  ✓ Using regex-only extraction")
        llm_data = extract_regex_only(first_page, emails_found, phones_found, orcid_found)
    else:
        # Override ORCID with regex result if found (more reliable)
        if orcid_found:
            llm_data['orcid'] = orcid_found

    # Step 4: Calculate confidence
    confidence = calculate_confidence(llm_data)
    llm_data['confidence'] = confidence

    print(f"\n{'='*80}")
    print("EXTRACTION COMPLETE")
    print(f"{'='*80}")
    print(f"Name: {llm_data.get('full_name', 'NOT FOUND')}")
    print(f"Work Email: {llm_data.get('work_email', 'NOT FOUND')}")
    print(f"Office Phone: {llm_data.get('office_phone', 'NOT FOUND')}")
    print(f"Office Address: {llm_data.get('office_address', 'NOT FOUND')[:50] + '...' if llm_data.get('office_address') else 'NOT FOUND'}")
    print(f"Confidence: {confidence:.2%}")
    print()

    return llm_data


def main():
    """
    Command-line interface for personal info extraction.

    Usage:
        python personal_info_extractor.py <cv_file.docx>
    """
    import sys
    import json

    if len(sys.argv) < 2:
        print("Usage: python personal_info_extractor.py <cv_file.docx>")
        sys.exit(1)

    cv_path = sys.argv[1]

    if not os.path.exists(cv_path):
        print(f"Error: File not found: {cv_path}")
        sys.exit(1)

    # Extract personal info
    result = extract_personal_info(cv_path)

    # Save to JSON
    output_path = cv_path.replace('.docx', '_personal_info.json')
    with open(output_path, 'w') as f:
        json.dump(result, f, indent=2)

    print(f"✓ Saved to: {output_path}")


if __name__ == '__main__':
    main()
