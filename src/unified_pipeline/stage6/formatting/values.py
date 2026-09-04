"""Rendering a value as the string that appears on the page (#398).

The sibling of `docx.py`, split from it on what they operate on: `docx.py` takes
a python-docx object and mutates its appearance; this takes a value and returns
the text. Neither knows what a CV is.

The line against `normalization` is also deliberate. Normalization decides what a
value *is* -- which author-name spelling, which institution string. This decides
how it *reads*: 14876 becomes "$14,876", a publication record becomes a numbered
Vancouver citation. A change to one should not require a change to the other.

The citation half is the sharpest example, and it did not start out that way.
`_format_citation` used to resolve its own fields -- enrichment precedence, type
guards, stage-5d reconciliation, author normalization -- and then render them, so
the two concerns were one 123-line function and the renderer named four pipeline
keys. It now receives a `ResolvedPublication` from
`normalization/publication.py`, and what is left here is the rendering: which
shape a record takes, and what punctuation joins it (round-4 review of PR #737,
points 1/2/3 and 8-15).
"""
import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict

from ..normalization import ResolvedPublication, resolve_publication

# Money is quantized to cents under a named rounding policy rather than
# whatever a binary float's repr happens to do (round-2 review of #481,
# point 5): $0.125 is $0.13, not $0.12.
_CURRENCY_CENTS = Decimal('0.01')
# The magnitude a currency amount is allowed to have, as `Decimal.adjusted()`
# -- the exponent of the leading digit, so 24 admits everything under 10**25.
# It is checked BEFORE `int()` or `quantize()` touch a digit (round-3 review of
# #481). `f"${int(num):,}"` on Decimal('1e999999999') -- eleven characters of
# input -- materialises a billion-digit integer, and the cost grows with the
# square of the exponent: measured on the commit this fixes, 1e50000 took
# 0.043s, 1e200000 0.696s, 1e1000000 17.289s and 1e2000000 69.173s, so the
# eleven-character input above is hours of CPU. That is worse than the
# OverflowError the round-2 fix replaced -- b77d766 failed in 0.000s and loudly
# -- because a try/except cannot catch a hang and no stage-6 caller has a
# timeout. `adjusted()` reads the exponent without materialising anything --
# Decimal('1e999999999').adjusted() is 999999999 in about a microsecond -- so
# the bound costs nothing.
#
# Why 24, rather than the ~10**30 past which a dollar figure has stopped being
# an amount at all: 10**25 is already eleven orders of magnitude past world
# GDP, so a grant line reading $10,000,000,000,000,000,000,000,000 is a parse
# artefact and not an award either way -- and 10**25 is also the largest
# magnitude this function can render to the cent under the default 28-digit
# decimal context, carry included (25 integer digits + 2 cents + 1 for a
# rounding carry = exactly 28). Choosing the smaller of the two makes this one
# bound subsume the InvalidOperation that the round-2 `quantize` guard was
# added for: no magnitude reaches that except clause any more, so the two
# guards cannot disagree about what an oversized value renders as.
_CURRENCY_MAX_ADJUSTED_EXPONENT = 24


def _source_parts(pub: ResolvedPublication) -> list[str]:
    """The line saying where the work appeared, as zero or one part.

    A journal for an article (S1/S2/S6); the `In: Editors, eds. Book Title.`
    clause for a chapter (S4, #481), with the `, eds.` half written only when
    editors were extracted. Two shapes rather than one because they are
    mutually exclusive on a real record and the journal wins: an article that
    happens to carry a stray `publisher` is still an article.

    Neither, for the two shapes that have no source line at all -- an S3 book,
    whose own title already rendered above, and an S7 paper that is still in
    review and has nowhere to have appeared yet.
    """
    if pub.journal:
        return [pub.journal + "."]
    if not pub.book_title:
        return []
    if pub.editors:
        return [f"In: {pub.editors}, eds. {pub.book_title}."]
    return [f"In: {pub.book_title}."]


def _journal_trailer_parts(pub: ResolvedPublication) -> list[str]:
    """`Year;Volume(Issue):Pages.` -- the journal-article trailer, and the
    fallback for every other shape that names no publisher.

    That fallback is not incidental: a book chapter whose stage-4 record has
    no publisher rendered exactly this before #481 and still must
    (test_fallback_s4_entry_without_publisher_matches_pre_fix_trailer).
    """
    cit_parts = []
    if pub.year:
        cit_parts.append(pub.year)
    if pub.volume:
        cit_parts.append(f";{pub.volume}")
    if pub.issue:
        cit_parts.append(f"({pub.issue})")
    if pub.pages:
        cit_parts.append(f":{pub.pages}")
    if not cit_parts:
        return []
    return ["".join(cit_parts) + "."]


def _book_trailer_parts(pub: ResolvedPublication) -> list[str]:
    """`Publisher; Year:Pages.` -- the book/chapter trailer (#481).

    S3/S4 never carry volume or issue, which is why this and the journal
    trailer can be alternatives rather than having to merge: the fields the
    other one would add are never populated on a record that reaches here.
    """
    trailer = pub.publisher
    year_pages = pub.year
    if pub.pages:
        year_pages = f"{year_pages}:{pub.pages}" if year_pages else pub.pages
    if year_pages:
        trailer = f"{trailer}; {year_pages}"
    return [trailer + "."]


def _identifier_parts(pub: ResolvedPublication) -> list[str]:
    """The `doi:... PMID:... PMCID:...` run, as zero or one part.

    One owner for the punctuation (round-2 review of #481, point 12). Each id
    used to carry its own trailing period and the join then stripped the tail
    back off and re-added one, so two places decided the same character and
    the separator only worked because the strip undid it. The ids are built
    bare, the separator adds the period between them, and one period closes
    the run.
    """
    ids = []
    if pub.doi:
        ids.append(f"doi:{pub.doi}")
    if pub.pmid:
        ids.append(f"PMID:{pub.pmid}")
    if pub.pmcid:
        ids.append(f"PMCID:{pub.pmcid}")
    if not ids:
        return []
    return [". ".join(ids) + "."]


def _format_citation(entry: dict, num: int) -> tuple[str, str | None, list[str]]:
    """Format a publication entry as a numbered Vancouver-style citation.

    Orchestration only: resolve the entry, pick the trailer, join the parts.
    Every question about what the values *are* -- which of an enriched and an
    extracted value wins, whether a stage-4 field is text at all, whether
    stage 5d already wrote the whole citation, how an author list is spelled
    -- is answered by `resolve_publication` before this function sees the
    record (round-4 review of PR #737, points 1/2/3 and 8-15). That is why no
    pipeline key appears below.

    One decision is left, and it is a rendering decision: a record that names
    a publisher and no journal ends in the book trailer, everything else in
    the journal trailer. It is deliberately not two whole-citation "shape"
    renderers, because the source line and the trailer are not chosen by the
    same predicate -- a chapter with no publisher takes the book source line
    and the journal trailer -- and a shape split would have to either
    duplicate the `In:` clause or carry a branch no input can distinguish.

    The taxonomy code is not consulted: a misclassified entry should still
    render as what its own fields say it is.

    Returns:
        (citation_text, target_name, enriched_fields) tuple
    """
    pub = resolve_publication(entry)

    # Stage 5d's LLM already wrote this one, and the resolver already topped
    # it up with the editors/publisher its text omitted (#481).
    if pub.formatted_citation:
        return (f"{num}. {pub.formatted_citation}",
                pub.target_name, list(pub.enriched_fields))

    parts = []
    if pub.authors:
        parts.append(pub.authors + ".")
    if pub.title:
        parts.append(pub.title.rstrip('.') + ".")
    parts.extend(_source_parts(pub))
    if pub.publisher and not pub.journal:
        parts.extend(_book_trailer_parts(pub))
    else:
        parts.extend(_journal_trailer_parts(pub))
    parts.extend(_identifier_parts(pub))

    return f"{num}. " + " ".join(parts), pub.target_name, list(pub.enriched_fields)


def _format_currency(value) -> str:
    """Format a value as US currency ($).

    Args:
        value: Number, string with digits, or empty value

    Returns:
        Formatted currency string (e.g., "$14,876") or empty string

    Zero is an amount, not an absence: 0, 0.0 and "0" all render "$0". "" is
    returned for None, an empty or blank string, a bool, and any other type
    that is not a number or a string. `True` and `False` are named here
    because they are `int` subclasses and so would otherwise be amounts -- the
    docstring said "not a number or string at all" through round 3, which did
    not describe them (round-3 review of #481). Parsing goes through
    `decimal.Decimal`, so an amount binary float cannot hold exactly keeps its
    cents, and the fractional branch states its rounding (half-up to two
    places) instead of inheriting the repr's.

    A magnitude past `_CURRENCY_MAX_ADJUSTED_EXPONENT`, and "nan"/"inf", take
    the same fallback an unparseable string takes: the original text, rather
    than an exception raised into a caller that has no handler, or -- the
    round-3 defect that bound now closes -- an unbounded materialisation of the
    digits inside `int()`.

    Not total, and deliberately not claimed to be: an `int` at or past
    10**4301 raises ValueError out of the `str(value)` on the way in, above
    `sys.get_int_max_str_digits()`. That conversion sits outside the guarded
    region and stays there, because the fallback the guard returns *is*
    `value_str` -- catching its own construction would leave nothing to return.
    The input is unreachable through `json.loads`, which raises on the same
    limit while parsing the literal, and it raised identically before this
    change (verified against b77d766).
    """
    # `if not value` also swallowed a real $0 (round-2 review of #481, point 4).
    # The check is on type, not truthiness: a bool or a list from raw stage-4
    # JSON is not an amount and must not reach the formatter, which would print
    # its repr into the document.
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal, str)):
        return ''
    if isinstance(value, str) and not value.strip():
        return ''

    # Convert to string and extract numeric portion
    value_str = str(value).strip()

    # If already formatted with $, just return it
    if value_str.startswith('$'):
        return value_str

    # Remove any existing currency symbols, commas, and whitespace
    cleaned = re.sub(r'[$,\s]', '', value_str)

    # Parsing and formatting are guarded together, not just the constructor
    # (round-2 review of #481). `quantize` raises InvalidOperation whenever the
    # result needs more digits than the decimal context allows (28 by default),
    # so a value with 27 or more integer digits and a fraction escaped this
    # function uncaught; the sole caller (`sections/research_support.py`) has no
    # handler, so on the web path that failed the whole run. The magnitude bound
    # below now turns every such value away before `quantize` sees it, which
    # leaves this clause covering what it is really for: `Decimal(cleaned)`
    # signalling InvalidOperation on text that is not a number at all.
    try:
        # Handle cases like "14876" or "14876.00"
        num = Decimal(cleaned)
        if not num.is_finite():
            # Decimal parses "nan" and "inf" as values rather than rejecting
            # them. float parsed them too: "nan" then raised ValueError inside
            # int() and fell through to the original string, while "inf" raised
            # OverflowError, which the except clause did not catch and which
            # escaped this function. Both now return the original string.
            return value_str
        # Bound the magnitude before anything materialises the digits. This
        # cannot be an exception handler: past this point `int()` does not
        # raise, it runs -- for minutes, on eleven characters of input (see
        # `_CURRENCY_MAX_ADJUSTED_EXPONENT`). `adjusted()` is O(1) and reads
        # only the exponent, so the check is free.
        if num.adjusted() > _CURRENCY_MAX_ADJUSTED_EXPONENT:
            return value_str
        # Format with commas and $ symbol, no decimal places for whole numbers
        if num == num.to_integral_value():
            return f"${int(num):,}"
        return f"${num.quantize(_CURRENCY_CENTS, rounding=ROUND_HALF_UP):,f}"
    except (ValueError, ArithmeticError):
        # ArithmeticError rather than InvalidOperation alone: it is the parent
        # of every decimal signal (InvalidOperation, Overflow) and of
        # OverflowError, so no arithmetic path can leave this function.
        # If we can't parse or format it, return the original value
        return value_str


def _format_mentee_duration(fields: Dict) -> str:
    """Format mentee duration."""
    start = fields.get('start_date', '')
    end = fields.get('end_date', '')

    if start and end:
        return f"{start}-{end}"
    elif start:
        return f"{start}-present"
    return ''
