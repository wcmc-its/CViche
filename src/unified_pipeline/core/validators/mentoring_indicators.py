"""
Mentoring Indicators Validator

Detects student/mentee presentations and lab members that should be classified
as mentoring (N) rather than teaching (K) or positions (D).

Based on ChatGPT feedback for CV 2036 Hoffman:
- Rule 1: Student presentations (with "presenter" annotations) → N3/N4, not K1
- Rule 2: Mentee rosters (with "; Advisor-" patterns) → N4, not D1/D2
- Rule 10: "MENTEES" in group label → N3/N4, not D/K
"""

from typing import Dict, List
import re

try:
    from .base_validator import BaseValidator, ValidatorGuidance
except ImportError:
    from base_validator import BaseValidator, ValidatorGuidance


class MentoringIndicatorsValidator(BaseValidator):
    """
    Detect mentoring-related content:
    - Student presentations (N3/N4) vs teaching (K1)
    - Mentee positions (N4) vs own positions (D)
    """

    def applies_to(self) -> List[str]:
        """
        This validator applies to sections that might be confused with mentoring.
        """
        return [
            'educational_contributions',  # K sections
            'professional_positions_employment'  # D sections
        ]

    def priority(self) -> int:
        """
        High priority - should run early to prevent K/D misclassification.
        """
        return 10

    def analyze(self, group: Dict, parent_section: str) -> ValidatorGuidance:
        """
        Analyze group for student/mentee indicators.

        Returns guidance to:
        - Exclude K1-K4 if student presentations detected
        - Exclude D1/D2 if mentee roster detected
        - Recommend N3/N4 based on patterns
        """
        label = group.get('label', '').lower()
        entries = group.get('entries', [])

        # Collect all text from entries
        all_text = ' '.join([
            e.get('text', '') or e.get('text_snippet', '')
            for e in entries
        ])

        signals = []
        hints = []
        exclude = []
        recommend = []

        # RULE 1: Student presentation patterns
        # Pattern: (Name – presenter) or similar
        presenter_pattern = r'\([^)]*–\s*presenter\)'
        presenter_matches = re.findall(presenter_pattern, all_text, re.IGNORECASE)

        student_header_patterns = [
            'student presentation', 'presentations by my students',
            'undergraduate presentation', 'graduate student presentation',
            'mentee presentation', 'trainee presentation'
        ]

        if presenter_matches or any(pat in label for pat in student_header_patterns):
            signals.append(
                f"Student presentation pattern detected "
                f"({len(presenter_matches)} presenter annotations)"
            )
            hints.append(
                "GROUP HEADER or entries indicate STUDENT/MENTEE presentations, not courses taught by the CV owner. "
                "These should be classified as N3 (Current Mentees) or N4 (Past Mentees) scholarly output, NOT K1 (Teaching). "
                "Pattern: '(Name – presenter)' indicates the student presented, not faculty teaching."
            )
            exclude.extend(['K1', 'K2', 'K3', 'K4'])
            recommend.extend(['N3', 'N4'])

            return ValidatorGuidance(
                exclude_sections=exclude,
                recommend_sections=recommend,
                hints=hints,
                confidence=0.95,
                severity='hard',
                allow_override=False,
                deterministic_signals=signals,
                exclusion_reasons={
                    'K1': 'Student presentations are mentoring output, not teaching',
                    'K2': 'Student presentations are mentoring output, not clinical teaching',
                    'K3': 'Student presentations are mentoring output, not administrative teaching',
                    'K4': 'Student presentations are mentoring output, not CME'
                }
            )

        # RULE 2: Mentee position roster patterns
        # Pattern: "; Advisor-<name>" in multiple entries
        advisor_pattern = r';\s*Advisor-'
        advisor_matches = re.findall(advisor_pattern, all_text, re.IGNORECASE)

        mentee_header_patterns = [
            'mentee', 'students mentored', 'undergraduate researchers',
            'graduate students supervised', 'visiting scholars',
            'lab members', 'postdoctoral fellows', 'trainees'
        ]

        if (len(advisor_matches) >= 2 or
            any(pat in label for pat in mentee_header_patterns)):
            signals.append(
                f"Mentee roster detected "
                f"({len(advisor_matches)} advisor annotations)"
            )
            hints.append(
                "GROUP appears to list MENTEES/STUDENTS (their positions/advisors), NOT the CV owner's own positions. "
                "These should be classified as N4 (Past Mentees) or N3 (Current Mentees), NOT D1/D2 (Positions). "
                "Pattern: '; Advisor-<name>' indicates this person was advised, not hired."
            )
            exclude.extend(['D1', 'D2'])
            recommend.extend(['N3', 'N4'])

            return ValidatorGuidance(
                exclude_sections=exclude,
                recommend_sections=recommend,
                hints=hints,
                confidence=0.95,
                severity='hard',
                allow_override=False,
                deterministic_signals=signals,
                exclusion_reasons={
                    'D1': 'These are mentee positions, not CV owner academic positions',
                    'D2': 'These are mentee positions, not CV owner clinical positions'
                }
            )

        # RULE 10: "MENTEES" in group label
        explicit_mentee_labels = [
            'mentee', 'students mentored', 'advisees',
            'trainees supervised', 'mentoring'
        ]

        if any(label_word in label for label_word in explicit_mentee_labels):
            signals.append("Mentee/student header detected in group label")
            hints.append(
                "Group label explicitly indicates MENTEES or STUDENTS MENTORED. "
                "Default to N3 (current) or N4 (past) based on dates. "
                "Do NOT classify as D (positions) or K (teaching)."
            )
            exclude.extend(['D1', 'D2', 'K1', 'K2'])
            recommend.extend(['N3', 'N4'])

            return ValidatorGuidance(
                exclude_sections=exclude,
                recommend_sections=recommend,
                hints=hints,
                confidence=0.90,
                severity='soft',  # Softer than specific patterns
                allow_override=True,
                deterministic_signals=signals,
                exclusion_reasons={
                    'D1': 'Group labeled as mentees, not positions',
                    'D2': 'Group labeled as mentees, not positions',
                    'K1': 'Group labeled as mentees, not teaching',
                    'K2': 'Group labeled as mentees, not teaching'
                }
            )

        # No mentoring indicators detected
        return ValidatorGuidance.no_guidance()
