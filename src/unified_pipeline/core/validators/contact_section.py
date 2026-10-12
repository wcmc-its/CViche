"""
Contact/Personal Data Section Validator

Distinguishes true contact/personal data sections (section A) from:
- Research resources/products (URLs to databases, tools, consortia)
- Late-document "contact" sections (actually funding/collaboration info)

Principle: CVs have AT MOST ONE contact section, always near the top.

Uses positional awareness + content signals to identify the true contact block
and prevent research resources from being misclassified.
"""

import re

from .base_validator import BaseValidator, ValidatorGuidance


class ContactSectionValidator(BaseValidator):
    """
    Identifies the true Personal Data / Contact section (WCM section A).

    Key insight: Contact info is ALWAYS near the document header and there's
    only ONE such section. Everything else is likely research resources,
    affiliations, or other content.
    """

    name = "ContactSectionValidator"

    def applies_to(self) -> list[str]:
        """Helps identify section A (Personal Data)."""
        return ['personal_data', 'research', 'professional_affiliations']

    def priority(self) -> int:
        """High priority - runs early to exclude contact from other classifications."""
        return 90

    def analyze(self, entry_text: str) -> ValidatorGuidance | None:
        """Per-entry analysis not used - this validator works at section level."""
        return None

    # =========================================================================
    # DETECTION PATTERNS
    # =========================================================================

    EMAIL_PATTERN = re.compile(
        r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    )

    PHONE_PATTERN = re.compile(
        r'\b(?:\+?1[-.]?)?\(?[0-9]{3}\)?[-.\s]?[0-9]{3}[-.\s]?[0-9]{4}\b|'
        r'\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b'
    )

    ORCID_PATTERN = re.compile(
        r'\b(?:orcid[:\s]+)?0000-\d{4}-\d{4}-\d{3}[0-9X]\b',
        re.I
    )

    # Personal profile URLs
    PERSONAL_URL_PATTERNS = [
        r'linkedin\.com/in/',
        r'twitter\.com/',
        r'github\.com/[^/]+/?$',  # User page, not repo
        r'~[\w]+/?$',  # Personal tilde pages
        r'/people/',
        r'/faculty/',
        r'/profile/',
    ]

    # Research resource URLs
    RESOURCE_URL_PATTERNS = [
        r'github\.com/[^/]+/[^/]+',  # Repo (not just user page)
        r'\.org/(?:project|data|resource|database|tool|software)',
        r'monarchinitiative',
        r'bioontology',
        r'obofoundry',
        r'ncbi\.nlm\.nih\.gov',
        r'clinicaltrials\.gov',
    ]

    # Research nouns (suggest not contact)
    RESEARCH_NOUNS = [
        'collaborative', 'initiative', 'network', 'platform',
        'data', 'resource', 'database', 'ontology', 'tool',
        'project', 'center', 'program', 'consortium', 'repository',
        'software', 'package', 'library', 'service',
    ]

    # Contact nouns (suggest contact section)
    CONTACT_NOUNS = [
        'email', 'phone', 'address', 'contact', 'website',
        'home page', 'homepage', 'office', 'orcid',
    ]

    def analyze_all_sections(
        self,
        sections: list[dict]
    ) -> list[ValidatorGuidance]:
        """
        Analyze all sections to find the ONE true contact section.

        Returns list of guidance (one per section that needs it).
        """
        if not sections:
            return []

        # Step 0: Find education section index for positional context
        edu_idx = self._find_education_index(sections)
        n_sections = len(sections)

        # Step 1: Extract features and compute scores for each section
        for idx, section in enumerate(sections):
            section['_idx'] = idx
            section['_n_sections'] = n_sections

            # Positional features
            if edu_idx == -1:
                # No education found, use first 10%
                section['_is_before_education'] = idx <= int(0.10 * n_sections)
            else:
                section['_is_before_education'] = idx < edu_idx

            section['_is_near_header'] = (idx <= 1) or (idx <= int(0.10 * n_sections))

            # Content features
            features = self._extract_features(section)
            section['_features'] = features

            # Compute contact score
            score = self._compute_contact_score(section, features)
            section['_contact_score'] = score

        # Step 2: Find THE contact section (at most one, must be near header)
        candidates = [
            s for s in sections
            if s['_contact_score'] >= 2 and s['_is_near_header']
        ]

        chosen_contact = None
        if candidates:
            # Pick highest scoring candidate
            chosen_contact = max(candidates, key=lambda s: s['_contact_score'])

        # Step 3: Generate guidance for each section
        guidance_list = []

        for section in sections:
            if section is chosen_contact:
                # This is THE contact section
                guidance = ValidatorGuidance(
                    recommend_sections=['personal_data'],
                    exclude_sections=[],
                    hints=[
                        f"✓ CONTACT SECTION IDENTIFIED (score: {section['_contact_score']})",
                        f"  Position: Index {section['_idx']}, near header",
                        f"  Signals: {self._format_signals(section['_features'])}",
                        "",
                        "This should be classified as A (Personal Data)."
                    ],
                    confidence=0.95,
                    deterministic_signals=[self.name]
                )
                guidance_list.append((section.get('id'), guidance))

            elif section['_contact_score'] >= 2:
                # False positive: looks like contact but isn't (probably research resources)
                alt_section = self._identify_alternative_section(section)

                guidance = ValidatorGuidance(
                    recommend_sections=[alt_section] if alt_section else [],
                    exclude_sections=['personal_data'],
                    hints=[
                        f"⚠️ NOT CONTACT SECTION (score: {section['_contact_score']}, but excluded)",
                        f"  Reason: Another section chosen as contact, or position too late",
                        f"  Position: Index {section['_idx']}, {'' if section['_is_before_education'] else 'NOT '}before education",
                        "",
                        f"Likely: {alt_section or 'other content'} (research resources/products/affiliations)",
                        "Exclude from A (Personal Data)."
                    ],
                    confidence=0.90,
                    deterministic_signals=[self.name]
                )
                guidance_list.append((section.get('id'), guidance))

        return guidance_list

    def _find_education_index(self, sections: list[dict]) -> int:
        """Find index of education section, or -1 if not found."""
        education_keywords = ['education', 'training', 'qualifications', 'degrees']

        for idx, section in enumerate(sections):
            label = section.get('label_inferred', '') or section.get('label', '')
            label_lower = label.lower()

            if any(kw in label_lower for kw in education_keywords):
                return idx

        return -1

    def _extract_features(self, section: dict) -> dict:
        """Extract content features from section."""
        entries = section.get('entries', [])
        label = section.get('label_inferred', '') or section.get('label', '')

        # Combine all text
        all_text = ' '.join([e.get('text_snippet', '') for e in entries]) + ' ' + label
        all_text_lower = all_text.lower()

        features = {
            'has_email': bool(self.EMAIL_PATTERN.search(all_text)),
            'has_phone': bool(self.PHONE_PATTERN.search(all_text)),
            'has_orcid': bool(self.ORCID_PATTERN.search(all_text)),
            'entry_count': len(entries),
            'url_count': all_text.count('http'),
            'personal_url_count': sum(
                1 for pattern in self.PERSONAL_URL_PATTERNS
                if re.search(pattern, all_text, re.I)
            ),
            'resource_url_count': sum(
                1 for pattern in self.RESOURCE_URL_PATTERNS
                if re.search(pattern, all_text, re.I)
            ),
            'research_noun_count': sum(
                1 for noun in self.RESEARCH_NOUNS
                if noun in all_text_lower
            ),
            'contact_noun_count': sum(
                1 for noun in self.CONTACT_NOUNS
                if noun in all_text_lower
            ),
        }

        return features

    def _compute_contact_score(self, section: dict, features: dict) -> int:
        """Compute contact likelihood score."""
        score = 0

        # Positive signals
        if features['has_email']:
            score += 3
        if features['has_phone']:
            score += 2
        if features['has_orcid']:
            score += 2

        score += min(2, features['personal_url_count'])
        score += min(2, features['contact_noun_count'])

        if section['_is_near_header']:
            score += 1

        # Negative signals (inconsistent with contact)
        if features['entry_count'] >= 8:  # Long lists suggest resources, not contact
            score -= 2

        score -= min(2, features['resource_url_count'])
        score -= min(2, features['research_noun_count'])

        # Positional penalty: contact late in document is very unlikely
        if not section['_is_before_education']:
            score -= 3

        return score

    def _format_signals(self, features: dict) -> str:
        """Format feature signals for display."""
        signals = []
        if features['has_email']:
            signals.append("email")
        if features['has_phone']:
            signals.append("phone")
        if features['has_orcid']:
            signals.append("ORCID")
        if features['personal_url_count'] > 0:
            signals.append(f"{features['personal_url_count']} personal URLs")
        if features['contact_noun_count'] > 0:
            signals.append(f"{features['contact_noun_count']} contact nouns")

        return ", ".join(signals) if signals else "none"

    def _identify_alternative_section(self, section: dict) -> str | None:
        """Identify what this false-positive contact section actually is."""
        label = section.get('label_inferred', '') or section.get('label', '')
        label_lower = label.lower()
        features = section['_features']

        # Check label for research product keywords
        research_product_keywords = [
            'contributions', 'databases', 'resources', 'projects',
            'software', 'tools', 'products', 'data', 'infrastructure',
            'platforms', 'services', 'repositories'
        ]

        if any(kw in label_lower for kw in research_product_keywords):
            return 'research'  # Research products/resources

        # Check content features
        if features['resource_url_count'] >= 2 or features['research_noun_count'] >= 2:
            return 'research'  # Research resources

        if 'affiliation' in label_lower or 'membership' in label_lower:
            return 'professional_affiliations'

        return None  # Unknown, let LLM decide
