"""Institution names and award organizations.

Two normalizations over one input domain -- the name of a place a CV entry is
attached to: the stage-5b enriched institution name for an education or
position entry, and the trailing organization segment an award name duplicates
from its own Organization cell. Neither has anything to say about how a value
is rendered.
"""
import logging
import re
from collections.abc import Mapping
from typing import TypedDict

logger = logging.getLogger(__name__)


class InstitutionEnrichment(TypedDict, total=False):
    """The stage 5b `institution_enrichment` fields stage 6 reads.

    `total=False` is the shape, not a convenience: stage 5b writes whichever
    of these its LLM returned, and both keys are routinely absent or empty.
    A TypedDict is erased at runtime, so the `or {}` guard in
    `_get_cleaned_institution_name` stays load-bearing (#559).
    """
    cleaned_name: str
    official_name: str


class InstitutionEnrichmentEntry(TypedDict, total=False):
    """A stage-4/5 entry insofar as `_get_cleaned_institution_name` reads it.

    Entries carry many more keys; naming only the one this function touches
    keeps the annotation honest. The value may be present and explicitly
    None (#559).
    """
    institution_enrichment: InstitutionEnrichment | None


def _get_cleaned_institution_name(
        entry: InstitutionEnrichmentEntry) -> str | None:
    """The stage-5b cleaned institution name, or None to use the raw field.

    `cleaned_name` is the institution with its embedded location removed, so
    preferring it stops "Duke Medical Center, Durham, NC, Durham, NC". The
    fallback to `official_name` is not belt and braces: stage 5b routinely
    returns an empty `cleaned_name` beside a correctly populated
    `official_name`.
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
        return cleaned
    # Fall back to official_name — always the institution without embedded location
    official = enrichment.get('official_name', '')
    return official if official else None


def _strip_org_tail(name: str, org: str) -> str:
    """Remove a trailing organization segment (plus one short comma-led
    city tail, "..., Indiana University, Bloomington") from an award name
    so the org isn't duplicated across the name and Organization cells
    (#229). Conservative: only strips at end-of-string."""
    if not org:
        return name
    stripped = re.sub(
        r'[\s,]*' + re.escape(org) +
        r'(?:,\s*[A-Z][\w.-]+(?:\s+[A-Z][\w.-]+)?)?[\s,.]*$',
        '', name).strip()
    return stripped or name
