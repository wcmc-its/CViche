"""Protected personal data: detection, and provenance for one value.

A data-governance concern, not a normalization one. Nothing here cleans a
value; it decides whether a value may be rendered at all. Kept apart from
the rest of ``normalization`` deliberately -- a change to an author-name or
separator rule must not be able to widen or narrow what counts as protected
data, and the two now cannot share an edit by accident.
"""
import re


# Protected-personal-data detection. Deny by value provenance rather than
# by entry, so a contact block that also carries a birth date keeps its
# live office address, phone and work email (#472).

_PII_LABEL_RE = re.compile(r"""^\s*
    (?: date \s* of \s* birth
      | birth \s*-? \s* date (?: \s+ and \s+ birth \s*-? \s* place )?
      | birthdate (?: \s+ and \s+ birthplace )?
      | born
      | d\.?o\.?b\.?
      | place \s* of \s* birth | birth \s*-? \s* place | birthplace
      | marital \s* status
      | spouse (?: [’']s )? (?: \s* name )? | wife | husband
      | children (?: [’']s \s* names? )? | dependents?
      | social \s* security (?: \s* (?: number | no\.? ) )? | ssn
    ) \s* :""", re.X | re.I)


_PII_FIELD_KEY_RE = re.compile(r"""^(?:.*_)?(?:
      date_of_birth | birth_?date | birth_?place | place_of_birth | dob
    | marital_status (?:_\w+)? | spouse (?:_\w+)? | wife (?:_\w+)? | husband (?:_\w+)?
    | children (?:_\w+)? | dependents? (?:_\w+)?
    | ssn | social_security\w* )$""", re.X | re.I)


_PII_FRAGMENT_SPLIT_RE = re.compile(r"[\n\t|]|\s{3,}")


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
      | born
      | d\.?o\.?b\.?
      | social \s* security (?: \s* (?: number | no\.? ) )?
      | ssn
    )
"""

_PII_COLONLESS_SEPARATOR = r"""
    \s* (?: [-–—] \s* | \t \s* | \s{2,} )
"""

_PII_COLONLESS_VALUE = r"""
    (?: \d{1,2} [/-] \d{1,2} [/-] \d{2,4}
      | \d{3} -? \d{2} -? \d{4}
      | (?: jan(?:uary)? | feb(?:ruary)? | mar(?:ch)? | apr(?:il)? | may
          | jun(?:e)? | jul(?:y)? | aug(?:ust)? | sep(?:tember)? | oct(?:ober)?
          | nov(?:ember)? | dec(?:ember)?
        ) \s+ \d{1,2} , \s* \d{4}
      | \d{4}
    )
"""

_PII_LABEL_VALUE_RE = re.compile(
    _PII_COLONLESS_STEM + _PII_COLONLESS_SEPARATOR + _PII_COLONLESS_VALUE,
    re.X | re.I,
)


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


def _pii_fragments(text: str | None) -> list[str]:
    """The fragments of an entry that carry protected personal data."""
    text = str(text or "")
    colon_fragments = [f for f in _PII_FRAGMENT_SPLIT_RE.split(text)
                        if _PII_LABEL_RE.match(f)]
    colonless_fragments = [m.group(0) for m in _PII_LABEL_VALUE_RE.finditer(text)]
    return colon_fragments + colonless_fragments


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
