"""
Leadership Level Corrector

Post-classification validator that corrects O (Institutional Leadership)
to P (Institutional Administrative Activities) for non-executive roles.

Problem: LLM often inflates administrative roles to O when they should be P.
         O is for executive-level leadership with budget/personnel authority.
         P is for committee service, program administration, and most internal roles.

Rule: O should only be used for:
- Department Chair, Division Chief, Section Head
- Center/Institute Director (with budget authority)
- Vice Chair, Associate Dean, Dean
- Executive-level institutional governance

Everything else is P (or Q2 if external).
"""

import re


# Patterns that indicate TRUE executive leadership (should stay O)
EXECUTIVE_LEADERSHIP_PATTERNS = [
    r'\bDepartment\s+Chair\b',
    r'\bDivision\s+Chief\b',
    r'\bSection\s+Chief\b',
    r'\bSection\s+Head\b',
    r'\bUnit\s+Chief\b',
    r'\bVice\s+Chair\b',
    r'\bAssociate\s+Chair\b',
    r'\bAssistant\s+Chair\b',
    r'\bChief,\s+\w+\s+Division\b',
    r'\bChief,\s+\w+\s+Section\b',
    r'\bCenter\s+Director\b',
    r'\bInstitute\s+Director\b',
    r'\bAssociate\s+Dean\b',
    r'\bAssistant\s+Dean\b',
    r'\bVice\s+Dean\b',
    r'\bDean\s+of\b',
    r'\bChief\s+Medical\s+Officer\b',
    r'\bChief\s+Scientific\s+Officer\b',
    r'\bChief\s+Executive\b',
    r'\bExecutive\s+Director\b',  # At institution level
    r'\bMedical\s+Director\b',  # Clinical leadership
    r'\bClinical\s+Director\b',
]

# Patterns that should be P (administrative/committee service), not O
ADMINISTRATIVE_PATTERNS = [
    r'\bDirector\s+of\s+\w+\s+Track\b',
    r'\bTrack\s+Director\b',
    r'\bProgram\s+Director\b(?!.*T32)',  # Program Director NOT on a T32
    r'\bSecretary\b',
    r'\bTreasurer\b',
    r'\bSecretary/Treasurer\b',
    r'\bChair,\s+\w*\s*Committee\b',
    r'\bCommittee\s+Chair\b',
    r'\bMember,\s+\w+\s+Committee\b',
    r'\bCouncil\s+Member\b',
    r'\bExecutive\s+Council\b(?!.*Chair)',  # Council membership, not chair
    r'\bAdvisory\s+Board\b(?!.*Chair)',
    r'\bTask\s+Force\b',
    r'\bWorking\s+Group\b',
    r'\bCoordinator\b',
    r'\bCo-Director,\s+(?!Center|Institute|Division)',  # Co-Director of programs, not centers
    r'\bDirector,\s+\w+\s+Education\b',  # Education programs
    r'\bDirector,\s+Clinical\s+Research\s+Education\b',
    r'\bDEI\b',
    r'\bDiversity\b.*\b(?:Committee|Council|Initiative)\b',
    r'\bEquity\b.*\b(?:Committee|Council)\b',
    r'\bInclusion\b.*\b(?:Committee|Council)\b',
]

# Compile patterns
EXECUTIVE_PATTERNS_COMPILED = [re.compile(p, re.IGNORECASE) for p in EXECUTIVE_LEADERSHIP_PATTERNS]
ADMIN_PATTERNS_COMPILED = [re.compile(p, re.IGNORECASE) for p in ADMINISTRATIVE_PATTERNS]


def is_executive_leadership(text: str) -> tuple[bool, str]:
    """Check if text contains executive leadership signals."""
    for pattern in EXECUTIVE_PATTERNS_COMPILED:
        match = pattern.search(text)
        if match:
            return True, match.group()
    return False, ""


def is_administrative_role(text: str) -> tuple[bool, str]:
    """Check if text contains administrative (non-executive) role signals."""
    for pattern in ADMIN_PATTERNS_COMPILED:
        match = pattern.search(text)
        if match:
            return True, match.group()
    return False, ""


def correct_leadership_level(entry: dict) -> dict:
    """
    Correct O to P if the role is administrative, not executive leadership.

    Args:
        entry: Classified entry dict with 'taxonomy_code', 'text'

    Returns:
        Entry with potentially corrected taxonomy_code
    """
    code = entry.get('taxonomy_code', '')
    text = entry.get('text', '')

    # Only review O entries
    if code != 'O':
        return entry

    # Check if it's truly executive leadership (should stay O)
    is_exec, exec_match = is_executive_leadership(text)
    if is_exec:
        return entry  # Keep as O

    # Check if it's actually administrative (should be P)
    is_admin, admin_match = is_administrative_role(text)
    if is_admin:
        entry = entry.copy()
        entry['taxonomy_code'] = 'P'
        entry['original_taxonomy_code'] = code
        entry['leadership_level_correction'] = {
            'from': code,
            'to': 'P',
            'reason': f"Administrative role detected: '{admin_match}' is committee/program service (P), not executive leadership (O)"
        }

    return entry


def apply_leadership_level_corrections(entries: list[dict]) -> tuple[list[dict], dict]:
    """
    Apply leadership level corrections to all entries.

    Args:
        entries: List of classified entry dicts

    Returns:
        (corrected_entries, stats)
    """
    corrected = []
    stats = {
        'o_entries_reviewed': 0,
        'corrections_applied': 0,
        'correction_details': []
    }

    for entry in entries:
        code = entry.get('taxonomy_code', '')

        if code == 'O':
            stats['o_entries_reviewed'] += 1

        corrected_entry = correct_leadership_level(entry)

        if 'leadership_level_correction' in corrected_entry:
            stats['corrections_applied'] += 1
            stats['correction_details'].append({
                'element_idx': corrected_entry.get('element_idx_start'),
                'text_preview': corrected_entry.get('text', '')[:100],
                'correction': corrected_entry['leadership_level_correction']
            })

        corrected.append(corrected_entry)

    return corrected, stats
