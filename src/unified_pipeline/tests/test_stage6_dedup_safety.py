"""Dedup safe-drop guard (#227): deduplicate_entries may only drop an entry
when the loss is provably recoverable — verbatim containment in the kept
entry, full significant-word containment of a token-rich entry, or a fused
multi-record sibling the #221/#225 recovery pass re-verifies. Similarity
scores alone must not drop single-record entries: on 2Q1_ZQ, 7 of 8 such
drops were distinct records lost (distinct degrees, editorial boards,
journal-review rows, multi-part paper series, venue instances).

Fixtures mirror the 2Q1_ZQ decision shapes with fictional content. Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_dedup_safety.py -p no:cacheprovider
"""

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage6.dedup import (  # noqa: E402
    _dates_compatible,
    _drop_is_safe,
    _names_record,
    _record_name,
    recovered_row_already_rendered,
)
from unified_pipeline.stage6.fan_out import _RENDERED_FIELDS  # noqa: E402
from unified_pipeline.stage_6_word_template import deduplicate_entries  # noqa: E402


def _texts(entries):
    return [e["text"] for e in entries]


# --------------------------------------------------- losses that must be kept

def test_distinct_degrees_kept():
    # 2Q1_ZQ B1: the tokenizer destroys 'M.S'/'PhD', leaving only the shared
    # field of study — similarity is high but the records are distinct.
    ms = {"text": "M.S: Recreational Cartography Studies"}
    phd = {"text": "PhD: Recreational Cartography Studies"}
    assert deduplicate_entries([phd, ms]) == [phd, ms]


def test_distinct_journal_review_rows_kept():
    # 2Q1_ZQ Q4D: different journals sharing only the service date tokens.
    rows = [{"text": "Journal of Improbable Results (Jan 2024-Present)"},
            {"text": "Annals of Speculative Chemistry (Jan 2024-Present)"}]
    assert deduplicate_entries(list(rows)) == rows


def test_distinct_editorial_boards_kept():
    # 2Q1_ZQ Q4C: role boilerplate dominates; one token distinguishes.
    rows = [{"text": "Editorial Board Member, Plumbob Medical Education, (2025-Present)"},
            {"text": "Editorial Board Member, Gormless Journal of Medical Education, (2025-Present)"}]
    assert deduplicate_entries(list(rows)) == rows


def test_multi_part_paper_series_kept():
    # 2Q1_ZQ S8 drops 6/7: same authors/venue/topic, distinct papers.
    rows = [{"text": "Trebor S, Gnuj E. A case study on paradigm change in "
                     "Ovaltinia: formative research for an individual session. "
                     "AECT International Convention; November 2014; Jacksonville, FL."},
            {"text": "Gnuj E, Trebor S. Macro-level formative research on "
                     "state-level paradigm change in Ovaltinia: a case study. "
                     "AECT International Convention; November 2014; Jacksonville, FL."}]
    assert deduplicate_entries(list(rows)) == rows


def test_same_talk_distinct_venue_instances_kept():
    # 2Q1_ZQ S8 drop 5: identical title, different conference instance.
    rows = [{"text": "Does management reasoning display context specificity? "
                     "Poster presented at: 2025 Founder's Day Research Week."},
            {"text": "Does management reasoning display context specificity? "
                     "Poster presented at: 2026 International HPE Virtual Conference."}]
    assert deduplicate_entries(list(rows)) == rows


# ------------------------------------------------ true duplicates still drop

def test_distinct_trial_phases_of_one_drug_kept():
    # #1106 (web059 M2A): every significant word of the Phase I trial is in
    # the randomized Phase II trial; only the phase tells them apart.
    phase1 = {"text": "Phase I study of the invented inhibitor, ZX-101, in combination with examplecin."}
    phase2 = {"text": "Randomized Phase II study of ZX-101 (invented inhibitor) versus placebo "
                      "in combination with examplecin in patients with advanced disease "
                      "(US Principal Investigator)."}
    assert deduplicate_entries([phase1, phase2]) == [phase1, phase2]


@pytest.mark.parametrize("kept_phase,dropped_phase", [
    ("Phase II/III", "Phase III"), ("Phase I/II", "Phase II"),
])
def test_a_combined_phase_trial_covers_a_copy_naming_one_of_its_phases(kept_phase, dropped_phase):
    kept = {"text": f"Randomized {kept_phase} study of ZX-101 (invented inhibitor) versus placebo "
                    "in combination with examplecin in patients with advanced disease."}
    dup = {"text": f"{dropped_phase} study of ZX-101 invented inhibitor versus placebo with examplecin."}
    assert deduplicate_entries([kept, dup]) == [kept]


def test_same_trial_phase_written_two_ways_still_dropped():
    # "Phase 2" and "Phase II" are one phase, so the reworded copy still goes.
    kept = {"text": "Randomized Phase II study of ZX-101 (invented inhibitor) versus placebo "
                    "in combination with examplecin in patients with advanced disease."}
    dup = {"text": "Phase 2 study of ZX-101 invented inhibitor versus placebo with examplecin."}
    assert deduplicate_entries([kept, dup]) == [kept]


def test_verbatim_contained_line_dropped():
    fused = {"text": "October 2025-Present\nAssociate Professor of Whimsy, "
                     "Department of Applied Daydreams\nJune 2020-September 2025, "
                     "Assistant Professor of Whimsy, Department of Applied Daydreams"}
    dup = {"text": "Associate Professor of Whimsy, Department of Applied Daydreams"}
    assert deduplicate_entries([fused, dup]) == [fused]


def test_reworded_token_contained_duplicate_dropped():
    # 2Q1_ZQ D1 shape: reworded but every significant word (>=5 tokens)
    # appears in the kept entry.
    fused = {"text": "October 2025-Present, Associate Professor, School of "
                     "Ephemera, Department of Whimsical Sciences (DWS), "
                     "Untethered Services University of the Ephemeral "
                     "Sciences (USUES), Bethesda, Maryland"}
    dup = {"text": "Associate Professor, Whimsical Sciences, USUES"}
    assert deduplicate_entries([fused, dup]) == [fused]


def test_fused_multi_record_sibling_still_dropped():
    # The #221/#225 contract: a fused sibling with >=2 record lines may drop —
    # the recovery pass re-verifies its lines against the rendered document.
    kept = {"text": "Member | Committee on Subterranean Balloon Safety "
                    "Standards | Guild of Meandering Auditors | reviews annual "
                    "protocols and certification checklists for subterranean "
                    "balloon safety inspections across member lodges, "
                    "2013-2016"}
    sibling = {"text": "Member | Committee on Subterranean Balloon Safety "
                       "Standards | Guild of Meandering Auditors\n"
                       "Chair | Panel of Improbable Weights and Measures "
                       "2013-2016 | Norvale Metrology Circle"}
    assert deduplicate_entries([kept, sibling]) == [kept]


def test_unsafe_drop_records_no_decision():
    decisions = []
    ms = {"text": "M.S: Recreational Cartography Studies"}
    phd = {"text": "PhD: Recreational Cartography Studies"}
    deduplicate_entries([phd, ms], decisions=decisions)
    assert decisions == []


# ------------------------------------- career-progression date-overlap guard

def test_date_aware_dedup_keeps_distinct_career_progression():
    # require_date_overlap=True is what actually protects DATE_AWARE_DEDUP_CODES
    # (D1/D2/D3/C/B1) in production (stage_6_word_template.py) -- these two
    # roles at the same institution would otherwise score similar enough to
    # dedup (shared institution/department vocabulary), but their dates don't
    # overlap. Never previously exercised by this file.
    resident = {"text": "Resident Physician, Department of Whimsical Medicine, "
                        "Untethered Services University, Jul 2018-Jun 2021"}
    chief = {"text": "Chief Resident, Department of Whimsical Medicine, "
                     "Untethered Services University, Jul 2021-Jun 2022"}
    result = deduplicate_entries([resident, chief], require_date_overlap=True)
    assert result == [resident, chief]


# ------------------------------------------------ #666: dates and dedup

def test_distinct_fused_terms_different_dates_kept():
    kept = {"text": "Chair | Committee on Institutional Research Compliance "
                    "and Ethics Standards Review | Office of Research "
                    "Integrity | oversees annual protocol review\n"
                    "Member | Subcommittee on Data Governance and Ethics "
                    "Standards Review | Office of Research Integrity | "
                    "2021-2024"}
    dropped = {"text": "Chair | Committee on Institutional Research "
                       "Compliance and Ethics Standards Review | Office of "
                       "Research Integrity | 2015-2018\n"
                       "Member | Subcommittee on Data Governance and Ethics "
                       "Standards Review | Office of Research Integrity | "
                       "quarterly meetings"}
    result = deduplicate_entries([kept, dropped])
    assert result == [kept, dropped]


def test_distinct_fused_terms_full_mmddyyyy_dates_kept():
    # Full MM/DD/YYYY ranges (M2B grant format): the "06/30/2018" end must not
    # be read as year 2030 and vouch for the later term.
    kept = {"text": "Chair | Committee on Institutional Research Compliance "
                    "and Ethics Standards Review | Office of Research "
                    "Integrity | oversees annual protocol review\n"
                    "Member | Subcommittee on Data Governance and Ethics "
                    "Standards Review | Office of Research Integrity | "
                    "07/01/2015-06/30/2018"}
    dropped = {"text": "Chair | Committee on Institutional Research "
                       "Compliance and Ethics Standards Review | Office of "
                       "Research Integrity | 07/01/2021-06/30/2024\n"
                       "Member | Subcommittee on Data Governance and Ethics "
                       "Standards Review | Office of Research Integrity | "
                       "quarterly meetings"}
    result = deduplicate_entries([kept, dropped])
    assert result == [kept, dropped]


# #666: two mentees fused into one un-split prose entry. One mentee's text is
# a literal substring of the other, so the verbatim branch approves the drop.
# Whether that loses the mentee depends on what the kept entry renders: the
# N3B section writes its fields, so the drop is safe only when those fields
# name the dropped mentee.
_ONE_MENTEE = "Mentor: Avery Quill, PhD Candidate, Dept of Tinkering"
_FUSED_MENTEES = (_ONE_MENTEE + " and Blair Sprocket, MS Candidate, Dept of "
                  "Gadgetry, Harbor Institute, 2019-2023")


def test_distinct_mentees_fused_into_prose_entry_kept():
    one = {"text": _ONE_MENTEE, "extracted_fields": {"mentee_name": "Avery Quill"}}
    fused = {"text": _FUSED_MENTEES,
             "extracted_fields": {"mentee_name": "Blair Sprocket"}}
    result = deduplicate_entries([one, fused], code="N3B", document=[one, fused])
    assert result == [one, fused]


def test_mentee_the_fused_entry_renders_is_still_dropped():
    # The kept fields name the dropped mentee: that row is already on the
    # page, and keeping the copy would print it twice.
    one = {"text": _ONE_MENTEE, "extracted_fields": {"mentee_name": "Avery Quill"}}
    fused = {"text": _FUSED_MENTEES,
             "extracted_fields": {"mentee_name": "Avery Quill"}}
    assert deduplicate_entries([one, fused], code="N3B",
                               document=[one, fused]) == [fused]


def test_fused_prose_without_fields_is_still_dropped():
    # With no fields the section writes the kept entry's text, which holds
    # both mentees, so dropping the verbatim copy loses nothing.
    one, fused = {"text": _ONE_MENTEE}, {"text": _FUSED_MENTEES}
    assert deduplicate_entries([one, fused], code="N3B",
                               document=[one, fused]) == [fused]


def test_distinct_committee_memberships_mentioned_in_passing_kept():
    prior_term = {"text": "Member, Data Safety Monitoring Board for the ABC "
                          "diabetes trial, 2015-2018"}
    current_term = {"text": "Member, Data Safety Monitoring Board, XYZ "
                            "diabetes trial, 2021-2024. Data safety "
                            "monitoring board service for NIH-funded "
                            "diabetes trials dates to 2015; this board "
                            "succeeds the ABC trial DSMB, which concluded "
                            "in 2018."}
    result = deduplicate_entries([prior_term, current_term])
    assert result == [prior_term, current_term]


def test_dropped_range_kept_when_kept_entry_states_no_range():
    # The dropped entry's dates would be lost: the kept one has none.
    dropped = {"text": "Chair, Committee on Curricular Harmony and Assessment "
                       "Review, School of Applied Whimsy, 2015-2018"}
    kept = {"text": "Chair, Committee on Curricular Harmony and Assessment "
                    "Review, School of Applied Whimsy, oversees annual "
                    "program review"}
    assert _drop_is_safe(dropped, kept) is False
    assert deduplicate_entries([dropped, kept]) == [dropped, kept]


def test_same_range_reworded_duplicate_still_dropped():
    kept = {"text": "Chair, Committee on Curricular Harmony and Assessment "
                    "Review, School of Applied Whimsy, 2015-2018, oversees "
                    "annual program review"}
    dup = {"text": "Chair, Committee on Curricular Harmony and Assessment "
                   "Review, School of Applied Whimsy, 2015 to 2018"}
    assert deduplicate_entries([dup, kept]) == [kept]


def test_undated_dropped_entry_is_date_compatible():
    kept = {"text": "Chair, Committee on Curricular Harmony and Assessment "
                    "Review, School of Applied Whimsy, 2015-2018, oversees "
                    "annual program review"}
    dup = {"text": "Chair, Committee on Curricular Harmony and Assessment "
                   "Review, School of Applied Whimsy"}
    assert deduplicate_entries([dup, kept]) == [kept]


@pytest.mark.parametrize("dropped, kept, compatible", [
    ("2015-2018", "2015-2018", True),
    ("2015-2018", "2017-2020", True),            # overlap
    ("2015-2018", "2021-2024", False),           # disjoint
    ("2015-18", "2021-24", False),               # two-digit ends
    ("2015-18", "2016-2019", True),
    ("07/01/2021-06/30/2024", "07/01/2015-06/30/2018", False),  # full MM/DD/YYYY end is not 20DD
    ("07/01/2021-06/30/2024", "07/01/2021-06/30/2024", True),
    ("Jul 2018-Jun 2021", "2018-2021", True),    # month-name ends
    ("07/2008 - 06/2009", "2008-2009", True),    # month/year ends
    ("07/2008 - 06/2009", "2010-2012", False),
    ("2012-present", "2019-2021", True),         # open end reaches everything after
    ("2012-present", "2001-2004", False),
    ("2015-2018", "2021-2024 also served 2015 and 2018", False),  # boundary years alone do not vouch
    # each separator and month form must parse as a RANGE, or the boundary years
    # would be judged as bare years and pass against the passing mention above
    ("2015\u20132018", "2021-2024, 2015 and 2018 mentioned", False),
    ("2015 to 2018", "2021-2024, 2015 and 2018 mentioned", False),
    ("2015 through 2018", "2021-2024, 2015 and 2018 mentioned", False),
    ("Jul 2015-Jun 2018", "2021-2024, 2015 and 2018 mentioned", False),
    ("07/2015 - 06/2018", "2021-2024, 2015 and 2018 mentioned", False),
    ("May 14, 2031", "October 9, 2029", False),                 # bare year not in kept
    ("May 14, 2031", "2028-2033", True),                         # bare year inside a kept range
    ("May 14, 2031", "given again in 2031", True),
    ("no dates here", "2021-2024", True),
    ("2015-2018", "no dates here", False),
])
def test_dates_compatible(dropped, kept, compatible):
    assert _dates_compatible(dropped, kept) is compatible


def test_distinct_dated_records_of_one_talk_both_kept():
    # Same title, two series of talks: the first record's tokens are all inside
    # the second, whose text names its boundary years only in passing. Token
    # containment approves the drop; only the date gate keeps both.
    first = {"text": "Grand Rounds: Widgets and Whimsy in Gardening, Department "
                     "of Applied Daydreams, 2029-2031"}
    second = {"text": "Grand Rounds: Widgets and Whimsy in Gardening, Department "
                      "of Applied Daydreams, 2033-2035, invited talk with panel "
                      "discussion; earlier series ran 2029 and 2031"}
    assert deduplicate_entries([first, second]) == [first, second]


# ----------------------------------------- #983: records fanned out of one entry

def _child(text, fields, index=0):
    return {"text": text, "extracted_fields": fields,
            "fanned_out_from": {"key": "roles", "index": index, "count": 2}}


def test_fanned_out_record_is_not_dropped_for_sharing_a_longer_entrys_words():
    """A record fanned out of a multi-record entry is one short line, so every
    word of it can sit inside a longer entry that is a different record: the
    token-containment branch approves that drop and nothing re-checks it (a
    single segment is not a record line). Its dates differ from the long
    entry's, so it is a distinct post."""
    child = _child("Co-Leader, Cancer Epidemiology, Ashby Cancer Center 2012-",
                   {"leadership_role": "Co-Leader, Cancer Epidemiology", "start_date": "2012"})
    longer = {"text": "Co-Leader, Cancer Epidemiology Program\tAshby Cancer Center\t2012-2015",
              "extracted_fields": {"leadership_role": "Co-Leader, Cancer Epidemiology Program",
                                   "start_date": "2012", "end_date": "2015"}}
    assert deduplicate_entries([longer, child]) == [longer, child]
    assert deduplicate_entries([child, longer]) == [child, longer]


def test_same_person_under_two_funders_is_two_records_with_identical_text():
    """Two fanned records whose text is the same line but whose inherited
    fields differ (the parent's funder) are not duplicates."""
    text = "Ana Cruz, Ph.D. | 2013"
    first = _child(text, {"mentee_name": "Ana Cruz", "start_date": "2013", "funding_source": "Fund One"})
    second = _child(text, {"mentee_name": "Ana Cruz", "start_date": "2013", "funding_source": "Fund Two"}, 1)
    assert deduplicate_entries([first, second]) == [first, second]


def test_fanned_out_record_that_is_an_exact_copy_is_still_dropped():
    text = "Ana Cruz, Ph.D. | 2013"
    fields = {"mentee_name": "Ana Cruz", "start_date": "2013"}
    first, second = _child(text, dict(fields)), _child(text, dict(fields), 1)
    assert deduplicate_entries([first, second]) == [first]


def test_a_plain_entry_is_still_dropped_against_a_fanned_out_record_that_contains_it():
    """The guard protects the fanned record, not the entries around it."""
    child = _child("Ana Cruz, Ph.D. | Post-graduate | 2013 | 2014",
                   {"mentee_name": "Ana Cruz", "start_date": "2013"})
    plain = {"text": "Ana Cruz, Ph.D.", "extracted_fields": {"mentee_name": "Ana Cruz"}}
    assert deduplicate_entries([child, plain]) == [child]


def test_fanned_out_records_with_equal_fields_but_different_text_are_both_kept():
    """Equal fields alone are not proof: the text is what the record line
    check and the reader see, so only a verbatim copy may go."""
    fields = {"mentee_name": "Ana Cruz", "start_date": "2013"}
    first = _child("Ana Cruz, Ph.D. | 2013 | Waisman", dict(fields))
    second = _child("Ana Cruz, Ph.D. | 2013 | Ashby", dict(fields), 1)
    assert deduplicate_entries([first, second]) == [first, second]


# ------------------------- #666: a record fused into a kept entry's text
# The verbatim branch proves the dropped text sits inside the kept text, but a
# field-rendered section prints the kept entry's FIELDS, which describe one of
# the records it fused. All names, titles and amounts below are invented.

_FUSED_GRANTS = ("Program Lead, Harbor Widget Initiative\t"
                 "Widget Outreach  PI: Dr Quill (1 of 4 Sites)  $111,111\t"
                 "Gizmo Clinic  PI: Dr Quill (1 of 2 Sites)  $222,222")


def _umbrella(**extra) -> dict:
    fields = {"title": "Harbor Widget Initiative", "pi_role": "Program Lead",
              "total_funding": "$333,333"}
    fields.update(extra)
    return {"taxonomy_code": "M2A", "text": _FUSED_GRANTS, "extracted_fields": fields}


def _sub_grant(**extra) -> dict:
    fields = {"title": "Gizmo Clinic", "pi_name": "Dr Quill",
              "total_funding": "$222,222"}
    fields.update(extra)
    return {"taxonomy_code": "M2A",
            "text": "Gizmo Clinic  PI: Dr Quill (1 of 2 Sites)  $222,222",
            "extracted_fields": fields}


def test_record_fused_into_a_kept_row_is_kept():
    kept, sub = _umbrella(), _sub_grant()
    assert _drop_is_safe(sub, kept, "M2A", [kept, sub]) is False
    assert deduplicate_entries([kept, sub], code="M2A",
                               document=[kept, sub]) == [kept, sub]


def test_record_the_kept_fields_name_is_still_dropped():
    kept, sub = _umbrella(title="Gizmo Clinic"), _sub_grant()
    assert _drop_is_safe(sub, kept, "M2A", [kept, sub]) is True


def test_name_split_across_kept_rendered_fields_is_dropped():
    # One record whose fields split the dropped name into committee and
    # organization: keeping the copy would print that row twice.
    kept = {"text": "Gadget Committee, Northern Tinkerers Society\t2013-2014\t"
                    "Sprocket Council, Northern Tinkerers Society\t2013-2014",
            "extracted_fields": {"committee_name": "Sprocket Council",
                                 "organization": "Northern Tinkerers Society"}}
    dropped = {"text": "Gadget Committee, Northern Tinkerers Society\t2013-2014",
               "extracted_fields": {"committee_name": "Gadget Committee, "
                                                      "Northern Tinkerers Society"}}
    assert _drop_is_safe(dropped, kept, "Q2", [kept, dropped]) is False
    kept["extracted_fields"]["committee_name"] = "Gadget Committee"
    assert _drop_is_safe(dropped, kept, "Q2", [kept, dropped]) is True


def test_a_kept_field_the_section_does_not_write_does_not_vouch():
    # `sub_awards` (a nested list) and `narrative` hold the name, but no M2A
    # renderer writes either, so the record still never reaches the page.
    kept = _umbrella(sub_awards=[{"sub_title": "Gizmo Clinic"}],
                     narrative="Gizmo Clinic")
    sub = _sub_grant()
    assert "narrative" not in _RENDERED_FIELDS["M2A"]
    assert _drop_is_safe(sub, kept, "M2A", [kept, sub]) is False


def test_a_stage5_rendering_of_the_kept_entry_vouches():
    kept = {"text": "2018\tQuill A. Widget safety in harbors. J Gadg 2018;1:1\t"
                    "Quill A. Sprocket wear in cold water. J Gadg 2018;2:2",
            "extracted_fields": {"title": "Widget safety in harbors",
                                 "formatted_citation": "Quill A. Widget safety in "
                                 "harbors. Sprocket wear in cold water."}}
    dropped = {"text": "Quill A. Sprocket wear in cold water. J Gadg 2018;2:2",
               "extracted_fields": {"title": "Sprocket wear in cold water"}}
    assert _drop_is_safe(dropped, kept, "S1", [kept, dropped]) is True
    del kept["extracted_fields"]["formatted_citation"]
    assert _drop_is_safe(dropped, kept, "S1", [kept, dropped]) is False


def test_a_text_rendered_code_keeps_its_verbatim_drop():
    # K4 writes the kept entry's text, which already holds the dropped record.
    kept, sub = _umbrella(), _sub_grant()
    assert "K4" not in _RENDERED_FIELDS
    assert _drop_is_safe(sub, kept, "K4", [kept, sub]) is True


def test_without_a_code_the_verbatim_drop_stands():
    kept, sub = _umbrella(), _sub_grant()
    assert _drop_is_safe(sub, kept) is True
    assert deduplicate_entries([kept, sub]) == [kept]


def test_a_shared_amount_ties_a_fragment_to_its_record():
    # A description that stage 4 filed under `title`, carrying the kept
    # grant's own amount: the same grant, not a second one.
    kept = _umbrella(total_funding="222222")
    sub = _sub_grant()
    assert _drop_is_safe(sub, kept, "M2A", [kept, sub]) is True


def test_an_id_of_fewer_than_four_digits_ties_nothing():
    kept = _umbrella(total_funding="$222")
    sub = _sub_grant(total_funding="$222")
    assert _drop_is_safe(sub, kept, "M2A", [kept, sub]) is False


def test_a_record_another_entry_names_is_still_dropped():
    # The CV lists the sub-grant again elsewhere: that entry carries it, and
    # keeping this copy could print it twice.
    kept, sub = _umbrella(), _sub_grant()
    other = {"taxonomy_code": "T", "text": "Funding list: Gizmo Clinic, 2021"}
    assert _drop_is_safe(sub, kept, "M2A", [kept, sub, other]) is True


def test_a_dropped_entry_with_no_name_is_still_dropped():
    kept, sub = _umbrella(), _sub_grant()
    del sub["extracted_fields"]["title"]
    assert _drop_is_safe(sub, kept, "M2A", [kept, sub]) is True


def test_a_kept_entry_with_no_fields_is_still_dropped():
    kept, sub = {"text": _FUSED_GRANTS}, _sub_grant()
    assert _drop_is_safe(sub, kept, "M2A", [kept, sub]) is True


def test_record_name_skips_a_field_the_section_does_not_write():
    fields = {"title": "Widget Notes", "journal_name": "Journal of Widgets"}
    assert _record_name(fields, _RENDERED_FIELDS["Q4D"]) == "Journal of Widgets"
    assert _record_name(fields, _RENDERED_FIELDS["S1"]) == "Widget Notes"
    assert _record_name({"title": "  "}, _RENDERED_FIELDS["S1"]) is None


@pytest.mark.parametrize("text, named", [
    # five significant words: four of them (80%) is a mention, three is not
    ("harbor widget sprocket gizmo repair", True),
    ("harbor widget sprocket gizmo", True),
    ("harbor widget sprocket", False),
])
def test_a_long_name_is_named_by_most_of_its_words(text, named):
    assert _names_record(text, "Harbor Widget Sprocket Gizmo Repair") is named


@pytest.mark.parametrize("text, named", [
    ("Funding: GIZMO-CLINIC, 2021", True),
    ("a clinic for every gizmo", False),
])
def test_a_short_name_is_named_only_as_one_run(text, named):
    assert _names_record(text, "Gizmo Clinic") is named


# The field that names a record, per field-rendered code. Stated here, not
# derived, so a name field dropped from (or added to) the list is a visible
# change to which records the #666 check can protect.
_NAME_FIELD_BY_CODE = {
    "B1": "degree", "B2": "program_name", "C": "specialty", "D1": "title",
    "D2": "title", "D3": "title", "F1": None, "F2": "specialty", "H": "award_name",
    "I": None, "K1": "course_title", "L3": "leadership_role", "M2A": "title",
    "M2B": "title", "M2C": "title", "M2D": "title", "N2": "grant_title",
    "N3A": "mentee_name", "N3B": "mentee_name", "O": "leadership_role",
    "P": "committee_name", "Q1": None, "Q2": "committee_name", "Q3": "panel_name",
    "Q4": "journal_name", "Q4A": "journal_name", "Q4B": "journal_name",
    "Q4C": "journal_name", "Q4D": "journal_name", "R": "title", "S1": "title",
    "S2": "title", "S3": "title", "S4": "chapter_title", "S5": "title",
    "S6": "title", "S7": "title", "S8": "title", "S9": "title",
}


def test_every_field_rendered_code_has_a_stated_name_field():
    assert set(_NAME_FIELD_BY_CODE) == set(_RENDERED_FIELDS)


@pytest.mark.parametrize("code", sorted(_NAME_FIELD_BY_CODE))
def test_record_name_reads_the_field_that_names_the_code_record(code):
    # Every key any schema could carry, each holding its own name as value.
    every_key = {key for keys in _RENDERED_FIELDS.values() for key in keys}
    every_key |= {"activity_title", "clinical_role", "teaching_role", "project_name",
                  "research_area", "license_type", "book_title", "role"}
    assert _record_name({key: key for key in every_key},
                        _RENDERED_FIELDS[code]) == _NAME_FIELD_BY_CODE[code]


@pytest.mark.parametrize("key", [
    "grant_number", "total_funding", "annual_funding", "doi", "pmid", "pmcid",
    "isbn", "patent_number", "license_number", "abstract_number"])
def test_each_shared_id_ties_a_fragment_to_its_record(key):
    kept = _umbrella(**{key: "ID-4455-01"})
    sub = _sub_grant(total_funding=None)
    sub["extracted_fields"][key] = "id 445501"
    assert _drop_is_safe(sub, kept, "M2A", [kept, sub]) is True
    kept["extracted_fields"][key] = "ID-4455-02"
    assert _drop_is_safe(sub, kept, "M2A", [kept, sub]) is False


def test_a_four_digit_shared_id_ties_a_fragment_to_its_record():
    kept = _umbrella(total_funding="$4,455")
    sub = _sub_grant(total_funding="4455")
    assert _drop_is_safe(sub, kept, "M2A", [kept, sub]) is True


def test_a_rendered_field_holding_a_list_vouches_with_its_words():
    # A rendered key stage 4 filled with a list still prints its items.
    kept = _umbrella(pi_name=["Dr Quill", "Gizmo Clinic"])
    sub = _sub_grant()
    assert _drop_is_safe(sub, kept, "M2A", [kept, sub]) is True


def test_an_empty_rendered_field_vouches_for_nothing():
    # A null field prints nothing; its "None" must not stand for a record.
    kept = _umbrella(pi_name=None, agency="")
    sub = _sub_grant(title="None")
    assert _drop_is_safe(sub, kept, "M2A", [kept, sub]) is False


# ---------------------------------------- recovered-row appendix drop (A5IZ6Q)
#
# `recover_unclaimed_table_rows` (stage 2, #420) emits one entry per table
# row no delimiter claimed, independently of whatever delimiter DID claim
# the surrounding table. A wide table-level delimiter spanning several
# element indices (a whole grant's label/value block) and the individual
# rows inside it can both survive as separate entries: the fused parent
# classifies into a render-routed code (e.g. M2B) and renders structurally,
# while each single-field recovered row is too sparse to classify as
# anything but T and is otherwise a verbatim duplicate of content the reader
# already saw. `deduplicate_entries` above never sees the pair -- parent and
# row land in different taxonomy-code groups, and dedup only compares within
# one.
#
# Round 3 (simplify, LEAD directive): earlier rounds scoped the drop to one
# specific parent entry, first by raw-text containment then by resolving
# that same parent's own rendered block. Both were removed after a shared
# value (an agency common to two grants) was shown to resolve two different
# parents to the same block, which could drop a row whose OWN content never
# rendered. `recovered_row_already_rendered` is provenance-blind: it checks
# only whether the row's own value is already printed anywhere in the
# document's rendered body -- never which entry printed it.

def test_recovered_row_dropped_when_its_value_is_already_rendered():
    row = {"text": "Award Source: | Fictional Research Foundation",
           "recovered_row": True}
    rendered = ["Award Source:", "Fictional Research Foundation"]
    assert recovered_row_already_rendered(row, rendered)


def test_recovered_row_kept_when_its_value_is_not_rendered_anywhere():
    row = {"text": "Non-financial support: | Conference travel support",
           "recovered_row": True}
    rendered = ["Award Source:", "Fictional Research Foundation"]
    assert not recovered_row_already_rendered(row, rendered)


def test_non_recovered_row_never_dropped_even_when_its_text_is_rendered():
    # Only stage 2's structural backstop sets `recovered_row` -- an ordinary
    # model-attested entry goes through deduplicate_entries's own
    # Jaccard/containment path above, never this one.
    row = {"text": "Award Source: | Fictional Research Foundation"}
    rendered = ["Award Source:", "Fictional Research Foundation"]
    assert not recovered_row_already_rendered(row, rendered)


def test_recovered_row_label_cell_excluded_from_confirmation():
    # Only the VALUE half of "Label: | Value" may confirm a drop -- the
    # label is always excluded. A grant table writes every row's label
    # unconditionally, value or not (CLAUDE.md "Stage 6 drops unnamed
    # fields" is the adjacent failure mode: a fixed-slot renderer still
    # writes the label even when the value cell is blank), so treating the
    # label as a value cell would let a blank-value row read as "confirmed"
    # merely because its label text is everywhere in the document.
    row = {"text": "Non-financial support: | ", "recovered_row": True}
    assert not recovered_row_already_rendered(row, ["Non-financial support:"])


def test_recovered_row_all_blank_value_cells_never_confirmed():
    row = {"text": "Non-financial support: |  | ", "recovered_row": True}
    assert not recovered_row_already_rendered(row, ["anything at all"])


def test_recovered_row_no_separator_falls_back_to_whole_text_as_the_value():
    # A row with no separator at all is malformed --
    # recover_unclaimed_table_rows always emits label|value -- but there is
    # no label to split off, so the whole text is the one value cell.
    row = {"text": "StandaloneValue2024", "recovered_row": True}
    assert recovered_row_already_rendered(row, ["StandaloneValue2024"])
    assert not recovered_row_already_rendered(row, ["nothing relevant here"])


# ------------------------------------------- multi-cell rows (>2 columns)
#
# `recover_unclaimed_table_rows` joins the WHOLE physical table row with
# " | ", regardless of column count -- a 3-column row (Label, StartDate,
# EndDate) survives as one string with TWO separators in it. Each half is
# its own value cell and is checked independently.

def test_recovered_row_multi_cell_value_all_cells_confirmed_drops_the_row():
    row = {"text": "Duration of support: | 2021 | 2022", "recovered_row": True}
    assert recovered_row_already_rendered(
        row, ["Duration of support:", "2021", "2022"])


def test_recovered_row_multi_cell_value_one_unconfirmed_cell_keeps_the_row():
    row = {"text": "Duration of support: | 2021 | 2022", "recovered_row": True}
    # "2022" never rendered anywhere -- the row must stay even though "2021"
    # did; joining the two cells back into "2021 | 2022" before searching
    # would never match a real render either (nothing renders a raw " | "),
    # so cells are checked separately rather than rejoined.
    assert not recovered_row_already_rendered(row, ["Duration of support:", "2021"])


# ------------------------------------------------- tab-separated fallback path
#
# `recover_unclaimed_table_rows` renders " | " when it joins a physical
# table row's cells, but a bare "\t" on its non-table fallback path --
# `_CELL_SEPARATOR_RE` must split on both, not just "|".

def test_recovered_row_tab_separated_value_matches():
    row = {"text": "Award Source:\tFictional Research Foundation",
           "recovered_row": True}
    assert recovered_row_already_rendered(row, ["Fictional Research Foundation"])


def test_recovered_row_tab_separated_unrendered_value_stays():
    row = {"text": "Award Source:\tFictional Research Foundation",
           "recovered_row": True}
    assert not recovered_row_already_rendered(row, ["something unrelated"])


# ------------------------------------- trivial cell beside a confirmed cell
#
# `_is_trivial_value_cell` (dedup.py) filters a blank OR punctuation-only
# cell OUT before confirmation is checked, so it can never itself supply
# evidence -- but symmetrically it must never BLOCK a drop a sibling
# non-trivial cell already confirms either.

def test_recovered_row_punctuation_only_cell_does_not_block_a_drop():
    row = {"text": "Non-financial support: | -- | Fictional Research Foundation",
           "recovered_row": True}
    assert recovered_row_already_rendered(row, ["Fictional Research Foundation"])


def test_recovered_row_blank_cell_does_not_block_a_drop():
    row = {"text": "Non-financial support: |  | Fictional Research Foundation",
           "recovered_row": True}
    assert recovered_row_already_rendered(row, ["Fictional Research Foundation"])


# ------------------------------------- non-alphanumeric edge of the value
#
# A leading non-alphanumeric character (a currency symbol) needs no LEADING
# boundary: '$' itself can never be part of the digit run that would create
# a false partial-number match the way an adjacent alnum char could, so a
# raw "$15,000.00" is still confirmed by a render that prefixes it with a
# currency code -- the boundary only has to hold on the alnum-adjacent
# trailing edge (already covered above).

def test_recovered_row_currency_value_matches_with_a_prefixed_currency_code():
    row = {"text": "Annual direct costs: | $15,000.00", "recovered_row": True}
    assert recovered_row_already_rendered(row, ["US$15,000.00"])


# --------------------------------------------- normalization (§ LEAD item 1)
#
# "normalized: casefold, collapsed whitespace, word-boundary match on both
# sides" -- each clause has its own test.

def test_recovered_row_value_matches_case_insensitively():
    row = {"text": "Award Source: | fictional research foundation",
           "recovered_row": True}
    assert recovered_row_already_rendered(row, ["FICTIONAL RESEARCH FOUNDATION"])


def test_recovered_row_value_matches_despite_irregular_whitespace():
    row = {"text": "Award Source: |  Fictional   Research Foundation ",
           "recovered_row": True}
    assert recovered_row_already_rendered(row, ["Fictional Research Foundation"])


def test_recovered_row_value_not_glued_across_a_rendered_line_join():
    # Every rendered line is joined into one string before searching it.
    # Collapsing whitespace (never squashing it away entirely) keeps a real
    # word boundary at that join: "...Foundation" ending one line and
    # "1%..." starting the next must not satisfy a value like "foundation1"
    # that never existed as contiguous rendered text.
    row = {"text": "Label: | foundation1", "recovered_row": True}
    assert not recovered_row_already_rendered(
        row, ["...Fictional Research Foundation", "1% effort..."])


def test_recovered_row_value_not_matched_inside_a_longer_trailing_run():
    # '2021' must not match the leading digits of '20215' -- the boundary
    # has to hold at the TRAILING edge of the value, not just the leading
    # one (a mutant dropping only the trailing lookahead survived earlier
    # rounds' coverage).
    row = {"text": "Duration of support: | 2021", "recovered_row": True}
    assert not recovered_row_already_rendered(row, ["20215"])


def test_recovered_row_value_not_matched_inside_a_longer_leading_run():
    # '2021' must not match the trailing digits of '12021' -- the LEADING
    # edge of the value.
    row = {"text": "Duration of support: | 2021", "recovered_row": True}
    assert not recovered_row_already_rendered(row, ["12021"])


def test_recovered_row_currency_value_matches_with_trailing_boundary():
    row = {"text": "Annual direct costs: | $15,000.00", "recovered_row": True}
    assert recovered_row_already_rendered(
        row, ["Annual direct costs:", "$15,000.00"])
    assert not recovered_row_already_rendered(
        row, ["$15,000.005"])


# ------------------------------------------ shared-agency cross-vouching
#
# The exact repro that sank round 2's parent-block scoping: two grants share
# an agency. Grant A's own render legitimately carries dates/effort; grant
# B's recovered rows must stay when B's OWN value is not printed anywhere,
# and a shared digit run (A's "25%" vs B's "5%") must not cross-match.

def test_shared_agency_grant_with_unrendered_value_is_not_vouched_for():
    rendered = [
        "National Institutes of Health", "Alpha Sequencing Initiative",
        "Duration of support:", "2019-2020",
        "Your percent (%) effort:", "25%",
        "National Institutes of Health", "Beta Imaging Cohort",
        "Duration of support:", "",
        "Your percent (%) effort:", "",
    ]
    row_effort_b = {"text": "Your percent (%) effort: | 5%", "recovered_row": True}
    assert not recovered_row_already_rendered(row_effort_b, rendered)


def test_five_percent_does_not_match_inside_twenty_five_percent():
    row = {"text": "Your percent (%) effort: | 5%", "recovered_row": True}
    assert not recovered_row_already_rendered(
        row, ["Your percent (%) effort:", "25%"])


# -------------------------------------------------- accepted trade-off
#
# Removing per-parent scoping means a match is accepted regardless of WHICH
# record actually printed it -- documented in `recovered_row_already_rendered`'s
# own docstring as the deliberate trade for a simpler, unconditionally
# content-safe rule. The one direction this is allowed to be wrong in is
# duplication, never loss: a value stage 6 reformats on the way to a render
# slot (a raw "00/2021" cell rendered as "2021") will not verbatim-match, so
# the row correctly stays rather than being wrongly dropped.

def test_recovered_row_value_confirmed_by_an_unrelated_records_render():
    # By design (see the module docstring): this never asks WHICH record
    # rendered a value, only whether it is already visible in the document.
    row = {"text": "Your percent (%) effort: | 25%", "recovered_row": True}
    rendered = ["A completely different grant", "Your percent (%) effort:", "25%"]
    assert recovered_row_already_rendered(row, rendered)


def test_recovered_row_reformatted_date_value_is_not_confirmed_and_stays():
    # Stage 6 reformats "00/2021-00/2022" to "2021-2022" -- this function
    # does no date parsing, so the row stays (extra duplication, never lost
    # content) rather than being dropped on a value that never rendered
    # verbatim.
    row = {"text": "Duration of support: | 00/2021-00/2022", "recovered_row": True}
    assert not recovered_row_already_rendered(row, ["Duration of support:", "2021-2022"])
