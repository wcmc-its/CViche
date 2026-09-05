"""
Invited Talk Corrector

Post-classification validator that corrects S8 (Abstracts & Conference
Proceedings) to R (Invitations to Speak/Present) for invited talks
at conferences.

Problem: LLM sometimes classifies invited conference talks as S8
         (abstracts/proceedings) when they should be R (invited talks)
         because they occur at conferences.

Rule: If a talk is explicitly "invited" at a conference, or is a
      keynote/plenary, or is at a federal/interagency event, it's R.
      Regular contributed talks and poster presentations are S8.
"""

import re
from typing import Dict, List, Tuple


# Patterns indicating R (invited talk) even at a conference
INVITED_TALK_PATTERNS = [
    # Explicit invited signals
    r'\bInvited\s+(?:Talk|Lecture|Speaker|Presentation|Address)\b',
    r'\bInvited\s+Scientific\s+Talk\b',
    r'\bInvited\s+Oral\b',

    # Keynote and plenary (always R)
    r'\bKeynote\b',
    r'\bPlenary\b',
    r'\bFeatured\s+Speaker\b',
    r'\bFeatured\s+Lecture\b',
    r'\bDistinguished\s+Lecture\b',
    r'\bNamed\s+Lecture\b',

    # Federal/Interagency events (usually invited)
    r'\bFederal\s+Interagency\b',
    r'\bInteragency\s+Conference\b',
    r'\bNIH\s+(?:Workshop|Symposium|Meeting)\b',
    r'\bCDC\s+(?:Workshop|Symposium|Meeting)\b',
    r'\bFDA\s+(?:Workshop|Symposium|Meeting)\b',

    # State-of-the-art sessions
    r'\bState-of-the-Art\b',
    r'\bState\s+of\s+the\s+Art\b',

    # Panel presentations when explicitly invited
    r'\bInvited\s+Panel\b',
    r'\bInvited\s+Participant\b',
    r'\bInvited\s+Discussant\b',

    # Grand Rounds (always R)
    r'\bGrand\s+Rounds\b',
]

# Patterns indicating S8 (conference abstracts/proceedings) - should NOT be corrected
S8_PATTERNS = [
    r'\bPoster\s+Presentation\b',
    r'\bPoster\s+Session\b',
    r'\bAbstract\s+#?\d+\b',
    r'\bContributed\s+(?:Talk|Presentation|Paper)\b',
    r'\bOral\s+Presentation\b(?!\s+\(Invited\))',  # Unless marked invited
    r'\bSubmitted\s+Abstract\b',
    r'\bAccepted\s+Abstract\b',
    r'\bPlatform\s+Presentation\b',
]

# Conference hierarchy signals (help identify context)
CONFERENCE_HIERARCHY_SIGNALS = [
    'conference',
    'meeting',
    'symposium',
    'congress',
    'workshop',
    'presentation',
    'abstract',
]

# Compile patterns
INVITED_PATTERNS_COMPILED = [re.compile(p, re.IGNORECASE) for p in INVITED_TALK_PATTERNS]
S8_PATTERNS_COMPILED = [re.compile(p, re.IGNORECASE) for p in S8_PATTERNS]


def is_invited_talk(text: str) -> tuple[bool, str]:
    """Check if text indicates an invited talk."""
    for pattern in INVITED_PATTERNS_COMPILED:
        match = pattern.search(text)
        if match:
            return True, match.group()
    return False, ""


def is_contributed_abstract(text: str) -> bool:
    """Check if text indicates a contributed abstract/poster."""
    for pattern in S8_PATTERNS_COMPILED:
        if pattern.search(text):
            return True
    return False


def correct_invited_talk(entry: dict) -> dict:
    """
    Correct S8 to R if the entry is an invited talk at a conference.

    Args:
        entry: Classified entry dict with 'taxonomy_code', 'text'

    Returns:
        Entry with potentially corrected taxonomy_code
    """
    code = entry.get('taxonomy_code', '')
    text = entry.get('text', '')

    # Only review S8 entries
    if code != 'S8':
        return entry

    # Check if it's clearly a contributed abstract/poster (should stay S8)
    if is_contributed_abstract(text):
        return entry  # Keep as S8

    # Check if it's an invited talk
    is_invited, invited_match = is_invited_talk(text)
    if is_invited:
        entry = entry.copy()
        entry['taxonomy_code'] = 'R'
        entry['original_taxonomy_code'] = code
        entry['invited_talk_correction'] = {
            'from': code,
            'to': 'R',
            'reason': f"Invited talk detected: '{invited_match}' indicates invited presentation (R), not contributed abstract (S8)"
        }

    return entry


def apply_invited_talk_corrections(entries: list[dict]) -> tuple[list[dict], dict]:
    """
    Apply invited talk corrections to all entries.

    Args:
        entries: List of classified entry dicts

    Returns:
        (corrected_entries, stats)
    """
    corrected = []
    stats = {
        's8_entries_reviewed': 0,
        'corrections_applied': 0,
        'correction_details': []
    }

    for entry in entries:
        code = entry.get('taxonomy_code', '')

        if code == 'S8':
            stats['s8_entries_reviewed'] += 1

        corrected_entry = correct_invited_talk(entry)

        if 'invited_talk_correction' in corrected_entry:
            stats['corrections_applied'] += 1
            stats['correction_details'].append({
                'element_idx': corrected_entry.get('element_idx_start'),
                'text_preview': corrected_entry.get('text', '')[:100],
                'correction': corrected_entry['invited_talk_correction']
            })

        corrected.append(corrected_entry)

    return corrected, stats
