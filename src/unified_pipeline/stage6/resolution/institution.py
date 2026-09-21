"""Deciding which institution and location a record actually refers to (#398).

A source CV names the same employer four ways and often omits the location
entirely, so a record arrives incomplete or ambiguous and something has to
decide what it should be. `_get_institution_location` consults stage 5b
enrichment and a known-institution table; `_recover_institution_from_nearby_entries`
consults the record's neighbours.

Neither renders anything. They return the value a section writer should use --
plus, in the first case, whether it came from enrichment, because an enriched
value is rendered as a tracked change and an extracted one is not.
"""
import re


def _location_stated_in_source(entry: dict, *candidates: str) -> bool:
    """True when the source CV already states one of `candidates` as its own
    segment (delimited by a comma, newline, pipe or the string ends) inside
    the raw institution field or the entry text.

    Stage 5b returns a city/state for "The College of New Jersey, Ewing, NJ"
    by parsing it OUT of the source, and `_get_institution_location` reported
    that as an enrichment, so the renderers wrote ", Ewing, NJ" back as a
    tracked insertion -- the faculty saw their own text flagged as a pipeline
    addition (#897 item 3). A location the source states is not a change.
    Segment-anchored so "Tokyo" is not found inside "University of Tokyo".
    """
    fields = entry.get('extracted_fields') or {}
    source = f"{fields.get('institution') or ''}\n{entry.get('text') or ''}"
    for candidate in candidates:
        if candidate and re.search(
                r'(?:^|[,\n|])\s*' + re.escape(candidate) + r'\s*(?:$|[,\n|])',
                source, re.IGNORECASE):
            return True
    return False
def _location_already_in_institution(location: str, institution: str) -> bool:
    """True when the institution string already carries the location as a
    comma-led tail: "Massachusetts General Hospital, Boston, MA" or
    "Columbia University, New York" against "Boston, MA" / "New York, NY".

    Matched as a TAIL (", City" then optionally ", State" then end of string),
    not as the bare city word anywhere in the name. The word match dropped the
    location from every institution whose name contains its city -- New York
    University, New York Presbyterian, Boston Children's -- which is most WCM
    faculty CVs (#897). The three renderers that append a location all consult
    this one predicate, so the rule cannot drift between them.
    """
    if not (location and institution):
        return False
    city = location.split(',')[0].strip()
    if not city:
        return False
    tail = r',\s*' + re.escape(city) + r'(?:\s*,\s*[A-Za-z][A-Za-z .]*)?\s*$'
    return bool(re.search(tail, institution, re.IGNORECASE))


def _get_institution_location(entry: dict) -> tuple[str, bool]:
    """Get formatted location string from institution enrichment data.

    Uses institution_enrichment from Stage 5b if available, otherwise falls back
    to extracted_fields.location.

    IMPORTANT: For known institutions (from config.yaml), we use the default location
    instead of enrichment when:
    1. The institution name contains a known institution (substring match)
    2. The original text doesn't have an explicit location different from the default

    This handles cases like "Weill Cornell Medical College, Doha, Qatar" where
    enrichment returns "Doha, Qatar" but we want "New York, NY" for the main campus.

    Args:
        entry: Entry dict with potential institution_enrichment

    Returns:
        Tuple of (location_string, is_from_enrichment)
        - location_string: Formatted location (e.g., "Columbus, OH") or empty string
        - is_from_enrichment: True if location came from enrichment (needs track change)
    """
    # Known institutions with default locations (should match config.yaml)
    known_institutions = {
        'weill cornell': 'New York, NY',
        'new york presbyterian': 'New York, NY',
        'newyork-presbyterian': 'New York, NY',
        'nyp': 'New York, NY',
        'memorial sloan': 'New York, NY',
        'hospital for special surgery': 'New York, NY',
    }

    # Check if this is a known institution that should use default location
    fields = entry.get('extracted_fields') or {}
    institution_name = (fields.get('institution', '') or '').lower()
    original_text = (entry.get('text', '') or '').lower()

    # Check for known institution match (substring)
    default_location = None
    for known_inst, default_loc in known_institutions.items():
        if known_inst in institution_name or known_inst in original_text:
            default_location = default_loc
            break

    # If it's a known institution, check if original text has a different explicit location
    # (like "Doha, Qatar" or "Valhalla, NY") - if so, we should NOT override
    if default_location:
        # Check if original text contains a non-default location
        non_default_locations = ['doha', 'qatar', 'valhalla', 'ithaca', 'london', 'houston']
        has_explicit_non_default = any(loc in original_text for loc in non_default_locations)

        if not has_explicit_non_default:
            # Use default location for known institution
            return (default_location, False)  # False = not from enrichment (no track change needed)

    # Check for institution enrichment data (from Stage 5b)
    enrichment = entry.get('institution_enrichment') or {}
    if enrichment:
        city = enrichment.get('city', '')
        state = enrichment.get('state', '')
        country_code = enrichment.get('country_code', '')

        if city and state:
            # For US, use state abbreviation
            if country_code == 'US':
                state_abbrevs = {
                    'Alabama': 'AL', 'Alaska': 'AK', 'Arizona': 'AZ', 'Arkansas': 'AR',
                    'California': 'CA', 'Colorado': 'CO', 'Connecticut': 'CT', 'Delaware': 'DE',
                    'Florida': 'FL', 'Georgia': 'GA', 'Hawaii': 'HI', 'Idaho': 'ID',
                    'Illinois': 'IL', 'Indiana': 'IN', 'Iowa': 'IA', 'Kansas': 'KS',
                    'Kentucky': 'KY', 'Louisiana': 'LA', 'Maine': 'ME', 'Maryland': 'MD',
                    'Massachusetts': 'MA', 'Michigan': 'MI', 'Minnesota': 'MN', 'Mississippi': 'MS',
                    'Missouri': 'MO', 'Montana': 'MT', 'Nebraska': 'NE', 'Nevada': 'NV',
                    'New Hampshire': 'NH', 'New Jersey': 'NJ', 'New Mexico': 'NM', 'New York': 'NY',
                    'North Carolina': 'NC', 'North Dakota': 'ND', 'Ohio': 'OH', 'Oklahoma': 'OK',
                    'Oregon': 'OR', 'Pennsylvania': 'PA', 'Rhode Island': 'RI', 'South Carolina': 'SC',
                    'South Dakota': 'SD', 'Tennessee': 'TN', 'Texas': 'TX', 'Utah': 'UT',
                    'Vermont': 'VT', 'Virginia': 'VA', 'Washington': 'WA', 'West Virginia': 'WV',
                    'Wisconsin': 'WI', 'Wyoming': 'WY', 'District of Columbia': 'DC'
                }
                state_abbrev = state_abbrevs.get(state, state)
                location = f"{city}, {state_abbrev}"
                # "Ewing, New Jersey" in the source is the same statement as
                # the rendered "Ewing, NJ"; a non-US "Crewe, Cheshire" is not
                # the same as the rendered "Crewe, United Kingdom", so only
                # the US branch gets the spelt-out form as a second candidate.
                as_stated = (location, f"{city}, {state}")
            else:
                # For non-US, include country
                country = enrichment.get('country', '')
                location = f"{city}, {country}" if country else f"{city}, {state}"
                as_stated = (location,)
        elif city:
            location, as_stated = city, (city,)
        else:
            location, as_stated = '', ()
        if location:
            # An enrichment only if the source did not already say it --
            # stage 5b parses an embedded location out rather than adding one.
            return (location, not _location_stated_in_source(entry, *as_stated))

    # Fall back to extracted_fields.location (not from enrichment)
    fields = entry.get('extracted_fields') or {}
    return (fields.get('location', ''), False)  # False = not from enrichment


def _recover_institution_from_nearby_entries(entry: dict, all_entries: list[dict]) -> str:
    """Recover institution name from nearby entries in the original CV.

    When a training entry (like Graduate Research Assistant) is missing institution,
    look at subsequent entries by element_idx that might contain the institution name.
    Common patterns: "University of X", "Department of X", institution names.
    """
    entry_end_idx = entry.get('element_idx_end', entry.get('element_idx_start', -1))
    # Ensure entry_end_idx is an integer (may be string from JSON)
    try:
        entry_end_idx = int(entry_end_idx)
    except (ValueError, TypeError):
        entry_end_idx = -1
    if entry_end_idx < 0:
        return ''

    # University/institution patterns
    institution_patterns = [
        'university of', 'college of', 'institute of', 'school of',
        'department of', 'center for', 'laboratory', 'hospital',
        ' – department', ' - department'
    ]

    # Look at entries within the next 5 element indices
    for other_entry in all_entries:
        other_start = other_entry.get('element_idx_start', -1)
        # Ensure other_start is an integer (may be string from JSON)
        try:
            other_start = int(other_start)
        except (ValueError, TypeError):
            continue

        # Check if this entry is immediately after our target (within 5 elements)
        if other_start > entry_end_idx and other_start <= entry_end_idx + 5:
            other_text = other_entry.get('text', '').strip()
            other_text_lower = other_text.lower()

            # Check if this looks like an institution
            for pattern in institution_patterns:
                if pattern in other_text_lower:
                    # Return the institution text (clean it up)
                    return other_text

    return ''
