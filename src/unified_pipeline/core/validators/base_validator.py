"""
Base Validator Interface

All validators must inherit from BaseValidator and implement:
- applies_to(): Which parent sections this validator checks
- analyze(): Return guidance based on entry analysis
- priority(): Execution order (lower = earlier)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Optional


@dataclass
class ValidatorGuidance:
    """
    Guidance provided by a validator to narrow classification options.
    """
    # Sections to exclude from consideration
    exclude_sections: list[str] = field(default_factory=list)

    # Sections to recommend (narrowed options)
    recommend_sections: list[str] = field(default_factory=list)

    # Hints/explanations for LLM prompt
    hints: list[str] = field(default_factory=list)

    # Confidence in this guidance (0.0-1.0)
    confidence: float = 0.0

    # Severity: 'hard' (must not violate) or 'soft' (guidance only)
    severity: str = 'soft'

    # Can LLM override this guidance?
    allow_override: bool = True

    # Reasons for exclusions (section_id -> reason)
    exclusion_reasons: dict[str, str] = field(default_factory=dict)

    # Deterministic signals detected (for logging)
    deterministic_signals: list[str] = field(default_factory=list)

    @classmethod
    def no_guidance(cls) -> ValidatorGuidance:
        """Return empty guidance (no signals detected)."""
        return cls()

    def has_guidance(self) -> bool:
        """Check if any guidance was provided."""
        return bool(
            self.exclude_sections or
            self.recommend_sections or
            self.hints
        )


@dataclass
class GuidanceResult:
    """
    Combined guidance from all applicable validators.
    """
    # Narrowed subsection options
    recommended_subsections: list[str] = field(default_factory=list)

    # Sections to exclude
    excluded_subsections: list[str] = field(default_factory=list)

    # Reasons for exclusions
    exclusion_reasons: dict[str, str] = field(default_factory=dict)

    # Hints for LLM prompt
    hints: list[str] = field(default_factory=list)

    # Overall confidence in guidance (0.0-1.0)
    confidence_in_guidance: float = 0.0

    # Deterministic signals detected
    deterministic_signals: list[str] = field(default_factory=list)

    # Can LLM override this guidance?
    allow_override: bool = True

    # Validator IDs that contributed
    validators_applied: list[str] = field(default_factory=list)

    def has_guidance(self) -> bool:
        """Check if any guidance was provided."""
        return bool(
            self.recommended_subsections or
            self.excluded_subsections or
            self.hints
        )


class BaseValidator(ABC):
    """
    Base class for all validators.

    Validators analyze entries and provide guidance to narrow
    classification options before LLM sees them.
    """

    @abstractmethod
    def applies_to(self) -> list[str]:
        """
        Which parent sections this validator checks.

        Returns:
            List of parent section IDs (e.g., ['S', 'bibliography'])
        """
        pass

    @abstractmethod
    def analyze(self, entry_text: str) -> ValidatorGuidance:
        """
        Analyze entry and return guidance.

        Args:
            entry_text: Entry content to analyze

        Returns:
            ValidatorGuidance with recommendations/exclusions
        """
        pass

    @abstractmethod
    def priority(self) -> int:
        """
        Execution order (lower = earlier).

        Returns:
            Priority value (0-100). Standard priorities:
            - 0-20: Critical validators (must run first)
            - 21-50: Normal validators
            - 51-100: Low-priority validators
        """
        pass

    def get_name(self) -> str:
        """Get validator name (defaults to class name)."""
        return self.__class__.__name__

    def applies_to_parent(self, parent_section_id: str) -> bool:
        """
        Check if this validator applies to the given parent section.

        Args:
            parent_section_id: Parent section ID or code

        Returns:
            True if validator should run for this parent
        """
        applies_to = self.applies_to()
        return parent_section_id in applies_to
