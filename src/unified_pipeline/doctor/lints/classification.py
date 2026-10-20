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

Reads stage 3b only: the code, the heading path stage 2 gave the entry, and
its text. One finding per shape and heading path, naming up to three entries.

`lint_pinned_heading_empty` (#1949) is the one lint here that also reads
stage 3a: a heading 3a mapped to code X at the header-pin floor whose entries
3b coded anything but X, so X's template slot gets none of them (XAXVYB's
N1 heading, #1938).
"""

import copy
import re
from collections import Counter, defaultdict
from typing import NamedTuple

from unified_pipeline.core.retired_taxonomy_codes import live_taxonomy_code
from unified_pipeline.stage3b.context import build_mapping_index, get_taxonomy_context
from unified_pipeline.stage3b.header_pin import (
    _GRANT_CODES,
    _GRANT_MARKER,
    _PI_ROLE_LEAD,
    HEADER_PIN_MIN_CONFIDENCE,
)
from unified_pipeline.stage3b.io import _normalize_taxonomy_mappings
from unified_pipeline.stage6.parsing.text import (
    ATTENDANCE_OPENING_RE,
    heading_names_teaching_role,
    names_teaching_or_authorship_role,
)
from unified_pipeline.stage_6_word_template import RENDER_ROUTED_CODES

from ..shared import _finding

#: Severity of a finding whose shape SHAPE_SEVERITY does not name. Measured
#: over the 63-run EBYSBC/s7ab/pilot farm (doctor/PRECISION.md): every hit
#: hand-checked, at or above the 80% WARN bar.
SECTION_CONSISTENCY_SEVERITY = "WARN"

#: A shape whose severity differs from SECTION_CONSISTENCY_SEVERITY. An
#: honorific fellowship coded I follows the stage-3b prompt's own rules 16 and
#: 27 ("Fellow, [Society]" is I), so whether it is wrong waits on the taxonomy
#: decision #1577 names; until then it is reported, not weighed.
FELLOWSHIP_AS_MEMBERSHIP_SHAPE = "fellowship_as_membership"
SHAPE_SEVERITY = {FELLOWSHIP_AS_MEMBERSHIP_SHAPE: "INFO"}

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
MEMBERSHIP_CODE = "I"
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

#: A heading that names honors or awards, and one that names funding. "Grants
#: and Contract Awards", "Current Awards" and "Research Awards" head grants
#: lists, so an honors heading must not name funding too (YUYVIG, #1577).
_HONORS_HEADING_RE = re.compile(r"honou?r|\bawards?\b|\bprizes?\b|recogni[tz]|distinction",
                                re.IGNORECASE)
_FUNDING_HEADING_RE = re.compile(
    r"grant|fund|support|contract|research|investigat|current|\bpast\b|pending|completed"
    r"|ongoing|active", re.IGNORECASE)
#: A leaf heading that names memberships or credentials: a fellowship under it
#: is where the author put it ("Awards, Honors, and Membership in Honorary
#: Societies", UVZNIC; "Professional Certifications", HBALTD).
_MEMBERSHIP_HEADING_RE = re.compile(r"member|societ|organi[sz]ation|affiliation|certific|licens",
                                    re.IGNORECASE)
#: A line that opens, after an optional date or bullet, with an election to a
#: fellowship: "2017: Elected Fellow of ...", "Elected to Fellowship, ...",
#: "Named Fellow of ...", "Inducted as a Fellow in ...".
_ELECTED_FELLOW_RE = re.compile(
    r"^[\W\d]*(?:[A-Za-z]{3,9}\.?\s+)?(?:(?:19|20)\d{2}\W*)?(?:elected|named|inducted)\s+"
    r"(?:(?:as|to|an?)\s+)*(?:associate\s+|distinguished\s+|honorary\s+)?fellow(?:ship)?\b",
    re.IGNORECASE)
#: A fellowship as a title: a line that opens with "Fellow, <body>", "Fellow
#: of/in <body>" or "Distinguished Fellow ...", or that names a body and then
#: "Fellow" ("<Society> Fellow"). "Fellow" must be followed by a comma, of/in,
#: a bracket or a digit, so "Fellow Travel Award" and "Fellow - <programme>"
#: stay quiet. A fellowship programme ("Fellowship") is training or an award,
#: not this shape. "<ACRONYM> Fellow" is not matched: "NIH Fellow 1998" and
#: "AHA Fellow" name a training or funding fellowship as often as an honorific
#: one (SQMWHM 1536 "NATA Fellow" is the one corpus hit it cost).
_FELLOW_TITLE_RE = re.compile(
    r"^[\W\d]*(?:(?i:present)\W*)?"
    r"(?:(?i:distinguished|founding|honorary|associate)\s+)?(?i:fellow)\b"
    r"(?:\s*,|\s+(?i:of|in)\b|\s*\(|\s+\d)"
    r"|\b(?i:association|society|college|academy|institute)\s+(?i:fellow)\b")


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
        allowed_codes=LICENSURE_AND_CERTIFICATION_CODES),
    # MOFVYH-02: BLS/ACLS is a course certificate, not a licence or a board.
    HeadingCodeRule(
        "life_support_as_license", "B2 (non-degree education)",
        text=re.compile(r"\b(?:BLS|ACLS|PALS|BCLS|NRP|ATLS)\b"),
        wrong_codes=LICENSURE_AND_CERTIFICATION_CODES),
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


def _classified_entries(stage3b: dict) -> list[_ClassifiedEntry]:
    return [
        _ClassifiedEntry(
            idx=entry.get("element_idx_start"),
            code=str(entry.get("taxonomy_code") or ""),
            heading_path=tuple(str(h) for h in entry.get("hierarchy") or ()),
            text=str(entry.get("text") or ""),
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


def _is_honors_heading(heading: str) -> bool:
    return bool(_HONORS_HEADING_RE.search(heading)) and not _FUNDING_HEADING_RE.search(heading)


def _is_honor_as_funding(entry: _ClassifiedEntry) -> bool:
    """A grant code (M2, M2A/B/C) under a leaf heading that names honors and
    not funding, on a line with no grant marker and no PI role at its head:
    stage 6 renders it as a one-year funding table with no title and no PI
    (UVZNIC 117-126, ZDOAZO 333, IXJMKS 112). The marker and role tests are
    stage 3b's H pin's own (#1618); this reports what that pin leaves, a
    confident grant answer or a heading stage 3a did not pin to H."""
    leaf = entry.heading_path[-1] if entry.heading_path else ""
    return (entry.code in _GRANT_CODES and _is_honors_heading(leaf)
            and not _GRANT_MARKER.search(entry.text)
            and not _PI_ROLE_LEAD.match(entry.text))


def _is_fellowship_as_membership(entry: _ClassifiedEntry) -> bool:
    """An I entry that opens with an election to a fellowship, or that names
    a fellowship as a title under a heading path naming honors whose leaf
    names no membership (MVUREJ 44, IZIXVF 55, XNWSZN 53; NECRGC ZASOTV,
    LWMSPJ, UAURFU). It renders as "Fellow, <body> yyyy-Present" in
    Memberships and is missing from Honors."""
    if entry.code != MEMBERSHIP_CODE:
        return False
    if _ELECTED_FELLOW_RE.search(entry.text):
        return True
    leaf = entry.heading_path[-1] if entry.heading_path else ""
    return (any(_HONORS_HEADING_RE.search(h) for h in entry.heading_path)
            and not _MEMBERSHIP_HEADING_RE.search(leaf)
            and bool(_FELLOW_TITLE_RE.search(entry.text)))


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


def lint_section_consistency(stage3b: dict) -> list[dict]:
    """Stage-3b codes that contradict the entry's own heading, text or
    siblings (EBYSBC E11/E30). One finding per shape and heading path."""
    entries = _classified_entries(stage3b)
    siblings: dict[tuple[str, ...], Counter] = defaultdict(Counter)
    for entry in entries:
        siblings[entry.heading_path][entry.code] += 1

    groups: dict[tuple[str, str, tuple[str, ...]], list[_ClassifiedEntry]] = defaultdict(list)
    for entry, in_attendance_context in zip(entries, _attendance_contexts(entries), strict=True):
        for rule in HEADING_CODE_RULES:
            if _matches(rule, entry):
                groups[(rule.shape, rule.expected, entry.heading_path)].append(entry)
        if _is_misfiled_journal_article(entry, siblings[entry.heading_path]):
            groups[("journal_article_as_report", "S1/S2 (a journal article; #1344's decision)",
                    entry.heading_path)].append(entry)
        if _is_reviewer_journal_as_board(entry, siblings[entry.heading_path]):
            groups[("reviewer_journal_as_editorial_board", "Q4D (journal reviewer)",
                    entry.heading_path)].append(entry)
        if _is_attended_as_teacher(entry, in_attendance_context):
            groups[("attended_as_teacher", "B2 (attended, not taught or presented)",
                    entry.heading_path)].append(entry)
        if _is_honor_as_funding(entry):
            groups[("honor_as_funding", "H (an award listed under honors, no grant marker)",
                    entry.heading_path)].append(entry)
        if _is_fellowship_as_membership(entry):
            groups[(FELLOWSHIP_AS_MEMBERSHIP_SHAPE, "H (an elected or honorific fellowship)",
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
