"""Regression tests for #532: the PII deny predicate missed colon-less
labels.

`_PII_LABEL_RE` (`stage6/normalization/pii.py`) requires a colon
terminator, so it denies "Date of Birth: 12/13/1947" but not "Date of Birth
- 01/01/1990", "DOB\\t01/01/1990", or "SSN  123-45-6789" -- a colon-less
label evades both deny paths #473 added (the value stays in the Personal
Data slot if it lands in one; the entry is appended verbatim to the
appendix if it does not).

The colon was not simply relaxed in #473 because relaxing it destroys real
CV content that merely starts with a listed word ("Children's Oncology
Group - Emeritus", "Born - Digital: A Study of Youth Media Practices").
#532's fix is a companion predicate that requires the VALUE to carry the
signal too: a PII stem, then a non-colon separator (tab, dash, or 2+
spaces), then a date- or SSN-shaped value. `test_deny_predicate_requires_a_
colon_terminator` in test_stage6_personal_data_recovery.py is the existing
#473 negative-control test that pins the false positives this predicate
must not reintroduce; it stays green (asserted again here, from this file,
as the acceptance criterion this predicate is built against).

Scoped to date-of-birth and SSN only (declined here: a colon-less
"place of birth" companion, since a place name has no comparably
distinctive shape -- see the module comment above `_PII_LABEL_VALUE_RE`).

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_pii_colonless_labels.py -p no:cacheprovider
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage6.normalization.pii import _pii_fragments  # noqa: E402


# --------------------------------------------------------------------------
# #532's three reproduction lines, plus the shapes named in "the shape a
# fix has to take".
# --------------------------------------------------------------------------

def test_dash_separated_date_of_birth_is_caught():
    assert _pii_fragments('Date of Birth - 01/01/1990')


def test_tab_separated_dob_is_caught():
    assert _pii_fragments('DOB\t01/01/1990')


def test_double_space_separated_ssn_is_caught():
    assert _pii_fragments('SSN  123-45-6789')


def test_en_dash_separated_born_date_is_caught():
    assert _pii_fragments('Born – December 13, 1965')


def test_colonless_dob_with_year_only_value_is_caught():
    assert _pii_fragments('D.O.B.\t1947')


def test_ssn_without_dashes_is_caught():
    assert _pii_fragments('Social Security Number  123456789')


# --------------------------------------------------------------------------
# The predicate must not fire on a bare label with no PII-shaped value --
# denying by label text alone is exactly what #473 already rejected.
# --------------------------------------------------------------------------

def test_stem_with_non_date_value_is_not_denied():
    assert not _pii_fragments('Born - Digital: A Study of Youth Media Practices')


def test_stem_with_no_separator_at_all_is_not_denied():
    assert not _pii_fragments('Born in the USA: Birth Cohort Methods')


def test_declined_scope_colonless_place_of_birth_is_not_caught():
    """Judgement call (#532): a colon-less place-of-birth companion is
    declined -- a place name has no shape distinctive enough to require
    without risking new false positives. This is a known, disclosed gap,
    not a silent one."""
    assert not _pii_fragments('PLACE OF BIRTH    Cincinnati, Ohio')


# --------------------------------------------------------------------------
# The #473 negative controls this predicate must not reintroduce. Mirrors
# test_stage6_personal_data_recovery.py::
# test_deny_predicate_requires_a_colon_terminator, which is the test of
# record for these -- pinned again here as this predicate's own acceptance
# bar, so a future change to _PII_LABEL_VALUE_RE is caught from this file
# too.
# --------------------------------------------------------------------------

def test_473_negative_controls_stay_undenied():
    for keeper in [
        "Born - Digital: A Study of Youth Media Practices",
        "Children - Oncology Group Consortium, Emeritus",
        "Spouse – A Documentary Film Review",
        "Children and Fire: Research on Burn Prevention",
        "Children of the Pandemic: A Longitudinal Cohort",
        "Born in the USA: Birth Cohort Methods",
        "Children's Oncology Group - Emeritus",
        "Children's Hospital Colorado - Pillar Award",
        "Religion and Healing in America",
    ]:
        assert not _pii_fragments(keeper), f"false positive on {keeper!r}"


def test_stem_as_word_fragment_is_not_denied():
    """Round-1 regression: `_PII_LABEL_VALUE_RE` had no word boundary
    before the stem group, so 'born'/'ssn' matched INSIDE a longer word
    when followed by a dash/tab/2-space + date-or-SSN-shaped value --
    'Michigan-Dearborn – 2015' and 'Osborn - 2012' are plausible CV
    substrings (an institution name, a co-author's surname) that must not
    be mistaken for a date-of-birth label."""
    for keeper in [
        "Education\tUniversity of Michigan-Dearborn – 2015",
        "Osborn - 2012",
        "Sanborn – 1998",
        "Firstborn – 2001",
        "Assn  2019",
    ]:
        assert not _pii_fragments(keeper), f"false positive on {keeper!r}"


def test_existing_colon_terminated_labels_are_still_caught():
    """The original #473 predicate is untouched -- this is a companion,
    not a replacement."""
    for pii in [
        "Date of Birth: 12/13/1947",
        "PLACE OF BIRTH: Cincinnati, Ohio",
        "Born: December 13, 1965",
        "D.O.B.: 1/1/1970",
    ]:
        assert _pii_fragments(pii), f"missed {pii!r}"


def test_hyphen_compound_stem_is_not_denied():
    """Round-2 regression: `\\b` is not enough of a boundary, because a
    hyphen is itself a word boundary -- "Foreign-born – 2015" and "US-born
    1990" (a demographic term beside a CV date column) matched on the
    "born" half and, via `_pii_fragments`, redacted the whole entry in the
    appendix. The lookbehind now rejects a preceding hyphen or dash too."""
    for keeper in [
        "Foreign-born – 2015",
        "US-born  1990",
        "Native-born\t2001",
        "Foreign–born – 2015",
    ]:
        assert not _pii_fragments(keeper), f"false positive on {keeper!r}"


def test_colonless_dotted_date_is_now_caught():
    """#847 residual round 3: `_FULL_DATE_VALUE` only accepted `/`/`-`
    separators, so a colonless dotted date ("Born 12.03.1970",
    "DOB - 12.03.1970") matched no value shape at all and the whole
    colonless row silently failed to fire -- at render time, not only in
    the pre-LLM scrub."""
    for now_caught in [
        "Born 12.03.1970",
        "DOB - 12.03.1970",
        "Date of Birth\t12.03.1970",
    ]:
        assert _pii_fragments(now_caught), f"missed {now_caught!r}"


def test_single_space_separator_is_now_caught_for_shaped_values():
    """#820 (comment) closes #532's disclosed gap: a single space IS now
    accepted, but only ahead of a value shaped distinctively enough not to
    occur in ordinary prose -- a full date or an SSN. Widening to a bare
    year was considered and declined (it would deny "born 1970" inside a
    sentence) -- see `test_stem_with_non_date_value_is_not_denied` and the
    module docstring in `pii.py`."""
    for now_caught in [
        "SSN 123-45-6789",
        "Date of Birth 01/01/1990",
    ]:
        assert _pii_fragments(now_caught), f"missed {now_caught!r}"
