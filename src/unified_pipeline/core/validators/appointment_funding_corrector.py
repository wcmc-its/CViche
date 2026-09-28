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
      (D) and not funding codes (M2), whose text carries no funding evidence,
      is recoded T (Appendix) so the owner reviews it. Headings that expect
      both (e.g. "Research Positions and Funding") are left alone.

Funding evidence is one funding-shaped signal (an amount, an NIH mechanism,
a funder acronym, a grant/award/project/contract number, a PI or Co-I
role, direct/indirect costs), or two DISTINCT funding words (award, grant,
sponsor, agency, fund, investigator). One funding word alone is ordinary
appointment prose ("funded by departmental start-up", "Sponsored by
Dr. Lee", "Grants and Contracts Office") and does not count.

Runs BEFORE the date-based grant status corrector, which only rewrites M2
codes: once the entry is T, that corrector cannot re-route it back into
funding. It runs AFTER the grant-to-position corrector, which gets first
chance to turn a real position into a D/O/L3 code.
"""

import re

from .grant_status_corrector import is_grant_code
from .hierarchy_mismatch_flagger import (
    get_expected_codes_from_hierarchy,
    is_funding_code,
    is_position_code,
)
from .taxonomy_codes import APPENDIX_CODE


# One of these alone is funding evidence. Acronyms and roles are matched
# case-sensitively ((?-i:...)): lower-case "pi" or "coi" is not a role.
FUNDING_SHAPED = re.compile(
    r'\$\s?[\d,]+'
    r'|(?-i:\b[RPUKFT]\d{2}\b)'
    r'|(?-i:\b(?:NIH|NSF|NCI|NHLBI|NINDS|NIMH|NIA|NIDDK|NIAID|AHRQ|PCORI|HRSA|CDC|DOD)\b)'
    r'|\b(?:grant|award|project|contract)s?\s*(?:#|No\b\.?|Number\b)'
    # \bPI\b also matches Co-PI, Multiple PI and Dual-PI.
    r'|(?-i:\bM?PI\b|\bCo-?I\b)'
    r'|\b(?:principal|co-?)\s*investigator\b'
    r'|\b(?:in)?direct\s+costs?\b',
    re.IGNORECASE,
)

# Funding words that are ordinary appointment prose on their own. Two
# distinct ones together are funding evidence.
FUNDING_WORDS = tuple(re.compile(p, re.IGNORECASE) for p in (
    r'\bawards?\b|\bawarded\b',
    r'\bgrants?\b',
    r'\bsponsor(?:ed|s|ship)?\b',
    r'\bagency\b',
    r'\bfund(?:ed|s|ing)?\b',
    r'\binvestigator\b',
))
MIN_DISTINCT_FUNDING_WORDS = 2


def is_positions_heading(hierarchy: list[str] | None) -> bool:
    """True when the heading path expects position codes and not funding codes."""
    expected = get_expected_codes_from_hierarchy(hierarchy or [])
    return any(map(is_position_code, expected)) and not any(map(is_funding_code, expected))


def has_funding_evidence(text: str) -> bool:
    """True for one funding-shaped signal, or two distinct funding words."""
    if FUNDING_SHAPED.search(text):
        return True
    return sum(bool(word.search(text)) for word in FUNDING_WORDS) >= MIN_DISTINCT_FUNDING_WORDS


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
    entry.setdefault('original_taxonomy_code', code)
    entry['appointment_funding_correction'] = {
        'from': code,
        'to': APPENDIX_CODE,
        'reason': (
            f"{code} under an appointments heading with no funding evidence "
            "(amount, funder, grant number, PI role, or two funding words); Appendix for owner review"
        ),
    }
    return entry


def apply_appointment_funding_corrections(entries: list[dict]) -> tuple[list[dict], dict]:
    """Apply correct_appointment_funding to every entry; return (entries, stats)."""
    pairs = [(entry, correct_appointment_funding(entry)) for entry in entries]
    details = [
        {
            'element_idx': after.get('element_idx_start'),
            'text_preview': (after.get('text') or '')[:100],
            'correction': after['appointment_funding_correction'],
        }
        for before, after in pairs
        if after is not before
    ]
    stats = {'corrections_applied': len(details), 'correction_details': details}
    return [after for _, after in pairs], stats
