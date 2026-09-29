"""Expand a stage-4 list of records into one entry per record (#983).

Stage 4 sometimes returns several sibling records from ONE stage-2 entry,
stored as a list of dicts under a key the code's schema does not define
(``{"awards": [{...}, {...}]}`` on an H entry, ``{"committees": [...]}`` on a
P entry). Every section renderer reads the schema's own keys only, so the
whole list is invisible to it and the entry falls back to its raw text: one
row, tabs and all, cut at a fixed character cap.

`fan_out_multi_record_entries` runs on the entry list before grouping and
replaces such an entry with one child per record, each carrying the record's
fields under the schema keys the renderer already reads. Text the records do
not carry is kept, verbatim, as one residual entry (below), so a fan-out never
drops content the raw-text fallback used to show. Nothing else in the render
path changes.

Pure: takes and returns plain dicts, imports no I/O library, and takes the
schema lookup as a parameter so this package does not import stage 4
(`stage6/__init__.py`: dependencies run one way).
"""
import copy
import re
from collections.abc import Callable, Collection
from typing import Any

from .dedup import _significant_words

# The fewest records that make a list "several siblings" rather than one
# record stage 4 happened to wrap in a list.
MIN_RECORDS_TO_FAN_OUT = 2

# Taxonomy-code prefixes whose nested lists are NOT siblings. A grant's
# `sub_awards` / `sub_projects` are parts of ONE research-support record
# (the M2A renderer owns them); fanning them out would turn one grant into
# several. Prefix match, so M2A/M2B/M3... are all covered.
NESTED_NOT_SIBLING_CODE_PREFIXES = ("M",)

# Field names that carry a date: `start_date`, `issue_date`, `date`, `year`, ...
DATE_KEY_SUFFIXES = ("_date", "_dates", "_year")
DATE_KEYS = frozenset({"date", "dates", "year", "dates_attended"})

# The one text separator a stage-2 paragraph range is joined with.
_SEGMENT_SEPARATOR = "\t"

# A segment of the parent's text (split on tab and newline) counts as
# represented by the children when at least this share of its significant
# words appears in some child's field values. Guessed from the 126-CV corpus
# (#983): the records' own segments score 0.9-1.0, a segment stage 4 did not
# extract at all (a registration number, a "served from ... through ..."
# sentence) scores under 0.7. Below it the segment is kept verbatim as a
# residual entry, so a fan-out can never drop text. Revisit if stage 4's
# extraction changes.
SEGMENT_REPRESENTED_MIN_SHARE = 0.8

_SEGMENT_SPLIT_RE = re.compile(r"[\t\n]")

# Joins a fanned-out child's values when its text has to be rebuilt. A pipe,
# because `entry_fragments` (the render-presence check) splits on it, so each
# value is tested for presence on its own.
_BUILT_TEXT_SEPARATOR = " | "

SchemaFieldsLookup = Callable[[str], Collection[str]]


def _is_empty(value: object) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _record_lists(fields: dict[str, Any],
                  schema_fields: Collection[str]) -> list[tuple[str, list[dict]]]:
    """The (key, records) pairs in `fields` that should fan out.

    A pair qualifies when the key is not a schema field, the value is a list
    of MIN_RECORDS_TO_FAN_OUT or more dicts, and every dict shares a key with
    the schema (a list of dicts that name none of the schema's fields is
    something else, e.g. a mentee list on a code whose schema has no mentee
    field).
    """
    pairs = []
    for key, value in fields.items():
        if key in schema_fields:
            continue
        if not isinstance(value, list) or len(value) < MIN_RECORDS_TO_FAN_OUT:
            continue
        if not all(isinstance(item, dict) for item in value):
            continue
        if not all(set(item) & set(schema_fields) for item in value):
            continue
        pairs.append((key, value))
    return pairs


def _is_date_key(key: str) -> bool:
    return key.endswith(DATE_KEY_SUFFIXES) or key in DATE_KEYS


def _parent_is_a_record(fields: dict[str, Any], records: list[dict],
                        schema_fields: Collection[str]) -> bool:
    """True when the parent's own schema fields already hold one of the
    records (stage 4 put the first record in the scalars and only the REST in
    the list, e.g. `additional_roles`). Then the parent must stay in the
    output; replacing it would drop that first record.

    A field both carry makes the parent a record. A DATE field does so only
    when some record's value differs from the parent's: one date range stated
    for a whole list of committees is context every record shares, not a
    record of its own.
    """
    for record in records:
        for key, value in record.items():
            if key not in schema_fields or _is_empty(value) or _is_empty(fields.get(key)):
                continue
            if not _is_date_key(key) or value != fields[key]:
                return True
    return False


def _child_text(parent_text: str, index: int, count: int,
                record: dict[str, Any]) -> str:
    """The text a child entry carries: its own tab segment when the parent's
    text splits into exactly one segment per record, else the record's values.
    """
    segments = [seg.strip() for seg in parent_text.split(_SEGMENT_SEPARATOR)
                if seg.strip()]
    if len(segments) == count:
        return segments[index]
    return _BUILT_TEXT_SEPARATOR.join(
        str(value) for value in record.values() if not _is_empty(value))


def _unrepresented_segments(parent_text: str,
                            children: list[dict[str, Any]]) -> tuple[list[str], int]:
    """(segments of `parent_text` the children do not carry, segment count)."""
    represented: set[str] = set()
    for child in children:
        for value in child["extracted_fields"].values():
            represented |= _significant_words(str(value))
    segments = [seg.strip() for seg in _SEGMENT_SPLIT_RE.split(parent_text)
                if seg.strip()]
    missing = []
    for segment in segments:
        words = _significant_words(segment)
        if words and (len(words & represented) / len(words)
                      < SEGMENT_REPRESENTED_MIN_SHARE):
            missing.append(segment)
    return missing, len(segments)


def _residual_entry(parent: dict[str, Any], segments: list[str]) -> dict[str, Any]:
    """The parent's text the children do not carry, as an entry with no
    extracted fields: every renderer already shows such an entry as its raw
    text, and it runs through the PII pass like any other entry."""
    residual = {name: copy.deepcopy(value) for name, value in parent.items()
                if name not in ("extracted_fields", "text")}
    residual["extracted_fields"] = {}
    residual["text"] = "\n".join(segments)
    residual["fanned_out_from"] = {"residual": True}
    return residual


def _make_child(parent: dict[str, Any], scalars: dict[str, Any],
                key: str, index: int, count: int,
                record: dict[str, Any]) -> dict[str, Any]:
    child = {name: copy.deepcopy(value) for name, value in parent.items()
             if name not in ("extracted_fields", "text")}
    # The record wins where it has a value; an empty value never erases the
    # parent's shared context (an institution stated once for a list of degrees).
    merged = dict(scalars)
    merged.update({name: value for name, value in record.items()
                   if not _is_empty(value)})
    child["extracted_fields"] = copy.deepcopy(merged)
    child["text"] = _child_text(parent.get("text") or "", index, count, record)
    child["fanned_out_from"] = {"key": key, "index": index, "of": count}
    return child


def _has_list_of_dicts(fields: object) -> bool:
    """Cheap pre-check, so the schema lookup runs only for entries that
    could possibly fan out (nearly none do)."""
    return isinstance(fields, dict) and any(
        isinstance(value, list) and len(value) >= MIN_RECORDS_TO_FAN_OUT
        and all(isinstance(item, dict) for item in value)
        for value in fields.values())


def _fan_out_entry(entry: dict[str, Any],
                   schema_fields: Collection[str]) -> list[dict[str, Any]]:
    fields = entry["extracted_fields"]
    pairs = _record_lists(fields, schema_fields)
    if not pairs:
        return [entry]

    fanned_keys = {key for key, _ in pairs}
    scalars = {name: value for name, value in fields.items()
               if name not in fanned_keys}
    every_record = [rec for _, records in pairs for rec in records]
    keep_parent = _parent_is_a_record(fields, every_record, schema_fields)

    out = []
    shared = scalars
    if keep_parent:
        parent = dict(entry)
        parent["extracted_fields"] = scalars
        out.append(parent)
        # The scalars ARE the first record, not context shared by all of
        # them: a sibling must not inherit its role, dates or type.
        shared = {}
    children = [_make_child(entry, shared, key, index, len(records), record)
                for key, records in pairs
                for index, record in enumerate(records)]
    if not keep_parent:
        missing, total = _unrepresented_segments(entry.get("text") or "", children)
        if total and len(missing) == total:
            return [entry]  # nothing of the text is in the records: leave it whole
        if missing:
            children.append(_residual_entry(entry, missing))
    return out + children


def fan_out_multi_record_entries(
        entries: list[dict[str, Any]],
        schema_fields_for: SchemaFieldsLookup) -> list[dict[str, Any]]:
    """`entries` with each multi-record entry replaced by one child per record.

    `schema_fields_for(code)` returns the field names the renderer for that
    taxonomy code reads. Entries with no qualifying list, and every entry of
    a NESTED_NOT_SIBLING_CODE_PREFIXES code, come back as the same object.
    """
    out: list[dict[str, Any]] = []
    for entry in entries:
        code = str(entry.get("taxonomy_code") or "")
        if (code.startswith(NESTED_NOT_SIBLING_CODE_PREFIXES)
                or not _has_list_of_dicts(entry.get("extracted_fields"))):
            out.append(entry)
            continue
        out.extend(_fan_out_entry(entry, schema_fields_for(code)))
    return out
