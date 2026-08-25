"""Reverse-chronological ordering, the default for a dated WCM section (#398).

`extract_sort_date` is the key and `sort_entries_reverse_chronological` applies
it; they are one thing in two functions because sixteen section writers call the
sort and a couple need the key alone.

The key is total on purpose. A record with no parseable date sorts to (0, 0, 0)
-- last -- and a current one to (9999, 12, 31) -- first. Neither raises, because
a section that raises mid-render loses the whole document.
"""
from collections.abc import Mapping
from typing import Dict, List

from ..parsing.dates import _parse_date_components

def extract_sort_date(entry: Dict) -> tuple:
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
    date_candidates = [
        fields.get('end_date', ''),
        fields.get('year', ''),
        fields.get('year_awarded', ''),
        fields.get('start_date', ''),
        fields.get('date', ''),
        fields.get('publication_date', ''),
    ]

    for date_str in date_candidates:
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


def sort_entries_reverse_chronological(entries: List[Dict]) -> List[Dict]:
    """
    Sort entries in reverse chronological order (most recent first).

    Entries with 'Present' or current dates sort first.
    Entries with no date sort last.

    Args:
        entries: List of entry dictionaries

    Returns:
        Sorted list of entries
    """
    return sorted(entries, key=extract_sort_date, reverse=True)
