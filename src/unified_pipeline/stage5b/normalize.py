"""Institution-name normalization and location formatting (#523), and the
check of a looked-up location against the entry's own text (#1257).

Pure functions: no I/O, no cache access, no LLM calls.
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


# Standalone research institutes whose own name matches an internal-unit
# pattern ("Institute for ...") and names no parent institution (#678). An
# exact-name allowlist, not a broader rule: "Institute for Cancer Research"
# is indistinguishable in shape from these and is usually a university unit.
STANDALONE_INSTITUTES: Final[frozenset] = frozenset({
    'institute for advanced study',
    'institute for systems biology',
    'institute for health metrics and evaluation',
    'institute for defense analyses',
})


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

    # "Institute for Advanced Study, Princeton" -> match on the part before
    # the first comma so a trailing location doesn't defeat the allowlist.
    if name_lower.split(',')[0].strip() in STANDALONE_INSTITUTES:
        return False

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


# --- The looked-up location against the entry's own text (#1257) ------------
#
# The lookup sees the institution name, the code, a title and the dates
# (lookup._build_context_string), never the entry text. So when stage 4
# leaves `city` empty, the lookup's city can contradict a city the entry
# states (pilot TXTATQ D2 el 123), or be a guess for a name that identifies
# no one place ("University Hospital": ZDCXIV/FLYBMX/KJJVVO el 30) or for a
# course taken online (OIEPQD YYVITM el 637). These decide, from the entry
# alone, whether that city stands.
#
# Every alternative, word and character class below is one the corpus of
# stage-4/5b outputs needs; one it never exercised was left out (#1257).

#: `institution_enrichment.location_source` when the city came from the entry
#: text rather than the lookup, and when the lookup's location was dropped.
#: Absent means the lookup's location stands.
LOCATION_SOURCE_ENTRY_TEXT: Final[str] = 'entry_text'
LOCATION_SOURCE_WITHHELD: Final[str] = 'withheld'

_US_STATE_NAMES_BY_ABBREVIATION: Final[dict] = {
    abbreviation: name for name, abbreviation in US_STATE_ABBREVIATIONS.items()}

#: One word of a stated city: a capital, then lower case ("St." and
#: "Winston-Salem" pass; "NY" and "OKC" do not).
_CITY_WORD: Final[str] = r"[A-Z][a-z][A-Za-z\-]*\.?"

#: "<City>, <State>" standing as its own segment: after a comma, tab,
#: newline, pipe, semicolon or parenthesis, and ending the segment. Never at
#: the very start of the entry text, where "<Name>, MD" heads a CV line
#: (web223 el 2); the institution field is scanned with a newline in front,
#: so there it may. Case-sensitive: "MD" is a state, "md" is not.
_STATED_LOCATION_RE: Final[re.Pattern] = re.compile(
    r"[,\t\n|;(] *"
    r"(?P<city>" + _CITY_WORD + r"(?: " + _CITY_WORD + r"){0,2})"
    r" *, *"
    r"(?P<state>" + '|'.join([*US_STATE_ABBREVIATIONS.values(), *US_STATE_ABBREVIATIONS]) + r")"
    r"(?= *(?:$|[,\t\n|).;\d—])| +USA)")

#: A credential after the matched state: "<Name>, MD, PhD" is a person.
_CREDENTIAL_AFTER_RE: Final[re.Pattern] = re.compile(r", (?:PhD|FAC)")

#: State codes that are also degrees a name is written with. Two or more
#: words before one of them, with more of the line after, read as a person
#: ("..., Charles Bellows, MD, Surgery Department, ..." naming a mentor,
#: web068 el 115), not a city. A one-word city ("Baltimore, MD"), the
#: spelled-out state ("Silver Spring, Maryland"), and a place that ends the
#: line ("..., Silver Spring, MD") still count.
_CREDENTIAL_STATE_CODES: Final[frozenset] = frozenset({'MD', 'MA'})

#: The rest of a line that ends after the state: nothing, or a full stop.
_ENDS_THE_LINE_RE: Final[re.Pattern] = re.compile(r"\.?(?:\n|$)")

#: Words that make a "<words>, <State>" segment an institution or a unit
#: ("Boston College, MA"), not a city.
_NOT_A_CITY_WORDS: Final[frozenset] = frozenset({
    'center', 'college', 'health', 'hospital', 'inc', 'institute', 'medicine',
    'university',
})

#: Every word an institution name may contain and still name no one place:
#: "University Hospital", "School of Pharmacy", "The Children's Hospital".
_GENERIC_NAME_WORDS: Final[frozenset] = frozenset({
    'center', 'children', 'health', 'hospital', 'medical', 'of', 'pharmacy',
    'public', 's', 'school', 'the', 'university',
})

#: A delivery mode that puts the entry nowhere: a segment that is just
#: "online" or "virtual" (", online", "(online)", ", Virtual via Zoom"), or
#: an online course. Not "online coursework followed by in-residence"
#: (VECUUM el 115), which happened somewhere.
#: Read only in an entry of `ONLINE_COURSE_CODES`.
_DELIVERED_ONLINE_RE: Final[re.Pattern] = re.compile(
    r"[,(] *(?:online|virtual)(?: via \w+)? *(?=$|[\t)])|online courses?\b", re.IGNORECASE)

#: Taxonomy codes for a course or certificate the owner took, the one kind
#: of entry that can be taken online. An appointment that mentions an online
#: course ("Online course development with ...", web206 D3 el 24) still
#: happened at the institution.
ONLINE_COURSE_CODES: Final[frozenset] = frozenset({'B2'})


def _words(text: str) -> list[str]:
    """The lower-case letter runs of `text`: "Inc." -> ["inc"]."""
    return re.findall(r'[a-z]+', text.casefold())


def _place_key(text: str) -> str:
    """`text` folded so two spellings of a place compare equal, padded with a
    space each side: case and punctuation ignored, "Saint" read as "St"."""
    return f" {' '.join(_words(text))} ".replace(' saint ', ' st ')


def names_city(source: str, city: str) -> bool:
    """True when `source` contains `city` as whole words, in any spelling
    `_place_key` treats as the same."""
    wanted = _place_key(city)
    return bool(wanted.strip()) and wanted in _place_key(source)


def _is_city_shaped(candidate: str) -> bool:
    """True unless a word of `candidate` makes it an institution or unit."""
    return not any(word in _NOT_A_CITY_WORDS for word in _words(candidate))


def _reads_as_a_person(match: re.Match) -> bool:
    """True when a `_STATED_LOCATION_RE` match is shaped like "<Firstname
    Lastname>, MD" with more of the line after it."""
    return (match.group('state') in _CREDENTIAL_STATE_CODES
            and ' ' in match.group('city')
            and not _ENDS_THE_LINE_RE.match(match.string, match.end()))


def stated_us_locations(institution: str, text: str) -> list[tuple[str, str]]:
    """Each distinct "<City>, <US state>" the institution field or the entry
    text states, as (city, full state name), in order of first appearance."""
    found: dict[tuple[str, str], tuple[str, str]] = {}
    for source in (f"\n{institution}", text):
        for match in _STATED_LOCATION_RE.finditer(source):
            city, state = match.group('city'), match.group('state')
            if (not _is_city_shaped(city) or _reads_as_a_person(match)
                    or _CREDENTIAL_AFTER_RE.match(source, match.end())):
                continue
            state = _US_STATE_NAMES_BY_ABBREVIATION.get(state, state)
            found.setdefault((_place_key(city), state), (city, state))
    return list(found.values())


def is_generic_institution_name(name: str) -> bool:
    """True when `name` has words and every one is one any number of places
    share."""
    words = _words(name)
    return bool(words) and all(word in _GENERIC_NAME_WORDS for word in words)


def is_delivered_online(institution: str, text: str, taxonomy_code: str | None) -> bool:
    """True when the entry is a course (`ONLINE_COURSE_CODES`) and its
    institution field or text says it was taken online rather than at the
    institution's site."""
    return taxonomy_code in ONLINE_COURSE_CODES and any(
        _DELIVERED_ONLINE_RE.search(source) for source in (institution, text))


def _with_lookup_location_replaced(result: dict, city: str, state: str,
                                   location_source: str) -> dict:
    """A copy of `result` whose location is `city`/`state` in the US, or
    nothing when both are '', recording where it came from and what the
    lookup had said."""
    return {
        **result,
        'city': city,
        'state': state,
        'country': 'United States' if city else '',
        'country_code': 'US' if city else '',
        'location_source': location_source,
        'llm_location': format_location(
            result.get('city', ''), result.get('state', ''),
            result.get('country', ''), result.get('country_code', '')),
    }


def reconcile_location_with_source(result: dict, institution: str, text: str,
                                   taxonomy_code: str | None) -> dict:
    """The lookup `result` with its location checked against the entry (#1257).

    Unchanged when the result has no city, or the institution field or the
    entry text names it. Otherwise: the one location the entry states
    replaces the lookup's; several (a row spanning hospitals in different
    cities, none of them the lookup's) drop it, as does a generic name or an
    online course whose entry states none. Any other city -- "Brown
    University" to Providence -- is the enrichment stage 5b exists to make,
    and stands.
    """
    city = result.get('city')
    if not city or names_city(f"{institution}\n{text}", city):
        return result
    stated = stated_us_locations(institution, text)
    if len(stated) == 1:
        [(stated_city, stated_state)] = stated
        return _with_lookup_location_replaced(
            result, stated_city, stated_state, LOCATION_SOURCE_ENTRY_TEXT)
    if (stated or is_generic_institution_name(institution)
            or is_delivered_online(institution, text, taxonomy_code)):
        return _with_lookup_location_replaced(result, '', '', LOCATION_SOURCE_WITHHELD)
    return result
