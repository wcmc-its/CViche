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
from collections import Counter
from typing import Any

#: Taxonomy code stage 3b gives an unclassifiable / heading-like entry.
HEADING_TAXONOMY_CODE = "T"

#: Longest raw heading text considered. Corpus-derived (see #985 census).
MAX_HEADING_CHARS = 70

#: Longest heading in words for the bare-label trigger. Corpus census (#985
#: round 2): real bare headings top out at 6 words ("University of Maryland
#: School of Medicine"); 7+ is a table header or a wrapped title fragment.
MAX_LABEL_WORDS = 6

#: Fewest words a bare label needs: a one-word line ("Connecticut",
#: "Ementorship") is a place or a section title, not a sub-heading.
MIN_LABEL_WORDS = 2

#: A bare label right after this many T entries is the tail of a list of
#: journal / organisation names, not a sub-heading ("Welsh Office" after 30).
MAX_T_STREAK_BEFORE_BARE_LABEL = 1

#: Fewest letters an ALL-CAPS heading needs ("TLS", "JAMA": journal / acronym list items).
MIN_CAPS_LETTERS = 5

#: stage 3b element_type of a wrapped-line tail ("Maryland School of
#: Medicine" after "...University of"): never a bare heading.
WRAPPED_ELEMENT_TYPE = "break"

#: A bare label opening with one of these is a sentence or title fragment
#: ("And Date", "Between ... and Child Maltreatment", "My achievements include").
_FRAGMENT_LEAD_WORDS = frozenset({"and", "or", "of", "between", "my", "in", "for", "with", "to", "by"})

#: Most entries one heading may govern. Census (#985 round 2): every named
#: example is <= 18; the spans above 30 are a section title over a mixed leaf
#: ("Ongoing reviewer for" into grant study-section rows) or a list item.
MAX_STAMPED_RUN = 30

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
    if raw.endswith(":") or _is_all_caps(body):
        return True
    return not strong_only and _is_bare_label(body)


def _is_all_caps(body: str) -> bool:
    letters = [c for c in body if c.isalpha()]
    return len(letters) >= MIN_CAPS_LETTERS and body == body.upper()


def _is_bare_label(body: str) -> bool:
    """Title-Case label with no colon: 2..MAX_LABEL_WORDS words, no comma or
    period (places, wrapped sentences), not opening on a connective."""
    words = body.split()
    return (
        MIN_LABEL_WORDS <= len(words) <= MAX_LABEL_WORDS
        and body[:1].isupper()
        and "." not in body
        and "," not in body
        and words[0].lower() not in _FRAGMENT_LEAD_WORDS
    )


def _is_generic(heading: str, hierarchy: list[str]) -> bool:
    norm = _normalise(heading)
    if not norm or norm in GENERIC_HEADINGS or "curriculum vit" in norm or _CONTENT_TYPE_TAIL_RE.search(norm) or _PAREN_ASIDE_RE.match(heading.strip()):
        return True
    leaf = _normalise(hierarchy[-1]) if hierarchy else ""
    return bool(leaf) and norm == leaf


def _is_dropped(entry: dict[str, Any]) -> bool:
    """Stage 4 does not extract fragments and duplicates; they are neither
    stamped nor headings, but a heading-shaped one still ends a run."""
    return bool(entry.get("is_fragment") or entry.get("is_duplicate"))


def _is_heading(entry: dict[str, Any], t_streak: int, next_entry: dict[str, Any] | None) -> bool:
    """A T entry that is heading-shaped. A bare label (no colon, not ALL CAPS)
    must also not be a wrapped-line tail, sit inside a run of T entries (a list
    of journal / organisation names), or lack a non-T entry right after it."""
    if entry.get("taxonomy_code") != HEADING_TAXONOMY_CODE:
        return False
    text = entry.get("text") or ""
    if _is_heading_shaped(text, strong_only=True):
        return True
    return (
        t_streak <= MAX_T_STREAK_BEFORE_BARE_LABEL
        and entry.get("element_type") != WRAPPED_ELEMENT_TYPE
        and next_entry is not None
        and next_entry.get("taxonomy_code") != HEADING_TAXONOMY_CODE
        and _is_heading_shaped(text)
    )


#: A hierarchy leaf whose entries span this many taxonomy letters is a flat /
#: mis-hierarchied section (web46 'Personal Data' holds A, B, D, I, M, N, P, S;
#: web204 'BOARDS OF TRUSTEES' holds N, Q, R), so one heading there must not govern children of a different letter.
#: A coherent leaf ('Institutional Service': P, O) legitimately mixes codes.
FLAT_HIERARCHY_MIN_LETTERS = 3

#: Entries a taxonomy letter needs in a leaf before it counts toward
#: FLAT_HIERARCHY_MIN_LETTERS: one stray misclassified entry (web051's lone Q1
#: and Q2 under 'Institutional Service') must not make a coherent leaf flat.
FLAT_LETTER_MIN_ENTRIES = 3


def _code_family(code: str) -> str:
    """Leading letter of a taxonomy code ('N3A' -> 'N')."""
    return code[:1]


def _flat_hierarchies(entries: list[dict[str, Any]]) -> set[tuple[str, ...]]:
    letters: dict[tuple[str, ...], Counter[str]] = {}
    for entry in entries:
        code = entry.get("taxonomy_code") or ""
        if code and code != HEADING_TAXONOMY_CODE and not _is_dropped(entry):
            letters.setdefault(tuple(entry.get("hierarchy") or []), Counter())[_code_family(code)] += 1
    return {
        h for h, found in letters.items()
        if sum(1 for n in found.values() if n >= FLAT_LETTER_MIN_ENTRIES) >= FLAT_HIERARCHY_MIN_LETTERS
    }


class _Run:
    """The entries one heading governs; in a flat hierarchy, one code letter."""

    def __init__(self, heading: str, hierarchy: list[str], flat: bool) -> None:
        self.heading = heading
        self.hierarchy = hierarchy
        self.flat = flat
        self.family: str | None = None
        self.indices: list[int] = []

    def accepts(self, entry: dict[str, Any]) -> bool:
        code = entry.get("taxonomy_code") or ""
        return not self.flat or code == HEADING_TAXONOMY_CODE or self.family in (None, _code_family(code))

    def add(self, idx: int, entry: dict[str, Any]) -> None:
        code = entry.get("taxonomy_code") or ""
        if self.family is None and code != HEADING_TAXONOMY_CODE:
            self.family = _code_family(code)
        self.indices.append(idx)


def _heading_runs(entries: list[dict[str, Any]]) -> list[_Run]:
    """One run per usable heading, in document order."""
    runs: list[_Run] = []
    flat = _flat_hierarchies(entries)
    active: _Run | None = None
    t_streak = 0
    for idx, entry in enumerate(entries):
        hierarchy = entry.get("hierarchy") or []
        text = entry.get("text") or ""
        if _is_dropped(entry):
            if _is_heading_shaped(text, strong_only=True):
                active = None
        elif _is_heading(entry, t_streak, entries[idx + 1] if idx + 1 < len(entries) else None):
            heading = _strip_heading(text)
            active = None if _is_generic(heading, hierarchy) else _Run(heading, list(hierarchy), tuple(hierarchy) in flat)
            if active is not None:
                runs.append(active)
        elif (entry.get("taxonomy_code") == HEADING_TAXONOMY_CODE
              or _is_heading_shaped(text, strong_only=True) or active is None
              or hierarchy != active.hierarchy or not active.accepts(entry)):
            active = None
        else:
            active.add(idx, entry)
        t_streak = t_streak + 1 if entry.get("taxonomy_code") == HEADING_TAXONOMY_CODE else 0
    return runs


def stamp_context_headings(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return copies of `entries` with `context_heading` set on the entries
    that follow a heading under the same hierarchy.

    `entries` must be stage 3b's FULL list in document order, fragments and
    duplicates included: a dropped sibling sub-heading ("University of
    Maryland:" flagged is_fragment) must still end the run before it. A stamp
    stops at the next heading, at ANY other T entry (a T line that is not
    heading-shaped -- "2003-2016 University of ..." -- still separates record
    groups), a hierarchy change, a strongly heading-shaped non-T or dropped
    entry, or a change of taxonomy family among the children of a flat leaf.
    A heading that would govern more than MAX_STAMPED_RUN entries stamps nothing.
    """
    out = [dict(entry) for entry in entries]
    for run in _heading_runs(entries):
        if len(run.indices) > MAX_STAMPED_RUN:
            continue
        for idx in run.indices:
            out[idx]["context_heading"] = run.heading
    return out
