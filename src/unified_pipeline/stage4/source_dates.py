"""Stage-4 dates held to what the entry's own source states (#1342).

Two passes over the extracted entries, each for a date the stage-4 LLM
stored that claims more than the line does:

- `date_completion_column_rows`. A table whose header names a completion
  column ("Trainee | Field | Complete Date") gives each row the year
  the work finished. The model stores that lone year as `start_date`, and a
  current mentee then renders "<year>-present": a completion year shown as
  an open-ended period (batch KFUTTD, FHQBHY 594.15). The header is another
  entry, so only a pass over the entries can read it. The year moves to
  `end_date`.
- `close_unsupported_open_ends`. An `end_date` of "present" on an entry
  whose text and headings carry no open marker, while the text itself
  closes the range ("09/2019 to 06/2024") or gives the start year alone in
  its last cell (EBYSBC CMTQDR 541). The end becomes the one the text
  writes, or is cleared, and stage 6 then renders what the source states.
  "The text leaves a year open" is stage 6's own reading
  (`core.source_open_year`), asked of every year the text writes.

Pure and offline: no LLM, no I/O. Imports nothing from the other stage-4
modules (the multi-record key comes in as an argument), and never mutates
its input.
"""

import re
from typing import Any

from unified_pipeline.core.source_open_year import source_leaves_year_open
from unified_pipeline.core.two_digit_year import expand_two_digit_year

#: The stored ends that say a range is still running: `coercion.
#: _OPEN_END_VALUES`, pinned equal by `test_open_end_values_match_coercion`
#: (this module and `coercion` import nothing from each other).
OPEN_END_VALUES = frozenset({'present', 'current', 'ongoing', 'now', 'to date', 'date'})

#: Codes whose open end is kept whatever the text writes: I, by the owner
#: decision of 2026-10-30 on #1342 (a membership with only a start year
#: renders "<year>-Present").
OPEN_END_KEPT_CODES = frozenset({'I'})

_START_FIELD = 'start_date'
_END_FIELD = 'end_date'

#: Any dash a CV types, the box-drawing one included.
_DASH = r'[-‐-―−─]'
_YEAR = r'(?:19|20)\d{2}'
_STORED_YEAR_RE = re.compile(rf'({_YEAR})(?:-\d{{1,2}}){{0,2}}')

#: A word, anywhere in the entry's text or headings, that can support an open
#: end. Read loosely on purpose: a false marker leaves the stored end alone.
_OPEN_WORD_RE = re.compile(
    r'\b(?:present(?:ly)?|current(?:ly)?|now|ongoing|active|since|continu\w*'
    r'|to\s+date|today|annual(?:ly)?|yearly|(?:each|every)\s+year)\b|' + _DASH + r'\s*date\b', re.IGNORECASE)
#: Every year a text writes in full, and the two digits of one it may write
#: short after a slash ("6/04-", "6/01/04-"): the years `_leaves_a_year_open`
#: asks the shared reader about.
_WRITTEN_YEAR_RE = re.compile(r'(?<!\d)\d{4}(?!\d)')
_SLASHED_SHORT_YEAR_RE = re.compile(r'/(\d{2})(?!\d)')
_DASH_RE = re.compile(_DASH)
_PLAIN_DASH = '-'

_LAST_MONTH = 12
_CLOSED_END_REASON = "Replaced an open end the source does not state with the end it writes"
_CLEARED_END_REASON = "Cleared an open end the source does not state"
_COMPLETION_REASON = "Moved a completion-column year from start_date to end_date"


def _stored_year(value: object) -> str | None:
    """The year of a stored date ("2019", "2019-09", "2019-09-15"), or None."""
    match = _STORED_YEAR_RE.fullmatch(str(value or '').strip())
    return match[1] if match else None


def _leaves_a_year_open(text: str) -> bool:
    """Whether `text` writes any of its years as an open range, by the
    reading stage 6 renders "-Present" from (`source_leaves_year_open`:
    "2019-", "6/04-", "2019.04.15 -", "2019 - <name>" opening the entry).
    Asked of every year, not the stored start alone: after "2011-2013" a
    later "chair 6/15-" still leaves the entry open. Any dash a CV types is
    read as the plain one the reader knows."""
    text = _DASH_RE.sub(_PLAIN_DASH, text)
    years = {int(year) for year in _WRITTEN_YEAR_RE.findall(text)}
    years.update(expand_two_digit_year(int(yy)) for yy in _SLASHED_SHORT_YEAR_RE.findall(text))
    return any(source_leaves_year_open(text, year) for year in years)


def _written_end(text: str, year: str) -> str | None:
    """The end of the one closed range `text` starts in `year`, as stage 4
    stores a date: "2024", or "2024-06" when the text gives the end a month
    ("09/2019 to 06/2024"). None when the text writes no such range, more
    than one, or a year later than that end ("2019-2021, 2024": the range is
    not the last thing the line dates; a range written backwards has its
    own start as that later year)."""
    ranges = re.findall(
        rf'(?<!\d){year}\s*(?:{_DASH}|\bto\b|\bthrough\b)\s*'
        rf'(?:(\d{{1,2}})/)?({_YEAR})(?!\d)', text, re.IGNORECASE)
    if len(ranges) != 1:
        return None
    month, end_year = ranges[0]
    if any(int(later) > int(end_year) for later in _WRITTEN_YEAR_RE.findall(text)):
        return None
    if month and 1 <= int(month) <= _LAST_MONTH:
        return f'{end_year}-{int(month):02d}'
    return end_year


def _year_alone_in_last_cell(text: str, year: str) -> bool:
    """Whether `text` gives `year` as its last cell's whole value
    ("<course><tab>2011"): a date column holding the year and nothing else.
    A year inside prose ("... from 2011") is not one: the words before it
    may open the range in any language."""
    return re.search(rf'[\t|\n][ ]*\(?{year}\)?[ .]*\Z', text) is not None


def _closed_end(entry: dict[str, Any]) -> tuple[str | None, str] | None:
    """What an unsupported open end of `entry` becomes and why, or None to
    keep it: the entry's code keeps open ends, the end is not an open value,
    the start has no year, or the text or a heading carries an open marker."""
    fields = entry.get('extracted_fields')
    if (not isinstance(fields, dict)
            or str(entry.get('taxonomy_code') or '') in OPEN_END_KEPT_CODES
            or str(fields.get(_END_FIELD) or '').strip().lower() not in OPEN_END_VALUES):
        return None
    year = _stored_year(fields.get(_START_FIELD))
    text = str(entry.get('text') or '')
    headings = [str(heading) for heading in entry.get('hierarchy') or []]
    headings.append(str(entry.get('context_heading') or ''))
    if (year is None or _leaves_a_year_open(text)
            or _OPEN_WORD_RE.search('\n'.join([text, *headings]))):
        return None
    written = _written_end(text, year)
    if written is not None:
        return written, _CLOSED_END_REASON
    if _year_alone_in_last_cell(text, year):
        return None, _CLEARED_END_REASON
    return None


def _with_fields(entry: dict[str, Any], values: dict[str, str | None],
                 reason: str) -> dict[str, Any]:
    """A copy of `entry` with `values` in its fields, each reported in
    `reformatted_fields` as stage 4's other repairs report theirs."""
    fields = entry['extracted_fields']
    changed = dict(entry)
    changed['extracted_fields'] = {**fields, **values}
    changed['reformatted_fields'] = {
        **(entry.get('reformatted_fields') or {}),
        **{name: {'original': fields.get(name), 'reformatted': value or '', 'reason': reason}
           for name, value in values.items()}}
    return changed


def close_unsupported_open_ends(entries: list[dict[str, Any]],
                                records_key: str) -> list[dict[str, Any]]:
    """`entries` with each one-record entry's unsupported open `end_date`
    replaced by the end its text writes, or cleared (see the module
    docstring and `_closed_end`). An entry that keeps several records under
    `records_key` is left alone: its text dates more than one record.
    Changed entries are copies."""
    out: list[dict[str, Any]] = []
    for entry in entries:
        fields = entry.get('extracted_fields')
        closed = None if isinstance(fields, dict) and fields.get(records_key) \
            else _closed_end(entry)
        out.append(entry if closed is None
                   else _with_fields(entry, {_END_FIELD: closed[0]}, closed[1]))
    return out


#: The code stage 3b gives a table's header row, and the row it sits in.
_HEADER_CODE = 'T'
_HEADER_ROW = 0
#: Stage 2's cell separator in a table row's text.
_CELL_SEPARATOR = '|'
#: A header cell naming the date the work finished: "Complete Date",
#: "Completion Year", "Year Graduated".
_COMPLETION_LABEL_RE = re.compile(
    r'\b(?:complet(?:e|ed|ion)|graduat(?:ed|ion))\b.*\b(?:date|year)\b'
    r'|\b(?:date|year)\b.*\b(?:complet(?:ed|ion)|graduat(?:ed|ion))\b',
    re.IGNORECASE | re.DOTALL)
#: A header cell naming any other date: with one, the model had a start
#: column to read and the table is left alone.
_OTHER_DATE_LABEL_RE = re.compile(
    r'\b(?:start(?:ed)?|began|from|period|dates?|years?|term|duration)\b', re.IGNORECASE)
#: A completion cell's whole value: a year, a numeric month before it allowed.
_COLUMN_YEAR_RE = re.compile(rf'(?:\d{{1,2}}/)?({_YEAR})')


def _cells(entry: dict[str, Any]) -> list[str]:
    return [cell.strip() for cell in str(entry.get('text') or '').split(_CELL_SEPARATOR)]


def _completion_columns(entries: list[dict[str, Any]]) -> dict[Any, tuple[int, int]]:
    """{table_index: (completion column, column count)} for each table whose
    header row names exactly one completion column and no other date."""
    columns: dict[Any, tuple[int, int]] = {}
    for entry in entries:
        table = entry.get('table_index')
        if (table is None or entry.get('row_index') != _HEADER_ROW
                or entry.get('taxonomy_code') != _HEADER_CODE):
            continue
        labels = _cells(entry)
        completion = [place for place, label in enumerate(labels)
                      if _COMPLETION_LABEL_RE.search(label)]
        others = [label for place, label in enumerate(labels)
                  if place not in completion and _OTHER_DATE_LABEL_RE.search(label)]
        if len(completion) == 1 and not others:
            columns[table] = (completion[0], len(labels))
    return columns


def date_completion_column_rows(entries: list[dict[str, Any]],
                                records_key: str) -> list[dict[str, Any]]:
    """`entries` with each completion-column row's lone year moved from
    `start_date` to `end_date` (see the module docstring).

    A row qualifies when its table's header names one completion column and
    no other date (`_completion_columns`), it has as many cells as the
    header, its cell in that column is a year and nothing else, and its
    fields hold that year as `start_date` with no `end_date`. A row keeping
    several records under `records_key` is left alone. Changed entries are
    copies."""
    columns = _completion_columns(entries)
    out: list[dict[str, Any]] = []
    for entry in entries:
        fields = entry.get('extracted_fields')
        column = columns.get(entry.get('table_index'))
        cells = _cells(entry)
        if (column is None or not isinstance(fields, dict) or fields.get(records_key)
                or fields.get(_END_FIELD) or len(cells) != column[1]):
            out.append(entry)
            continue
        written = _COLUMN_YEAR_RE.fullmatch(cells[column[0]])
        start = fields.get(_START_FIELD)
        if written is None or _stored_year(start) != written[1]:
            out.append(entry)
            continue
        out.append(_with_fields(entry, {_START_FIELD: None, _END_FIELD: str(start)},
                                _COMPLETION_REASON))
    return out
