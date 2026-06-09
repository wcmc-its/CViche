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

import json
import re
from pathlib import Path
from typing import List

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

    # Rule (b): pipe-split cell match. Real table rows arrive as "cell | cell |
    # cell"; only a distinctive, non-protected instruction cell triggers a drop.
    if "|" in text:
        for cell in (_normalize(c) for c in text.split("|")):
            if (
                cell
                and cell not in _PROTECTED
                and len(cell) >= _MIN_EXACT_LEN
                and cell in _INSTRUCTION_SET
            ):
                return True

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


def filter_template_instructions(texts: List[str]) -> List[str]:
    """Convenience: return only the texts that are NOT template instructions."""
    return [t for t in texts if not is_template_instruction(t)]
