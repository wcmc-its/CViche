"""Shared date parsing for stage 6 (#266).

format_date_for_section (rendering) and extract_sort_date (reverse-chron
sorting) used to carry near-duplicate date parsers that had drifted: the sort
copy's month-name pattern lacked the optional trailing period, so "Aug. 2021"
and "Sept. 2019" failed every branch and the entry sorted to the BOTTOM of its
section while still rendering its date correctly. Both now share
_parse_date_components, so they can't drift and the abbreviated-with-period
months parse.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_date_parsing.py -p no:cacheprovider
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest  # noqa: E402

from unified_pipeline.stage_6_word_template import (  # noqa: E402
    _parse_date_components,
    extract_sort_date,
    format_date_for_section,
)


def _sort(date_str):
    return extract_sort_date({"extracted_fields": {"end_date": date_str}})


# --- the #266 regression: abbreviated month with a trailing period ----------

@pytest.mark.parametrize("date_str, expected", [
    ("Aug. 2021", (2021, 8, 1)),
    ("Sept. 2019", (2019, 9, 1)),
    ("aug. 2021", (2021, 8, 1)),        # extract_sort_date lowercases first
    ("August 2021", (2021, 8, 1)),      # unabbreviated still works
])
def test_abbreviated_month_no_longer_sorts_last(date_str, expected):
    got = _sort(date_str)
    assert got == expected, f"{date_str!r} -> {got}, expected {expected}"
    assert got != (0, 0, 0), f"{date_str!r} fell through to the sort-last tuple"


def test_month_precision_is_preserved_within_a_year():
    # Guards against the shared helper flattening month to a constant: August
    # must still sort ahead of March in reverse-chronological order.
    assert _sort("August 2021") > _sort("March 2021")


# --- _parse_date_components: the single source of truth ---------------------

@pytest.mark.parametrize("date_str, expected", [
    ("2019-05-15", (2019, 5, 15)),
    ("05/15/2019", (2019, 5, 15)),
    ("2019-05", (2019, 5, None)),
    ("05/2019", (2019, 5, None)),
    ("2019", (2019, None, None)),
    ("March 2020", (2020, 3, None)),
    ("Mar. 2020", (2020, 3, None)),
    ("Foobar 2021", (2021, None, None)),   # unknown month name -> year only
    ("", (None, None, None)),
    ("garbage", (None, None, None)),
])
def test_parse_date_components(date_str, expected):
    assert _parse_date_components(date_str) == expected


def test_both_functions_use_the_shared_parser():
    # format (rendering) and extract_sort_date (sorting) must agree on the year
    # and month a string yields -- that is the whole point of sharing the parser.
    for date_str in ("Aug. 2021", "March 2020", "05/2019", "2019-05-15"):
        year, month, _ = _parse_date_components(date_str)
        sort_year, sort_month, _ = _sort(date_str)
        assert sort_year == year
        assert sort_month == (month if month is not None else 1)


# --- format_date_for_section output unchanged for common cases --------------

@pytest.mark.parametrize("date_str, code, expected", [
    ("2019", "N1", "2019"),
    ("August 2021", "M2A", "08/21"),   # mm/yy: month must survive the refactor
    ("05/2019", "M2A", "05/19"),
    ("2019-05-15", "M2A", "05/19"),
    ("present", "M2A", "Present"),
    ("", "M2A", ""),
    ("2019-05-15", "M2D", "05/2019"),  # day-precision: day dropped, not fabricated
    ("August 2021", "M2D", "08/2021"), # month-precision
    ("2019", "M2D", "2019"),           # year-only: no month to fall back on
])
def test_format_date_for_section_outputs(date_str, code, expected):
    assert format_date_for_section(date_str, code) == expected
