"""Year-group dating for stage 4 (class E26, 2026-10-04 NDMRSO autopsy: GCFEBE).

Some CVs list talks under a year, one "MM-DD: <title>" line per talk:

    2016:  03-16: <title>
    05-11: <title>
    09-14: <title>

Stage 2 makes one entry per line, so only the first line of each year carries
the year. The stage-4 LLM reads a later line's "MM-DD" token as a month and a
two-digit year ("09-14" -> 2014-09, "12-19" -> 2012-19) and the per-record
post-checks cannot catch it: the token's digits are in the text. The year that
governs the line is in the group's first entry, which only a pass over the
entries in document order can see. `date_year_group_members` is that pass.

Pure and offline: no LLM, no I/O. Imports nothing from the other stage-4
modules (the multi-record key comes in as an argument), and never mutates
its input.
"""

import re
from typing import Any

#: The first entry of a year group: a four-digit year, a colon, then the
#: group's first "MM-DD" token and its colon ("2016:  03-16: <title>").
_GROUP_HEAD_PATTERN = re.compile(
    r'\s*((?:19|20)\d{2})\s*:\s+(\d{1,2})-(\d{1,2})\s*:')

#: A later entry of a year group: its "MM-DD" token and colon alone.
_GROUP_MEMBER_PATTERN = re.compile(r'\s*(\d{1,2})-(\d{1,2})\s*:')

_LAST_MONTH = 12
_LAST_DAY_OF_MONTH = 31

#: The date fields a teaching line's one date is stored in.
_ONE_DATE_FIELD_NAMES = ('date', 'start_date')
_END_DATE_FIELD_NAME = 'end_date'

#: A four-digit year inside a stored date value.
_STORED_YEAR_PATTERN = re.compile(r'(?<!\d)(\d{4})(?!\d)')

_REDATED_REASON = "Re-dated an 'MM-DD' line from its year group's heading year"
_CLEARED_REASON = "Cleared an end date read from an 'MM-DD' line's own token"


def _month_day(month: str, day: str) -> tuple[int, int] | None:
    """`(month, day)` when the two numbers can be a month and its day."""
    if 1 <= int(month) <= _LAST_MONTH and 1 <= int(day) <= _LAST_DAY_OF_MONTH:
        return int(month), int(day)
    return None


def _token_year(value: object, month_day: tuple[int, int], group_year: str,
                text: str) -> bool:
    """True when `value` holds a year other than `group_year` whose last two
    digits are the line's month or day: the "MM-DD" token read as a year.
    A year the line writes out in full is the line's own and stays."""
    if not isinstance(value, str):
        return False
    token_digits = {f'{part:02d}' for part in month_day}
    return any(
        year != group_year and year[2:] in token_digits
        and not re.search(rf'(?<!\d){year}(?!\d)', text)
        for year in _STORED_YEAR_PATTERN.findall(value))


def _redate_record(record: dict[str, Any], month_day: tuple[int, int],
                   group_year: str, text: str) -> dict[str, dict[str, Any]]:
    """Rewrite `record`'s token-read dates in place: the line's one date
    becomes the group year with the token's month and day, and an end date
    read from the token is cleared (a line names one day). Returns what
    changed, keyed by field, in `reformatted_fields`' shape."""
    changes: dict[str, dict[str, Any]] = {}
    month, day = month_day
    for field_name in (*_ONE_DATE_FIELD_NAMES, _END_DATE_FIELD_NAME):
        value = record.get(field_name)
        if not _token_year(value, month_day, group_year, text):
            continue
        if field_name == _END_DATE_FIELD_NAME:
            repaired, reason = None, _CLEARED_REASON
        else:
            repaired, reason = f'{group_year}-{month:02d}-{day:02d}', _REDATED_REASON
        record[field_name] = repaired
        changes[field_name] = {
            'original': value, 'reformatted': repaired or '', 'reason': reason}
    return changes


def _redate_entry(entry: dict[str, Any], month_day: tuple[int, int],
                  group_year: str, records_key: str) -> dict[str, Any] | None:
    """A copy of `entry` with its records re-dated, or None when no record
    holds a token-read year. Only the entry's own fields are reported in
    `reformatted_fields`, as stage 4's other repairs report them."""
    text = entry.get('text') or ''
    fields = dict(entry.get('extracted_fields') or {})
    changes = _redate_record(fields, month_day, group_year, text)
    changed = bool(changes)
    records = fields.get(records_key)
    if isinstance(records, list):
        records = [dict(record) if isinstance(record, dict) else record for record in records]
        for record in records:
            if isinstance(record, dict) and _redate_record(record, month_day, group_year, text):
                changed = True
        fields[records_key] = records
    if not changed:
        return None
    redated = dict(entry)
    redated['extracted_fields'] = fields
    if changes:
        redated['reformatted_fields'] = {**(entry.get('reformatted_fields') or {}), **changes}
    return redated


def date_year_group_members(entries: list[dict[str, Any]],
                            records_key: str) -> list[dict[str, Any]]:
    """`entries` with each year-group member's token-read year replaced by
    its group's year (see the module docstring).

    `entries` must be in document order. A group starts at an entry whose
    text opens "YYYY: MM-DD:" and runs over the following entries under the
    same hierarchy whose text opens "MM-DD:"; any other entry ends it. Only a
    year whose last two digits are the member's own month or day, and that
    the member does not write in full, is changed: a member with no year, or
    with the group's year, is left as it is. Changed entries are copies.
    `records_key` is the key a multi-record entry keeps its records under;
    each of them is re-dated the same way.
    """
    out: list[dict[str, Any]] = []
    group: tuple[str, list[Any]] | None = None
    for entry in entries:
        text = entry.get('text') or ''
        hierarchy = list(entry.get('hierarchy') or [])
        head = _GROUP_HEAD_PATTERN.match(text)
        member = _GROUP_MEMBER_PATTERN.match(text)
        month_day = _month_day(*member.groups()) if member else None
        if head and _month_day(head.group(2), head.group(3)):
            group = (head.group(1), hierarchy)
        elif group is not None and month_day is not None and hierarchy == group[1]:
            out.append(_redate_entry(entry, month_day, group[0], records_key) or entry)
            continue
        else:
            group = None
        out.append(entry)
    return out
