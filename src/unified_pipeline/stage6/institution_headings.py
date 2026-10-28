"""#985: an institution sub-heading fills the empty cells of the rows under it.

A CV often writes the institution or organization once, as a sub-heading over
the records it governs ("Example University", then its committees; a society
name, then the offices held in it). When stage 1b does not make that line a
hierarchy level, stage 2 emits it as an entry of its own, stage 3b codes it as
content (P, I, Q2, N3B, K2 ...) and stage 4 extracts only the name. Rendered as
it stands, the heading is a stray row holding only that name, sorted away from
its children, and every row under it renders with an empty Institution or
Organization cell.

`consume_institution_headings` runs once per render, on the routed entries in
document order, right after stage 6 decides each entry's code and before the
PII pass and dedup see them. For each HEADING (`heading_name`) it walks the
entries after it (`_run`). A row whose code's renderer prints the institution
from an empty field (`FILL_FIELD_BY_CODE`) gets the heading's name there, but
only when its renderer then prints that name and still prints every word it
printed before (`_fill_outcome`): several renderers print a row's own text in
that cell when the field is blank, and a fill would replace it. When at least
`MIN_FILLED_ROWS` rows print the name, filled or already, the heading is
dropped from its code's group: its text now renders in its children's cells,
so it renders no row of its own, is not sent to the Appendix, and no later pass
sees it. A society line coded I is kept as its own row unless a membership row
under it prints the name (`_keeps_its_row`): alone in its section, a bare
society name is the membership itself. A heading whose name fewer rows print is
left exactly as it was, and so are its rows.

Never overwrites a value: a row that names its own institution under any field
its renderer reads it from keeps it. Stage 6 is the one place this reaches
stored runs as well as fresh ones; the stage-4 stamp
(`stage4/context_headings.py`, #1047) covers only T-coded headings, and only on
a fresh run. Running after routing means the code a row is filled for is the
code whose renderer prints it, and a filled field cannot change a reroute
decision (`_fields_fit_reroute_target` reads the fields).

What is deliberately NOT filled: a K or L row. Their renderers print an
institution only on some paths (`teaching._teaching_entry_lines` drops it when
stage 5c formatted the row), and the open card layout (#2019) renders the
source's own words, so a filled value would not reliably print and the consumed
heading would take the institution with it. Nor an appointment (D1/D2/D3),
which positions already gives its employer line. Nor a row of any code whose
renderer prints no institution (H, R, M2*, S* ...): `FILL_FIELD_BY_CODE` lists
the codes that do.

No docx and no I/O: what a row prints is read off each renderer's own helpers,
imported from `sections/`. The weak heading-shape test lives in stage 4
(`context_headings._is_heading_shaped`), which nothing under `stage6/` may
import, so `stage_6_word_template.py` passes it in, as it passes
`FIELD_SCHEMAS` to `fan_out.py`.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Hashable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from unified_pipeline.core.render_check import entry_lines

from .fan_out import _is_date_key, fallback_text, has_built_text, is_split_record
from .normalization import (
    _cell_text,
    _line_without_shown_role_and_dates,
    _organization_left_in_text,
)
from .parsing import _is_mentee_record
from .sections.administrative_activities import (
    _CommitteeRecord,
    _first_committee_alias,
    _name_with_institution,
)
from .sections.leadership import _first_held, _with_division
from .sections.memberships import (
    _entry_parts,
    _is_source_header_row,
    _parse_multi_membership_entry,
    _single_membership_row,
)
from .sections.mentoring import _mentee_name_is_award_title, _normalize_mentee
from .sections.service import (
    _MEMBERSHIP_BODY_RE,
    EXTRAMURAL_ORGANIZATION_FIELDS,
    _board_committee_text,
    _is_board_role_title,
    _route_q2_entries,
    _route_q2_grant_reviews,
    _without_another_persons_role,
)
from .supplementary import (
    LEADERSHIP_INSTITUTION_FIELDS,
    LEADERSHIP_ROLE_FIELDS,
    MEMBERSHIP_ORGANIZATION_FIELDS,
    MEMBERSHIP_TYPE_FIELDS,
)

#: An entry and the code stage 6 routed it under, in document order.
CodedEntry = tuple[dict[str, Any], str]

#: The field each code's section renderer prints the institution or
#: organization from, for the codes this pass fills: leadership
#: (`LEADERSHIP_INSTITUTION_FIELDS`), administrative activities
#: (`_name_with_institution`, #1014), memberships, service, and the mentee table
#: (`mentoring._SITE_EXTRA_KEYS`, which joins `institution` to Site/Position).
#: Each is in `fan_out._RENDERED_FIELDS` except the mentee's, which is
#: off-schema and joined by `_with_site_extras`.
FILL_FIELD_BY_CODE: Mapping[str, str] = MappingProxyType({
    'O': 'institution', 'P': 'institution',
    'I': 'organization', 'Q1': 'organization', 'Q2': 'organization',
    'N3A': 'institution', 'N3B': 'institution',
})

#: Every field a row may name its own institution under: the ones a renderer
#: prints it from (memberships `MEMBERSHIP_ORGANIZATION_FIELDS`, which holds
#: leadership's `LEADERSHIP_INSTITUTION_FIELDS`; Q1 also
#: `EXTRAMURAL_ORGANIZATION_FIELDS`), read on every code, since a name a
#: renderer does not print still says the row is not the heading's. A row
#: holding any value under one of them, a list or a dict included, names its
#: own.
_OWN_NAME_FIELDS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    code: tuple(dict.fromkeys((*MEMBERSHIP_ORGANIZATION_FIELDS, *LEADERSHIP_INSTITUTION_FIELDS,
                               *(EXTRAMURAL_ORGANIZATION_FIELDS if code == 'Q1' else ()))))
    for code in FILL_FIELD_BY_CODE
})

#: A run of rows under one heading stays within one of these: an institution's
#: leadership roles and committees (O, P), a society's memberships and offices
#: (I, Q1, Q2), or a program's mentees (N3A, N3B). A row of another family is a
#: different block of the CV and ends the run.
_FAMILY_BY_CODE: Mapping[str, str] = MappingProxyType({
    **dict.fromkeys(('O', 'P'), 'institutional'),
    **dict.fromkeys(('I', 'Q1', 'Q2'), 'extramural'),
    **dict.fromkeys(('N3A', 'N3B'), 'mentoring'),
})

#: The codes a heading may carry: the ones whose renderer gives a bare name
#: line no record of its own, as the #985 census found them (2026-10-28) --
#: the filled codes and K2 (a teaching line over a mentee list, ULHCAP
#: 418/427). Not G: a hospital affiliation is a whole record when it is only a
#: name (FVQANK 378/399 rendered as Other Institutional Affiliations rows). Not
#: an appointment (D1/D2/D3): positions already gives an appointment with no
#: employer the employer line above it, and an employer-only row may be an
#: appointment whose title stage 2 split onto another line. Not T: stage 4's
#: stamp owns those (#1047).
_HEADING_CODES = frozenset({*FILL_FIELD_BY_CODE, 'K2'})

#: The code of a heading that is a record of its own when no membership row
#: under it names it (`_keeps_its_row`): a bare society line in a memberships
#: section is that membership (CHXRBM 129, EBZFDN 286, RBPXCD 155).
_MEMBERSHIP_CODE = 'I'

#: The fewest rows a heading's name must print on for it to be consumed or to
#: fill any. One is not a group: a name line over a single record (AQAJHD's
#: employer lines over one role each) is as likely that record's own header
#: row, and it keeps rendering as today.
MIN_FILLED_ROWS = 2

#: Longest heading text, colon excluded: stage 4's own bound for a heading line
#: (`context_headings.MAX_HEADING_CHARS`), restated because stage 6 may not
#: import it. The longest heading the #985 census consumes is 68 characters (a
#: society name with its acronym).
MAX_HEADING_CHARS = 70

#: The stage-4 fields that can hold a heading's name: the institution or
#: organization, and Site/Position, where stage 4 files a mentee list's
#: institution sub-heading (WZSMUO 166/184).
_NAME_FIELDS = ('institution', 'organization', 'site_position')

#: Fields that restate the whole entry rather than add to it (stage 5c's and
#: 5d's renderings, their provenance) and so do not make a heading a record.
_RESTATEMENT_FIELDS = frozenset({'formatted_text', 'formatted_citation', 'formatting_source'})

#: Stage 3b's code for an unclassifiable or heading-like line: it is never this
#: pass's heading (stage 4's stamp owns those, #1047) and it ends a run.
_HEADING_TAXONOMY_CODE = 'T'

#: The fields that hold a role or a person: a row holding one is a record
#: under the heading, never the next group's name line (`_is_bare_name_line`).
_ROLE_FIELDS = frozenset({*LEADERSHIP_ROLE_FIELDS, *MEMBERSHIP_TYPE_FIELDS, 'mentee_name', 'name'})

#: A line holding any of these is a record or a table row, not a bare name.
_NOT_A_NAME_RE = re.compile(r'[\d\t\n|]')
_WORD_RE = re.compile(r'[a-z0-9]+')
#: Connectives a stage-4 name may drop or add ("University of X" vs "X University").
_STOPWORDS = frozenset({'a', 'an', 'and', 'at', 'for', 'in', 'of', 'the'})

#: Words that name a KIND of institution, unit or body rather than a particular
#: one. A heading made only of these ("University", "Medical Center", "School of
#: Medicine", "Board of Directors") names no institution a reader could place:
#: it is a sub-group label under one the CV named earlier (CXRYCF 86/93 under
#: a level naming two such kinds, HVLDGS 2302 over a society's task forces),
#: so it is not a heading here. Census of #985 (2026-10-28).
_GENERIC_NAME_WORDS = frozenset({
    'university', 'college', 'school', 'medical', 'medicine', 'center', 'centre', 'hospital',
    'department', 'division', 'institute', 'faculty', 'program', 'programs', 'office', 'section',
    'chapter', 'board', 'directors', 'trustees', 'governors', 'committee', 'committees', 'council',
    'pharmacy', 'nursing', 'health', 'system', 'campus', 'other',
})

#: The render-warnings check this pass reports under.
CONSUMED_HEADING_CHECK = 'institution_heading_consumed'

#: The key on that record listing each consumed heading and the rows it
#: filled, by element_idx_start: `[[heading, [row, ...]], ...]`. The doctor
#: reads stage 4's JSON, which has neither the fill nor the removal, so it
#: reads them back here (`heading_fills`, `consumed_headings`).
CONSUMED_HEADINGS_KEY = 'consumed_headings'

#: The same for each heading that filled rows and still renders its own row
#: (`_keeps_its_row`): `[[heading, [row, ...]], ...]`.
KEPT_HEADINGS_KEY = 'kept_headings'

#: `_fill_outcome`'s two answers for a row the heading's name prints on.
FILLED = 'filled'
SHOWN = 'shown'


def _content_words(value: object) -> frozenset[str]:
    """The lowercased words of a string value, connectives dropped."""
    if not isinstance(value, str):
        return frozenset()
    return frozenset(w for w in _WORD_RE.findall(value.lower()) if w not in _STOPWORDS)


def _cell_words(cells: Sequence[object]) -> frozenset[str]:
    """Every lowercased word a row's cells print, connectives included."""
    return frozenset(_WORD_RE.findall(' '.join(_cell_text(cell) for cell in cells).lower()))


def _is_blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or value in ([], {})


def _held_fields(entry: Mapping[str, Any]) -> dict[str, Any]:
    """The entry's non-blank stage-4 fields, restatements left out."""
    fields = entry.get('extracted_fields')
    if not isinstance(fields, Mapping):
        return {}
    return {key: value for key, value in fields.items()
            if key not in _RESTATEMENT_FIELDS and not _is_blank(value)}


def _heading_text(entry: Mapping[str, Any]) -> str | None:
    """The entry's text as a bare name (trailing colon dropped), or None when
    it is too long, holds a digit, a tab, a line break or a pipe, or has
    unbalanced parentheses."""
    text = str(entry.get('text') or '').strip()
    name = text[:-1].strip() if text.endswith(':') else text
    if (not name or len(name) > MAX_HEADING_CHARS or _NOT_A_NAME_RE.search(name)
            or name.count('(') != name.count(')')):
        return None
    return name


def _is_content(entry: Mapping[str, Any], code: str) -> bool:
    """Whether the entry is routed as content: not T, a fragment or a duplicate."""
    return (code != _HEADING_TAXONOMY_CODE
            and not entry.get('is_fragment') and not entry.get('is_duplicate'))


def _may_head(entry: Mapping[str, Any], code: str) -> bool:
    """Whether the entry may be a heading: content, of a `_HEADING_CODES` code."""
    return _is_content(entry, code) and code in _HEADING_CODES


def heading_name(entry: Mapping[str, Any], code: str) -> str | None:
    """The name a HEADING entry gives the rows under it, else None.

    A heading is a content entry of a `_HEADING_CODES` code (`_may_head`)
    whose text is a bare name (`_heading_text`) of a particular institution
    (`_names_a_particular_one`) and whose stage-4 fields hold nothing but that
    name: every non-blank field is a `_NAME_FIELDS` one, and the text has no
    word the name fields lack. So a dated line, a role, a committee or a title
    is never one. The name is stage 4's own spelling of it when that field
    holds the text's words and no others (an enumerator or a heading's
    capitals left out), else the text (`_display_name`).
    """
    if not _may_head(entry, code):
        return None
    name = _heading_text(entry)
    held = _held_fields(entry)
    if name is None or not held.keys() <= set(_NAME_FIELDS) or not _names_a_particular_one(name):
        return None
    named = frozenset().union(*(_content_words(value) for value in held.values()))
    return _display_name(name, held) if _content_words(name) <= named else None


def _display_name(name: str, held: Mapping[str, Any]) -> str:
    """The value the rows are filled with: the first `_NAME_FIELDS` value
    whose words are exactly the heading text's (AOJEQD 71 without its "A."
    enumerator, SDEBQJ 346 without its capitals), else the text itself."""
    words = _content_words(name)
    for key in _NAME_FIELDS:
        value = held.get(key)
        if isinstance(value, str) and _content_words(value) == words:
            return value.strip()
    return name


def _names_a_particular_one(name: str) -> bool:
    """Whether `name` has a word outside `_GENERIC_NAME_WORDS`."""
    return bool(_content_words(name) - _GENERIC_NAME_WORDS)


def _unextracted_name(entry: Mapping[str, Any], code: str) -> str | None:
    """The text of a content entry stage 4 extracted no field from, when it is
    a bare name of a particular institution (NJIKGI 212: a lone university line
    coded Q2), else None. It is a heading only when a row of its run names it
    (`_corroborated`)."""
    if not _may_head(entry, code) or _held_fields(entry):
        return None
    name = _heading_text(entry)
    return name if name is not None and _names_a_particular_one(name) else None


def _words_match(word: str, other: str) -> bool:
    """Whether two words are the same, or one is the other's initial ("E" in
    "U of E" for "Exampleton")."""
    return word == other or (len(word) == 1 and other.startswith(word)) or (len(other) == 1 and word.startswith(other))


def _covered(words: frozenset[str], by: frozenset[str]) -> bool:
    """Whether every one of `words` matches one of `by` (`_words_match`)."""
    return all(any(_words_match(word, other) for other in by) for word in words)


def _same_institution(own: str, name: str) -> bool:
    """Whether a row's own institution is the heading's, or a unit of it, or
    the heading's abbreviation: one's words contain the other's, initials
    matching the words they stand for ("Exampleton" under "Exampleton",
    "Exampleton Cancer Center" under "Exampleton", "ESIM" under "Example
    Society of Imaginary Medicine (ESIM)", "U of E Programs" under "U of
    Exampleton")."""
    own_words, words = _content_words(own), _content_words(name)
    return bool(own_words) and (_covered(own_words, words) or _covered(words, own_words))


def _is_unit_of(own: str, name: str) -> bool:
    """Whether a row's own institution is the heading's or a unit of it: the
    same institution (`_same_institution`), or a name of only a kind of unit
    ("School of Nursing" under a university; WZSMUO 345/346), which names no
    other institution a reader could place. A placeholder with no word ("-")
    is neither."""
    return _same_institution(own, name) or (bool(_content_words(own)) and not _names_a_particular_one(own))


def _own_name(entry: Mapping[str, Any], code: str) -> str:
    """The institution the row names itself under any of its code's
    `_OWN_NAME_FIELDS`, as cell text; '' when none holds a value."""
    fields = entry['extracted_fields']  # a row reaching here holds stage-4 fields (`_ends_run`)
    for key in _OWN_NAME_FIELDS[code]:
        value = fields.get(key)
        if not _is_blank(value):
            return _cell_text(value) or str(value)
    return ''


def _in_scope(heading: Mapping[str, Any], entry: Mapping[str, Any], name: str) -> bool:
    """Whether `entry` sits under the heading's hierarchy leaf: the same path,
    or one below it whose every deeper level names no particular institution
    or names the heading's (WZSMUO 331, whose two sub-levels name its
    university by initials). A level named after another institution is that
    institution's block."""
    path = list(heading.get('hierarchy') or [])
    entry_path = list(entry.get('hierarchy') or [])
    return entry_path[:len(path)] == path and all(
        not _names_a_particular_one(str(level)) or _same_institution(str(level), name)
        for level in entry_path[len(path):])


def _same_element(heading: Mapping[str, Any], entry: Mapping[str, Any]) -> bool:
    """Whether `entry` starts in the heading's own source element: the
    "heading" is then a cell of that row, split off by stage 2 (HVLDGS 1878, a
    table row's first cell), not a line over the rows below."""
    start = heading.get('element_idx_start')
    return start is not None and entry.get('element_idx_start') == start


def _is_bare_name_line(entry: Mapping[str, Any]) -> bool:
    """Whether the entry is an undated line holding only the name of a
    particular institution, whatever field stage 4 filed it under: no role, no
    person and no date in its fields, no digit in its text (`_heading_text`),
    and no committee-like body named in it (`service._MEMBERSHIP_BODY_RE`,
    the test the boards writer gives a seat its default role by). It is the
    next group's heading, one `heading_name` does not read as one because
    stage 4 filed the name as a committee ("St. Example Hospital", with the
    period that also keeps it from being weakly heading-shaped), and the rows
    under it are that institution's (#1365 R1: a run stops at the next
    organization line). An undated committee row with a comma is a row of the
    run (FLYBMX 303)."""
    name = _heading_text(entry)
    return (name is not None and _names_a_particular_one(name) and not _MEMBERSHIP_BODY_RE.search(name)
            and not any(key in _ROLE_FIELDS or _is_date_key(key) for key in _held_fields(entry)))


@dataclass
class HeadingRun:
    """One heading and the rows its name prints on, by position in the coded
    list: rows it fills (`fills`, with the field), rows that print it already
    (`shown`), and rows naming it as their own (`own_names`)."""
    heading: int
    name: str
    fills: list[tuple[int, str]] = field(default_factory=list)
    shown: list[int] = field(default_factory=list)
    own_names: list[int] = field(default_factory=list)

    @property
    def printed(self) -> int:
        """The number of rows the heading's name prints on, filled or already."""
        return len(self.fills) + len(self.shown)


def _element_gap(prev: Mapping[str, Any], entry: Mapping[str, Any]) -> int | None:
    """The number of source elements strictly between `prev` and `entry` (0
    when they share one, as fanned-out records do), None when either has no
    integer element index."""
    end = prev.get('element_idx_end', prev.get('element_idx_start'))
    start = entry.get('element_idx_start')
    if not isinstance(end, int) or not isinstance(start, int):
        return None
    return max(start - end - 1, 0)


# ------------------------------------------------- what each renderer prints
#
# Each returns the cells the code's renderer writes from a row's institution
# field and the text it falls back to, given `fields`, or None when the row
# takes a renderer path that prints no entry-level institution (a list of
# records, a block it re-parses line by line, a row it reroutes or drops).
# `_fill_outcome` calls each twice, with and without the fill.

def _leadership_cells(entry: Mapping[str, Any], fields: Mapping[str, Any]) -> tuple[object, ...] | None:
    """O, as `LeadershipSection._fill_leadership` writes its one-row path:
    (role, institution). A row with no role prints its whole text as the role
    when its institution is blank, which a filled institution turns off. Not
    that path: 3+ lines, 2 lines with no role, or a raw pipe (read here as the
    re-parse it may be)."""
    text = str(entry.get('text') or '')
    role = _first_held(dict(fields), LEADERSHIP_ROLE_FIELDS)
    lines = entry_lines(text)
    if len(lines) >= 3 or (len(lines) > 1 and not role) or ('|' in text and not has_built_text(entry)):
        return None
    institution = _with_division(_first_held(dict(fields), LEADERSHIP_INSTITUTION_FIELDS),
                                 fields.get('division_department'), role)
    if not role and not institution:
        role = text
    return role, institution


def _administrative_cells(entry: Mapping[str, Any], fields: Mapping[str, Any]) -> tuple[object, ...] | None:
    """P, as `_parse_administrative_activity_rows` writes its one-row path:
    (name with institution, role). The renderer only appends the institution
    (`_name_with_institution`), to the committee name or, with none, to the
    line less its role and dates. Not that path: a list of records under the
    committee name (each prints its own institution), 3+ lines, 2 lines with
    no committee name, or a raw pipe."""
    text = str(entry.get('text') or '')
    if _first_committee_alias(fields, as_list=True):
        return None
    record = _CommitteeRecord.from_raw(fields)
    lines = entry_lines(text)
    if len(lines) >= 3 or (len(lines) > 1 and not record.activity) or ('|' in text and not has_built_text(entry)):
        return None
    activity = record.activity or _line_without_shown_role_and_dates(
        fallback_text(entry), record.role, record.start_date, record.end_date)
    return _name_with_institution(activity, record.institution), record.role


def _extramural_leadership_cells(entry: Mapping[str, Any], fields: Mapping[str, Any]) -> tuple[object, ...] | None:
    """Q1, as `_fill_extramural_leadership` writes it: (organization, role).
    With a role and no organization the Organization cell prints the line less
    its role and dates (`_organization_left_in_text`); with neither, the whole
    text (past 3 lines, a re-parse of it, which prints its words as well)."""
    entry = _without_another_persons_role({**entry, 'extracted_fields': dict(fields)})
    fields = entry['extracted_fields']
    organization = next((fields[key] for key in EXTRAMURAL_ORGANIZATION_FIELDS if fields.get(key)), '')
    role = fields.get('role', '')
    if organization or role:
        if not organization:
            organization = _organization_left_in_text(
                fallback_text(entry), role, _cell_text(fields.get('start_date', '')),
                _cell_text(fields.get('end_date', '')))
        return organization, role
    return (str(entry.get('text') or ''),)


def _board_cells(entry: Mapping[str, Any], fields: Mapping[str, Any]) -> tuple[object, ...] | None:
    """Q2, as `_fill_service_boards` writes it: (committee, role,
    organization). With no committee name the organization moves into the
    committee cell, and with neither the committee cell prints the line less
    its role and dates. Not that path: a row `_fill_service` reroutes to
    journal or grant reviewing, whole or a line of it; the rest of a row of
    several lines is read as the boards writer gets it, its board lines."""
    entry = _without_another_persons_role({**entry, 'extracted_fields': dict(fields)})
    journal, boards = _route_q2_entries([entry])
    grants, boards = _route_q2_grant_reviews(boards)
    if journal or grants:
        return None
    entry = boards[0]
    fields = entry['extracted_fields']
    committee = _board_committee_text(fields)
    role = _cell_text(fields.get('role'))
    organization = _cell_text(fields.get('organization'))
    if not committee and ', ' in role:
        role_word, rest = role.split(', ', 1)
        if _is_board_role_title(role_word.strip().lower()):
            role, committee = role_word.strip(), rest.strip()
    if not committee:
        committee, organization = organization, ''
    if not committee and is_split_record(entry):
        committee = _organization_left_in_text(fallback_text(entry), fields.get('role'),
                                               fields.get('start_date'), fields.get('end_date'))
    elif not committee:
        committee = _line_without_shown_role_and_dates(entry.get('text', ''), fields.get('role'),
                                                       fields.get('start_date'), fields.get('end_date'))
    return committee, role, organization


def _membership_cells(entry: Mapping[str, Any], fields: Mapping[str, Any]) -> tuple[object, ...] | None:
    """I, as `MembershipsSection._fill_memberships` writes one membership:
    (organization cell, dates cell), the organization recovered from the
    row's own text when its field is blank (`_single_membership_row`). Not
    that path: a source header row it drops, or text it splits into several
    memberships."""
    text = str(entry.get('text') or '')
    if _is_source_header_row(dict(fields), text):
        return None
    parts = _entry_parts(text)
    memberships = _parse_multi_membership_entry(parts) if len(parts) > 2 and not is_split_record(entry) else []
    if len(memberships) > 1 or (memberships and len(entry_lines(text)) > 2):
        return None
    return _single_membership_row(dict(fields), text)[:2]


def _mentee_cells(entry: Mapping[str, Any], fields: Mapping[str, Any]) -> tuple[object, ...] | None:
    """N3A/N3B, as the mentee table writes Site/Position, which joins the
    institution when it does not already hold it (`_normalize_mentee`). Not a
    mentee table: a line naming no mentee (a summary) or a refused award
    title."""
    entry = {**entry, 'extracted_fields': dict(fields)}
    if not _is_mentee_record(entry) or _mentee_name_is_award_title(entry):
        return None
    return (_normalize_mentee(entry, ongoing=False).site_position,)


_CELLS_BY_CODE: Mapping[str, Callable[[Mapping[str, Any], Mapping[str, Any]], tuple[object, ...] | None]] = (
    MappingProxyType({'O': _leadership_cells, 'P': _administrative_cells, 'Q1': _extramural_leadership_cells,
                      'Q2': _board_cells, 'I': _membership_cells,
                      'N3A': _mentee_cells, 'N3B': _mentee_cells}))


def _fill_outcome(entry: Mapping[str, Any], code: str, name: str) -> str | None:
    """`FILLED` when filling the row's `FILL_FIELD_BY_CODE` field with `name`
    makes its renderer print the name and keeps every word it printed
    without it; `SHOWN` when the row prints the name already; None otherwise
    (no fill). A renderer that prints the row's own text in that cell while
    the field is blank (O with no role, Q1 and Q2 with no organization or
    committee, I) loses it to a fill unless its words are the name's."""
    cells_of = _CELLS_BY_CODE[code]
    fields = entry['extracted_fields']
    before = cells_of(entry, fields)
    if before is None:
        return None
    if _content_words(name) <= _cell_words(before):
        return SHOWN
    after = cells_of(entry, {**fields, FILL_FIELD_BY_CODE[code]: name})
    if after is None or not _cell_words(before) <= _cell_words(after):
        return None
    return FILLED if _content_words(name) <= _cell_words(after) else None


def _ends_run(heading: Mapping[str, Any], entry: Mapping[str, Any], code: str, name: str,
              family: str | None, is_heading_shaped: Callable[[str], bool]) -> bool:
    """Whether `entry` is outside the heading's run whatever it names (`_run`)."""
    return (not _in_scope(heading, entry, name) or _same_element(heading, entry)
            or not _is_content(entry, code) or not _held_fields(entry)
            or heading_name(entry, code) is not None or is_heading_shaped(str(entry.get('text') or ''))
            or _is_bare_name_line(entry)
            or code not in FILL_FIELD_BY_CODE or family not in (None, _FAMILY_BY_CODE[code]))


def _run(coded: Sequence[CodedEntry], start: int, name: str,
         is_heading_shaped: Callable[[str], bool]) -> HeadingRun:
    """The rows the heading at `start` governs and the field to fill on each.

    The run ends at the first entry that leaves the heading's hierarchy leaf
    (`_in_scope`), starts in the heading's own source element
    (`_same_element`), is another heading (a bare name line, extracted or not,
    or `_is_bare_name_line`), is heading-shaped in stage 4's weak sense (a bare
    Title-Case label, a colon or ALL CAPS line: a sibling or role sub-heading),
    is T, a fragment or a duplicate, has no stage-4 field at all, or is of a
    code this pass does not fill or of another family than the run's first row
    (`_ends_run`).

    It also ends at a row further from the row before it, in source elements,
    than the run's rows have been so far (`spacing`, set by the first step that
    stays on one hierarchy path). Stage 4 drops the lines it does not extract,
    a fragment or a duplicate, so that gap is all stage 6 sees of a sibling
    heading stage 3b flagged: VQFSDI 126 (a fragment between adjacent rows),
    CXRYCF 107 and CHXRBM 134 (duplicates). A step onto another sub-level is
    not measured: its gap holds that level's own heading line (WZSMUO 332/339).

    A row that names its own institution keeps it; the run goes on past it
    when that is the heading's institution or a unit of it (`_is_unit_of`)
    and ends there otherwise, since a row naming another institution may be
    the next group's unrecognised heading (MYNQRA 46, a 103-character college
    line) or a list with no heading at all (DUTAVD 62-74, one society per
    row). A row the fill would not print on, or would cost words of its own
    (`_fill_outcome`), is left as it is and the run goes on.
    """
    heading = coded[start][0]
    run = HeadingRun(start, name)
    family = None
    spacing = None
    prev = heading
    for idx in range(start + 1, len(coded)):
        entry, code = coded[idx]
        if _ends_run(heading, entry, code, name, family, is_heading_shaped):
            break
        gap = _element_gap(prev, entry)
        if gap is not None and prev.get('hierarchy') == entry.get('hierarchy'):
            if spacing is None:
                spacing = gap
            elif gap > spacing:
                break
        prev = entry
        family = _FAMILY_BY_CODE[code]
        own = _own_name(entry, code)
        if own:
            if not _is_unit_of(own, name):
                break
            if _same_institution(own, name):
                run.own_names.append(idx)
            continue
        outcome = _fill_outcome(entry, code, name)
        if outcome == FILLED:
            run.fills.append((idx, FILL_FIELD_BY_CODE[code]))
        elif outcome == SHOWN:
            run.shown.append(idx)
    return run


def _corroborated(run: HeadingRun) -> bool:
    """Whether a row of the run names the heading's institution as its own
    (`_same_institution`), which is what makes an unextracted name line a
    heading."""
    return bool(run.own_names)


def _keeps_its_row(run: HeadingRun, coded: Sequence[CodedEntry]) -> bool:
    """Whether the heading renders its own row although its name prints on
    its rows: a society line coded I, when no membership row under it prints
    the name or names the society. Its offices and committees go to other
    sections, so it is the only row of Memberships that names the society:
    the membership itself (CHXRBM 129, EBZFDN 286, RBPXCD 155)."""
    rows = [idx for idx, _ in run.fills] + run.shown + run.own_names
    return (coded[run.heading][1] == _MEMBERSHIP_CODE
            and not any(coded[idx][1] == _MEMBERSHIP_CODE for idx in rows))


def heading_runs(coded: Sequence[CodedEntry],
                 is_heading_shaped: Callable[[str], bool]) -> list[HeadingRun]:
    """Every heading whose name prints on at least `MIN_FILLED_ROWS` rows
    (`HeadingRun.printed`), in document order."""
    runs = []
    for idx, (entry, code) in enumerate(coded):
        name = heading_name(entry, code)
        unextracted = name is None
        if unextracted:
            name = _unextracted_name(entry, code)
        if name is None:
            continue
        run = _run(coded, idx, name, is_heading_shaped)
        if run.printed >= MIN_FILLED_ROWS and (not unextracted or _corroborated(run)):
            runs.append(run)
    return runs


def _pairs(runs: Sequence[HeadingRun], coded: Sequence[CodedEntry]) -> list[list[object]]:
    """`[[heading element_idx_start, [filled row element_idx_start, ...]], ...]`."""
    return [[coded[run.heading][0].get('element_idx_start'),
             [coded[idx][0].get('element_idx_start') for idx, _ in run.fills]]
            for run in runs]


def consumed_heading_warnings(runs: Sequence[HeadingRun],
                              coded: Sequence[CodedEntry]) -> list[dict[str, Any]]:
    """One INFO render-warnings record naming every heading that filled rows,
    consumed or kept as its own row (`_keeps_its_row`), and the rows each
    filled, by element_idx_start only (never CV text); none when no heading
    did."""
    if not runs:
        return []
    kept = [run for run in runs if _keeps_its_row(run, coded)]
    consumed = [run for run in runs if not _keeps_its_row(run, coded)]
    parts = []
    if consumed:
        parts.append(f"{len(consumed)} heading{'' if len(consumed) == 1 else 's'}, "
                     f"{sum(len(run.fills) for run in consumed)} rows filled")
    if kept:
        parts.append(f"{len(kept)} society heading{'' if len(kept) == 1 else 's'} kept as "
                     f"{'its' if len(kept) == 1 else 'their'} own row, "
                     f"{sum(len(run.fills) for run in kept)} rows filled")
    record = {
        'check': CONSUMED_HEADING_CHECK,
        'code': None,
        'section': None,
        'message': "institution sub-heading moved into the rows under it: " + '; '.join(parts),
        'evidence': [f"element_idx_start {coded[run.heading][0].get('element_idx_start')} "
                     f"{'kept, ' if _keeps_its_row(run, coded) else ''}filled "
                     + (', '.join(str(coded[idx][0].get('element_idx_start')) for idx, _ in run.fills) or 'none')
                     for run in runs],
        'severity': 'INFO',
        CONSUMED_HEADINGS_KEY: _pairs(consumed, coded),
    }
    if kept:
        record[KEPT_HEADINGS_KEY] = _pairs(kept, coded)
    return [record]


def _recorded_pairs(warnings: Sequence[object], key: str) -> dict[object, list[object]]:
    """{heading element_idx_start: [filled row element_idx_start, ...]} under
    `key` of every consumed-heading record; a malformed pair is skipped, as
    the doctor skips any record it cannot read: a value under `key` that is
    no list, a pair that is not a heading index and a list of row indexes,
    or an index that is not hashable."""
    fills: dict[object, list[object]] = {}
    for warning in warnings:
        if not isinstance(warning, Mapping) or warning.get('check') != CONSUMED_HEADING_CHECK:
            continue
        pairs = warning.get(key)
        for pair in pairs if isinstance(pairs, list) else []:
            if (isinstance(pair, list) and len(pair) == 2 and isinstance(pair[1], list)
                    and all(isinstance(index, Hashable) for index in (pair[0], *pair[1]))):
                fills[pair[0]] = list(pair[1])
    return fills


def heading_fills(warnings: Sequence[object]) -> dict[object, list[object]]:
    """{a heading's element_idx_start: the element_idx_start of each row it
    filled}, for every heading that filled rows, consumed or kept, read back
    from a render-warnings list. Empty for a render that filled none, or that
    predates this pass."""
    return {**_recorded_pairs(warnings, CONSUMED_HEADINGS_KEY), **_recorded_pairs(warnings, KEPT_HEADINGS_KEY)}


def consumed_headings(warnings: Sequence[object]) -> frozenset[object]:
    """The element_idx_start of every heading this pass removed, which renders
    no row of its own, read back from a render-warnings list."""
    return frozenset(_recorded_pairs(warnings, CONSUMED_HEADINGS_KEY))


def consume_institution_headings(coded: Sequence[CodedEntry],
                                 groups: MutableMapping[str, list[dict[str, Any]]],
                                 is_heading_shaped: Callable[[str], bool],
                                 warnings: list[dict[str, Any]] | None = None) -> None:
    """Fill the rows of every heading `heading_runs` finds in `coded` with the
    heading's name, and remove the heading from `groups` (its code's list in
    stage 6's `entries_by_code`) unless it keeps its own row
    (`_keeps_its_row`). A filled row gets a new `extracted_fields` dict, so a
    dict two entries share is never filled through the other. When `warnings`
    is given, the headings are reported to it (`consumed_heading_warnings`)."""
    runs = heading_runs(coded, is_heading_shaped)
    for run in runs:
        for idx, key in run.fills:
            entry = coded[idx][0]
            entry['extracted_fields'] = {**entry['extracted_fields'], key: run.name}
        if _keeps_its_row(run, coded):
            continue
        heading, code = coded[run.heading]
        kept = [entry for entry in groups.get(code, []) if entry is not heading]
        if kept:
            groups[code] = kept
        else:
            groups.pop(code, None)
    if warnings is not None:
        warnings.extend(consumed_heading_warnings(runs, coded))
