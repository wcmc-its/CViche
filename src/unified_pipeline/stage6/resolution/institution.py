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
import logging
import re
from collections.abc import Mapping

from ..normalization.institutions import enrichment_names_the_institution

logger = logging.getLogger(__name__)

_US_STATE_ABBREVS = {
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
    faculty CVs (#897). Every renderer that appends a location consults this
    one predicate (B2 through `_location_remainder`), so the rule cannot drift
    between them.
    """
    if not (location and institution):
        return False
    parts = [p.strip() for p in location.split(',')]
    city = parts[0]
    if not city:
        return False
    tail = r',\s*' + re.escape(city) + r'(?:\s*,\s*([A-Za-z][A-Za-z .]*))?\s*$'
    found = re.search(tail, institution, re.IGNORECASE)
    if not found:
        return False
    # Keep the location only when BOTH sides name a US state and the states
    # differ: "Rochester, MN" is not already present in "Mayo Clinic,
    # Rochester, NY" (#566). Every other pairing -- a country against a
    # province, "USA" or "England" against a state, "Tex.", a ZIP -- cannot be
    # judged different from a token alone and keeps the city-only match, so a
    # place written two ways is not printed twice. Residual: "Cambridge, MA"
    # against "Cambridge, UK" is still treated as already present.
    stated, wanted = _us_state(found.group(1) or ''), _us_state(parts[1] if len(parts) > 1 else '')
    return not (stated and wanted and stated != wanted)


def _location_remainder(location: str, institution: str | None) -> str:
    """The part of `location` an institution cell still lacks: '' when the
    institution already carries all of it, the whole location when it
    carries none of it.

    Two ways the institution already says it. As a comma-led tail, judged by
    `_location_already_in_institution`. Or by BEING the place: a conference
    row whose source names only "Riverton, OR" gets that string as its stage-4
    institution and the same string as its location, so the cell read
    "Riverton, OR, Riverton, OR" (#1257); stage 5b cleans "Riverton, OR" to
    "Riverton" and the cell read "Riverton, Riverton, OR". When the
    institution's comma parts are the location's leading parts, only the
    parts after them are still missing. A part matches case-insensitively,
    or as the same US state written two ways ("Oregon" against "OR").
    """
    if not (location and institution):
        return location
    if _location_already_in_institution(location, institution):
        return ''
    held = [p.strip() for p in institution.split(',')]
    wanted = [p.strip() for p in location.split(',')]
    if len(held) > len(wanted) or not all(
            _same_place_part(h, w) for h, w in zip(held, wanted)):
        return location
    return ', '.join(wanted[len(held):])


def _same_place_part(held: str, wanted: str) -> bool:
    """True when two comma parts of a place name say the same thing."""
    if held.casefold() == wanted.casefold():
        return True
    state = _us_state(held)
    return bool(state) and state == _us_state(wanted)


def _us_state(token: str) -> str:
    """The two-letter abbreviation `token` names, or '' when it names no US
    state. Case, dots and spaces are ignored: "N. Y.", "new york" and "NY"
    all give "NY"."""
    squashed = re.sub(r'[\s.]', '', token).lower()
    for name, abbrev in _US_STATE_ABBREVS.items():
        if squashed in (abbrev.lower(), re.sub(r'\s', '', name).lower()):
            return abbrev
    return ''


_US_COUNTRY_CODE = 'US'


def _names_a_foreign_country(enrichment: Mapping) -> bool:
    """True when stage 5b names a country and its code says it is not the
    US. Both are required: a country name with no code cannot be told from
    "United States" spelt some other way, and a US location drops its
    country by design (#698)."""
    country, code = enrichment.get('country'), enrichment.get('country_code')
    return (isinstance(country, str) and bool(country.strip())
            and isinstance(code, str) and bool(code.strip())
            and code.strip().upper() != _US_COUNTRY_CODE)


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
    if not isinstance(enrichment, Mapping):
        # A non-Mapping (list, str, ...) shouldn't reach here, but stage 5b
        # is an LLM output and one malformed run is enough (#743). Treat it
        # like no enrichment rather than failing the section.
        logger.warning(
            "institution_enrichment is %s, not a mapping; treating as absent",
            type(enrichment).__name__)
        enrichment = {}
    names_it = enrichment_names_the_institution(entry)
    if not names_it:
        enrichment = {}  # a lookup of some other place: its city is not this one's
    if enrichment:
        city = enrichment.get('city', '')
        state = enrichment.get('state', '')
        country_code = enrichment.get('country_code', '')

        if city and state:
            # For US, use state abbreviation
            if country_code == 'US':
                state_abbrev = _US_STATE_ABBREVS.get(state, state)
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
        elif city and _names_a_foreign_country(enrichment):
            # No state, but a non-US country: "Cambridge, United Kingdom",
            # not "Cambridge" alone, which the reader takes for a US town
            # (#698; EBYSBC GJXIWD-05 lost the country on three rows).
            location = f"{city}, {enrichment['country'].strip()}"
            as_stated = (location,)
        elif city:
            location, as_stated = city, (city,)
        else:
            location, as_stated = '', ()
        if location:
            # An enrichment only if the source did not already say it --
            # stage 5b parses an embedded location out rather than adding one.
            return (location, not _location_stated_in_source(entry, *as_stated))

    # Fall back to extracted_fields.location (not from enrichment) -- unless
    # stage 5b wrote it there from the lookup just rejected (`enriched_fields`).
    fields = entry.get('extracted_fields') or {}
    if not names_it and 'location' in (entry.get('enriched_fields') or ()):
        return ('', False)
    return (fields.get('location', ''), False)  # False = not from enrichment


# The Postdoctoral Training codes (postdoc_training.POSTDOC_TRAINING_CODES,
# which cannot be imported here: sections import this package). A test pins
# the two together.
_TRAINING_CODES = ('C', 'C1', 'C2', 'C3')


def _recover_institution_from_nearby_entries(entry: dict, all_entries: list[dict]) -> str:
    """The institution a training block names once above its lines (#1038).

    A training entry whose own institution is empty takes the extracted
    `institution` FIELD of the nearest PRECEDING training entry (C/C1/C2/C3)
    in the same block (same `hierarchy`). Never raw entry text, never an
    entry of another code -- the old forward scan returned the whole text of
    an education, teaching or personal-data entry, degree and years included.
    Returns '' when nothing qualifies: an empty cell is correct, a stranger's
    institution is not.
    """
    try:
        entry_start = int(entry.get('element_idx_start', -1))
    except (ValueError, TypeError):
        return ''
    if entry_start < 0:
        return ''

    best_start, best = -1, ''
    for other in all_entries:
        if other.get('taxonomy_code') not in _TRAINING_CODES:
            continue
        if other.get('hierarchy') != entry.get('hierarchy'):
            continue
        try:
            other_start = int(other.get('element_idx_start', -1))
        except (ValueError, TypeError):
            continue
        institution = ((other.get('extracted_fields') or {}).get('institution') or '')
        if not isinstance(institution, str) or not institution.strip():
            continue
        if best_start < other_start < entry_start:
            best_start, best = other_start, institution.strip()
    return best
