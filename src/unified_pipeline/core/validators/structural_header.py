"""
Structural Header Validator

Detects document-level structural elements that should NOT receive content codes:
- Document titles: "CURRICULUM VITAE", "CV", "Curriculum Vitae"
- Name-only entries: Just the person's full name
- Page markers: "Page 1", "1 of 15", "-1-"
- Update timestamps: "Updated: January 2024", "Last revised: 2023"
- Section dividers: "---", "***", pure whitespace

These should be classified as T (structural/other), NOT as content codes like A (Personal Data).

This validator runs POST-classification and AUTO-CORRECTS misclassifications.
"""

import re
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass

try:
    from .base_validator import BaseValidator, ValidatorGuidance
except ImportError:
    from base_validator import BaseValidator, ValidatorGuidance


@dataclass
class CorrectionResult:
    """Result of a post-classification correction."""
    corrected: bool
    original_code: str
    new_code: str
    confidence: float
    reason: str


class StructuralHeaderValidator(BaseValidator):
    """
    Detect and auto-correct document-level structural elements.

    This validator identifies entries that are structural artifacts
    (not actual CV content) and ensures they're classified as T.
    """

    # Patterns for document titles
    CV_TITLE_PATTERNS = [
        r'^curriculum\s*vitae$',
        r'^cv$',
        r'^résumé$',
        r'^resume$',
        r'^biographical\s*sketch$',
        r'^biosketch$',
    ]

    # Patterns for page markers
    PAGE_MARKER_PATTERNS = [
        r'^page\s*\d+',                    # "Page 1", "Page 12"
        r'^\d+\s*of\s*\d+$',               # "1 of 15"
        r'^-\s*\d+\s*-$',                  # "-1-", "- 5 -"
        r'^\[\s*\d+\s*\]$',                # "[1]", "[ 3 ]"
        r'^p\.\s*\d+$',                    # "p. 1", "p.12"
    ]

    # Patterns for update timestamps
    TIMESTAMP_PATTERNS = [
        r'^updated:?\s*',                  # "Updated: January 2024"
        r'^last\s*(updated|revised|modified):?\s*',
        r'^revised:?\s*',
        r'^as\s*of:?\s*',
        r'^current\s*as\s*of:?\s*',
        r'^version:?\s*',
    ]

    # Patterns for section dividers
    DIVIDER_PATTERNS = [
        r'^[-_=*]{3,}$',                   # "---", "===", "***"
        r'^\s*$',                          # Empty/whitespace only
    ]

    def __init__(self, document_name: str | None = None):
        """
        Initialize with optional document name for name-matching.

        Args:
            document_name: The person's name from the document UID
                          (e.g., "John Smith" from "2071_Smith_John_CV")
        """
        self.document_name = document_name
        self._name_patterns = []
        if document_name:
            self._build_name_patterns(document_name)

    def _build_name_patterns(self, name: str):
        """Build regex patterns to match the person's name."""
        # Normalize name
        name_clean = name.strip()

        # Split into parts
        parts = re.split(r'[_\s,]+', name_clean)
        parts = [p for p in parts if p and len(p) > 1]

        if len(parts) >= 2:
            # Try different orderings: "John Smith", "Smith, John", "JOHN SMITH"
            first_last = r'\s*'.join(parts)
            last_first = r'\s*,?\s*'.join(reversed(parts))

            self._name_patterns = [
                re.compile(rf'^{first_last}$', re.IGNORECASE),
                re.compile(rf'^{last_first}$', re.IGNORECASE),
                # With optional credentials: "John Smith, PhD"
                re.compile(rf'^{first_last}\s*,?\s*(ph\.?d\.?|m\.?d\.?|m\.?s\.?|m\.?a\.?|m\.?b\.?a\.?)?$', re.IGNORECASE),
            ]

    def set_document_name(self, name: str):
        """Set/update the document name for matching."""
        self.document_name = name
        self._build_name_patterns(name)

    def applies_to(self) -> list[str]:
        """This validator applies to all sections (post-classification)."""
        return ['*']

    def priority(self) -> int:
        """High priority - structural elements should be caught first."""
        return 5

    def analyze(self, entry_text: str) -> ValidatorGuidance:
        """
        Analyze entry for structural patterns (pre-classification guidance).

        For pre-classification, we provide hints but don't force codes.
        """
        text = entry_text.strip()
        text_lower = text.lower()

        signals = []
        hints = []

        # Check CV title patterns
        for pattern in self.CV_TITLE_PATTERNS:
            if re.match(pattern, text_lower):
                signals.append(f"Document title detected: '{text}'")
                hints.append(
                    "This appears to be a document title (CURRICULUM VITAE, CV, etc.), "
                    "not personal data content. Classify as T (structural element)."
                )
                return ValidatorGuidance(
                    exclude_sections=['A', 'B', 'C', 'D', 'H', 'I', 'S'],
                    recommend_sections=['T'],
                    hints=hints,
                    confidence=0.95,
                    severity='hard',
                    allow_override=False,
                    deterministic_signals=signals
                )

        # Check page markers
        for pattern in self.PAGE_MARKER_PATTERNS:
            if re.match(pattern, text_lower):
                signals.append(f"Page marker detected: '{text}'")
                hints.append("Page number/marker - structural element, classify as T.")
                return ValidatorGuidance(
                    exclude_sections=['A', 'B', 'C', 'D', 'H', 'I', 'S', 'M', 'K'],
                    recommend_sections=['T'],
                    hints=hints,
                    confidence=0.99,
                    severity='hard',
                    allow_override=False,
                    deterministic_signals=signals
                )

        # Check timestamps
        for pattern in self.TIMESTAMP_PATTERNS:
            if re.match(pattern, text_lower):
                signals.append(f"Timestamp detected: '{text}'")
                hints.append("Document timestamp - structural element, classify as T.")
                return ValidatorGuidance(
                    recommend_sections=['T'],
                    hints=hints,
                    confidence=0.90,
                    severity='soft',
                    deterministic_signals=signals
                )

        # Check dividers
        for pattern in self.DIVIDER_PATTERNS:
            if re.match(pattern, text):
                signals.append(f"Divider/empty detected: '{text[:20]}'")
                return ValidatorGuidance(
                    recommend_sections=['T'],
                    hints=["Section divider or empty content - structural element."],
                    confidence=0.99,
                    severity='hard',
                    allow_override=False,
                    deterministic_signals=signals
                )

        # Check name-only (if we have name patterns)
        if self._name_patterns:
            for pattern in self._name_patterns:
                if pattern.match(text):
                    signals.append(f"Name-only entry detected: '{text}'")
                    hints.append(
                        "This appears to be the CV owner's name as a header/title, "
                        "not contact information. Classify as T (structural element)."
                    )
                    return ValidatorGuidance(
                        exclude_sections=['A'],  # Not personal data content
                        recommend_sections=['T'],
                        hints=hints,
                        confidence=0.85,
                        severity='soft',
                        deterministic_signals=signals
                    )

        return ValidatorGuidance.no_guidance()

    def check_and_correct(
        self,
        entry: dict,
        document_name: str | None = None
    ) -> CorrectionResult:
        """
        Check a classified entry and auto-correct if it's a structural element.

        This is the POST-CLASSIFICATION method that enforces corrections.

        Args:
            entry: Classified entry dict with 'text', 'taxonomy_code', etc.
            document_name: Optional name override for this check

        Returns:
            CorrectionResult indicating if correction was made
        """
        if document_name:
            self.set_document_name(document_name)

        text = entry.get('text', '').strip()
        text_lower = text.lower()
        current_code = entry.get('taxonomy_code', '')

        # Already T - no correction needed
        if current_code == 'T':
            return CorrectionResult(
                corrected=False,
                original_code=current_code,
                new_code=current_code,
                confidence=1.0,
                reason="Already classified as T"
            )

        # Check CV title patterns - MUST be T
        for pattern in self.CV_TITLE_PATTERNS:
            if re.match(pattern, text_lower):
                return CorrectionResult(
                    corrected=True,
                    original_code=current_code,
                    new_code='T',
                    confidence=0.99,
                    reason=f"Document title '{text}' must be T, not {current_code}"
                )

        # Check page markers - MUST be T
        for pattern in self.PAGE_MARKER_PATTERNS:
            if re.match(pattern, text_lower):
                return CorrectionResult(
                    corrected=True,
                    original_code=current_code,
                    new_code='T',
                    confidence=0.99,
                    reason=f"Page marker '{text}' must be T, not {current_code}"
                )

        # Check dividers - MUST be T
        for pattern in self.DIVIDER_PATTERNS:
            if re.match(pattern, text):
                return CorrectionResult(
                    corrected=True,
                    original_code=current_code,
                    new_code='T',
                    confidence=0.99,
                    reason=f"Divider/empty must be T, not {current_code}"
                )

        # Check timestamps - should be T
        for pattern in self.TIMESTAMP_PATTERNS:
            if re.match(pattern, text_lower):
                return CorrectionResult(
                    corrected=True,
                    original_code=current_code,
                    new_code='T',
                    confidence=0.90,
                    reason=f"Timestamp '{text}' should be T, not {current_code}"
                )

        # Check name-only entries - should be T (not A)
        if self._name_patterns and current_code == 'A':
            for pattern in self._name_patterns:
                if pattern.match(text):
                    return CorrectionResult(
                        corrected=True,
                        original_code=current_code,
                        new_code='T',
                        confidence=0.85,
                        reason=f"Name-only header '{text}' should be T (structural), not A (personal data content)"
                    )

        # No correction needed
        return CorrectionResult(
            corrected=False,
            original_code=current_code,
            new_code=current_code,
            confidence=1.0,
            reason="No structural pattern detected"
        )


def apply_structural_corrections(
    entries: list[dict],
    document_name: str | None = None
) -> tuple[list[dict], dict]:
    """
    Apply structural header corrections to a list of classified entries.

    Args:
        entries: List of classified entry dicts
        document_name: Person's name for name-matching

    Returns:
        Tuple of (corrected_entries, stats)
    """
    validator = StructuralHeaderValidator(document_name)

    corrections_made = 0
    correction_details = []

    for entry in entries:
        result = validator.check_and_correct(entry, document_name)

        if result.corrected:
            # Apply correction
            entry['taxonomy_code'] = result.new_code
            entry['taxonomy_confidence'] = result.confidence
            entry['structural_correction'] = {
                'original_code': result.original_code,
                'reason': result.reason
            }
            corrections_made += 1
            correction_details.append({
                'entry_id': entry.get('entry_id'),
                'text_preview': entry.get('text', '')[:50],
                'original': result.original_code,
                'corrected_to': result.new_code,
                'reason': result.reason
            })

    stats = {
        'entries_checked': len(entries),
        'corrections_made': corrections_made,
        'correction_details': correction_details
    }

    return entries, stats


# For testing
if __name__ == "__main__":
    # Test cases
    test_entries = [
        {"text": "CURRICULUM VITAE", "taxonomy_code": "A"},
        {"text": "John Smith", "taxonomy_code": "A"},
        {"text": "Page 1", "taxonomy_code": "T"},
        {"text": "Updated: January 2024", "taxonomy_code": "A"},
        {"text": "---", "taxonomy_code": "S1"},
        {"text": "Professor of Medicine", "taxonomy_code": "D1"},  # Should NOT be corrected
    ]

    corrected, stats = apply_structural_corrections(test_entries, "John Smith")

    print(f"Corrections made: {stats['corrections_made']}")
    for detail in stats['correction_details']:
        print(f"  {detail['original']} → {detail['corrected_to']}: {detail['reason']}")
