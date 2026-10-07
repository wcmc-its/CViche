"""
URL Domain Validator

Uses URL domains as deterministic hints for section classification.
Domains like doi.org, pubmed, github.com provide objective signals.

Confidence Tiers:
- Tier 1 (0.8-0.9): Unambiguous domains (e.g., clinicaltrials.gov → M2A/M2B, a clinical trial)
- Tier 2 (0.5-0.7): Strongly suggestive (e.g., doi.org → S1-S9)
- Tier 3 (0.2-0.4): Weak hints (e.g., .edu → multiple possibilities)

Position-Aware: Considers section position in document.
"""

import re
import json
from pathlib import Path
from .base_validator import BaseValidator, ValidatorGuidance


class URLDomainValidator(BaseValidator):
    """
    Validates section assignments based on URL domain patterns.

    Uses URL domains as objective hints. Confidence varies by:
    1. Domain specificity (clinicaltrials.gov > *.edu)
    2. Number of URLs detected (more URLs = higher confidence)
    3. Section position (early vs late in document)
    """

    # URL extraction pattern
    URL_PATTERN = r'https?://[^\s<>"\'\)]+|www\.[^\s<>"\'\)]+|(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s<>"\'\)]*)?'

    def __init__(self, config_path: str | None = None):
        """
        Initialize validator with domain configuration.

        Args:
            config_path: Path to domain mapping JSON. If None, uses default config.
        """
        if config_path is None:
            # Use default config file in same directory
            config_path = Path(__file__).parent / 'url_domain_config.json'

        self.config = self._load_config(config_path)
        self.domain_mappings = self.config.get('domain_mappings', {})

    def _load_config(self, config_path: Path) -> dict:
        """Load domain mapping configuration from JSON."""
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            # Return empty config if file doesn't exist
            return {'domain_mappings': {}}

    def applies_to(self) -> list[str]:
        """
        Applies to all parent sections.

        URL domains can appear in any section, so we check universally.
        """
        return ['*']  # Special marker: applies to all sections

    def priority(self) -> int:
        """
        Low priority - provides hints, doesn't override strong signals.

        Run after high-confidence validators (DOI/PMID, education, etc.)
        but before final LLM classification.
        """
        return 35

    def analyze(self, entry_text: str, section_position: float | None = None) -> ValidatorGuidance:
        """
        Analyze entry for URL domains and provide guidance.

        Args:
            entry_text: Entry content to analyze
            section_position: Optional position ratio (0.0-1.0) where 0.0 is start of CV

        Returns:
            ValidatorGuidance with recommendations based on URL domains
        """
        # Extract all URLs from entry
        urls = self._extract_urls(entry_text)

        if not urls:
            return ValidatorGuidance.no_guidance()

        # Extract domains from URLs
        domains = [self._extract_domain(url) for url in urls]
        domains = [d for d in domains if d]  # Remove None values

        if not domains:
            return ValidatorGuidance.no_guidance()

        # Aggregate signals from all domains
        section_votes: dict[str, list[float]] = {}  # section_id -> list of confidence scores
        all_hints = []
        all_signals = []

        for domain in domains:
            mapping = self._find_domain_mapping(domain)

            if mapping:
                section_id = mapping['section_id']
                base_confidence = mapping['confidence']
                tier = mapping['tier']

                # Handle "multiple" section_id - expand to possible_sections
                if section_id == 'multiple' and 'possible_sections' in mapping:
                    target_sections = mapping['possible_sections']
                else:
                    target_sections = [section_id]

                # Apply position adjustment and record votes
                for target_section in target_sections:
                    adjusted_confidence = self._apply_position_adjustment(
                        base_confidence, tier, target_section, section_position
                    )

                    # Record vote
                    if target_section not in section_votes:
                        section_votes[target_section] = []
                    section_votes[target_section].append(adjusted_confidence)

                # Add hint
                if len(target_sections) == 1:
                    all_hints.append(f"Domain {domain} suggests {target_sections[0]} (confidence: {base_confidence:.2f})")
                else:
                    all_hints.append(f"Domain {domain} suggests multiple sections: {', '.join(target_sections)} (confidence: {base_confidence:.2f})")
                all_signals.append(f"url_domain_{domain}")

        if not section_votes:
            return ValidatorGuidance.no_guidance()

        # Aggregate votes: average confidence per section
        section_scores = {
            section_id: sum(scores) / len(scores)
            for section_id, scores in section_votes.items()
        }

        # Apply multi-URL confidence boost (more URLs = more confident)
        num_urls = len(domains)
        if num_urls >= 3:
            boost = 1.15  # 15% boost for 3+ URLs
        elif num_urls == 2:
            boost = 1.05  # 5% boost for 2 URLs
        else:
            boost = 1.0  # No boost for single URL

        section_scores = {
            section_id: min(0.95, score * boost)  # Cap at 0.95
            for section_id, score in section_scores.items()
        }

        # Sort by confidence
        sorted_sections = sorted(section_scores.items(), key=lambda x: x[1], reverse=True)

        # Recommend top sections (confidence > 0.3)
        recommend_sections = [s for s, conf in sorted_sections if conf > 0.3]

        # Get overall confidence (highest score)
        overall_confidence = sorted_sections[0][1] if sorted_sections else 0.0

        # Build summary hint
        if len(recommend_sections) == 1:
            summary = f"URL domain(s) strongly suggest {recommend_sections[0]}"
        else:
            summary = f"URL domain(s) suggest: {', '.join(recommend_sections[:3])}"

        return ValidatorGuidance(
            exclude_sections=[],  # URL domains never hard-exclude
            recommend_sections=recommend_sections[:3],  # Top 3 recommendations
            hints=[summary] + all_hints[:5],  # Limit hints to avoid prompt bloat
            confidence=overall_confidence,
            severity='soft',  # Always soft guidance
            allow_override=True,  # LLM can always override
            deterministic_signals=list(set(all_signals))[:5]  # Unique signals, limit 5
        )

    def _extract_urls(self, text: str) -> list[str]:
        """
        Extract all URLs from text, excluding email addresses.

        Strategy: Replace email addresses with placeholders before URL extraction,
        then extract URLs from the cleaned text.
        """
        # Email pattern: captures full email addresses
        EMAIL_PATTERN = r'\b[\w.+-]+@[\w.-]+\.[\w]{2,}\b'

        # Replace all email addresses with a placeholder to prevent matching
        text_without_emails = re.sub(EMAIL_PATTERN, '[EMAIL_REMOVED]', text, flags=re.IGNORECASE)

        # Now extract URLs from the cleaned text
        urls = re.findall(self.URL_PATTERN, text_without_emails, re.IGNORECASE)

        # Strip trailing punctuation
        urls = [url.rstrip('.,;:)') for url in urls]

        # Filter out the placeholder if it somehow got through
        urls = [url for url in urls if url != '[EMAIL_REMOVED]' and 'EMAIL_REMOVED' not in url]

        return urls

    def _extract_domain(self, url: str) -> str | None:
        """
        Extract domain from URL.

        Examples:
            https://doi.org/10.1234 → doi.org
            www.github.com/user/repo → github.com
            faculty.medicine.edu/profile → medicine.edu

        Returns:
            Domain string or None if extraction fails
        """
        # Remove protocol
        url = re.sub(r'^https?://', '', url)
        url = re.sub(r'^www\.', '', url)

        # Extract domain (everything before first /)
        domain = url.split('/')[0]

        # Validate domain (has at least one dot and looks domain-like)
        if '.' in domain and re.match(r'^[a-z0-9.-]+\.[a-z]{2,}$', domain, re.IGNORECASE):
            return domain.lower()

        return None

    def _find_domain_mapping(self, domain: str) -> dict | None:
        """
        Find mapping for domain, checking exact match and pattern match.

        Args:
            domain: Domain to look up (e.g., 'faculty.medicine.edu')

        Returns:
            Mapping dict or None if no match
        """
        # Check exact match first
        if domain in self.domain_mappings:
            return self.domain_mappings[domain]

        # Check pattern match (e.g., '*.edu')
        for pattern, mapping in self.domain_mappings.items():
            if self._domain_matches_pattern(domain, pattern):
                return mapping

        return None

    def _domain_matches_pattern(self, domain: str, pattern: str) -> bool:
        """
        Check if domain matches pattern.

        Patterns:
            *.edu → matches any .edu domain
            *.faculty.* → matches any domain with 'faculty' subdomain
            doi.org → exact match only

        Args:
            domain: Domain to check (e.g., 'faculty.medicine.edu')
            pattern: Pattern to match (e.g., '*.edu')

        Returns:
            True if domain matches pattern
        """
        if not '*' in pattern:
            # Exact match only
            return domain == pattern

        # Convert pattern to regex
        regex_pattern = pattern.replace('.', r'\.')
        regex_pattern = regex_pattern.replace('*', r'[a-z0-9.-]+')
        regex_pattern = f'^{regex_pattern}$'

        return bool(re.match(regex_pattern, domain, re.IGNORECASE))

    def _apply_position_adjustment(
        self,
        base_confidence: float,
        tier: int,
        section_id: str,
        section_position: float | None
    ) -> float:
        """
        Apply position-based confidence adjustment.

        Some domains are more/less likely depending on CV position:
        - .edu domains in early CV → more likely A-B (personal/education)
        - .edu domains in late CV → more likely K-N (teaching/mentoring)

        Args:
            base_confidence: Base confidence score
            tier: Tier level (1-3)
            section_id: Target section ID
            section_position: Position ratio (0.0-1.0) or None

        Returns:
            Adjusted confidence score
        """
        if section_position is None:
            return base_confidence

        # Position adjustments for specific section types

        # Personal/Education sections (A, B, C) more likely early in CV
        if section_id in ['A', 'B', 'B1', 'B2', 'C']:
            if section_position < 0.15:  # First 15% of CV
                return min(0.95, base_confidence * 1.2)  # +20% boost
            elif section_position > 0.7:  # Last 30% of CV
                return base_confidence * 0.8  # -20% penalty

        # Bibliography sections (S*) more likely late in CV
        elif section_id.startswith('S'):
            if section_position > 0.6:  # Last 40% of CV
                return min(0.95, base_confidence * 1.15)  # +15% boost
            elif section_position < 0.2:  # First 20% of CV
                return base_confidence * 0.85  # -15% penalty

        # Educational contributions (K*) more common mid-to-late CV
        elif section_id.startswith('K'):
            if 0.3 < section_position < 0.8:  # Middle 50% of CV
                return min(0.95, base_confidence * 1.1)  # +10% boost

        # No adjustment for other sections
        return base_confidence
