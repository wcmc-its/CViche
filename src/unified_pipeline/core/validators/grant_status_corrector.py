"""
Grant Status Corrector

Post-classification validator that corrects grant status codes (M2A/M2B/M2C)
based on date analysis, overriding section header-based misclassifications.

Problem: Grants in "Pending" sections may have ended (should be M2B, not M2C).
         Grants in "Non-funded applications" may have been funded (amounts present → M2B).

Solution: Parse date ranges from grant text and compare to current date.
"""

import re
from datetime import datetime


# Current year for comparison
CURRENT_YEAR = datetime.now().year

# A hierarchy heading that says the grant is an application, not an award
# (#981). Whole words: "Grants Pending Review" matches, "Impending" does not.
# Under such a heading a date range is the proposed project period, so it says
# nothing about whether the grant ended or is running.
_PENDING_HEADING_RE = re.compile(
    r'\b(?:pending|submitted|awaiting|(?:under|in)\s+review|(?:not|non)[\s-]?funded'
    r'|unfunded|declined|withdrawn)\b'
)
# The same words as an explicit status line in the entry's own text ("Status
# of Support: Pending"), for a grant whose heading is silent because the
# extractor filed it under an unrelated one (#981).
_PENDING_STATUS_TEXT_RE = re.compile(
    r'\bstatus(?:\s+of\s+support)?\s*:\s*(?:pending|submitted|awaiting'
    r'|(?:under|in)\s+review|(?:not|non)[\s-]?funded|unfunded|declined|withdrawn)\b',
    re.IGNORECASE,
)


def extract_year_range(text: str) -> tuple[int, int] | None:
    """
    Extract start and end years from grant text.

    Patterns handled:
    - "2019-2021" or "2019–2021" or "2019—2021"
    - "2019-present" or "2019-Present"
    - "2019-2021" (4-digit years)
    - "2019-21" (2-digit end year)
    - "01/2019-12/2021" (MM/YYYY format)

    Returns:
        (start_year, end_year) or None if no range found.
        For "present", end_year = CURRENT_YEAR + 1 (still active).
    """
    text_lower = text.lower()

    # Pattern 1: a range of full years, each end optionally a full date or a
    # MM/YYYY month: "2019-2021", "01/2019-12/2021", "03/01/2024-\n12/31/2028".
    # Tried before the two-digit-year pattern below, which would read the end
    # date's month in "2024-\n12/31/2028" as the year 2012 (#981).
    match = re.search(
        r'\b(19\d{2}|20\d{2})\s*[-–—]\s*'
        r'(?:\d{1,2}/){0,2}(19\d{2}|20\d{2})\b', text)
    if match:
        return int(match.group(1)), int(match.group(2))

    # Pattern 2: YYYY-YY (abbreviated end year). Not when the two digits are the
    # month or day of a date: "2024-12/31/2028" is not "2024-2012".
    match = re.search(r'\b(20\d{2})\s*[-–—]\s*(\d{2})\b(?!/)', text)
    if match:
        start = int(match.group(1))
        end_suffix = int(match.group(2))
        end = 2000 + end_suffix if end_suffix < 50 else 1900 + end_suffix
        return start, end

    # Pattern 3: YYYY-present
    match = re.search(r'\b(19\d{2}|20\d{2})\s*[-–—]\s*present\b', text_lower)
    if match:
        return int(match.group(1)), CURRENT_YEAR + 1  # Still active

    # Pattern 4: Just a single year (assume single year grant)
    # Only if it looks like a grant context
    if re.search(r'\$[\d,]+', text):  # Has dollar amount
        match = re.search(r'\b(20\d{2})\b', text)
        if match:
            year = int(match.group(1))
            return year, year

    return None


def has_funding_amount(text: str) -> bool:
    """Check if text contains a funding amount (suggests it was funded)."""
    return bool(re.search(r'\$\s*[\d,]+(?:,\d{3})*', text))


def is_grant_code(code: str) -> bool:
    """Check if taxonomy code is a grant code."""
    return code in ('M2', 'M2A', 'M2B', 'M2C')


def correct_grant_status(entry: dict) -> dict:
    """
    Correct grant status code based on date analysis.

    Rules:
    1. If end year < current year → M2B (completed)
    2. If end year >= current year and has "present" or ongoing → M2A (active)

    A grant under a pending / not-funded heading, or whose text carries a
    "Status: Pending"-style line, is left alone whatever its code: its dates are
    a proposed project period, and a dollar amount there is a requested budget,
    not an award (#981).

    Args:
        entry: Classified entry dict with 'taxonomy_code', 'text', 'hierarchy'

    Returns:
        Entry with potentially corrected 'taxonomy_code' and added 'status_correction' note
    """
    code = entry.get('taxonomy_code', '')
    text = entry.get('text', '')
    hierarchy = entry.get('hierarchy', [])
    hierarchy_str = ' > '.join(hierarchy).lower() if hierarchy else ''

    # Only process grant codes
    if not is_grant_code(code):
        return entry

    if _PENDING_HEADING_RE.search(hierarchy_str) or _PENDING_STATUS_TEXT_RE.search(text):
        return entry

    # Extract date range
    date_range = extract_year_range(text)
    has_amount = has_funding_amount(text)

    original_code = code
    correction_reason = None

    if date_range:
        start_year, end_year = date_range

        # Rule 1: End year in the past → M2B (completed)
        if end_year < CURRENT_YEAR:
            if code in ('M2A', 'M2C', 'M2'):
                code = 'M2B'
                correction_reason = f"Date range {start_year}-{end_year} ended; corrected to M2B (completed)"

        # Rule 2: End year is current or future → M2A (active)
        elif end_year >= CURRENT_YEAR:
            # Grant is still active (end year is current year or later)
            if code in ('M2C', 'M2B', 'M2'):
                # M2C (pending) with active dates and funding → M2A
                # M2B (completed) with active dates → M2A (incorrectly marked as completed)
                # M2 (generic) with active dates → M2A
                if has_amount or code == 'M2B':  # M2B with active dates is always wrong
                    code = 'M2A'
                    if original_code == 'M2B':
                        correction_reason = f"Date range {start_year}-{end_year} is current/future; corrected M2B to M2A (active, not completed)"
                    else:
                        correction_reason = f"Date range {start_year}-{end_year} is ongoing with funding; corrected to M2A (active)"

    # Apply correction if changed
    if code != original_code:
        entry = entry.copy()
        entry['taxonomy_code'] = code
        entry['original_taxonomy_code'] = original_code
        entry['status_correction'] = {
            'from': original_code,
            'to': code,
            'reason': correction_reason
        }

    return entry


def apply_grant_status_corrections(entries: list[dict]) -> tuple[list[dict], dict]:
    """
    Apply grant status corrections to a list of classified entries.

    Args:
        entries: List of classified entry dicts

    Returns:
        (corrected_entries, stats)
    """
    corrected = []
    stats = {
        'total_grants': 0,
        'corrections_applied': 0,
        'correction_details': []
    }

    for entry in entries:
        code = entry.get('taxonomy_code', '')

        if is_grant_code(code):
            stats['total_grants'] += 1

        corrected_entry = correct_grant_status(entry)

        if 'status_correction' in corrected_entry:
            stats['corrections_applied'] += 1
            stats['correction_details'].append({
                'element_idx': corrected_entry.get('element_idx_start'),
                'text_preview': corrected_entry.get('text', '')[:100],
                'correction': corrected_entry['status_correction']
            })

        corrected.append(corrected_entry)

    return corrected, stats


# Convenience function for integration with Stage 3b
def apply_grant_corrections(entries: list[dict]) -> list[dict]:
    """Apply grant status corrections, returning only the corrected list."""
    corrected, _ = apply_grant_status_corrections(entries)
    return corrected
