"""
Prose mentee corrector.

Companion to ``wcm_table_corrector``. That corrector fixes the WCM template's
*structured* mentee table (gated on "Mentoring Period" + "Type of Supervision").
This one fixes the *prose* case it cannot see: a free-text entry under an
ADVISING & MENTORING section that names an individual mentee but was classified
as a K-family teaching code (typically K2, "Research Mentoring & Clinical
Teaching").

The taxonomy distinction (taxonomy_v7.json) is subtle and is exactly why this
corrector is deliberately narrow:

  * K2  = the mentoring *activity / role* with no named individual
          (e.g. "Faculty coach: provided 70 hours of peer coaching to faculty").
  * N3A = a *named* mentee, relationship ongoing  ("current mentees").
  * N3B = a *named* mentee, relationship ended     ("past mentees").

So the corrector fires ONLY when an explicit named mentee is present
(e.g. "mentee Dr. Smith", "Mentor for Jane Doe"). A mentoring/coaching role with
no named individual is legitimately K2 and is left untouched -- a bare "Mentor"
role label (e.g. "Freshman Mentor and Teaching Assistant", a real adjudicated K2
in the gold set) must NOT be promoted.

Like the other validators here it runs post-classification, only overrides a
small allow-list of K-family source codes, and never touches an entry already
coded N3A/N3B. It is wired AFTER wcm_table (10b) so structured mentee rows are
already N3A/N3B by the time this runs.
"""

import re

# --- section signal ---------------------------------------------------------
# The entry must sit under an advising/mentoring section. Same keyword family
# the hierarchy_mismatch_flagger uses, so the two stay consistent.
_SECTION_MENTOR = re.compile(r"advis|mentor|mentee", re.I)

# --- named-mentee signals ---------------------------------------------------
# A capitalized person name, optionally prefixed by a title. The name token is
# matched case-sensitively (outside the (?i:...) keyword group) so a lowercase
# common noun after the keyword does not look like a name.
_NAME = r"(?:(?:Drs?|Prof|Mr|Ms|Mrs|Mr?s)\.?\s+)?[A-Z][a-z]+"

# "mentee Dr. Smith" / "advisee Jane" / "protege Lee"
_MENTEE_NAMED = re.compile(r"(?i:\b(?:mentee|advisee|protege|protégé)s?\b)[\s:]+(" + _NAME + r")")
# "Mentor for Dr. Smith" / "Coach to Jane"
_MENTOR_FOR_NAMED = re.compile(
    r"(?i:\b(?:co-?mentor|mentor|coach|advisor|adviser|preceptor)\b\s+(?:for|to)\b)\s+(" + _NAME + r")"
)
# "mentored Dr. Smith" / "precepted Jane"
_MENTORED_NAMED = re.compile(r"(?i:\b(?:mentored|precepted)\b)\s+(" + _NAME + r")")

# Capitalized tokens that are NOT a person -- guards the regexes above against
# e.g. "mentee Program" / "Mentor for Students".
_NOT_A_NAME = frozenset({
    "Program", "Programs", "Committee", "Society", "Lab", "Laboratory", "Group",
    "Network", "Award", "Awards", "Fellowship", "Initiative", "Office", "Center",
    "Centre", "Department", "Division", "School", "College", "University",
    "Institute", "Faculty", "Students", "Student", "Trainees", "Trainee",
    "Residents", "Resident", "Fellows", "Fellow", "Mentees", "Advisees",
    "Junior", "Senior", "Various", "Multiple", "Several", "Numerous", "Many",
    "Medical", "Graduate", "Undergraduate", "Postdoctoral", "Clinical",
})

# --- N3A (current) vs N3B (past) --------------------------------------------
_OPEN_PERIOD = re.compile(r"\b(present|current|ongoing|to\s+date)\b", re.I)
# A range end that is a concrete year, optionally month-prefixed: "- 2023",
# "– May 2023". Used only when there is no open-period marker.
_END_DATED = re.compile(r"[-–—]\s*(?:[A-Z][a-z]{2,8}\.?\s+)?(?:19|20)\d{2}\b")

# Only override these known-wrong source codes (the K teaching family). Never
# touch an already-plausible N3A/N3B, nor non-teaching codes.
_MENTEE_FROM = {"K1", "K2", "K3", "K4", "K5"}


def _named_mentee(text: str) -> bool:
    """True iff the text names an individual mentee (not just a mentoring role)."""
    for pat in (_MENTEE_NAMED, _MENTOR_FOR_NAMED, _MENTORED_NAMED):
        m = pat.search(text)
        if m:
            # Last word of the captured span is the bare surname/given name.
            name = m.group(1).split()[-1]
            if name not in _NOT_A_NAME:
                return True
    return False


def _correct(entry: dict):
    """Return (new_code, reason) or (None, None) if no correction applies."""
    code = entry.get("taxonomy_code", "") or ""
    if code not in _MENTEE_FROM:
        return None, None

    hierarchy = entry.get("hierarchy", []) or []
    section = " ".join(str(h) for h in hierarchy)
    if not _SECTION_MENTOR.search(section):
        return None, None

    text = entry.get("text", "") or ""
    if not _named_mentee(text):
        return None, None

    is_past = bool(_END_DATED.search(text)) and not _OPEN_PERIOD.search(text)
    return ("N3B" if is_past else "N3A"), "prose named-mentee under advising/mentoring section"


def apply_prose_mentee_corrections(entries: list[dict]) -> tuple[list[dict], dict]:
    """Apply prose named-mentee corrections in place. Returns (entries, stats)."""
    corrections_made = 0
    details = []
    for entry in entries:
        new_code, reason = _correct(entry)
        if new_code and new_code != entry.get("taxonomy_code"):
            original = entry.get("taxonomy_code")
            entry["taxonomy_code"] = new_code
            entry["taxonomy_confidence"] = 0.85
            entry["prose_mentee_correction"] = {"original_code": original, "reason": reason}
            existing = entry.get("classification_reasoning", "")
            entry["classification_reasoning"] = f"[prose-mentee corrected {original}->{new_code}] {existing}"
            corrections_made += 1
            details.append({
                "text_preview": (entry.get("text", "") or "")[:60],
                "original": original,
                "corrected_to": new_code,
                "reason": reason,
            })

    stats = {
        "entries_checked": len(entries),
        "corrections_made": corrections_made,
        "correction_details": details,
    }
    return entries, stats
