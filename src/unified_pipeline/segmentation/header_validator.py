#!/usr/bin/env python3
"""
Header Validation Pass: Detect false section headers (metadata, identifiers, etc.)

This runs AFTER detect_section_headers() to filter out false positives like:
- ISBN: XXX
- DOI: XXX
- Grant numbers (R01, U54, etc.)
- Standalone identifiers

But KEEPS intentional organizational headers like:
- Years (2024, 2023)
- Real section titles
"""

import re
from typing import List, Dict
from unified_pipeline.llm_client import call_llm

# Import comprehensive locked headers list (~850+ headers)
try:
    from .locked_headers_v6 import LOCKED_CV_HEADERS
except (ImportError, ValueError):
    from locked_headers_v6 import LOCKED_CV_HEADERS

# Regex patterns for obvious non-headers (metadata/identifiers)
# NOTE: These should be VERY conservative - only match clear technical identifiers
METADATA_PATTERNS = [
    r'^ISBN:\s*[\d\-X]+$',                    # ISBN: 978-1-119-63516-1
    r'^DOI:\s*10\.\d+/.+$',                   # DOI: 10.1234/example
    r'^PMID:\s*\d+$',                         # PMID: 12345678
    r'^[A-Z]\d{2}\s+[A-Z]{2}\d{6}',          # R01 OH010295, U54 OH007548
    r'^\d{3}-\d{4}-[A-Z]-\d{5}$',            # Grant formats like 254-2007-M-19684
    r'^https?://',                            # URLs
    # Note: Removed email pattern - emails in header area might be intentional CV info
]

# JSON schema for batch header validation (V6: includes header_likeness_score)
HEADER_VALIDATION_SCHEMA = {
    "type": "object",
    "properties": {
        "validations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "header_index": {
                        "type": "integer",
                        "description": "Index of the header in the input list"
                    },
                    "is_valid_header": {
                        "type": "boolean",
                        "description": "True if this is a valid CV section header, False if it's metadata/content"
                    },
                    "header_likeness_score": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                        "description": "Confidence score (0.0-1.0) that this text is actually a section header"
                    },
                    "reason": {
                        "type": "string",
                        "description": "Brief explanation of the classification and score"
                    }
                },
                "required": ["header_index", "is_valid_header", "header_likeness_score", "reason"],
                "additionalProperties": False
            }
        }
    },
    "required": ["validations"],
    "additionalProperties": False
}


def is_metadata_pattern(text: str) -> bool:
    """
    Check if text matches known metadata patterns (quick regex filter).

    Returns True if it's obviously metadata (not a header).
    """
    text = text.strip()

    for pattern in METADATA_PATTERNS:
        if re.match(pattern, text, re.IGNORECASE):
            return True

    return False


def validate_headers_batch(
    headers: List[str],
) -> List[Dict]:
    """
    Validate a batch of detected headers using LLM.

    Args:
        headers: List of header texts to validate

    Returns:
        List of validation results: [{"header_index": 0, "is_valid_header": True/False, "reason": "..."}]
    """

    if not headers:
        return []

    # Build numbered list for LLM
    headers_text = "\n".join(f"{i}. {h}" for i, h in enumerate(headers))

    system_prompt = """You are validating candidate CV / résumé section headers.

For each candidate, your job is to:
1. Decide how likely it is that the text is a section or subsection header (vs. just metadata or random text).
2. Remove only obvious non-headers, such as technical identifiers, reference lines, or fragments.
3. Return both:
   • a boolean (is_valid_header)
   • a header_likeness_score between 0.0 and 1.0.

─────────────────────────────────────────────────────────────────────

1. What counts as a "header"?

Treat as header-like anything that could plausibly organize CV content in any discipline:

Keep as potentially valid headers:
• Group names (NOT the CV owner's own name at the top)
  "Katalin Group Members", "Lab Members", "Research Team"
• Department/unit names AS SECTION HEADERS (not just institution identification)
  "Department of Epidemiology" (as a section), "Harvard Medical School" (as affiliation section)
• Contact info LABELS (short labels, not full contact info lines)
  "Email", "Phone", "Contact Information", "Address"
• Canonical CV sections (any industry/discipline)
  "Education", "Work Experience", "Research Experience", "Teaching",
  "Publications", "Other Publications", "Presentations",
  "Grants", "Awards", "Honors and Awards", "Service", "Skills",
  "Clinical Experience", "Leadership", "Projects", "Summary"
• Subsections / content categories
  "Peer-reviewed Articles", "Conference Proceedings",
  "Graduate Education", "Invited Presentations",
  "National Service", "Editorial Activities"
• Organizational / period headers
  "2024", "2020–2023", "Current Positions", "Active (Funded)"
• Sections with notes
  "Mentoring and Student Supervision – Removed for Public CV",
  "Teaching (Selected)", "Publications – In Preparation"

If the text can reasonably be a label above a group of entries, treat it as header-like.

─────────────────────────────────────────────────────────────────────

2. What should be rejected as non-headers?

2.1 Pure technical identifiers (always invalid)
• ISBNs: "ISBN: 978-1-119-63516-1"
• DOIs: "DOI: 10.1234/example", "10.1002/abc.12345"
• Grant / contract numbers by themselves: "R01 OH010295", "U54-OH007548", "R21-OH009920-01", "254-2007-M-19684"
• Database IDs: "PMID: 12345678", "PMCID: PMC12345678", "ORCID: 0000-0002-1234-5678"
• Standalone URLs: "https://example.com"
• Standalone email addresses: "john.doe@email.com"

These should have low scores (~0.0–0.2) and is_valid_header = false.

2.2 Obvious fragments or junk
• Just a stopword or short fragment: "the", "and clinical", "of the", "and"
• Clearly mid-sentence: "as described in the following section"
• Contains trailing commas or conjunctions: "Teaching and", "Studies in"
• Only one or two characters (unless a known header type like a year): "A", "B", "X"

2.3 Full sentences or paragraphs
• Contains pronouns and verbs: "I led the development of…"
• Ends with a period and has 10+ words
• Looks like an abstract, summary, or instruction sentence, not a short noun phrase

2.4 Reference lines / citation-style entries
• "Smith J, 2012. Title of article. Journal of X 5(3):123–130."
• Lines with authors, year, title, journal/book, volume/pages.

2.5 Data blobs / addresses
• Full address line: "123 Main Street, Ithaca, NY 14850"

2.6 CV header/title area content (NOT section headers)
These typically appear at the VERY TOP of a CV before any actual sections:
• Person's full name with credentials: "John Smith, MD, PhD", "Curriculum Vitae - Jane Doe, MS, MA"
• Job titles without section context: "PhD Student", "Associate Professor", "Research Scientist"
• Institution names alone: "University of Missouri – Columbia, United States", "Harvard Medical School"
• Contact info lines: "Phone: 555-1234 | Email: x@y.com", combined phone/email/address lines
• CV document titles: "Curriculum Vitae", "CV", "Resume" (when appearing as document header, not section)

These are the NAME/TITLE/CONTACT block, not organizational sections. Score them 0.2-0.4 and mark as invalid.

─────────────────────────────────────────────────────────────────────

3. Scoring guidelines

Produce header_likeness_score between 0.0 and 1.0.

Use this rough scale:
• 0.90–1.00 – Canonical / obvious header
  "EDUCATION", "RESEARCH EXPERIENCE", "PUBLICATIONS", "CLINICAL PRACTICE"
• 0.70–0.89 – Strongly plausible header
  "Invited Presentations", "Grants and Contracts", "Technical Skills"
• 0.40–0.69 – Ambiguous; could be a header or content
  "Current", "Active", "National", "Recent Work"
• 0.10–0.39 – Unlikely header
  Fragmentary or more like content
• 0.00–0.09 – Clearly an identifier or junk
  DOIs, ISBNs, pure grant numbers, standalone URLs/emails

Also return is_valid_header using a ~0.5 threshold:
• true if you believe it functions as a header in a CV
• false otherwise

─────────────────────────────────────────────────────────────────────

4. Output format

For each header, return:
• header_index: number in the list
• is_valid_header: true/false
• header_likeness_score: float between 0.0 and 1.0
• reason: short explanation"""

    user_prompt = f"""Validate these detected CV section headers:

{headers_text}

Return a validation for each header indicating whether it's a valid section header or metadata/identifier."""

    try:
        import json

        llm_result = call_llm(
            stage="segmentation_header_validator",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "header_validations",
                    "strict": True,
                    "schema": HEADER_VALIDATION_SCHEMA
                }
            },
            temperature=0.1
        )

        result = json.loads(llm_result["content"])

        return {
            'validations': result['validations'],
            'cost': llm_result["cost"],
            'tokens': {
                'prompt': llm_result["prompt_tokens"],
                'completion': llm_result["completion_tokens"],
                'total': llm_result["total_tokens"]
            },
            'model_used': llm_result["model"]
        }

    except Exception as e:
        print(f"Error validating headers: {e}")
        # Fallback: assume all are valid if LLM fails
        return {
            'validations': [
                {'header_index': i, 'is_valid_header': True, 'reason': f'LLM validation failed: {str(e)}'}
                for i in range(len(headers))
            ],
            'cost': 0.0,
            'tokens': {'prompt': 0, 'completion': 0, 'total': 0},
            'model_used': 'unknown',
            'error': str(e)
        }


def filter_false_headers(
    sections: List[Dict],
    batch_size: int = 20,
    use_llm: bool = True,
) -> Dict:
    """
    Filter out false section headers from detected sections.

    Args:
        sections: List of section dicts from detect_section_headers()
        batch_size: How many headers to validate per LLM call
        use_llm: Whether to use LLM for validation (False = regex only)

    Returns:
        {
            'filtered_sections': List of valid sections,
            'removed_sections': List of removed sections with reasons,
            'stats': Summary statistics,
            'cost': Total cost of validation
        }
    """

    # Use imported comprehensive locked headers list (~850+ headers)
    # Covers: Academic/industry/tech/business/creative CVs + all international formats
    LOCKED_HEADERS = LOCKED_CV_HEADERS

    total_cost = 0.0
    removed_sections = []
    valid_sections = []
    locked_sections = []

    # Phase 0: Lock known good headers (bypass validation)
    ambiguous_sections = []

    for section in sections:
        header_text = section.get('text', '').strip()
        header_lower = header_text.lower().rstrip(':').strip()

        # Check if this is a locked header (known CV section)
        if header_lower in LOCKED_HEADERS:
            # Annotate locked header with metadata
            annotated_section = {
                **section,
                'header_likeness_score': 1.0,  # Locked headers get perfect score
                'validation_reason': 'Known CV section header (locked)',
                'validated_by': 'locked_list'
            }
            locked_sections.append(annotated_section)
            valid_sections.append(annotated_section)
            continue

        # Phase 1: Quick regex filter for metadata
        if is_metadata_pattern(header_text):
            # Definitely metadata - remove it
            removed_sections.append({
                **section,
                'removal_reason': 'Metadata pattern (regex)',
                'filter_method': 'regex'
            })
        else:
            # Not obvious metadata - might be valid header
            ambiguous_sections.append(section)

    print(f"\nPhase 0 (Lock): Protected {len(locked_sections)} known CV section headers")
    if locked_sections and len(locked_sections) <= 10:
        for section in locked_sections:
            print(f"  🔒 LOCKED: '{section.get('text', '').strip()}'")

    print(f"\nPhase 1 (Regex): Filtered {len(removed_sections)} obvious metadata headers")
    if removed_sections:
        for section in removed_sections:
            print(f"  ✗ REMOVED: '{section.get('text', '').strip()}' - {section.get('removal_reason', 'Unknown')}")

    print(f"\nPhase 1 (Regex): {len(ambiguous_sections)} ambiguous headers remain for LLM validation")

    # Phase 2: LLM validation for ambiguous cases
    if use_llm and ambiguous_sections:
        print(f"\nPhase 2 (LLM): Validating {len(ambiguous_sections)} ambiguous headers...")

        # Process in batches
        for i in range(0, len(ambiguous_sections), batch_size):
            batch = ambiguous_sections[i:i+batch_size]
            batch_headers = [s.get('text', '').strip() for s in batch]

            print(f"  Batch {i//batch_size + 1}: Validating {len(batch_headers)} headers...")

            result = validate_headers_batch(batch_headers)
            total_cost += result.get('cost', 0.0)

            # Process results
            for validation in result['validations']:
                idx = validation['header_index']
                section = batch[idx]
                header_text = section.get('text', '').strip()
                score = validation.get('header_likeness_score', 0.0)
                reason = validation.get('reason', 'No reason provided')

                if validation['is_valid_header']:
                    # Keep header but annotate with validation metadata
                    annotated_section = {
                        **section,
                        'header_likeness_score': score,
                        'validation_reason': reason,
                        'validated_by': 'llm'
                    }
                    valid_sections.append(annotated_section)
                    print(f"    ✓ KEPT: '{header_text}' (score: {score:.2f}) - {reason}")
                else:
                    removed_sections.append({
                        **section,
                        'header_likeness_score': score,
                        'removal_reason': reason,
                        'filter_method': 'llm'
                    })
                    print(f"    ✗ REMOVED: '{header_text}' (score: {score:.2f}) - {reason}")

        print(f"Phase 2 (LLM): Validated {len(ambiguous_sections)} headers (${total_cost:.4f})")
    else:
        # No LLM - treat all ambiguous as valid
        valid_sections.extend(ambiguous_sections)

    # CRITICAL: Sort valid_sections by document order (idx) to preserve original CV order
    # Without this, locked headers appear before ambiguous headers regardless of document position
    valid_sections.sort(key=lambda s: s.get('idx', 0))

    stats = {
        'total_headers_input': len(sections),
        'locked_headers': len(locked_sections),
        'removed_by_regex': sum(1 for r in removed_sections if r['filter_method'] == 'regex'),
        'removed_by_llm': sum(1 for r in removed_sections if r['filter_method'] == 'llm'),
        'total_removed': len(removed_sections),
        'total_kept': len(valid_sections),
        'removal_rate': len(removed_sections) / len(sections) if sections else 0.0
    }

    return {
        'filtered_sections': valid_sections,
        'removed_sections': removed_sections,
        'stats': stats,
        'cost': total_cost
    }
