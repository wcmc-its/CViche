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
from typing import Dict, List, Optional, Tuple

from ..normalization import _normalize_author_names

def _format_citation(entry: Dict, num: int) -> Tuple[str, Optional[str], List[str]]:
    """
    Format a publication entry as Vancouver-style citation.

    Returns:
        (citation_text, target_name, enriched_fields) tuple
    """
    fields = entry.get('extracted_fields', {})
    enrichment = entry.get('enrichment_data', {})
    enriched_fields = entry.get('enriched_fields', [])  # Track which fields were enriched

    # Check if Stage 5d provided a pre-formatted citation (for non-enriched entries)
    formatted_citation = fields.get('formatted_citation', '')
    if formatted_citation and fields.get('formatting_source') == 'stage_5d_llm':
        # Use the LLM-formatted citation directly
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
        # For book chapters (S4), use "In: Book Title"
        parts.append(f"In: {book_title}.")

    # Year;Volume(Issue):Pages
    year = str(fields.get('year', ''))
    volume = enrichment.get('pubmed_volume') or fields.get('volume', '')
    issue = enrichment.get('pubmed_issue') or fields.get('issue', '')
    pages = enrichment.get('pubmed_pages') or fields.get('pages', '')

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
        ids.append(f"doi:{doi}.")
    if pmid:
        ids.append(f"PMID:{pmid}.")
    if pmcid:
        ids.append(f"PMCID:{pmcid}.")

    if ids:
        parts.append(" ".join(ids).rstrip('.') + ".")  # Ensure single final period

    citation = f"{num}. " + " ".join(parts)
    target_name = fields.get('target_name')

    return citation, target_name, enriched_fields


def _format_currency(value) -> str:
    """Format a value as US currency ($).

    Args:
        value: Number, string with digits, or empty value

    Returns:
        Formatted currency string (e.g., "$14,876") or empty string
    """
    if not value:
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
        num = float(cleaned)
        # Format with commas and $ symbol, no decimal places for whole numbers
        if num == int(num):
            return f"${int(num):,}"
        else:
            return f"${num:,.2f}"
    except ValueError:
        # If we can't parse it, return the original value
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
