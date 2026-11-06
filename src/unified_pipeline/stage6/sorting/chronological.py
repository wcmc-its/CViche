"""Reverse-chronological ordering, the default for a dated WCM section (#398).

`extract_sort_date` is the key and `sort_entries_reverse_chronological` applies
it; they are one thing in two functions because sixteen section writers call the
sort and a couple need the key alone.

The key is total on purpose. A record with no parseable date sorts to (0, 0, 0)
-- last -- and a current one to (9999, 12, 31) -- first. Neither raises, because
a section that raises mid-render loses the whole document.

Rows tied on that date are ordered by their start date, newest first (#2003).
A tied row whose start can't be read keeps its place, and rows tied on both
dates keep source order.
"""
from collections.abc import Mapping
from itertools import groupby

from ..parsing.dates import (
    CURRENT_DATE_VALUES,
    RANGE_END_KEY,
    RANGE_START_KEY,
    _parse_date_components,
)

# The key of a row whose date says it is ongoing ('Present', 'current', ...):
# sorts above every real date.
_CURRENT_SORT_DATE = (9999, 12, 31)
# The key of a row with no parseable date: sorts below every real one.
_NO_SORT_DATE = (0, 0, 0)


def _range_sort_text(value: object) -> object:
    """The one date a `{start_date, end_date}` mapping sorts by (#1233): its
    end, else its start -- the order the flat `end_date`/`start_date` fields are
    tried in below, since a range ends after it starts. Any other value is
    returned as it came.

    Stage 4 returns R's and K4's `date` this way. Without the unwrap `str()`
    below turned the mapping into its repr, no year is findable in that, and the
    entry keyed (0, 0, 0): a correctly dated row landed at the bottom of a
    reverse-chronological table.
    """
    if isinstance(value, Mapping):
        return _range_sort_text(value.get(RANGE_END_KEY) or value.get(RANGE_START_KEY) or '')
    return value


def _recertification_sort_text(value: object) -> object:
    """F2's `recertification_date` as the key reads it: a `{start_date, end_date}`
    range is left out, so the entry falls through to `year_certified` (#1233).

    That is where it fell before #1233 (the mapping's repr held no year) and
    where the string form of a range ("2008-2018") still falls (it holds none
    either). Keying the mapping by its end would rank a certification by the
    end of a renewal window, which can be a future expiry, and reorder F2 rows
    that today sit in `year_certified` order. Any other value is returned as it
    came.
    """
    return '' if isinstance(value, Mapping) else value


def extract_sort_date(entry: dict) -> tuple:
    """
    Extract a sortable date tuple from an entry for reverse-chronological ordering.

    Returns (year, month, day) tuple where:
    - 'Present'/current entries get (9999, 12, 31) to sort first
    - Entries with dates get their actual values
    - Entries with no date get (0, 0, 0) to sort last

    Args:
        entry: Entry dictionary with extracted_fields

    Returns:
        Tuple (year, month, day) for sorting
    """
    # Total over malformed payloads too: a truthy non-dict (a stray list, say)
    # must sort to (0, 0, 0), not AttributeError on fields.get(). This subsumes
    # the `or {}` form -- that one still reaches .get() on a truthy non-dict.
    raw_fields = entry.get('extracted_fields')
    fields = raw_fields if isinstance(raw_fields, Mapping) else {}

    # Try various date fields in order of preference
    #
    # `recertification_date` / `year_certified` are F2 (Board Certification)
    # fields -- the schema (field_schemas_v1.json / v1.1.json) defines both
    # nowhere else, so no other section's entries can carry them and no other
    # section's sort key can change by adding them here (#625 thread
    # 3850478580: the sort was wired into board_certification.py but its real
    # date fields weren't in this list, so real F2 entries all keyed to
    # (0, 0, 0) and kept their input order). `recertification_date` is placed
    # near `end_date` and `year_certified` near `start_date` because a
    # recertification is the more recent event for a certification renewed
    # over time, so it should dominate `year_certified` the same way an
    # `end_date` dominates a `start_date`.
    date_candidates = [
        fields.get('end_date', ''),
        _recertification_sort_text(fields.get('recertification_date', '')),
        fields.get('year', ''),
        fields.get('year_awarded', ''),
        fields.get('start_date', ''),
        fields.get('year_certified', ''),
        fields.get('date', ''),
        fields.get('publication_date', ''),
    ]

    for date_str in map(_range_sort_text, date_candidates):
        sort_date = _date_sort_tuple(date_str)
        if sort_date is not None:
            return sort_date

    # No date found - sort last
    return _NO_SORT_DATE


def _date_sort_tuple(value: object) -> tuple | None:
    """One date value as a (year, month, day) sort tuple, or None when it holds
    no date. 'Present', 'current', 'to date' key to `_CURRENT_SORT_DATE`.

    Shared parser (see format_date_for_section). Month/day absent from the
    input default to 1 so partial dates ("2019", "Aug. 2021") still sort
    sensibly within their year.
    """
    if not value:
        return None
    date_str = str(value).strip().lower()
    if date_str in CURRENT_DATE_VALUES:
        return _CURRENT_SORT_DATE
    year, month, day = _parse_date_components(date_str)
    if not year:
        return None
    return (year, month if month is not None else 1,
            day if day is not None else 1)


def _range_start_text(value: object) -> object:
    """The start of a `{start_date, end_date}` mapping; any other value as it
    came. The counterpart of `_range_sort_text`, for the tie-break key."""
    if isinstance(value, Mapping):
        return _range_start_text(value.get(RANGE_START_KEY) or '')
    return value


def _start_sort_date(entry: dict) -> tuple | None:
    """When the row started, as a sort tuple, or None when that is unknown --
    the tie-break between rows `extract_sort_date` ties (#2003).

    A row dated by one point (a `year`, a `date`, a start alone) starts at that
    point, so it keys to its `extract_sort_date`. A span whose start is
    unreadable -- an end date alone, or an ongoing row with no start -- has no
    start to compare, and is None.
    """
    raw_fields = entry.get('extracted_fields')
    fields = raw_fields if isinstance(raw_fields, Mapping) else {}
    start = _readable_start(fields)
    if start is not None:
        return start
    primary = extract_sort_date(entry)
    has_end = _date_sort_tuple(_range_sort_text(fields.get('end_date', ''))) is not None
    if has_end or primary == _CURRENT_SORT_DATE:
        return None
    return primary


def _readable_start(fields: Mapping) -> tuple | None:
    """A span's start date as a sort tuple, or None when the row is not a span
    or its start can't be read.

    A span is a row with an `end_date` or a range-valued `date`. Its start is
    read from `start_date`, else the start of that range, else a plain `date`
    beside the `end_date` -- stage 4 writes a span's start there too (e.g.
    `{'date': '1995', 'end_date': 'present'}`). A row with no end -- a start
    alone, a `year`, a plain `date` -- is dated by one point, and a more
    precise `start_date` beside its `year` (a bibliography entry's meeting
    day) is not read: it would rank the entry above its year's undated ones.
    """
    date_value = fields.get('date', '')
    if isinstance(date_value, Mapping):
        date_start = _range_start_text(date_value)
    elif fields.get('end_date'):
        date_start = date_value
    else:
        return None
    for value in (_range_start_text(fields.get('start_date', '')), date_start):
        sort_date = _date_sort_tuple(value)
        if sort_date is not None:
            return sort_date
    return None


def _order_ties_by_start(rows: list[dict]) -> list[dict]:
    """Within each run of rows tied on `extract_sort_date`, reorder the rows
    with a known start by that start, latest first (#2003): two ongoing roles,
    or two that ended the same year, order by which began later.

    A tied row with no known start keeps its slot, so where it lands is what it
    was before the tie-break existed: nothing says whether it began before or
    after its neighbours. Rows tied on both dates keep their order (stable sort).
    """
    ordered = list(rows)
    position = 0
    for _, run in groupby(rows, key=extract_sort_date):
        run_length = len(list(run))
        slots = [i for i in range(position, position + run_length)
                 if _start_sort_date(rows[i]) is not None]
        by_start = sorted((rows[i] for i in slots), key=_start_sort_date, reverse=True)
        for slot, row in zip(slots, by_start, strict=True):
            ordered[slot] = row
        position += run_length
    return ordered


def sort_entries_reverse_chronological(entries: list[dict]) -> list[dict]:
    """
    Sort entries in reverse chronological order (most recent first).

    Entries with 'Present' or current dates sort first.
    Entries with no date sort last.
    Entries tied on that date sort by start date, latest first, where the
    start is known (see `_order_ties_by_start`).

    Args:
        entries: List of entry dictionaries

    Returns:
        Sorted list of entries
    """
    by_date = sorted(entries, key=extract_sort_date, reverse=True)
    return _order_ties_by_start(by_date)
