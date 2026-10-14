"""Class E26 (2026-10-04 NDMRSO autopsy): `date_year_group_members` re-dates
an "MM-DD:" line from the "YYYY:" line that opens its group, where stage 4
read the line's own token as a year.

    python3 -m pytest src/unified_pipeline/tests/test_stage4_year_groups.py -p no:cacheprovider

Pure and offline. Synthetic entries only.
"""

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from unified_pipeline.stage4.schemas import STAGE4_RECORDS_KEY  # noqa: E402
from unified_pipeline.stage3b.io import GROUP_YEAR_KEY  # noqa: E402
from unified_pipeline.stage4.year_groups import date_group_year_twins, date_year_group_members  # noqa: E402

_H = ["Teaching Record"]


def _e(text, hierarchy=None, **fields):
    return {"text": text, "hierarchy": list(hierarchy or _H), "extracted_fields": fields}


def _dated(entries):
    return [e["extracted_fields"] for e in date_year_group_members(entries, STAGE4_RECORDS_KEY)]


def test_a_member_whose_token_was_read_as_a_year_takes_the_group_year():
    entries = [
        _e("2016:  03-16: Example lecture A", date="2016-03-16"),
        _e("09-14: Example lecture B", start_date="2014-09", end_date=None),
    ]

    assert _dated(entries)[1] == {"start_date": "2016-09-14", "end_date": None}


@pytest.mark.parametrize("fields, expected", [
    # the token's day read as the year, or its month
    ({"start_date": "2014-09"}, {"start_date": "2016-09-14"}),
    ({"start_date": "2009-14"}, {"start_date": "2016-09-14"}),
    # the token read as a "YYYY-DD" value
    ({"start_date": "2009-14-01"}, {"start_date": "2016-09-14"}),
    # the token read as a two-year range: the end is the line's own token
    ({"start_date": "2009", "end_date": "2014"}, {"start_date": "2016-09-14", "end_date": None}),
    # a K4 line keeps its one date in `date`
    ({"date": "2014-09"}, {"date": "2016-09-14"}),
    # the group's year with the token's other half as an end: the end goes
    ({"start_date": "2016", "end_date": "2014"}, {"start_date": "2016", "end_date": None}),
])
def test_each_token_read_shape_is_re_dated(fields, expected):
    entries = [_e("2016:  03-16: Example lecture A"), _e("09-14: Example lecture B", **fields)]

    assert _dated(entries)[1] == expected


@pytest.mark.parametrize("text, fields", [
    # no year to replace, or the group's own year
    ("09-14: Example lecture B", {"date": "09-14"}),
    ("09-14: Example lecture B", {"start_date": None}),
    ("09-14: Example lecture B", {"start_date": "2016-09"}),
    # a year whose digits are not the token's
    ("09-14: Example lecture B", {"start_date": "2011-09"}),
    # a year the line writes in full is the line's own
    ("09-14: Example lecture B, revised 2014", {"start_date": "2014-09"}),
    # a non-string value
    ("09-14: Example lecture B", {"year": 2014}),
    ("09-14: Example lecture B", {"start_date": ["2014-09"]}),
])
def test_a_member_without_a_token_read_year_is_left_alone(text, fields):
    entries = [_e("2016:  03-16: Example lecture A"), _e(text, **copy.deepcopy(fields))]

    assert _dated(entries)[1] == fields


@pytest.mark.parametrize("entries", [
    # no group heading
    [_e("Example lecture A"), _e("09-14: Example lecture B", start_date="2014-09")],
    # a year line without its "MM-DD:" token is not a group heading
    [_e("2016: Example lecture A"), _e("09-14: Example lecture B", start_date="2014-09")],
    # the heading's own token is not a month and day
    [_e("2016:  13-16: Example lecture A"), _e("09-14: Example lecture B", start_date="2014-09")],
    [_e("2016:  03-32: Example lecture A"), _e("09-14: Example lecture B", start_date="2014-09")],
    # a member under another hierarchy
    [_e("2016:  03-16: Example lecture A"),
     _e("09-14: Example lecture B", ["Other Section"], start_date="2014-09")],
    # an entry of another shape ends the group
    [_e("2016:  03-16: Example lecture A"), _e("Example interlude"),
     _e("09-14: Example lecture B", start_date="2014-09")],
    # a line whose token is not a month and day is not a member
    [_e("2016:  03-16: Example lecture A"), _e("13-14: Example lecture B", start_date="2013-14")],
    [_e("2016:  03-16: Example lecture A"), _e("09-32: Example lecture B", start_date="2032-09")],
    # a line with no colon after its token is not a member
    [_e("2016:  03-16: Example lecture A"), _e("09-14 Example lecture B", start_date="2014-09")],
])
def test_entries_outside_a_year_group_are_left_alone(entries):
    before = copy.deepcopy(entries)

    assert date_year_group_members(entries, STAGE4_RECORDS_KEY) == before


def test_the_group_runs_over_every_member_and_a_new_heading_replaces_it():
    entries = [
        _e("2016:  03-16: Example lecture A", date="2016-03-16"),
        _e("05-11: Example lecture B", start_date="2011-05"),
        _e("9-14: Example lecture C", start_date="2014-09"),
        _e("2017:  06-21: Example lecture D", date="2017-06-21"),
        _e("10-12: Example lecture E", start_date="2012-10"),
    ]

    dated = _dated(entries)

    assert [d.get("start_date") for d in dated[1:3]] == ["2016-05-11", "2016-09-14"]
    assert dated[4]["start_date"] == "2017-10-12"


def test_records_of_a_multi_record_member_are_re_dated_too():
    records = [{"start_date": "2014-09"}, {"start_date": "2014-09", "end_date": "2009"}]
    entries = [
        _e("2016:  03-16: Example lecture A"),
        _e("09-14: Example lecture B\t09-14: Example lecture C",
           start_date="2014-09", end_date="2009", **{STAGE4_RECORDS_KEY: records}),
    ]

    fields = _dated(entries)[1]

    assert fields[STAGE4_RECORDS_KEY] == [
        {"start_date": "2016-09-14"}, {"start_date": "2016-09-14", "end_date": None}]
    assert (fields["start_date"], fields["end_date"]) == ("2016-09-14", None)


def test_a_record_only_change_still_returns_a_re_dated_copy():
    records = [{"start_date": "2014-09"}]
    entries = [
        _e("2016:  03-16: Example lecture A"),
        _e("09-14: Example lecture B", **{STAGE4_RECORDS_KEY: records}),
    ]

    out = date_year_group_members(entries, STAGE4_RECORDS_KEY)

    assert out[1]["extracted_fields"][STAGE4_RECORDS_KEY] == [{"start_date": "2016-09-14"}]
    assert "reformatted_fields" not in out[1]


def test_the_change_is_recorded_and_the_input_is_not_mutated():
    member = _e("09-14: Example lecture B", start_date="2014-09", end_date="2009")
    member["reformatted_fields"] = {"title": {"original": "x", "reformatted": "y", "reason": "r"}}
    entries = [_e("2016:  03-16: Example lecture A"), member]
    before = copy.deepcopy(entries)

    out = date_year_group_members(entries, STAGE4_RECORDS_KEY)

    assert entries == before
    reformatted = out[1]["reformatted_fields"]
    assert reformatted["title"] == before[1]["reformatted_fields"]["title"]
    assert reformatted["start_date"]["original"] == "2014-09"
    assert reformatted["start_date"]["reformatted"] == "2016-09-14"
    assert reformatted["end_date"]["original"] == "2009"
    assert reformatted["end_date"]["reformatted"] == ""
    assert reformatted["start_date"]["reason"] != reformatted["end_date"]["reason"]


# #1435: an undated twin stage 3b kept apart by its group year, and stamped
# with it, is dated in that year.

def _twin(stamp, text="Example seminar series, Example City", **fields):
    entry = {"text": text, "hierarchy": ["Invited Talks"], "extracted_fields": fields}
    if stamp is not None:
        entry[GROUP_YEAR_KEY] = stamp
    return entry


def _twin_fields(*entries):
    return [e["extracted_fields"] for e in date_group_year_twins(list(entries))]


@pytest.mark.parametrize("fields, expected", [
    # a talk or award: its one date
    ({"location": "Example City", "date": None},
     {"location": "Example City", "date": "2019"}),
    # a code whose one date is a year
    ({"title": "Example abstract", "year": None},
     {"title": "Example abstract", "year": "2019"}),
    # a course: both ends of its span, within the one year
    ({"course_title": "Example course", "start_date": None, "end_date": None},
     {"course_title": "Example course", "start_date": "2019", "end_date": "2019"}),
    # a span whose end field stage 4 left out: both ends all the same
    ({"start_date": ""}, {"start_date": "2019", "end_date": "2019"}),
    # `date` wins over `year` and a span, `year` over a span
    ({"date": None, "year": None}, {"date": "2019", "year": None}),
    ({"date": None, "start_date": None}, {"date": "2019", "start_date": None}),
    ({"year": None, "start_date": None}, {"year": "2019", "start_date": None}),
])
def test_an_undated_twin_is_dated_in_its_group_year(fields, expected):
    assert _twin_fields(_twin("2019", **fields)) == [expected]


@pytest.mark.parametrize("fields", [
    {"date": "2021-05"},                                   # stage 4 found its date
    {"date": None, "year": "2021"},
    {"date": None, "start_date": "2003", "end_date": "2004"},
    {"date": None, "issue_date": "2010"},                   # any *_date field
    {"title": "Example title"},                             # no date field to fill
    {},
])
def test_a_date_stage_4_extracted_or_no_date_field_leaves_the_twin_alone(fields):
    entry = _twin("2019", **fields)
    assert date_group_year_twins([entry])[0] is entry


@pytest.mark.parametrize("entry", [
    _twin(None, date=None),                                 # not a twin
    {**_twin(None, date=None), GROUP_YEAR_KEY: None},
    {**_twin(None, date=None), GROUP_YEAR_KEY: 2019},       # not 3b's string
    {"text": "Example", GROUP_YEAR_KEY: "2019", "extracted_fields": None},
    {"text": "Example", GROUP_YEAR_KEY: "2019"},
])
def test_an_entry_without_a_group_year_or_fields_is_left_alone(entry):
    assert date_group_year_twins([entry])[0] is entry


def test_the_group_year_fill_is_recorded_and_the_input_is_not_mutated():
    entry = _twin("2018", start_date="", end_date=None)
    entry["reformatted_fields"] = {"title": {"original": "x", "reformatted": "y", "reason": "r"}}
    other = _twin(None, date=None)
    before = copy.deepcopy([entry, other])

    out = date_group_year_twins([entry, other])

    assert [entry, other] == before
    assert out[1] is other
    reformatted = out[0]["reformatted_fields"]
    assert reformatted["title"] == before[0]["reformatted_fields"]["title"]
    assert reformatted["start_date"]["original"] == ""
    assert reformatted["end_date"]["original"] is None
    assert reformatted["start_date"]["reformatted"] == "2018"
    assert reformatted["end_date"]["reformatted"] == "2018"
    assert "year group" in reformatted["start_date"]["reason"]
    assert out[0][GROUP_YEAR_KEY] == "2018"
    assert out[0]["text"] == entry["text"]


def test_identical_twins_in_two_years_come_out_with_two_dates():
    first, second = _twin("2019", date=None), _twin("2017", date=None)

    assert [f["date"] for f in _twin_fields(first, second)] == ["2019", "2017"]
