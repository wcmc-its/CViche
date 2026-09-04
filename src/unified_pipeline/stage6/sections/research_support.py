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

Logging rule for this module: no `logger` call carries a value taken from the
CV. Every one of them says what happened and why -- an effort figure was
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

try:
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

# Every stage-4 field key this section reads: the `fields.get(...)` slots in
# `_create_grant_table` and `_format_grant_duration`, plus `status`, which only
# the bucket rules read. Anything else stage 4 extracts for an M2 record reaches
# no row of the WCM grant block, and the module docstring's known gap is exactly
# that this happens silently -- so `_create_grant_table` names the leftovers at
# debug level. Adding a `fields.get('x')` above means adding 'x' here, or the
# key it now consumes still reads as dropped.
CONSUMED_GRANT_FIELDS = frozenset({
    'agency', 'funding_source', 'sponsor',
    'annual_direct_costs', 'total_funding',
    'co_investigators', 'pi_name', 'principal_investigator',
    'date', 'start_date', 'end_date',
    'description', 'major_goals', 'narrative',
    'grant_number', 'non_financial_support', 'percent_effort',
    'pi_role', 'role', 'status',
    'study_title', 'text', 'title', 'trial_title',
})

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
    unparseable, zero or negative, or over 100%.

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
            print(message)


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
        fields = entry.get('extracted_fields') or {}
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
        for entry in list(source_list):
            fields = entry.get('extracted_fields') or {}
            target, note = grant_status_rebucket_target(fields.get('status'))
            if not target or target == source_code:
                continue
            title = str(fields.get('title') or 'Unknown')
            if target not in REBUCKET_TARGET_CODES:
                raise UnsupportedRebucketTargetError(
                    f"grant_status_rebucket_target returned bucket {target!r} for "
                    f"a {source_code} grant '{title[:40]}'; research support renders "
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
        fields = entry.get('extracted_fields') or {}
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


def resolve_pi_name(fields: dict, raw_text: str, role: str, owner_name: str) -> str:
    """Resolve the principal investigator for one grant.

    Extracted field first, then the trailing cell of a pipe-delimited source row,
    then the CV owner when the role says they are the PI. Text in, text out: no
    docx, so the parser's heuristics are testable on their own (review thread
    3932312407 item 5).
    """
    pi_name = fields.get('pi_name') or fields.get('principal_investigator', '') or fields.get('co_investigators', '')

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
    if not pi_name and owner_name and 'principal' in role.lower() and 'investigator' in role.lower():
        pi_name = owner_name
    return pi_name


class ResearchSupportSection:
    """Section M2 writers, mixed into `WCMTemplateGenerator`."""

    def _fill_research_support(
        self,
        entries_by_code: dict[str, list[dict]],
        cv_owner: dict | None = None,
        document_uid: str = '',
        current_year: int | None = None,
    ):
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
            print(f"Filling Research Support ({total_grants} grants)...")

        # Process each category - find the corresponding section in the template
        for code, section_header, entries in categories:
            # Find the section header in the template
            section_idx = self._find_paragraph_with_text(section_header)
            if section_idx is None:
                if self.verbose:
                    print(f"  Warning: Could not find section header '{section_header}'")
                continue

            # Remove any existing template table after this section
            # (even if no entries, to avoid leaving empty template tables)
            existing_table = self._find_table_after_paragraph(section_idx)
            if existing_table:
                existing_table._element.getparent().remove(existing_table._element)

            if not entries:
                continue

            # Sort entries reverse chronologically (most recent first)
            sorted_entries = sort_entries_reverse_chronological(entries)

            if self.verbose:
                print(f"  {code}: {len(entries)} grants -> '{section_header}'")

            # Create a table for each grant with spacing between them
            # Track the last inserted element (as actual XML element reference)
            last_element = self.doc.paragraphs[section_idx]._element

            for i, entry in enumerate(sorted_entries):
                fields = entry.get('extracted_fields') or {}

                # Create grant table - pass the element to insert after and owner name
                grant_table = self._create_grant_table(fields, code, entry, insert_after_element=last_element, owner_name=owner_name)
                if grant_table:
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

    def _create_grant_table(
        self,
        fields: dict,
        code: str,
        entry: dict | None = None,
        insert_after_element=None,
        owner_name: str = '',
    ) -> Table | None:
        """Create an individual grant table with the WCM data model.

        Args:
            fields: Extracted field data for the grant
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
        # "Annual direct costs" row wants annual_direct_costs first. Both reads
        # go through these locals, so clearing a duplicate below no longer has to
        # write '' back into the caller's `fields` to make the second read agree
        # (review thread 3932312407 item 1).
        annual_direct_costs = fields.get('annual_direct_costs', '')
        total_funding = fields.get('total_funding', '') or annual_direct_costs

        # Detect and fix cross-field duplication where the same content appears in multiple fields
        # This happens when Stage 4 incorrectly puts the same text in agency, title, AND funding
        if title and agency and title.strip().lower() == agency.strip().lower():
            # Agency and title are identical - keep as title only, clear agency
            agency = ''
        if title and total_funding and title.strip().lower() == total_funding.strip().lower():
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
                print(f"  Skipping sparse grant entry: '{text}...'")
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

        # Format costs as currency
        costs = annual_direct_costs or total_funding
        costs_formatted = _format_currency(costs)

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
            ('Annual direct costs:', costs_formatted),
            ('Non-financial support:', fields.get('non_financial_support', '')),
            ('Duration of support:', self._format_grant_duration(fields, code)),
            ('Name of Principal Investigator:', pi_name),
            ('Your role:', role),
            ('Your percent (%) effort:', percent_effort),
        ]

        # Add optional major goals if present (check major_goals, description, or narrative)
        goals = fields.get('major_goals') or fields.get('description', '') or fields.get('narrative', '')
        if goals and len(goals.strip()) > 10:  # Only if substantive
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

    def _format_grant_duration(self, fields: dict, taxonomy_code: str = 'M2A') -> str:
        """Format grant duration according to WCM requirements.

        Grants use mm/yy format per the template.
        Clinical trials may use 'date' instead of 'start_date'.
        """
        start = fields.get('start_date', '') or fields.get('date', '')
        end = fields.get('end_date', '')

        return format_date_range(start, end, taxonomy_code)
