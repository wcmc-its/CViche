"""
Fellowship Classifier Validator

Handles cross-taxonomy confusion for "fellowship" content that can appear in:
- C: Postdoctoral Training (fellowship training positions)
- H: Honors & Awards (fellowship awards/honors like "Smith Fellow")
- M2: Research Funding (fellowship grants with funding)
- B: Education (educational fellowship programs attended)
- I: Professional Societies (fellowship membership status like "FACS")

Based on Haendel CV analysis showing 0.85 confidence is correct <50% of time.
"""

import re
from typing import Dict, List

try:
    from .base_validator import BaseValidator, ValidatorGuidance
except ImportError:
    from base_validator import BaseValidator, ValidatorGuidance


class FellowshipValidator(BaseValidator):
    """
    Detect fellowship types and provide routing guidance.

    Fellowship types:
    - Training positions (with dates, institution, role) → C
    - Named awards/honors (honorary title) → H
    - Grant funding (with dollar amounts, grant IDs) → M2
    - Educational programs (courses/workshops attended) → B
    - Society membership (professional designation) → I
    """

    # Training position indicators
    TRAINING_PATTERNS = [
        r'\d{4}\s*[-–]\s*\d{4}',  # Date ranges: 2015-2018
        r'\d{4}\s*[-–]\s*present',  # 2018-present
        r'postdoctoral\s+fellow',
        r'research\s+fellow',
        r'clinical\s+fellow',
    ]

    # Award/honor indicators
    AWARD_PATTERNS = [
        r'[A-Z][a-z]+\s+[A-Z][a-z]+\s+Fellow(?:ship)?',  # Named Fellowship: "Jane Doe Fellowship"
        r'Fellow(?:ship)?\s+in\s+[A-Z]',  # Fellowship in Cancer Research
        r'recipient',
        r'awardee',
        r'awarded',
        r'received',
    ]

    # Grant funding indicators
    GRANT_PATTERNS = [
        r'\$[\d,]+',  # Dollar amounts
        r'T32|K99|F31|F32|R00|R01',  # NIH training/career grants
        r'grant\s+number',
        r'award\s+number',
        r'funded\s+by',
        r'NIH|NSF|DoD',
    ]

    # Educational program indicators
    EDUCATION_PATTERNS = [
        r'course',
        r'workshop',
        r'seminar',
        r'certificate',
        r'program',
        r'training\s+in',
    ]

    # Society membership indicators
    SOCIETY_PATTERNS = [
        r'\bFACS\b',  # Fellow, American College of Surgeons
        r'\bFACP\b',  # Fellow, American College of Physicians
        r'\bFAHA\b',  # Fellow, American Heart Association
        r'Fellow,\s+[A-Z]',  # Fellow, American Society of...
        r'elected\s+fellow',
        r'inducted',
    ]

    def applies_to(self) -> list[str]:
        """
        Applies to multiple parent sections where fellowships can appear.
        """
        return [
            'postdoctoral_training',  # C
            'honors_and_awards',  # H
            'research_overview',  # M (for M2)
            'education_and_training',  # B
            'professional_orgs_societies'  # I
        ]

    def priority(self) -> int:
        """Medium-high priority - should run before generic classification."""
        return 12

    def analyze(self, group: dict, parent_section: str) -> ValidatorGuidance:
        """
        Analyze fellowship content and provide routing guidance.

        Args:
            group: Group data with entries and label
            parent_section: Parent section ID

        Returns:
            ValidatorGuidance with recommendations
        """
        label = group.get('label', '').lower()
        entries = group.get('entries', [])

        # Only trigger if "fellowship" is mentioned
        if 'fellow' not in label:
            return ValidatorGuidance.no_guidance()

        # Collect all entry text
        all_text = ' '.join([
            e.get('text', '') or e.get('text_snippet', '')
            for e in entries
        ])

        combined = all_text + ' ' + label

        # Score each fellowship type
        training_score = self._score_training_position(combined)
        award_score = self._score_award_honor(combined)
        grant_score = self._score_grant_funding(combined)
        education_score = self._score_education_program(combined)
        society_score = self._score_society_membership(combined)

        # Determine dominant type
        scores = {
            'training': training_score,
            'award': award_score,
            'grant': grant_score,
            'education': education_score,
            'society': society_score
        }

        max_score = max(scores.values())

        # Require minimum threshold to make a recommendation
        if max_score < 2:
            return ValidatorGuidance.no_guidance()

        # Get dominant type
        dominant_type = max(scores.items(), key=lambda x: x[1])[0]

        # Build guidance based on dominant type
        if dominant_type == 'training':
            return ValidatorGuidance(
                exclude_sections=['H', 'M2', 'B', 'I'],
                recommend_sections=['C'],
                hints=[
                    f"Training position pattern detected (score: {training_score})",
                    "Fellowship appears to be a POSTDOCTORAL TRAINING POSITION with dates/institution.",
                    "Classify as C (Postdoctoral Training), NOT H (awards), M2 (grants), B (education), or I (membership).",
                    "Pattern: 'Postdoctoral Fellow, [Institution], [Dates]' → C"
                ],
                confidence=0.85,
                severity='soft',
                deterministic_signals=[
                    f"Training indicators: dates, institution, fellow role (score={training_score})"
                ],
                exclusion_reasons={
                    'H': 'Training position, not honorary award',
                    'M2': 'Training position, not grant funding',
                    'B': 'Postdoctoral training (separate section), not pre-doctoral education',
                    'I': 'Training position, not membership status'
                }
            )

        elif dominant_type == 'award':
            return ValidatorGuidance(
                exclude_sections=['C', 'M2', 'B', 'I'],
                recommend_sections=['H'],
                hints=[
                    f"Award/honor pattern detected (score: {award_score})",
                    "Fellowship appears to be a NAMED AWARD or HONORARY TITLE.",
                    "Classify as H (Honors & Awards), NOT C (training), M2 (grants), B (education), or I (membership).",
                    "Pattern: '[Named] Fellowship' or 'Fellowship Recipient' → H"
                ],
                confidence=0.85,
                severity='soft',
                deterministic_signals=[
                    f"Award indicators: named fellowship, recipient, awarded (score={award_score})"
                ],
                exclusion_reasons={
                    'C': 'Honorary award, not training position',
                    'M2': 'Award/honor, not grant funding',
                    'B': 'Award/honor, not education received',
                    'I': 'Award/honor, not membership status'
                }
            )

        elif dominant_type == 'grant':
            return ValidatorGuidance(
                exclude_sections=['C', 'H', 'B', 'I'],
                recommend_sections=['M2'],
                hints=[
                    f"Grant funding pattern detected (score: {grant_score})",
                    "Fellowship appears to be GRANT FUNDING with dollar amounts or grant IDs.",
                    "Classify as M2 (Research Funding), NOT C (training), H (awards), B (education), or I (membership).",
                    "Pattern: 'Fellowship [Grant ID]' or '[Amount] Fellowship' → M2"
                ],
                confidence=0.85,
                severity='soft',
                deterministic_signals=[
                    f"Grant indicators: dollar amounts, grant IDs, funding agencies (score={grant_score})"
                ],
                exclusion_reasons={
                    'C': 'Grant funding, not training position',
                    'H': 'Grant funding, not honorary award',
                    'B': 'Grant funding, not education received',
                    'I': 'Grant funding, not membership status'
                }
            )

        elif dominant_type == 'education':
            return ValidatorGuidance(
                exclude_sections=['C', 'H', 'M2', 'I'],
                recommend_sections=['B'],
                hints=[
                    f"Educational program pattern detected (score: {education_score})",
                    "Fellowship appears to be an EDUCATIONAL PROGRAM or COURSE attended.",
                    "Classify as B (Education and Training), NOT C (postdoc), H (awards), M2 (grants), or I (membership).",
                    "Pattern: 'Fellowship Course/Workshop' → B"
                ],
                confidence=0.80,
                severity='soft',
                deterministic_signals=[
                    f"Education indicators: course, workshop, seminar, program (score={education_score})"
                ],
                exclusion_reasons={
                    'C': 'Educational program, not postdoctoral training',
                    'H': 'Educational program, not award',
                    'M2': 'Educational program, not grant funding',
                    'I': 'Educational program, not membership'
                }
            )

        elif dominant_type == 'society':
            return ValidatorGuidance(
                exclude_sections=['C', 'H', 'M2', 'B'],
                recommend_sections=['I'],
                hints=[
                    f"Society membership pattern detected (score: {society_score})",
                    "Fellowship appears to be PROFESSIONAL SOCIETY MEMBERSHIP designation.",
                    "Classify as I (Professional Organizations), NOT C (training), H (awards), M2 (grants), or B (education).",
                    "Pattern: 'Fellow, [Society Name]' or 'FACS/FACP/FAHA' → I"
                ],
                confidence=0.90,
                severity='soft',
                deterministic_signals=[
                    f"Society indicators: FACS, FACP, elected fellow, inducted (score={society_score})"
                ],
                exclusion_reasons={
                    'C': 'Society membership, not training position',
                    'H': 'Society membership (may overlap with H, but primary classification is I)',
                    'M2': 'Society membership, not grant funding',
                    'B': 'Society membership, not education'
                }
            )

        return ValidatorGuidance.no_guidance()

    def _score_training_position(self, text: str) -> int:
        """Score likelihood this is a training position."""
        score = 0

        for pattern in self.TRAINING_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                score += 1

        # Institutions boost score
        institutions = ['university', 'college', 'hospital', 'institute', 'center']
        if any(inst in text.lower() for inst in institutions):
            score += 1

        # Department/division indicates training
        if any(word in text.lower() for word in ['department', 'division', 'lab']):
            score += 1

        return score

    def _score_award_honor(self, text: str) -> int:
        """Score likelihood this is an award/honor."""
        score = 0

        for pattern in self.AWARD_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                score += 1

        # Named fellowships often have proper names
        if re.search(r'[A-Z][a-z]+\s+[A-Z][a-z]+\s+Fellow', text):
            score += 2

        return score

    def _score_grant_funding(self, text: str) -> int:
        """Score likelihood this is grant funding."""
        score = 0

        for pattern in self.GRANT_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                score += 1

        # Multiple dollar amounts strongly indicate grants
        if len(re.findall(r'\$[\d,]+', text)) >= 2:
            score += 2

        return score

    def _score_education_program(self, text: str) -> int:
        """Score likelihood this is an educational program."""
        score = 0

        for pattern in self.EDUCATION_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                score += 1

        return score

    def _score_society_membership(self, text: str) -> int:
        """Score likelihood this is society membership."""
        score = 0

        for pattern in self.SOCIETY_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                score += 1

        # Abbreviations like FACS are strong signals
        if re.search(r'\bF[A-Z]{2,4}\b', text):
            score += 2

        return score
