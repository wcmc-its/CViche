"""Author names, in one citation spelling.

Owns one domain: a CV's raw ``authors`` string in, Vancouver-style
"Surname Initials" elements out. It changes when a new author-list spelling
variant is observed in a CV, and for no other reason -- a leaked taxonomy
code or a new protected-data label does not touch a line of it.
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
# exactly as "Alpha, PL" is. 4 of the farm's 1,711 distinct author strings
# carry one (`measure_normalization_claims.py --only marks`).
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
    pair parser's surname-slot test, its upper-casing, and the fallback
    parser -- so a token can no longer be classified one way on one path and
    the other way on the other. Of those four, only the FALLBACK's own
    definition is measured: `measure_normalization_claims.py --only initials`
    reports `classified differently by the two: 81`.

    A single alphabetic character of any case or script is always an initial
    -- #560 gives "Kelly, r" and "Kelly, Å" as shapes the old ASCII-uppercase
    gate rejected, and a lone letter has no other plausible reading. 2-4
    characters must still be uppercase: a short mixed-case word ("Scot",
    "Li", "Wei") is at least as likely to be a real given name as an initials
    group. Hyphenated initials ("R-Y") follow the same rule per side.
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
    deleted it outright: "AB, PL, Smith, JA" rendered as "Smith JA".

    That placement follows `_parse_author_fallback`'s orphan rule on the
    FIRST orphan and diverges on the second, deliberately and on both sides:
    `test_the_two_parsers_place_a_second_consecutive_orphan_differently`
    holds the shapes.
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
            # do. Emitting it re-opens the merge target, so consecutive
            # orphans coalesce -- where this diverges from
            # `_parse_author_fallback`. The corpus does not adjudicate that:
            # `measure_normalization_claims.py --only orphans` reports
            # `strings hitting the surname slot : 1`, placing two tokens back
            # to back, `{'A': 2}` by shape -- two single-letter tokens
            # standing where a surname should be. #560's bar, no token
            # deleted, is met on either reading.
            if merge_target_open:
                cleaned_authors[-1] = f"{cleaned_authors[-1]} {surname}"
                merge_target_open = False
            else:
                cleaned_authors.append(surname)
                merge_target_open = True
            i += 1
            continue

        # Normalize spaced initials: "P L" -> "PL". The shape test before
        # the upper-casing is belt and braces -- the orphan branch above
        # restores the parity, so a real surname should not reach this slot
        # -- but nothing enforces that, and rendering a surname as "SMITH"
        # would be a fresh corruption of a name (#560).
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
    the exact shape a stray comma produces ("Alpha, L" split by one
    comma that shouldn't be there). An author that already has its own
    initials ("Bravo SM") is not reopened by a later fragment; a
    fragment with nothing open to attach to is emitted as its own element
    rather than dropped, so the token count never falls (#560).
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
                    # Nothing open to charge this fragment to: it is
                    # unattributable, not absent, so it is kept as its own
                    # element. The live shape is "... Alpha H, F, MJ. K" --
                    # the author before the fragment already carries its own
                    # initial, so it is closed, and dropping the "F" is the
                    # deletion #560 is about.
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

    Never drops a token that names or belongs to a real author (#560). Both
    parsers used to: the fallback discarded any comma-split token it could
    not place, case-blind, so a short *surname* ("Li", "Wu", "Ma", "Ye")
    went the way of a short initials fragment; the pair parser skipped an
    initials group standing in a surname slot.
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

    # Structural metadata only: these strings are CV-derived author names,
    # and debug logs land in centralized systems with wider retention and
    # access than the app itself. The token counts say nothing about who the
    # authors are, and a token disappearing between "in" and "out" is
    # exactly what #560 is.
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
    # Before AND after: it is the "after" count that would have shown the
    # old fallback deleting a token (#560).
    logger.debug(
        "_normalize_author_names: branch=fallback tokens_in=%d tokens_out=%d "
        "changed=%s",
        len(parts), len(cleaned_authors) + (1 if has_et_al else 0),
        result != original,
    )
    return result


#: A capital initial followed by a space and another lone capital ("D M").
_SPACED_INITIAL_RE = re.compile(r"\b([A-Z]) (?=[A-Z]\b)")


def _join_spaced_initials(authors: str) -> str:
    """"Maahs, D M" -> "Maahs, DM": `_normalize_author_names`' pair parser
    reads only joined initials, and turned "Maahs, D M" into "Maahs M, D"
    (#1259, stage-4 author lists on JFBPNC)."""
    return _SPACED_INITIAL_RE.sub(r"\1", authors)


# ---------------------------------------------------------------------------
# A CV's own author run, read back from the source line (#1259)
# ---------------------------------------------------------------------------
#
# Stage 6 gives the CV owner back to a citation stage 5d cut to "first 6,
# et al.". Stage 4's `authors` is the first choice, but on the EBYSBC batch it
# was too damaged to print on 20 owner-cut citations in 14 CVs (lone
# initials, inverted names, a "Jr" or a given name as its own item, a lost
# hyphen) and printed junk on three more (a consortium credit and an
# affiliation read as authors). The CV's own line still holds the run, in
# whatever spelling that CV uses. The functions below read it back one author
# at a time and accept only these shapes; anything else ends the read, so a
# title word, a group credit or an affiliation is never printed as an author.
# A read that ends at a person it cannot read says "et al." instead of
# printing a list that looks complete:
#
#     Surname Initials [Suffix]    "Wren C", "Quill W.C.", "Rowan ID 3rd", "Le T"
#     Surname, Initials            "Sorrel, M.H.", "Tansy, .S" (a stray period)
#     Initials Surname [Suffix]    "J. Umber", "J.R. Vetch Jr."
#     Given Initial Surname        "Opal V. Yarrow"     given-name-first
#     Given Surname Credential     "Juno Zinnia MD"     runs only

#: What separates two authors in a CV's author run: a comma or semicolon, one
#: followed by "and"/"&", or a bare "and"/"&".
_SOURCE_AUTHOR_SEPARATOR_RE = re.compile(
    r"\s*(?:[,;]\s*(?:and|&)\s+|[,;]|\s+(?:and|&)\s+)\s*")

#: Words that open a surname rather than end a given name ("van den Wren",
#: "de Quill", "Van Rowan"). Compared casefolded.
_SURNAME_PARTICLES = frozenset({
    'van', 'von', 'de', 'den', 'der', 'del', 'della', 'da', 'di', 'du',
    'la', 'le', 'ten', 'ter', 'dos', 'das',
})

#: Degrees a given-name-first run prints after a surname ("Juno Zinnia MD").
#: Compared casefolded, without a trailing period.
_AUTHOR_CREDENTIALS = frozenset({
    'md', 'phd', 'ma', 'ba', 'bs', 'bsc', 'ms', 'msc', 'mph', 'med', 'rn',
    'do', 'pharmd', 'mba', 'msn', 'np', 'mbbs', 'dds', 'dmd', 'drph', 'mhs',
})

#: An ordinal suffix ("Rowan ID 3rd"). `_AUTHOR_SUFFIX_RE` does not name
#: these, and widening it would also move `_normalize_author_names`, which
#: every parts-built citation goes through.
_ORDINAL_SUFFIX_RE = re.compile(r"^[2-9](?:nd|rd|th)$")

#: A two-letter abbreviated initial, capital then small, with its period
#: ("Th."). Without the period a two-letter word is a short name ("Bo").
_ABBREVIATED_INITIAL_RE = re.compile(r"^[A-Z][a-z]\.$")

#: The most source tokens one author takes ("Opal B. C. van den Wren").
_MAX_AUTHOR_TOKENS = 6

#: A word of an author run: what the CV prints between spaces, less the
#: co-first and corresponding-author marks ("Wren JA*", "Wren JA∗" with the
#: asterisk operator, daggers).
_AUTHOR_TOKEN_RE = re.compile(r"[^\s*\u2217†‡]+")

#: The most tokens a bare surname's given name and initials take after its
#: comma ("Quill, Opal J.", "Sorrel, H.-P.").
_MAX_TRAILER_TOKENS = 3

#: What opens a title or a note rather than an author: a quote or a bracket.
_RUN_BREAK_OPENERS = '“"(['
#: A line break or one of those, anywhere in a piece of the run.
_RUN_BREAK_RE = re.compile(r"\n|[" + re.escape(_RUN_BREAK_OPENERS) + "]")
#: A period or colon that ends a sentence rather than an initial's period.
_SENTENCE_END_RE = re.compile(r"[.:](?:\s|$)")

#: A group author inside a run ("The Lantern Study Group"): kept as the CV
#: prints it, because the run goes on to name more people, the owner among
#: them. One that closes the run ("...; for the X Network. Title") holds the
#: end of a sentence, and is not printed.
_GROUP_AUTHOR_RE = re.compile(
    r"\b(?:Group|Consortium|Network|Investigators|Collaborative|Collaboration"
    r"|Committee|Team|Trialists|Alliance)\b")

#: The shortest anchor surname matched at one edit's distance: 5d corrects a
#: co-author's spelling ("Rowen" in the CV, "Rowan" in the citation).
_ANCHOR_FUZZY_MIN_LEN = 5

#: Horizontal whitespace inside a run ("Wren      D."), the no-break space
#: included. Line breaks are kept: one ends the run.
_INLINE_SPACE_RE = re.compile(r"[ \t\u00a0]+")


def _run_ended_in(piece: str) -> bool:
    """Whether a piece that holds one author also holds the end of the run: a
    line break, a quote or a bracket, or a lower-case word no surname carries
    ("Zinnia MD. Tracking in a trial"). Particles ("van") and camel-cased
    names ("deVetch") are a surname's own."""
    if _RUN_BREAK_RE.search(piece):
        return True
    return any(word.islower() and word not in _SURNAME_PARTICLES
               for word in re.findall(r"[^\W\d_]+", piece))


def _is_initials_token(token: str) -> bool:
    """'JA', 'W.C.', 'H.-P.', 'S.', 'Th.': `_looks_like_initials` once the
    abbreviating periods are gone, or one abbreviated two-letter initial."""
    return (_looks_like_initials(token.replace('.', ''))
            or bool(_ABBREVIATED_INITIAL_RE.match(token)))


def _is_suffix_token(token: str) -> bool:
    return bool(_AUTHOR_SUFFIX_RE.match(token) or _ORDINAL_SUFFIX_RE.match(token))


def _is_name_token(token: str) -> bool:
    """A surname or given-name word: capitalised ("O'Wren", "Quill-Rowan") or
    camel-cased ("deVetch"), and not an initials group or a suffix."""
    core = re.sub(r"['’-]", '', token)
    if not core.isalpha() or _is_initials_token(token) or _is_suffix_token(token):
        return False
    return core[0].isupper() or any(c.isupper() for c in core[1:])


def _surname_at(tokens: list[str]) -> tuple[str, list[str]] | None:
    """Leading particles plus one name word, and the tokens after them.

    Capitalised particles with no name word after them are the surname
    itself ("Le T", "Du D", "T. Le"); each caller checks that only initials,
    or nothing, follow. Read as a particle, such a co-author's surname
    stopped the run there and dropped the co-authors after it (QITQWH
    269/283 on the EBYSBC farm, #1259)."""
    i = 0
    while i < len(tokens) and tokens[i].casefold() in _SURNAME_PARTICLES:
        i += 1
    if i < len(tokens) and _is_name_token(tokens[i]):
        return ' '.join(tokens[:i + 1]), tokens[i + 1:]
    if i and tokens[i - 1][0].isupper():
        return ' '.join(tokens[:i]), tokens[i:]
    return None


def _initials_of(tokens: list[str]) -> str | None:
    """Consecutive initials tokens as one Vancouver group ("S. R." -> "SR")."""
    if not tokens or not all(_is_initials_token(t) for t in tokens):
        return None
    return ''.join(t.replace('.', '').replace('-', '') for t in tokens).upper()


def _given_first_author(tokens: list[str]) -> str | None:
    """"Opal V. Yarrow" -> "Yarrow OV", "Juno Zinnia MD" -> "Zinnia J". Needs
    a middle initial or a credential: "Juno Zinnia" alone reads either way."""
    if not tokens or not _is_name_token(tokens[0]):
        return None
    surname_start = 1
    while surname_start < len(tokens) and _is_initials_token(tokens[surname_start]):
        surname_start += 1
    middle = _initials_of(tokens[1:surname_start]) or ''
    split = _surname_at(tokens[surname_start:])
    if split is None or not (middle or split[1]):
        return None
    if not all(t.rstrip('.').casefold() in _AUTHOR_CREDENTIALS for t in split[1]):
        return None
    return f"{split[0]} {tokens[0][0].upper()}{middle}"


def _vancouver_author(tokens: list[str], given_first: bool) -> str | None:
    """One author's source words as "Surname Initials[ Suffix]", or None when
    they are not one of the shapes listed above this section.

    A last word shaped like a suffix is one only when the words before it
    are an author ("Vetch JR Jr", "Opal V. Yarrow Jr"). Otherwise it is the
    initials: "Wren SR" and "Gorse JR" are initials, which `_AUTHOR_SUFFIX_RE`
    (case-blind) also matches. Read as a suffix, they stopped the read on 10
    of the 1,387 cut citations on the 63-run farm (#1259)."""
    if len(tokens) > 1 and _is_suffix_token(tokens[-1]):
        author = _unsuffixed_author(tokens[:-1], given_first)
        if author is not None:
            return f"{author} {tokens[-1].rstrip('.')}"
    return _unsuffixed_author(tokens, given_first)


def _unsuffixed_author(tokens: list[str], given_first: bool) -> str | None:
    """`_vancouver_author` once a suffix is set aside."""
    surname_first = _surname_at(tokens)
    if surname_first is not None and _initials_of(surname_first[1]):
        return f"{surname_first[0]} {_initials_of(surname_first[1])}"
    name_start = next((i for i, t in enumerate(tokens) if not _is_initials_token(t)), 0)
    initials_first = _surname_at(tokens[name_start:])
    if (name_start and _initials_of(tokens[:name_start])
            and initials_first is not None and not initials_first[1]):
        return f"{initials_first[0]} {_initials_of(tokens[:name_start])}"
    if given_first:
        return _given_first_author(tokens)
    return None


def _item_words(tokens: list[str]) -> list[str]:
    """An author's words as printed, less the colon or period that closed the
    run ("Wren AB:", "Juno Zinnia."). An initial keeps its own period, which
    is all that marks "Th." as one."""
    words = list(tokens)
    if words:
        last = words[-1].rstrip(':')
        words[-1] = last if _is_initials_token(last) else last.rstrip('.')
    return [w for w in words if w]


def _closes_run(piece: str, tokens: list[re.Match[str]], n: int) -> bool:
    """Whether the run ends after the first `n` tokens of a piece: the n-th
    ends in a period or colon, or a line break, quote or bracket follows."""
    if tokens[n - 1].group().endswith(('.', ':')):
        return True
    gap = piece[tokens[n - 1].end():tokens[n].start()]
    return '\n' in gap or tokens[n].group()[0] in _RUN_BREAK_OPENERS


def _leading_author(
    piece: str, open_surname: list[str], given_first: bool,
) -> tuple[str | None, bool]:
    """The author a piece of the run starts with, and whether the run ends
    after it. `open_surname` is a bare surname this piece's initials complete
    ("Sorrel" then "M.H."). The whole piece is tried first, then the
    longest prefix that closes the run -- longest, so that "Opal V. Yarrow."
    is not read as "Opal V"."""
    tokens = list(_AUTHOR_TOKEN_RE.finditer(piece))
    words = [t.group() for t in tokens]
    whole = _vancouver_author(open_surname + _item_words(words), given_first)
    if whole is not None:
        return whole, False
    for n in range(min(len(tokens) - 1, _MAX_AUTHOR_TOKENS), 0, -1):
        if _closes_run(piece, tokens, n):
            author = _vancouver_author(open_surname + _item_words(words[:n]), given_first)
            if author is not None:
                return author, True
    return None, False


def _group_author(piece: str) -> str | None:
    """A group author inside the run, as the CV prints it, or None when the
    piece names no group or also holds the end of a sentence."""
    if (_GROUP_AUTHOR_RE.search(piece) and not _SENTENCE_END_RE.search(piece)
            and not _RUN_BREAK_RE.search(piece)):
        return ' '.join(piece.split())
    return None


def _is_author_word(word: str) -> bool:
    """A word one author's name can hold: a name word, an initials group (a
    credential such as "MD" is shaped like one), a particle or a suffix."""
    return (_is_name_token(word) or _is_initials_token(word) or _is_suffix_token(word)
            or word.casefold() in _SURNAME_PARTICLES)


def _names_a_person(piece: str, given_first: bool) -> bool:
    """Whether a piece the reader could not read as an author still names a
    person, so the run goes on past the authors read. Up to where the piece
    ends a sentence or breaks the run: one to `_MAX_AUTHOR_TOKENS` words, each
    one an author can hold, no group word, and an initials group unless the
    run is given-name-first. "S. Gorse Holly" does, and so does "Juno Wren"
    in a run of "Given I. Surname" items; a title ("The Lantern Effect", with
    no initials), an affiliation with a lower-case word ("School of
    Medicine"), a closing group credit, a lone credential ("MD.") or "J Wood
    2020" do not."""
    ends = [m.start() for m in (_SENTENCE_END_RE.search(piece), _RUN_BREAK_RE.search(piece)) if m]
    head = piece[:min(ends)] if ends else piece
    words = _item_words(_AUTHOR_TOKEN_RE.findall(head))
    return (0 < len(words) <= _MAX_AUTHOR_TOKENS and not _GROUP_AUTHOR_RE.search(head)
            and all(_is_author_word(w) for w in words)
            and not all(w.rstrip('.').casefold() in _AUTHOR_CREDENTIALS for w in words)
            and (given_first or any(_is_initials_token(w) for w in words)))


def _authors_in_run(pieces: list[str], given_first: bool) -> tuple[list[str], bool] | None:
    """Read authors off the run's pieces until one is not an author. Returns
    them and whether the run goes on past them -- it says "et al.", or the
    piece that stopped the read still names a person -- or None when a bare
    surname is left with no initials to complete it. A list that stops short
    of the run's end must say "et al." rather than read as complete (#1259:
    a reader that stopped at a particle-word surname printed QITQWH 283 five
    co-authors short)."""
    authors: list[str] = []
    open_surname: list[str] = []
    for piece in pieces:
        words = _item_words(_AUTHOR_TOKEN_RE.findall(piece))
        if not words:
            continue
        if piece.casefold().startswith('et al'):
            return None if open_surname else (authors, True)
        if authors and not open_surname and len(words) == 1 and _is_suffix_token(words[0]):
            authors[-1] = f"{authors[-1]} {words[0].rstrip('.')}"
            continue
        bare = _surname_at(words)
        if not open_surname and bare is not None and not bare[1]:
            open_surname = words
            continue
        if open_surname and _vancouver_author(words, given_first) is not None:
            break  # "Lantern, D. Wren": a whole author, so nothing completes the bare word
        author, run_ends = _leading_author(piece, open_surname, given_first)
        if author is None and not open_surname:
            author = _group_author(piece)
        if author is None and _initials_of(words) is not None:
            return None  # initials with no surname to finish: the pairs are misaligned
        if author is None:
            return None if open_surname else (authors, _names_a_person(piece, given_first))
        authors.append(author)
        open_surname = []
        if run_ends:
            break
    return None if open_surname else (authors, False)


def _one_edit_apart(a: str, b: str) -> bool:
    """One substitution, insertion or deletion turns `a` into `b`."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i:] == b[i + 1:] or (len(a) == len(b) and a[i + 1:] == b[i + 1:])


def _letters(word: str) -> str:
    """A word's letters only, casefolded ("Quill-Rowan" -> "quillrowan")."""
    return ''.join(c for c in word if c.isalpha()).casefold()


def _anchor_in_source(source_text: str, surname: str, occurrence: int) -> re.Match[str] | None:
    """The `occurrence`-th word of the source line that is `surname`, compared
    by `_letters` ("Quill-Rowan" is "QuillRowan"); one edit apart when no
    word matches exactly and the surname is long enough to trust that."""
    key = _letters(surname)
    words = list(re.finditer(r"[^\W\d_][\w'’-]*", source_text))
    matches = [w for w in words if _letters(w.group()) == key]
    if not matches and len(key) >= _ANCHOR_FUZZY_MIN_LEN:
        matches = [w for w in words if _one_edit_apart(_letters(w.group()), key)]
    return matches[occurrence - 1] if len(matches) >= occurrence else None


def _last_surname_word(author: str) -> str:
    """A Vancouver author's last surname word ("van den Wren PA" -> "Wren",
    "Vetch JR Jr" -> "Vetch")."""
    words = author.split()
    while len(words) > 1 and (_is_initials_token(words[-1]) or _is_suffix_token(words[-1])):
        words.pop()
    return words[-1] if words else ''


def _is_anchor_trailer(piece: str) -> bool:
    """A piece that only finishes the author before it: its initials or its
    suffix printed after a comma ("Sorrel, J", "Wren AB, Jr")."""
    words = _item_words(_AUTHOR_TOKEN_RE.findall(piece))
    return bool(words) and (_initials_of(words) is not None
                            or (len(words) == 1 and _is_suffix_token(words[0])))


def _source_authors_after(
    source_text: str, kept: list[str],
) -> tuple[list[str], bool] | None:
    """The authors a CV's source line lists after the last of `kept` (the
    authors stage 5d kept, in its own spelling), each as "Surname Initials",
    and whether the run goes on past them: the line says "et al.", or the
    read stopped at a person it cannot read (#1259). None when that author
    cannot be found in the line, or the run leaves a bare surname.

    The anchor is the last kept author's surname, at the same occurrence it
    has among the kept ("Elm EC, Elm M" anchors on the second "Elm"). Its
    item runs to the next separator, plus the piece after a bare surname
    ("Sorrel, J", "Quill, Opal J.") and any piece that only repeats initials
    or adds a suffix. The run reads given-name-first only when a name word
    came before the anchor's surname and no initials follow it ("Juno
    Zinnia MD", not the two-word surname of "Quill Rowan M")."""
    surname = _last_surname_word(kept[-1]) if kept else ''
    if not surname:
        return None
    occurrence = sum(1 for author in kept if _last_surname_word(author) == surname)
    anchor = _anchor_in_source(source_text, surname, occurrence)
    if anchor is None:
        return None
    item_head = [t for t in _SOURCE_AUTHOR_SEPARATOR_RE.split(source_text[:anchor.start()])[-1].split()
                 if t.casefold() not in _SURNAME_PARTICLES]
    pieces = _SOURCE_AUTHOR_SEPARATOR_RE.split(
        _INLINE_SPACE_RE.sub(' ', source_text[anchor.start():]))
    anchor_words = _item_words(_AUTHOR_TOKEN_RE.findall(pieces[0]))
    if len(anchor_words) > _MAX_AUTHOR_TOKENS or _run_ended_in(pieces[0]):
        return [], False
    following = pieces[1:]
    if not item_head and len(anchor_words) == 1 and following:
        # A bare surname: its initials or given name follow ("Quill, Opal J.").
        trailer = following.pop(0)
        if len(_AUTHOR_TOKEN_RE.findall(trailer)) > _MAX_TRAILER_TOKENS or _run_ended_in(trailer):
            return [], False
    while following and _is_anchor_trailer(following[0]):
        following = following[1:]
    given_first = (any(_is_name_token(t) for t in item_head)
                   and not any(_is_initials_token(w) for w in anchor_words[1:]))
    return _authors_in_run(following, given_first)
