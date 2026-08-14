"""FIX #400: hierarchy-consistency backward scan must find a genuine ancestor.

Regression for _enforce_hierarchy_consistency demoting a KNOWN_SUBSECTION_TERMS
header wrongly detected at level 1 to a child of the first preceding
non-subsection header, even when that header is a sibling or cousin (same or
deeper level) rather than an actual ancestor. See issue #400.

The fix falls back to the nearest preceding L1 header (the alternative named
directly in #400's own fix sketch), because a "strictly shallower than this
header's own level" scan is impossible at the point of demotion -- the
header's recorded level is always 1 there, and no header level is < 1. Every
test below fails against the pre-#400 code (nearest non-subsection header,
regardless of level) and passes against the fix -- confirmed by temporarily
reverting the source change and rerunning this file alone.
"""
from unified_pipeline.segmentation.header_detection import (
    _enforce_hierarchy_consistency,
)


def _header(text, level):
    return {"text": text, "level": level, "signals": []}


def test_sibling_not_child_of_shallow_sibling():
    # [H1 Grants, H2 Federal, H1 Current] -- "Current" is a subsection term
    # wrongly detected at H1. "Federal" (level 2) is a sibling, not an
    # ancestor, of "Current"; the real parent is "Grants" (level 1), so
    # "Current" should land as its sibling at level 2, not as a child of
    # "Federal" at level 3.
    headers = [
        _header("Grants", 1),
        _header("Federal", 2),
        _header("Current", 1),
    ]

    result = _enforce_hierarchy_consistency(headers)

    assert result[2]["text"] == "Current"
    assert result[2]["level"] == 2


def test_sibling_not_child_of_deeper_cousin():
    # [H1 Research, H2 Grant Support, H3 Aims, H1 Pending] -- "Pending" is a
    # subsection term wrongly detected at H1. "Aims" (level 3) is deeper than
    # the intended ancestor -- a descendant of "Grant Support", not a cousin
    # of "Pending" -- and must not be selected as the parent; the real
    # parent is "Research" (level 1), so "Pending" should land as a sibling
    # of "Grant Support" at level 2, not as a child of "Aims" at level 4
    # (clamped to 3).
    headers = [
        _header("Research", 1),
        _header("Grant Support", 2),
        _header("Aims", 3),
        _header("Pending", 1),
    ]

    result = _enforce_hierarchy_consistency(headers)

    assert result[3]["text"] == "Pending"
    assert result[3]["level"] == 2


def test_two_prior_top_level_sections_uses_nearest_one():
    # [H1 A, H2 B, H1 C, H2 D, H1 Pending] -- two L1 sections precede the
    # subsection term this time, so the pre-#400 bug (nearest
    # non-subsection header regardless of level -> "D", H2 -> H3) and the
    # fix (nearest L1 -> "C" -> H2) disagree on both WHICH header is chosen
    # and the resulting level, not just by coincidence of a single L1 in
    # scope like the two fixtures above.
    headers = [
        _header("A", 1),
        _header("B", 2),
        _header("C", 1),
        _header("D", 2),
        _header("Pending", 1),
    ]

    result = _enforce_hierarchy_consistency(headers)

    assert result[4]["text"] == "Pending"
    assert result[4]["level"] == 2


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all passed")
