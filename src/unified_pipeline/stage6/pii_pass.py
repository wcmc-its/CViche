"""#820 piece 2: one pre-render deny pass over every entry, every code.

Before this pass, `pii.py`'s deny was consulted at exactly two sites --
`sections/personal_data.py` (value provenance for the A-coded contact
block) and `stage_6_word_template.py::_unconsumed_personal_data_batch`
(A-coded orphans headed for the Appendix). Every OTHER taxonomy code --
in particular T, the catch-all a misclassified personal-data line most
often lands in -- reached its section renderer or the Appendix completely
unfiltered (#820 finding 2; the web057 spouse line was exactly this shape).

`run_pii_pass` runs ONCE, in `stage_6_word_template.py::generate()`, after
the entries are grouped by code and before any section filler touches
them. For every entry it cuts every fragment `pii.py`'s policy matches AT
THAT ENTRY'S SCOPE out of `entry['text']`, drops every PII-keyed
`extracted_fields` entry, and records ONE `WithheldItem` per cut fragment
or dropped key. No section renderer or the Appendix can then leak a
fragment this pass has already removed, by construction rather than by
each renderer remembering to ask.

Scope is decided the way `generate()` itself decides routing: an entry is
Appendix-bound iff its code is not in `RENDER_ROUTED_CODES` (the exact set
`generate()` builds the unmapped batch from), and the Personal Data block
is code 'A'. Those two get the full policy; every routed content code gets
only the rows unambiguous as a label (see `pii.py`'s module docstring for
the #473 title-word trade-off this preserves). The routed set and the
section-name map are PASSED IN rather than imported: this package may not
import `stage_6_word_template` (a back-edge, `stage6/__init__.py`), and a
second copy of either set is the drift §1.5 forbids.

The withheld notice paragraph and the Word comment (`withheld_comment_text`)
are emitted iff `PiiPassResult.withheld` is non-empty -- an item is
recorded only when something was actually removed from what would render,
so a document with nothing withheld renders exactly as before (the render
gate's CHANGED 0 on every PII-free uid).

Kept pure and dependency-light on purpose (§1.2: no `docx` import) so it
can run before the document object even exists, and so the section-
renderer package (`stage6/sections/`) does not need to import it back
(§1.1/1.3) -- a renderer that needs to know whether ITS entry was touched
reads the `_pii_fragments` / `_pii_withheld` keys this pass writes onto the
entry dict, not this module.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from .normalization.pii import (  # noqa: F401
    SCOPE_ALL_CODES,
    SCOPE_PERSONAL_AND_APPENDIX,
    WithheldItem,
    _pii_field_key_category,
    _pii_matches,
)

#: The taxonomy code of the Personal Data block -- the one routed code that
#: still gets the full policy.
PERSONAL_DATA_CODE = "A"

#: The section label recorded for an entry `generate()` does not route.
APPENDIX_SECTION_LABEL = "Appendix"

PII_REDACTED_NOTICE = (
    "[Personal data from the source CV was withheld here "
    "(e.g. date or place of birth, marital status, family members' names). "
    "Review the original CV if this content is needed.]"
)

#: The Word comment on the notice paragraph (the A-820 addendum): a header,
#: one bullet per category, a footer. Categories and counts only -- NEVER a
#: withheld value, never a fragment of one.
WITHHELD_COMMENT_HEADER = (
    "Withheld by CViche's personal-data policy "
    "(nothing else in this document was altered):"
)
WITHHELD_COMMENT_FOOTER = (
    "Categories are listed; the values themselves are not reproduced here or "
    "anywhere in this document. Review the source CV if this content is needed."
)
WITHHELD_COMMENT_AUTHOR = "CViche"

#: Whitespace cleanup applied once after every fragment in an entry has
#: been cut out: a removed span can leave a doubled space or an empty line
#: where its label used to sit ("Personal Information:: " with the
#: "Husband: ..." half gone). Collapses that residue without touching any
#: spacing the removal itself did not create.
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")
_BLANK_LINE_RE = re.compile(r"\n[ \t]*\n")


@dataclass
class PiiPassResult:
    """`withheld`: one `WithheldItem` (defined in `normalization/pii.py`,
    beside the policy) per fragment cut or field key dropped, in document
    order. `sections/personal_data.py` appends the values its docx recovery
    withheld, so the notice and the comment cover both paths."""
    withheld: list[WithheldItem] = field(default_factory=list)


def _entry_scope(code: str, routed_codes: frozenset[str] | set[str]) -> str:
    """Which policy scope an entry with taxonomy `code` is rendered under:
    the full policy for the Personal Data block and for anything
    `generate()` will send to the Appendix, the unambiguous rows for every
    routed content code."""
    if code == PERSONAL_DATA_CODE or code not in routed_codes:
        return SCOPE_PERSONAL_AND_APPENDIX
    return SCOPE_ALL_CODES


def _section_label(code: str, routed_codes: frozenset[str] | set[str],
                   section_names: Mapping[str, str]) -> str:
    """Human section name for the notice/comment: `section_names`' entry
    for a routed code ("personal_data" -> "Personal Data"), the code itself
    when a routed code has no name there, "Appendix" otherwise."""
    if code not in routed_codes:
        return APPENDIX_SECTION_LABEL
    name = section_names.get(code)
    if not name:
        return code
    return name.replace("_", " ").title()


def _cut_spans(text: str, spans: Sequence[tuple[int, int]]) -> str:
    """`text` with each (start, end) span removed, by OFFSET -- the spans
    are `pii.py`'s own, so the right occurrence is always the one cut even
    when the same fragment recurs elsewhere in the entry."""
    pieces = []
    cursor = 0
    for start, end in spans:
        pieces.append(text[cursor:start])
        cursor = max(cursor, end)
    pieces.append(text[cursor:])
    stripped = "".join(pieces)
    stripped = _MULTI_SPACE_RE.sub(" ", stripped)
    stripped = _BLANK_LINE_RE.sub("\n", stripped)
    return stripped.strip()


def run_pii_pass(entries_by_code: Mapping[str, Sequence[dict]], *,
                 routed_codes: frozenset[str] | set[str],
                 section_names: Mapping[str, str]) -> PiiPassResult:
    """Strip protected-data fragments and field keys from every entry in
    place, at each entry's own scope, and return what was withheld.

    Determinism (the render-gate control this ticket requires): an entry
    that carries no in-scope fragment and no PII-keyed field is left
    completely untouched -- not even re-assigned -- so its identity, its
    dict, and its position are exactly what they were. Entries are never
    reordered; only an individual entry's own `text` and `extracted_fields`
    are edited, in place, in the order given.

    Two things are recorded onto a touched entry, both read by
    `sections/personal_data.py` and by
    `stage_6_word_template.py::_unconsumed_personal_data_batch` instead of
    either recomputing with its own call to `_pii_fragments` (which would
    now run against the ALREADY-STRIPPED text and find nothing):

    - ``_pii_fragments``: the fragment strings this pass found, computed
      against the entry's ORIGINAL text. `personal_data.py`'s value-
      provenance check asks whether an `extracted_fields` value came from
      inside one of them, answerable only against the pre-strip fragments.
    - ``_pii_withheld``: True whenever this pass touched the entry at all --
      the Appendix path's ENTRY-level deny (#473's granularity: an orphan
      renders nothing, so discarding it whole is free) reads this flag.
    """
    result = PiiPassResult()
    index = -1
    for code, entries in entries_by_code.items():
        scope = _entry_scope(code, routed_codes)
        section = _section_label(code, routed_codes, section_names)
        for entry in entries:
            index += 1
            raw_text = entry.get("text", "") or ""
            matches = _pii_matches(raw_text, scope) if raw_text else []

            fields = entry.get("extracted_fields") or {}
            pii_keys = [(k, _pii_field_key_category(k)) for k in fields]
            pii_keys = [(k, c) for k, c in pii_keys if c is not None]

            if not matches and not pii_keys:
                continue

            entry["_pii_fragments"] = [raw_text[m.start:m.end] for m in matches]
            entry["_pii_withheld"] = True
            if matches:
                entry["text"] = _cut_spans(raw_text, [(m.start, m.end) for m in matches])
                result.withheld.extend(
                    WithheldItem(m.category, section, index) for m in matches)
            for key, category in pii_keys:
                del fields[key]
                result.withheld.append(WithheldItem(category, section, index))

    return result


def withheld_comment_text(withheld: Sequence[WithheldItem]) -> str:
    """The Word comment's text: header, one bullet per category (in order
    of first appearance) with its item count and the section(s) the items
    would have rendered in, footer. Categories and counts only."""
    counts: dict[str, int] = {}
    sections: dict[str, list[str]] = {}
    for item in withheld:
        counts[item.category] = counts.get(item.category, 0) + 1
        seen = sections.setdefault(item.category, [])
        if item.section_label not in seen:
            seen.append(item.section_label)
    lines = [WITHHELD_COMMENT_HEADER]
    for category, count in counts.items():
        noun = "item" if count == 1 else "items"
        lines.append(f" • {category} — {count} {noun}, {', '.join(sections[category])}")
    lines.append(WITHHELD_COMMENT_FOOTER)
    return "\n".join(lines)
