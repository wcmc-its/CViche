"""Is a value already present in a citation's text? (#481)

Split out of `formatting/values.py` at the round-4 review of PR #737 (point 7).
The test itself is generic word overlap, but its stop list is not: "eds",
"edition", "vol", "publisher" and "chief" identify nothing *in a
bibliography* and plenty in ordinary prose. A matcher that silently encodes
one domain's vocabulary inside a general-purpose token routine is one nobody
can reuse and nobody can read as a table -- so the vocabulary, the threshold
and the routine that applies them are named together here, and tested as a
table in `tests/test_stage6_citation_matching.py`.

Its own module rather than a section of `publication.py`, because the two
answer different questions. `publication.py` asks *what are this entry's
canonical values*; this asks *does this text already say that value*. The
first needs the second -- a stage-5d citation is topped up with the values
its own text omitted -- so the dependency runs `publication` ->
`citation_matching` and never back.

    _value_referenced                is this value already present in this text?
    _append_missing_stage5d_values   top a stage-5d citation up with what it omits
    _restore_stage5d_owner           give back the CV owner 5d's "et al." cut (#1259)

All take text and return text. None reads an entry, a field dict or any
pipeline key: the caller resolves those first (that is `publication.py`'s
job), which is why there is not an `isinstance` check anywhere in this file.
The owner restore reads author names, which `authors.py` owns, so this
module imports that one and nothing else in the package.
"""
import re

from .authors import _join_spaced_initials, _normalize_author_names, _source_authors_after

# #481: a value is treated as already present in an LLM-formatted citation
# once any of its own significant words shows up there -- not the whole
# value verbatim -- so a reworded-but-present publisher/editors ("Springer"
# for "Springer-Verlag, NY") isn't appended a second time. Below this length
# a token (an initial, "of", "NY") is too common to mean anything on its own.
_CITATION_TOKEN_MIN_LEN = 3
# Tokens that clear the length floor and still identify nothing: English
# function words plus the editorial boilerplate a citation carries anyway.
# The floor alone was not enough -- len("and") and len("eds") are both 3, so
# "A. Smith and B. Jones" read as already present in any citation whose text
# contained the word "and" anywhere, and the editors half of the safety net
# below could never fire (found reviewing this fix, #481). Shorter function
# words ("of", "in", "an") need no entry here; the floor already drops them.
_CITATION_STOPWORDS = frozenset({
    'and', 'the', 'for', 'with', 'from', 'that', 'this',
    'eds', 'edited', 'editor', 'editors', 'edition', 'chief',
    'vol', 'volume', 'page', 'pages', 'published', 'publisher',
})
_CITATION_TOKEN_RE = re.compile(r"[^\W_]+")
# How much of a value has to show up before it reads as already present.
# "Any one significant token" was the round-1 rule and it was too loose:
# "Oxford University Press" matched a citation naming "Oxford Medical
# Journal" on "oxford" alone, and the real publisher was then silently
# dropped from the rendered citation (round-2 review of #481, point 6).
# Half, not more: "Springer-Verlag, NY" against "In: Springer; 2021." is a
# genuine match that offers exactly one of its two tokens.
_CITATION_MATCH_MIN_RATIO = 0.5


def _value_referenced(value: str, citation_text: str) -> bool:
    """Whole-word, casefolded overlap test (#481) between a candidate value
    (a publisher or editors string) and an already-formatted citation.

    A token counts only if it clears `_CITATION_TOKEN_MIN_LEN` *and* is not a
    stop word. A value left with no significant token of its own reads as
    absent, so the caller appends it rather than trusting a match on a word
    ("and", "eds") that appears in citations regardless of this value.

    At least `_CITATION_MATCH_MIN_RATIO` of the surviving tokens must appear.
    Known residual, stated rather than papered over: a two-token value with
    one matching token is exactly at the threshold, so "Oxford University"
    against "Oxford Medical Journal" still reads as referenced and that
    publisher is still dropped. Requiring more than half would break
    "Springer-Verlag, NY" against "In: Springer; 2021." -- the same 1-of-2
    shape, but a real match. Token overlap alone cannot separate the two, and
    this deliberately does not try to be cleverer than that.

    Both arguments are text by contract. The `isinstance` guard this used to
    need against a list-shaped stage-4 `editors` now lives once, in
    `publication.resolve_publication`, which is the only thing that reads raw
    stage-4 JSON (round-4 review of #737, points 7 and 13).
    """
    if not value:
        return False
    haystack = citation_text.casefold()
    tokens = [
        t for t in (raw.casefold() for raw in _CITATION_TOKEN_RE.findall(value))
        if len(t) >= _CITATION_TOKEN_MIN_LEN and t not in _CITATION_STOPWORDS
    ]
    if not tokens:
        return False
    matched = sum(1 for t in tokens if re.search(rf"\b{re.escape(t)}\b", haystack))
    return matched / len(tokens) >= _CITATION_MATCH_MIN_RATIO


def _append_missing_stage5d_values(
    formatted_citation: str, editors: str, publisher: str
) -> str:
    """Deterministic safety net for the stage-5d LLM path (#481).

    Stage 5d's copy-back loop -- the `for field in [...]` list of names it
    writes back onto `entry['extracted_fields']` in
    `stage_5d_citation_formatter.py` -- omits `editors` and `publisher` even
    when its own prompt extracted them, so a book/chapter citation the LLM
    formatted without one of those values has no later stage that can add it.
    Append whichever of the two the entry actually carries and the LLM's own
    text does not already reference.

    `editors` and `publisher` arrive as text or as `''`; a non-string
    stage-4 value became `''` in the resolver upstream, so there is no
    type check here and no way for a list's repr to reach a citation.
    """
    additions = []
    if editors and not _value_referenced(editors, formatted_citation):
        additions.append(f"{editors}, eds.")
    if publisher and not _value_referenced(publisher, formatted_citation):
        additions.append(f"{publisher}.")
    if not additions:
        return formatted_citation
    return f"{formatted_citation} " + " ".join(additions)


#: The "et al." that closes a Vancouver author list.
_ET_AL_RE = re.compile(r"\bet al\.?", re.IGNORECASE)

#: A normalized author list item that is initials alone ("Perri G, M"): stage
#: 4 split one author in two, and the list would render that damage (24 of
#: 205 truncated lists on the pilot CVs, #1259).
_LONE_INITIALS_RE = re.compile(r"(?:^|,\s*)[A-Z]{1,3}\s*(?:,|$)")


#: A word of `target_name` that can be the owner's surname: 3+ characters,
#: not all capitals (initials), not a credential or suffix.
_NAME_WORD_RE = re.compile(r"[^\W\d_][\w'-]{2,}")
_NOT_A_SURNAME = frozenset({"phd", "jr", "sr", "msc", "mph", "facp", "frcp"})

#: Marks of a stage-4 author no person carries, which the restore must not
#: print (QITQWH 269/283, HTNNHG 75 on the EBYSBC batch). An all-capitals
#: word of four or more letters the CV's own line does not hold: a
#: consortium credit or an affiliation squeezed into the shape "Network
#: WXYZ". A real four-letter initials group is in the line as printed.
_ALL_CAPS_WORD_RE = re.compile(r"\b[A-Z]{4,}\b")
#: And a suffix standing first in an item ("3rd AB", ", Jr, ").
_SUFFIX_FIRST_RE = re.compile(r"(?:^|,\s*)(?:Jr|Sr|II|III|IV|[2-9](?:nd|rd|th))\b")


def _prints_junk_author(authors: str, source_text: str) -> bool:
    """Whether a normalized stage-4 list holds an author no person carries."""
    source_words = set(re.findall(r"\w+", source_text))
    return bool(_SUFFIX_FIRST_RE.search(authors)) or any(
        word not in source_words for word in _ALL_CAPS_WORD_RE.findall(authors))


def _owner_words(target_name: str) -> set[str]:
    """The words of `target_name` that can be the owner's surname, casefolded."""
    return {w.casefold() for w in _NAME_WORD_RE.findall(target_name)
            if not w.isupper() and w.casefold() not in _NOT_A_SURNAME}


def _names(word: str, text: str) -> bool:
    """Whether `text` holds `word` (casefolded) as a whole word."""
    return bool(re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text.casefold()))


def _stage5d_author_segment(formatted_citation: str) -> str:
    """What a 5d citation lists as authors: everything before an "et al."
    that closes the list (no period ahead of it, no comma after it), else
    the whole citation. ZGBCIT 348 named the owner only in a "Presented by"
    note after the title, so a test over the whole citation called the owner
    present (#1259). "Ash A, et al., Gorse G, et al., Holly H." is a CV's own
    elided list, and its owner is in it."""
    match = _ET_AL_RE.search(formatted_citation)
    if match and "." not in formatted_citation[:match.start()] \
            and not formatted_citation[match.end():].lstrip().startswith((",", ";")):
        return formatted_citation[:match.start()]
    return formatted_citation


def _stage5d_cut_owner(formatted_citation: str, authors: str, target_name: str) -> bool:
    """Whether stage 5d's citation lost the CV owner's name (#1259): a word
    of `target_name` that `authors` (stage 4's list, or the source line)
    names is not among the citation's authors. Only such a citation gets its
    author list back: across the 163-CV wave-1 farm, restoring every cut
    list rewrote 1,117 lines from stage-4 lists that are often damaged ("de
    Groot M" -> "Groot D M", role labels as authors), and lost the owner from
    10 of them."""
    segment = _stage5d_author_segment(formatted_citation)
    return any(_names(w, authors) and not _names(w, segment)
               for w in _owner_words(target_name))


def _restore_stage5d_owner(
    formatted_citation: str, authors: str, source_text: str, target_name: str,
) -> str:
    """`formatted_citation` with the CV owner back in its author list, when
    stage 5d's "first 6, et al." cut them out (#1259); otherwise unchanged.

    Stage 4's list (`authors`) is printed whole when it is intact -- the
    restore PR #1292 shipped, unchanged. When it is damaged or holds junk,
    the CV's own line (`source_text`) supplies the authors after the ones 5d
    kept, read back one at a time; the kept ones keep 5d's spelling."""
    if _stage5d_cut_owner(formatted_citation, authors, target_name):
        source_list = _normalize_author_names(_join_spaced_initials(authors))
        if not _prints_junk_author(source_list, source_text):
            restored = _restore_stage5d_authors(formatted_citation, source_list)
            if restored != formatted_citation:
                return restored
    if _stage5d_cut_owner(formatted_citation, source_text, target_name):
        return _extend_from_source_line(formatted_citation, source_text, target_name)
    return formatted_citation


def _extend_from_source_line(formatted_citation: str, source_text: str, target_name: str) -> str:
    """5d's kept authors, then the ones the CV's line lists after them, then
    "et al." only if the line says it. Unchanged unless that run names the
    owner, and 5d's authors name no word of `target_name` (a given name
    5d shortened to an initial is not a cut owner). A run that meets a
    non-author (a group credit closing it, an affiliation, the title) stops
    there."""
    segment = _stage5d_author_segment(formatted_citation)
    owner = _owner_words(target_name)
    if segment == formatted_citation or any(_names(w, segment) for w in owner):
        return formatted_citation
    kept = [a.strip() for a in segment.split(",") if a.strip()]
    run = _source_authors_after(source_text, kept)
    if run is None:
        return formatted_citation
    following, et_al = run
    if not any(_names(w, author) for author in following for w in owner):
        return formatted_citation
    rest = formatted_citation[len(segment):]
    rest = rest[_ET_AL_RE.match(rest).end():].lstrip(" .")
    tail = ", et al" if et_al else ""
    return f"{', '.join(kept + following)}{tail}. {rest}"


def _restore_stage5d_authors(formatted_citation: str, authors: str) -> str:
    """`formatted_citation` with stage 4's full author list in place of an
    "et al." stage 5d added (#1259).

    The 5d prompt's "first 6 authors, et al." rule cut 206 author lists on
    11 of the 12 pilot CVs and removed the CV owner's own name from 84 of
    them. When the source list (stage 4's `authors`, already normalized) has
    no "et al." of its own, everything up to the citation's first "et al."
    is its author segment, and the source list replaces it. A "." before
    that "et al." means it is not in the author segment (a Vancouver author
    list has none; "In: Topol E, et al., eds." follows the title), and the
    citation is left alone. The rest of the
    5d citation is kept as it is.
    """
    match = _ET_AL_RE.search(formatted_citation)
    if not authors or not match or _ET_AL_RE.search(authors) \
            or _LONE_INITIALS_RE.search(authors) \
            or "." in formatted_citation[:match.start()]:
        return formatted_citation
    if not _same_list_start(formatted_citation[:match.start()], authors):
        return formatted_citation
    rest = formatted_citation[match.end():].lstrip(" .")
    return f"{authors.rstrip('. ')}. {rest}"


def _surnames(author_list: str) -> list[str]:
    """The first word of each comma-separated author, casefolded."""
    return [item.split()[0].casefold() for item in author_list.split(",") if item.split()]


def _same_list_start(cut_segment: str, authors: str) -> bool:
    """Whether the source list starts with the authors 5d kept, by surname.
    On the wave-1 farm a source list that disagreed was a damaged one: a
    misspelled owner ("Yianoutsos" for 5d's "Yiannoutsos") and a consortium
    name run together, both of which the restore would have printed. Such a
    list is not printed; `_extend_from_source_line` reads the CV's line instead."""
    kept = _surnames(cut_segment)
    return bool(kept) and _surnames(authors)[:len(kept)] == kept
