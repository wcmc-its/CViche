"""
S6 Case Reports Validator

Detects case reports/case series to route to S6 instead of S1 (original research).
"""

import re
from typing import List
from .base_validator import BaseValidator, ValidatorGuidance


class CaseReportValidator(BaseValidator):
    """
    Detects case reports (S6) vs original research articles (S1).

    Deterministic signals:
    - Title contains "case report", "case series", "case study"
    - Case report journals (e.g., BMJ Case Reports, Case Reports in ...)
    - Explicit "n=1", "single patient", "patient presentation" language
    - Small case series: "three cases", "5 patients", etc.

    Soft exclusions when S6 detected:
    - S1 (Original Research)

    Recommended:
    - S6 (Case Reports/Series)
    """

    name = "CaseReportValidator"

    def applies_to(self) -> List[str]:
        """This validator applies to publication sections."""
        return ['S', 'bibliography']

    def priority(self) -> int:
        """High priority - distinguishes S6 from S1."""
        return 85

    # Definitive case report patterns in title
    TITLE_PATTERNS = [
        r'\bcase\s+report\b',
        r'\bcase\s+series\b',
        r'\bcase\s+study\b',
        r'\brare\s+case\b',
        r'\bunusual\s+case\b',
        r'\bcase\s+presentation\b',
    ]

    # Case report-specific journals
    CASE_REPORT_JOURNALS = [
        r'\bBMJ\s+Case\s+Rep(?:orts)?\b',
        r'\bCase\s+Reports\s+in\b',
        r'\bJ(?:ournal)?\s+(?:of\s+)?Med(?:ical)?\s+Case\s+Rep',
        r'\bAm\s+J\s+Case\s+Rep\b',
    ]

    # Small sample size indicators
    SMALL_N_PATTERNS = [
        r'\bn\s*=\s*[1-5]\b',
        r'\bsingle\s+patient\b',
        r'\bone\s+patient\b',
        r'\b(two|three|four|five)\s+cases?\b',
        r'\b(two|three|four|five)\s+patients?\b',
    ]

    # Clinical description patterns
    CLINICAL_DESCRIPTION = [
        r'\bpatient\s+presentation\b',
        r'\bclinical\s+presentation\b',
        r'\b\d{1,2}-year-old\s+(male|female|patient)\b',  # e.g., "52-year-old male"
        r'\bpresented\s+with\b',
    ]

    # Anti-patterns (indicates original research, not case report)
    RESEARCH_PATTERNS = [
        r'\brandomized\b',
        r'\bcontrolled\s+trial\b',
        r'\bcohort\s+study\b',
        r'\bn\s*=\s*\d{2,}',  # n >= 10
        r'\b(retrospective|prospective)\s+analysis\b',
        r'\bmulti-?center\b',
    ]

    def analyze(self, entry_text: str) -> ValidatorGuidance:
        """
        Analyze entry for case report characteristics.

        Returns soft recommendation for S6 if case report signals detected.
        """
        text_lower = entry_text.lower()

        # Check for research study indicators (override case report)
        has_research_indicators = any(
            re.search(pattern, entry_text, re.IGNORECASE)
            for pattern in self.RESEARCH_PATTERNS
        )

        if has_research_indicators:
            # Likely a research study, not a case report
            return ValidatorGuidance()

        # Check for definitive case report patterns
        has_title_pattern = any(
            re.search(pattern, text_lower, re.IGNORECASE)
            for pattern in self.TITLE_PATTERNS
        )

        has_case_journal = any(
            re.search(pattern, entry_text, re.IGNORECASE)
            for pattern in self.CASE_REPORT_JOURNALS
        )

        has_small_n = any(
            re.search(pattern, entry_text, re.IGNORECASE)
            for pattern in self.SMALL_N_PATTERNS
        )

        has_clinical_desc = any(
            re.search(pattern, text_lower, re.IGNORECASE)
            for pattern in self.CLINICAL_DESCRIPTION
        )

        # HARD ROUTING: Definitive case report (title + journal or title + small N)
        if has_title_pattern and (has_case_journal or has_small_n):
            deterministic_signals = []
            hints = []

            if has_title_pattern:
                deterministic_signals.append('case_report_title')
                hints.append('Title contains "case report" or "case series" → S6')

            if has_case_journal:
                deterministic_signals.append('case_report_journal')
                hints.append('Published in case report journal → S6')

            if has_small_n:
                deterministic_signals.append('small_sample_size')
                hints.append('Small sample size (n=1-5) typical of case reports → S6')

            return ValidatorGuidance(
                exclude_sections=['S1'],  # Not original research
                recommend_sections=['S6'],
                hints=hints + [
                    'Case reports/series → S6',
                    'Original research with larger samples → S1'
                ],
                confidence=0.92,
                severity='hard',
                allow_override=True,  # Allow LLM override for edge cases
                deterministic_signals=deterministic_signals
            )

        # SOFT GUIDANCE: Possible case report based on single indicator
        if has_title_pattern or has_case_journal:
            deterministic_signals = []
            hints = []

            if has_title_pattern:
                deterministic_signals.append('possible_case_report_title')
                hints.append('Title suggests case report → likely S6')

            if has_case_journal:
                deterministic_signals.append('case_journal_match')
                hints.append('Case report journal → S6')

            return ValidatorGuidance(
                recommend_sections=['S6'],
                hints=hints,
                confidence=0.75,
                severity='soft',
                deterministic_signals=deterministic_signals
            )

        # Check for clinical description patterns alone (weak signal)
        if has_clinical_desc and not has_research_indicators:
            return ValidatorGuidance(
                recommend_sections=['S6'],
                hints=['Clinical presentation language may indicate case report'],
                confidence=0.55,
                severity='soft',
                deterministic_signals=['clinical_presentation_language']
            )

        # No clear signals
        return ValidatorGuidance()

    def get_description(self) -> str:
        return "Detects case reports/series (S6) vs original research (S1)"
