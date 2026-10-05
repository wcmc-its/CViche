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
this shape.

Each shape is one `HeadingCodeRule`: a heading pattern, a text pattern, the
codes that contradict them, and the code the source points to. The rules
encode the header pins E11's fix paragraph names for stage 3b (#312); they
are reported here so the defect is visible before that fix ships, and so the
fix can be measured against them afterwards. The S5 shape also needs the
sibling codes, so it is its own function; it follows the 2026-10-02 decision
on #1344 that S5 is only for an item with no evidence of journal
publication.

Reads stage 3b only: the code, the heading path stage 2 gave the entry, and
its text. One finding per shape and heading path, naming up to three entries.
"""

import re
from collections import Counter, defaultdict
from typing import NamedTuple

from ..shared import _finding

#: Severity of every finding. Measured over the 63-run EBYSBC/s7ab/pilot farm
#: (doctor/PRECISION.md): every hit hand-checked, at or above the 80% WARN bar.
SECTION_CONSISTENCY_SEVERITY = "WARN"

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
#: Researcher Profile & Bibliometric Summary (core/taxonomy_v7.json).
PROFILE_SUMMARY_CODE = "S0"


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


HEADING_CODE_RULES: tuple[HeadingCodeRule, ...] = (
    # QITQWH-02, RNKYST-04: intern, resident and fellow rows filed as
    # appointments leave the training table empty.
    HeadingCodeRule(
        "training_as_appointment", "C (training)",
        text=re.compile(
            r"^\W*(?:[\d/]{4,7}\s*(?:[-–—]|to)\s*(?:[\d/]{4,7}|present)?\s*,?\s*)?"
            r"(?:chief\s+|senior\s+|assistant\s+(?:and\s+senior\s+)?)?"
            r"(?:intern(?:ship)?|resident|residency|(?:post-?doctoral|clinical|research)\s+fellow(?:ship)?"
            r"|fellow(?:ship)?\s+in)\b", re.IGNORECASE),
        # A faculty role over a training program is an appointment.
        text_excluded=re.compile(r"director|coordinator|chair", re.IGNORECASE),
        wrong_codes=APPOINTMENT_CODES),
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
    siblings (EBYSBC E11/E30). One WARN per shape and heading path."""
    entries = _classified_entries(stage3b)
    siblings: dict[tuple[str, ...], Counter] = defaultdict(Counter)
    for entry in entries:
        siblings[entry.heading_path][entry.code] += 1

    groups: dict[tuple[str, str, tuple[str, ...]], list[_ClassifiedEntry]] = defaultdict(list)
    for entry in entries:
        for rule in HEADING_CODE_RULES:
            if _matches(rule, entry):
                groups[(rule.shape, rule.expected, entry.heading_path)].append(entry)
        if _is_misfiled_journal_article(entry, siblings[entry.heading_path]):
            groups[("journal_article_as_report", "S1/S2 (a journal article; #1344's decision)",
                    entry.heading_path)].append(entry)
    return [_finding("section_consistency", SECTION_CONSISTENCY_SEVERITY,
                     *_group_message(shape, expected, path, members))
            for (shape, expected, path), members in groups.items()]
