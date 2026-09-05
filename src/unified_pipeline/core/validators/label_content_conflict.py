"""
Label-Content Conflict Validator

Detects mismatches between generic section labels and specific content types.

Problem: Some CVs use misleading headers (e.g., "Contact Information" for
publications section). The segmenter correctly extracts these labels but the
mapper may trust them blindly.

Solution: Detect conflicts when:
- Label is generic (contact, personal, CV, information)
- Content is specific (publications, presentations, grants, etc.)
- Entries are consistent (>70% same type)

Then provide advisory guidance to LLM suggesting the content-based mapping.

Design Principles:
- YAGNI: Start with 6 core entry types, add more only when proven necessary
- Pattern-based: Use startswith() to catch subtypes (publication_*)
- Advisory: LLM makes final decision with strong guidance
- Logging: Track conflict frequency for monitoring
"""

from typing import Counter as CounterType
from collections import Counter
from .base_validator import BaseValidator, ValidatorGuidance


class LabelContentConflictValidator(BaseValidator):
    """
    Detects label-content conflicts and guides LLM to correct mapping.

    Case Study: CV 2039 had "Contact Information" headers containing 28
    publications and 13 presentations, incorrectly mapped to section A.
    """

    name = "LabelContentConflictValidator"

    # Generic labels that should NOT contain specific content types
    GENERIC_LABELS = {
        'contact', 'personal', 'cv', 'curriculum vitae',
        'information', 'data', 'vitae', 'bio', 'biography'
    }

    # Specific entry types that conflict with generic labels
    # Using pattern-based detection: 'publication' catches 'publication_peer_reviewed', etc.
    SPECIFIC_TYPES = {
        'publication', 'presentation', 'grant',
        'award', 'position', 'education'
    }

    # Map entry types to expected parent sections
    ENTRY_TYPE_TO_PARENT = {
        'publication': 'S',  # Bibliography
        'presentation': 'R',  # Presentations
        'grant': 'M',  # Research Support
        'award': 'H',  # Honors/Awards
        'position': 'C',  # Professional Experience
        'education': 'B',  # Education/Training
    }

    # Consistency threshold: what % of entries must be same type to detect conflict
    CONSISTENCY_THRESHOLD = 0.70

    # Minimum entries to detect conflict (avoid false positives on tiny sections)
    MIN_ENTRIES = 3

    def applies_to(self) -> list[str]:
        """
        Universal validator - applies to all parent sections.

        Need to check all sections because we're detecting conflicts
        between expected (based on label) and actual (based on content).
        """
        return ['*']

    def priority(self) -> int:
        """
        High priority - run early to catch obvious label-content mismatches.

        Priority 5 = very early (before most domain-specific validators)
        """
        return 5

    def analyze(self, entry_text: str) -> ValidatorGuidance:
        """
        Per-entry analysis not applicable for this validator.

        We need section-level analysis to:
        - Examine section label
        - Determine dominant entry type across all entries
        - Detect conflict
        """
        return ValidatorGuidance.no_guidance()

    def analyze_section(
        self,
        section_label: str,
        entries: list
    ) -> ValidatorGuidance:
        """
        Analyze section for label-content conflicts.

        V6 UPDATE: This validator is DISABLED in V6.
        entry_type field removed from segmentation stage - classification happens in Stage 2.
        Label-content conflicts are now detected during taxonomy mapping, not segmentation.

        Args:
            section_label: Section label (e.g., "Contact Information")
            entries: List of entry objects (dicts) - entry_type no longer present

        Returns:
            ValidatorGuidance (always returns no_guidance in V6)
        """
        # V6: Validator disabled - entry_type removed from segmentation
        # Classification and conflict detection now happens in Stage 2 (taxonomy mapping)
        return ValidatorGuidance.no_guidance()

    def _detect_conflict(
        self,
        label: str,
        entry_types: list[str]
    ) -> dict | None:
        """
        Detect label-content conflict.

        Returns conflict info dict if conflict detected, None otherwise.
        """
        label_lower = label.lower()

        # Check 1: Is label generic?
        label_is_generic = any(
            generic in label_lower
            for generic in self.GENERIC_LABELS
        )

        if not label_is_generic:
            # Label is specific, no conflict expected
            return None

        # Check 2: Determine dominant entry type
        type_counter = Counter(entry_types)
        dominant_type, dominant_count = type_counter.most_common(1)[0]
        consistency = dominant_count / len(entry_types)

        # Check 3: Is dominant type specific (not 'other')?
        # Use pattern matching: 'publication' catches 'publication_peer_reviewed', etc.
        is_specific = any(
            dominant_type.startswith(specific_type)
            for specific_type in self.SPECIFIC_TYPES
        )

        if not is_specific:
            # Content is generic ('other'), no conflict
            return None

        # Check 4: Is consistency high enough?
        if consistency < self.CONSISTENCY_THRESHOLD:
            # Entries are mixed, too ambiguous to detect conflict
            return None

        # CONFLICT DETECTED
        # Find base type for mapping
        base_type = None
        for specific_type in self.SPECIFIC_TYPES:
            if dominant_type.startswith(specific_type):
                base_type = specific_type
                break

        return {
            'label': label,
            'label_type': 'generic',
            'dominant_type': dominant_type,
            'base_type': base_type,
            'consistency': consistency,
            'entry_count': len(entry_types),
            'dominant_count': dominant_count,
            'type_distribution': type_counter  # Keep as Counter for .most_common() method
        }

    def _generate_guidance(self, conflict_info: dict) -> ValidatorGuidance:
        """
        Generate advisory guidance for detected conflict.

        Provides strong suggestion to LLM while allowing semantic override.
        """
        base_type = conflict_info['base_type']
        expected_parent = self.ENTRY_TYPE_TO_PARENT.get(base_type, None)

        consistency_pct = int(conflict_info['consistency'] * 100)

        hints = [
            f"⚠️ LABEL-CONTENT CONFLICT DETECTED",
            f"  Label: '{conflict_info['label']}' (generic)",
            f"  Content: {conflict_info['dominant_count']}/{conflict_info['entry_count']} entries are '{conflict_info['dominant_type']}' ({consistency_pct}% consistent)",
            "",
            f"Recommendation: Trust content over label",
        ]

        # Add specific mapping suggestion if we know the parent
        if expected_parent:
            hints.append(f"  → Map to parent section {expected_parent} ({self._parent_description(expected_parent)})")

        # Add type distribution for transparency
        if len(conflict_info['type_distribution']) > 1:
            hints.append("")
            hints.append("Entry type distribution:")
            for entry_type, count in conflict_info['type_distribution'].most_common(3):
                hints.append(f"  - {entry_type}: {count}")

        return ValidatorGuidance(
            exclude_sections=[],  # No hard exclusions, let LLM decide
            recommend_sections=[expected_parent] if expected_parent else [],
            hints=hints,
            confidence=0.85,  # High confidence but not absolute
            severity='soft',  # Advisory, not mandatory
            allow_override=True,  # LLM can override with semantic reasoning
            deterministic_signals=[
                f'label_content_conflict:{base_type}:{consistency_pct}pct'
            ]
        )

    def _parent_description(self, parent_code: str) -> str:
        """Get human-readable description of parent section."""
        descriptions = {
            'A': 'Personal Data',
            'B': 'Education/Training',
            'C': 'Professional Experience',
            'H': 'Honors/Awards',
            'M': 'Research Support',
            'R': 'Presentations',
            'S': 'Bibliography',
        }
        return descriptions.get(parent_code, parent_code)
