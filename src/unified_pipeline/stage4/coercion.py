"""Value coercion and normalisation for stage 4 extracted fields.

Moved verbatim from `stage_4_field_extractor.py` (#498), which re-exports every
name here. Pure functions over extracted-field dicts: no LLM calls, no I/O.
"""

from typing import Dict, Any


def coerce_field_value_types(extracted_fields: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce LLM-extracted field values to the scalar types downstream stages assume.

    Stage 4 stores raw LLM JSON (the extraction call uses
    ``response_format={"type": "json_object"}`` with no schema), so a field the
    prompt asks for as a string can legitimately come back as a list of strings.
    This is common in practice -- e.g. ``narrative`` is a list in 55/65 sample
    outputs, and ``training_type``/``program_name``/``description``/``specialty``/
    ``start_date``/``end_date`` have all been observed as lists. Downstream code
    then calls ``.strip()``, ``.lower()``, ``.split()``, ``re.search()``,
    ``< 0.7`` or ``", ".join([...])`` on the value and crashes the entire run
    (e.g. "Pipeline failed -- expected str instance, list found", or a
    ``'<' not supported between instances of 'str' and 'int'`` TypeError).
    stage_6_word_template.py already patches a handful of fields ad hoc with
    ``isinstance(x, list)`` checks; this normalizes every field once, centrally,
    before any consumer sees it.

    The rule is deliberately conservative -- it only touches values that would
    otherwise crash a string/number consumer:

    - a list whose items are all scalars -> ``"; "``-joined string of the
      non-empty items (mirrors the ``"; ".join(...)`` convention already used in
      stage_6_word_template.py)
    - everything else is returned untouched, so numeric fields (``year``,
      ``volume``) stay numeric, structured fields (``locations`` and other
      list-of-dict / dict values) keep their shape, and ``None`` stays ``None``.
    """
    if not isinstance(extracted_fields, dict):
        return extracted_fields

    coerced = {}
    for key, value in extracted_fields.items():
        if isinstance(value, list) and all(
            item is None or isinstance(item, (str, int, float)) for item in value
        ):
            coerced[key] = "; ".join(
                str(item).strip() for item in value if item not in (None, "")
            )
        else:
            coerced[key] = value
    return coerced

def normalize_dates(extracted_fields: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize date fields by splitting ranges into start_date and end_date.

    Handles formats like:
    - "YYYY-YYYY" → start_date: YYYY, end_date: YYYY
    - "YYYY-present" → start_date: YYYY, end_date: "present"
    - "YYYY-MM-YYYY-MM" → start_date: YYYY-MM, end_date: YYYY-MM

    Args:
        extracted_fields: Dictionary of extracted fields

    Returns:
        Dictionary with normalized date fields (always uses start_date/end_date)
    """
    import re

    normalized = extracted_fields.copy()

    # Fields that might contain date ranges
    date_field_names = ['date', 'year', 'years', 'years_taught', 'dates', 'period']

    for field_name in date_field_names:
        if field_name not in normalized:
            continue

        value = normalized[field_name]
        if not value or not isinstance(value, str):
            continue

        value_str = str(value).strip()

        # Pattern 1: YYYY-YYYY (e.g., "2015-2016")
        match = re.match(r'^(\d{4})-(\d{4})$', value_str)
        if match:
            start_year, end_year = match.groups()
            # Always use standardized start_date/end_date field names
            normalized['start_date'] = start_year
            normalized['end_date'] = end_year
            # Remove original field to avoid duplication
            del normalized[field_name]
            continue

        # Pattern 2: YYYY-present (e.g., "2009-present", "2009-2025" where 2025 might mean ongoing)
        match = re.match(r'^(\d{4})-(present|ongoing|current)$', value_str, re.IGNORECASE)
        if match:
            start_year = match.group(1)
            normalized['start_date'] = start_year
            normalized['end_date'] = 'present'
            del normalized[field_name]
            continue

        # Pattern 3: YYYY-MM-YYYY-MM (e.g., "2015-06-2016-08")
        match = re.match(r'^(\d{4}-\d{2})-(\d{4}-\d{2})$', value_str)
        if match:
            start_date, end_date = match.groups()
            normalized['start_date'] = start_date
            normalized['end_date'] = end_date
            del normalized[field_name]
            continue

    return normalized

# ============================================================================
# Regex Post-Processing Patterns
# ============================================================================
REGEX_PATTERNS = {
    'pmid': r'PMID[:\s]*(\d{7,8})',
    'pmcid': r'PMC(\d+)',
    'doi': r'(10\.\d{4,}/[^\s\]>\),]+)',
    'orcid': r'(\d{4}-\d{4}-\d{4}-\d{3}[\dX])',
}

def normalize_authors_vancouver(authors_string: str) -> str:
    """
    Normalize author string to Vancouver citation style.

    Vancouver format: LastName AB, LastName CD, LastName EF, et al.
    - No periods in initials
    - No comma between last name and initials
    - Authors separated by commas
    - "et al." at end (with period)

    Examples:
        "Smith, John A., Jones, Mary B." → "Smith JA, Jones MB"
        "Smith J.A., Jones M.B." → "Smith JA, Jones MB"
        "Smith, J. A. and Jones, M. B." → "Smith JA, Jones MB"
        "Smith JA, Jones MB, et al.," → "Smith JA, Jones MB, et al."

    Args:
        authors_string: Original author string in any format

    Returns:
        Normalized Vancouver-style author string
    """
    import re

    if not authors_string:
        return authors_string

    result = authors_string

    # Step 1: Normalize "et al" variations at the end
    # Capture and temporarily remove et al to process authors
    et_al_match = re.search(r',?\s*(et\.?\s*al\.?)\s*[,;\.]*\s*$', result, re.IGNORECASE)
    has_et_al = bool(et_al_match)
    if has_et_al:
        result = result[:et_al_match.start()]

    # Step 2: Replace "and" / "&" with comma for consistent splitting
    result = re.sub(r'\s+and\s+', ', ', result, flags=re.IGNORECASE)
    result = re.sub(r'\s*&\s*', ', ', result)

    # Step 3: Split into individual authors
    # Split on comma, semicolon, or period followed by space and capital letter
    authors = re.split(r'[;]\s*|,\s*(?=[A-Z])', result)

    normalized_authors = []
    for author in authors:
        author = author.strip()
        if not author:
            continue

        # Try to parse "LastName, FirstName MiddleName" format
        # e.g., "Smith, John Albert" or "Smith, J. A." or "Smith, JA"
        comma_match = re.match(r'^([A-Za-z\-\']+),\s*(.+)$', author)
        if comma_match:
            last_name = comma_match.group(1)
            first_parts = comma_match.group(2)

            # Extract initials from first/middle names
            initials = extract_initials(first_parts)
            if initials:
                normalized_authors.append(f"{last_name} {initials}")
                continue

        # Try "FirstName LastName" format (less common in citations)
        # e.g., "John A. Smith" or "John Albert Smith"
        space_parts = author.split()
        if len(space_parts) >= 2:
            # Check if last part looks like a last name (not initials)
            if len(space_parts[-1]) > 2 and not re.match(r'^[A-Z]+$', space_parts[-1]):
                last_name = space_parts[-1]
                first_parts = ' '.join(space_parts[:-1])
                initials = extract_initials(first_parts)
                if initials:
                    normalized_authors.append(f"{last_name} {initials}")
                    continue

        # Already in "LastName AB" format or couldn't parse - clean up periods
        cleaned = re.sub(r'\.(?=[A-Z]|\s|$)', '', author)  # Remove periods from initials
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()  # Normalize spaces
        if cleaned:
            normalized_authors.append(cleaned)

    # Step 4: Rejoin with commas
    result = ', '.join(normalized_authors)

    # Step 5: Add back "et al." if it was present
    if has_et_al:
        result = result.rstrip(',').strip() + ', et al.'

    # Step 6: Clean up any trailing punctuation
    result = re.sub(r'[,;\.]+\s*$', '', result)
    if has_et_al:
        result += '.'  # et al. needs the period

    return result

def extract_initials(name_parts: str) -> str:
    """
    Extract initials from first/middle name string.

    Examples:
        "John Albert" → "JA"
        "J. A." → "JA"
        "JA" → "JA"
        "John A." → "JA"

    Args:
        name_parts: First and middle names as string

    Returns:
        Uppercase initials without periods or spaces
    """
    import re

    if not name_parts:
        return ""

    # If already looks like initials (all caps, 1-3 chars), just clean periods
    cleaned = re.sub(r'[\.\s]', '', name_parts)
    if re.match(r'^[A-Z]{1,4}$', cleaned):
        return cleaned

    # Extract first letter of each word/initial
    initials = []
    parts = re.split(r'[\s\.]+', name_parts)
    for part in parts:
        part = part.strip()
        if part:
            initials.append(part[0].upper())

    return ''.join(initials)

def apply_regex_post_processing(
    original_text: str,
    extracted_fields: Dict[str, Any],
    taxonomy_code: str
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Apply regex patterns to catch identifiers missed by LLM extraction.

    Also tracks reformatted values for transparency.

    Args:
        original_text: Original CV entry text
        extracted_fields: Fields extracted by LLM
        taxonomy_code: Entry taxonomy code (e.g., S1, M2A)

    Returns:
        Tuple of (updated_fields, reformatted_fields)
        - updated_fields: Extracted fields with regex-caught values added
        - reformatted_fields: Dict tracking original -> reformatted values
    """
    import re

    updated = extracted_fields.copy()
    reformatted = {}

    # Only apply identifier extraction to relevant taxonomy codes
    if taxonomy_code.startswith('S') or taxonomy_code in ('R', 'N4'):

        # Extract PMID if not already present
        if not updated.get('pmid'):
            pmid_match = re.search(REGEX_PATTERNS['pmid'], original_text, re.IGNORECASE)
            if pmid_match:
                original_pmid_text = pmid_match.group(0)  # Full match like "PMID: 12345678"
                pmid_value = pmid_match.group(1)  # Just the number
                updated['pmid'] = pmid_value
                reformatted['pmid'] = {
                    'original': original_pmid_text,
                    'reformatted': pmid_value,
                    'reason': 'Extracted PMID number via regex'
                }

        # Extract PMCID if not already present
        if not updated.get('pmcid'):
            pmcid_match = re.search(REGEX_PATTERNS['pmcid'], original_text, re.IGNORECASE)
            if pmcid_match:
                pmcid_value = f"PMC{pmcid_match.group(1)}"
                updated['pmcid'] = pmcid_value
                reformatted['pmcid'] = {
                    'original': pmcid_match.group(0),
                    'reformatted': pmcid_value,
                    'reason': 'Extracted PMCID via regex'
                }

        # Extract/normalize DOI if not already present
        if not updated.get('doi'):
            doi_match = re.search(REGEX_PATTERNS['doi'], original_text)
            if doi_match:
                doi_value = doi_match.group(1)
                # Clean up trailing punctuation that might have been captured
                doi_value = re.sub(r'[\.,;:]+$', '', doi_value)
                updated['doi'] = doi_value
                reformatted['doi'] = {
                    'original': doi_match.group(0),
                    'reformatted': doi_value,
                    'reason': 'Extracted DOI via regex'
                }
        elif updated.get('doi'):
            # Normalize existing DOI (remove doi.org prefix, etc.)
            existing_doi = updated['doi']
            normalized_doi = re.sub(r'^https?://(dx\.)?doi\.org/', '', existing_doi)
            normalized_doi = re.sub(r'^doi:', '', normalized_doi, flags=re.IGNORECASE)
            normalized_doi = re.sub(r'[\.,;:]+$', '', normalized_doi)
            if normalized_doi != existing_doi:
                updated['doi'] = normalized_doi
                reformatted['doi'] = {
                    'original': existing_doi,
                    'reformatted': normalized_doi,
                    'reason': 'Normalized DOI format'
                }

    # Extract ORCID for profile sections
    if taxonomy_code in ('A', 'S0'):
        if not updated.get('orcid'):
            orcid_match = re.search(REGEX_PATTERNS['orcid'], original_text)
            if orcid_match:
                updated['orcid'] = orcid_match.group(1)
                reformatted['orcid'] = {
                    'original': orcid_match.group(0),
                    'reformatted': orcid_match.group(1),
                    'reason': 'Extracted ORCID via regex'
                }

    # Normalize author formatting to Vancouver style
    # Vancouver: LastName AB, LastName CD (no periods in initials, no comma before initials)
    if updated.get('authors'):
        original_authors = updated['authors']
        normalized_authors = normalize_authors_vancouver(original_authors)
        if normalized_authors != original_authors:
            updated['authors'] = normalized_authors
            reformatted['authors'] = {
                'original': original_authors,
                'reformatted': normalized_authors,
                'reason': 'Normalized to Vancouver author format'
            }

    # Clean title (remove leading labels like "Featured:", "Submitted:")
    if updated.get('title'):
        original_title = updated['title']
        # Remove common prefixes
        cleaned_title = re.sub(r'^(Featured|Submitted|In Review|In Preparation|Accepted)[:\s]+', '',
                               original_title, flags=re.IGNORECASE)
        # Remove stray punctuation artifacts
        cleaned_title = re.sub(r'^[:\?\s]+', '', cleaned_title)
        cleaned_title = re.sub(r'[:\?\s]+$', '', cleaned_title)
        if cleaned_title != original_title:
            updated['title'] = cleaned_title
            reformatted['title'] = {
                'original': original_title,
                'reformatted': cleaned_title,
                'reason': 'Removed prefix label from title'
            }

    # Extract percent effort/FTE for grant entries
    if taxonomy_code.startswith('M2'):
        if not updated.get('percent_effort'):
            # Match various FTE formats: .08FTE, 0.08 FTE, 8%, 8 %, .08 FTE, 8% effort, etc.
            fte_patterns = [
                r'\.(\d{1,2})\s*FTE',          # .08FTE, .08 FTE
                r'0\.(\d{1,2})\s*FTE',         # 0.08FTE, 0.08 FTE
                r'(\d{1,3})\s*%\s*(?:effort|FTE)?',  # 8%, 8 %, 8% effort
                r'(\d{1,3})\s*percent',        # 8 percent
            ]
            for pattern in fte_patterns:
                fte_match = re.search(pattern, original_text, re.IGNORECASE)
                if fte_match:
                    fte_value = fte_match.group(1)
                    # Normalize to percentage format
                    if '.' not in pattern:  # Already a percentage
                        percent_effort = f"{fte_value}%"
                    else:  # Decimal FTE format, convert to percentage
                        percent_effort = f"{int(fte_value)}%"
                    updated['percent_effort'] = percent_effort
                    reformatted['percent_effort'] = {
                        'original': fte_match.group(0),
                        'reformatted': percent_effort,
                        'reason': 'Extracted percent effort via regex'
                    }
                    break

    return updated, reformatted
