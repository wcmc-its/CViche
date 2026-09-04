"""Value normalization lifted out of stage_6_word_template (#398).

Text in, cleaner text out. Author names in a single citation spelling, an
institution string with its trailing org suffix removed, markdown stripped for a
Word run, a repeated phrase collapsed, the reader's internal cell separators
made readable, a leaked 3b taxonomy code stripped off the front of a bullet.

They are kept together because they change for the same reason -- a new spelling
variant, separator or leaked token observed in a CV -- and apart from
`formatting`, which changes when the WCM template's appearance changes. The
sibling `fields.py` handles the case where the input is not text yet.

Names keep their leading underscore for now. Renaming and relocating in one
change would make a failure impossible to attribute to either.
"""
import logging
import re
from typing import TypedDict

logger = logging.getLogger(__name__)


class InstitutionEnrichment(TypedDict, total=False):
    """The stage 5b `institution_enrichment` fields stage 6 reads.

    `total=False` is the shape, not a convenience: stage 5b writes whichever
    of these its LLM returned and both keys are routinely absent or empty
    (an empty `cleaned_name` next to a correct `official_name` is the case
    the fallback below exists for). Documentation for the reader and for
    mypy -- a TypedDict is erased at runtime, so the `or {}` guard in
    `_get_cleaned_institution_name` stays load-bearing (#559).
    """
    cleaned_name: str
    official_name: str


class InstitutionEnrichmentEntry(TypedDict, total=False):
    """A stage-4/5 entry insofar as `_get_cleaned_institution_name` reads it.

    Entries carry many more keys than this; only the one this function
    touches is named, so the annotation stays honest about what is actually
    required. The value may be present and explicitly None (#559).
    """
    institution_enrichment: InstitutionEnrichment | None


def _get_cleaned_institution_name(
        entry: InstitutionEnrichmentEntry) -> str | None:
    """Get cleaned institution name from enrichment data if available.

    When Stage 5b LLM enrichment provides a cleaned_name (institution name with
    embedded location removed), use it instead of the raw institution field.
    This prevents duplication like "Duke Medical Center, Durham, NC, Durham, NC".

    Falls back to official_name when cleaned_name is empty — the LLM sometimes
    returns empty cleaned_name even when official_name is correctly populated
    (e.g., official_name="Duke Regional Hospital" with cleaned_name="").

    Args:
        entry: Entry dict with potential institution_enrichment

    Returns:
        Cleaned institution name, or None if not available (use original)
    """
    enrichment = entry.get('institution_enrichment') or {}
    cleaned = enrichment.get('cleaned_name', '')
    if cleaned:
        return cleaned
    # Fall back to official_name — always the institution without embedded location
    official = enrichment.get('official_name', '')
    return official if official else None


def _strip_org_tail(name: str, org: str) -> str:
    """Remove a trailing organization segment (plus one short comma-led
    city tail, "..., Indiana University, Bloomington") from an award name
    so the org isn't duplicated across the name and Organization cells
    (#229). Conservative: only strips at end-of-string."""
    if not org:
        return name
    stripped = re.sub(
        r'[\s,]*' + re.escape(org) +
        r'(?:,\s*[A-Z][\w.-]+(?:\s+[A-Z][\w.-]+)?)?[\s,.]*$',
        '', name).strip()
    return stripped or name


def _deduplicate_repeated_content(text: str, separator: str = '|') -> str:
    """Remove repeated content from pipe-separated text.

    Handles cases where table extraction causes the same content to repeat:
    "Title .08FTE | Title .08FTE | Title .08FTE" -> "Title .08FTE"

    Args:
        text: Raw text that may contain repeated segments
        separator: The separator between repeated segments (default: '|')

    Returns:
        The first segment, but ONLY when every segment is an exact repeat of
        it (a merged cell repeating itself). Anything less -- including two
        segments that simply don't match -- is meaningfully different
        content and is returned unchanged, untruncated (#561).
    """
    if not text or separator not in text:
        return text

    parts = [p.strip() for p in text.split(separator) if p.strip()]
    if len(parts) <= 1:
        return text

    # Check if all parts are identical (using first part as reference)
    first_part = parts[0]

    # Normalize for comparison (lowercase, remove extra whitespace)
    def normalize(s):
        return ' '.join(s.lower().split())

    first_normalized = normalize(first_part)

    # Count how many parts match the first
    matching_count = sum(1 for p in parts if normalize(p) == first_normalized)

    # Collapse only when EVERY segment is identical -- the merged-cell
    # pathology this function exists for. `matching_count >= len(parts) *
    # 0.5` was a tautology at len(parts) == 2 (matching_count always counts
    # the reference segment against itself, so 1 >= 1.0 unconditionally),
    # truncating any two-segment title to its first half regardless of
    # whether the segments matched at all (#561).
    if matching_count == len(parts):
        # Structural metadata only: the segment itself is CV-derived text
        # (a grant or appointment title), so its length is logged and its
        # content is not.
        logger.debug(
            "_deduplicate_repeated_content: collapsed %d identical segments "
            "to one of %d characters", len(parts), len(first_part),
        )
        return first_part

    # Otherwise return original (parts are meaningfully different)
    return text


def _strip_markdown_for_word(text: str, preserve_newlines: bool = False) -> str:
    r"""Strip the SUPPORTED markdown subset from stage-5c text for a Word run.

    The name says "markdown"; the contract is narrower on purpose. The input
    is not arbitrary markdown, it is the small set of markers stage 5c
    emits, and everything outside that set is passed through as written --
    the safe direction, since an unrecognised construct then renders as
    literal characters instead of having content cut out of it.

    Stripped:
    - `**bold**` markers, anywhere in a line ("**Course:** X" -> "Course: X")
    - a `- ` list prefix at the start of a line (after that line is
      stripped, so an indented sub-bullet is flattened to the same level,
      not preserved as one), and stage 5c's `Notes:` structural marker
      immediately after it
    - leading/trailing whitespace on every line, and empty lines
    Lines are then rejoined with "; " -- or with newlines when
    `preserve_newlines` is set.

    Deliberately NOT handled, and passed through unchanged:
    - links, `[text](url)`
    - emphasis, `*text*` and `_text_`
    - inline code, `` `x` ``, and fenced code blocks
    - escapes: `\*\*not bold\*\*` keeps its backslashes and its asterisks
    - ATX headers, `# Header`. The previous docstring claimed these were
      stripped; the implementation has never touched them.
    - ordered list markers, `1. item`
    - a bullet written `-item`, with no space after the dash

    Pinned as a contract by
    `src/unified_pipeline/tests/test_stage6_markdown_subset_contract.py`.

    Args:
        preserve_newlines: If True, join lines with newlines instead of
            semicolons. Use for teaching entries where each line becomes
            a separate bullet (main entry + notes).
    """
    if not text:
        return ''

    # Remove bold markers
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)

    # Handle sub-bullets - strip the dash prefix
    lines = text.split('\n')
    result_parts = []
    for line in lines:
        line = line.strip()
        if line.startswith('- '):
            # Sub-bullet, strip the prefix and any "Notes: " structural marker from Stage 5c
            line = line[2:].strip()
            if line.startswith('Notes: '):
                line = line[7:]
            elif line.startswith('Notes:'):
                line = line[6:].strip()
            result_parts.append(line)
        elif line:
            result_parts.append(line)

    # Join with appropriate separator
    if preserve_newlines:
        return '\n'.join(result_parts)
    elif len(result_parts) > 1:
        # Multiple lines - join with semicolon for compactness
        result = '; '.join(result_parts)
    else:
        result = result_parts[0] if result_parts else ''

    return result


def _clean_inline_tabs(text: str) -> str:
    """Render the pipeline's internal cell separators readably.

    Two separators are artifacts of how the readers flatten a source CV, and
    neither belongs in a rendered Word document:

    - " | " joins the cells of a table row (``docx_structure_extractor``). Those
      cells are columns, not a label/value pair, so they rejoin with " — ".
      A row whose cells are all empty is a blank template row carrying no
      information; it collapses to "" so callers can drop it.
    - "\\t" joins the "Label\\tValue" pairs of the WCM template's tables. A raw
      tab renders ragged against Word's default tab stops, so the first becomes
      ": " (label: value) and any further tabs become " — ".

    UPSTREAM CONTRACT -- what every caller has already decided before it
    gets here. This is the residual-text path: the routing that sends a
    table-shaped record to a real Word table runs first, and only what that
    routing declined reaches this function. The six call sites, each read
    before being written down here:

    - ``stage_6_word_template.py:1137`` ``_insert_bulleted_entry`` -- one
      entry rendered as a single bullet paragraph.
    - ``stage_6_word_template.py:2084`` ``_insert_reconsidered_segment`` --
      one LLM-reclassified segment, likewise a single bullet.
    - ``stage_6_word_template.py:2386`` ``_unconsumed_personal_data_batch``
      -- A entries no Personal Data slot consumed. Its PII scan reads the
      RAW text, precisely because this call destroys the fragment boundaries
      that scan splits on.
    - ``sections/appendix.py:70`` -- the appendix, which by construction
      holds only entries no section renderer claimed.
    - ``sections/mentoring.py:203`` ``_insert_mentoring_summaries`` -- only
      the aggregate entries ``_is_mentee_record()`` rejected; every mentee
      record went to ``_create_mentee_table_with_spacing`` instead.
    - ``sections/researcher_profiles.py:62`` -- S0 identifier lines (ORCID,
      Google Scholar), one short line each.

    None of that is enforced by a type or an assertion, so it is a
    convention and not a guarantee: a genuine table row whose upstream
    classification failed arrives here and is flattened into one
    "cell — cell — cell" line, with no warning. That cost is pinned rather
    than left to be discovered in a delivered document --
    ``test_cell_separators.py`` covers the residual-text path and
    ``test_stage6_cell_separator_contract.py`` covers what the flattening
    fallback actually produces when a row does reach it.

    ponytail: the name says "tabs" but it now handles both separators. Kept as-is
    so this change does not collide with the three bullet call sites that #254
    also edits; rename to _clean_cell_separators once that has landed.
    """
    if not text:
        return text
    if "|" in text:
        text = " — ".join(c.strip() for c in text.split("|") if c.strip())
    if "\t" not in text:
        return text
    parts = [p.strip() for p in text.split("\t") if p.strip()]
    if len(parts) <= 1:
        return text.replace("\t", " ").strip()
    return parts[0] + ": " + " — ".join(parts[1:])


# A leading 3b taxonomy code (M2B, D1, S6, N3A …) that leaked into a rendered
# bullet — code letter + 1-2 digits + optional trailing letter, bracketed at the
# very start and followed by whitespace. Seen verbatim in output on the WCM-
# template CVs (issue #251): "• [M2B] Project title: …", "• [D1] Visiting Prof…".
_TAXONOMY_CODE_PREFIX = re.compile(r"^\s*\[[A-Z]\d{1,2}[A-Z]?\]\s+")


def _strip_taxonomy_code(text: str) -> str:
    """Drop a leading bracketed taxonomy code from bullet text before render.

    Shape-match, not an allowlist of the 42 real codes, and the shape also
    matches a leading grant mechanism such as "[R01] ". Measured before
    keeping it that way: across the farm's 412 stage-3b/4/5/5b/5c/5d
    artifacts (1,326,667 string values), exactly 2 values begin with a
    bracketed token at all, both "[Editor]", and neither matches this
    shape -- 0 false positives, and 0 true positives too, since a leaked
    code is a per-run artifact rather than something the stored stage
    outputs carry. An allowlist buys nothing at 0 measured collisions and
    would need editing every time the taxonomy gains a code, so the shape
    stays; switch to the TAXONOMY_TO_SECTION key set if that measurement
    ever comes back non-zero. The cost of the shape is pinned by
    `test_stage6_taxonomy_code_strip.py::
    test_a_leading_grant_mechanism_is_stripped_by_the_shape_match`.
    """
    return _TAXONOMY_CODE_PREFIX.sub("", text) if text else text


# Protected-personal-data detection. Deny by value provenance rather than
# by entry, so a contact block that also carries a birth date keeps its
# live office address, phone and work email (#472).

_PII_LABEL_RE = re.compile(r"""^\s*
    (?: date \s* of \s* birth
      | birth \s*-? \s* date (?: \s+ and \s+ birth \s*-? \s* place )?
      | birthdate (?: \s+ and \s+ birthplace )?
      | born
      | d\.?o\.?b\.?
      | place \s* of \s* birth | birth \s*-? \s* place | birthplace
      | marital \s* status
      | spouse (?: [’']s )? (?: \s* name )? | wife | husband
      | children (?: [’']s \s* names? )? | dependents?
      | social \s* security (?: \s* (?: number | no\.? ) )? | ssn
    ) \s* :""", re.X | re.I)


_PII_FIELD_KEY_RE = re.compile(r"""^(?:.*_)?(?:
      date_of_birth | birth_?date | birth_?place | place_of_birth | dob
    | marital_status (?:_\w+)? | spouse (?:_\w+)? | wife (?:_\w+)? | husband (?:_\w+)?
    | children (?:_\w+)? | dependents? (?:_\w+)?
    | ssn | social_security\w* )$""", re.X | re.I)


_PII_FRAGMENT_SPLIT_RE = re.compile(r"[\n\t|]|\s{3,}")


# Colon-less companion to _PII_LABEL_RE (#532), composed from three named
# parts below so the shape reads off the code: stem, non-colon separator,
# date-or-SSN-shaped value. Why both halves are required: the label alone
# swallows real CV content that merely starts with a listed word
# ("Children's Oncology Group - Emeritus", "Born - Digital: A Study of Youth
# Media Practices"), so the VALUE has to carry signal too. Why the stem's
# lookbehind is not a plain \b: a hyphen IS a word boundary, so \b still let
# "Foreign-born – 2015" and "University of Michigan-Dearborn – 2015" match
# on their "born" half -- the #473 false-positive class this predicate
# exists not to reintroduce. Scope (date of birth and SSN only, no
# place-of-birth), the deliberate single-space residual, and every negative
# control are pinned by test_stage6_pii_colonless_labels.py.
_PII_COLONLESS_STEM = r"""
    (?<![\w\-–—])
    (?: date \s* of \s* birth
      | birth \s*-? \s* date
      | birthdate
      | born
      | d\.?o\.?b\.?
      | social \s* security (?: \s* (?: number | no\.? ) )?
      | ssn
    )
"""

_PII_COLONLESS_SEPARATOR = r"""
    \s* (?: [-–—] \s* | \t \s* | \s{2,} )
"""

_PII_COLONLESS_VALUE = r"""
    (?: \d{1,2} [/-] \d{1,2} [/-] \d{2,4}
      | \d{3} -? \d{2} -? \d{4}
      | (?: jan(?:uary)? | feb(?:ruary)? | mar(?:ch)? | apr(?:il)? | may
          | jun(?:e)? | jul(?:y)? | aug(?:ust)? | sep(?:tember)? | oct(?:ober)?
          | nov(?:ember)? | dec(?:ember)?
        ) \s+ \d{1,2} , \s* \d{4}
      | \d{4}
    )
"""

_PII_LABEL_VALUE_RE = re.compile(
    _PII_COLONLESS_STEM + _PII_COLONLESS_SEPARATOR + _PII_COLONLESS_VALUE,
    re.X | re.I,
)


# Fewer alphanumeric characters than this and a value is not evidence of
# anything: "1965" sits inside every birth date and inside half a CV's year
# columns, so a short value found inside a PII fragment is a coincidence,
# not provenance.
_PII_CONTAINMENT_MIN_ALNUM = 6


def _squash(text) -> str:
    """Whitespace-FREE normalization for verbatim containment checks."""
    return re.sub(r"\s+", "", str(text or "")).lower()


def _collapse_whitespace(text) -> str:
    """Whitespace-COLLAPSED, case-folded normalization.

    Deliberately not `_squash`: that one deletes whitespace outright, which
    also deletes the token boundaries `_from_pii_fragment` has to align on.
    """
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


# "Alphanumeric" for the edge test: a word character that is not an
# underscore, so it is Unicode-aware without treating "_" as part of a token.
_ALNUM_CHAR = r"[^\W_]"


def _pii_containment_pattern(value) -> re.Pattern | None:
    """A whitespace-insensitive, token-aligned matcher for one extracted value.

    None when the value carries fewer than `_PII_CONTAINMENT_MIN_ALNUM`
    alphanumeric characters -- too short to be evidence of anything.

    Whitespace must not decide the match: the value and the fragment come
    from different places and stage 4 re-spaces what it extracts, so the
    value's characters are joined by "any whitespace, or none", which is the
    same tolerance `_squash` had. What is added is the edge condition: an
    alphanumeric first or last character may not sit against another
    alphanumeric character in the fragment, so a match cannot begin or end
    in the middle of one of the fragment's own tokens.
    """
    squashed = _squash(value)
    if sum(c.isalnum() for c in squashed) < _PII_CONTAINMENT_MIN_ALNUM:
        return None
    body = r"\s*".join(re.escape(c) for c in squashed)
    lead = f"(?<!{_ALNUM_CHAR})" if squashed[0].isalnum() else ""
    trail = f"(?!{_ALNUM_CHAR})" if squashed[-1].isalnum() else ""
    return re.compile(lead + body + trail)


def _pii_fragments(text: str | None) -> list[str]:
    """The fragments of an entry that carry protected personal data."""
    text = str(text or "")
    colon_fragments = [f for f in _PII_FRAGMENT_SPLIT_RE.split(text)
                        if _PII_LABEL_RE.match(f)]
    colonless_fragments = [m.group(0) for m in _PII_LABEL_VALUE_RE.finditer(text)]
    return colon_fragments + colonless_fragments


def _from_pii_fragment(value, pii_fragments: list[str]) -> bool:
    """Whether an extracted value's text was taken out of a PII fragment.

    Deny by value PROVENANCE, not by entry. Dropping a whole entry that
    contains a PII label is right in the appendix, where the entry renders
    nothing so discarding it is free -- but it is wrong here, where the same
    entry is actively supplying live contact data: on the corpus it drops real
    office addresses, an office phone and a work email from three CVs whose
    contact block happens to also carry a birth date.

    This is a containment test and not true provenance, and it cannot be
    made into one here: real provenance would need stage 4 to record the
    source span each value was lifted from, and stage 4 emits raw LLM JSON
    against no schema and no spans. So the containment test is made as sound
    as a containment test can be, closing the two ways it goes wrong:

    - it must not match mid-token. The value's characters are still
      matched across any whitespace, exactly as `_squash` did, but the
      match may no longer begin or end against an alphanumeric character
      inside the fragment -- so a value cannot be denied because its
      characters happen to run through the middle of a date or an SSN.
    - it must not match on a value too short to mean anything.
      `_PII_CONTAINMENT_MIN_ALNUM` is the floor: a bare "1965", which is
      inside every birth date, no longer denies an address.

    Measured over the 66-CV farm before the change: 319 A-coded entries,
    288 deny decisions, 0 of which differ between the old bare-substring
    test and this one.
    """
    pattern = _pii_containment_pattern(value)
    if pattern is None:
        return False
    return any(pattern.search(_collapse_whitespace(f)) for f in pii_fragments)
