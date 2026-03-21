#!/usr/bin/env python3
"""
Quick test for Phase 2 Session 2 structural fixes.

Tests:
- Fix #8: Institution header merging
- Fix #9: Address block merging
- Fix #12: Repeated header deduplication
- Fix #13: N/A placeholder demotion
- Fix #15: Roman numeral deduplication
"""

from repair_segmentation import (
    merge_adjacent_institution_headers,
    merge_address_blocks,
    deduplicate_repeated_headers,
    demote_na_placeholders,
    deduplicate_roman_numeral_headers
)


def test_institution_header_merging():
    """Test Fix #8: Institution header merging."""
    print("Testing Fix #8: Institution Header Merging")
    print("=" * 60)

    # Test case: UMSOM + UNIVERSITY OF MARYLAND SCHOOL OF MEDICINE
    groups = [
        {
            'id': 'G1',
            'label_inferred': 'UMSOM',
            'entries': [],
            'subgroups': []
        },
        {
            'id': 'G2',
            'label_inferred': 'UNIVERSITY OF MARYLAND SCHOOL OF MEDICINE',
            'entries': [],
            'subgroups': []
        },
        {
            'id': 'G3',
            'label_inferred': 'EDUCATION',
            'entries': [],
            'subgroups': []
        }
    ]

    result = merge_adjacent_institution_headers(groups)

    # Should merge first two into one
    expected_count = 2  # G1+G2 merged, G3 separate
    status = "✓" if len(result) == expected_count else "✗"
    print(f"{status} Merged UMSOM + full name: {len(groups)} → {len(result)} groups")

    if len(result) == expected_count and len(result) > 0:
        merged_label = result[0].get('label_inferred', '')
        has_both = 'UMSOM' in merged_label and 'University' in merged_label
        status = "✓" if has_both else "✗"
        print(f"{status} Merged label contains both: '{merged_label[:50]}'")

    print()


def test_address_block_merging():
    """Test Fix #9: Address block merging."""
    print("Testing Fix #9: Address Block Merging")
    print("=" * 60)

    # Test case: Consecutive address lines
    groups = [
        {
            'id': 'G1',
            'label_inferred': 'STILES-NICHOLSON BRAIN INSTITUTE',
            'entries': [{'text_snippet': 'STILES-NICHOLSON BRAIN INSTITUTE'}],
            'subgroups': []
        },
        {
            'id': 'G2',
            'label_inferred': 'Room Info',
            'entries': [{'text_snippet': 'Rm 208G, MC-22'}],
            'subgroups': []
        },
        {
            'id': 'G3',
            'label_inferred': 'University',
            'entries': [{'text_snippet': 'FLORIDA ATLANTIC UNIVERSITY'}],
            'subgroups': []
        },
        {
            'id': 'G4',
            'label_inferred': 'City',
            'entries': [{'text_snippet': 'Jupiter, FL 33458'}],
            'subgroups': []
        },
        {
            'id': 'G5',
            'label_inferred': 'EDUCATION',
            'entries': [],
            'subgroups': []
        }
    ]

    result = merge_address_blocks(groups)

    # Should merge first 4 address components into 1
    expected_max = 2  # Address block + EDUCATION
    status = "✓" if len(result) <= expected_max else "✗"
    print(f"{status} Merged address block: {len(groups)} → {len(result)} groups")

    if len(result) > 0:
        first_group = result[0]
        is_personal_info = 'Personal Information' in first_group.get('label_inferred', '')
        is_address_block = first_group.get('meta', {}).get('address_block', False)
        status = "✓" if is_personal_info or is_address_block else "✗"
        print(f"{status} Address block labeled correctly: {first_group.get('label_inferred', '')}")

    print()


def test_repeated_header_deduplication():
    """Test Fix #12: Repeated header deduplication."""
    print("Testing Fix #12: Repeated Header Deduplication")
    print("=" * 60)

    # Test case: Multiple "Grants" headers
    groups = [
        {
            'id': 'G10',
            'label_inferred': 'Grants',
            'entries': [{'text_snippet': 'Grant 1'}],
            'subgroups': []
        },
        {
            'id': 'G15',
            'label_inferred': 'Grants',
            'entries': [{'text_snippet': 'Grant 2'}],
            'subgroups': []
        },
        {
            'id': 'G20',
            'label_inferred': 'Grants',
            'entries': [{'text_snippet': 'Grant 3'}],
            'subgroups': []
        },
        {
            'id': 'G25',
            'label_inferred': 'EDUCATION',
            'entries': [],
            'subgroups': []
        }
    ]

    result = deduplicate_repeated_headers(groups)

    # Should merge 3 "Grants" into 1
    expected_count = 2  # 1 Grants + 1 Education
    status = "✓" if len(result) == expected_count else "✗"
    print(f"{status} Deduplicated repeated headers: {len(groups)} → {len(result)} groups")

    if len(result) > 0:
        grants_group = next((g for g in result if 'grants' in g.get('label_inferred', '').lower()), None)
        if grants_group:
            entry_count = len(grants_group.get('entries', []))
            status = "✓" if entry_count == 3 else "✗"
            print(f"{status} Merged entries preserved: {entry_count} entries in Grants group")

    print()


def test_na_placeholder_demotion():
    """Test Fix #13: N/A placeholder demotion."""
    print("Testing Fix #13: N/A Placeholder Demotion")
    print("=" * 60)

    # Test case 1: N/A with subgroups
    groups = [
        {
            'id': 'G1',
            'label_inferred': 'N/A',
            'entries': [],
            'subgroups': [
                {
                    'id': 'G1.1',
                    'label_inferred': 'Real Section',
                    'level': 2,
                    'entries': [{'text_snippet': 'Content'}],
                    'subgroups': []
                }
            ]
        },
        {
            'id': 'G2',
            'label_inferred': 'EDUCATION',
            'entries': [],
            'subgroups': []
        }
    ]

    result = demote_na_placeholders(groups)

    # Should promote subgroup and remove N/A parent
    expected_count = 2  # Real Section (promoted) + EDUCATION
    status = "✓" if len(result) == expected_count else "✗"
    print(f"{status} N/A with subgroups: {len(groups)} → {len(result)} groups (N/A removed, child promoted)")

    # Test case 2: N/A without subgroups
    groups2 = [
        {
            'id': 'G1',
            'label_inferred': 'N/A',
            'entries': [{'text_snippet': 'N/A'}],
            'subgroups': []
        },
        {
            'id': 'G2',
            'label_inferred': 'EDUCATION',
            'entries': [],
            'subgroups': []
        }
    ]

    result2 = demote_na_placeholders(groups2)

    # Should remove N/A entirely
    expected_count2 = 1  # Only EDUCATION
    status = "✓" if len(result2) == expected_count2 else "✗"
    print(f"{status} N/A without subgroups: {len(groups2)} → {len(result2)} groups (N/A completely removed)")

    print()


def test_roman_numeral_deduplication():
    """Test Fix #15: Roman numeral deduplication."""
    print("Testing Fix #15: Roman Numeral Deduplication")
    print("=" * 60)

    # Test case: Duplicate Roman numeral headers
    groups = [
        {
            'id': 'G3',
            'label_inferred': 'I. GENERAL INFORMATION',
            'entries': [{'text_snippet': 'First occurrence'}],
            'subgroups': []
        },
        {
            'id': 'G10',
            'label_inferred': 'II. EDUCATION',
            'entries': [{'text_snippet': 'Education content'}],
            'subgroups': []
        },
        {
            'id': 'G15',
            'label_inferred': 'I. GENERAL INFORMATION',  # Duplicate
            'entries': [{'text_snippet': 'Second occurrence'}],
            'subgroups': []
        },
        {
            'id': 'G20',
            'label_inferred': 'III. EMPLOYMENT',
            'entries': [],
            'subgroups': []
        }
    ]

    result = deduplicate_roman_numeral_headers(groups)

    # Should merge duplicate "I. GENERAL INFORMATION"
    expected_count = 3  # 1 General Info + 1 Education + 1 Employment
    status = "✓" if len(result) == expected_count else "✗"
    print(f"{status} Deduplicated Roman numerals: {len(groups)} → {len(result)} groups")

    if len(result) > 0:
        gen_info = next((g for g in result if 'GENERAL INFORMATION' in g.get('label_inferred', '')), None)
        if gen_info:
            entry_count = len(gen_info.get('entries', []))
            status = "✓" if entry_count == 2 else "✗"
            print(f"{status} Merged duplicate entries: {entry_count} entries in General Information")

    print()


def main():
    """Run all tests."""
    print()
    print("="*70)
    print("PHASE 2 SESSION 2 - STRUCTURAL FIX TESTS")
    print("="*70)
    print()

    try:
        test_institution_header_merging()
        test_address_block_merging()
        test_repeated_header_deduplication()
        test_na_placeholder_demotion()
        test_roman_numeral_deduplication()

        print("="*70)
        print("✓ ALL TESTS COMPLETED")
        print("="*70)
        print()

    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == '__main__':
    exit(main())
