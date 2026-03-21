"""
Service vs Publication Validator

Distinguishes journal/funder names (Q3 service) from publications (S).

Problem: Bare journal names like "PLOS ONE" or "Nature Reviews" are being
mapped as S1/S2 (publications) instead of Q3 (editorial/review service).

Case Study: CV 2032 had groups with bare journal names mapped incorrectly.
"""

import re
from typing import List
from .base_validator import BaseValidator, ValidatorGuidance


class ServiceVsPublicationValidator(BaseValidator):
    """
    Detects bare journal names vs full publication entries.

    Bare journal names → Q3 (Editorial/Review Service)
    Author + title + journal → S (Bibliography)
    """

    name = "ServiceVsPublicationValidator"

    # Journal name patterns
    JOURNAL_PATTERNS = [
        r'^[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s*$',  # "Psychological Science"
        r'^[A-Z]+\s+[A-Z]+',  # "PLOS ONE", "JAMA Psychiatry"
        r'Journal of',
        r'Proceedings of',
        r'Annals of',
        r'American Journal',
        r'Nature\s',
        r'Science\s',
        r'Cell\s',
        r'Lancet',
        r'BMJ\s',
        r'JAMA\s',
    ]

    # Service context keywords
    SERVICE_KEYWORDS = [
        'reviewer', 'review', 'editorial', 'editor',
        'ad hoc', 'reviewed for', 'panel', 'committee',
        'referee', 'peer review', 'manuscript'
    ]

    # Publication indicators (author, title, year)
    PUBLICATION_INDICATORS = [
        r'\d{4}',  # Year
        r'\.',  # Period (often in author names or titles)
        r',',  # Comma (author lists)
        r'\bet al\b',  # Et al
        r'\bvol\b',  # Volume
        r'\bpp\b',  # Pages
        r'\bdoi:',  # DOI
    ]

    def applies_to(self) -> List[str]:
        """Applies to S (Bibliography) and Q (Professional Service)."""
        return ['S', 'Q']

    def priority(self) -> int:
        """Run early to guide S vs Q classification."""
        return 18

    def analyze(self, entry_text: str) -> ValidatorGuidance:
        """
        Analyze entry to determine if it's a bare journal name or publication.

        Args:
            entry_text: Entry text to analyze

        Returns:
            ValidatorGuidance with recommendations
        """
        if not entry_text or len(entry_text) < 3:
            return ValidatorGuidance.no_guidance()

        text_clean = entry_text.strip()
        text_lower = text_clean.lower()

        # Check if it's a bare journal name
        is_journal_name = self._is_bare_journal_name(text_clean)

        # Check for service context keywords
        has_service_keywords = any(kw in text_lower for kw in self.SERVICE_KEYWORDS)

        # Check for publication indicators
        has_pub_indicators = any(re.search(pattern, text_clean)
                                for pattern in self.PUBLICATION_INDICATORS)

        # Decision logic
        if is_journal_name and not has_pub_indicators:
            # Bare journal name → Q3 service
            return ValidatorGuidance(
                exclude_sections=['S1', 'S2', 'S3', 'S4', 'S5', 'S6'],
                recommend_sections=['Q3'],
                hints=[
                    f'Bare journal name detected: "{text_clean[:50]}"',
                    'Recommend Q3 (Editorial/Review Service) over S (Bibliography)'
                ],
                confidence=0.85,
                severity='soft',
                allow_override=True,
                deterministic_signals=[
                    f'bare_journal_name:{len(text_clean)}chars'
                ]
            )

        elif has_service_keywords and not has_pub_indicators:
            # Service keywords without publication structure → Q3
            return ValidatorGuidance(
                recommend_sections=['Q3'],
                hints=[
                    'Service context keywords detected',
                    'Recommend Q3 (Editorial/Review Service)'
                ],
                confidence=0.75,
                severity='soft',
                allow_override=True,
                deterministic_signals=['service_keywords_present']
            )

        elif has_pub_indicators and not is_journal_name:
            # Full publication structure → S
            return ValidatorGuidance(
                exclude_sections=['Q3'],
                recommend_sections=['S1', 'S2'],
                hints=[
                    'Publication structure detected (author/title/year)',
                    'Recommend S (Bibliography) over Q3'
                ],
                confidence=0.80,
                severity='soft',
                allow_override=True,
                deterministic_signals=['publication_structure_present']
            )

        return ValidatorGuidance.no_guidance()

    def _is_bare_journal_name(self, text: str) -> bool:
        """
        Check if text is a bare journal name (no author/title info).

        Args:
            text: Text to check

        Returns:
            True if appears to be a bare journal name
        """
        text_clean = text.strip()

        # Bare journal names are typically:
        # - Short (< 60 chars)
        # - No commas (except trailing)
        # - No periods (except trailing)
        # - Match journal patterns

        if len(text_clean) > 60:
            return False

        # Remove trailing punctuation
        text_clean = text_clean.rstrip('.,;:')

        # Check if it has structural elements (commas, periods mid-text)
        if ',' in text_clean or '.' in text_clean:
            return False

        # Check against journal patterns
        for pattern in self.JOURNAL_PATTERNS:
            if re.search(pattern, text_clean):
                return True

        return False

    def get_name(self) -> str:
        """Return validator name."""
        return self.name
