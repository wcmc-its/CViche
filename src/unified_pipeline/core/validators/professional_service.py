"""
Professional Service Validator

Distinguishes between Q2 (Grant Reviewing) and Q3 (Editorial Activities).

Based on ChatGPT feedback for CV 2036 Hoffman:
- Rule 4: GROUP 32 mapped to invalid Q4D → should be Q3 (Editorial)
  Pattern: "Reviewer for the following journals", "External referee"
"""


try:
    from .base_validator import BaseValidator, ValidatorGuidance
except ImportError:
    from base_validator import BaseValidator, ValidatorGuidance


class ProfessionalServiceValidator(BaseValidator):
    """
    Detect professional service types:
    - Q2: Grant reviewing (NSF, NIH panels)
    - Q3: Editorial (journal reviewer, editor)
    """

    def applies_to(self) -> list[str]:
        """Applies to extramural professional activities."""
        return ['extramural_professional_activities']

    def priority(self) -> int:
        """Medium priority - helps with Q subsection classification."""
        return 15

    def analyze(self, group: dict, parent_section: str) -> ValidatorGuidance:
        """
        Analyze for grant vs editorial service.

        Returns guidance to distinguish:
        - Q2 for grant/proposal reviewing
        - Q3 for journal/manuscript reviewing
        """

        entries = group.get('entries', [])
        all_text = ' '.join([
            e.get('text', '') or e.get('text_snippet', '')
            for e in entries
        ]).lower()

        label = group.get('label', '').lower()
        combined = all_text + ' ' + label

        signals = []
        hints = []

        # Q2 indicators: Grant reviewing
        grant_indicators = [
            'grant program', 'study section', 'review panel',
            'nsf', 'nih', 'grant review', 'proposal review',
            'panel member', 'scientific review', 'funding panel'
        ]

        # Q3 indicators: Editorial work
        editorial_indicators = [
            'reviewer for the following journals',
            'journal reviewer', 'manuscript review',
            'associate editor', 'editorial board',
            'tenure review', 'external referee',
            'peer review', 'reviewer for', 'manuscript reviewer'
        ]

        grant_score = sum(1 for ind in grant_indicators if ind in combined)
        editorial_score = sum(1 for ind in editorial_indicators if ind in combined)

        if editorial_score > grant_score:
            signals.append(
                f"Editorial indicators: {editorial_score}, "
                f"Grant indicators: {grant_score}"
            )
            hints.append(
                "Content indicates JOURNAL/MANUSCRIPT REVIEWING or EDITORIAL work. "
                "Classify as Q3 (Editorial Activities), NOT Q2 (Grant Reviewing). "
                "Patterns: 'Reviewer for the following journals', 'External referee', 'Manuscript review'."
            )
            return ValidatorGuidance(
                exclude_sections=['Q2'],
                recommend_sections=['Q3'],
                hints=hints,
                confidence=0.85,
                severity='soft',
                deterministic_signals=signals,
                exclusion_reasons={
                    'Q2': 'Editorial/reviewer work, not grant reviewing'
                }
            )

        elif grant_score > editorial_score:
            signals.append(
                f"Grant indicators: {grant_score}, "
                f"Editorial indicators: {editorial_score}"
            )
            hints.append(
                "Content indicates GRANT/PROPOSAL REVIEWING or STUDY SECTION work. "
                "Classify as Q2 (Grant Reviewing), NOT Q3 (Editorial). "
                "Patterns: 'NSF', 'NIH', 'grant review', 'study section', 'funding panel'."
            )
            return ValidatorGuidance(
                exclude_sections=['Q3'],
                recommend_sections=['Q2'],
                hints=hints,
                confidence=0.85,
                severity='soft',
                deterministic_signals=signals,
                exclusion_reasons={
                    'Q3': 'Grant reviewing, not editorial work'
                }
            )

        # No clear signal
        return ValidatorGuidance.no_guidance()
