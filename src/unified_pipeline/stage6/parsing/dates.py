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


def _parse_date_components(date_str: str):
    """Parse a date string into (year, month, day) ints; any component absent
    from the input is None. Returns (None, None, None) when nothing parses.

    Single source of truth for date parsing, shared by format_date_for_section
    (rendering) and extract_sort_date (reverse-chron sorting) so the two cannot
    drift. They previously carried near-duplicate copies that HAD drifted: the
    sort copy lacked the '\\.?' in the month-name pattern, so "Aug. 2021" /
    "Sept. 2019" failed every branch and the entry sorted to the bottom of its
    section while still rendering its date correctly (issue #266).
    """
    s = str(date_str or '').strip()
    if not s:
        return (None, None, None)
    # YYYY-MM-DD / YYYY/MM/DD
    m = re.match(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', s)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    # MM/DD/YYYY / MM-DD-YYYY
    m = re.match(r'(\d{1,2})[-/](\d{1,2})[-/](\d{4})', s)
    if m:
        return (int(m.group(3)), int(m.group(1)), int(m.group(2)))
    # YYYY-MM / YYYY/MM (disjoint from MM/YYYY below: 4-digit lead vs 4-digit tail)
    m = re.match(r'(\d{4})[-/](\d{1,2})$', s)
    if m:
        return (int(m.group(1)), int(m.group(2)), None)
    # MM/YYYY / MM-YYYY
    m = re.match(r'(\d{1,2})[-/](\d{4})', s)
    if m:
        return (int(m.group(2)), int(m.group(1)), None)
    # Just YYYY
    m = re.match(r'^(\d{4})$', s)
    if m:
        return (int(m.group(1)), None, None)
    # Month YYYY -- "August 2021", "Aug 2021", "Aug. 2021", "Sept. 2019"
    m = re.match(r'([a-zA-Z]+)\.?\s*(\d{4})', s)
    if m:
        return (int(m.group(2)), _MONTH_NAME_TO_NUM.get(m.group(1).lower()), None)
    return (None, None, None)


def _get_entry_date_range(entry: Dict) -> tuple:
    """Extract (start_date, end_date) strings from an entry for dedup comparison."""
    fields = entry.get('extracted_fields', {}) or {}
    return (fields.get('start_date', '') or '', fields.get('end_date', '') or '')


def _dates_overlap_or_match(entry_a: Dict, entry_b: Dict) -> bool:
    """Return True if two entries have the same or overlapping date ranges.

    Used to distinguish true duplicates (same thing listed twice) from career
    progressions (different roles at the same institution in different periods).
    If either entry lacks dates, we conservatively return True (assume possible dup).
    """
    start_a, end_a = _get_entry_date_range(entry_a)
    start_b, end_b = _get_entry_date_range(entry_b)
    # If either lacks dates, can't prove they're different — allow dedup
    if not start_a or not start_b:
        return True
    # Exact match (most common for true duplicates)
    if start_a == start_b and end_a == end_b:
        return True
    # Check overlap: parse to comparable strings (YYYY-MM format sorts correctly)
    # Normalize to just YYYY-MM for comparison
    sa = start_a[:7]  # "2008-07" from "2008-07-01"
    sb = start_b[:7]
    ea = (end_a[:7] if end_a and end_a.lower() != 'present' else '9999-12')
    eb = (end_b[:7] if end_b and end_b.lower() != 'present' else '9999-12')
    # Overlap test: A.start <= B.end AND B.start <= A.end
    return sa <= eb and sb <= ea
