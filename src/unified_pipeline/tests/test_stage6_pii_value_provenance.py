"""`_from_pii_fragment` must not deny a value by accidental containment.

The predicate exists so that a Personal Data entry which carries a birth
date alongside a live office address keeps the address (#472): the deny is
per VALUE, not per entry. It decided that by asking whether the value's
characters occurred anywhere inside a PII fragment, which answers a
different question -- a short or badly-aligned value is denied because its
characters happen to run through the inside of a date or an SSN.

Real provenance is not available here and cannot be faked: it would need
stage 4 to record the source span each value was lifted from, and stage 4
emits raw LLM JSON against no schema and no spans. So the containment test
is made as sound as a containment test can be -- TOKEN-ALIGNED -- and these
tests pin that plus the cases that motivated the predicate in the first
place.

Round 2 removed a second narrowing, a minimum value length, which had
re-opened #472: "Ohio" out of "PLACE OF BIRTH: Ohio" is four characters
and it is the protected value, not a coincidental collision with one. The
tests below pin the short protected values as denied.

Two of the five real values in #472's table -- an office address and an
office phone -- are pinned by test_stage6_personal_data_recovery.py::
test_real_contact_data_survives_an_entry_that_also_carries_pii; the work
email and the two home addresses in that table are not.

    python3 -m pytest src/unified_pipeline/tests/test_stage6_pii_value_provenance.py -p no:cacheprovider

Self-contained: no DB, no template, no python-docx, no PII -- every string
below is synthetic.
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage6.normalization.pii import (  # noqa: E402
    _from_pii_fragment,
    _pii_fragments,
)


# --------------------------------------------------------------------------
# The behaviour the predicate exists for, which must not regress
# --------------------------------------------------------------------------

def test_the_birthplace_web07_rendered_as_an_address_is_still_denied():
    """The motivating case: stage 4 lifted "Cincinnati, Ohio" out of
    "PLACE OF BIRTH: ..." into the `address` field, and the address
    catch-all rendered it in the Office address row."""
    fragments = _pii_fragments("PLACE OF BIRTH: Cincinnati, Ohio")
    assert fragments
    assert _from_pii_fragment("Cincinnati, Ohio", fragments)


def test_a_whole_protected_value_is_still_denied():
    assert _from_pii_fragment("12/13/1947", _pii_fragments("Date of Birth: 12/13/1947"))
    assert _from_pii_fragment("123-45-6789", _pii_fragments("SSN: 123-45-6789"))


def test_a_value_from_a_different_line_of_the_same_entry_is_not_denied():
    """Fragment-level, not entry-level: the office address on the next line
    of the same contact block is not inside the birth-date fragment."""
    entry = ("Born: December 13, 1965\n"
             "Office: 1300 York Avenue, New York, NY")
    assert not _from_pii_fragment("1300 York Avenue, New York, NY",
                                  _pii_fragments(entry))


# --------------------------------------------------------------------------
# The accidental-containment class the review names
# --------------------------------------------------------------------------

def test_a_value_that_cuts_a_token_in_half_is_not_denied():
    """"23-45-6789" occurs inside "123-45-6789" only by starting in the
    middle of its first token, which is a coincidence of characters and not
    evidence the value came from the SSN. Bare substring containment denied
    it; a token-aligned match does not."""
    fragments = _pii_fragments("SSN: 123-45-6789")
    assert fragments
    assert not _from_pii_fragment("23-45-6789", fragments)


def test_a_value_that_ends_mid_token_is_not_denied():
    fragments = _pii_fragments("Date of Birth: 12/13/1947")
    assert fragments
    assert not _from_pii_fragment("12/13/194", fragments)


def test_a_year_that_is_a_component_of_the_birth_date_is_denied():
    """Round 1 dismissed a bare year as "inside every birth date, so not
    evidence of anything" and put a minimum value length in to stop it.
    That is backwards: the year of a birth date IS the protected value, and
    the same floor then let "PLACE OF BIRTH: Ohio" render its Ohio. The
    year is token-aligned inside the date and stays denied; a year that is
    NOT part of a PII fragment was never reachable here, because
    `_pii_fragments` only returns the fragments a PII label introduces."""
    fragments = _pii_fragments("Date of Birth: 12/13/1947")
    assert fragments
    assert _from_pii_fragment("1947", fragments)


def test_a_short_birthplace_value_is_denied():
    """The #472 case at its shortest: a one-word birthplace. Round 1's
    six-alphanumeric-character floor let "Ohio" and "Utah" through, and
    `personal_data.py` renders whatever survives into the Office address
    row -- exactly the defect the module exists to prevent."""
    for state in ("Ohio", "Utah"):
        fragments = _pii_fragments(f"PLACE OF BIRTH: {state}")
        assert fragments
        assert _from_pii_fragment(state, fragments), state


def test_a_short_two_digit_year_birth_date_is_denied():
    """"1/1/90" carries four alphanumeric characters, so the floor let the
    whole birth date through."""
    fragments = _pii_fragments("Date of Birth: 1/1/90")
    assert fragments
    assert _from_pii_fragment("1/1/90", fragments)


def test_an_empty_or_missing_value_is_never_denied():
    fragments = _pii_fragments("Date of Birth: 12/13/1947")
    assert not _from_pii_fragment(None, fragments)
    assert not _from_pii_fragment("", fragments)
    assert not _from_pii_fragment("   ", fragments)


def test_no_fragments_means_nothing_is_denied():
    assert not _from_pii_fragment("1300 York Avenue, New York, NY", [])


def test_a_non_str_value_is_squashed_through_str_like_any_other() -> None:
    """#735 review item 2's one case the existing 12 tests do not cover:
    `value` is typed `object`, not `str`, and stage 4 can hand back a bare
    int for a birth year. `_squash` runs every value through `str(value or
    "")`, so a truthy int is matched exactly as its string form would be --
    but a FALSY int (0) is indistinguishable from a missing value: `0 or ""`
    evaluates to `""` before `str()` ever runs, so it is never denied even
    when the fragment would otherwise match "0"."""
    fragments = _pii_fragments("Date of Birth: 12/13/1947")
    assert fragments
    assert _from_pii_fragment(1947, fragments)
    assert not _from_pii_fragment(0, _pii_fragments("Marital Status: 0"))


# --------------------------------------------------------------------------
# Whitespace still does not matter, only alignment does
# --------------------------------------------------------------------------

def test_the_match_survives_different_whitespace():
    """Stage 4 re-spaces what it extracts, so whitespace must not decide the
    match -- the tolerance the old `_squash` comparison had is kept, and
    only the edge condition is new. Both directions: the value spaced where
    the fragment is not, and the fragment spaced where the value is not."""
    fragments = _pii_fragments("Place of Birth: Cincinnati, Ohio")
    assert fragments
    assert _from_pii_fragment("Cincinnati,Ohio", fragments)
    assert _from_pii_fragment("Cincinnati,   Ohio", fragments)
    assert _from_pii_fragment("Cincinnati, Ohio", _pii_fragments(
        "Place of Birth: Cincinnati,Ohio"))


def test_the_match_is_still_case_insensitive():
    fragments = _pii_fragments("PLACE OF BIRTH: Cincinnati, Ohio")
    assert _from_pii_fragment("cincinnati, ohio", fragments)


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_"):
            _fn()
            print(f"ok  {_name}")
    print("all checks passed")
