"""Expand a stage-4 list of records into one entry per record (#983).

Stage 4 sometimes returns several sibling records from ONE stage-2 entry,
stored as a list of dicts under a key the code's schema does not define
(``{"awards": [{...}, {...}]}`` on an H entry, ``{"committees": [...]}`` on a
P entry). Every section renderer reads the schema's own keys only, so the
whole list is invisible to it and the entry falls back to its raw text: one
row, tabs and all.

`fan_out_multi_record_entries` replaces such an entry with one child per
record, each carrying the record's fields under the schema keys the renderer
already reads and ITS OWN paragraph of the source text as `text`. It does so
only when that pairing can be checked: the entry's text must split into one
paragraph per record (or one more, a leading paragraph for the parent's own
fields), and each record must share more of its words with its own paragraph
than with any other. Otherwise the entry is left whole and renders as it did before (through the
tab-free, untruncated raw fallback). Every paragraph goes to exactly one
output entry, verbatim: no entry's text is dropped or repeated, and none is
rebuilt from field values (a rebuilt text repeated dates and lost the context
a renderer prints). What a renderer then shows of an entry is the renderer's
business, as it is for any entry of that code; a record with a value under a
key the schema does not define (`_is_sibling_record`) is not fanned out for
that reason, since the raw text is the only place such a value would show.

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
# A record that holds only a date is a part of the entry, not a sibling record:
# it names no thing of its own.
DATE_KEY_SUFFIXES = ("_date", "_dates", "_year")
DATE_KEYS = frozenset({"date", "dates", "year", "dates_attended"})

# A stage-2 paragraph range is joined with a tab; a newline is a break inside one.
_SEGMENT_SPLIT_RE = re.compile(r"[\t\n]")

SchemaFieldsLookup = Callable[[str], Collection[str]]


def _is_empty(value: object) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _is_date_key(key: str) -> bool:
    return key.endswith(DATE_KEY_SUFFIXES) or key in DATE_KEYS


def _is_sibling_record(item: object, schema_fields: Collection[str]) -> bool:
    """A dict that names a non-date schema field and holds no value under any
    other key. A value under a key the schema does not define (a K4 session's
    `title`, an S3 chapter's `chapter`) is one the renderer cannot show, so
    the raw text is the only place it survives: that entry stays whole."""
    if not isinstance(item, dict):
        return False
    held = [key for key, value in item.items() if not _is_empty(value)]
    return (all(key in schema_fields for key in held)
            and any(not _is_date_key(key) for key in held))


def _record_lists(fields: dict[str, Any],
                  schema_fields: Collection[str]) -> list[tuple[str, list[dict]]]:
    """The (key, records) pairs in `fields` that should fan out: the key is
    not a schema field, and the value is MIN_RECORDS_TO_FAN_OUT or more dicts
    that are each a sibling record."""
    return [(key, value) for key, value in fields.items()
            if key not in schema_fields
            and isinstance(value, list) and len(value) >= MIN_RECORDS_TO_FAN_OUT
            and all(_is_sibling_record(item, schema_fields) for item in value)]


def _share_of_record_in(segment: str, record: dict[str, Any]) -> float:
    """The share of the record's significant words that `segment` contains.
    Dates are left out: records of one list often share a year, and a shared
    year would pull a record toward a neighbour's paragraph."""
    record_words: set[str] = set()
    for key, value in record.items():
        if not _is_empty(value) and not _is_date_key(key):
            record_words |= _significant_words(str(value))
    if not record_words:
        return 0.0
    return len(record_words & _significant_words(segment)) / len(record_words)


def _is_best_match(index: int, segments: list[str], record: dict[str, Any]) -> bool:
    """True when the record shares words with its own paragraph, and no other
    paragraph shares a larger part of them. Unlike a fixed share threshold it
    can disagree with the positional pairing: on the corpus a record's own
    share runs from 0.17 to 1.0 and a neighbour's reaches 0.67, so no single
    cut-off separates them."""
    shares = [_share_of_record_in(segment, record) for segment in segments]
    return shares[index] > 0 and shares[index] >= max(shares)


def _pair_segments(text: str,
                   records: list[dict]) -> tuple[str | None, list[str]] | None:
    """(the parent's own leading paragraph or None, one paragraph per record),
    or None when the text does not pair with the records."""
    segments = [seg.strip() for seg in _SEGMENT_SPLIT_RE.split(text) if seg.strip()]
    lead = None
    if len(segments) == len(records) + 1:
        lead, segments = segments[0], segments[1:]
    if len(segments) != len(records):
        return None
    if not all(_is_best_match(index, segments, record)
               for index, record in enumerate(records)):
        return None
    return lead, segments


def _make_child(parent: dict[str, Any], shared: dict[str, Any], key: str,
                index: int, count: int, record: dict[str, Any],
                segment: str) -> dict[str, Any]:
    child = {name: copy.deepcopy(value) for name, value in parent.items()
             if name not in ("extracted_fields", "text")}
    # The record wins where it has a value; an empty value never erases the
    # parent's shared context (an institution stated once for a list of degrees).
    merged = dict(shared)
    merged.update({name: value for name, value in record.items()
                   if not _is_empty(value)})
    child["extracted_fields"] = copy.deepcopy(merged)
    child["text"] = segment
    child["fanned_out_from"] = {"key": key, "index": index, "of": count}
    return child


def _fan_out_entry(entry: dict[str, Any],
                   schema_fields: Collection[str]) -> list[dict[str, Any]]:
    fields = entry["extracted_fields"]
    pairs = _record_lists(fields, schema_fields)
    if len(pairs) != 1:
        return [entry]  # nothing to expand, or two lists one text cannot pair with
    key, records = pairs[0]
    paired = _pair_segments(entry.get("text") or "", records)
    if paired is None:
        return [entry]
    lead, segments = paired

    scalars = {name: value for name, value in fields.items() if name != key}
    out = []
    shared = scalars
    if lead is not None:
        # The scalars ARE a record of their own (the first one, stated in the
        # parent's fields, with the REST in the list): keep it, with its own
        # paragraph, and do not let a sibling inherit its role, dates or type.
        parent = dict(entry)
        parent["extracted_fields"] = scalars
        parent["text"] = lead
        out.append(parent)
        shared = {}
    out.extend(_make_child(entry, shared, key, index, len(records), record, segment)
               for index, (record, segment) in enumerate(zip(records, segments)))
    return out


def fan_out_multi_record_entries(
        entries: list[dict[str, Any]],
        schema_fields_for: SchemaFieldsLookup) -> list[dict[str, Any]]:
    """`entries` with each multi-record entry replaced by one child per record.

    `schema_fields_for(code)` returns the field names the renderer for that
    taxonomy code reads (empty for a code with no schema of its own). Entries
    with no qualifying list, entries whose text does not pair with their
    records, and every entry of a NESTED_NOT_SIBLING_CODE_PREFIXES code come
    back as the same object.
    """
    out: list[dict[str, Any]] = []
    for entry in entries:
        code = str(entry.get("taxonomy_code") or "")
        fields = entry.get("extracted_fields")
        if (code.startswith(NESTED_NOT_SIBLING_CODE_PREFIXES)
                or not isinstance(fields, dict)
                or not any(isinstance(value, list) for value in fields.values())):
            out.append(entry)
            continue
        out.extend(_fan_out_entry(entry, schema_fields_for(code)))
    return out
