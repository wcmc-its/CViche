"""Reverse-chronological ordering, the default for a dated WCM section (#398).

`extract_sort_date` is the key and `sort_entries_reverse_chronological` applies
it; they are one thing in two functions because sixteen section writers call the
sort and a couple need the key alone.

The key is total on purpose. A record with no parseable date sorts to (0, 0, 0)
-- last -- and a current one to (9999, 12, 31) -- first. Neither raises, because
a section that raises mid-render loses the whole document.
"""
from collections.abc import Mapping

from ..parsing.dates import (
    RANGE_END_KEY,
    RANGE_START_KEY,
    _parse_date_components,
)


def _range_sort_text(value: object) -> object:
    """The one date a `{start_date, end_date}` mapping sorts by (#1233): its
    end, else its start -- the order the flat `end_date`/`start_date` fields are
    tried in below, since a range ends after it starts. Any other value is
    returned as it came.

    Stage 4 returns R's `date` and F2's `recertification_date` this way. Without
    the unwrap `str()` below turned the mapping into its repr, no year is
    findable in that, and the entry keyed (0, 0, 0): a correctly dated row
    landed at the bottom of a reverse-chronological table.
    """
    if isinstance(value, Mapping):
        return _range_sort_text(value.get(RANGE_END_KEY) or value.get(RANGE_START_KEY) or '')
    return value


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
        fields.get('recertification_date', ''),
        fields.get('year', ''),
        fields.get('year_awarded', ''),
        fields.get('start_date', ''),
        fields.get('year_certified', ''),
        fields.get('date', ''),
        fields.get('publication_date', ''),
    ]

    for date_str in map(_range_sort_text, date_candidates):
        if not date_str:
            continue

        date_str = str(date_str).strip().lower()

        # 'Present' or 'current' sorts first (most recent)
        if date_str in ('present', 'current', 'ongoing', 'now'):
            return (9999, 12, 31)

        # Shared parser (see format_date_for_section). Month/day absent from the
        # input default to 1 so partial dates ("2019", "Aug. 2021") still sort
        # sensibly within their year.
        year, month, day = _parse_date_components(date_str)
        if year:
            return (year, month if month is not None else 1,
                    day if day is not None else 1)

    # No date found - sort last
    return (0, 0, 0)


def _field_sort_date(entry: dict, field: str) -> tuple:
    """`entry`'s sort key read from `field` alone: `extract_sort_date` over a
    copy that carries nothing else. A non-mapping `extracted_fields` is a
    missing one, as it is in `extract_sort_date`."""
    raw_fields = entry.get('extracted_fields')
    value = raw_fields.get(field) if isinstance(raw_fields, Mapping) else None
    return extract_sort_date({'extracted_fields': {field: value}})


def sort_entries_reverse_chronological(
    entries: list[dict], then_by: str | None = None
) -> list[dict]:
    """
    Sort entries in reverse chronological order (most recent first).

    Entries with 'Present' or current dates sort first.
    Entries with no date sort last.

    Args:
        entries: List of entry dictionaries
        then_by: Optional date field that orders entries whose keys tie, most
            recent first (#1233). Default: ties keep their input order.

    Returns:
        Sorted list of entries
    """
    if then_by is None:
        return sorted(entries, key=extract_sort_date, reverse=True)
    return sorted(
        entries,
        key=lambda entry: (extract_sort_date(entry), _field_sort_date(entry, then_by)),
        reverse=True,
    )
