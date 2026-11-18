"""Where a duplicate candidate sits in the source CV, and when two same-text
entries are two records the CV itself lists twice (#1435, #2154).

Moved out of `stage3b/classify.py` unchanged, because two stages ask the
question: stage 3b's `detect_duplicates` before it marks the second copy
`is_duplicate`, and stage 6's render-time dedup (`stage6/dedup.py`) before
it drops one. One definition (CODING_STANDARDS §1.5), in `core/` because it
is shared by two stages (§2); stage 6 must not import `stage3b/classify.py`,
which holds the stage's LLM call sites.

Pure: entries in, booleans out. Imports nothing from the pipeline.
"""

import math
import re
from typing import NamedTuple

# The unclassified (Appendix) fallback code, and the prefix of every code
# under it (see docs/CODING_STANDARDS.md §8.2).
APPENDIX_TAXONOMY_PREFIX = "T"
# The mentee codes (N3, N3A, N3B).
MENTEE_CODE_PREFIX = "N3"


def duplicate_key(text: str) -> str:
    """The comparison key two entries must share EXACTLY to be duplicates.

    Lowercased, with every non-word character removed -- punctuation AND
    whitespace. So a case, punctuation, or spacing difference (including a
    token split or joined: "2013 - present" vs "2013present") still matches,
    but any change to a letter or digit does not.
    """
    return re.sub(r"[^\w]", "", text.lower())


# A line that names only a place or a form label, not a record: a CV repeats
# these under several sections as sub-headings or template rows, so a second
# copy is noise, not a second record (#1435 narrowing: CXRYCF "University of
# Vermont", UJNBIT "Medical College of Wisconsin (1987-2004)", ZASOTV
# "Project Title:\tRole:", RLADNC "Title Institution, city and state Dates
# (mm/yy - mm/yy)").
_PLACE_WORD_RE = re.compile(
    r"\b(?:university|college|school|hospital|health|department|institute|center|centre)\b",
    re.IGNORECASE)
_PLACE_LINE_MAX_WORDS = 8
# A comma-joined line is a place line only when every part is one: a role or
# title before the place ("Research Fellow, Karolinska Institute") is a
# record, a nested place ("University of Florida College of Medicine,
# Department of Anesthesiology", ULHCAP/WPDLWC) is not. "Journal" marks a
# venue that names a college ("Journal of the American College of Surgeons",
# FVQANK's own target row).
_JOURNAL_RE = re.compile(r"\bjournal\b", re.IGNORECASE)
_FORM_LABEL_RE = re.compile(r"\w:(?:\s|$)")
_FORM_LABEL_MIN_LABELS = 2
_TEMPLATE_PLACEHOLDER_RE = re.compile(r"\b(?:mm/yy|mm/dd|yyyy)\b", re.IGNORECASE)
_PARENTHESISED_RE = re.compile(r"\([^)]*\)?")


def _is_place_or_label_line(text: str) -> bool:
    """True when `text` is a bare institution name (every comma-separated
    part short, naming a place word, and not a journal), a line ending in a
    colon, a row of two or more "Label:" cells, or a template placeholder."""
    stripped = text.strip()
    if stripped.endswith(":") or _TEMPLATE_PLACEHOLDER_RE.search(stripped):
        return True
    if len(_FORM_LABEL_RE.findall(stripped)) >= _FORM_LABEL_MIN_LABELS:
        return True
    if _JOURNAL_RE.search(stripped):
        return False
    parts = [part.strip() for part in _PARENTHESISED_RE.sub(" ", stripped).split(",")]
    return all(_is_place_part(part) for part in parts)


def _is_place_part(part: str) -> bool:
    return len(part.split()) <= _PLACE_LINE_MAX_WORDS and bool(_PLACE_WORD_RE.search(part))


#: A place in the source document: one number per dotted part of an element
#: index, so a table row ("602.8", row 8 of element 602) sorts inside its
#: table and row 10 does not read as row 1 the way float("602.10") would.
_ElementPosition = tuple[float, ...]

#: Closes a span after every row of its last element: (602, inf) is past
#: (602, 8), so a whole table overlaps each of its own rows (#2154).
_SPAN_END = (math.inf,)

#: A hierarchy this deep has a sub-heading under a section heading (#2154).
_SUB_HEADING_DEPTH = 2


class SourcePlacement(NamedTuple):
    """Where a duplicate candidate came from: its taxonomy code, the span of
    source positions it was cut from (None when the entry has none), whether
    its text reads as a record rather than a place or label line, whether
    it was cut from whole elements (integer indices, not table rows), and
    the headings it sits under."""

    code: str
    span: tuple[_ElementPosition, _ElementPosition] | None
    record_shaped: bool
    whole_elements: bool
    hierarchy: tuple[str, ...]


def _element_position(idx: object) -> _ElementPosition | None:
    """An element index as a sortable position, or None when it is not one.
    An int is a whole element; a table row arrives as "602.8" (a float in
    some older artifacts), a pseudo-row as "92.5.10"."""
    if isinstance(idx, float) and idx.is_integer():
        idx = int(idx)
    parts = str(idx).split(".")
    return tuple(int(part) for part in parts) if all(part.isdecimal() for part in parts) else None


def source_placement(entry: dict) -> SourcePlacement:
    start, end = entry.get("element_idx_start"), entry.get("element_idx_end")
    first, last = _element_position(start), _element_position(end)
    has_span = first is not None and last is not None
    return SourcePlacement(entry.get("taxonomy_code") or "", (first, last + _SPAN_END) if has_span else None,
                            not _is_place_or_label_line(entry.get("text") or ""),
                            isinstance(start, int) and isinstance(end, int),
                            tuple(str(level) for level in entry.get("hierarchy") or []))


def _in_two_mentee_rosters(first: SourcePlacement, second: SourcePlacement) -> bool:
    """True when two same-code entries are mentee rows under two different
    sub-headings of one section (#2154). A roster row records a relationship
    and its sub-heading names which one: in one corpus CV a student sat on
    the thesis-committee table and again on the qualifying-exam table, and
    the second table rendered a row short. Outside the mentee codes a
    second sub-heading files the same record again (a paper under two
    publication lists, a society under two membership lists), and the two
    copies would land in one output table: those stay duplicates, as do
    twins under headings that are not two sub-headings of one section."""
    if not first.code.startswith(MENTEE_CODE_PREFIX):
        return False
    one, other = first.hierarchy, second.hierarchy
    return (len(one) >= _SUB_HEADING_DEPTH and one[:-1] == other[:-1]
            and duplicate_key(one[-1]) != duplicate_key(other[-1]))


def filed_twice_by_the_cv(first: SourcePlacement, second: SourcePlacement) -> bool:
    """True when two same-text entries are two records the CV itself lists
    twice, cut from two separate source positions, neither coded as the
    Appendix fallback. Either they carry two different codes, so each
    renders in its own table (#1435: FVQANK listed three journals under
    REVIEWER FOR (Q4D) and again under EDITORIAL BOARDS (Q4C), and the
    editorial-board rows were dropped), or they share a mentee code under
    two sub-headings (`_in_two_mentee_rosters`, #2154).
    Twins cut from overlapping source elements are one line emitted twice
    upstream (in one corpus CV a single service line, element 168, came
    out under both O and P, with no second copy in the source), and a
    pair with a T copy still resolves by the M-over-T preference: both
    stay duplicates, as does a pair with no element span to compare, a
    two-code pair with a table-row copy (20 such corpus pairs include
    column-header and empty label rows coded as records), and a place or
    label line (`_is_place_or_label_line`), which a CV repeats as a
    sub-heading rather than lists as a record."""
    if first.span is None or second.span is None or not first.record_shaped:
        return False
    apart = first.span[1] < second.span[0] or second.span[1] < first.span[0]
    if (not apart or not first.code or not second.code
            or first.code.startswith(APPENDIX_TAXONOMY_PREFIX)
            or second.code.startswith(APPENDIX_TAXONOMY_PREFIX)):
        return False
    if first.code != second.code:
        return first.whole_elements and second.whole_elements
    return _in_two_mentee_rosters(first, second)
