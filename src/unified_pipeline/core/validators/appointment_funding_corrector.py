"""
Appointment Funding Corrector

Post-classification validator that keeps an entry from an appointments /
employment section out of M2 (Research Funding) unless the entry itself
shows funding evidence.

Problem (#946 item 5): "Applicant for Instructor ..., <University>", from an
         ACADEMIC APPOINTMENTS section, was classified M2C (pending funding)
         because "applicant" read as a pending application. The date-based
         grant status corrector then made it M2B, and it rendered as a
         completed grant with the university as Award Source. The source has
         no grant.

Rule: an M2/M2A/M2B/M2C entry under a heading that expects position codes
      (D) and not funding codes (M2), whose text carries no funding evidence
      (award, grant, sponsor, agency, funding, an amount, a grant number,
      a PI/investigator role), is recoded T (Appendix) so the owner reviews
      it. Headings that expect both (e.g. "Research Positions and Funding")
      are left alone.

Runs BEFORE the date-based grant status corrector, which only rewrites M2
codes: once the entry is T, that corrector cannot re-route it back into
funding. It runs AFTER the grant-to-position corrector, which gets first
chance to turn a real position into a D/O/L3 code.
"""

import re

from .event_volunteer_corrector import APPENDIX_CODE
from .grant_position_corrector import has_grant_indicators
from .grant_status_corrector import is_grant_code
from .hierarchy_mismatch_flagger import get_expected_codes_from_hierarchy


# Expected-code prefixes (hierarchy_mismatch_flagger vocabulary) that mark a
# heading as a positions section, and as a funding section.
POSITION_CODE_PREFIX = 'D'
FUNDING_CODE_PREFIX = 'M2'

# Funding evidence beyond grant_position_corrector's GRANT_PATTERNS (which
# already cover NIH-style mechanisms, dollar amounts, "Grant No.", funders,
# "PI:" and Investigator).
FUNDING_EVIDENCE = re.compile(
    r'\b(?:awards?|awarded|grants?|sponsor(?:ed|s)?|agency|fund(?:ed|s)?)\b'
    # \bPI\b already matches Co-PI, Multiple PI and Dual-PI (the hyphen or
    # space is a word boundary); only MPI needs its own prefix.
    r'|\bM?PI\b'
    r'|\bCo-?I\b'
    r'|\b(?:Project|Award|Contract)\s*(?:#|No\.?|Number)',
    re.IGNORECASE,
)


def is_positions_heading(hierarchy: list[str]) -> bool:
    """True when the heading path expects position codes and not funding codes."""
    expected = get_expected_codes_from_hierarchy(hierarchy or [])
    expects_position = any(c.startswith(POSITION_CODE_PREFIX) for c in expected)
    expects_funding = any(c.startswith(FUNDING_CODE_PREFIX) for c in expected)
    return expects_position and not expects_funding


def has_funding_evidence(text: str) -> bool:
    """True when the entry text itself names a grant, award, sponsor, amount or PI role."""
    has_grant, _ = has_grant_indicators(text)
    return has_grant or bool(FUNDING_EVIDENCE.search(text))


def correct_appointment_funding(entry: dict) -> dict:
    """Return a copy of an unevidenced M2 entry under a positions heading, recoded to T."""
    code = entry.get('taxonomy_code', '')
    if not is_grant_code(code):
        return entry
    if not is_positions_heading(entry.get('hierarchy')):
        return entry
    if has_funding_evidence(entry.get('text', '') or ''):
        return entry

    entry = entry.copy()
    entry['taxonomy_code'] = APPENDIX_CODE
    entry['original_taxonomy_code'] = code
    entry['appointment_funding_correction'] = {
        'from': code,
        'to': APPENDIX_CODE,
        'reason': (
            f"{code} under an appointments heading with no funding evidence "
            "(award/grant/sponsor/agency/amount/grant number/PI); Appendix for owner review"
        ),
    }
    return entry


def apply_appointment_funding_corrections(entries: list[dict]) -> tuple[list[dict], dict]:
    """Apply correct_appointment_funding to every entry; return (entries, stats)."""
    corrected = [correct_appointment_funding(entry) for entry in entries]
    details = [
        {
            'element_idx': entry.get('element_idx_start'),
            'text_preview': (entry.get('text') or '')[:100],
            'correction': entry['appointment_funding_correction'],
        }
        for entry in corrected
        if 'appointment_funding_correction' in entry
    ]
    stats = {'corrections_applied': len(details), 'correction_details': details}
    return corrected, stats
