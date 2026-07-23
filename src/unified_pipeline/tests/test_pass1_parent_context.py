"""FIX #312 Part B: document-order parent context for orphaned sub-labels.

Bare geographic sub-labels (Regional/National/International) are ambiguous —
'national' is a child of BOTH R (National Invitations) and Q (National
Boards/Committees). The flat segmenter strips the parent, so PASS-1 can
misroute (on C0ZGFW the National invited-presentations table went to K).
The disambiguator is the canonical parent that precedes it in document order.
See issue #312.
"""
from unified_pipeline.core.taxonomy_mapper_v2 import apply_geographic_sublabel_pin


def _r(parent, name="x", conf=0.9):
    return {"parent_section_id": parent, "parent_canonical_name": name,
            "confidence": conf, "reasoning": "content reads as teaching"}


def test_national_under_invited_presentations_pins_R():
    # The C0ZGFW bug: National table content-classified K, parent (doc order) = R.
    r = apply_geographic_sublabel_pin(_r("K"), "National", "R")
    assert r["parent_section_id"] == "R"
    assert r["confidence"] >= 0.90
    assert "#312B" in r["reasoning"]


def test_national_under_extramural_pins_Q():
    # Same label, different parent -> different section (Q3 National Committees).
    assert apply_geographic_sublabel_pin(_r("K"), "National", "Q")["parent_section_id"] == "Q"


def test_geo_under_non_geo_parent_left_alone():
    # 'International' following BIBLIOGRAPHY (S) must NOT be pulled to R —
    # S has no geographic children, so the rule does not fire.
    assert apply_geographic_sublabel_pin(_r("S2"), "International", "S")["parent_section_id"] == "S2"


def test_no_effective_parent_left_alone():
    assert apply_geographic_sublabel_pin(_r("K"), "National", None)["parent_section_id"] == "K"


def test_non_geographic_label_left_alone():
    # Chapters is a sub-label but not geographic; the geo pin ignores it
    # (it relies on the parent-hint path instead).
    assert apply_geographic_sublabel_pin(_r("K"), "Chapters", "S")["parent_section_id"] == "K"


def test_boilerplate_T_left_alone():
    assert apply_geographic_sublabel_pin(_r("T"), "National", "R")["parent_section_id"] == "T"


def test_escape_hatch_left_alone():
    assert apply_geographic_sublabel_pin(_r("MIXED_CONTENT"), "National", "R")["parent_section_id"] == "MIXED_CONTENT"


def test_noop_when_already_matches_parent():
    r = apply_geographic_sublabel_pin(_r("R"), "National", "R")
    assert r["parent_section_id"] == "R"
    assert "#312B" not in r["reasoning"]


def test_case_and_prefix_insensitive():
    assert apply_geographic_sublabel_pin(_r("K"), "  INTERNATIONAL  ", "R")["parent_section_id"] == "R"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all passed")
