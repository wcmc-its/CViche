"""Deterministic, precision-biased detector for WCM-template instruction boilerplate.

Faculty build their CVs on the blank WCM CV template and frequently leave the
template's instruction scaffolding (directive sentences, field/column labels,
subcategory prompt lines) in the document. The pipeline parses that boilerplate
into entries that pollute the output (e.g. a large spurious "T. APPENDIX" dump,
polluted sections, and many noise comments).

``is_template_instruction`` decides whether a given text block is WCM-template
boilerplate that should be dropped. It is biased HARD toward precision: when in
doubt it returns ``False`` so that real CV content is never dropped.

The known-phrase sets are generated from the tracked WCM template by
``scripts/gen_template_boilerplate.py`` into
``template_boilerplate_phrases.json`` (loaded once at import). Top-level section
headers are deliberately kept OUT of the drop set: dropping a genuine section
header could disturb downstream structure.
"""

import difflib
import json
import re
from pathlib import Path

# Resolve the JSON relative to THIS file so it works regardless of cwd.
_JSON_PATH = Path(__file__).resolve().parent / "template_boilerplate_phrases.json"

# Minimum length (normalized chars) for an exact / pipe-cell match to count as
# boilerplate. Short template field labels ("Teaching", "National", "Books",
# "Full Name of Board", ...) collide with words faculty legitimately use as
# their own content, and real table rows arrive with such labels as cells, so we
# only exact-match-drop DISTINCTIVE longer strings. The real leaked boilerplate
# (directive sentences, parenthetical prompts, detailed column labels) is well
# above this threshold.
_MIN_EXACT_LEN = 25

# Minimum length for the containment heuristic. Only long directive sentences
# are matched by containment; short field labels must match exactly so we never
# drop a real CV line that merely happens to contain a short label substring.
_CONTAINMENT_MIN_LEN = 40

# WCM BIBLIOGRAPHY category labels. Faculty legitimately keep these as the
# organizing sub-headings of their own bibliography, so they must NEVER be
# dropped even though they appear in the template (and some exceed
# _MIN_EXACT_LEN). Protected exactly like section headers. The parenthetical
# "(optional, ...)" template variants are deliberately NOT listed here, so those
# fuller forms still drop as boilerplate.
_BIBLIOGRAPHY_CATEGORIES = (
    "Peer-reviewed Research Articles",
    "Reviews and Editorials",
    "Books",
    "Chapters",
    "Non-peer-reviewed Research Publications",
    "Case Reports",
    "Abstracts",
    "In review",
    "Other (media, podcasts, etc.)",
)

# Leading bullet / list-marker characters to strip during normalization.
_LEADING_MARKERS = "•‣◦⁃∙*-–—.) \t"
# Surrounding punctuation/whitespace to strip from both ends.
_SURROUNDING = " \t:;.,*•-"


def _normalize(text: str) -> str:
    """Normalize a text block for matching.

    - collapse all whitespace to single spaces and lowercase
    - strip leading bullets / list numbers / asterisks
    - strip surrounding punctuation
    """
    if not text:
        return ""
    s = re.sub(r"\s+", " ", text).strip().lower()
    # Strip leading bullet glyphs / dashes / asterisks / stray punctuation.
    s = s.lstrip(_LEADING_MARKERS)
    # Strip a leading "1." / "2)" style list number.
    s = re.sub(r"^\d+[.\)]\s*", "", s)
    s = s.strip().strip("*").strip()
    # Strip surrounding punctuation (e.g. trailing colon on a label/header).
    s = s.strip(_SURROUNDING)
    return s


def _load_phrases():
    """Load and normalize the phrase sets.

    Returns ``(instruction_set, section_header_set)``. The instruction set is
    DROP-eligible; the section-header set is a precision GUARD (entries matching
    a top-level section header are never dropped, even if they collide with a
    short field label such as the "Research" column label).
    """
    try:
        with open(_JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        # Fail safe: empty sets -> detector returns False for everything
        # (precision bias: never drop real content if the data is missing).
        return set(), set()

    instructions = data.get("instructions", []) or []
    section_headers = data.get("section_headers", []) or []

    instruction_set = {n for n in (_normalize(p) for p in instructions) if n}
    section_header_set = {n for n in (_normalize(h) for h in section_headers) if n}
    return instruction_set, section_header_set


# Module-level cache: load the normalized phrase sets once at import.
_INSTRUCTION_SET, _SECTION_HEADER_SET = _load_phrases()

# Protected terms are never dropped and take precedence over instruction
# matching: top-level section headers plus bibliography category labels.
_PROTECTED = _SECTION_HEADER_SET | {
    n for n in (_normalize(c) for c in _BIBLIOGRAPHY_CATEGORIES) if n
}


def is_template_instruction(text: str) -> bool:
    """Return True if *text* is WCM-template instruction boilerplate.

    PRECISION-BIASED: when unsure, return False — real CV content must never be
    dropped.

    Matching strategy (any rule -> True), after a protected-term guard:
      a. exact normalized match against the known instruction set, but only for
         DISTINCTIVE strings (>= _MIN_EXACT_LEN chars). Short generic template
         labels (Teaching, National, Books, ...) are intentionally NOT dropped,
         because faculty use those same words as real content.
      b. pipe-split: real CV table rows reach this joined by "|". Drop only if
         some cell is a distinctive (>= _MIN_EXACT_LEN), non-protected known
         instruction; a cell that is just a short label ("... | Teaching") or a
         protected term never triggers a drop.
      c. containment: a known instruction of length >= _CONTAINMENT_MIN_LEN is
         contained in the normalized entry (catches a template sentence left in
         with extra faculty text appended).

    Protected terms (top-level section headers + bibliography category labels)
    are never dropped, even when they collide with a template field label.
    """
    if not text:
        return False

    normalized = _normalize(text)
    if not normalized:
        return False

    # Precision guard: never drop a protected term (section header or
    # bibliography category). Takes precedence over all instruction matching.
    if normalized in _PROTECTED:
        return False

    # Rule (a): exact match, but only on a distinctive (long enough) instruction
    # string, so short generic labels can never drop a real one-word entry.
    if len(normalized) >= _MIN_EXACT_LEN and normalized in _INSTRUCTION_SET:
        return True

    # Rule (b): pipe-split. Real table rows arrive as "cell | cell | cell".
    # Drop the row ONLY if EVERY non-empty cell is recognized scaffolding (a
    # known instruction or a protected header/label) AND at least one cell is a
    # distinctive instruction. A single unrecognized cell means real faculty
    # data, so the whole row is kept -- e.g. a filled board-cert row that still
    # carries a leftover "(indicate if board eligible)" cell. Pure header/label
    # concatenations still drop; anything that slips through is caught by the
    # Stage 6 appendix backstop.
    if "|" in text:
        cells = [c for c in (_normalize(c) for c in text.split("|")) if c]
        if cells and all(c in _INSTRUCTION_SET or c in _PROTECTED for c in cells):
            if any(
                c not in _PROTECTED
                and len(c) >= _MIN_EXACT_LEN
                and c in _INSTRUCTION_SET
                for c in cells
            ):
                return True
        elif cells:
            # A cell nobody recognises is faculty data, and this rule's
            # verdict is final: rules (c)/(d) below see only the joined
            # string, so a filled row whose LABEL is a long template phrase
            # ("Is your eligibility to work in the U.S. based on an
            # employment visa?: | No") would otherwise drop with its answer
            # by containment (#897).
            return False

    # Rule (c): containment of a long known instruction inside the entry
    # (a template sentence left in with extra faculty text appended).
    for known in _INSTRUCTION_SET:
        if len(known) >= _CONTAINMENT_MIN_LEN and known in normalized:
            return True

    # Rule (d): reverse containment — the entry is a long (>= _CONTAINMENT_MIN_LEN)
    # verbatim fragment of an even longer template instruction paragraph (faculty
    # left part of a directive paragraph in). Safe: a real CV line is never a
    # verbatim substring of the template's directive prose, and the >= 40-char
    # floor rules out short generic fragments.
    if len(normalized) >= _CONTAINMENT_MIN_LEN:
        for known in _INSTRUCTION_SET:
            if len(known) > len(normalized) and normalized in known:
                return True

    return False


# Near-match of a long instruction (#829). Faculty fill in whichever template
# revision they were sent, and an older revision's directive paragraph differs
# from the tracked Oct-2022 file by a comma or a word ("Please include
# title/audience/dates ..." is 417 chars and one comma off), which rules (a)-(d)
# miss because they are all exact-substring tests. Measured on the S3 pilot
# corpus (126 runs, 38 distinct CVs): at 0.93 on pipe-free lines, 16 appendix
# lines in 4 CVs match and no non-T entry does. At 0.90, or with '|' rows
# allowed, it also matched filled "label | answer" rows ("... employment
# visa?: | No") -- real answers, so both limits are load-bearing.
_NEAR_MATCH_MIN_RATIO = 0.93

# A prompt's answer that says there is nothing to report (#885). Alone, or as
# the answer cell of a known label ("Primary Hospital Affiliation: | N/A"),
# the line carries no CV content.
_UNANSWERED = frozenset({"n/a", "na", "not applicable", "none", "listed above"})


def is_near_template_instruction(text: str) -> bool:
    """True if *text* is a long template instruction in slightly different
    wording -- another template revision's copy of the same paragraph.

    Pipe-free text of at least _CONTAINMENT_MIN_LEN normalized chars only;
    see _NEAR_MATCH_MIN_RATIO for why both limits hold. A protected term is
    never matched, as in `is_template_instruction`.
    """
    if not text or "|" in text:
        return False
    normalized = _normalize(text)
    if len(normalized) < _CONTAINMENT_MIN_LEN or normalized in _PROTECTED:
        return False
    for known in _INSTRUCTION_SET:
        # The two cheap upper bounds reject almost every candidate (a short
        # label, a paragraph of a different length) before ratio() runs.
        matcher = difflib.SequenceMatcher(None, normalized, known, autojunk=False)
        if (matcher.real_quick_ratio() >= _NEAR_MATCH_MIN_RATIO
                and matcher.quick_ratio() >= _NEAR_MATCH_MIN_RATIO
                and matcher.ratio() >= _NEAR_MATCH_MIN_RATIO):
            return True
    return False


def is_unanswered_prompt(text: str) -> bool:
    """True if *text* says only that a prompt has nothing to report: "N/A",
    "Not Applicable |  |", or a known template label whose answer cell is
    one of those ("Primary Hospital Affiliation: | N/A").

    At least one cell must be an _UNANSWERED value; every other non-empty
    cell must be a known template label. A cell nobody recognises is
    faculty data, and keeps the line.
    """
    if not text:
        return False
    cells = [c for c in (_normalize(c) for c in text.split("|")) if c]
    if not any(c in _UNANSWERED for c in cells):
        return False
    return all(c in _UNANSWERED or c in _INSTRUCTION_SET or c in _PROTECTED
               for c in cells)


# A line's pieces for `is_template_label_line`: its table cells, and the
# tab- or newline-separated parts inside them.
_LABEL_PIECE_SPLIT_RE = re.compile(r"[|\t\n]")


def is_template_label_line(text: str) -> bool:
    """True if every non-empty piece of *text* is a known template label.

    FOR THE APPENDIX ONLY. `is_template_instruction` refuses short labels
    ("Signature:", "If no license:", "Site/Position |") so a faculty member's
    own one-word line ("Teaching") is never dropped before classification.
    A line on its way to the Appendix has already been judged non-content,
    and one made of nothing but template labels -- an unfilled field, a
    column-header row -- carries nothing to show there, so the length floor
    does not apply. Any piece that is not a known label ("Your role* |
    oversight") keeps the line (#829).
    """
    pieces = [p for p in (_normalize(x) for x in _LABEL_PIECE_SPLIT_RE.split(text or ""))
              if p]
    return bool(pieces) and all(p in _INSTRUCTION_SET or p in _PROTECTED for p in pieces)


def filter_template_instructions(texts: list[str]) -> list[str]:
    """Convenience: return only the texts that are NOT template instructions."""
    return [t for t in texts if not is_template_instruction(t)]


# Source-document furniture (NOT WCM-template scaffolding): title lines, date
# stamps, and page markers from the ORIGINAL CV ("CURRICULUM VITAE",
# "Last Updated - JUN 2026", "Page 3 of 12"). These carry no CV content and
# only pollute the Appendix as "unmapped content" (#213). Single short lines
# only, and the updated/revised forms require a separator or a date-like tail
# so that real content ("Updated the curriculum for ...") is never matched.
_MONTHS = r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
_SOURCE_FURNITURE = re.compile(
    rf"""^(?:
        curriculum\s+vitae
      | (?:last\s+)?(?:updated|revised)\s*[:\-–—]\s*\S.{{0,30}}
      | (?:last\s+)?(?:updated|revised)\b\s*(?:on\s+)?(?:\d|{_MONTHS})[\w\s,./-]{{0,25}}
      | page\s+\d+(?:\s+of\s+\d+)?
    )$""",
    re.IGNORECASE | re.VERBOSE,
)


def is_source_boilerplate(text: str) -> bool:
    """True if ``text`` is source-CV furniture that should never surface as
    Appendix "unmapped content". Precision-biased, like everything above."""
    if not text:
        return False
    stripped = text.strip()
    if not stripped or "\n" in stripped or len(stripped) > 60:
        return False
    return bool(_SOURCE_FURNITURE.match(stripped))
