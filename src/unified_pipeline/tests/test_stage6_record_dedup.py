"""Record-level dedup (`stage6/record_dedup.py`, #666, EBYSBC E28/E10).

Every fixture is invented. Each rule has a positive case shaped like the
EBYSBC finding it repairs and the guards that keep a record of another rank,
date, part or venue on the page (#1369).

    python3 -m pytest src/unified_pipeline/tests/test_stage6_record_dedup.py -q -p no:cacheprovider
"""
import copy
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage6.record_dedup import deduplicate_record_groups  # noqa: E402
from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402


def _entry(code: str, idx: int, text: str, **fields) -> dict:
    return {"taxonomy_code": code, "element_idx_start": idx, "text": text,
            "hierarchy": ["Section"], "extracted_fields": fields}


def _run(entries: list[dict]) -> tuple[dict, list[dict], set[int]]:
    grouped: dict[str, list[dict]] = {}
    for entry in entries:
        grouped.setdefault(entry["taxonomy_code"], []).append(entry)
    decisions: list[dict] = []
    dropped: set[int] = set()
    deduplicate_record_groups(grouped, decisions, dropped)
    return grouped, decisions, dropped


def _indexes(grouped: dict) -> list[int]:
    return sorted(e["element_idx_start"] for group in grouped.values() for e in group)


# Grant family: one grant coded M2A under Honors and M2B under Grant support (AKPQEB-04).

def _grant(code: str, idx: int, **extra) -> dict:
    fields = {"title": "Widget Flux in Quenby Cells", "pi_role": "Co-Investigator",
              "agency": "NWF", "start_date": "2031", "end_date": None}
    fields.update(extra)
    return _entry(code, idx, f"Co-Investigator. NWF. Widget Flux in Quenby Cells. 2031. {idx}", **fields)


def test_a_grant_in_two_m2_codes_keeps_the_fuller_copy() -> None:
    sparse = _grant("M2A", 10)
    full = _grant("M2B", 50, total_funding="$100,000", percent_effort="5%")
    grouped, decisions, dropped = _run([sparse, full])
    assert grouped["M2A"] == [] and grouped["M2B"] == [full]
    assert dropped == {id(sparse)}
    assert decisions[0]["metric"] == "record=grant_family"
    assert (decisions[0]["code"], decisions[0]["kept_code"]) == ("M2A", "M2B")
    assert decisions[0]["dropped_text"] == sparse["text"]


@pytest.mark.parametrize("change", [
    {"start_date": "2032"},                        # another year: a renewal
    {"title": "Widget Flux in Quenby Cells II"},   # another title
    {"pi_role": "Principal Investigator"},         # another role
    {"agency": "Gadget Trust"},                    # another funder
    {"end_date": "2034"},                          # another end year
])
def test_a_grant_that_differs_in_a_field_stays(change: dict) -> None:
    grouped, decisions, _ = _run([_grant("M2A", 10, end_date="2033"), _grant("M2B", 50, **change)])
    assert _indexes(grouped) == [10, 50] and decisions == []


def test_a_dropped_grant_copy_neither_drops_nor_vouches_again() -> None:
    """Three copies: the sparse M2A copy goes once, then the M2C copy goes to
    the fuller M2B one; neither is dropped twice."""
    sparse = _grant("M2A", 10)
    full = _grant("M2B", 50, total_funding="$100,000", percent_effort="5%")
    middle = _grant("M2C", 70, total_funding="$100,000")
    grouped, decisions, _ = _run([sparse, full, middle])
    assert _indexes(grouped) == [50]
    assert [d["dropped_text"] for d in decisions] == [sparse["text"], middle["text"]]


def test_an_entry_already_dropped_is_not_paired_again() -> None:
    """The M2B copy goes to the first M2A copy; the second M2A copy, which the
    cross-code rule may not drop against its own code, does not drop it again."""
    first = _grant("M2A", 10, total_funding="$100,000", percent_effort="5%")
    second = _grant("M2A", 20, total_funding="$100,000")
    sparse = _grant("M2B", 50)
    grouped, decisions, _ = _run([first, second, sparse])
    assert _indexes(grouped) == [10, 20]
    assert [d["dropped_text"] for d in decisions] == [sparse["text"]]


def test_two_grant_copies_with_no_start_date_stay() -> None:
    """An empty date names no year, so it never matches another empty date."""
    grouped, decisions, _ = _run([_grant("M2A", 10, start_date=None),
                                  _grant("M2B", 50, start_date=None, total_funding="$1")])
    assert _indexes(grouped) == [10, 50] and decisions == []


def test_two_grant_copies_with_no_title_stay() -> None:
    grouped, decisions, _ = _run([_grant("M2A", 10, title=None),
                                  _grant("M2B", 50, title=None, total_funding="$1")])
    assert _indexes(grouped) == [10, 50] and decisions == []


@pytest.mark.parametrize("roles", [("Co-Investigator (Site)", "Co-Investigator"),
                                   ("Co-Investigator", "Co-Investigator (Site)")])
def test_a_grant_role_inside_the_other_pairs_in_either_order(roles: tuple[str, str]) -> None:
    grouped, decisions, _ = _run([_grant("M2A", 10, pi_role=roles[0]),
                                  _grant("M2B", 50, pi_role=roles[1], total_funding="$1")])
    assert _indexes(grouped) == [50] and len(decisions) == 1


def test_the_return_value_counts_the_drops() -> None:
    grouped = {"M2A": [_grant("M2A", 10)], "M2B": [_grant("M2B", 50, total_funding="$1")],
               "H": [_honor(5, "Quenby Foundation Civic Widget Award for Service", "2031"),
                     _honor(9, "Quenby Foundation Civic Widget Award", "2031")]}
    assert deduplicate_record_groups(grouped, [], set()) == 2


def test_two_grant_numbers_stay_and_a_same_code_pair_is_left_to_text_dedup() -> None:
    grouped, _, _ = _run([_grant("M2A", 10, grant_number="R01 1111"),
                          _grant("M2B", 50, grant_number="R01 2222")])
    assert _indexes(grouped) == [10, 50]
    grouped, _, _ = _run([_grant("M2B", 10), _grant("M2B", 50, total_funding="$1")])
    assert _indexes(grouped) == [10, 50]


# Honors: one award listed under two headings, one name a run of the other's (EOSAFF-06).

def _honor(idx: int, name: str, date: str) -> dict:
    return _entry("H", idx, f"{name} {date}", award_name=name, granting_body=None, date=date)


def test_an_award_named_inside_the_other_in_the_same_year_drops_the_shorter() -> None:
    long = _honor(5, "Quenby Foundation Civic Widget Award, Youth Gadget Award and Grant", "2031-05")
    short = _honor(9, "Quenby Foundation Civic Widget Award, Youth Gadget", "2031")
    grouped, decisions, _ = _run([long, short])
    assert grouped["H"] == [long]
    assert decisions[0]["metric"] == "record=honor_name"


@pytest.mark.parametrize("short_name, short_date", [
    ("Quenby Foundation Civic Widget Award", "2032"),          # another year
    ("Quenby Foundation Civic Widget Award", "2031-04"),       # another month
    ("Civic Widget Award", "2031"),                            # too generic a name
    ("Quenby Foundation Civic Award", "2031"),                 # not a word run
])
def test_an_award_of_another_date_or_name_stays(short_name: str, short_date: str) -> None:
    long = _honor(5, "Quenby Foundation Civic Widget Award for Service", "2031-05")
    grouped, _, _ = _run([long, _honor(9, short_name, short_date)])
    assert _indexes(grouped) == [5, 9]


def test_awards_with_no_date_stay() -> None:
    """One award won in two periods may reach stage 6 with an empty date on
    both copies; two empty dates are not the same year (#1369)."""
    grouped, decisions, _ = _run([_honor(5, "Quenby Foundation Civic Widget Award for Service", ""),
                                  _honor(9, "Quenby Foundation Civic Widget Award", "")])
    assert _indexes(grouped) == [5, 9] and decisions == []


def test_an_award_of_another_rank_or_part_stays() -> None:
    grouped, _, _ = _run([_honor(5, "Senior Quenby Foundation Widget Research Award", "2031"),
                          _honor(9, "Quenby Foundation Widget Research Award", "2031")])
    assert _indexes(grouped) == [5, 9]
    grouped, _, _ = _run([_honor(5, "Quenby Foundation Widget Lecture Award Part 2", "2031"),
                          _honor(9, "Quenby Foundation Widget Lecture Award", "2031")])
    assert _indexes(grouped) == [5, 9]


def test_an_award_from_another_granting_body_stays() -> None:
    long = _honor(5, "Distinguished Widget Service Award for Teaching", "2031")
    short = _honor(9, "Distinguished Widget Service Award", "2031")
    long["extracted_fields"]["granting_body"] = "Quenby Society"
    short["extracted_fields"]["granting_body"] = "Norvale Gadget Guild"
    grouped, _, _ = _run([long, short])
    assert _indexes(grouped) == [5, 9]


# Training and leadership under a university and its affiliated hospital (EQGGRB-07).

def _training(idx: int, specialty: str, institution: str, start: str = "2031",
              end: str = "2033") -> dict:
    return _entry("C", idx, f"{start}-{end} {institution} {specialty} Fellow",
                  training_type="Fellowship", specialty=specialty, institution=institution,
                  start_date=start, end_date=end)


def test_a_fellowship_listed_under_an_affiliate_keeps_the_longer_specialty() -> None:
    kept = _training(26, "Pediatric Widget Care Medicine", "Norvale University")
    dropped = _training(34, "Pediatric Widget Care", "Gadget Children's Hospital at Norvale, Quenby, ZQ")
    grouped, decisions, _ = _run([kept, dropped])
    assert grouped["C"] == [kept]
    assert decisions[0]["metric"] == "record=training_affiliate"


@pytest.mark.parametrize("other", [
    {"institution": "Quexley University"},                 # no shared word: another institution
    {"start": "2030"},                                     # another start year
    {"end": "2034"},                                       # another end year
    {"specialty": "Pediatric Gadget Care"},                # another specialty
])
def test_a_fellowship_at_another_place_time_or_specialty_stays(other: dict) -> None:
    args = {"specialty": "Pediatric Widget Care", "institution": "Gadget Children's Hospital at Norvale"}
    args.update(other)
    grouped, _, _ = _run([_training(26, "Pediatric Widget Care Medicine", "Norvale University"),
                          _training(34, **args)])
    assert _indexes(grouped) == [26, 34]


def test_fellowships_with_no_dates_stay() -> None:
    grouped, decisions, _ = _run([
        _training(26, "Pediatric Widget Care Medicine", "Norvale University", "", ""),
        _training(34, "Pediatric Widget Care", "Gadget Children's Hospital at Norvale", "", "")])
    assert _indexes(grouped) == [26, 34] and decisions == []


@pytest.mark.parametrize("long_first", [False, True])
def test_equal_fellowship_copies_keep_the_longer_text(long_first: bool) -> None:
    """Same specialty and the same filled fields: the copy whose text says
    more is kept, wherever it sits in the list."""
    short = _training(26, "Pediatric Widget Care", "Norvale University")
    long = _training(34, "Pediatric Widget Care", "Gadget Children's Hospital at Norvale, Quenby, ZQ")
    grouped, decisions, _ = _run([long, short] if long_first else [short, long])
    assert grouped["C"] == [long]
    assert decisions[0]["dropped_text"] == short["text"]


def test_the_shorter_specialty_drops_even_when_it_has_more_fields() -> None:
    kept = _training(26, "Pediatric Widget Care Medicine", "Norvale University")
    dropped = _training(34, "Pediatric Widget Care", "Gadget Children's Hospital at Norvale")
    dropped["extracted_fields"]["program_director"] = "Dr. Quenby"
    grouped, _, _ = _run([kept, dropped])
    assert grouped["C"] == [kept]


@pytest.mark.parametrize("institution, pairs", [("NGCHQZXW", True), ("NGCHQZXWV", False)])
def test_an_acronym_is_at_most_eight_capitals(institution: str, pairs: bool) -> None:
    grouped, _, _ = _run([_leader(23, "Norvale University"), _leader(212, institution)])
    assert _indexes(grouped) == ([23] if pairs else [23, 212])


def test_a_one_word_specialty_never_pairs() -> None:
    grouped, _, _ = _run([_training(26, "Widgetry", "Norvale University"),
                          _training(34, "Widgetry", "Norvale University")])
    assert _indexes(grouped) == [26, 34]


def _leader(idx: int, institution: str, unit: str = "Widget Consult Service",
            start: str = "2031", role: str = "Medical Director", end: str = "2034") -> dict:
    return _entry("L3", idx, f"{start}-{end} {role} {unit} {institution}",
                  leadership_role=role, institution=institution,
                  unit_program=unit, start_date=start, end_date=end)


def test_a_leadership_role_under_an_acronym_affiliate_drops_once() -> None:
    kept = _leader(23, "Norvale University, Department of Gadgets")
    grouped, decisions, _ = _run([kept, _leader(212, "NGCH")])
    assert grouped["L3"] == [kept]
    assert decisions[0]["metric"] == "record=leadership_affiliate"


@pytest.mark.parametrize("other", [
    {"unit": "Gadget Consult Service"}, {"start": "2030"}, {"end": "2035"},
    {"institution": "Quexley Clinic"}, {"role": "Associate Medical Director"},
    {"start": "", "end": ""}])
def test_a_leadership_role_of_another_unit_span_or_place_stays(other: dict) -> None:
    args = {"institution": "Norvale Children's Hospital"}
    args.update(other)
    first = {key: args[key] for key in ("start", "end") if key in args and not args[key]}
    grouped, _, _ = _run([_leader(23, "Norvale University", **first), _leader(212, **args)])
    assert _indexes(grouped) == [23, 212]


@pytest.mark.parametrize("empty", [{"unit": ""}, {"role": ""}])
def test_leadership_rows_with_an_empty_role_or_unit_stay(empty: dict) -> None:
    grouped, decisions, _ = _run([_leader(23, "Norvale University", **empty),
                                  _leader(212, "Norvale Children's Hospital", **empty)])
    assert _indexes(grouped) == [23, 212] and decisions == []


def test_an_acronym_does_not_pair_with_a_row_that_names_no_institution() -> None:
    grouped, _, _ = _run([_leader(23, ""), _leader(212, "NGCH")])
    assert _indexes(grouped) == [23, 212]


# An undated appointment line repeating a dated one (RVROVQ-05, OTBUCZ-03, QNZADH-07).

def _appointment(idx: int, title: str, institution: str | None = None,
                 start: str | None = None, end: str | None = None, text: str | None = None) -> dict:
    return _entry("D1", idx, text or f"{title} {institution or ''}", title=title,
                  institution=institution, start_date=start, end_date=end)


def test_an_undated_title_inside_a_current_dated_row_drops() -> None:
    dated = _appointment(23, "Associate Prof.", "Dept. of Widgets, Norvale Univ.", "2031-07", "present",
                         text="7/2031-present Associate Prof., Dept. of Widgets, Norvale Univ.")
    undated = _appointment(8, "Associate Professor", text="Title: Associate Professor")
    grouped, decisions, _ = _run([undated, dated])
    assert grouped["D1"] == [dated]
    assert decisions[0]["metric"] == "record=undated_appointment"


def test_an_undated_banner_of_titles_held_by_appointment_and_leadership_rows_drops() -> None:
    banner = _appointment(0, "Professor of Widgetry; Deputy Director, Gadget Institute")
    appointment = _appointment(35, "Professor with tenure, Department of Widgetry",
                               "Norvale University", "2031", "present")
    leader = _entry("O", 51, "Deputy Director 2032-present", leadership_role="Deputy Director",
                    institution="Gadget Institute", start_date="2032", end_date="present")
    grouped, decisions, _ = _run([banner, appointment, leader])
    assert grouped["D1"] == [appointment] and grouped["O"] == [leader]
    assert decisions[0]["kept_code"] == "D1"


def test_an_undated_institution_with_a_plural_ending_still_matches() -> None:
    dated = _appointment(28, "Assistant Professor, Widgetry", "Norvale Health Sciences Center",
                         "2031", "present")
    undated = _appointment(4, "Assistant Professor, Widgetry", "Norvale Health Science Center")
    grouped, _, _ = _run([undated, dated])
    assert grouped["D1"] == [dated]


@pytest.mark.parametrize("undated", [
    _appointment(8, "Professor"),                                   # another rank
    _appointment(8, "Associate Professor", "Quexley College"),      # another institution
    _appointment(8, "Associate Professor; Chair, Gadget Council"),  # a title held nowhere else
    _appointment(8, "Associate Professor", text="Associate Professor 2031"),  # a year in its text
    _appointment(8, "Associate Professor of Gadgetry"),             # a word the dated row lacks
])
def test_an_undated_row_of_another_rank_place_or_title_stays(undated: dict) -> None:
    dated = _appointment(23, "Associate Professor of Widgetry", "Norvale University", "2031", "present")
    grouped, _, _ = _run([copy.deepcopy(undated), dated])
    assert _indexes(grouped) == [8, 23]


def test_an_undated_title_held_by_a_leadership_row_names_its_code() -> None:
    banner = _appointment(0, "Deputy Director, Gadget Institute")
    leader = _entry("O", 51, "Deputy Director 2032-present", leadership_role="Deputy Director",
                    institution="Gadget Institute", start_date="2032", end_date="present")
    grouped, decisions, _ = _run([banner, leader])
    assert grouped["D1"] == [] and (decisions[0]["code"], decisions[0]["kept_code"]) == ("D1", "O")


def test_a_short_word_keeps_its_final_s() -> None:
    """"Arts" is not "Art": only words longer than four letters lose a plural s."""
    dated = _appointment(23, "Associate Professor of Art", "Norvale University", "2031", "present")
    grouped, _, _ = _run([_appointment(8, "Associate Professor of Arts"), dated])
    assert _indexes(grouped) == [8, 23]


def test_an_undated_row_is_not_held_by_a_past_row() -> None:
    dated = _appointment(23, "Associate Professor of Widgetry", "Norvale University", "2031", "2035")
    grouped, _, _ = _run([_appointment(8, "Associate Professor"), dated])
    assert _indexes(grouped) == [8, 23]


def test_stage6_dedup_runs_the_record_rules_and_records_the_drop() -> None:
    """The wire: `_dedup_grouped_entries` drops the cross-code grant copy and
    writes its decision for the sidecar."""
    gen = WCMTemplateGenerator(verbose=False)
    sparse, full = _grant("M2A", 10), _grant("M2B", 50, total_funding="$100,000", percent_effort="5%")
    grouped, pre_dedup, decisions = gen._dedup_grouped_entries(
        gen._group_entries_by_code([sparse, full]))
    assert [e["element_idx_start"] for e in grouped.get("M2A", []) + grouped.get("M2B", [])] == [50]
    assert any(d["metric"] == "record=grant_family" for d in decisions)
    assert any(e["element_idx_start"] == 10 for group in pre_dedup.values() for e in group)
