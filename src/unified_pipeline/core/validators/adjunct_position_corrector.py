"""
Adjunct/Instructor Position Corrector

Post-classification validator that corrects D1 (Academic Appointments)
to D3 (Other Professional Positions) for adjunct and instructor roles
at community colleges or non-faculty positions.

Problem: LLM often classifies all teaching positions as D1 (faculty),
         but adjunct instructors at community colleges and lab managers
         are D3 (other professional positions), not faculty appointments.

Rule: D1 = Faculty OR research staff positions at academic institutions
      D3 = Adjunct, instructor, lab manager, teaching-only roles at
           community colleges or positions without faculty rank.
"""

import re
from typing import Dict, List, Tuple


# Patterns indicating D3 (non-faculty positions)
D3_POSITION_PATTERNS = [
    # Adjunct positions (usually D3 unless explicitly faculty)
    r'\bAdjunct\s+Instructor\b',
    r'\bAdjunct\s+Lecturer\b',
    r'\bPart-time\s+Instructor\b',

    # Community college positions (almost always D3)
    r'\bCommunity\s+College\b',
    r'\bCounty\s+Community\s+College\b',

    # Lab and technical positions
    r'\bLab\s+Manager\b',
    r'\bLaboratory\s+Manager\b',
    r'\bLab\s+Instructor\b',
    r'\bLaboratory\s+Instructor\b',
    r'\bLab\s+Technician\b',
    r'\bResearch\s+Technician\b',

    # Teaching-only positions without faculty rank
    r'\bInstructor,\s+\w+\s+Lab\b',
    r'\bTeaching\s+Assistant\b(?!\s+Professor)',  # TA, not TAP
]

# Patterns indicating D1 (faculty positions) - should NOT be corrected
D1_POSITION_PATTERNS = [
    r'\bProfessor\b',
    r'\bAssistant\s+Professor\b',
    r'\bAssociate\s+Professor\b',
    r'\bFull\s+Professor\b',
    r'\bClinical\s+Professor\b',
    r'\bResearch\s+Professor\b',
    r'\bVisiting\s+Professor\b',
    r'\bAdjunct\s+Professor\b',  # Adjunct Professor IS faculty (D1)
    r'\bAdjunct\s+Faculty\b',
    r'\bFaculty\s+Appointment\b',
    r'\bFaculty\s+Member\b',
    r'\bLecturer\b(?!\s+at\s+.*Community)',  # Lecturer at university is D1
    r'\bSenior\s+Lecturer\b',
    r'\bInstructor\s+of\s+\w+,\s+\w+\s+University\b',  # Instructor at university
]

# Compile patterns
D3_PATTERNS_COMPILED = [re.compile(p, re.IGNORECASE) for p in D3_POSITION_PATTERNS]
D1_PATTERNS_COMPILED = [re.compile(p, re.IGNORECASE) for p in D1_POSITION_PATTERNS]


def is_d3_position(text: str) -> tuple[bool, str]:
    """Check if text indicates a D3 (non-faculty) position."""
    for pattern in D3_PATTERNS_COMPILED:
        match = pattern.search(text)
        if match:
            return True, match.group()
    return False, ""


def is_d1_position(text: str) -> tuple[bool, str]:
    """Check if text indicates a D1 (faculty) position."""
    for pattern in D1_PATTERNS_COMPILED:
        match = pattern.search(text)
        if match:
            return True, match.group()
    return False, ""


def correct_adjunct_position(entry: dict) -> dict:
    """
    Correct D1 to D3 if the position is adjunct/instructor at non-faculty level.

    Args:
        entry: Classified entry dict with 'taxonomy_code', 'text'

    Returns:
        Entry with potentially corrected taxonomy_code
    """
    code = entry.get('taxonomy_code', '')
    text = entry.get('text', '')

    # Only review D1 entries
    if code != 'D1':
        return entry

    # First check if it's clearly a faculty position (should stay D1)
    is_d1, d1_match = is_d1_position(text)
    if is_d1:
        return entry  # Keep as D1

    # Check if it's a D3 position (adjunct/instructor/lab manager)
    is_d3, d3_match = is_d3_position(text)
    if is_d3:
        entry = entry.copy()
        entry['taxonomy_code'] = 'D3'
        entry['original_taxonomy_code'] = code
        entry['adjunct_position_correction'] = {
            'from': code,
            'to': 'D3',
            'reason': f"Non-faculty position detected: '{d3_match}' is professional position (D3), not faculty appointment (D1)"
        }

    return entry


def apply_adjunct_position_corrections(entries: list[dict]) -> tuple[list[dict], dict]:
    """
    Apply adjunct position corrections to all entries.

    Args:
        entries: List of classified entry dicts

    Returns:
        (corrected_entries, stats)
    """
    corrected = []
    stats = {
        'd1_entries_reviewed': 0,
        'corrections_applied': 0,
        'correction_details': []
    }

    for entry in entries:
        code = entry.get('taxonomy_code', '')

        if code == 'D1':
            stats['d1_entries_reviewed'] += 1

        corrected_entry = correct_adjunct_position(entry)

        if 'adjunct_position_correction' in corrected_entry:
            stats['corrections_applied'] += 1
            stats['correction_details'].append({
                'element_idx': corrected_entry.get('element_idx_start'),
                'text_preview': corrected_entry.get('text', '')[:100],
                'correction': corrected_entry['adjunct_position_correction']
            })

        corrected.append(corrected_entry)

    return corrected, stats
