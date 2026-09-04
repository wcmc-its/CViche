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
is made as sound as a containment test can be -- token-aligned, with a
minimum length -- and these tests pin both halves plus the two cases that
motivated the predicate in the first place.

The three corpus CVs whose real office address, office phone and work email
must survive an entry that also carries a birth date are covered end to end
by test_stage6_personal_data_recovery.py::
test_real_contact_data_survives_an_entry_that_also_carries_pii.

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


def test_a_bare_year_no_longer_denies_by_sitting_inside_a_date():
    """A four-character year is inside every birth date and inside half the
    year columns of a CV, so it is not evidence of anything. The minimum
    length is what stops it; the year IS token-aligned inside the date."""
    fragments = _pii_fragments("Date of Birth: 12/13/1947")
    assert fragments
    assert not _from_pii_fragment("1947", fragments)


def test_an_empty_or_missing_value_is_never_denied():
    fragments = _pii_fragments("Date of Birth: 12/13/1947")
    assert not _from_pii_fragment(None, fragments)
    assert not _from_pii_fragment("", fragments)
    assert not _from_pii_fragment("   ", fragments)


def test_no_fragments_means_nothing_is_denied():
    assert not _from_pii_fragment("1300 York Avenue, New York, NY", [])


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
