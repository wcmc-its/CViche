#!/usr/bin/env python3
"""
Quick test for Phase 2 Session 1 signal additions.

Tests:
- Fix #7: URL/ORCID detection
- Fix #11: Grant number patterns
- Fix #14: Date-only line detection
- Fix #19: Conference pattern detection
"""

from confusion_matrix import compute_structural_hints


def test_url_orcid_detection():
    """Test Fix #7: URL/ORCID pattern detection."""
    print("Testing Fix #7: URL/ORCID Detection")
    print("=" * 60)

    # Test URL detection
    test_cases = [
        ("Website: https://www.example.com", True, "URL"),
        ("LinkedIn: linkedin.com/in/johndoe", True, "URL"),
        ("Email: john@example.com", False, "URL"),  # Should trigger email, not URL
        ("ORCID: 0000-0002-1825-0097", True, "ORCID"),
        ("Regular text without links", False, "URL"),
    ]

    for text, should_detect, signal_type in test_cases:
        result = compute_structural_hints(text, "")
        scores = result['scores']

        if signal_type == "URL":
            detected = scores.get('url_pattern', 0.0) >= 0.8
        else:  # ORCID
            detected = scores.get('orcid_pattern', 0.0) >= 0.8

        status = "✓" if detected == should_detect else "✗"
        print(f"{status} {text[:40]:40} -> {signal_type}: {detected}")

    print()


def test_grant_number_detection():
    """Test Fix #11: Grant number pattern detection."""
    print("Testing Fix #11: Grant Number Detection")
    print("=" * 60)

    test_cases = [
        ("NIH R01-HL123456, Principal Investigator", True),
        ("K99AG067890 Career Development Award", True),
        ("U01HL098765 Cooperative Agreement", True),
        ("P01CA123456 Program Project Grant", True),
        ("Regular research without grant number", False),
        ("Award for Excellence in Teaching", False),
    ]

    for text, should_detect in test_cases:
        result = compute_structural_hints(text, "")
        scores = result['scores']
        detected = scores.get('grant_number_patterns', 0.0) >= 0.8

        status = "✓" if detected == should_detect else "✗"
        print(f"{status} {text[:50]:50} -> {detected}")

    print()


def test_date_only_detection():
    """Test Fix #14: Date-only line detection."""
    print("Testing Fix #14: Date-Only Line Detection")
    print("=" * 60)

    test_cases = [
        ("2015-2020", True),
        ("Jan 2018 - Dec 2020", True),
        ("2015 - present", True),
        ("September 2019", True),
        ("2020", True),
        ("Research activities during 2015-2020 period", False),
        ("Professor of Medicine", False),
    ]

    for text, should_detect in test_cases:
        result = compute_structural_hints(text, "")
        scores = result['scores']
        detected = scores.get('date_only_line', 0.0) >= 0.8

        status = "✓" if detected == should_detect else "✗"
        print(f"{status} {text[:40]:40} -> {detected}")

    print()


def test_conference_detection():
    """Test Fix #19: Conference pattern detection."""
    print("Testing Fix #19: Conference Pattern Detection")
    print("=" * 60)

    test_cases = [
        ("Society for Neuroscience Annual Meeting, 2020", True),
        ("Poster presentation at AHA Scientific Sessions", True),
        ("International Conference on Machine Learning", True),
        ("Workshop on Clinical Research Methods", True),
        ("Journal of Medicine, 2020;15(3):123-145", False),
        ("Professor of Clinical Research", False),
    ]

    for text, should_detect in test_cases:
        result = compute_structural_hints(text, "")
        scores = result['scores']
        detected = scores.get('conference_pattern', 0.0) >= 0.8

        status = "✓" if detected == should_detect else "✗"
        print(f"{status} {text[:50]:50} -> {detected}")

    print()


def test_hint_messages():
    """Test that hint messages are generated correctly."""
    print("Testing Hint Message Generation")
    print("=" * 60)

    test_cases = [
        ("ORCID: 0000-0002-1825-0097", "ORCID identifier detected"),
        ("R01-HL123456", "NIH/NSF grant number detected"),
        ("2015-2020", "date/date range"),
        ("Annual Conference on Neuroscience", "Conference/meeting/symposium"),
    ]

    for text, expected_hint_fragment in test_cases:
        result = compute_structural_hints(text, "")
        hints = result['triggered_hints']

        found = any(expected_hint_fragment in hint for hint in hints)
        status = "✓" if found else "✗"

        print(f"{status} {text[:40]:40}")
        if found:
            matching_hints = [h for h in hints if expected_hint_fragment in h]
            for hint in matching_hints:
                print(f"   -> {hint[:70]}...")
        print()


def main():
    """Run all tests."""
    print()
    print("="*70)
    print("PHASE 2 SESSION 1 - SIGNAL DETECTION TESTS")
    print("="*70)
    print()

    try:
        test_url_orcid_detection()
        test_grant_number_detection()
        test_date_only_detection()
        test_conference_detection()
        test_hint_messages()

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
