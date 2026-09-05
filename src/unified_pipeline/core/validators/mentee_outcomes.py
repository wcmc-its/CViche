"""
Mentee Outcomes Validator

Detects career outcome tracking sections for past mentees.

Problem: Sections like "Current professional positions of Former Graduate
Students" are unmapped because they don't contain "mentee" keyword and don't
match standard mentoring patterns.

Case Study: CV 2032 had multiple groups (G11-G24) with mentee career outcomes
that were unmapped or misclassified.
"""

import re
from typing import List, Dict
from .base_validator import BaseValidator, ValidatorGuidance


class MenteeOutcomesValidator(BaseValidator):
    """
    Detects sections tracking career outcomes of former mentees/trainees.

    Former graduate student/postdoc career tracking → N4 (Past Mentees)
    """

    name = "MenteeOutcomesValidator"

    # Former role indicators
    FORMER_ROLE_INDICATORS = [
        'former graduate student',
        'former postdoc',
        'former postdoctoral',
        'former undergraduate',
        'former trainee',
        'former phd student',
        'former doctoral student',
        'former fellow',
        'past mentee',
        'past trainee',
        'alumni',
        'alumna',
        'alumnus',
    ]

    # Career outcome indicators
    CAREER_OUTCOME_INDICATORS = [
        'currently',
        'current position',
        'now at',
        'assistant professor',
        'associate professor',
        'professor at',
        'postdoctoral fellow',
        'postdoc at',
        'industry',
        'research scientist',
        'faculty',
        'position at',
        'joined',
        'accepted position',
    ]

    # Section label patterns
    SECTION_LABEL_PATTERNS = [
        r'former.*student',
        r'former.*postdoc',
        r'former.*trainee',
        r'past.*mentee',
        r'alumni',
        r'career.*outcome',
        r'professional.*position.*former',
        r'current.*position.*former',
        r'placement',
    ]

    def applies_to(self) -> list[str]:
        """Applies to N (Mentoring) sections primarily."""
        return ['N', 'D', 'E']

    def priority(self) -> int:
        """Run with medium priority."""
        return 16

    def analyze_section(
        self,
        section_label: str,
        entries: list[dict]
    ) -> ValidatorGuidance:
        """
        Analyze section to detect mentee outcome tracking.

        Args:
            section_label: Section label
            entries: Entry objects with entry_type

        Returns:
            ValidatorGuidance with recommendations
        """
        if not entries:
            return ValidatorGuidance.no_guidance()

        label_lower = section_label.lower()

        # Check section label for former/alumni patterns
        label_has_former_pattern = any(
            re.search(pattern, label_lower)
            for pattern in self.SECTION_LABEL_PATTERNS
        )

        # Analyze entries
        former_role_count = 0
        outcome_indicator_count = 0
        total_entries = len(entries)

        for entry in entries:
            # Extract text from entry object
            if isinstance(entry, dict):
                text = entry.get('text_snippet', '')
            else:
                text = entry

            text_lower = text.lower()

            # Check for former role indicators
            if any(indicator in text_lower for indicator in self.FORMER_ROLE_INDICATORS):
                former_role_count += 1

            # Check for outcome indicators
            if any(indicator in text_lower for indicator in self.CAREER_OUTCOME_INDICATORS):
                outcome_indicator_count += 1

        # Calculate percentages
        former_pct = former_role_count / total_entries if total_entries > 0 else 0
        outcome_pct = outcome_indicator_count / total_entries if total_entries > 0 else 0

        # Decision logic
        if label_has_former_pattern and (former_pct > 0.3 or outcome_pct > 0.3):
            # Strong signal: label mentions former/alumni + content supports it
            return ValidatorGuidance(
                exclude_sections=['N1', 'N2', 'N3', 'D1', 'D2', 'E1', 'E2'],
                recommend_sections=['N4'],
                hints=[
                    f'Former role tracking detected in label: "{section_label}"',
                    f'{former_role_count}/{total_entries} entries mention former roles',
                    f'{outcome_indicator_count}/{total_entries} entries mention career outcomes',
                    'Recommend N4 (Past Mentees/Trainees)'
                ],
                confidence=0.90,
                severity='soft',
                allow_override=True,
                deterministic_signals=[
                    f'former_label_pattern',
                    f'former_roles:{former_pct:.0%}',
                    f'outcomes:{outcome_pct:.0%}'
                ]
            )

        elif former_pct > 0.5 and outcome_pct > 0.3:
            # Content-based signal: high percentage of former roles + outcomes
            return ValidatorGuidance(
                exclude_sections=['N1', 'N2', 'N3'],
                recommend_sections=['N4'],
                hints=[
                    f'Former role career tracking detected in content',
                    f'{former_role_count}/{total_entries} entries mention former roles',
                    f'{outcome_indicator_count}/{total_entries} entries mention outcomes',
                    'Recommend N4 (Past Mentees/Trainees)'
                ],
                confidence=0.85,
                severity='soft',
                allow_override=True,
                deterministic_signals=[
                    f'former_roles:{former_pct:.0%}',
                    f'outcomes:{outcome_pct:.0%}'
                ]
            )

        elif label_has_former_pattern or former_pct > 0.3:
            # Weak signal: label or some content suggests former roles
            return ValidatorGuidance(
                recommend_sections=['N4'],
                hints=[
                    'Possible former mentee career tracking detected',
                    'Consider N4 (Past Mentees/Trainees)'
                ],
                confidence=0.70,
                severity='soft',
                allow_override=True,
                deterministic_signals=['weak_former_signal']
            )

        return ValidatorGuidance.no_guidance()

    def analyze(self, entry_text: str) -> ValidatorGuidance:
        """
        Fallback per-entry analysis.

        Args:
            entry_text: Entry text

        Returns:
            ValidatorGuidance
        """
        # Simple per-entry check
        text_lower = entry_text.lower()

        has_former = any(indicator in text_lower
                        for indicator in self.FORMER_ROLE_INDICATORS)
        has_outcome = any(indicator in text_lower
                         for indicator in self.CAREER_OUTCOME_INDICATORS)

        if has_former and has_outcome:
            return ValidatorGuidance(
                recommend_sections=['N4'],
                hints=['Former mentee career outcome detected'],
                confidence=0.75,
                severity='soft',
                allow_override=True,
                deterministic_signals=['former_with_outcome']
            )

        return ValidatorGuidance.no_guidance()

    def get_name(self) -> str:
        """Return validator name."""
        return self.name
