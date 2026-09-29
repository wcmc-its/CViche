"""Retired taxonomy codes and the live code each one now files under.

Shared by stage 3b, which rewrites an LLM answer that still names a retired
code instead of rejecting it as unknown, and stage 6, which renders stage-3b
output stored before a code was retired. One list, so the two cannot drift.
"""
from types import MappingProxyType

RETIRED_TAXONOMY_CODES = MappingProxyType({
    # ponytail: M3 is a pure rename -- old code and target mean the same thing.
    'M3': 'M2D',  # Patents & Innovations -- former M3 renamed to M2D (taxonomy v7)
    # ponytail: the M4 codes are a re-route, not a rename -- a trial TYPE lands
    # on a funding STATUS. That is right only because the grant date rule runs
    # afterwards (grant_status_corrector in 3b, reclassify_past_m2a_grants in
    # stage 6) and moves an ended trial to M2B (#291, decided 2026-09-09: the
    # WCM CV has no trials section). The trial type itself is not kept; if the
    # template ever grows a trials section, route by type instead of here.
    'M4': 'M2A',
    'M4A': 'M2A',
    'M4B': 'M2A',
    'M4C': 'M2A',
})


def live_taxonomy_code(code: object) -> object:
    """`code` rewritten to its live equivalent when retired; anything else,
    including a non-string LLM value, is returned unchanged."""
    return RETIRED_TAXONOMY_CODES.get(code, code) if isinstance(code, str) else code
