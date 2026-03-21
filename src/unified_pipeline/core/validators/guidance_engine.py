"""
Guidance Engine

Orchestrates all validators to provide combined guidance
for classification narrowing before LLM sees entries.
"""

from typing import List, Dict, Optional
from .base_validator import BaseValidator, ValidatorGuidance, GuidanceResult


class GuidanceEngine:
    """
    Orchestrates validators to provide classification guidance.
    """

    def __init__(self):
        self.validators: List[BaseValidator] = []
        self._validators_by_parent: Dict[str, List[BaseValidator]] = {}

    def register_validator(self, validator: BaseValidator):
        """
        Register a validator.

        Args:
            validator: Validator instance to register
        """
        self.validators.append(validator)

        # Index by parent sections
        for parent_id in validator.applies_to():
            if parent_id not in self._validators_by_parent:
                self._validators_by_parent[parent_id] = []
            self._validators_by_parent[parent_id].append(validator)

        # Sort validators by priority
        for parent_id in self._validators_by_parent:
            self._validators_by_parent[parent_id].sort(
                key=lambda v: v.priority()
            )

    def get_applicable_validators(
        self,
        parent_section_id: str
    ) -> List[BaseValidator]:
        """
        Get validators applicable to parent section.

        Args:
            parent_section_id: Parent section ID

        Returns:
            List of applicable validators (sorted by priority)
        """
        # Get validators specific to this parent
        validators = self._validators_by_parent.get(parent_section_id, []).copy()

        # Add universal validators (those that apply to '*')
        universal_validators = self._validators_by_parent.get('*', [])
        validators.extend(universal_validators)

        # Sort by priority
        validators.sort(key=lambda v: v.priority())

        return validators

    def analyze_entries_for_guidance(
        self,
        parent_section_id: str,
        entries: List[str],
        section_label: str = ""
    ) -> GuidanceResult:
        """
        Analyze entries to provide classification guidance.

        Args:
            parent_section_id: Parent section from Pass 1
            entries: Entry texts to analyze
            section_label: Original section label (optional)

        Returns:
            GuidanceResult with combined guidance from all validators
        """
        # Get applicable validators
        validators = self.get_applicable_validators(parent_section_id)

        if not validators:
            # No validators for this parent section
            return GuidanceResult()

        # Collect guidance from all validators
        all_guidance: List[ValidatorGuidance] = []
        validators_applied: List[str] = []

        for validator in validators:
            # Check if validator has section-level analysis
            if hasattr(validator, 'analyze_section'):
                # Use section-level analysis (gets all entries)
                guidance = validator.analyze_section(section_label, entries)
            else:
                # Fall back to per-entry analysis on first entry
                # Extract text from entry object if needed
                if entries:
                    first_entry = entries[0]
                    if isinstance(first_entry, dict):
                        entry_text = first_entry.get('text_snippet', '')
                    else:
                        entry_text = first_entry
                else:
                    entry_text = ""
                guidance = validator.analyze(entry_text)

            if guidance.has_guidance():
                all_guidance.append(guidance)
                validators_applied.append(validator.get_name())

        # Combine guidance
        return self._combine_guidance(
            all_guidance,
            validators_applied
        )

    def _combine_guidance(
        self,
        all_guidance: List[ValidatorGuidance],
        validators_applied: List[str]
    ) -> GuidanceResult:
        """
        Combine guidance from multiple validators.

        Args:
            all_guidance: List of guidance from validators
            validators_applied: Names of validators that contributed

        Returns:
            Combined GuidanceResult
        """
        if not all_guidance:
            return GuidanceResult()

        result = GuidanceResult()
        result.validators_applied = validators_applied

        # Collect exclusions (union of all exclusions)
        for guidance in all_guidance:
            for section in guidance.exclude_sections:
                if section not in result.excluded_subsections:
                    result.excluded_subsections.append(section)

                    # Use reason from first validator that excluded it
                    if section in guidance.exclusion_reasons:
                        result.exclusion_reasons[section] = (
                            guidance.exclusion_reasons[section]
                        )

        # Collect recommendations (union, but remove excluded)
        for guidance in all_guidance:
            for section in guidance.recommend_sections:
                if (section not in result.recommended_subsections
                    and section not in result.excluded_subsections):
                    result.recommended_subsections.append(section)

        # Collect hints
        for guidance in all_guidance:
            result.hints.extend(guidance.hints)

        # Collect deterministic signals
        for guidance in all_guidance:
            result.deterministic_signals.extend(
                guidance.deterministic_signals
            )

        # Overall confidence: max confidence from all validators
        result.confidence_in_guidance = max(
            (g.confidence for g in all_guidance),
            default=0.0
        )

        # Allow override: False if any validator says False
        result.allow_override = all(
            g.allow_override for g in all_guidance
        )

        return result


# Global guidance engine instance
_guidance_engine = GuidanceEngine()


def get_guidance_engine() -> GuidanceEngine:
    """Get the global guidance engine instance."""
    return _guidance_engine


def register_validator(validator: BaseValidator):
    """Register a validator with the global engine."""
    _guidance_engine.register_validator(validator)


def analyze_entries_for_guidance(
    parent_section_id: str,
    entries: List[str],
    section_label: str = ""
) -> GuidanceResult:
    """
    Analyze entries for guidance (convenience function).

    Args:
        parent_section_id: Parent section from Pass 1
        entries: Entry texts to analyze
        section_label: Original section label (optional)

    Returns:
        GuidanceResult with combined guidance
    """
    return _guidance_engine.analyze_entries_for_guidance(
        parent_section_id,
        entries,
        section_label
    )
