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
entries after it (`_run`) and, when at least `MIN_FILLED_ROWS` of them leave
empty the field their code's renderer prints the institution from
(`FILL_FIELD_BY_CODE`), fills that field with the heading's name on each and
drops the heading from its code's group: its text now renders in its
children's cells, so it renders no row of its own, is not sent to the Appendix,
and no later pass sees it. A heading that fills fewer rows is left exactly as
it was.

Never overwrites a value: a row that names its own institution keeps it.
Stage 6 is the one place this reaches stored runs as well as fresh ones; the
stage-4 stamp (`stage4/context_headings.py`, #1047) covers only T-coded
headings, and only on a fresh run. Running after routing means the code a row
is filled for is the code whose renderer prints it, and a filled field cannot
change a reroute decision (`_fields_fit_reroute_target` reads the fields).

What is deliberately NOT filled: a K or L row. Their renderers print an
institution only on some paths (`teaching._teaching_entry_lines` drops it when
stage 5c formatted the row), and the open card layout (#2019) renders the
source's own words, so a filled value would not reliably print and the consumed
heading would take the institution with it. Nor an appointment (D1/D2/D3),
which positions already gives its employer line (`_APPOINTMENT_CODES`). Nor a
row of any code whose renderer prints no institution (H, R, M2*, S* ...):
`FILL_FIELD_BY_CODE` lists the codes that do.

Pure: no docx, no I/O. The weak heading-shape test lives in stage 4
(`context_headings._is_heading_shaped`), which nothing under `stage6/` may
import, so `stage_6_word_template.py` passes it in, as it passes
`FIELD_SCHEMAS` to `fan_out.py`.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

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

#: Every field a code's renderer reads for its institution cell: a row holding
#: any of them names its own institution. Leadership reads `institution` or
#: `organization`; the others read only their fill field.
_OWN_NAME_FIELDS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    code: (('institution', 'organization') if code == 'O' else (fill,))
    for code, fill in FILL_FIELD_BY_CODE.items()
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

#: Appointment codes, which this pass neither fills nor takes a heading from.
#: Positions already gives an appointment with no employer the employer line
#: above it (`positions._propagate_institution_to_subentries`, with that line's
#: stage-5b location, and marked so its merge rule can tell an inherited
#: employer from a named one); and an employer-only appointment row may be an
#: appointment whose title stage 2 split onto another line, a real record that
#: must not vanish. A D row ends a run like any other code this pass does not
#: fill.
_APPOINTMENT_CODES = frozenset({'D1', 'D2', 'D3'})

#: The fewest rows a heading must fill to be consumed. One is not a group: a
#: name line over a single record (AQAJHD's employer lines over one role each)
#: is as likely that record's own header row, and it keeps rendering as today.
MIN_FILLED_ROWS = 2

#: Longest heading text, colon excluded: stage 4's own bound for a heading line
#: (`context_headings.MAX_HEADING_CHARS`), restated because stage 6 may not
#: import it. The longest heading the #985 census consumes is 68 characters (a
#: society name with its acronym).
MAX_HEADING_CHARS = 70

#: The stage-4 fields that can hold a heading's name: the institution or
#: organization, and Site/Position, where stage 4 files a mentee list's
#: institution sub-heading (WZSMUO 166/184).
_NAME_FIELDS = frozenset({'institution', 'organization', 'site_position'})

#: Fields that restate the whole entry rather than add to it (stage 5c's and
#: 5d's renderings, their provenance) and so do not make a heading a record.
_RESTATEMENT_FIELDS = frozenset({'formatted_text', 'formatted_citation', 'formatting_source'})

#: Stage 3b's code for an unclassifiable or heading-like line: it is never this
#: pass's heading (stage 4's stamp owns those, #1047) and it ends a run.
_HEADING_TAXONOMY_CODE = 'T'

#: A line holding any of these is a record or a table row, not a bare name.
_NOT_A_NAME_RE = re.compile(r'[\d\t\n|]')
_WORD_RE = re.compile(r'[a-z0-9]+')
#: Connectives a stage-4 name may drop or add ("University of X" vs "X University").
_STOPWORDS = frozenset({'a', 'an', 'and', 'at', 'for', 'in', 'of', 'the'})

#: Words that name a KIND of institution, unit or body rather than a particular
#: one. A heading made only of these ("University", "Medical Center", "School of
#: Medicine", "Board of Directors") names no institution a reader could place:
#: it is a sub-group label under one the CV named earlier (CXRYCF 86/93 under
#: "University/Medical Center Committees", HVLDGS 2302 over a society's task
#: forces), so it is not a heading here. Census of #985 (2026-10-28).
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
#: reads them back here (`heading_fills`).
CONSUMED_HEADINGS_KEY = 'consumed_headings'


def _content_words(value: object) -> frozenset[str]:
    """The lowercased words of a string value, connectives dropped."""
    if not isinstance(value, str):
        return frozenset()
    return frozenset(w for w in _WORD_RE.findall(value.lower()) if w not in _STOPWORDS)


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
    """Whether the entry may be a heading: content, and not an appointment
    (`_APPOINTMENT_CODES`)."""
    return _is_content(entry, code) and code not in _APPOINTMENT_CODES


def heading_name(entry: Mapping[str, Any], code: str) -> str | None:
    """The name a HEADING entry gives the rows under it, else None.

    A heading is a content entry that is not an appointment (`_may_head`)
    whose text is a bare name (`_heading_text`) of a particular institution
    (`_names_a_particular_one`) and whose stage-4 fields hold nothing but that
    name: every non-blank field is a `_NAME_FIELDS` one, and the text has no
    word the name fields lack. So a dated line, a role, a committee or a title
    is never one.
    """
    if not _may_head(entry, code):
        return None
    name = _heading_text(entry)
    held = _held_fields(entry)
    if name is None or not held.keys() <= _NAME_FIELDS or not _names_a_particular_one(name):
        return None
    named = frozenset().union(*(_content_words(value) for value in held.values()))
    return name if _content_words(name) <= named else None


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


def _same_institution(own: str, name: str) -> bool:
    """Whether a row's own institution is the heading's, or a unit of it, or
    the heading's abbreviation: one's words contain the other's ("UCLA" under
    "UCLA", "Stanford Cancer Center" under "Stanford", "ASHP" under its full
    name)."""
    own_words, words = _content_words(own), _content_words(name)
    return bool(own_words) and (own_words <= words or words <= own_words)


def _own_name(entry: Mapping[str, Any], code: str) -> str:
    """The institution the row names itself, '' when none."""
    fields = entry['extracted_fields']  # a row reaching here holds stage-4 fields (`_ends_run`)
    for key in _OWN_NAME_FIELDS[code]:
        value = fields.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ''


def _in_scope(heading: Mapping[str, Any], entry: Mapping[str, Any]) -> bool:
    """Whether `entry` sits under the heading's hierarchy leaf: the same path,
    or one below it (a sub-heading stage 1b did make a level, WZSMUO's
    "Programs" / "Committees" under an institution line)."""
    path = list(heading.get('hierarchy') or [])
    return list(entry.get('hierarchy') or [])[:len(path)] == path


def _same_element(heading: Mapping[str, Any], entry: Mapping[str, Any]) -> bool:
    """Whether `entry` starts in the heading's own source element: the
    "heading" is then a cell of that row, split off by stage 2 (HVLDGS 1878, a
    table row's first cell), not a line over the rows below."""
    start = heading.get('element_idx_start')
    return start is not None and entry.get('element_idx_start') == start


@dataclass
class HeadingRun:
    """One heading and the rows it fills, by position in the coded list."""
    heading: int
    name: str
    fills: list[tuple[int, str]] = field(default_factory=list)
    own_names: list[str] = field(default_factory=list)


def _element_gap(prev: Mapping[str, Any], entry: Mapping[str, Any]) -> int | None:
    """The number of source elements strictly between `prev` and `entry` (0
    when they share one, as fanned-out records do), None when either has no
    integer element index."""
    end = prev.get('element_idx_end', prev.get('element_idx_start'))
    start = entry.get('element_idx_start')
    if not isinstance(end, int) or not isinstance(start, int):
        return None
    return max(start - end - 1, 0)


def _ends_run(heading: Mapping[str, Any], entry: Mapping[str, Any], code: str,
              family: str | None, is_heading_shaped: Callable[[str], bool]) -> bool:
    """Whether `entry` is outside the heading's run whatever it names (`_run`)."""
    return (not _in_scope(heading, entry) or _same_element(heading, entry)
            or not _is_content(entry, code) or not _held_fields(entry)
            or heading_name(entry, code) is not None or is_heading_shaped(str(entry.get('text') or ''))
            or code not in FILL_FIELD_BY_CODE or family not in (None, _FAMILY_BY_CODE[code]))


def _run(coded: Sequence[CodedEntry], start: int, name: str,
         is_heading_shaped: Callable[[str], bool]) -> HeadingRun:
    """The rows the heading at `start` governs and the field to fill on each.

    The run ends at the first entry that leaves the heading's hierarchy leaf,
    starts in the heading's own source element (`_same_element`), is another
    heading (a bare name line, extracted or not), is heading-shaped in stage
    4's weak sense (a bare Title-Case label, a colon or ALL CAPS line: a sibling
    or role sub-heading), is T, a fragment or a duplicate, has no stage-4 field
    at all, or is of a code this pass does not fill or of another family than
    the run's first row (`_ends_run`).

    It also ends at a row further from the row before it, in source elements,
    than the run's rows have been so far (`spacing`, set by the first step that
    stays on one hierarchy path). Stage 4 drops the lines it does not extract,
    a fragment or a duplicate, so that gap is all stage 6 sees of a sibling
    heading stage 3b flagged: VQFSDI 126 (a fragment between adjacent rows),
    CXRYCF 107 and CHXRBM 134 (duplicates). A step onto another sub-level is
    not measured: its gap holds that level's own heading line (WZSMUO 332/339).

    A row that names its own institution keeps it; the run goes on past it
    when that is the heading's institution (`_same_institution`) and ends there
    otherwise, since a row naming another institution may be the next group's
    unrecognised heading (MYNQRA 46, a 103-character college line) or a list
    with no heading at all (DUTAVD 62-74, one society per row).
    """
    heading = coded[start][0]
    run = HeadingRun(start, name)
    family = None
    spacing = None
    prev = heading
    for idx in range(start + 1, len(coded)):
        entry, code = coded[idx]
        if _ends_run(heading, entry, code, family, is_heading_shaped):
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
        if not own:
            run.fills.append((idx, FILL_FIELD_BY_CODE[code]))
        elif _same_institution(own, name):
            run.own_names.append(own)
        else:
            break
    return run


def _corroborated(run: HeadingRun) -> bool:
    """Whether a row of the run names the heading's institution as its own
    (`_same_institution`), which is what makes an unextracted name line a
    heading."""
    return bool(run.own_names)


def heading_runs(coded: Sequence[CodedEntry],
                 is_heading_shaped: Callable[[str], bool]) -> list[HeadingRun]:
    """Every heading that fills at least `MIN_FILLED_ROWS` rows, in document order."""
    runs = []
    for idx, (entry, code) in enumerate(coded):
        name = heading_name(entry, code)
        unextracted = name is None
        if unextracted:
            name = _unextracted_name(entry, code)
        if name is None:
            continue
        run = _run(coded, idx, name, is_heading_shaped)
        if len(run.fills) >= MIN_FILLED_ROWS and (not unextracted or _corroborated(run)):
            runs.append(run)
    return runs


def consumed_heading_warnings(runs: Sequence[HeadingRun],
                              coded: Sequence[CodedEntry]) -> list[dict[str, Any]]:
    """One INFO render-warnings record naming every consumed heading and the
    rows it filled, by element_idx_start only (never CV text); none when no
    heading was consumed."""
    if not runs:
        return []
    filled = sum(len(run.fills) for run in runs)
    return [{
        'check': CONSUMED_HEADING_CHECK,
        'code': None,
        'section': None,
        'message': (f"institution sub-heading moved into the rows under it: "
                    f"{len(runs)} heading{'' if len(runs) == 1 else 's'}, {filled} rows filled"),
        'evidence': [f"element_idx_start {coded[run.heading][0].get('element_idx_start')} filled "
                     + ', '.join(str(coded[idx][0].get('element_idx_start')) for idx, _ in run.fills)
                     for run in runs],
        'severity': 'INFO',
        CONSUMED_HEADINGS_KEY: [[coded[run.heading][0].get('element_idx_start'),
                                 [coded[idx][0].get('element_idx_start') for idx, _ in run.fills]]
                                for run in runs],
    }]


def heading_fills(warnings: Sequence[object]) -> dict[object, list[object]]:
    """{a consumed heading's element_idx_start: the element_idx_start of each
    row it filled}, read back from a render-warnings list. Empty for a render
    that consumed no heading, or that predates this pass; a malformed pair is
    skipped, as the doctor skips any record it cannot read."""
    fills: dict[object, list[object]] = {}
    for warning in warnings:
        if not isinstance(warning, Mapping) or warning.get('check') != CONSUMED_HEADING_CHECK:
            continue
        for pair in warning.get(CONSUMED_HEADINGS_KEY) or []:
            if isinstance(pair, list) and len(pair) == 2 and isinstance(pair[1], list):
                fills[pair[0]] = list(pair[1])
    return fills


def consume_institution_headings(coded: Sequence[CodedEntry],
                                 groups: MutableMapping[str, list[dict[str, Any]]],
                                 is_heading_shaped: Callable[[str], bool],
                                 warnings: list[dict[str, Any]] | None = None) -> None:
    """Fill the rows of every heading `heading_runs` finds in `coded` with the
    heading's name, and remove the heading from `groups` (its code's list in
    stage 6's `entries_by_code`). A filled row gets a new `extracted_fields`
    dict, so a dict two entries share is never filled through the other. When
    `warnings` is given, the consumed headings are reported to it
    (`consumed_heading_warnings`)."""
    runs = heading_runs(coded, is_heading_shaped)
    for run in runs:
        for idx, key in run.fills:
            entry = coded[idx][0]
            entry['extracted_fields'] = {**entry['extracted_fields'], key: run.name}
        heading, code = coded[run.heading]
        kept = [entry for entry in groups.get(code, []) if entry is not heading]
        if kept:
            groups[code] = kept
        else:
            groups.pop(code, None)
    if warnings is not None:
        warnings.extend(consumed_heading_warnings(runs, coded))
