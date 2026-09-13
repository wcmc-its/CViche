"""#820 piece 2: one pre-render deny pass over every entry, every code.

Before this pass, `pii.py`'s deny was consulted at exactly two sites --
`sections/personal_data.py` (value provenance for the A-coded contact
block) and `stage_6_word_template.py::_unconsumed_personal_data_batch`
(A-coded orphans headed for the Appendix). Every OTHER taxonomy code --
in particular T, the catch-all a misclassified personal-data line most
often lands in -- reached its section renderer or the Appendix completely
unfiltered (#820 finding 2; the web057 spouse line was exactly this shape).

This module runs ONCE, in `stage_6_word_template.py::generate()`, after
`entries` is loaded and before any section filler touches them: it strips
every PII fragment `pii.py` finds out of `entry['text']` and drops every
PII-keyed `extracted_fields` entry, for every entry regardless of code. No
section renderer or the Appendix can then leak a fragment this pass has
already removed, by construction rather than by each renderer remembering
to ask.

Deliberately built against ONLY `pii.py`'s stable, pre-existing public
surface (`_pii_fragments`, `_PII_FIELD_KEY_RE`) rather than a new offset-
aware primitive: this is commit 2 of this ticket's three (3, then 2, then
1 -- see the PR description), landing before piece 1's detector rewrite
(commit 3) touches `pii.py` at all, and must be correct and green on its
own against `pii.py` exactly as it stands on `origin/dev` today.

Kept pure and dependency-light on purpose (§1.2: no `docx` import) so it can
run before the document object even exists, and so the section-renderer
package (`stage6/sections/`) does not need to import it back (§1.1/1.3) --
a renderer that still needs to know whether ITS entry was touched reads the
`_pii_fragments` key this pass writes onto the entry dict, not this module.
"""
from __future__ import annotations

import re

from .normalization.pii import _PII_FIELD_KEY_RE, _pii_fragments

#: Whitespace cleanup applied once after every fragment in an entry has
#: been cut out: a removed span can leave a doubled space or an empty line
#: where its label used to sit ("Personal Information:: " with the
#: "Husband: ..." half gone). Collapses that residue without touching any
#: spacing the removal itself did not create.
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")
_BLANK_LINE_RE = re.compile(r"\n[ \t]*\n")


def _strip_fragments(text: str, fragments: list[str]) -> str:
    """`text` with each of `fragments` cut out once, in order.

    By CONTENT (`str.replace(fragment, "", 1)`), not by offset: `pii.py`'s
    stable public surface returns fragment TEXT, not positions, and
    `_pii_fragments` is exactly the function two other call sites already
    trust for this same text (`sections/personal_data.py`,
    `_unconsumed_personal_data_batch`), so re-deriving offsets here would
    be a second interpretation of the same result. The residual risk --
    an entry whose PII fragment string recurs verbatim elsewhere in the
    SAME entry, so the wrong occurrence is cut -- is narrow (the removed
    text is still a genuine PII fragment either way) and unmeasured on the
    corpus to date; revisit with a real instance rather than pre-optimizing
    for one.
    """
    for fragment in fragments:
        text = text.replace(fragment, "", 1)
    text = _MULTI_SPACE_RE.sub(" ", text)
    text = _BLANK_LINE_RE.sub("\n", text)
    return text.strip()


def apply_protected_data_pass(entries: list[dict]) -> bool:
    """Strip protected-data fragments and field keys from every entry in
    place. Returns whether anything was withheld anywhere in the document,
    so the caller can emit the existing withheld notice exactly once.

    Determinism (the render-gate control this ticket requires): an entry
    that carries no PII fragment and no PII-keyed field is left completely
    untouched -- not even re-assigned -- so its identity, its dict, and its
    position in `entries` are exactly what they were. Entries are never
    reordered; only an individual entry's own `text` and
    `extracted_fields` are edited, in place, in the order given.

    Two things are recorded onto a touched entry, both read by
    `sections/personal_data.py` and by
    `stage_6_word_template.py::_unconsumed_personal_data_batch` instead of
    either recomputing with its own call to `_pii_fragments` (which would
    now run against the ALREADY-STRIPPED text and find nothing -- the two
    call sites must not diverge from this pass, and reading its stored
    result is how they cannot):

    - ``_pii_fragments``: the fragments this pass found, computed against
      the entry's ORIGINAL text, before stripping. `personal_data.py`'s
      value-provenance check needs these unchanged -- it asks whether an
      `extracted_fields` value (already computed by stage 4, independent of
      `text`) came from inside one of them, and that question is only
      answerable against the pre-strip fragments.
    - ``_pii_withheld``: True whenever this pass touched the entry at all
      (a fragment, a field key, or both) -- the appendix path's ENTRY-level
      deny (#473's granularity: an orphan renders nothing, so discarding it
      whole is free) reads this flag rather than re-deriving it.
    """
    withheld_anything = False
    for entry in entries:
        raw_text = entry.get("text", "") or ""
        fragments = _pii_fragments(raw_text) if raw_text else []

        fields = entry.get("extracted_fields") or {}
        pii_keys = [k for k in fields if _PII_FIELD_KEY_RE.match(k)]

        if not fragments and not pii_keys:
            continue

        withheld_anything = True
        entry["_pii_fragments"] = fragments
        entry["_pii_withheld"] = True
        if fragments:
            entry["text"] = _strip_fragments(raw_text, fragments)
        for key in pii_keys:
            del fields[key]

    return withheld_anything
