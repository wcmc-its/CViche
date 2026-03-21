#!/usr/bin/env python3
"""
2-PASS CV SEGMENTATION WITH VALIDATION AND AUTO-REPAIR

Pass 1: Segment CV sections into entries (using GPT-5.1)
Pass 2: Validate entries and auto-repair oversplit/undersplit (using gpt-4o-mini)

This is the industry-standard architecture for stable CV parsing.
"""

from pathlib import Path
from typing import Optional
import json

from .word_chunked import segment_word_cv_chunked
from .entry_validator import validate_and_repair_group


def segment_word_cv_with_validation(
    docx_path: str,
    output_dir: str = "outputs/stage_1a_segmentation",
    segmentation_model: str = "gpt-5.1",
    validator_model: str = "gpt-4o-mini",
    auto_repair: bool = True,
    confidence_threshold: float = 0.7,
    validate_all_groups: bool = False
) -> dict:
    """
    2-pass CV segmentation with validation and auto-repair.

    Args:
        docx_path: Path to Word document
        output_dir: Output directory for results
        segmentation_model: Model for Pass 1 segmentation (default: gpt-5.1)
        validator_model: Model for Pass 2 validation (default: gpt-4o-mini - cheap!)
        auto_repair: Whether to automatically repair oversplit entries
        confidence_threshold: Minimum confidence to trigger repair (default: 0.7)
        validate_all_groups: If True, validate ALL groups. If False, only validate groups
                           with multiple entries (more efficient)

    Returns:
        Dictionary with segmentation results + validation metadata
    """

    print()
    print("=" * 80)
    print("2-PASS CV SEGMENTATION WITH VALIDATION")
    print("=" * 80)
    print(f"Pass 1: Segmentation model = {segmentation_model}")
    print(f"Pass 2: Validator model = {validator_model}")
    print(f"Auto-repair: {auto_repair}")
    print(f"Confidence threshold: {confidence_threshold}")
    print()

    # PASS 1: Initial segmentation
    print("=" * 80)
    print("PASS 1: SEGMENTATION")
    print("=" * 80)

    result = segment_word_cv_chunked(
        docx_path=docx_path,
        output_dir=output_dir,
        segmentation_model=segmentation_model
    )

    # Load the segmentation output
    with open(result['output_file'], 'r') as f:
        data = json.load(f)

    # PASS 2: Validation and repair
    print()
    print("=" * 80)
    print("PASS 2: VALIDATION & REPAIR")
    print("=" * 80)
    print()

    groups = data.get('groups', [])

    # Filter groups to validate
    if validate_all_groups:
        groups_to_validate = groups
        print(f"Validating ALL {len(groups)} groups...")
    else:
        # Only validate groups with entries (skip empty groups)
        groups_to_validate = [g for g in groups if len(g.get('entries', [])) > 0]
        print(f"Validating {len(groups_to_validate)} groups with entries...")
        if len(groups) - len(groups_to_validate) > 0:
            print(f"(Skipping {len(groups) - len(groups_to_validate)} empty groups)")

    print()

    validated_count = 0
    repaired_count = 0
    total_validation_cost = 0.0

    for i, group in enumerate(groups_to_validate, 1):
        group_id = group.get('id', f'G{i}')
        label = group.get('label_inferred', 'Unknown')
        entry_count = len(group.get('entries', []))

        print(f"  [{i}/{len(groups_to_validate)}] {group_id}: {label} ({entry_count} entries)")

        # Validate and repair
        validated_group = validate_and_repair_group(
            group=group,
            validator_model=validator_model,
            auto_repair=auto_repair,
            confidence_threshold=confidence_threshold
        )

        # Update in data
        group_index = groups.index(group)
        groups[group_index] = validated_group

        # Track stats
        validated_count += 1
        validation_meta = validated_group.get('validation_meta', {})
        total_validation_cost += validation_meta.get('validation_cost', 0.0)

        if validation_meta.get('auto_repaired', False):
            repaired_count += 1
            before = validation_meta['total_entries_validated']
            after = validation_meta['entries_after_repair']
            print(f"      ✓ Auto-repaired: {before} → {after} entries")

    print()
    print("=" * 80)
    print("VALIDATION SUMMARY")
    print("=" * 80)
    print(f"Groups validated: {validated_count}")
    print(f"Groups auto-repaired: {repaired_count}")
    print(f"Total validation cost: ${total_validation_cost:.4f}")
    print()

    # Update metadata
    data['meta']['validation_enabled'] = True
    data['meta']['validator_model'] = validator_model
    data['meta']['groups_validated'] = validated_count
    data['meta']['groups_repaired'] = repaired_count
    data['meta']['validation_cost'] = total_validation_cost

    # Save validated output
    output_path = Path(result['output_file'])
    validated_output = output_path.parent / f"{output_path.stem}_validated.json"

    with open(validated_output, 'w') as f:
        json.dump(data, f, indent=2)

    print(f"✓ Validated output saved: {validated_output}")
    print()

    return {
        'output_file': str(validated_output),
        'groups': groups,
        'meta': data['meta'],
        'validation_summary': {
            'groups_validated': validated_count,
            'groups_repaired': repaired_count,
            'validation_cost': total_validation_cost
        }
    }
