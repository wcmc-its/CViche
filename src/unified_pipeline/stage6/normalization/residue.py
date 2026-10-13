"""What is left of a source line once the other cells of its row are shown.

A row renders a few stage-4 fields in their own cells -- a role, dates -- and
falls back to the entry's source line for the one cell stage 4 left empty. That
line still holds what the other cells show, so the row printed it twice
(#946, #1437). `_organization_left_in_text` takes the role and the dates stage 4
extracted back out of the line and tidies what the removals strand.

It lives here, not in one section, because more than one section falls back
this way: Q1's Organization, a Q2 row's Name of Committee (both
`sections/service.py`) and a P row's Activity/Committee
(`sections/administrative_activities.py`, #1346), and sections never import
one another (CODING_STANDARDS.md §1.3).
"""
import re

from ..parsing.dates import YEAR, YEAR_OR_OPEN_SPAN
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
