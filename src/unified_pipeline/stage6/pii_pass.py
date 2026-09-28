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
    CAT_THIRD_PARTY_CONTACT,
    SCOPE_ALL_CODES,
    SCOPE_PERSONAL_AND_APPENDIX,
    WITHHOLD_POLICY,
    PiiMatch,
    WithheldItem,
    _BARE_EMAIL_SHAPE,
    _BARE_PHONE_SHAPE,
    _PII_FRAGMENT_SPLIT_RE,
    _merge_matches,
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


#: #833: local parts of a generic mailbox an editorial board / journal
#: publishes ("submissions@<journal>.org"), not a person's own address --
#: the negative control the issue names. A small, closed vocabulary rather
#: than a word-shape guess, same reasoning as `_RENDER_SET_FIELD_LABELS`
#: above: matched on the FULL local part, so "editorial@..." (not "editor")
#: is not spared by accident.
#:
#: No phone equivalent (a published department / front-desk line) exists
#: here. That is deliberate, not an oversight: #833 is about a References
#: block's PERSONAL contact data, and a generic-mailbox concept for phones
#: was never raised against this corpus. Out of scope for this issue --
#: add one the same way (a closed vocabulary of known front-desk numbers,
#: not a shape guess) if a real CV ever needs it.
_GENERIC_MAILBOX_LOCAL_PARTS = frozenset({
    "editor", "office", "info", "journal", "admin", "submissions",
})

#: Neither shape below has a case-sensitive literal or a literal space
#: outside a character class, so `re.I`/`re.X` matched nothing either flag
#: would change; dropped rather than kept as decoration, since a future
#: edit that DID add a literal space outside `[...]` would otherwise be
#: silently verbose-formatted away instead of erroring.
_THIRD_PARTY_EMAIL_RE = re.compile(_BARE_EMAIL_SHAPE)
_THIRD_PARTY_PHONE_RE = re.compile(_BARE_PHONE_SHAPE)

#: Name tokens shorter than this (initials, "Dr", "Jr") are dropped before
#: the owner-name-sharing check below, so they cannot cheaply satisfy it.
_MIN_OWNER_NAME_TOKEN_LEN = 3
_NAME_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'-]+")


def _phone_digits(value: str) -> str:
    """Digits only, with a US country-code `1` stripped when it produces
    an 11-digit result (#920 review: `_phone_digits` normalised
    "+1 212-555-0100" and "212-555-0100" to different strings, so the
    owner-contact comparison below missed the same number in two formats
    -- correctness only, since the old behaviour failed SAFE by
    over-withholding the owner's own number rather than leaking anyone
    else's)."""
    digits = re.sub(r"\D", "", value)
    return digits[1:] if len(digits) == 11 and digits.startswith("1") else digits


def _owner_contacts(a_entries: Sequence[dict]) -> frozenset[str]:
    """Every email/phone the CV owner's own 'A' entries carry, from `text`
    and `extracted_fields` alike (#833): the whole point of the rule below
    is telling the owner's OWN contact block -- which legitimately repeats
    inside an Appendix-bound entry, e.g. a resume-portal footer -- from a
    third party's. Phones are digit-normalised so a formatting difference
    ("212-555-0100" vs "(212) 555-0100") cannot defeat the comparison;
    emails are case-folded."""
    contacts: set[str] = set()
    for entry in a_entries:
        values: list[str] = [entry.get("text") or ""]
        values.extend(v for v in (entry.get("extracted_fields") or {}).values()
                      if isinstance(v, str))
        for value in values:
            contacts.update(m.group().lower()
                            for m in _THIRD_PARTY_EMAIL_RE.finditer(value))
            contacts.update(_phone_digits(m.group())
                            for m in _THIRD_PARTY_PHONE_RE.finditer(value))
    return frozenset(contacts)


def _owner_name_tokens(a_entries: Sequence[dict]) -> frozenset[str]:
    """Tokens of the CV owner's own name (the #833 second-email negative
    control, now the #920 per-value email exemption too):
    `extracted_fields['name']` of the first 'A' entry that has one, else
    the first 'A' entry's raw text -- a References-block-style entry
    ("Name, Title, Institution") puts the owner's own name first exactly
    the way a normal Personal Data block does.

    Document-order guarantee for the fallback: `a_entries` is
    `entries_by_code['A']` as `run_pii_pass` receives it, built by
    `stage_6_word_template.py::_group_entries_by_code`'s single
    `for entry in entries: entries_by_code[code].append(entry)` pass, so
    within-code order is exactly `entries`' own order. `entries` itself is
    Stage 4's `all_entries`, EXPLICITLY sorted by
    `(element_idx_start, element_idx_end)`
    (`stage4/extraction.py:1037`) before Stage 6 ever sees it, and no
    later stage (5, 5b, 5c, 5d) re-sorts it (`stage_5_pubmed_enrichment.py`,
    `stage_5b_institution_enrichment.py` sort only glob results, never the
    entries list; `stage_5c_teaching_formatter.py` and
    `stage_5d_citation_formatter.py` group `data['entries']` by k-code with
    a plain `dict.get`, with no `sorted(...)`/`.sort()` on the list
    itself either). `a_entries[0]` is therefore document-order-first,
    modulo #916's own residual (same-start duplicate headers in stage 1a,
    OPEN) -- a defect in how `element_idx` itself is computed, not in
    whether this list is sorted by it."""
    name = ""
    for entry in a_entries:
        candidate = (entry.get("extracted_fields") or {}).get("name")
        if isinstance(candidate, str) and candidate.strip():
            name = candidate
            break
    if not name and a_entries:
        name = a_entries[0].get("text") or ""
    return frozenset(tok.lower() for tok in _NAME_TOKEN_RE.findall(name)
                     if len(tok) >= _MIN_OWNER_NAME_TOKEN_LEN)


def _shares_owner_name(text: str, owner_name_tokens: frozenset[str]) -> bool:
    """True when `text` carries enough of the owner's own name tokens that
    it reads as the owner's OWN entry rather than a third party's -- at
    least two tokens, or the one token there is when the name has only
    one. Restored verbatim from the #833 baseline (82f3744) -- the #920
    fix keeps this ENTRY-level gate as one of two conjuncts an email must
    now satisfy (`_email_spared_by_owner_name`) rather than replacing it:
    on its own it is per-entry (the #920 leak: a whole References block
    sharing the owner's name spared every referee's contact in it), and on
    its own it is also too permissive at the SEGMENT level, which is what
    conjunct (b) below narrows."""
    if not owner_name_tokens:
        return False
    text_tokens = {tok.lower() for tok in _NAME_TOKEN_RE.findall(text)}
    return len(owner_name_tokens & text_tokens) >= min(2, len(owner_name_tokens))


#: Splits an email local part into `.`/`_`/`-`/digit-delimited segments
#: (#920 fix): "jane.doe77" -> ["jane", "doe", ""], "eduardo" -> ["eduardo"]
#: (one segment, the whole string -- no delimiter to split on). Matched
#: against `owner_name_tokens` by WHOLE-SEGMENT equality, never substring:
#: `_local_part_shares_owner_name`, the function this fix removes, tested
#: `token in local` -- a token anywhere in the local part, so an owner
#: token "edu" spared `eduardo@` (the #920 review finding) and an owner
#: token "lee" spared `kathleen@`. A local part carries the OWNER's own
#: address in a `first.last`/`flast`/`firstl` shape, where a name token is
#: always its own segment; a substring hit that is not also a whole
#: segment is, by construction, some OTHER word merely containing the
#: token as a fragment.
_LOCAL_PART_SEGMENT_RE = re.compile(r"[^a-z]+")


def _email_spared_by_owner_name(
    text: str, local: str, owner_name_tokens: frozenset[str],
) -> bool:
    """True only when BOTH the #833 baseline's entry-level gate
    (`_shares_owner_name`) and a per-value, whole-segment check on the
    email's OWN local part hold (#920 fix for the blind verifier's FAIL on
    `b0c5d9e`): `jane.doe@`, `jdoe@`, `doej@` style is the owner's own
    address, `kathleen@`/`doeringer@`/`kimberly.jones@` are not, even
    though each contains an owner token as a bare substring
    (`lee`/`doe`/`kim`).

    Requiring conjunct (a) as well as (b) is what makes the result a
    SUBSET of what the #833 baseline (82f3744) itself spared by name: the
    baseline's `_shares_owner_name` alone (conjunct (a)) is a superset of
    every email this function spares, so no email the baseline withheld
    can newly be spared here -- the #920 entry-wide leak
    (`test_a_references_entry_sharing_the_owner_s_name_still_withholds_a_third_party_s_contact`)
    stays fixed, because a referee's local part does not carry an owner
    name segment even when the surrounding entry does carry the owner's
    name."""
    if not _shares_owner_name(text, owner_name_tokens):
        return False
    segments = _LOCAL_PART_SEGMENT_RE.split(local.lower())
    return any(segment in owner_name_tokens for segment in segments)


def _third_party_contact_matches(
    text: str, owner: frozenset[str], owner_name_tokens: frozenset[str],
) -> list[PiiMatch]:
    """CAT_THIRD_PARTY_CONTACT spans in an Appendix-bound entry: an email or
    US phone shape that is not one of `owner`'s own contacts and is not a
    generic editorial mailbox -- an email is ADDITIONALLY spared when BOTH
    the entry as a whole reads as the owner's own AND the email's OWN local
    part carries one of the owner's name tokens as a whole segment
    (`_email_spared_by_owner_name`).

    #920 review round 1 (the PR's headline finding): the exemption used to
    be PER ENTRY ONLY -- any value in an entry that also carried the
    owner's own name anywhere in its text was spared whole, so a
    References entry with a "References for <owner>" heading, letterhead
    line or footer leaked every referee's phone and email in that same
    block. #920 review round 2 (the blocker on the round-1 fix): making the
    check per-value by switching to a BARE SUBSTRING test on the local
    part alone (dropping the entry-level gate entirely) went too far the
    other way -- `_local_part_shares_owner_name`'s `any(token in local ...)`
    spared `eduardo@` on the owner's own harvested "edu" fragment and
    `kathleen@`/`doeringer@`/`kimberly.jones@` on "lee"/"doe"/"kim", none
    of which the #833 baseline (82f3744) spared. The exemption now
    requires BOTH conjuncts, so it can only ever spare a SUBSET of what the
    baseline itself spared by name (see `_email_spared_by_owner_name`'s
    docstring) -- the round-1 fix (a phone has no per-value name signal at
    all, so a phone can only ever be spared by being in `owner`) is
    unchanged.

    # ponytail: the entry-level gate is a coarse two-token heuristic, not a
    # name parser, and the segment gate does not know a name from any
    # other word -- ceiling, narrowed by this two-conjunct rewrite from the
    # round-1 per-value-only version's bare substring test: an email is
    # now wrongly spared only when BOTH (a) its own entry already meets
    # the baseline's two-token gate (`_shares_owner_name`) AND (b) its
    # local part has a whole `.`/`_`/`-`/digit-delimited SEGMENT equal to
    # an owner token -- e.g. a same-surname relative sharing the owner's
    # entry, or, when `_owner_name_tokens` had no `extracted_fields['name']`
    # and fell back to the whole first 'A' entry's text, a fallback token
    # that reads as a real word purely because the fallback tokenizer has
    # no concept of "label" or "domain fragment" (real fallback token sets
    # measured on the local corpus: `and`, `edu`, `com`, `gmail`, `email`,
    # `phone`, `number`, `address`, `name`, `this`, `some`, `text`, `room`,
    # `floor`) landing as a WHOLE segment of a third party's local part
    # rather than a substring inside a longer one. Conjunct (a) is
    # entry-text-wide, so under the fallback an email's own local part and
    # domain can supply both matching tokens by themselves (e.g.
    # `email.desk@example-state.edu` matches fallback tokens `email` and
    # `edu`), satisfying (a) with no other owner-referencing text in the
    # entry at all -- corpus incidence today is 0. Upgrade path unchanged
    # in kind from the #833/#920-round-1 docstrings: restrict the fallback
    # in `_owner_name_tokens` to a leading name-shaped run, if a real CV
    # ever shows this narrower false negative
    # (`test_email_local_part_sharing_a_fallback_word_as_its_own_segment_is_still_spared`).
    """
    found: list[PiiMatch] = []
    for m in _THIRD_PARTY_EMAIL_RE.finditer(text):
        value = m.group()
        local = value.split("@", 1)[0].lower()
        if (value.lower() in owner
                or local in _GENERIC_MAILBOX_LOCAL_PARTS
                or _email_spared_by_owner_name(text, local, owner_name_tokens)):
            continue
        found.append(PiiMatch(m.start(), m.end(), CAT_THIRD_PARTY_CONTACT))
    for m in _THIRD_PARTY_PHONE_RE.finditer(text):
        if _phone_digits(m.group()) in owner:
            continue
        found.append(PiiMatch(m.start(), m.end(), CAT_THIRD_PARTY_CONTACT))
    return found


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


def _pii_field_value_hits(fields: Mapping, scope: str) -> dict[str, list[str]]:
    """Field key -> the PII categories its string VALUE matches at `scope`
    (#892): a field-first renderer prints the value whatever key it sits
    under ("award_name": "... O-1 Visa"), so the key-name check alone
    leaves it on the page beside a notice saying it was withheld."""
    hits: dict[str, list[str]] = {}
    for key, value in fields.items():
        if isinstance(value, str) and value:
            categories = [m.category for m in _pii_matches(value, scope)]
            if categories:
                hits[key] = categories
    return hits


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
    a_entries = entries_by_code.get(PERSONAL_DATA_CODE, [])
    owner_contacts = _owner_contacts(a_entries)
    owner_name_tokens = _owner_name_tokens(a_entries)
    index = -1
    for code, entries in entries_by_code.items():
        scope = _entry_scope(code, routed_codes)
        section = _section_label(code, routed_codes, section_names)
        for entry in entries:
            index += 1
            raw_text = entry.get("text", "") or ""
            matches = _pii_matches(raw_text, scope) if raw_text else []
            # #833: an Appendix-bound entry also gets the value-shape
            # third-party-contact check -- scope already excludes every
            # routed content code (SCOPE_ALL_CODES). #920 review: an
            # explicit `code != PERSONAL_DATA_CODE` guard used to also
            # exclude 'A' itself, disclosed in the original PR as a proven
            # -equivalent mutant (every value in an 'A' entry's own text is
            # harvested into `owner_contacts` below, from that SAME set of
            # entries, so it is always already a member of `owner` by the
            # time this runs against it -- removing the guard cannot
            # change what an 'A' entry renders). Deleted rather than kept
            # "for readability": a proven-dead branch is dead weight in a
            # PII-withholding path. The equivalence is uniform across every
            # 'A' entry, not just the first: a second, distinct 'A' entry
            # harvests into `owner_contacts` from that same
            # `entries_by_code[PERSONAL_DATA_CODE]` loop, so the guard's
            # removal is a no-op there too, for the identical reason --
            # not a new case the removal additionally "covers".
            if raw_text and scope == SCOPE_PERSONAL_AND_APPENDIX:
                matches = _merge_matches(list(matches) + _third_party_contact_matches(
                    raw_text, owner_contacts, owner_name_tokens))

            fields = entry.get("extracted_fields") or {}
            pii_keys = [(k, _pii_field_key_category(k)) for k in fields]
            pii_keys = [(k, c) for k, c in pii_keys if c is not None]

            pii_key_names = {k for k, _ in pii_keys}
            value_hits = {
                k: cats for k, cats in _pii_field_value_hits(fields, scope).items()
                if k not in pii_key_names}

            if not matches and not pii_keys and not value_hits:
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
            # #892: a PII value under a non-PII key is dropped like a
            # PII-keyed field. The same item already recorded from `text`
            # (the entry's raw text usually carries the very same phrase)
            # is not counted twice.
            noticed = {m.category for m in matches}
            for key, categories in value_hits.items():
                del fields[key]
                for category in categories:
                    if category not in noticed:
                        noticed.add(category)
                        result.withheld.append(
                            WithheldItem(category, section, index))

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
