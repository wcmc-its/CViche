"""
Temporal Pattern Validator

Uses date patterns and durations to disambiguate classification.
Critical for distinguishing one-time events from ongoing activities.
"""

import re
from datetime import datetime
from typing import List, Optional, Tuple
from .base_validator import BaseValidator, ValidatorGuidance


class TemporalPatternValidator(BaseValidator):
    """
    Analyzes temporal patterns in entries to guide classification.

    Key patterns:
    - Single year (2019) → one-time event (H honor, B degree)
    - Range with "present" (2019-present) → ongoing (I membership, D position)
    - Short duration (3-6 months) → training/fellowship (C)
    - Long duration (10+ years) → career position (D) or sustained membership (I)
    - Multiple discrete years (2015, 2018, 2020) → recurring honors (H)

    Critical for:
    - H vs I: Honor (one-time) vs Membership (ongoing)
    - C vs D: Training (short-term) vs Position (long-term)
    - M2 vs H: Grant period (multi-year) vs Award (one-time)
    """

    name = "TemporalPatternValidator"

    def applies_to(self) -> List[str]:
        """Universal validator - temporal context useful everywhere."""
        return ['*']

    def priority(self) -> int:
        """Medium priority - provides context, doesn't override strong signals."""
        return 60

    # Date range patterns
    ONGOING_PATTERNS = [
        r'\b(\d{4})\s*[-–—]\s*(?:present|current|ongoing)\b',
        r'\b(\d{4})\s*[-–—]\s*$',  # Open-ended range
    ]

    CLOSED_RANGE_PATTERNS = [
        r'\b(19|20)(\d{2})\s*[-–—]\s*(19|20)?(\d{2})\b',  # 2015-2020 or 2015-20
    ]

    SINGLE_YEAR_PATTERNS = [
        r'\b(19|20)\d{2}\b(?!\s*[-–—])',  # Year not followed by dash
    ]

    MONTH_YEAR_PATTERNS = [
        r'\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(19|20)\d{2}\b',
    ]

    def extract_year_range(self, entry_text: str) -> Optional[Tuple[int, Optional[int]]]:
        """
        Extract year range from entry.

        Returns:
            (start_year, end_year) or None
            end_year is None for ongoing activities
        """
        # Check for ongoing patterns
        for pattern in self.ONGOING_PATTERNS:
            match = re.search(pattern, entry_text, re.IGNORECASE)
            if match:
                start_year = int(match.group(1))
                return (start_year, None)  # Ongoing

        # Check for closed ranges
        for pattern in self.CLOSED_RANGE_PATTERNS:
            match = re.search(pattern, entry_text)
            if match:
                # Handle formats like "2015-2020" or "2015-20"
                start_year = int(match.group(1) + match.group(2))
                end_year_str = match.group(4)

                # Handle abbreviated year (2015-20 → 2015-2020)
                if match.group(3):  # Full century provided
                    end_year = int(match.group(3) + end_year_str)
                else:  # Abbreviated year
                    # Use same century as start year
                    century = match.group(1)
                    end_year = int(century + end_year_str)

                return (start_year, end_year)

        # Check for single year
        match = re.search(self.SINGLE_YEAR_PATTERNS[0], entry_text)
        if match:
            year = int(match.group(0))
            return (year, year)  # Single year represented as start=end

        return None

    def calculate_duration(self, year_range: Tuple[int, Optional[int]]) -> Optional[int]:
        """
        Calculate duration in years.

        Args:
            year_range: (start_year, end_year) where end_year=None means ongoing

        Returns:
            Duration in years, or None for single-year events
        """
        start_year, end_year = year_range

        if end_year is None:
            # Ongoing - calculate from start to current year
            current_year = datetime.now().year
            return current_year - start_year

        if start_year == end_year:
            # Single year event
            return 0

        return end_year - start_year

    def analyze(self, entry_text: str) -> ValidatorGuidance:
        """
        Analyze entry for temporal patterns.

        Returns:
            Guidance based on temporal context
        """
        # DEFENSIVE: Handle dict entries (extract text if needed)
        if isinstance(entry_text, dict):
            entry_text = entry_text.get('text_snippet', '')

        # Ensure it's a string
        if not isinstance(entry_text, str):
            entry_text = str(entry_text) if entry_text else ''

        year_range = self.extract_year_range(entry_text)

        if not year_range:
            # No clear temporal pattern
            return ValidatorGuidance()

        start_year, end_year = year_range
        duration = self.calculate_duration(year_range)

        # PATTERN 1: Ongoing activity (no end year)
        if end_year is None:
            return ValidatorGuidance(
                recommend_sections=['I', 'D', 'D1'],  # Membership, Position
                exclude_sections=['H', 'H1', 'H2'],  # Not one-time honor
                hints=[
                    f'Ongoing activity ({start_year}-present) suggests membership (I) or position (D)',
                    'One-time honors (H) have discrete years',
                    f'Duration so far: {duration} years'
                ],
                confidence=0.75,
                severity='soft',
                deterministic_signals=['ongoing_activity']
            )

        # PATTERN 2: Single year (duration = 0)
        if duration == 0:
            return ValidatorGuidance(
                recommend_sections=['H', 'B', 'H1', 'H2'],  # Honor, Degree
                hints=[
                    f'Single year ({start_year}) suggests one-time event',
                    'Awards and degrees (H, B) are typically conferred once',
                    'Memberships (I) and positions (D) typically have ranges'
                ],
                confidence=0.70,
                severity='soft',
                deterministic_signals=['single_year_event']
            )

        # PATTERN 3: Short duration (3-6 months typical for short-term training)
        # Note: We only have year-level granularity, so look for same-year ranges with month indicators
        if duration == 0:  # Same year, but check for month-level detail
            month_matches = list(re.finditer(self.MONTH_YEAR_PATTERNS[0], entry_text, re.IGNORECASE))
            if len(month_matches) >= 2:
                # Multiple months in same year suggests short-term activity
                return ValidatorGuidance(
                    recommend_sections=['C', 'C1'],  # Training
                    hints=[
                        f'Short-term activity in {start_year} (months specified) suggests training (C)',
                        'Typical for fellowships, short courses, visiting positions'
                    ],
                    confidence=0.65,
                    severity='soft',
                    deterministic_signals=['short_term_training']
                )

        # PATTERN 4: 1-3 year duration (typical postdoc/fellowship)
        if 1 <= duration <= 3:
            return ValidatorGuidance(
                recommend_sections=['C', 'C1'],  # Training/Fellowship
                hints=[
                    f'{duration}-year duration ({start_year}-{end_year}) typical of postdoc training (C)',
                    'Standard postdoc/fellowship is 2-3 years',
                    'Longer positions (5+ years) typically indicate faculty appointments (D)'
                ],
                confidence=0.70,
                severity='soft',
                deterministic_signals=['training_duration_typical']
            )

        # PATTERN 5: 4-7 year duration (ambiguous - could be position or extended training)
        if 4 <= duration <= 7:
            return ValidatorGuidance(
                recommend_sections=['D', 'D1', 'C', 'C1'],
                hints=[
                    f'{duration}-year duration ({start_year}-{end_year}) could be position (D) or extended fellowship (C)',
                    'Check for faculty titles (D) vs training language (C)'
                ],
                confidence=0.60,
                severity='soft',
                deterministic_signals=['medium_duration_ambiguous']
            )

        # PATTERN 6: Long duration (8+ years, typical faculty position or sustained membership)
        if duration >= 8:
            return ValidatorGuidance(
                recommend_sections=['D', 'D1', 'I'],  # Position or Membership
                exclude_sections=['C', 'C1', 'H'],  # Not training or one-time honor
                hints=[
                    f'Long duration ({duration} years: {start_year}-{end_year}) suggests position (D) or membership (I)',
                    'Postdoc training (C) rarely exceeds 5 years',
                    'Honors (H) are typically one-time events'
                ],
                confidence=0.80,
                severity='soft',
                deterministic_signals=['long_duration_position_or_membership']
            )

        # PATTERN 7: Multi-year grant period (3-5 years typical for R01)
        if 3 <= duration <= 5:
            # Check if entry contains grant-like language
            grant_indicators = [
                r'\b(R01|R21|K08|K23|P01|P30|U01)\b',
                r'\$[\d,]+',
                r'\b(PI|Co-PI|Principal Investigator)\b',
            ]
            has_grant_language = any(
                re.search(pattern, entry_text, re.IGNORECASE)
                for pattern in grant_indicators
            )

            if has_grant_language:
                return ValidatorGuidance(
                    recommend_sections=['M2'],  # Research Support
                    hints=[
                        f'{duration}-year period ({start_year}-{end_year}) + grant language → Research grant (M2)',
                        'Typical NIH R01 duration is 4-5 years',
                        'One-time award (H) would be single year'
                    ],
                    confidence=0.75,
                    severity='soft',
                    deterministic_signals=['grant_period_duration']
                )

        # Default: provide general temporal context
        if duration is not None:
            return ValidatorGuidance(
                hints=[
                    f'Duration: {duration} years ({start_year}-{end_year})',
                    'Consider: Short-term (C/H) vs Long-term (D/I/M2)'
                ],
                confidence=0.50,
                severity='soft',
                deterministic_signals=['temporal_context_general']
            )

        return ValidatorGuidance()

    def get_description(self) -> str:
        return "Analyzes temporal patterns (duration, single year, ongoing) to disambiguate classification"
