"""Lints for whether stage 3b's taxonomy codes agree with the source (EBYSBC E11/E30).

One responsibility: an entry's code against what its own source heading, its
own text and its sibling entries say it is. Stage 3b classifies each entry
with the heading path as context, and the EBYSBC autopsy verified 28 findings
across 21 runs (classes E11 and E30) where the code contradicts that
context: training rows filed as appointments, board certifications as
memberships, grant reviews as external committees, courses attended as
teaching, a thesis-committee block as institutional committees, a
cross-reference line as a record, and guideline articles with a journal,
volume and pages as non-peer-reviewed reports. The doctor had no lint for
this shape. The RCBKFG autopsy added one more: training rows filed as degrees
(#1415). The NECRGC autopsy added courses and meetings attended, filed as
continuing education taught or as the owner's abstracts (#1691). The YUYVIG
autopsy added two (#1577): an award under an honors heading coded as a grant,
which stage 6 renders as a one-year funding table, and an elected or honorific
fellowship coded I, which renders as a membership and is missing from Honors.
#1577 slice B added a third, `credential_misfiled`: a permit, an exam, a
course or a technologist certificate coded F1 or F2, or a licence or board
certification coded B2 or I, by `core.credential_lines`, the module stage
3b's credential pin, the WCM table corrector and stage 6's licensure guard
read too.

Each shape is one `HeadingCodeRule`: a heading pattern, a text pattern, the
codes that contradict them, and the code the source points to. The rules
encode the header pins E11's fix paragraph names for stage 3b (#312); they
are reported here so the defect is visible before that fix ships, and so the
fix can be measured against them afterwards. The S5 shape also needs the
sibling codes, so it is its own function; it follows the 2026-10-02 decision
on #1344 that S5 is only for an item with no evidence of journal
publication. So does the Q4C shape (RCBKFG JJUQDF 342-346): a bare journal
name coded as an editorial board among journals coded as reviewing, which
stage 3b reads that way when its batch holds none of the list's lead-in.

Reads stage 3b: the code, the heading path stage 2 gave the entry, and its
text. One finding per shape and heading path, naming up to three entries.
`lint_section_consistency` also reads stage 4 when the run has it, for the one
thing stage 3b cannot say: which grant records stage 6's research-support
guard lists under Honors (#1577), whose honor_as_funding shape stage 6 has
already put right, and which licence or board records stage 6's licensure
guard regroups under their credential's own code, whose credential_misfiled
and life_support_as_license shapes it has put right.

`lint_pinned_heading_empty` (#1949) is the one lint here that also reads
stage 3a: a heading 3a mapped to code X at the header-pin floor whose entries
3b coded anything but X, so X's template slot gets none of them (XAXVYB's
N1 heading, #1938).
"""

import copy
import re
from collections import Counter, defaultdict
from collections.abc import Callable
from typing import NamedTuple

from unified_pipeline.core.credential_lines import (
    BOARD_CODE,
    LICENCE_CODE,
    LIFE_SUPPORT,
    OTHER_EDUCATION_CODE,
    CredentialHome,
    is_credential_pin,
    misfiled_credential_home,
    next_in_board_block,
)
from unified_pipeline.core.honor_lines import (
    GRANT_MARKER,
    MEMBERSHIP_CODE,
    PI_ROLE_LEAD,
    heading_names_honors,
    is_honorific_fellowship,
)
from unified_pipeline.core.retired_taxonomy_codes import live_taxonomy_code
from unified_pipeline.stage3b.context import build_mapping_index, get_taxonomy_context
from unified_pipeline.stage3b.header_pin import (
    _GRANT_CODES,
    HEADER_PIN_MIN_CONFIDENCE,
)
from unified_pipeline.stage3b.io import _normalize_taxonomy_mappings
from unified_pipeline.stage6.parsing.text import (
    ATTENDANCE_OPENING_RE,
    heading_names_teaching_role,
    names_teaching_or_authorship_role,
)
from unified_pipeline.stage6.sections.licensure import credential_listed_as_licence
from unified_pipeline.stage6.sections.research_support import is_honor_listed_as_funding
from unified_pipeline.stage_6_word_template import RENDER_ROUTED_CODES

from ..shared import _finding

#: Severity of a finding whose shape SHAPE_SEVERITY does not name. Measured
#: over the 63-run EBYSBC/s7ab/pilot farm (doctor/PRECISION.md): every hit
#: hand-checked, at or above the 80% WARN bar.
SECTION_CONSISTENCY_SEVERITY = "WARN"

#: A shape whose severity differs from SECTION_CONSISTENCY_SEVERITY. An
#: honorific fellowship coded I follows the stage-3b prompt's own rules 16 and
#: 27 ("Fellow, [Society]" is I). The owner decided on 2026-10-30 (#1577) that
#: an elected fellowship, or one listed under honors, is an honor, and stage
#: 3b's fellowship pin now applies that with this lint's own test. The shape
#: stays INFO until its precision against that decision is measured on a
#: fresh batch.
FELLOWSHIP_AS_MEMBERSHIP_SHAPE = "fellowship_as_membership"
#: A credential coded as the wrong one of licence, board certification and
#: other education (#1577 slice B). Stage 3b's credential pin recodes the
#: model's answers by this shape's own test, so on a fresh run it reports
#: what the pin does not reach: a stored run, or a code a later stage-3b
#: step gave. INFO until its precision is measured on a fresh batch, as
#: fellowship_as_membership started.
CREDENTIAL_MISFILED_SHAPE = "credential_misfiled"
#: The older WARN shapes that already report part of what credential_misfiled
#: reads: an entry one of them reports is not reported twice.
CREDENTIAL_WARN_SHAPES = frozenset({"board_certification_misfiled", "life_support_as_license"})
SHAPE_SEVERITY = {FELLOWSHIP_AS_MEMBERSHIP_SHAPE: "INFO", CREDENTIAL_MISFILED_SHAPE: "INFO"}
#: The home a credential_misfiled finding points to, by code.
CREDENTIAL_HOME_EXPECTED = {
    OTHER_EDUCATION_CODE: "B2 (other education: an exam, permit, course or certificate, not a licence or board)",
    LICENCE_CODE: "F1 (licensure)",
    BOARD_CODE: "F2 (board certification)",
}

#: Entries a finding names; the doctor's evidence cap elsewhere is the same 3.
SECTION_CONSISTENCY_EVIDENCE_MAX = 3

#: Characters of an entry's text quoted in evidence.
EVIDENCE_TEXT_CHARS = 80

#: Codes for a peer-reviewed article (S1 original research, S2 reviews and
#: other peer-reviewed articles) and for a report without peer review (S5).
PEER_REVIEWED_ARTICLE_CODES = frozenset({"S1", "S2"})
NON_PEER_REVIEWED_REPORT_CODE = "S5"

#: An S5 entry is out of place among its siblings when at least this many
#: classified siblings share its heading and at least this share of them are
#: peer-reviewed articles. On the farm, every heading holding a verified E30
#: entry has 30 or more siblings at 77% or more S1/S2; a non-peer-reviewed
#: heading has a minority.
S5_MIN_SIBLINGS = 5
S5_MIN_ARTICLE_SHARE = 0.6

#: A citation's volume and pages: '14(1):82-113', '2006; 3:1250', ', 6: 869'.
_VOLUME_PAGES_RE = re.compile(
    r"\b\d{1,4}\s*\(\s*[\d\s\-–]+(?:suppl[^)]*)?\)\s*:\s*[A-Za-z]?\d+"
    r"|[;,]\s*\d{1,4}\s*(?:\([^)]*\))?\s*:\s*[A-Za-z]?\d+", re.IGNORECASE)

#: A heading path that names peer review, and one that denies it.
_PEER_REVIEWED_HEADING_RE = re.compile(r"peer|refereed", re.IGNORECASE)
_NOT_PEER_REVIEWED_HEADING_RE = re.compile(r"non[- ]?(?:peer|refereed)|not peer",
                                           re.IGNORECASE)

APPOINTMENT_CODES = frozenset({"D1", "D2", "D3"})
LICENSURE_AND_CERTIFICATION_CODES = frozenset({"F1", "F2"})
TEACHING_CODES = frozenset({"K1", "K2", "K3", "K4", "K5"})
INTERNAL_COMMITTEE_CODES = frozenset({"P", "O"})
#: What a grant review is filed as when it is not Q3: an external committee
#: (Q2), a society office (Q1), an editorial role (Q4*), an internal
#: committee (P, O) or a membership (I). An invited talk (R) filed under the
#: same heading is a segmentation slip, not this shape.
NOT_GRANT_REVIEW_CODES = frozenset({"Q1", "Q2", "Q4A", "Q4B", "Q4C", "Q4D", "P", "O", "I"})
UNCLASSIFIED_CODE = "T"
EDITORIAL_BOARD_CODE = "Q4C"
JOURNAL_REVIEWER_CODE = "Q4D"
#: Words that name the owner's part in a journal: an editorial board seat, an
#: editor, or another role. A Q4C entry with none of them, under a heading
#: that names no board, is a journal's bare name; the role came from
#: elsewhere. A panel or committee seat ("Member, <newsletter> Consultant
#: Panel") is a role of its own (EBYSBC BFSUMA 170). The heading test is
#: narrower: "Editorial Activities" heads reviewing too.
_JOURNAL_ROLE_WORD_RE = re.compile(
    r"board|editor|member|panel|consult|advis|chair|committee", re.IGNORECASE)
_BOARD_HEADING_RE = re.compile(r"board", re.IGNORECASE)
#: Researcher Profile & Bibliometric Summary (core/taxonomy_v7.json).
PROFILE_SUMMARY_CODE = "S0"

#: Codes that state the owner taught an event (K4, rendered under "Continuing
#: education ... as teacher") or wrote for it (S8, rendered as an abstract).
ATTENDED_AS_TEACHER_CODES = frozenset({"K4", "S8"})
#: Headings that list what the owner attended as often as what he taught
#: (NECRGC HVSKET, XUTTKJ, MQLKPQ, YMDTEE; #1691). Read on the leaf heading
#: only: "Research and Training" over a grants list says nothing of its rows.
#: A heading that also names teaching ("Formal Teaching of Peers (e.g., CME
#: and other continuing education courses)", "National Teaching and
#: Training") lists what the owner taught, unless it names attendance too
#: ("Continuing Education - Lectures Attended"; PR #1829 review).
_ATTENDANCE_HEADING_RE = re.compile(
    r"professional development|career development|\btraining\b|certifications?\b"
    r"|continuing education", re.IGNORECASE)
#: A paragraph entry stage 1b did not take as a heading (YMDTEE 192, "Career
#: Development____", coded T): unclassified, no year, at most this many words.
SUBHEADING_MAX_WORDS = 6
_SUBHEADING_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_WORD_RE = re.compile(r"[^\W\d_]+")

class HeadingCodeRule(NamedTuple):
    """One shape of a code that contradicts its heading or its own text.

    An entry matches when its leaf heading matches `heading` (if set), its
    text matches `text` (if set) and not `text_excluded` (if set), and its
    code is in `wrong_codes` (if set) and not in `allowed_codes` (if set)."""
    shape: str
    expected: str
    heading: re.Pattern[str] | None = None
    text: re.Pattern[str] | None = None
    text_excluded: re.Pattern[str] | None = None
    wrong_codes: frozenset[str] | None = None
    allowed_codes: frozenset[str] | None = None
    #: A test of the line that keeps it out, when a pattern cannot say it.
    line_excluded: Callable[[str], bool] | None = None
    #: Whether a record stage 6's licensure guard regroups is left out: its
    #: render is right (`credential_listed_as_licence`, #1577).
    guarded_in_stage6: bool = False


#: An entry whose text opens (after an optional date range) with a training
#: title: intern, resident, a postdoctoral/clinical/research fellow.
_TRAINING_TITLE_TEXT_RE = re.compile(
    r"^\W*(?:[\d/]{4,7}\s*(?:[-–—]|to)\s*(?:[\d/]{4,7}|present)?\s*,?\s*)?"
    r"(?:chief\s+|senior\s+|assistant\s+(?:and\s+senior\s+)?)?"
    r"(?:intern(?:ship)?|resident|residency|(?:post-?doctoral|clinical|research)\s+fellow(?:ship)?"
    r"|fellow(?:ship)?\s+in)\b", re.IGNORECASE)
#: A faculty role over a training program, which is not itself training.
_TRAINING_PROGRAM_ROLE_RE = re.compile(r"director|coordinator|chair", re.IGNORECASE)

#: Academic degrees: an entry the Education table renders as conferred.
DEGREE_CODES = frozenset({"B1"})


def _homed_in_other_education(text: str) -> bool:
    """Whether the line, read as a board certification, is an exam, a course
    or another B2 credential (`misfiled_credential_home`): "Diplomate,
    National Board of Medical Examiners" is a licensing exam, not the
    board certification board_certification_misfiled would point to."""
    home = misfiled_credential_home(text, BOARD_CODE)
    return home is not None and home.code == OTHER_EDUCATION_CODE

HEADING_CODE_RULES: tuple[HeadingCodeRule, ...] = (
    # QITQWH-02, RNKYST-04: intern, resident and fellow rows filed as
    # appointments leave the training table empty.
    HeadingCodeRule(
        "training_as_appointment", "C (training)",
        text=_TRAINING_TITLE_TEXT_RE,
        text_excluded=_TRAINING_PROGRAM_ROLE_RE,
        wrong_codes=APPOINTMENT_CODES),
    # RCBKFG GKAQHB 17/20 (#1415): the same rows filed as degrees render in
    # Academic Degrees, absent from the training table.
    HeadingCodeRule(
        "training_as_degree", "C (training)",
        text=_TRAINING_TITLE_TEXT_RE,
        text_excluded=_TRAINING_PROGRAM_ROLE_RE,
        wrong_codes=DEGREE_CODES),
    # KYOPUV-04, NDXXAD-08: an ABIM line or 'American Board of ...' filed as
    # a membership or an education row.
    HeadingCodeRule(
        "board_certification_misfiled", "F2 (board certification)",
        text=re.compile(r"^\W*(?:certifications?:\s*)?(?:\d{4}\W+)?(?:AB[A-Z]{1,4}\b|American Board of"
                        r"|National Board of|Board[- ]certified|Diplomate)"),
        # A board named as a funder, an award giver or a training program.
        text_excluded=re.compile(r"award|prize|foundation|fund|scholar|lecture|grant|residency|fellowship",
                                 re.IGNORECASE),
        allowed_codes=LICENSURE_AND_CERTIFICATION_CODES,
        # #1577 slice B: an exam or a course naming a board is no board certification.
        line_excluded=_homed_in_other_education),
    # MOFVYH-02: BLS/ACLS is a course certificate, not a licence or a board.
    # The life-support pattern is `core.credential_lines.LIFE_SUPPORT`, stage
    # 3b's credential pin's own (#1577 slice B: "Basic Cardiac Life Support"
    # and "Neonatal Resuscitation Program" spelled out count too).
    HeadingCodeRule(
        "life_support_as_license", "B2 (non-degree education)",
        text=LIFE_SUPPORT,
        wrong_codes=LICENSURE_AND_CERTIFICATION_CODES,
        guarded_in_stage6=True),
    # GJXIWD-02, HFAJCC-10: rows under a grant-review heading are Q3.
    HeadingCodeRule(
        "grant_review_not_q3", "Q3 (grant review)",
        heading=re.compile(r"grants?\s+review|review(?:er|ers|s)?\s+(?:of\s+|for\s+)?grants?\b|^grants?$",
                           re.IGNORECASE),
        text_excluded=re.compile(r"journal|editor|manuscript", re.IGNORECASE),
        wrong_codes=NOT_GRANT_REVIEW_CODES),
    # EQGGRB-05: courses the owner attended filed as CME the owner taught.
    HeadingCodeRule(
        "courses_attended_as_teaching", "B2 (non-degree education)",
        heading=re.compile(r"\battended\b|\battendance\b", re.IGNORECASE),
        wrong_codes=TEACHING_CODES),
    # HFAJCC-04: students on whose thesis committee the owner sat, filed as
    # institutional committees, render student names as committee names.
    HeadingCodeRule(
        "thesis_committee_as_committee", "N3A/N3B (mentoring)",
        heading=re.compile(r"thes[ie]s|dissertation", re.IGNORECASE),
        wrong_codes=INTERNAL_COMMITTEE_CODES),
    # KDAZOM-11, OTBUCZ-04: a line that only points elsewhere is not a record.
    HeadingCodeRule(
        "cross_reference_as_record", "T (not a record)",
        text=re.compile(r"\bsee (?:section|publication|item|#|appendix)", re.IGNORECASE),
        allowed_codes=frozenset({UNCLASSIFIED_CODE})),
    # SJWASY-03: nor is a bare URL -- unless it is coded S0, whose taxonomy
    # entry (Researcher Profile & Bibliometric Summary) lists a bare profile
    # or bibliography URL as a typical entry.
    HeadingCodeRule(
        "cross_reference_as_record", "T (not a record)",
        text=re.compile(r"^\W*(?:https?://|www\.)\S+\W*$", re.IGNORECASE),
        allowed_codes=frozenset({UNCLASSIFIED_CODE, PROFILE_SUMMARY_CODE})),
)


class _ClassifiedEntry(NamedTuple):
    """The stage-3b fields this lint reads, taken once at the boundary."""
    idx: object
    code: str
    heading_path: tuple[str, ...]
    text: str
    #: The code's reasoning: a credential_misfiled row made F2 by stage 3b's
    #: board-block rule opens no board block for the row after it.
    reasoning: str = ""


def _classified_entries(stage3b: dict) -> list[_ClassifiedEntry]:
    return [
        _ClassifiedEntry(
            idx=entry.get("element_idx_start"),
            code=str(entry.get("taxonomy_code") or ""),
            heading_path=tuple(str(h) for h in entry.get("hierarchy") or ()),
            text=str(entry.get("text") or ""),
            reasoning=str(entry.get("classification_reasoning") or ""),
        )
        for entry in stage3b.get("entries", [])
    ]


def _matches(rule: HeadingCodeRule, entry: _ClassifiedEntry) -> bool:
    leaf = entry.heading_path[-1] if entry.heading_path else ""
    if rule.heading is not None and not rule.heading.search(leaf):
        return False
    if rule.text is not None and not rule.text.search(entry.text):
        return False
    if rule.text_excluded is not None and rule.text_excluded.search(entry.text):
        return False
    if rule.wrong_codes is not None and entry.code not in rule.wrong_codes:
        return False
    if rule.line_excluded is not None and rule.line_excluded(entry.text):
        return False
    return rule.allowed_codes is None or entry.code not in rule.allowed_codes


def _is_misfiled_journal_article(entry: _ClassifiedEntry, sibling_codes: Counter) -> bool:
    """An S5 entry citing a journal volume and pages, among siblings that are
    mostly peer-reviewed articles or under a heading that names peer review
    (KYOPUV-09, and the guideline articles E30 lists)."""
    if entry.code != NON_PEER_REVIEWED_REPORT_CODE or not _VOLUME_PAGES_RE.search(entry.text):
        return False
    path = " > ".join(entry.heading_path)
    if _NOT_PEER_REVIEWED_HEADING_RE.search(path):
        return False
    classified = sum(n for code, n in sibling_codes.items() if code != UNCLASSIFIED_CODE)
    articles = sum(sibling_codes[code] for code in PEER_REVIEWED_ARTICLE_CODES)
    mostly_articles = (classified >= S5_MIN_SIBLINGS
                       and articles / classified >= S5_MIN_ARTICLE_SHARE)
    return mostly_articles or bool(_PEER_REVIEWED_HEADING_RE.search(path))


def _is_reviewer_journal_as_board(entry: _ClassifiedEntry, sibling_codes: Counter) -> bool:
    """A Q4C entry that names no role, under a heading that names no board,
    beside one or more Q4D entries under that heading."""
    leaf = entry.heading_path[-1] if entry.heading_path else ""
    return (entry.code == EDITORIAL_BOARD_CODE and sibling_codes[JOURNAL_REVIEWER_CODE] > 0
            and not _JOURNAL_ROLE_WORD_RE.search(entry.text)
            and not _BOARD_HEADING_RE.search(leaf))


def _is_subheading_paragraph(entry: _ClassifiedEntry) -> bool:
    """An unclassified entry that reads as a heading: no year, few words."""
    return (entry.code == UNCLASSIFIED_CODE and not _SUBHEADING_YEAR_RE.search(entry.text)
            and 0 < len(_WORD_RE.findall(entry.text)) <= SUBHEADING_MAX_WORDS)


def _attendance_contexts(entries: list[_ClassifiedEntry]) -> list[bool]:
    """Per entry, whether its leaf heading, with the nearest sub-heading
    paragraph before it under that path, names an attendance heading
    (`_ATTENDANCE_HEADING_RE`) and no teaching role
    (`heading_names_teaching_role`, the stage-6 refusal's carve-out too)."""
    subheading: dict[tuple[str, ...], str] = {}
    contexts = []
    for entry in entries:
        if _is_subheading_paragraph(entry):
            subheading[entry.heading_path] = entry.text
        leaf = entry.heading_path[-1] if entry.heading_path else ""
        context = f"{leaf} > {subheading.get(entry.heading_path, '')}"
        contexts.append(bool(_ATTENDANCE_HEADING_RE.search(context))
                        and not heading_names_teaching_role(context))
    return contexts


def _is_attended_as_teacher(entry: _ClassifiedEntry, in_attendance_context: bool) -> bool:
    """A K4 or S8 entry that opens as attendance, or that sits under an
    attendance heading and gives the owner no teaching or authoring part
    (`names_teaching_or_authorship_role`; NECRGC, #1691)."""
    if entry.code not in ATTENDED_AS_TEACHER_CODES:
        return False
    if ATTENDANCE_OPENING_RE.search(entry.text):
        return True
    return in_attendance_context and not names_teaching_or_authorship_role(entry.text)


def _is_honor_as_funding(entry: _ClassifiedEntry) -> bool:
    """A grant code (M2, M2A/B/C) under a leaf heading that names honors and
    not funding (`heading_names_honors`, stage 6's research-support guard's
    heading test too), on a line with no grant marker and no PI role at its
    head: stage 6 renders it as a one-year funding table with no title and
    no PI (UVZNIC 117-126, ZDOAZO 333, IXJMKS 112). The marker and role tests
    are stage 3b's H pin's own (#1618); this reports what that pin leaves, a
    confident grant answer or a heading stage 3a did not pin to H. A record
    stage 6's research-support guard lists under Honors is left out by the
    caller (`_rerouted_to_honors`)."""
    leaf = entry.heading_path[-1] if entry.heading_path else ""
    return (entry.code in _GRANT_CODES and heading_names_honors(leaf)
            and not GRANT_MARKER.search(entry.text)
            and not PI_ROLE_LEAD.match(entry.text))


def _rerouted_to_honors(stage4: dict | None) -> frozenset[object]:
    """The element_idx_start of every stage-4 record stage 6's research-support
    guard groups under H (`is_honor_listed_as_funding`, #1577): Honors prints
    it, so it is no honor_as_funding finding. Empty without stage 4."""
    if not stage4:
        return frozenset()
    return frozenset(raw.get("element_idx_start") for raw in stage4.get("entries", [])
                     if is_honor_listed_as_funding(raw, str(raw.get("taxonomy_code") or "")))


def _regrouped_by_stage6(stage4: dict | None) -> frozenset[object]:
    """The element_idx_start of every stage-4 record stage 6's licensure
    guard groups under its credential's own code
    (`credential_listed_as_licence`, #1577) or that stage 3b's credential
    pin recoded: either renders where it belongs, so it is no
    credential_misfiled or life_support_as_license finding. Empty without
    stage 4."""
    if not stage4:
        return frozenset()
    return frozenset(raw.get("element_idx_start") for raw in stage4.get("entries", [])
                     if credential_listed_as_licence(raw, str(raw.get("taxonomy_code") or ""))
                     or is_credential_pin(raw))


def _misfiled_credentials(entries: list[_ClassifiedEntry]) -> list[CredentialHome | None]:
    """Per entry, the home `misfiled_credential_home` gives it, the rows
    before it under the same heading path telling it whether it follows a
    board certification inside a board-certification block
    (`next_in_board_block`), as stage 3b's credential pin reads its group."""
    in_block: dict[tuple[str, ...], bool] = {}
    homes = []
    for entry in entries:
        homes.append(misfiled_credential_home(entry.text, entry.code, in_block.get(entry.heading_path, False)))
        in_block[entry.heading_path] = next_in_board_block(in_block.get(entry.heading_path, False), entry.code,
                                                           entry.text, entry.reasoning, entry.heading_path)
    return homes


def _is_fellowship_as_membership(entry: _ClassifiedEntry) -> bool:
    """An I entry for a fellowship that the 2026-10-30 decision on #1577
    makes an honor (`is_honorific_fellowship`, stage 3b's fellowship pin's
    own test): an elected one, or a fellowship title under an honors heading
    whose leaf names no membership (MVUREJ 44, IZIXVF 55, XNWSZN 53; NECRGC
    ZASOTV, LWMSPJ, UAURFU). It renders as "Fellow, <body> yyyy-Present" in
    Memberships and is missing from Honors. On a fresh run the pin has
    already recoded the model's answers of this shape, so this reports what
    the pin does not reach: a stored run, or an I a later stage-3b step gave."""
    return entry.code == MEMBERSHIP_CODE and is_honorific_fellowship(entry.text, entry.heading_path)


def _group_message(shape: str, expected: str, heading_path: tuple[str, ...],
                   entries: list[_ClassifiedEntry]) -> tuple[str, list[str]]:
    """(message, evidence) of one finding: a shape under one heading path."""
    codes = ", ".join(sorted({e.code for e in entries}))
    heading = " > ".join(heading_path) or "(no heading)"
    evidence = [f"entry {e.idx} ({e.code}): {e.text[:EVIDENCE_TEXT_CHARS]}"
                for e in entries[:SECTION_CONSISTENCY_EVIDENCE_MAX]]
    message = (f"{shape}: {len(entries)} entr{'y' if len(entries) == 1 else 'ies'} under "
               f"'{heading}' coded {codes}; the source points to {expected}")
    return message, evidence


def lint_section_consistency(stage3b: dict, stage4: dict | None = None) -> list[dict]:
    """Stage-3b codes that contradict the entry's own heading, text or
    siblings (EBYSBC E11/E30). One finding per shape and heading path.
    `stage4`, when the run has it, says which grant records stage 6 lists
    under Honors (`_rerouted_to_honors`) and which licence or board records
    it regroups (`_regrouped_by_stage6`)."""
    entries = _classified_entries(stage3b)
    rerouted, regrouped = _rerouted_to_honors(stage4), _regrouped_by_stage6(stage4)
    siblings: dict[tuple[str, ...], Counter] = defaultdict(Counter)
    for entry in entries:
        siblings[entry.heading_path][entry.code] += 1

    groups: dict[tuple[str, str, tuple[str, ...]], list[_ClassifiedEntry]] = defaultdict(list)
    for entry, in_attendance_context, credential_home in zip(
            entries, _attendance_contexts(entries), _misfiled_credentials(entries), strict=True):
        guarded = entry.idx in regrouped
        reported = set()
        for rule in HEADING_CODE_RULES:
            if _matches(rule, entry) and not (rule.guarded_in_stage6 and guarded):
                groups[(rule.shape, rule.expected, entry.heading_path)].append(entry)
                reported.add(rule.shape)
        if _is_misfiled_journal_article(entry, siblings[entry.heading_path]):
            groups[("journal_article_as_report", "S1/S2 (a journal article; #1344's decision)",
                    entry.heading_path)].append(entry)
        if _is_reviewer_journal_as_board(entry, siblings[entry.heading_path]):
            groups[("reviewer_journal_as_editorial_board", "Q4D (journal reviewer)",
                    entry.heading_path)].append(entry)
        if _is_attended_as_teacher(entry, in_attendance_context):
            groups[("attended_as_teacher", "B2 (attended, not taught or presented)",
                    entry.heading_path)].append(entry)
        if _is_honor_as_funding(entry) and entry.idx not in rerouted:
            groups[("honor_as_funding", "H (an award listed under honors, no grant marker)",
                    entry.heading_path)].append(entry)
        if _is_fellowship_as_membership(entry):
            groups[(FELLOWSHIP_AS_MEMBERSHIP_SHAPE, "H (an elected or honorific fellowship)",
                    entry.heading_path)].append(entry)
        if credential_home is not None and not guarded and not reported & CREDENTIAL_WARN_SHAPES:
            groups[(CREDENTIAL_MISFILED_SHAPE, CREDENTIAL_HOME_EXPECTED[credential_home.code],
                    entry.heading_path)].append(entry)
    return [_finding("section_consistency", SHAPE_SEVERITY.get(shape, SECTION_CONSISTENCY_SEVERITY),
                     *_group_message(shape, expected, path, members))
            for (shape, expected, path), members in groups.items()]


#: Severity of a confidently mapped heading none of whose entries got its code.
#: INFO, not WARN: on 418 local runs it fired 59 times on 32 runs, 14 true and
#: 7 partial (doctor/PRECISION.md, PHE-1). Most of the rest were correctly
#: coded rows stage 2 put under the wrong heading (talks under a service
#: sub-heading), a real defect but not the one this lint names (#1949).
PINNED_HEADING_EMPTY_SEVERITY = "INFO"

#: Stage 3b's heading context stops at three levels (`get_taxonomy_context`):
#: a deeper heading is judged by its third level, as stage 3b judged it.
_TAXONOMY_CONTEXT_DEPTH = 3

_NON_RECORD_ELEMENT_TYPES = frozenset({"header", "break"})


def _has_template_slot(code: str) -> bool:
    """Stage 6 routes the code, or a child of it (N3 -> N3A/N3B)."""
    return any(routed.startswith(code) for routed in RENDER_ROUTED_CODES)


def _same_family(code: str, other: str) -> bool:
    """One taxonomy letter (M2A/M2B, S1/S8, K1/K2): a grant-status or
    publication-type call within a family is the model's to make, not an
    empty section (most of the 552 hits before this rule, PHE-1)."""
    return code[:1] == other[:1]


def _leaf_heading_code(path: tuple[str, ...], mapping_index: dict) -> str | None:
    """The code stage 3a gave the entry's own (deepest, up to three) heading
    at or above HEADER_PIN_MIN_CONFIDENCE, else None. Only that level counts:
    a confident MENTORING parent says nothing about rows under an unmapped or
    differently mapped sub-heading."""
    context = get_taxonomy_context(list(path), mapping_index)
    level = (context.meta_section, context.section, context.subsection)[len(path) - 1]
    options = (level or {}).get("taxonomy_options")
    if not options:
        return None
    top = max(options, key=lambda option: option["confidence"])
    return live_taxonomy_code(top["code"]) if top["confidence"] >= HEADER_PIN_MIN_CONFIDENCE else None


def lint_pinned_heading_empty(stage3b: dict, stage3a: dict) -> list[dict]:
    """A heading stage 3a mapped to X at the header-pin floor whose records
    stage 3b coded outside X's family, while no entry anywhere in the CV is X
    or a child of X, where X has a template slot (#1949). X's slot renders
    empty and the heading's rows land elsewhere. Notes (T) are not records.
    One finding per heading."""
    mappings = copy.deepcopy(stage3a.get("mappings") or [])
    _normalize_taxonomy_mappings(mappings)
    mapping_index = build_mapping_index(mappings)

    records = [entry for raw, entry in zip(stage3b.get("entries", []), _classified_entries(stage3b),
                                           strict=True)
               if raw.get("element_type") not in _NON_RECORD_ELEMENT_TYPES
               and entry.code != UNCLASSIFIED_CODE]
    cv_codes = {live_taxonomy_code(entry.code) for entry in records}
    by_heading: dict[tuple[str, ...], list[_ClassifiedEntry]] = defaultdict(list)
    for entry in records:
        if entry.heading_path:
            by_heading[entry.heading_path[:_TAXONOMY_CONTEXT_DEPTH]].append(entry)

    findings = []
    for path, entries in by_heading.items():
        expected = _leaf_heading_code(path, mapping_index)
        if expected is None or not _has_template_slot(expected):
            continue
        if any(code.startswith(expected) for code in cv_codes):
            continue
        if any(_same_family(live_taxonomy_code(e.code), expected) for e in entries):
            continue
        message, evidence = _group_message("pinned_heading_empty", f"{expected} (stage 3a mapped "
                                           f"this heading to {expected} at >= {HEADER_PIN_MIN_CONFIDENCE}, "
                                           f"and no entry in the CV is {expected})",
                                           path, entries)
        findings.append(_finding("pinned_heading_empty", PINNED_HEADING_EMPTY_SEVERITY,
                                 message, evidence))
    return findings
