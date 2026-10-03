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
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, NamedTuple
import logging
import re

from ..parsing.dates import (
    RANGE_END_KEY,
    RANGE_START_KEY,
    _parse_date_components,
    _year_of_calendar_invalid_date,
)

logger = logging.getLogger(__name__)

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
#   B2  Other Educational Experiences -- a workshop, course or conference
#       attended once; the table's column is "Dates attended" (#1220).
# Not members, and unchanged: every D code (the D1 rank ladder is decided in
# sections/positions.py), O and Q1 (leadership posts held for a term), I
# (memberships are ongoing), Q4/Q4A/Q4B/Q4C (editorial posts held for a
# term), and Q4D (a journal reviewer lists the year reviewing began).
# H, R and K4 reach no `format_date_range` caller today -- their renderers
# format a single date -- so for them this set only fixes what a future
# range caller would print.
# A member renders the bare start even when its caller passes no source text.
# Every other code gets the same rule wherever its caller does pass the
# entry's text: a start with no end is that year alone, unless the source
# leaves it open (decision 2026-10-02 on the s7ab autopsy's class 13: a one-
# year chair, a guest-edited issue or a 1989 postdoc read "-Present"). A
# caller that passes no text keeps "<start>-Present" for a non-member.
POINT_IN_TIME_CODES = frozenset({'H', 'R', 'K4', 'P', 'Q2', 'Q3', 'B2'})


# The words that close a range as still open, after the record's own year
# and a dash or "to": "2011 - present", "2011 to current" (decision
# 2026-10-02, class 13). Anchored on the year because a row routinely says
# "now Professor at ..." or names "Current Opinion in ..." about something
# else; a spaced dash followed by text is otherwise a column separator.
_ONGOING_RANGE_END_WORDS = r'(?:present|current|now)\b'

# A month, or a month and a day, written after the year with dots:
# "2014.01", "2014.01.15". A CV that dates its rows this way writes an open
# range "2014.01 - <journal>" (EBYSBC autopsy, class E9: three editorial
# boards read as one-time because the year was not followed by the dash).
_DOTTED_MONTH_DAY = r'(?:\.\d{1,2}){0,2}'


def _source_leaves_year_open(source_text: str, year: int | None) -> bool:
    """True when the source writes `year` as an open range: "2020-",
    "(2011-", "11/2019- Clinical ...", "2024 -<tab>Member", "2014.01- ...",
    or closes it in words: "2011 - present", "2011 to current", "2011 - now".

    Stage 4 records "2020-" as a start date with no end date, the same as a
    bare "2020", but the author wrote the open dash on purpose -- it is how a
    CV says "since 2020". A dash with a space on both sides and text after it
    ("Award winner, 2023 - Excellence Award") is a column separator, not an
    open range, so it does not count -- except at the very start of the
    entry. "2021 - Member, ..." and "2014.01 - Editorial Board, ..." open
    the entry with the year and a dash: that is the CV's date column saying
    "since 2021" (class E9, EBYSBC autopsy). It also covers "2021 -<tab>",
    whose tab the reader turns into a space before this text is stored, so
    the tab rule above never sees it. A digit after the dash is a closed
    range ("2021 - 2023 Member") and does not count.
    """
    if not source_text or year is None:
        return False
    year_token = rf'(?<!\d){year}{_DOTTED_MONTH_DAY}'
    open_range = re.compile(
        rf'{year_token}(?:[-\u2013\u2014](?!\s*\d)|\s+[-\u2013\u2014][ ]*(?:\t|\)|$)'
        rf'|\s*(?:[-\u2013\u2014]|\bto\b)\s*{_ONGOING_RANGE_END_WORDS})',
        re.MULTILINE | re.IGNORECASE)
    entry_opens_with_open_year = re.compile(
        rf'\A\s*{year_token}\s+[-\u2013\u2014][ \t]*(?=[^\s\d])')
    return bool(open_range.search(source_text)
                or entry_opens_with_open_year.match(source_text))

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


def _format_date_mapping(value: Mapping, taxonomy_code: str) -> str:
    """A `{start_date, end_date}` mapping read as the range it states (#1233).

    Every caller hands `format_date_for_section` the raw stage-4 field, and
    before this a mapping fell through to `str(value)`: no year is findable in
    the repr, so the "not a date at all" passthrough returned the repr and
    `{'start_date': '2008', 'end_date': '2018'}` was printed into the cell. It
    reads as the range here, so it renders exactly as the same two dates do
    from a `format_date_range` call.

    A mapping with neither key holds no date this module can find, so it
    renders blank and says so in the log -- never as its repr.
    """
    if RANGE_START_KEY not in value and RANGE_END_KEY not in value:
        logger.warning(
            "date value is a mapping without %s/%s keys (keys: %s) -- "
            "rendering it blank rather than as its repr (#1233)",
            RANGE_START_KEY, RANGE_END_KEY, sorted(map(str, value)))
        return ''
    return format_date_range(value.get(RANGE_START_KEY) or '',
                             value.get(RANGE_END_KEY) or '', taxonomy_code)


# An end date that says the range is still running.
_ONGOING_DATE_WORDS = frozenset({'present', 'current', 'ongoing', 'now'})


def format_date_for_section(date_str: str | Mapping, taxonomy_code: str,
                            is_end_date: bool = False) -> str:
    """
    Format a date string according to the WCM template requirements for a section.

    Args:
        date_str: Input date string (various formats), or a
            `{start_date, end_date}` mapping, which formats as the range
        taxonomy_code: Taxonomy code to determine required format
        is_end_date: True if this is an end date (affects 'present' handling)

    Returns:
        Formatted date string according to WCM requirements
    """
    if not date_str:
        return ''

    if isinstance(date_str, Mapping):
        return _format_date_mapping(date_str, taxonomy_code)

    date_str = str(date_str).strip()

    # Handle 'present', 'current', 'ongoing' - always return as 'Present'
    if date_str.lower() in _ONGOING_DATE_WORDS:
        return 'Present'

    # Get required format for this taxonomy code
    required_format = DATE_FORMATS.get(taxonomy_code, 'yyyy')

    year, month, day = _parse_date_components(date_str)

    # A numeric date with an impossible month renders its year alone -- never
    # the invalid fragment as if it were data, never a guessed month (#543,
    # owner decision 2026-09-01).
    # No month survives, so the format falls through to its year-only branch.
    if not year:
        year = _year_of_calendar_invalid_date(date_str)
    # Not a date at all ("TBD", "n/a"): return as-is
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

    A start with no end renders the bare start unless `source_text` leaves
    that year open, for a `POINT_IN_TIME_CODES` code (#946) and for any code
    whose caller passes `source_text` (class 13, 2026-10-02). Otherwise it
    renders "<start>-Present".

    Args:
        start_date: Start date string
        end_date: End date string (may be 'present', empty, or a date)
        taxonomy_code: Taxonomy code to determine required format
        source_text: The entry's source text, read only when there is a
            start and no end date

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
        if taxonomy_code in POINT_IN_TIME_CODES or source_text:
            start_year = _parse_date_components(str(start_date).strip())[0]
            if not _source_leaves_year_open(source_text, start_year):
                return formatted_start
        return f"{formatted_start}-Present"
    elif formatted_end:
        return formatted_end
    return ''


# Keys outside every schema under which stage 4 files a record's second and
# later spans (EBYSBC class E22, #1245): a list of `{start_date, end_date}`
# periods, or a string of dates, under `additional_dates`/`additional_periods`
# (VNUAHA 142's second interim-chair term, EQADVR 33/34's later teaching
# years), and one more span as a start/end pair (HZGJFM 190's 1994-present
# committee term). No schema names them, so `fan_out._RENDERED_FIELDS` (which
# may hold schema fields only) does not either; the doctor's offschema_fields
# lint grades them against the record's own line instead.
EXTRA_SPAN_LIST_KEYS = ('additional_dates', 'additional_periods')
EXTRA_SPAN_START_KEY = 'additional_period_start'
EXTRA_SPAN_END_KEY = 'additional_period_end'
EXTRA_SPAN_KEYS = frozenset({*EXTRA_SPAN_LIST_KEYS, EXTRA_SPAN_START_KEY,
                             EXTRA_SPAN_END_KEY})

# How the spans of one record are joined in its date cell: "2019-2020, 2021".
DATE_SPAN_SEPARATOR = ', '

# A string of extra dates is a list: "1993-12; 1995-09" or "2010, 2012".
_EXTRA_DATE_STRING_SPLIT_RE = re.compile(r'\s*[;,]\s*')

# Upper bound of a span still running ("present"), for `_span_is_covered`.
_OPEN_END_YEAR = 10_000


class _Span(NamedTuple):
    """One extra span as stage 4 wrote it: a start and an optional end."""
    start: object
    end: object


def _extra_spans(fields: Mapping[str, Any]) -> list[_Span]:
    """The spans under `EXTRA_SPAN_KEYS`, in the order stage 4 wrote them."""
    spans: list[_Span] = []
    for key in EXTRA_SPAN_LIST_KEYS:
        value = fields.get(key)
        items = value if isinstance(value, list) else [value]
        for item in items:
            if isinstance(item, Mapping):
                spans.append(_Span(item.get(RANGE_START_KEY) or item.get('date') or '',
                                   item.get(RANGE_END_KEY) or ''))
            elif isinstance(item, str):
                spans.extend(_Span(part, '') for part in
                             _EXTRA_DATE_STRING_SPLIT_RE.split(item) if part)
    if fields.get(EXTRA_SPAN_START_KEY) or fields.get(EXTRA_SPAN_END_KEY):
        spans.append(_Span(fields.get(EXTRA_SPAN_START_KEY) or '',
                           fields.get(EXTRA_SPAN_END_KEY) or ''))
    return spans


def _span_years(start: object, end: object) -> tuple[int, int] | None:
    """The first and last year a span covers, `_OPEN_END_YEAR` for a running
    one; None when its start has no year."""
    first = _parse_date_components(str(start or '').strip())[0]
    if first is None:
        return None
    end_text = str(end or '').strip()
    if end_text.lower() in _ONGOING_DATE_WORDS:
        return first, _OPEN_END_YEAR
    last = _parse_date_components(end_text)[0] if end_text else None
    return first, max(first, last or first)


def _span_is_covered(span: _Span, primary: tuple[int, int]) -> bool:
    """Whether every year of `span` lies inside the record's own range: a
    teaching row dated 1985-2005 whose extra dates are the years in between
    (EQADVR 48) gains nothing from listing them."""
    years = _span_years(span.start, span.end)
    return years is not None and primary[0] <= years[0] and years[1] <= primary[1]


def extra_date_spans(fields: Mapping[str, Any], taxonomy_code: str) -> list[str]:
    """The record's other spans (`EXTRA_SPAN_KEYS`), each formatted as its
    date column shows a range, less any inside the record's own
    `start_date`-`end_date` range or repeating an earlier one. Empty when
    the record has no own start year: there is no date cell to add to."""
    primary = _span_years(fields.get(RANGE_START_KEY), fields.get(RANGE_END_KEY))
    if primary is None:
        return []
    shown = [format_date_range(fields.get(RANGE_START_KEY) or '',
                               fields.get(RANGE_END_KEY) or '', taxonomy_code)]
    extras: list[str] = []
    for span in _extra_spans(fields):
        if _span_is_covered(span, primary):
            continue
        # A span with no end is that date alone: an extra date in a list
        # ("2010" beside a 1978-1979 course) is one occasion, not a range
        # still running, which `format_date_range` would make it.
        text = (format_date_range(str(span.start or ''), str(span.end), taxonomy_code)
                if span.end else format_date_for_section(str(span.start or ''), taxonomy_code))
        if text and text not in shown:
            shown.append(text)
            extras.append(text)
    return extras


def with_extra_date_spans(dates: str, fields: Mapping[str, Any],
                          taxonomy_code: str) -> str:
    """`dates` (the record's own range, as its renderer formatted it) followed
    by `extra_date_spans`, joined with `DATE_SPAN_SEPARATOR`: "2019-2020,
    2021". An empty `dates` stays empty -- its renderer has its own fallback
    for a row with no date, which an extra span alone must not pre-empt."""
    if not dates:
        return dates
    return DATE_SPAN_SEPARATOR.join([dates, *extra_date_spans(fields, taxonomy_code)])


_MONTH_NAMES = MappingProxyType({
    '01': 'January', '02': 'February', '03': 'March', '04': 'April',
    '05': 'May', '06': 'June', '07': 'July', '08': 'August',
    '09': 'September', '10': 'October', '11': 'November', '12': 'December',
    '1': 'January', '2': 'February', '3': 'March', '4': 'April',
    '5': 'May', '6': 'June', '7': 'July', '8': 'August',
    '9': 'September',
})

# A range whose two ends read the same: "2011-2011", "2011 to 2011",
# "March 2006 to March 2006" (EBYSBC autopsy, class E21). Stage 5c writes the
# first shape for a one-year course; the second comes out of the day being
# dropped below from "2006-03-21 to 2006-03-23". Not inside a longer token:
# "R01-2011-2011" and "1990-1990s" are left alone.
_SAME_VALUE_RANGE_RE = re.compile(
    rf'(?<![\w-])((?:(?:{"|".join(sorted(set(_MONTH_NAMES.values())))}) )?\d{{4}})'
    rf'\s*(?:[-\u2013\u2014]|\bto\b)\s*\1(?![\w-])')


def normalize_iso_dates_in_text(text: str) -> str:
    """Replace ISO-format dates in free text with human-readable equivalents,
    then collapse a range whose two ends read the same to one value.

    Handles patterns the Stage 5c LLM sometimes produces:
      2021-03-01  -> March 2021
      2019-08-01–2019-09-01  -> August 2019–September 2019
      2018-08  -> August 2018
      2012-06–2012-07  -> June 2012–July 2012
      2006-03-21 to 2006-03-23  -> March 2006
      2011-2011  -> 2011
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

    return _SAME_VALUE_RANGE_RE.sub(r'\1', text)
