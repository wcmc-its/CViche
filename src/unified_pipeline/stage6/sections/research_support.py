"""Section M2: research support -- current, past and pending funding (#398).

Unlike every other section here, M2 does not render into one template table. It
builds an individual label/value table per grant under the M2A/M2B/M2C headers,
which is why `_create_grant_table` is the largest thing in the module.

The section also re-buckets its own records. A grant coded M2A whose end date is
already past belongs under Past (Completed) Funding, and one whose status text
reads "Under review" belongs under Pending regardless of its dates. The status
rule lives in `normalization.grant_status_rebucket_target`, imported below; the
date rule is `reclassify_past_m2a_grants` here, and either move is annotated on
the grant as a comment.

Those rules, and the percent-effort rules above them, are module-level functions
taking plain data and returning plain data -- no `self`, no python-docx, and no
writes back into the records the caller handed in, which arrive copied
(`copy_entries_for_render`). `_fill_research_support` is the rendering half and
calls them. The verbose lines are a parsed contract (CODING_STANDARDS.md §7.1),
so the classifiers return their progress lines and the writer prints them,
rather than either half rewording one.

Known gap, unchanged by this move: `_create_grant_table` is a fixed-slot
`fields.get(...)` enumeration, so a stage-4 field it does not name is dropped
with no warning. `grant_number` used to be the standing example -- 528 of 537
corpus values reached no render -- until it was folded into the Award Source
label below; that fix is a one-off `.get()` addition, not a registry, so the
*next* unnamed field will drop exactly as silently. Adding a field means
editing this file, not just stage 4 (CODING_STANDARDS.md §7.3). What is new is
that the drop is at least *visible*: `CONSUMED_GRANT_FIELDS` lists every key the
renderer reads and `_create_grant_table` logs the leftovers at debug level. That
is a diagnostic, not a registry -- it tells you a field was dropped, it does not
render it.

Logging rule for this module: no `logger` call and no exception message
carries a value taken from the CV. Every one of them says what happened and why -- an effort figure was
discarded as unparseable or out of range, a title matched several project
names, a field was not consumed -- using counts, key names and module
constants only. The verbose `print` lines are the other channel and do quote
titles; those are the stage's parsed stdout contract (CODING_STANDARDS.md 7.1)
and are unchanged, word for word, from before this module was split out.
"""
import logging
import re
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import TypedDict, cast

try:
    from docx.oxml.xmlchemy import BaseOxmlElement
    from docx.table import Table
except ImportError as exc:
    raise ImportError(
        "python-docx is required for stage 6. Install with: pip install python-docx lxml"
    ) from exc

from ..formatting import (
    _format_currency,
    _set_cell_vertical_alignment,
    _set_font,
    _set_table_border,
    format_date_range,
)
from ..normalization import (
    _deduplicate_repeated_content,
    grant_heading_is_past,
    grant_heading_rebucket_target,
    grant_status_rebucket_target,
)
from ..resolution import _get_cv_owner_name
from ..sorting import sort_entries_reverse_chronological

logger = logging.getLogger(__name__)

# M2A/M2B/M2C -> WCM template section header text, in display order
# (docs/CODING_STANDARDS.md §8.2).
RESEARCH_SUPPORT_SECTIONS = (
    ('M2A', 'Current Research Funding'),
    ('M2B', 'Past (Completed) Funding'),
    ('M2C', 'Pending Funding'),
)


class GrantFields(TypedDict, total=False):
    """One M2 record's `extracted_fields`, as this section reads it.

    The keys are the `fields.get(...)` slots in `_create_grant_table` and
    `_format_grant_duration`, plus `status`, which only the bucket rules read.
    Anything else stage 4 extracts for an M2 record reaches no row of the WCM
    grant block -- the module docstring's known gap -- so adding a
    `fields.get('x')` below means adding `x` here, or the key it now consumes
    still reads as dropped by `CONSUMED_GRANT_FIELDS`.

    `total=False`, and every value nullable, because that is what stage 4
    actually guarantees: it stores raw LLM JSON from a call made with
    `response_format={"type": "json_object"}` and no schema, and
    `coerce_field_value_types` (stage4/coercion.py) only joins lists of
    scalars, leaving every other shape as the model emitted it. This records
    the contract, it does not enforce it: every runtime `.get(...)` fallback
    and guard below stays exactly where it was (review thread 3932312407
    items 1 and 10).

    The value types are measured rather than assumed. The population is the
    335 records the 66-CV local corpus classifies M2A, M2B or M2C -- not the
    348 whose code merely starts "M2": the other 13 carry the bare parent
    code, which `RESEARCH_SUPPORT_SECTIONS` does not name, so this section
    never sees them. Those 335 carry these keys as `str` or as JSON null and
    as nothing else: `start_date` 285 str / 30 null, `title` 282/28, `end_date`
    267/48, `agency` 265/45, `pi_role` 229/81, `total_funding` 206/97,
    `grant_number` 97/213. Re-derive from `src/unified_pipeline/`::

        import collections, glob, json
        counts = collections.defaultdict(collections.Counter)
        for path in glob.glob('outputs/stage_4_field_extraction/*.json'):
            for entry in json.load(open(path)).get('entries') or []:
                if entry.get('taxonomy_code') in ('M2A', 'M2B', 'M2C'):
                    for key, value in (entry.get('extracted_fields') or {}).items():
                        counts[key][type(value).__name__] += 1

    Null is the ordinary case, not the exotic one --
    `fields.get('title', '')` really does hand back None on a record that
    carries the key empty, which is why the reads below are `or`-chained
    rather than defaulted, and why typing these `str` would have been a lie
    that hid it.

    Seven keys (`annual_direct_costs`, `funding_source`, `major_goals`,
    `narrative`, `non_financial_support`, `principal_investigator`, `status`)
    appear on no M2 record in that corpus at all, so their types come from
    their consumer rather than from data. The two money keys are the only ones
    widened past `str`: `_format_currency` documents "Number, string with
    digits, or empty value" and stage-4 coercion leaves a JSON number numeric.

    Structured stage-4 values -- a dict, or a list of record dicts -- are real
    and are what `normalization/fields.py` exists to absorb, but the shapes
    observed there are committee, address and phone fields, none of which are
    grant fields, and none of the 335 records shows one here.
    """

    agency: str | None
    funding_source: str | None
    sponsor: str | None
    annual_direct_costs: str | int | float | None
    # What the stage-4 M2 schema actually emits for a yearly amount
    # (`stage4/schemas.py`: "annual_funding": "Annual/yearly direct costs");
    # `annual_direct_costs` above is the older spelling, kept as the first read.
    annual_funding: str | int | float | None
    total_funding: str | int | float | None
    pi_name: str | None
    principal_investigator: str | None
    date: str | None
    start_date: str | None
    end_date: str | None
    description: str | None
    major_goals: str | None
    narrative: str | None
    grant_number: str | None
    non_financial_support: str | None
    percent_effort: str | None
    pi_role: str | None
    role: str | None
    status: str | None
    study_title: str | None
    text: str | None
    title: str | None
    trial_title: str | None


# Every stage-4 field key this section reads, derived from the record type above
# so the diagnostic and the contract cannot drift apart. Anything outside it
# reaches no row of the WCM grant block, which the module docstring's known gap
# says happens silently -- so `_create_grant_table` names the leftovers at debug
# level.
CONSUMED_GRANT_FIELDS: frozenset[str] = GrantFields.__optional_keys__

# "Individual's role in project including percent effort" and its variants: a
# source-table header row, not a grant. Searched over the first 60 characters
# only, so a real grant that happens to mention a role further in survives.
ROLE_EFFORT_HEADER_RE = re.compile(
    r"(?:Individual's role|your role|role in project|percent effort)",
    re.IGNORECASE
)

# The only buckets a status rebucket may move a grant INTO. M2A is deliberately
# absent: `grant_status_rebucket_target` returns 'M2B', 'M2C' or None
# (normalization/records.py), and no status text promotes a grant back to
# Current. Kept as a named set so the rebucketer can say which code it was
# handed instead of dying on a dict lookup.
REBUCKET_TARGET_CODES = frozenset({'M2B', 'M2C'})

# One line under that header: "Project Title 0.01" / "Project Title .08FTE".
PROJECT_EFFORT_LINE_RE = re.compile(r'^(.+?)\s+(\d*\.?\d+)\s*(?:FTE)?$', re.IGNORECASE)

# A figure at or below this reads as a fraction of full time (0.08 -> 8%);
# above it, as a percentage already (25 -> 25%). Anything outside
# (0%, MAX_PERCENT_EFFORT] is not a percent effort at all and is dropped rather
# than rendered.
FRACTIONAL_EFFORT_CEILING = Decimal('1')
MAX_PERCENT_EFFORT = Decimal('100')
# Two decimal places: "1.5%" survives, ".08 FTE" does not become "8.00%", and a
# stray long decimal cannot render as a 12-digit percentage.
PERCENT_EFFORT_PRECISION = Decimal('0.01')

# End-date text that means "still running", so an M2A grant carrying it is
# never reclassified as completed however the year parses.
OPEN_ENDED_END_DATES = ('present', 'current', 'ongoing', '')

# The goals phrase in a grant's own text or in a goals row of its own (#958),
# and four measured wording variants (#829): the WCM label "(Optional - The
# major goals of this project are): | <goal>", the plain "The major goals of
# this project are:<tab><goal>", the prose "The major goals of this project
# are to <goal>", singular "The major goal of this project is" (YME2VA),
# "...of this program are" (YME2VA), the bare "Major Goals:" / "Major Goals
# of (the) Project:" label with no project/program noun before the separator
# at all (BYFQBG), and A5IZ6Q's typo "The major gals of this project:" --
# plural only; the pattern below deliberately does not also accept the
# singular "gal", since no run has shown that typo. `proj` is the "of
# (this|the) project/program [are|is]" anchor. `sep` -- a colon, pipe, closing
# paren or tab -- is what makes the phrase a label rather than (with `proj`
# present) the start of the sentence; `rest` stops at the line end, since a
# table-form grant carries one source row per line. A bare label needs `sep`
# to read as a label at all -- checked in `parse_major_goals`, not here,
# because Python's `re` cannot express "`sep` required only when `proj` is
# absent" with one named group shared across alternatives. Without that
# check, a stray "major goal(s)" with neither anchor would swallow the rest
# of its line as if it were a whole-sentence claim.
#
# `_MAJOR_GOALS_ANCHOR_PATTERN` is factored out, rather than inlined twice,
# because `parse_major_goals` needs a *second*, shorter regex built from the
# same leading text: one that matches only the "major goal(s)" keyword itself,
# with none of the trailing groups below. Scanning occurrences of that short
# anchor (instead of `MAJOR_GOALS_LABEL_RE` itself) is what lets it walk past
# an unanchored "major goal(s)" mention to find a real, later label on the
# *same* line -- with the full pattern's own greedy `rest` group, a skipped,
# unanchored match's span already swallows the remainder of the line,
# including any real label in it, so nothing would be left to find.
_MAJOR_GOALS_ANCHOR_PATTERN = r'(?:the\s+)?major\s+(?:goals?|gals)\b'
MAJOR_GOALS_LABEL_RE = re.compile(
    _MAJOR_GOALS_ANCHOR_PATTERN
    + r'(?P<proj>\s+of\s+(?:this\s+|the\s+)?(?:project|program)(?:\s+(?:are|is))?)?'
    + r'(?P<sep>[ \t]*[:|)\t][ \t:|)]*)?'
    + r'(?P<rest>[^\n]*)',
    re.IGNORECASE,
)
_MAJOR_GOALS_ANCHOR_RE = re.compile(_MAJOR_GOALS_ANCHOR_PATTERN, re.IGNORECASE)
# A paragraph-form grant tab-separates its fields, and the one observed after a
# goal is the role ("...prognosis.<tab>Role: PI"). Any other tab stays in the
# goal: a wrapped source line is tab-joined as well.
MAJOR_GOALS_VALUE_END_RE = re.compile(r'\t(?=(?:your\s+)?role\b)', re.IGNORECASE)


class UnsupportedRebucketTargetError(ValueError):
    """A status rule asked for a funding bucket this section cannot render into.

    Preventive, with no incident behind it: today
    `grant_status_rebucket_target` returns only 'M2B', 'M2C' or None, so this
    cannot fire. The point is what happens the day it returns something else --
    a bare KeyError off `bucket_lists[target]`, naming neither the code nor the
    grant, is a poor way to find that out (review thread 3932312407 item 2).
    """


def normalize_percent_effort(effort_value: str) -> str | None:
    """Render one raw effort figure as the percentage the WCM row shows.

    `0.015 -> '1.5%'`, `0.08 -> '8%'`, `1.5 -> '1.5%'`, `25 -> '25%'`; no
    trailing '.0'. Returns None for a figure this section will not show at all:
    unparseable, not a number, zero or negative, or over 100%.

    "Unparseable" and "not a number" are two guards, not one, because Decimal
    splits them: `Decimal('abc')` raises InvalidOperation from the constructor,
    but `Decimal('NaN')` constructs happily and then raises the same exception
    off the very next line -- every comparison against a NaN signals. The
    docstring promised None for both and the code only delivered one, so the
    `is_nan()` guard below makes the code match the contract rather than the
    contract match the code. Widened rather than narrowed on purpose: the
    function is module-public, `normalize_percent_effort` is what the M2 tests
    call directly, and a docstring that says "returns None for unparseable"
    while raising is the more expensive of the two lies. `Decimal('Infinity')`
    needs no guard -- it compares fine and the range check below already
    discards it, in both signs.

    Not reachable from the sole production call site, the
    `PROJECT_EFFORT_LINE_RE` match inside `filter_role_effort_headers`: group 2
    is digits with at most one decimal point and no letter, so it can never
    hand over the string "nan". Stage 6 renders
    the same bytes either way -- this is a contract fix, not a bug fix.

    The two int() calls this replaces did not round, they truncated, and did it
    on both branches: `int(0.015 * 100)` rendered a 1.5% effort as "1%" and
    `int(1.5)` rendered a 1.5% effort as "1%" as well, while 0 rendered as "0%"
    and 150 as "150%" (review thread 3932312407 item 3). Decimal, not float, so
    the fraction branch cannot land on 1.4999999999999998.
    """
    try:
        value = Decimal(effort_value)
    except InvalidOperation:
        logger.debug("Discarded a percent effort figure: not a number")
        return None
    if value.is_nan():
        logger.debug("Discarded a percent effort figure: NaN")
        return None
    percent = value * 100 if value <= FRACTIONAL_EFFORT_CEILING else value
    if percent <= 0 or percent > MAX_PERCENT_EFFORT:
        logger.debug(
            "Discarded a percent effort figure: outside the range (0, %s] percent",
            MAX_PERCENT_EFFORT,
        )
        return None
    text = format(percent.quantize(PERCENT_EFFORT_PRECISION, rounding=ROUND_HALF_UP), 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return f"{text}%"


def _print_verbose(messages: list[str], verbose: bool) -> None:
    """Print what a pure classifier reported, if the generator is verbose.

    The classifiers below return their progress lines instead of printing them,
    because stage stdout is a parsed contract (CODING_STANDARDS.md 7.1) and the
    strings therefore have to survive the split from rendering unchanged.
    """
    if verbose:
        for message in messages:
            logger.debug(message)


def copy_entries_for_render(entries: list[dict]) -> list[dict]:
    """Shallow-copy one bucket's entries so classification never writes through
    to the caller's pipeline records (review thread 3932312407 item 1).

    Both levels this section writes to are copied: the entry itself, which gains
    `reclassification_note`, and its `extracted_fields`, which gains
    `percent_effort`. Nothing here writes any deeper, so deeper values stay
    shared. A non-dict `extracted_fields` is passed through exactly as it
    arrived rather than coerced -- a malformed record still fails where it
    always did, instead of failing somewhere new.
    """
    copies: list[dict] = []
    for entry in entries:
        clone = dict(entry)
        fields = clone.get('extracted_fields')
        if isinstance(fields, dict):
            clone['extracted_fields'] = dict(fields)
        copies.append(clone)
    return copies


def filter_role_effort_headers(
    entries: list[dict], effort_lookup: dict[str, str]
) -> tuple[list[dict], list[str]]:
    """Split the role/effort header rows out of one bucket's entries.

    Such a row is not a grant: it is a source-table header whose body lists
    "<project name> <effort>" pairs, e.g. "Individual's role in project
    including percent effort" then "Project Alpha 0.01" on the next line. The
    pairs are added to `effort_lookup` (normalized project name -> "1%") and
    the row itself is dropped from the entries to render.

    `effort_lookup` is the section's own accumulator and is extended in place,
    because the count reported per header row is cumulative across all three
    buckets and always has been. Returns (entries to render, verbose lines).
    """
    filtered: list[dict] = []
    messages: list[str] = []
    for entry in entries:
        text = entry.get('text', '')
        if not ROLE_EFFORT_HEADER_RE.search(text[:60]):
            filtered.append(entry)
            continue
        for line in text.split('\n')[1:]:  # Skip the header line
            line = line.strip()
            if not line:
                continue
            effort_match = PROJECT_EFFORT_LINE_RE.search(line)
            if not effort_match:
                continue
            # Normalize to percentage (0.01 -> 1%, .08 -> 8%, 0.015 -> 1.5%)
            effort_pct = normalize_percent_effort(effort_match.group(2))
            if effort_pct is None:
                continue
            effort_lookup[effort_match.group(1).strip().lower()] = effort_pct
        messages.append(
            f"  Filtered role/effort header entry, extracted {len(effort_lookup)} effort values")
    return filtered, messages


def match_effort_for_title(title: str, effort_lookup: dict[str, str]) -> str | None:
    """Resolve one normalized grant title to an extracted percent effort.

    Normalized exact match first. Substring matching stays as the explicit
    fallback -- a source table really does write "Project Alpha" in the effort
    header and "Project Alpha: aims 1-3" in the grant list -- but it no longer
    guesses: a title that substring-matches more than one project name gets
    nothing.

    The old rule was substring-only and took the first hit in insertion order,
    so with both "Project Alpha" (1%) and "Project Alpha Extended" (8%) in the
    header, the *Extended* grant was handed Alpha's 1% (review thread
    3932312407 item 6). Exact-first settles that pair outright; the
    ambiguity rule covers the pairs no exact match settles.
    """
    exact = effort_lookup.get(title)
    if exact is not None:
        return exact
    candidates = [effort for project_name, effort in effort_lookup.items()
                  if project_name in title or title in project_name]
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        logger.debug(
            "Assigned no percent effort: the grant title substring-matches %d "
            "project names in the effort header", len(candidates))
    return None


def apply_effort_to_grants(entries: list[dict], effort_lookup: dict[str, str]) -> list[str]:
    """Attach each extracted percent effort to the grant whose title it names.

    An effort already on the record wins -- the lookup only fills a gap.
    Writes land on this section's copies of the records (`copy_entries_for_render`),
    never on the caller's. Returns the verbose lines.
    """
    messages: list[str] = []
    for entry in entries:
        fields = cast(GrantFields, entry.get('extracted_fields') or {})
        title = (fields.get('title', '') or '').lower().strip()
        if not title or fields.get('percent_effort'):
            continue
        # Try to find matching effort in lookup
        effort = match_effort_for_title(title, effort_lookup)
        if effort is None:
            continue
        fields['percent_effort'] = effort
        messages.append(f"  Matched effort {effort} to '{title[:40]}...'")
    return messages


def explicit_status_target(entry: dict) -> tuple[str | None, str | None]:
    """The bucket a grant's own words put it in: its status field, or, when
    stage 4 left no status, the heading the CV filed it under (#981)."""
    fields = cast(GrantFields, entry.get('extracted_fields') or {})
    status = fields.get('status')
    if status:
        return grant_status_rebucket_target(status)
    return grant_heading_rebucket_target(entry.get('hierarchy') or [])


def rebucket_grants_by_status(
    m2a_entries: list[dict], m2b_entries: list[dict], m2c_entries: list[dict]
) -> tuple[list[dict], list[dict], list[dict], list[str]]:
    """Move grants between buckets on their own extracted status text (#210).

    An explicit "Under review" / "Not funded" beats date inference, which is why
    this runs before `reclassify_past_m2a_grants`. Returns the three buckets in
    M2A/M2B/M2C order plus the verbose lines; the inputs are left as they were.

    Raises:
        UnsupportedRebucketTargetError: the status rule named a bucket outside
            REBUCKET_TARGET_CODES. Not swallowed and not defaulted to a bucket
            of our choosing: filing a grant under a guess is worse than
            stopping (CODING_STANDARDS.md 5.4).
    """
    current = list(m2a_entries)
    completed = list(m2b_entries)
    pending = list(m2c_entries)
    bucket_lists = {'M2B': completed, 'M2C': pending}
    messages: list[str] = []
    for source_code, source_list in (('M2A', current), ('M2B', completed)):
        for position, entry in enumerate(list(source_list)):
            fields = cast(GrantFields, entry.get('extracted_fields') or {})
            target, note = explicit_status_target(entry)
            if not target or target == source_code:
                continue
            title = str(fields.get('title') or 'Unknown')
            if target not in REBUCKET_TARGET_CODES:
                # The grant is identified by its position, not its title: this
                # message reaches a traceback and from there the pod logs, and a
                # grant title is CV content.
                raise UnsupportedRebucketTargetError(
                    f"grant_status_rebucket_target returned bucket {target!r} for "
                    f"{source_code} grant #{position}; research support renders "
                    f"only {sorted(REBUCKET_TARGET_CODES)}"
                )
            source_list.remove(entry)
            entry.setdefault('reclassification_note', note)
            bucket_lists[target].append(entry)
            messages.append(f"  Status rebucket {source_code}->{target}: '{title[:40]}'")
    return current, completed, pending, messages


def reclassify_past_m2a_grants(
    m2a_entries: list[dict], m2b_entries: list[dict], current_year: int
) -> tuple[list[dict], list[dict], list[str]]:
    """Move M2A grants whose end date is already past into M2B, with a note.

    `current_year` is a parameter rather than a `datetime.now()` read so the
    boundary is testable and re-rendering an old document reproduces the buckets
    it had (review thread 3932312407 item 7). Returns (M2A, M2B, verbose lines);
    the inputs are left as they were.
    """
    current = list(m2a_entries)
    completed = list(m2b_entries)
    messages: list[str] = []

    # Check each M2A entry for past end dates
    entries_to_move = []
    for entry in current:
        fields = cast(GrantFields, entry.get('extracted_fields') or {})
        end_date = fields.get('end_date', '')

        # Parse end date to check if it's in the past
        if end_date and end_date.lower() not in OPEN_ENDED_END_DATES:
            # Try to extract year from end date
            year_match = re.search(r'(\d{4})', str(end_date))
            if year_match:
                end_year = int(year_match.group(1))
                if end_year < current_year:
                    # This grant has ended - reclassify to M2B
                    entries_to_move.append((entry, end_date, end_year))

    # Move entries and add reclassification comments
    for entry, end_date, end_year in entries_to_move:
        current.remove(entry)

        # Add reclassification note to the entry
        if 'reclassification_note' not in entry:
            entry['reclassification_note'] = f"Reclassified from Current (M2A) to Completed (M2B): end date {end_date} is before {current_year}"

        completed.append(entry)

        title = (entry.get('extracted_fields') or {}).get('title') or 'Unknown'
        messages.append(f"  Reclassified to M2B: '{title[:40]}...' (ended {end_year})")

    return current, completed, messages


def promote_open_ended_m2b_grants(
    m2a_entries: list[dict], m2b_entries: list[dict], current_year: int
) -> tuple[list[dict], list[dict], list[str]]:
    """Move M2B grants that are still running into M2A, with a note (#981).

    The mirror of `reclassify_past_m2a_grants`: an end date of 'present' or a
    year >= `current_year` is a current grant, however stage 3b coded it. A
    grant with no end date is left alone -- nothing says it is running -- and so
    is one whose own status or heading names its bucket
    (`explicit_status_target`) or a heading that files it as past, which beats
    date inference. Returns
    (M2A, M2B, verbose lines); the inputs are left as they were.
    """
    current = list(m2a_entries)
    completed = list(m2b_entries)
    messages: list[str] = []
    for entry in list(completed):
        fields = cast(GrantFields, entry.get('extracted_fields') or {})
        end_date = str(fields.get('end_date') or '').strip()
        if not end_date or 'reclassification_note' in entry:
            continue
        if explicit_status_target(entry)[0] or grant_heading_is_past(
                entry.get('hierarchy') or []):
            continue
        year_match = re.search(r'(\d{4})', end_date)
        if end_date.lower() in OPEN_ENDED_END_DATES:
            running = True
        else:
            running = bool(year_match) and int(year_match.group(1)) >= current_year
        if not running:
            continue
        completed.remove(entry)
        entry['reclassification_note'] = (
            f"Reclassified from Completed (M2B) to Current (M2A): end date "
            f"{end_date} is not before {current_year}"
        )
        current.append(entry)
        title = fields.get('title') or 'Unknown'
        messages.append(f"  Reclassified to M2A: '{title[:40]}...' (ends {end_date})")
    return current, completed, messages


def parse_major_goals(text: str | None) -> str | None:
    """The faculty member's own goal text after a goals label, verbatim (#958).

    Two shapes carry it. A label -- `MAJOR_GOALS_LABEL_RE` with a separator
    (`sep`) after the anchor -- is followed by the goal, which is returned
    without the label. Without a separator the phrase must still carry the "of
    (this|the) project/program [are|is]" anchor (`proj`): it opens the faculty
    member's own sentence ("The major goals of this project are to ..."), and
    the whole sentence is the goal. A match with neither `proj` nor `sep` --
    some other use of "major goal(s)" with no project/program noun and no
    separator -- is not a label at all, so it is left as grant content rather
    than treated as an unbounded whole-sentence claim.

    That "leave it as grant content" choice is per-match, not per-text: a
    single `.search()` would stop at the first "major goal(s)" mention even
    when it is the unanchored kind and a real, anchored label follows later in
    the same text -- on the same line or a later one -- silently dropping the
    real label. So this walks every occurrence of the bare "major goal(s)"
    anchor (`_MAJOR_GOALS_ANCHOR_RE`, not `MAJOR_GOALS_LABEL_RE` itself: its
    greedy `rest` group would swallow a same-line label into the very
    unanchored match being skipped) and, at each one, tries the full label
    pattern anchored to that position (`.match(text, pos)`), taking the first
    that is actually anchored (`proj` or `sep`). An unanchored mention is
    skipped rather than treated as the answer. Either way only surrounding
    whitespace is stripped. An empty label is no goal either: None, so no row
    renders.
    """
    text = text or ''
    match = next(
        (candidate for candidate in (
            MAJOR_GOALS_LABEL_RE.match(text, anchor.start())
            for anchor in _MAJOR_GOALS_ANCHOR_RE.finditer(text)
        ) if candidate and (candidate['proj'] or candidate['sep'])),
        None,
    )
    if match is None or not match['rest'].strip():
        return None
    goal = match['rest'] if match['sep'] else match.group(0)
    return MAJOR_GOALS_VALUE_END_RE.split(goal, maxsplit=1)[0].strip()


def fill_major_goals_from_text(entries: list[dict]) -> None:
    """Give a grant the goal written in its own text when stage 4 left none (#958).

    Stage 4 never extracts `major_goals` for M2 records, so a goal stated inside
    the grant entry reached no row. Writes land on this section's copies of the
    records (`copy_entries_for_render`), never on the caller's.
    """
    for entry in entries:
        fields = cast(GrantFields, entry.get('extracted_fields') or {})
        if not fields.get('major_goals'):
            fields['major_goals'] = parse_major_goals(entry.get('text'))


def _spans_element(entry: dict, element_idx: int) -> bool:
    """True when `element_idx` lies inside the entry's own source-element range."""
    start, end = entry.get('element_idx_start'), entry.get('element_idx_end')
    return isinstance(start, int) and isinstance(end, int) and start <= element_idx <= end


def claim_goal_rows(grants: list[dict], rows: list[dict]) -> list[tuple[dict, dict]]:
    """Attach each goals-row entry to the one grant whose source table holds it (#958).

    When the goals row sits in a table of its own, stage 2 emits it as a
    separate `table_row` entry whose `parent_idx` is a source element inside the
    grant's own `element_idx_start..element_idx_end` range, and 3b classifies it
    T, so it went to the Appendix. The signal is that range, not proximity: a
    row inside no grant's range, or inside two (ambiguous), is left alone, and
    so is one whose goal differs from a goal the grant already carries -- the
    row is then still the only place that text appears.

    Returns (row, grant) pairs. The grant's copied fields gain `major_goals`; the
    row itself is not touched. The caller decides which rows actually left the
    Appendix, because only a grant that rendered took its goal with it.
    """
    claimed: list[tuple[dict, dict]] = []
    for row in rows:
        goal = parse_major_goals(row.get('text'))
        parent_idx = row.get('parent_idx')
        if goal is None or not isinstance(parent_idx, int):
            continue
        owners = [grant for grant in grants if _spans_element(grant, parent_idx)]
        if len(owners) != 1:
            continue
        fields = cast(GrantFields, owners[0].get('extracted_fields') or {})
        existing = fields.get('major_goals')
        if existing and existing != goal:
            continue
        fields['major_goals'] = goal
        claimed.append((row, owners[0]))
    return claimed


# A grant's own text often names its PI, and `co_investigators` (which resolve_pi_name
# no longer reads) sometimes held exactly that name. These are the shapes the
# corpus carries, tried in this order, first hit wins:
#
#   "PI: Lee", "(PI Park)", "[PI Dr. Ann B. Cole, ..."
#   "Principal Investigator: Sam Ortiz", "Principal Investigators: A & B"
#   "Nguyen (PI)", "Lee & Park (MPI)", "Dr. Ana Cruz (Principal Investigator)"
#   "M. Silva, Principal Investigator", "Rosa Diaz, PI"
#   "[PIs Dr. Ann Cole, Dr. Ben Hale, et al.]" (comma-separated names)
#
# Every shape wants a capitalised name, which is what keeps "PI: 75%",
# "Subcontract-PI: $103,886" and "PI: smith" from matching. "Co-PI:" and
# "Subcontract-PI:" are refused by the lookbehind (a hyphen or word character
# before the "PI"): they name someone who is not the grant's PI. The separator
# after a colon is spaces only, so a tab-delimited "PI:\t<next cell>" does not
# read the next cell as a name. A name is a capitalised, digit-free token plus up
# to PI_NAME_MAX_EXTRA_TOKENS more ("Dr." counts as a token), all on one line and
# starting at a word boundary; two names may join with "&" or "and". A name that
# would run past the token limit ("PI Kim Tu Thi Tran" at four tokens) does not
# match at all rather than truncating to a wrong name. The "Name, Principal
# Investigator" shape needs at least two tokens, so "Institute of Chicago,
# Principal Investigator" (the owner's own role) does not read "Chicago" as a PI.
PI_NAME_MAX_EXTRA_TOKENS = 3
_PI_NAME_TOKEN = r"[A-Z](?:[^\W\d_]|[.'\u2019-])*"
_PI_NAME_END = r"(?![\w'\u2019-]| +[A-Z])"


def _pi_name(min_extra_tokens: int) -> str:
    """A name pattern: `min_extra_tokens`..PI_NAME_MAX_EXTRA_TOKENS tokens after the first."""
    return (
        r"(?<![\w-])" + _PI_NAME_TOKEN
        + r"(?: +" + _PI_NAME_TOKEN + r"){%d,%d}" % (min_extra_tokens, PI_NAME_MAX_EXTRA_TOKENS)
        + _PI_NAME_END
    )


_PI_NAMES = _pi_name(0) + r"(?: +(?:&|and) +" + _pi_name(0) + r")*"
PI_LABEL_PATTERNS = (
    re.compile(r"(?:(?<![-\w])PI: *|[(\[]PI +)(" + _PI_NAMES + r")"),
    re.compile(r"(?<![-\w])Principal Investigators?: *(" + _PI_NAMES + r")"),
    re.compile(r"(" + _PI_NAMES + r") +\((?:PI|MPI|Principal Investigator)\)"),
    re.compile(r"(" + _pi_name(1) + r"), (?:Principal Investigator|PI)\b"),
    re.compile(r"[(\[]PIs +(" + _pi_name(0) + r"(?:, +" + _pi_name(0) + r")*)"),
)
_NAME_JOINER_RE = re.compile(r" (?:&|and) ")
_NAME_WORD_RE = re.compile(r"[^\W\d_]{3,}(?:-[^\W\d_]+)*")


def _pi_name_from_label(raw_text: str) -> str:
    """The PI a grant's own text names, or '' when it names none."""
    for pattern in PI_LABEL_PATTERNS:
        match = pattern.search(raw_text or '')
        if match:
            return match.group(1).strip()
    return ''


def _surname_parts(name: str) -> frozenset[str]:
    """The casefolded parts of the surname in "First M. Last".

    A hyphenated surname contributes each part ("Rivera-Ortiz" gives both), so
    "Ana Rivera" still reads as the owner "Ana Rivera-Ortiz". Any comma means a
    list of names ("[PIs Lee, Park]"): no label pattern captures "Last, First",
    and `_get_cv_owner_name` builds "First Last", so a comma never marks a
    surname. Empty for a list or no name.
    """
    if ',' in name:
        return frozenset()
    words = _NAME_WORD_RE.findall(name)
    return frozenset(words[-1].casefold().split('-')) if words else frozenset()


def _is_cv_owner(pi_name: str, owner_name: str) -> bool:
    """True when `pi_name` is one person whose surname is the CV owner's surname.

    "Lee (PI)" names the CV owner "Ann Lee" by surname; the owner's full name is
    the better cell, and it is what the owner auto-fill rendered before the label
    was read. Surname only: a shared first name ("PI: Ann Smith" on Ann Lee's
    CV) or a given name that is another person's surname ("PI: Mark Hale" on
    Tom Mark Lee's CV) is someone else. A joined pair ("Lee & Park") or a list
    ("Lee, Park") is never replaced, since dropping the other name would lose a
    PI.
    """
    if not owner_name or _NAME_JOINER_RE.search(pi_name):
        return False
    return bool(_surname_parts(pi_name) & _surname_parts(owner_name))


def resolve_pi_name(
    fields: GrantFields, raw_text: str, role: str | None, owner_name: str
) -> str | None:
    """Resolve the principal investigator for one grant.

    Extracted field first, then a PI the source text names (`PI_LABEL_PATTERNS`),
    then the trailing cell of a pipe-delimited source row, then the CV owner when the
    role says they are the PI. Text in, text out: no docx, so the parser's
    heuristics are testable on their own (review thread 3932312407 item 5).

    `co_investigators` is deliberately not a source. It lists the people who
    worked on the grant alongside the PI, and on a CV that is usually the CV
    owner: reading it here rendered the owner as "Name of Principal
    Investigator" on a grant whose own text said "PI: <someone else>" (#982).
    The label parse is what fills that cell instead, and it runs before the
    owner auto-fill so a named PI is never overwritten by the owner.

    Returns None, not '', when the record carries `pi_name` or
    `principal_investigator` as a JSON null and nothing else resolves -- the
    `or` chain hands the null straight back. The caller renders a falsy PI as an
    empty cell either way.

    `role` is `str | None` for the same reason, on the way in. The caller reads
    it as `fields.get('pi_role') or fields.get('role', '')`
    (`_create_grant_table`), and `GrantFields` types both keys `str | None`, so
    the chain yields None -- not '' -- on a record that carries `role` present
    and JSON-null while `pi_role` is falsy. Annotating it `str` made the
    `role.lower()` below an AttributeError on exactly that record, reachable
    only when nothing else resolved a PI and an owner name was present. Hence
    the `role and` guard: a falsy role never matched the auto-fill anyway, so
    it changes no record that renders today. The 66-CV corpus never fires it --
    over its 335 M2A/M2B/M2C records `role` is absent 324 times and a str 11
    times, never null -- but `pi_role` is null on 81 of them, which is what
    puts the read on the `role` branch at all.
    """
    pi_name = fields.get('pi_name') or fields.get('principal_investigator', '')

    if not pi_name:
        pi_name = _pi_name_from_label(raw_text)
        if pi_name and _is_cv_owner(pi_name, owner_name):
            pi_name = owner_name

    # Parse PI name from raw text if not in extracted fields
    # Common format: "Agency | Amount | Dates | PI Name"
    if not pi_name and raw_text and '|' in raw_text:
        parts = [p.strip() for p in raw_text.split('|')]
        # Skip if all parts are identical (repeated content from merged cells)
        unique_parts = set(p.lower() for p in parts if p)
        if len(parts) >= 4 and len(unique_parts) > 1:
            # The last part is often the PI name
            potential_name = parts[-1].strip()
            # Check if it looks like a name:
            # - Not just digits/dates
            # - Matches the whitespace-separated name pattern below
            # - Short enough to be a name (< 50 chars)
            # - Doesn't contain project/grant keywords
            project_keywords = ['project', 'study', 'grant', 'research', 'program', 'trial',
                                'investigation', 'promotion', 'implementation', 'development']
            if (potential_name and
                len(potential_name) < 50 and
                not re.match(r'^[\d\-/]+$', potential_name) and
                not any(kw in potential_name.lower() for kw in project_keywords)):
                # "First Last" or "First M. Last" -- whitespace-separated only.
                # "Last, First" is NOT supported: the comma form never matches
                # this pattern, so "Smith, Jane" in the trailing cell is left
                # alone rather than half-parsed (review thread 3932312407 item
                # 4). Teaching the pattern the comma form would change rendered
                # PI names across the corpus, so it is a measured change, not a
                # comment fix.
                if re.match(r'^[A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+$', potential_name):
                    pi_name = potential_name

    # Auto-fill PI name when role indicates Principal Investigator and no PI name specified
    if not pi_name and owner_name and role and 'principal' in role.lower() and 'investigator' in role.lower():
        pi_name = owner_name
    return pi_name


# The WCM template's own M2A placeholder table has row-0 cell-0 "Award
# Source: (funding agency ...)*"; a freshly built grant table
# (`_create_grant_table`, below) labels its own row 0 "Award Source:" with no
# trailing text. Both start with this prefix. N2's placeholder table (a
# different section entirely) reads "Award Source (funding agency, type of
# grant):" -- no colon immediately after "Source" -- and must NOT match
# (#836).
_FUNDING_PLACEHOLDER_LABEL_PREFIX = 'award source:'


def _looks_like_funding_placeholder(table: Table) -> bool:
    """True when `table`'s header row looks like M2's own placeholder or a
    grant table this section built (#836, same pattern as
    `leadership._looks_like_leadership_table`).

    Only `Current Research Funding` has a placeholder table of its own in the
    WCM template; `Past (Completed) Funding` and `Pending Funding` do not, so
    `_find_table_after_paragraph`'s unbounded forward scan used to hand those
    two steps whatever table came next in the document -- N2's placeholder,
    then a mentee placeholder -- and both got deleted on every render. This
    guard is what stops the removal from firing on a table that is not M2's.
    """
    if not table.rows:
        return False
    header_cells = table.rows[0].cells
    if not header_cells:
        return False
    return header_cells[0].text.strip().lower().startswith(_FUNDING_PLACEHOLDER_LABEL_PREFIX)


# The costs row of the WCM grant block. The template's own label is "Annual
# direct costs:"; a total award amount gets its own label instead, because a
# total under "Annual" states a false fact (#982).
ANNUAL_COSTS_LABEL = 'Annual direct costs:'
TOTAL_AWARD_LABEL = 'Total award:'


def _first_rendering_amount(fields: GrantFields) -> str | int | float:
    """The yearly amount, from `annual_direct_costs` then `annual_funding`.

    Decided on what each value *renders*, not on its truthiness, so a real $0
    in the first key wins over the second (round-2 review of #481, point 4) and
    a blank or non-amount value falls through to it.
    """
    annual_direct_costs = fields.get('annual_direct_costs')
    if _format_currency(annual_direct_costs):
        return cast(str | int | float, annual_direct_costs)
    annual_funding = fields.get('annual_funding')
    if _format_currency(annual_funding):
        return cast(str | int | float, annual_funding)
    return ''


def _format_grant_costs(
    annual_costs: str | int | float | None, total_funding: str | int | float | None
) -> list[tuple[str, str]]:
    """The costs rows as (label, currency text) pairs.

    `annual_costs` goes under "Annual direct costs:" whenever it renders. A
    `total_funding` that renders goes under "Total award:", never under
    "Annual": alone it is the only costs row, and beside a different yearly
    amount it is a second row, so neither figure is dropped. With neither, the
    template's own label and an empty cell. Both arguments are
    `_create_grant_table`'s locals rather than fresh `fields` reads, so a
    duplicate-of-title blanking made before the call is what renders here too.
    """
    annual_formatted = _format_currency(annual_costs)
    total_formatted = _format_currency(total_funding)
    rows = []
    if annual_formatted or not total_formatted:
        rows.append((ANNUAL_COSTS_LABEL, annual_formatted))
    if total_formatted and total_formatted != annual_formatted:
        rows.append((TOTAL_AWARD_LABEL, total_formatted))
    return rows


class ResearchSupportSection:
    """Section M2 writers, mixed into `WCMTemplateGenerator`."""

    def _fill_research_support(
        self,
        entries_by_code: dict[str, list[dict]],
        cv_owner: dict | None = None,
        document_uid: str = '',
        current_year: int | None = None,
    ) -> list[dict]:
        """Fill research support section with individual tables per grant.

        Creates a table for each grant with the WCM data model:
        - Award Source (funding agency)
        - Project title
        - Annual direct costs
        - Non-financial support
        - Duration of support (mm/yyyy-mm/yyyy)
        - Name of Principal Investigator
        - Your role
        - Your percent (%) effort
        - Major goals (optional)

        Grants are organized under WCM template headers:
        - Current Research Funding (M2A)
        - Past (Completed) Funding (M2B)
        - Pending Funding (M2C)

        Reclassification: Grants classified as M2A (current) but with end dates
        before the current year are moved to M2B (completed) with a comment.

        Every rule above lives in the module-level classifiers -- they take plain
        data and return plain data, so a bucket decision is testable without a
        DOCX (review thread 3932312407 item 5). This method is the rendering
        half: template lookup, table deletion, table creation, XML insertion and
        stats.

        Args:
            entries_by_code: stage-4 entries keyed by taxonomy code.
            cv_owner: the CV owner's record, for auto-filling the PI name.
            document_uid: run-scoped document id, used to resolve the owner.
            current_year: the year "current funding" is judged against. Defaults
                to the system clock; pass it to make a boundary reproducible.

        Returns:
            The T-coded goals-row entries (#958) whose goal now renders inside a
            grant table -- the caller's own dicts, by identity, for `generate()`
            to keep out of the Appendix.
        """
        # Get CV owner name for auto-filling PI when role is Principal Investigator
        owner_name = _get_cv_owner_name(cv_owner, document_uid)
        if current_year is None:
            current_year = datetime.now().year

        # Classification, on this section's own copies of the records so nothing
        # below writes back into the caller's pipeline data.
        effort_lookup: dict[str, str] = {}  # project_name_normalized -> percent_effort
        buckets: list[list[dict]] = []
        for code, _header in RESEARCH_SUPPORT_SECTIONS:
            entries = copy_entries_for_render(entries_by_code.get(code, []))
            entries, messages = filter_role_effort_headers(entries, effort_lookup)
            _print_verbose(messages, self.verbose)
            buckets.append(entries)
        m2a_entries, m2b_entries, m2c_entries = buckets

        for entries in (m2a_entries, m2b_entries, m2c_entries):
            _print_verbose(apply_effort_to_grants(entries, effort_lookup), self.verbose)

        m2a_entries, m2b_entries, m2c_entries, messages = rebucket_grants_by_status(
            m2a_entries, m2b_entries, m2c_entries)
        _print_verbose(messages, self.verbose)

        m2a_entries, m2b_entries, messages = reclassify_past_m2a_grants(
            m2a_entries, m2b_entries, current_year)
        _print_verbose(messages, self.verbose)

        m2a_entries, m2b_entries, messages = promote_open_ended_m2b_grants(
            m2a_entries, m2b_entries, current_year)
        _print_verbose(messages, self.verbose)

        grants = m2a_entries + m2b_entries + m2c_entries
        fill_major_goals_from_text(grants)
        claimed_goal_rows = claim_goal_rows(grants, entries_by_code.get('T', []))
        rendered_grant_ids: set[int] = set()

        # Map taxonomy codes to WCM template section headers
        # These must match the exact text in the official WCM template
        categories = [
            (code, header, entries)
            for (code, header), entries in zip(
                RESEARCH_SUPPORT_SECTIONS, (m2a_entries, m2b_entries, m2c_entries)
            )
        ]

        total_grants = sum(len(entries) for _, _, entries in categories)
        if self.verbose:
            logger.info("Filling Research Support (%s grants)...", total_grants)

        # Process each category - find the corresponding section in the template
        for code, section_header, entries in categories:
            # Find the section header in the template
            section_idx = self._find_paragraph_with_text(section_header)
            if section_idx is None:
                if self.verbose:
                    logger.warning("  Warning: Could not find section header '%s'", section_header)
                continue

            # Remove the existing template table after this section, if it is
            # M2's own placeholder (even with no entries, to avoid leaving an
            # empty template table). `_find_table_after_paragraph` has no
            # section boundary and returns the first table anywhere below --
            # for Past/Pending, which have no placeholder of their own, that
            # was N2's table and a mentee placeholder (#836); the shape guard
            # is what keeps this removal inside M2.
            existing_table = self._find_table_after_paragraph(section_idx)
            if existing_table and _looks_like_funding_placeholder(existing_table):
                existing_table._element.getparent().remove(existing_table._element)

            if not entries:
                continue

            # Sort entries reverse chronologically (most recent first)
            sorted_entries = sort_entries_reverse_chronological(entries)

            if self.verbose:
                logger.info("  %s: %s grants -> '%s'", code, len(entries), section_header)

            # Create a table for each grant with spacing between them
            # Track the last inserted element (as actual XML element reference)
            last_element = self.doc.paragraphs[section_idx]._element

            for i, entry in enumerate(sorted_entries):
                fields = cast(GrantFields, entry.get('extracted_fields') or {})

                # Create grant table - pass the element to insert after and owner name
                grant_table = self._create_grant_table(fields, code, entry, insert_after_element=last_element, owner_name=owner_name)
                if grant_table:
                    rendered_grant_ids.add(id(entry))
                    self.stats['tables_populated'] += 1
                    self.stats['entries_inserted'] += 1

                    # Update last_element to the newly inserted table
                    last_element = grant_table._tbl

                    # Add blank paragraph after each grant table for spacing
                    # (except after the last one in each category)
                    if i < len(sorted_entries) - 1:
                        spacing_para = self._add_spacing_paragraph(after_element=grant_table._tbl)
                        if spacing_para is not None:
                            last_element = spacing_para

        # A goals row leaves the Appendix only with a grant that rendered: a
        # declined grant, or one under a header the template lacks, took nothing.
        return [row for row, grant in claimed_goal_rows if id(grant) in rendered_grant_ids]

    def _create_grant_table(
        self,
        fields: GrantFields,
        code: str,
        entry: dict | None = None,
        insert_after_element: BaseOxmlElement | None = None,
        owner_name: str = '',
    ) -> Table | None:
        """Create an individual grant table with the WCM data model.

        Args:
            fields: Extracted field data for the grant. Typed as `GrantFields`,
                which names the keys and their observed shapes but enforces
                nothing at runtime -- the `.get(...)` fallbacks below are still
                the only thing standing between a missing or null field and a
                blank cell.
            code: Taxonomy code (M2A, M2B, M2C)
            entry: Full entry dict for comments/metadata
            insert_after_element: XML element to insert after. If None, falls back to RESEARCH SUPPORT.
            owner_name: CV owner's name for auto-filling PI when role is Principal Investigator

        Returns:
            Table object, or None if entry is too sparse to create a useful table
        """
        # The rows below are a fixed enumeration, so a stage-4 field none of them
        # names is dropped with no warning (module docstring). Say which keys
        # those are, at debug level. KEYS ONLY -- a grant's values are CV content
        # and this logger is not a place to put PII.
        unconsumed_fields = sorted(set(fields) - CONSUMED_GRANT_FIELDS)
        if unconsumed_fields:
            logger.debug(
                "Extracted %s fields not consumed by the research-support renderer: %s",
                code, ', '.join(unconsumed_fields),
            )

        # Validate minimum required fields - skip header-like entries
        # A valid grant should have at least a title OR (agency + role/dates)
        # Check multiple title field names since clinical trials use trial_title/study_title/text
        title = fields.get('title', '') or fields.get('trial_title', '') or fields.get('study_title', '') or fields.get('text', '')

        # Clean up title - remove repeated content from merged table cells
        # e.g., "Title .08FTE | Title .08FTE | Title .08FTE" -> "Title .08FTE"
        title = _deduplicate_repeated_content(title)

        agency = fields.get('agency') or fields.get('funding_source', '') or fields.get('sponsor', '')
        # The two funding keys are read twice, in opposite precedence: the
        # duplicate-content check below wants total_funding first, the rendered
        # costs row wants the yearly amount first (`annual_direct_costs`, else
        # `annual_funding`, the key stage 4 emits). Both reads
        # go through these locals, so clearing a duplicate below no longer has to
        # write '' back into the caller's `fields` to make the second read agree
        # (review thread 3932312407 item 1).
        annual_direct_costs = _first_rendering_amount(fields)
        total_funding = fields.get('total_funding', '') or annual_direct_costs

        # Detect and fix cross-field duplication where the same content appears in multiple fields
        # This happens when Stage 4 incorrectly puts the same text in agency, title, AND funding
        #
        # The `.strip()` on `total_funding` below assumes a string. `GrantFields`
        # types the two money keys `str | int | float | None` because that is
        # what stage 4 can hand over -- `coerce_field_value_types` leaves a JSON
        # number numeric -- so mypy reads this line as a possible
        # AttributeError, the same class of abort that #442 and #450 were.
        # Nothing here is changed to absorb it: no M2 record in the 66-CV corpus
        # carries a numeric money field, and making the comparison total is a
        # behaviour change in an unobserved case, not a typing change. Left
        # visible on purpose rather than hidden behind a `str` annotation.
        if title and agency and title.strip().lower() == agency.strip().lower():
            # Agency and title are identical - keep as title only, clear agency
            agency = ''
        if title and total_funding and title.strip().lower() == str(total_funding).strip().lower():
            # Funding is same as title - clear funding
            total_funding = ''
            annual_direct_costs = ''
        role = fields.get('pi_role') or fields.get('role', '') or fields.get('description', '')
        start_date = fields.get('start_date', '') or fields.get('date', '')
        end_date = fields.get('end_date', '')
        grant_number = (fields.get('grant_number') or '').strip()

        has_title = bool(title and len(title.strip()) > 10)
        # For clinical trials: if we have a title and a date, that's substantive enough
        has_substantive_info = bool((agency and (role or start_date or end_date)) or (title and start_date))
        # A grant identified only by its number is still identifiable now that #478/#479
        # render grant_number in Award Source (see below) -- but require a second
        # corroborating field (agency, dates, role, or effort) so a bare fragment with
        # no number at all, or a number with nothing else, still gets rejected (#486).
        has_grant_number_info = bool(
            grant_number and (agency or role or start_date or end_date or fields.get('percent_effort'))
        )

        if not has_title and not has_substantive_info and not has_grant_number_info:
            # This is likely a header like "Funding: National Cancer Institute" - skip it
            if self.verbose:
                text = entry.get('text', '')[:50] if entry else ''
                logger.warning("  Skipping sparse grant entry: '%s...'", text)
            if isinstance(entry, dict):
                self._declined_grant_entries.append(entry)
            return None

        # Get role and determine PI name
        role = fields.get('pi_role') or fields.get('role', '')
        percent_effort = fields.get('percent_effort', '')

        # Fix: If role looks like a percent value (just a number, possibly with %), it was misextracted
        # The LLM sometimes puts percent effort in the pi_role field
        if role and not percent_effort:
            role_stripped = role.strip().rstrip('%').strip()
            if role_stripped.isdigit() or (role_stripped.replace('.', '', 1).isdigit()):
                # This looks like a percent value, not a role description
                percent_effort = role
                role = ''

        raw_text = entry.get('text', '') if entry else ''
        pi_name = resolve_pi_name(fields, raw_text, role, owner_name)

        cost_rows = _format_grant_costs(annual_direct_costs, total_funding)

        # Carry the grant/award identifier in Award Source. The WCM template has no
        # grant-number row -- its block is exactly these 8 rows plus optional goals --
        # but the Award Source label itself reads "(funding agency ...; type of grant)",
        # so the identifier belongs there. Without this, stage 4 extracts grant_number
        # and no renderer ever consumes it: 528 of 537 corpus values reached no render.
        # (grant_number itself was already extracted above, for the sparsity guard.)
        if grant_number and grant_number.casefold() not in f"{agency} {title}".casefold():
            agency = f"{agency} ({grant_number})" if agency else grant_number

        # Define the grant data model rows
        # Use the extracted title/agency variables (which check multiple field names) instead of just fields.get()
        rows = [
            ('Award Source:', agency),
            ('Project title:', title),
            *cost_rows,
            ('Non-financial support:', fields.get('non_financial_support', '')),
            ('Duration of support:', self._format_grant_duration(fields, code)),
            ('Name of Principal Investigator:', pi_name),
            ('Your role:', role),
            ('Your percent (%) effort:', percent_effort),
        ]

        # Add optional major goals if present (check major_goals, description, or narrative)
        goals = fields.get('major_goals') or fields.get('description') or fields.get('narrative') or ''
        # Same-text guard as title/agency/funding above -- same fact via two paths (#829, BYFQBG#82).
        goal_repeats_title = goals.strip().lower() == (title or '').strip().lower()
        if len(goals.strip()) > 10 and not goal_repeats_title:  # Only if substantive
            rows.append(('Major project goals:', goals))

        # Create a new table with 2 columns
        table = self.doc.add_table(rows=len(rows), cols=2)

        # Set table borders and formatting
        _set_table_border(table, color='808080', size=4)

        # Fill in the table
        first_cell_para = None
        for i, (label, value) in enumerate(rows):
            row = table.rows[i]
            # Label cell (bold)
            label_cell = row.cells[0]
            label_cell.text = label
            _set_cell_vertical_alignment(label_cell, 'center')
            for para in label_cell.paragraphs:
                if i == 0 and first_cell_para is None:
                    first_cell_para = para
                for run in para.runs:
                    _set_font(run, bold=True)

            # Value cell
            value_cell = row.cells[1]
            value_cell.text = str(value) if value else ''
            _set_cell_vertical_alignment(value_cell, 'center')
            for para in value_cell.paragraphs:
                for run in para.runs:
                    _set_font(run)

        # Add comments from entry
        if entry and first_cell_para:
            self._add_entry_comments(first_cell_para, entry)

        # Move table to correct position
        body = self.doc.element.body
        body_elements = list(body)

        # Determine insertion point
        if insert_after_element is not None:
            insert_element = insert_after_element
        else:
            # Fallback: find RESEARCH SUPPORT
            support_idx = self._find_paragraph_with_text("RESEARCH SUPPORT")
            if support_idx is None:
                return table
            insert_element = self.doc.paragraphs[support_idx]._element

        # Find where to insert and place table after the element
        try:
            elem_idx = body_elements.index(insert_element)
            body.insert(elem_idx + 1, table._tbl)
        except (ValueError, IndexError):
            pass

        return table

    def _format_grant_duration(
        self, fields: GrantFields, taxonomy_code: str = 'M2A'
    ) -> str:
        """Format grant duration according to WCM requirements.

        Grants use mm/yy format per the template.
        Clinical trials may use 'date' instead of 'start_date'.
        """
        start = fields.get('start_date', '') or fields.get('date', '')
        end = fields.get('end_date', '')

        return format_date_range(start, end, taxonomy_code)
