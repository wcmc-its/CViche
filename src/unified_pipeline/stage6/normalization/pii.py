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
# #847: the day may carry an ordinal suffix ("April 1st, 1970", "21st of
# April 1970"); without it only the year matched and the month and day
# reached the LLM (run 6TJNBQ).
_ORDINAL_DAY = r"\d{1,2}(?:st|nd|rd|th)?"
_FULL_DATE_VALUE = (
    r"(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
    r"|" + _MONTH_NAMES + r"\s+" + _ORDINAL_DAY + r",?\s*\d{4}"
    r"|" + _ORDINAL_DAY + r"\s+(?:of\s+)?" + _MONTH_NAMES + r",?\s*\d{4})"
)

# ISO (yyyy-mm-dd) and dot-separated (dd.mm.yyyy) date shapes -- #847
# residual round 3: `_FULL_DATE_VALUE` only accepts `/` or `-` as the
# separator, so a colonless dotted DOB ("Born 12.03.1970") matched no
# value shape at all and the whole colonless row failed to fire -- not
# just the pre-LLM scrub, the render-time match too. Defined here (not
# only where the pre-LLM scrub used to define them) so both the colonless
# policy row below and `_PRE_LLM_DATE_RE` share one definition (#1.5).
_ISO_DATE_VALUE = r"\d{4}-\d{1,2}-\d{1,2}"
_DOTTED_DATE_VALUE = r"\d{1,2}\.\d{1,2}\.\d{2,4}"
_WHOLE_DATE_VALUE = _ISO_DATE_VALUE + r"|" + _DOTTED_DATE_VALUE + r"|" + _FULL_DATE_VALUE

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

# #1071: a template's DOB label can carry a parenthetical before its colon
# -- a format hint ("Date of Birth (mm/dd/yyyy):") or the value itself
# ("Birth Date (01/02/1970):", nothing after the colon), which rendered
# verbatim in the Appendix. Replaces the old `(D.O.B.)`-only parenthetical,
# which this one subsumes. One bounded character class with no nested
# quantifier: a match attempt reads at most `_DOB_PAREN_MAX` characters
# past the stem, so an unclosed "(" cannot make the scan super-linear. Not
# a newline and not another parenthesis, so it never spans two fragments or
# pairs an opening parenthesis with a later, unrelated closing one.
_DOB_PAREN_MAX = 40
_DOB_LABEL_PARENTHETICAL = r"(?: \s* \( [^()\n]{0,%d} \) )?" % _DOB_PAREN_MAX


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
        date \s* of \s* birth""" + _DOB_LABEL_PARENTHETICAL + r"""
      | date \s+ and \s+ place \s* of \s* birth
      | birth \s*-? \s* date""" + _DOB_LABEL_PARENTHETICAL + r"""
      | birth \s*-? \s* date \s+ and \s+ birth \s*-? \s* place
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
    # #847 residual round 3: `wide_value`/`single_space_value` add
    # `_DOTTED_DATE_VALUE` so a colonless dotted date ("Born 12.03.1970",
    # "DOB - 12.03.1970") matches -- before, only `/`/`-` separators
    # (`_FULL_DATE_VALUE`) were in the union, so this row's `finditer`
    # never found ANY value after a dotted-date stem and the whole
    # colonless match silently failed, at render time as well as pre-LLM.
    WithholdRule(CAT_DATE_OF_BIRTH, SCOPE_ALL_CODES, DECIDED_820, shape=_colonless(
        _DOB_STEM,
        wide_value=_FULL_DATE_VALUE + r"|" + _DOTTED_DATE_VALUE + r"|" + _YEAR_VALUE,
        single_space_value=_FULL_DATE_VALUE + r"|" + _DOTTED_DATE_VALUE)),
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
    # #1041: a bare child count that is its OWN fragment ("Marital Status:
    # <status>; <n> Children" -- the `;` hard split leaves it behind the
    # marital-status cut, and it rendered in the Appendix). The count must
    # be the WHOLE fragment -- opening right after a hard delimiter
    # (`_PII_FRAGMENT_SPLIT_RE`) and closing at the next -- so "20 children
    # and 20 adults" or "20 subjects, 20 children" in a study never matches.
    WithholdRule(CAT_CHILDREN, SCOPE_PERSONAL_AND_APPENDIX, DECIDED_820,
                 shape=r"(?: ^ | (?<= [\n\t|;] ) | (?<= [ ]{3} ) ) [ ]*"
                       r" \d{1,2} \s+ (?: children | child | kids | sons? | daughters? )"
                       r" (?= [ \t.]* (?: [\n\t|;] | \s{3,} | $ ) )",
                 anchored=True),
    WithholdRule(CAT_FAMILY, SCOPE_PERSONAL_AND_APPENDIX, DECIDED_820, label=r"family"),
    WithholdRule(CAT_AGE, SCOPE_PERSONAL_AND_APPENDIX, DECIDED_820, label=r"age"),
    WithholdRule(CAT_GENDER, SCOPE_PERSONAL_AND_APPENDIX, DECIDED_820, label=r"gender"),
    WithholdRule(CAT_RELIGION, SCOPE_PERSONAL_AND_APPENDIX, DECIDED_820, label=r"religion"),
    # #1071: "Race/Ethnicity:", "Race / Ethnicity:", "Race and Ethnicity:"
    # -- the "ethnicity" half alone never opened a label there, because the
    # boundary rule does not accept "/" or "and" before it. Never a bare
    # "race": "Race: reporting practices in clinical trials" is a title.
    WithholdRule(CAT_ETHNICITY, SCOPE_PERSONAL_AND_APPENDIX, DECIDED_820, label=r"""
        ethnicity
      | race \s* / \s* ethnicity
      | race \s+ and \s+ ethnicity
    """),
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


#: #1041: these rows' labels also close on a hyphen or dash followed by
#: whitespace or the end of the text ("Marital Status-<gap><value>" rendered
#: its value in a corpus run). A dash glued to the next character
#: ("Visa-free") is not a terminator.
#: Every other row keeps the colon only: with a dash, the title-word rows
#: matched "Age-related ...", "Gender- and ...", "Health- <employer>" across
#: 24 corpus uids, and spouse/salary open real titles ("Spouse – A
#: Documentary Film Review", #473's negative controls).
_DASH_TERMINATED_CATEGORIES = frozenset({
    CAT_DATE_OF_BIRTH, CAT_PLACE_OF_BIRTH, CAT_SSN, CAT_PASSPORT,
    CAT_ALIEN_REGISTRATION, CAT_DRIVERS_LICENSE, CAT_MARITAL_STATUS,
    CAT_EMERGENCY_CONTACT, CAT_VISA,
})
_COLON_TERMINATOR = r"\s*:"
_COLON_OR_DASH_TERMINATOR = r"\s*(?::|[-–—](?=\s|$))"


def _label_pattern(rule: WithholdRule) -> re.Pattern:
    """The compiled opener for a label row: the row's alternatives, then
    its terminator (`_COLON_OR_DASH_TERMINATOR` for a
    `_DASH_TERMINATED_CATEGORIES` row, else `_COLON_TERMINATOR`), appended
    ONCE for the whole group."""
    terminator = (_COLON_OR_DASH_TERMINATOR if rule.category in _DASH_TERMINATED_CATEGORIES
                  else _COLON_TERMINATOR)
    return re.compile(r"(?:" + str(rule.label) + r")" + terminator, re.X | re.I)


def _shape_pattern(rule: WithholdRule) -> re.Pattern:
    return re.compile(str(rule.shape), re.X | re.I)


_COMPILED_POLICY: tuple[tuple[WithholdRule, re.Pattern], ...] = tuple(
    (rule, _label_pattern(rule) if rule.label is not None else _shape_pattern(rule))
    for rule in WITHHOLD_POLICY
)


def _is_bare_label_span(text: str, start: int, end: int) -> bool:
    """True when `text[start:end]` is a label with no value after it: it
    ends in a colon (the pre-#1041 test, unchanged), or it is a
    dash-terminated label row's opener followed by nothing but whitespace
    ("Marital Status -" before a column gap, tab, newline or cell end)."""
    if text[start:end].rstrip().endswith(":"):
        return True
    for rule, pattern in _COMPILED_POLICY:
        if rule.label is None or rule.category not in _DASH_TERMINATED_CATEGORIES:
            continue
        opener = pattern.match(text, start, end)
        if opener is not None and not text[opener.end():end].strip():
            return True
    return False


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


# #847 residual: an explicit DOB label, its colon, then a WHOLE date right
# after it is a DOB whatever text precedes the label -- "Jane Doe DOB:
# 1/12/45", "Name: Jane Doe, MD Birth Date:  01/12/1945" -- so the boundary
# rule is not asked. ANDed on all three: one of the DOB row's date-of-birth,
# birth-date or DOB alternatives (`_EXPLICIT_DOB_LABEL_RE` -- never
# "Birthday", which also names an event: "Dr. Smith's 70th Birthday:
# 06/15/2019"), the colon its pattern already requires, and a full date
# (never a bare year).
_EXPLICIT_DOB_LABEL_RE = re.compile(r"""
    (?: date \s* of \s* birth""" + _DOB_LABEL_PARENTHETICAL + r"""
      | date \s+ and \s+ place \s* of \s* birth
      | birth \s*-? \s* date""" + _DOB_LABEL_PARENTHETICAL + r"""
      | birth \s*-? \s* date \s+ and \s+ birth \s*-? \s* place
      | birthdate (?: \s+ and \s+ birthplace )?
      | d\.?o\.?b\.? (?: \s* / \s* p\.?o\.?b\.? )?
    ) \s* :""", re.X | re.I)
_WHOLE_DATE_AFTER_LABEL_RE = re.compile(r"[ \t\xa0]*(?:" + _WHOLE_DATE_VALUE + r")", re.I)


def _explicit_dob_label(text: str, m: re.Match) -> bool:
    """True when `m` (a CAT_DATE_OF_BIRTH label opener, colon included) is a
    whole word, one of `_EXPLICIT_DOB_LABEL_RE`'s alternatives, and a whole
    date follows it (`_WHOLE_DATE_AFTER_LABEL_RE`)."""
    word_start = m.start() == 0 or not text[m.start() - 1].isalnum()
    return (word_start and _EXPLICIT_DOB_LABEL_RE.fullmatch(m.group()) is not None
            and _WHOLE_DATE_AFTER_LABEL_RE.match(text, m.end()) is not None)


#: A policy label opens at a word start: no letter, digit, apostrophe or
#: hyphen right before it -- the same guard `_KNOWN_FIELD_LABEL_RE` leads
#: with. Without it "Language:" matched its tail "age:" and rendered as
#: "Langu" once a known field sat earlier on the line.
_LABEL_WORD_START_RE = re.compile(r"(?<![\w'’-])")


def _after_known_field(text: str, frag_start: int, label_start: int) -> bool:
    """True when the policy label at `label_start` starts a word and a
    KNOWN field label (`_KNOWN_FIELD_LABEL_RE`) opens earlier in the same
    fragment: a policy label after another field's value ("Citizenship: US
    Date of Birth: ...") starts that field's successor, wherever the value
    ends (#849). A vocabulary, never a guess at where a value stops --
    #821 R4."""
    return (_LABEL_WORD_START_RE.match(text, label_start) is not None
            and _KNOWN_FIELD_LABEL_RE.search(text, frag_start, label_start) is not None)


def _label_spans(text: str, pattern: re.Pattern, category: str | None = None) -> list[tuple[int, int]]:
    """(start, end) of every fragment `pattern` opens in `text`: from the
    opener's own start to the next hard delimiter (or end of string), kept
    only where `_boundary_ok` accepts the text since the previous delimiter,
    the opener follows a known field label in the same fragment
    (`_after_known_field`), or it is an explicit DOB label with a whole date
    after it."""
    spans = []
    for m in pattern.finditer(text):
        start = m.start()
        prev_delim_end = 0
        for d in _PII_FRAGMENT_SPLIT_RE.finditer(text, 0, start):
            prev_delim_end = d.end()
        if not (_boundary_ok(text[prev_delim_end:start])
                or _after_known_field(text, prev_delim_end, start)
                or (category == CAT_DATE_OF_BIRTH and _explicit_dob_label(text, m))):
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
                         for s, e in _label_spans(text, pattern, rule.category))
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
# Pre-LLM value scrub (#847)
# ---------------------------------------------------------------------------

#: Digit-free, fixed placeholder for a pre-LLM value scrub: never a value
#: shape itself, so a scrubbed text is idempotent under a second pass.
PRE_LLM_PLACEHOLDER = "[withheld]"

# Value-shape search for the categories #847 scrubs before any LLM stage
# reads the text. Built from the SAME building blocks the table's DOB/SSN
# rows already match against (`_FULL_DATE_VALUE`, `_YEAR_VALUE`,
# `_BARE_SSN_SHAPE`, `_SSN_WIDE_VALUE`) plus the two whole-date shapes
# above -- not a second definition of what a date or an SSN looks like
# (module docstring, "#1.5"). `CAT_BIRTH` ("Born: ...", no "on"/"in") gets
# the same date search as `CAT_DATE_OF_BIRTH` -- round 2: pre-LLM there is
# no taxonomy code yet to route by, so scope (which row applies to which
# ENTRY) is not a meaningful filter here; category is. The whole-date
# alternatives come BEFORE `_YEAR_VALUE` so a whole date wins at its own
# start position instead of leaking all but its last four digits (round 2:
# "1970-01-02", "12.03.1970").
_PRE_LLM_DATE_RE = re.compile(_WHOLE_DATE_VALUE + r"|" + _YEAR_VALUE, re.X | re.I)

# The same whole-date shapes without the bare-year fallback, for a value at
# a lower-confidence position -- a cell below a label, the next paragraph --
# where a bare year is as likely an appointment or publication year as a
# birth year.
_PRE_LLM_FULL_DATE_ONLY_RE = re.compile(_WHOLE_DATE_VALUE, re.I)
_PRE_LLM_VALUE_RE: dict[str, re.Pattern] = {
    CAT_DATE_OF_BIRTH: _PRE_LLM_DATE_RE,
    CAT_BIRTH: _PRE_LLM_DATE_RE,
    CAT_SSN: re.compile(_BARE_SSN_SHAPE + r"|" + _SSN_WIDE_VALUE, re.X | re.I),
}
# For the two cross-boundary callers in core/docx_structure_extractor.py
# (`redact_pre_llm_value_of_category(cross_boundary=True)`): a bare "Date of
# Birth:" above "2001", or followed by "1990-1994 BA, Example College",
# must leave the year alone.
_PRE_LLM_VALUE_RE_FULL_DATE_ONLY: dict[str, re.Pattern] = {
    **_PRE_LLM_VALUE_RE,
    CAT_DATE_OF_BIRTH: _PRE_LLM_FULL_DATE_ONLY_RE,
    CAT_BIRTH: _PRE_LLM_FULL_DATE_ONLY_RE,
}

# A Children/Dependents label names no birth date, and "Children: Research,
# Practice and Policy. Oxford Press, 03/15/2019" is a book title (module
# docstring). So a full date after it is withheld only inside a child item:
# ONE capitalised given-name token, then either the date in parentheses
# ("Ann (01/02/2010)") or a born/DOB keyword before it ("Ann DOB:
# 01/02/2010", "Ann, born 01/02/2010"). Items are matched back to back from
# the label's colon; the first thing that isn't one ends the list, so
# "Bob (03/04/2012), Appointed 07/01/2015" keeps the appointment date and
# "A Randomized Trial (03/15/2019)" or "Health Plan, 01/01/2020" is left
# alone. A whole date with no name before it is an item too when it ENDS
# its item -- a comma, a semicolon, "and" or the end of the line comes next
# ("Dependents: 01/02/2010", "Ann (01/02/2010), 03/04/2012"): no title
# opens with a whole date, and "Children: 01/02/2010 Symposium" or
# "..., 07/01/2015 Appointed" is left alone.
_CHILD_BIRTH_KEYWORD = r"(?i:born|b\.|d\.?o\.?b\.?)\s*:?"
_CHILD_DATE = r"(?i:" + _WHOLE_DATE_VALUE + r")"
_CHILD_ITEM_END = r"(?=[ \t]*(?:[,;]|and\b|$))"
# Case-sensitive on purpose: the given name must be capitalised.
_CHILD_ITEM_RE = re.compile(
    r"[\s,;]*(?:and\s+)?(?:[A-Z][a-z'’-]+\s*"
    r"(?:\(\s*(?:" + _CHILD_BIRTH_KEYWORD + r"\s*)?(?P<paren>" + _CHILD_DATE + r")\s*\)"
    r"|,?\s*" + _CHILD_BIRTH_KEYWORD + r"\s*(?P<keyword>" + _CHILD_DATE + r"))"
    r"|(?P<bare>" + _CHILD_DATE + r")" + _CHILD_ITEM_END + r")"
)
_CHILD_ITEM_GROUPS = ("paren", "keyword", "bare")

#: The one Children/Dependents label row, compiled -- walked on its own
#: (`_child_label_date_spans`) because `_merge_matches` folds a Children
#: label that follows another field on its line ("Marital Status: Married
#: Children: Ann (01/02/2010)") into that field's span and category, and the
#: child-item rule then never ran on it.
_CHILDREN_LABEL_RE = next(pattern for rule, pattern in _COMPILED_POLICY
                          if rule.category == CAT_CHILDREN and rule.label is not None)


def _child_list_date_spans(text: str, label_start: int) -> list[tuple[int, int]]:
    """The date of every child item (`_CHILD_ITEM_RE`) that follows, back to
    back, the Children label opening at `label_start`, on its own line."""
    pos = text.index(":", label_start) + 1
    line_end = text.find("\n", pos)
    line_end = len(text) if line_end < 0 else line_end
    spans: list[tuple[int, int]] = []
    while (item := _CHILD_ITEM_RE.match(text, pos, line_end)) is not None:
        group = next(g for g in _CHILD_ITEM_GROUPS if item.group(g) is not None)
        spans.append(item.span(group))
        pos = item.end()
    return spans


def _fragment_start(text: str, pos: int) -> int:
    """Where the `_PII_FRAGMENT_SPLIT_RE` fragment holding `pos` begins."""
    start = 0
    for delim in _PII_FRAGMENT_SPLIT_RE.finditer(text, 0, pos):
        start = delim.end()
    return start


def _known_field_child_date_spans(text: str, label_start: int, frag_end: int) -> list[tuple[int, int]]:
    """Every whole date in the value of the Children label at `label_start`
    when its fragment OPENS with another known field label
    (`_KNOWN_FIELD_LABEL_RE`, #849) and the Children label starts a word
    after it: "Marital Status: Married Children: (1), Jane Roe, 01/02/94" is
    a Personal Data line, not a title, so the child item shape is not
    asked. A known label somewhere mid-line is not enough -- "Arora M. ...
    Threats to Health: ... Health of Children: Hazards, Bangkok, 3-7 March
    2002" is a citation. Stops at the next colon -- the next field's label,
    known or not ("... Date of Appointment: 07/01/2015") -- or the
    fragment's end; never a bare year."""
    frag_start = _fragment_start(text, label_start)
    opener = _KNOWN_FIELD_LABEL_RE.match(text, len(text) - len(text[frag_start:].lstrip()))
    if (opener is None or opener.end() > label_start
            or _LABEL_WORD_START_RE.match(text, label_start) is None):
        return []
    value_start = text.index(":", label_start) + 1
    next_colon = text.find(":", value_start, frag_end)
    stop = frag_end if next_colon < 0 else next_colon
    return [m.span() for m in _PRE_LLM_FULL_DATE_ONLY_RE.finditer(text, value_start, stop)]


def _pre_llm_child_date_spans(text: str) -> list[tuple[int, int]]:
    """Every child's birth date or year `redact_pre_llm_values` withholds:
    the child items after each Children/Dependents label
    (`_child_list_date_spans`), every whole date after one that follows
    another known field (`_known_field_child_date_spans`). A child's birth
    year in unlabelled prose is not taken: every rule tried also cut years
    out of publication titles ("... two children born 1998. Clin Pediatr")."""
    spans: list[tuple[int, int]] = []
    for label_start, frag_end in _label_spans(text, _CHILDREN_LABEL_RE, CAT_CHILDREN):
        spans.extend(_child_list_date_spans(text, label_start))
        spans.extend(_known_field_child_date_spans(text, label_start, frag_end))
    return spans


def _pre_llm_value_span_in_next_run(text: str, frag_end: int, value_re: re.Pattern) -> tuple[int, int] | None:
    """When a bare label's own fragment (`_label_spans`) was cut short at a
    hard delimiter before its value ever started -- a tab, a literal `|`,
    or a 3+-space column gap (`_PII_FRAGMENT_SPLIT_RE`) -- the value that
    OPENS the NEXT run of `text`, starting at `frag_end` (round 2, #847:
    `Date of Birth:\\t01/02/1970`, three-space and pipe-joined table-row
    variants all leaked this way). A run with any text before its value
    ("Date of Appointment:\\t07/01/2005") carries its own label, so it is
    left alone.

    Mirrors `stage6/pii_pass.py::_extend_bare_label_span`'s own hard stop
    ("it never crosses a newline") rather than importing it: that function
    computes how far to CUT a span for full deletion (label discarded with
    it), the opposite of what a value-only scrub needs, and `pii.py`
    importing FROM `pii_pass.py` would reverse pii_pass's existing
    top-level `from .normalization.pii import ...` into a same-package
    import cycle."""
    delim = _PII_FRAGMENT_SPLIT_RE.match(text, frag_end)
    if delim is None or "\n" in delim.group():
        return None
    nxt = _PII_FRAGMENT_SPLIT_RE.search(text, delim.end())
    run_end = nxt.start() if nxt else len(text)
    run = text[delim.end():run_end]
    vm = value_re.match(text, run_end - len(run.lstrip()), run_end)
    return (vm.start(), vm.end()) if vm else None


def _pre_llm_label_value_spans(text: str) -> list[tuple[int, int]]:
    """The one value each DOB/SSN label fragment of `text` carries: the
    first value shape inside the fragment, else the value opening the next
    tab/`|`/column-gap run (`_pre_llm_value_span_in_next_run`)."""
    spans: list[tuple[int, int]] = []
    for m in _pii_matches(text, scope=SCOPE_PERSONAL_AND_APPENDIX):
        value_re = _PRE_LLM_VALUE_RE.get(m.category)
        if value_re is None:
            continue
        vm = value_re.search(text, m.start, m.end)
        span = (vm.start(), vm.end()) if vm else None
        if span is None and _is_bare_label_span(text, m.start, m.end):
            span = _pre_llm_value_span_in_next_run(text, m.end, value_re)
        if span:
            spans.append(span)
    return spans


def redact_pre_llm_values(text: str | None) -> str:
    """Replace the VALUE half of a date-of-birth or SSN fragment with
    `PRE_LLM_PLACEHOLDER`, leaving the label and everything else in `text`
    untouched -- so a downstream label-deny (stage 6) still fires on the
    label, and no provider ever sees the value (#847). Every other
    WITHHOLD_POLICY category (marital status, visa, ...) is out of scope
    here; those stay render-time-only per #820/#821.

    Matched at `SCOPE_PERSONAL_AND_APPENDIX` (the full policy) rather than
    `SCOPE_ALL_CODES`: scope decides which row applies to which taxonomy
    CODE at render time, and pre-LLM nothing has been classified into a
    code yet, so filtering by scope here only dropped real DOB fragments
    ("Born: 01/02/1970", round 2). Filtering is by CATEGORY instead
    (`_PRE_LLM_VALUE_RE`'s keys).

    Idempotent: a value already replaced has no digits left for
    `_PRE_LLM_VALUE_RE` to find, so a second pass is a no-op.

    A DOB/SSN label takes its FIRST value only, so "Date of Birth:
    01/02/1970, Appointed 2005" keeps 2005 (`_pre_llm_label_value_spans`).
    Children's dates and birth years come from
    `_pre_llm_child_date_spans`."""
    text = str(text or "")
    if not text:
        return text
    edits: list[tuple[int, int]] = []
    for span in sorted(_pre_llm_label_value_spans(text) + _pre_llm_child_date_spans(text)):
        if not edits or span[0] >= edits[-1][1]:
            edits.append(span)
    if not edits:
        return text
    out: list[str] = []
    pos = 0
    for start, end in edits:
        out.append(text[pos:start])
        out.append(PRE_LLM_PLACEHOLDER)
        pos = end
    out.append(text[pos:])
    return "".join(out)


def redact_pre_llm_value_of_category(
    text: str | None, category: str, *, cross_boundary: bool = False
) -> str:
    """Replace the FIRST value-shape match for a known pre-LLM `category` in
    `text` with `PRE_LLM_PLACEHOLDER` -- for a value cell whose OWN text
    carries no label at all (round 2, #847 table fix: the label is a
    SIBLING cell, identified separately by `pre_llm_bare_label_category`).
    A no-op if `category` isn't one of `_PRE_LLM_VALUE_RE`'s keys, or no
    value shape is found.

    cross_boundary: True at a call site where the label sits in another
    cell or element (the cell below it, the next paragraph). The value must
    then OPEN `text` (after leading whitespace) and, for CAT_DATE_OF_BIRTH/
    CAT_BIRTH, be a whole date, never a bare year
    (`_PRE_LLM_VALUE_RE_FULL_DATE_ONLY`). So "Date of Appointment:
    07/01/2005" or "Appointed 07/01/2005" below a blank "Date of Birth:"
    keeps its date."""
    text = str(text or "")
    value_map = _PRE_LLM_VALUE_RE_FULL_DATE_ONLY if cross_boundary else _PRE_LLM_VALUE_RE
    value_re = value_map.get(category)
    if not text or value_re is None:
        return text
    if cross_boundary:
        vm = value_re.match(text, len(text) - len(text.lstrip()))
    else:
        vm = value_re.search(text)
    if vm is None:
        return text
    return text[:vm.start()] + PRE_LLM_PLACEHOLDER + text[vm.end():]


def pre_llm_bare_label_category(text: str | None) -> str | None:
    """When `text` (typically ONE table cell, taken alone) is nothing but a
    bare DOB/SSN label with no value anywhere in it -- the whole string
    matches a label fragment, ending in `:`, and no value shape is found
    after it -- the category it names, else None.

    Round 2 (#847): a label cell and its value cell are siblings in the
    SAME row, never a `_PII_FRAGMENT_SPLIT_RE` delimiter apart within one
    cell's own text, so `redact_pre_llm_values`'s in-text extension (which
    only looks inside `text`) cannot see across the cell boundary. The
    caller (`core/docx_structure_extractor.py`) uses this to decide
    whether to scrub the VALUE shape out of the NEXT cell in the row."""
    text = str(text or "")
    if not text or not _is_bare_label_span(text, 0, len(text)):
        return None
    for m in _pii_matches(text, scope=SCOPE_PERSONAL_AND_APPENDIX):
        if m.category not in _PRE_LLM_VALUE_RE:
            continue
        if m.start == 0 and m.end == len(text):
            value_re = _PRE_LLM_VALUE_RE[m.category]
            if value_re.search(text, m.start, m.end) is None:
                return m.category
    return None


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
