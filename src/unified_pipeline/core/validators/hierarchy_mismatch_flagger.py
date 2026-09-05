"""
Hierarchy-Taxonomy Mismatch Flagger

QA validator that flags entries where the assigned taxonomy code
appears inconsistent with the CV section hierarchy.

This is NOT an auto-corrector - it flags for human review when
content-based classification diverges from section-based expectations.

Examples of flaggable mismatches:
- Entry under "Publications" classified as M2 (grant)
- Entry under "Teaching" classified as S1 (publication)
- Entry under "Grants" classified as H (honor)

These may be CORRECT (content overrides hierarchy), but warrant review.
"""

import re
from typing import Dict, List, Tuple, Optional


# Map hierarchy keywords to expected taxonomy code families
HIERARCHY_TO_EXPECTED_CODES = {
    # Publication sections
    "publication": ["S", "N4"],  # S1-S9, or mentee outputs N4
    "publications": ["S", "N4"],
    "articles": ["S1", "S2", "S6"],
    "peer-reviewed": ["S1"],
    "journal": ["S1", "S2"],
    "book": ["S3", "S4"],
    "books": ["S3", "S4"],
    "chapter": ["S4"],
    "chapters": ["S4"],
    "abstract": ["S8"],
    "abstracts": ["S8"],
    "poster": ["S8"],
    "posters": ["S8"],
    "proceeding": ["S8"],
    "proceedings": ["S8"],
    "conference": ["S8", "R"],
    "presentation": ["S8", "R"],
    "presentations": ["S8", "R"],
    "dissertation": ["S", "B1"],  # Could be pub or degree-related

    # Patent sections - M2D for all patent-related (issued AND pending)
    "patent": ["M2D"],
    "patents": ["M2D"],
    "invention": ["M2D"],
    "inventions": ["M2D"],
    "applications for patent": ["M2D"],  # Pending patents are still M2D, NOT M2C
    "patent application": ["M2D"],
    "intellectual property": ["M2D"],

    # Grant sections
    "grant": ["M2", "M2A", "M2B", "M2C"],
    "grants": ["M2", "M2A", "M2B", "M2C"],
    "funding": ["M2", "M2A", "M2B", "M2C"],
    "research support": ["M2", "M2A", "M2B", "M2C"],
    "active": ["M2A"],
    "current funding": ["M2A"],
    "current grant": ["M2A"],
    "current grants": ["M2A"],
    "completed": ["M2B"],
    "past funding": ["M2B"],
    "pending": ["M2C"],  # Note: This is for grants, not patents
    "submitted": ["M2C"],

    # Teaching sections
    "teaching": ["K"],  # K1-K5
    "courses": ["K1"],
    "course": ["K1"],
    "lectures": ["K1", "K4", "R"],
    "curriculum": ["K3"],
    "cme": ["K4"],
    "continuing education": ["K4"],
    "mentoring": ["N", "K2"],  # N1-N4 or K2
    "mentees": ["N3", "N3A", "N3B"],
    "students": ["N3", "N3A", "N3B"],
    "trainees": ["N3", "N3A", "N3B", "K2"],
    "supervision": ["N3", "K2"],

    # Position/appointment sections
    "appointment": ["D", "C"],  # D1-D3 or C (training)
    "appointments": ["D", "C"],
    "position": ["D"],
    "positions": ["D"],
    "employment": ["D"],
    "professional experience": ["D"],
    "academic": ["D1"],
    "clinical appointment": ["D2"],
    "hospital": ["D2"],

    # Service sections
    "service": ["P", "Q", "O"],  # P, Q1-Q4, O
    "committee": ["P", "Q2", "O"],
    "committees": ["P", "Q2", "O"],
    "editorial": ["Q4", "Q4A", "Q4B", "Q4C", "Q4D"],
    "reviewer": ["Q3", "Q4D"],
    "review panel": ["Q3"],
    "study section": ["Q3"],

    # Honors/awards sections
    # Note: CV authors often put invited talks (R) under Honors - this is acceptable
    "honor": ["H", "R"],  # R is acceptable because CV authors often misplace invited talks here
    "honors": ["H", "R"],
    "award": ["H"],
    "awards": ["H"],
    "fellowship": ["H", "I", "C"],  # Context-dependent
    "recognition": ["H"],

    # Membership sections
    "membership": ["I"],
    "memberships": ["I"],
    "affiliation": ["G", "I"],
    "affiliations": ["G", "I"],
    "society": ["I"],
    "societies": ["I"],

    # Leadership sections
    "leadership": ["O", "Q1"],
    "administrative": ["O", "P"],

    # Invited talks
    "invited": ["R"],
    "invited talk": ["R"],
    "invited talks": ["R"],
    "keynote": ["R"],
    "grand rounds": ["R", "K4"],
}

# Codes that are "universal" and can appear anywhere without flagging
UNIVERSAL_CODES = {"T", "A"}  # T (misc) and A (personal info)


def normalize_hierarchy_text(text: str) -> str:
    """Normalize hierarchy text for keyword matching."""
    return text.lower().strip()


def get_expected_codes_from_hierarchy(hierarchy: list[str]) -> list[str]:
    """
    Determine expected taxonomy codes based on hierarchy path.

    Returns list of expected code prefixes/families.
    """
    expected = set()

    for level in hierarchy:
        normalized = normalize_hierarchy_text(level)

        # Check each keyword pattern
        for keyword, codes in HIERARCHY_TO_EXPECTED_CODES.items():
            if keyword in normalized:
                expected.update(codes)

    return list(expected)


def code_matches_expected(
    taxonomy_code: str,
    expected_codes: list[str]
) -> bool:
    """
    Check if taxonomy code matches any of the expected codes.

    Handles prefix matching (e.g., "S" matches "S1", "S2", etc.)
    """
    if not expected_codes:
        return True  # No expectations = no mismatch

    if taxonomy_code in UNIVERSAL_CODES:
        return True  # Universal codes always "match"

    for expected in expected_codes:
        # Exact match
        if taxonomy_code == expected:
            return True
        # Prefix match (e.g., "S" matches "S1")
        if len(expected) == 1 and taxonomy_code.startswith(expected):
            return True
        # taxonomy_code is prefix of expected (e.g., "M2" matches "M2A")
        if taxonomy_code.startswith(expected.rstrip('ABC')):
            return True
        # Handle M2/M2A/M2B/M2C family
        if expected in ("M2", "M2A", "M2B", "M2C") and taxonomy_code.startswith("M2"):
            return True
        # Handle Q4 family
        if expected.startswith("Q4") and taxonomy_code.startswith("Q4"):
            return True
        # Handle N3 family
        if expected.startswith("N3") and taxonomy_code.startswith("N3"):
            return True

    return False


def flag_hierarchy_mismatches(entries: list[dict]) -> tuple[list[dict], dict]:
    """
    Flag entries where taxonomy code doesn't match hierarchy expectations.

    Args:
        entries: List of classified entry dicts with 'taxonomy_code' and 'hierarchy'

    Returns:
        (entries_with_flags, stats)
    """
    stats = {
        "total_entries": len(entries),
        "entries_flagged": 0,
        "flagged_details": []
    }

    for entry in entries:
        taxonomy_code = entry.get("taxonomy_code", "")
        hierarchy = entry.get("hierarchy", [])

        if not hierarchy or not taxonomy_code:
            continue

        # Skip if already marked as a duplicate or fragment
        if entry.get("is_duplicate") or entry.get("is_fragment"):
            continue

        # Get expected codes based on hierarchy
        expected_codes = get_expected_codes_from_hierarchy(hierarchy)

        if not expected_codes:
            continue  # No expectations for this hierarchy

        # Check if actual code matches expectations
        if not code_matches_expected(taxonomy_code, expected_codes):
            # This is a mismatch - flag for QA review
            entry["hierarchy_mismatch_flag"] = True
            entry["hierarchy_mismatch_detail"] = {
                "hierarchy": hierarchy,
                "assigned_code": taxonomy_code,
                "expected_codes": expected_codes,
                "reason": f"Code '{taxonomy_code}' may not match section '{' > '.join(hierarchy)}'"
            }

            stats["entries_flagged"] += 1
            stats["flagged_details"].append({
                "element_idx": entry.get("element_idx_start"),
                "text_preview": entry.get("text", "")[:100],
                "hierarchy": hierarchy,
                "assigned_code": taxonomy_code,
                "expected_codes": expected_codes[:5]  # Limit for readability
            })

    return entries, stats


def get_mismatch_summary(entries: list[dict]) -> dict:
    """
    Generate a summary of hierarchy mismatches for reporting.

    Returns summary dict with counts by code and hierarchy.
    """
    mismatches_by_code = {}
    mismatches_by_hierarchy = {}

    for entry in entries:
        if entry.get("hierarchy_mismatch_flag"):
            code = entry.get("taxonomy_code", "?")
            hierarchy_key = " > ".join(entry.get("hierarchy", ["?"]))

            mismatches_by_code[code] = mismatches_by_code.get(code, 0) + 1
            mismatches_by_hierarchy[hierarchy_key] = mismatches_by_hierarchy.get(hierarchy_key, 0) + 1

    return {
        "total_mismatches": sum(mismatches_by_code.values()),
        "by_code": dict(sorted(mismatches_by_code.items(), key=lambda x: -x[1])),
        "by_hierarchy": dict(sorted(mismatches_by_hierarchy.items(), key=lambda x: -x[1])[:10])  # Top 10
    }
