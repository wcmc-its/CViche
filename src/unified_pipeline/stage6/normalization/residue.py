"""What is left of a source line once the other cells of its row are shown.

A row renders a few stage-4 fields in their own cells -- a role, dates -- and
falls back to the entry's source line for the one cell stage 4 left empty. That
line still holds what the other cells show, so the row printed it twice
(#946, #1437). Two ways to take the role and the dates stage 4 extracted back
out of the line:

- `_organization_left_in_text` (Q1, and a Q2 split record's own line) removes
  them wherever they stand and tidies what the removals strand.
- `_line_without_shown_role_and_dates` (P, and any other Q2 row, #1346) removes
  them only from the line's two ends. Those rows fall back to whole entries,
  some of them sentences ("Member. Collaborated with ..."), and a removal from
  the middle of a sentence or a parenthesis broke it; a role or a date at an
  edge is the shape of a record line ("Webmaster July 2015 - October 2017").

It lives here, not in one section, because more than one section falls back
this way: Q1's Organization, a Q2 row's Name of Committee (both
`sections/service.py`) and a P row's Activity/Committee
(`sections/administrative_activities.py`, #1346), and sections never import
one another (CODING_STANDARDS.md §1.3).
"""
import re

from ..parsing.dates import DATE_DASH, OPEN_END, SHORT_END_YEAR, YEAR, YEAR_OR_OPEN_SPAN
from .fields import _cell_text

# A year, or a year range ("2005-present", "1993-1996", "2001-"), used to find
# the date text stage 4 already moved into `start_date`/`end_date`. On the
# shared stage-6 date grammar (parsing/dates.py, #665).
_YEAR_RE = re.compile(YEAR)
_DATE_SPAN = re.compile(rf'(?<!\d){YEAR_OR_OPEN_SPAN}(?!\d)', re.IGNORECASE)
_TEXT_CELL_SEPARATORS = re.compile(r'[\t\n|]+')
_EDGE_PUNCTUATION = ' ,;:.-–—|"“”'

# Words that cannot name an organization on their own. Taking the years out of
# "September 1999 – September 2001" leaves the months behind, and the row
# rendered "September September" as its Organization (KHXOUF 1264, #1437).
_MONTH_WORDS = frozenset({
    'january', 'february', 'march', 'april', 'may', 'june', 'july', 'august',
    'september', 'october', 'november', 'december',
    'jan', 'feb', 'mar', 'apr', 'jun', 'jul', 'aug', 'sep', 'sept', 'oct',
    'nov', 'dec', 'present', 'current', 'ongoing'})
# A connective left at the edge of the residue once the role around it is gone:
# "Chair of the Fictional Society" less "Chair" is "of the Fictional Society"
# (#1437). A leading "the" alone is kept -- it is part of "The Fictional Society".
_CONNECTIVE_WORDS = frozenset({
    'of', 'the', 'at', 'from', 'by', 'for', 'in', 'to', 'and', 'with', 'on'})
_LEADING_CONNECTIVE_RE = re.compile(
    r'^(?:(?:of|at|from|by|for|in|to|and|with|on)\s+)+(?:the\s+)?', re.IGNORECASE)
_TRAILING_CONNECTIVE_RE = re.compile(
    r'(?:\s+(?:of|the|at|from|by|for|in|to|and|with|on))+$|\s*&$', re.IGNORECASE)
# A clause this short is a list glyph or a stub the removals left, not a name:
# Word's Courier sub-bullet "o" (or the "◦" the PDF converter now reads it as)
# rendered as six Q1 Organizations on one CV (SXPHOG-02, #1584). Kept at this
# length: a two-capital acronym ("UN"), and anything with a digit ("3M", or a
# year written short, "2019, 21, 23", which the Dates cell may not show).
_RESIDUE_MAX_CHARS = 2
_SHORT_RESIDUE_KEPT_RE = re.compile(r'[A-Z]{2}|.*\d.*')


def _organization_left_in_text(text: object, role: object, start_date: object,
                               end_date: object) -> str:
    """The organization of a Q1 row whose stage-4 fields name none: the entry
    text with what the other two columns already show taken out.

    `_fill_extramural_leadership` used to put the whole raw entry line in the
    Organization column when no organization field was set, so the role and the
    dates were printed twice and the source's tab separators came through
    (#946 shape: "2022-present Fictional Gazette<TAB>Section Editor").
    What is left after removing the role text and the dates stage 4 extracted
    is the organization ("Fictional Gazette"). Nothing else is removed: a year
    that is not in `start_date`/`end_date` (the Dates cell would not show it)
    and every word outside the role stay, so the row never loses text the
    other columns do not carry. When the role is the whole line the result is
    '' -- the Role cell already holds it. What the removals strand is tidied
    by `_organization_segment` (#1437): a connective left at a piece's edge,
    and a piece of nothing but month names.

    A `role` that is not a plain string (stage 4 files a list of per-record
    dicts there for a fused entry, and `_cell_text` then renders them with
    their own organization names) is no text to subtract, so the entry text is
    returned as it stands.
    """
    text = _cell_text(text)
    if not isinstance(role, str):
        return text
    known_years = set(_YEAR_RE.findall(f'{_cell_text(start_date)} {_cell_text(end_date)}'))

    def _drop_known_dates(match: re.Match[str]) -> str:
        return '' if set(_YEAR_RE.findall(match.group())) <= known_years else match.group()

    text = _DATE_SPAN.sub(_drop_known_dates, text)
    for part in re.split(r'\s*;\s*', _cell_text(role)):
        words = re.findall(r'\w+', part)
        if words:
            joined = r'[\W_]*'.join(map(re.escape, words))
            text = re.sub(rf'\b{joined}\b', '', text, flags=re.IGNORECASE)
    segments = (_organization_segment(segment) for segment in _TEXT_CELL_SEPARATORS.split(text))
    return ', '.join(segment for segment in segments if segment)


def _organization_segment(segment: str) -> str:
    """One separator-delimited piece of `_organization_left_in_text`'s residue,
    tidied clause by clause (#1437): each comma clause loses the edge
    punctuation and edge connectives the removals exposed, and a clause made
    only of months and connectives -- none of which can name an organization --
    is dropped, as is one of one or two characters with no digit that is not
    a two-capital acronym (#1584: a bullet glyph). A year stays: it may be one the Dates cell does
    not show."""
    clauses = (_organization_clause(clause) for clause in segment.split(','))
    return ', '.join(clause for clause in clauses if clause)


def _organization_clause(clause: str) -> str:
    """One comma clause of an organization residue, tidied; '' when nothing in
    it could name an organization."""
    clause = re.sub(r'\s{2,}', ' ', clause.strip(_EDGE_PUNCTUATION))
    clause = _LEADING_CONNECTIVE_RE.sub('', clause)
    clause = _TRAILING_CONNECTIVE_RE.sub('', clause).strip(_EDGE_PUNCTUATION)
    words = {word.lower() for word in re.findall(r'\w+', clause)}
    if words <= _MONTH_WORDS | _CONNECTIVE_WORDS:
        return ''
    if len(clause) <= _RESIDUE_MAX_CHARS and not _SHORT_RESIDUE_KEPT_RE.fullmatch(clause):
        return ''
    return clause


# One date at a line's edge: a year, optionally after a month name and day
# ("March 21, 2015") or a numeric month ("09/2009", "12/15/2025").
_MONTH_NAME = r'(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?'
_DATE_POINT = (rf'(?:{_MONTH_NAME}\s+(?:\d{{1,2}}(?:st|nd|rd|th)?,?\s+)?'
               rf'|\d{{1,2}}/(?:\d{{1,2}}/)?)?{YEAR}(?!\d)')
# A date or a range of them: "2005-present", "July 2015 - October 2017",
# "2001 to 2014", "2019- now", "1991-92", "2019-".
_DATE_RANGE = (rf'{_DATE_POINT}(?:\s*(?:{DATE_DASH}|\bto\b)\s*'
               rf'(?:{_DATE_POINT}|{SHORT_END_YEAR}|{OPEN_END}|now)?)?')
# Each alternative is balanced, so the ")" closing a parenthesis that holds more
# than the date ("(Biology, Advisor: ..., 2013 – 2015)") is never taken.
_EDGE_DATE = rf'(?:\(\s*{_DATE_RANGE}\s*\)|{_DATE_RANGE})'
_LEADING_DATE_RE = re.compile(rf'^{_EDGE_DATE}', re.IGNORECASE)
# A trailing date stands apart from what precedes it: "IL 2/09-2012" ends in a
# range whose start the date grammar cannot read, not in the date "2012".
_TRAILING_DATE_RE = re.compile(rf'(?:^|(?<=[\s,;:(|])){_EDGE_DATE}$', re.IGNORECASE)
# A date a word like "in" or "since" leads into ends a sentence ("... was
# reaccredited by the Council (CEPH) in 2021.", AQCLHS 88), not a record's
# date column; taking it cut the sentence short (#1346).
_DATE_LEAD_IN_RE = re.compile(
    rf'\b(?:{"|".join(sorted(_CONNECTIVE_WORDS | {"since", "until"}))})\W*$', re.IGNORECASE)
# What a removal can leave at an edge: separators, a list bullet, a stop. Not a
# quote or a bracket -- those close something the line opened.
_LINE_EDGE_CHARS = ' \t\n,;:.|-–—•·▪◦*'
# A role at the start of the line stands as its own segment ("Member. ...",
# "Program Director, Ashby University"); in "Member of the Board" it does not,
# nor before a parenthesis, which would be left as the cell on its own
# ("<role> (Organic Chemistry, ...)", REOYVH 218).
_ROLE_FOLLOWER = r'(?=\s*(?:[,;:.|\-–—]|$))'


def _role_pattern(role: str) -> str:
    """`role` as a regex that tolerates the punctuation and spacing between its
    words the source line may use ("co-chair" for "Co Chair")."""
    return r'[\W_]*'.join(map(re.escape, re.findall(r'\w+', role)))


def _strip_edge_date(line: str, known_years: set[str]) -> str:
    """`line` without a date at its start or end whose years are all ones
    the Dates cell shows (`known_years`)."""
    for pattern in (_LEADING_DATE_RE, _TRAILING_DATE_RE):
        match = pattern.search(line)
        if (match and set(_YEAR_RE.findall(match.group())) <= known_years
                and not _DATE_LEAD_IN_RE.search(line[:match.start()])):
            line = line[:match.start()] + line[match.end():]
    return line


def _strip_edge_role(line: str, roles: list[str]) -> str:
    """`line` without a role phrase at its start (set off by a separator) or
    at its end ("Zqsociety Advisor", "Executive Committee Member")."""
    for role in roles:
        line = re.sub(rf'^{role}{_ROLE_FOLLOWER}', '', line, flags=re.IGNORECASE)
        line = re.sub(rf'(?:^|(?<=[\s,;:|\-–—])){role}$', '', line, flags=re.IGNORECASE)
    return line


def _line_without_shown_role_and_dates(text: object, role: object, start_date: object,
                                       end_date: object) -> str:
    """The Name of Committee / Activity of a P or Q2 row whose stage-4 fields
    name none: the entry text, less the role and the dates its other cells
    show, where they stand at either end of the line (#1346).

    KJJVVO 240 "<society> Advisor — 2018–Present" printed whole beside its
    Advisor and 2018-Present cells; it renders "<society>". A line that is
    nothing but its role and dates ("Webmaster July 2015 - October 2017",
    YYVITM 308) renders ''. A date is removed only when the Dates cell shows
    every year in it (`start_date`/`end_date`), and a role only as a whole
    phrase. A `role` that is not a plain string is no text to remove. Text
    with nothing at its edges to remove is returned exactly as given, and so
    is text that still holds a year once its edges are gone: the line then
    carries more than one record's dates ("Council Member, 1981, 1990-1994",
    a fused "<committee> 1985-94<TAB><committee> 1986-2003"), and taking the
    one the Dates cell shows would leave the others reading as undated.
    """
    text = _cell_text(text)
    known_years = set(_YEAR_RE.findall(f'{_cell_text(start_date)} {_cell_text(end_date)}'))
    roles = ([pattern for part in re.split(r'\s*;\s*', role)
              if (pattern := _role_pattern(part))] if isinstance(role, str) else [])
    line = text.strip(_LINE_EDGE_CHARS)
    previous = None
    while line != previous:
        previous = line
        line = _strip_edge_date(line, known_years).strip(_LINE_EDGE_CHARS)
        line = _strip_edge_role(line, roles).strip(_LINE_EDGE_CHARS)
    if line == text.strip(_LINE_EDGE_CHARS) or _YEAR_RE.search(line):
        return text
    return line
