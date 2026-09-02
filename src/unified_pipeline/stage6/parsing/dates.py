"""Reading dates off raw text and off an extracted record (#398).

The sibling of `text.py` and `records.py`, split out because dates are the one
value in a CV that both of them touch: `_parse_date_components` reads a string,
`_get_entry_date_range` reads a record, and `_dates_overlap_or_match` compares
two of them. Keeping the three in one file is what stops the string parser and
the record reader drifting -- they already had, once: the sort path carried a
near-copy of the parser missing the `\\.?` in its month-name pattern, so
"Aug. 2021" rendered correctly and still sorted to the bottom of its section
(#266).

Read-only, like the rest of `parsing/`: a string or a record in, an answer about
it out. Deciding how a date should *look* on the page is `formatting/dates.py`;
deciding what order records go in is `sorting/`. Both import from here, so
nothing in this file may import from either.
"""
import datetime
from types import MappingProxyType
import re
from typing import Dict

# Month name -> month number, for the date parser below. Includes the common
# 3-4 letter abbreviations CVs use ("Aug", "Sept"). Distinct from _MONTH_NAMES
# further down, which is the reverse (number -> name) for range formatting.
_MONTH_NAME_TO_NUM = MappingProxyType({
    'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
    'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11,
    'december': 12,
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'jun': 6, 'jul': 7, 'aug': 8,
    'sep': 9, 'sept': 9, 'oct': 10, 'nov': 11, 'dec': 12,
})

# Season names a CV states in place of a month ("Fall 2016"): the year is
# real, the month is not stated, distinct from an unknown token that names
# neither (#716 review round 1 -- an unknown alphabetic token used to fall
# back to the same (year, None, None) a season gets, making "Foo 2021" and
# "Fall 2016" indistinguishable).
_SEASON_TOKENS = frozenset({'spring', 'summer', 'fall', 'autumn', 'winter'})

# The keywords that mean "still ongoing" wherever a CV omits an end date.
# Single source of truth, shared by formatting/dates.py (rendering) and
# sorting/chronological.py (reverse-chron ordering) so the vocabulary can't
# drift between the three date modules again -- it already had (#553): this
# module recognized only 'present', so an end_date of "ongoing" reached
# _dates_overlap_or_match as a literal string instead of the open-ended
# range the other two modules already treat it as.
CURRENT_DATE_VALUES = frozenset({'present', 'current', 'ongoing', 'now'})


def _parse_date_components(date_str: str):
    """Parse a date string into (year, month, day) ints; any component absent
    from the input is None. Returns (None, None, None) when nothing parses.

    Single source of truth for date parsing, shared by format_date_for_section
    (rendering) and extract_sort_date (reverse-chron sorting) so the two cannot
    drift. They previously carried near-duplicate copies that HAD drifted: the
    sort copy lacked the '\\.?' in the month-name pattern, so "Aug. 2021" /
    "Sept. 2019" failed every branch and the entry sorted to the bottom of its
    section while still rendering its date correctly (issue #266).

    Every pattern below is `re.fullmatch`, not `re.match` (#716 review round
    1): a list or a range packed into one field -- "february 2022, july 2022
    and july 2023", "2006-07-14 to 2006-07-16" -- is not a single date, and is
    left to render/sort as the literal text it is rather than being read as
    its first date.

    A shape that parses is also calendar-checked, not just digit-shaped: an
    impossible day (2024-04-31) degrades to (year, month, None) since the
    month is still trustworthy, and an impossible month (2021-13) drops the
    whole date, since nothing about it can be.
    """
    s = str(date_str or '').strip()
    if not s:
        return (None, None, None)
    # YYYY-MM-DD / YYYY/MM/DD
    m = re.fullmatch(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', s)
    if m:
        return _validate_full_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    # MM/DD/YYYY / MM-DD-YYYY
    m = re.fullmatch(r'(\d{1,2})[-/](\d{1,2})[-/](\d{4})', s)
    if m:
        return _validate_full_date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    # YYYY-MM / YYYY/MM (disjoint from MM/YYYY below: 4-digit lead vs 4-digit tail)
    m = re.fullmatch(r'(\d{4})[-/](\d{1,2})', s)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        return (year, month, None) if 1 <= month <= 12 else (None, None, None)
    # MM/YYYY / MM-YYYY
    m = re.fullmatch(r'(\d{1,2})[-/](\d{4})', s)
    if m:
        year, month = int(m.group(2)), int(m.group(1))
        return (year, month, None) if 1 <= month <= 12 else (None, None, None)
    # Just YYYY
    m = re.fullmatch(r'(\d{4})', s)
    if m:
        return (int(m.group(1)), None, None)
    # Month YYYY -- "August 2021", "Aug 2021", "Aug. 2021", "Sept. 2019",
    # or a season ("Fall 2016": year kept, month left unstated).
    m = re.fullmatch(r'([a-zA-Z]+)\.?\s*(\d{4})', s)
    if m:
        token = m.group(1).lower()
        year = int(m.group(2))
        if token in _MONTH_NAME_TO_NUM:
            return (year, _MONTH_NAME_TO_NUM[token], None)
        if token in _SEASON_TOKENS:
            return (year, None, None)
        return (None, None, None)
    return (None, None, None)


def _validate_full_date(year: int, month: int, day: int):
    """Calendar-check a full y/m/d triple parsed off a complete-date pattern.

    An invalid day (2024-04-31, a non-leap 2023-02-29) degrades to
    (year, month, None): the month is still stated and valid even though the
    day is not. An invalid month drops the whole date -- nothing about it can
    be trusted once the month itself is impossible.
    """
    try:
        datetime.date(year, month, day)
    except ValueError:
        return (year, month, None) if 1 <= month <= 12 else (None, None, None)
    return (year, month, day)


def _get_entry_date_range(entry: Dict) -> tuple:
    """Extract (start_date, end_date) strings from an entry for dedup comparison."""
    fields = entry.get('extracted_fields', {}) or {}
    return (fields.get('start_date', '') or '', fields.get('end_date', '') or '')


# Sentinel for a range whose end is absent or a current-date keyword: it has no
# upper bound, so nothing can be proven to start after it ends.
_OPEN_ENDED = object()


def _overlap_boundary(date_str: str, *, is_end: bool):
    """One range boundary for `_dates_overlap_or_match`, at the granularity the
    CV actually states: `(year, month-or-None)`, `_OPEN_ENDED`, or None.

    An end that is blank or a current-date keyword is `_OPEN_ENDED`. Anything
    else that does not parse to at least a year is None -- unknown, NOT
    open-ended: a boundary we cannot read is a boundary we cannot reason from.
    A stated year with no stated month keeps `month=None` rather than being
    filled in; see `_dates_overlap_or_match` for why nothing is imputed.
    """
    # str()-coerced the same way _parse_date_components coerces its own input
    # (#716 review round 1): the two entry paths must treat a malformed,
    # non-string extracted value identically rather than one stringifying and
    # the other calling .strip() on it directly.
    s = str(date_str or '').strip()
    if is_end and (not s or s.lower() in CURRENT_DATE_VALUES):
        return _OPEN_ENDED
    year, month, _day = _parse_date_components(s)
    if year is None:
        return None
    return (year, month)


def _ends_strictly_before(end, start) -> bool:
    """True only when the stated data PROVES `end` precedes `start`.

    Decidable when the years differ, or when the years are equal and both
    months are stated. Equal years with a month missing on either side is not
    decidable at the granularity available, so it is not a proof.
    """
    if end is _OPEN_ENDED:
        return False
    end_year, end_month = end
    start_year, start_month = start
    if end_year != start_year:
        return end_year < start_year
    return (end_month is not None and start_month is not None
            and end_month < start_month)


def _dates_overlap_or_match(entry_a: Dict, entry_b: Dict) -> bool:
    """Return True if two entries have the same, overlapping, or unprovably
    distinct date ranges -- the dedup path's "these could be the same thing".

    Used to distinguish true duplicates (same thing listed twice) from career
    progressions (different roles at the same institution in different periods).
    If either entry lacks dates, we conservatively return True (assume possible dup).

    The comparison is granularity-honest (#553): each boundary is read at the
    precision the CV states -- (year, month) when a month is given, (year, None)
    when it is not -- and two ranges are called disjoint only when the stated
    data proves it, never by imputing a month nobody wrote. Where no proof is
    available (an unreadable boundary, or the same year with a month missing on
    either side) this returns True, the same "can't prove they differ" answer
    the missing-date guard above already gives; the decision to actually drop an
    entry stays with the content gates behind this one (`_drop_is_safe`). An
    entry whose OWN end is stated before its own start (#716) is malformed
    extraction, not a real range, and is treated the same conservative way.
    """
    start_a, end_a = _get_entry_date_range(entry_a)
    start_b, end_b = _get_entry_date_range(entry_b)
    # If either lacks dates, can't prove they're different -- allow dedup
    if not start_a or not start_b:
        return True
    # Exact match (most common for true duplicates)
    if start_a == start_b and end_a == end_b:
        return True
    boundaries = (_overlap_boundary(start_a, is_end=False),
                  _overlap_boundary(start_b, is_end=False),
                  _overlap_boundary(end_a, is_end=True),
                  _overlap_boundary(end_b, is_end=True))
    if any(b is None for b in boundaries):
        return True  # a boundary we can't read -- can't prove they differ
    start_a_b, start_b_b, end_a_b, end_b_b = boundaries
    # An inverted range (an entry's own end stated before its own start)
    # proves nothing about either entry -- it is malformed extraction, not
    # data (#716 review round 1). Conservative True, same as an unreadable
    # boundary above, rather than letting the malformed range "prove" a
    # disjointness the CV never stated.
    if (_ends_strictly_before(end_a_b, start_a_b)
            or _ends_strictly_before(end_b_b, start_b_b)):
        return True
    return not (_ends_strictly_before(end_a_b, start_b_b)
                or _ends_strictly_before(end_b_b, start_a_b))
