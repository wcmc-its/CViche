"""
Section Header Context Validator

Uses section headers to provide context-based classification guidance.
Critical for disambiguating entries that appear in unexpected sections.
"""

import re
from typing import List, Set
from .base_validator import BaseValidator, ValidatorGuidance


class SectionHeaderContextValidator(BaseValidator):
    """
    Provides classification guidance based on section header context.

    Examples:
    - "MENTEES" header → bias toward N, away from C/D
    - "GRANTS" header → bias toward M2, away from H
    - "HONORS" header → bias toward H, away from M2/I
    - "TEACHING" header → bias toward K, away from S
    """

    name = "SectionHeaderContextValidator"

    def applies_to(self) -> list[str]:
        """Applies to all parent sections."""
        return ['*']  # Universal validator

    def priority(self) -> int:
        """Medium priority - provides context, doesn't override strong signals."""
        return 50

    # Header patterns and their biases
    HEADER_BIASES = {
        # Mentoring section headers
        'mentees': {
            'favor': ['N'],
            'disfavor': ['C', 'D', 'B'],
            'reason': 'MENTEES section lists supervisory relationships, not own training/positions'
        },
        'advisees': {
            'favor': ['N'],
            'disfavor': ['C', 'D', 'B'],
            'reason': 'ADVISEES section documents mentoring, not own career'
        },
        'trainees': {
            'favor': ['N'],
            'disfavor': ['C', 'D'],
            'reason': 'TRAINEES SUPERVISED indicates mentoring roles'
        },

        # Research funding headers
        'grants': {
            'favor': ['M2', 'M'],
            'disfavor': ['H', 'N'],
            'reason': 'GRANTS section emphasizes funding mechanisms, not honors or mentoring'
        },
        'funding': {
            'favor': ['M2', 'M'],
            'disfavor': ['H'],
            'reason': 'FUNDING section is about research support'
        },
        'research support': {
            'favor': ['M2', 'M'],
            'disfavor': ['H', 'N'],
            'reason': 'RESEARCH SUPPORT lists funded projects'
        },

        # Honors/Awards headers
        'honors': {
            'favor': ['H'],
            'disfavor': ['M2', 'I', 'M'],
            'reason': 'HONORS section emphasizes recognition, not funding or membership'
        },
        'awards': {
            'favor': ['H'],
            'disfavor': ['M2', 'I'],
            'reason': 'AWARDS section is about achievements and prizes'
        },
        'distinctions': {
            'favor': ['H'],
            'disfavor': ['M2', 'I'],
            'reason': 'DISTINCTIONS emphasizes recognition'
        },

        # Teaching headers
        'teaching': {
            'favor': ['K'],
            'disfavor': ['S', 'S3', 'S4'],
            'reason': 'TEACHING section focuses on instructional activities, not publications'
        },
        'courses': {
            'favor': ['K', 'K1'],
            'disfavor': ['S'],
            'reason': 'COURSES section is about teaching, not scholarly outputs'
        },
        'instruction': {
            'favor': ['K'],
            'disfavor': ['S'],
            'reason': 'INSTRUCTION section documents teaching roles'
        },

        # Publications headers
        'publications': {
            'favor': ['S'],
            'disfavor': ['N', 'K', 'M'],
            'reason': 'PUBLICATIONS section is bibliography, not mentoring/teaching/research activities'
        },
        'bibliography': {
            'favor': ['S'],
            'disfavor': ['N', 'K'],
            'reason': 'BIBLIOGRAPHY lists scholarly outputs'
        },
        'peer-reviewed': {
            'favor': ['S1', 'S2', 'S'],
            'disfavor': ['N'],
            'reason': 'PEER-REVIEWED PUBLICATIONS emphasizes published work'
        },

        # Membership/Service headers
        'professional societies': {
            'favor': ['I'],
            'disfavor': ['H', 'Q'],
            'reason': 'PROFESSIONAL SOCIETIES typically lists memberships'
        },
        'memberships': {
            'favor': ['I'],
            'disfavor': ['H', 'Q'],
            'reason': 'MEMBERSHIPS section is about organizational affiliations'
        },

        # Service headers
        'professional service': {
            'favor': ['Q'],
            'disfavor': ['I', 'H'],
            'reason': 'PROFESSIONAL SERVICE emphasizes active contributions, not passive membership'
        },
        'committee service': {
            'favor': ['Q', 'Q4'],
            'disfavor': ['I'],
            'reason': 'COMMITTEE SERVICE is active participation'
        },

        # Training headers (own training)
        'education': {
            'favor': ['B'],
            'disfavor': ['N', 'K'],
            'reason': 'EDUCATION section is about own degrees, not teaching or mentoring'
        },
        'postdoctoral training': {
            'favor': ['C', 'C1'],
            'disfavor': ['D', 'N'],
            'reason': 'POSTDOCTORAL TRAINING is own training, not positions or mentoring'
        },
        'fellowship': {
            'favor': ['C', 'C1'],
            'disfafor': ['D', 'N', 'H'],
            'reason': 'FELLOWSHIP (in training context) is about own training'
        },

        # Position headers
        'appointments': {
            'favor': ['D'],
            'disfavor': ['C', 'N'],
            'reason': 'APPOINTMENTS section lists faculty/staff positions'
        },
        'employment': {
            'favor': ['D'],
            'disfavor': ['C', 'N'],
            'reason': 'EMPLOYMENT is about job history'
        },
        'academic positions': {
            'favor': ['D'],
            'disfavor': ['C', 'N'],
            'reason': 'ACADEMIC POSITIONS lists faculty roles'
        },
    }

    def analyze(self, entry_text: str, header: str = None) -> ValidatorGuidance:
        """
        Analyze entry based on section header context.

        Args:
            entry_text: Entry text (not used directly, but required by interface)
            header: Section header/label (from group metadata)

        Returns:
            Guidance based on header context
        """
        if not header:
            return ValidatorGuidance()

        header_lower = header.lower().strip()

        # Check for header pattern matches
        for pattern, bias_info in self.HEADER_BIASES.items():
            if pattern in header_lower:
                favor = bias_info['favor']
                disfavor = bias_info.get('disfavor', [])
                reason = bias_info['reason']

                hints = [
                    f"Section header '{header}' suggests: {reason}",
                    f"Recommended sections: {', '.join(favor)}",
                ]

                if disfavor:
                    hints.append(f"Less likely: {', '.join(disfavor)}")

                # Soft guidance - doesn't hard exclude, just recommends
                return ValidatorGuidance(
                    recommend_sections=favor,
                    exclude_sections=[],  # No hard exclusions from header alone
                    hints=hints,
                    confidence=0.70,
                    severity='soft',
                    deterministic_signals=[f'header_pattern_{pattern}']
                )

        return ValidatorGuidance()

    def get_description(self) -> str:
        return "Provides classification guidance based on section header context"
