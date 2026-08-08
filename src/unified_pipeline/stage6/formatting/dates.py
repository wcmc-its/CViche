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

# Date format specifications per WCM template section
# Format codes: 'mm/yyyy', 'mm/yy', 'yyyy', 'mm/dd/yyyy'
DATE_FORMATS = MappingProxyType({
    'B1': 'mm/yyyy',      # Education: Dates attended (mm/yyyy-mm/yyyy)
    'B2': 'mm/yy',        # Other Education: Dates attended (mm/yy – mm/yy)
    'C': 'mm/yy',         # Postdoc Training: Dates (mm/yy - mm/yy)
    'C1': 'mm/yy',
    'C2': 'mm/yy',
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
            return f"{month:02d}/01/{year}"  # Default to 1st of month
        return year

    return date_str


def format_date_range(start_date: str, end_date: str, taxonomy_code: str) -> str:
    """
    Format a date range according to WCM template requirements.

    Args:
        start_date: Start date string
        end_date: End date string (may be 'present', empty, or a date)
        taxonomy_code: Taxonomy code to determine required format

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
