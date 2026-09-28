"""Rendering a date the way one WCM section wants to see it (#398).

The third file under `formatting/`, and split from `values.py` on the same line
`values.py` is split from `docx.py`: this takes a date and returns the string
that appears on the page. What makes it its own file rather than more of
`values.py` is `DATE_FORMATS` -- every function here is a lookup into that one
per-section table, and the table is the thing that changes when the WCM template
changes.

`normalize_iso_dates_in_text` lives here despite its name. It rewrites
"2021-03-01" as "March 2021" inside free text, which is the same job as
`_format_currency` turning 14876 into "$14,876": how the value reads, not what
it is. Normalization would be the wrong shelf -- nothing about the date changes,
only its presentation.

Parsing the string into components is `parsing/dates.py`, imported below. That
direction is one-way and must stay so.
"""
from types import MappingProxyType
import re

from ..parsing.dates import _parse_date_components

# Taxonomy codes whose entries are single occasions, so a record with a start
# date and no end date happened in that year -- it is not still going on
# (#946, decision recorded on the issue 2026-09-24). `format_date_range` renders
# such a record as the bare start, and adds "-Present" only when the source
# itself leaves the range open (`_source_leaves_year_open`). Members, from
# core/taxonomy_v7.json:
#   H   Honors & Awards -- an award is conferred once, in the year given.
#   R   Invitations to Speak/Present -- one talk, one date.
#   K4  CME & Professional Education -- lectures and workshops, each one event.
#   P   Institutional Administrative Activities -- the decision's "P one-off
#       rows": a year alone on a committee or event row names that year.
#   Q2  Service on Boards/Committees -- the extramural counterpart of P.
#   Q3  Grant Reviewing / Study Sections -- a review panel sits per cycle.
# Not members, and unchanged: every D code (the D1 rank ladder is decided in
# sections/positions.py), O and Q1 (leadership posts held for a term), I
# (memberships are ongoing), Q4/Q4A/Q4B/Q4C (editorial posts held for a
# term), and Q4D (a journal reviewer lists the year reviewing began).
# H, R and K4 reach no `format_date_range` caller today -- their renderers
# format a single date -- so for them this set only fixes what a future
# range caller would print.
POINT_IN_TIME_CODES = frozenset({'H', 'R', 'K4', 'P', 'Q2', 'Q3'})


def _source_leaves_year_open(source_text: str, year: int | None) -> bool:
    """True when the source writes `year` as an open range: "2020-",
    "(2011-", "11/2019- Clinical ...", "2024 -<tab>Member".

    Stage 4 records "2020-" as a start date with no end date, the same as a
    bare "2020", but the author wrote the open dash on purpose -- it is how a
    CV says "since 2020". A dash with a space on both sides and text after it
    ("2023 - Excellence Award") is a column separator, not an open range, so
    it does not count.
    """
    if not source_text or year is None:
        return False
    open_range = re.compile(
        rf'(?<!\d){year}(?:[-\u2013\u2014](?!\s*\d)|\s+[-\u2013\u2014][ ]*(?:\t|\)|$))',
        re.MULTILINE)
    return bool(open_range.search(source_text))

# Date format specifications per WCM template section
# Format codes: 'mm/yyyy', 'mm/yy', 'yyyy', 'mm/dd/yyyy'
DATE_FORMATS = MappingProxyType({
    'B1': 'mm/yyyy',      # Education: Dates attended (mm/yyyy-mm/yyyy)
    'B2': 'mm/yy',        # Other Education: Dates attended (mm/yy – mm/yy)
    'C': 'mm/yy',         # Postdoc Training: Dates (mm/yy - mm/yy)
    'C1': 'mm/yy',
    'C2': 'mm/yy',
    'C3': 'mm/yy',
    'D1': 'mm/yy',        # Academic Appointments: Dates (mm/yy - mm/yy)
    'D2': 'mm/yy',        # Hospital Appointments
    'D3': 'mm/yy',        # Other Positions
    'F1': 'mm/dd/yyyy',   # Licensure: Date of issue (mm/dd/yyyy)
    'F2': 'yyyy',         # Board Certification: Dates (yyyy–yyyy)
    'H': 'yyyy',          # Honors: Date awarded (yyyy)
    'I': 'yyyy',          # Memberships: Date (yyyy-yyyy)
    'K1': 'yyyy',         # Teaching activities
    'K2': 'yyyy',
    'K3': 'yyyy',
    'K4': 'yyyy',
    'K5': 'yyyy',
    'M2A': 'mm/yy',       # Grants: various date formats
    'M2B': 'mm/yy',
    'M2C': 'mm/yy',
    'M2D': 'mm/yyyy',     # Patents: filing/issue dates; template specifies no format
    'N3A': 'yyyy',        # Mentoring
    'N3B': 'yyyy',
    'O': 'yyyy',          # Leadership
    'P': 'yyyy',          # Committees: Dates (yyyy-yyyy)
    'Q1': 'yyyy',         # Service activities
    'Q2': 'yyyy',
    'Q3': 'yyyy',
    'Q4': 'yyyy',
    'Q4A': 'yyyy',
    'Q4B': 'yyyy',
    'Q4C': 'yyyy',
    'Q4D': 'yyyy',
    'R': 'yyyy',          # Invited Presentations: Dates (yyyy)
    'S1': 'yyyy',         # Publications: year only
    'S2': 'yyyy',
    'S3': 'yyyy',
    'S4': 'yyyy',
    'S5': 'yyyy',
    'S6': 'yyyy',
    'S7': 'yyyy',
    'S8': 'yyyy',
    'S9': 'yyyy',
})


def format_date_for_section(date_str: str, taxonomy_code: str, is_end_date: bool = False) -> str:
    """
    Format a date string according to the WCM template requirements for a section.

    Args:
        date_str: Input date string (various formats)
        taxonomy_code: Taxonomy code to determine required format
        is_end_date: True if this is an end date (affects 'present' handling)

    Returns:
        Formatted date string according to WCM requirements
    """
    if not date_str:
        return ''

    date_str = str(date_str).strip()

    # Handle 'present', 'current', 'ongoing' - always return as 'Present'
    if date_str.lower() in ('present', 'current', 'ongoing', 'now'):
        return 'Present'

    # Get required format for this taxonomy code
    required_format = DATE_FORMATS.get(taxonomy_code, 'yyyy')

    year, month, day = _parse_date_components(date_str)

    # If we couldn't parse it, return as-is
    if not year:
        return date_str
    year = str(year)

    # Format according to required format
    if required_format == 'yyyy':
        return year
    elif required_format == 'mm/yyyy':
        if month:
            return f"{month:02d}/{year}"
        return year  # Fall back to year only if no month
    elif required_format == 'mm/yy':
        if month:
            return f"{month:02d}/{year[-2:]}"
        # If no month, use full 4-digit year (2-digit looks odd standalone)
        return year
    elif required_format == 'mm/dd/yyyy':
        if month and day:
            return f"{month:02d}/{day:02d}/{year}"
        elif month:
            # No day in the source: degrade to mm/yyyy rather than fabricate
            # the 1st of the month — the CV never stated a day (#575).
            return f"{month:02d}/{year}"
        return year

    return date_str


def format_date_range(start_date: str, end_date: str, taxonomy_code: str,
                      source_text: str = '') -> str:
    """
    Format a date range according to WCM template requirements.

    A start with no end renders "<start>-Present", except for a
    `POINT_IN_TIME_CODES` code, which renders the bare start unless
    `source_text` leaves that year open (#946).

    Args:
        start_date: Start date string
        end_date: End date string (may be 'present', empty, or a date)
        taxonomy_code: Taxonomy code to determine required format
        source_text: The entry's source text, read only for a
            `POINT_IN_TIME_CODES` code with no end date

    Returns:
        Formatted date range string (e.g., "08/17-07/21" or "2017-Present")
    """
    formatted_start = format_date_for_section(start_date, taxonomy_code)
    formatted_end = format_date_for_section(end_date, taxonomy_code, is_end_date=True)

    if formatted_start and formatted_end:
        # Avoid redundant ranges like "2024-2024" when both resolve to the same string
        if formatted_start == formatted_end:
            return formatted_start
        return f"{formatted_start}-{formatted_end}"
    elif formatted_start:
        # Avoid "Present-Present" when start is already 'Present'
        if formatted_start == 'Present':
            return 'Present'
        if taxonomy_code in POINT_IN_TIME_CODES:
            start_year = _parse_date_components(str(start_date).strip())[0]
            if not _source_leaves_year_open(source_text, start_year):
                return formatted_start
        return f"{formatted_start}-Present"
    elif formatted_end:
        return formatted_end
    return ''


_MONTH_NAMES = MappingProxyType({
    '01': 'January', '02': 'February', '03': 'March', '04': 'April',
    '05': 'May', '06': 'June', '07': 'July', '08': 'August',
    '09': 'September', '10': 'October', '11': 'November', '12': 'December',
    '1': 'January', '2': 'February', '3': 'March', '4': 'April',
    '5': 'May', '6': 'June', '7': 'July', '8': 'August',
    '9': 'September',
})

def normalize_iso_dates_in_text(text: str) -> str:
    """Replace ISO-format dates in free text with human-readable equivalents.

    Handles patterns the Stage 5c LLM sometimes produces:
      2021-03-01  -> March 2021
      2019-08-01–2019-09-01  -> August 2019–September 2019
      2018-08  -> August 2018
      2012-06–2012-07  -> June 2012–July 2012
    """
    if not text:
        return text

    def _iso_to_readable(m):
        year, month = m.group(1), m.group(2)
        day = m.group(3) if m.lastindex >= 3 and m.group(3) else None
        month_name = _MONTH_NAMES.get(month, month)
        return f"{month_name} {year}"

    # YYYY-MM-DD (drop the day)
    text = re.sub(r'\b(\d{4})[-/](0?[1-9]|1[0-2])[-/](0?[1-9]|[12]\d|3[01])\b', _iso_to_readable, text)
    # YYYY-MM (no day)
    text = re.sub(r'\b(\d{4})[-/](0?[1-9]|1[0-2])\b', _iso_to_readable, text)

    return text
