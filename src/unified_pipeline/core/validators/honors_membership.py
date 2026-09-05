"""
Honors/Membership Validator

Refines distinction between H (honors/awards) and I (professional memberships).
Critical for entries that could be either (elected fellowships, society honors).
"""

import re
from .base_validator import BaseValidator, ValidatorGuidance


class HonorsMembershipValidator(BaseValidator):
    """
    Distinguishes honors/awards (H) from professional memberships (I).

    Key distinctions:
    - H (Honors): Competitive, selective, awarded recognition (Fellow, Award Winner)
    - I (Membership): Organizational affiliation, often fee-based or automatic
    - H2 (Elected Fellowships): Honorary membership requiring nomination/election
    - I (Regular Membership): Standard member, dues-paying

    Patterns detected:
    - "Elected Fellow" → H2 (honor) not I (membership)
    - "Fellow" with selection criteria → H2
    - "Member" without selection language → I
    - Award names (Prize, Medal, Laureate) → H
    - Dues/fee language → I
    """

    name = "HonorsMembershipValidator"

    def applies_to(self) -> list[str]:
        """Applies to honors and membership sections."""
        return ['H', 'honors_awards', 'I', 'memberships', 'professional_societies']

    def priority(self) -> int:
        """High priority - critical for H vs I disambiguation."""
        return 85

    # Honor/award indicators (H)
    HONOR_INDICATORS = [
        # Award types
        r'\b(Award|Prize|Medal|Laureate)\b',
        r'\bNamed\s+(?:Lecturer|Lectureship)\b',
        r'\bRecognition\s+Award\b',
        r'\bDistinguished\s+(?:Scientist|Scholar|Investigator|Service)\b',

        # Selection language
        r'\b(?:Elected|Selected|Chosen|Named)\s+(?:Fellow|Member)\b',
        r'\bElected\s+to\b',
        r'\b(?:Competitive|Selective)\s+(?:Award|Honor)\b',
        r'\bNominated\s+(?:for|as)\b',

        # Honorific titles
        r'\bHonorary\s+(?:Fellow|Member|Doctorate)\b',
        r'\bEmeritus\b',

        # Achievement language
        r'\bin\s+recognition\s+of\b',
        r'\bfor\s+(?:outstanding|exceptional|distinguished)\b',
        r'\bachievement\s+award\b',
    ]

    # Elected fellowship patterns (H2)
    ELECTED_FELLOWSHIP_PATTERNS = [
        r'\bFellow\s+of\s+the\s+(?:American|National|Royal|International)',
        r'\b(?:Elected|Named)\s+Fellow\b',
        r'\bFAAP\b',  # Fellow of American Academy of Pediatrics
        r'\bFACP\b',  # Fellow of American College of Physicians
        r'\bFACS\b',  # Fellow of American College of Surgeons
        r'\bFAHA\b',  # Fellow of American Heart Association
        r'\bFAAAS\b',  # Fellow of American Association for the Advancement of Science
    ]

    # Membership indicators (I)
    MEMBERSHIP_INDICATORS = [
        # Basic membership language
        r'\bMember(?:ship)?\s+(?:of|in)\s+the\b',
        r'\bProfessional\s+Member\b',
        r'\bStudent\s+Member\b',
        r'\bAssociate\s+Member\b',

        # Fee/dues language
        r'\b(?:Dues|Fee)(?:-paying)?\b',
        r'\bAnnual\s+(?:Membership|Dues)\b',

        # Automatic/non-selective membership
        r'\bOpen\s+(?:to|membership)\b',
        r'\bVoluntary\s+(?:Member|Association)\b',
    ]

    # Society names (context-dependent)
    HONOR_SOCIETY_PATTERNS = [
        # Honor societies (typically selective)
        r'\bPhi\s+Beta\s+Kappa\b',
        r'\bSigma\s+Xi\b',
        r'\bAlpha\s+Omega\s+Alpha\b',  # Medical honor society
        r'\bGolden\s+Key\b',
    ]

    PROFESSIONAL_SOCIETY_PATTERNS = [
        # Professional societies (typically fee-based membership)
        r'\b(?:American|National|International)\s+(?:Association|Society)\s+(?:of|for)\b',
    ]

    # Anti-patterns
    MEMBERSHIP_ANTI_PATTERNS = [
        # These suggest honor, not membership
        r'\baward(?:ed)?\s+fellow(?:ship)?\b',
        r'\belected\b',
        r'\bselected\b',
        r'\bin\s+recognition\b',
    ]

    def analyze(self, entry_text: str) -> ValidatorGuidance:
        """
        Analyze entry to distinguish honors from memberships.

        Returns:
            Guidance for H vs I classification
        """
        # DEFENSIVE: Handle dict entries (extract text if needed)
        if isinstance(entry_text, dict):
            entry_text = entry_text.get('text_snippet', '')

        # Ensure it's a string
        if not isinstance(entry_text, str):
            entry_text = str(entry_text) if entry_text else ''

        text_lower = entry_text.lower()

        # Check for honor indicators
        has_honor_language = any(
            re.search(pattern, entry_text, re.IGNORECASE)
            for pattern in self.HONOR_INDICATORS
        )

        has_elected_fellowship = any(
            re.search(pattern, entry_text, re.IGNORECASE)
            for pattern in self.ELECTED_FELLOWSHIP_PATTERNS
        )

        # Check for membership indicators
        has_membership_language = any(
            re.search(pattern, entry_text, re.IGNORECASE)
            for pattern in self.MEMBERSHIP_INDICATORS
        )

        # Check for anti-patterns (honor language with "member")
        has_membership_anti_pattern = any(
            re.search(pattern, text_lower, re.IGNORECASE)
            for pattern in self.MEMBERSHIP_ANTI_PATTERNS
        )

        # Check for honor society patterns
        has_honor_society = any(
            re.search(pattern, entry_text, re.IGNORECASE)
            for pattern in self.HONOR_SOCIETY_PATTERNS
        )

        # HARD ROUTING: Elected Fellowship (H2)
        if has_elected_fellowship or (has_honor_language and 'fellow' in text_lower):
            deterministic_signals = []
            hints = []

            if has_elected_fellowship:
                deterministic_signals.append('elected_fellowship')
                hints.append('Elected fellowship (e.g., FACP, FACS) → Honor (H2)')

            if has_honor_language:
                deterministic_signals.append('honor_language_with_fellow')
                hints.append('Selection/recognition language with "Fellow" → H2 not I')

            return ValidatorGuidance(
                exclude_sections=['I'],
                recommend_sections=['H2', 'H'],
                hints=hints + [
                    'Elected fellowships are honors requiring nomination/selection',
                    'Regular memberships (I) use "Member" without selection language'
                ],
                confidence=0.92,
                severity='hard',
                allow_override=True,  # Edge cases exist
                deterministic_signals=deterministic_signals
            )

        # HARD ROUTING: Award/Prize (H1 or H2)
        if has_honor_language and not has_membership_language:
            # Determine H1 vs H2 based on permanence
            permanent_indicators = ['fellow', 'elected', 'honorary', 'emeritus']
            is_permanent = any(term in text_lower for term in permanent_indicators)

            if is_permanent:
                recommended = ['H2']  # Permanent honor
                hints = ['Permanent honor/fellowship → H2']
            else:
                recommended = ['H1', 'H']  # Award/prize
                hints = ['Award/prize → H1']

            return ValidatorGuidance(
                exclude_sections=['I'],
                recommend_sections=recommended,
                hints=hints + [
                    'Recognition language indicates honor (H), not membership (I)',
                    'Memberships (I) typically use "Member" without selection criteria'
                ],
                confidence=0.88,
                severity='hard',
                deterministic_signals=['award_recognition_language']
            )

        # HARD ROUTING: Honor society membership (H3)
        if has_honor_society:
            return ValidatorGuidance(
                exclude_sections=['I'],
                recommend_sections=['H3', 'H'],
                hints=[
                    'Honor society (Phi Beta Kappa, Sigma Xi, etc.) → H3',
                    'These are selective academic honors, not professional memberships (I)'
                ],
                confidence=0.95,
                severity='hard',
                deterministic_signals=['honor_society_name']
            )

        # SOFT GUIDANCE: "Member" with anti-patterns (suggests honor despite "member" language)
        if has_membership_anti_pattern and 'member' in text_lower:
            return ValidatorGuidance(
                recommend_sections=['H2', 'H'],
                hints=[
                    'Contains "Member" but with selection/award language → likely honor (H)',
                    'Example: "Elected Member" or "Awarded membership" → H2',
                    'Regular membership without selection → I'
                ],
                confidence=0.75,
                severity='soft',
                deterministic_signals=['member_with_honor_language']
            )

        # SOFT GUIDANCE: Generic "Member" without selection language → I
        if has_membership_language and not has_honor_language and not has_membership_anti_pattern:
            return ValidatorGuidance(
                recommend_sections=['I'],
                exclude_sections=['H', 'H1', 'H2'],
                hints=[
                    'Membership language without selection criteria → Professional membership (I)',
                    'Honors (H) typically require nomination, election, or competitive selection'
                ],
                confidence=0.80,
                severity='soft',
                deterministic_signals=['membership_no_honor_language']
            )

        # SOFT GUIDANCE: Professional society (ambiguous)
        society_match = any(
            re.search(pattern, entry_text, re.IGNORECASE)
            for pattern in self.PROFESSIONAL_SOCIETY_PATTERNS
        )

        if society_match and not has_honor_language and not has_elected_fellowship:
            return ValidatorGuidance(
                recommend_sections=['I'],
                hints=[
                    'Professional society membership → likely I',
                    'If fellowship or award, would have selection language (H)'
                ],
                confidence=0.65,
                severity='soft',
                deterministic_signals=['professional_society_generic']
            )

        # No clear signals
        return ValidatorGuidance()

    def get_description(self) -> str:
        return "Distinguishes honors/awards (H) from professional memberships (I), especially elected fellowships"
