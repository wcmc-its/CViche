"""
S10 Preprint Validator

Deterministic detection of preprints vs published journal articles.

CRITICAL PATTERNS (from confusion matrix routing rules):
- bioRxiv/medRxiv/arXiv mentions
- DOI starting with 10.1101/ (bioRxiv/medRxiv)
- Explicit "preprint" mention
- Repository platform mentions: OSF Preprints, etc.

This is a "Pass 1.5" validator that provides hard exclusions for obvious cases.
"""

import re
from .base_validator import BaseValidator, ValidatorGuidance


class PublicationPreprintValidator(BaseValidator):
    """
    Detects preprints (S10) vs published journal articles (S1/S2).

    Uses deterministic pattern matching for high-confidence cases.
    """

    name = "PublicationPreprintValidator"

    # Preprint DOI patterns (most definitive)
    PREPRINT_DOI_PATTERNS = [
        r'10\.1101/',  # bioRxiv/medRxiv DOI prefix
        r'doi:\s*10\.1101/',
        r'doi\.org/10\.1101/',
        r'https?://doi\.org/10\.1101/',
    ]

    # Preprint platform keywords
    PREPRINT_PLATFORMS = [
        'biorxiv',
        'medrxiv',
        'arxiv',
        'preprint',
        'osf preprint',
        'osf preprints',
        'ssrn',
        'research square',
        'authorea',
        'preprints.org',
    ]

    # arXiv-specific pattern
    ARXIV_PATTERN = re.compile(
        r'arXiv:\s*\d{4}\.\d{4,5}',
        re.IGNORECASE
    )

    # Published article indicators (negative evidence)
    PUBLISHED_INDICATORS = [
        r'\d{4};\d+\(\d+\):\d+',  # Journal citation with volume/issue/pages
        r'PMID:\s*\d{7,}',  # PubMed ID (preprints don't have PMIDs)
        r'Published in',
        r'Appeared in',
    ]

    # Preprint version indicators
    PREPRINT_VERSION_PATTERNS = [
        r'v\d+',  # v1, v2, etc.
        r'version\s+\d+',
        r'preprint version',
    ]

    def applies_to(self) -> list[str]:
        """Applies to Publications parent section (S)."""
        return ['S', 'scholarly_outputs', 'bibliography']

    def priority(self) -> int:
        """Very high priority - run before LLM classification."""
        return 5

    def analyze(self, entry_text: str) -> ValidatorGuidance:
        """
        Analyze entry for preprint vs published article indicators.

        Args:
            entry_text: Publication entry text

        Returns:
            ValidatorGuidance with hard exclusions for definitive cases
        """
        text_lower = entry_text.lower()

        # Check for definitive preprint patterns
        has_preprint_doi = any(
            re.search(pattern, entry_text, re.IGNORECASE)
            for pattern in self.PREPRINT_DOI_PATTERNS
        )
        has_preprint_platform = any(
            platform in text_lower
            for platform in self.PREPRINT_PLATFORMS
        )
        has_arxiv = bool(self.ARXIV_PATTERN.search(entry_text))
        has_version = any(
            re.search(pattern, entry_text, re.IGNORECASE)
            for pattern in self.PREPRINT_VERSION_PATTERNS
        )

        # Check for published article indicators (negative evidence)
        has_pmid = bool(re.search(self.PUBLISHED_INDICATORS[1], entry_text, re.IGNORECASE))
        has_journal_citation = bool(re.search(self.PUBLISHED_INDICATORS[0], entry_text))
        has_published_mention = any(
            phrase in text_lower
            for phrase in ['published in', 'appeared in']
        )

        # =====================================================================
        # DEFINITIVE PREPRINT (Hard exclusion of S1/S2)
        # =====================================================================

        # PATTERN 1: 10.1101/ DOI (bioRxiv/medRxiv)
        if has_preprint_doi:
            return ValidatorGuidance(
                exclude_sections=['S1', 'S2', 'S6', 'S8'],  # NOT published articles
                recommend_sections=['S10'],
                hints=[
                    "✓ PREPRINT DETECTED: 10.1101/ DOI (bioRxiv/medRxiv)",
                    "→ S10 (Preprint), NOT S1 (Published Article)"
                ],
                confidence=0.98,
                severity='hard',
                allow_override=False,  # Very confident
                exclusion_reasons={
                    'S1': '10.1101/ DOI is definitive bioRxiv/medRxiv preprint',
                    'S2': 'Preprints are not peer-reviewed publications',
                },
                deterministic_signals=['biorxiv_medrxiv_doi']
            )

        # PATTERN 2: arXiv identifier
        if has_arxiv and not has_pmid:
            return ValidatorGuidance(
                exclude_sections=['S1', 'S2', 'S6'],
                recommend_sections=['S10'],
                hints=[
                    "✓ PREPRINT DETECTED: arXiv identifier",
                    "→ S10 (Preprint), NOT S1"
                ],
                confidence=0.95,
                severity='hard',
                allow_override=False,
                exclusion_reasons={
                    'S1': 'arXiv identifier indicates preprint, not peer-reviewed publication'
                },
                deterministic_signals=['arxiv_identifier']
            )

        # PATTERN 3: Preprint platform mention + no PMID
        if has_preprint_platform and not has_pmid and not has_journal_citation:
            confidence = 0.90 if has_version else 0.80
            severity = 'hard' if has_version else 'soft'

            return ValidatorGuidance(
                exclude_sections=['S1', 'S2'] if severity == 'hard' else [],
                recommend_sections=['S10'],
                hints=[
                    f"⚠️ Preprint platform detected ({', '.join([p for p in self.PREPRINT_PLATFORMS if p in text_lower])})",
                    "→ Likely S10 (Preprint)" if severity == 'soft' else "→ S10 (Preprint)"
                ],
                confidence=confidence,
                severity=severity,
                allow_override=(severity == 'soft'),
                exclusion_reasons={
                    'S1': 'Preprint platform mention indicates non-peer-reviewed preprint'
                } if severity == 'hard' else {},
                deterministic_signals=[f'preprint_platform_{severity}']
            )

        # =====================================================================
        # DEFINITIVE PUBLISHED ARTICLE (Negative evidence for S10)
        # =====================================================================

        if has_pmid and not has_preprint_platform:
            return ValidatorGuidance(
                exclude_sections=['S10'],  # NOT preprint
                recommend_sections=['S1', 'S2'],
                hints=[
                    "PMID present → published article, NOT preprint"
                ],
                confidence=0.95,
                severity='hard',
                allow_override=False,
                exclusion_reasons={
                    'S10': 'PMID indicates peer-reviewed published article, not preprint'
                },
                deterministic_signals=['pmid_present']
            )

        if has_published_mention and not has_preprint_platform:
            return ValidatorGuidance(
                exclude_sections=['S10'],
                recommend_sections=['S1', 'S2'],
                hints=[
                    "'Published in' mention → published article, NOT preprint"
                ],
                confidence=0.85,
                severity='soft',
                allow_override=True,
                exclusion_reasons={
                    'S10': 'Explicit publication mention suggests peer-reviewed article'
                },
                deterministic_signals=['published_mention']
            )

        # No strong signals
        return ValidatorGuidance.no_guidance()

    def get_name(self) -> str:
        """Return validator name."""
        return self.name
