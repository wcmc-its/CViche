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
)


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
    # same city, different state or country: a different place (#566)
    ("Rochester, MN", "Mayo Clinic, Rochester, NY"),
    ("Portland, OR", "Maine Medical Center, Portland, ME"),
    ("Cambridge, MA", "University of Cambridge, Cambridge, UK"),
    ("Cambridge, MA", "University of Cambridge, Cambridge, U.K."),
    # known limit: no country table, so "United Kingdom" vs "UK" is left undeduped (both render)
    ("Cambridge, United Kingdom", "Cambridge University, Cambridge, UK"),
    ("Cambridge, United Kingdom", "Harvard University, Cambridge, MA"),
    ("Columbia, SC", "University of Missouri, Columbia, Missouri"),
    ("Springfield, IL", "Baystate Medical Center, Springfield, Massachusetts"),
    # a city that is only a substring of another word or city
    ("York, PA", "Mount Sinai Hospital, New York, NY"),
    ("Paris, France", "Parish Medical Center"),
    ("Paris, France", "Sorbonne, Parisian Campus"),
    ("Rochester, NY", "Mayo Clinic, Rochester Hills, MI"),
])
def test_same_city_other_state_or_country_is_not_already_present(location, institution):
    assert _location_already_in_institution(location, institution) is False


@pytest.mark.parametrize("location, institution", [
    ("Durham, NC", "Duke University, Durham, North Carolina"),   # spelt-out state
    ("Durham, North Carolina", "Duke University, Durham, NC"),
    ("Durham, NC", "Duke University, Durham, north carolina"),  # case of a spelt-out state
    ("New York, NY", "Memorial Sloan Kettering Cancer Center, New York, N.Y."),  # dotted abbreviation
    ("Cambridge, United Kingdom", "Cambridge University, Cambridge, united kingdom"),
    ("Rochester, NY", "Univ of Rochester, Rochester, NY"),
    ("Boston", "Beth Israel, Boston, MA"),          # location without a state
    ("Boston, MA", "Beth Israel, Boston"),          # institution without a state
])
def test_matching_or_absent_state_still_counts_as_present(location, institution):
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
