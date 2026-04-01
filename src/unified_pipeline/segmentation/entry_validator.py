#!/usr/bin/env python3
"""
PASS 2: Entry Validation and Self-Critique
A lightweight classifier that detects segmentation errors and enables auto-repair.
"""

import json
from typing import List, Dict, Optional, Literal
from unified_pipeline.llm_client import call_llm

# JSON schema for validator output
VALIDATOR_SCHEMA = {
    "type": "object",
    "properties": {
        "judgment": {
            "type": "string",
            "enum": ["correct", "oversplit", "undersplit"],
            "description": "Whether this entry is correctly segmented, oversplit, or undersplit"
        },
        "confidence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "description": "Confidence in the judgment (0.0-1.0)"
        },
        "reason": {
            "type": "string",
            "description": "Short explanation of the judgment"
        }
    },
    "required": ["judgment", "confidence", "reason"],
    "additionalProperties": False
}

VALIDATOR_SYSTEM_PROMPT = """You are validating whether a segmented CV entry is correctly split.

Given:
1. The full text of the original block (before segmentation)
2. The segmented entry (one item)
3. The neighboring entries (if any)

Your task:
Determine whether THIS entry appears to be:
- correctly segmented,
- oversplit (this item should have been merged with neighbors),
- or undersplit (this item actually contains multiple items).

Rules:
- Metadata-only lines (dates, ISBN, identifiers, amounts, roles, etc.) almost never form a standalone entry; flag these as likely oversplit.
- A conceptual entry normally contains an anchor (title, citation, job title, award name, project title, etc.).
- If two adjacent entries share the same anchor context, they are likely parts of one item.

Return JSON with your judgment, confidence (0.0-1.0), and a short reason."""


def validate_entry(
    entry: Dict,
    original_block: str,
    previous_entry: Optional[Dict] = None,
    next_entry: Optional[Dict] = None,
) -> Dict:
    """
    Validate a single entry to detect segmentation errors.

    Args:
        entry: The entry to validate (must have 'text_snippet')
        original_block: The full text of the original section before segmentation
        previous_entry: The previous entry in the section (if any)
        next_entry: The next entry in the section (if any)

    Returns:
        Dict with keys: judgment, confidence, reason, cost, tokens
    """

    entry_text = entry.get('text_snippet', '')

    # Build context
    context_parts = [f"Original block:\n{original_block}\n"]

    context_parts.append(f"\nEntry to evaluate:\n{entry_text}\n")

    if previous_entry:
        prev_text = previous_entry.get('text_snippet', '')
        context_parts.append(f"\nPrevious entry:\n{prev_text}\n")

    if next_entry:
        next_text = next_entry.get('text_snippet', '')
        context_parts.append(f"\nNext entry:\n{next_text}\n")

    user_prompt = "".join(context_parts)

    try:
        llm_result = call_llm(
            stage="segmentation_entry_validator",
            messages=[
                {"role": "system", "content": VALIDATOR_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "validation_result",
                    "strict": True,
                    "schema": VALIDATOR_SCHEMA
                }
            },
            temperature=0.1
        )

        result = json.loads(llm_result["content"])

        result['cost'] = llm_result["cost"]
        result['tokens'] = {
            'prompt': llm_result["prompt_tokens"],
            'completion': llm_result["completion_tokens"],
            'total': llm_result["total_tokens"]
        }
        result['model_used'] = llm_result["model"]

        return result

    except Exception as e:
        # Fallback on error
        return {
            'judgment': 'correct',
            'confidence': 0.0,
            'reason': f'Validation failed: {str(e)}',
            'cost': 0.0,
            'tokens': {'prompt': 0, 'completion': 0, 'total': 0},
            'model_used': 'unknown',
            'error': str(e)
        }


def validate_section_entries(
    entries: List[Dict],
    original_text: str,
) -> List[Dict]:
    """
    Validate all entries in a section.

    Args:
        entries: List of entries from segmentation (each with 'text_snippet')
        original_text: The original section text before segmentation

    Returns:
        List of validation results (one per entry)
    """

    validations = []

    for i, entry in enumerate(entries):
        prev_entry = entries[i-1] if i > 0 else None
        next_entry = entries[i+1] if i < len(entries) - 1 else None

        validation = validate_entry(
            entry=entry,
            original_block=original_text,
            previous_entry=prev_entry,
            next_entry=next_entry,
        )

        validations.append(validation)

    return validations


def repair_oversplit_entries(entries: List[Dict], validations: List[Dict]) -> List[Dict]:
    """
    Auto-repair oversplit entries by merging them with neighbors.

    Args:
        entries: Original list of entries
        validations: Validation results from validate_section_entries

    Returns:
        Repaired list of entries (merged where appropriate)
    """

    if len(entries) != len(validations):
        raise ValueError("Entries and validations must have same length")

    # Group entries that should be merged
    merge_groups = []
    current_group = [0]

    for i in range(1, len(entries)):
        # If previous entry was oversplit with high confidence, merge with current
        if (validations[i-1]['judgment'] == 'oversplit' and
            validations[i-1]['confidence'] >= 0.7):
            current_group.append(i)
        else:
            if len(current_group) > 1:
                merge_groups.append(current_group)
            current_group = [i]

    # Don't forget last group
    if len(current_group) > 1:
        merge_groups.append(current_group)

    # Build repaired entries
    repaired = []
    merged_indices = set()

    for group in merge_groups:
        # Merge all entries in group
        merged_text = "\n".join(entries[idx]['text_snippet'] for idx in group)

        repaired_entry = {
            'text_snippet': merged_text,
            'entry_type': entries[group[0]].get('entry_type', 'other'),
            'confidence': entries[group[0]].get('confidence', 1.0),
            'merged_from': group,
            'repair_action': 'merged_oversplit'
        }

        repaired.append(repaired_entry)
        merged_indices.update(group)

    # Add non-merged entries
    for i, entry in enumerate(entries):
        if i not in merged_indices:
            repaired.append({
                **entry,
                'repair_action': 'kept_as_is'
            })

    return repaired


def validate_and_repair_group(
    group: Dict,
    auto_repair: bool = True,
    confidence_threshold: float = 0.7
) -> Dict:
    """
    Validate and optionally repair all entries in a CV section group.

    Args:
        group: A section group with 'entries' and optionally 'original_text'
        auto_repair: Whether to auto-repair oversplit entries
        confidence_threshold: Minimum confidence to trigger auto-repair

    Returns:
        Updated group with validation results and optionally repaired entries
    """

    entries = group.get('entries', [])

    if not entries:
        return group

    # Get original text (if available)
    # Otherwise, reconstruct from entries
    original_text = group.get('original_text', '')
    if not original_text:
        original_text = "\n\n".join(e.get('text_snippet', '') for e in entries)

    # Validate all entries
    validations = validate_section_entries(
        entries=entries,
        original_text=original_text,
    )

    # Add validation metadata to group
    total_cost = sum(v.get('cost', 0.0) for v in validations)
    oversplit_count = sum(1 for v in validations if v['judgment'] == 'oversplit' and v['confidence'] >= confidence_threshold)
    undersplit_count = sum(1 for v in validations if v['judgment'] == 'undersplit' and v['confidence'] >= confidence_threshold)

    group['validation_meta'] = {
        'total_entries_validated': len(validations),
        'oversplit_detected': oversplit_count,
        'undersplit_detected': undersplit_count,
        'validation_cost': total_cost,
    }

    # Attach validation results to entries
    for entry, validation in zip(entries, validations):
        entry['validation'] = validation

    # Auto-repair if requested
    if auto_repair and oversplit_count > 0:
        repaired_entries = repair_oversplit_entries(entries, validations)
        group['entries'] = repaired_entries
        group['validation_meta']['auto_repaired'] = True
        group['validation_meta']['entries_after_repair'] = len(repaired_entries)
        group['validation_meta']['repair_type'] = 'merged_oversplit'
    elif undersplit_count > 0:
        # Flag undersplit for manual review or recursive segmentation
        # We don't auto-split here because it requires re-running Pass 1 segmentation
        group['validation_meta']['auto_repaired'] = False
        group['validation_meta']['needs_resegmentation'] = True
        group['validation_meta']['undersplit_entries'] = [
            i for i, v in enumerate(validations)
            if v['judgment'] == 'undersplit' and v['confidence'] >= confidence_threshold
        ]
    else:
        group['validation_meta']['auto_repaired'] = False

    return group
