"""
S8 Conference Abstract Validator

Deterministic detection of conference abstracts vs full published articles.

CRITICAL PATTERNS (from confusion matrix routing rules):
- volume:abstract# format with 3-4 digit abstract number (e.g., "30:1153.1154")
- (Suppl) or (Supplement) in citation
- Abstract journals: FASEB Journal, Circulation, AHA, ASCO, RSNA
- Explicit "Abstract" or "Poster" + meeting name

This is a "Pass 1.5" validator that provides hard exclusions for obvious cases.
"""

import re
from typing import List
from .base_validator import BaseValidator, ValidatorGuidance


class PublicationAbstractValidator(BaseValidator):
    """
    Detects conference abstracts (S8) vs full journal articles (S1/S2).

    Uses deterministic pattern matching for high-confidence cases.
    """

    name = "PublicationAbstractValidator"

    # DEFINITIVE PATTERN: volume:abstract# with multi-digit abstract number
    ABSTRACT_NUMBER_PATTERN = re.compile(
        r'\b\d{1,3}:\d{3,4}\b',  # e.g., "30:1153" or "45:1234.5678"
        re.IGNORECASE
    )

    # Supplement indicators
    SUPPLEMENT_PATTERNS = [
        r'\bSuppl(?:ement)?\b',
        r'\bSuppl\s*\d+\b',
        r'\(\s*Suppl',
    ]

    # Abstract journals (from confusion matrix)
    ABSTRACT_JOURNALS = [
        'faseb journal',
        'faseb j',
        'circulation',
        'aha ',
        'asco ',
        'aacr ',
        'rsna',
        'blood',
        'journal of the american college of cardiology',
        'jacc',
    ]

    # Conference/meeting indicators
    CONFERENCE_KEYWORDS = [
        'annual meeting',
        'conference',
        'symposium',
        'abstract',
        'poster presentation',
        'poster session',
        'oral presentation',
        'platform presentation',
    ]

    # Full article indicators (negative evidence)
    FULL_ARTICLE_INDICATORS = [
        r'\d{4};\d+\(\d+\):\d+-\d+',  # 2020;45(2):123-145 (page range)
        r'doi:\s*10\.\d{4,}',  # DOI (abstracts can have DOIs too, but lower priority)
        r'PMID:\s*\d{7,}',  # PMID
    ]

    def applies_to(self) -> List[str]:
        """Applies to Publications parent section (S)."""
        return ['S', 'scholarly_outputs', 'bibliography']

    def priority(self) -> int:
        """Very high priority - run before LLM classification."""
        return 5

    def analyze(self, entry_text: str) -> ValidatorGuidance:
        """
        Analyze entry for abstract vs full article indicators.

        Args:
            entry_text: Publication entry text

        Returns:
            ValidatorGuidance with hard exclusions for definitive cases
        """
        # Check for definitive abstract patterns
        has_abstract_number = bool(self.ABSTRACT_NUMBER_PATTERN.search(entry_text))
        has_supplement = any(
            re.search(pattern, entry_text, re.IGNORECASE)
            for pattern in self.SUPPLEMENT_PATTERNS
        )
        has_abstract_journal = any(
            journal in entry_text.lower()
            for journal in self.ABSTRACT_JOURNALS
        )
        has_conference_keyword = any(
            keyword in entry_text.lower()
            for keyword in self.CONFERENCE_KEYWORDS
        )

        # Check for full article indicators (negative evidence)
        has_page_range = bool(re.search(self.FULL_ARTICLE_INDICATORS[0], entry_text))
        has_doi = bool(re.search(self.FULL_ARTICLE_INDICATORS[1], entry_text, re.IGNORECASE))
        has_pmid = bool(re.search(self.FULL_ARTICLE_INDICATORS[2], entry_text, re.IGNORECASE))

        # =====================================================================
        # DEFINITIVE ABSTRACT (Hard exclusion of S1)
        # =====================================================================

        # PATTERN 1: volume:abstract# format
        if has_abstract_number:
            return ValidatorGuidance(
                exclude_sections=['S1', 'S2', 'S6'],  # NOT full articles
                recommend_sections=['S8'],
                hints=[
                    "✓ ABSTRACT DETECTED: volume:abstract# pattern (e.g., '30:1153')",
                    "→ S8 (Conference Abstract), NOT S1 (Full Article)"
                ],
                confidence=0.98,
                severity='hard',
                allow_override=False,  # Very confident
                exclusion_reasons={
                    'S1': 'volume:abstract# format is definitive abstract pattern',
                    'S2': 'Abstracts are not full review articles',
                },
                deterministic_signals=['volume_abstract_number_pattern']
            )

        # PATTERN 2: Supplement + abstract journal
        if has_supplement and has_abstract_journal:
            return ValidatorGuidance(
                exclude_sections=['S1', 'S2', 'S6'],
                recommend_sections=['S8'],
                hints=[
                    "✓ ABSTRACT DETECTED: Supplement + abstract journal (FASEB, Circulation, etc.)",
                    "→ S8 (Conference Abstract), NOT S1"
                ],
                confidence=0.95,
                severity='hard',
                allow_override=False,
                exclusion_reasons={
                    'S1': 'Supplement in abstract journal indicates conference abstract',
                },
                deterministic_signals=['supplement_abstract_journal']
            )

        # PATTERN 3: Conference keyword + no page range
        if has_conference_keyword and not has_page_range and not has_doi and not has_pmid:
            return ValidatorGuidance(
                exclude_sections=[],  # Soft guidance
                recommend_sections=['S8'],
                hints=[
                    "⚠️ Likely abstract: Conference/meeting mention without full citation",
                    "Consider S8 (Abstract) if no full article citation present"
                ],
                confidence=0.75,
                severity='soft',
                allow_override=True,
                deterministic_signals=['conference_keyword_no_full_citation']
            )

        # =====================================================================
        # DEFINITIVE FULL ARTICLE (Negative evidence for S8)
        # =====================================================================

        if has_page_range and not has_abstract_number:
            return ValidatorGuidance(
                exclude_sections=['S8'],  # NOT abstract
                recommend_sections=['S1', 'S2'],
                hints=[
                    "Full page range citation → full article, NOT abstract"
                ],
                confidence=0.90,
                severity='soft',
                allow_override=True,
                exclusion_reasons={
                    'S8': 'Full page range indicates complete article, not abstract'
                },
                deterministic_signals=['full_page_range']
            )

        # No strong signals
        return ValidatorGuidance.no_guidance()

    def get_name(self) -> str:
        """Return validator name."""
        return self.name
