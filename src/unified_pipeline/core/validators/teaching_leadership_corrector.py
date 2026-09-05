"""
Teaching Leadership Corrector

Post-classification validator that corrects teaching codes when
administrative/leadership roles are present.

Problem: LLM often classifies "Course Director", "Program Director",
         "Co-Director" teaching roles as K1 (didactic) instead of K3
         (educational program leadership).

Rule: If the teaching entry contains leadership signals (Director,
      Coordinator, Chair), it should be K3, not K1.
"""

import re
from typing import Dict, List, Tuple


# Leadership signals that indicate K3 (program leadership) not K1 (didactic)
LEADERSHIP_SIGNALS = [
    r'\bCourse\s+Director\b',
    r'\bProgram\s+Director\b',
    r'\bCo-Director\b',
    r'\bAssociate\s+Director\b',
    r'\bAssistant\s+Director\b',
    r'\bTrack\s+Director\b',
    r'\bCurriculum\s+Director\b',
    r'\bClerkship\s+Director\b',
    r'\bFellowship\s+Director\b',
    r'\bResidency\s+Director\b',
    r'\bCourse\s+Coordinator\b',
    r'\bProgram\s+Coordinator\b',
    r'\bCurriculum\s+Coordinator\b',
    r'\bEducation\s+Director\b',
    r'\bTraining\s+Director\b',
    r'\bDirector\s+of\s+Education\b',
    r'\bDirector\s+of\s+Training\b',
    r'\bDirector,\s+\w+\s+Program\b',
    r'\bChair,\s+\w+\s+Committee\b',  # Only in teaching context
]

# Compile patterns for efficiency
LEADERSHIP_PATTERNS = [re.compile(p, re.IGNORECASE) for p in LEADERSHIP_SIGNALS]


def has_teaching_leadership_signal(text: str) -> tuple[bool, str]:
    """
    Check if text contains teaching leadership signals.

    Returns:
        (has_signal, matched_pattern)
    """
    for pattern in LEADERSHIP_PATTERNS:
        match = pattern.search(text)
        if match:
            return True, match.group()
    return False, ""


def is_teaching_code(code: str) -> bool:
    """Check if taxonomy code is a teaching code."""
    return code.startswith('K')


def correct_teaching_leadership(entry: dict) -> dict:
    """
    Correct teaching code from K1 to K3 if leadership signals present.

    Args:
        entry: Classified entry dict with 'taxonomy_code', 'text'

    Returns:
        Entry with potentially corrected taxonomy_code
    """
    code = entry.get('taxonomy_code', '')
    text = entry.get('text', '')

    # Only correct K1 entries
    if code != 'K1':
        return entry

    # Check for leadership signals
    has_leadership, matched = has_teaching_leadership_signal(text)

    if has_leadership:
        entry = entry.copy()
        entry['taxonomy_code'] = 'K3'
        entry['original_taxonomy_code'] = code
        entry['teaching_leadership_correction'] = {
            'from': code,
            'to': 'K3',
            'reason': f"Leadership role detected: '{matched}' indicates program leadership (K3), not didactic teaching (K1)"
        }

    return entry


def apply_teaching_leadership_corrections(entries: list[dict]) -> tuple[list[dict], dict]:
    """
    Apply teaching leadership corrections to all entries.

    Args:
        entries: List of classified entry dicts

    Returns:
        (corrected_entries, stats)
    """
    corrected = []
    stats = {
        'k1_entries_reviewed': 0,
        'corrections_applied': 0,
        'correction_details': []
    }

    for entry in entries:
        code = entry.get('taxonomy_code', '')

        if code == 'K1':
            stats['k1_entries_reviewed'] += 1

        corrected_entry = correct_teaching_leadership(entry)

        if 'teaching_leadership_correction' in corrected_entry:
            stats['corrections_applied'] += 1
            stats['correction_details'].append({
                'element_idx': corrected_entry.get('element_idx_start'),
                'text_preview': corrected_entry.get('text', '')[:100],
                'correction': corrected_entry['teaching_leadership_correction']
            })

        corrected.append(corrected_entry)

    return corrected, stats
