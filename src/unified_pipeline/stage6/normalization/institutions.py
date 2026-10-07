"""Institution names and award organizations.

Two normalizations over one input domain -- the name of a place a CV entry is
attached to: the stage-5b enriched institution name for an education or
position entry, and the trailing organization segment an award name duplicates
from its own Organization cell. Neither has anything to say about how a value
is rendered.
"""
import logging
import re
import unicodedata
from collections.abc import Mapping
from typing import NamedTuple, TypedDict

logger = logging.getLogger(__name__)


class InstitutionEnrichment(TypedDict, total=False):
    """The stage 5b `institution_enrichment` fields stage 6 reads.

    `total=False` is the shape, not a convenience: stage 5b writes whichever
    of these its LLM returned, and every key is routinely absent or empty.
    A TypedDict is erased at runtime, so the `or {}` guard in
    `_get_cleaned_institution_name` stays load-bearing (#559).
    """
    cleaned_name: str
    official_name: str
    city: str | None
    state: str | None
    country: str | None


class InstitutionEnrichmentEntry(TypedDict, total=False):
    """A stage-4/5 entry insofar as `_get_cleaned_institution_name` reads it.

    Entries carry many more keys; naming only the ones this function touches
    keeps the annotation honest. The value may be present and explicitly
    None (#559).
    """
    institution_enrichment: InstitutionEnrichment | None
    extracted_fields: Mapping[str, object]


#: Words too common in institution names to show that two names are the same
#: place: "University Hospital" and "Institut Supérieur de ..." share nothing
#: once these are set aside.
_GENERIC_INSTITUTION_WORDS = frozenset({
    "university", "universidad", "universite", "universita", "college", "school",
    "institute", "institut", "instituto", "hospital", "hospitals", "center",
    "centre", "medical", "medicine", "department", "departments", "society",
    "national", "international", "american", "association", "foundation",
    "health", "system", "sciences", "science", "research", "faculty", "division",
    "program", "clinic", "clinical", "state", "city", "county", "group",
    "council", "academy", "board", "general", "memorial", "community",
    "children", "online"})
_INSTITUTION_WORD_RE = re.compile(r"[a-z]{4,}")


def _distinctive_words(name: str) -> set[str]:
    """Accent-folded words of 4+ letters, minus the generic ones. A name in a
    non-Latin script has none."""
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return set(_INSTITUTION_WORD_RE.findall(folded)) - _GENERIC_INSTITUTION_WORDS


def enrichment_names_the_institution(entry: InstitutionEnrichmentEntry) -> bool:
    """Whether stage 5b's lookup is of the institution the CV wrote.

    A `cleaned_name` is the CV's own value without its location, so it always
    is. An `official_name` alone is the LLM's identification and is believed
    only when it shares a distinctive word with the entry's `institution`:
    "International Society for ECT and Neurostimulation (ISEN), online" came
    back as a French engineering school in Lille, and the retired ROR lookup's
    names are often in another script. With no raw value there is nothing to
    contradict. Its city and country belong to the same lookup, so a caller
    that rejects the name rejects the location too.
    """
    enrichment = entry.get('institution_enrichment') or {}
    if not isinstance(enrichment, Mapping) or enrichment.get('cleaned_name'):
        return True
    official = enrichment.get('official_name') or ''
    fields = entry.get('extracted_fields')
    raw = fields.get('institution') if isinstance(fields, Mapping) else None
    if not official or not isinstance(raw, str) or not raw.strip():
        return True
    return bool(_distinctive_words(official) & _distinctive_words(raw))


#: Words that join the parts of a location without naming anything: "at
#: Dallas", "Stanford and Oakland", "The Peoples Republic of China" (#1257).
_LOCATION_FILLER_WORDS = frozenset({
    "at", "and", "of", "the", "in", "republic", "peoples", "people's"})
#: The separators between the parts of an institution string: a comma,
#: semicolon, slash or parenthesis, or a dash with space around it
#: ("UMDNJ - New Jersey Medical School"). A bare hyphen ("Yale-New Haven
#: Hospital") is part of a name.
_INSTITUTION_PART_SEPARATOR_RE = re.compile(r"\s*(?:[,;/()]|\s[-\u2013\u2014]\s)\s*")
_NAME_TOKEN_RE = re.compile(r"[^\W_]+(?:['\u2019][^\W_]+)?")


def _fold(text: str) -> str:
    """Accent-folded: "León" and "Leon" are one city."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def _is_acronym_of(token: str, name: str) -> bool:
    """Whether an all-capitals `token` abbreviates `name`: its first letter
    starts `name` and the rest occur in `name` in order. Covers a state
    ("CT", Connecticut), a country ("USA", "UK"), a city ("OKC") and the
    institution's own initials ("UCSOM"), without a table of each. Anchored
    at the start of one name, so "KHSU" is not read into a cleaned name that
    merely holds a K, an H, an S and a U."""
    letters = token.replace(".", "")
    if len(letters) < 2 or not letters.isupper():
        return False
    name = name.lower()
    if not name.startswith(letters[0].lower()):
        return False
    rest = iter(name[1:])
    return all(letter in rest for letter in letters[1:].lower())


def _names_nothing_new(text: str, names: tuple[str, ...]) -> bool:
    """Whether every word of `text` is a location filler, a postal code, or
    a word or acronym of one of `names` (the cleaned name and 5b's city,
    state and country). Text with no words names nothing."""
    known_words = {w.lower() for name in names for w in _NAME_TOKEN_RE.findall(name)}
    for token in _NAME_TOKEN_RE.findall(_fold(text)):
        lowered = token.lower()
        if (lowered in known_words or lowered in _LOCATION_FILLER_WORDS
                or any(ch.isdigit() for ch in token)
                or any(_is_acronym_of(token, name) for name in names)):
            continue
        return False
    return True


class _PlaceVocabulary(NamedTuple):
    """What a dropped part may name and still be only location (#1257)."""
    #: The cleaned name and 5b's city, state and country, accent-folded.
    known: tuple[str, ...]
    #: 5b's state and country alone: a part naming one of these makes the
    #: part before it a city ("New Haven, CT").
    region: tuple[str, ...]


#: The most words a city named before its state has ("Research Triangle Park").
_MAX_CITY_WORDS = 3


def _looks_like_city(part: str) -> bool:
    """A short run of capitalised words with no institution word in it."""
    words = part.split()
    return (0 < len(words) <= _MAX_CITY_WORDS
            and all(word[0].isupper() for word in words)
            and not {w.lower() for w in words} & _GENERIC_INSTITUTION_WORDS)


def _location_free_end(raw: str, keep_to: int, vocab: _PlaceVocabulary) -> int:
    """Where `raw` ends once its trailing location parts are cut, never
    before `keep_to`. A part that names nothing new is location; so is a
    city-shaped part just before one that named the state or country."""
    end = len(raw.rstrip())
    tails = [m for m in _INSTITUTION_PART_SEPARATOR_RE.finditer(raw, keep_to, end)
             if m.end() < end]
    after_region = False
    for sep in reversed(tails):
        part = raw[sep.end():end]
        if not part.strip():
            break
        if not (_names_nothing_new(part, vocab.known)
                or (after_region and _looks_like_city(part))):
            break
        after_region = _names_nothing_new(part, vocab.region)
        end = sep.start()
    return end


def _location_free_start(raw: str, keep_from: int, vocab: _PlaceVocabulary) -> int:
    """Where `raw` starts once its leading location parts are cut, never
    after `keep_from`."""
    start = len(raw) - len(raw.lstrip())
    for sep in _INSTITUTION_PART_SEPARATOR_RE.finditer(raw, start, keep_from):
        part = raw[start:sep.start()]
        if not part.strip() or not _names_nothing_new(part, vocab.known):
            break
        start = sep.end()
    return start


def _place_vocabulary(cleaned: str, enrichment: Mapping[str, object]) -> _PlaceVocabulary:
    """The names a dropped part may use and still name only location."""
    city, state, country = (
        _fold(str(enrichment.get(k) or "")) for k in ('city', 'state', 'country'))
    region = tuple(name for name in (state, country) if name)
    known = tuple(name for name in (_fold(cleaned), city) if name) + region
    return _PlaceVocabulary(known=known, region=region)


def _restore_dropped_qualifiers(entry: InstitutionEnrichmentEntry,
                                enrichment: Mapping[str, object],
                                cleaned: str) -> str:
    """The cleaned name, unless it dropped more than the location (#1257).

    Stage 5b's `cleaned_name` is meant to be the CV's institution without its
    location, but its LLM also drops a department ("..., Department of
    Pharmacy Practice"), a platform ("(Coursera)") or a parent institution
    ("UMDNJ - "). When the cleaned name is a substring of the raw value and
    what it dropped names something besides the location, the raw value
    renders instead, minus only its leading and trailing location parts. A
    cleaned name that is not a substring of the raw value (an expanded
    abbreviation) is kept.
    """
    fields = entry.get('extracted_fields')
    raw = fields.get('institution') if isinstance(fields, Mapping) else None
    keep_from = raw.lower().find(cleaned.lower()) if isinstance(raw, str) else -1
    if keep_from < 0:
        return cleaned
    keep_to = keep_from + len(cleaned)
    vocab = _place_vocabulary(cleaned, enrichment)
    start = _location_free_start(raw, keep_from, vocab)
    end = _location_free_end(raw, keep_to, vocab)
    if _names_nothing_new(f"{raw[start:keep_from]} {raw[keep_to:end]}", vocab.known):
        return cleaned
    return raw[start:end]


def _get_cleaned_institution_name(
        entry: InstitutionEnrichmentEntry) -> str | None:
    """The stage-5b cleaned institution name, or None to use the raw field.

    `cleaned_name` is the institution with its embedded location removed, so
    preferring it stops "Duke Medical Center, Durham, NC, Durham, NC". The
    fallback to `official_name` is not belt and braces: stage 5b routinely
    returns an empty `cleaned_name` beside a correctly populated
    `official_name`. An `official_name` that shares no distinctive word with
    the entry's own `institution` names some other place, and is not used.
    """
    enrichment = entry.get('institution_enrichment') or {}
    if not isinstance(enrichment, Mapping):
        # A non-Mapping (list, str, ...) shouldn't reach here, but stage 5b
        # is an LLM output and one malformed run is enough (#743). Treat it
        # like no enrichment rather than failing the section.
        logger.warning(
            "institution_enrichment is %s, not a mapping; treating as absent",
            type(enrichment).__name__)
        enrichment = {}
    cleaned = enrichment.get('cleaned_name', '')
    if cleaned:
        return _restore_dropped_qualifiers(entry, enrichment, cleaned)
    # Fall back to official_name, the institution without its location -- but
    # only when it names the institution the CV wrote.
    if not enrichment_names_the_institution(entry):
        return None
    official = enrichment.get('official_name', '')
    return official if official else None


# A word that cannot end an award name: what is left when the organization
# was the object of the name's own last phrase ("Elected to the
# <academy>", "Fellowship from the <foundation>"), not a tail after it.
_DANGLING_NAME_END_RE = re.compile(
    r'\b(?:of|the|at|from|by|for|in|to|and|with|on)\s*$|&\s*$',
    re.IGNORECASE)


def _strip_org_tail(name: str, org: str) -> str:
    """Remove a trailing organization segment (plus one short comma-led
    city tail, "..., Indiana University, Bloomington") from an award name
    so the org isn't duplicated across the name and Organization cells
    (#229). Conservative: only strips at end-of-string, and only a true
    tail -- when the org is the object of the name's last phrase, stripping
    it left the name ending in a dangling "of" (RCBKFG KUUKNJ 139, #1412),
    so the name is kept whole."""
    if not org:
        return name
    stripped = re.sub(
        r'[\s,]*' + re.escape(org) +
        r'(?:,\s*[A-Z][\w.-]+(?:\s+[A-Z][\w.-]+)?)?[\s,.]*$',
        '', name).strip()
    if not stripped or _DANGLING_NAME_END_RE.search(stripped):
        return name
    return stripped
