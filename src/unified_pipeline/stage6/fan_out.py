"""#983: one entry, several sibling records -> one entry per record.

Stage 4 sometimes separates the records of a multi-record entry correctly and
then files them under a key the taxonomy code's schema does not define:
`awards: [3 dicts]` on an H entry, `committees: [4 dicts]` / `entries: [3
dicts]` on a P entry, `mentees`, `degrees`, `licenses`, `journals` ... The
schema's own fields (`award_name`, `committee_name`, ...) stay empty, so every
section renderer, which reads the schema's keys and nothing else, falls back to
the entry's raw text: one table row with literal tabs, empty Role/Date cells
and (until #983's second half) a cut-off tail.

`fan_out_multi_record_entries` runs once per render, before the #820 PII pass
and before dedup, and replaces such an entry with one child entry per record,
so both of those see -- and the renderers render -- each record on its own.

Pure and dependency-free. The schema is PASSED IN (`{code: field names}`)
rather than imported: nothing under `stage6/` may import `stage4`
(`tests/test_stage6_normalization_import_direction.py` pins that absence), so
`stage_6_word_template.py` hands over `stage4.schemas.FIELD_SCHEMAS`.

What is deliberately NOT fanned out:

- a list that is not 2+ dicts (a one-item list is not a fused entry, and a list
  of strings is a list of values);
- a list any of whose dicts shares no key with the schema (plus the
  `_EXTRA_RECORD_KEYS` the code's items are known to carry) -- web181's K2
  `mentees: [{name, year}]` is the named example. Its items name nothing the K2
  renderer reads, so a child built from one would render empty and the entry's
  raw text would be lost;
- an entry a stage-5 formatter already rendered whole (`formatted_text` /
  `formatted_citation`), which would repeat that rendering on every child;
- an entry whose text has a token no rendered field holds (`_fields_carry_text`);
- an entry that carries more than one such list, or an item that carries a key
  outside the schema next to schema keys (web228's K4 `sessions: [{date, title,
  duration}]`: `title` is the session's own name and no K4 renderer reads it, so
  each child would render the parent's activity title and the date and lose the
  session title).
"""
from __future__ import annotations

import copy
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any

from unified_pipeline.stage6.formatting.dates import format_date_for_section

# Provenance key written on every child. Shows which list the record came from
# and where in it: `{'key': 'awards', 'index': 1, 'count': 3}`.
FANNED_OUT_FROM = 'fanned_out_from'

# A list of records has at least this many; below it the entry is one record.
_MIN_RECORDS = 2

# The text stage 2 fused N paragraphs into is tab-joined.
_TEXT_SEGMENT_SEPARATOR = '\t'

# The built-text separator: the same one stage 2 uses between table cells, so
# a child's built text reads as the record line it stands for and stays visible
# to `_recover_unrendered_records`, which verifies pipe/tab record lines.
_BUILT_TEXT_SEPARATOR = ' | '

# A child renders from its fields, so a token no RENDERED field holds is lost:
# a paragraph on committee service that stage 4 reduced to six committees
# (web185), a service entry that also describes a grant (web240), or a field
# the target renderer never writes (P's `institution`, until #985 made P append
# it to the name cell: web240's group prefixes). The entry is
# fanned out only when EVERY token of its own text is held, with at least the
# multiplicity the text has, by a field the target code's renderer writes
# (`_RENDERED_FIELDS`). Zero uncovered tokens, not a tolerance: any
# "qualifier" a tolerance forgave was content the output no longer had.
# A token is a run of letters or digits, lowercased. `_STOPWORDS` are the
# connectives that carry no content and that a field never holds ("Chair of
# the Board" against `role: Chair`, `committee_name: Board`).
_STOPWORDS = frozenset({'a', 'an', 'and', 'at', 'for', 'in', 'of', 'on', 'the', 'to'})
_TOKEN_RE = re.compile(r'[a-z0-9]+')

# Per taxonomy code, the `extracted_fields` keys its section renderer writes
# into the document, read off the renderer by rendering one entry per code with
# a unique marker in every schema field and listing the markers that reach the
# .docx while the entry's own text is a neutral line
# (`test_rendered_fields_match_what_each_section_writes` repeats that probe, so a
# renderer that starts or stops reading a field fails it). `narrative` is in no
# set: nothing writes it. A code missing here is never fanned out. That includes
# every code whose section writes the entry's TEXT and no field (E, G, J, K2-K5,
# L1, L2, M1, M2, N1, N3, N4, S0, T; `_TEXT_RENDERED_CODES`): a child of one would
# render only its built text, which does not carry the parent's scalars.
_RENDERED_FIELDS: Mapping[str, frozenset[str]] = MappingProxyType({
    'B1': frozenset({'degree', 'discipline', 'institution', 'year'}),
    'B2': frozenset({'institution', 'program_name', 'year'}),
    'C': frozenset({'end_date', 'institution', 'role', 'specialty', 'start_date', 'training_type'}),
    'D1': frozenset({'department', 'end_date', 'institution', 'start_date', 'title'}),
    'D2': frozenset({'department', 'end_date', 'institution', 'start_date', 'title'}),
    'D3': frozenset({'department', 'end_date', 'organization', 'start_date', 'title'}),
    'F1': frozenset({'expiration_date', 'issue_date', 'license_number', 'state_country'}),
    'F2': frozenset({'certifying_board', 'recertification_date', 'specialty', 'year_certified'}),
    'H': frozenset({'award_name', 'date', 'granting_body'}),
    'I': frozenset({'end_date', 'membership_type', 'organization', 'start_date'}),
    'K1': frozenset({'course_code', 'course_title', 'institution', 'role'}),
    'L3': frozenset({'end_date', 'institution', 'leadership_role', 'start_date'}),
    'M2A': frozenset({'agency', 'annual_funding', 'end_date', 'grant_number', 'notes', 'percent_effort', 'pi_name', 'pi_role', 'start_date', 'status', 'title', 'total_funding'}),
    'M2B': frozenset({'agency', 'end_date', 'grant_number', 'notes', 'percent_effort', 'pi_name', 'pi_role', 'start_date', 'status', 'title', 'total_funding'}),
    'M2C': frozenset({'agency', 'grant_number', 'notes', 'pi_name', 'pi_role', 'status', 'title'}),
    'M2D': frozenset({'assignee', 'filing_date', 'inventors', 'issue_date', 'patent_number', 'status', 'title'}),
    'N2': frozenset({'agency', 'end_date', 'grant_number', 'grant_title', 'role', 'start_date'}),
    'N3A': frozenset({'mentee_level', 'mentee_name', 'research_focus', 'start_date'}),
    'N3B': frozenset({'current_position', 'end_date', 'mentee_level', 'mentee_name', 'start_date'}),
    'O': frozenset({'end_date', 'institution', 'leadership_role', 'start_date'}),
    'P': frozenset({'committee_name', 'end_date', 'institution', 'role', 'start_date'}),
    'Q1': frozenset({'end_date', 'organization', 'role', 'start_date'}),
    'Q2': frozenset({'committee_name', 'end_date', 'organization', 'role', 'start_date'}),
    'Q3': frozenset({'agency', 'end_date', 'panel_name', 'role', 'start_date'}),
    'Q4': frozenset({'end_date', 'journal_name', 'role', 'start_date'}),
    'Q4A': frozenset({'end_date', 'journal_name', 'role', 'start_date'}),
    'Q4B': frozenset({'end_date', 'journal_name', 'role', 'start_date'}),
    'Q4C': frozenset({'end_date', 'journal_name', 'start_date'}),
    'Q4D': frozenset({'journal_name', 'year'}),
    'R': frozenset({'date', 'event_name', 'location', 'role', 'title'}),
    'S1': frozenset({'authors', 'doi', 'issue', 'journal', 'pages', 'pmcid', 'pmid', 'title', 'volume', 'year'}),
    'S2': frozenset({'authors', 'doi', 'issue', 'journal', 'pages', 'pmcid', 'pmid', 'title', 'volume', 'year'}),
    'S3': frozenset({'authors', 'publisher', 'title', 'year'}),
    'S4': frozenset({'authors', 'book_title', 'chapter_title', 'doi', 'editors', 'pages', 'publisher', 'year'}),
    'S5': frozenset({'authors', 'title', 'year'}),
    'S6': frozenset({'authors', 'doi', 'journal', 'pages', 'pmcid', 'pmid', 'title', 'volume', 'year'}),
    'S7': frozenset({'authors', 'title', 'year'}),
    'S8': frozenset({'authors', 'doi', 'title', 'year'}),
    'S9': frozenset({'authors', 'title', 'year'}),
})

# The codes whose section renders the entry's text, not its fields (see above).
# Pinned by `test_text_rendered_codes_write_the_text_and_no_field`.
_TEXT_RENDERED_CODES = frozenset({'E', 'G', 'J', 'K2', 'K3', 'K4', 'K5', 'L1', 'L2', 'M1', 'M2', 'N1', 'N3', 'N4', 'S0', 'T'})

# Keys a stage-5 formatter writes for the WHOLE entry (5c teaching prose, 5d
# citation). An entry that carries one already has a rendering of all its
# records; a child copying it would repeat the whole list once per record.
_FORMATTED_KEYS = ('formatted_text', 'formatted_citation')

# Stage 4's free-text remark about the entry as a whole ("Entry contains three
# tab-separated roles ..."). Not a field of any record, so children do not
# inherit it.
_ENTRY_REMARK_KEY = 'notes'

# #1187: keys stage 4 emits on a code's records that its `FIELD_SCHEMAS` entry
# does not define. The schema is also the stage-4 extraction prompt, so the
# allowance lives here, not there. B1 carries its attendance dates under
# `dates_attended` (a string or a `{start_date, end_date}` dict), flat
# `dates_attended_start_date` / `_end_date`, or plain `start_date` / `end_date`
# (the three shapes `sections/education.py` reads); a multi-degree `degrees`
# list whose items hold one failed `_record_list` on that key alone and both
# degrees vanished. Counted over the 126 stage-4 artifacts: the only extra key
# a B1 list item carries is `dates_attended`; the flat and generic keys sit on
# the parent and are listed so a child's own copy is accepted too.
_EXTRA_RECORD_KEYS: Mapping[str, frozenset[str]] = MappingProxyType({
    'B1': frozenset({'dates_attended', 'dates_attended_start_date',
                     'dates_attended_end_date', 'start_date', 'end_date'}),
})

# #1187: codes whose renderer writes nothing for a record list left whole --
# `sections/education.py` skips a B1 row with no top-level degree or
# institution, so an unsplit `degrees` list vanishes. Other codes keep the
# entry's text (K2/K4 refuse to split by design), so a declined list there
# loses nothing; warning on them put 31 false alarms in 16 corpus CVs.
_LIST_LOST_WHEN_KEPT_WHOLE = frozenset({'B1'})

# #1187: the attendance dates `sections/education.py` writes into B1's Dates
# column on top of `_RENDERED_FIELDS['B1']`: flat, generic, and the nested
# `dates_attended: {start_date, end_date}` dict. A STRING `dates_attended` is
# written too, but only when no start/end builds a range (`_is_written_date`). Without these a degree child whose dates the text also
# names would always be refused by `_fields_carry_text`, and the entry would
# still vanish.
_RENDERED_DATE_FIELDS: Mapping[str, frozenset[str]] = MappingProxyType({
    'B1': frozenset({'dates_attended_start_date', 'dates_attended_end_date',
                     'start_date', 'end_date'}),
})
_NESTED_DATES_KEY = 'dates_attended'

# Render-warning record for a record list that was not fanned out (#1187).
REJECTED_LIST_CHECK = 'fan_out_record_list_rejected'
_WARN_SEVERITY = 'WARN'
_EVIDENCE_KEYS_SHOWN = 8

_DATE_KEY_SUFFIX = '_date'
_BARE_DATE_KEYS = frozenset({'date', 'year'})
# `dates_attended` and its flat `dates_attended_start_date` / `_end_date` (#1187).
_DATES_ATTENDED_PREFIX = 'dates_attended'


def _is_date_key(key: str) -> bool:
    return (key in _BARE_DATE_KEYS or key.endswith(_DATE_KEY_SUFFIX)
            or key.startswith(_DATES_ATTENDED_PREFIX))


def _is_blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) \
        or (isinstance(value, (list, dict)) and not value)


def _tokens(text: str) -> Counter[str]:
    return Counter(t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS)


def _leaf_text(value: object) -> str:
    """Every string, number and nested string inside `value`, space-joined."""
    if isinstance(value, Mapping):
        return ' '.join(_leaf_text(v) for v in value.values())
    if isinstance(value, list):
        return ' '.join(_leaf_text(v) for v in value)
    return '' if value is None else str(value)


def _rendered_text(key: str, value: object, code: str) -> str:
    """What the renderer writes for one field: a date as the code's date column
    shows it (year only for most, so a month the text names is not held), any
    other value as it stands."""
    text = _leaf_text(value)
    return format_date_for_section(text, code) if _is_date_key(key) else text


def _is_written_date(code: str, key: str, value: object,
                     fields: Mapping[str, Any]) -> bool:
    """Whether B1's renderer writes this attendance-date field (#1187). A
    string `dates_attended` is written only when no flat or generic start/end
    is there to build the range from."""
    bounds = _RENDERED_DATE_FIELDS.get(code, frozenset())
    if key in bounds:
        return True
    if code not in _RENDERED_DATE_FIELDS or key != _NESTED_DATES_KEY:
        return False
    if isinstance(value, Mapping):
        return True
    return isinstance(value, str) and not any(fields.get(k) for k in bounds)


def _fields_carry_text(entry: Mapping[str, Any],
                       child_fields: Sequence[Mapping[str, Any]]) -> bool:
    """Whether the fields the renderer writes, summed over the children (each
    child writes the scalars it inherited, so they count once per child), hold
    every token of the entry's text -- see `_STOPWORDS` for what is not counted.
    A schema key the renderer never reads, and a key outside the schema, hold
    nothing."""
    code = str(entry.get('taxonomy_code'))
    rendered = _RENDERED_FIELDS.get(code)
    if rendered is None:
        return False
    held: Counter[str] = Counter()
    for fields in child_fields:
        for key, value in fields.items():
            if key in rendered:
                held += _tokens(_rendered_text(key, value, code))
            elif _is_written_date(code, key, value, fields):
                held += _tokens(_leaf_text(value))
    return not _tokens(str(entry.get('text') or '')) - held


def _record_list(value: object, schema: frozenset[str]) -> bool:
    """True when `value` is 2+ dicts, every one of which is made of schema
    keys and shares at least one with the schema."""
    if not isinstance(value, list) or len(value) < _MIN_RECORDS:
        return False
    return all(isinstance(item, Mapping) and item
               and set(item) <= schema for item in value)


def _record_schema(code: object, schema: frozenset[str]) -> frozenset[str]:
    """The keys a record of `code` may carry: the stage-4 schema plus the
    `_EXTRA_RECORD_KEYS` stage 4 is known to add (#1187)."""
    return schema | _EXTRA_RECORD_KEYS.get(str(code), frozenset())


def _record_keys(fields: Mapping[str, Any], schema: frozenset[str]) -> list[str]:
    """The `extracted_fields` keys outside the schema that hold a record list."""
    return [key for key, value in fields.items()
            if key not in schema and _record_list(value, schema)]


def _parent_is_own_record(scalars: Mapping[str, Any],
                          items: Sequence[Mapping[str, Any]],
                          schema: frozenset[str], segment_count: int) -> bool:
    """True when the parent's own scalar fields are a record the list does not
    repeat (a Co-Leader row whose `additional_roles` are the later posts).

    Needs an identity value first: a non-date field the items also use. A
    `mentee_level` the items never carry is shared context, and start/end dates
    the items repeat are the same span, so a parent with neither is not a record.
    Given one, the entry's own text settles it when it can: one more tab
    segment than list items means the parent is the extra record, exactly as
    many means the list already holds every one of them (a first post stage 4
    also left in the parent's scalars). When the counts say neither, a parent
    whose values some item repeats is that item, not emitted twice.
    """
    item_keys = set().union(*items)
    identity = {key: value for key, value in scalars.items()
                if key in schema and key in item_keys
                and not _is_date_key(key) and not _is_blank(value)}
    if not identity:
        return False
    if segment_count in (len(items), len(items) + 1):
        return segment_count == len(items) + 1
    return not any(all(item.get(key) == value for key, value in identity.items())
                   for item in items)


def _segments(text: object) -> list[str]:
    return [part.strip() for part in str(text or '').split(_TEXT_SEGMENT_SEPARATOR)
            if part.strip()]


def _built_text(record: Mapping[str, Any]) -> str:
    """One line out of a record's own string values, in the record's key order."""
    return _BUILT_TEXT_SEPARATOR.join(
        str(value).strip() for value in record.values()
        if isinstance(value, (str, int, float)) and str(value).strip())


def _child_texts(text: object, records: Sequence[Mapping[str, Any]]) -> list[str]:
    """Each record's text: its own tab segment when the segments line up one to
    one with the records, else a line built from the record's values (a record
    that wrapped across lines makes the counts disagree, and pairing segments
    by position then would hand a record another record's tail)."""
    segments = _segments(text)
    if len(segments) == len(records):
        return segments
    return [_built_text(record) for record in records]


def _child(entry: Mapping[str, Any], fields: dict[str, Any], text: str,
           key: str, index: int, count: int) -> dict[str, Any]:
    child = copy.deepcopy(dict(entry))
    child['text'] = text
    child['extracted_fields'] = fields
    child[FANNED_OUT_FROM] = {'key': key, 'index': index, 'count': count}
    return child


def _is_dict_list(value: object) -> bool:
    """2+ non-empty dicts: record-like, whatever their keys."""
    return (isinstance(value, list) and len(value) >= _MIN_RECORDS
            and all(isinstance(item, Mapping) and item for item in value))


def _rejected_record_lists(fields: Mapping[str, Any],
                           schema: frozenset[str]) -> dict[str, list[str]]:
    """`{key: item keys outside the schema}` for every field outside the
    schema that holds a list of record-like dicts `_record_list` refused
    (#1187). Key names only; never a value."""
    return {key: sorted({k for item in value for k in item} - schema)
            for key, value in fields.items()
            if key not in schema and _is_dict_list(value)
            and not _record_list(value, schema)}


def _rejection_warnings(rejected: Mapping[tuple[str, str], list[list[str]]]) -> list[dict[str, Any]]:
    """One sidecar WARN per (code, list key), counting the entries and, when
    item keys outside the schema kept the list from fanning out, naming them.
    An empty key list means the list was record-shaped and the entry was
    declined by the other guards (text a rendered field does not carry, a
    stage-5 rendering, several lists). Sorted, so the sidecar is deterministic."""
    out = []
    for (code, key), cases in sorted(rejected.items()):
        stray = sorted({k for extra in cases for k in extra})
        why = (f"item keys outside the {code} schema: {', '.join(stray[:_EVIDENCE_KEYS_SHOWN])}"
               if stray else "the entry's text holds content its fields do not carry")
        out.append({
            'check': REJECTED_LIST_CHECK,
            'code': code,
            'section': None,
            'message': (f"{len(cases)} {code} entr{'y' if len(cases) == 1 else 'ies'}: "
                        f"`{key}` holds several records that were not split into "
                        f"separate rows ({why}); a record may be missing from "
                        "the output"),
            'evidence': [f"{code}.{key}: {len(cases)} entr{'y' if len(cases) == 1 else 'ies'}",
                         *stray[:_EVIDENCE_KEYS_SHOWN]],
            'severity': _WARN_SEVERITY,
        })
    return out


def _declined_record_lists(entry: Mapping[str, Any], schema: frozenset[str]) -> dict[str, list[str]]:
    """Every record-list key of an entry `_fan_out_entry` declined: the lists
    `_record_list` refused (with their stray item keys) plus the ones it
    accepted that a later guard then declined (no stray keys)."""
    fields = entry.get('extracted_fields')
    if not isinstance(fields, Mapping):
        return {}
    return {**{key: [] for key in _record_keys(fields, schema)},
            **_rejected_record_lists(fields, schema)}


def _fan_out_entry(entry: Mapping[str, Any], schema: frozenset[str]) -> list[dict[str, Any]] | None:
    """The children of `entry`, or None when it is not a multi-record entry."""
    fields = entry.get('extracted_fields')
    if not isinstance(fields, Mapping):
        return None
    keys = _record_keys(fields, schema)
    if len(keys) != 1 or any(fields.get(k) for k in _FORMATTED_KEYS):
        return None
    key = keys[0]
    items = fields[key]
    scalars = {k: v for k, v in fields.items() if k not in (key, _ENTRY_REMARK_KEY)}
    own = _parent_is_own_record(scalars, items, schema, len(_segments(entry.get('text'))))
    records = ([scalars] if own else []) \
        + [dict(item) for item in items]
    child_fields = [{**copy.deepcopy(scalars), **copy.deepcopy(record)} for record in records]
    if not _fields_carry_text(entry, child_fields):
        return None
    texts = _child_texts(entry.get('text'), records)
    return [_child(entry, fields, text, key, i, len(records))
            for i, (fields, text) in enumerate(zip(child_fields, texts))]


def fan_out_multi_record_entries(
        entries: Sequence[Mapping[str, Any]],
        schema_fields: Mapping[str, Mapping[str, Any]],
        warnings: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """`entries` with every multi-record entry replaced, in place and in order,
    by one child per record. Everything else is returned untouched.

    `schema_fields` is `{taxonomy_code: {'fields': [...]}}`, i.e.
    `stage4.schemas.FIELD_SCHEMAS`. Each child gets the parent's scalar
    `extracted_fields` plus its own record, the record winning any conflict;
    the parent's other keys are copied; `FANNED_OUT_FROM` names the list it
    came from.

    A list of record-like dicts that is not fanned out -- `_record_list`
    refused it, or a later guard declined the entry -- is not dropped silently
    (#1187): for a code in `_LIST_LOST_WHEN_KEPT_WHOLE`, when `warnings` is given, one render-warning dict per (code, list
    key) is appended to it (`REJECTED_LIST_CHECK`).
    """
    out: list[dict[str, Any]] = []
    rejected: dict[tuple[str, str], list[list[str]]] = {}
    for entry in entries:
        code = entry.get('taxonomy_code')
        schema = _record_schema(code, frozenset(schema_fields.get(code, {}).get('fields', ())))
        children = _fan_out_entry(entry, schema)
        if children is None and code in _LIST_LOST_WHEN_KEPT_WHOLE:
            for key, stray in _declined_record_lists(entry, schema).items():
                rejected.setdefault((str(code), key), []).append(stray)
        out.extend(children if children is not None else [entry])
    if warnings is not None:
        warnings.extend(_rejection_warnings(rejected))
    return out
