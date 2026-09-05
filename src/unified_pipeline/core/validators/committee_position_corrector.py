"""
Committee vs Position Auto-Corrector

Addresses the common misclassification of committee/board service as positions.

Problem:
- "Appointed to the R&D Committee" gets coded as D2 (hospital position)
- "Member, Research Committee" gets coded as D1 (faculty appointment)

These should be:
- P: Internal institutional committees (your own institution)
- Q2: External professional committees (other organizations)

This validator runs POST-classification and AUTO-CORRECTS misclassifications.

Key distinction:
- POSITION (D codes): You have a job title, get paid, have ongoing responsibilities
- COMMITTEE SERVICE (P/Q2): You serve on a committee, attend meetings, provide input

Signals for COMMITTEE (not position):
- "Committee", "Board", "Council", "Working Group", "Task Force", "Panel"
- "Member", "Appointed to", "Elected to", "Serves on"
- Absence of: salary/compensation, FTE, ongoing clinical/teaching duties
"""

import re
from typing import List, Dict, Tuple
from dataclasses import dataclass

try:
    from .base_validator import BaseValidator, ValidatorGuidance
except ImportError:
    from base_validator import BaseValidator, ValidatorGuidance


@dataclass
class CommitteeCorrectionResult:
    """Result of committee vs position correction."""
    corrected: bool
    original_code: str
    new_code: str
    confidence: float
    reason: str
    is_internal: bool  # True = P (internal), False = Q2 (external)


class CommitteePositionCorrector(BaseValidator):
    """
    Detect and auto-correct committee service misclassified as positions.
    """

    # Strong committee keywords (almost always service, not position)
    COMMITTEE_KEYWORDS = [
        r'\bcommittee\b',
        r'\bboard\b(?!\s+certified)',  # "board" but not "board certified"
        r'\bcouncil\b',
        r'\bworking\s*group\b',
        r'\btask\s*force\b',
        r'\bpanel\b',
        r'\badvisory\b',
        r'\bsteering\b',
        r'\bsearch\s*committee\b',
        r'\breview\s*(board|panel|committee)\b',
    ]

    # Committee role patterns (indicate service, not employment)
    COMMITTEE_ROLE_PATTERNS = [
        r'\bappointed\s+to\b',
        r'\belected\s+to\b',
        r'\bserves?\s+on\b',
        r'\bserving\s+on\b',
        r'\bmember\s*(,|of|:|\s)',
        r'\bco-?chair\b',  # Co-chair of committee = service
        r'\bvice[\s-]?chair\b',
        r'\bex[\s-]?officio\b',
    ]

    # Position keywords (indicate actual employment, NOT service)
    POSITION_KEYWORDS = [
        r'\bprofessor\b',
        r'\bassociate\s+professor\b',
        r'\bassistant\s+professor\b',
        r'\binstructor\b',
        r'\blecturer\b',
        r'\bphysician\b',
        r'\bclinician\b',
        r'\bresearch\s+scientist\b',
        r'\bstaff\s+scientist\b',
        r'\bpostdoc',
        r'\bfellow(?:ship)?\b(?!.*society)',  # Fellowship but not "fellow of society"
        r'\bresident\b',
        r'\battending\b',
        r'\bfte\b',
        r'\bsalary\b',
        r'\b\d+%\s*effort\b',  # "50% effort"
    ]

    # Internal institution signals (→ P)
    INTERNAL_SIGNALS = [
        r'\bdepartment\b',
        r'\bschool\s+of\b',
        r'\bcollege\s+of\b',
        r'\buniversity\b.*\bcommittee\b',
        r'\bfaculty\s+(senate|council|committee)\b',
        r'\binstitutional\b',
        r'\bcurriculum\s+committee\b',
        r'\bpromotion\b.*\bcommittee\b',
        r'\btenure\b.*\bcommittee\b',
        r'\birb\b',  # Institutional Review Board
        r'\biacuc\b',  # Institutional Animal Care
    ]

    # External organization signals (→ Q2)
    EXTERNAL_SIGNALS = [
        r'\bnational\b',
        r'\binternational\b',
        r'\bsociety\b',
        r'\bassociation\b',
        r'\bfoundation\b',
        r'\bnih\b',
        r'\bnsf\b',
        r'\bcdc\b',
        r'\bfda\b',
        r'\bstudy\s+section\b',
        r'\breview\s+panel\b',
        r'\bprofessional\b',
        r'\bexternal\b',
        r'\bconsortium\b',
    ]

    def applies_to(self) -> list[str]:
        """Applies to position-related sections."""
        return ['D', 'D1', 'D2', 'D3', 'O', 'P', 'Q', 'Q1', 'Q2']

    def priority(self) -> int:
        """Medium-high priority."""
        return 15

    def analyze(self, entry_text: str) -> ValidatorGuidance:
        """Pre-classification guidance."""
        text_lower = entry_text.lower()

        # Check for committee indicators
        has_committee = any(re.search(p, text_lower) for p in self.COMMITTEE_KEYWORDS)
        has_committee_role = any(re.search(p, text_lower) for p in self.COMMITTEE_ROLE_PATTERNS)
        has_position = any(re.search(p, text_lower) for p in self.POSITION_KEYWORDS)

        if has_committee and has_committee_role and not has_position:
            # This looks like committee service
            is_internal = any(re.search(p, text_lower) for p in self.INTERNAL_SIGNALS)
            is_external = any(re.search(p, text_lower) for p in self.EXTERNAL_SIGNALS)

            if is_external and not is_internal:
                return ValidatorGuidance(
                    exclude_sections=['D1', 'D2', 'D3'],
                    recommend_sections=['Q2'],
                    hints=[
                        "Committee/board service in EXTERNAL organization detected. "
                        "This is professional service (Q2), not a position (D)."
                    ],
                    confidence=0.85,
                    severity='soft',
                    deterministic_signals=[f"External committee: {entry_text[:50]}"]
                )
            else:
                # Default to internal if unclear
                return ValidatorGuidance(
                    exclude_sections=['D1', 'D2', 'D3'],
                    recommend_sections=['P'],
                    hints=[
                        "Committee/board service detected. "
                        "This is institutional service (P), not a position (D)."
                    ],
                    confidence=0.80,
                    severity='soft',
                    deterministic_signals=[f"Committee service: {entry_text[:50]}"]
                )

        return ValidatorGuidance.no_guidance()

    def check_and_correct(self, entry: dict) -> CommitteeCorrectionResult:
        """
        Check a classified entry and auto-correct if it's committee service
        misclassified as a position.

        Args:
            entry: Classified entry dict

        Returns:
            CommitteeCorrectionResult
        """
        text = entry.get('text', '')
        text_lower = text.lower()
        current_code = entry.get('taxonomy_code', '')

        # Only correct D-family codes to P/Q2
        if not current_code.startswith('D'):
            return CommitteeCorrectionResult(
                corrected=False,
                original_code=current_code,
                new_code=current_code,
                confidence=1.0,
                reason="Not a D-code, no correction needed",
                is_internal=True
            )

        # Check for committee indicators
        has_committee = any(re.search(p, text_lower) for p in self.COMMITTEE_KEYWORDS)
        has_committee_role = any(re.search(p, text_lower) for p in self.COMMITTEE_ROLE_PATTERNS)
        has_position = any(re.search(p, text_lower) for p in self.POSITION_KEYWORDS)

        # If it has strong position indicators, don't correct
        if has_position:
            return CommitteeCorrectionResult(
                corrected=False,
                original_code=current_code,
                new_code=current_code,
                confidence=1.0,
                reason="Has position keywords, keeping D-code",
                is_internal=True
            )

        # If it has committee + role indicators, this is service not position
        if has_committee and has_committee_role:
            # Determine internal vs external
            is_internal = any(re.search(p, text_lower) for p in self.INTERNAL_SIGNALS)
            is_external = any(re.search(p, text_lower) for p in self.EXTERNAL_SIGNALS)

            if is_external and not is_internal:
                new_code = 'Q2'
                reason = f"External committee service misclassified as {current_code}"
            else:
                new_code = 'P'
                reason = f"Institutional committee service misclassified as {current_code}"

            return CommitteeCorrectionResult(
                corrected=True,
                original_code=current_code,
                new_code=new_code,
                confidence=0.85,
                reason=reason,
                is_internal=(new_code == 'P')
            )

        # Check for "appointed to" pattern specifically
        if re.search(r'\bappointed\s+to\b.*\b(committee|board|council|panel)\b', text_lower):
            is_external = any(re.search(p, text_lower) for p in self.EXTERNAL_SIGNALS)
            new_code = 'Q2' if is_external else 'P'

            return CommitteeCorrectionResult(
                corrected=True,
                original_code=current_code,
                new_code=new_code,
                confidence=0.90,
                reason=f"'Appointed to [committee]' is service, not position {current_code}",
                is_internal=(new_code == 'P')
            )

        return CommitteeCorrectionResult(
            corrected=False,
            original_code=current_code,
            new_code=current_code,
            confidence=1.0,
            reason="No committee service pattern detected",
            is_internal=True
        )


def apply_committee_corrections(entries: list[dict]) -> tuple[list[dict], dict]:
    """
    Apply committee vs position corrections to classified entries.

    Args:
        entries: List of classified entry dicts

    Returns:
        Tuple of (corrected_entries, stats)
    """
    corrector = CommitteePositionCorrector()

    corrections_made = 0
    to_p_count = 0
    to_q2_count = 0
    correction_details = []

    for entry in entries:
        result = corrector.check_and_correct(entry)

        if result.corrected:
            # Apply correction
            entry['taxonomy_code'] = result.new_code
            entry['taxonomy_confidence'] = result.confidence
            entry['committee_correction'] = {
                'original_code': result.original_code,
                'reason': result.reason,
                'is_internal': result.is_internal
            }
            corrections_made += 1

            if result.new_code == 'P':
                to_p_count += 1
            elif result.new_code == 'Q2':
                to_q2_count += 1

            correction_details.append({
                'entry_id': entry.get('entry_id'),
                'text_preview': entry.get('text', '')[:80],
                'original': result.original_code,
                'corrected_to': result.new_code,
                'reason': result.reason
            })

    stats = {
        'entries_checked': len(entries),
        'corrections_made': corrections_made,
        'corrected_to_P': to_p_count,
        'corrected_to_Q2': to_q2_count,
        'correction_details': correction_details
    }

    return entries, stats


# For testing
if __name__ == "__main__":
    test_entries = [
        # Should be corrected: D2 → P
        {
            "text": "Appointed to the William S. Middleton Memorial Veterans Hospital Research and Development (R&D) Committee",
            "taxonomy_code": "D2"
        },
        # Should be corrected: D1 → Q2
        {
            "text": "Member, NIH Study Section on Musculoskeletal Disorders",
            "taxonomy_code": "D1"
        },
        # Should NOT be corrected (actual position)
        {
            "text": "Associate Professor of Medicine, Department of Internal Medicine",
            "taxonomy_code": "D1"
        },
        # Should be corrected: D3 → P
        {
            "text": "Elected to Faculty Senate, School of Medicine",
            "taxonomy_code": "D3"
        },
        # Should be corrected: D2 → Q2
        {
            "text": "Serves on the American Heart Association Scientific Advisory Board",
            "taxonomy_code": "D2"
        },
    ]

    corrected, stats = apply_committee_corrections(test_entries)

    print(f"Corrections made: {stats['corrections_made']}")
    print(f"  To P (internal): {stats['corrected_to_P']}")
    print(f"  To Q2 (external): {stats['corrected_to_Q2']}")
    print()
    for detail in stats['correction_details']:
        print(f"  {detail['original']} → {detail['corrected_to']}")
        print(f"    Text: {detail['text_preview']}")
        print(f"    Reason: {detail['reason']}")
        print()
