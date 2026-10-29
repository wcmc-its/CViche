"""#1575: `stage4.grounding.ground_group`, the check that a group reply item's
values come from its own entry's text.

What each test pins:

- a value its own entry lacks but another entry states is removed;
- the entry's own text is read generously, so a year it does state is never
  called borrowed (two-digit years, short range ends, a mistyped digit, a
  year run into other digits, a year mistyped by one keystroke in a date
  shape), nor an effort written as a fraction of a full-time post (#1840);
- the one-keystroke reading skips a run that is not a word of its own (after
  "$", inside an identifier, a page after a volume, a run that continues),
  a real year and a cut-off year, and the run-into-digits reading skips a
  dollar amount (#1840; the two- and three-digit readings still read
  amounts, as before; an identifier or amount written in a date shape, such
  as "Protocol 2018-3019", still reads as a date);
- another entry's text is read strictly (a four-digit year, a percent);
- `tokens_not_in_own_text` and `entry_holding_token`, which the doctor's
  `cross_entry_value` lint reads, give the same verdicts field by field;
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

from unified_pipeline.stage4.grounding import (  # noqa: E402
    entry_holding_token,
    ground_group,
    text_facts,
    tokens_not_in_own_text,
)


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
    # #1840: the stored shapes stage 4 nulled and re-extracted.
    ("Percent Effort: 4 hours a week/0.100 FTE", "percent_effort", "10%"),   # RNKYST 197
    ("Effort .08FTE", "percent_effort", "8%"),
    ("I.D.: B-58\tFTE 0.100\tRole: Site PI", "percent_effort", "10.0%"),
    ("2009-20011 | Clinical Image Retrieval", "end_date", "2011"),           # web172 123.0
    ("NICHD (4/1/2016-3/31/2018 in NCE to 3/31/3019, $437,777)", "end_date", "2019-03-31"),
    ("Ann Chir Gynaecol Journal, 1191, 80:224-229.", "year", "1991"),        # SVYSGY 487
    ("Cancer Prevention and Detection. 1196;20:108-113.", "year", "1996"),   # BYFQBG 1112
    ("Palo Alto, CA, February 20015", "date", "2015-02"),                    # RGUNJV 2113
    ("Special Emphasis Panel. December 9-10, 3013.", "start_date", "2013-12-09"),  # web32 1152
    ("National Chapter of the Year -20218", "date", "2018"),                 # XVSQFF 37
    ("Issued 3/3019", "date", "2019-03"),                                    # m/yyyy (SPIHVQ "(2/1018)")
    ("2106-present Member", "start_date", "2006"),                           # CKDDEG
    ("2107-current Maple", "start_date", "2007"),
    ("Neurosurgical Focus 2104;36(1):E5.", "year", "2004"),                  # farm 2100
    ("Effort 0.07 FTE", "percent_effort", "7%"),                             # 0.07 * 100 is not exactly 7
    ("Effort FTE: 0.25", "percent_effort", "25%"),
    ("Effort 1.0 FTE", "percent_effort", "100%"),                            # a whole post is at most 1
    ("Effort 0.125 FTE", "percent_effort", "12.5%"),                         # not rounded to 12 or 13
    ("Effort 0.1234 FTE", "percent_effort", "12.34%"),
    ("Period\t2009-20011", "end_date", "2011"),                              # after a tab
    ("Bialosky J. (2/1018). Biomarkers", "year", "2018"),                    # SPIHVQ: in brackets
    ("Westchester 10/17/2108", "date", "2008-10-17"),                        # CXRYCF
    ("Ends 3/1/3019", "end_date", "2019-03-01"),
    ("Panel, Dec  9-10,  3013", "start_date", "2013-12-09"),
    ("Reviewer through June 30, 1014", "end_date", "2014-06-30"),            # NJHLUR
    ("Candidate talk, 18 Nov. 20011, Gainesville", "date", "2011-11"),       # ECXGAT
    ("Dental Meeting. DECEMBER 2109.", "date", "2009-12"),
    ("Fellow 1971-19373", "end_date", "1973"),                               # EJPAIA
    ("2009 - 20011 Clinical", "end_date", "2011"),
    ("2009\u201320011 Clinical", "end_date", "2011"),
    ("20011 - 2014 Chair", "start_date", "2011"),
    ("J Clin 1196;20(12):108", "year", "1996"),
])
def test_a_year_or_amount_the_entry_states_in_another_form_is_its_own(own_text, field, value):
    other = ("Another entry 2000 2004 2006 2017 2018 2022 2025 1988 $1,200,000 7% 8% 10% 10.0% 1991 1996 "
             "2007 2011 2013 2015 2019 25% 100% 2008 2009 2014 1973")
    assert _borrowed([own_text, other], {0: [{field: value}]}) == {}


@pytest.mark.parametrize(("own_text", "field", "value"), [
    ("Total Costs: $19900", "year", "1990"),            # an amount: #1840's named risk
    ("Total Costs: $20011", "end_date", "2011"),
    ("Direct costs $3019 per year", "end_date", "2019"),
    ("Award: $ 3019 - present", "start_date", "2019-01-01"),
    ("Award $3019 - present", "start_date", "2019"),     # a dollar sign right before the run
    ("Award $3019-2020", "start_date", "2019"),
    ("Award $\t3019 - present", "start_date", "2019"),  # any blank after the dollar sign
    ("Award $\xa03019 - present", "start_date", "2019"),
    ("Grant 1234-3019", "start_date", "2019"),         # a range end needs a real year at the other end
    ("pp 3019-3025", "year", "2019"),
    ("Ext 12-3019", "date", "2019"),                   # m-yyyy with a dash is not read
    ("reviewed 120/1019", "year", "2019"),             # a day or month is one or two digits
    ("MAY3019", "date", "2019-05"),                    # a month needs a blank before the year
    ("Budget: $12/3019", "date", "2019-12"),
    ("Grant M16-3019-2020", "start_date", "2019"),      # inside an identifier
    ("R01CA3019-2020", "start_date", "2019"),
    ("ph 212-3019-2020", "start_date", "2019"),         # a phone number
    ("Award $ 19900", "year", "1990"),                  # a spaced amount, run into other digits
    ("Budget 2009-20011,500", "end_date", "2011"),      # the run continues as a number
    ("Dec 3019.5 hours", "date", "2019-12"),
    ("May 3019K", "date", "2019-05"),                   # the run continues as a word
    ("Dec 3019th", "date", "2019-12"),
    ("3019 - 2020-0004", "start_date", "2019"),         # the range end continues an identifier
    ("J Immunol 141:1897-1903, 1988", "year", "1997"),  # a page range
    ("Vol 2, 1986-2202.", "year", "2002"),
    ("McGraw-Hill Inc. 2011; Vol 2,\t1986-2202.", "year", "2002"),   # CKDDEG
    ("Exp Res, 41, 1196-1906.", "year", "1996"),
    ("Antibiotics. 2023, 12, 1173 \u2013 1195.", "year", "1995"),
    ("Dr. Preston (1879-1928)", "year", "1979"),        # a real nineteenth-century year
    ("Protocol 2012-1013: A Pilot Study", "end_date", "2013"),
    ("Protocol 2020-0927", "year", "1927"),             # a leading 0 is a number, not a typo
    ("doi: 10.2217/cer-2020-0004", "year", "2004"),     # a DOI
    ("Protocol 3133K1-3000-US", "start_date", "2000"),  # an identifier
    ("June 2325, 1999, Crystal City", "date", "2025"),  # two days run together
    ("Invited talk, May 201", "date", "2011-05"),       # a cut-off year stays missing
    ("Total $1,19900", "year", "1990"),                 # the amount's digits after a comma
    ("Total $5.19900", "year", "1990"),                 # ... or a point
    ("Grant M16-2009-20011", "end_date", "2011"),       # each date shape needs a word of its own
    ("R01CA3019, 12:44", "year", "2019"),
    ("Dismay 3019", "year", "2019"),
    ("3/12/3019/45", "date", "2019-03-12"),
    ("Cohort 3019, 12 patients", "year", "2019"),       # a citation needs its volume's ":"
    ("Blood 106: 2019-3025", "year", "2025"),           # a page range after a volume
    ("Blood 106: 3019-2020", "year", "2019"),
    ("Talk, May 2015", "date", "2016"),                 # a real year is never a typo
    ("Talk, Dec 1979", "date", "1909"),
])
def test_a_year_is_never_read_out_of_an_amount_a_page_or_an_identifier(own_text, field, value):
    """#1840: the one-keystroke year is read only in a date shape that is a
    word of its own, and a year is not read as run into a dollar amount's
    digits, so another entry's year stays borrowed."""
    other = "Another entry 1909 1927 1979 1990 1995 1996 1997 2000 2002 2004 2011 2013 2016 2019 2025"
    assert _borrowed([own_text, other], {0: [{field: value}]}) == {0: [field]}


@pytest.mark.parametrize(("own_text", "value"), [
    ("Team of 1.5 FTE", "150%"),
    ("Team of 1.05 FTE", "105%"),
    ("Team of 11 FTE", "100%"),     # not the "1" at the end of 11
    ("Team of 10.5 FTE", "50%"),    # not the 0.5 at the end of 10.5
])
def test_an_fte_over_one_is_not_a_fraction_of_a_post(own_text, value):
    """'1.5 FTE' is a head count, not 150% effort."""
    assert _borrowed([own_text, f"Other grant, Effort: {value}"],
                     {0: [{"percent_effort": value}]}) == {0: ["percent_effort"]}


@pytest.mark.parametrize(("own_text", "value"), [
    ("Effort 0.125 FTE", "12%"),
    ("Effort 0.1234 FTE", "12.3%"),
])
def test_an_fte_fraction_is_compared_unrounded(own_text, value):
    """'0.125 FTE' is 12.5%, so another entry's 12% stays borrowed."""
    assert _borrowed([own_text, f"Other grant, Effort: {value}"],
                     {0: [{"percent_effort": value}]}) == {0: ["percent_effort"]}


@pytest.mark.parametrize("month", [
    "January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
    "November", "December", "Jan", "Feb", "Mar", "Apr", "Jun", "Jul", "Aug", "Sep", "Sept", "Oct", "Nov", "Dec",
])
def test_a_mistyped_year_after_any_month_is_its_own(month):
    assert _borrowed([f"Invited talk, {month} 3019", "Other talk 2019"], {0: [{"year": "2019"}]}) == {}


def test_an_fte_fraction_is_only_an_effort():
    """'0.100 FTE' is 10% effort, not an amount of 10."""
    assert _borrowed(["Grant A, Effort 0.100 FTE", "Grant B, award $10"],
                     {0: [{"total_funding": "$10"}]}) == {0: ["total_funding"]}


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


def test_the_field_by_field_checks_give_ground_groups_verdict():
    own = text_facts("NCI R01 (MPI, Effort: 20%) $326,020/year 04/21/17-03/31/23")
    others = [text_facts("Health Rhythms (PI) $300,000 04/01/2022-2023"),
              text_facts("NARSAD (PI, Effort: 30%) $30,000/year 07/01/1997-06/30/1999")]

    assert tokens_not_in_own_text("start_date", "1997-07-01", own) == (["1997"], False)
    assert tokens_not_in_own_text("start_date", "2017-04-21", own) == ([], True)
    assert tokens_not_in_own_text("pi_role", "PI", own) == ([], False)
    assert entry_holding_token("start_date", "1997", others) == 1
    assert entry_holding_token("percent_effort", "30", others) == 1
    assert entry_holding_token("start_date", "1985", others) is None


def test_a_record_with_any_value_of_its_own_is_kept_and_only_its_borrowed_field_cleared():
    """A record is foreign only when none of its checked values is the
    entry's own: one own start year keeps it, whichever field comes last."""
    texts = ["Mentee B, 2017-18", "Mentee C (2021-2022)"]
    own = {"mentee_name": "Mentee B", "start_date": "2017", "end_date": "2018"}
    mixed = {"mentee_name": "Mentee B", "start_date": "2017", "end_date": "2022"}

    result = ground_group(texts, {0: [own, mixed]})

    assert result.items[0] == [own, {**mixed, "end_date": None}]
    assert result.dropped_records == {}
    assert result.borrowed_fields == {0: ["end_date"]}
