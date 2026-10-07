"""
Postdoc Position Validator

Disambiguates C (own postdoc training) from D (faculty positions) from N (mentee records).
Critical for entries like "Research Fellow" or "Postdoctoral Fellow" that could be any of these.
"""

import re
from .base_validator import BaseValidator, ValidatorGuidance


class PostdocPositionValidator(BaseValidator):
    """
    Disambiguates postdoctoral/fellowship entries across C/D/N sections.

    Key distinctions:
    - C (own training): "Postdoctoral Fellow" in own training history
    - D (position): "Director of Postdoctoral Training" or later-career fellowship
    - N (mentoring): "Postdoctoral Fellow: John Smith" (mentee's name)

    Uses section headers, temporal context, and structural patterns.
    """

    name = "PostdocPositionValidator"

    def applies_to(self) -> list[str]:
        """Applies to training, positions, and mentoring sections."""
        return ['C', 'postdoc', 'D', 'positions', 'N', 'mentoring']

    def priority(self) -> int:
        """High priority - critical for C/D/N disambiguation."""
        return 85

    # Own training indicators (C)
    OWN_TRAINING_PATTERNS = [
        r'\bPostdoctoral\s+Fellow(?:\s+in)?\b',
        r'\bResearch\s+Fellow(?:\s+in)?\b',
        r'\bClinical\s+Fellow\b',
        r'\bPostdoc(?:toral)?\s+Training\b',
        r'\bFellowship\s+Training\b',
        r'\bPostdoc(?:toral)?\s+Research\s+Associate\b',
    ]

    # Position/leadership indicators (D)
    POSITION_PATTERNS = [
        r'\bDirector\s+of\s+Postdoc',
        r'\bPostdoc(?:toral)?\s+Program\s+Director\b',
        r'\bChief\s+Fellow\b',
        r'\bSenior\s+Fellow\b',
        r'\bStaff\s+Fellow\b',
        r'\bFaculty\b',
        r'\bProfessor\b',
        r'\bAssistant\s+Professor\b',
        r'\bAssociate\s+Professor\b',
    ]

    # Mentee indicators (N)
    MENTEE_PATTERNS = [
        r'\bMentee:\s*[A-Z][a-z]+\s+[A-Z]',  # "Mentee: John Smith"
        r'\bFellow:\s*[A-Z][a-z]+\s+[A-Z]',  # "Fellow: Jane Doe"
        r'\b(?:Postdoc|Fellow):\s*[A-Z][a-z]+',  # "Postdoc: Alice..."
        r'\b[A-Z][a-z]+\s+[A-Z][a-z]+,?\s+(?:MD|PhD|PharmD)\b',  # "John Smith, PhD"
        r'\bSupervised:\s*[A-Z]',
        r'\bAdvisee:\s*[A-Z]',
    ]

    # Temporal context patterns
    EARLY_CAREER_YEAR_RANGE = range(1990, 2030)  # Adjust based on current year

    def analyze(self, entry_text: str, header: str | None = None, year_range: tuple | None = None) -> ValidatorGuidance:
        """
        Analyze entry for postdoc/fellowship classification.

        Args:
            entry_text: Entry text
            header: Section header (from group metadata)
            year_range: (start_year, end_year) if available

        Returns:
            Guidance for C/D/N classification
        """
        # DEFENSIVE: Handle dict entries (extract text if needed)
        if isinstance(entry_text, dict):
            entry_text = entry_text.get('text_snippet', '')

        # Ensure it's a string
        if not isinstance(entry_text, str):
            entry_text = str(entry_text) if entry_text else ''

        text_lower = entry_text.lower()

        # Check for mentee patterns (strongest signal for N)
        has_mentee_pattern = any(
            re.search(pattern, entry_text, re.IGNORECASE)
            for pattern in self.MENTEE_PATTERNS
        )

        # Check for position/leadership patterns (strong signal for D)
        has_position_pattern = any(
            re.search(pattern, entry_text, re.IGNORECASE)
            for pattern in self.POSITION_PATTERNS
        )

        # Check for own training patterns
        has_training_pattern = any(
            re.search(pattern, entry_text, re.IGNORECASE)
            for pattern in self.OWN_TRAINING_PATTERNS
        )

        # HARD ROUTING: Mentee name detected
        if has_mentee_pattern:
            return ValidatorGuidance(
                exclude_sections=['C', 'C1', 'D', 'D1'],
                recommend_sections=['N'],
                hints=[
                    'Entry contains mentee name or "Mentee:" prefix → Mentoring (N)',
                    'Not own training (C) or position (D)'
                ],
                confidence=0.95,
                severity='hard',
                allow_override=False,
                deterministic_signals=['mentee_name_pattern']
            )

        # HARD ROUTING: Leadership/directorial role
        if has_position_pattern:
            return ValidatorGuidance(
                exclude_sections=['C', 'C1', 'N'],
                recommend_sections=['D', 'D1'],
                hints=[
                    'Entry contains leadership title (Director, Chief) → Position (D)',
                    'Not own training (C) or mentoring (N)'
                ],
                confidence=0.90,
                severity='hard',
                allow_override=True,  # Edge cases exist
                deterministic_signals=['leadership_position']
            )

        # SECTION HEADER CONTEXT
        if header:
            header_lower = header.lower()

            # "MENTEES" or "ADVISEES" section → bias toward N
            if any(term in header_lower for term in ['mentee', 'advisee', 'trainee', 'supervised']):
                return ValidatorGuidance(
                    recommend_sections=['N'],
                    exclude_sections=['C', 'D'],
                    hints=[
                        f'Section header "{header}" indicates mentoring → N',
                        'Own training (C) would be in Education/Training section'
                    ],
                    confidence=0.85,
                    severity='hard',
                    deterministic_signals=['mentoring_header_context']
                )

            # "POSTDOCTORAL TRAINING" or "FELLOWSHIP" section → bias toward C
            if any(term in header_lower for term in ['postdoctoral training', 'fellowship training', 'education']):
                if has_training_pattern:
                    return ValidatorGuidance(
                        recommend_sections=['C', 'C1'],
                        exclude_sections=['D', 'N'],
                        hints=[
                            f'Section header "{header}" + training pattern → Own training (C)',
                            'Not a position (D) or mentoring (N)'
                        ],
                        confidence=0.88,
                        severity='hard',
                        deterministic_signals=['training_header_with_pattern']
                    )

            # "APPOINTMENTS" or "POSITIONS" section → bias toward D
            if any(term in header_lower for term in ['appointment', 'position', 'employment', 'academic']):
                return ValidatorGuidance(
                    recommend_sections=['D', 'D1'],
                    exclude_sections=['C', 'N'],
                    hints=[
                        f'Section header "{header}" indicates positions → D',
                        'Training (C) would be in separate section'
                    ],
                    confidence=0.80,
                    severity='soft',
                    deterministic_signals=['position_header_context']
                )

        # TEMPORAL CONTEXT (if year_range provided)
        if year_range and has_training_pattern:
            start_year, end_year = year_range
            duration = end_year - start_year if end_year else 0

            # Short duration (1-3 years) typical of postdoc training
            if 1 <= duration <= 3:
                return ValidatorGuidance(
                    recommend_sections=['C', 'C1'],
                    hints=[
                        f'Short duration ({duration} years) + training pattern → Postdoc training (C)',
                        'Typical postdoc duration is 2-3 years'
                    ],
                    confidence=0.75,
                    severity='soft',
                    deterministic_signals=['short_duration_training']
                )

            # Long duration (5+ years) suggests position, not training
            elif duration >= 5:
                return ValidatorGuidance(
                    recommend_sections=['D', 'D1'],
                    exclude_sections=['C'],
                    hints=[
                        f'Long duration ({duration} years) suggests position (D), not training',
                        'Postdoc training typically 2-3 years'
                    ],
                    confidence=0.70,
                    severity='soft',
                    deterministic_signals=['long_duration_position']
                )

        # SOFT GUIDANCE: Training pattern detected, no strong context
        if has_training_pattern and not has_position_pattern:
            return ValidatorGuidance(
                recommend_sections=['C', 'C1'],
                hints=[
                    'Training language detected → likely own postdoc (C)',
                    'Check section context and duration for confirmation'
                ],
                confidence=0.65,
                severity='soft',
                deterministic_signals=['training_pattern_only']
            )

        # No clear signals
        return ValidatorGuidance()

    def get_description(self) -> str:
        return "Disambiguates postdoctoral/fellowship entries across C (own training), D (positions), and N (mentoring)"
