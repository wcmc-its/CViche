"""Cross-entry grounding of one taxonomy-code group's reply (#1575).

Stage 4 extracts every entry of a code group in one call. The model sometimes
gives an entry a value it read from ANOTHER entry of the same call: UVZNIC
592, an active R01, came back with entry 603's dates, amount and effort, and
UVZNIC 559 came back with a second record that was entry 573's. Nothing
checked a reply item against its own entry, so the borrowed values rendered
as fact.

`ground_group` checks the values that can be checked by their characters
(years, amounts, percents, grant numbers) against each entry's own text. A
value that its own entry does not contain but another entry of the group does
is borrowed. Names, roles and titles are not checked: a role word or an
organisation legitimately repeats across entries.

Pure: takes data, returns data, imports no I/O (CODING_STANDARDS §1.2).
"""

import re
from typing import Any, NamedTuple

# ponytail: fixed field lists, not the schemas' field types (they have none).
# A new date, amount, percent or identifier field is unchecked until it is
# named here; the check then only misses a borrowed value, never invents one.
YEAR_FIELDS = frozenset({
    "date", "start_date", "end_date", "effective_date", "expiration_date",
    "filing_date", "issue_date", "recertification_date", "submission_date",
    "year", "year_certified",
})
AMOUNT_FIELDS = frozenset({
    "amount", "annual_funding", "total_funding", "total_funding_requested",
})
PERCENT_FIELDS = frozenset({
    "percent_effort", "fte_percentage", "admin_percent", "clinical_percent",
    "research_percent", "teaching_percent",
})
IDENTIFIER_FIELDS = frozenset({"grant_number"})

# A grant number shorter than this (after dropping punctuation) is too likely
# to occur by chance inside another entry's text to call it borrowed.
_MIN_IDENTIFIER_CHARS = 5

_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d\d)(?!\d)")
_DIGIT_RUN_RE = re.compile(r"\d+")
# "2004-6", "2017-18": a range whose end year is written short.
_SHORT_RANGE_RE = re.compile(r"(?<!\d)((?:19|20)\d\d)\s*[-\u2013/]\s*(\d{1,2})(?!\d)")
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_NOT_ALNUM_RE = re.compile(r"[^0-9A-Z]")


class _TextFacts(NamedTuple):
    """The checkable tokens of one entry's text."""

    years: frozenset[str]
    digit_runs: frozenset[str]
    numbers: frozenset[str]
    percents: frozenset[str]
    alnum: str


class GroupGrounding(NamedTuple):
    """`ground_group`'s result.

    items: the reply items by entry index, with borrowed fields set to None
        and wholly borrowed records removed.
    borrowed_fields: entry index -> the fields set to None, for each entry
        that had one.
    dropped_records: entry index -> the number of records removed.
    """

    items: dict[int, list[dict[str, Any]]]
    borrowed_fields: dict[int, list[str]]
    dropped_records: dict[int, int]


def _number_tokens(text: str) -> frozenset[str]:
    return frozenset(match.replace(",", "") for match in _NUMBER_RE.findall(text))


def _short_range_ends(text: str) -> set[str]:
    return {start[:4 - len(end)] + end for start, end in _SHORT_RANGE_RE.findall(text)}


def _text_facts(text: str) -> _TextFacts:
    return _TextFacts(
        years=frozenset(_YEAR_RE.findall(text)) | _short_range_ends(text),
        digit_runs=frozenset(_DIGIT_RUN_RE.findall(text)),
        numbers=_number_tokens(text),
        percents=frozenset(_PERCENT_RE.findall(text)),
        alnum=_NOT_ALNUM_RE.sub("", text.upper()),
    )


def _value_tokens(field: str, value: object) -> list[str]:
    """The tokens of `value` that grounding checks; none for an unchecked field."""
    if value is None or isinstance(value, (dict, list)):
        return []
    text = str(value)
    if field in YEAR_FIELDS:
        return _YEAR_RE.findall(text)
    if field in AMOUNT_FIELDS or field in PERCENT_FIELDS:
        return sorted(_number_tokens(text))
    if field in IDENTIFIER_FIELDS:
        identifier = _NOT_ALNUM_RE.sub("", text.upper())
        return [identifier] if len(identifier) >= _MIN_IDENTIFIER_CHARS else []
    return []


def _year_in_own_text(year: str, own: _TextFacts) -> bool:
    """Read generously, so a year the entry does state is never called
    borrowed: a short range end ("2004-6"), two digits ("04/21/17",
    "3-30-00"), a mistyped first digit ("l988" reads as the run "988"), or a
    year run into the digits beside it ("172:7512022", "202526;8(4)")."""
    return (year in own.years
            or year[2:] in own.digit_runs
            or year[1:] in own.digit_runs
            or any(year in run for run in own.digit_runs))


def _in_own_text(field: str, token: str, own: _TextFacts) -> bool:
    """Whether the entry's own text holds `token`, read generously (see
    `_year_in_own_text`); an amount or percent anywhere counts."""
    if field in YEAR_FIELDS:
        return _year_in_own_text(token, own)
    if field in IDENTIFIER_FIELDS:
        return token in own.alnum
    return token in own.numbers


def _in_other_text(field: str, token: str, other: _TextFacts) -> bool:
    """Whether another entry's text holds `token`, read strictly: a four-digit
    year, an amount, a number written as a percent, a whole grant number."""
    if field in YEAR_FIELDS:
        return token in other.years
    if field in PERCENT_FIELDS:
        return token in other.percents
    if field in IDENTIFIER_FIELDS:
        return token in other.alnum
    return token in other.numbers


class _ItemCheck(NamedTuple):
    borrowed: list[str]
    any_own_token: bool


def _check_item(item: dict[str, Any], own: _TextFacts,
                others: list[_TextFacts]) -> _ItemCheck:
    """Which of `item`'s fields hold a token absent from its own entry's text
    but present in another entry's, and whether any checked token is its own."""
    borrowed = []
    any_own_token = False
    for field, value in item.items():
        missing = []
        for token in _value_tokens(field, value):
            if _in_own_text(field, token, own):
                any_own_token = True
            else:
                missing.append(token)
        if any(_in_other_text(field, token, other) for token in missing for other in others):
            borrowed.append(field)
    return _ItemCheck(borrowed, any_own_token)


def _ground_entry(items: list[dict[str, Any]], own: _TextFacts, others: list[_TextFacts],
                  ) -> tuple[list[dict[str, Any]], list[str], int]:
    """`(items, nulled fields, records dropped)` for one entry.

    A record none of whose checked values is the entry's own, and some of
    which are another entry's, is that other entry's record (UVZNIC 559 held
    573's mentee as a second record): it is removed, as long as a record of
    the entry's own remains. In every record left, a borrowed field is set to
    None so no borrowed value can render.
    """
    checks = [_check_item(item, own, others) for item in items]
    foreign = [check.borrowed and not check.any_own_token for check in checks]
    if any(foreign) and not all(foreign):
        kept = [(item, check) for item, check, is_foreign in zip(items, checks, foreign, strict=True)
                if not is_foreign]
    else:
        kept = list(zip(items, checks, strict=True))
    nulled = sorted({field for _, check in kept for field in check.borrowed})
    grounded = [{**item, **dict.fromkeys(check.borrowed)} for item, check in kept]
    return grounded, nulled, len(items) - len(kept)


def ground_group(entry_texts: list[str], items_by_entry: dict[int, list[dict[str, Any]]],
                 ) -> GroupGrounding:
    """Remove the values each entry's reply items borrowed from another entry
    of the same group (see `_ground_entry`). `entry_texts[i]` is the text the
    prompt showed for entry i. An index outside the group is passed through
    untouched: `_extraction_map` owns those (#1243)."""
    facts = [_text_facts(text) for text in entry_texts]
    items: dict[int, list[dict[str, Any]]] = {}
    borrowed_fields: dict[int, list[str]] = {}
    dropped_records: dict[int, int] = {}
    for index, entry_items in items_by_entry.items():
        if not 0 <= index < len(facts):
            items[index] = entry_items
            continue
        others = facts[:index] + facts[index + 1:]
        items[index], nulled, dropped = _ground_entry(entry_items, facts[index], others)
        if nulled:
            borrowed_fields[index] = nulled
        if dropped:
            dropped_records[index] = dropped
    return GroupGrounding(items, borrowed_fields, dropped_records)
