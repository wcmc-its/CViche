"""
Event Volunteer Corrector

Post-classification validator that moves extramural event medical
volunteering out of P (Institutional Administrative Activities) and into
T (Appendix).

Problem (#946 item 4): "Medical Volunteer" rows for races, marathons,
         tournaments and cups -- medical coverage at a public event -- are
         classified P, so they render as institutional committees. P is for
         internal committees, task forces and administrative roles; the
         prompt has no destination for extramural event coverage, so the
         model picks the nearest code.

Rule (decided on #946, 2026-09-24): route such rows to the Appendix. The
      WCM template has no slot for them, and the Appendix is where the owner
      reviews what was not sorted.

Both signals are required: a medical-volunteer role AND an event word. A
"Medical Committee, Member" row names a committee, not a volunteer role, so
it is left alone; "Race around the Table, Facilitator" names no volunteer
role, so it is left alone too. A row that also names an institutional body
(committee, board, council, working group, task force, panel, advisory,
steering) is internal service even when an event word appears ("Cycling
Safety Task Force"), so it is left alone as well.
"""

import re

from .taxonomy_codes import APPENDIX_CODE


# The code this corrector reviews: P, internal committees and admin roles.
INSTITUTIONAL_ADMIN_CODE = 'P'

# A medical role held as a volunteer (not a committee seat, not a job title).
MEDICAL_VOLUNTEER_ROLE = re.compile(
    r'\bmedical\s+volunteer\b'
    r'|\bvolunteer\s+(?:physician|medical\s+staff)\b'
    r'|\bmedical\s+coverage\s+(?:for|at)\b',
    re.IGNORECASE,
)

# A public sporting or community event the volunteering covered.
EVENT_WORDS = re.compile(
    r'\b(?:marathons?|miler|triathlons?|ironman|races?|regatta|cycling'
    r'|tournaments?|cup|games|championships?|olympics)\b',
    re.IGNORECASE,
)

# An institutional body: a row naming one is internal service, not event
# volunteering. The same bodies as committee_position_corrector's
# COMMITTEE_KEYWORDS, so a row step 2 turns into P is never recoded here.
INSTITUTIONAL_BODY = re.compile(
    r'\b(?:committees?|boards?(?!\s+certified)|councils?|working\s*groups?'
    r'|task\s*forces?|panels?|advisory|steering)\b',
    re.IGNORECASE,
)


def correct_event_volunteer(entry: dict) -> dict:
    """Return a copy of a P entry recoded to T when it is event medical volunteering."""
    code = entry.get('taxonomy_code', '')
    if code != INSTITUTIONAL_ADMIN_CODE:
        return entry
    text = entry.get('text', '') or ''
    if INSTITUTIONAL_BODY.search(text):
        return entry

    role = MEDICAL_VOLUNTEER_ROLE.search(text)
    event = EVENT_WORDS.search(text)
    if not (role and event):
        return entry

    entry = entry.copy()
    entry['taxonomy_code'] = APPENDIX_CODE
    entry.setdefault('original_taxonomy_code', code)
    entry['event_volunteer_correction'] = {
        'from': code,
        'to': APPENDIX_CODE,
        'reason': (
            f"Extramural event medical volunteering ('{role.group()}' at "
            f"'{event.group()}') is not institutional service (P); Appendix for owner review"
        ),
    }
    return entry


def apply_event_volunteer_corrections(entries: list[dict]) -> tuple[list[dict], dict]:
    """Apply correct_event_volunteer to every entry; return (entries, stats)."""
    pairs = [(entry, correct_event_volunteer(entry)) for entry in entries]
    details = [
        {
            'element_idx': after.get('element_idx_start'),
            'text_preview': (after.get('text') or '')[:100],
            'correction': after['event_volunteer_correction'],
        }
        for before, after in pairs
        if after is not before
    ]
    stats = {'corrections_applied': len(details), 'correction_details': details}
    return [after for _, after in pairs], stats
