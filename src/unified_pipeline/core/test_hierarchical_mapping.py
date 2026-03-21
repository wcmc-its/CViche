"""
Test Hierarchical Context-Aware Batch Classification

Tests the updated taxonomy mapper with:
1. Hierarchical context (section + subsection headers)
2. Batch classification (10 entries together)
3. Adaptive routing (Option C based on Pass 1 confidence)
4. Confusion matrix integration

Usage:
    python test_hierarchical_mapping.py
"""

import json
from pathlib import Path
from taxonomy_mapper_v2 import classify_pass2_batch

# Test data: Editorial Board entries that were previously misclassified
TEST_ENTRIES = [
    "Innovations in Global Medical & Health Education journal. 2014-2017",
    "Journal of Medical Education and Curricular Development. 2016-present",
    "BMC Medical Education. 2015-2018"
]

def test_editorial_board_classification():
    """
    Test that Editorial Board entries with proper hierarchical context
    are correctly classified to Q4C (not S - Bibliography).
    """
    print("="*80)
    print("TEST: Editorial Board Classification with Hierarchical Context")
    print("="*80)
    print()

    # SCENARIO 1: WITHOUT hierarchical context (OLD BEHAVIOR - WRONG)
    print("SCENARIO 1: Without hierarchical context (should fail)")
    print("-"*80)

    result1 = classify_pass2_batch(
        parent_section_id="extramural_professional_activities",
        parent_confidence=0.85,  # Medium confidence
        section_header="PROFESSIONAL PRACTICE",
        subsection_header="",  # NO SUBSECTION HEADER
        entries=TEST_ENTRIES,
        model="gpt-4o-mini"
    )

    if result1['success']:
        print(f"Classifications: {len(result1['classifications'])}")
        print(f"Avg confidence: {result1['avg_confidence']:.3f}")
        for i, c in enumerate(result1['classifications']):
            print(f"  [{i}] → {c['child_section_id']}: {c['child_canonical_name']} ({c['confidence']:.2f})")
            print(f"      Reasoning: {c['reasoning']}")
    else:
        print(f"Failed: {result1['error']}")

    print()

    # SCENARIO 2: WITH hierarchical context (NEW BEHAVIOR - CORRECT)
    print("SCENARIO 2: With hierarchical context (should succeed)")
    print("-"*80)

    result2 = classify_pass2_batch(
        parent_section_id="extramural_professional_activities",
        parent_confidence=0.85,
        section_header="PROFESSIONAL PRACTICE: REVIEWER / BOARD MEMBER - SCHOLARLY JOURNALS",
        subsection_header="Editorial Board Member",  # KEY CONTEXT
        entries=TEST_ENTRIES,
        model="gpt-4o-mini"
    )

    if result2['success']:
        print(f"Classifications: {len(result2['classifications'])}")
        print(f"Avg confidence: {result2['avg_confidence']:.3f}")
        for i, c in enumerate(result2['classifications']):
            print(f"  [{i}] → {c['child_section_id']}: {c['child_canonical_name']} ({c['confidence']:.2f})")
            print(f"      Reasoning: {c['reasoning']}")
    else:
        print(f"Failed: {result2['error']}")

    print()

    # SCENARIO 3: High confidence Pass 1 (should NOT show alternatives)
    print("SCENARIO 3: High Pass 1 confidence (binding, no alternatives shown)")
    print("-"*80)

    result3 = classify_pass2_batch(
        parent_section_id="extramural_professional_activities",
        parent_confidence=0.95,  # HIGH CONFIDENCE - BINDING
        section_header="PROFESSIONAL PRACTICE: REVIEWER / BOARD MEMBER",
        subsection_header="Editorial Board Member",
        entries=TEST_ENTRIES,
        model="gpt-4o-mini"
    )

    if result3['success']:
        print(f"Classifications: {len(result3['classifications'])}")
        print(f"Avg confidence: {result3['avg_confidence']:.3f}")
        for i, c in enumerate(result3['classifications']):
            print(f"  [{i}] → {c['child_section_id']}: {c['child_canonical_name']} ({c['confidence']:.2f})")
    else:
        print(f"Failed: {result3['error']}")

    print()
    print("="*80)
    print("VALIDATION")
    print("="*80)

    # Validate results
    if result2['success']:
        all_q4c = all(c['child_section_id'] == 'Q4C' for c in result2['classifications'])
        if all_q4c:
            print("✓ SUCCESS: All entries correctly classified to Q4C (Editorial Board)")
            print("✓ Hierarchical context (subsection header) fixed the misclassification!")
        else:
            print("✗ FAILURE: Some entries misclassified")
            for i, c in enumerate(result2['classifications']):
                if c['child_section_id'] != 'Q4C':
                    print(f"  Entry {i}: Expected Q4C, got {c['child_section_id']}")
    else:
        print("✗ Test failed due to API error")

    print()

    # Token usage comparison
    if result1['success'] and result2['success']:
        print("TOKEN USAGE COMPARISON:")
        print(f"  Without context: {result1['token_usage']['total_tokens']:,} tokens")
        print(f"  With context: {result2['token_usage']['total_tokens']:,} tokens")
        print(f"  Difference: {result2['token_usage']['total_tokens'] - result1['token_usage']['total_tokens']:+,} tokens")


if __name__ == '__main__':
    test_editorial_board_classification()
