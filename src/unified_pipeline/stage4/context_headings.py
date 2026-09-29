"""Sub-heading context for stage 4 (#985 fixes A and B's input side).

Stage 3b codes a bare sub-heading such as "University of Michigan:" or
"Course Director:" as T (Appendix) and leaves the entries under it with no
trace of it, so stage 4 extracts each child with no institution / role /
audience that only the heading carried. `stamp_context_headings` copies the
governing heading onto the children as `context_heading`; the prompt builder
(`extraction.build_extraction_prompt`) then shows it to the LLM.

Pure and offline: no LLM, no I/O. Never touches hierarchy, taxonomy_code or
text, and never mutates its input.
"""

import re
from typing import Any

#: Taxonomy code stage 3b gives an unclassifiable / heading-like entry.
HEADING_TAXONOMY_CODE = "T"

#: Longest raw heading text considered. Corpus-derived (see #985 census).
MAX_HEADING_CHARS = 70

#: Longest heading in words for the bare-label trigger.
MAX_LABEL_WORDS = 8

#: Most entries one heading may govern. Corpus census: the dubious spans are
#: section titles under a mis-hierarchied flat CV (100+ entries across many
#: taxonomy codes); every named #985 example is well under this.
MAX_STAMPED_RUN = 100

#: Leading enumerator on a heading ("IV. ", "B) ", "2. "), stripped before the
#: no-digit test and from the stamped text.
_ENUMERATOR_RE = re.compile(r"^\s*(?:[IVXLC]+|[A-Za-z]|\d{1,2})[.)]\s+")

#: Generic headings that name no institution, role or audience. Grown from the
#: corpus census; compared after normalisation (lower, no punctuation).
GENERIC_HEADINGS = frozenset({
    # document titles
    "curriculum vitae", "curriculum vita", "curriculum vitae bibliography", "cv",
    # catch-alls and navigation
    "other", "others", "any other", "miscellaneous", "additional", "additional information",
    "references", "contents", "table of contents", "summary", "continued", "cont", "present",
    # top-level section titles that sit under a flat / unrelated hierarchy leaf
    "contact information", "personal data", "personal information", "professional experience",
    "employment", "objective", "scope of clinical practice", "education", "education and training",
    "academic appointments", "administrative appointments", "awards", "honors",
    "honors and awards", "publications", "selected publications", "books", "book chapters",
    "editorials", "review articles", "refereed journals", "selected abstracts",
    "manuscripts in progress", "cooking",
    # form labels and boilerplate
    "committees", "committee", "mentee", "mentees", "courses", "classes",
    "check if activity involves wmc", "explanation of time gaps on cv",
})

#: A column gap (2+ spaces), an interior colon ("Name:value"), or a question
#: mark marks a table header, a label:value line or a form question, not a
#: sub-heading.
_INTERIOR_BREAK_RE = re.compile(r"\s{2,}|:|\?|https?")

#: "GOVERNMENT (not necessarily an exhaustive listing)": the aside is dropped
#: from the stamped text, the label before it is kept.
_TRAILING_ASIDE_RE = re.compile(r"\s*\((?:(?:not|non)\b|[^)]*(?:exhaustive|indicates|selected))[^)]*\)\s*$", re.I)

#: A heading that only names the KIND of output ("Book Reviews", "Selected
#: national/regional press") adds nothing the taxonomy code does not already
#: say; the fields worth filling (institution, role, audience, level, status)
#: come from headings that name those instead.
_CONTENT_TYPE_TAIL_RE = re.compile(
    r"\b(?:publications?|articles?|papers?|abstracts?|chapters?|editorials?|reviews?|presentations?"
    r"|talks|books|theses|manuscripts|press|activities|perspectives|panels|organized|design)$"
)

_PAREN_ASIDE_RE = re.compile(r"^\(.*\)$")
_NON_WORD_RE = re.compile(r"[^a-z0-9 ]+")


def _normalise(text: str) -> str:
    return " ".join(_NON_WORD_RE.sub(" ", text.lower()).split())


def _strip_heading(text: str) -> str:
    """Heading text without leading enumerator or trailing colon."""
    body = _ENUMERATOR_RE.sub("", text.strip()).rstrip(":").strip()
    return _TRAILING_ASIDE_RE.sub("", body).strip()


def _is_heading_shaped(text: str, strong_only: bool = False) -> bool:
    """One short line, no digit / pipe / tab, that reads as a label.

    strong_only drops the bare-label trigger, leaving trailing colon and
    ALL CAPS: used to end a stamp on a non-T entry, where a bare short line
    is as likely a child entry ("Jane Roe, a PhD student, Duke") as a heading.
    """
    raw = text.strip()
    if not raw or len(raw) > MAX_HEADING_CHARS or "\n" in raw or "|" in raw or "\t" in raw:
        return False
    body = _ENUMERATOR_RE.sub("", raw)
    if any(ch.isdigit() for ch in body) or body.count("(") != body.count(")") or _INTERIOR_BREAK_RE.search(raw.rstrip(":")):
        return False
    if raw.endswith(":"):
        return True
    letters = [c for c in body if c.isalpha()]
    if letters and body == body.upper():
        return True
    return (
        not strong_only
        and len(body.split()) <= MAX_LABEL_WORDS
        and body[:1].isupper()
        and "." not in body
    )


def _is_generic(heading: str, hierarchy: list[str]) -> bool:
    norm = _normalise(heading)
    if not norm or norm in GENERIC_HEADINGS or "curriculum vit" in norm or _CONTENT_TYPE_TAIL_RE.search(norm) or _PAREN_ASIDE_RE.match(heading.strip()):
        return True
    leaf = _normalise(hierarchy[-1]) if hierarchy else ""
    return bool(leaf) and norm == leaf


def _is_heading(entry: dict[str, Any]) -> bool:
    return entry.get("taxonomy_code") == HEADING_TAXONOMY_CODE and _is_heading_shaped(entry.get("text") or "")


def _heading_runs(entries: list[dict[str, Any]]) -> list[tuple[str, list[int]]]:
    """(heading, indices of the entries it governs) for every usable heading."""
    runs: list[tuple[str, list[int]]] = []
    active: list[int] | None = None
    active_hierarchy: list[str] | None = None
    for idx, entry in enumerate(entries):
        hierarchy = entry.get("hierarchy") or []
        text = entry.get("text") or ""
        if _is_heading(entry):
            heading = _strip_heading(text)
            if _is_generic(heading, hierarchy):
                active = None
            else:
                active = []
                runs.append((heading, active))
                active_hierarchy = list(hierarchy)
        elif _is_heading_shaped(text, strong_only=True) or hierarchy != active_hierarchy:
            active = None
        elif active is not None:
            active.append(idx)
    return runs


def stamp_context_headings(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return copies of `entries` with `context_heading` set on the entries
    that follow a heading under the same hierarchy.

    `entries` must be in document order. A stamp stops at the next heading, a
    hierarchy change, or a non-T entry that is itself strongly heading-shaped.
    A heading that would govern more than MAX_STAMPED_RUN entries is a section
    title under a flat hierarchy, not a sub-heading, and stamps nothing.
    """
    out = [dict(entry) for entry in entries]
    for heading, indices in _heading_runs(entries):
        if len(indices) > MAX_STAMPED_RUN:
            continue
        for idx in indices:
            out[idx]["context_heading"] = heading
    return out
