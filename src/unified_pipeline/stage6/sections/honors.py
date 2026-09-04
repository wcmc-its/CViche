"""Section H: honors and awards (#398).

The template table is three columns -- award name, organization, year -- and
almost none of the work here is filling it. Source CVs write honors as one free
line ("2020 AECT Distinguished Service Award, Purdue University"), so the
section has to take that line apart before it can be rendered, and that parsing
is what this module mostly is.

The module is split the way the data flows: everything above `HonorsSection`
is a pure text-to-`HonorRecord` parser that never sees a document, and the
class is the renderer, which only locates the table and writes the records
into it (#476 review). Nothing in the parser reads `self`, so every rule below
is testable on strings alone.

    _entry_parts                    the entry's parts: newlines always, '|'
                                    for the newline-blind single-line case
    _parse_honor_lines              one pass over those parts, sorting them
                                    into award cells and loose year cells
    _split_award_year               peels a leading or trailing year off a
                                    line, including ranges and the dangling
                                    month "..., August 2025." leaves behind
    _extract_organization_from_award finds the granting body by institutional
                                    keyword, in several passes
    parse_honor_entry               the whole entry -> `list[HonorRecord]`
    HonorsSection._add_honors_row   the only part that touches the table

The delimiter contract, stated once here because the reviewer asked for it in
writing and because every parse below depends on it:

    newline   always a record boundary. One line, one award.
    '|'       a STRUCTURAL column or record boundary, never a character
              inside an award's name. Column order is the template's own:
              `award | organization | date`, in the two-column form
              ("Award | 2020", "Award | Organization") or the three-column
              one ("Award | Organization | 2020"). Any other pipe shape --
              a leading date, four or more cells, an odd count that pairs no
              year to an award -- is left as one unsplit line rather than
              guessed at, and renders as its own text.
    tab       a column boundary inside ONE award ("Award\\tDate\\tNote"),
              except when the last cell is a bare year, which is that
              award's date. A tab-bearing line is never additionally split
              on '|'; see `_entry_parts` for the three farm entries that
              rule exists for.

The consequence the reviewer named explicitly: "Excellence in Research |
Teaching Award | 2024" is read as one award, the organization "Teaching
Award" and the date 2024 -- not as one award whose name contains a pipe, and
not as two awards. Nothing in the text distinguishes those readings, and the
pipeline's own table-cell join emits exactly this three-column shape. It is a
fallback either way: whatever stage 4 extracted for the entry wins over it.

`_US_STATE_ABBREVS` and `_MONTH_TAIL_RE` are the two vocabularies those parsers
filter against, and they exist because of specific mis-parses (#229): a bare
state abbreviation out of "Bethesda, MD" is not an organization, and a trailing
month is not part of an award's name. They are module constants now that the
parsers are module functions; `WCMTemplateGenerator` carries its own identical
copies, which is what `self._US_STATE_ABBREVS` resolved to before this split.

Three of these -- `_fill_honors`, `_extract_organization_from_award` and
`_split_award_year` -- are on the pinned class surface in
`tests/test_stage6_import_surface.py`; the last two stay on the class as thin
delegates to the module functions, which is what that guard checks.
"""
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ..formatting import _clear_table_data, _set_font, format_date_for_section
from ..normalization import _strip_org_tail
from ..parsing import _is_table_header_entry
from ..sorting import sort_entries_reverse_chronological
from unified_pipeline.core.render_check import entry_lines

logger = logging.getLogger(__name__)


# A '|' part that is nothing but a year or a year range is a date column, not
# an award. `_parse_honor_lines` classifies parts with exactly this shape, and
# `_entry_parts` has to agree with it, so the pattern is compiled once here and
# shared rather than written out twice (docs/CODING_STANDARDS.md §8.2).
_YEAR_ONLY_RE = re.compile(
    r'^(\d{4}(?:\s*-\s*\d{4})?|\d{4}(?:\s*-\s*present)?)$', re.IGNORECASE)

# Below this many award-looking parts the entry is not a multi-award list.
_MIN_AWARDS_FOR_SPLIT = 2

# Passed to `_is_table_header_entry` for a whole entry: the source CV's own
# table header row, extracted as if it were data.
_ENTRY_HEADER_KEYWORDS = ['award', 'honor', 'organization', 'date', 'year',
                          'granting']

# The same thing one line at a time, for a header row fused into a
# multi-line entry. Two of these in one line is a header, not an award.
_LINE_HEADER_KEYWORDS = frozenset({
    'name of award', 'date awarded', 'organization', 'granting body',
    'honor', 'year'})
_MIN_LINE_HEADER_KEYWORDS = 2

# A raw entry with no extracted award name renders as its own text; cap it so
# a runaway blob cannot fill the cell.
_MAX_RAW_AWARD_CHARS = 150

# The WCM template's H table: award | organization | date awarded (yyyy).
_HONORS_TABLE_COLUMNS = 3

# How many rows were written into a table that does not have those columns.
_FALLBACK_SCHEMA_STAT = 'honors_fallback_table_schema'

# "MD" (from "Bethesda, MD") and "Bloomington" are comma segments the
# short-proper-noun org fallback happily returns (#229) — never treat a
# bare state abbreviation as an organization.
_US_STATE_ABBREVS = frozenset({
    'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA', 'HI',
    'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD', 'MA', 'MI',
    'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ', 'NM', 'NY', 'NC',
    'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC', 'SD', 'TN', 'TX', 'UT',
    'VT', 'VA', 'WA', 'WV', 'WI', 'WY', 'DC'})

_MONTH_NAME = (r'(?:January|February|March|April|May|June|July|August|'
               r'September|October|November|December)')

_MONTH_TAIL_RE = re.compile(r'[\s,]*' + _MONTH_NAME + r'$', re.IGNORECASE)

# What a '|' cell has to look like to be the template's "Date awarded"
# column. Wider than `_YEAR_ONLY_RE`, which answers a different question (is
# this whole PART a bare year, i.e. its own record separator?): a date cell
# that stage 2 lifted out of a source table can carry the month or a slash
# form the source wrote, and "Award | Organization | August 2025" is the same
# three-column line as "Award | Organization | 2025".
_DATE_COLUMN_RE = re.compile(
    r'^(?:' + _MONTH_NAME + r'\s+)?(?:\d{1,2}/)?(?:19|20)\d{2}'
    r'(?:\s*[-–]\s*(?:(?:19|20)\d{2}|present))?$', re.IGNORECASE)

# Words that are part of award descriptions, not organization names.
_ORG_STOP_WORDS = frozenset([
    'award', 'excellence', 'teaching', 'list', 'recognition',
    'member', 'elected', 'senior', 'certificate', 'mentoring',
    'director', 'subinternship', 'housestaff', 'faculty',
    'resident', 'scholarship', 'honor', 'clinical', 'student'])

# Words that survive the backwards walk without being the organization's own
# name -- "University *of* the State *of* New York".
_ORG_CONNECTIVES = frozenset(['of', 'the', 'and', 'at', 'in', 'for'])

# Keywords that name an institution wherever they appear. A CV writes these
# in an organization's name, essentially never inside an award's.
_STRONG_ORG_KEYWORDS = (r'(?:University|College|Hospital|Medical\s+Center|'
                        r'Society|Association|Institute|Academy|Foundation|'
                        r'Program\s+Directors)')

# ...and the generic one, which does occur inside award names ("Cancer Center
# Excellence Award"). It is still an organization signal, but only where
# nothing stronger appears: matching on the LAST keyword of a single flat
# list let a generic word inside an award name beat the real granting body
# (#733 review). `Medical Center` stays in the strong list and, being the
# longer alternative, still wins over a bare `Center` at the same position.
_GENERIC_ORG_KEYWORDS = r'(?:Center)'

_ORG_KEYWORDS = (r'(?:' + _STRONG_ORG_KEYWORDS + r'|'
                 + _GENERIC_ORG_KEYWORDS + r')')

# A built organization has to be more than one word, and Strategy 4 additionally
# refuses one that is most of the line it was cut from -- that is an award name
# with a keyword in it, not an organization.
_MIN_ORG_WORDS = 2
_MAX_ORG_SHARE_OF_TEXT = 0.7

# A comma segment longer than this is a sentence, not an organization name.
_MAX_ORG_SEGMENT_WORDS = 10

# "Society of Fictional Medicine Mentoring Award" -- a granting body named
# at the START of the award text. Two different questions are being asked
# here and they need two different case rules (#733 review): the
# institutional keyword and the award vocabulary are matched
# case-insensitively, because a CV writes them however it likes, while
# `[A-Z]` is the signal that what follows "of"/"for" is a PROPER NAME and
# must stay case-sensitive. Compiling the whole pattern with re.IGNORECASE
# made `[A-Z]` match a lowercase letter, so the branch accepted text with no
# proper name in it at all -- exactly what the check was there to reject.
_NAMED_BODY_RE = re.compile(
    r'((?i:(?:Medical\s+)?' + _ORG_KEYWORDS
    + r'\s+(?:of|for)\s+(?:the\s+)?(?:State\s+of\s+)?)'
    r'[A-Z][\w\s.-]+?)'
    r'(?i:\s+(?:Mentoring|Award|Certificate|Medical\s+Student|Grant))')


@dataclass(frozen=True, slots=True)
class HonorRecord:
    """One honors row: exactly what the parser produces and all the renderer
    consumes.

    The upstream boundary is a stage-5 `dict` whose award/organization/date
    live under any of several key names and may be absent, `None` or a
    non-string; every `.get()` chain that used to answer those questions ran
    inside `_fill_honors`, so malformed data reached the table cells silently
    (#476 review). Everything past `parse_honor_entry` is one of these
    instead: three strings, already normalized, already date-formatted.
    """

    award: str
    organization: str = ''
    date: str = ''


def _entry_parts(text: str, column_values: Sequence[str] = ()) -> list[str]:
    """Non-empty parts of an honors entry (#476), scoped to exactly the
    newline-blind case entry_lines already names as its own blind spot: a
    text with no literal newline at all, where entry_lines returns the whole
    thing as a single opaque part.

    A genuinely multi-line entry (entry_lines already returns >1 part) is
    returned UNCHANGED -- its existing per-part tab/pipe handling below stays
    exactly as today. Reading the farm's changed uids proved this matters:
    2054_Opresko_Cv's "1994 | American Chemical Society Award,\\nLehigh Valley
    Chapter of ACS" already has 2 lines by newline alone, and additionally
    splitting line 0's '|' collides with the existing (separate, pre-existing)
    "DATE | AWARD" mislabeling in `_parse_honor_lines`, adding a spurious
    duplicate row that isn't this issue's to fix.

    For the single-line, tab-free case, '|' is added as a boundary -- the
    corpus's other unambiguous "Award Name | Year" separator (module
    docstring, existing `'|' in line` branch) -- but a line carrying a tab is
    still returned whole, untouched by '|' too. 293 of the farm's
    newline-blind H entries carry a tab, and reading them shows why: 038WKA's
    "Award Name\\tDate\\tDescription" is one award with three tab-separated
    fields, correctly rejoined today by the per-part loop's "does the last
    tab part look like a year" check; 2082_Dr_Scot's is a mid-sentence
    line-wrap artifact ("...NISOD) Award for\\tTeaching Excellence.
    Valencia..." ) that pre-splitting tears in half, verified to produce three
    garbled rows instead of the one correct one `award_name` already
    carries. And 2068_Yount_Cv's single blind entry has BOTH a tab and a '|'
    ("2022\\tWorld's Best ... Ranking | Research.com"): splitting on '|' first
    hands the tab-rejoin branch a part with no leading year to isolate,
    which is what makes it emit a spurious "Research.com" row instead of
    correctly falling back to the clean extracted fields. Excluding any
    tab-bearing line from the '|' split avoids all three at once by leaving
    every one of them exactly as `entry_lines` already had it.

    Splitting on '|' unconditionally was wrong in one direction the farm
    cannot show, and this is the guard for it: the pipeline's own table-cell
    join writes ONE award as "Award | Organization | Year", and splitting that
    re-emitted the organization as a second, empty award row (a row `dev` does
    not produce). `column_values` are the column cells stage 4 already
    extracted for this entry -- its granting body and its date -- and a part
    they already name is a column, not an award. What survives that filter,
    plus the bare-year parts, decides:

    - fewer than `_MIN_AWARDS_FOR_SPLIT` award-looking parts: not a list of
      awards, return `lines` unchanged (`dev`'s exact behaviour);
    - bare years present but not one per award: the pipes are column
      separators, not record separators -- "Award | Organization | Year" has
      two award-looking parts and one year, so it fails here even when stage 4
      extracted nothing to filter with. Return `lines` unchanged.
    - otherwise (each award carries its own year, or none of them do): the
      pipes separate records, so the split stands.

    Note what is returned on the split path: `parts`, the WHOLE split, never
    the filtered `awards` list -- the filters decide, they never drop text.

    Residual, disclosed rather than guessed at: a two-column "Award |
    Organization" join for which stage 4 extracted no granting body has two
    award-looking parts and no year, and still splits here -- with no rendered
    effect: `_fill_honors` emits zero rows for that text on this branch and on
    origin/dev alike. Nothing in the farm has that shape and inventing a third
    rule for it would be untested.
    """
    lines = entry_lines(text)
    if len(lines) != 1 or '\t' in lines[0]:
        return lines
    parts = [p.strip() for p in lines[0].split('|') if p.strip()]
    claimed = {v.strip().lower() for v in column_values if v and v.strip()}
    years = [p for p in parts if _YEAR_ONLY_RE.match(p)]
    awards = [p for p in parts
              if not _YEAR_ONLY_RE.match(p) and p.lower() not in claimed]
    if len(awards) < _MIN_AWARDS_FOR_SPLIT:
        return lines
    if years and len(years) != len(awards):
        return lines
    return parts


def _field_text(fields: Mapping, *names: str) -> str:
    """First non-empty value among `names`, coerced to a string.

    The stage-5 record is not a contract anything enforces: a key can be
    absent, `None`, or a number. Coercing here is what keeps a malformed
    field from reaching a table cell as `None` (#476 review).
    """
    for name in names:
        value = fields.get(name)
        if value:
            return value if isinstance(value, str) else str(value)
    return ''


def _is_date_column(part: str) -> bool:
    """Is this '|' cell the template's date column?"""
    return bool(_YEAR_ONLY_RE.match(part) or _DATE_COLUMN_RE.match(part))


def _honor_columns(line: str) -> HonorRecord | None:
    """The columns a '|'-joined line names, or None if it names none.

    The section's contract is `award | organization | date`, and both the
    two- and the three-column form of it occur. Reading the second cell as
    the date unconditionally -- what this branch did before (#733 review) --
    turns the three-column form's ORGANIZATION into the date and loses the
    real one, so the two forms are told apart here by which cells look like a
    date:

        "Award | 2020"                -> award, no org, date        (2 cells)
        "Award | Organization"        -> award, org, no date        (2 cells)
        "Award | Organization | 2020" -> award, org, date           (3 cells)

    Anything else -- a leading date ("2020 | Award", a real farm shape whose
    mislabeling `_entry_parts` documents as a separate, pre-existing bug), a
    date in the middle, four or more cells -- returns None, and the caller
    falls back to the historical first-cell/second-cell reading rather than
    guessing at a shape nothing has established.

    LIMITATION, since the reviewer asked for it in writing: '|' is a
    STRUCTURAL delimiter here, never a character inside an award's name.
    "Excellence in Research | Teaching Award | 2024" therefore parses as
    award "Excellence in Research", organization "Teaching Award", date
    2024 -- not as one award whose name contains a pipe, and not as two
    awards. Nothing in the text can distinguish those readings, and the
    pipeline's own table-cell join emits exactly this three-column shape, so
    the column reading is the one that matches how the text is produced. It
    is also only a FALLBACK: when stage 4 extracted an award name,
    granting body or date for the entry, those win over anything parsed out
    of the raw text (`_record_for_single_award`).
    """
    parts = [p.strip() for p in line.split('|') if p.strip()]
    if (len(parts) == 3 and not _is_date_column(parts[0])
            and not _is_date_column(parts[1]) and _is_date_column(parts[2])):
        return HonorRecord(parts[0], parts[1], parts[2])
    if len(parts) == 2 and not _is_date_column(parts[0]):
        if _is_date_column(parts[1]):
            return HonorRecord(parts[0], '', parts[1])
        return HonorRecord(parts[0], parts[1], '')
    return None


def _looks_like_column_header(line: str) -> bool:
    """A source CV's own table header row, fused into an entry as a line."""
    line_lower = line.lower().replace('\t', ' ')
    hits = sum(1 for kw in _LINE_HEADER_KEYWORDS if kw in line_lower)
    return hits >= _MIN_LINE_HEADER_KEYWORDS


def _parse_honor_lines(lines: Sequence[str]) -> tuple[list[HonorRecord], list[str]]:
    """Sort an entry's parts into (award cells, loose year cells).

    One pass, in encounter order, over what `_entry_parts` returned. A part is
    either a header row (dropped), a bare year (a loose year cell), a
    tab-joined "Award\\tYear" pair, a '|'-joined column tuple, or an award
    line. The two lists are returned separately because a CV can write the
    years on their own lines ("2020\\nAward A\\n2018\\nAward B"), where the
    only thing tying a year to an award is its position -- see
    `_order_years`.
    """
    awards: list[HonorRecord] = []
    years: list[str] = []
    for line in lines:
        if _looks_like_column_header(line):
            continue

        if '\t' in line:
            tab_parts = [p.strip() for p in line.split('\t') if p.strip()]
            if len(tab_parts) >= 2 and _YEAR_ONLY_RE.match(tab_parts[-1]):
                # Last tab-field is a year, everything before is the award
                awards.append(HonorRecord('\t'.join(tab_parts[:-1])))
                years.append(tab_parts[-1])
                continue
            if len(tab_parts) == 1:
                # Tab-prefixed year or award
                line = tab_parts[0]
            else:
                # One award written across tab-separated fields
                line = ' '.join(tab_parts)

        if _YEAR_ONLY_RE.match(line):
            years.append(line)
        elif '|' in line:
            columns = _honor_columns(line)
            if columns is None:
                # Not one of the documented column forms — the historical
                # first-cell/second-cell reading, unchanged.
                parts = line.split('|')
                awards.append(HonorRecord(parts[0].strip()))
                if len(parts) > 1 and parts[1].strip():
                    years.append(parts[1].strip())
            else:
                awards.append(columns)
                if columns.date:
                    # Also a loose year, so an entry that mixes column lines
                    # with bare-year lines keeps its positional alignment.
                    years.append(columns.date)
        else:
            awards.append(HonorRecord(line))
    return awards, years


def _leading_year(text: str) -> int:
    """The four-digit year a cell starts with, or 0."""
    m = re.match(r'(\d{4})', text)
    return int(m.group(1)) if m else 0


def _order_years(years: Sequence[str]) -> list[str]:
    """Loose year cells in the order the award cells are written in.

    Award lists are typically reverse-chronological, so descending years map
    onto the awards as written; an ascending list is reversed to align.
    """
    if len(years) < 2:  # nothing to compare first against last
        return list(years)
    if _leading_year(years[0]) >= _leading_year(years[-1]):
        return list(years)
    return list(reversed(years))


def _records_for_award_list(awards: Sequence[HonorRecord],
                            years: Sequence[str],
                            award_name: str,
                            granting_body: str,
                            date: str) -> list[HonorRecord]:
    """Records for an entry that fused several awards into one text."""
    ordered_years = _order_years(years)
    records: list[HonorRecord] = []
    for i, parsed in enumerate(awards):
        # Stage 4 extracted clean fields for (at most) one award of the fused
        # entry — use them for the line they belong to instead of re-parsing
        # it from raw text (#229).
        if award_name and award_name.lower() in parsed.award.lower():
            records.append(HonorRecord(
                award_name,
                granting_body or _extract_organization_from_award(parsed.award),
                format_date_for_section(date, 'H') if date else ''))
            continue

        # The line's own date column if it named one, else the corresponding
        # year from the ordered list
        award_text = parsed.award
        year_for_award = parsed.date or (
            ordered_years[i] if i < len(ordered_years) else '')

        # If no year found from text, extract the inline year
        # (leading "2020 Award ...", range, or trailing "... 2025.")
        if not year_for_award:
            award_text, year_for_award = _split_award_year(award_text)

        if year_for_award:
            year_for_award = format_date_for_section(year_for_award, 'H')

        org = parsed.organization or _extract_organization_from_award(award_text)

        # The org is usually a trailing segment of the raw line —
        # keep it out of the name cell (#229)
        records.append(HonorRecord(_strip_org_tail(award_text, org), org,
                                   year_for_award))
    return records


def _record_for_single_award(original_text: str,
                             columns: HonorRecord | None,
                             award_name: str,
                             granting_body: str,
                             date: str) -> HonorRecord:
    """The record for an entry that holds one award, from stage 4's fields.

    `columns` is the column tuple `_honor_columns` recognised when the whole
    entry was one '|'-joined line, else None. Stage 4's extracted fields win
    over it wherever it has them; it only fills what they left empty, which
    is what keeps "Award | Organization | Year" out of the award cell as a
    raw string (#733 review).
    """
    if not award_name:
        award_name = (columns.award if columns is not None
                      else original_text[:_MAX_RAW_AWARD_CHARS])
    if columns is not None:
        granting_body = granting_body or columns.organization
        date = date or columns.date

    # If no extracted date, parse the inline year out of the name
    # (leading "2021 Award ...", range, or trailing "... 2021.");
    # fall back to the original text for the year alone.
    if not date:
        award_name, date = _split_award_year(award_name)
        if not date:
            _, date = _split_award_year(original_text)

    if date:
        date = format_date_for_section(date, 'H')

    # Extract organization if field extraction didn't provide one
    if not granting_body:
        granting_body = _extract_organization_from_award(award_name)

    # Same duplication hazard as the multi-award path (#229)
    return HonorRecord(_strip_org_tail(award_name, granting_body),
                       granting_body, date)


def _award_with_organization(record: HonorRecord) -> str:
    """Award and granting body in one cell, for a template variant whose
    honors table has no organization column of its own."""
    if not record.organization:
        return record.award
    if not record.award:
        return record.organization
    return f"{record.award}, {record.organization}"


def parse_honor_entry(entry: Mapping) -> list[HonorRecord]:
    """Every honors row one stage-5 entry should render as.

    Pure: no document, no `self`, no stats. `_fill_honors` calls this and then
    only populates cells, which is the whole point of the split (#476 review).
    An entry that fused several awards into one text yields one record per
    award; anything else yields exactly one record built from stage 4's
    extracted fields, falling back to parsing the raw text.
    """
    fields = entry.get('extracted_fields') or {}
    original_text = str(entry.get('text') or '')

    award_name = _field_text(fields, 'award_name')
    granting_body = _field_text(fields, 'granting_body', 'organization')
    date = _field_text(fields, 'date', 'year')

    # Check if this entry contains multiple awards (newline-separated).
    # This happens when multiple honors were merged during extraction.
    # #476: '\n' and '|' boundaries; the extracted column cells are passed so
    # a single award's own "Award | Organization | Year" cell join is not
    # mistaken for two awards -- see _entry_parts.
    awards, years = _parse_honor_lines(
        _entry_parts(original_text, (granting_body, date)))

    if len(awards) > 1:
        return _records_for_award_list(awards, years, award_name,
                                       granting_body, date)
    # A single award line that named its own columns is the only thing that
    # beats the raw text as a fallback -- a plain line, a tab-welded line and
    # an unrecognised '|' shape all carry no columns and leave `columns` None.
    columns = (awards[0] if len(awards) == 1
               and (awards[0].organization or awards[0].date) else None)
    return [_record_for_single_award(original_text, columns, award_name,
                                     granting_body, date)]


# A year or a year range, in either of the two dash characters CVs use.
# Shared by the leading and the trailing half of `_split_award_year` so the
# two cannot drift apart again (#733 review): the trailing half used to
# accept a bare year only, so "2015-2017 Award" parsed and "Award 2015-2017"
# did not, despite the function's contract naming ranges.
_YEAR_OR_RANGE = (r'(?:19|20)\d{2}(?:\s*[-–]\s*(?:(?:19|20)\d{2}|present))?')

_LEADING_YEAR_RE = re.compile(r'^\s*(' + _YEAR_OR_RANGE + r')\b[\s,.:–-]*',
                              re.IGNORECASE)
_TRAILING_YEAR_RE = re.compile(r'(?:^|[\s,(])(' + _YEAR_OR_RANGE
                               + r')\s*[).]?\s*$', re.IGNORECASE)


def _split_award_year(text: str) -> tuple[str, str]:
    """Split an award line into (name-without-year, year-or-range).

    Handles the shapes the honors fallback parser actually sees (#229):
    leading years/ranges ("2020 AECT ...", "2015-2017 Featured ...") and
    trailing years and ranges with punctuation ("..., August 2025." /
    "... (2021)" / "... 2015–2017"). Leading and trailing accept the same
    year grammar, hyphen or en-dash. Returns the original text and '' when
    no year is found.
    """
    m = _LEADING_YEAR_RE.match(text)
    if m:
        return text[m.end():].strip(' ,.;'), m.group(1)
    m = _TRAILING_YEAR_RE.search(text)
    if m:
        cleaned = text[:m.start()].rstrip(' ,.(;')
        # "..., August 2025." leaves a dangling month — drop it too
        cleaned = _MONTH_TAIL_RE.sub('', cleaned).rstrip(' ,.;')
        return cleaned, m.group(1)
    return text, ''


def _continues_org_name(between: str) -> bool:
    """Is the text between two keywords still part of one organization name?

    The same test the backwards walk below applies word by word: proper
    nouns and connectives continue a name, award vocabulary ends it.
    """
    for word in between.split():
        wc = word.strip('.,;–—-()\"’')
        if not wc:
            continue
        if wc.lower() in _ORG_STOP_WORDS:
            return False
        if wc[0].islower() and wc.lower() not in _ORG_CONNECTIVES:
            return False
    return True


def _org_keyword_anchor(txt: str) -> re.Match | None:
    """The institutional keyword an organization name should be built around.

    The last STRONG keyword, not the last keyword of one flat list: a
    generic word can sit inside an award's own name and used to win purely
    by coming later ("... Teaching Center Award" beating the university that
    granted it, #733 review). The generic keyword still decides when no
    strong one appears anywhere in the text.

    It also still decides when it only CONTINUES the strong keyword's name
    -- "New York Presbyterian Hospital Weill Cornell Center" is one
    organization, and anchoring on `Hospital` would cut it in half. That is
    the difference the stop-word test makes: award vocabulary between the
    two keywords means the generic one belongs to the award, not the org.
    """
    strong = list(re.finditer(_STRONG_ORG_KEYWORDS, txt, re.IGNORECASE))
    generic = list(re.finditer(_GENERIC_ORG_KEYWORDS, txt, re.IGNORECASE))
    if not strong:
        return generic[-1] if generic else None
    anchor = strong[-1]
    for m in generic:
        if m.start() >= anchor.end() \
                and _continues_org_name(txt[anchor.end():m.start()]):
            anchor = m
    return anchor


def _build_org_around_keyword(txt: str) -> str:
    """Build an organization name around the text's keyword anchor."""
    km = _org_keyword_anchor(txt)
    if km is None:
        return ''

    # Walk backwards from keyword
    before = txt[:km.start()]
    words = before.rstrip().split()
    pre = []
    for w in reversed(words):
        wc = w.strip('.,;–—-()\"’')
        if not wc:
            # Dash/punctuation-only token - preserve and keep walking
            pre.insert(0, w.strip())
            continue
        if wc.lower() in _ORG_STOP_WORDS:
            break
        if wc[0].islower() and wc.lower() not in _ORG_CONNECTIVES:
            break
        pre.insert(0, wc)

    # Walk forward: handle dash-connected institution names
    after = txt[km.end():]
    post = ''
    dm = re.match(r'(\s*[–—-]\s*(?:[A-Z][\w.]+\s+)*?' + _ORG_KEYWORDS
                  + r')', after)
    if dm:
        post = dm.group(1).strip('–—- ').strip()

    parts = pre + [km.group(0)]
    org = ' '.join(parts)
    if post:
        org += ' – ' + post
    return org.strip('.,; ')


def _extract_organization_from_award(text: str) -> str:
    """Extract organization name from award/honor text using institutional keyword patterns.

    Uses a multi-strategy approach:
    1. 'from [Organization]' explicit pattern
    2. Comma-separated segments with institutional keywords
    3. 'Association/Society of X' at start of text
    4. Proper noun phrases around institutional keywords anywhere in text

    Returns the organization name, or empty string if none identified.
    """
    if not text:
        return ''

    # Strategy 1: "from [Organization]"
    fm = re.search(r'\bfrom\b\s+(.+)$', text, re.IGNORECASE)
    if fm:
        org = _build_org_around_keyword(fm.group(1))
        if len(org.split()) >= _MIN_ORG_WORDS:
            return org

    # Strategy 2: comma-separated segments (check last segments first).
    # Three passes: a strong-keyword segment beats a generic-keyword one
    # (#733 review, same reason as `_build_org_around_keyword`), and either
    # beats the short-proper-noun fallback — a single reversed pass used to
    # return "MD" or a bare city before ever reaching the real org (#229).
    if ',' in text:
        segs = [s.strip().rstrip('.,;') for s in text.split(',')]
        for keywords in (_STRONG_ORG_KEYWORDS, _GENERIC_ORG_KEYWORDS):
            for seg in reversed(segs):
                if seg and re.search(keywords, seg, re.IGNORECASE) \
                        and len(seg.split()) <= _MAX_ORG_SEGMENT_WORDS:
                    return seg
        for seg in reversed(segs):
            if not seg or seg.upper() in _US_STATE_ABBREVS \
                    or any(ch.isdigit() for ch in seg):
                continue
            # Short proper-noun segment (e.g., "Weill Cornell")
            if re.match(r'^[A-Z][\w.-]+(?:\s+[A-Z][\w.-]+){0,2}$', seg):
                return seg

    # Strategy 3: "Association/Society of X" at start of text
    m = _NAMED_BODY_RE.match(text)
    if m:
        return m.group(1).strip().rstrip('.,;')

    # Strategy 4: institutional keyword anywhere - build org around last match
    org = _build_org_around_keyword(text)
    if len(org.split()) >= _MIN_ORG_WORDS \
            and len(org) < len(text) * _MAX_ORG_SHARE_OF_TEXT:
        return org

    return ''


class HonorsSection:
    """Section H writers, mixed into `WCMTemplateGenerator`."""

    def _note_honors_schema_fallback(self, num_cols: int):
        """Record that the honors table is not the template's three columns.

        Counted per row so the shortfall is measurable, warned once per
        document so a CV with thirty honors does not write thirty identical
        lines. No CV content is logged -- only the shape.
        """
        seen = self.stats.get(_FALLBACK_SCHEMA_STAT, 0)
        self.stats[_FALLBACK_SCHEMA_STAT] = seen + 1
        if not seen:
            logger.warning(
                "Honors table has %d column(s), not the template's %d; "
                "folding the granting body into the award cell for this "
                "document's honors rows.", num_cols, _HONORS_TABLE_COLUMNS)

    def _split_award_year(self, text: str) -> tuple[str, str]:
        """Pinned class surface for the module-level parser of the same name."""
        return _split_award_year(text)

    def _extract_organization_from_award(self, text: str) -> str:
        """Pinned class surface for the module-level parser of the same name."""
        return _extract_organization_from_award(text)

    def _fill_honors(self, entries: list[dict]):
        """Fill H. HONORS, AWARDS section.

        WCM template has table with columns: Name of award | Organization | Date awarded (yyyy)
        """
        if not entries:
            return

        if self.verbose:
            print(f"Filling Honors ({len(entries)} entries)...")

        # Find the HONORS section
        honors_idx = self._find_paragraph_with_text("HONORS")
        if honors_idx is None:
            honors_idx = self._find_paragraph_with_text("AWARDS")
        if honors_idx is None:
            return

        # Find the table after the section header
        table = self._find_table_after_paragraph(honors_idx)
        if not table:
            return

        _clear_table_data(table, keep_header=True)
        self.stats['tables_populated'] += 1

        # Sort by date (most recent first)
        for entry in sort_entries_reverse_chronological(entries):
            original_text = entry.get('text', '')

            # Skip table header entries that were mistakenly extracted as data
            # Common patterns: "Name of award\tOrganization\tDate awarded" or similar
            if _is_table_header_entry(original_text, _ENTRY_HEADER_KEYWORDS):
                if self.verbose:
                    print(f"  Skipping header entry: '{original_text[:50]}...'")
                continue

            for record in parse_honor_entry(entry):
                self._add_honors_row(table, record)

    def _add_honors_row(self, table, record: HonorRecord):
        """Add a single row to the honors table.

        The WCM template's H table is three columns -- award, organization,
        date. A template variant with fewer columns has nowhere to put the
        organization, and the narrower branches used to silently drop it,
        producing a valid-looking DOCX with the granting body missing (#733
        review). A renderer must not abort a document over a template
        variant, so the alternate schema is supported explicitly instead:
        the granting body is folded into the award cell, and the
        degradation is counted and logged rather than swallowed (§5.4).
        """
        num_cols = len(table.columns)
        if num_cols < 1:
            self._note_honors_schema_fallback(num_cols)
            return

        row = table.add_row()
        num_cols = len(row.cells)

        if num_cols >= _HONORS_TABLE_COLUMNS:
            row.cells[0].text = record.award
            row.cells[1].text = record.organization
            row.cells[2].text = record.date
        else:
            self._note_honors_schema_fallback(num_cols)
            label = _award_with_organization(record)
            if num_cols >= 2:
                row.cells[0].text = label
                row.cells[1].text = record.date
            else:
                row.cells[0].text = (f"{label} ({record.date})"
                                     if record.date else label)

        # Apply font formatting to each cell
        for cell in row.cells:
            for para in cell.paragraphs:
                for run in para.runs:
                    _set_font(run)

        self.stats['entries_inserted'] += 1
