"""Protected personal data: detection, and provenance for one value.

A data-governance concern, not a normalization one. Nothing here cleans a
value; it decides whether a value may be rendered at all. Kept apart from
the rest of ``normalization`` deliberately -- a change to an author-name or
separator rule must not be able to widen or narrow what counts as protected
data, and the two now cannot share an edit by accident.

#820 widened this module twice over dev's original #473/#532 predicate,
which anchored every label at the START of a fragment (`^\\s*...`) and only
split fragments on ``\\n``, ``\\t``, ``|`` or 3+ spaces. That missed a label
sitting after ANOTHER label in the same fragment ("Personal Information:
Date of Birth: ...") and everything after a semicolon or comma
("Nationality: X; Date of Birth: ..."). The fix is UNANCHORED matching: a
label counts wherever it sits, provided the text immediately before it (back
to the previous hard delimiter) is empty or ends in one of the separators
the issue names -- ``;``, ``,``, ``:``, ``(``, or start-of-fragment. This is
a strict superset of the old anchored behaviour (the empty-prefix case IS
"anchored"), so every #473/#532 fragment-initial match still fires; it does
not relax the per-label shape checks (colon terminator, colonless date/SSN
value, hyphen-guarded stem) those two issues fought for -- SEE their own
negative-control tests, still pinned unmodified by this file's tests.
"""
import re


# Protected-personal-data detection. Deny by value provenance rather than
# by entry, so a contact block that also carries a birth date keeps its
# live office address, phone and work email (#472).

# Every colon-terminated label the deny recognizes. One alternation so a
# label found ANYWHERE in a fragment (not just at its start) is still this
# same vocabulary -- see _PII_LABEL_OPENER_RE below, which appends `\s*:`
# once for the whole group rather than per alternative.
#
# #820 additions (traced to the issue's probe table and its comment):
# birthday, year of birth, age, DOB/(DOB)/DOB-POB combined forms, family,
# emergency contact, the government-ID class (passport, visa, alien
# registration, driver's license, DEA -- NPI is deliberately absent, it is
# public), the protected-class attributes the #473 design note originally
# excluded as "ordinary research vocabulary" (religion, ethnicity, gender,
# veteran status, disability, health, blood type -- the colon terminator is
# what makes these safe: "Gender Medicine" and "Health Sciences" carry no
# colon immediately after the word), salary/honorarium (#821's withhold
# decision), the wife/husband possessive-name forms, and three non-English
# equivalents.
_PII_LABEL_BODY = r"""
    (?: date \s* of \s* birth (?: \s* \( d\.?o\.?b\.?\) )?
      | birth \s*-? \s* date (?: \s+ and \s+ birth \s*-? \s* place )?
      | birthdate (?: \s+ and \s+ birthplace )?
      | birthday
      | year \s* of \s* birth
      | d\.?o\.?b\.? \s* / \s* p\.?o\.?b\.?
      | d\.?o\.?b\.?
      | born
      | age
      | place \s* of \s* birth | birth \s*-? \s* place | birthplace
      | marital \s* status
      | family
      | spouse (?: [’'] s )? (?: \s* name )?
      | wife (?: [’'] s )? (?: \s* name )?
      | husband (?: [’'] s )? (?: \s* name )?
      | children (?: [’'] s \s* names? )? | dependents?
      | emergency \s* contact
      | social \s* security (?: \s* (?: number | no\.? | \# ) )?
      | ss \s* \#
      | ssn (?: \s* \( last \s* 4 \) )?
      | passport (?: \s* (?: number | no\.? ) )?
      | visa (?: \s* status )?
      | alien \s* registration (?: \s* (?: number | no\.? ) )?
      | driver.?s? \s* licen[sc]e
      | dea (?: \s* (?: \# | number | registration ) )?
      | religion
      | ethnicity
      | gender
      | veteran (?: \s* status )?
      | disability
      | health
      | blood \s* type
      | salary
      | honorarium
      | fecha \s* de \s* nacimiento
      | date \s* de \s* naissance
      | geburtsdatum
    )
"""

_PII_LABEL_RE = re.compile(r"^\s*" + _PII_LABEL_BODY + r"\s* :", re.X | re.I)

# The unanchored form used by `_pii_fragment_spans`: identical vocabulary,
# no `^\s*` anchor -- the anchoring is now the boundary check the span
# finder does itself (empty-prefix IS one of the boundary conditions), so
# this is a strict widening, not a second definition.
_PII_LABEL_OPENER_RE = re.compile(_PII_LABEL_BODY + r"\s* :", re.X | re.I)

# "Married to <name>" carries no colon and no shaped value -- unlike every
# other label here, the phrase itself is the whole signal (#820 comment).
# Guarded the same way `born` is: not preceded by a word character or
# hyphen, so "Newly-married to" or a hypothetical "unmarried too" cannot
# glue onto it from the wrong side.
_PII_PHRASE_RE = re.compile(r"(?<![\w\-–—])married \s+ to \b", re.X | re.I)


_PII_FIELD_KEY_RE = re.compile(r"""^(?:.*_)?(?:
      date_of_birth | birth_?date | birth_?place | place_of_birth | dob
    | marital_status (?:_\w+)? | spouse (?:_\w+)? | wife (?:_\w+)? | husband (?:_\w+)?
    | children (?:_\w+)? | dependents? (?:_\w+)?
    | ssn | social_security\w* )$""", re.X | re.I)


# Hard fragment boundaries: a label after any of these starts a NEW
# fragment scope for the "is this label preceded by nothing" check, and
# also caps how far a matched fragment extends. #820 adds `;` to the
# original `\n`, `\t`, `|`, 3+-space set (the issue's "Nationality: X;
# Date of Birth: ..." case) -- `:` and `,` are NOT hard boundaries here
# because a fragment must still capture its own value after the colon;
# they are handled instead as ALLOWED PRECEDING punctuation in
# `_boundary_ok` below, which is the "follows a separator" half of the
# issue's rule.
_PII_FRAGMENT_SPLIT_RE = re.compile(r"[\n\t|;]|\s{3,}")


def _boundary_ok(prefix_since_last_delim: str) -> bool:
    """True when a label match is fragment-initial OR immediately follows
    one of the issue's separators: `;` (handled upstream, as a hard split),
    `,`, `:`, `(`, or start-of-line. `;` is folded into
    `_PII_FRAGMENT_SPLIT_RE` instead of here because it should also CAP the
    fragment's own extent, the same as `\\n`/`\\t`/`|`; `,`/`:`/`(` are not
    hard boundaries because the label's *own* trailing value must stay in
    the same fragment as a preceding label's."""
    stripped = prefix_since_last_delim.strip()
    return stripped == "" or stripped[-1] in ",:("


def _label_spans(text: str, pattern: re.Pattern) -> list[tuple[int, int]]:
    """(start, end) of every fragment `pattern` opens in `text`: from the
    opener's own start to the next hard delimiter (or end of string),
    kept only where `_boundary_ok` accepts the text since the previous
    hard delimiter. Shared by the colon-label and the "married to" phrase
    scans -- same opener/boundary/extent rules, different vocabulary."""
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


# Colon-less companion to _PII_LABEL_RE (#532), composed from the three named
# parts below so the shape reads off the code. Both halves have to carry
# signal, and the stem's lookbehind is not a plain \b: a hyphen IS a word
# boundary, so \b let "Foreign-born – 2015" match on its "born" half. Scope,
# separator, and every #473 negative control are pinned by
# test_stage6_pii_colonless_labels.py.
_PII_COLONLESS_STEM = r"""
    (?<![\w\-–—])
    (?: date \s* of \s* birth
      | birth \s*-? \s* date
      | birthdate
      | born (?: \s+ (?: on | in ) )?
      | d\.?o\.?b\.?
      | social \s* security (?: \s* (?: number | no\.? ) )?
      | ssn
    )
"""

_MONTH_NAMES = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?"
    r"|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)

# Full-date shapes only (month/day/year all present) -- deliberately NOT a
# bare year, so the widened single-space separator below cannot turn "the
# clinic, born 1970, ..." into a false positive. #820 adds the day-month-year
# order ("2 January 1970") to the month-day-year order #532 already had.
_PII_COLONLESS_DATE_VALUE = (
    r"(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
    r"|" + _MONTH_NAMES + r"\s+\d{1,2},?\s*\d{4}"
    r"|\d{1,2}\s+" + _MONTH_NAMES + r",?\s*\d{4})"
)

# Every SSN spelling #532/#820 catch behind a colon-less separator: dashed,
# space-grouped, and no-separator-at-all. Shared by the wide (tab / dash /
# 2+-space) and single-space separators alike -- "SSN 123-45-6789" and
# "Social Security No. 123 45 6789" are both the issue comment's own
# examples of the single-space form.
_PII_COLONLESS_SSN_WIDE_VALUE = r"\d{3}[\s-]?\d{2}[\s-]?\d{4}"

_PII_COLONLESS_YEAR_VALUE = r"\d{4}"

_PII_COLONLESS_WIDE_VALUE = (
    r"(?:" + _PII_COLONLESS_DATE_VALUE + r"|" + _PII_COLONLESS_SSN_WIDE_VALUE
    + r"|" + _PII_COLONLESS_YEAR_VALUE + r")"
)

_PII_COLONLESS_WIDE_SEP = r"\s* (?: [-–—] \s* | \t \s* | \s{2,} )"

# #820: a single space is now accepted, but ONLY ahead of a shaped value
# distinctive enough not to occur in ordinary prose -- a full date or an
# SSN (dashed, space-grouped, or no separator at all: "Social Security No.
# 123 45 6789" is the issue comment's own example). Widening it to a bare
# year, as the wide separator allows, would deny "born 1970" in a sentence;
# declined -- see the module docstring.
_PII_COLONLESS_SINGLE_SPACE_VALUE = (
    r"(?:" + _PII_COLONLESS_DATE_VALUE + r"|" + _PII_COLONLESS_SSN_WIDE_VALUE + r")"
)

_PII_LABEL_VALUE_RE = re.compile(
    _PII_COLONLESS_STEM
    + r"""(?:""" + _PII_COLONLESS_WIDE_SEP + r"(?:" + _PII_COLONLESS_WIDE_VALUE + r")"
    + r"|[ ](?!\s)(?:" + _PII_COLONLESS_SINGLE_SPACE_VALUE + r"))",
    re.X | re.I,
)


# A bare SSN, label or not (#820 comment: "the SSN value shape ... is
# distinct from a phone (3-3-4) and should be denied on the value alone").
# The distinguishing shape from a phone number is the middle group's width
# (2 digits, not 3), so "212-555-1234" never matches this.
#
# Guarded by "not digit-or-hyphen adjacent" on BOTH sides rather than `\b`:
# a plain `\b` treats digit-to-hyphen as a boundary, which let this match
# the first three groups of a five-group ISBN-13 ("978-2-1234-567-1", group
# widths 3-1-4-3-1) -- confirmed on web228's book-chapter bibliography (24
# false hits, one CV) during the #820 corpus scan. A real SSN is never
# hyphen-adjacent on either side; an ISBN/ORCID/grant-number fragment that
# happens to contain a 3-2-4 run usually is.
#
# `doctor/lints/protected_data.py` keeps an intentionally SEPARATE copy of
# this same pattern for its own bare-value scan over rendered text -- not
# imported from here. That check is piece 3's own named requirement,
# landing in this ticket's FIRST commit, before this module (piece 1, the
# THIRD commit) has been touched at all; sharing one definition across
# commits in the wrong order is not possible, so each stays independently
# correct and the two are cross-referenced in comments instead.
_SSN_VALUE_SHAPE_RE = re.compile(r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])")


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


def _pii_fragment_spans(text: str) -> list[tuple[int, int]]:
    """(start, end) offsets of every PII fragment in `text`, merged where
    overlapping, in document order. `_pii_fragments` below is the only
    caller -- kept as its own function so a future consumer that needs
    OFFSETS (not just fragment text) has one place to compute them from,
    rather than re-deriving positions from returned strings."""
    text = str(text or "")
    spans = (
        _label_spans(text, _PII_LABEL_OPENER_RE)
        + _label_spans(text, _PII_PHRASE_RE)
        + [(m.start(), m.end()) for m in _PII_LABEL_VALUE_RE.finditer(text)]
        + [(m.start(), m.end()) for m in _SSN_VALUE_SHAPE_RE.finditer(text)]
    )
    spans.sort()
    merged: list[tuple[int, int]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def _pii_fragments(text: str | None) -> list[str]:
    """The fragments of an entry that carry protected personal data."""
    text = str(text or "")
    return [text[s:e] for s, e in _pii_fragment_spans(text)]


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
