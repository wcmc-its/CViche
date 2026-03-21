"""
Training/Compliance Corrector

Post-classification validator that corrects P (Institutional Administrative
Activities) to B2 (Other Educational Experiences) for required training
and compliance courses.

Problem: LLM classifies DEI trainings, Title IX training, and compliance
         courses as P (institutional service), but these are actually
         B2 (non-degree educational experiences the CV owner completed).

Rule: Required institutional trainings and compliance courses are B2,
      not P. P is for committee service and administrative activities
      the CV owner provides, not trainings they receive.
"""

import re
from typing import Dict, List, Tuple


# Patterns indicating B2 (training/compliance courses)
TRAINING_PATTERNS = [
    # Title IX and compliance
    r'\bTitle\s+IX\b',
    r'\bTitle\s+9\b',
    r'\bNon-Discrimination\s+Training\b',
    r'\bCompliance\s+Training\b',
    r'\bRequired\s+Training\b',
    r'\bAnnual\s+Training\b',
    r'\bMandatory\s+Training\b',

    # DEI trainings (received, not facilitated)
    r'\bDiversity\s+Training\b',
    r'\bInclusion\s+Training\b',
    r'\bEquity\s+Training\b',
    r'\bBias\s+Training\b',
    r'\bEveryday\s+Bias\b',
    r'\bUnconscious\s+Bias\b',
    r'\bImplicit\s+Bias\b',
    r'\bCultural\s+Competency\s+Training\b',
    r'\bAnti-Harassment\s+Training\b',
    r'\bSexual\s+Harassment\s+Training\b',
    r'\bWorkplace\s+Harassment\b',

    # Safety and research compliance
    r'\bHIPAA\s+Training\b',
    r'\bIRB\s+Training\b',
    r'\bCITI\s+Training\b',
    r'\bCITI\s+Certification\b',
    r'\bGood\s+Clinical\s+Practice\b',
    r'\bGCP\s+Training\b',
    r'\bHuman\s+Subjects\s+Training\b',
    r'\bResearch\s+Ethics\s+Training\b',
    r'\bLab\s+Safety\s+Training\b',
    r'\bBiosafety\s+Training\b',
    r'\bRadiation\s+Safety\b',
    r'\bFire\s+Safety\s+Training\b',
    r'\bEmergency\s+Preparedness\b',

    # Professional development courses (received)
    r'\bLeadership\s+Development\s+Program\b',
    r'\bManagement\s+Training\b',
    r'\bProfessional\s+Development\s+Course\b',
]

# Patterns indicating the CV owner FACILITATED training (should stay as K or P)
FACILITATOR_PATTERNS = [
    r'\bFacilitator\b',
    r'\bInstructor\b',
    r'\bTrainer\b',
    r'\bLed\s+training\b',
    r'\bConducted\s+training\b',
    r'\bDeveloped\s+training\b',
    r'\bCreated\s+training\b',
    r'\bOrganized\s+training\b',
]

# Compile patterns
TRAINING_PATTERNS_COMPILED = [re.compile(p, re.IGNORECASE) for p in TRAINING_PATTERNS]
FACILITATOR_PATTERNS_COMPILED = [re.compile(p, re.IGNORECASE) for p in FACILITATOR_PATTERNS]


def is_training_received(text: str) -> Tuple[bool, str]:
    """Check if text indicates training/compliance course received."""
    for pattern in TRAINING_PATTERNS_COMPILED:
        match = pattern.search(text)
        if match:
            return True, match.group()
    return False, ""


def is_training_facilitated(text: str) -> bool:
    """Check if text indicates the CV owner facilitated the training."""
    for pattern in FACILITATOR_PATTERNS_COMPILED:
        if pattern.search(text):
            return True
    return False


def correct_training_compliance(entry: Dict) -> Dict:
    """
    Correct P to B2 if the entry is a training/compliance course received.

    Args:
        entry: Classified entry dict with 'taxonomy_code', 'text'

    Returns:
        Entry with potentially corrected taxonomy_code
    """
    code = entry.get('taxonomy_code', '')
    text = entry.get('text', '')

    # Only review P entries
    if code != 'P':
        return entry

    # Check if the CV owner facilitated (should stay P or become K)
    if is_training_facilitated(text):
        return entry  # Keep as is

    # Check if it's a training course received
    is_training, training_match = is_training_received(text)
    if is_training:
        entry = entry.copy()
        entry['taxonomy_code'] = 'B2'
        entry['original_taxonomy_code'] = code
        entry['training_compliance_correction'] = {
            'from': code,
            'to': 'B2',
            'reason': f"Training/compliance course detected: '{training_match}' is educational experience (B2), not service (P)"
        }

    return entry


def apply_training_compliance_corrections(entries: List[Dict]) -> Tuple[List[Dict], Dict]:
    """
    Apply training/compliance corrections to all entries.

    Args:
        entries: List of classified entry dicts

    Returns:
        (corrected_entries, stats)
    """
    corrected = []
    stats = {
        'p_entries_reviewed': 0,
        'corrections_applied': 0,
        'correction_details': []
    }

    for entry in entries:
        code = entry.get('taxonomy_code', '')

        if code == 'P':
            stats['p_entries_reviewed'] += 1

        corrected_entry = correct_training_compliance(entry)

        if 'training_compliance_correction' in corrected_entry:
            stats['corrections_applied'] += 1
            stats['correction_details'].append({
                'element_idx': corrected_entry.get('element_idx_start'),
                'text_preview': corrected_entry.get('text', '')[:100],
                'correction': corrected_entry['training_compliance_correction']
            })

        corrected.append(corrected_entry)

    return corrected, stats
