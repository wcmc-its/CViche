#!/usr/bin/env python3
"""
Quick test for Phase 2 Session 5 LLM-based fixes.

Tests:
- Fix #22: Research narrative vs output distinction
- Fix #23: A vs D disambiguation guidance
- Fix #21: LLM context repair for unknowns (function test only, no actual LLM calls)
"""

import sys


def test_fix22_research_narrative():
    """Test Fix #22: Research narrative vs output distinction."""
    print("Testing Fix #22: Research Narrative vs Output Distinction")
    print("=" * 70)

    from taxonomy_mapper_v2 import distinguish_research_narrative_vs_output

    # Test case: Research Interests (narrative) misclassified as S
    pass1_result = {
        'parent_section_id': 'S',
        'parent_canonical_name': 'Bibliography',
        'reasoning': 'Original LLM classification'
    }

    section_label = 'Research Interests'
    sample_entries = [
        'My research focuses on developing novel computational methods',
        'I am interested in machine learning applications in biology',
        'Investigating the role of genetics in disease susceptibility'
    ]

    result = distinguish_research_narrative_vs_output(
        pass1_result=pass1_result,
        section_label=section_label,
        sample_entries=sample_entries
    )

    # Should reclassify S → E
    expected_section = 'E'
    status = "✓" if result['parent_section_id'] == expected_section else "✗"
    print(f"{status} Research Interests reclassified: S → {result['parent_section_id']} (expected: E)")

    has_reclassification_note = 'RECLASSIFIED by PHASE 2 FIX #22' in result['reasoning']
    status = "✓" if has_reclassification_note else "✗"
    print(f"{status} Reasoning includes reclassification note: {has_reclassification_note}")

    print()


def test_fix23_disambiguation_guidance():
    """Test Fix #23: A vs D disambiguation guidance."""
    print("Testing Fix #23: Personal Information vs Employment Guidance")
    print("=" * 70)

    from taxonomy_mapper_v2 import PERSONAL_INFO_VS_EMPLOYMENT_GUIDANCE

    # Check that constant exists and has expected content
    status = "✓" if PERSONAL_INFO_VS_EMPLOYMENT_GUIDANCE else "✗"
    print(f"{status} Disambiguation guidance constant loaded: {bool(PERSONAL_INFO_VS_EMPLOYMENT_GUIDANCE)}")

    guidance_length = len(PERSONAL_INFO_VS_EMPLOYMENT_GUIDANCE)
    status = "✓" if guidance_length > 500 else "✗"
    print(f"{status} Guidance length: {guidance_length} chars (expected: >500)")

    has_a_vs_d = 'Personal Information (A)' in PERSONAL_INFO_VS_EMPLOYMENT_GUIDANCE
    has_a_vs_d &= 'Employment/Positions (D)' in PERSONAL_INFO_VS_EMPLOYMENT_GUIDANCE
    status = "✓" if has_a_vs_d else "✗"
    print(f"{status} Contains A vs D guidance: {has_a_vs_d}")

    has_red_flags = 'RED FLAGS' in PERSONAL_INFO_VS_EMPLOYMENT_GUIDANCE
    status = "✓" if has_red_flags else "✗"
    print(f"{status} Contains RED FLAGS section: {has_red_flags}")

    print()


def test_fix21_function_import():
    """Test Fix #21: LLM context repair function can be imported."""
    print("Testing Fix #21: LLM Context Repair Function")
    print("=" * 70)

    try:
        from repair_segmentation import llm_context_repair_for_unknowns

        status = "✓"
        print(f"{status} Function imported successfully: llm_context_repair_for_unknowns")

        # Test with mock data (no LLM calls)
        groups = [
            {
                'id': 'G1',
                'label_inferred': 'Education',
                'entries': [{'text_snippet': 'Ph.D. in Biology'}],
                'subgroups': []
            },
            {
                'id': 'G2',
                'label_inferred': 'Unknown',
                'entries': [],  # No entries - should skip LLM call
                'subgroups': []
            }
        ]

        # This should return groups unchanged (no LLM calls for empty unknowns)
        result = llm_context_repair_for_unknowns(groups, verbose=False)

        status = "✓" if len(result) == 2 else "✗"
        print(f"{status} Function executes without errors (2 groups → {len(result)} groups)")

        # Check that empty unknown was kept
        unknown_still_exists = any('unknown' in g.get('label_inferred', '').lower() for g in result)
        status = "✓" if unknown_still_exists else "✗"
        print(f"{status} Empty unknown preserved (no LLM call): {unknown_still_exists}")

    except Exception as e:
        print(f"✗ Error importing or testing function: {e}")
        import traceback
        traceback.print_exc()

    print()


def test_repair_segmentation_integration():
    """Test that repair_segmentation accepts enable_llm_repair parameter."""
    print("Testing repair_segmentation Integration")
    print("=" * 70)

    try:
        from repair_segmentation import repair_segmentation
        import inspect

        # Check function signature
        sig = inspect.signature(repair_segmentation)
        params = list(sig.parameters.keys())

        has_enable_param = 'enable_llm_repair' in params
        status = "✓" if has_enable_param else "✗"
        print(f"{status} repair_segmentation has enable_llm_repair parameter: {has_enable_param}")

        if has_enable_param:
            default_value = sig.parameters['enable_llm_repair'].default
            is_false_by_default = default_value == False
            status = "✓" if is_false_by_default else "✗"
            print(f"{status} enable_llm_repair defaults to False: {is_false_by_default} (default={default_value})")

    except Exception as e:
        print(f"✗ Error checking repair_segmentation integration: {e}")
        import traceback
        traceback.print_exc()

    print()


def main():
    """Run all tests."""
    print()
    print("=" * 70)
    print("PHASE 2 SESSION 5 - LLM-BASED FIX TESTS")
    print("=" * 70)
    print()

    try:
        test_fix22_research_narrative()
        test_fix23_disambiguation_guidance()
        test_fix21_function_import()
        test_repair_segmentation_integration()

        print("=" * 70)
        print("✓ ALL TESTS COMPLETED")
        print("=" * 70)
        print()
        print("Note: Fix #21 LLM calls are not tested here (would require OpenAI API).")
        print("      Only function structure and integration are validated.")
        print()

    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == '__main__':
    exit(main())
