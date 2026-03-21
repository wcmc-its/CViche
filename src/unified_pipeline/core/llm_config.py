"""
LLM Configuration and Escalation Strategy

Provides configurable model selection with confidence-based escalation
and structured output enforcement.
"""

from typing import Literal, Optional, Dict, Any
import os

# Model configuration
DEFAULT_MODEL = os.getenv("CV_TAXONOMY_MODEL", "gpt-4o-mini-2024-07-18")  # Can override with o1-mini or o4-mini when available
ENABLE_REASONING_EFFORT = os.getenv("ENABLE_REASONING_EFFORT", "false").lower() == "true"

ReasoningEffort = Literal["low", "medium", "high"]

# Confidence thresholds for escalation
CONFIDENT_THRESHOLD = 0.75   # Above this: use low reasoning (if supported)
AMBIGUOUS_THRESHOLD = 0.60   # Below this: escalate to medium reasoning
UNCERTAIN_THRESHOLD = 0.45   # Below this: escalate to high reasoning (deep thinking)

# Class-specific threshold adjustments (confusion pairs need higher certainty)
CLASS_SPECIFIC_THRESHOLDS = {
    # High-ambiguity pairs need stricter thresholds
    'H_vs_I': 0.80,          # Honor vs Membership - very ambiguous
    'M2_vs_N': 0.75,         # Grant vs Mentoring - confusable
    'C_vs_D_vs_N': 0.75,     # Training vs Position vs Mentee - three-way confusion
    'S11_vs_M3': 0.70,       # Software pub vs development
    'S12_vs_M1': 0.70,       # Dataset pub vs cohort
    'S3_vs_K': 0.70,         # Books vs Teaching
    'H_vs_M2': 0.75,         # Honor vs Grant (new confusion area)

    # Lower-ambiguity pairs can use standard thresholds
    'S1_vs_S2': 0.60,        # Original research vs Review - easier to distinguish
    'D1_vs_D2': 0.60,        # Current vs Past position - temporal cue
    'B_vs_C': 0.60,          # Degree vs Postdoc - different stages
}


def get_model_config(
    use_reasoning: bool = ENABLE_REASONING_EFFORT,
    reasoning_effort: Optional[ReasoningEffort] = None
) -> Dict[str, Any]:
    """
    Get model configuration for LLM API call.

    Args:
        use_reasoning: Whether to use reasoning models (o1-mini, o4-mini)
        reasoning_effort: Reasoning effort level if supported

    Returns:
        Dict with model config (model name, optional reasoning_effort, etc.)
    """
    config = {
        "model": DEFAULT_MODEL,
        "temperature": 0.1,
    }

    # If using reasoning models and reasoning_effort is supported
    if use_reasoning and reasoning_effort and ENABLE_REASONING_EFFORT:
        config["reasoning_effort"] = reasoning_effort

    return config


def get_reasoning_effort(
    confidence: float,
    parent_section: Optional[str] = None,
    subsection_candidates: Optional[list] = None
) -> ReasoningEffort:
    """
    Determine reasoning effort based on confidence and context.

    Args:
        confidence: Model's confidence score (0.0-1.0)
        parent_section: Parent section ID (for context)
        subsection_candidates: List of candidate subsections

    Returns:
        "low", "medium", or "high" reasoning effort
    """
    # Check for confusion pairs
    threshold = CONFIDENT_THRESHOLD

    if subsection_candidates and len(subsection_candidates) >= 2:
        # Check if this is a known confusion pair
        # Try both orderings
        pair_key_1 = f"{subsection_candidates[0]}_vs_{subsection_candidates[1]}"
        pair_key_2 = f"{subsection_candidates[1]}_vs_{subsection_candidates[0]}"

        threshold = CLASS_SPECIFIC_THRESHOLDS.get(
            pair_key_1,
            CLASS_SPECIFIC_THRESHOLDS.get(pair_key_2, threshold)
        )

    # Determine reasoning effort
    if confidence >= threshold:
        return "low"  # Fast path for clear cases
    elif confidence >= AMBIGUOUS_THRESHOLD:
        return "medium"  # Thoughtful for ambiguous cases
    else:
        return "high"  # Deep reasoning for very uncertain cases


# JSON Schema for structured taxonomy classification
TAXONOMY_CLASSIFICATION_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "cv_section_classification",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                # Primary classification
                "section_id": {
                    "type": "string",
                    "description": "The taxonomy code (e.g., 'S1', 'H2', 'M2')"
                },
                "section_name": {
                    "type": "string",
                    "description": "Human-readable section name"
                },
                "confidence": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "description": "Confidence score for this classification"
                },

                # Reasoning and alternatives
                "reasoning": {
                    "type": "string",
                    "description": "Step-by-step reasoning for classification"
                },
                "key_factors": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Key factors that influenced the decision"
                },
                "alternative_sections": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "section_id": {"type": "string"},
                            "likelihood": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                            "reason": {"type": "string"}
                        },
                        "required": ["section_id", "likelihood", "reason"],
                        "additionalProperties": False
                    },
                    "description": "Alternative sections considered, in order of likelihood"
                }
            },
            "required": ["section_id", "section_name", "confidence", "reasoning"],
            "additionalProperties": False
        }
    }
}


# Simplified schema for Pass 1 (parent section only)
PARENT_SECTION_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "parent_section_classification",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "section_id": {
                    "type": "string",
                    "description": "Parent section code (A-T)"
                },
                "section_name": {
                    "type": "string",
                    "description": "Section name"
                },
                "confidence": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0
                },
                "reasoning": {
                    "type": "string",
                    "description": "Brief explanation"
                }
            },
            "required": ["section_id", "section_name", "confidence", "reasoning"],
            "additionalProperties": False
        }
    }
}
