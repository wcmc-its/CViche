"""
Education vs Postdoc Position Validator

GROUND TRUTH FIX #2: Distinguish degrees from postdoc research/clinical positions.

Problem: "EDUCATION" sections often contain both degrees (B.A., Ph.D.) and
postdoc positions. LLM samples first few entries (often postdocs) and misclassifies
entire section as C (Postdoctoral Training), missing the degrees.

Solution: Detect mixed content and provide guidance to LLM.
- If both present → recommend B (Education) as primary
- Section label "EDUCATION" is strong signal for B
- Postdocs are often listed first chronologically but degrees are primary content
"""

import re

from .base_validator import BaseValidator, ValidatorGuidance


class EducationPostdocValidator(BaseValidator):
    """
    Detects mixed education content (degrees + postdoc positions).

    Distinguishes:
    - Degrees: B.A., M.S., Ph.D., M.D., etc. → B (Education)
    - Postdoc positions: Post-doctoral Fellow, Research Fellow → C (Postdoctoral Training)
    - Clinical positions: Resident, Clinical Fellow → C (Postdoctoral Training)

    When mixed content detected, provides guidance to help LLM classify correctly.
    """

    # Postdoc position indicators
    POSTDOC_KEYWORDS = [
        'post-doctoral fellow',
        'postdoctoral fellow',
        'postdoc',
        'post doctoral fellow',
        'post-doc',
        'postdoctoral research',
        'postdoctoral training',
    ]

    # Clinical training position indicators
    CLINICAL_TRAINING_KEYWORDS = [
        'resident',
        'residency',
        'clinical fellow',
        'research fellow',
        'intern',
        'internship',
        'trainee',
        'training program',
        'fellowship',  # When paired with clinical/medical context
    ]

    # Degree keywords
    DEGREE_KEYWORDS = [
        'b.a.', 'b.s.', 'bachelor',
        'm.a.', 'm.s.', 'm.d.', 'master',
        'ph.d.', 'phd', 'doctorate', 'd.d.s.', 'j.d.', 'psy.d.',
        'mba', 'mpa', 'mph', 'm.p.h.',
        'doctor of',
        'bachelor of',
        'master of',
    ]

    # Position indicators (job-like characteristics)
    POSITION_INDICATORS = [
        r'dr\.\s+\w+',  # "Dr. Bailey" (supervisor)
        r'pi:\s*\w+',   # "PI: Smith"
        r'\d{4}\s*[-–]\s*\d{4}',  # Date range: "2002-2004"
        r'research\s+(?:on|in|at)',  # "Research on X"
        r'supervised\s+by',
        r'advisor:',
        r'mentor:',
    ]

    # FALSE POSITIVES to exclude (not positions)
    EXCLUDE_PATTERNS = [
        'fellow of',  # "Fellow of AAAS" (honor, not position)
        'fellow,',    # "John Smith, Fellow, AAAS"
        'honorary fellow',
        'distinguished fellow',
    ]

    # Degree-specific indicators
    DEGREE_INDICATORS = [
        'major in',
        'degree in',
        'graduated',
        'graduation',
        'thesis:',
        'dissertation:',
        'summa cum laude',
        'magna cum laude',
        'cum laude',
        'honors',
        r'^\d{4}$',  # Single year (graduation year)
    ]

    def applies_to(self) -> list[str]:
        """This validator applies to B and C parent sections (and borderline cases)."""
        return ['B', 'C', 'D']  # Education, Postdoc, and Positions (borderline)

    def priority(self) -> int:
        """High priority - helps distinguish B vs C early."""
        return 15

    def analyze(self, entry_text: str) -> ValidatorGuidance:
        """
        Analyze a single entry to determine if it's a degree or postdoc position.

        Note: This gets called per-entry by guidance_engine, which will aggregate
        results across all entries in the section.
        """
        # DEFENSIVE: Handle dict entries (extract text if needed)
        if isinstance(entry_text, dict):
            entry_text = entry_text.get('text_snippet', '')

        # Ensure it's a string
        if not isinstance(entry_text, str):
            entry_text = str(entry_text) if entry_text else ''

        return self._detect_entry_type(entry_text)

    def analyze_section(self, section_label: str, entries: list[str]) -> ValidatorGuidance:
        """
        Analyze entire section to detect mixed content.

        This is called by guidance_engine after individual entry analysis.
        """
        entry_types = []
        for entry in entries:
            # Handle both dict entries (from entry objects) and strings
            if isinstance(entry, dict):
                entry_text = entry.get('text_snippet', '')
            else:
                entry_text = entry

            # DEFENSIVE: Ensure entry_text is a string
            if not isinstance(entry_text, str):
                entry_text = str(entry_text) if entry_text else ''

            # Skip empty entries
            if not entry_text:
                continue

            entry_type = self._classify_entry_type(entry_text)
            entry_types.append(entry_type)

        degree_count = sum(1 for t in entry_types if t == 'degree')
        postdoc_count = sum(1 for t in entry_types if t in ['postdoc_position', 'clinical_position'])

        # Mixed content detected
        if degree_count > 0 and postdoc_count > 0:
            return ValidatorGuidance(
                exclude_sections=[],  # Don't exclude anything - could be either
                recommend_sections=['B', 'C'],  # Both are possible
                hints=[
                    f"Section contains {degree_count} degree program(s) and {postdoc_count} postdoc research/clinical position(s)",
                    f"Section labeled: '{section_label}'",
                    "If section is primarily about DEGREES → B (Education)",
                    "If section is primarily about POSTDOC POSITIONS → C (Postdoctoral Training)",
                    "Consider: section label and primary focus",
                    "Note: EDUCATION sections often list postdocs chronologically but degrees are primary content"
                ],
                confidence=0.70,  # Medium confidence - LLM should decide based on label
                severity='soft',
                allow_override=True,
                exclusion_reasons={},
                deterministic_signals=[f'mixed_education_content: {degree_count}degrees+{postdoc_count}postdocs']
            )

        # Only degrees
        elif degree_count > 0 and postdoc_count == 0:
            return ValidatorGuidance(
                exclude_sections=['C'],  # No postdocs, exclude C
                recommend_sections=['B'],
                hints=[
                    f"Section contains {degree_count} degree program(s), no postdoc research/clinical positions",
                    "Recommend: B (Education)"
                ],
                confidence=0.85,
                severity='soft',
                allow_override=True,
                exclusion_reasons={'C': 'No postdoc positions detected, only degree programs'},
                deterministic_signals=['only_degrees']
            )

        # Only postdocs
        elif postdoc_count > 0 and degree_count == 0:
            return ValidatorGuidance(
                exclude_sections=['B'],  # No degrees, exclude B
                recommend_sections=['C'],
                hints=[
                    f"Section contains {postdoc_count} postdoc research/clinical position(s), no degrees",
                    "Recommend: C (Postdoctoral Training)"
                ],
                confidence=0.85,
                severity='soft',
                allow_override=True,
                exclusion_reasons={'B': 'No degree programs detected, only postdoc positions'},
                deterministic_signals=['only_postdocs']
            )

        # Couldn't detect clearly
        else:
            return ValidatorGuidance.no_guidance()

    def _classify_entry_type(self, entry_text: str) -> str:
        """
        Classify a single entry as 'degree', 'postdoc_position', 'clinical_position', or 'unknown'.

        Returns:
            Entry type classification
        """
        # DEFENSIVE: Ensure entry_text is a string
        if not isinstance(entry_text, str):
            if isinstance(entry_text, dict):
                entry_text = entry_text.get('text_snippet', '')
            else:
                entry_text = str(entry_text) if entry_text else ''

        text_lower = entry_text.lower()

        # Check for false positives first (honors, not positions)
        for exclude in self.EXCLUDE_PATTERNS:
            if exclude in text_lower:
                # Likely honor or membership, not training position
                return 'honor'

        # Check for postdoc indicators
        if any(keyword in text_lower for keyword in self.POSTDOC_KEYWORDS):
            return 'postdoc_position'

        # Check for clinical training
        has_clinical_keyword = any(keyword in text_lower for keyword in self.CLINICAL_TRAINING_KEYWORDS)
        if has_clinical_keyword:
            # "Fellow" is ambiguous - need context
            if 'fellow' in text_lower:
                # "Fellow" with supervisor/dates/research = position
                # "Fellow of AAAS" = honor (already excluded above)
                has_position_indicator = any(
                    re.search(pattern, entry_text, re.IGNORECASE)
                    for pattern in self.POSITION_INDICATORS
                )
                if has_position_indicator:
                    return 'clinical_position'
                else:
                    # Ambiguous fellowship - could be honor
                    return 'unknown'
            else:
                # Other clinical keywords are clear (resident, intern, etc.)
                return 'clinical_position'

        # Check for degree indicators
        if any(keyword in text_lower for keyword in self.DEGREE_KEYWORDS):
            return 'degree'

        # Check for degree-specific indicators
        for indicator in self.DEGREE_INDICATORS:
            if isinstance(indicator, str):
                if indicator in text_lower:
                    return 'degree'
            else:  # regex pattern
                if re.search(indicator, entry_text):
                    # Single year at end might be graduation year
                    # But only if no date range present
                    if indicator == r'^\d{4}$':
                        # Check it's not part of a range
                        if not re.search(r'\d{4}\s*[-–]\s*\d{4}', entry_text):
                            return 'degree'

        return 'unknown'

    def _detect_entry_type(self, entry_text: str) -> ValidatorGuidance:
        """
        Detect entry type and return minimal guidance.

        This is called per-entry, so we just classify and let section-level
        analysis aggregate the results.
        """
        entry_type = self._classify_entry_type(entry_text)

        # Return minimal guidance - section-level analysis will aggregate
        if entry_type in ['degree', 'postdoc_position', 'clinical_position']:
            return ValidatorGuidance(
                deterministic_signals=[f'entry_type: {entry_type}']
            )

        return ValidatorGuidance.no_guidance()
