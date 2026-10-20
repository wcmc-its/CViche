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

# Every four-digit year the text states.
_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
# Anything that may leave a year open: "since 2015", "from 2015", an open
# word ("2015 onwards", "2015 till date", "2015 (continuing)", "2015 to
# now", "currently"), or a dash after a year that no year follows ("2015 -",
# "2015 - Mentor for ..."). Looser than an open range on purpose: a false
# match keeps the entry N3A, as it was coded before the lone-past-year rule
# below, and only a clean miss moves it. Read only when no range is
# end-dated, so the end-dated rule above stays as it was.
_MAY_BE_OPEN = re.compile(
    r"\b(?:since|from|onwards?|now|today|currently|continu\w*|pres|active|still|in\s+progress"
    r"|(?:till?|until)\s+date)\b"
    r"|(?:19|20)\d{2}\s*[-–—](?!\s*(?:[A-Z][a-z]{2,8}\.?\s+)?\d)",
    re.I,
)

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


def _states_only_past_years(text: str, current_year: int) -> bool:
    """True when the text states a year, every year it states is before
    `current_year`, and nothing in it may leave a year open.

    A mentee written with a lone past year ("2016 Mentor ... for <name>")
    was coded N3A, because only an end-dated range read as past,
    and rendered under Current Mentees as "<year>-present" (EBYSBC autopsy,
    XELRLZ 250/258/260, #1342). A year that is not before `current_year`
    stays current: it can be this year's mentee, the same line stage 6
    draws in `_n3a_entry_has_ended`.
    """
    years = [int(year) for year in _YEAR.findall(text)]
    if not years or max(years) >= current_year:
        return False
    return not (_OPEN_PERIOD.search(text) or _MAY_BE_OPEN.search(text))


def _is_past_mentorship(text: str, current_year: int) -> bool:
    """N3B rather than N3A: an end-dated range with no open-period marker,
    or only past years and nothing that may leave one open."""
    if _END_DATED.search(text):
        return not _OPEN_PERIOD.search(text)
    return _states_only_past_years(text, current_year)


def _correct(entry: dict, current_year: int):
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

    is_past = _is_past_mentorship(text, current_year)
    return ("N3B" if is_past else "N3A"), "prose named-mentee under advising/mentoring section"


def apply_prose_mentee_corrections(entries: list[dict], current_year: int,
                                   ) -> tuple[list[dict], dict]:
    """Apply prose named-mentee corrections in place. Returns (entries, stats).

    `current_year` is the run's year: a mentee whose text states only
    earlier years is past (`_states_only_past_years`). The caller reads the
    clock, so a test or replay can pin it (CODING_STANDARDS 7.4).
    """
    corrections_made = 0
    details = []
    for entry in entries:
        new_code, reason = _correct(entry, current_year)
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


# --- bare N3 -> N3A / N3B (#1574) -------------------------------------------
# Stage 3b sometimes leaves a named trainee on the parent code N3. Stage 4 then
# extracts the generic N3 schema instead of the N3A/N3B one, and stage 6 has no
# N3 renderer (#1621 routes the leftovers there as a safety net). This step
# resolves a bare N3 that names a person to N3A or N3B. It leaves N3 alone when
# the line names nobody (a column header, a lead-in, "Not applicable"), when the
# entry has neither a mentoring heading nor an N3A/N3B entry in its run (below),
# and when any heading above it names staff: technical staff and lab managers
# are not mentees (YUYVIG IZIXVF-01), and such a list keeps N3.
#
# Current vs past comes from the entry's own text first: an open range
# ("2017-present", "2023 - ") is N3A, a closed one ("1990-1991") is N3B. With
# neither, the entry takes the code of the nearest N3A/N3B entry in its run --
# the entries next to it under the same heading, stepping over other bare N3s --
# so one list is not split across two tables (LTTYWI-06). With no such entry it
# is N3A, the default stage 6 gives a surviving bare N3 (`_route_bare_mentee`),
# and stage 6 moves an N3A whose own dates ended to Past Mentees.
# An N3A/N3B run neighbour also stands in for a mentoring heading: LTTYWI's
# trainees sit under "Teaching Responsibilities".
BARE_MENTEE_CODE = "N3"
CURRENT_MENTEE_CODE = "N3A"
PAST_MENTEE_CODE = "N3B"
_RESOLVED_MENTEE_CODES = (CURRENT_MENTEE_CODE, PAST_MENTEE_CODE)

# Anywhere in the heading path: a list of staff, not trainees.
_STAFF_HEADING = re.compile(r"\bstaff\b|technician|lab(?:oratory)?\s+manager|personnel|employee", re.I)

# The line opens with a person's name: an optional list number, a given name,
# optional middle initials, one or two surnames, and then the end of the line or
# a delimiter that starts the rest of the record. A name followed by a colon and
# nothing else is a lead-in label ("Doctoral Advising:"), not a person.
_GIVEN_NAME = r"[A-Z][a-z]+['’]?"
_SURNAME = r"(?:[A-Z][a-z]?['’])?[A-Z][a-zA-Z]+(?:-[A-Z][a-zA-Z]+)?"
_LEADING_NAME = re.compile(
    rf"^\s*(?:\d{{1,3}}[.)]\s*)?({_GIVEN_NAME}(?:\s+[A-Z]\.)*(?:\s+{_SURNAME}){{1,2}})"
    r"(?=\s*$|\s*[,;(]|\s*:\s*\S|\s+[-–—]\s)"
)
# Capitalized words that open a header row, a lead-in or an institution rather
# than a name, on top of the prose corrector's `_NOT_A_NAME`.
_NOT_A_LEADING_NAME = _NOT_A_NAME | frozenset({
    "Name", "Names", "Dates", "Project", "Projects", "Semester", "Period", "Role",
    "Position", "Current", "Past", "Former", "Present", "State", "Hospital",
    "Doctoral", "Master", "Masters", "Committees", "Advising", "Supervision",
    "Scholar", "Scholars", "Visiting", "Research", "Staff", "Not", "None", "The", "At",
    # Role nouns: "Thesis Advisor, 2015-2018" names the owner's role, not a trainee.
    "Thesis", "Advisor", "Adviser", "Mentor", "Primary", "Supervisor", "Facilitator",
    "Journal", "Club", "Director", "Chair", "Member", "Committee", "Program", "Course",
})

# An open range in the entry's own text: a year, a dash or "to", then nothing,
# or present/current/now/date/ongoing, before the end or a closing mark.
_OPEN_RANGE = re.compile(
    r"(?:19|20)\d{2}\s*(?:[-–—]|\bto\b)\s*(?:present|current|now|date|ongoing)?\s*(?:$|[).,;\]])",
    re.I,
)


def _names_a_person(text: str) -> bool:
    """True iff the line opens with a person's name (see `_LEADING_NAME`)."""
    match = _LEADING_NAME.match(text)
    if not match:
        return False
    tokens = (token.rstrip("'’.") for token in match.group(1).split())
    return not any(token in _NOT_A_LEADING_NAME for token in tokens)


def _run_neighbour_code(entries: list[dict], codes: list[str], index: int) -> str | None:
    """The N3A/N3B code of the nearest entry in the same run as entries[index]:
    walking back, then forward, under the same heading path and across only
    other bare N3 entries. `codes` are the codes before this step ran, so a
    neighbour resolved earlier in the same pass does not count. None when the
    run holds no N3A/N3B entry."""
    path = entries[index].get("hierarchy") or []
    for step in (-1, 1):
        position = index + step
        while (0 <= position < len(entries) and (entries[position].get("hierarchy") or []) == path
               and codes[position] == BARE_MENTEE_CODE):
            position += step
        if (0 <= position < len(entries) and (entries[position].get("hierarchy") or []) == path
                and codes[position] in _RESOLVED_MENTEE_CODES):
            return codes[position]
    return None


def _resolve_bare_mentee(entries: list[dict], codes: list[str], index: int) -> str | None:
    """N3A or N3B for entries[index] when it is a bare N3 that names a
    mentee (see the comment above), else None."""
    entry = entries[index]
    if codes[index] != BARE_MENTEE_CODE:
        return None
    heading = " ".join(str(h) for h in entry.get("hierarchy") or [])
    if _STAFF_HEADING.search(heading):
        return None
    neighbour = _run_neighbour_code(entries, codes, index)
    if not (_SECTION_MENTOR.search(heading) or neighbour):
        return None
    text = entry.get("text", "") or ""
    if not _names_a_person(text):
        return None
    if _OPEN_RANGE.search(text):
        return CURRENT_MENTEE_CODE
    if _END_DATED.search(text):
        return PAST_MENTEE_CODE
    return neighbour or CURRENT_MENTEE_CODE


def apply_bare_mentee_resolution(entries: list[dict]) -> tuple[list[dict], dict]:
    """Resolve each bare N3 named mentee to N3A/N3B in place. Returns
    (entries, stats); the original code is kept on the entry."""
    codes = [entry.get("taxonomy_code") for entry in entries]
    details = []
    for index, entry in enumerate(entries):
        new_code = _resolve_bare_mentee(entries, codes, index)
        if new_code is None:
            continue
        entry["taxonomy_code"] = new_code
        entry["bare_mentee_resolution"] = {"original_code": BARE_MENTEE_CODE, "resolved_to": new_code}
        existing = entry.get("classification_reasoning", "")
        entry["classification_reasoning"] = f"[bare-mentee resolved {BARE_MENTEE_CODE}->{new_code}] {existing}"
        details.append({"element_idx_start": entry.get("element_idx_start"), "resolved_to": new_code})
    return entries, {"corrections_made": len(details), "correction_details": details}
