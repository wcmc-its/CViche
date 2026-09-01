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
    _dates_overlap_or_match,
    _parse_date_components,
    extract_sort_date,
    format_date_for_section,
)
import unified_pipeline.stage6.formatting.dates as _formatting_dates  # noqa: E402
import unified_pipeline.stage6.parsing.dates as _parsing_dates  # noqa: E402
import unified_pipeline.stage6.sorting.chronological as _sorting_chronological  # noqa: E402
from unified_pipeline.stage6.parsing.dates import CURRENT_DATE_VALUES  # noqa: E402


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
    # F1 (mm/dd/yyyy, Licensure) — #575: a stated day is preserved, but a
    # month-only date must degrade to mm/yyyy, never fabricate the 1st.
    ("2019-03-14", "F1", "03/14/2019"),
    ("2019-03", "F1", "03/2019"),
    ("March 2019", "F1", "03/2019"),
    ("03/2019", "F1", "03/2019"),
    ("2019", "F1", "2019"),
])
def test_format_date_for_section_outputs(date_str, code, expected):
    assert format_date_for_section(date_str, code) == expected


# --- _dates_overlap_or_match: parsed tuples, not string slices (#553) -------

def _entry(start, end):
    return {"extracted_fields": {"start_date": start, "end_date": end}}


class TestDatesOverlapOrMatch:
    def test_unpadded_months_that_overlap(self):
        # A=2021-2..2021-11, B=2021-10..2021-12: they overlap in Oct-Nov, so
        # this must be True. The old `[:7]` string slice returned False:
        # "2021-2-01"[:7] is "2021-2-", which string-compares LESS than
        # "2021-12" because the 5th character '2' < '1' — an artifact of the
        # unpadded month, not the true year/month relationship.
        a = _entry("2021-2-01", "2021-11-01")
        b = _entry("2021-10-01", "2021-12-01")
        assert _dates_overlap_or_match(a, b) is True

    def test_unpadded_months_zero_padded_control_agrees(self):
        # Same pair as above, zero-padded — the control that isolates the
        # padding as the sole cause of the old defect. Padded already worked;
        # this pins that the fix doesn't regress the padded case.
        a = _entry("2021-02-01", "2021-11-01")
        b = _entry("2021-10-01", "2021-12-01")
        assert _dates_overlap_or_match(a, b) is True

    def test_unpadded_disjoint_months_not_reported_as_overlapping(self):
        # A=2021-1..2021-2, B=2021-10..2021-11: genuinely disjoint. The old
        # code read this as an overlap (True) — the harmful direction, since
        # it feeds a dedup decision that can drop a real, distinct entry.
        a = _entry("2021-1-01", "2021-2-01")
        b = _entry("2021-10-01", "2021-12-01")
        assert _dates_overlap_or_match(a, b) is False

    @pytest.mark.parametrize("keyword", sorted(CURRENT_DATE_VALUES))
    def test_current_keyword_end_date_is_open_ended(self, keyword):
        # Every keyword in the shared vocabulary — not just 'present' — must
        # make the range open-ended. Before the fix, 'ongoing'/'current'/'now'
        # reached the string comparison as literals and only came out right
        # because every letter outranks every digit in ASCII.
        current = _entry("2020-01-01", keyword)
        later = _entry("2021-01-01", "2021-06-01")
        assert _dates_overlap_or_match(current, later) is True

    def test_current_keyword_is_case_insensitive(self):
        assert _dates_overlap_or_match(
            _entry("2020-01-01", "Present"),
            _entry("2021-01-01", "2021-06-01"),
        ) is True

    def test_exact_match_still_true(self):
        # Regression guard: the exact-match short circuit above the tuple
        # comparison is untouched by this fix.
        a = _entry("2019-06-01", "2020-01-01")
        b = _entry("2019-06-01", "2020-01-01")
        assert _dates_overlap_or_match(a, b) is True

    def test_missing_start_date_conservatively_true(self):
        # Regression guard: an entry with no start date at all still can't be
        # proven distinct, so this stays True ahead of any tuple parsing.
        assert _dates_overlap_or_match(_entry("", ""), _entry("2020-01-01", "present")) is True

    def test_unparseable_end_date_falls_back_open_ended(self):
        # An end_date that isn't a recognized keyword and doesn't parse (e.g.
        # a stray label) can't be proven to close the range, so it is treated
        # as open-ended rather than silently sorting as the smallest tuple.
        assert _dates_overlap_or_match(
            _entry("2020-01-01", "TBD"),
            _entry("2021-01-01", "2021-06-01"),
        ) is True

    def test_year_only_date_defaults_to_january_on_both_ends(self):
        # comparison_key = (year, month or 1) -- the form #553's own tracked
        # review comment (PR #514 discussion_r3721948927) asked for -- applies
        # the same January default whether the year-only value is a start or
        # an end. "2020"..."2020" and "2021-01"..."2021-01" stay disjoint...
        assert _dates_overlap_or_match(_entry("2020", "2020"), _entry("2021-01-01", "2021-01-01")) is False
        # ...and a year-only "2020" end no longer reaches into December: a
        # range that only touches December 2020 is not read as overlapping
        # it. (An earlier version of this fix defaulted a year-only end to
        # December instead, so this pair read True -- the harmful direction,
        # since it fed a real dedup decision.)
        assert _dates_overlap_or_match(_entry("2020", "2020"), _entry("2020-12-01", "2021-01-01")) is False

    def test_year_only_end_vs_later_dated_start_not_reported_as_overlap(self):
        # Regression pin for the live corpus case this round's fix corrects:
        # an "Assistant Professor" role with a year-only end ("2014") and a
        # "Tenured Associate Professor" role starting a month into the same
        # year ("2014-03") are a career progression, not a duplicate. With
        # the year-only end defaulting to December this read as an overlap
        # (2014-03 <= 2014-12); with the (year, month or 1) fix it does not.
        earlier = _entry("2005-05-01", "2014")
        later = _entry("2014-03-01", "2018-01-31")
        assert _dates_overlap_or_match(earlier, later) is False


# --- shared CURRENT_DATE_VALUES constant, one vocabulary across 3 modules ---

# The three modules that must agree on the open-ended vocabulary.
_CURRENT_KEYWORD_MODULES = (_formatting_dates, _sorting_chronological, _parsing_dates)

# The subset whose call site produces an observable that DISTINGUISHES a
# recognized keyword from an unrecognized string, and what it produces.
# parsing/dates.py is deliberately absent: _month_tuple_for_overlap answers
# (9999, 12) for an end and None for a start on BOTH branches -- a recognized
# keyword and an unparseable string are indistinguishable there by design (its
# fallback is "can't prove they differ"), so a sentinel probe against it would
# pass whether or not the sentinel were in the vocabulary. Its half of the
# sharing is pinned by the identity assert below only; see the PR description.
_CURRENT_KEYWORD_OBSERVABLES = (
    (_formatting_dates, lambda kw: format_date_for_section(kw, "M2A"), "Present"),
    (_sorting_chronological,
     lambda kw: extract_sort_date({"extracted_fields": {"end_date": kw}}), (9999, 12, 31)),
)


def test_current_date_values_shared_across_date_modules():
    # #553's second defect: formatting/dates.py and sorting/chronological.py
    # respelled ('present', 'current', 'ongoing', 'now') as their own local
    # tuples, and parsing/dates.py recognized only 'present' -- so an
    # end_date of "ongoing" reached _dates_overlap_or_match as a literal
    # string while the other two modules already treated it as open-ended.
    assert CURRENT_DATE_VALUES == frozenset({"present", "current", "ongoing", "now"})
    for module in _CURRENT_KEYWORD_MODULES:
        assert module.CURRENT_DATE_VALUES is CURRENT_DATE_VALUES, (
            f"{module.__name__} does not read parsing/dates.py's frozenset"
        )


@pytest.mark.parametrize("module,observe,expected", _CURRENT_KEYWORD_OBSERVABLES,
                         ids=[m.__name__ for m, _o, _e in _CURRENT_KEYWORD_OBSERVABLES])
def test_current_keyword_call_sites_read_the_shared_constant(monkeypatch, module, observe, expected):
    # The membership assert above is not by itself a revert detector: restoring
    # a module's own local ('present', 'current', 'ongoing', 'now') tuple at its
    # call site leaves every value -- and therefore every observable -- exactly
    # as it is today, so a test that only checks values still passes. This one
    # extends the shared frozenset with a keyword no local copy could contain
    # and asserts the call site honours it, which is false the moment that call
    # site stops reading the module-level name.
    sentinel = "definitely-not-a-real-date-keyword"
    monkeypatch.setattr(module, "CURRENT_DATE_VALUES",
                        CURRENT_DATE_VALUES | {sentinel}, raising=True)
    assert observe(sentinel) == expected
    # ...and the real vocabulary still works through the same call site.
    assert observe("ongoing") == expected
