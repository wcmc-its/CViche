"""Rendering a value as the string that appears on the page (#398).

The sibling of `docx.py`, split from it on what they operate on: `docx.py` takes
a python-docx object and mutates its appearance; this takes a value and returns
the text. Neither knows what a CV is.

The line against `normalization` is also deliberate. Normalization decides what a
value *is* -- which author-name spelling, which institution string. This decides
how it *reads*: 14876 becomes "$14,876", a publication record becomes a numbered
Vancouver citation. A change to one should not require a change to the other.
"""
import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, List, Optional, Tuple

from ..normalization import _append_missing_stage5d_values, _normalize_author_names

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


def _format_citation(entry: Dict, num: int) -> Tuple[str, Optional[str], List[str]]:
    """
    Format a publication entry as Vancouver-style citation.

    Returns:
        (citation_text, target_name, enriched_fields) tuple
    """
    fields = entry.get('extracted_fields') or {}
    # `or`, not a dict/list default (#659): a default only applies when the key
    # is absent, so an entry carrying the key with an explicit None hands the
    # None straight back and the first `.get`/iteration on it raises. Exactly
    # the `extracted_fields` defect above, on the two adjacent lines (found
    # reviewing that fix). Stage 5 writes a dict today
    # (`stage_5_pubmed_enrichment.py:655`) and no entry in the 61-CV local farm
    # carries an explicit null here, so this is hardening, not an observed
    # crash -- the entries reaching stage 6 are raw LLM-shaped JSON with no
    # schema between them and this line.
    enrichment = entry.get('enrichment_data') or {}
    enriched_fields = entry.get('enriched_fields') or []  # Track which fields were enriched

    # Read once, for both branches below. Stage 4 is raw LLM-shaped JSON, so
    # either value can arrive as a list, and both the stage-5d safety net and
    # the deterministic "In: ..., eds." clause render it straight into the
    # citation -- a repr in the document rather than a crash, which is worse
    # (round-2 review of #481, point 13).
    editors = fields.get('editors', '')
    publisher = fields.get('publisher', '')
    if not isinstance(editors, str):
        editors = ''
    if not isinstance(publisher, str):
        publisher = ''

    # Check if Stage 5d provided a pre-formatted citation (for non-enriched entries)
    formatted_citation = fields.get('formatted_citation', '')
    if formatted_citation and fields.get('formatting_source') == 'stage_5d_llm':
        # Use the LLM-formatted citation directly, topped up with any
        # extracted editors/publisher the LLM's own text dropped (#481).
        formatted_citation = _append_missing_stage5d_values(
            formatted_citation, editors, publisher)
        citation = f"{num}. {formatted_citation}"
        target_name = fields.get('target_name')
        return citation, target_name, enriched_fields

    parts = []

    # Authors - prefer enriched PubMed authors, fall back to extracted
    authors = enrichment.get('pubmed_authors') or fields.get('authors', '')
    if authors:
        # Clean and normalize author names
        authors = _normalize_author_names(authors)
        parts.append(authors + ".")

    # Title - prefer enriched PubMed title, fall back to extracted
    title = enrichment.get('pubmed_title') or fields.get('title', '')
    if title:
        title = title.rstrip('.')
        parts.append(title + ".")

    # Journal or Book title - prefer enriched
    journal = enrichment.get('pubmed_journal') or fields.get('journal', '')
    book_title = fields.get('book_title', '')
    if journal:
        parts.append(journal + ".")
    elif book_title:
        # For book chapters (S4), use "In: Editors, eds. Book Title." (#481)
        if editors:
            parts.append(f"In: {editors}, eds. {book_title}.")
        else:
            parts.append(f"In: {book_title}.")

    # Year;Volume(Issue):Pages -- or, for a book/chapter (S3/S4) with a
    # publisher and no journal, "Publisher; Year:Pages." (#481). S3/S4 never
    # carry volume/issue, so the two trailer shapes don't collide.
    year = str(fields.get('year', ''))
    volume = enrichment.get('pubmed_volume') or fields.get('volume', '')
    issue = enrichment.get('pubmed_issue') or fields.get('issue', '')
    pages = enrichment.get('pubmed_pages') or fields.get('pages', '')

    if publisher and not journal:
        trailer = publisher
        year_pages = year
        if pages:
            year_pages = f"{year_pages}:{pages}" if year_pages else pages
        if year_pages:
            trailer = f"{trailer}; {year_pages}"
        parts.append(trailer + ".")
    else:
        cit_parts = []
        if year:
            cit_parts.append(year)
        if volume:
            cit_parts.append(f";{volume}")
        if issue:
            cit_parts.append(f"({issue})")
        if pages:
            cit_parts.append(f":{pages}")

        if cit_parts:
            parts.append("".join(cit_parts) + ".")

    # Identifiers - separated by periods
    ids = []
    doi = fields.get('doi', '')
    pmid = fields.get('pmid', '')
    pmcid = fields.get('pmcid', '')

    if doi:
        ids.append(f"doi:{doi}")
    if pmid:
        ids.append(f"PMID:{pmid}")
    if pmcid:
        ids.append(f"PMCID:{pmcid}")

    if ids:
        # One owner for the punctuation (round-2 review of #481, point 12).
        # Each id used to carry its own trailing period and the join then
        # stripped the tail back off and re-added one, so two places decided
        # the same character and the separator only worked because the strip
        # undid it. The ids are built bare, the separator adds the period
        # between them, and one period closes the run.
        parts.append(". ".join(ids) + ".")

    citation = f"{num}. " + " ".join(parts)
    target_name = fields.get('target_name')

    return citation, target_name, enriched_fields


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
