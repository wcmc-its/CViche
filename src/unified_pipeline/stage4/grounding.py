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

`text_facts`, `tokens_not_in_own_text` and `entry_holding_token` are the
same checks, field by field, for the doctor's `cross_entry_value` lint, which
reads the values a stored run kept (one definition, CODING_STANDARDS §1.5).

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

# An effort written as a fraction of a full-time post ("4 hours a week/0.100
# FTE", RNKYST 197): a decimal beside "FTE", at most 1, read as a percent.
# Not a whole number ("FTE 2009", "1 FTE" stay unread); the decimal is read
# whole, so "10.5 FTE" is 10.5 and not read, never 0.5.
_FTE_FRACTION = r"(\d*\.\d+)"
_FTE_RE = re.compile(rf"{_FTE_FRACTION}\s*FTE|FTE[\s:]*{_FTE_FRACTION}")
_MAX_FTE_FRACTION = 1.0
_PERCENT_PER_FTE = 100

# A year mistyped by one keystroke: a digit too many ("2009-20011") or one
# digit wrong ("3/31/3019", "1191, 80:224"), read only in one of five date
# shapes, and only where the date is a word of its own: it starts after a
# space, an opening bracket or nothing (not "$3019", "$ 3019", "R01CA3019",
# "M16-3019", "106:3019"), and ends before closing punctuation and a space or
# nothing (not "3019-0004", "3019/45", "3019:", "3019.5", "3019K"). A range
# right after a comma or colon and a space is a page range ("Vol 2,\t1986-2202",
# CKDDEG) and is not read. These guards are lexical: an identifier or amount written in a date
# shape still reads as one, so "Protocol 2018-3019", "IRB 3019-2018",
# "pp. 2018-3019", "Blood 106 2019-3025" (a page range after a space),
# "doi 10.1/x 2018-3019", "£ 3019 - present" and "Budget 3019 - present"
# (an amount with no "$") each ground 2019 or 2025. Only a run of four or
# five digits has a candidate (`_one_keystroke_years`): a cut-off year
# ("May 201") stays missing.
_MISTYPED_RUN = r"(?P<run>\d+)"
_STARTS_A_WORD = r"(?<![^\s(])(?<!\$ )"
_ENDS_A_WORD = r"(?=[.,)]*(?:\s|$))"
_NOT_AFTER_A_VOLUME = r"(?<![,:]\s)"
_MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August",
                "September", "October", "November", "December")
_MONTH = rf"(?:{'|'.join(_MONTH_NAMES + tuple(name[:3] for name in _MONTH_NAMES))}|Sept)\.?"
_DAY = r"\d\d?"
_DASH = r"[-\u2013]"
_FOUR_DIGIT_YEAR = r"(?:19|20)\d\d"
_MISTYPED_YEAR_CONTEXT_RES = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    # "February 20015", "December 9-10, 3013" (not days run together before
    # the year: "June 2325, 1999")
    rf"{_STARTS_A_WORD}{_MONTH}\s+(?:{_DAY}(?:{_DASH}{_DAY})?,?\s+)?{_MISTYPED_RUN}{_ENDS_A_WORD}"
    rf"(?![,\s]*{_FOUR_DIGIT_YEAR})",
    # "3/31/3019", "3/3019"
    rf"{_STARTS_A_WORD}{_DAY}/(?:{_DAY}/)?{_MISTYPED_RUN}{_ENDS_A_WORD}",
    # "2009-20011"; "Chapter of the Year -20218"
    rf"(?:{_STARTS_A_WORD}{_NOT_AFTER_A_VOLUME}{_FOUR_DIGIT_YEAR}\s*|(?<=[a-z] )){_DASH}\s*"
    rf"{_MISTYPED_RUN}{_ENDS_A_WORD}",
    # "20011-2014", "20011 - present"
    rf"{_STARTS_A_WORD}{_NOT_AFTER_A_VOLUME}{_MISTYPED_RUN}\s*{_DASH}\s*"
    rf"(?:{_FOUR_DIGIT_YEAR}|present|current){_ENDS_A_WORD}",
    # a citation's year before its volume: "1191, 80:224-229", "1196;20:108"
    rf"{_STARTS_A_WORD}{_MISTYPED_RUN}[;,]\s*\d+(?:\(\d+\))?:",
))
_FIVE_DIGIT_RUN = 5
_WRITTEN_AS_A_YEAR_RE = re.compile(r"(?:18|19|20)\d\d")
# A dollar amount, removed before the "year run into other digits" rule only
# ("$19900" does not state 1990 through that rule). The `year[2:]` /
# `year[1:]` digit-run rules still read amounts, as on dev ("$19,900" still
# states 2019, "$90,000" 1990), and so does every rule for an amount with
# another currency sign or none ("£19900", "19900 USD").
_CURRENCY_AMOUNT_RE = re.compile(r"\$\s*[\d,.]+")


class TextFacts(NamedTuple):
    """The checkable tokens of one entry's text. `runs_outside_amounts`,
    `mistyped_years` and `fte_percents` serve the own-text reading only
    (#1840); another entry's text is read strictly, without them."""

    years: frozenset[str]
    digit_runs: frozenset[str]
    numbers: frozenset[str]
    percents: frozenset[str]
    alnum: str
    runs_outside_amounts: frozenset[str]
    mistyped_years: frozenset[str]
    fte_percents: frozenset[str]


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


def _percent_key(number: float) -> str:
    """A percent as one spelling, so "10", "10.0" and 0.100 FTE compare equal."""
    return f"{number:g}"


def _fte_percents(text: str) -> frozenset[str]:
    fractions = (float(left or right) for left, right in _FTE_RE.findall(text))
    return frozenset(_percent_key(fraction * _PERCENT_PER_FTE)
                     for fraction in fractions if fraction <= _MAX_FTE_FRACTION)


def _one_keystroke_years(run: str) -> set[str]:
    """The four-digit years `run` is one keystroke from: a digit dropped from
    five ("20011" -> 2001, 2011), or one digit changed in four that are not
    already a year, a nineteenth-century one included ("1879-1928" is a
    lifespan), and do not start with 0 ("2020-0927" is a protocol number):
    "3019" -> 2019."""
    if len(run) == _FIVE_DIGIT_RUN:
        candidates = {run[:i] + run[i + 1:] for i in range(len(run))}
    elif _WRITTEN_AS_A_YEAR_RE.fullmatch(run) or run.startswith("0"):
        return set()
    else:
        candidates = {run[:i] + digit + run[i + 1:] for i in range(len(run)) for digit in "0123456789"}
    return {candidate for candidate in candidates if _YEAR_RE.fullmatch(candidate)}


def _mistyped_years(text: str) -> frozenset[str]:
    runs = {match.group("run") for pattern in _MISTYPED_YEAR_CONTEXT_RES
            for match in pattern.finditer(text)}
    return frozenset().union(*map(_one_keystroke_years, runs))


def text_facts(text: str) -> TextFacts:
    return TextFacts(
        years=frozenset(_YEAR_RE.findall(text)) | _short_range_ends(text),
        digit_runs=frozenset(_DIGIT_RUN_RE.findall(text)),
        runs_outside_amounts=frozenset(_DIGIT_RUN_RE.findall(_CURRENCY_AMOUNT_RE.sub(" ", text))),
        numbers=_number_tokens(text),
        percents=frozenset(_PERCENT_RE.findall(text)),
        alnum=_NOT_ALNUM_RE.sub("", text.upper()),
        mistyped_years=_mistyped_years(text),
        fte_percents=_fte_percents(text),
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


def _year_in_own_text(year: str, own: TextFacts) -> bool:
    """Read generously, so a year the entry does state is never called
    borrowed: a short range end ("2004-6"), two digits ("04/21/17",
    "3-30-00"), a mistyped first digit ("l988" reads as the run "988"), or a
    year run into the digits beside it ("172:7512022", "202526;8(4)") but
    not into a dollar amount ("$19900"; only this rule skips amounts, the
    two- and three-digit rules still read them), or a year mistyped by one
    keystroke in a date shape ("2009-20011", "3/31/3019";
    `_mistyped_years`, whose guards are lexical: see `_MISTYPED_RUN`)."""
    return (year in own.years
            or year[2:] in own.digit_runs
            or year[1:] in own.digit_runs
            or any(year in run for run in own.runs_outside_amounts)
            or year in own.mistyped_years)


def _in_own_text(field: str, token: str, own: TextFacts) -> bool:
    """Whether the entry's own text holds `token`, read generously (see
    `_year_in_own_text`); an amount or percent anywhere counts, and an effort
    the entry writes as a fraction of a full-time post ("0.100 FTE" is 10%)."""
    if field in YEAR_FIELDS:
        return _year_in_own_text(token, own)
    if field in IDENTIFIER_FIELDS:
        return token in own.alnum
    if field in PERCENT_FIELDS and _percent_key(float(token)) in own.fte_percents:
        return True
    return token in own.numbers


def _in_other_text(field: str, token: str, other: TextFacts) -> bool:
    """Whether another entry's text holds `token`, read strictly: a four-digit
    year, an amount, a number written as a percent, a whole grant number."""
    if field in YEAR_FIELDS:
        return token in other.years
    if field in PERCENT_FIELDS:
        return token in other.percents
    if field in IDENTIFIER_FIELDS:
        return token in other.alnum
    return token in other.numbers


def tokens_not_in_own_text(field: str, value: object, own: TextFacts) -> tuple[list[str], bool]:
    """`(tokens of value its own entry's text lacks, whether any token is
    its own)`; both empty/False for a field grounding does not check."""
    missing = []
    any_own = False
    for token in _value_tokens(field, value):
        if _in_own_text(field, token, own):
            any_own = True
        else:
            missing.append(token)
    return missing, any_own


def entry_holding_token(field: str, token: str, others: list[TextFacts]) -> int | None:
    """The position in `others` of the first entry whose text states `token`
    (read strictly, `_in_other_text`), or None."""
    return next((i for i, other in enumerate(others) if _in_other_text(field, token, other)), None)


class _ItemCheck(NamedTuple):
    borrowed: list[str]
    any_own_token: bool


def _check_item(item: dict[str, Any], own: TextFacts,
                others: list[TextFacts]) -> _ItemCheck:
    """Which of `item`'s fields hold a token absent from its own entry's text
    but present in another entry's, and whether any checked token is its own."""
    borrowed = []
    any_own_token = False
    for field, value in item.items():
        missing, any_own = tokens_not_in_own_text(field, value, own)
        any_own_token = any_own_token or any_own
        if any(entry_holding_token(field, token, others) is not None for token in missing):
            borrowed.append(field)
    return _ItemCheck(borrowed, any_own_token)


def _ground_entry(items: list[dict[str, Any]], own: TextFacts, others: list[TextFacts],
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
    facts = [text_facts(text) for text in entry_texts]
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
