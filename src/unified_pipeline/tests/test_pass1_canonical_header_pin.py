"""FIX #312: canonical WCM section header trumps content in PASS-1.

Regression for C0ZGFW, where an all-POCUS CV had "INSTITUTIONAL LEADERSHIP
ACTIVITIES" content-classified to K (Educational Contributions), emptying
section O. See issue #312.
"""
from unified_pipeline.core.taxonomy_mapper_v2 import (
    apply_canonical_header_pin,
    _normalize_header,
)


def _result(parent, name="x", conf=0.9):
    return {"parent_section_id": parent, "parent_canonical_name": name,
            "confidence": conf, "reasoning": "content says teaching"}


def test_rescues_leadership_misclassified_as_teaching():
    # The C0ZGFW bug: header O, content-classified K4.
    r = apply_canonical_header_pin(_result("K4"), "INSTITUTIONAL LEADERSHIP ACTIVITIES")
    assert r["parent_section_id"] == "O"
    assert r["confidence"] >= 0.90
    assert "#312" in r["reasoning"]


def test_case_and_prefix_insensitive():
    r = apply_canonical_header_pin(_result("K"), "O.  institutional / leadership, activities")
    assert r["parent_section_id"] == "O"


def test_noop_when_header_already_correct():
    r = apply_canonical_header_pin(_result("O"), "INSTITUTIONAL LEADERSHIP ACTIVITIES")
    assert r["parent_section_id"] == "O"
    assert "#312" not in r["reasoning"]  # untouched


def test_ambiguous_sublabel_not_pinned():
    # "National"/"Regional" are orphaned sub-labels, not canonical titles.
    # They need the hierarchy-nesting fix (#312 Part B), NOT this pin.
    r = apply_canonical_header_pin(_result("K4"), "National")
    assert r["parent_section_id"] == "K4"


def test_alias_not_pinned():
    # "Seminars" is an R alias but legitimately reads as teaching (K1).
    # Canonical-only: do not pin on aliases.
    r = apply_canonical_header_pin(_result("K1"), "Seminars")
    assert r["parent_section_id"] == "K1"


def test_escape_hatch_left_alone():
    # PASS-1 escape hatches all contain '_'; never override them.
    r = apply_canonical_header_pin(_result("NOT_VALID_SECTION"), "Institutional Leadership Activities")
    assert r["parent_section_id"] == "NOT_VALID_SECTION"


def test_invitations_canonical_pins_to_R():
    r = apply_canonical_header_pin(_result("K4"), "Invitations to Speak/Present")
    assert r["parent_section_id"] == "R"


def test_honors_pins_to_H_not_I():
    # Map-source guard: PARENT_SECTIONS has H=Honors, I=Organizations.
    # CV_SECTIONS swaps them — building from it misrouted ~5 CVs (corpus-measured).
    assert apply_canonical_header_pin(_result("K"), "Honors and Awards")["parent_section_id"] == "H"
    assert apply_canonical_header_pin(_result("K"),
        "Professional Organizations and Society Memberships")["parent_section_id"] == "I"


def test_boilerplate_T_left_alone():
    # A 'T' (Appendix/Other) group is WCM template boilerplate; don't force it
    # into a real section (would surface instruction text as content).
    r = apply_canonical_header_pin(_result("T"), "Bibliography")
    assert r["parent_section_id"] == "T"


def test_normalize_strips_code_prefix():
    assert _normalize_header("O. Institutional Leadership Activities") == \
        _normalize_header("institutional leadership activities")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all passed")
