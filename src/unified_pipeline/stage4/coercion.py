"""Value coercion and normalisation for stage 4 extracted fields.

Moved verbatim from `stage_4_field_extractor.py` (#498), which re-exports every
name here. Pure functions over extracted-field dicts: no LLM calls, no I/O.
"""

import re
from typing import Any, TypedDict

from unified_pipeline.core.two_digit_year import expand_two_digit_year


class ExtractedFields(TypedDict, total=False):
    """One entry's stage-4 extracted fields, keyed by schema field name.

    Every key is optional (``total=False``): an entry carries only the fields
    its own taxonomy code's schema declares, so any single dict holds a small
    subset of the names below and no key is guaranteed. Values stay ``Any``
    on purpose -- stage 4 stores raw LLM JSON (the extraction call uses
    ``response_format={"type": "json_object"}`` with no schema), so a field
    the prompt asked for as a string can arrive as a list, a number or None.
    ``coerce_field_value_types`` below narrows the common list case; it does
    not make any field's type guaranteed, so claiming ``str`` here would be
    a promise nothing keeps.

    The key set is the union of every field name the active schemas declare.
    Hand-kept, not a live lookup, for the same reason
    ``DATE_RANGE_TAXONOMY_CODES`` below is: coercion.py may not import
    stage4.schemas (module boundary, stage4/__init__.py -- schemas, coercion
    and owner_name import nothing from each other). A TypedDict's keys must
    be literals in any case, so no arrangement of modules could derive them
    at runtime. Computed from ``schemas.get_active_schemas()`` at
    ``fix/556-date-range-reconciliation`` @ b1d87e7 (2026-09-04);
    ``test_extracted_fields_typeddict_matches_active_schemas`` is the drift
    guard, and imports both modules -- which a test may do and this module
    may not.

    Not a validated shape, and measurably not the observed one:
    ``_ExtractedEntryFields`` in extraction.py is ``extra="allow"``, so an
    LLM response may carry keys outside this set -- and does. Measured
    2026-09-04 over the 66-CV local corpus
    (``outputs/stage_4_field_extraction``): 568 of the 10737 entries that
    carry an ``extracted_fields`` dict -- 5.3% -- hold at least one of 74
    distinct undeclared keys, led by ``text`` (133 entries), ``event_name``
    (116), ``learner_level`` (58), ``division_department`` (51) and
    ``primary_email`` (48), tailing off into ``column_1``..``column_12``.
    They are LLM output noise, not a declared contract, and are
    deliberately absent from the list above: adding them would break the
    ``get_active_schemas()`` drift guard that is what makes this type mean
    anything. They ride through ``apply_regex_post_processing``'s
    ``.copy()`` untouched; what this type documents is the declared
    surface, which is what every helper below keys off.

    That 5.3% gap is inert only because nothing type-checks it: both origin
    sites declare the dict ``Any`` (``recovered_fields`` on the recovery
    path, ``extracted_fields`` on the main path, both in extraction.py),
    and neither file is in mypy's scope (``mypy.ini`` lists only
    ``segmentation_regression.py``). Narrow either annotation, or add
    either file to that scope, and the same 568 entries turn this
    docstring's "declared surface" framing into a live inaccuracy -- a
    checker would then be asked to reconcile an observed key set against a
    declared one that does not contain it. The answer at that point is an
    explicit ``dict[str, Any]`` bridge at the boundary, not noise keys
    here.
    """

    abstract_number: Any
    activity: Any
    activity_title: Any
    address: Any
    affiliation_type: Any
    agency: Any
    annual_funding: Any
    audience: Any
    authors: Any
    award_name: Any
    book_title: Any
    certificate_number: Any
    certifying_board: Any
    chapter_title: Any
    clinical_role: Any
    co_investigators: Any
    committee_name: Any
    conference_name: Any
    content_type: Any
    course_title: Any
    current_position: Any
    date: Any
    dates_attended: Any
    dea_number: Any
    degree: Any
    description: Any
    discipline: Any
    doi: Any
    edition: Any
    editors: Any
    email: Any
    employer: Any
    end_date: Any
    expiration_date: Any
    fte_percentage: Any
    funding_source: Any
    google_scholar_url: Any
    grant_number: Any
    grant_title: Any
    granting_body: Any
    institution: Any
    inventors: Any
    involves_trainees: Any
    isbn: Any
    issue: Any
    issue_date: Any
    journal: Any
    journal_name: Any
    launch_date: Any
    leadership_role: Any
    license_number: Any
    location: Any
    media_type: Any
    membership_type: Any
    mentee_level: Any
    mentee_name: Any
    name: Any
    narrative: Any
    notes: Any
    npi_number: Any
    orcid: Any
    organization: Any
    output_type: Any
    pages: Any
    patent_number: Any
    percent_effort: Any
    phone: Any
    pi_name: Any
    pi_role: Any
    pmcid: Any
    pmid: Any
    program_name: Any
    project_name: Any
    publication_venue: Any
    publisher: Any
    pubmed_url: Any
    recertification_date: Any
    report_number: Any
    research_focus: Any
    role: Any
    scopus_id: Any
    setting: Any
    site_position: Any
    specialty: Any
    start_date: Any
    state_country: Any
    status: Any
    submission_date: Any
    target_journal: Any
    target_name: Any
    teaching_role: Any
    thesis_title: Any
    title: Any
    total_funding: Any
    total_funding_requested: Any
    training_type: Any
    unit_program: Any
    url: Any
    venue: Any
    volume: Any
    year: Any
    year_certified: Any


class ReformattedField(TypedDict):
    """One ``reformatted`` entry: what a post-processor changed, and why.

    Total (no ``total=False``): all three keys are written at every site in
    this module, so a partial record is a bug, not a variant.

    ``original`` is ``Any`` rather than ``str`` because the producers
    disagree in kind: the regex extractors store the matched source
    substring (a ``str``), the date-range repair stores the pre-repair
    field value, which is falsy by construction -- ``None`` or ``""`` -- since
    that repair only runs on an empty field, and the two-digit century repair
    stores the pre-repair value, which may be an ``int`` year.
    """

    original: Any
    reformatted: str
    reason: str


class ReformattedFields(TypedDict, total=False):
    """The reformatted-value tracking dict, keyed by the field it reports on.

    ``apply_regex_post_processing`` builds this empty and threads it through
    every helper below, each of which adds its own key in place -- and only
    when it actually changed something, hence ``total=False``. The key set is
    closed on purpose: this module is the only producer (extraction.py stores
    the result as an entry's ``reformatted_fields`` and counts non-empty ones
    for ``entries_reformatted``; nothing else writes it), so a new
    post-processor adds its key here.
    """

    authors: ReformattedField
    date: ReformattedField
    dates_attended: ReformattedField
    doi: ReformattedField
    end_date: ReformattedField
    expiration_date: ReformattedField
    issue_date: ReformattedField
    launch_date: ReformattedField
    notes: ReformattedField
    orcid: ReformattedField
    percent_effort: ReformattedField
    pmcid: ReformattedField
    pmid: ReformattedField
    recertification_date: ReformattedField
    start_date: ReformattedField
    submission_date: ReformattedField
    title: ReformattedField
    year: ReformattedField
    year_certified: ReformattedField


def coerce_field_value_types(extracted_fields: dict[str, Any]) -> dict[str, Any]:
    """Coerce LLM-extracted field values to the scalar types downstream stages assume.

    Stage 4 stores raw LLM JSON (the extraction call uses
    ``response_format={"type": "json_object"}`` with no schema), so a field the
    prompt asks for as a string can legitimately come back as a list of strings.
    This is common in practice -- e.g. ``narrative`` is a list in 55/65 sample
    outputs, and ``training_type``/``program_name``/``description``/``specialty``/
    ``start_date``/``end_date`` have all been observed as lists. Downstream code
    then calls ``.strip()``, ``.lower()``, ``.split()``, ``re.search()``,
    ``< 0.7`` or ``", ".join([...])`` on the value and crashes the entire run
    (e.g. "Pipeline failed -- expected str instance, list found", or a
    ``'<' not supported between instances of 'str' and 'int'`` TypeError).
    stage_6_word_template.py already patches a handful of fields ad hoc with
    ``isinstance(x, list)`` checks; this normalizes every field once, centrally,
    before any consumer sees it.

    The rule is deliberately conservative -- it only touches values that would
    otherwise crash a string/number consumer:

    - a list whose items are all scalars -> ``"; "``-joined string of the
      non-empty items (mirrors the ``"; ".join(...)`` convention already used in
      stage_6_word_template.py)
    - everything else is returned untouched, so numeric fields (``year``,
      ``volume``) stay numeric, structured fields (``locations`` and other
      list-of-dict / dict values) keep their shape, and ``None`` stays ``None``.

    Raises:
        TypeError: if ``extracted_fields`` is not a dict. The declared return
            type is ``dict[str, Any]``; silently handing back a non-dict input
            unchanged would violate that contract for any caller that skips
            its own type check. Callers on an untrusted-JSON boundary (e.g.
            LLM output) must validate/guard before calling this function, not
            rely on it to no-op.
    """
    if not isinstance(extracted_fields, dict):
        raise TypeError(
            f"extracted_fields must be a dict, got {type(extracted_fields).__name__}"
        )

    coerced = {}
    for key, value in extracted_fields.items():
        if isinstance(value, list) and all(
            item is None or isinstance(item, (str, int, float)) for item in value
        ):
            coerced[key] = "; ".join(
                str(item).strip() for item in value if item not in (None, "")
            )
        else:
            coerced[key] = value
    return coerced

def normalize_dates(extracted_fields: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize date fields by splitting ranges into start_date and end_date.

    Handles formats like:
    - "YYYY-YYYY" → start_date: YYYY, end_date: YYYY
    - "YYYY-present" → start_date: YYYY, end_date: "present"
    - "YYYY-MM-YYYY-MM" → start_date: YYYY-MM, end_date: YYYY-MM

    Args:
        extracted_fields: Dictionary of extracted fields

    Returns:
        Dictionary with normalized date fields (always uses start_date/end_date).
        Existing non-empty canonical start_date/end_date values take precedence
        over anything derived from a raw date-range field (date/year/years/...):
        a value split out of the raw field only fills in a canonical field that
        is missing or empty, it never overwrites one that is already set.
    """
    normalized = extracted_fields.copy()

    # Fields that might contain date ranges
    date_field_names = ['date', 'year', 'years', 'years_taught', 'dates', 'period']

    for field_name in date_field_names:
        if field_name not in normalized:
            continue

        value = normalized[field_name]
        if not value or not isinstance(value, str):
            continue

        value_str = str(value).strip()

        # Pattern 1: YYYY-YYYY (e.g., "2015-2016")
        match = re.match(r'^(\d{4})-(\d{4})$', value_str)
        if match:
            start_year, end_year = match.groups()
            # Standardized start_date/end_date field names -- but never
            # clobber a canonical value that is already set (#3819068608).
            if not normalized.get('start_date'):
                normalized['start_date'] = start_year
            if not normalized.get('end_date'):
                normalized['end_date'] = end_year
            # Remove original field to avoid duplication
            del normalized[field_name]
            continue

        # Pattern 2: YYYY-present (e.g., "2009-present", "2009-2025" where 2025 might mean ongoing)
        match = re.match(r'^(\d{4})-(present|ongoing|current)$', value_str, re.IGNORECASE)
        if match:
            start_year = match.group(1)
            if not normalized.get('start_date'):
                normalized['start_date'] = start_year
            if not normalized.get('end_date'):
                normalized['end_date'] = 'present'
            del normalized[field_name]
            continue

        # Pattern 3: YYYY-MM-YYYY-MM (e.g., "2015-06-2016-08")
        match = re.match(r'^(\d{4}-\d{2})-(\d{4}-\d{2})$', value_str)
        if match:
            start_date, end_date = match.groups()
            if not normalized.get('start_date'):
                normalized['start_date'] = start_date
            if not normalized.get('end_date'):
                normalized['end_date'] = end_date
            del normalized[field_name]
            continue

    return normalized

# ============================================================================
# Regex Post-Processing Patterns
# ============================================================================
REGEX_PATTERNS = {
    'pmid': r'PMID[:\s]*(\d{7,8})',
    'pmcid': r'PMC(\d+)',
    'doi': r'(10\.\d{4,}/[^\s\]>\),]+)',
    'orcid': r'(\d{4}-\d{4}-\d{4}-\d{3}[\dX])',
}

def normalize_authors_vancouver(authors_string: str) -> str:
    """
    Normalize author string to Vancouver citation style.

    Vancouver format: LastName AB, LastName CD, LastName EF, et al.
    - No periods in initials
    - No comma between last name and initials
    - Authors separated by commas
    - "et al." at end (with period)

    Examples:
        "Smith, John A., Jones, Mary B." → "Smith JA, Jones MB"
        "Smith J.A., Jones M.B." → "Smith JA, Jones MB"
        "Smith, J. A. and Jones, M. B." → "Smith JA, Jones MB"
        "Smith JA, Jones MB, et al.," → "Smith JA, Jones MB, et al."

    Args:
        authors_string: Original author string in any format

    Returns:
        Normalized Vancouver-style author string
    """
    if not authors_string:
        return authors_string

    result = authors_string

    # Step 1: Normalize "et al" variations at the end
    # Capture and temporarily remove et al to process authors
    et_al_match = re.search(r',?\s*(et\.?\s*al\.?)\s*[,;\.]*\s*$', result, re.IGNORECASE)
    has_et_al = bool(et_al_match)
    if has_et_al:
        result = result[:et_al_match.start()]

    # Step 2: Replace "and" / "&" with comma for consistent splitting
    result = re.sub(r'\s+and\s+', ', ', result, flags=re.IGNORECASE)
    result = re.sub(r'\s*&\s*', ', ', result)

    # Step 3: Split into individual authors
    # Split on comma, semicolon, or period followed by space and capital letter
    authors = re.split(r'[;]\s*|,\s*(?=[A-Z])', result)

    normalized_authors = []
    for author in authors:
        author = author.strip()
        if not author:
            continue

        # Try to parse "LastName, FirstName MiddleName" format
        # e.g., "Smith, John Albert" or "Smith, J. A." or "Smith, JA"
        # Unicode-aware (not ASCII-only) so accented surnames (e.g. "Garcia"
        # with an accent, "Muller" with an umlaut) and compound surnames with
        # internal hyphens/apostrophes/spaces (e.g. "de la Cruz", "O'Brien",
        # "Garcia-Lopez") match instead of silently falling through to the
        # less-accurate "FirstName LastName" / cleanup paths below. `[^\W\d_]`
        # is any Unicode letter; digits, underscore, and other punctuation
        # stay excluded.
        comma_match = re.match(
            r"^([^\W\d_][^\W\d_\-'\s]*(?:[-'\s][^\W\d_]+)*),\s*(.+)$", author
        )
        if comma_match:
            last_name = comma_match.group(1)
            first_parts = comma_match.group(2)

            # Extract initials from first/middle names
            initials = extract_initials(first_parts)
            if initials:
                normalized_authors.append(f"{last_name} {initials}")
                continue

        # Try "FirstName LastName" format (less common in citations)
        # e.g., "John A. Smith" or "John Albert Smith"
        space_parts = author.split()
        if len(space_parts) >= 2:
            # Check if last part looks like a last name (not initials)
            if len(space_parts[-1]) > 2 and not re.match(r'^[A-Z]+$', space_parts[-1]):
                last_name = space_parts[-1]
                first_parts = ' '.join(space_parts[:-1])
                initials = extract_initials(first_parts)
                if initials:
                    normalized_authors.append(f"{last_name} {initials}")
                    continue

        # Already in "LastName AB" format or couldn't parse - clean up periods
        cleaned = re.sub(r'\.(?=[A-Z]|\s|$)', '', author)  # Remove periods from initials
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()  # Normalize spaces
        if cleaned:
            normalized_authors.append(cleaned)

    # Step 4: Rejoin with commas
    result = ', '.join(normalized_authors)

    # Step 5: Add back "et al." if it was present
    if has_et_al:
        result = result.rstrip(',').strip() + ', et al.'

    # Step 6: Clean up any trailing punctuation
    result = re.sub(r'[,;\.]+\s*$', '', result)
    if has_et_al:
        result += '.'  # et al. needs the period

    return result

def extract_initials(name_parts: str) -> str:
    """
    Extract initials from first/middle name string.

    Examples:
        "John Albert" → "JA"
        "J. A." → "JA"
        "JA" → "JA"
        "John A." → "JA"

    Args:
        name_parts: First and middle names as string

    Returns:
        Uppercase initials without periods or spaces
    """
    if not name_parts:
        return ""

    # If already looks like initials (all caps, 1-3 chars), just clean periods
    cleaned = re.sub(r'[\.\s]', '', name_parts)
    if re.match(r'^[A-Z]{1,4}$', cleaned):
        return cleaned

    # Extract first letter of each word/initial
    initials = []
    parts = re.split(r'[\s\.]+', name_parts)
    for part in parts:
        part = part.strip()
        if part:
            initials.append(part[0].upper())

    return ''.join(initials)

#: Taxonomy codes/prefixes gating which regex post-processors below apply to
#: a given entry. Named per CODING_STANDARDS.md 8.2 (no bare literals in the
#: gating logic).
IDENTIFIER_TAXONOMY_PREFIX = 'S'          # S1, S2, ... -- scholarship/publication codes
IDENTIFIER_TAXONOMY_CODES = ('R', 'N4')   # non-'S' codes that also carry PMID/PMCID/DOI
ORCID_TAXONOMY_CODES = ('A', 'S0')        # profile-section codes that carry an ORCID
GRANT_EFFORT_TAXONOMY_PREFIX = 'M2'       # grant entries where percent-effort/FTE applies

#: Codes whose active field schema (schemas.get_active_schemas()) declares
#: BOTH start_date and end_date, so a dropped end can safely be restored from
#: the entry's own text (#556). Hand-kept, not a live lookup: coercion.py may
#: not import stage4.schemas (module boundary, stage4/__init__.py -- schemas,
#: coercion and owner_name import nothing from each other), so this mirrors
#: IDENTIFIER_TAXONOMY_CODES/ORCID_TAXONOMY_CODES above rather than calling
#: get_field_schema(). Computed from get_active_schemas() on origin/dev @
#: 2109c5b (2026-09-01); re-derive if a listed code's declared fields change,
#: or a code gains both dates.
DATE_RANGE_TAXONOMY_CODES = (
    'B2', 'C', 'D1', 'D2', 'D3', 'I', 'K1', 'K2', 'K3', 'L1', 'L3',
    'M2', 'M2A', 'M2B', 'N1', 'N2', 'N3', 'N3B',
    'O', 'P', 'Q1', 'Q2', 'Q3', 'Q4', 'Q4A', 'Q4B', 'Q4C', 'Q4D',
)

#: A present/ongoing marker anywhere in the entry text blocks the date-range
#: repair below -- reuses the vocabulary normalize_dates already recognises
#: (pattern 2 above) plus "presents"/"currently", both traced to real
#: "-Present" renders during the #556 investigation.
_PRESENT_MARKER_PATTERN = re.compile(r'\b(?:presents?|ongoing|current(?:ly)?)\b', re.IGNORECASE)

#: A single unambiguous closed 4-digit year range: "YYYY-YYYY", "YYYY–YYYY",
#: "YYYY—YYYY", or "YYYY to YYYY". More than one match in an entry's text
#: means the text is ambiguous about which range belongs to this entry (#556).
CLOSED_DATE_RANGE_PATTERN = re.compile(
    r'(?<!\d)(\d{4})(?:\s*[-–—]\s*|\s+to\s+)(\d{4})(?!\d)',
    re.IGNORECASE,
)

#: Plausible calendar-year bounds for a range CLOSED_DATE_RANGE_PATTERN
#: matched (#556 round-1 finding 1). The pattern's digit-pair shape also
#: matches non-date content that happens to sit next to a hyphen -- a course
#: code ("MSELCT 5130-1020"), a catalog number, a zip+4, a dollar range --
#: and without this check such a pair would be "repaired" into a rendered
#: date. CVs in this corpus span the 20th century to a few years out; the
#: bounds are widened generously to avoid rejecting a real historical entry.
_MIN_PLAUSIBLE_YEAR = 1900
_MAX_PLAUSIBLE_YEAR = 2100


def find_single_closed_range(text: str) -> tuple[str, str] | None:
    """The one plausible closed 4-digit year range in `text` as
    (start, end) strings, or None when the text has a present/ongoing
    marker, no range, more than one range, or an implausible one. Shared by
    `reconcile_date_range` (which repairs from it) and the doctor's
    `wrong_start_date` lint (which only reports on it, #729) so the two
    cannot disagree about what "exactly one closed range" means."""
    if not text or _PRESENT_MARKER_PATTERN.search(text):
        return None
    matches = CLOSED_DATE_RANGE_PATTERN.findall(text)
    if len(matches) != 1:
        return None
    range_start, range_end = matches[0]
    if not (_MIN_PLAUSIBLE_YEAR <= int(range_start) <= int(range_end) <= _MAX_PLAUSIBLE_YEAR):
        return None
    return range_start, range_end


def reconcile_date_range(
    original_text: str, updated: ExtractedFields, reformatted: ReformattedFields
) -> None:
    """Restore a closed date range's end_date when the LLM dropped it (#556).

    The caller (`apply_regex_post_processing`) has already confirmed the
    entry's taxonomy code declares both start_date and end_date. Beyond that
    this only repairs when ALL of the following hold:

    - `end_date` is empty. A populated end_date, even a wrong one, is left
      alone -- this repairs a *dropped* end, not a wrong one.
    - `original_text` has no present/ongoing/current marker anywhere. An
      entry that is genuinely ongoing must keep rendering "-Present";
      `formatting/dates.py:149-153` is correct given that input and is not
      touched by this function.
    - `original_text` contains exactly one closed 4-digit year range. More
      than one match means the text is ambiguous about which range applies
      to this entry, so it is left untouched.
    - the matched range is a plausible calendar-year span: both halves fall
      within `_MIN_PLAUSIBLE_YEAR`..`_MAX_PLAUSIBLE_YEAR` and start <= end.
      `CLOSED_DATE_RANGE_PATTERN` matches any two 4-digit numbers joined by a
      hyphen/dash/"to", not only years -- a course code ("MSELCT 5130-1020"),
      catalog number, zip+4, or dollar range would otherwise be "repaired"
      into a rendered date (#556 round-1 finding 1).

    When `start_date` is already set and disagrees with the range's start
    year, the repair is skipped entirely (both fields left as extracted)
    rather than partially applied: overwriting a value the model already
    committed to is a bigger step than filling in one it left blank, and
    skipping leaves an honest trail -- no `reformatted_fields` entry, so no
    false report of a repair -- instead of silently substituting a third,
    unreviewed value. Concretely, this means the FSMB case from #556 (source
    text "2025-2026" extracted as `start_date=2026, end_date=None`, i.e. the
    model took the range's *end* as the start) is not repaired here; only
    the shape where `start_date` is missing or already agrees with the text
    is. "Agrees" is an exact string comparison against the matched 4-digit
    year (`str(existing_start).strip() != range_start`), not a year-only
    comparison, so a month-qualified `start_date` such as "Sep 2018" also
    reads as disagreement against text "2018-2020" and blocks the repair --
    conservative (fails closed) rather than a defect, disclosed per #556
    round-1 finding 5.

    Args:
        original_text: Original CV entry text.
        updated: Extracted fields for the entry, mutated in place.
        reformatted: Reformatted-value tracking dict, mutated in place.
            Gains `start_date`/`end_date` keys only when a repair applies.
    """
    if updated.get('end_date'):
        return
    closed_range = find_single_closed_range(original_text)
    if closed_range is None:
        return
    range_start, range_end = closed_range

    existing_start = updated.get('start_date')
    if existing_start and str(existing_start).strip() != range_start:
        return

    original_end = updated.get('end_date')
    updated['end_date'] = range_end
    reformatted['end_date'] = {
        'original': original_end,
        'reformatted': range_end,
        'reason': 'Restored end_date from a single closed date range in source text',
    }
    if not existing_start:
        updated['start_date'] = range_start
        reformatted['start_date'] = {
            'original': existing_start,
            'reformatted': range_start,
            'reason': 'Restored start_date from a single closed date range in source text',
        }


#: Every date-valued field the active schemas declare -- the fields whose year
#: the two-digit century repair below may rewrite. Hand-kept for the same
#: reason as DATE_RANGE_TAXONOMY_CODES (coercion.py may not import
#: stage4.schemas); `test_date_field_names_match_declared_date_fields` is the
#: drift guard. Undeclared keys the LLM invents are left alone: a repair is
#: recorded in ReformattedFields, whose key set is closed.
DATE_FIELD_NAMES = (
    'date', 'dates_attended', 'end_date', 'expiration_date', 'issue_date',
    'launch_date', 'recertification_date', 'start_date', 'submission_date',
    'year', 'year_certified',
)

#: A 19xx year inside an extracted date value. Only 19xx is a repair
#: candidate: the observed failure is the LLM reading "03" as 1903, never the
#: reverse. Pulling 20yy back to 19yy for yy above the pivot would break real
#: future dates -- the 163-CV farm has a grant whose m/d/yy end date lies
#: just past the pivot, which the LLM correctly read as 20yy.
_TWENTIETH_CENTURY_YEAR_PATTERN = re.compile(r'(?<!\d)19(\d\d)(?!\d)')

#: What may precede a two-digit year in the source text for it to count as
#: one: a one- or two-digit number and a slash -- the month of m/yy, or the
#: day of m/d/yy -- or an apostrophe ('03, straight or curly). The two digits
#: themselves must end the number.
_TWO_DIGIT_YEAR_PREFIX = r"(?:(?<!\d)\d{1,2}/|['\u2018\u2019])"

_TWO_DIGIT_CENTURY_REASON = 'Re-derived the century of a two-digit source year'


def _recentury_year(year_match: re.Match[str], original_text: str) -> str:
    """The year `year_match` matched in a date value, moved to 20yy when the
    LLM put a year the text writes as 20yy, or as a two-digit token, in the
    wrong century; otherwise the matched year unchanged.

    The year moves only when the 19xx year is not written out anywhere in
    the text (so the text does not support it), and then either the text
    writes 20yy in full ("6/30/2014" stored as 1914, #1248) or it carries
    the two digits as a two-digit year token. For the token the shared
    pivot decides the century, so "5/65" still reads as 1965 and nothing
    changes.
    """
    year, yy = year_match.group(0), year_match.group(1)
    if re.search(rf'(?<!\d){year}(?!\d)', original_text):
        return year
    if re.search(rf'(?<!\d)20{yy}(?!\d)', original_text):
        return f'20{yy}'
    if not re.search(rf'{_TWO_DIGIT_YEAR_PREFIX}{yy}(?!\d)', original_text):
        return year
    return str(expand_two_digit_year(int(yy)))


def _recentury_date_string(value: str, original_text: str) -> str:
    """`value` ("1903-01-14", "1902") with every wrongly-centuried 19xx year
    in it moved to 20xx (see `_recentury_year`)."""
    return _TWENTIETH_CENTURY_YEAR_PATTERN.sub(
        lambda year_match: _recentury_year(year_match, original_text), value
    )


def _recentury_date_value(value: object, original_text: str) -> object:
    """A date field's value with its century repaired, keeping its type.

    A string is rewritten in place and an int year stays an int. Anything
    else -- None, a bool, a list, a dict -- is returned as is: no
    two-digit-sourced year sits inside a nested value in either corpus farm
    (163 + 12 CVs, measured 2026-10-02).
    """
    if isinstance(value, str):
        return _recentury_date_string(value, original_text)
    if isinstance(value, int) and not isinstance(value, bool):
        return int(_recentury_date_string(str(value), original_text))
    return value


def repair_two_digit_year_century(
    original_text: str, updated: ExtractedFields, reformatted: ReformattedFields
) -> None:
    """Move a 19xx year the LLM read off a two-digit source year to 20xx.

    The stage-4 LLM sometimes reads "10/08" or "3/22/05" as 1908 or 1905
    rather than 2008 or 2005. Nothing downstream can see the error: by stage
    6 the value is a plain four-digit year. This applies the century pivot
    stage 6 uses for raw mm/dd/yy strings (core/two_digit_year.py) to each
    declared date field, rewriting only years the source text does not
    contain as four digits but does contain as a two-digit token (see
    `_recentury_year`). Each rewritten field gets a `reformatted` record.
    """
    for field_name in DATE_FIELD_NAMES:
        value = updated.get(field_name)
        repaired = _recentury_date_value(value, original_text)
        if repaired == value:
            continue
        updated[field_name] = repaired
        reformatted[field_name] = {
            'original': value,
            'reformatted': str(repaired),
            'reason': _TWO_DIGIT_CENTURY_REASON,
        }


#: A stored date value that names a month: "1997-03" or "1997-03-01".
_DATED_VALUE_PATTERN = re.compile(r'(\d{4})-(\d{2})(?:-(\d{2}))?')

#: The first three letters of each month name, in calendar order: a matched
#: month name maps to its number by them.
_MONTH_NAME_PREFIXES = (
    'jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec',
)

#: A numeric source date: m/d/yy, m/d/yyyy, m/yy or m/yyyy. The day is
#: optional. The year is matched at any length up to four digits so that a
#: cut-off year ("05/01/202") counts as a date whose year is unreadable.
_NUMERIC_SOURCE_DATE_PATTERN = re.compile(
    r'(?<![\d/])(\d{1,2})/(?:(\d{1,2})/)?(\d{1,4})(?![\d/])')

#: The year lengths a numeric source date can be read from.
_READABLE_YEAR_LENGTHS = (2, 4)

#: A month-name source date with a four-digit year: "June 2011",
#: "Nov. 4, 2015", "Sept 15 2004". Only a month's full name or its usual
#: abbreviation counts, so "Marine 2010" or "Junior 2015" is not a date.
_NAMED_SOURCE_DATE_PATTERN = re.compile(
    r'\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?'
    r'|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b'
    r'\.?\s+(?:(\d{1,2}),?\s+)?(\d{4})(?!\d)',
    re.IGNORECASE,
)

_YEAR_NOT_IN_SOURCE_REASON = 'Re-derived a year the source text does not contain'
_YEAR_NOT_IN_SOURCE_NULLED_REASON = (
    'Cleared a year the source text does not contain and gives no single year for')


def _source_writes_year(year: str, original_text: str) -> bool:
    """True when `original_text` has `year` written out, or its last two
    digits as a number of their own ("10/01/98", "'98", "1997-98")."""
    return bool(
        re.search(rf'(?<!\d){year}(?!\d)', original_text)
        or re.search(rf'(?<!\d){year[2:]}(?!\d)', original_text)
    )


def _source_years_for_month(
    month: int, day: int | None, original_text: str
) -> set[int | None]:
    """Every year `original_text` gives a date in `month` (and on `day`, when
    both the value and the source date carry a day); None stands for a
    matching date whose year is cut off or otherwise unreadable."""
    years: set[int | None] = set()
    for match in _NUMERIC_SOURCE_DATE_PATTERN.finditer(original_text):
        source_month, source_day, source_year = match.groups()
        if int(source_month) != month:
            continue
        if day is not None and source_day is not None and int(source_day) != day:
            continue
        if len(source_year) not in _READABLE_YEAR_LENGTHS:
            years.add(None)
        elif len(source_year) == 4:
            years.add(int(source_year))
        else:
            years.add(expand_two_digit_year(int(source_year)))
    for match in _NAMED_SOURCE_DATE_PATTERN.finditer(original_text):
        month_name, source_day, source_year = match.groups()
        if _MONTH_NAME_PREFIXES.index(month_name[:3].lower()) + 1 != month:
            continue
        if day is not None and source_day is not None and int(source_day) != day:
            continue
        years.add(int(source_year))
    return years


def _year_not_in_source_repair(value: str, original_text: str) -> tuple[str | None, str] | None:
    """`(repaired value, reason)` for a dated `value` whose year the source
    text does not contain, or None to leave it alone.

    Left alone: a value without a month (a bare year carries nothing to
    re-derive it from), a year the text writes in full or as its two digits,
    a value whose month the text gives no date for -- an entry split across
    elements can carry its year in a neighbour, and nothing here can tell
    that from a wrong year -- and a value whose month the text gives a date
    with an unreadable year for ("05/01/202"): the stored year may be the
    LLM's reading of it. Re-derived when the text's dates in that month (and
    on that day) all give one year; cleared when they give more than one.
    """
    dated = _DATED_VALUE_PATTERN.fullmatch(value.strip())
    if dated is None:
        return None
    year, month, day = dated.groups()
    if _source_writes_year(year, original_text):
        return None
    source_years = _source_years_for_month(
        int(month), int(day) if day else None, original_text)
    if not source_years or None in source_years:
        return None
    if len(source_years) > 1:
        return None, _YEAR_NOT_IN_SOURCE_NULLED_REASON
    (source_year,) = source_years
    return f'{source_year}{value.strip()[4:]}', _YEAR_NOT_IN_SOURCE_REASON


def repair_year_not_in_source(
    original_text: str, updated: ExtractedFields, reformatted: ReformattedFields
) -> None:
    """Re-derive, or clear, a dated field's year the source text does not
    contain.

    The stage-4 LLM sometimes writes a year the entry never states: "3/01/17"
    stored as 1997-03-01, or "June 2011" stored with the previous entry's
    year (class E26, 2026-10-02 EBYSBC autopsy: HFAJCC, VVRTUC). The year is
    plausible, so nothing downstream sees it. Runs after the century repair,
    so a year that repair moved is already supported. An empty
    `original_text` (an earlier record of a multi-record entry) gives no
    source date, so it changes nothing.
    """
    for field_name in DATE_FIELD_NAMES:
        value = updated.get(field_name)
        if not isinstance(value, str):
            continue
        repair = _year_not_in_source_repair(value, original_text)
        if repair is None:
            continue
        repaired, reason = repair
        updated[field_name] = repaired
        reformatted[field_name] = {
            'original': value, 'reformatted': repaired or '', 'reason': reason,
        }


#: The two range fields the two repairs below read and rewrite.
_RANGE_FIELD_NAMES = ('start_date', 'end_date')

#: A stored date value shaped year-month: "1999-02".
_YEAR_MONTH_VALUE_PATTERN = re.compile(r'(\d{4})-(\d{2})')

#: The longest span a "YYYY-YY" source token may read as. Past it the two
#: digits are taken as the month stage 4 read them as ("2005-03" would
#: otherwise end in 2103). A trainee period or appointment written this way
#: runs a few years; 20 leaves room without reaching a second century.
_MAX_SHORT_RANGE_SPAN_YEARS = 20

_SHORT_RANGE_REASON = 'Read a YYYY-YY source range written into one date field'
_MONTH_SLASH_YEAR_REASON = 'Re-read an MM/YY source date stage 4 read as YY/MM'


def _short_range_end_year(start_year: int, yy: int) -> int | None:
    """The year "YYYY-yy" ends in: the first year after `start_year` whose
    last two digits are `yy` ("1999-02" -> 2002, "1999-00" -> 2000), or None
    when that is more than `_MAX_SHORT_RANGE_SPAN_YEARS` away."""
    end_year = start_year // 100 * 100 + yy
    if end_year <= start_year:
        end_year += 100
    if end_year - start_year > _MAX_SHORT_RANGE_SPAN_YEARS:
        return None
    return end_year


def _source_writes_short_range(original_text: str, year: str, yy: str) -> bool:
    """True when the source writes `year`-`yy` as one dash-joined token that
    is not the start of a longer date ("1999-02-15", "1999-02/03")."""
    return bool(re.search(
        rf'(?<![\d/]){year}\s*[-\u2013\u2014]\s*{yy}(?![\d/]|-\d)', original_text))


def split_year_short_range(
    original_text: str, updated: ExtractedFields, reformatted: ReformattedFields
) -> None:
    """Split a "YYYY-YY" source range stage 4 stored whole in one field.

    The LLM copies a CV's "1999-02" (1999 to 2002) into end_date, or
    start_date, verbatim, with the other field empty -- and every reader
    downstream takes it as February 1999 (class 2, 2026-10-02 s7ab autopsy:
    RXYBVF, 10 mentee periods rendered blank). Repaired only when the other
    range field is empty, the stored value is exactly that token, the source
    text writes it as a dash-joined token, and the end it implies lies
    after the start and within `_MAX_SHORT_RANGE_SPAN_YEARS`.
    """
    for field_name, other_name in (_RANGE_FIELD_NAMES, _RANGE_FIELD_NAMES[::-1]):
        value = updated.get(field_name)
        if not isinstance(value, str) or updated.get(other_name):
            continue
        token = _YEAR_MONTH_VALUE_PATTERN.fullmatch(value.strip())
        if token is None:
            continue
        year, yy = token.groups()
        end_year = _short_range_end_year(int(year), int(yy))
        if end_year is None or not _source_writes_short_range(original_text, year, yy):
            continue
        _record_range_repair(updated, reformatted, (year, str(end_year)), _SHORT_RANGE_REASON)
        return


def _record_range_repair(
    updated: ExtractedFields, reformatted: ReformattedFields,
    new_range: tuple[str, str], reason: str,
) -> None:
    """Write `new_range` into start_date/end_date, recording both fields.
    The caller has checked that one was empty and the other held the whole
    range, so both change."""
    for field_name, new_value in zip(_RANGE_FIELD_NAMES, new_range):
        original = updated.get(field_name)
        updated[field_name] = new_value
        reformatted[field_name] = {
            'original': original, 'reformatted': new_value, 'reason': reason,
        }


def _month_slash_year_reading(value: str, original_text: str) -> str | None:
    """`value` ("2001-09") re-read as month/year when stage 4 took the source
    token "01/09" as year/month; None when the source does not show that.

    It is the misreading only when the four-digit year is nowhere in the
    text, the text has "<yy>/<mm>" for it, the first number is a valid month,
    and the text does not also have "<mm>/<yy>" (which would support the
    reading stage 4 made).
    """
    token = _YEAR_MONTH_VALUE_PATTERN.fullmatch(value.strip())
    if token is None:
        return None
    year, month = token.groups()
    yy = year[2:]
    if re.search(rf'(?<!\d){year}(?!\d)', original_text) or not 1 <= int(yy) <= 12:
        return None
    if not re.search(rf'(?<![\d/]){yy}/{month}(?![\d/])', original_text):
        return None
    if re.search(rf'(?<![\d/]){month}/{yy}(?![\d/])', original_text):
        return None
    return f"{expand_two_digit_year(int(month))}-{yy}"


def reread_month_slash_year(
    original_text: str, updated: ExtractedFields, reformatted: ReformattedFields
) -> None:
    """Re-read an MM/YY source date stage 4 read as YY/MM.

    "01/09- 08/14" is January 2009 to August 2014; the LLM stored the start
    as "2001-09" while reading the end correctly (class 2, 2026-10-02 s7ab
    autopsy: MQSUIC). Each range field is checked on its own against the
    source text (`_month_slash_year_reading`).
    """
    for field_name in _RANGE_FIELD_NAMES:
        value = updated.get(field_name)
        if not isinstance(value, str):
            continue
        reread = _month_slash_year_reading(value, original_text)
        if reread is None:
            continue
        updated[field_name] = reread
        reformatted[field_name] = {
            'original': value, 'reformatted': reread, 'reason': _MONTH_SLASH_YEAR_REASON,
        }


#: An "M-YY-MM-YY" source range: four dash-joined numbers, month and
#: two-digit year twice ("3-21-11-24", March 2021 to November 2024).
#: Four groups cannot be an m-d-yy date, which has three.
_MONTH_YEAR_DASH_RANGE_PATTERN = re.compile(
    r'(?<![\d/.-])(\d{1,2})-(\d{2})-(\d{1,2})-(\d{2})(?![\d/.-])')

_MONTH_YEAR_DASH_RANGE_REASON = 'Re-read an M-YY-MM-YY source range stage 4 read as M-D-YY'

#: The months of the year, for checking a number read as one.
_MONTHS_OF_YEAR = range(1, 13)


def _month_year_dash_range_reading(
    start_value: str, original_text: str
) -> tuple[str, str] | None:
    """(start, end) for the one "M-YY-MM-YY" range in `original_text` when
    stage 4 stored its first three numbers as an M-D-YY start date
    ("3-21-11-24" as 2011-03-21); None when the text does not show that.

    It is the misreading only when the text has exactly one such range, the
    stored start is that M-D-YY reading, its year is not written out in the
    text, both months are months, and the range does not run backwards.
    """
    ranges = _MONTH_YEAR_DASH_RANGE_PATTERN.findall(original_text)
    if len(ranges) != 1:
        return None
    start_month, start_yy, end_month, end_yy = (int(number) for number in ranges[0])
    misread = f'{expand_two_digit_year(end_month)}-{start_month:02d}-{start_yy:02d}'
    if start_value.strip() != misread:
        return None
    if re.search(rf'(?<!\d){misread[:4]}(?!\d)', original_text):
        return None
    if start_month not in _MONTHS_OF_YEAR or end_month not in _MONTHS_OF_YEAR:
        return None
    start_year, end_year = expand_two_digit_year(start_yy), expand_two_digit_year(end_yy)
    if (start_year, start_month) > (end_year, end_month):
        return None
    return f'{start_year}-{start_month:02d}', f'{end_year}-{end_month:02d}'


def reread_month_year_dash_range(
    original_text: str, updated: ExtractedFields, reformatted: ReformattedFields
) -> None:
    """Re-read an "M-YY-MM-YY" source range stage 4 read as an M-D-YY date.

    "3-21-11-24" is March 2021 to November 2024; the LLM stored the start
    as 2011-03-21, ten years early (class E26, 2026-10-02 EBYSBC autopsy:
    YYVHNN). Both range fields are rewritten from the source range
    (`_month_year_dash_range_reading`).
    """
    start_value = updated.get('start_date')
    if not isinstance(start_value, str):
        return
    reading = _month_year_dash_range_reading(start_value, original_text)
    if reading is None:
        return
    for field_name, new_value in zip(_RANGE_FIELD_NAMES, reading):
        original = updated.get(field_name)
        if original == new_value:
            continue
        updated[field_name] = new_value
        reformatted[field_name] = {
            'original': original, 'reformatted': new_value,
            'reason': _MONTH_YEAR_DASH_RANGE_REASON,
        }


def _normalize_pmid(original_text: str, updated: ExtractedFields, reformatted: ReformattedFields) -> None:
    """Extract a PMID from the source text if the LLM didn't already fill it in."""
    if updated.get('pmid'):
        return
    pmid_match = re.search(REGEX_PATTERNS['pmid'], original_text, re.IGNORECASE)
    if pmid_match:
        pmid_value = pmid_match.group(1)  # Just the number
        updated['pmid'] = pmid_value
        reformatted['pmid'] = {
            'original': pmid_match.group(0),  # Full match like "PMID: 12345678"
            'reformatted': pmid_value,
            'reason': 'Extracted PMID number via regex'
        }


def _normalize_pmcid(original_text: str, updated: ExtractedFields, reformatted: ReformattedFields) -> None:
    """Extract a PMCID from the source text if the LLM didn't already fill it in."""
    if updated.get('pmcid'):
        return
    pmcid_match = re.search(REGEX_PATTERNS['pmcid'], original_text, re.IGNORECASE)
    if pmcid_match:
        pmcid_value = f"PMC{pmcid_match.group(1)}"
        updated['pmcid'] = pmcid_value
        reformatted['pmcid'] = {
            'original': pmcid_match.group(0),
            'reformatted': pmcid_value,
            'reason': 'Extracted PMCID via regex'
        }


def _normalize_doi(original_text: str, updated: ExtractedFields, reformatted: ReformattedFields) -> None:
    """Extract a DOI if missing, or normalize an existing one (strip doi.org/doi: prefixes)."""
    if not updated.get('doi'):
        doi_match = re.search(REGEX_PATTERNS['doi'], original_text)
        if doi_match:
            # Clean up trailing punctuation that might have been captured
            doi_value = re.sub(r'[\.,;:]+$', '', doi_match.group(1))
            updated['doi'] = doi_value
            reformatted['doi'] = {
                'original': doi_match.group(0),
                'reformatted': doi_value,
                'reason': 'Extracted DOI via regex'
            }
        return

    # Normalize existing DOI (remove doi.org prefix, etc.)
    existing_doi = updated['doi']
    normalized_doi = re.sub(r'^https?://(dx\.)?doi\.org/', '', existing_doi)
    normalized_doi = re.sub(r'^doi:', '', normalized_doi, flags=re.IGNORECASE)
    normalized_doi = re.sub(r'[\.,;:]+$', '', normalized_doi)
    if normalized_doi != existing_doi:
        updated['doi'] = normalized_doi
        reformatted['doi'] = {
            'original': existing_doi,
            'reformatted': normalized_doi,
            'reason': 'Normalized DOI format'
        }


def _normalize_orcid(original_text: str, updated: ExtractedFields, reformatted: ReformattedFields) -> None:
    """Extract an ORCID from the source text if the LLM didn't already fill it in."""
    if updated.get('orcid'):
        return
    orcid_match = re.search(REGEX_PATTERNS['orcid'], original_text)
    if orcid_match:
        updated['orcid'] = orcid_match.group(1)
        reformatted['orcid'] = {
            'original': orcid_match.group(0),
            'reformatted': orcid_match.group(1),
            'reason': 'Extracted ORCID via regex'
        }


def _normalize_authors_field(updated: ExtractedFields, reformatted: ReformattedFields) -> None:
    """Rewrite the authors field to Vancouver style (LastName AB, LastName CD), if it changes anything."""
    if not updated.get('authors'):
        return
    original_authors = updated['authors']
    normalized_authors = normalize_authors_vancouver(original_authors)
    if normalized_authors != original_authors:
        updated['authors'] = normalized_authors
        reformatted['authors'] = {
            'original': original_authors,
            'reformatted': normalized_authors,
            'reason': 'Normalized to Vancouver author format'
        }


def _clean_title_field(updated: ExtractedFields, reformatted: ReformattedFields) -> None:
    """Strip leading status labels (e.g. "Featured:", "Submitted:") and stray punctuation from a title."""
    if not updated.get('title'):
        return
    original_title = updated['title']
    cleaned_title = re.sub(
        r'^(Featured|Submitted|In Review|In Preparation|Accepted)[:\s]+', '',
        original_title, flags=re.IGNORECASE
    )
    cleaned_title = re.sub(r'^[:\?\s]+', '', cleaned_title)
    cleaned_title = re.sub(r'[:\?\s]+$', '', cleaned_title)
    if cleaned_title != original_title:
        updated['title'] = cleaned_title
        reformatted['title'] = {
            'original': original_title,
            'reformatted': cleaned_title,
            'reason': 'Removed prefix label from title'
        }


# Regex candidates for percent-effort/FTE, tried in order; the first to match
# wins. The decimal-FTE pattern captures the WHOLE decimal value (integer +
# fractional part, e.g. ".8", "0.08", "1.0") instead of only the digits after
# the point, so ".8 FTE" converts to 80% rather than 8% (#3819054910: the old
# two-pattern version dropped the integer part and treated the raw digit
# string as already the percent, which only read correctly by coincidence
# for exactly-two-digit fractions like ".08").
_FTE_DECIMAL_PATTERN = r'(\d*\.\d{1,2})\s*FTE'      # .08FTE, 0.08 FTE, .8 FTE, 1.0 FTE
_FTE_PERCENT_PATTERNS = (
    r'(\d{1,3})\s*%\s*(?:effort|FTE)?',             # 8%, 8 %, 8% effort
    r'(\d{1,3})\s*percent',                          # 8 percent
)


def _find_percent_effort(original_text: str) -> tuple[str, str] | None:
    """Find the first percent-effort/FTE mention in ``original_text``.

    Returns ``(matched_text, percent)`` -- the literal text the winning
    pattern matched, and the normalised ``"NN%"`` value -- or ``None`` when
    no pattern matches. Patterns are tried in the same order as before this
    helper existed: the decimal-FTE pattern first, then each of
    ``_FTE_PERCENT_PATTERNS``; first match wins.

    Returning the pair is what makes the pairing checkable. The caller used
    to hold a match object and a percent string as two separate locals,
    assigned in lockstep and both left ``None`` on the no-match path, so
    their "either both or neither" invariant existed only dynamically and
    no reader (or checker) could confirm it from the code. As one return
    value, the invariant is the type.
    """
    decimal_match = re.search(_FTE_DECIMAL_PATTERN, original_text, re.IGNORECASE)
    if decimal_match:
        return decimal_match.group(0), f"{round(float(decimal_match.group(1)) * 100)}%"

    for pattern in _FTE_PERCENT_PATTERNS:
        percent_match = re.search(pattern, original_text, re.IGNORECASE)
        if percent_match:
            return percent_match.group(0), f"{percent_match.group(1)}%"

    return None


def _normalize_grant_effort(original_text: str, updated: ExtractedFields, reformatted: ReformattedFields) -> None:
    """Extract percent-effort/FTE for grant entries, if the LLM didn't already fill it in."""
    if updated.get('percent_effort'):
        return

    found = _find_percent_effort(original_text)
    if found is None:
        return

    matched_text, percent_effort = found
    updated['percent_effort'] = percent_effort
    reformatted['percent_effort'] = {
        'original': matched_text,
        'reformatted': percent_effort,
        'reason': 'Extracted percent effort via regex'
    }


#: Codes whose active schema declares `notes`, so a goal statement has a field
#: to land in. Hand-kept for the same module-boundary reason as
#: DATE_RANGE_TAXONOMY_CODES; computed from get_active_schemas() on origin/dev
#: @ 0964c263 (2026-10-02).
GRANT_NOTES_TAXONOMY_CODES = ('M2A', 'M2B', 'M2C')

# A grant's own goal statement (#1205 slice a). Stage 4 put it in `notes` for
# some grants and not for identical siblings in the same CV, so the same source
# shape rendered with a Notes row or without one. Three shapes, measured over
# the EBYSBC/s7ab/pilot farm (63 runs):
# - a label, "Goal: To ..." / "Goals: ...", which may follow other text on its
#   line ("<title> Goal: To ...");
# - a sentence opening "The|This [main|overall|...] goal(s) of this|the|our|my
#   ...". The capital T is what makes it a sentence start ("<title> The goal
#   of this fund ..." carries no period), so only "goal(s) of" and the
#   adjective are case-insensitive;
# - a mid-sentence "the goal of <verb>ing" ("My role is ..., with the goal of
#   testing ..."). Its sentence has no clean start, so the whole source
#   segment is the statement, which is what stage 4 put in `notes` for the
#   siblings of that shape.
# "major goal(s)" is left alone: stage 6's research_support.parse_major_goals
# reads that phrase into `major_goals` and renders its own row, so taking it
# here as well would print the goal twice. Mirrors
# research_support._MAJOR_GOALS_ANCHOR_PATTERN (stage 4 may not import stage 6).
_GOAL_LABEL_PATTERN = re.compile(r'(?<![\w-])goals?[ \t]*:', re.IGNORECASE)
_GOAL_SENTENCE_PATTERN = re.compile(
    r'(?<![\w-])(?:The|This)\s+'
    r'(?:(?i:main|overall|primary|central|principal|ultimate|long[- ]term)\s+)?'
    r'(?i:goals?\s+of\s+(?:this|the|our|my))\b'
)
_GOAL_GERUND_PATTERN = re.compile(r'(?<![\w-])goals?\s+of\s+[a-z]+ing\b', re.IGNORECASE)
_MAJOR_GOALS_PATTERN = re.compile(r'\bmajor\s+(?:goals?|gals)\b', re.IGNORECASE)
# A grant entry's fields are tab- or line-separated; one goal statement never
# spans two of them in the farm.
_SOURCE_SEGMENT_SEPARATOR = re.compile(r'[\t\n]')
# A role label after the statement on the same line ("<goal>. Role: PI") is
# the grant's role, which has its own field and row; the statement stops before
# it, as stage 6's MAJOR_GOALS_VALUE_END_RE does.
_GOAL_STATEMENT_END = re.compile(r'\s+(?=(?:your\s+)?role\s*:)', re.IGNORECASE)
# Shorter than this is a stray label, not a statement; matches the bar stage 6
# sets for its Major project goals row (`len(goals.strip()) > 10`).
_MIN_GOAL_STATEMENT_CHARS = 11
_SENTENCE_END_CHARS = '.!?;:'
_GOAL_NOTES_REASON = "Copied the grant's own goal statement into notes"


def _comparable_text(value: str) -> str:
    """`value` as lower-case words only, so a copy that differs in quoting,
    punctuation or whitespace still compares equal."""
    return ' '.join(re.findall(r'\w+', value.lower()))


def _goal_segment(text: str) -> str | None:
    """The one source segment that states a goal, or None when none or several do.

    Several goal segments in one entry usually mean several grants, and stage 4
    offers the entry's text to its last record only, so no one segment can be
    assigned safely.
    """
    candidates = [
        segment for segment in _SOURCE_SEGMENT_SEPARATOR.split(text)
        if _GOAL_LABEL_PATTERN.search(segment)
        or _GOAL_SENTENCE_PATTERN.search(segment)
        or _GOAL_GERUND_PATTERN.search(segment)
    ]
    return candidates[0] if len(candidates) == 1 else None


def _find_grant_goal_statement(original_text: str) -> str | None:
    """The goal statement in a grant entry's own text, verbatim, or None.

    Starts at the earliest goal label or goal sentence in its segment, so a
    title sharing the segment stays out; a mid-sentence goal keeps the whole
    segment. It ends at the segment's end or at a role label, whichever comes
    first. Text that names a "major goal(s)" is stage 6's (see the patterns
    above) and gives None.
    """
    if not original_text or _MAJOR_GOALS_PATTERN.search(original_text):
        return None
    segment = _goal_segment(original_text)
    if segment is None:
        return None
    starts = [match.start() for match in (_GOAL_LABEL_PATTERN.search(segment),
                                          _GOAL_SENTENCE_PATTERN.search(segment)) if match]
    statement = _GOAL_STATEMENT_END.split(segment[min(starts, default=0):], maxsplit=1)[0].strip()
    return statement if len(statement) >= _MIN_GOAL_STATEMENT_CHARS else None


def _fill_grant_goal_notes(original_text: str, updated: ExtractedFields,
                           reformatted: ReformattedFields) -> None:
    """Put the grant's goal statement in `notes` when `notes` does not hold it (#1205).

    An empty `notes` takes the statement; a `notes` holding something else
    keeps it and gains the statement after it. A statement that only repeats
    the title is skipped, as stage 6 drops a note equal to the title.
    """
    statement = _find_grant_goal_statement(original_text)
    if statement is None:
        return
    goal = _comparable_text(statement)
    title = _comparable_text(str(updated.get('title') or ''))
    if title and goal in title:
        return
    notes = str(updated.get('notes') or '').strip()
    if goal in _comparable_text(notes):
        return
    if not notes:
        merged = statement
    elif notes.endswith(tuple(_SENTENCE_END_CHARS)):
        merged = f'{notes} {statement}'
    else:
        merged = f'{notes}. {statement}'
    reformatted['notes'] = {
        'original': updated.get('notes'),
        'reformatted': merged,
        'reason': _GOAL_NOTES_REASON,
    }
    updated['notes'] = merged


def apply_regex_post_processing(
    original_text: str,
    extracted_fields: ExtractedFields,
    taxonomy_code: str
) -> tuple[ExtractedFields, ReformattedFields]:
    """
    Apply regex patterns to catch identifiers missed by LLM extraction.

    Also tracks reformatted values for transparency. Each distinct concern
    (identifiers, DOI, ORCID, author formatting, title cleanup, grant effort,
    MM/YY re-reading, YYYY-YY range split, two-digit-year century, date-range
    reconciliation) is a small helper above; this function is the
    orchestrator that decides, per taxonomy code, which ones apply, in the
    same order as before the split.

    This is where the untyped LLM JSON becomes the typed `ExtractedFields`
    contract. `coerce_field_value_types` and `normalize_dates` above stay
    `dict[str, Any]` deliberately: both rebuild the dict through variable
    keys (`coerced[key] = ...`, `del normalized[field_name]`), which a
    TypedDict cannot express without restructuring their bodies -- a
    runtime change this annotation-only pass does not make.

    Args:
        original_text: Original CV entry text
        extracted_fields: Fields extracted by LLM
        taxonomy_code: Entry taxonomy code (e.g., S1, M2A)

    Returns:
        Tuple of (updated_fields, reformatted_fields)
        - updated_fields: `ExtractedFields` -- the input's own keys, copied,
          plus any regex-caught value the helpers below filled in
        - reformatted_fields: `ReformattedFields` -- one `ReformattedField`
          per field this pass changed; empty when nothing changed
    """
    updated = extracted_fields.copy()
    reformatted: ReformattedFields = {}

    # Only apply identifier extraction to relevant taxonomy codes
    if taxonomy_code.startswith(IDENTIFIER_TAXONOMY_PREFIX) or taxonomy_code in IDENTIFIER_TAXONOMY_CODES:
        _normalize_pmid(original_text, updated, reformatted)
        _normalize_pmcid(original_text, updated, reformatted)
        _normalize_doi(original_text, updated, reformatted)

    # Extract ORCID for profile sections
    if taxonomy_code in ORCID_TAXONOMY_CODES:
        _normalize_orcid(original_text, updated, reformatted)

    # Normalize author formatting to Vancouver style
    # Vancouver: LastName AB, LastName CD (no periods in initials, no comma before initials)
    _normalize_authors_field(updated, reformatted)

    # Clean title (remove leading labels like "Featured:", "Submitted:")
    _clean_title_field(updated, reformatted)

    # Extract percent effort/FTE for grant entries
    if taxonomy_code.startswith(GRANT_EFFORT_TAXONOMY_PREFIX):
        _normalize_grant_effort(original_text, updated, reformatted)

    # Copy a grant's own goal statement into notes, so identical sibling
    # grants render alike (#1205)
    if taxonomy_code in GRANT_NOTES_TAXONOMY_CODES:
        _fill_grant_goal_notes(original_text, updated, reformatted)

    # Re-read a two-digit-year date stage 4 took the wrong way round
    # ("01/09" as 2001-09), then split a "1999-02" range stored whole in one
    # field. Both before the century repair and the range repair, which read
    # the dates these rewrite.
    reread_month_slash_year(original_text, updated, reformatted)
    if taxonomy_code in DATE_RANGE_TAXONOMY_CODES:
        split_year_short_range(original_text, updated, reformatted)
        reread_month_year_dash_range(original_text, updated, reformatted)

    # Move a 19xx year read off a two-digit source year ("10/08") to 20xx.
    # Before the range repair, so it compares against the corrected start.
    repair_two_digit_year_century(original_text, updated, reformatted)

    # Re-derive (or clear) a dated year the text does not contain at all
    # ("3/01/17" stored as 1997-03-01). After the century repair, which may
    # already have moved it to a year the text supports.
    repair_year_not_in_source(original_text, updated, reformatted)

    # Restore a dropped end_date when the schema declares both dates and the
    # source text unambiguously carries the closed range (#556)
    if taxonomy_code in DATE_RANGE_TAXONOMY_CODES:
        reconcile_date_range(original_text, updated, reformatted)

    return updated, reformatted
