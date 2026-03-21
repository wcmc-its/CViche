"""
S7 Unpublished Validator

Validates S7 (Unpublished) assignments based on Phase 2 evaluation
showing 0% accuracy (35/35 errors).

Rule: If DOI/PMID present → NOT unpublished
"""

import re
from typing import List
from .base_validator import BaseValidator, ValidatorGuidance


class S7UnpublishedValidator(BaseValidator):
    """
    Validates S7 (In Review / Submitted / In Preparation) assignments.

    Based on Phase 2 Batch 1 evaluation results showing all S7 assignments
    were actually published articles with DOI/PMID.

    Main rule: DOI or PMID present → definitely published, NOT S7
    """

    # DOI patterns (including embedded in URLs)
    DOI_PATTERNS = [
        r'\bdoi:\s*10\.\d{4,}',  # doi: 10.xxxx
        r'https?://doi\.org/10\.\d{4,}',  # doi.org URL
        r'doi\.org/10\.\d{4,}',
        r'/doi/10\.\d{4,}/[\w\.\-]+',  # DOI in any URL
        r'doi\.org/10\.\d{4,}/[\w\.\-]+',
    ]

    # PMID patterns
    PMID_PATTERNS = [
        r'\bPMID:\s*\d{7,}',
        r'PubMed:\s*\d{7,}',
    ]

    # Journal citation patterns
    JOURNAL_CITATION_PATTERNS = [
        r'\.\s+\d{4};?\d+\(\d+\)',  # . 2020;45(2)
        r'[A-Z][a-z]+\s+[A-Z][a-z]+\.\s+\d{4}',  # Nature Med. 2020
        r'\d{4}\s+[A-Z][a-z]+\s+\d+;',  # 2020 Mar 15;
    ]

    # Review journal indicators
    REVIEW_JOURNALS = [
        'Annual Review',
        'Nature Reviews',
        'Current Opinion',
        'Yearbook of',
        'Yearb Med Inform',
    ]

    REVIEW_KEYWORDS = [
        'Ten quick tips',
        'Commentary:',
        'Editorial:',
        'Perspective:',
        'Opinion:',
        'The case for',
        'case for',
        'Viewpoint',
        'Letter to',
    ]

    # Protocol journal indicators
    PROTOCOL_JOURNALS = [
        'Current Protocols',
        'Curr Protoc',
        'STAR Protocols',
        'JoVE',
        'Journal of Visualized Experiments',
        'Methods Mol Biol',
    ]

    # Book chapter series
    BOOK_CHAPTER_SERIES = [
        'Advances in Experimental Medicine and Biology',
        'Adv Exp Med Biol',
        'Methods in Molecular Biology',
        'Methods Mol Biol',
    ]

    # Guidelines/standards keywords
    GUIDELINES_KEYWORDS = [
        'Minimal Information',
        'Guidelines for',
        'Consensus Statement',
        'Standards for',
        'Recommendations for',
    ]

    def applies_to(self) -> List[str]:
        """Applies to Publications parent section."""
        return ['S', 'bibliography']

    def priority(self) -> int:
        """High priority - run early."""
        return 10

    def analyze(self, entry_text: str) -> ValidatorGuidance:
        """
        Analyze entry for S7 (unpublished) vs published indicators.

        Args:
            entry_text: Entry content to analyze

        Returns:
            ValidatorGuidance with exclusions/recommendations
        """
        has_doi = self._check_doi(entry_text)
        has_pmid = self._check_pmid(entry_text)
        has_journal_citation = self._check_journal_citation(entry_text)
        has_year = self._check_year(entry_text)

        # HARD RULE 1: DOI or PMID present → NOT unpublished
        if has_doi or has_pmid:
            # Determine correct section based on type
            if self._is_protocol_journal(entry_text):
                recommend = ['S13']
                hint_type = "protocol journal (Current Protocols, etc.)"
            elif self._is_review_journal(entry_text) or self._has_review_keywords(entry_text):
                recommend = ['S2']
                hint_type = "review/editorial"
            elif self._has_guidelines_keywords(entry_text):
                recommend = ['S14']
                hint_type = "guidelines/standards"
            elif self._is_book_chapter_series(entry_text):
                recommend = ['S4']
                hint_type = "book chapter series"
            else:
                recommend = ['S1', 'S2']
                hint_type = "published article"

            return ValidatorGuidance(
                exclude_sections=['S7'],
                recommend_sections=recommend,
                hints=[
                    f"DOI/PMID present → published, NOT S7 (unpublished)",
                    f"Appears to be {hint_type}"
                ],
                confidence=0.95,
                severity='hard',
                allow_override=False,  # No exceptions for DOI/PMID
                exclusion_reasons={
                    'S7': 'DOI/PMID present → definitely published'
                },
                deterministic_signals=['doi_or_pmid_present']
            )

        # HARD RULE 2: Journal citation + year → likely published
        if has_journal_citation and has_year:
            if self._is_protocol_journal(entry_text):
                recommend = ['S13']
            elif self._is_review_journal(entry_text) or self._has_review_keywords(entry_text):
                recommend = ['S2']
            elif self._is_book_chapter_series(entry_text):
                recommend = ['S4']
            else:
                recommend = ['S1', 'S2']

            return ValidatorGuidance(
                exclude_sections=[],  # Soft guidance (could be in-press)
                recommend_sections=recommend,
                hints=[
                    "Journal citation format with year → likely published",
                    "Consider published sections over S7"
                ],
                confidence=0.75,
                severity='soft',
                allow_override=True,  # Could be in-press
                deterministic_signals=['journal_citation_present']
            )

        # No strong indicators → could be unpublished
        return ValidatorGuidance.no_guidance()

    def _check_doi(self, text: str) -> bool:
        """Check if DOI is present."""
        for pattern in self.DOI_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        return False

    def _check_pmid(self, text: str) -> bool:
        """Check if PMID is present."""
        for pattern in self.PMID_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        return False

    def _check_journal_citation(self, text: str) -> bool:
        """Check if text has journal citation format."""
        for pattern in self.JOURNAL_CITATION_PATTERNS:
            if re.search(pattern, text):
                return True
        return False

    def _check_year(self, text: str) -> bool:
        """Check if publication year is present (1990-2030)."""
        return bool(re.search(r'\b(19\d{2}|20[0-3]\d)\b', text))

    def _is_review_journal(self, text: str) -> bool:
        """Check if text mentions a review journal."""
        for journal in self.REVIEW_JOURNALS:
            if journal.lower() in text.lower():
                return True
        return False

    def _has_review_keywords(self, text: str) -> bool:
        """Check for review/editorial keywords."""
        for keyword in self.REVIEW_KEYWORDS:
            if keyword.lower() in text.lower():
                return True
        return False

    def _is_protocol_journal(self, text: str) -> bool:
        """Check if text is from a protocol journal."""
        for journal in self.PROTOCOL_JOURNALS:
            if journal.lower() in text.lower():
                return True
        return False

    def _has_guidelines_keywords(self, text: str) -> bool:
        """Check for guidelines/standards keywords."""
        for keyword in self.GUIDELINES_KEYWORDS:
            if keyword.lower() in text.lower():
                return True
        return False

    def _is_book_chapter_series(self, text: str) -> bool:
        """Check if text is from a book chapter series."""
        for series in self.BOOK_CHAPTER_SERIES:
            if series.lower() in text.lower():
                return True
        return False

    # RULE 7: Additional classification methods for S1/S2/S6/S8/S10
    def detect_abstract_patterns(self, text: str) -> bool:
        """
        Detect conference abstracts (S8) vs full articles.

        Patterns from ChatGPT feedback - Rule 7:
        - volume:abstract# (e.g., "30:1153")
        - (Suppl) or (Supplement)
        - Conference/Meeting name only (no journal)
        """
        # Abstract number pattern: volume:number
        if re.search(r'\d+:\d+', text) and 'suppl' in text.lower():
            return True

        # Supplement indicator
        if re.search(r'\(\s*Suppl', text, re.IGNORECASE):
            return True

        # Conference-only (no full journal citation)
        conference_indicators = [
            'annual meeting', 'conference', 'symposium',
            'abstract', 'poster presentation', 'poster'
        ]
        has_conference = any(ind in text.lower() for ind in conference_indicators)
        has_full_citation = re.search(r'\d{4};\d+\(\d+\):\d+-\d+', text)

        if has_conference and not has_full_citation:
            return True

        return False

    def detect_preprint(self, text: str) -> bool:
        """
        Detect preprints (S10).

        Patterns:
        - biorxiv, medrxiv, arxiv
        - DOI starting with 10.1101 (biorxiv/medrxiv)
        """
        preprint_indicators = [
            'biorxiv', 'medrxiv', 'arxiv',
            'doi:10.1101', '10.1101', 'preprint'
        ]
        return any(ind in text.lower() for ind in preprint_indicators)

    def detect_case_report(self, text: str) -> bool:
        """
        Detect case reports (S6).

        Patterns:
        - 'case report' or 'case study' in title/text
        """
        return 'case report' in text.lower() or 'case study' in text.lower()

    def classify_publication_type(self, entry_text: str) -> str:
        """
        Classify individual publication into S subsections.

        Based on ChatGPT feedback - Rule 7.

        Returns: S1, S2, S6, S7, S8, or S10
        """
        text = entry_text.lower()

        # S10: Preprints (highest priority after S7)
        if self.detect_preprint(entry_text):
            return 'S10'

        # S8: Abstracts
        if self.detect_abstract_patterns(entry_text):
            return 'S8'

        # S6: Case reports
        if self.detect_case_report(entry_text):
            return 'S6'

        # S2: Reviews/editorials
        if self._is_review_journal(entry_text) or self._has_review_keywords(entry_text):
            return 'S2'

        # S7: In review/submitted/in prep (check keywords)
        s7_keywords = [
            'in press', 'submitted', 'under review',
            'in review', 'in preparation', 'forthcoming'
        ]
        if any(keyword in text for keyword in s7_keywords):
            return 'S7'

        # Default: S1 (research article)
        return 'S1'
