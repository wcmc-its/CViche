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
from typing import NamedTuple

from .normalization.pii import (  # noqa: F401
    SCOPE_ALL_CODES,
    SCOPE_PERSONAL_AND_APPENDIX,
    WITHHOLD_POLICY,
    WithheldItem,
    _PII_FRAGMENT_SPLIT_RE,
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

#: The opener of the NEXT labelled field AT a candidate value position
#: (#821 R3 F-B). A label is a short run of word-like tokens ending in a
#: colon, optionally behind a list marker ("3. Work Email:",
#: "• Citizenship:"), which is how every fielded corpus CV separates one
#: field from the next inside a single extracted entry.
#: Used with `.match` ONLY -- at the position a bare label's value would
#: start, and at the position after a refused extension. There it is a
#: statement about POSITION, not about vocabulary: a run that BEGINS with
#: a colon-terminated word run, right where this label's value was
#: expected, is the next field rather than this label's value, whatever it
#: is called.
#: It is deliberately NOT used to search INSIDE a run (#821 R4 F-1): the
#: leftmost "1-4 words then a colon" inside
#: "Home Address:<gap>12 Elm St New York NY Citizenship: US" starts on the
#: VALUE's own trailing words, so cutting up to it left part of a
#: withheld address in the document. Searching inside a run is
#: `_KNOWN_FIELD_LABEL_RE` below, which can only stop somewhere a field is
#: actually NAMED.
#: Four words is the cap because the policy's own longest label
#: ("Social Security Number", "Date of Birth") is four; a longer colon-
#: terminated run is prose, not a field name.
_SIBLING_LABEL_MAX_WORDS = 4
_SIBLING_LABEL_RE = re.compile(
    r"(?:[•·*–—-]|\d{1,3}[.)])?[ \t]*"
    r"[A-Za-z][\w.'’&/-]*"
    r"(?:[ \t][A-Za-z][\w.'’&/-]*){0," + str(_SIBLING_LABEL_MAX_WORDS - 1) + r"}"
    r"[ \t]*:"
)

#: Field labels this pipeline RENDERS, curated from the code that already
#: recognises each one -- the second half of `_KNOWN_FIELD_LABEL_RE`'s
#: vocabulary (the first half is `WITHHOLD_POLICY`'s own label rows).
#: Verbose-mode alternatives, case-insensitive, no colon (the shared
#: `\s*:` terminator is appended once, the way `pii.py::_label_pattern`
#: appends it for a policy row).
#:
#: Every entry names a field some renderer or classifier looks for by
#: name; nothing here is a guess about English word shape. Source per
#: entry (all on this branch):
#:
#: - the six PERSONAL DATA table rows `_write_personal_data_table_cells`
#:   matches by their own cell text -- 'office address'
#:   (`sections/personal_data.py:631`), 'office telephone' (`:637`),
#:   'work email' (`:642`), 'home address' (`:647`), 'cell phone'
#:   (`:653`), 'personal email' (`:658`). 'home address' also arrives via
#:   `WITHHOLD_POLICY`'s own home-contact row, which is fine: the
#:   vocabulary is a union.
#: - `_classify_contact_label`'s label words -- `_EMAIL_LABEL_WORDS`
#:   ('e-mail', 'email') `:158`, `_PHONE_LABEL_WORDS` ('phone',
#:   'telephone') `:159`, `_ADDRESS_LABEL_WORDS` ('address', 'business')
#:   `:163`, `_PERSON_NAME_LABELS` ('name', 'full name', 'legal name',
#:   'candidate name', 'applicant name') `:169-172`.
#: - the phone-type words the entry classifier reads out of the raw text:
#:   'cell', 'mobile' (`personal_data.py:423`).
#: - 'fax' -- consumed and deliberately dropped by
#:   `_parse_address_block` (`personal_data.py:892`) and by
#:   `normalization/fields.py:174-177`.
#: - 'citizenship', 'nationality' and 'personal email' -- the #821
#:   "render" defaults, listed as NOT in the table at
#:   `normalization/pii.py:304-305`.
#: - 'npi' -- the same list's public identifier (`pii.py:306`), detected
#:   by `sections/licensure.py:86` `_NPI_LABEL_RE`.
#: - 'orcid' -- section S0's own identifier
#:   (`sections/researcher_profiles.py:1-3`, `sections/__init__.py:29`).
#: - 'website', 'home page', 'homepage', 'contact' -- the contact nouns
#:   `core/validators/contact_section.py:90-92` `CONTACT_NOUNS` lists.
#:
#: Adding a field is one row. A label NOT here is not leaked: the run is
#: cut whole (see `_extend_bare_label_span`), so the cost of a gap is a
#: lost sibling field, never a rendered protected value.
_RENDER_SET_FIELD_LABELS: tuple[str, ...] = (
    r"office \s* address",
    r"office \s* (?: telephone | phone )",
    r"work \s* e-? \s* mail",
    r"personal \s* e-? \s* mail",
    r"cell (?: \s* phone )?",
    r"mobile (?: \s* phone )?",
    r"e-? \s* mail (?: \s* address )?",
    r"telephone",
    r"phone",
    r"address",
    r"business",
    r"name",
    r"(?: full | legal | candidate | applicant ) \s* name",
    r"fax",
    r"citizenship",
    r"nationality",
    r"npi",
    r"orcid",
    r"website",
    r"home \s* page | homepage",
    r"contact",
)

#: The ONLY thing a bare-label extension may stop at part-way through a
#: whitespace run (#821 R4 F-1): a label this codebase actually knows, at
#: a word start, optionally behind a list marker.
#:
#: Built from `WITHHOLD_POLICY`'s label rows PLUS
#: `_RENDER_SET_FIELD_LABELS`, the same way `pii.py::_PII_LABEL_RE` is
#: built from those rows -- one source, so the vocabulary cannot drift
#: from the policy when a row is added.
#:
#: The leading `(?<![\w'’-])` is what makes it a FIELD NAME rather than a
#: substring: without it "MyCitizenship:" inside a value stops the cut and
#: everything before it renders.
_KNOWN_FIELD_LABEL_RE = re.compile(
    r"(?<![\w'’-])"
    r"(?:(?:[•·*–—-]|\d{1,3}[.)])[ \t]*)?"
    r"(?:"
    + "|".join(
        [r"(?:" + str(rule.label) + r")"
         for rule in WITHHOLD_POLICY if rule.label is not None]
        + [r"(?:" + label + r")" for label in _RENDER_SET_FIELD_LABELS]
    )
    + r")\s*:",
    re.X | re.I,
)


class _BareLabelSpan(NamedTuple):
    """What `_extend_bare_label_span` decided about one bare-label match.

    `end` is the offset the cut should run to. `orphaned_value` is True when
    the extension was REFUSED and what follows the label is an unlabelled
    value rather than the next labelled field -- i.e. the protected value is
    still sitting in the entry's residual text, unlabelled and
    indistinguishable from safe content. Only the pass can tell: by the time
    `stage_6_word_template.py` sees the entry, the cut has already happened
    and the fragment strings alone cannot say whether anything was left
    behind (#821 R3 F-D)."""

    end: int
    orphaned_value: bool


def _value_is_orphaned_after(text: str, pos: int) -> bool:
    """True when the content of `text` from `pos` on opens with something
    that is NOT another labelled field -- the shape that means a bare
    label's own value survived the cut uncut."""
    rest = text[pos:]
    lead = len(rest) - len(rest.lstrip())
    if lead == len(rest):
        return False  # nothing but whitespace follows; nothing was orphaned
    return not _SIBLING_LABEL_RE.match(text, pos + lead)


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


def _extend_bare_label_span(text: str, start: int, end: int) -> _BareLabelSpan:
    """Extend a label match's span past its own colon when nothing else
    was captured (#821 R2 F3 / #834 follow-up).

    `pii.py`'s `_label_spans` runs a match "from the opener's own start to
    the next hard delimiter" (`_PII_FRAGMENT_SPLIT_RE`: `\\n`, a tab, `|`,
    `;`, or 3+ spaces). Two corpus shapes put that delimiter directly after
    the label's own colon, before its value ever starts: a label and its
    value joined by a literal pipe, the way a two-cell table row is
    rendered elsewhere in this pipeline ("Home telephone: | <phone>"), and
    a column-aligned field pair whose label is padded out to a fixed width
    with spaces ("Home Phone:<wide gap><phone> Citizenship: ..."). Either
    way the match this function is handed already stops AT the colon -- the
    value is left completely uncut, indistinguishable, once
    `stage_6_word_template.py::_unconsumed_personal_data_batch` sees it,
    from safe kept content (the pipe shape leaked a home phone into the
    Appendix exactly this way before this fix).

    Only fires when the match text, right-stripped, ends in `:` -- a
    normal label+value match (the far more common shape, e.g. "Home
    Phone: 555-1234") already captured its value and is returned
    unchanged. Computed against the ORIGINAL `text` and its real offsets,
    before `_cut_spans` collapses the very whitespace run that marks this
    boundary -- the one place in the pass where that information still
    exists at all.

    Two hard stops, because an extension is a CUT and a predicate that
    reaches past the value it is named for deletes someone else's content
    (#821 R3 F-B, found by the round-2 verifier -- both shapes below lost a
    #821 "render" item: citizenship, a work email):

    - it never crosses a newline. A line break is the one delimiter the
      source document itself drew; whatever is on the next line is a new
      field, not this label's orphaned value.
    - it stops where the value would start if what is there is the next
      labelled field by POSITION (`_SIBLING_LABEL_RE.match`), and PART-WAY
      through the run only at a label this codebase actually knows
      (`_KNOWN_FIELD_LABEL_RE`). "Home Phone:<gap>555-0100 Citizenship: US"
      separates the sibling by a SINGLE space, which is not a hard
      delimiter at all, so without a stop inside the run the extension ran
      to the end of the entry.

    The in-run stop is a VOCABULARY, not a word shape, and that is the
    whole of #821 R4 F-1. The previous version took the leftmost run of
    1-4 word tokens ending in a colon, which the protected value's own
    trailing words satisfy: "Home Address:<gap>12 Elm St New York NY
    Citizenship: US" stopped on the value's city/state and rendered them.
    Any rule that guesses where a value ENDS from word shape leaks some
    value; only a rule that recognises where the NEXT FIELD BEGINS is
    leak-proof by construction. The price is stated and accepted: a
    sibling field whose label is not in the vocabulary, inside the same
    whitespace run, is cut together with the value -- never leaked,
    possibly lost. Extending the vocabulary is one row in
    `_RENDER_SET_FIELD_LABELS`.

    When a stop refuses the extension, `orphaned_value` says whether the
    value is still sitting there uncut (see `_BareLabelSpan`): a label with
    nothing after it, or with another labelled field after it, orphans
    nothing and its residual is safe to render (#821 R3 F-D)."""
    if not text[start:end].rstrip().endswith(':'):
        return _BareLabelSpan(end, False)
    delim = _PII_FRAGMENT_SPLIT_RE.match(text, end)
    if delim is None:
        # A bare label at the very end of the entry: nothing follows it to
        # cut, and nothing follows it to leak either.
        return _BareLabelSpan(end, False)
    if "\n" in delim.group():
        return _BareLabelSpan(end, _value_is_orphaned_after(text, delim.end()))
    after = text[delim.end():]
    value_start = delim.end() + (len(after) - len(after.lstrip(" \t")))
    if value_start >= len(text) or text[value_start] == "\n":
        # the delimiter was trailing whitespace, or the line ends here
        return _BareLabelSpan(end, _value_is_orphaned_after(text, value_start))
    if _SIBLING_LABEL_RE.match(text, value_start):
        return _BareLabelSpan(end, False)  # the next field, not our value
    nxt = _PII_FRAGMENT_SPLIT_RE.search(text, value_start)
    limit = nxt.start() if nxt else len(text)
    known = _KNOWN_FIELD_LABEL_RE.search(text, value_start, limit)
    return _BareLabelSpan(known.start() if known else limit, False)


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
      against the entry's ORIGINAL text -- extended past a bare label's own
      colon when nothing else was captured there (`_extend_bare_label_span`,
      #821 R2 F3 follow-up), so a value a hard delimiter separated from its
      label is still part of what these fragments say was removed.
      `personal_data.py`'s value-provenance check asks whether an
      `extracted_fields` value came from inside one of them, answerable
      only against the pre-strip fragments.
    - ``_pii_withheld``: True whenever this pass touched the entry at all --
      the Appendix path's ENTRY-level deny (#473's granularity: an orphan
      renders nothing, so discarding it whole is free) reads this flag.
    - ``_pii_orphaned_value``: True when a bare label's own value could not
      be pulled into the cut and is still in the residual text, unlabelled
      (`_BareLabelSpan.orphaned_value`). The Appendix path refuses that
      entry's residual outright; a bare label with nothing after it, or
      with the next labelled field after it, sets this False and its
      residual renders (#821 R3 F-D).
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

            # Extend a bare-label match (`_extend_bare_label_span`) to pull
            # in its orphaned value BEFORE any offset is used for anything
            # -- both the cut and the recorded fragment read the extended
            # span, so `_pii_fragments` reflects what was actually removed.
            spans = [_extend_bare_label_span(raw_text, m.start, m.end)
                     for m in matches]
            entry["_pii_fragments"] = [raw_text[m.start:s.end]
                                       for m, s in zip(matches, spans)]
            entry["_pii_withheld"] = True
            # Whether any of those extensions was refused with the label's
            # own value left uncut in the residual (#821 R3 F-D): the one
            # verdict the Appendix path cannot recompute for itself.
            entry["_pii_orphaned_value"] = any(s.orphaned_value for s in spans)
            if matches:
                entry["text"] = _cut_spans(
                    raw_text, [(m.start, s.end) for m, s in zip(matches, spans)])
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
