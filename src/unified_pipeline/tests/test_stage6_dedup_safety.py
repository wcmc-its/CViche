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
    _DECISION_FIELD_MAX_CHARS,
    _GROUP_HEADER_RE,
    _bare_occasion_apart,
    _carries_record,
    _decision_fields,
    _dates_compatible,
    _companion_title,
    _different_book,
    _different_institution,
    _different_rank,
    _distinct_bare_names,
    _drop_is_safe,
    _lists_name,
    _names_a_sibling,
    _names_record,
    _other_journal_same_row,
    _part_numbers,
    _place_only_event,
    _record_name,
    _title_only_fragment,
    _verbatim_contained,
    recovered_row_already_rendered,
    _row_residue,
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


def test_decision_records_clipped_name_fields_of_both_entries():
    # #666: the doctor compares the two entries' extracted names.
    decisions = []
    dropped = {"text": "Example Optics Journal Reviewer",
               "extracted_fields": {"journal_name": "Example Optics", "note": "n",
                                    "year": 2020, "location": "Example City"}}
    kept = {"text": "European Example Optics Journal Reviewer Journal Reviewer",
            "extracted_fields": {"journal_name": "European Example Optics"}}
    deduplicate_entries([dropped, kept], decisions=decisions, code="Q4D")
    assert len(decisions) == 1
    assert decisions[0]["dropped_fields"] == {
        "journal_name": "Example Optics"}
    assert decisions[0]["kept_fields"] == {"journal_name": "European Example Optics"}


def test_decision_name_fields_are_clipped():
    entry = {"extracted_fields": {"journal_name": "x" * 500}}
    assert _decision_fields(entry, "Q4D") == {
        "journal_name": "x" * _DECISION_FIELD_MAX_CHARS}


def test_decision_name_fields_follow_the_code():
    entry = {"extracted_fields": {"institution": "Example Hospital",
                                  "organization": "Example Society",
                                  "title": "Example Title", "location": "X",
                                  "award_name": "Example Award",
                                  "committee_name": "Example Committee"}}
    assert _decision_fields(entry, "D2") == {"institution": "Example Hospital"}
    assert _decision_fields(entry, "I") == {"organization": "Example Society"}
    assert _decision_fields(entry, "S3") == {"title": "Example Title"}
    # #666 (EBYSBC): the lint could not see a D1 title, H award or P committee.
    assert _decision_fields(entry, "D1") == {"title": "Example Title"}
    assert _decision_fields(entry, "H") == {"award_name": "Example Award"}
    assert _decision_fields(entry, "P") == {"committee_name": "Example Committee"}
    assert _decision_fields(entry, "R") == {}
    assert _decision_fields(entry, None) == {}


@pytest.mark.parametrize("code,key", [("I", "organization"), ("D2", "institution"),
                                      ("S3", "title"), ("D1", "title"),
                                      ("H", "award_name"), ("P", "committee_name")])
def test_deduplicate_entries_writes_the_code_keyed_name_fields(code, key):
    # #666: the code must reach _decision_fields from the dedup loop itself.
    decisions = []
    text = "Example Name Alpha Beta Gamma Delta Epsilon Zeta 2001"
    dropped = {"text": text, "extracted_fields": {key: "Example Name"}}
    kept = {"text": text + " Eta",
            "extracted_fields": {key: "Example Name Instructor Guide"}}
    deduplicate_entries([dropped, kept], decisions=decisions, code=code)
    assert len(decisions) == 1
    assert decisions[0]["dropped_fields"] == {key: "Example Name"}
    assert decisions[0]["kept_fields"] == {key: "Example Name Instructor Guide"}


def test_decision_name_fields_skip_null_and_non_string_values():
    entry = {"extracted_fields": {"journal_name": None, "organization": 7}}
    assert _decision_fields(entry, "I") == {}


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


# #1181: WCM label|value mentee tables share every label, so two residents at
# one site with overlapping periods score as duplicates. Synthetic names.
def _mentee_table(name: str, period: str) -> dict:
    text = (f"Name | {name}\nSite/Position | Harbor Institute - Resident\n"
            f"Expected Mentoring Period (mm/yyyy-mm/yyyy) | {period}\n"
            "Project/Accomplishments** | Case report on widget toxicity\n"
            "Goals/expected Outcomes | Abstract submission, Poster Presentation\n"
            "Type of Supervision (Research, clinical, teaching, leadership) | Research")
    return {"text": text, "extracted_fields": {"mentee_name": name}}


def test_distinct_mentees_in_label_value_tables_kept():
    first = _mentee_table("Avery Quill", "11/2024 – 11/2026")
    second = _mentee_table("Blair Sprocket", "01/2025 – 06/2026")
    document = [first, second]
    assert deduplicate_entries(document, code="N3A", document=document) == document


def test_same_mentee_written_two_ways_still_dropped():
    # Case and punctuation are not a different person.
    first = _mentee_table("Avery O'Quill", "11/2024 – 11/2026")
    second = _mentee_table("avery o’ quill", "11/2024 – 11/2026")
    assert len(deduplicate_entries([first, second], code="N3A",
                                   document=[first, second])) == 1


def test_placeholder_mentee_table_still_dropped():
    first = _mentee_table("Avery Quill", "11/2024 – 11/2026")
    blank = _mentee_table("N/A", "11/2024 – 11/2026")
    assert deduplicate_entries([first, blank], code="N3A",
                               document=[first, blank]) == [first]


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
    # #666 (EBYSBC): a date stated to the month, or the day, is compared there
    ("3/2029 widget talk", "9/2029 widget talk", False),         # another month
    ("4/8/27 session", "02/11/27 session", False),               # two-digit year
    ("4/8/27 session", "4/15/27 session", False),                # another day
    ("4/8/27 session", "04/08/2027 session", True),
    ("8/2029 panel", "August 2029 panel", True),                 # named month
    ("August 2nd, 2029", "August 2, 2029", True),
    ("August 2nd, 2029", "August 3, 2029", False),
    ("6/2028 talk", "7/2028 talk, 8/2028 talk", False),         # a list, not a range
    ("8/2028 talk", "7/2028 talk, 8/2028 talk", True),
    ("2011-6/30/2019", "9/1/2011-6/30/2019", True),              # a range holds its ends
    ("8/2014", "7/2011 - 12/2019", True),                        # and what lies between
    ("8/2021", "7/2011 - present", True),                        # an open end
    ("3/2016-present", "2016-present", True),                    # kept states no month
    ("Spring 2029", "4/2029", True),                             # a season states none
    ("Market 2029", "Mar 2029", True),                           # not a month name
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
    "D2": "title", "D3": "title", "F1": "state_country", "F2": "specialty", "H": "award_name",
    "I": "organization", "K1": "course_title", "L3": "leadership_role", "M2A": "title",
    "M2B": "title", "M2C": "title", "M2D": "title", "N2": "grant_title",
    "N3A": "mentee_name", "N3B": "mentee_name", "O": "leadership_role",
    "P": "committee_name", "Q1": "organization", "Q2": "committee_name", "Q3": "panel_name",
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
def test_postdoc_training_role_vouches_for_a_fused_record():
    """#946: `role` renders on the C type line, so dedup's rendered-words set for C
    includes it (the field is consumed here as well as by the section renderer)."""
    assert "role" in _RENDERED_FIELDS["C"]


# ------------- #666: two identical standalone copies of a fused record
# Each copy used to count as "another entry naming the record" for the other,
# so both dropped against the kept row that fused the record.

@pytest.mark.parametrize("order", [(0, 1, 2), (1, 2, 0), (1, 0, 2), (2, 0, 1)])
def test_identical_copies_of_a_fused_record_keep_exactly_one(order):
    kept, first, second = _umbrella(), _sub_grant(), _sub_grant()
    entries = [(kept, first, second)[i] for i in order]
    survivors = deduplicate_entries(entries, code="M2A", document=list(entries))
    assert any(e is kept for e in survivors)
    assert sum(e is first or e is second for e in survivors) == 1


def test_an_entry_dropped_already_does_not_vouch_for_another_drop():
    kept, sub, copy = _umbrella(), _sub_grant(), _sub_grant()
    document = [kept, sub, copy]
    assert _drop_is_safe(sub, kept, "M2A", document) is True
    assert _drop_is_safe(sub, kept, "M2A", document, {id(copy)}) is False
    shared = {id(copy)}
    assert deduplicate_entries([kept, sub], code="M2A", document=document,
                               dropped_ids=shared) == [kept, sub]
    assert shared == {id(copy)}, "a kept entry was recorded as dropped"


def test_dedup_records_every_entry_it_drops_in_the_shared_set():
    kept, sub = _umbrella(), _sub_grant()
    shared: set[int] = set()
    deduplicate_entries([kept, sub], dropped_ids=shared)
    assert shared == {id(sub)}


# ------ #666: the token-containment branch against a fused kept entry
# A kept Q2 row fused two committee records; its fields describe the first.

_FUSED_COMMITTEES = ("Chair, Gadget Committee, Harbor Tinkerers Guild\t"
                     "Member, Sprocket Council, Harbor Tinkerers Guild")


def _fused_committee_row() -> dict:
    return {"text": _FUSED_COMMITTEES,
            "extracted_fields": {"committee_name": "Gadget Committee", "role": "Chair",
                                 "organization": "Harbor Tinkerers Guild"}}


def _reworded_sibling() -> dict:
    # Not a verbatim copy of the sibling row: token containment approves it.
    return {"text": "Harbor Tinkerers Guild Sprocket Council (Member)",
            "extracted_fields": {"committee_name": "Sprocket Council", "role": "Member",
                                 "organization": "Harbor Tinkerers Guild"}}


def test_reworded_sibling_of_a_fused_kept_row_is_kept():
    kept, sibling = _fused_committee_row(), _reworded_sibling()
    assert _drop_is_safe(sibling, kept, "Q2", [kept, sibling]) is False
    assert deduplicate_entries([kept, sibling], code="Q2",
                               document=[kept, sibling]) == [kept, sibling]


def test_reworded_sibling_another_entry_names_is_still_dropped():
    kept, sibling = _fused_committee_row(), _reworded_sibling()
    other = {"text": "Service list: Sprocket Council, 2019"}
    assert _drop_is_safe(sibling, kept, "Q2", [kept, sibling, other]) is True
    # ...unless dedup has dropped that entry already.
    assert _drop_is_safe(sibling, kept, "Q2", [kept, sibling, other], {id(other)}) is False


def test_reworded_copy_of_the_kept_record_itself_is_still_dropped():
    # The kept row's own committee, the organization folded into the name:
    # its words never stand together in the kept text, so no sibling row.
    kept = {"text": "Gadget Committee\t\t2012-2014\tHarbor Tinkerers Guild",
            "extracted_fields": {"committee_name": "Gadget Committee",
                                 "start_date": "2012", "end_date": "2014"}}
    dropped = {"text": "Harbor Tinkerers Guild, Gadget Committee\t2012-2014",
               "extracted_fields": {"committee_name": "Harbor Tinkerers Guild, Gadget Committee",
                                    "start_date": "2012", "end_date": "2014"}}
    assert _drop_is_safe(dropped, kept, "P", [kept, dropped]) is True


def test_token_drop_without_a_code_stands():
    kept, sibling = _fused_committee_row(), _reworded_sibling()
    assert _drop_is_safe(sibling, kept) is True


@pytest.mark.parametrize("text,sibling", [
    ("Chair, Gadget Committee\tMember, Sprocket Council", True),
    ("Chair, Gadget Committee (Sprocket Council liaison)", True),
    ("Chair, Gadget Committee, Sprocket Councils", False),   # a longer word
    ("Chair, Gadget Committee, Minisprocket Council", False),  # a longer word
    ("Chair, Gadget Committee, Sprocketcouncil", False),     # one word
    ("Chair, Gadget Committee\tSprocket, Council", True),     # punctuation between
    ("Chair, Gadget Committee\tCouncil Sprocket", False),     # the words reordered
    ("Chair, Gadget Committee", False),
])
def test_names_a_sibling_needs_the_name_as_a_run_outside_written_values(text, sibling):
    fields = {"committee_name": "Gadget Committee", "role": "Chair"}
    kept = {"text": text, "extracted_fields": fields}
    assert _names_a_sibling(kept, fields, _RENDERED_FIELDS["Q2"], "Sprocket Council") is sibling


def test_a_written_value_holding_the_name_is_no_sibling():
    fields = {"committee_name": "Gadget Committee", "role": "Chair of Sprocket Council"}
    kept = {"text": "Gadget Committee\tSprocket Council", "extracted_fields": fields}
    assert _names_a_sibling(kept, fields, _RENDERED_FIELDS["Q2"], "Sprocket Council") is False


def test_a_value_the_section_does_not_write_is_not_cut_out():
    # `notes` is not a Q2 field the section writes: the name it holds is
    # still a sibling the page never shows.
    fields = {"committee_name": "Gadget Committee", "notes": "Sprocket Council"}
    kept = {"text": "Gadget Committee\tSprocket Council", "extracted_fields": fields}
    assert "notes" not in _RENDERED_FIELDS["Q2"]
    assert _names_a_sibling(kept, fields, _RENDERED_FIELDS["Q2"], "Sprocket Council") is True


def test_a_stage5_rendering_is_cut_out_of_the_kept_text():
    fields = {"committee_name": "Gadget Committee",
              "formatted_text": "Gadget Committee; Sprocket Council"}
    kept = {"text": "Gadget Committee; Sprocket Council", "extracted_fields": fields}
    assert _names_a_sibling(kept, fields, _RENDERED_FIELDS["Q2"], "Sprocket Council") is False


def test_a_name_split_across_written_values_is_no_sibling():
    # The kept record itself, its name split into committee and organization.
    fields = {"committee_name": "Gadget Committee", "role": "-",
              "organization": "Northern Tinkerers Society"}
    kept = {"text": "Gadget Committee, Northern Tinkerers Society\t2013",
            "extracted_fields": fields}
    name = "Gadget Committee, Northern Tinkerers Society"
    assert _names_a_sibling(kept, fields, _RENDERED_FIELDS["Q2"], name) is False


def test_a_null_written_field_cuts_nothing_out():
    fields = {"committee_name": "Gadget Committee", "role": None}
    kept = {"text": "Gadget Committee\tNone Society", "extracted_fields": fields}
    assert _names_a_sibling(kept, fields, _RENDERED_FIELDS["Q2"], "None Society") is True


def test_a_name_without_words_names_no_sibling():
    fields = {"committee_name": "Gadget Committee"}
    kept = {"text": "Gadget Committee\t--", "extracted_fields": fields}
    assert _names_a_sibling(kept, fields, _RENDERED_FIELDS["Q2"], "--") is False


# ------------ #666: a short name inside a longer kept name
# A reviewer list of bare journal names: "Widgets" is contained in "Widgets
# Quarterly", and it is another journal.

def _journal(name: str) -> dict:
    return {"taxonomy_code": "Q4D", "text": name, "extracted_fields": {"journal_name": name}}


def test_bare_short_name_inside_a_longer_bare_name_is_kept():
    kept, short = _journal("Widgets Quarterly"), _journal("Widgets")
    assert _drop_is_safe(short, kept, "Q4D", [kept, short]) is False
    assert deduplicate_entries([kept, short], code="Q4D",
                               document=[kept, short]) == [kept, short]


@pytest.mark.parametrize("kept_name", [
    "Acme Society (APS)",  # an acronym is no other word of the name
    "Acme Society (ACME), formerly Gadget Society (GS)",  # history, not another record
])
def test_a_kept_name_with_an_acronym_or_history_is_the_same_name(kept_name):
    assert _distinct_bare_names({"text": "Acme Society"}, {"text": kept_name},
                                "Acme Society", kept_name) is False
    assert _distinct_bare_names({"text": "Acme Society"}, {"text": kept_name},
                                "Acme Society", "Acme Society (Exchange Program)") is False
    assert _distinct_bare_names({"text": "Acme Society"}, {"text": kept_name},
                                "Acme Society", "Beta Society - Acme Society Exchange Program") is True
    assert _distinct_bare_names({"text": "Acme Society"}, {"text": kept_name},
                                "Acme Society", "Acme Society Council on Widgets") is False
    assert _distinct_bare_names({"text": "Acme Society"}, {"text": kept_name},
                                "Acme Society", "Acme Society - Gadget Society Exchange Program") is True


def test_bare_name_differing_only_in_a_stop_word_is_still_dropped():
    kept, short = _journal("The Widgets"), _journal("Widgets")
    assert _drop_is_safe(short, kept, "Q4D", [kept, short]) is True


def test_bare_name_another_entry_lists_exactly_is_still_dropped():
    kept, short = _journal("Widgets Quarterly"), _journal("Widgets")
    listed = {"taxonomy_code": "Q4D", "text": "Ad hoc reviewer: Gizmo Review; Widgets"}
    assert _drop_is_safe(short, kept, "Q4D", [kept, short, listed]) is True


def test_a_longer_name_elsewhere_does_not_list_the_short_one():
    kept, short = _journal("Widgets Quarterly"), _journal("Widgets")
    other = _journal("Annals of Widgets Research")
    assert _drop_is_safe(short, kept, "Q4D", [kept, short, other]) is False


def test_a_dropped_bare_name_listed_elsewhere_vouches_for_nothing():
    kept, short, copy = _journal("Widgets Quarterly"), _journal("Widgets"), _journal("Widgets")
    document = [kept, short, copy]
    assert _drop_is_safe(short, kept, "Q4D", document) is True
    assert _drop_is_safe(short, kept, "Q4D", document, {id(copy)}) is False


def test_short_name_in_a_text_rendered_code_is_still_dropped():
    kept, short = _journal("Widgets Quarterly"), _journal("Widgets")
    assert "K4" not in _RENDERED_FIELDS
    assert _drop_is_safe(short, kept, "K4", [kept, short]) is True


@pytest.mark.parametrize("dropped_text,kept_text,kept_name,distinct", [
    ("Widgets", "Widgets Quarterly", "Widgets Quarterly", True),
    ("Ad hoc Widgets", "Widgets Quarterly", "Widgets Quarterly", False),  # dropped holds more
    ("Widgets", "Advisory Panel, Widgets Quarterly", "Widgets Quarterly", False),  # kept name opens with it: a unit of the same body
    ("Widgets", "Advisory Panel, Gizmo Widgets", "Gizmo Widgets", True),  # kept row holds it and more
    ("Widgets Quarterly", "Advisory Panel, Widgets", "Widgets", False),  # kept row holds less
    ("Widgets", "Gadgets", "Gadgets", True),  # both bare, neither holds the other

    ("Widgets", "The Widgets", "The Widgets", False),  # same significant words
    ("WIDGETS.", "Widgets Quarterly", "Widgets Quarterly", True),  # case, punctuation
    ("Widgets", "", None, False),  # no kept name
])
def test_distinct_bare_names(dropped_text, kept_text, kept_name, distinct):
    assert _distinct_bare_names({"text": dropped_text}, {"text": kept_text},
                                "Widgets", kept_name) is distinct


@pytest.mark.parametrize("entry,listed", [
    ({"text": "Widgets"}, True),
    ({"text": "Reviewer: Gizmo Review, Widgets | 2019"}, True),
    ({"text": "Gizmo Review\nWidgets"}, True),
    ({"text": "Gizmo Review\tWidgets"}, True),
    ({"text": "Gizmo Review; widgets."}, True),
    ({"text": "Reviewer: Widgets"}, True),
    ({"text": "Widgets Quarterly"}, False),
    ({"text": "Reviewer for Widgets"}, False),
    ({"text": "Widgets", "taxonomy_code": "S7"}, False),  # a list of another code
    ({"text": "", "extracted_fields": {"journal_name": "Widgets"}}, False),  # no row to compare
    ({"text": "Widgets", "extracted_fields": {"journal_name": "Widgets"}}, True),
    ({"text": "Widgets Quarterly", "extracted_fields": {"journal_name": "Widgets Quarterly"}}, False),
    ({"text": "Widgets", "extracted_fields": {"notes": "Widgets"}}, True),  # the text lists it
    ({"text": "Gizmo", "extracted_fields": {"notes": "Widgets"}}, False),
    ({"text": None, "extracted_fields": None}, False),
])
def test_lists_name_needs_the_exact_name(entry, listed):
    entry = {"taxonomy_code": "Q4D", **entry}
    assert _lists_name(entry, "Widgets", "journal_name", "Q4D", "Widgets") is listed


@pytest.mark.parametrize("field,listed", [
    ("journal_name", True),
    ("specialty", False),  # a fellowship's specialty is not a journal
    ("degree", False),
    ("committee_name", False),
])
def test_only_the_field_that_named_the_record_vouches_for_it(field, listed):
    entry = {"taxonomy_code": "C", "text": "Widgets",
             "extracted_fields": {field: "Widgets"}}
    assert _lists_name(entry, "Widgets", "journal_name", "Q4D", "Widgets") is listed


def test_a_row_for_the_same_journal_with_another_role_does_not_vouch():
    board = {"taxonomy_code": "Q4C", "text": "Widgets\tEditorial Board\t2012-2017",
             "extracted_fields": {"journal_name": "Widgets"}}
    assert _lists_name(board, "Widgets", "journal_name", "Q4B",
                       "Widgets\tAssociate Editor\t2019-present") is False
    same_role = {"taxonomy_code": "Q4B", "text": "Widgets\tAssociate Editor\t2009-2011",
                 "extracted_fields": {"journal_name": "Widgets"}}
    assert _lists_name(same_role, "Widgets", "journal_name", "Q4B",
                       "Widgets\tAssociate Editor\t2019-present") is True


# ------------ #666: a journal row that says more than the bare name
# "Ad hoc Widgets, 2013-" beside "Ad hoc Widgets Quarterly, 2013-": the rows
# say the same thing about two journals.

def _journal_row(text: str, name: str) -> dict:
    return {"taxonomy_code": "Q4D", "text": text, "extracted_fields": {"journal_name": name}}


def test_dated_row_for_a_short_journal_name_is_kept_beside_a_longer_one():
    kept = _journal_row("Ad hoc Widgets Quarterly\t2013- Present", "Widgets Quarterly")
    short = _journal_row("Ad hoc Widgets\t2013- Present", "Widgets")
    assert _drop_is_safe(short, kept, "Q4D", [kept, short]) is False
    assert deduplicate_entries([kept, short], code="Q4D",
                               document=[kept, short]) == [kept, short]


def test_journal_rows_with_different_dates_are_kept():
    kept = _journal_row("2021-2023 Ad Hoc Reviewer, Gadget Widgets", "Gadget Widgets")
    short = _journal_row("2021 Ad Hoc Reviewer, Widgets", "Widgets")
    assert _drop_is_safe(short, kept, "Q4D", [kept, short]) is False


def test_journal_row_that_says_less_than_the_kept_row_is_still_dropped():
    # The kept row also names an editorial role, so the two rows are not the
    # same statement about two journals.
    kept = _journal_row("Ad hoc reviewer and editor, Widgets Quarterly, 2013- Present",
                        "Widgets Quarterly")
    short = _journal_row("Ad hoc reviewer, Widgets, 2013- Present", "Widgets")
    assert _drop_is_safe(short, kept, "Q4D", [kept, short]) is True


def test_journal_row_naming_the_same_journal_reworded_is_still_dropped():
    kept = _journal_row("Ad hoc reviewer, The Widgets, 2013- Present", "The Widgets")
    short = _journal_row("Ad hoc reviewer, Widgets, 2013- Present", "Widgets")
    assert _drop_is_safe(short, kept, "Q4D", [kept, short]) is True


def test_journal_row_another_entry_lists_exactly_is_still_dropped():
    kept = _journal_row("Ad hoc Widgets Quarterly\t2013- Present", "Widgets Quarterly")
    short = _journal_row("Ad hoc Widgets\t2013- Present", "Widgets")
    copy = _journal_row("Widgets", "Widgets")
    assert _drop_is_safe(short, kept, "Q4D", [kept, short, copy]) is True
    assert _drop_is_safe(short, kept, "Q4D", [kept, short, copy], {id(copy)}) is False


def test_committee_row_with_an_institution_prefix_is_still_dropped():
    # A committee's name is descriptive: the prefixed name is the same committee.
    def row(text, name):
        return {"taxonomy_code": "Q2", "text": text,
                "extracted_fields": {"committee_name": name}}
    kept = row("Acme University, Clinic Services Committee\t2012-2014",
               "Acme University, Clinic Services Committee")
    short = row("Clinic Services Committee\t2012-2014", "Clinic Services Committee")
    assert _drop_is_safe(short, kept, "Q2", [kept, short]) is True


@pytest.mark.parametrize("dropped_fields,kept_fields,same_row", [
    ({"journal_name": "Widgets"}, {"journal_name": "Widgets Quarterly"}, True),
    ({"journal_name": "Widgets"}, {"journal_name": "The Widgets"}, False),  # same words
    ({"title": "Widgets"}, {"journal_name": "Widgets Quarterly"}, False),  # not a journal row
    ({"journal_name": "Widgets"}, {"title": "Widgets Quarterly"}, False),
    ({"journal_name": "Widgets"}, {"journal_name": None}, False),
    ({"journal_name": "Widgets"}, {"journal_name": "Gizmo Review"}, False),  # not in the kept text
    ({"journal_name": "Widgets"}, {"journal_name": ["Widgets Quarterly"]}, False),  # not a string
])
def test_other_journal_same_row_table(dropped_fields, kept_fields, same_row):
    dropped = {"text": "Ad hoc Widgets 2013", "extracted_fields": dropped_fields}
    kept = {"text": "Ad hoc Widgets Quarterly 2013", "extracted_fields": kept_fields}
    assert _other_journal_same_row(dropped, kept, "Widgets", kept_fields) is same_row


def test_a_trailing_journal_is_not_another_journal():
    dropped = _journal_row("Widgets Record\tReviewing Editor\t2015-2020", "Widgets Record")
    kept = _journal_row("Reviewing Editor\tWidgets Record journal\t2015-2020",
                        "Widgets Record journal")
    assert _other_journal_same_row(dropped, kept, "Widgets Record",
                                   kept["extracted_fields"]) is False


def test_two_rows_that_do_not_hold_their_names_are_not_the_same_row():
    # Neither text holds its journal's name, so neither has a residue to compare.
    dropped = {"text": "Ad hoc 2013", "extracted_fields": {"journal_name": "Widgets"}}
    kept = {"text": "Ad hoc 2013", "extracted_fields": {"journal_name": "Gizmo Review"}}
    assert _other_journal_same_row(dropped, kept, "Widgets", kept["extracted_fields"]) is False


@pytest.mark.parametrize("text,name,residue", [
    ("Ad hoc Widgets\t2013- Present", "Widgets", "adhocpresent"),
    ("AD HOC, widgets.", "Widgets", "adhoc"),
    ("Widgets, Widgets", "Widgets", "widgets"),  # the first run only
    ("Ad hoc Gizmo", "Widgets", None),  # name not in the text
    ("Ad hoc Widgetsmith", "Widgets", None),  # a longer word is not the name
    ("Ad hoc Widgets", "--", None),  # a name without words
])
def test_row_residue(text, name, residue):
    assert _row_residue(text, name) == residue


# ------------ #666 (batch IPXFBA): five ways a distinct record still dropped.

def _entry(code: str, text: str, **fields) -> dict:
    return {"taxonomy_code": code, "text": text, "extracted_fields": fields}


def _kept_both(code: str, dropped: dict, kept: dict, *others: dict) -> bool:
    """True when `deduplicate_entries` keeps `dropped` beside `kept`."""
    document = [kept, dropped, *others]
    result = deduplicate_entries([kept, dropped], code=code, document=document)
    return any(entry is dropped for entry in result)


def test_other_fields_that_hold_a_journal_word_do_not_vouch_for_its_bare_name():
    # (a) A fellowship's specialty and a certification's specialty are not
    # another listing of the journal "Widgets".
    dropped = _entry("Q4D", "Widgets | Journal Reviewer", journal_name="Widgets")
    kept = _entry("Q4D", "Gizmo and Widgets | Journal Reviewer",
                  journal_name="Gizmo and Widgets")
    fellowship = _entry("C", "Fellowship, Widgets", specialty="Widgets")
    board = _entry("F2", "Board certified, Widgets", specialty="Widgets")
    assert _kept_both("Q4D", dropped, kept, fellowship, board)


def test_a_citation_listing_the_word_does_not_vouch_for_a_bare_journal():
    dropped = _journal("Widgets")
    kept = _journal("Gizmo Widgets")
    citation = {"taxonomy_code": "S7", "text": "Doe J, Roe R, Widgets, Gizmo, 2019"}
    assert _kept_both("Q4D", dropped, kept, citation)


def test_a_board_row_of_the_same_journal_does_not_vouch_for_an_editor_row():
    dropped = _entry("Q4B", "Widgets\tAssociate Editor\t2019-present",
                     journal_name="Widgets", role="Associate Editor")
    kept = _entry("Q4B", "Widgets Letters\tAssociate Editor\t2019-present",
                  journal_name="Widgets Letters", role="Associate Editor")
    board = _entry("Q4C", "Widgets\tEditorial Board\t2012-2017", journal_name="Widgets")
    assert _kept_both("Q4B", dropped, kept, board)


def test_a_bare_journal_listed_by_another_entry_of_its_list_is_still_dropped():
    dropped = _journal("Widgets")
    kept = _journal("Gizmo Widgets")
    again = _journal("Widgets")
    assert not _kept_both("Q4D", dropped, kept, again)


def test_a_membership_listed_bare_beside_a_longer_exchange_row_is_kept():
    # (b) I has no name field in `_RECORD_NAME_FIELDS` before this change.
    kept = _entry("I", "Member of Gadget Society - Acme Society Exchange Program, 2003",
                  organization="Gadget Society - Acme Society Exchange Program",
                  start_date="2003", membership_type="Member")
    dropped = _entry("I", "Acme Society", organization="Acme Society")
    assert _kept_both("I", dropped, kept)


@pytest.mark.parametrize("dropped_text,kept_org", [
    ("Acme Society", "Acme Society"),
    ("Acme Society", "Acme Society (ACME)"),
    ("Acme Society", "Acme Society (ACME), formerly Gadget Society (GS)"),
])
def test_a_membership_that_is_the_kept_one_reworded_is_still_dropped(dropped_text, kept_org):
    kept = _entry("I", f"Fellow, {kept_org}, 2010-present", organization=kept_org,
                  start_date="2010", membership_type="Fellow")
    dropped = _entry("I", dropped_text, organization=dropped_text)
    assert not _kept_both("I", dropped, kept)


def test_f1_and_q1_name_a_record_by_state_and_organization():
    assert _record_name({"state_country": "Ohio", "license_number": "A1"},
                        _RENDERED_FIELDS["F1"]) == "Ohio"
    assert _record_name({"organization": "Acme Society", "role": "Chair"},
                        _RENDERED_FIELDS["Q1"]) == "Acme Society"
    # A code that fills an earlier name field keeps it.
    assert _record_name({"committee_name": "Review Board", "organization": "Acme"},
                        _RENDERED_FIELDS["Q2"]) == "Review Board"


def test_a_place_only_talk_beside_a_dated_one_at_that_place_is_kept():
    place = "Example University, Department of Widgets"
    kept = _entry("R", f"2004 {place}", location=place, date="2004")
    dropped = _entry("R", place, location=place)
    assert _kept_both("R", dropped, kept)


def test_a_place_only_talk_beside_an_undated_copy_is_still_dropped():
    place = "Example University, Department of Widgets"
    kept = _entry("R", f"{place}, seminar", location=place, event_name="Seminar")
    dropped = _entry("R", place, location=place)
    assert not _kept_both("R", dropped, kept)


def _appointment(institution: str, text: str | None = None, code: str = "D2") -> dict:
    title = "Senior Fellow"
    return _entry(code, text or f"{institution}\t{title}, 2013-present",
                  title=title, institution=institution,
                  start_date="2013", end_date="present")


def test_one_title_at_two_hospitals_is_two_appointments():
    # (c) Both hospitals' words occur in the other's name; their order differs.
    kept = _appointment("Gadget Clinic for Children at Sample Regional")
    dropped = _appointment("Sample Regional Teaching Clinic")
    assert _kept_both("D2", dropped, kept)
    assert _kept_both("D2", _appointment("Gadget Hospital"), _appointment("Acme Hospital"))


@pytest.mark.parametrize("dropped_institution,kept_institution", [
    ("Acme Medical Center", "Acme Medical Center"),
    ("Acme Medical Center", "Acme Medical Center, Springfield, IL"),
    ("Sample Institute, Country Z", "Sample Institute, Citytown, Country Z"),
    ("Division of Widgets, College of", "Division of Widgets, College of Acme"),
    ("U. of Acme", "University of Acme"),
])
def test_one_hospital_worded_two_ways_is_still_one_appointment(dropped_institution,
                                                                kept_institution):
    kept = _appointment(kept_institution)
    dropped = _appointment(dropped_institution)
    assert not _kept_both("D2", dropped, kept)


@pytest.mark.parametrize("longer,shorter", [
    ("Sample Institute, Citytown, Country Z", "Sample Institute, Country Z"),
    ("Acme Medical Center, Springfield, IL", "Acme Medical Center"),
])
def test_an_institution_cut_short_is_the_same_in_either_direction(longer, shorter):
    longer_fields, shorter_fields = {"institution": longer}, {"institution": shorter}
    assert _different_institution("D2", longer_fields, shorter_fields) is None
    assert _different_institution("D2", shorter_fields, longer_fields) is None
    assert _different_institution("D2", longer_fields, {"institution": "Gadget Hospital"}) == longer


def test_a_place_only_row_of_another_code_is_not_a_talk():
    assert _place_only_event("D1", {"location": "Acme"}, {"date": "2004"}) is None
    assert _place_only_event("R", {"location": "Acme"}, {"date": "2004"}) == "Acme"
    assert _place_only_event("R", {"location": "Acme", "title": "Talk"},
                             {"date": "2004"}) is None
    assert _place_only_event("R", {"location": "Acme"}, {}) is None


def test_appointments_of_another_code_are_not_told_apart_by_institution():
    kept = _entry("L3", "Chair, Gadget Hospital", leadership_role="Chair",
                  institution="Gadget Hospital")
    dropped = _entry("L3", "Chair, Acme Health Hospital", leadership_role="Chair",
                     institution="Acme Health Hospital")
    assert _different_institution_for("L3", dropped, kept) is None


def _different_institution_for(code, dropped, kept):
    return _different_institution(code, dropped["extracted_fields"], kept["extracted_fields"])


def _book(title: str, edition: str) -> dict:
    return _entry("S3", f"Doe, Jane, {title} {edition}, Springfield: Example Press, 2017.",
                  authors="Doe, Jane", year="2017", title=title,
                  publisher="Example Press", edition=edition)


def test_a_book_beside_its_instructors_guide_is_kept():
    # (d) The kept title holds the dropped one after "Workbook for".
    kept = _book("Study Workbook for Widget Anatomy Atlas (Revised)", "4th ed")
    dropped = _book("Widget Anatomy Atlas", "4th ed")
    assert _kept_both("S3", dropped, kept)


@pytest.mark.parametrize("kept_title", [
    "Fundamentals of Widget Anatomy Atlas",  # may be the same book reworded
    "Widget Anatomy Atlas",
    "Widget Anatomy Atlas: A Companion",
])
def test_a_title_nested_without_a_companion_word_is_still_dropped(kept_title):
    assert not _kept_both("S3", _book("Widget Anatomy Atlas", "4th ed"), _book(kept_title, "4th ed"))


def _grant(title: str, **fields) -> dict:
    text = title + (" (PI)" if fields.get("pi_role") else "")
    return _entry("M2B", text, title=title, **fields)


def test_a_title_only_grant_row_with_another_title_is_kept():
    # (e) The other cells of the dropped row landed on a different grant.
    kept = _grant("Sprocket Dynamics and Cogs of Gizmo Assembly Under Cold Storage Conditions", pi_role="PI",
                  agency="Example Foundation", start_date="2014", end_date="2016")
    dropped = _grant("Sprocket Dynamics of Gizmo Assembly Under Cold Storage Conditions", pi_role="PI")
    assert _kept_both("M2B", dropped, kept)


def test_a_title_only_grant_row_with_the_kept_title_is_still_dropped():
    kept = _grant("Sprocket Dynamics of Gizmo Assembly Under Cold Storage Conditions", pi_role="PI",
                  agency="Example Foundation", start_date="2014", end_date="2016")
    dropped = _grant("The sprocket dynamics of gizmo assembly under cold storage conditions.")
    assert not _kept_both("M2B", dropped, kept)


def test_a_grant_row_with_an_agency_is_not_a_fragment():
    title = "Sprocket Dynamics of Gizmo Assembly Under Cold Storage Conditions"
    kept_title = "Sprocket Dynamics and Cogs of Gizmo Assembly Under Cold Storage Conditions"
    kept = {"title": kept_title}
    assert _title_only_fragment("M2B", {"title": title}, kept) == title
    assert _title_only_fragment("M2B", {"title": title, "agency": "Example Foundation"},
                                kept) is None
    assert _title_only_fragment("S3", {"title": title}, kept) is None


def test_a_membership_beside_a_unit_of_the_same_society_is_still_dropped():
    # A parallel listing: the kept row opens with the dropped name, and what
    # follows is a council of that society, not another organization.
    kept = _entry("I", "Member, Acme Society - Council on Widgets",
                  organization="Acme Society - Council on Widgets", membership_type="Member")
    other = _entry("I", "Member, Acme Society - Gadget Society Exchange Program",
                   organization="Acme Society - Gadget Society Exchange Program",
                   membership_type="Member")
    assert _kept_both("I", _entry("I", "Acme Society", organization="Acme Society"), other)
    dropped = _entry("I", "Acme Society", organization="Acme Society")
    assert not _kept_both("I", dropped, kept)


def test_a_companion_title_is_told_apart_in_a_book_only():
    dropped, kept = {"title": "Widget Repair"}, {"title": "A Guide to Widget Repair"}
    assert _companion_title("S3", dropped, kept) == "Widget Repair"
    assert _companion_title("R", dropped, kept) is None


def test_a_title_only_grant_that_is_the_kept_title_plus_a_suffix_is_still_dropped():
    kept = _grant("Sprocket Dynamics of Gizmo Assembly Under Cold Storage Conditions (R01)", pi_role="PI",
                  agency="Example Foundation")
    dropped = _grant("Sprocket Dynamics of Gizmo Assembly Under Cold Storage Conditions", pi_role="PI")
    assert not _kept_both("M2B", dropped, kept)


# ------------------------------- #666 (EBYSBC E4): distinct records over-dropped
# Shapes of the EBYSBC autopsy's verified dedup losses, with invented content.

def test_a_course_part_inside_a_longer_part_number_is_not_verbatim():
    # OIYKZE-01: squashed, "... II" sits inside "... III"; so does "Page 2"
    # inside "Page 21". Neither ends on a word boundary there.
    assert not _verbatim_contained("Widget Teamwork Seminar II", "Widget Teamwork Seminar III")
    assert not _verbatim_contained("Page 2", "Page 21")
    assert not _verbatim_contained("Seminar II", "Widget Seminar III and IV")
    assert _verbatim_contained("Widget Teamwork Seminar II", "Fall: Widget Teamwork Seminar II, 3 credits")


def test_spacing_inside_a_verbatim_copy_is_still_ignored():
    # A footnote mark glued onto a word ("Studiesb" for "Studies b") is the same text.
    assert _verbatim_contained("Gizmo Studies b Lecturer, 6 hours",
                               "2031 Gizmo Studiesb Lecturer, 6 hours")


def test_numbered_course_parts_are_both_kept():
    kept = _entry("K1", "Widget Teamwork in Practice (WTP) III",
                  course_title="Widget Teamwork in Practice (WTP) III")
    dropped = _entry("K1", "Widget Teamwork in Practice (WTP) II",
                     course_title="Widget Teamwork in Practice (WTP) II")
    assert _kept_both("K1", dropped, kept)


@pytest.mark.parametrize("text, parts", [
    ("Widget Care Part I, noon conference", {"1"}),
    ("Widget Care Part II", {"2"}),
    ("Widget care, part 2.", {"2"}),
    ("Widget Care Parts I and II", {"1", "2"}),
    ("Widget Care Part 03", {"3"}),
    ("a particular widget, department 2, partly", set()),
])
def test_part_numbers(text, parts):
    assert _part_numbers(text) == parts


_SERIES_TALK = ("“Widget Care Part {}”, co-taught with Dr. Sprocket, Lunch Seminar for "
                "Gizmo Residents, Example University, Harbor City, Nov. 2031")


def test_two_parts_of_one_talk_are_both_kept():
    # XWNZWW-03: "i" is a stop word, so every word of Part I is in Part II.
    kept, dropped = {"text": _SERIES_TALK.format("II")}, {"text": _SERIES_TALK.format("I")}
    assert deduplicate_entries([kept, dropped]) == [kept, dropped]


def test_a_listing_of_both_parts_still_covers_a_copy_of_one():
    listing, dropped = {"text": _SERIES_TALK.format("I and II")}, {"text": _SERIES_TALK.format("I")}
    assert deduplicate_entries([listing, dropped]) == [listing]


def test_one_lecture_given_in_two_months_is_two_records():
    # DPEHSZ-01: the year alone read "3/2029" and "9/2029" as one date.
    kept = {"text": "9/2029 “Widget Repair Basics”, Example Medical School, for trainees"}
    dropped = {"text": "3/2029 “Widget Repair Basics”, Example Medical School, for trainees"}
    assert deduplicate_entries([kept, dropped]) == [kept, dropped]


def test_a_lecture_dated_outside_a_kept_list_of_dates_is_kept():
    kept = {"text": "7/2028 “Gizmo Exam”, Example Medical School, for trainees "
                    "8/2028 “Widget Repair Basics”, Example Medical School, for trainees"}
    dropped = {"text": "6/2028 “Widget Repair Basics”, Example Medical School, for trainees"}
    assert deduplicate_entries([kept, dropped]) == [kept, dropped]
    same_month = {"text": "8/2028 “Widget Repair Basics”, Example Medical School, for trainees"}
    assert deduplicate_entries([kept, same_month]) == [kept]


def _appointment_of_rank(title: str, text: str, **dates) -> dict:
    return _entry("D1", text, title=title, institution="Example University", **dates)


def test_an_appointment_of_another_rank_is_kept():
    # SEKQUI-01: the dropped text is verbatim inside the kept one.
    kept = _appointment_of_rank("Clinical Assistant Professor, Widget Medicine",
                                "2029-2031 Clinical Assistant Professor, Widget Medicine, "
                                "Example University", start_date="2029", end_date="2031")
    dropped = _appointment_of_rank("Assistant Professor, Widget Medicine",
                                   "Assistant Professor, Widget Medicine, Example University")
    assert _kept_both("D1", dropped, kept)
    same_rank = _appointment_of_rank("Clinical Assistant Professor, Widget Medicine",
                                     "Clinical Assistant Professor, Widget Medicine, Example University")
    assert not _kept_both("D1", same_rank, kept)


@pytest.mark.parametrize("code, key, dropped_value, kept_value", [
    ("D1", "title", "Professor", "Associate Professor"),                      # KDAZOM-01
    ("D1", "title", "Professor of Gizmology", "Asst. Clinical Professor"),    # VNUAHA-01
    ("D2", "title", "Fellow", "Senior Fellow"),
    ("H", "award_name", "Gizmo Service Award", "Outreach Gizmo Service Award"),  # NDXXAD-01
    ("P", "committee_name", "Widget Safety Committee", "Widget Safety Research Committee"),  # VYICGW-02
    ("P", "committee_name", "Acme Widget Center", "Executive Committee of the Acme Widget Center"),
    ("O", "leadership_role", "Division Chief", "Co-Division Chief"),
    ("Q1", "role", "President", "Vice President"),
])
def test_another_rank_or_body_is_another_record(code, key, dropped_value, kept_value):
    assert _different_rank(code, {key: dropped_value}, {key: kept_value}) == dropped_value
    assert _different_rank(code, {key: kept_value}, {key: dropped_value}) == kept_value


@pytest.mark.parametrize("code, dropped_fields, kept_fields", [
    ("D1", {"title": "Assoc. Professor"}, {"title": "Associate Professor of Gizmology"}),
    ("D1", {"title": "Asst. Professor"}, {"title": "Assistant Professor of Gizmology"}),
    ("D1", {"title": "Clin. Professor"}, {"title": "Clinical Professor of Gizmology"}),
    ("Q1", {"role": "Chair"}, {"role": "Chair and Treasurer, Board of Governors"}),
    ("P", {"committee_name": "Widget Committee"}, {"committee_name": "Widget Committee", "role": "Member"}),
    ("D1", {"title": "Professor"}, {}),                                  # nothing to compare
    ("R", {"title": "Professor"}, {"title": "Associate Professor"}),     # not a ranked code
])
def test_the_same_rank_or_no_rank_is_not_another_record(code, dropped_fields, kept_fields):
    assert _different_rank(code, dropped_fields, kept_fields) is None


def test_a_committee_of_a_center_is_not_the_center():
    # RNKYST-01: the dropped row is verbatim inside the committee row.
    kept = _entry("P", "2031  Member, Acme Widget Center Seminar Committee",
                  committee_name="Acme Widget Center Seminar Committee", role="Member",
                  start_date="2031")
    dropped = _entry("P", "2031     Member, Acme Widget Center",
                     committee_name="Acme Widget Center", role="Member", start_date="2031")
    assert _kept_both("P", dropped, kept)


def test_one_chapter_in_two_books_is_two_records():
    # TAUBPU-02: a board's curriculum and the pediatric board's.
    def chapter(book: str) -> dict:
        return _entry("S4", f"Quill A, Sprocket B. Widget Repair. Online course for the "
                            f"{book} program. 2031",
                      authors="Quill A, Sprocket B", chapter_title="Widget Repair",
                      book_title=f"Online course for the {book} program", year="2031")
    assert _kept_both("S4", chapter("Example Board of Gizmos"),
                      chapter("Example Board of Pediatric Gizmos"))
    assert _different_book("S4", {"book_title": "Gizmo Atlas"}, {"book_title": "Gizmo Atlas"}) is None
    assert _different_book("S3", {"book_title": "Gizmo Atlas"}, {"book_title": "Widget Atlas"}) is None


def _talk(location: str, event: str) -> dict:
    return _entry("R", f"2031 “Widget Safety for Families” {event} at {location}",
                  title="Widget Safety for Families", location=location, date="2031",
                  event_name=event)


def test_one_lecture_in_two_towns_is_two_talks():
    # RNKYST-01: every word of the first venue is in the second's name.
    kept = _talk("Gizmo Outreach-Harbor City, East City, ZZ", "Talk in French")
    dropped = _talk("Gizmo Outreach-Harbor City, Harbor City, ZZ", "Talk")
    assert _kept_both("R", dropped, kept)


def test_a_venue_cut_short_is_still_one_talk():
    assert _different_institution("R", {"location": "Example University"},
                                  {"location": "Example University College of Widgets"}) is None


def _headed(entry: dict, heading: str) -> dict:
    return dict(entry, hierarchy=[heading])


def test_a_dated_place_under_another_heading_is_another_occasion():
    # TAUBPU: a course's later offering beside a conference talk that day.
    kept = _headed(_entry("R", "Gizmo Families Conference. August 2nd, 2031. Harbor City, Zedland.",
                          location="Harbor City, Zedland", date="2031-08-02",
                          event_name="Gizmo Families Conference"), "GUEST SPEAKER")
    dropped = _headed(_entry("R", "August 2nd, 2031 - Harbor City, Zedland",
                             location="Harbor City, Zedland", date="2031-08-02"), "COURSES")
    assert _kept_both("R", dropped, kept)
    assert not _kept_both("R", _headed(dropped, "GUEST SPEAKER"), kept)
    assert _bare_occasion_apart("D1", dropped, kept) is None


def _award(text: str, name: str, date: str) -> dict:
    return _entry("H", text, award_name=name, granting_body="Example Ceremony", date=date)


_FUSED_AWARDS = ("Gizmo Faculty Award, Example Ceremony, May 2030 "
                 "Widget Faculty Award, Example Ceremony, March 2030 "
                 "Gizmo Faculty Award, Example Ceremony, May 2031")


def test_an_award_of_another_year_inside_a_fused_kept_row_is_kept():
    # VNUAHA-02: the kept row's fields are the 2030 award, so they print no 2031.
    kept = _award(_FUSED_AWARDS, "Gizmo Faculty Award", "2030-05")
    dropped = _award("Gizmo Faculty Award, Example Ceremony, May 2031",
                     "Gizmo Faculty Award", "2031-05")
    assert _kept_both("H", dropped, kept)
    same_year = _award("Gizmo Faculty Award, Example Ceremony, May 2030",
                       "Gizmo Faculty Award", "2030-05")
    assert not _kept_both("H", same_year, kept)


def test_the_award_in_other_years_does_not_vouch_for_this_year():
    # VNUAHA-02: four other years of one award vouched for the dropped fifth.
    kept = _award(_FUSED_AWARDS, "Gizmo Faculty Award", "2030-05")
    dropped = _award("Widget Faculty Award, Example Ceremony, March 2030",
                     "Widget Faculty Award", "2030-03")
    other_year = _award("Widget Faculty Award, Example Ceremony, March 2028",
                        "Widget Faculty Award", "2028-03")
    assert _kept_both("H", dropped, kept, other_year)
    same_year = _award("Widget Faculty Award, Example Ceremony, 2030",
                       "Widget Faculty Award", "2030")
    assert not _kept_both("H", dropped, kept, same_year)


@pytest.mark.parametrize("text, carries", [
    ("Widget Faculty Award, 2030", True),
    ("Widget Faculty Award, March 2030", True),
    ("Widget Faculty Award (every year)", True),     # no year: still a mention
    ("Widget Faculty Award, March 2028", False),
    ("Widget Faculty Award, May 2030", False),       # another month
    ("Gizmo Faculty Award, March 2030", False),
])
def test_carries_record(text, carries):
    assert _carries_record(text, "Widget Faculty Award",
                           "Widget Faculty Award, Example Ceremony, March 2030") is carries


def _row(code: str, index: int, text: str) -> dict:
    return {"taxonomy_code": code, "element_idx_start": index, "text": text,
            "extracted_fields": {}, "hierarchy": ["TEACHING", "Resident Training"]}


def test_one_activity_under_two_employer_lines_is_two_records():
    # BZZNRL-04: the employer lines are rows of their own, not fields.
    first_employer = _row("K4", 1, "Example University School of Widgets (2028-2031)")
    kept = _row("K4", 2, "Gizmo Repair Widget/Imaging Rounds (weekly)")
    second_employer = _row("K4", 5, "Acme University School of Widgets (2031 to present)")
    dropped = _row("K4", 6, "Gizmo Repair Widget Rounds (weekly)")
    assert _kept_both("K4", dropped, kept, first_employer, second_employer)
    assert not _kept_both("K4", dropped, kept, first_employer)
    other_section = dict(second_employer, hierarchy=["SERVICE"])
    assert not _kept_both("K4", dropped, kept, first_employer, other_section)


def test_one_activity_under_two_year_lines_is_two_records():
    rows = [_row("T", 1, "2029"),
            _row("K2", 2, "Widget lab instructor (WID 101), 4 hours, 40 learners"),
            _row("T", 3, "2030 (on leave)"),
            _row("K2", 4, "Widget lab instructor (WID 101), 5 hours, 40 learners")]
    assert _kept_both("K2", rows[3], rows[1], rows[0], rows[2])
    assert not _kept_both("K2", rows[3], rows[1], rows[0])
    assert not _kept_both("D1", rows[3], rows[1], rows[0], rows[2])  # not a list code


@pytest.mark.parametrize("text, is_group_line", [
    ("Example University School of Widgets (2028-2031)", True),
    ("Acme University School of Widgets (2031 to present)", True),
    ("2029", True),
    ("2030 (on leave)", True),
    ("2028-2031", True),
    ("Grand Rounds 2031", False),                         # a record with its year
    ("Widget lab instructor 4 hours, 2028-2031", False),
    ("Gizmo Repair Widget Rounds (weekly)", False),
])
def test_group_header_shape(text, is_group_line):
    assert bool(_GROUP_HEADER_RE.fullmatch(text)) is is_group_line
