"""Value coercion and normalisation for stage 4 extracted fields.

Moved verbatim from `stage_4_field_extractor.py` (#498), which re-exports every
name here. Pure functions over extracted-field dicts: no LLM calls, no I/O.
"""

import re
from typing import Any, TypedDict


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
    nct_number: Any
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
    sponsor: Any
    start_date: Any
    state_country: Any
    status: Any
    study_title: Any
    submission_date: Any
    target_journal: Any
    target_name: Any
    teaching_role: Any
    thesis_title: Any
    title: Any
    total_funding: Any
    total_funding_requested: Any
    training_type: Any
    trial_title: Any
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

    ``original`` is ``Any`` rather than ``str`` because the two producers
    disagree in kind: the regex extractors store the matched source
    substring (a ``str``), while the date-range repair stores the pre-repair
    field value, which is falsy by construction -- ``None`` or ``""`` -- since
    that repair only runs on an empty field.
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
    doi: ReformattedField
    end_date: ReformattedField
    orcid: ReformattedField
    percent_effort: ReformattedField
    pmcid: ReformattedField
    pmid: ReformattedField
    start_date: ReformattedField
    title: ReformattedField


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
    'M2', 'M2A', 'M2B', 'M4A_DEPRECATED', 'M4B', 'N1', 'N2', 'N3', 'N3B',
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
    if not original_text:
        return
    if _PRESENT_MARKER_PATTERN.search(original_text):
        return

    matches = CLOSED_DATE_RANGE_PATTERN.findall(original_text)
    if len(matches) != 1:
        return

    range_start, range_end = matches[0]
    start_year, end_year = int(range_start), int(range_end)
    if not (_MIN_PLAUSIBLE_YEAR <= start_year <= end_year <= _MAX_PLAUSIBLE_YEAR):
        return

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


def apply_regex_post_processing(
    original_text: str,
    extracted_fields: ExtractedFields,
    taxonomy_code: str
) -> tuple[ExtractedFields, ReformattedFields]:
    """
    Apply regex patterns to catch identifiers missed by LLM extraction.

    Also tracks reformatted values for transparency. Each distinct concern
    (identifiers, DOI, ORCID, author formatting, title cleanup, grant effort,
    date-range reconciliation) is a small helper above; this function is the
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

    # Restore a dropped end_date when the schema declares both dates and the
    # source text unambiguously carries the closed range (#556)
    if taxonomy_code in DATE_RANGE_TAXONOMY_CODES:
        reconcile_date_range(original_text, updated, reformatted)

    return updated, reformatted
