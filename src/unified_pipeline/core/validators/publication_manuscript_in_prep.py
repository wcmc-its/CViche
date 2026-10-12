"""
S7 Manuscripts in Preparation Validator

Detects unpublished manuscripts (submitted, under review, in preparation) to route to S7
instead of S1 (published original research).
"""

import re

from .base_validator import BaseValidator, ValidatorGuidance


class ManuscriptInPrepValidator(BaseValidator):
    """
    Detects manuscripts in preparation/submission (S7) vs published articles (S1/S2).

    Deterministic signals:
    - "in preparation", "in prep", "manuscript in prep"
    - "submitted", "under review", "revise and resubmit", "R&R"
    - "in press", "accepted", "forthcoming"
    - Absence of publication year + presence of submission language

    Hard exclusions when S7 detected:
    - S1 (Original Research - published)
    - S2 (Reviews - published)
    - S6 (Case Reports - published)

    Recommended:
    - S7 (Manuscripts in Preparation)
    """

    name = "ManuscriptInPrepValidator"

    def applies_to(self) -> list[str]:
        """This validator applies to publication sections."""
        return ['S', 'bibliography']

    def priority(self) -> int:
        """High priority - run after abstract/preprint validators."""
        return 90

    # Definitive unpublished status patterns
    IN_PREP_PATTERNS = [
        r'\bin\s+prep(?:aration)?\b',
        r'\bmanuscript\s+in\s+prep',
        r'\bunpublished\b',
        r'\bin\s+progress\b',
    ]

    SUBMISSION_PATTERNS = [
        r'\bsubmitted\b',
        r'\bunder\s+review\b',
        r'\bin\s+review\b',
        r'\bpeer\s+review\b',
        r'\brevise\s+and\s+resubmit\b',
        r'\br\s*&\s*r\b',  # R&R
        r'\bresubmitted\b',
    ]

    ACCEPTED_PATTERNS = [
        r'\baccepted\b',
        r'\bin\s+press\b',
        r'\bforthcoming\b',
        r'\bto\s+appear\b',
    ]

    # Anti-patterns (indicates published)
    PUBLISHED_PATTERNS = [
        r'\bPMID:\s*\d+',
        r'\bPMC\d+',
        r'\bdoi:\s*10\.\d+',
        r'\b\d{4};\d+\(\d+\):\d+-\d+\b',  # Volume(issue):pages
        r'\bpublished\s+online\b',
    ]

    def analyze(self, entry_text: str) -> ValidatorGuidance:
        """
        Analyze entry for manuscript preparation/submission status.

        Returns hard exclusion of S1/S2/S6 if definitive unpublished signals found.
        """
        text_lower = entry_text.lower()

        # Check for published indicators (override unpublished status)
        has_published_indicators = any(
            re.search(pattern, entry_text, re.IGNORECASE)
            for pattern in self.PUBLISHED_PATTERNS
        )

        if has_published_indicators:
            # Has PMID/DOI/pagination → published, not S7
            return ValidatorGuidance()

        # Check for definitive unpublished patterns
        has_in_prep = any(
            re.search(pattern, text_lower, re.IGNORECASE)
            for pattern in self.IN_PREP_PATTERNS
        )

        has_submission = any(
            re.search(pattern, text_lower, re.IGNORECASE)
            for pattern in self.SUBMISSION_PATTERNS
        )

        has_accepted = any(
            re.search(pattern, text_lower, re.IGNORECASE)
            for pattern in self.ACCEPTED_PATTERNS
        )

        # HARD ROUTING: Definitive unpublished status
        if has_in_prep or has_submission or has_accepted:
            deterministic_signals = []
            hints = []

            if has_in_prep:
                deterministic_signals.append('in_preparation_language')
                hints.append('Entry contains "in preparation" or "in prep" → unpublished manuscript')

            if has_submission:
                deterministic_signals.append('submission_status')
                hints.append('Entry contains submission status ("submitted", "under review") → S7')

            if has_accepted:
                deterministic_signals.append('accepted_not_yet_published')
                hints.append('Entry contains "accepted" or "in press" → awaiting publication')

            return ValidatorGuidance(
                exclude_sections=['S1', 'S2', 'S6', 'S8', 'S10'],  # Not published articles
                recommend_sections=['S7'],
                hints=hints + [
                    'Unpublished manuscripts go to S7 (Manuscripts in Preparation)',
                    'Published articles with PMID/DOI/pagination → S1/S2/S6'
                ],
                confidence=0.95,
                severity='hard',
                allow_override=False,
                deterministic_signals=deterministic_signals
            )

        # SOFT GUIDANCE: Ambiguous cases
        # Check for year patterns
        year_pattern = r'\b(19|20)\d{2}\b'
        has_year = bool(re.search(year_pattern, entry_text))

        # Check for journal name (common in published articles)
        # Simple heuristic: capitalized words suggesting journal title
        has_journal_like = bool(re.search(r'\b[A-Z][a-z]+\s+[A-Z][a-z]+', entry_text))

        if not has_year and has_journal_like:
            # Journal mentioned but no year → possibly in prep
            return ValidatorGuidance(
                recommend_sections=['S7'],
                hints=['No publication year found despite journal mention → possibly unpublished'],
                confidence=0.60,
                severity='soft',
                deterministic_signals=['missing_year_with_journal']
            )

        # No clear signals
        return ValidatorGuidance()

    def get_description(self) -> str:
        return "Detects unpublished manuscripts (in prep, submitted, accepted) for S7 routing"
