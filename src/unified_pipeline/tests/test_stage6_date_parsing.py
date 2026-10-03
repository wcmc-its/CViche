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

from unified_pipeline.stage6.parsing import _get_entry_date_range  # noqa: E402
from unified_pipeline.stage_6_word_template import (  # noqa: E402
    _dates_overlap_or_match,
    _parse_date_components,
    extract_sort_date,
    format_date_for_section,
)
import unified_pipeline.stage6.parsing.dates as _parsing_dates  # noqa: E402
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
    ("Foobar 2021", (None, None, None)),   # unknown month name -> unreadable (#716)
    ("", (None, None, None)),
    ("garbage", (None, None, None)),
])
def test_parse_date_components(date_str, expected):
    assert _parse_date_components(date_str) == expected


# --- #716 review round 1: unknown month token vs. season token --------------
#
# T1.1/T2.5: an unknown alphabetic token used to fall back to the same
# (year, None, None) a season token gets, making "Foo 2021" indistinguishable
# from a legitimate year-only "2021". A season names a real year with an
# unstated month; an unknown token names neither and is dropped entirely.

@pytest.mark.parametrize("date_str", ["Foo 2021", "Blah 2021", "Unknown 2024"])
def test_unknown_month_token_is_unreadable(date_str):
    assert _parse_date_components(date_str) == (None, None, None)


@pytest.mark.parametrize("date_str, expected", [
    ("Fall 2016", (2016, None, None)),
    ("Spring 2020", (2020, None, None)),
    ("autumn 2019", (2019, None, None)),
])
def test_season_token_keeps_the_year(date_str, expected):
    assert _parse_date_components(date_str) == expected


@pytest.mark.parametrize("date_str, expected", [
    ("March 2020", (2020, 3, None)),
    ("Sept. 2019", (2019, 9, None)),
    ("Jun 2024", (2024, 6, None)),
])
def test_known_month_token_still_parses(date_str, expected):
    assert _parse_date_components(date_str) == expected


# --- #716 review round 1: anchoring (T1.2/T2.1) ------------------------------
#
# Every complete-date pattern is re.fullmatch now: a string that carries a
# valid date plus trailing text -- extra digits, garbage, or (the corpus
# shapes below) a list or a range packed into one field -- is not a single
# date, and parses to nothing rather than being read as its first token.

@pytest.mark.parametrize("date_str", [
    "2021-06-30foo",
    "2021-05-15 garbage",
    "2021-13",
    "02/2021x",
])
def test_anchoring_rejects_valid_prefix_plus_trailing_text(date_str):
    assert _parse_date_components(date_str) == (None, None, None)


@pytest.mark.parametrize("date_str", [
    # Corpus shapes (scout716) that previously parsed as their first date
    # under `re.match` -- a list or a range in one field, not a single date.
    "february 2022, july 2022 and july 2023",
    "march 2021, march 2022, march 2023, and march 2024",
    "2023-06-29-30",
    "2006-07-14 to 2006-07-16",
    "2006-05-18 to 2006-05-20; 2006-02-09",
])
def test_anchoring_rejects_corpus_list_and_range_shapes(date_str):
    assert _parse_date_components(date_str) == (None, None, None)


# --- #716 review round 1: calendar validity (T1.3/T2.2) ---------------------

@pytest.mark.parametrize("date_str", [
    "2021-13-40",   # month out of range
    "99/2021",      # MM/YYYY with an impossible month
    "2021-00-10",   # month 0
])
def test_calendar_invalid_month_is_unreadable(date_str):
    assert _parse_date_components(date_str) == (None, None, None)


@pytest.mark.parametrize("date_str, expected", [
    ("02/31/2021", (2021, 2, None)),   # valid month, impossible day
    ("2024-04-31", (2024, 4, None)),   # corpus case (scout716): April has 30 days
])
def test_calendar_invalid_day_degrades_to_month_precision(date_str, expected):
    assert _parse_date_components(date_str) == expected


def test_calendar_leap_year_day_is_kept():
    assert _parse_date_components("2024-02-29") == (2024, 2, 29)


def test_calendar_non_leap_year_day_degrades_to_month():
    assert _parse_date_components("2023-02-29") == (2023, 2, None)


# --- #867: mm/dd/yy, a two-digit year, with a century pivot at 30 ----------

@pytest.mark.parametrize("date_str, expected", [
    ("10/30/17", (2017, 10, 30)),
    ("01/01/00", (2000, 1, 1)),
    ("01/01/30", (2030, 1, 1)),     # the pivot itself -> 20xx
    ("01/01/31", (1931, 1, 1)),     # just above -> 19xx
    ("10/30/99", (1999, 10, 30)),
    ("10-30-17", (2017, 10, 30)),
])
def test_two_digit_year_reads_through_the_pivot(date_str, expected):
    assert _parse_date_components(date_str) == expected


def test_two_digit_year_reaches_format_date_for_section_as_a_year():
    # The actual #867 symptom: the honors taxonomy code renders 'yyyy' only.
    assert format_date_for_section("10/30/17", "H") == "2017"
    assert format_date_for_section("10/30/99", "H") == "1999"


def test_four_digit_year_mm_dd_is_unaffected_by_the_two_digit_pattern():
    # The pre-existing MM/DD/YYYY pattern must still win for a 4-digit year.
    assert _parse_date_components("10/30/2017") == (2017, 10, 30)


@pytest.mark.parametrize("date_str", [
    "07/09-08/10",     # a range, not a single mm/dd/yy date
    "10/17",            # bare mm/yy: no day, too ambiguous to guess at
    "10/30/170",        # a 3-digit trailing group is not a 2-digit year
])
def test_two_digit_year_pattern_does_not_swallow_other_shapes(date_str):
    assert _parse_date_components(date_str) == (None, None, None)


def test_two_digit_year_with_an_impossible_month_is_unreadable():
    # Read as mm/dd/yy only, like the four-digit mm/dd/yyyy shape: no guess
    # at dd/mm when the first number can't be a month.
    assert _parse_date_components("13/05/17") == (None, None, None)


def test_both_functions_use_the_shared_parser():
    # format_date_for_section (rendering), extract_sort_date (sorting), and
    # _parse_date_components itself must all agree on what a string yields --
    # that is the whole point of sharing the parser (#716 T2.4: this test's
    # name already claimed to check this and did not -- it only compared
    # _parse_date_components against extract_sort_date, so a duplicate parser
    # inside format_date_for_section's own path could have drifted silently).
    for date_str in ("Aug. 2021", "March 2020", "05/2019", "2019-05-15"):
        year, month, _ = _parse_date_components(date_str)
        sort_year, sort_month, _ = _sort(date_str)
        assert sort_year == year
        assert sort_month == (month if month is not None else 1)
        # format_date_for_section (yyyy-format code) surfaces the same parsed
        # year the other two agreed on.
        formatted = format_date_for_section(date_str, "H")
        assert formatted == str(year)

    # An unparsed string (#716 D2/D1) is returned as written by the formatter,
    # exactly as it sorts last rather than acquiring a fabricated date.
    unparsed = "february 2022, july 2022 and july 2023"
    assert _parse_date_components(unparsed) == (None, None, None)
    assert format_date_for_section(unparsed, "H") == unparsed
    assert _sort(unparsed) == (0, 0, 0)


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


# --- calendar-invalid month renders the year alone (#543) -------------------
#
# Owner decision 2026-09-01: never fabricate a date component and never render
# the invalid fragment verbatim. Year-only form chosen (no placeholder).

@pytest.mark.parametrize("date_str, code, expected", [
    ("13/2024", "B1", "2024"),          # the issue's case
    ("13/2024", "C", "2024"),           # mm/yy code: year alone, not "13/24"
    ("99/2024", "M2A", "2024"),
    ("2024-13-01", "B1", "2024"),
    ("2024-99-99", "F1", "2024"),
    ("00/2024", "B1", "2024"),          # month 00
    ("2024-00-00", "B1", "2024"),
    ("2024-00-15", "M2A", "2024"),
    ("13/05/2020", "F1", "2020"),       # invalid month in a m/d/yyyy shape
    # Unchanged: valid dates, calendar-invalid day (month kept), non-dates.
    # A two-part YYYY-NN is ambiguous with an academic-year range: left as
    # written, never collapsed to its first year.
    ("2014-15", "H", "2014-15"),
    ("2019/20", "B1", "2019/20"),
    ("2024-13", "H", "2024-13"),
    ("12/2024", "B1", "12/2024"),
    ("2024-02-31", "B1", "02/2024"),
    ("TBD", "B1", "TBD"),
    ("n/a", "C", "n/a"),
    ("2024-invalid", "B1", "2024-invalid"),
    ("13/2024 to 2025", "B1", "13/2024 to 2025"),  # a range in one field is not a date
    ("13/05/17", "C", "13/05/17"),      # two-digit year: no trustworthy year
    # A hyphenated NN-YYYY is a two-digit-start year range, not month 98:
    # left as written, never cut to its end year.
    ("98-2002", "B1", "98-2002"),
    ("85-1990", "H", "85-1990"),
    ("15-2016", "C", "15-2016"),
    ("00-2005", "F1", "00-2005"),
])
def test_format_date_for_section_invalid_month_renders_year_only(date_str, code, expected):
    assert format_date_for_section(date_str, code) == expected


def test_format_date_range_invalid_month_endpoint_renders_year_only():
    assert format_date_range("08/2020", "13/2024", "B1") == "08/2020-2024"


def test_format_date_range_keeps_a_two_digit_start_year_range():
    assert format_date_range("97-2001", "Present", "B1") == "97-2001-Present"


# --- _dates_overlap_or_match: granularity-honest comparison (#553) ---------
#
# The rule under test: parse each boundary to (year, month-or-None) and never
# substitute a month nobody stated. Two ranges are disjoint only when the
# stated data proves it -- years differ, or same year with BOTH months stated.
# Anything else (unreadable boundary, same year with a month missing) is
# "can't prove they differ" -> True, matching the function's own documented
# convention for entries that lack dates.

def _entry(start, end):
    return {"extracted_fields": {"start_date": start, "end_date": end}}


class TestDatesOverlapOrMatch:
    # -- disjointness the data PROVES: different years -----------------------

    def test_different_years_are_provably_disjoint(self):
        earlier = _entry("2010-01-01", "2011-06-01")
        later = _entry("2015-01-01", "2016-06-01")
        assert _dates_overlap_or_match(earlier, later) is False

    def test_different_years_disjoint_in_the_other_argument_order(self):
        # The proof must not depend on which entry is passed first: the
        # function tests both directions (A ends before B, B ends before A).
        earlier = _entry("2010-01-01", "2011-06-01")
        later = _entry("2015-01-01", "2016-06-01")
        assert _dates_overlap_or_match(later, earlier) is False

    def test_different_years_that_actually_overlap_stay_true(self):
        assert _dates_overlap_or_match(
            _entry("2010-01-01", "2016-01-01"),
            _entry("2015-01-01", "2020-01-01"),
        ) is True

    # -- disjointness the data PROVES: same year, both months stated ---------

    def test_same_year_both_months_stated_is_decidable(self):
        a = _entry("2021-01-01", "2021-02-01")
        b = _entry("2021-10-01", "2021-12-01")
        assert _dates_overlap_or_match(a, b) is False

    def test_same_year_both_months_stated_overlapping_is_true(self):
        a = _entry("2021-02-01", "2021-11-01")
        b = _entry("2021-10-01", "2021-12-01")
        assert _dates_overlap_or_match(a, b) is True

    # -- no proof available -> conservative True -----------------------------

    def test_same_year_missing_month_on_one_side_cannot_be_proven_disjoint(self):
        # The live corpus shape (Opresko): an "Assistant Professor" role ending
        # a year-only "2014" against a "Tenured Associate Professor" role
        # starting "2014-03". Nothing in the CV says which month 2014 ended, so
        # nothing proves the two are disjoint -- True, and the content gates
        # behind this call (_drop_is_safe) decide whether anything is dropped.
        # An implementation that imputes a month (December OR January) answers
        # False here off data the CV never contained.
        earlier = _entry("2005-05-01", "2014")
        later = _entry("2014-03-01", "2018-01-31")
        assert _dates_overlap_or_match(earlier, later) is True

    def test_same_year_missing_month_on_the_other_side_too(self):
        assert _dates_overlap_or_match(
            _entry("2014-03-01", "2018-01-31"),
            _entry("2005-05-01", "2014"),
        ) is True

    def test_year_only_versus_year_only_in_the_same_year_is_true(self):
        # Neither boundary carries a month, so the same year is the whole of
        # what is known: unprovable either way.
        assert _dates_overlap_or_match(_entry("2020", "2020"),
                                       _entry("2020", "2021")) is True

    def test_year_only_versus_year_only_in_different_years_is_still_decidable(self):
        # Losing the imputed month must not lose the year-level proof.
        assert _dates_overlap_or_match(_entry("2018", "2019"),
                                       _entry("2021", "2022")) is False

    def test_missing_start_date_conservatively_true(self):
        # Regression guard: an entry with no start date at all still can't be
        # proven distinct, so this stays True ahead of any boundary parsing.
        assert _dates_overlap_or_match(_entry("", ""),
                                       _entry("2020-01-01", "present")) is True

    def test_unparseable_end_is_unknown_not_open_ended(self):
        # A non-blank end that doesn't parse and isn't a current-date keyword
        # is UNKNOWN, and is treated exactly like a missing one: conservative
        # True. Treating it as open-ended instead would answer False here (B
        # provably ends before A starts) -- an undisclosed behaviour change,
        # and a guess about data the CV does not contain.
        assert _dates_overlap_or_match(
            _entry("2020-01-01", "TBD"),
            _entry("2018-01-01", "2019-01-01"),
        ) is True

    def test_unparseable_start_is_unknown_too(self):
        assert _dates_overlap_or_match(
            _entry("see below", "2019-01-01"),
            _entry("2021-01-01", "2022-01-01"),
        ) is True

    # -- open-ended ends -----------------------------------------------------

    @pytest.mark.parametrize("keyword", sorted(CURRENT_DATE_VALUES))
    def test_current_keyword_end_date_is_open_ended(self, keyword):
        # Every keyword in the shared vocabulary -- not just 'present' -- must
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

    def test_open_ended_end_does_not_reach_backwards(self):
        # 'present' opens the range forwards only: an entry that provably
        # closed before this one started is still disjoint.
        assert _dates_overlap_or_match(
            _entry("2020-01-01", "present"),
            _entry("2017-01-01", "2018-01-01"),
        ) is False

    # -- fast path -----------------------------------------------------------

    def test_exact_match_still_true(self):
        # Regression guard: the exact-string short circuit above the boundary
        # comparison is untouched by this fix.
        a = _entry("2019-06-01", "2020-01-01")
        b = _entry("2019-06-01", "2020-01-01")
        assert _dates_overlap_or_match(a, b) is True

    def test_exact_match_wins_even_when_the_boundaries_are_unparseable(self):
        a = _entry("whenever", "whenever")
        b = _entry("whenever", "whenever")
        assert _dates_overlap_or_match(a, b) is True

    # -- #553's stated trigger: the unpadded-month shape ---------------------
    #
    # Census over the 66-CV corpus' stage-4 artifacts found ZERO values in the
    # YYYY-M / YYYY-M-D / M/YYYY shapes (2052 padded, 2882 year-only, 1147
    # month-name/keyword, 27 other), so these two cases are not reproducible
    # from corpus data. They are pinned anyway because the issue names them and
    # because parsing (rather than slicing) the string is what makes them work.

    def test_unpadded_disjoint_months_are_provably_disjoint(self):
        # A=2021-1..2021-2, B=2021-10..2021-12: genuinely disjoint, both
        # months stated. The old `[:7]` string slice read this as an overlap
        # (True) -- "2021-1-01"[:7] is "2021-1-", which string-compares LESS
        # than "2021-12" because '1' < '2' at the 6th character.
        a = _entry("2021-1-01", "2021-2-01")
        b = _entry("2021-10-01", "2021-12-01")
        assert _dates_overlap_or_match(a, b) is False

    def test_unpadded_overlapping_months_are_true(self):
        a = _entry("2021-2-01", "2021-11-01")
        b = _entry("2021-10-01", "2021-12-01")
        assert _dates_overlap_or_match(a, b) is True

    def test_unpadded_zero_padded_control_agrees(self):
        # The control that isolates padding as the sole variable: the padded
        # spelling of the pair above must give the same answers.
        assert _dates_overlap_or_match(
            _entry("2021-01-01", "2021-02-01"),
            _entry("2021-10-01", "2021-12-01"),
        ) is False
        assert _dates_overlap_or_match(
            _entry("2021-02-01", "2021-11-01"),
            _entry("2021-10-01", "2021-12-01"),
        ) is True

    # -- #716 review round 1: inverted own-range (T2.3) ----------------------
    #
    # A malformed extraction where an entry's own end is stated before its own
    # start proves nothing about either entry -- conservative True, same as an
    # unreadable boundary, rather than letting the cross-check "prove" a
    # disjointness the CV never actually stated.

    def test_inverted_own_range_is_conservatively_true(self):
        # Before #716's D4, the cross-check alone (ignoring that A's own range
        # is inverted) already answered False here: B's end (2016) is before
        # A's start (2020), regardless of A's own malformed range. D4 catches
        # A's inversion first and returns True instead.
        a = _entry("2020-06", "2019-01")   # inverted: end before start
        b = _entry("2015", "2016")
        assert _dates_overlap_or_match(a, b) is True
        assert _dates_overlap_or_match(b, a) is True

    def test_non_inverted_control_is_unaffected(self):
        # Same shape, A's own range corrected (start before end): D4 must not
        # fire, and the two remain provably disjoint.
        a = _entry("2019-01", "2020-06")
        b = _entry("2015", "2016")
        assert _dates_overlap_or_match(a, b) is False


# --- #666 (EBYSBC): a record dated by one `date` field spans that date --------

def _dated(date):
    return {"extracted_fields": {"date": date}}


@pytest.mark.parametrize("fields, expected", [
    ({"date": "2031-04-08"}, ("2031-04-08", "2031-04-08")),
    ({"date": {"start_date": "2028", "end_date": "2030"}}, ("2028", "2030")),  # #1233 dict
    ({"date": {"start_date": "2028"}}, ("2028", "")),
    ({"start_date": "2028", "date": "2031"}, ("2028", "")),   # a stated range wins
    ({"end_date": "2030", "date": "2031"}, ("", "2030")),
    ({"date": ""}, ("", "")),
    ({}, ("", "")),
])
def test_entry_date_range_reads_a_single_date(fields, expected):
    assert _get_entry_date_range({"extracted_fields": fields}) == expected


def test_two_sessions_dated_in_different_months_are_provably_disjoint():
    # KDAZOM-03 shape: one teaching session, two dates in one year.
    assert _dates_overlap_or_match(_dated("2031-04-08"), _dated("2031-02-11")) is False
    assert _dates_overlap_or_match(_dated("2031-04-08"), _dated("2031-04")) is True
    assert _dates_overlap_or_match(_dated("2031-04-08"), _dated("2031")) is True
    assert _dates_overlap_or_match(_dated("Monthly"), _dated("2031-02-11")) is True


# --- #716 review round 1: _overlap_boundary coerces non-string input --------


def test_overlap_boundary_non_string_start_does_not_raise():
    # 12345 stringifies to "12345", a 5-digit string that matches no date
    # shape: unreadable, same as any other unparseable boundary -- the
    # assertion here is that coercing an int does not raise, not that it
    # produces a date.
    assert _parsing_dates._overlap_boundary(12345, is_end=False) is None


def test_overlap_boundary_none_end_does_not_raise():
    assert _parsing_dates._overlap_boundary(None, is_end=True) is _parsing_dates._OPEN_ENDED


# --- #716 review round 1: sorting/rendering consumers see the new behaviour -


def test_extract_sort_date_sees_season_at_year_level():
    # "Fall 2016" now parses to (2016, None, None): the sort key falls back
    # to month=1 the same way any other year-only date does.
    assert _sort("Fall 2016") == (2016, 1, 1)


def test_extract_sort_date_sees_unparsed_list_shape_sort_last():
    # A corpus list-in-one-field string no longer acquires a false date from
    # its first token; it is unreadable and sorts last, like any other
    # unparseable value.
    assert _sort("february 2022, july 2022 and july 2023") == (0, 0, 0)


def test_format_date_for_section_sees_season_at_year_level():
    # DATE_FORMATS['H'] is 'yyyy': a season keeps its year and renders it,
    # same as any other year-only date.
    assert format_date_for_section("Fall 2016", "H") == "2016"


def test_format_date_for_section_sees_unparsed_list_shape_as_written():
    # An unparsed string is returned exactly as written, never truncated to
    # its first token.
    literal = "february 2022, july 2022 and july 2023"
    assert format_date_for_section(literal, "H") == literal


# --- the 'present'/'current' vocabulary, pinned where it lives ---


def test_current_date_vocabulary_is_pinned():
    """The end-of-range keywords _parse_date_components treats as open-ended.

    Pinned so a vocabulary edit is a visible, deliberate act. The formatting
    and sorting modules keep their own literals on dev; consolidating them
    was deliberately kept out of #553's PR (cross-file constant conversion).
    """
    assert CURRENT_DATE_VALUES == frozenset({"present", "current", "ongoing", "now"})


# --- #946: a start with no end on a point-in-time code is that one year ------

from unified_pipeline.stage6.formatting.dates import (  # noqa: E402
    POINT_IN_TIME_CODES,
    format_date_range,
    normalize_iso_dates_in_text,
)


def test_point_in_time_codes_are_pinned():
    """The decision on #946 names awards, talks, CME lectures and P/Q one-off
    rows, and #1220 added B2; each member's reason is at the constant. Pinned
    so adding or dropping a code is a visible, deliberate act."""
    assert POINT_IN_TIME_CODES == frozenset({"H", "R", "K4", "P", "Q2", "Q3", "B2"})


@pytest.mark.parametrize("code", sorted(POINT_IN_TIME_CODES))
def test_point_in_time_start_only_renders_the_bare_year(code):
    assert format_date_range("2021", "", code) == "2021"


@pytest.mark.parametrize("code", sorted(POINT_IN_TIME_CODES))
def test_point_in_time_keeps_present_when_the_end_says_so(code):
    assert format_date_range("2021", "present", code) == "2021-Present"
    assert format_date_range("2021", "current", code) == "2021-Present"


@pytest.mark.parametrize("code", ["D1", "D2", "D3", "O", "I", "Q1", "Q4B", "Q4C", "Q4D", "M2A"])
def test_other_codes_still_read_a_start_only_record_as_ongoing(code):
    assert format_date_range("2021", "", code) == "2021-Present"


@pytest.mark.parametrize("source_text", [
    "2021-\tMember, Quality Committee",          # open dash in a date column
    "Quality Committee (2021-",                  # open parenthetical
    "2021- Member, Quality Committee",           # dash touching the year
    "11/2021- Member, Quality Committee",        # month/year form
    "2021 -\tMember, Quality Committee",         # spaced dash, nothing after it
    "2021 –\nMember, Quality Committee",         # spaced en dash at line end
    "2021– Member, Quality Committee",           # en dash touching the year
    "2021— Member, Quality Committee",           # em dash touching the year
    "Quality Committee (2021 -)",                # spaced dash closed by a paren
    "2021 -  \tMember, Quality Committee",       # spaces between dash and tab
    "Member, Quality Committee, 2021-present",   # stated in words
    "2021 –\tMember, Quality Committee",         # spaced en dash, then a tab
    # The entry opens with the year and a spaced dash: the CV's date column
    # saying "since 2021", whether or not the reader kept the tab after the
    # dash (class E9, EBYSBC autopsy).
    "2021 – Member, Quality Committee",
    "  2021 - Member, Quality Committee",
    # A month (and a day) written after the year with dots.
    "2021.07 - Member, Quality Committee",
    "2021.07.15 - Member, Quality Committee",
    "2021.07- Member, Quality Committee",
    "2021.07 -\tMember, Quality Committee",
    "Member, Quality Committee, 2021.07 - present",
])
def test_point_in_time_keeps_present_when_the_source_leaves_the_year_open(source_text):
    assert format_date_range("2021", "", "P", source_text) == "2021-Present"


@pytest.mark.parametrize("source_text", [
    "2021\tMember, Quality Committee",
    "Quality Committee: 2021 – Member",          # a column separator, not a range
    "Quality Committee\n2021 – Member",          # only the entry's first line opens it
    "2021 – 2022 Member, Quality Committee",     # an opening closed range
    "2021.07 - 2022.06 Member, Quality Committee",
    "2021-2022 Member, Quality Committee",       # a closed range
    "2021-22 Member, Quality Committee",
    "2019- Member, Quality Committee",           # a different year left open
    "12021- Quality Committee",                  # not the year, part of a number
])
def test_point_in_time_bare_year_when_the_source_does_not_leave_it_open(source_text):
    assert format_date_range("2021", "", "P", source_text) == "2021"


def test_point_in_time_month_start_reads_the_year_off_the_start():
    assert format_date_range("2019-11", "", "P", "11/2019- Clinical Committee") == "2019-Present"
    assert format_date_range("2019-11", "", "P", "11/2019 Clinical Committee") == "2019"


def test_point_in_time_unreadable_start_renders_as_written():
    assert format_date_range("Fall", "", "P", "Fall- Committee") == "Fall"


def test_an_unreadable_year_is_never_searched_for():
    from unified_pipeline.stage6.formatting.dates import _source_leaves_year_open
    assert _source_leaves_year_open("None- Committee", None) is False


# --- class 13 (2026-10-02): every code reads the source text it is given ----

_NON_POINT_CODES = ["C", "D1", "I", "K3", "M2B", "O", "Q1", "Q4B", "Q4D"]


@pytest.mark.parametrize("code", _NON_POINT_CODES)
def test_any_code_given_its_source_renders_a_lone_start_as_that_year(code):
    """A one-year chair, a guest-edited issue or a 1989 postdoc read
    "<year>-Present" because only `POINT_IN_TIME_CODES` read the source."""
    assert format_date_range("2011", "", code, "2011 Chair, Fictional Working Group") == "2011"


@pytest.mark.parametrize("code", _NON_POINT_CODES)
def test_any_code_keeps_present_when_its_source_leaves_the_year_open(code):
    assert format_date_range("2011", "", code, "2011- Chair, Fictional Working Group") \
        == "2011-Present"


@pytest.mark.parametrize("source_text", [
    "2011 - present Chair, Fictional Working Group",
    "Chair, Fictional Working Group, 2011 to present",
    "2011 \u2013 Current\tChair, Fictional Working Group",
    "Chair, Fictional Working Group (2011 - NOW)",
])
@pytest.mark.parametrize("code", ["P", "Q1"])
def test_a_source_that_ends_the_year_in_present_current_or_now_keeps_present(source_text, code):
    """A spaced dash with text after it is a column separator, except when
    that text is a word that leaves the range open."""
    assert format_date_range("2011", "", code, source_text) == "2011-Present"


@pytest.mark.parametrize("source_text", [
    "2011 President, Fictional Society",
    "Fictional Society, 2011 - Presentation Committee",
    "Fictional Society, 2011 - Nowell Lecture Committee",
    "2011 Chair, Fictional Working Group; now Professor at Fictional University",
    "Reviewer, Current Fictional Opinion, 2011",
    "2009 - present Member; 2011 Chair, Fictional Working Group",
])
def test_present_current_or_now_that_does_not_end_this_year_does_not_count(source_text):
    """Not the whole word, not after this record's year, or about something
    else ("now Professor at ...", a journal named "Current ...")."""
    assert format_date_range("2011", "", "Q1", source_text) == "2011"


@pytest.mark.parametrize("code", _NON_POINT_CODES)
def test_a_caller_that_passes_no_source_still_gets_present(code):
    """The rule reaches only the callers that pass the entry's text; the rest
    (positions, mentoring, clinical practice, education) are unchanged."""
    assert format_date_range("2011", "", code) == "2011-Present"


# --- class E9 (EBYSBC autopsy): a dotted month after an open year ------------

@pytest.mark.parametrize("code", ["Q4C", "Q3", "O"])
def test_an_entry_opening_with_a_dotted_month_and_a_dash_keeps_present(code):
    """An editorial board written "YYYY.MM - <journal>" is an open range;
    #1330's rule read it as a lone year because the dash did not follow the
    year directly."""
    source_text = "2014.01 - Fictional Journal of Testing, Editorial Board"
    assert format_date_range("2014-01", "", code, source_text) == "2014-Present"


def test_an_entry_opening_with_a_single_digit_dotted_month_keeps_present():
    """Some CVs write the month without a leading zero: "YYYY.M - <text>"."""
    source_text = "2021.7 - Member, Fictional Society of Testing"
    assert format_date_range("2021-07", "", "Q3", source_text) == "2021-Present"


def test_a_dotted_month_closed_range_stays_the_start_year():
    source_text = "2014.01 - 2016.12 Fictional Journal of Testing, Editorial Board"
    assert format_date_range("2014-01", "", "Q4C", source_text) == "2014"


# --- class E21 (EBYSBC autopsy): a range whose two ends read the same --------

@pytest.mark.parametrize("text, expected", [
    # 5c's ISO day range in one month: the day is dropped, then the range
    # collapses instead of reading "March 2006 to March 2006".
    ("**2006-03-21 to 2006-03-23** - Fictional lecture",
     "**March 2006** - Fictional lecture"),
    ("2009-01-13 to 2009-01-15 Fictional course", "January 2009 Fictional course"),
    ("2009-01-13\u20132009-01-15 Fictional course", "January 2009 Fictional course"),
    # 5c's one-year course.
    ("**2011-2011** - Fictional course", "**2011** - Fictional course"),
    ("2011 - 2011 Fictional course", "2011 Fictional course"),
    ("2011 to 2011 Fictional course", "2011 Fictional course"),
])
def test_normalize_collapses_a_range_whose_ends_read_the_same(text, expected):
    assert normalize_iso_dates_in_text(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("2006-03-21 to 2006-04-02 Fictional course", "March 2006 to April 2006 Fictional course"),
    ("2011-2012 Fictional course", "2011-2012 Fictional course"),
    ("Grant R01-2011-2011 renewal", "Grant R01-2011-2011 renewal"),
    ("1990-1990s survey", "1990-1990s survey"),
    ("2011-20110 Fictional course", "2011-20110 Fictional course"),
    ("March 2006 to March 2007", "March 2006 to March 2007"),
])
def test_normalize_leaves_a_real_range_or_a_longer_token_alone(text, expected):
    assert normalize_iso_dates_in_text(text) == expected


# --- #1233: a {start_date, end_date} mapping is a range, never its repr -------

@pytest.mark.parametrize("code, value, expected", [
    ("F2", {"start_date": "2008", "end_date": "2018"}, "2008-2018"),
    ("B1", {"start_date": "2006-09", "end_date": "2010-06"}, "09/2006-06/2010"),
    # a one-occasion code collapses a same-year span to that year ...
    ("R", {"start_date": "2021-05-17", "end_date": "2021-05-19"}, "2021"),
    # ... and keeps both years when the span crosses a year boundary
    ("R", {"start_date": "2021-12-30", "end_date": "2022-01-02"}, "2021-2022"),
    ("R", {"start_date": "2021-05-17", "end_date": None}, "2021"),
    ("F2", {"start_date": "2008", "end_date": "present"}, "2008-Present"),
    ("F2", {"start_date": "", "end_date": "2018"}, "2018"),
    ("F2", {"start_date": "2008"}, "2008-Present"),
])
def test_a_start_end_mapping_formats_as_the_range(code, value, expected):
    assert format_date_for_section(value, code) == expected


@pytest.mark.parametrize("code", ["F2", "R", "K4", "P", "B1"])
def test_a_start_end_mapping_formats_like_the_same_two_dates_as_a_range(code):
    value = {"start_date": "2021-05-17", "end_date": "2022-03-02"}
    assert format_date_for_section(value, code) == format_date_range(
        value["start_date"], value["end_date"], code)


def test_a_start_end_mapping_never_renders_as_its_repr():
    out = format_date_for_section({"start_date": "2008", "end_date": "2018"}, "F2")
    assert "{" not in out and "start_date" not in out and "'" not in out


def test_a_mapping_holding_no_dates_renders_blank():
    assert format_date_for_section({}, "F2") == ""
    assert format_date_for_section({"start_date": "", "end_date": ""}, "F2") == ""


def test_a_mapping_without_the_range_keys_renders_blank_and_is_logged(caplog):
    with caplog.at_level("WARNING", logger="unified_pipeline.stage6.formatting.dates"):
        out = format_date_for_section({"year": "2008"}, "F2")
    assert out == ""
    assert "without start_date/end_date keys" in caplog.text
    assert "['year']" in caplog.text


def test_a_string_date_is_unchanged_by_the_mapping_branch():
    assert format_date_for_section("March 2008", "F2") == "2008"
    assert format_date_for_section("2008-2018", "F2") == "2008-2018"
    assert format_date_for_section("TBD", "F2") == "TBD"


@pytest.mark.parametrize("value, expected", [
    ({"start_date": "2021-05-17", "end_date": "2021-05-19"}, (2021, 5, 19)),
    ({"start_date": "2021-05-17", "end_date": None}, (2021, 5, 17)),
    ({"start_date": "2021-05-17", "end_date": "present"}, (9999, 12, 31)),
    ({"start_date": "2008", "end_date": "2018"}, (2018, 1, 1)),
    ({"start_date": "2008"}, (2008, 1, 1)),
    ({"start_date": {"start_date": "2008", "end_date": "2018"}}, (2018, 1, 1)),
    ({}, (0, 0, 0)),
    ({"year": "2008"}, (0, 0, 0)),
])
def test_a_start_end_mapping_sorts_by_its_end_else_its_start(value, expected):
    """#1233: `str()` of the mapping held no year, so the entry keyed (0, 0, 0)
    and sank to the bottom of its reverse-chronological table."""
    assert extract_sort_date({"extracted_fields": {"date": value}}) == expected


def test_a_range_valued_recertification_date_is_left_out_of_the_sort_key():
    """F2 falls through to `year_certified`, as it did while the mapping's repr
    held no year, rather than ranking a certification by its renewal window."""
    fields = {"year_certified": "2001",
              "recertification_date": {"start_date": "2008", "end_date": "2018"}}
    assert extract_sort_date({"extracted_fields": fields}) == (2001, 1, 1)


def test_a_range_recertification_date_keys_the_same_as_its_string_form():
    as_dict = {"year_certified": "2001",
               "recertification_date": {"start_date": "2008", "end_date": "2018"}}
    as_text = {"year_certified": "2001", "recertification_date": "2008-2018"}
    assert (extract_sort_date({"extracted_fields": as_dict})
            == extract_sort_date({"extracted_fields": as_text}))


def test_a_single_date_recertification_still_outranks_year_certified():
    fields = {"year_certified": "2001", "recertification_date": "2018"}
    assert extract_sort_date({"extracted_fields": fields}) == (2018, 1, 1)


def test_a_mapping_dated_entry_sorts_among_string_dated_entries():
    from unified_pipeline.stage6.sorting import sort_entries_reverse_chronological
    entries = [
        {"extracted_fields": {"label": "newer-string", "date": "2020-03-01"}},
        {"extracted_fields": {"label": "newest-mapping",
                              "date": {"start_date": "2021-05-17", "end_date": "2021-05-19"}}},
        {"extracted_fields": {"label": "oldest-string", "date": "2019"}},
    ]
    ordered = [e["extracted_fields"]["label"] for e in sort_entries_reverse_chronological(entries)]
    assert ordered == ["newest-mapping", "newer-string", "oldest-string"]


# --- EBYSBC E22 (#1245): a record's further spans in its date cell ----------

from unified_pipeline.stage6.formatting import (  # noqa: E402
    extra_date_spans,
    with_extra_date_spans,
)


def _spans(**fields):
    return {"start_date": "2019", "end_date": "2020", **fields}


def test_a_list_of_periods_adds_each_span():
    fields = _spans(additional_periods=[{"start_date": "2021-01", "end_date": "2021-12"},
                                        {"start_date": "2023", "end_date": "2025"}])
    assert extra_date_spans(fields, "O") == ["2021", "2023-2025"]


def test_a_start_end_pair_adds_one_span_and_reads_present():
    fields = _spans(additional_period_start="2022", additional_period_end="present")
    assert with_extra_date_spans("2019-2020", fields, "P") == "2019-2020, 2022-Present"


def test_a_string_of_dates_adds_each_date_alone_never_an_open_range():
    fields = _spans(additional_dates="2024; 2026")
    assert extra_date_spans(fields, "O") == ["2024", "2026"]


def test_a_span_inside_the_records_own_range_adds_nothing():
    fields = {"start_date": "2010", "end_date": "2020",
              "additional_dates": "2011; 2015", "additional_periods": [
                  {"start_date": "2012", "end_date": "2014"}]}
    assert extra_date_spans(fields, "K1") == []


def test_a_span_repeating_an_earlier_one_is_written_once():
    fields = _spans(additional_dates=[{"start_date": "2022"}, {"start_date": "2022"}])
    assert extra_date_spans(fields, "O") == ["2022"]


def test_a_mm_yy_code_formats_the_span_as_its_own_range():
    fields = {"start_date": "2010-03", "end_date": "2012-06",
              "additional_dates": [{"start_date": "2014-09", "end_date": "2015-01"}]}
    assert with_extra_date_spans("03/10-06/12", fields, "D1") == "03/10-06/12, 09/14-01/15"


def test_no_own_start_year_and_an_empty_cell_take_no_span():
    assert extra_date_spans({"additional_dates": "2022"}, "O") == []
    assert with_extra_date_spans("", _spans(additional_dates="2022"), "O") == ""


def test_a_record_without_extra_spans_keeps_its_cell_unchanged():
    assert with_extra_date_spans("2019-2020", _spans(), "O") == "2019-2020"


def test_a_span_inside_a_running_range_adds_nothing():
    fields = {"start_date": "2010", "end_date": "present",
              "additional_period_start": "2015", "additional_period_end": "2016"}
    assert extra_date_spans(fields, "O") == []
