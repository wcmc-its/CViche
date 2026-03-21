"""
Leadership vs Committee Membership Validator

Distinguishes:
- O: Institutional leadership (President, Chair, Director, Chief, Dean)
- P: Institutional committees (Member, Representative, Co-Chair)
- Q1: External organization leadership
- I: Professional society membership (simple member/fellow)

Based on ChatGPT feedback - Rule 5 (high confusion risk area)
"""

from typing import Dict, List

try:
    from .base_validator import BaseValidator, ValidatorGuidance
except ImportError:
    from base_validator import BaseValidator, ValidatorGuidance


class LeadershipCommitteeValidator(BaseValidator):
    """Detect leadership authority vs committee membership."""

    def applies_to(self) -> List[str]:
        return [
            'institutional_leadership',  # O
            'institutional_administration',  # P
            'extramural_professional_activities',  # Q
            'professional_orgs_societies'  # I
        ]

    def priority(self) -> int:
        """Higher priority - should run before less specific validators."""
        return 20

    def analyze(self, group: Dict, parent_section: str) -> ValidatorGuidance:
        """Analyze for leadership keywords and context."""

        entries = group.get('entries', [])
        all_text = ' '.join([
            e.get('text', '') or e.get('text_snippet', '')
            for e in entries
        ])
        label = group.get('label', '').lower()

        # Leadership keywords (executive authority)
        leadership_titles = [
            'president', 'chair', 'vice chair', 'vice president',
            'director', 'chief', 'dean', 'head',
            'program director', 'section chief', 'vice dean'
        ]

        # Committee keywords (no executive authority)
        committee_titles = [
            'member', 'representative', 'co-chair',
            'committee member', 'council member',
            'elected member', 'search committee', 'task force'
        ]

        # Simple membership keywords
        membership_only = [
            'fellow', 'member since', 'society member',
            'association member'
        ]

        combined = (all_text + ' ' + label).lower()

        # Count matches
        leadership_count = sum(1 for title in leadership_titles if title in combined)
        committee_count = sum(1 for title in committee_titles if title in combined)
        membership_count = sum(1 for word in membership_only if word in combined)

        # Internal vs external context
        internal_indicators = [
            'department', 'university', 'college', 'school',
            'institutional', 'institution', 'faculty'
        ]
        external_indicators = [
            'society', 'association', 'foundation',
            'international', 'national', 'organization'
        ]

        is_internal = any(ind in combined for ind in internal_indicators)
        is_external = any(ind in combined for ind in external_indicators)

        signals = []
        hints = []

        # INTERNAL LEADERSHIP (O)
        if leadership_count > 0 and is_internal:
            signals.append(
                f"Leadership titles detected: {leadership_count}, "
                f"Context: Internal"
            )
            hints.append(
                "Leadership titles (President, Chair, Director, Chief, Dean) detected in INTERNAL/INSTITUTIONAL context. "
                "These roles have EXECUTIVE AUTHORITY over people, programs, or budgets. "
                "Classify as O (Institutional Leadership), NOT P (Committee). "
                "Examples: 'Program Director', 'Section Chief', 'Department Chair'."
            )
            return ValidatorGuidance(
                exclude_sections=['P'],
                recommend_sections=['O'],
                hints=hints,
                confidence=0.85,
                severity='soft',
                deterministic_signals=signals,
                exclusion_reasons={
                    'P': 'Leadership role with executive authority, not committee participation'
                }
            )

        # EXTERNAL LEADERSHIP (Q1)
        if leadership_count > 0 and is_external:
            signals.append(
                f"Leadership titles detected: {leadership_count}, "
                f"Context: External"
            )
            hints.append(
                "Leadership titles detected in EXTERNAL organization context. "
                "These are officer/leadership roles in professional societies or external organizations. "
                "Classify as Q1 (Extramural Leadership), NOT I (Membership). "
                "Examples: 'President, American Society...', 'Board Member, Foundation...'."
            )
            return ValidatorGuidance(
                exclude_sections=['I'],
                recommend_sections=['Q1'],
                hints=hints,
                confidence=0.85,
                severity='soft',
                deterministic_signals=signals,
                exclusion_reasons={
                    'I': 'Leadership role in external organization, not simple membership'
                }
            )

        # INTERNAL COMMITTEE (P)
        if committee_count > 0 and leadership_count == 0 and is_internal:
            signals.append(
                f"Committee titles detected: {committee_count}, "
                f"No leadership titles, Context: Internal"
            )
            hints.append(
                "Committee membership (Member, Representative, Co-Chair) detected WITHOUT executive authority. "
                "These are participatory roles in institutional committees/councils. "
                "Classify as P (Institutional Administrative Activities), NOT O (Leadership). "
                "Examples: 'Committee Member', 'Faculty Senate Representative', 'Search Committee'."
            )
            return ValidatorGuidance(
                exclude_sections=['O'],
                recommend_sections=['P'],
                hints=hints,
                confidence=0.80,
                severity='soft',
                deterministic_signals=signals,
                exclusion_reasons={
                    'O': 'Committee participation without executive authority'
                }
            )

        # SIMPLE MEMBERSHIP (I)
        if membership_count > 0 and leadership_count == 0 and is_external:
            signals.append(
                f"Membership indicators: {membership_count}, "
                f"No leadership titles, Context: External"
            )
            hints.append(
                "Simple professional society MEMBERSHIP detected (Fellow, Member since...). "
                "No leadership role indicated. "
                "Classify as I (Professional Organizations), NOT Q1 (Leadership). "
                "Examples: 'Member, American Medical Association', 'Fellow, AAAS'."
            )
            return ValidatorGuidance(
                exclude_sections=['Q1'],
                recommend_sections=['I'],
                hints=hints,
                confidence=0.75,
                severity='soft',
                deterministic_signals=signals,
                exclusion_reasons={
                    'Q1': 'Simple membership without leadership role'
                }
            )

        # No clear signal
        return ValidatorGuidance.no_guidance()
