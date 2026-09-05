"""
Validation Checker - Post-Validation Safety Net

Checks if LLM violated guidance rules after classification.
"""

from typing import Dict, List
from .base_validator import GuidanceResult


def check_violations(
    llm_result: dict,
    guidance: GuidanceResult
) -> dict:
    """
    Check if LLM violated guidance rules.

    Args:
        llm_result: LLM classification result with:
            - section_id: Assigned section
            - confidence: LLM confidence (0.0-1.0)
            - reasoning: LLM explanation
        guidance: Guidance provided to LLM

    Returns:
        Dict with:
            - has_violations: bool
            - violated_rules: List[Dict]
            - severity: 'hard' or 'soft' or None
            - recommended_action: str
    """
    violations = {
        'has_violations': False,
        'violated_rules': [],
        'severity': None,
        'recommended_action': 'accept'
    }

    assigned = llm_result.get('section_id')
    llm_confidence = llm_result.get('confidence', 0.0)

    # Check 1: Assigned to excluded section?
    if assigned in guidance.excluded_subsections:
        violations['has_violations'] = True
        violations['violated_rules'].append({
            'type': 'excluded_section_chosen',
            'section': assigned,
            'reason': guidance.exclusion_reasons.get(assigned, 'Unknown'),
            'guidance_confidence': guidance.confidence_in_guidance,
            'llm_confidence': llm_confidence,
            'deterministic_signals': guidance.deterministic_signals
        })

        # Determine severity
        if not guidance.allow_override:
            violations['severity'] = 'hard'
            violations['recommended_action'] = 'override'
        else:
            violations['severity'] = 'soft'
            violations['recommended_action'] = 'flag'

    # Check 2: High confidence in non-recommended section?
    elif (assigned not in guidance.recommended_subsections
          and guidance.recommended_subsections
          and llm_confidence >= 0.85):

        violations['has_violations'] = True
        violations['violated_rules'].append({
            'type': 'high_confidence_non_recommended',
            'section': assigned,
            'llm_confidence': llm_confidence,
            'guidance_confidence': guidance.confidence_in_guidance,
            'recommended_sections': guidance.recommended_subsections
        })

        # If guidance was very confident, this is suspicious
        if guidance.confidence_in_guidance >= 0.9:
            violations['severity'] = 'hard'
            violations['recommended_action'] = 'flag_calibration'
        else:
            violations['severity'] = 'soft'
            violations['recommended_action'] = 'accept'

    return violations


def apply_correction(
    llm_result: dict,
    guidance: GuidanceResult
) -> dict:
    """
    Apply correction to LLM result based on guidance.

    Used when hard rule is violated (e.g., DOI + S7).

    Args:
        llm_result: Original LLM result
        guidance: Guidance that was violated

    Returns:
        Corrected result with metadata
    """
    # Choose first recommended section as correction
    corrected_section = (
        guidance.recommended_subsections[0]
        if guidance.recommended_subsections
        else 'UNKNOWN'
    )

    corrected = llm_result.copy()
    corrected['section_id'] = corrected_section
    corrected['original_section_id'] = llm_result.get('section_id')
    corrected['validation_override'] = True
    corrected['override_reason'] = (
        f"Hard rule violation: {guidance.exclusion_reasons.get(llm_result.get('section_id'))}"
    )

    # Lower confidence to reflect override
    corrected['confidence'] = min(
        llm_result.get('confidence', 0.0),
        guidance.confidence_in_guidance
    )

    return corrected
