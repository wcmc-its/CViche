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
- a list any of whose dicts shares no key with the schema -- web181's K2
  `mentees: [{name, year}]` is the named example. Its items name nothing the K2
  renderer reads, so a child built from one would render empty and the entry's
  raw text would be lost;
- an entry a stage-5 formatter already rendered whole (`formatted_text` /
  `formatted_citation`), which would repeat that rendering on every child;
- an entry that carries more than one such list, or an item that carries a key
  outside the schema next to schema keys (web228's K4 `sessions: [{date, title,
  duration}]`: `title` is the session's own name and no K4 renderer reads it, so
  each child would render the parent's activity title and the date and lose the
  session title).
"""
from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any

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

# Keys a stage-5 formatter writes for the WHOLE entry (5c teaching prose, 5d
# citation). An entry that carries one already has a rendering of all its
# records; a child copying it would repeat the whole list once per record.
_FORMATTED_KEYS = ('formatted_text', 'formatted_citation')

# Stage 4's free-text remark about the entry as a whole ("Entry contains three
# tab-separated roles ..."). Not a field of any record, so children do not
# inherit it.
_ENTRY_REMARK_KEY = 'notes'

_DATE_KEY_SUFFIX = '_date'
_BARE_DATE_KEYS = frozenset({'date', 'year'})


def _is_date_key(key: str) -> bool:
    return key in _BARE_DATE_KEYS or key.endswith(_DATE_KEY_SUFFIX)


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) \
        or (isinstance(value, (list, dict)) and not value)


def _record_list(value: Any, schema: frozenset[str]) -> bool:
    """True when `value` is 2+ dicts, every one of which is made of schema
    keys and shares at least one with the schema."""
    if not isinstance(value, list) or len(value) < _MIN_RECORDS:
        return False
    return all(isinstance(item, Mapping) and item
               and set(item) <= schema for item in value)


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


def _segments(text: Any) -> list[str]:
    return [part.strip() for part in str(text or '').split(_TEXT_SEGMENT_SEPARATOR)
            if part.strip()]


def _built_text(record: Mapping[str, Any]) -> str:
    """One line out of a record's own string values, in the record's key order."""
    return _BUILT_TEXT_SEPARATOR.join(
        str(value).strip() for value in record.values()
        if isinstance(value, (str, int, float)) and str(value).strip())


def _child_texts(text: Any, records: Sequence[Mapping[str, Any]]) -> list[str]:
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
    scalars = {k: copy.deepcopy(v) for k, v in fields.items()
               if k not in (key, _ENTRY_REMARK_KEY)}
    own = _parent_is_own_record(scalars, items, schema, len(_segments(entry.get('text'))))
    records = ([scalars] if own else []) \
        + [dict(item) for item in items]
    texts = _child_texts(entry.get('text'), records)
    return [_child(entry, {**scalars, **copy.deepcopy(record)}, text, key, i, len(records))
            for i, (record, text) in enumerate(zip(records, texts))]


def fan_out_multi_record_entries(
        entries: Sequence[Mapping[str, Any]],
        schema_fields: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """`entries` with every multi-record entry replaced, in place and in order,
    by one child per record. Everything else is returned untouched.

    `schema_fields` is `{taxonomy_code: {'fields': [...]}}`, i.e.
    `stage4.schemas.FIELD_SCHEMAS`. Each child gets the parent's scalar
    `extracted_fields` plus its own record, the record winning any conflict;
    the parent's other keys are copied; `FANNED_OUT_FROM` names the list it
    came from.
    """
    out: list[dict[str, Any]] = []
    for entry in entries:
        schema = frozenset(schema_fields.get(entry.get('taxonomy_code'), {}).get('fields', ()))
        children = _fan_out_entry(entry, schema) if schema else None
        out.extend(children if children is not None else [entry])
    return out
