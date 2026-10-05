"""Lint for office contact values that do not reach their Personal Data row.

One responsibility: read stage 4's Personal Data (A) entries and the rendered
Personal Data table, and report an office phone or office address the entries
hold that the table's Office row does not show (EBYSBC E25). It reads the
label beside each value in the entry, not stage 6's routing, so a value stage
6 sent to the wrong row or withheld as home is still reported.

Home and personal values are withheld on purpose (#821) and are never
expected. A value is skipped whenever its label, or the whole block's, says
home, cell, fax or pager, and an address is skipped whenever the block says
home anywhere, the line is a birth line, or the value names no street,
number or state (a department, #1222).
"""
import re
from collections.abc import Mapping
from typing import Any

from unified_pipeline.stage6.sections.personal_data import (
    _BIRTH_WORD_RE,
    _PHONE_NUMBER_PATTERN,
    _home_and_office_parts,
    _names_a_street_or_number,
    _offschema_contact,
)

from ..shared import _finding

_PERSONAL_DATA_CODE = "A"
# The Personal Data table's row labels (the WCM template's first column).
_OFFICE_ADDRESS_ROW = "office address"
_OFFICE_PHONE_ROW = "office telephone"
_PHONE_FIELD = "phone"
_ADDRESS_FIELD = "address"
# `_offschema_contact`'s slot names.
_OFFSCHEMA_OFFICE_PHONE = "office"
_OFFSCHEMA_OFFICE_ADDRESS = "office_address"
_LABEL_KIND_OFFICE = "office"
_LABEL_KIND_OTHER = "other"

_PHONE_RE = re.compile(_PHONE_NUMBER_PATTERN)
_NON_DIGIT_RE = re.compile(r"\D")
# Numbers are compared on their last seven digits: a country code, a leading
# 1 or an extension stage 6 drops does not make a number another number.
_PHONE_DIGITS_COMPARED = 7
# A label in the text before a number is read this far back, never past
# the number before it, a ';' or a newline, and from the last tab-separated
# piece that is not blank: a tab ends the line before in a contact block, so
# "Department of Stem Cell Biology<TAB>Phone: <n>" does not label the number
# 'cell' (the #1222 shape stage 6 had), while "Cell phone:<TAB><n>" does.
_LABEL_LOOKBACK_CHARS = 30
_LABEL_SEGMENT_SPLIT_RE = re.compile(r"[;\n]")
# "<number> (cell)": a parenthesised label written right after the number.
_TRAILING_LABEL_RE = re.compile(r"^\s*\(([^)]{1,20})\)")
# A number labelled any of these is not the office number. Letter labels
# "(h)", "(c)", "(m)", "(f)" are what "Ph (h):" and "<number> (c)" use.
_NOT_OFFICE_LABEL_RE = re.compile(
    r"\b(?:home|residence|residential|cell(?:ular)?|mobile|mob|fax|pager|beeper|personal)\b"
    r"|\([hcmf]\)", re.IGNORECASE)
_HOME_LABEL_RE = re.compile(r"\b(?:home|residence|residential)\b|\(h\)", re.IGNORECASE)
_WORK_LABEL_RE = re.compile(r"\b(?:office|work|business)\b|\([wo]\)", re.IGNORECASE)
# Address words compared with the Office address cell: numbers of two or
# more digits and words of five or more letters; the value is shown when at
# least half of them are.
_ADDRESS_TOKEN_RE = re.compile(r"\d{2,}|[^\W\d_]{5,}")
_ADDRESS_TOKENS_SHOWN_MIN = 0.5
# The "Office:" label each half of a "Home: ... Office: ..." value opens with.
_LEADING_LABEL_RE = re.compile(r"^\s*[^\W\d_]+(?:\s+address)?\s*:", re.IGNORECASE)


def _digits(value: str) -> str:
    return _NON_DIGIT_RE.sub("", value)


def _personal_data_rows(table_rows: list[list[list[str]]]) -> dict[str, str] | None:
    """{row label: value} of the rendered Personal Data table, labels lower
    case without their colon; None when the document has no such table."""
    for table in table_rows:
        labels = [row[0].strip().lower() for row in table if row]
        if any(label.startswith(_OFFICE_PHONE_ROW) for label in labels):
            return {row[0].strip().rstrip(":").lower(): " ".join(row[1:])
                    for row in table if row}
    return None


def _number_label(digits: str, text: str) -> str | None:
    """`_label_kind` of the number in `text` whose digits end like `digits`:
    of the label written right after it in parentheses, else of the text
    before it; None when the text does not hold the number."""
    tail = digits[-_PHONE_DIGITS_COMPARED:]
    previous_end = 0
    for match in _PHONE_RE.finditer(text):
        if not _digits(match.group()).endswith(tail):
            previous_end = match.end()
            continue
        trailing = _TRAILING_LABEL_RE.match(text[match.end():])
        if trailing:
            return _label_kind(trailing.group())
        before = text[max(previous_end, match.start() - _LABEL_LOOKBACK_CHARS):match.start()]
        pieces = [p for p in _LABEL_SEGMENT_SPLIT_RE.split(before)[-1].split("\t") if p.strip()]
        return _label_kind(pieces[-1]) if pieces else None
    return None


def _label_kind(label: str) -> str | None:
    """'other' for a home, cell, fax or pager label, 'office' for an office,
    work or business one, None for none or a bare 'Phone:'."""
    if _NOT_OFFICE_LABEL_RE.search(label):
        return _LABEL_KIND_OTHER
    return _LABEL_KIND_OFFICE if _WORK_LABEL_RE.search(label) else None


def _office_numbers(fields: Mapping[str, Any], text: str) -> list[str]:
    """Digits of each office number one A entry holds: every number of its
    `phone` value whose label is not home, cell, fax or pager, plus the
    off-schema office phone (#1222). An unlabelled number in a block whose
    only label is home is the home number."""
    home_block = bool(_HOME_LABEL_RE.search(text)) and not _WORK_LABEL_RE.search(text)
    numbers = []
    values = fields.get(_PHONE_FIELD)
    for value in values if isinstance(values, list) else [values]:
        if not isinstance(value, str):
            continue
        for match in _PHONE_RE.finditer(value):
            digits = _digits(match.group())
            label = _number_label(digits, value) or _number_label(digits, text)
            if label == _LABEL_KIND_OTHER or (label is None and home_block):
                continue
            numbers.append(digits)
    offschema = _offschema_contact(dict(fields), []).phone or {}
    if offschema.get(_OFFSCHEMA_OFFICE_PHONE):
        numbers.append(_digits(offschema[_OFFSCHEMA_OFFICE_PHONE]))
    return [digits for digits in numbers if len(digits) >= _PHONE_DIGITS_COMPARED]


def _office_addresses(fields: Mapping[str, Any], text: str) -> list[str]:
    """Each office address one A entry holds: the office half of a 'Home:
    ... Office: ...' value, else the `address` value or the off-schema office
    address (#1222), unless the block says home, is a birth line, or the
    value names no street, number or state."""
    address = fields.get(_ADDRESS_FIELD)
    if isinstance(address, str) and address.strip():
        parts = _home_and_office_parts(address)
        if parts:
            return [_LEADING_LABEL_RE.sub("", part) for kind, part in parts
                    if kind == _LABEL_KIND_OFFICE]
    else:
        address = (_offschema_contact(dict(fields), []).address or {}).get(_OFFSCHEMA_OFFICE_ADDRESS)
    if not isinstance(address, str) or not address.strip():
        return []
    if _HOME_LABEL_RE.search(text) or _BIRTH_WORD_RE.search(text):
        return []
    return [address] if _names_a_street_or_number(address) else []


def _address_shown(address: str, cell: str) -> bool:
    tokens = _ADDRESS_TOKEN_RE.findall(address.lower())
    if not tokens:
        return True
    shown = sum(1 for token in tokens if token in cell.lower())
    return shown / len(tokens) >= _ADDRESS_TOKENS_SHOWN_MIN


def _lost_contact(fields: Mapping[str, Any], text: str, rows: Mapping[str, str]) -> list[str]:
    """What one A entry's office contact lost: 'office phone ending NNNN in
    <where>' and 'office address in <where>' items."""
    office_phone = _digits(rows.get(_OFFICE_PHONE_ROW, ""))
    every_row = " ".join(rows.values())
    lost = []
    for digits in dict.fromkeys(_office_numbers(fields, text)):
        tail = digits[-_PHONE_DIGITS_COMPARED:]
        if tail in office_phone:
            continue
        where = "another Personal Data row" if tail in _digits(every_row) else "no Personal Data row"
        lost.append(f"office phone ending {digits[-4:]} is in {where}")
    for address in _office_addresses(fields, text):
        if _address_shown(address, rows.get(_OFFICE_ADDRESS_ROW, "")):
            continue
        where = "another Personal Data row" if _address_shown(address, every_row) else "no Personal Data row"
        lost.append(f"office address is in {where}")
    return lost


def lint_contact_slot_lost(stage4: dict, table_rows: list[list[list[str]]]) -> list[dict]:
    """Office phones and addresses stage 4 extracted for Personal Data that
    the rendered Office telephone or Office address row does not show
    (EBYSBC E25): a value routed to another row, withheld as home, or under
    an off-schema key nothing read. One WARN per A entry. Nothing is
    reported when the document has no Personal Data table."""
    rows = _personal_data_rows(table_rows)
    if rows is None:
        return []
    findings = []
    for entry in stage4.get("entries") or []:
        if entry.get("taxonomy_code") != _PERSONAL_DATA_CODE:
            continue
        lost = _lost_contact(entry.get("extracted_fields") or {}, str(entry.get("text") or ""), rows)
        if lost:
            findings.append(_finding(
                "contact_slot_lost", "WARN",
                f"entry {entry.get('element_idx_start')} (A): {'; '.join(lost)} -- "
                f"the Office row does not show it", lost))
    return findings
