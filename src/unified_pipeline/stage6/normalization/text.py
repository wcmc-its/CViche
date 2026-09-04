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

# Name suffixes that belong to the surname immediately before them, never to
# an initials slot of their own: "Smith, John, Jr., Brown" is one author with
# a suffix, not two authors named "Jr." and "Brown" (#560).
_AUTHOR_SUFFIX_RE = re.compile(r'^(?:Jr|Sr|II|III|IV)\.?$', re.I)

# Marks an author list puts *on* an initials group rather than in it: the
# abbreviating period, and the co-first / corresponding-author asterisk and
# daggers. Stripped before the shape test, so "Opresko, PL*" is classified
# exactly as "Opresko, PL" is instead of falling out of the initials rule on
# a typographic mark (5 corpus author strings, all Opresko/Bournique).
_INITIALS_TRAILING_MARKS = ".*†‡"

# One initials group, hyphenated: "R-Y". Each side is a single letter of any
# script, so the uppercase test below is applied to the sides, not the hyphen.
_HYPHENATED_INITIALS_RE = re.compile(r'^[^\W\d_](-[^\W\d_])+$')

# Longest run of letters still readable as an initials group rather than as a
# short given name ("Scot", "Wang").
_MAX_INITIALS_LETTERS = 4


def _looks_like_initials(token: str) -> bool:
    """Whether a comma-split token is an initials group rather than a name.

    THE initials rule for this module. Every path that has to decide
    "initials or name?" calls this one predicate -- the pair detector, the
    pair parser's upper-casing, and the fallback parser -- so a token can no
    longer be classified one way on one path and the other way on the other.
    The fallback used to carry its own pair of tests (an ASCII-only
    1-3 uppercase-letter regex, plus a case-blind "2 characters or fewer and
    isupper()"), and those disagreed with this function on spaced groups
    ("N J"), four-letter groups, hyphenated groups and lone lowercase
    initials -- 45 of the farm's 1,711 distinct author strings parse
    differently depending on which of the two definitions ran.

    A single alphabetic character of any case or script is always an
    initial -- "Kelly, r" and "Kelly, Å" both occur in the corpus (#560),
    and a lone letter has no other plausible reading. 2-4 characters must
    still be uppercase: initials are conventionally written that way, and a
    short mixed-case word ("Scot", "Li", "Wei") is at least as likely to be
    a real given name as an initials group. Hyphenated initials ("R-Y")
    follow the same rule per side.
    """
    t = token.rstrip(_INITIALS_TRAILING_MARKS).replace(' ', '')
    if not t:
        return False
    if _HYPHENATED_INITIALS_RE.match(t):
        return t.replace('-', '').isupper()
    return t.isalpha() and (
        len(t) == 1 or (len(t) <= _MAX_INITIALS_LETTERS and t.isupper())
    )


def _parse_surname_initial_pairs(parts: list[str]) -> list[str]:
    """Join alternating "Surname", "Initials" tokens into "Surname Initials".

    Reached only once `_normalize_author_names` has verified that every
    odd-indexed token is an initials group or a recognised name suffix, so
    the parity walked here is the parity the detector validated -- with one
    deliberate exception, the "surname is itself initials" skip, which
    advances by one and therefore shifts the loop off that parity for
    everything after it.

    Never reduces the token count: a trailing element with no initials to
    pair with is emitted on its own rather than dropped (#560).
    """
    cleaned_authors: list[str] = []
    i = 0
    while i < len(parts):
        surname = parts[i].strip().rstrip('.,')

        if i + 1 >= len(parts):
            # A trailing element with no initials to pair with is still
            # a name -- emit it rather than drop it (#560).
            if surname:
                cleaned_authors.append(surname)
            i += 1
            continue

        initials = parts[i + 1].strip().rstrip('.,')

        if _AUTHOR_SUFFIX_RE.match(initials.rstrip('.')):
            # The slot after this surname is a suffix, not an initials
            # group for a *following* pair -- attach it here.
            cleaned_authors.append(f"{surname} {initials.rstrip('.')}")
            i += 2
            continue

        # Normalize spaced initials: "P L" -> "PL". Upper-case only a
        # token that is actually initials-shaped. The `len(surname) <= 2`
        # skip below advances by one, which shifts this loop off the
        # parity the detector validated, so a real surname can land in
        # the initials slot ("AB, A-B, Smith, AB" puts "Smith" here) --
        # and rendering it as "SMITH" would be a new corruption of a
        # name this function is supposed to leave alone (#560).
        initials_normalized = initials.replace(' ', '')
        if _looks_like_initials(initials_normalized):
            initials_normalized = initials_normalized.upper()

        # Skip if surname looks like just initials
        if len(surname) <= 2 and surname.isupper():
            i += 1
            continue

        cleaned_authors.append(f"{surname} {initials_normalized}")
        i += 2

    return cleaned_authors


def _parse_author_fallback(parts: list[str]) -> tuple[list[str], bool]:
    """Parse a comma-split author list the pair detector declined.

    Returns the cleaned author elements and whether an "et al." marker was
    seen; the marker is not one of the elements, the caller re-attaches it.

    The pair detector rejected this input -- one missing comma anywhere in
    the list is enough (#560) -- so comma position can no longer be trusted
    to mean "surname, initials" across the whole string. A token that is
    initials-shaped (`_looks_like_initials`) or a recognised suffix merges
    into the author immediately before it -- but only when that
    author is still "open": a bare name with no initials of its own yet,
    the exact shape a stray comma produces ("Konopasek, L" split by one
    comma that shouldn't be there). An author that already has its own
    initials ("Sanguino SM") is not reopened by a later fragment; a
    fragment with nothing open to attach to is emitted as its own element
    rather than dropped, so the token count never falls (#560).

    A mixed-case or lowercase 1-2 character token is not initials-shaped and
    so is not treated as a fragment at all -- it is at least as likely to be
    a real short surname ("Li", "Wu", "Ma", "Ye") as an initials group, so
    it is kept as its own standalone author instead (#560).
    """
    cleaned_authors: list[str] = []
    has_et_al = False
    merge_target_open = False

    for author in parts:
        author_stripped = author.strip()

        # Handle "et al" specially
        if author_stripped.lower() in ('et al', 'et al.'):
            has_et_al = True
            continue

        is_suffix = bool(_AUTHOR_SUFFIX_RE.match(author_stripped.rstrip('.')))

        if _looks_like_initials(author_stripped) or is_suffix:
            fragment = author_stripped.rstrip('.,')
            if fragment:
                if merge_target_open:
                    cleaned_authors[-1] = f"{cleaned_authors[-1]} {fragment}"
                else:
                    # Nothing open to charge this fragment to. Keep it as its
                    # own element: it is unattributable, not absent, and the
                    # live case is a source string that already reads
                    # "... Shariati H, F, MJ. K" -- dropping the "F" is the
                    # deletion #560 is about, and there is no author here it
                    # can be merged onto without inventing an attribution.
                    cleaned_authors.append(fragment)
            merge_target_open = False
            continue

        # Clean up individual author formatting
        author = author_stripped.rstrip('.,')

        # Remove periods from initials: "J.A." -> "JA"
        author = re.sub(r'([A-Z])\.([A-Z])', r'\1\2', author)
        author = re.sub(r'([A-Z])\.$', r'\1', author)

        if author:
            cleaned_authors.append(author)
            # "Open" for exactly one merge iff this author is a bare token
            # (no internal space) -- one that already reads "Surname XY"
            # is complete and shouldn't absorb a later stray fragment too.
            merge_target_open = ' ' not in author

    return cleaned_authors, has_et_al


def _normalize_author_names(authors: str) -> str:
    """
    Normalize author names to proper Vancouver format.

    Input cleanup, format detection, dispatch to one of the two parsers
    above, and the "et al." marker; the parsing itself lives in
    `_parse_surname_initial_pairs` and `_parse_author_fallback`.

    Handles formats like:
    - "Kelly, R, Pirog, R" -> "Kelly R, Pirog R" (LastName, Initial pairs)
    - "Smith JA, Jones MB" -> "Smith JA, Jones MB" (already Vancouver)
    - "Smith, John A., Jones, Mary B." -> "Smith, John A, Jones, Mary B"
      (full given names aren't initials -- the pair detector correctly
      declines this shape; abbreviating "John A" to "JA" is not attempted)

    Fixes common issues:
    - Double commas: "Watson, K.,," -> "Watson K"
    - Trailing punctuation

    Never drops a token that names or belongs to a real author (#560).
    Previously, a comma-split token the pair detector or the fallback below
    couldn't place -- an initials group, a name suffix, a bare 1-2 character
    fragment -- was silently discarded, and that test was case-blind: a
    short *surname* ("Li", "Wu", "Ma", "Ye") was discarded exactly like a
    short initials fragment. Because a single missing comma anywhere in the
    list is enough to make the pair detector decline the whole string, that
    one discard rule was stripping every later author's initials from
    citations that had them, or dropping short-surnamed authors outright.
    Such a token now merges into the author immediately before it, and when
    there is no open author to merge into it is emitted as its own element
    instead. Neither branch reduces the token count any more.
    """
    if not authors:
        return ''

    original = authors

    # Clean up double/triple commas
    authors = re.sub(r',{2,}', ',', authors)

    # Remove trailing punctuation
    authors = authors.rstrip('.,;')

    # Replace " & " with ", "
    authors = re.sub(r'\s*&\s*', ', ', authors)

    # Handle the "LastName, Initial, LastName, Initial" format
    # Pattern: word followed by comma and single letter(s)
    # e.g., "Kelly, R, Pirog, R" -> list of ("Kelly", "R"), ("Pirog", "R")

    # First, check if this looks like alternating "Name, Initial" pairs
    parts = [p.strip() for p in authors.split(',') if p.strip()]

    # Try to detect the pattern: alternating surnames and initials. A
    # recognised suffix in the initials slot doesn't have to look like
    # initials itself -- it belongs to the surname before it (#560).
    looks_like_pairs = True
    if len(parts) >= 2:
        for i in range(1, len(parts), 2):
            part = parts[i]
            if _AUTHOR_SUFFIX_RE.match(part.rstrip('.')):
                continue
            if not _looks_like_initials(part):
                looks_like_pairs = False
                break

    # Structural metadata only. These strings are CV-derived author names,
    # so neither the input nor the result is logged: debug logs land in
    # centralized systems with wider retention and access than the app
    # itself. The token counts are what makes #560 visible in a log -- the
    # bug is a token disappearing between "in" and "out" -- and they say
    # nothing about who the authors are.
    if looks_like_pairs and len(parts) >= 2:
        cleaned_authors = _parse_surname_initial_pairs(parts)
        result = ', '.join(cleaned_authors)
        logger.debug(
            "_normalize_author_names: branch=pairs tokens_in=%d tokens_out=%d "
            "changed=%s", len(parts), len(cleaned_authors), result != original,
        )
        return result

    logger.debug(
        "_normalize_author_names: branch=fallback tokens_in=%d "
        "(pair detector declined)", len(parts),
    )
    cleaned_authors, has_et_al = _parse_author_fallback(parts)
    result = ', '.join(cleaned_authors)
    if has_et_al:
        result += ', et al.'
    # The issue asks for the token count before AND after: the count is the
    # only thing that makes this bug visible in a log, and it is the "after"
    # number that would have shown the old fallback deleting a token (#560).
    logger.debug(
        "_normalize_author_names: branch=fallback tokens_in=%d tokens_out=%d "
        "changed=%s",
        len(parts), len(cleaned_authors) + (1 if has_et_al else 0),
        result != original,
    )
    return result


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
    """
    Convert markdown-formatted text to plain text suitable for Word document.

    Handles:
    - Bold: **text** -> text
    - Sub-bullets: - (item) -> (item)
    - Headers: # Header -> Header
    - Preserves quotes and other content

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

    Properly structured content (mentee/board tables) is routed to real Word
    tables upstream via classification; this is the fallback for residual text.

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
    # ponytail: shape-match, not a code allowlist — could also strip a leading
    # grant-mechanism token like "[R01] " (rare as a bullet's first token); switch
    # to the TAXONOMY_TO_SECTION key set if that ever shows up in output."""
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


def _squash(text) -> str:
    """Whitespace-FREE normalization for verbatim containment checks."""
    return re.sub(r"\s+", "", str(text or "")).lower()


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
    """
    squashed = _squash(value)
    return bool(squashed) and any(squashed in _squash(f) for f in pii_fragments)
