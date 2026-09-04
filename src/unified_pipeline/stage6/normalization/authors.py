"""Author names, in one citation spelling.

Owns one domain: a CV's raw ``authors`` string in, Vancouver-style
"Surname Initials" elements out. It changes when a new author-list spelling
variant is observed in a CV, and for no other reason -- a leaked taxonomy
code or a new protected-data label does not touch a line of it.

Split out of the former ``text.py``, which held this and five other
unrelated domains behind one import. Names keep their leading underscore for
now. Renaming and relocating in one change would make a failure impossible
to attribute to either.
"""
import logging
import re

logger = logging.getLogger(__name__)


# Name suffixes that belong to the surname immediately before them, never to
# an initials slot of their own: "Smith, John, Jr., Brown" is one author with
# a suffix, not two authors named "Jr." and "Brown" (#560).
_AUTHOR_SUFFIX_RE = re.compile(r'^(?:Jr|Sr|II|III|IV)\.?$', re.I)

# Marks an author list puts *on* an initials group rather than in it: the
# abbreviating period, and the co-first / corresponding-author asterisk and
# daggers. Stripped before the shape test, so "Alpha, PL*" is classified
# exactly as "Alpha, PL" is instead of falling out of the initials rule on a
# typographic mark. 4 of the farm's 1,711 distinct author strings carry a
# marked initials group; regenerate with
# `scripts/measure_normalization_claims.py --only marks`.
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
    pair parser's surname-slot test, the pair parser's upper-casing, and the
    fallback parser -- so a token can no longer be classified one way on one
    path and the other way on the other. The surname-slot test was the last
    holdout: it asked `len(surname) <= 2 and surname.isupper()` on its own,
    a fourth definition that disagreed with this one on marked groups
    ("PL*"), on 3-4 letter groups and on a lone lowercase initial.
    The fallback used to carry its own pair of tests (an ASCII-only
    1-3 uppercase-letter regex, plus a case-blind "2 characters or fewer and
    isupper()"), and those disagreed with this function on spaced groups
    ("N J"), four-letter groups, hyphenated groups and lone lowercase
    initials -- 81 of the farm's 1,711 distinct author strings carry at
    least one comma-token the two definitions classify differently.
    Regenerate with
    `scripts/measure_normalization_claims.py --only initials`.

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
    exception, an initials group turning up in a SURNAME slot, which
    advances by one and therefore shifts the loop off that parity for
    everything after it.

    Nothing is discarded, on either shape (#560). A trailing element with no
    initials to pair with is emitted on its own; an initials group in a
    surname slot -- a list that is missing a surname, so the group has no
    pair -- is placed rather than skipped over. It used to be skipped, which
    deleted it outright: "AB, PL*, Smith, JA" rendered as "PL* Smith, JA".

    That placement follows `_parse_author_fallback`'s orphan rule on the
    FIRST orphan and diverges on the second. Both merge an orphan into the
    element before it while that element is still "open", and both emit it
    on its own when nothing is open. This parser then leaves the emitted
    orphan open, so a second consecutive orphan coalesces into it
    (["Alpha", "B", "C", "D", "Echo", "F"] -> "Alpha B", "C D", "Echo F");
    the fallback closes it, so a second fragment stands alone
    (["Alpha B", "C", "D", "Echo F"] -> "Alpha B", "C", "D", "Echo F").
    The divergence is deliberate -- it is an explicit assignment on each
    side, and
    `test_the_two_parsers_place_a_second_consecutive_orphan_differently`
    pins both -- and the corpus does not adjudicate it; see the branch
    comment below for what it does say.
    """
    cleaned_authors: list[str] = []
    merge_target_open = False
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
            merge_target_open = False
            i += 2
            continue

        if _looks_like_initials(surname):
            # An initials group in a surname slot: the source list is
            # missing the surname it belongs to, so there is no pair to
            # make. Merged into the element before it while that element is
            # still "open" (a bare token carrying no initials of its own
            # yet), emitted on its own when there is nothing open to charge
            # it to -- and never dropped, which is what this branch used to
            # do. Emitting it re-opens the merge target, which is where
            # this diverges from `_parse_author_fallback`; see the docstring
            # above.
            #
            # Consecutive orphans therefore coalesce into one element. What
            # the corpus actually says about that, measured with
            # `scripts/measure_normalization_claims.py --only orphans`: of
            # the farm's 1,711 distinct author strings, 228 reach this
            # parser and exactly 1 reaches this branch, where it places two
            # tokens back to back -- each a single uppercase letter (shapes
            # {'A': 2}, runs {2: 1}). So the corpus does NOT show a surname
            # being rejoined to its own initial; there is no surname in that
            # string at all, only two bare letters. Coalescing renders them
            # as one element with the shape of a Vancouver author, not
            # coalescing renders them as two bare letters, and nothing in
            # the source says which reading is right. #560's bar -- no
            # token deleted -- is met either way, which is the part the
            # measurement does settle.
            if merge_target_open:
                cleaned_authors[-1] = f"{cleaned_authors[-1]} {surname}"
                merge_target_open = False
            else:
                cleaned_authors.append(surname)
                merge_target_open = True
            i += 1
            continue

        # Normalize spaced initials: "P L" -> "PL". Upper-case only a
        # token that is actually initials-shaped. Belt and braces since the
        # orphan branch above started placing rather than skipping: it
        # advances by one onto an odd-indexed token, which the detector has
        # already validated as initials, so that token takes the orphan
        # branch too and the parity restores -- a real surname can no longer
        # reach this slot. Nothing enforces that reasoning, and rendering a
        # surname as "SMITH" would be a new corruption of a name this
        # function is supposed to leave alone (#560), so the test stays.
        initials_normalized = initials.replace(' ', '')
        if _looks_like_initials(initials_normalized):
            initials_normalized = initials_normalized.upper()

        cleaned_authors.append(f"{surname} {initials_normalized}")
        merge_target_open = False
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

    Never drops a token that names or belongs to a real author (#560), on
    either parser. Both used to.

    The fallback discarded any comma-split token it could not place -- an
    initials group, a name suffix, a bare 1-2 character fragment -- and that
    test was case-blind: a short *surname* ("Li", "Wu", "Ma", "Ye") was
    discarded exactly like a short initials fragment. Because a single
    missing comma anywhere in the list is enough to make the pair detector
    decline the whole string, that one discard rule was stripping every
    later author's initials from citations that had them, or dropping
    short-surnamed authors outright.

    The pair parser had a discard of its own, on the shape the fallback
    never sees: an initials group sitting where a surname should be, which
    it skipped over. "AB, PL*, Smith, JA" lost "AB" outright, and the farm's
    one live occurrence of the shape lost two tokens together, both single
    uppercase letters. It is placed rather than skipped now.

    In both, such a token merges into the author immediately before it, and
    when there is no open author to merge into it is emitted as its own
    element instead. Neither parser reduces the token count any more. They
    are not the same rule, though: on a SECOND consecutive orphan the pair
    parser coalesces and the fallback does not -- deliberately, and pinned
    on both sides. `_parse_surname_initial_pairs`' docstring has the shapes.
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
