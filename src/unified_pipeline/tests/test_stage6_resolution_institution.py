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

from unified_pipeline.stage6.resolution import _location_already_in_institution  # noqa: E402


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
    ("", "Massachusetts General Hospital"),
    ("Boston, MA", ""),
    (", MA", "Boston, MA"),  # no city segment
])
def test_degenerate_inputs_are_false(location, institution):
    assert _location_already_in_institution(location, institution) is False
