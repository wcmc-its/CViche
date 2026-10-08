"""#1575: `stage4.grounding.ground_group`, the check that a group reply item's
values come from its own entry's text.

What each test pins:

- a value its own entry lacks but another entry states is removed;
- the entry's own text is read generously, so a year it does state is never
  called borrowed (two-digit years, short range ends, a mistyped digit, a
  year run into other digits);
- another entry's text is read strictly (a four-digit year, a percent);
- fields with no checkable characters (role, name) are never touched;
- a wholly foreign record is removed only while a record of the entry's own
  remains; otherwise its borrowed fields are set to None;
- an index outside the group passes through for `_extraction_map` (#1243).

    python3 -m pytest src/unified_pipeline/tests/test_stage4_grounding.py -p no:cacheprovider

Pure: no LLM, no I/O. Synthetic texts only.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from unified_pipeline.stage4.grounding import ground_group  # noqa: E402


def _borrowed(texts, items_by_entry):
    return ground_group(texts, items_by_entry).borrowed_fields


def test_a_value_only_another_entry_states_is_set_to_none():
    texts = ["Grant A $326,020/year Effort: 20% 04/21/17-03/31/23",
             "Grant B $30,000/year Effort: 30% 07/01/1997-06/30/1999"]
    item = {"start_date": "1997-07-01", "end_date": "1999-06-30", "total_funding": "$30,000/year",
            "percent_effort": "30%", "pi_role": "PI", "title": "Grant A"}

    result = ground_group(texts, {0: [item]})

    assert result.borrowed_fields == {0: ["end_date", "percent_effort", "start_date", "total_funding"]}
    [grounded] = result.items[0]
    assert grounded == {**item, "start_date": None, "end_date": None, "total_funding": None,
                        "percent_effort": None}


def test_a_value_no_other_entry_states_is_left_alone():
    """Absent from its own entry but from every other entry too: not borrowed
    from the group, so not this check's to judge."""
    assert _borrowed(["Grant A 2017", "Grant B 2019"], {0: [{"start_date": "2005"}]}) == {}


@pytest.mark.parametrize(("own_text", "field", "value"), [
    ("04/21/17-03/31/23", "start_date", "2017-04-21"),   # two-digit year
    ("Mentee, 2004-6", "end_date", "2006"),              # short range end
    ("Mentee, 2017-18", "end_date", "2018"),
    ("talk 3-30-00", "date", "2000-03-30"),             # m-d-yy
    ("position, l988 to 1994", "start_date", "1988"),   # mistyped first digit
    ("Journal 172:7512022", "year", "2022"),            # run into the page number
    ("Journal 202526;8(4)", "year", "2025"),
    ("award $1,200,000", "total_funding", "$1,200,000"),
])
def test_a_year_or_amount_the_entry_states_in_another_form_is_its_own(own_text, field, value):
    other = "Another entry 2000 2004 2006 2017 2018 2022 2025 1988 $1,200,000"
    assert _borrowed([own_text, other], {0: [{field: value}]}) == {}


def test_another_entrys_text_is_read_strictly():
    """A two-digit year or a bare number in the other entry is not enough to
    call a value borrowed: "06/30/99" does not state 1999, and "30" in a date
    is not an effort of 30%."""
    texts = ["Grant A, no dates or effort", "Grant B 06/30/99"]
    assert _borrowed(texts, {0: [{"end_date": "1999", "percent_effort": "30%"}]}) == {}


def test_a_grant_number_is_compared_without_punctuation_and_a_short_one_is_skipped():
    texts = ["Grant A", "Grant B R01 CA 000001; award 12"]
    assert _borrowed(texts, {0: [{"grant_number": "R01-CA000001"}]}) == {0: ["grant_number"]}
    assert _borrowed(texts, {0: [{"grant_number": "12"}]}) == {}


def test_names_roles_titles_and_other_fields_are_not_checked():
    """Only the named date, amount, percent and grant-number fields are: a
    role or an organisation legitimately repeats across entries."""
    texts = ["Grant A (MPI)", "Grant B (PI) Dr. Example, 40 hours"]
    item = {"pi_role": "PI", "pi_name": "Dr. Example", "title": "Grant B", "hours_per_year": "40"}
    assert _borrowed(texts, {0: [item]}) == {}


def test_a_record_that_is_wholly_another_entrys_is_removed_while_one_of_its_own_remains():
    texts = ["Mentee B, 2017-18", "Mentee C (2021-2022)"]
    own = {"mentee_name": "Mentee B", "start_date": "2017", "end_date": "2018"}
    foreign = {"mentee_name": "Mentee C", "start_date": "2021", "end_date": "2022"}

    result = ground_group(texts, {0: [own, foreign]})

    assert result.items[0] == [own]
    assert result.dropped_records == {0: 1}
    assert result.borrowed_fields == {}


def test_an_entrys_only_record_is_never_removed_its_borrowed_fields_are_set_to_none():
    texts = ["Mentee B, no dates", "Mentee C (2021-2022)"]
    foreign = {"mentee_name": "Mentee C", "start_date": "2021", "end_date": "2022"}

    result = ground_group(texts, {0: [foreign]})

    assert result.items[0] == [{"mentee_name": "Mentee C", "start_date": None, "end_date": None}]
    assert result.dropped_records == {}
    assert result.borrowed_fields == {0: ["end_date", "start_date"]}


def test_an_index_outside_the_group_passes_through():
    items = {5: [{"start_date": "2021"}]}
    result = ground_group(["Mentee B 2017", "Mentee C 2021"], items)
    assert result.items == items
    assert result.borrowed_fields == {}
