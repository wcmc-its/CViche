"""Protected personal data: the withhold policy, detection, and provenance.

A data-governance concern, not a normalization one. Nothing here cleans a
value; it decides whether a value may be rendered at all. Kept apart from
the rest of ``normalization`` deliberately -- a change to an author-name or
separator rule must not be able to widen or narrow what counts as protected
data, and the two now cannot share an edit by accident.

Three things live here, in this order:

1. ``WITHHOLD_POLICY`` -- ONE data table of every category the pipeline
   withholds: the human label the notice and the Word comment use, the
   regex that recognizes it, the SCOPE it applies at, and who decided it.
   Flipping a #821 decision is a one-row edit.
2. The match engine (``_pii_matches`` / ``_pii_fragments``): unanchored
   label matching over one text, scoped by where the entry is going.
3. ``_from_pii_fragment``: value PROVENANCE for the Personal Data block --
   whether an extracted value was lifted out of a matched fragment.

#820 widened detection twice over dev's original #473/#532 predicate, which
anchored every label at the START of a fragment (`^\\s*...`) and only split
fragments on ``\\n``, ``\\t``, ``|`` or 3+ spaces. That missed a label sitting
after ANOTHER label in the same fragment ("Personal Information: Date of
Birth: ...") and everything after a semicolon or comma ("Nationality: X;
Date of Birth: ..."). Matching is now UNANCHORED: a label counts wherever
it sits, provided the text immediately before it (back to the previous hard
delimiter) is empty or ends in one of the separators the issue names --
``;``, ``,``, ``:``, ``(``, or start-of-fragment. A strict superset of the
old anchored behaviour, so every #473/#532 fragment-initial match still
fires; it does not relax the per-label shape checks (colon terminator,
colonless date/SSN value, hyphen-guarded stem) those two issues fought for.

SCOPE (#820 round 2). The first all-codes pass stripped ANY entry whose
text matched a label, so a book-chapter title "Children: Research, Practice
and Policy" (a #473 negative control) was deleted from a delivered document
and 14 corpus CVs carried a "withheld" notice when nothing had left the
page (a `DEA number:` label on a Licensure entry whose value still rendered
from `license_number`). Every rule therefore carries a scope:

- ``SCOPE_ALL_CODES`` -- unambiguous as a label wherever it appears: a
  publication title never starts with "Date of Birth:". Applied to every
  entry, every taxonomy code.
- ``SCOPE_PERSONAL_AND_APPENDIX`` -- ambiguous as a title word ("Children:",
  "Gender: A Review...", "Health: ..."). Applied only to A-coded entries
  and to entries that will render in the Appendix (a code `generate()`
  does not route to a section) -- the two places a stray personal-data
  line lands, and the places where #473 already accepted the colon
  trade-off. A routed content entry (S4, R, F1, ...) never sees these.

The DEA rule is the worked example: a `DEA #:` line in the Appendix is
withheld, but an F1 (Licensure) entry -- routed, not A -- is out of THIS
row's scope, deliberately (see the row's own comment below). #821 (settled
2026-09-14) withholds the template's DEA slot too, but
`stage6/sections/licensure.py` enforces that decision at render time
rather than through this row's scope, because scope alone cannot: some
entries classify as DEA by a number SHAPE with no "DEA" text at all.
"""
import re
from dataclasses import dataclass
from typing import NamedTuple


# ---------------------------------------------------------------------------
# The policy table
# ---------------------------------------------------------------------------

#: Applied to every entry regardless of taxonomy code.
SCOPE_ALL_CODES = "ALL_CODES"
#: Applied only to A-coded entries and entries headed for the Appendix.
SCOPE_PERSONAL_AND_APPENDIX = "PERSONAL_AND_APPENDIX"

#: Decided in #820 (the detector issue) -- settled.
DECIDED_820 = "#820"
#: Withheld by this pipeline's default pending the user's #821 decision.
DECIDED_821_PENDING = "#821 pending"
#: Paul's #821 decision comment (2026-09-14): DEA template slot and home
#: address/phone both move from render to withhold-with-notice.
DECIDED_821 = "https://github.com/wcmc-its/CViche/issues/821#issuecomment-5671621909"

# Category labels -- the vocabulary the withheld notice and the Word comment
# speak in. Named once so a row, a field-key rule and a comment line cannot
# spell the same category three ways.
CAT_DATE_OF_BIRTH = "date of birth"
CAT_PLACE_OF_BIRTH = "place of birth"
CAT_BIRTH = "date or place of birth"
CAT_SSN = "social security number"
CAT_PASSPORT = "passport number"
CAT_ALIEN_REGISTRATION = "alien registration number"
CAT_DRIVERS_LICENSE = "driver's license"
CAT_MARITAL_STATUS = "marital status"
CAT_SPOUSE = "spouse"
CAT_EMERGENCY_CONTACT = "emergency contact"
CAT_VISA = "visa / immigration status"
CAT_SALARY = "salary / honorarium"
CAT_CHILDREN = "children / dependents"
CAT_FAMILY = "family"
CAT_AGE = "age"
CAT_GENDER = "gender"
CAT_RELIGION = "religion"
CAT_ETHNICITY = "ethnicity"
CAT_VETERAN = "veteran status"
CAT_DISABILITY = "disability"
CAT_HEALTH = "health"
CAT_BLOOD_TYPE = "blood type"
CAT_DEA = "DEA number"
CAT_HOME_CONTACT = "home address / phone"
# #833: a third party's contact data in an unlabelled Appendix-bound entry
# (a free-form References block -- "Name, Title, Institution" / phone /
# email, no label WITHHOLD_POLICY can key on). Not a WithholdRule row: the
# rule needs per-CV OWNER-CONTACT context (which email/phone is the CV
# owner's own) that a scope-only row cannot express, so it is applied in
# `stage6/pii_pass.py` directly rather than through this table.
CAT_THIRD_PARTY_CONTACT = "third-party contact"


@dataclass(frozen=True)
class WithholdRule:
    """One row of ``WITHHOLD_POLICY``.

    ``label`` is a verbose-mode regex alternative for the LABEL (the colon
    is appended by the engine, once, so every label row shares the same
    terminator rule); ``shape`` is a complete regex for a VALUE shape that
    needs no label at all (a bare SSN, "O-1 Visa"). A row carries exactly
    one of the two. ``anchored`` makes a shape row obey the label rows'
    position rule -- fragment-initial or after a separator, extent to the
    next hard delimiter -- for a phrase that is a label without a colon
    ("Married to <name>"): the same words inside prose ("a method married
    to clinical judgement", three farm abstracts) are not a label.
    """
    category: str
    scope: str
    decided_by: str
    label: str | None = None
    shape: str | None = None
    anchored: bool = False


# Building blocks shared by several rows -- kept as plain strings so a row
# reads as data, not code.

_MONTH_NAMES = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?"
    r"|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)

# Full-date shapes only (month/day/year all present) -- deliberately NOT a
# bare year, so the single-space separator below cannot turn "the clinic,
# born 1970, ..." into a false positive. #820 adds the day-month-year order
# ("2 January 1970") to the month-day-year order #532 already had.
_FULL_DATE_VALUE = (
    r"(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
    r"|" + _MONTH_NAMES + r"\s+\d{1,2},?\s*\d{4}"
    r"|\d{1,2}\s+" + _MONTH_NAMES + r",?\s*\d{4})"
)

# Every SSN spelling #532/#820 catch behind a colon-less separator: dashed,
# space-grouped, and no-separator-at-all -- "SSN 123-45-6789" and "Social
# Security No. 123 45 6789" are both the issue comment's own examples.
_SSN_WIDE_VALUE = r"\d{3}[\s-]?\d{2}[\s-]?\d{4}"

_YEAR_VALUE = r"\d{4}"

# A bare SSN, label or not (#820 comment). The distinguishing shape from a
# phone number (3-3-4) is the middle group's width. Guarded by "not
# digit-or-hyphen adjacent" on BOTH sides rather than `\b`, which let it
# match three groups of a five-group ISBN-13 (24 false hits on one CV's
# book-chapter bibliography during the #820 corpus scan).
_BARE_SSN_SHAPE = r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])"

# #833: a bare US phone value (3-3-4), every separator the corpus's
# References-block shape actually uses -- hyphen, dot, space or a
# parenthesised area code -- with an optional leading "+1". Guarded the
# same way as `_BARE_SSN_SHAPE` (digit/hyphen adjacency, not `\b`). The
# middle group's WIDTH is what keeps this distinct from an SSN: an SSN's
# 3-2-4 shape never has three contiguous digits in the position this
# pattern's second group requires, so `123-45-6789` still classifies as
# CAT_SSN only, never as a phone (`test_bare_ssn_is_not_also_a_phone`).
#
# #920 review: every separator here is optional EXCEPT the one right
# before the final four digits (`\d{3}[\s.-]\d{4}` has no trailing `?`,
# unlike the three separators before it), so an unpunctuated
# `2125550100` never matches while every punctuated style does. That is
# DELIBERATE, not an oversight: a fully unpunctuated 10-digit run collides
# with grant numbers, accession numbers and other bare identifiers that
# show up in a CV, and this pattern has no label or context to tell those
# apart from a phone number the way `WITHHOLD_POLICY`'s labelled rows can.
# Widening to an optional final separator is a one-character change if a
# real CV ever shows an unpunctuated phone in this exact (Appendix,
# not-the-owner's) shape.
_BARE_PHONE_SHAPE = (
    r"(?<![\d-])(?:\+1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]\d{4}(?![\d-])"
)

# #833: one conservative email shape -- the issue's own spelling. The
# local-part class (`[\w.+-]+`) is intentionally permissive -- it can
# match into adjacent text with no delimiter under Unicode-aware `\w` --
# because over-redaction is the preferred failure direction here: a
# redaction boundary that over-matches costs a little residual text, one
# that under-matches leaks a value.
_BARE_EMAIL_SHAPE = r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"

# The stem's lookbehind is not a plain \b: a hyphen IS a word boundary, so
# \b let "Foreign-born – 2015" match on its "born" half (#532 round 2).
_STEM_GUARD = r"(?<![\w\-–—])"

# Colon-less separators (#532): a dash, a tab, or 2+ spaces ahead of ANY
# shaped value including a bare year; a SINGLE space (#820) only ahead of a
# full date or an SSN -- shapes distinctive enough not to occur in prose.
_WIDE_SEP = r"\s* (?: [-–—] \s* | \t \s* | \s{2,} )"
_SINGLE_SPACE_SEP = r"[ ](?!\s)"

_DOB_STEM = (
    r"(?: date \s* of \s* birth | birth \s*-? \s* date | birthdate"
    r"  | born (?: \s+ (?: on | in ) )? | d\.?o\.?b\.? )"
)
_SSN_STEM = r"(?: social \s* security (?: \s* (?: number | no\.? ) )? | ssn )"


def _colonless(stem: str, wide_value: str, single_space_value: str) -> str:
    """A colon-less label+value shape: `stem` then either a wide separator
    and `wide_value`, or a single space and `single_space_value`."""
    return (
        _STEM_GUARD + stem
        + r"(?:" + _WIDE_SEP + r"(?:" + wide_value + r")"
        + r"|" + _SINGLE_SPACE_SEP + r"(?:" + single_space_value + r"))"
    )


#: THE table. Row order is match-priority order within a scope: the engine
#: tries every row and merges overlapping spans, keeping the category of
#: the leftmost (then first-listed) match, so "Birth date and birth place:"
#: is one date-of-birth fragment, not two.
WITHHOLD_POLICY: tuple[WithholdRule, ...] = (
    # --- unambiguous as a label: every code -------------------------------
    WithholdRule(CAT_DATE_OF_BIRTH, SCOPE_ALL_CODES, DECIDED_820, label=r"""
        date \s* of \s* birth (?: \s* \( d\.?o\.?b\.?\) )?
      | birth \s*-? \s* date (?: \s+ and \s+ birth \s*-? \s* place )?
      | birthdate (?: \s+ and \s+ birthplace )?
      | birthday
      | year \s* of \s* birth
      | d\.?o\.?b\.? \s* / \s* p\.?o\.?b\.?
      | d\.?o\.?b\.?
      | born \s+ (?: on | in )
      | fecha \s* de \s* nacimiento
      | date \s* de \s* naissance
      | geburtsdatum
    """),
    WithholdRule(CAT_DATE_OF_BIRTH, SCOPE_ALL_CODES, DECIDED_820, shape=_colonless(
        _DOB_STEM,
        wide_value=_FULL_DATE_VALUE + r"|" + _YEAR_VALUE,
        single_space_value=_FULL_DATE_VALUE)),
    WithholdRule(CAT_PLACE_OF_BIRTH, SCOPE_ALL_CODES, DECIDED_820, label=r"""
        place \s* of \s* birth | birth \s*-? \s* place | birthplace
    """),
    WithholdRule(CAT_SSN, SCOPE_ALL_CODES, DECIDED_820, label=r"""
        social \s* security (?: \s* (?: number | no\.? | \# ) )?
      | ss \s* \#
      | ssn (?: \s* \( last \s* 4 \) )?
    """),
    WithholdRule(CAT_SSN, SCOPE_ALL_CODES, DECIDED_820, shape=_colonless(
        _SSN_STEM, wide_value=_SSN_WIDE_VALUE, single_space_value=_SSN_WIDE_VALUE)),
    WithholdRule(CAT_SSN, SCOPE_ALL_CODES, DECIDED_820, shape=_BARE_SSN_SHAPE),
    WithholdRule(CAT_PASSPORT, SCOPE_ALL_CODES, DECIDED_820,
                 label=r"passport (?: \s* (?: number | no\.? ) )?"),
    WithholdRule(CAT_ALIEN_REGISTRATION, SCOPE_ALL_CODES, DECIDED_820,
                 label=r"alien \s* registration (?: \s* (?: number | no\.? ) )?"),
    WithholdRule(CAT_DRIVERS_LICENSE, SCOPE_ALL_CODES, DECIDED_820,
                 label=r"driver.?s? \s* licen[sc]e"),
    WithholdRule(CAT_MARITAL_STATUS, SCOPE_ALL_CODES, DECIDED_820,
                 label=r"marital \s* status"),
    WithholdRule(CAT_SPOUSE, SCOPE_ALL_CODES, DECIDED_820, label=r"""
        spouse (?: [’'] s )? (?: \s* name )?
      | wife (?: [’'] s )? (?: \s* name )?
      | husband (?: [’'] s )? (?: \s* name )?
    """),
    # "Married to <name>" carries no colon and no shaped value -- the phrase
    # is the whole signal, so it is anchored like a label. Guarded like
    # `born`: "Newly-married to" cannot glue onto it from the wrong side.
    WithholdRule(CAT_SPOUSE, SCOPE_ALL_CODES, DECIDED_820,
                 shape=_STEM_GUARD + r"married \s+ to \b", anchored=True),
    WithholdRule(CAT_EMERGENCY_CONTACT, SCOPE_ALL_CODES, DECIDED_821_PENDING,
                 label=r"emergency \s* contact"),
    WithholdRule(CAT_VISA, SCOPE_ALL_CODES, DECIDED_821_PENDING,
                 label=r"visa (?: \s* status )? | immigration \s* status"),
    # The bare value shape: "O-1 Visa", "H-1B visa", "J-1 Visa" (web228's
    # "..., O-1 Visa." reached the Appendix with no label to match).
    WithholdRule(CAT_VISA, SCOPE_ALL_CODES, DECIDED_821_PENDING,
                 shape=r"\b[A-Z]{1,2}-\d{1,2}[A-Z]?\s+visa\b"),
    WithholdRule(CAT_SALARY, SCOPE_ALL_CODES, DECIDED_821_PENDING,
                 label=r"salary | honorarium"),
    # --- ambiguous as a title word: A-coded and Appendix-bound only ---------
    WithholdRule(CAT_BIRTH, SCOPE_PERSONAL_AND_APPENDIX, DECIDED_820, label=r"born"),
    WithholdRule(CAT_CHILDREN, SCOPE_PERSONAL_AND_APPENDIX, DECIDED_820,
                 label=r"children (?: [’'] s \s* names? )? | dependents?"),
    WithholdRule(CAT_FAMILY, SCOPE_PERSONAL_AND_APPENDIX, DECIDED_820, label=r"family"),
    WithholdRule(CAT_AGE, SCOPE_PERSONAL_AND_APPENDIX, DECIDED_820, label=r"age"),
    WithholdRule(CAT_GENDER, SCOPE_PERSONAL_AND_APPENDIX, DECIDED_820, label=r"gender"),
    WithholdRule(CAT_RELIGION, SCOPE_PERSONAL_AND_APPENDIX, DECIDED_820, label=r"religion"),
    WithholdRule(CAT_ETHNICITY, SCOPE_PERSONAL_AND_APPENDIX, DECIDED_820, label=r"ethnicity"),
    WithholdRule(CAT_VETERAN, SCOPE_PERSONAL_AND_APPENDIX, DECIDED_820,
                 label=r"veteran (?: \s* status )?"),
    WithholdRule(CAT_DISABILITY, SCOPE_PERSONAL_AND_APPENDIX, DECIDED_820,
                 label=r"disability"),
    WithholdRule(CAT_HEALTH, SCOPE_PERSONAL_AND_APPENDIX, DECIDED_820, label=r"health"),
    WithholdRule(CAT_BLOOD_TYPE, SCOPE_PERSONAL_AND_APPENDIX, DECIDED_820,
                 label=r"blood \s* type"),
    # #821 (settled 2026-09-14): withhold with notice, including the
    # template's own DEA slot. This row's SCOPE stays
    # PERSONAL_AND_APPENDIX -- it still never reaches an F1 (Licensure)
    # entry's TEXT, deliberately: `_classify_licensure_entry`
    # (stage6/sections/licensure.py) checks `license_type`/raw text before
    # falling back to a number-SHAPE tiebreak, and only when the entry
    # names no state. Widening this row to SCOPE_ALL_CODES would have the
    # pre-render pass cut "DEA" out of the entry's text before that
    # classifier ever runs; a state-bearing DEA entry would then match no
    # label at all, skip the shape tiebreak (state present), fall through
    # to KIND_LICENSE and render the real number as an ordinary licence
    # row -- worse than doing nothing, and unreachable by a text-only
    # fix since some DEA entries classify by NUMBER SHAPE ALONE with no
    # "DEA" text at all (`test_shape_fallback_dea_two_letters_seven_
    # alphanumeric`), which this row could never match either way.
    # `_resolve_licensure` (licensure.py) withholds every entry it
    # classifies as DEA -- by label OR by shape -- before the docx is
    # written, and records it on the same `_pii_result.withheld` list this
    # row's own matches feed, so the notice/comment mechanism is not
    # duplicated. A stray `DEA #:` line in the Appendix is still withheld
    # by this row exactly as before.
    WithholdRule(CAT_DEA, SCOPE_PERSONAL_AND_APPENDIX, DECIDED_821,
                 label=r"dea (?: \s* (?: \# | number | registration ) )?"),
    # #821: a home address or home phone label anywhere in scope (an
    # A-coded entry, or an Appendix-bound orphan whose ONLY content is a
    # home phone -- the template has no phone row for it, so before this
    # row such an orphan reached the Appendix unfiltered). A value that
    # reaches `sections/personal_data.py` by a path with no text label at
    # all (a structured `address: {home_address: ..., office_address:
    # ...}` dict) carries no fragment for this row to match either --
    # `_fill_personal_data` withholds `home_address`/`home_phone`
    # unconditionally at write time for that reason, same as DEA above.
    WithholdRule(CAT_HOME_CONTACT, SCOPE_PERSONAL_AND_APPENDIX, DECIDED_821,
                 label=r"home \s* (?: address | phone | telephone | tel\.? )"),
)

# --- what is NOT in the table, and why (decided in DECIDED_821) -------------
# citizenship / nationality -- render;  personal email -- render (both the
# owner's #821 decision, so no CAT_* constant: a category constant exists
# to be shared by a policy row, a field-key rule and a comment line, and
# neither has one); NPI -- a public identifier, render.  Third-party
# contacts in an unlabelled References section -- withheld by #833's
# value-shape rule in `stage6/pii_pass.py`, not by a row of this table (see
# CAT_THIRD_PARTY_CONTACT above).


# Stage-4 field KEYS that name protected data outright (the label pattern
# cannot see them: stage 4 leaves extracted_fields empty for most PII
# entries but names some explicitly -- marital_status_spouse, birthplace --
# where the source label is unusual). Anchored whole-key match, so
# 'institutional_email' and friends can never hit; every person stem takes
# the same optional suffix, so 'wife_name' is caught wherever 'spouse_name'
# is. Each stem carries its category so the notice can name it.
_PII_FIELD_KEY_RULES: tuple[tuple[str, str], ...] = (
    (r"date_of_birth | birth_?date | dob", CAT_DATE_OF_BIRTH),
    (r"birth_?place | place_of_birth", CAT_PLACE_OF_BIRTH),
    (r"marital_status (?:_\w+)?", CAT_MARITAL_STATUS),
    (r"spouse (?:_\w+)? | wife (?:_\w+)? | husband (?:_\w+)?", CAT_SPOUSE),
    (r"children (?:_\w+)? | dependents? (?:_\w+)?", CAT_CHILDREN),
    (r"ssn | social_security\w*", CAT_SSN),
)

_PII_FIELD_KEY_RE = re.compile(
    r"^(?:.*_)?(?:" + "|".join(stem for stem, _ in _PII_FIELD_KEY_RULES) + r")$",
    re.X | re.I)

_PII_FIELD_KEY_CATEGORY_RES: tuple[tuple[re.Pattern, str], ...] = tuple(
    (re.compile(r"^(?:.*_)?(?:" + stem + r")$", re.X | re.I), category)
    for stem, category in _PII_FIELD_KEY_RULES
)


def _pii_field_key_category(key: str) -> str | None:
    """The category a stage-4 field key names, or None for an ordinary key."""
    for pattern, category in _PII_FIELD_KEY_CATEGORY_RES:
        if pattern.match(key):
            return category
    return None


# ---------------------------------------------------------------------------
# The match engine
# ---------------------------------------------------------------------------

# Hard fragment boundaries: a label after any of these starts a NEW fragment
# scope for the "is this label preceded by nothing" check, and also caps how
# far a matched fragment extends. #820 adds `;` to the original `\n`, `\t`,
# `|`, 3+-space set. `:` and `,` are NOT hard boundaries -- a fragment must
# still capture its own value after the colon -- they are ALLOWED PRECEDING
# punctuation in `_boundary_ok`, the "follows a separator" half of the rule.
_PII_FRAGMENT_SPLIT_RE = re.compile(r"[\n\t|;]|\s{3,}")


class WithheldItem(NamedTuple):
    """One thing the pre-render pass (or the Personal Data block's docx
    recovery) removed from what would have rendered. Defined here, beside
    the policy whose `category` it names, so `sections/personal_data.py`
    can record one without importing `stage6/pii_pass.py` back."""
    category: str
    #: The WCM section the item would have rendered in ("Personal Data",
    #: "Licensure", ...), or "Appendix".
    section_label: str
    #: Position of the entry in `generate()`'s flattened entry order; None
    #: for a value recovered straight from the source docx (no entry).
    entry_index: int | None


class PiiMatch(NamedTuple):
    """One protected-data span of a text: half-open offsets and the policy
    category that matched. Overlapping spans are merged before return."""
    start: int
    end: int
    category: str


def _label_pattern(rule: WithholdRule) -> re.Pattern:
    """The compiled opener for a label row: the row's alternatives, then
    the shared `\\s*:` terminator, appended ONCE for the whole group."""
    return re.compile(r"(?:" + str(rule.label) + r")\s*:", re.X | re.I)


def _shape_pattern(rule: WithholdRule) -> re.Pattern:
    return re.compile(str(rule.shape), re.X | re.I)


_COMPILED_POLICY: tuple[tuple[WithholdRule, re.Pattern], ...] = tuple(
    (rule, _label_pattern(rule) if rule.label is not None else _shape_pattern(rule))
    for rule in WITHHOLD_POLICY
)


def _rules_for_scope(scope: str) -> tuple[tuple[WithholdRule, re.Pattern], ...]:
    """The rows in force at `scope`: ALL_CODES rows always; the ambiguous
    rows only for the Personal Data / Appendix scope."""
    if scope == SCOPE_PERSONAL_AND_APPENDIX:
        return _COMPILED_POLICY
    if scope == SCOPE_ALL_CODES:
        return tuple((r, p) for r, p in _COMPILED_POLICY if r.scope == SCOPE_ALL_CODES)
    raise ValueError(f"unknown withhold scope {scope!r}")


# Every colon-terminated label in the table, anchored at fragment start --
# the original #473 shape, kept for the consumers that import it by name
# (`stage_6_word_template`, `normalization/__init__`). Built from the SAME
# rows, so it cannot drift from the policy.
_PII_LABEL_RE = re.compile(
    r"^\s*(?:" + "|".join(
        r"(?:" + str(r.label) + r")" for r in WITHHOLD_POLICY if r.label is not None
    ) + r")\s*:",
    re.X | re.I,
)


# A list marker is not a word: "• Children: ..." (the Appendix renders
# every bullet this way) and "12. Date of Birth: ..." are fragment-initial
# labels for the doctor lint's purposes, which reads RENDERED text.
_LIST_MARKER_RE = re.compile(r"^(?:[•·\-–—*]|\d{1,3}[.)])$")


def _boundary_ok(prefix_since_last_delim: str) -> bool:
    """True when a label match is fragment-initial OR immediately follows
    one of the issue's separators: `,`, `:`, `(`, or start-of-line (`;` is
    a hard split upstream, so it is covered by the empty-prefix case). A
    bare list marker counts as start-of-line."""
    stripped = prefix_since_last_delim.strip()
    return (stripped == "" or stripped[-1] in ",:("
            or bool(_LIST_MARKER_RE.match(stripped)))


def _label_spans(text: str, pattern: re.Pattern) -> list[tuple[int, int]]:
    """(start, end) of every fragment `pattern` opens in `text`: from the
    opener's own start to the next hard delimiter (or end of string), kept
    only where `_boundary_ok` accepts the text since the previous delimiter."""
    spans = []
    for m in pattern.finditer(text):
        start = m.start()
        prev_delim_end = 0
        for d in _PII_FRAGMENT_SPLIT_RE.finditer(text, 0, start):
            prev_delim_end = d.end()
        if not _boundary_ok(text[prev_delim_end:start]):
            continue
        nxt = _PII_FRAGMENT_SPLIT_RE.search(text, start)
        end = nxt.start() if nxt else len(text)
        spans.append((start, end))
    return spans


def _merge_matches(spans: list[PiiMatch]) -> list[PiiMatch]:
    """Overlapping or touching spans merged into one, keeping the category
    of the leftmost (then first-listed) match, in document order."""
    spans.sort(key=lambda s: (s.start, -s.end))
    merged: list[PiiMatch] = []
    for span in spans:
        if merged and span.start <= merged[-1].end:
            last = merged[-1]
            merged[-1] = PiiMatch(last.start, max(last.end, span.end), last.category)
        else:
            merged.append(span)
    return merged


def _pii_matches(text: str | None, scope: str = SCOPE_PERSONAL_AND_APPENDIX) -> list[PiiMatch]:
    """Every protected-data span of `text` under the rows in force at
    `scope`, merged, in document order. A label row's span runs from the
    label to the next hard delimiter (its value included); a shape row's
    span is the matched value itself."""
    text = str(text or "")
    if not text:
        return []
    found: list[PiiMatch] = []
    for rule, pattern in _rules_for_scope(scope):
        if rule.label is not None or rule.anchored:
            found.extend(PiiMatch(s, e, rule.category)
                         for s, e in _label_spans(text, pattern))
        else:
            found.extend(PiiMatch(m.start(), m.end(), rule.category)
                         for m in pattern.finditer(text))
    return _merge_matches(found)


def _pii_fragment_spans(text: str) -> list[tuple[int, int]]:
    """(start, end) offsets of every PII fragment in `text` at the full
    (Personal Data / Appendix) scope -- the offsets behind `_pii_fragments`."""
    return [(m.start, m.end) for m in _pii_matches(text)]


def _pii_fragments(text: str | None, scope: str = SCOPE_PERSONAL_AND_APPENDIX) -> list[str]:
    """The fragments of an entry that carry protected personal data.

    Defaults to the FULL vocabulary: this is what the Personal Data block,
    the Appendix, the doctor lint's Personal Data / Appendix blocks and the
    corpus measurement script ask for. A routed content entry passes
    `SCOPE_ALL_CODES` (see the module docstring)."""
    text = str(text or "")
    return [text[m.start:m.end] for m in _pii_matches(text, scope)]


# ---------------------------------------------------------------------------
# Value provenance
# ---------------------------------------------------------------------------

def _squash(text: object) -> str:
    """Whitespace-FREE normalization for verbatim containment checks."""
    return re.sub(r"\s+", "", str(text or "")).lower()


def _collapse_whitespace(text: object) -> str:
    """Whitespace-COLLAPSED, case-folded normalization.

    Deliberately not `_squash`: that one deletes whitespace outright, which
    also deletes the token boundaries `_from_pii_fragment` has to align on.
    """
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


# "Alphanumeric" for the edge test: a word character that is not an
# underscore, so it is Unicode-aware without treating "_" as part of a token.
_ALNUM_CHAR = r"[^\W_]"


def _pii_containment_pattern(value: object) -> re.Pattern | None:
    """A whitespace-insensitive, token-aligned matcher for one extracted value.

    None only when there is nothing to match -- an absent or blank value.
    Whitespace must not decide the match: the value and the fragment come
    from different places and stage 4 re-spaces what it extracts, so the
    value's characters are joined by "any whitespace, or none". The edge
    condition is the narrowing -- an alphanumeric first or last character may
    not sit against another alphanumeric character in the fragment, so a
    match cannot begin or end in the middle of one of the fragment's own
    tokens. There is deliberately no minimum value length; the tests named in
    `_from_pii_fragment` are where that is settled.
    """
    squashed = _squash(value)
    if not squashed:
        return None
    body = r"\s*".join(re.escape(c) for c in squashed)
    lead = f"(?<!{_ALNUM_CHAR})" if squashed[0].isalnum() else ""
    trail = f"(?!{_ALNUM_CHAR})" if squashed[-1].isalnum() else ""
    return re.compile(lead + body + trail)


def _from_pii_fragment(value: object, pii_fragments: list[str]) -> bool:
    """Whether an extracted value's text was taken out of a PII fragment.

    Deny by value PROVENANCE, not by entry. Dropping a whole entry that
    contains a PII label is right in the appendix, where the entry renders
    nothing so discarding it is free -- but it is wrong here, where the same
    entry is actively supplying live contact data: #472's table is three CVs
    losing five real values -- an office address, an office phone, a work
    email and two home addresses -- to a birth date in the same block.

    This is a containment test and not true provenance, and it cannot be made
    into one here: real provenance would need stage 4 to record the source
    span each value was lifted from, and stage 4 emits raw LLM JSON against
    no schema and no spans. So it is narrowed the one way that gives nothing
    back -- the match must be TOKEN-ALIGNED, so a value is not denied because
    its characters run through the middle of a date or an SSN ("23-45-6789"
    out of "123-45-6789"). A minimum value length was tried as a second
    narrowing and reverted: it is not separable from the protected values
    themselves. Both, and two of that table's five values, are pinned by
    `test_stage6_pii_value_provenance.py` and by
    `test_stage6_personal_data_recovery.py::
    test_real_contact_data_survives_an_entry_that_also_carries_pii`.

    No corpus run can see this predicate move in either direction.
    `measure_normalization_claims.py --only pii` over the 66-CV farm:

        A-coded entries                  : 319
        entries carrying a PII fragment  : 0
        stage-4 entries at any code      : 10737
        ... with a PII label or field key: 0

    so a corpus-green result is not evidence about it, and the three-CV
    figure above is not re-derivable from the artifacts the farm now holds.
    """
    pattern = _pii_containment_pattern(value)
    if pattern is None:
        return False
    return any(pattern.search(_collapse_whitespace(f)) for f in pii_fragments)


def _pii_category_of(value: object, text: str) -> str | None:
    """The policy category of the fragment of `text` that `value` was taken
    out of (by `_from_pii_fragment`'s own containment test), or None when
    no fragment supplied it. Lets a value denied by provenance be reported
    under the same category name the pre-render pass uses. Full scope: the
    only caller is the Personal Data block's docx recovery path."""
    text = str(text or "")
    for m in _pii_matches(text):
        if _from_pii_fragment(value, [text[m.start:m.end]]):
            return m.category
    return None
