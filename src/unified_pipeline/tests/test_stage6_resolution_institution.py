"""`_location_already_in_institution` (stage6/resolution/institution.py).

The predicate every location-appending renderer (education, postdoc training,
positions) consults before adding ", City, ST" to an institution cell. It used
to match the bare city WORD anywhere in the name, so "New York University",
"New York Presbyterian" and "Boston Children's" all lost their location --
faculty feedback 2026-09-15, #897. It now matches the location only as a
comma-led tail of the string.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_resolution_institution.py -p no:cacheprovider
"""

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage6.resolution import (  # noqa: E402
    _get_institution_location,
    _location_already_in_institution,
    _recover_institution_from_nearby_entries,
)
from unified_pipeline.stage6.resolution.institution import _TRAINING_CODES  # noqa: E402
from unified_pipeline.stage6.sections.postdoc_training import POSTDOC_TRAINING_CODES  # noqa: E402


@pytest.mark.parametrize("location, institution", [
    # the #897 class: the city is part of the NAME, not an embedded location
    ("New York, NY", "New York University School of Medicine"),
    ("New York, NY", "New York Presbyterian – Weill Cornell Medical Center"),
    ("Boston, MA", "Boston Children's Hospital"),
    # a comma-led city that is not a trailing location
    ("New York, NY", "Department of Medicine, New York University"),
    # nothing in common
    ("New York, NY", "Bellevue Hospital Center"),
])
def test_city_inside_the_name_is_not_a_location(location, institution):
    assert _location_already_in_institution(location, institution) is False


@pytest.mark.parametrize("location, institution", [
    ("Boston, MA", "Massachusetts General Hospital, Boston, MA"),
    ("Pittsburgh, PA", "University of Pittsburgh, Pittsburgh, PA"),
    ("New York, NY", "Columbia University, New York"),                 # city only
    ("New York, NY", "Weill Cornell Medical College, New York, New York"),  # state spelt out
    ("new york, ny", "Columbia University, NEW YORK, NY"),             # case
])
def test_trailing_location_is_detected(location, institution):
    assert _location_already_in_institution(location, institution) is True


@pytest.mark.parametrize("location, institution", [
    # same city, two DIFFERENT US states: a different place (#566)
    ("Rochester, MN", "Mayo Clinic, Rochester, NY"),
    ("Portland, OR", "Maine Medical Center, Portland, ME"),
    ("Columbia, SC", "University of Missouri, Columbia, Missouri"),
    ("Springfield, IL", "Baystate Medical Center, Springfield, Massachusetts"),
    ("Springfield, IL", "Baystate Medical Center, Springfield, M. A."),
    ("Rochester, MN", "Mayo Clinic, Rochester, New York"),      # two-word spelt-out state
    ("Rochester, MN, USA", "Mayo Clinic, Rochester, NY"),        # state is the SECOND segment
    # a city that is only a substring of another word or city
    ("York, PA", "Mount Sinai Hospital, New York, NY"),
    ("Paris, France", "Parish Medical Center"),
    ("Paris, France", "Sorbonne, Parisian Campus"),
    ("Rochester, NY", "Mayo Clinic, Rochester Hills, MI"),
])
def test_same_city_other_us_state_is_not_already_present(location, institution):
    assert _location_already_in_institution(location, institution) is False


@pytest.mark.parametrize("location, institution", [
    # one place written two ways must dedup: token forms differ, place does not
    ("Durham, NC", "Duke University, Durham, North Carolina"),
    ("Durham, North Carolina", "Duke University, Durham, NC"),
    ("Durham, NC", "Duke University, Durham, north carolina"),
    ("New York, NY", "Memorial Sloan Kettering Cancer Center, New York, N.Y."),
    ("New York, NY", "Memorial Sloan Kettering Cancer Center, New York, N. Y."),
    ("Washington, D. C.", "Howard University, Washington, DC"),
    ("Toronto, Canada", "Sick Kids, Toronto, Ontario"),
    ("Toronto, Canada", "Sick Kids, Toronto, ON"),
    ("Toronto, ON", "Sick Kids, Toronto, Canada"),
    ("New York, NY", "Columbia, New York, USA"),
    ("New York, NY", "Columbia, New York, U.S.A."),
    ("New York, NY", "Columbia, New York, United States"),
    ("London, United Kingdom", "King's College, London, UK"),
    ("London, United Kingdom", "King's College, London, U.K."),
    ("London, United Kingdom", "King's College, London, England"),
    ("Cambridge, United Kingdom", "Cambridge University, Cambridge, England"),
    ("Cambridge, United Kingdom", "Cambridge University, Cambridge, UK"),
    ("Houston, TX", "Baylor, Houston, Tex."),
    ("Philadelphia, PA", "Penn, Philadelphia, Penn."),
    ("Sao Paulo, Brazil", "USP, Sao Paulo, SP"),
    ("Sao Paulo, Brazil", "USP, Sao Paulo, Brasil"),
    ("New York, NY 10065", "Weill Cornell, New York, NY"),   # ZIP on the location
    # city-only either side
    ("Boston", "Beth Israel, Boston, MA"),
    ("Boston, MA", "Beth Israel, Boston"),
    # residual: a US state against a foreign country is not told apart
    ("Cambridge, MA", "University of Cambridge, Cambridge, UK"),
])
def test_same_place_written_two_ways_still_counts_as_present(location, institution):
    assert _location_already_in_institution(location, institution) is True


@pytest.mark.parametrize("location, institution", [
    ("", "Massachusetts General Hospital"),
    ("Boston, MA", ""),
    (", MA", "Boston, MA"),  # no city segment
])
def test_degenerate_inputs_are_false(location, institution):
    assert _location_already_in_institution(location, institution) is False


@pytest.mark.parametrize("enrichment", [["a"], "abc"])
def test_non_mapping_enrichment_falls_back_to_no_enrichment(enrichment):
    """#743: a list- or str-valued institution_enrichment (a malformed
    stage-5b LLM output) used to raise AttributeError on `.get`, which
    failed every section that reads it; `_render_section` then sent that
    section to the Appendix. It must fall back to the
    extracted_fields.location result instead, not from enrichment."""
    entry = {"institution_enrichment": enrichment, "extracted_fields": {}}
    assert _get_institution_location(entry) == ("", False)


# --- _recover_institution_from_nearby_entries (#1038) ----------------------

def _e(idx, code, institution=None, text="Synthetic entry, 1999-2001\tMD", hier=("Training",)):
    fields = {"institution": institution} if institution is not None else {}
    return {"element_idx_start": idx, "element_idx_end": idx, "taxonomy_code": code,
            "hierarchy": list(hier), "text": text, "extracted_fields": fields}


def test_recovery_codes_match_the_postdoc_table():
    assert tuple(_TRAINING_CODES) == tuple(POSTDOC_TRAINING_CODES)


def test_recovers_the_nearest_preceding_training_institution_field():
    target = _e(20, "C2")
    entries = [_e(18, "C", "Far Hospital"), _e(19, "C3", " Near Hospital "), target,
               _e(21, "C", "Later Hospital")]
    assert _recover_institution_from_nearby_entries(target, entries) == "Near Hospital"


def test_nearest_wins_whatever_the_list_order():
    target = _e(20, "C2")
    entries = [_e(19, "C", "Near Hospital"), _e(18, "C", "Far Hospital"), target]
    assert _recover_institution_from_nearby_entries(target, entries) == "Near Hospital"


def test_whitespace_only_institution_is_skipped():
    target = _e(20, "C2")
    entries = [_e(18, "C", "Real Hospital"), _e(19, "C", "  "), target]
    assert _recover_institution_from_nearby_entries(target, entries) == "Real Hospital"


@pytest.mark.parametrize("code", ["A", "B1", "K4", "Q2", "T"])
def test_never_takes_from_a_non_training_code(code):
    target = _e(20, "C2")
    entries = [_e(19, code, "School of Medicine, Somewhere"), target]
    assert _recover_institution_from_nearby_entries(target, entries) == ""


def test_never_returns_raw_text_or_a_following_entry():
    target = _e(20, "C2")
    entries = [_e(19, "C", None, text="University of Nowhere Hospital 2001"),
               target, _e(21, "C", "Later Hospital")]
    assert _recover_institution_from_nearby_entries(target, entries) == ""


def test_other_block_is_not_the_same_block():
    target = _e(20, "C2")
    entries = [_e(19, "C", "Other Block Hospital", hier=("Elsewhere",)), target]
    assert _recover_institution_from_nearby_entries(target, entries) == ""


@pytest.mark.parametrize("start", [None, "x", -1])
def test_unusable_start_index_recovers_nothing(start):
    target = _e(20, "C2")
    target["element_idx_start"] = start
    assert _recover_institution_from_nearby_entries(target, [_e(19, "C", "H")]) == ""
