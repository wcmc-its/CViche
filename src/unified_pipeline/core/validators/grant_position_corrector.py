"""
Grant Position Corrector

Post-classification validator that catches position/leadership roles
misclassified as M2A/M2B (Research Funding).

Problem: Some CVs list leadership roles under "Research Support" or similar
         sections, causing them to be classified as grants when they should
         be positions (D1/D2/D3) or leadership (O/L3).

Examples of misclassifications:
- "Executive Vice Chair, Administration" → Should be O, not M2A
- "Chief, Division of General Pediatrics" → Should be O or L3, not M2A
- "Medical Director, Inpatient Pediatrics" → Should be O or L3, not M2A
- "Director, Residency Program" → Should be O, not M2A

Detection logic:
- Entry classified as M2A/M2B but contains leadership/position keywords
- Entry lacks typical grant indicators (funding amount, grant number, dates in MM/YY format)
"""

import re
from typing import Dict, List, Tuple


# Patterns that indicate position/leadership (should NOT be M2A/M2B)
POSITION_LEADERSHIP_PATTERNS = [
    # Executive/Chair roles
    r'\bExecutive\s+Vice\s+Chair\b',
    r'\bVice\s+Chair\b',
    r'\bAssociate\s+Chair\b',
    r'\bChair,?\s+(?:of\s+)?(?:the\s+)?(?:Department|Division|Section)\b',

    # Chief roles
    r'\bChief,?\s+(?:of\s+)?(?:the\s+)?(?:Division|Section|Department)\b',
    r'\bDivision\s+Chief\b',
    r'\bSection\s+Chief\b',
    r'\bSite\s+Chief\b',
    r'\bUnit\s+Chief\b',

    # Director roles (NOT research director, which could be grant-related)
    r'\bMedical\s+Director\b',
    r'\bClinical\s+Director\b',
    r'\bAssociate\s+Director\b(?!.*(?:Grant|Award|R01|R21|K|T32))',
    r'\bDirector,?\s+(?:of\s+)?(?:Pediatric\s+)?(?:Graduate\s+)?Medical\s+Education\b',
    r'\bProgram\s+Director\b(?!.*(?:Grant|Award|R01|R21))',
    r'\bResidency\s+(?:Program\s+)?Director\b',
    r'\bFellowship\s+(?:Program\s+)?Director\b',

    # Other position indicators
    r'\bAttending\s+Physician\b',
    r'\bHospital\s+Appointments?\b',
    r'\bAcademic\s+Appointment\b',
]

# Patterns that indicate TRUE grants (should stay M2A/M2B)
GRANT_PATTERNS = [
    r'\b[RPUKFT]\d{2}\b',  # NIH grant mechanisms (R01, K23, T32, etc.)
    r'\bR0[1-9]\b',
    r'\bK\d{2}\b',
    r'\bT32\b',
    r'\bU\d{2}\b',
    r'\bP\d{2}\b',
    r'\bGrant\s*(?:#|No|Number)?\s*:?\s*\w+',
    r'\$[\d,]+(?:\.\d{2})?',  # Dollar amounts
    r'\bNIH\b',
    r'\bNSF\b',
    r'\bNCI\b',
    r'\bNHLBI\b',
    r'\bNINDS\b',
    r'\bNIMH\b',
    r'\bfunding\b',
    r'\bdirect\s+costs?\b',
    r'\bindirect\s+costs?\b',
    r'\bPI\s*:',  # PI: John Smith
    r'\b(?:Co-?)?(?:Principal\s+)?Investigator\b',
    r'\b(?:\d{1,2}/\d{2,4})\s*[-–]\s*(?:\d{1,2}/\d{2,4}|present)\b',  # Date ranges like 07/22-06/27
]

# Compile patterns
POSITION_PATTERNS_COMPILED = [re.compile(p, re.IGNORECASE) for p in POSITION_LEADERSHIP_PATTERNS]
GRANT_PATTERNS_COMPILED = [re.compile(p, re.IGNORECASE) for p in GRANT_PATTERNS]


def has_position_indicators(text: str) -> Tuple[bool, str]:
    """Check if text contains position/leadership signals."""
    for pattern in POSITION_PATTERNS_COMPILED:
        match = pattern.search(text)
        if match:
            return True, match.group()
    return False, ""


def has_grant_indicators(text: str) -> Tuple[bool, str]:
    """Check if text contains grant/funding signals."""
    for pattern in GRANT_PATTERNS_COMPILED:
        match = pattern.search(text)
        if match:
            return True, match.group()
    return False, ""


def determine_correct_code(text: str) -> str:
    """
    Determine the correct code for a position/leadership entry.

    Returns:
        'O' for executive/institutional leadership
        'L3' for clinical leadership
        'D2' for hospital positions
        'D1' for academic positions
    """
    text_lower = text.lower()

    # Executive leadership → O
    if any(kw in text_lower for kw in ['executive', 'vice chair', 'associate chair',
                                         'division chief', 'section chief', 'department chair',
                                         'chief,', 'chief of']):
        return 'O'

    # Clinical leadership → L3
    if any(kw in text_lower for kw in ['medical director', 'clinical director',
                                         'site chief', 'unit chief']):
        return 'L3'

    # Hospital positions → D2
    if any(kw in text_lower for kw in ['hospital', 'attending', 'inpatient']):
        return 'D2'

    # Program Director → O (unless it's clearly D1)
    if 'program director' in text_lower or 'director' in text_lower:
        return 'O'

    # Default to D2 for hospital-adjacent positions
    return 'D2'


def correct_grant_to_position(entry: Dict) -> Dict:
    """
    Correct M2A/M2B to position/leadership code if the entry is actually a position.

    Args:
        entry: Classified entry dict with 'taxonomy_code', 'text'

    Returns:
        Entry with potentially corrected taxonomy_code
    """
    code = entry.get('taxonomy_code', '')
    text = entry.get('text', '')

    # Only review M2A/M2B entries
    if code not in ('M2A', 'M2B'):
        return entry

    # Check for position/leadership indicators
    has_position, position_match = has_position_indicators(text)
    if not has_position:
        return entry  # No position signals, keep as grant

    # Check for grant indicators - if present, it's likely a true grant
    has_grant, grant_match = has_grant_indicators(text)
    if has_grant:
        # Has both - likely a grant where the person has a leadership role
        # Keep as M2A/M2B
        return entry

    # Has position indicators but no grant indicators → reclassify
    new_code = determine_correct_code(text)

    entry = entry.copy()
    entry['taxonomy_code'] = new_code
    entry['original_taxonomy_code'] = code
    entry['grant_position_correction'] = {
        'from': code,
        'to': new_code,
        'reason': f"Position/leadership role '{position_match}' misclassified as grant. No grant indicators found.",
        'position_match': position_match
    }

    return entry


def apply_grant_position_corrections(entries: List[Dict]) -> Tuple[List[Dict], Dict]:
    """
    Apply grant→position corrections to all entries.

    Args:
        entries: List of classified entry dicts

    Returns:
        (corrected_entries, stats)
    """
    corrected = []
    stats = {
        'grant_entries_reviewed': 0,
        'corrections_applied': 0,
        'correction_details': []
    }

    for entry in entries:
        code = entry.get('taxonomy_code', '')

        if code in ('M2A', 'M2B'):
            stats['grant_entries_reviewed'] += 1

        corrected_entry = correct_grant_to_position(entry)

        if 'grant_position_correction' in corrected_entry:
            stats['corrections_applied'] += 1
            stats['correction_details'].append({
                'element_idx': corrected_entry.get('element_idx_start'),
                'text_preview': corrected_entry.get('text', '')[:100],
                'correction': corrected_entry['grant_position_correction']
            })

        corrected.append(corrected_entry)

    return corrected, stats


if __name__ == '__main__':
    # Test cases
    test_entries = [
        {
            'taxonomy_code': 'M2A',
            'text': '2010-Present Executive Vice Chair, (Education and) Administration - New York Presbyterian Hospital'
        },
        {
            'taxonomy_code': 'M2A',
            'text': '2003-Present Chief Division, General Academic Pediatrics - New York Presbyterian Hospital'
        },
        {
            'taxonomy_code': 'M2A',
            'text': '2014-2017 Site Chief, Pediatrics Lower Manhattan/NYP - New York Presbyterian Hospital'
        },
        {
            'taxonomy_code': 'M2A',
            'text': 'R01 CA123456 (PI: Smith) 07/2022-06/2027 NIH/NCI $2,500,000 Cancer treatment'
        },
        {
            'taxonomy_code': 'M2A',
            'text': '1999-2010 Director, Pediatric Graduate Medical Education - New York Presbyterian Hospital'
        },
    ]

    corrected, stats = apply_grant_position_corrections(test_entries)

    print(f"\nReviewed {stats['grant_entries_reviewed']} M2A/M2B entries")
    print(f"Applied {stats['corrections_applied']} corrections\n")

    for i, entry in enumerate(corrected):
        if 'grant_position_correction' in entry:
            print(f"Entry {i}: {entry['text'][:80]}...")
            print(f"  Corrected: {entry['grant_position_correction']['from']} → {entry['grant_position_correction']['to']}")
            print(f"  Reason: {entry['grant_position_correction']['reason']}\n")
