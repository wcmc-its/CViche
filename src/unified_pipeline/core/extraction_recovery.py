"""
Three-Tier Extraction Failure Recovery

Handles content that fails standard extraction through intelligent fallback:
  Tier 1: Narrative Detection → Preserve as descriptive text
  Tier 2: Re-classification with Enhanced Context → Retry with new section
  Tier 3: Route to Appendix → Last resort for unresolvable content

Based on the excellent extract_unextracted_fallback.py pattern.
"""

import json
import time
from typing import Dict, List, Optional, Any
import os

from unified_pipeline.llm_client import call_llm

# Import supporting modules
try:
    from .narrative_detector import detect_narrative_content, preserve_as_narrative
    from .taxonomy_contexts import get_section_context
    from .confusion_detector import detect_confusion_triggers
except ImportError:
    from narrative_detector import detect_narrative_content, preserve_as_narrative
    from taxonomy_contexts import get_section_context
    from confusion_detector import detect_confusion_triggers


# JSON schema for re-classification
RECLASSIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["KEEP_CURRENT", "RECLASSIFY", "PRESERVE_NARRATIVE", "ROUTE_TO_APPENDIX"],
            "description": "Recommended action based on analysis"
        },
        "recommended_section_id": {
            "type": "string",
            "description": "New section ID if RECLASSIFY, else original"
        },
        "recommended_section_name": {
            "type": "string",
            "description": "New section name if RECLASSIFY"
        },
        "confidence": {
            "type": "number",
            "description": "Confidence in recommendation (0.0-1.0)"
        },
        "reasoning": {
            "type": "string",
            "description": "Explanation of why this action was chosen"
        },
        "extraction_guidance": {
            "type": "string",
            "description": "Specific guidance for extraction attempt if RECLASSIFY"
        }
    },
    "required": ["action", "recommended_section_id", "recommended_section_name", "confidence", "reasoning", "extraction_guidance"],
    "additionalProperties": False
}


def extract_structural_headers(group_path: List[Dict[str, Any]]) -> List[str]:
    """
    Extract structural header labels from hierarchical group path.

    Args:
        group_path: List of parent groups from root to current (hierarchical path)

    Returns:
        List of header labels (e.g., ["Faculty of Health Sciences", "Education"])
    """
    headers = []
    for group in group_path:
        if group.get('is_structural_header', False):
            label = group.get('label_inferred', group.get('label', ''))
            if label and label != "Unknown":
                headers.append(label)
    return headers


def build_reclassification_prompt(
    entry_text: str,
    original_classification: Dict[str, Any],
    extraction_failure: Dict[str, Any],
    section_header: str = "",
    previous_entry_context: Optional[Dict] = None,
    structural_headers: Optional[List[str]] = None
) -> tuple[str, str]:
    """
    Build enhanced prompt for re-classification with failure context.

    Args:
        entry_text: Content that failed extraction
        original_classification: Original classification result
        extraction_failure: Details of extraction failure
        section_header: Original CV section label
        previous_entry_context: Optional previous entry for context
        structural_headers: Optional list of structural header labels from hierarchy

    Returns:
        (system_prompt, user_prompt)
    """
    original_section_id = original_classification.get('section_id', 'unknown')
    original_confidence = original_classification.get('confidence', 0.0)

    # Get section context with routing rules
    section_context = get_section_context(original_section_id)

    # Detect confusion triggers
    triggers = detect_confusion_triggers(entry_text, original_section_id, section_header)

    system_prompt = f"""You are a CV taxonomy expert performing RECOVERY ANALYSIS after an extraction failure.

CONTEXT:
- Original Classification: {original_classification.get('canonical_name', 'Unknown')} ({original_section_id})
- Original Confidence: {original_confidence:.2f}
- Extraction failed: {extraction_failure.get('reason', 'Unknown reason')}

Your task: Determine if this content belongs in a DIFFERENT section, should be PRESERVED AS NARRATIVE, or needs other handling.

AVAILABLE ACTIONS:
1. KEEP_CURRENT - Retry extraction in current section (if you think extraction just failed, classification was right)
2. RECLASSIFY - Move to a different WCM section (provide specific section ID and extraction guidance)
3. PRESERVE_NARRATIVE - Content is descriptive text, not extractable data (research overview, teaching philosophy, etc.)
4. ROUTE_TO_APPENDIX - Unresolvable content (formatting issues, garbled text, etc.)

Return structured JSON matching the provided schema."""

    user_prompt = f"""Analyze this extraction failure and recommend action:

ORIGINAL CLASSIFICATION: {original_classification.get('canonical_name', 'Unknown')}
CONFIDENCE: {original_confidence:.2f}

SECTION HEADER: "{section_header}"
"""

    # Add structural headers if available (from preserved empty groups)
    if structural_headers and len(structural_headers) > 0:
        user_prompt += f"\nSTRUCTURAL CONTEXT (from CV hierarchy):\n"
        for i, header in enumerate(structural_headers):
            user_prompt += f"  Level {i+1}: {header}\n"
        user_prompt += "\nNote: These headers may provide institutional, departmental, or organizational context.\n"

    # Add previous entry context if available
    if previous_entry_context:
        user_prompt += f"""
PREVIOUS ENTRY:
  Content: {previous_entry_context.get('content', 'N/A')[:150]}...
  Classification: {previous_entry_context.get('classification', 'N/A')}
"""

    user_prompt += f"""
CURRENT ENTRY (FAILED EXTRACTION):
{entry_text[:800]}{"..." if len(entry_text) > 800 else ""}

EXTRACTION FAILURE DETAILS:
- Reason: {extraction_failure.get('reason', 'Unknown')}
- Missing Fields: {', '.join(extraction_failure.get('missing_fields', []))}
- Extraction Confidence: {extraction_failure.get('confidence', 0.0):.2f}
"""

    # Add trigger warnings
    if triggers:
        user_prompt += f"\nCONFUSION TRIGGERS DETECTED: {', '.join(triggers)}\n"

    # Add routing rules and examples if section context available
    if section_context:
        user_prompt += f"\nCURRENT SECTION CONTEXT:\n"
        user_prompt += f"Description: {section_context.get('description', 'N/A')}\n"

        if 'disambiguation' in section_context:
            user_prompt += "\nDISAMBIGUATION RULES:\n"
            disambiguation = section_context['disambiguation']
            if isinstance(disambiguation, dict):
                for rule_key, rule_text in list(disambiguation.items())[:5]:
                    if isinstance(rule_text, str):
                        user_prompt += f"- {rule_text}\n"
                    elif isinstance(rule_text, list):
                        for rule_item in rule_text[:3]:
                            user_prompt += f"- {rule_item}\n"

        # Show alternative sections
        if 'related_sections' in section_context:
            user_prompt += "\nALTERNATIVE SECTIONS TO CONSIDER:\n"
            for section_id, section_info in list(section_context['related_sections'].items())[:5]:
                user_prompt += f"• {section_id}: {section_info.get('title', 'Unknown')}\n"
                if 'examples' in section_info:
                    user_prompt += f"  Example: {section_info['examples'][0]}\n"

    user_prompt += """
Based on the extraction failure, content, and context, recommend the BEST action.
Consider:
1. Could this be a misclassification? (Check triggers and content vs. current section)
2. Is this narrative/descriptive rather than structured? (Research overview, philosophy statement, etc.)
3. Is extraction retry likely to succeed?

Return structured JSON with your recommendation."""

    return system_prompt, user_prompt


def recover_from_extraction_failure(
    entry_text: str,
    original_classification: Dict[str, Any],
    extraction_failure: Dict[str, Any],
    section_header: str = "",
    previous_entry_context: Optional[Dict] = None,
    structural_headers: Optional[List[str]] = None,
    upgrade_model: bool = True
) -> Dict[str, Any]:
    """
    Attempt to recover from extraction failure using three-tier approach.

    Args:
        entry_text: Content that failed extraction
        original_classification: Original classification result
        extraction_failure: Extraction failure details
        section_header: Original CV section label
        previous_entry_context: Optional previous entry context
        structural_headers: Optional list of structural header labels from hierarchy
        upgrade_model: If True, upgrade from gpt-4o-mini to gpt-4o

    Returns:
        {
            "recovery_action": str,
            "final_section_id": str,
            "final_section_name": str,
            "confidence": float,
            "reasoning": str,
            "preserved_data": Optional[Dict],  # If PRESERVE_NARRATIVE
            "extraction_guidance": Optional[str],  # If RECLASSIFY
            "token_usage": Dict
        }
    """
    # TIER 1: Check if narrative content
    narrative_detection = detect_narrative_content(
        entry_text=entry_text,
        classification_section_id=original_classification.get('section_id', 'unknown'),
        classification_confidence=original_classification.get('confidence', 0.0),
        extraction_result=extraction_failure
    )

    if narrative_detection['is_narrative'] and narrative_detection['confidence'] >= 0.85:
        # Preserve as narrative
        preserved = preserve_as_narrative(
            entry_text=entry_text,
            classification_section_id=original_classification['section_id'],
            classification_confidence=original_classification['confidence'],
            narrative_detection=narrative_detection
        )

        return {
            "recovery_action": "PRESERVE_NARRATIVE",
            "final_section_id": original_classification['section_id'],
            "final_section_name": original_classification.get('canonical_name', 'Unknown'),
            "confidence": narrative_detection['confidence'],
            "reasoning": narrative_detection['reasoning'],
            "preserved_data": preserved,
            "extraction_guidance": None,
            "token_usage": {}
        }

    # TIER 2: Re-classification with enhanced context
    system_prompt, user_prompt = build_reclassification_prompt(
        entry_text=entry_text,
        original_classification=original_classification,
        extraction_failure=extraction_failure,
        section_header=section_header,
        previous_entry_context=previous_entry_context,
        structural_headers=structural_headers
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "extraction_recovery_analysis",
            "strict": True,
            "schema": RECLASSIFICATION_SCHEMA
        }
    }

    # Call LLM
    result_llm = call_llm(
        stage="core_extraction_recovery",
        messages=messages,
        response_format=response_format,
        max_tokens=800,
    )

    # Parse result
    result = json.loads(result_llm["content"])

    # Handle based on action
    recovery_action = result['action']

    if recovery_action == "ROUTE_TO_APPENDIX":
        final_section_id = "unknown"  # T - Appendix
        final_section_name = "Appendix / Other"
    elif recovery_action == "RECLASSIFY":
        final_section_id = result['recommended_section_id']
        final_section_name = result['recommended_section_name']
    else:  # KEEP_CURRENT or PRESERVE_NARRATIVE
        final_section_id = original_classification['section_id']
        final_section_name = original_classification.get('canonical_name', 'Unknown')

    return {
        "recovery_action": recovery_action,
        "final_section_id": final_section_id,
        "final_section_name": final_section_name,
        "confidence": result['confidence'],
        "reasoning": result['reasoning'],
        "preserved_data": None,  # Only set if PRESERVE_NARRATIVE from Tier 1
        "extraction_guidance": result.get('extraction_guidance'),
        "token_usage": {
            'prompt_tokens': result_llm["prompt_tokens"],
            'completion_tokens': result_llm["completion_tokens"],
            'total_tokens': result_llm["total_tokens"],
            'model_used': result_llm["model"]
        }
    }
