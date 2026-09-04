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
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Dict, List, Optional, Tuple

from ..normalization import _normalize_author_names

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
# Money is quantized to cents under a named rounding policy rather than
# whatever a binary float's repr happens to do (round-2 review of #481,
# point 5): $0.125 is $0.13, not $0.12.
_CURRENCY_CENTS = Decimal('0.01')


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


def _append_missing_stage5d_values(formatted_citation: str, fields: Dict) -> str:
    """Deterministic safety net for the stage-5d LLM path (#481).

    Stage 5d's copy-back loop -- the `for field in [...]` list of names it
    writes back onto `entry['extracted_fields']` in
    `stage_5d_citation_formatter.py` -- omits `editors` and `publisher` even
    when its own prompt extracted them, so a book/chapter citation the LLM
    formatted without one of those values has no later stage that can add it.
    Append whichever of the two `extracted_fields` actually carries and the
    LLM's own text does not already reference. A non-string value is skipped:
    stage 4 is raw LLM-shaped JSON, so `editors` can arrive as a list, and
    neither crashing the render nor writing a repr into a citation is wanted.
    """
    additions = []
    editors = fields.get('editors')
    if isinstance(editors, str) and editors and not _value_referenced(editors, formatted_citation):
        additions.append(f"{editors}, eds.")
    publisher = fields.get('publisher')
    if isinstance(publisher, str) and publisher and not _value_referenced(publisher, formatted_citation):
        additions.append(f"{publisher}.")
    if not additions:
        return formatted_citation
    return f"{formatted_citation} " + " ".join(additions)


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

    # Check if Stage 5d provided a pre-formatted citation (for non-enriched entries)
    formatted_citation = fields.get('formatted_citation', '')
    if formatted_citation and fields.get('formatting_source') == 'stage_5d_llm':
        # Use the LLM-formatted citation directly, topped up with any
        # extracted editors/publisher the LLM's own text dropped (#481).
        formatted_citation = _append_missing_stage5d_values(formatted_citation, fields)
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
    editors = fields.get('editors', '')
    publisher = fields.get('publisher', '')
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

    Zero is an amount, not an absence: 0, 0.0 and "0" all render "$0". Only
    None, an empty/blank string, and a value that is not a number or string at
    all render "". Parsing goes through `decimal.Decimal`, so an amount binary
    float cannot hold exactly keeps its cents, and the fractional branch states
    its rounding (half-up to two places) instead of inheriting the repr's.
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

    # Try to extract a number
    try:
        # Handle cases like "14876" or "14876.00"
        num = Decimal(cleaned)
    except (ValueError, InvalidOperation):
        # If we can't parse it, return the original value
        return value_str
    if not num.is_finite():
        # Decimal parses "nan"/"inf"; float did too, but int() on them raised
        # and the value fell through to the original string. Keep that.
        return value_str
    # Format with commas and $ symbol, no decimal places for whole numbers
    if num == num.to_integral_value():
        return f"${int(num):,}"
    return f"${num.quantize(_CURRENCY_CENTS, rounding=ROUND_HALF_UP):,f}"


def _format_mentee_duration(fields: Dict) -> str:
    """Format mentee duration."""
    start = fields.get('start_date', '')
    end = fields.get('end_date', '')

    if start and end:
        return f"{start}-{end}"
    elif start:
        return f"{start}-present"
    return ''
