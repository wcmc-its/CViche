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
    _append_missing_stage5d_values   top a stage-5d citation up with what it omits,
                                     each value in its Vancouver position
    _insert_missing_stage5d_year     put back a group-filled year 5d never saw (#1810)
    _restore_stage5d_owner           give back the CV owner 5d's "et al." cut (#1259)

All take text and return text. None reads an entry, a field dict or any
pipeline key: the caller resolves those first (that is `publication.py`'s
job), which is why there is not an `isinstance` check anywhere in this file.
The owner restore reads author names, which `authors.py` owns, so this
module imports that one and nothing else in the package.
"""
import re

from .authors import (
    _join_spaced_initials,
    _normalize_author_names,
    _source_authors_after,
)

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
    tokens = _significant_tokens(value)
    if not tokens:
        return False
    matched = sum(1 for t in tokens if _word_in(t, citation_text))
    return matched / len(tokens) >= _CITATION_MATCH_MIN_RATIO


def _significant_tokens(value: str) -> list[str]:
    """`value`'s casefolded words that clear the length floor and the stop
    list: the ones that can identify it."""
    return [
        t for t in (raw.casefold() for raw in _CITATION_TOKEN_RE.findall(value))
        if len(t) >= _CITATION_TOKEN_MIN_LEN and t not in _CITATION_STOPWORDS
    ]


def _word_in(token: str, text: str) -> bool:
    """True when casefolded `token` is a whole word of `text`."""
    return bool(re.search(rf"\b{re.escape(token)}\b", text.casefold()))


#: The editors clause stage 5d writes for a chapter, "In: Diler RS, ed.",
#: "In: Cabanyes J, Monge MA, eds." or "In: Diler RS (Ed.)": "In:", names
#: with no sentence break, then "ed", "eds" or "editor(s)" after a comma or
#: an opening parenthesis, in any case. ", 2nd ed." is not this clause: the
#: comma or parenthesis must sit directly before "ed".
_EDITORS_CLAUSE_RE = re.compile(
    r"\bIn:\s(?:(?!\.\s).)+?(?:,\s*|\s*\()(?:eds?\b|editors?\b)", re.IGNORECASE)
#: What separates two people in a stage-4 editors value.
_EDITOR_SEPARATOR_RE = re.compile(r"\s*(?:[;,&]|\band\b)\s*")
#: One place name: up to three capitalised words, "Totowa", "New York", "St. Louis".
_PLACE_NAME = r"[A-Z][\w.'-]*(?: [A-Z][\w.'-]*){0,2}"
#: A stage-4 publisher that carries its place after its own name, in a shape
#: only a place has: "Humana Press, Totowa, New Jersey" (a "City, Region"
#: pair after the comma) or "Saunders (New York)" (in parentheses). The name
#: must be non-empty. "Lippincott, Williams Wilkins" does not match: the part
#: after its comma is more of the name, not a "City, Region" pair, and
#: relabelling it as a place would misstate the publisher (#481).
_PUBLISHER_WITH_PLACE_RE = re.compile(
    rf"(?P<name>[^,(]*[^,(\s])\s*"
    rf"(?:\((?P<paren>{_PLACE_NAME}(?:, {_PLACE_NAME})?)\)|,\s*(?P<comma>{_PLACE_NAME}, {_PLACE_NAME}))")
#: A book trailer with no publisher: the year (and pages) alone after a
#: sentence break, closing the citation -- "Welcome to parenting. 2010." The
#: publisher goes in front of it, as Vancouver and `_book_trailer_parts`
#: place it ("Publisher; Year:Pages."). A year that already follows "; "
#: has something in the publisher slot, and a journal trailer
#: ("2002 Jun;31(2):353-368") is not this shape; both leave the publisher
#: appended at the end, as before.
_BARE_BOOK_YEAR_RE = re.compile(r"(?<=\. )(?:19|20)\d\d(?::\S+)?\.?$")
#: The book-title clause an editors name list goes in front of.
_IN_CLAUSE = "In: "


def _editors_referenced(editors: str, citation: str) -> bool:
    """True when the citation already names these editors.

    By the token test, or by person: 5d wrote an editors clause and at
    least half the people in stage 4's value have a word in the citation.
    Stage 4 often spells out names 5d abbreviated ("Rasim Somer Diler" for
    "Diler RS": one token in three), so the token test alone read them as
    absent and printed them a second time after the year: 6 of the 16
    citations the safety net changed across the stored corpus (#481). The
    person-wise test requires the editors clause, so it never lets a chapter
    author who shares an editor's surname stand in for the editors. The
    token test before it has no such requirement and still can: "Doe J. A
    chapter. In: Book." reads editors "Doe J, Roe R" as present.
    """
    if _value_referenced(editors, citation):
        return True
    if not _EDITORS_CLAUSE_RE.search(citation):
        return False
    people = [p for p in _EDITOR_SEPARATOR_RE.split(editors) if _significant_tokens(p)]
    named = sum(1 for p in people if any(_word_in(t, citation) for t in _significant_tokens(p)))
    return bool(people) and named / len(people) >= _CITATION_MATCH_MIN_RATIO


def _with_editors(citation: str, editors: str) -> str:
    """`citation` with an "Editors, eds." clause: after its "In: ", where
    Vancouver puts the editors of a chapter's book, else at the end. A
    citation that already has an editors clause has no free slot there:
    "In: Rey J, ed." with stage 4's longer list in front of it would read as
    two editors clauses, so the list goes at the end, as before."""
    clause = f"{editors}, eds."
    at = citation.find(_IN_CLAUSE)
    if at < 0 or _EDITORS_CLAUSE_RE.search(citation):
        return f"{citation} {clause}"
    at += len(_IN_CLAUSE)
    return f"{citation[:at]}{clause} {citation[at:]}"


def _with_publisher(citation: str, publisher: str) -> str:
    """`citation` with its publisher: in front of a bare closing year
    ("Publisher; Year."), else at the end."""
    trailer = _BARE_BOOK_YEAR_RE.search(citation)
    if not trailer:
        return f"{citation} {publisher.rstrip('.')}."
    return f"{citation[:trailer.start()]}{publisher.rstrip('.')}; {trailer.group()}"


def _with_publisher_place(citation: str, publisher: str) -> str | None:
    """`citation` with the place of stage 4's `publisher` put in front of the
    publisher name it already prints ("Totowa, New Jersey: Humana Press;
    1992."), or None when there is nothing safe to move: `publisher` is not
    a name followed by a place (`_PUBLISHER_WITH_PLACE_RE`), or the citation
    has no "Name;" to stand before, or a place already there."""
    parts = _PUBLISHER_WITH_PLACE_RE.fullmatch(publisher)
    if not parts:
        return None
    place = parts["paren"] or parts["comma"]
    slot = re.search(rf"(?<!: ){re.escape(parts['name'])};", citation, re.IGNORECASE)
    if not slot:
        return None
    return f"{citation[:slot.start()]}{place}: {citation[slot.start():]}"


def _with_missing_publisher(citation: str, publisher: str) -> str:
    """`citation` with whatever of stage 4's publisher it does not print.

    Stage 4 writes a publisher with its place ("Humana Press, Totowa, New
    Jersey"). When 5d printed the name and dropped the place, the place
    goes in front of the name, where Vancouver puts it; printing the whole
    value again after the year named the publisher twice (#481). Only a
    value whose tail has a place's shape counts as name plus place
    (`_PUBLISHER_WITH_PLACE_RE`); any other value keeps the whole of it,
    positioned by `_with_publisher`.

    Past the first test, a citation printing the name followed by ";" lacks
    the place: the whole value's token ratio lies between its name's and its
    place's, so a printed name with a printed place cannot get here.
    """
    if _value_referenced(publisher, citation):
        return citation
    placed = _with_publisher_place(citation, publisher)
    return placed if placed is not None else _with_publisher(citation, publisher)


def _append_missing_stage5d_values(
    formatted_citation: str, editors: str, publisher: str
) -> str:
    """Deterministic safety net for the stage-5d LLM path (#481).

    Stage 5d's LLM sometimes formats a book or chapter without the editors
    or publisher stage 4 extracted from the same line, and no later stage
    can add them -- except this one. Each value the entry carries and the
    LLM's own text does not already name goes where a Vancouver citation
    puts it: the editors after "In: ", the publisher before a bare closing
    year, its place before its name. Only a citation with no such place
    gets the value at its end.

    `editors` and `publisher` arrive as text or as `''`; a non-string
    stage-4 value became `''` in the resolver upstream, so there is no
    type check here and no way for a list's repr to reach a citation.
    """
    # Both are judged against 5d's own text, so an added editors clause can
    # never read as the publisher. The publisher is placed first because
    # its slot is the closing year, which editors appended at the end would
    # no longer be.
    editors_missing = bool(editors) and not _editors_referenced(editors, formatted_citation)
    citation = formatted_citation
    if publisher:
        citation = _with_missing_publisher(citation, publisher)
    if editors_missing:
        citation = _with_editors(citation, editors)
    return citation


#: A four-digit year anywhere in a citation. One is enough to leave the
#: citation alone: stage 5d wrote some year, and a second would contradict it.
_CITATION_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d\d(?!\d)")
#: The journal trailer 5d writes with no year in front of it, "Vol(Issue):Pages."
#: closing the citation after a sentence break.
_YEARLESS_JOURNAL_TRAILER_RE = re.compile(r"(?<=\. )\d+\w*(?:\([^)]*\))?:\S+$")


def _insert_missing_stage5d_year(formatted_citation: str, year: str, location: str) -> str:
    """Put `year` into a stage-5d citation that has no year at all (#1810).

    Stage 5d's LLM formats the CV's own line. A line dated only by its year
    group's heading (#1803) holds no year, so the citation has none either,
    although stage 4 filled one. The year goes where Vancouver puts it: before
    the place of a meeting ("Meeting; Year; Place.", the shape 5d writes for a
    dated talk), before a journal's volume ("Year;Vol(Issue):Pages.", as
    `formatting/values.py`'s `_journal_trailer_parts` writes it), else last.
    """
    if not year or _CITATION_YEAR_RE.search(formatted_citation):
        return formatted_citation
    place = f"; {location}."
    if location and formatted_citation.endswith(place):
        return f"{formatted_citation[:-len(place)]}; {year}{place}"
    trailer = _YEARLESS_JOURNAL_TRAILER_RE.search(formatted_citation)
    if trailer:
        return f"{formatted_citation[:trailer.start()]}{year};{trailer.group()}"
    return f"{formatted_citation} {year}."


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
    "et al." when the line's run goes on past them: it says "et al.", or the
    read stopped at a person it cannot read. Unchanged unless that run names
    the owner, and 5d's authors name no word of `target_name` (a given name
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
    following, goes_on = run
    if not any(_names(w, author) for author in following for w in owner):
        return formatted_citation
    rest = formatted_citation[len(segment):]
    rest = rest[_ET_AL_RE.match(rest).end():].lstrip(" .")
    tail = ", et al" if goes_on else ""
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
