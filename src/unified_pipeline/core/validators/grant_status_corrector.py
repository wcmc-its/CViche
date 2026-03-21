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
from typing import Dict, List, Optional, Tuple


# Current year for comparison
CURRENT_YEAR = datetime.now().year


def extract_year_range(text: str) -> Optional[Tuple[int, int]]:
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

    # Pattern 1: YYYY-YYYY or YYYY–YYYY (full years)
    match = re.search(r'\b(19\d{2}|20\d{2})\s*[-–—]\s*(19\d{2}|20\d{2})\b', text)
    if match:
        return int(match.group(1)), int(match.group(2))

    # Pattern 2: YYYY-YY (abbreviated end year)
    match = re.search(r'\b(20\d{2})\s*[-–—]\s*(\d{2})\b', text)
    if match:
        start = int(match.group(1))
        end_suffix = int(match.group(2))
        end = 2000 + end_suffix if end_suffix < 50 else 1900 + end_suffix
        return start, end

    # Pattern 3: YYYY-present
    match = re.search(r'\b(19\d{2}|20\d{2})\s*[-–—]\s*present\b', text_lower)
    if match:
        return int(match.group(1)), CURRENT_YEAR + 1  # Still active

    # Pattern 4: MM/YYYY-MM/YYYY
    match = re.search(r'\b\d{1,2}/(19\d{2}|20\d{2})\s*[-–—]\s*\d{1,2}/(19\d{2}|20\d{2})\b', text)
    if match:
        return int(match.group(1)), int(match.group(2))

    # Pattern 5: Just a single year (assume single year grant)
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


def correct_grant_status(entry: Dict) -> Dict:
    """
    Correct grant status code based on date analysis.

    Rules:
    1. If end year < current year → M2B (completed)
    2. If end year >= current year and has "present" or ongoing → M2A (active)
    3. If no dates but has dollar amount in "non-funded" section → likely M2B
    4. If classified as M2C but dates show it ended → M2B

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

    else:
        # No date range found - check for other signals

        # Rule 3: In "pending" or "submitted" section but has dollar amount → likely funded
        if code == 'M2C':
            pending_indicators = ['pending', 'submitted', 'under review', 'non-funded', 'unfunded']
            in_pending_section = any(ind in hierarchy_str for ind in pending_indicators)

            if in_pending_section and has_amount:
                # Has dollar amount in "pending" section - suspicious, may actually be funded
                code = 'M2B'
                correction_reason = "Has funding amount in 'pending' section; likely completed grant misclassified"

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


def apply_grant_status_corrections(entries: List[Dict]) -> Tuple[List[Dict], Dict]:
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
def apply_grant_corrections(entries: List[Dict]) -> List[Dict]:
    """Apply grant status corrections, returning only the corrected list."""
    corrected, _ = apply_grant_status_corrections(entries)
    return corrected
