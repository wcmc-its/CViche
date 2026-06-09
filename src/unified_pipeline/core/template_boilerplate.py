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

# Directive sentence prefixes. A normalized entry that begins with one of these
# is template boilerplate (these are the opening words of the template's
# instruction sentences). Kept explicit and lowercase.
_DIRECTIVE_PREFIXES = (
    "please include",
    "please list",
    "please summarize",
    "please annotate",
    "please choose",
    "duplicate table below",
    "include year",
    "list trainees",
    "entries should follow",
    "number the entries",
    "bold your name",
    "if joining wcm",
    "if this is the candidate",
    "categorize your entries",
    "mentorship is a longitudinal",
)

# Minimum length for the containment heuristic. Only long directive sentences
# are matched by containment; short field labels must match exactly so we never
# drop a real CV line that merely happens to contain a short label substring.
_CONTAINMENT_MIN_LEN = 40

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


def is_template_instruction(text: str) -> bool:
    """Return True if *text* is WCM-template instruction boilerplate.

    PRECISION-BIASED: when unsure, return False.

    Matching strategy (any rule matching -> True):
      a. exact normalized match against the known instruction set; ALSO split
         the entry on "|" and match each cell (handles pipe-joined template
         cells like "Full Name of Board | Certificate # (indicate if board
         eligible)").
      b. containment: a known instruction string of length >= 40 chars is
         contained in the normalized entry.
      c. directive-prefix: the normalized entry starts with a known directive
         prefix.

    Section headers (PERSONAL DATA, EDUCATION, ...) are never dropped here.
    """
    if not text:
        return False

    normalized = _normalize(text)
    if not normalized:
        return False

    # Precision guard: never drop a bare top-level WCM section header. Some
    # template field labels collide with section names once lowercased (e.g.
    # the percent-effort column label "Research" vs. the "RESEARCH" section),
    # so this guard takes precedence over the instruction matching below.
    if normalized in _SECTION_HEADER_SET:
        return False

    # Rule (a): exact normalized match.
    if normalized in _INSTRUCTION_SET:
        return True

    # Rule (a, continued): pipe-split cell match. If the raw text contains pipe
    # separators (template cells joined by "|"), match each cell individually.
    if "|" in text:
        cells = [_normalize(cell) for cell in text.split("|")]
        if any(cell and cell in _INSTRUCTION_SET for cell in cells):
            return True

    # Rule (b): containment of a long known instruction inside the entry.
    for known in _INSTRUCTION_SET:
        if len(known) >= _CONTAINMENT_MIN_LEN and known in normalized:
            return True

    # Rule (c): directive prefix.
    if normalized.startswith(_DIRECTIVE_PREFIXES):
        return True

    return False


def filter_template_instructions(texts: List[str]) -> List[str]:
    """Convenience: return only the texts that are NOT template instructions."""
    return [t for t in texts if not is_template_instruction(t)]
