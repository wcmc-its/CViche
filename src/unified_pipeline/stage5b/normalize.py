"""Institution-name normalization and location formatting (#523).

Pure functions moved verbatim out of stage_5b_institution_enrichment.py:
no I/O, no cache access, no LLM calls.
"""

import re
from typing import Final

# US state name -> two-letter abbreviation. Module-level so it isn't rebuilt
# on every format_location() call.
US_STATE_ABBREVIATIONS: Final[dict] = {
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
    'Wisconsin': 'WI', 'Wyoming': 'WY', 'District of Columbia': 'DC',
}


def normalize_institution_name(name: str) -> str:
    """Normalize institution name for better matching."""
    if not name:
        return ""

    # Remove extra whitespace
    name = ' '.join(name.split())

    # Common abbreviation expansions
    expansions = {
        r'\bUniv\.?\s+': 'University ',
        r'\bU\.?\s+of\s+': 'University of ',
        r'\bMed\.?\s+': 'Medical ',
        r'\bCtr\.?\s+': 'Center ',
        r'\bColl\.?\s+': 'College ',
        r'\bDept\.?\s+': 'Department ',
        r'\bInst\.?\s+': 'Institute ',
        r'\bHosp\.?\s+': 'Hospital ',
        r'\bSch\.?\s+': 'School ',
    }

    for pattern, replacement in expansions.items():
        name = re.sub(pattern, replacement, name, flags=re.IGNORECASE)

    return name.strip()


def is_likely_internal_unit(name: str) -> bool:
    """
    Check if institution name is likely an internal university unit rather than
    a standalone organization.

    These should NOT be looked up as they'll get false positives.

    This is a positive-only heuristic: True means "recognized as an internal
    unit," but False means only "not recognized as one" -- it is not an
    affirmative claim that the name is a standalone organization, and should
    not be treated as authoritative for names it wasn't tuned on (government
    agencies, independent schools, etc).
    """
    name_lower = name.lower()

    # Common internal unit patterns
    internal_patterns = [
        'center for ',
        'centre for ',
        'office of ',
        'department of ',
        'division of ',
        'school of ',  # When standalone (not "X School of Medicine")
        'institute for ',
        'program in ',
        'laboratory of ',
        'lab of ',
    ]

    # Check if it starts with an internal pattern AND doesn't contain
    # a major institution indicator
    major_indicators = ['university', 'college', 'hospital', 'medical center']

    for pattern in internal_patterns:
        if name_lower.startswith(pattern):
            # Check if it also mentions a major institution
            if not any(ind in name_lower for ind in major_indicators):
                return True

    return False


def format_location(city: str, state: str, country: str, country_code: str) -> str:
    """Format city, state, country into a display location string.

    Drops the country for US locations (a display assumption for US-heavy CV
    corpora, not a canonical/full-location formatter) and abbreviates US
    state names. country_code is matched case-insensitively.
    """
    parts = []
    is_us = (country_code or '').strip().upper() == 'US'

    if city:
        parts.append(city.strip())

    if state:
        state = state.strip()
        if is_us:
            parts.append(US_STATE_ABBREVIATIONS.get(state, state))
        else:
            parts.append(state)

    # Only add country if not US (common assumption for US CVs)
    if country and not is_us:
        parts.append(country.strip())

    return ', '.join(parts)
