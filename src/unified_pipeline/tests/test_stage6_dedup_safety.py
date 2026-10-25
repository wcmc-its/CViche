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
    DEDUP_UNNAMED_SHORT_MAX_WORDS,
    SPLIT_SIBLING_METRIC,
    _ad_hoc_apart,
    _author_name_sets,
    _bare_occasion_apart,
    _carries_record,
    _class_year_apart,
    _companion_title,
    _counts,
    _dates_compatible,
    _decision_fields,
    _different_book,
    _different_institution,
    _different_rank,
    _distinct_bare_names,
    _drop_is_safe,
    _drop_split_siblings,
    _field_dates_text,
    _first_author_names,
    _is_ad_hoc,
    _lists_name,
    _names_a_sibling,
    _names_record,
    _narrower_committee,
    _one_slip_apart,
    _other_award_name,
    _other_journal_same_row,
    _other_series_member,
    _part_numbers,
    _place_only_event,
    _record_carried,
    _record_name,
    _row_residue,
    _same_row,
    _series_marks,
    _shorter_journal_than_kept,
    _split_record_family,
    _standalone_numerals,
    _title_only_fragment,
    _undated_split_record_absorbs_date,
    _under_another_year_head,
    _verbatim_contained,
    _year_head,
    recovered_row_already_rendered,
)
from unified_pipeline.stage6.fan_out import (  # noqa: E402
    _RENDERED_FIELDS,
    FANNED_OUT_FROM,
)
from unified_pipeline.stage6.normalization.publication import (  # noqa: E402
    ResolvedPublication,
)
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


def test_the_same_course_with_another_hours_count_is_kept():
    # #666 (VGHNZD K1): every word of the 3-hour row is in the 4-hour row;
    # only the count tells the two years of the course apart.
    kept = {"text": "Example Curriculum Coordinator, Lecturer, Workshop Facilitator, "
                    "Squid Optics 4 hrs, 143 students"}
    other_year = {"text": "Example Curriculum Lecturer and Workshop Facilitator, Squid Optics 3 hrs, 143 students"}
    assert deduplicate_entries([kept, other_year]) == [kept, other_year]


def test_a_copy_with_the_same_counts_still_drops():
    # The same row with a year added and a space lost: a true duplicate.
    kept = {"text": "2002 Example Policy & Mgmta Co-Coordinator and Lecturer, Squid Design (2 hrs) 20 hrs, 13 students"}
    dup = {"text": "Example Policy & Mgmt a Co-Coordinator and Lecturer, Squid Design (2 hrs) 20 hrs, 13 students"}
    assert deduplicate_entries([kept, dup]) == [kept]


def test_a_bare_list_number_is_not_a_count():
    # A renumbered copy ("(9)" beside "(11)") is the same citation.
    assert _counts("(9) Quill AB, Reed EF. The squid lens. Example J. 2001") == set()


@pytest.mark.parametrize("text, counts", [
    ("Squid Optics 3 hrs, 143 students", {("3", "hour"), ("143", "student")}),
    ("2 hours; 1 lecture; 12 Residents", {("2", "hour"), ("1", "lecture"), ("12", "resident")}),
])
def test_counts(text, counts):
    assert _counts(text) == counts


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
    # The dropped row states the kept name's added words, so the P pair is not
    # a narrower committee (`_narrower_committee`) and still drops.
    decisions = []
    text = "Example Name Instructor Guide Alpha Beta Gamma Delta Epsilon Zeta 2001"
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
    ("May 45 2029", "May 3, 2029", True),                        # 45 is no day: the month alone
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
    "N3": "mentee_name", "N3A": "mentee_name", "N3B": "mentee_name", "O": "leadership_role",
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


# #666 (WZSMUO Q4D): a short entry stage 4 extracted no field for.

def _bare(text: str) -> dict:
    return {"taxonomy_code": "Q4D", "text": text, "extracted_fields": {}}


@pytest.mark.parametrize("short_text,kept_name", [
    ("Widgets", "Widgets Reports"),  # containment 1.00, jaccard 0.50
    ("Widgets Letters", "Widgets Gizmo Letters"),  # verbatim-free, jaccard 0.67
    ("Widgets", "Widgets: A Gizmo Journal"),
])
def test_an_unnamed_short_entry_inside_a_longer_kept_name_is_kept(short_text, kept_name):
    kept, short = _journal(kept_name), _bare(short_text)
    decisions = []
    assert deduplicate_entries([kept, short], code="Q4D", document=[kept, short],
                               decisions=decisions) == [kept, short]
    assert decisions == []


def test_an_unnamed_short_exact_copy_still_drops():
    kept, copy = _journal("Widgets"), _bare("Widgets")
    assert deduplicate_entries([kept, copy], code="Q4D",
                               document=[kept, copy]) == [kept]


def test_an_unnamed_short_entry_beside_a_kept_entry_with_no_name_still_drops():
    # A label fragment beside a longer unnamed line: no name tells them apart.
    kept, short = _bare("Widgets Reports"), _bare("Widgets")
    assert deduplicate_entries([kept, short], code="Q4D",
                               document=[kept, short]) == [kept]


def test_an_unnamed_short_entry_beside_a_kept_name_that_is_no_journal_still_drops():
    # A table cell cut a talk title into fragments; they are not records.
    kept = _entry("R", "Widget Angiography:\tInvited Speaker\tand Gizmo Detection\tSprocket Disease 2015",
                  title="Widget Angiography: Applications to Gizmo Detection and "
                        "Sprocket Disease")
    assert not _kept_both("R", _entry("R", "Sprocket Disease"), kept)
    # A bare listing of the society a longer row names.
    kept = _entry("I", "Fellow, Acme Society, 2010-present", organization="Acme Society",
                  start_date="2010", membership_type="Fellow")
    assert not _kept_both("I", _entry("I", "Acme Society", note="bare"), kept)


@pytest.mark.parametrize("dropped,kept_name,shorter", [
    ({"text": "Widgets"}, "Widgets Reports", True),
    ({"text": "Widgets Letters"}, "Widgets Gizmo Letters", True),
    # One word over the cap: a row, whose extra words a name need not hold.
    ({"text": "Widgets Gizmo Letters"}, "Widgets Gizmo Letters Quarterly",
     DEDUP_UNNAMED_SHORT_MAX_WORDS >= 3),
    ({"text": "Widgets"}, "Widgets", False),  # same name
    ({"text": "Widgets", "extracted_fields": {"journal_name": "Widgets"}},
     "Widgets Reports", False),  # named: `_record_would_be_lost` judges it
    ({"text": "Gizmo"}, "Widgets Reports", False),  # not a subset
])
def test_shorter_journal_than_kept(dropped, kept_name, shorter):
    kept = {"extracted_fields": {"journal_name": kept_name}}
    assert _shorter_journal_than_kept(dropped, kept, "Q4D") is shorter


def test_shorter_journal_than_kept_reads_only_a_rendered_journal_name():
    dropped = {"text": "Widgets"}
    assert _shorter_journal_than_kept(
        dropped, {"extracted_fields": {"title": "Widgets Reports"}}, "R") is False
    assert _shorter_journal_than_kept(
        dropped, {"extracted_fields": {"organization": "Widgets Society"}}, "Q2") is False
    assert _shorter_journal_than_kept(
        dropped, {"extracted_fields": {"journal_name": "Widgets Reports"}}, None) is False


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
    # a quoted title is one item: a book's title opening "Widgets:" lists no journal
    ({"text": "“Widgets: Gizmos, Sprockets and Cogs” A. Writer.\tBook Proposal Reviewer"}, False),
    ({"text": '"Widgets: Gizmos" A. Writer'}, False),
    ({"text": "Reviewer for “Gizmos”, “Widgets”"}, True),  # quoted items of a list
    ({"text": '"Gizmos"; "Widgets"'}, True),
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
    assert not _verbatim_contained("2 Widget Rounds", "12 Widget Rounds")
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


def test_a_course_part_is_kept_when_another_entry_names_the_longer_part():
    # OIYKZE-01's shape: a third entry names part III too. Its letters hold
    # "... II", so it lists the dropped name and `_distinct_bare_names` keeps
    # nothing; only the word boundary in `_verbatim_contained` keeps part II.
    kept = _entry("K1", "Widget Teamwork Seminar III",
                  course_title="Widget Teamwork Seminar III")
    dropped = _entry("K1", "Widget Teamwork Seminar II",
                     course_title="Widget Teamwork Seminar II")
    other = _entry("K1", "Widget Teamwork Seminar III, spring term",
                   course_title="Widget Teamwork Seminar III")
    assert _kept_both("K1", dropped, kept, other)


@pytest.mark.parametrize("text, parts", [
    ("Widget Care Part I, noon conference", {"1"}),
    ("Widget Care Part II", {"2"}),
    ("Widget care, part 2.", {"2"}),
    ("Widget Care Parts I and II", {"1", "2"}),
    ("Widget Care Part 03", {"3"}),
    ("Widget Care Part V", {"5"}),        # numerals past iv map too
    ("Widget Care Part VIII", {"8"}),
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


# #666 (OIEPQD YYVITM 425): a bare trailing Roman numeral names the course in
# a series as a part number does, with no "Part" word before it. "i" is a stop
# word, so every word of the "I" row is in the "II" row.
_SECTION_LEAD_ROW = ("Section Lead: Widget Compounding {} November 2031 - present\t"
                     "32 students daily; Contact time: 15 hours annually")


def test_two_courses_of_a_series_told_apart_by_a_bare_numeral_are_both_kept():
    kept = _entry("K1", _SECTION_LEAD_ROW.format("II"),
                  course_title="Widget Compounding II", role="Section Lead")
    dropped = _entry("K1", _SECTION_LEAD_ROW.format("I"),
                     course_title="Widget Compounding I", role="Section Lead")
    assert _kept_both("K1", dropped, kept)
    assert not _drop_is_safe(dropped, kept, "K1", [kept, dropped])


def test_a_copy_of_the_same_numbered_course_still_drops():
    kept = _entry("K1", _SECTION_LEAD_ROW.format("II") + ", Example School",
                  course_title="Widget Compounding II", role="Section Lead")
    dropped = _entry("K1", _SECTION_LEAD_ROW.format("II"),
                     course_title="Widget Compounding II", role="Section Lead")
    assert not _kept_both("K1", dropped, kept)


def test_a_course_whose_lone_numeral_a_dash_follows_is_kept():
    # The verifier's under-match: a lone "I" before " - Section Lead".
    kept = _entry("K1", "Widget Compounding II - Section Lead, Example School, 2031")
    dropped = _entry("K1", "Widget Compounding I - Section Lead, Example School, 2031")
    assert not _drop_is_safe(dropped, kept, "K1", [kept, dropped])


def test_a_citation_whose_author_initial_is_a_lone_i_still_drops():
    # "Sprocket I" beside "Sprocket IS" is one author: the initial is no numeral.
    citation = ("Sprocket {}, Gizmo J. Widget outcomes in adults with chronic "
                "widget disease. J Widg. 2031;12:100-110.")
    kept, dropped = {"text": citation.format("IS")}, {"text": citation.format("I")}
    assert deduplicate_entries([kept, dropped]) == [kept]


@pytest.mark.parametrize("text, numerals", [
    ("Widget Compounding I November 2031 - present", {"I"}),   # a month follows
    ("Widget Compounding I 2031-2033", {"I"}),                 # a year follows
    ("Widget Compounding I 3/2031", {"I"}),                    # a numeric date follows
    ("Widget Compounding I\tExample Society", {"I"}),          # the segment ends
    ("Widget Compounding I", {"I"}),
    ("Widget Seminar III, spring", {"III"}),
    ("I taught Widget Compounding", set()),                    # a pronoun
    ("I may 2031 teach", set()),                               # lower case is a word, not a month
    ("Widget Care Parts I and II", set()),                     # a part listing is `_part_numbers`'
    ("Example trial, Phase II", set()),                        # and a phase is `_trial_phases`'
    ("Sprocket V, Gizmo X. Widget study.", set()),             # initials, never V or X alone
    ("Widget Compounding I - Section Lead, 2031", {"I"}),      # a dash follows
    ("Widget Compounding I \u2013 2031", {"I"}),               # an en dash follows
    ("Widget Compounding I (Fall 2031)", {"I"}),               # a parenthesis follows
    ("Widget Compounding I Lecturer, 2031", {"I"}),            # a capitalised word follows
    ("where I Lectured on widgets", set()),                    # a pronoun after a lower-case word
    ("Sprocket I, Gizmo J. Widget study.", set()),             # a byline's lone initial
    ("Gizmo J, Sprocket I. Widget study.", set()),             # last in the byline
    ("Widget Seminar II, Section A", {"II"}),                  # "Seminar II" is no author
    ("Widget Pharmacology I/II, 2031", {"I", "II"}),           # a slash listing names both
    ("Widget Pharmacology I / III", {"I", "III"}),
    ("Widget Pharmacology II/I", {"I", "II"}),
    ("Widget I/O Board, 2031", set()),                         # a slash before no numeral
    ("Widget Pharmacology I/V", set()),                        # V is never a numeral alone
    ("Widget Pharmacology I and II, 2031", {"I", "II"}),       # so is an "and" listing
    ("Widget Pharmacology I & II", {"I", "II"}),
    ("Widget study, I and colleagues", set()),                 # "and" before no numeral
    ("Widget Course I andIV", set()),                          # "and" is a whole word
])
def test_standalone_numerals(text, numerals):
    assert _standalone_numerals(text) == numerals


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
    ("P", "committee_name", "Widget Safety Committee", "Executive Widget Safety Committee"),
    ("O", "leadership_role", "Division Chief", "Co-Division Chief"),
    ("Q1", "role", "President", "Vice President"),
    ("D1", "title", "Chair, Widget Medicine", "Interim Chair, Widget Medicine"),
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
    ("D1", {"title": "Associate Professor"}, {"institution": "Acme"}),   # no kept title
    ("R", {"title": "Professor"}, {"title": "Associate Professor"}),     # not a ranked code
])
def test_the_same_rank_or_no_rank_is_not_another_record(code, dropped_fields, kept_fields):
    assert _different_rank(code, dropped_fields, kept_fields) is None


def test_a_committee_role_of_another_rank_is_another_record():
    # P ranks by role too: the chair and the vice chair of one committee.
    dropped = {"committee_name": "Widget Safety Committee", "role": "Chair"}
    kept = {"committee_name": "Widget Safety Committee", "role": "Vice Chair"}
    assert _different_rank("P", dropped, kept) == "Widget Safety Committee"
    assert _different_rank("P", dropped, dict(kept, role="Chair")) is None


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


def test_a_titled_talk_under_another_heading_is_still_dropped():
    # Only a row that is a place and a date takes its event from its heading.
    # A row that names its talk is that talk listed again under any heading,
    # and keeping it would print it twice.
    title, place = "Widget Safety for Families", "Harbor City, Zedland"
    kept = _headed(_entry("R", f"Gizmo Families Conference: “{title}”, {place}, August 2nd, 2031",
                          title=title, location=place, date="2031-08-02",
                          event_name="Gizmo Families Conference"), "GUEST SPEAKER")
    dropped = _headed(_entry("R", f"“{title}”, {place}, August 2nd, 2031",
                             title=title, location=place, date="2031-08-02"), "COURSES")
    assert _bare_occasion_apart("R", dropped, kept) is None
    assert not _kept_both("R", dropped, kept)


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


def test_a_group_line_over_the_dropped_row_alone_does_not_refuse_the_drop():
    # The kept row sits in another source section, so it is listed under
    # neither group line: the line above the dropped row tells nothing apart.
    kept = dict(_row("K4", 2, "Gizmo Repair Widget/Imaging Rounds (weekly)"), hierarchy=["SERVICE"])
    employer = _row("K4", 5, "Acme University School of Widgets (2031 to present)")
    dropped = _row("K4", 6, "Gizmo Repair Widget Rounds (weekly)")
    assert not _kept_both("K4", dropped, kept, employer)


def test_a_pair_whose_own_rows_have_group_line_shape_is_still_deduplicated():
    # Only a line strictly between the two rows is a group line over them:
    # the rows themselves read as a name and a year range.
    kept = _row("K4", 2, "Gizmo Repair Widget Rounds (2028-2031)")
    dropped = _row("K4", 6, "Gizmo Repair Widget Rounds (2028-2031)")
    assert not _kept_both("K4", dropped, kept)


def test_one_activity_under_two_year_lines_is_two_records():
    rows = [_row("T", 1, "2029"),
            _row("K2", 2, "Widget lab instructor (WID 101), 4 hours, 40 learners"),
            _row("T", 3, "2030 (on leave)"),
            # The same count as row 2: only the year lines tell them apart (a
            # different count alone keeps both, see the hours-count test).
            _row("K2", 4, "Widget lab instructor (WID 101), 4 hours, 40 learners")]
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


# ------------------------------------- #446: citation identity before similarity

def _pub(text, **fields):
    """An S-code entry: its source line and its extracted citation fields."""
    return {"text": text, "extracted_fields": dict(fields)}


def _enriched(text, pmid, pubmed_title, **fields):
    entry = _pub(text, pmid=pmid, **fields)
    entry["enrichment_status"] = "enriched"
    entry["enrichment_data"] = {"pubmed_title": pubmed_title}
    return entry


def test_same_pmid_keeps_one_copy_whatever_the_notes_say():
    # EBYSBC SJWASY-01: the CV repeats a paper under a second heading with
    # its own role note; the copies share a PMID and the word-similarity loop
    # lets both through. The fuller (longer-noted) copy is kept.
    first = _enriched("Quill A, Brandt B. Gizmo outcomes in a toy cohort. J Imag Stud. "
                      "2019;4:1-9. Roles: designed the study, wrote the manuscript, analysed "
                      "the data, revised drafts.", "31234567", "Gizmo outcomes in a toy cohort.",
                      year=2019, volume="4")
    second = _enriched("Quill A, Brandt B. Gizmo outcomes in a toy cohort. J Imag Stud. 2019.",
                       "31234567", "Gizmo outcomes in a toy cohort.", year=2019, volume="5")
    decisions = []
    assert deduplicate_entries([second, first], decisions=decisions, code="S1") == [first]
    assert decisions[0]["metric"] == "citation_pmid"
    assert decisions[0]["dropped_text"] == decisions[0]["kept_text"] == "Gizmo outcomes in a toy cohort.. 2019"


def test_pmid_only_tail_drops_against_the_whole_citation():
    # EBYSBC NDXXAD-02 idx 247: a page break left "PubMed PMID: ..." as its
    # own entry, which enrichment completed into a second copy.
    whole = _enriched("Quill A. Gizmo outcomes. J Imag Stud. 2018;3:5-6.", "30000001",
                      "Gizmo outcomes.", year=2018, journal="J Imag Stud")
    tail = _enriched("27. PubMed PMID: 30000001.", "30000001", "Gizmo outcomes.")
    assert deduplicate_entries([whole, tail], code="S1") == [whole]


def test_same_title_journal_year_without_identifiers_is_one_paper():
    # EBYSBC SJWASY-01 (357/635) and VVRTUC-06: case and date form differ.
    a = _pub("Quill A, Brandt B. Onward with the widget. J Gadget Ed. 2016;3:11-14. Wrote it.",
             authors="Quill A, Brandt B", title="Onward with the widget", journal="J Gadget Ed",
             year="2016", pages="11-14")
    b = _pub("Quill A, Brandt B. Onward with the widget. J Gadget Ed. 2016 May;3:11-14.",
             authors="Quill A, Brandt B", title="Onward with the widget", journal="J Gadget Ed",
             year=2016, pages="11-14")
    assert deduplicate_entries([a, b], code="S2") == [a]


def test_identical_5d_citations_of_split_halves_are_one_paper():
    # EBYSBC MIFYLG-02: stage 2 split a citation; 5d completed both halves
    # into the same citation while the halves' own title fields differ.
    citation = "Quill A, Brandt B. Sprocket links in the toy gearbox. Toy Path. 2004:1-9."
    head = _pub("12. Quill A, Brandt B.", title="Quill", formatted_citation=citation,
                formatting_source="stage_5d_llm")
    tail = _pub("Sprocket links in the toy gearbox. Toy Path. 2004:1-9.",
                title="Sprocket links in the toy gearbox", formatted_citation=citation,
                formatting_source="stage_5d_llm")
    decisions = []
    assert len(deduplicate_entries([head, tail], decisions=decisions, code="S4")) == 1
    assert decisions[0]["metric"] == "citation_citation"
    assert decisions[0]["dropped_text"] == decisions[0]["kept_text"] == citation


@pytest.mark.parametrize("first, second", [
    # One supplement DOI over two different abstracts (farm: TXTATQ).
    ({"doi": "10.9999/supp.1", "title": "Oiling of sprockets in winter", "year": 2020},
     {"doi": "https://doi.org/10.9999/SUPP.1", "title": "Audits of gadgets in the toy ward",
      "year": 2020}),
    # One poster title by two groups at one meeting (farm: RGUNJV).
    ({"authors": "Quill A, Pike S", "title": "Gadget signals in widget fatigue",
      "journal": "Toy Pain Society", "year": 2013},
     {"authors": "Brandt B, Pike S", "title": "Gadget signals in widget fatigue",
      "journal": "Toy Pain Society", "year": 2013}),
    # A letter and its reply: one title, journal and year (farm: YOXXOH).
    ({"authors": "Quill A", "title": "Allegiance among sprocket makers", "journal": "N Toy J",
      "year": 1975, "volume": "293"},
     {"authors": "Quill A", "title": "Allegiance among sprocket makers", "journal": "N Toy J",
      "year": 1975, "volume": "292"}),
    # Two papers in one volume: title, journal, year, volume and first author
    # agree, the pages do not (farm: RGUNJV, XWNZWW).
    ({"authors": "Quill A, Pike S", "title": "Gadget signals in widget fatigue",
      "journal": "Toy Pain Society", "year": 2013, "volume": "14", "pages": "S21"},
     {"authors": "Quill A, Brandt B", "title": "Gadget signals in widget fatigue",
      "journal": "Toy Pain Society", "year": 2013, "volume": "14", "pages": "S88"}),
    # Two protocol reports under one DOI that differ by a patient group: the
    # titles share 5 of 7 significant words (Jaccard 0.71, under 0.8).
    ({"doi": "10.9999/supp.2", "title": "Widget protocol report for toy cohort adults",
      "year": 2021},
     {"doi": "10.9999/supp.2", "title": "Widget protocol report for toy cohort children",
      "year": 2021}),
    # Same PMID typed on two different papers.
    ({"pmid": "30000001", "title": "Gizmo outcomes", "year": 2018},
     {"pmid": "30000001", "title": "Unrelated widget trial", "year": 2018}),
    # One DOI and title under two PMIDs: two records in PubMed.
    ({"doi": "10.9999/rep.2", "pmid": "30000001", "title": "Widget registry update", "year": 2018},
     {"doi": "10.9999/rep.2", "pmid": "30000002", "title": "Widget registry update", "year": 2018}),
    # One PMID and title under two DOIs.
    ({"pmid": "30000003", "doi": "10.9999/rep.3", "title": "Widget registry update", "year": 2018},
     {"pmid": "30000003", "doi": "10.9999/rep.4", "title": "Widget registry update", "year": 2018}),
    # One DOI and title in two years: an annual report re-issued.
    ({"doi": "10.9999/rep.1", "title": "Annual widget report", "year": 2018},
     {"doi": "10.9999/rep.1", "title": "Annual widget report", "year": 2019}),
])
def test_a_shared_key_with_a_conflict_keeps_both(first, second):
    a, b = _pub("first source line", **first), _pub("second source line, longer", **second)
    assert deduplicate_entries([a, b], code="S8") == [a, b]


def test_one_supplement_doi_over_two_printed_titles_keeps_both():
    # The CV gave no title; PubMed printed a different one on each.
    a = _enriched("Oiling of sprockets in winter, poster.", "", "Oiling of sprockets.",
                  doi="10.9999/supp.1")
    b = _enriched("Audits of gadgets in the toy ward overnight.", "", "Audits of gadgets.",
                  doi="10.9999/supp.1")
    assert deduplicate_entries([a, b], code="S8") == [a, b]


def test_same_doi_in_two_spellings_is_one_paper():
    a = _pub("Quill A. Gizmo outcomes. J Imag Stud. 2019. https://doi.org/10.9999/GIZ.7",
             doi="https://doi.org/10.9999/GIZ.7", title="Gizmo outcomes", year=2019)
    b = _pub("Quill A. Gizmo outcomes. 2019.", doi="doi: 10.9999/giz.7.", title="Gizmo outcomes")
    decisions = []
    assert deduplicate_entries([a, b], decisions=decisions, code="S1") == [a]
    assert decisions[0]["metric"] == "citation_doi"


def test_two_cv_titles_resolved_to_one_pubmed_record_keep_both():
    # EBYSBC AQCLHS-02: the CV gave two papers one DOI, and enrichment printed
    # the first paper's PubMed title on both. The CV's own titles disagree.
    first = _enriched("Quill A. Yields of a gadget club. 2020.", "32800001",
                      "Yields of a gadget club.", doi="10.9999/gad.5",
                      title="Yields of a gadget club", year=2020)
    second = _enriched("Quill A. Two routes into a sprocket club. 2020.",
                       "32800001", "Yields of a gadget club.", doi="10.9999/gad.5",
                       title="Two routes into a sprocket club", year=2020)
    assert deduplicate_entries([first, second], code="S1") == [first, second]


def test_a_shared_pmid_survives_a_volume_typo_and_an_author_spelling():
    # Farm: RXYBVF 659/664. PMID and title agree; the CV's own volume and
    # author spelling do not.
    a = _pub("Xander J. Edge crispness of gizmos. Toy Phys. 2012;38:100-9.", pmid="29999901",
             authors="J Xander", title="Edge crispness of gizmos", year=2012, volume="38")
    b = _pub("Xander J. Edge crispness of gizmos. Toy Phys. 2012;39:100-9. Extra.",
             pmid="29999901", authors="Xander J", title="Edge crispness of gizmos",
             year=2012, volume="39")
    assert deduplicate_entries([a, b], code="S1") == [b]


def test_a_titled_commentary_under_the_papers_doi_keeps_both():
    # #446: an "Editorial: <title>" by another author carries the paper's
    # DOI in the same list. The longer editorial line used to drop the paper.
    paper = _pub("Quill A, Brandt B. Gizmo outcomes in a toy cohort. J Imag Stud. 2019.",
                 doi="10.9999/giz.8", authors="Quill A, Brandt B",
                 title="Gizmo outcomes in a toy cohort", year=2019)
    editorial = _pub("Pike S. Editorial: Gizmo outcomes in a toy cohort. J Imag Stud. 2019. "
                     "Invited commentary on the paper above.", doi="10.9999/giz.8",
                     authors="Pike S", title="Editorial: Gizmo outcomes in a toy cohort",
                     year=2019)
    assert deduplicate_entries([paper, editorial], code="S1") == [paper, editorial]


@pytest.mark.parametrize("first_title, second_title", [
    ("Widget care across the ages, part I", "Widget care across the ages, part II"),
    ("Widget care across the ages, part 1", "Widget care across the ages, part 2"),
])
def test_two_parts_under_one_doi_keep_both(first_title, second_title):
    a = _pub("Quill A. " + first_title + ". J Toy Care. 2020.", doi="10.9999/wid.3",
             authors="Quill A", title=first_title, year=2020)
    b = _pub("Quill A. " + second_title + ". J Toy Care. 2020. Second listing.",
             doi="10.9999/wid.3", authors="Quill A", title=second_title, year=2020)
    assert deduplicate_entries([a, b], code="S1") == [a, b]


def test_two_parts_under_one_pubmed_record_keep_both_on_the_cvs_own_titles():
    # Enrichment printed one PubMed title on both; the CV's own titles name
    # Part I and Part II.
    # Seven words beside the numeral, so the titles still agree (Jaccard 7/8).
    title = "Widget care across seven northern toy ages, part "
    a = _enriched("first source line", "32800002", title + "I.",
                  authors="Quill A", title=title + "I", year=2020)
    b = _enriched("second source line, longer", "32800002", title + "I.",
                  authors="Quill A", title=title + "II", year=2020)
    assert deduplicate_entries([a, b], code="S1") == [a, b]



def test_two_parts_only_pubmed_titles_keep_both():
    # No CV title on either copy; the PubMed titles name Part I and Part II.
    # Seven words beside the numeral, so the titles still agree (Jaccard 7/8).
    title = "Widget care across seven northern toy ages, part "
    a = _enriched("first source line", "", title + "I.", doi="10.9999/wid.5",
                  authors="Quill A", year=2020)
    b = _enriched("second source line, longer", "", title + "II.", doi="10.9999/wid.5",
                  authors="Quill A", year=2020)
    assert deduplicate_entries([a, b], code="S1") == [a, b]

def test_a_part_number_on_one_copy_only_still_drops():
    # A title that names no part says nothing about which part it is, so a
    # shared DOI or PMID still drops one copy, as on dev.
    # Nine words beside "part II", so the printed titles still agree (9/11).
    title = "Widget care across seven northern toy ages in winter clinics"
    plain = _pub("first source line", doi="10.9999/wid.4", authors="Quill A",
                 title=title, year=2020)
    enriched = _enriched("second source line, longer", "", title + ", part II.",
                         doi="10.9999/wid.4", authors="Quill A", title=title, year=2020)
    assert deduplicate_entries([plain, enriched], code="S1") == [enriched]
    a = _pub("first source line", doi="10.9999/wid.3", authors="Quill A",
             title="Widget care across the ages, part I", year=2020)
    b = _pub("second source line, longer", doi="10.9999/wid.3", authors="Quill A",
             title="Widget care across the ages", year=2020)
    assert deduplicate_entries([a, b], code="S1") == [b]
    # One copy has no CV title; both PubMed titles name Part II.
    title = "Edge crispness of gizmos, part II"
    no_title = _enriched("first source line", "11111111", title, authors="Xander J", year=2012)
    titled = _enriched("second source line, longer", "11111111", title,
                       authors="Xander J", title=title, year=2012)
    assert deduplicate_entries([no_title, titled], code="S1") == [titled]


@pytest.mark.parametrize("first_authors, second_authors", [
    ("Xander J, Pike S", "Xandr J, Pike S"),        # one letter off
    ("Xander J", "Jane A. Xander"),                 # given name first
    ("Xander, J., Pike, S.", "Xander J"),           # surname, comma, initials
    ("Müller K", "Muller K"),                       # an accent dropped
    ("JA", "Xander J"),                             # no name word on one copy
    ("", "Xander J"),                               # no author list on one copy
    ("2012", "Xander J"),                           # a year read as the author list
    ("Ibáñez J, Pike S", "Ibanez J, Pike S"),       # two accents dropped
    ("Gómez-Sánchez J", "Gomez-Sanchez J"),         # accented compound surname
    ("Garcia-Lopez M", "Garcia Lopez M"),           # hyphen or space
    ("Garcia-Lopez M", "Garcia M"),                 # one part of a compound
    ("Smith-Jones A", "Jones A"),                   # the other part
    ("van der Berg J", "Vanderberg J"),             # particles run together
    ("Xander J", "Xadner J"),                       # two letters swapped
    ("The Toy Consortium, Xander J", "Xander J, Pike S"),  # a group credited first
    ("Xander JA, Pike S", "Pike S, Xander JA"),     # co-first authors the other way round
    ("Pike S et al.", "Quill A, Brandt B, Pike S"),  # first author later in the other list
    ("Quill A, Brandt B, Pike S", "Pike S et al."),  # the same, the other copy first
])
def test_a_shared_doi_drops_when_the_first_authors_can_be_one_person(first_authors,
                                                                     second_authors):
    # Source lines that share no words, so only the identity pass can drop one.
    # Journal, volume and pages agree too, but the authors' first words
    # differ, so the title, journal and year key cannot drop one either.
    a = _pub("first source line", doi="10.9999/edge.1", authors=first_authors,
             title="Edge crispness of gizmos", journal="J Toy Care", volume="4",
             pages="1-9", year=2012)
    b = _pub("second source line, longer", doi="10.9999/edge.1", authors=second_authors,
             title="Edge crispness of gizmos", journal="J Toy Care", volume="4",
             pages="1-9", year=2012)
    decisions = []
    assert deduplicate_entries([a, b], decisions=decisions, code="S1") == [b]
    assert decisions[0]["metric"] == "citation_doi"


def test_a_shared_pmid_drops_when_co_first_authors_are_listed_the_other_way_round():
    # The owner lists themself first on a co-first paper; PubMed lists the
    # other co-first author first. One paper, so one copy is dropped, as on dev.
    title = "Edge crispness of gizmos"
    a = _enriched("first source line", "11111112", title + ".", authors="Xander JA, Pike S",
                  title=title, year=2012)
    b = _enriched("second source line, longer", "11111112", title + ".",
                  authors="Pike S, Xander JA", title=title, year=2012)
    decisions = []
    assert deduplicate_entries([a, b], decisions=decisions, code="S1") == [b]
    assert decisions[0]["metric"] == "citation_pmid"


@pytest.mark.parametrize("paper_authors, commentary_authors", [
    ("Xander JA et al.", "Pike S et al."),
    ("Xander JA, et al", "Pike S, et al."),
    # A bogus author "others" would sit one letter off a "Rothers".
    ("Xander JA and others", "Rothers J"),
])
def test_a_titled_commentary_by_et_al_authors_keeps_both(paper_authors, commentary_authors):
    # #446 round 3: "et al" is no author's name, so "Pike S et al." and
    # "Xander JA et al." share no author and the commentary keeps its line.
    paper = _pub("first source line", doi="10.9999/giz.9", authors=paper_authors,
                 title="Gizmo outcomes in a toy cohort", year=2019)
    commentary = _pub("second source line, longer", doi="10.9999/giz.9",
                      authors=commentary_authors,
                      title="Editorial: Gizmo outcomes in a toy cohort", year=2019)
    assert deduplicate_entries([paper, commentary], code="S1") == [paper, commentary]


@pytest.mark.parametrize("authors, name_sets", [
    ("Xander J, Pike S and Quill A", [{"xander"}, {"pike"}, {"quill"}]),
    ("Xander J; Pike S & Quill A, et al.", [{"xander"}, {"pike"}, {"quill"}, set()]),
    ("Xander J and others", [{"xander"}]),
])
def test_author_name_sets(authors, name_sets):
    assert _author_name_sets(ResolvedPublication(authors=authors)) == name_sets


@pytest.mark.parametrize("authors, names", [
    ("Xander J, Pike S", {"xander"}),
    ("Pike S et al.", {"pike"}),                    # "et al" is no name
    ("Pike S et. al, Xander J", {"pike"}),
    ("Pike S and others", {"pike"}),
    ("Bouquet Al", {"bouquet", "al", "bouquetal"}),  # "et Al" inside a name is no "et al"
    ("Roux et Alba J", {"roux", "et", "alba", "rouxetalba"}),
    ("J Xander", {"xander"}),
    ("Jane A. Xander and Sam Pike", {"jane", "xander", "janexander"}),
    ("Xander, J.; Pike, S.", {"xander"}),
    ("XANDER JA", {"xander"}),
    ("LI J", set()),                                # all capitals, as short as initials
    ("Li J", {"li"}),
    ("", set()),
    ("12, Xander J", set()),                        # a list number, no name word
    ("Gómez-Sánchez J", {"gomez", "sanchez", "gomezsanchez"}),
    ("van der Berg J", {"berg", "vanderberg"}),     # particles only in the joined name
    ("Le K", {"le"}),                               # a particle-shaped surname
    ("The Toy Consortium, Xander J", set()),        # a group author
])
def test_first_author_names(authors, names):
    assert _first_author_names(ResolvedPublication(authors=authors)) == names



@pytest.mark.parametrize("a, b, slip", [
    ("xander", "xadner", True),                     # two neighbours swapped
    ("xander", "xandr", True),                      # one letter dropped
    ("xander", "xbcder", False),                    # two neighbours changed, not swapped
    ("ab", "bacd", False),                          # a swap at the start, then two more letters
    ("xander", "xander", False),                    # equal is not a slip (the caller checks ==)
])
def test_one_slip_apart(a, b, slip):
    assert _one_slip_apart(a, b) is slip

def test_untitled_fragments_share_no_identity_key():
    # Two split-off place names with the same 5d citation and no title are
    # not identified as one paper (they are not papers; the renderer
    # declines both to the Appendix).
    a = _pub("Ruritania.", formatted_citation="Ruritania.", formatting_source="stage_5d_llm")
    b = _pub("Ruritania.", formatted_citation="Ruritania.", formatting_source="stage_5d_llm")
    placeholder = dict(formatted_citation="[Title not available]. Ruritania.",
                       formatting_source="stage_5d_llm", title="[Title not available]")
    c, d = _pub("Ruritania.", **placeholder), _pub("Ruritania.", **placeholder)
    decisions = []
    deduplicate_entries([a, b, c, d], decisions=decisions, code="S8")
    assert not [x for x in decisions if x["metric"].startswith("citation_")]


def test_identity_pass_is_bibliography_only():
    # The same PMID under a non-citation code is left to the similarity loop,
    # which keeps two dissimilar lines.
    a = _pub("Talk on gizmos at the Toy Society.", pmid="31234567")
    b = _pub("Completely different words entirely here.", pmid="31234567")
    assert deduplicate_entries([a, b], code="R") == [a, b]
    assert deduplicate_entries([a, b], code="S0") == [a, b]


def test_a_fanned_out_record_is_left_to_the_similarity_loop():
    a = _pub("Gizmo outcomes. J Imag Stud. 2019.", pmid="31234567", title="Gizmo outcomes")
    b = _pub("Another line, unlike the first one.", pmid="31234567", title="Gizmo outcomes")
    b[FANNED_OUT_FROM] = {'key': 'records', 'index': 1, 'count': 2}
    assert deduplicate_entries([a, b], code="S1") == [a, b]


def test_the_enriched_copy_is_kept_even_when_it_fills_fewer_fields():
    # Farm: BMAMWE. The unenriched copy fills more fields and has the longer
    # source line, but the PubMed-enriched copy is the one kept.
    plain = _pub("Quill A, Brandt B. Gizmo outcomes. J Imag Stud. 2019;4:1-9. Wrote the study.",
                 pmid="31234567", authors="Quill A, Brandt B", title="Gizmo outcomes",
                 journal="J Imag Stud", year=2019, volume="4", pages="1-9")
    enriched = _enriched("Gizmo outcomes.", "31234567", "Gizmo outcomes.")
    assert deduplicate_entries([plain, enriched], code="S1") == [enriched]


def test_one_year_in_two_forms_is_one_paper():
    # Farm: AKPQEB 214/247, RGUNJV 469/485. One copy states the year alone,
    # the other a full date; both name the same year.
    a = _pub("Quill A. Spinning tops at dusk. Toy Society Annual Meeting. Invited talk.",
             authors="Quill A", title="Spinning tops at dusk", journal="Toy Society Annual Meeting",
             year="2018")
    b = _pub("Gave it again on request for the regional chapter, same slides.",
             authors="Quill A", title="Spinning tops at dusk", journal="Toy Society Annual Meeting",
             year="2018-10")
    decisions = []
    assert len(deduplicate_entries([a, b], decisions=decisions, code="S3")) == 1
    assert decisions[0]["metric"] == "citation_title_journal_year"


def test_a_chapter_matches_on_its_book_title():
    # A chapter has no journal: its book title is the venue in the key.
    a = _pub("Quill A. Gears of the old toy shop. In: Handbook of Gizmos. Toy Press; 2015.",
             title="Gears of the old toy shop", book_title="Handbook of Gizmos", year=2015)
    b = _pub("Listed again under invited chapters, second printing noted.",
             title="Gears of the old toy shop", book_title="Handbook of Gizmos", year=2015)
    decisions = []
    assert len(deduplicate_entries([a, b], decisions=decisions, code="S5")) == 1
    assert decisions[0]["metric"] == "citation_title_journal_year"


@pytest.mark.parametrize("first, second", [
    # No year on either copy: title + journal alone is not an identity.
    ({"title": "Gadget signals in widget fatigue", "journal": "Toy Pain Society"},
     {"title": "Gadget signals in widget fatigue", "journal": "Toy Pain Society"}),
    # A year on one copy only.
    ({"title": "Gadget signals in widget fatigue", "journal": "Toy Pain Society", "year": 2013},
     {"title": "Gadget signals in widget fatigue", "journal": "Toy Pain Society"}),
    # A DOI value that is not DOI-shaped names no paper.
    ({"doi": "N/A", "year": 2020}, {"doi": "N/A", "year": 2020}),
    # A PMID shorter than an identifier names no paper.
    ({"pmid": "12", "year": 2020}, {"pmid": "12", "year": 2020}),
])
def test_no_identity_key_keeps_both(first, second):
    a = _pub("Talk on gizmos at the Toy Society.", **first)
    b = _pub("Completely different words entirely here.", **second)
    decisions = []
    assert deduplicate_entries([a, b], decisions=decisions, code="S8") == [a, b]
    assert not [x for x in decisions if x["metric"].startswith("citation_")]


def test_a_cv_title_typo_still_agrees_on_a_shared_pmid():
    # Ten significant words, one misspelled: Jaccard 9/11 = 0.82, over 0.8.
    a = _pub("Talk on gizmos at the Toy Society.", pmid="31234567",
             title="Gizmo outcomes across seven northern regional winter toy clinics measured yearly")
    b = _pub("Completely different words entirely here.", pmid="31234567",
             title="Gizmo outcomes across seven northern regional winter toy clinnics measured yearly")
    assert len(deduplicate_entries([a, b], code="S1")) == 1


def test_two_cv_chapter_titles_under_one_pubmed_record_keep_both():
    # Like AQCLHS-02, for chapters: the CV's own title is its chapter_title.
    first = _enriched("Quill A. Yields of a gadget club. 2020.", "32800001",
                      "Yields of a gadget club.", chapter_title="Yields of a gadget club", year=2020)
    second = _enriched("Quill A. Two routes into a sprocket club. 2020.", "32800001",
                       "Yields of a gadget club.", chapter_title="Two routes into a sprocket club",
                       year=2020)
    assert deduplicate_entries([first, second], code="S5") == [first, second]


def test_the_copy_filling_more_fields_is_kept_over_the_longer_line():
    # Neither copy is enriched: field count decides before source length.
    full = _pub("Quill A. Gizmo outcomes. J Imag Stud. 2019;4:1-9.", pmid="31234567",
                authors="Quill A", title="Gizmo outcomes", journal="J Imag Stud", year=2019,
                volume="4", pages="1-9")
    thin = _pub("Gizmo outcomes, a much longer line that names no venue or volume at all here.",
                pmid="31234567", title="Gizmo outcomes")
    assert deduplicate_entries([thin, full], code="S1") == [full]


def test_an_identity_drop_is_recorded_in_dropped_ids():
    # The caller's dropped_ids keeps a dropped copy from vouching for a later drop.
    a = _enriched("Quill A. Gizmo outcomes. J Imag Stud. 2018;3:5-6.", "30000001",
                  "Gizmo outcomes.", year=2018, journal="J Imag Stud")
    tail = _enriched("27. PubMed PMID: 30000001.", "30000001", "Gizmo outcomes.")
    dropped_ids = set()
    assert deduplicate_entries([a, tail], code="S1", dropped_ids=dropped_ids) == [a]
    assert dropped_ids == {id(tail)}


def test_a_superseded_in_press_copy_never_displaces_the_published_copy():
    # Farm: BMAMWE. Stage 5 marked the CV's "epub ahead of print" listing
    # in_press_superseded; stage 6 renders it only as a tracked deletion of
    # the published copy. The superseded copy is enriched and so would win
    # on fullness, but keeping it over the published copy loses the paper
    # once the deletion is accepted. Both reach the renderer.
    published = _pub("Quill A, Brandt B. Gizmo outcomes. J Imag Stud. 2019;4:1-9.",
                     pmid="31234567", authors="Quill A, Brandt B", title="Gizmo outcomes",
                     journal="J Imag Stud", year=2019, volume="4", pages="1-9")
    superseded = _enriched("Quill A, Brandt B. Gizmo outcomes. J Imag Stud. Epub ahead of print.",
                           "31234567", "Gizmo outcomes.", title="Gizmo outcomes", year=2019)
    superseded["in_press_superseded"] = True
    for order in ([published, superseded], [superseded, published]):
        kept = deduplicate_entries(list(order), code="S1")
        assert any(entry is published for entry in kept)


def test_a_superseded_copy_does_not_vouch_for_dropping_a_published_duplicate():
    # Two published listings plus a superseded one: the published pair still
    # dedups by PMID to one copy, and the superseded copy is not the keeper.
    first = _pub("Quill A, Brandt B. Gizmo outcomes. J Imag Stud. 2019;4:1-9. Wrote it.",
                 pmid="31234567", authors="Quill A, Brandt B", title="Gizmo outcomes",
                 journal="J Imag Stud", year=2019, volume="4", pages="1-9")
    second = _pub("Quill A, Brandt B. Gizmo outcomes. J Imag Stud. 2019.",
                  pmid="31234567", title="Gizmo outcomes", year=2019)
    superseded = _enriched("Gizmo outcomes. Epub ahead of print.", "31234567", "Gizmo outcomes.")
    superseded["in_press_superseded"] = True
    kept = deduplicate_entries([superseded, second, first], code="S1")
    assert any(entry is first for entry in kept)
    assert not any(entry is second for entry in kept)


def test_an_identity_drop_records_both_copies_name_fields():
    # The doctor's dedup_drops lint reads dropped_fields and kept_fields.
    a = _pub("Quill A. Gizmo outcomes. Toy Press. 2019.", pmid="31234567",
             title="Gizmo outcomes", journal_name="Toy Press", year=2019, authors="Quill A")
    b = _pub("Gizmo outcomes.", pmid="31234567", title="Gizmo outcomes")
    decisions = []
    assert deduplicate_entries([a, b], decisions=decisions, code="S3") == [a]
    assert decisions[0]["dropped_fields"] == {"title": "Gizmo outcomes"}
    assert decisions[0]["kept_fields"] == {"journal_name": "Toy Press", "title": "Gizmo outcomes"}


# ------------------- #1445: the earlier records of a parent dedup drops

def _split_q1(records, text, idx=70):
    """The children stage 6 fans a stage-4 records list out into (`fan_out`),
    the last of them carrying the parent's whole text."""
    from unified_pipeline.stage4.schemas import FIELD_SCHEMAS, STAGE4_RECORDS_KEY
    from unified_pipeline.stage6.fan_out import fan_out_multi_record_entries
    entry = {"taxonomy_code": "Q1", "element_idx_start": idx, "text": text,
             "extracted_fields": {**records[-1], STAGE4_RECORDS_KEY: records}}
    return fan_out_multi_record_entries([entry], FIELD_SCHEMAS, records_key=STAGE4_RECORDS_KEY)


def _q1(role, organization=None):
    return {"role": role, "organization": organization, "start_date": "2008", "end_date": "2012"}


_FULLER_Q1 = {"taxonomy_code": "Q1", "element_idx_start": 154,
              "text": "Zqboard Member, Ashby Pediatric Association 2008-2012\t"
                      "Elected by the membership\tServe as the Zqcomms Director of the association",
              "extracted_fields": _q1("Zqboard Member", "Ashby Pediatric Association")}


def test_a_dropped_parent_takes_the_earlier_records_the_kept_entry_holds():
    """EOAHMI GHCIXA 70: the parent was dropped as contained in a fuller entry,
    and its earlier record rendered on as an orphan copy of that entry's row."""
    children = _split_q1([_q1("Zqboard Member"), _q1("Zqcomms Director")],
                         "Zqboard Member, Zqcomms Director    2008-2012")
    decisions, dropped_ids = [], set()
    kept = deduplicate_entries([*children, _FULLER_Q1], decisions=decisions, code="Q1",
                               dropped_ids=dropped_ids)
    assert kept == [_FULLER_Q1]
    # A later group must not see the sibling vouch for another drop.
    assert dropped_ids == {id(children[0]), id(children[1])}
    assert [d["metric"].split("=")[0] for d in decisions] == [
        "containment", SPLIT_SIBLING_METRIC]


def test_an_earlier_record_the_kept_entry_does_not_hold_stays():
    # Its row would print an organization the kept entry never names.
    children = _split_q1([_q1("Zqboard Member", "Zqvarnor Society"), _q1("Zqcomms Director")],
                         "Zqboard Member, Zqcomms Director    2008-2012")
    kept = deduplicate_entries([*children, _FULLER_Q1], code="Q1")
    assert kept == [children[0], _FULLER_Q1]


def test_a_record_of_another_entry_is_not_a_sibling():
    other = _split_q1([_q1("Zqboard Member"), _q1("Zqtreasurer")],
                      "Zqboard Member; Zqtreasurer 2008-2012 Zqother", idx=12)
    children = _split_q1([_q1("Zqboard Member"), _q1("Zqcomms Director")],
                         "Zqboard Member, Zqcomms Director    2008-2012")
    kept = deduplicate_entries([other[0], *children, _FULLER_Q1], code="Q1")
    assert other[0] in kept and children[0] not in kept


@pytest.mark.parametrize("change", [
    {"fanned_out_from": {"key": "committees", "index": 0, "count": 2}},
    {"fanned_out_from": {"key": "stage4_records", "index": 0, "count": 3}},
    {"element_idx_start": 12},
], ids=["another-list", "another-count", "another-entry"])
def test_only_a_record_of_the_same_list_is_a_sibling(change):
    children = _split_q1([_q1("Zqboard Member"), _q1("Zqcomms Director")],
                         "Zqboard Member, Zqcomms Director    2008-2012")
    stranger = {**children[0], **change}
    kept = deduplicate_entries([stranger, children[1], _FULLER_Q1], code="Q1")
    assert kept == [stranger, _FULLER_Q1]


def test_a_sibling_the_parent_was_dropped_against_stays():
    # Should dedup ever keep an earlier record over its own parent, that
    # record is the keeper, not a copy to drop with the parent.
    children = _split_q1([_q1("Zqboard Member"), _q1("Zqcomms Director")],
                         "Zqboard Member, Zqcomms Director    2008-2012")
    drop_indices = {1}
    _drop_split_siblings(children, [(1, 0)], drop_indices, "Q1", None, set())
    assert drop_indices == {1}


# ---------------- #666 OWACZM 255: an undated split record absorbs a dated talk

_ZQ_VENUE = "Zqorbin Gizmo Guild (Quillford, ZZ)"
_ZQ_OTHER = "Zqmarrow Toy Works (Pellby, ZZ)"


def _split_r(text, records, idx=196):
    """The children stage 6 fans a two-line R entry's stage-4 records out into."""
    from unified_pipeline.stage4.schemas import FIELD_SCHEMAS, STAGE4_RECORDS_KEY
    from unified_pipeline.stage6.fan_out import fan_out_multi_record_entries
    entry = {"taxonomy_code": "R", "element_idx_start": idx, "text": text,
             "hierarchy": ["Talks"],
             "extracted_fields": {**records[-1], STAGE4_RECORDS_KEY: records}}
    return fan_out_multi_record_entries([entry], FIELD_SCHEMAS, records_key=STAGE4_RECORDS_KEY)


def _talk(location, date=None, idx=255):
    return {"taxonomy_code": "R", "element_idx_start": idx, "text": location,
            "hierarchy": ["Talks"], "extracted_fields": {"location": location, "date": date}}


def _r_records(date=None):
    return [{"location": _ZQ_VENUE, "date": None}, {"location": _ZQ_OTHER, "date": date}]


def _dedup_r(entries):
    return deduplicate_entries(entries, code="R", document=list(entries))


def test_an_undated_split_record_keeps_a_dated_talk_at_its_venue():
    """OWACZM 255: the 2019 talk was dropped as a copy of the undated first
    line of entry 196, a two-line entry that states no year anywhere."""
    children = _split_r(f"{_ZQ_VENUE}\t{_ZQ_OTHER}", _r_records())
    dated = _talk(_ZQ_VENUE, "2019")
    assert children[0][FANNED_OUT_FROM]
    assert _dedup_r([*children, dated]) == [*children, dated]
    assert _drop_is_safe(dated, children[0], "R", [*children, dated]) is False


def test_an_undated_talk_still_goes_against_an_undated_split_record():
    # KVWVHT 255: no date on either side, the verbatim copy goes as before.
    children = _split_r(f"{_ZQ_VENUE}\t{_ZQ_OTHER}", _r_records())
    assert _dedup_r([*children, _talk(_ZQ_VENUE)]) == children


@pytest.mark.parametrize("parent_text, records", [
    (f"{_ZQ_VENUE} 2019\t{_ZQ_OTHER}", _r_records()),
    (f"{_ZQ_VENUE}\t{_ZQ_OTHER} 2019", _r_records("2019")),
], ids=["year-in-the-entry-text", "year-on-a-sibling-record"])
def test_a_split_record_whose_entry_states_the_date_still_absorbs_it(parent_text, records):
    # The multi-record entry states 2019 somewhere, so it can be that talk.
    children = _split_r(parent_text, records)
    dated = _talk(_ZQ_VENUE, "2019")
    assert not _undated_split_record_absorbs_date(dated, children[0], [*children, dated])
    assert _drop_is_safe(dated, children[0], "R", [*children, dated]) is True


def test_another_year_in_the_entry_does_not_vouch_for_the_date():
    children = _split_r(f"{_ZQ_VENUE}\t{_ZQ_OTHER} 2021", _r_records("2021"))
    dated = _talk(_ZQ_VENUE, "2019")
    assert _undated_split_record_absorbs_date(dated, children[0], [*children, dated])
    assert _dedup_r([*children, dated]) == [*children, dated]


def test_the_same_line_twice_one_copy_undated_still_collapses():
    # Two whole entries, neither split out of a multi-record entry: the dated
    # copy goes as a copy of the undated one, as before. (The other way
    # round, the dated copy kept and the undated one dropped, was already
    # refused before this rule; that is not this shape.)
    undated = _talk(_ZQ_VENUE, idx=120)
    dated = _talk(_ZQ_VENUE, "2019")
    assert _dedup_r([undated, dated]) == [undated]
    assert _drop_is_safe(dated, undated, "R", [undated, dated]) is True
    assert not _undated_split_record_absorbs_date(dated, undated, [undated, dated])


def test_a_dated_split_record_is_left_to_the_date_checks():
    children = _split_r(f"{_ZQ_VENUE} 2018\t{_ZQ_OTHER}",
                        [{"location": _ZQ_VENUE, "date": "2018"}, {"location": _ZQ_OTHER}])
    dated = _talk(_ZQ_VENUE, "2019")
    assert children[0]["extracted_fields"]["date"] == "2018"
    assert not _undated_split_record_absorbs_date(dated, children[0], [*children, dated])


@pytest.mark.parametrize("date", [None, "", "n.d.", "  "])
def test_a_dropped_record_with_no_year_is_not_this_shape(date):
    children = _split_r(f"{_ZQ_VENUE}\t{_ZQ_OTHER}", _r_records())
    talk = _talk(_ZQ_VENUE, date)
    assert not _undated_split_record_absorbs_date(talk, children[0], [*children, talk])


def test_a_range_on_the_dropped_record_needs_an_overlapping_range():
    children = _split_r(f"{_ZQ_VENUE}\t{_ZQ_OTHER} 2014-2016", _r_records())
    talk = _talk(_ZQ_VENUE)
    talk["extracted_fields"] = {"location": _ZQ_VENUE, "start_date": "2015", "end_date": "2018"}
    assert not _undated_split_record_absorbs_date(talk, children[0], [*children, talk])
    talk["extracted_fields"].update(start_date="2017", end_date="2019")
    assert _undated_split_record_absorbs_date(talk, children[0], [*children, talk])


@pytest.mark.parametrize("change", [
    {"element_idx_start": 12},
    {"fanned_out_from": {"key": "roles", "index": 1, "count": 2}},
    {"fanned_out_from": {"key": "stage4_records", "index": 1, "count": 3}},
], ids=["another-entry", "another-list", "another-count"])
def test_only_the_same_multi_record_entry_vouches_for_the_date(change):
    children = _split_r(f"{_ZQ_VENUE}\t{_ZQ_OTHER}", _r_records())
    stranger = {**children[0], "text": f"{_ZQ_OTHER} 2019", **change}
    dated = _talk(_ZQ_VENUE, "2019")
    document = [*children, stranger, dated]
    assert stranger not in _split_record_family(children[0], document)
    assert _undated_split_record_absorbs_date(dated, children[0], document)


def test_the_family_is_the_records_of_one_entry():
    children = _split_r(f"{_ZQ_VENUE}\t{_ZQ_OTHER}", _r_records())
    dated = _talk(_ZQ_VENUE, "2019")
    family = _split_record_family(children[0], [*children, dated])
    assert family == children
    assert _split_record_family(children[0], None) == [children[0]]


@pytest.mark.parametrize("fields, expected", [
    ({"date": "2019"}, "2019"),
    ({"start_date": "2015", "end_date": "2018"}, "2015 - 2018"),
    ({"start_date": "2019", "end_date": "2019"}, "2019"),
    ({"start_date": " 2015 ", "end_date": " "}, "2015"),
    ({"start_date": "", "end_date": "2018"}, "2018"),
    ({}, ""),
])
def test_field_dates_text(fields, expected):
    assert _field_dates_text({"extracted_fields": fields}) == expected


def test_a_sibling_records_date_field_vouches_for_the_date():
    # A list fanned out of the fields (not stage 4's records): the sibling's
    # date sits in its fields, in no record's text.
    def record(index, text, **fields):
        return {"taxonomy_code": "R", "element_idx_start": 40, "text": text,
                "extracted_fields": fields,
                FANNED_OUT_FROM: {"key": "talks", "index": index, "count": 2}}
    kept = record(0, _ZQ_VENUE, location=_ZQ_VENUE)
    dated = _talk(_ZQ_VENUE, "2019")
    for sibling_date, absorbs in (("2019", False), ("2021", True)):
        sibling = record(1, _ZQ_OTHER, location=_ZQ_OTHER, date=sibling_date)
        assert _undated_split_record_absorbs_date(dated, kept, [kept, sibling, dated]) is absorbs


def test_a_whole_entry_at_the_same_source_position_is_not_a_record_of_it():
    # Another code's entry read off the same source line is no split record.
    children = _split_r(f"{_ZQ_VENUE}\t{_ZQ_OTHER}", _r_records())
    neighbour = {**_talk(f"{_ZQ_OTHER} 2019", idx=196), "taxonomy_code": "Q1"}
    dated = _talk(_ZQ_VENUE, "2019")
    document = [*children, neighbour, dated]
    assert _split_record_family(children[0], document) == children
    assert _undated_split_record_absorbs_date(dated, children[0], document)


# ------------- #666 residual: a lettered series, a slash listing, a narrower
# committee. Invented content shaped like IZJADE 942 (a lettered committee),
# JEFZWH 295, BLBVPD 94 and WYMVGU 557 (a committee the kept name qualifies).

def test_two_lettered_committees_of_one_council_are_both_kept():
    # IZJADE 942: undated, so the date test passes; one letter is no word.
    kept = {"text": "2029-2030 Member, Committee R, Council on Widget Standards and Gizmo Safety Testing"}
    dropped = {"text": "Member, Committee Q, Council on Widget Standards"}
    assert deduplicate_entries([kept, dropped]) == [kept, dropped]


# A semicolon listing: a comma one ("Committee R, Committee Q") reads as a
# byline and is blanked, so it letters no word and refuses nothing.
_LETTERED_LISTING = "2029-2030 Member, Committee R; Committee Q; Council on Widget Standards"


def test_a_listing_of_both_lettered_committees_still_covers_a_copy_of_one():
    kept = {"text": _LETTERED_LISTING}
    dropped = {"text": "Member, Committee Q, Council on Widget Standards"}
    assert deduplicate_entries([kept, dropped]) == [kept]


def test_a_listing_of_two_lettered_committees_does_not_cover_a_third():
    kept = {"text": _LETTERED_LISTING}
    dropped = {"text": "Member, Committee P, Council on Widget Standards"}
    assert deduplicate_entries([kept, dropped]) == [kept, dropped]


@pytest.mark.parametrize("kept_text", [
    "2029-2030 Member, Committee R and Committee Q, Council on Widget Standards",
    "2029-2030 Member, Committee R - Chair 2030; Committee Q, Council on Widget Standards",
])
def test_a_listing_joined_another_way_still_covers_a_copy_of_one(kept_text):
    # The "and" or the dash hides R from the letter reader; R still stands
    # on its own in the kept text, so the copy goes.
    kept = {"text": kept_text}
    dropped = {"text": "Member, Committee R, Council on Widget Standards"}
    assert deduplicate_entries([kept, dropped]) == [kept]


def test_a_kept_text_with_no_letter_on_the_word_proves_nothing():
    kept = {"text": "2029-2030 Member, Committee, Council on Widget Standards and Gizmo Safety Testing"}
    dropped = {"text": "Member, Committee Q, Council on Widget Standards"}
    assert deduplicate_entries([kept, dropped]) == [kept]


def test_a_citation_copy_whose_byline_the_blank_cannot_read_still_drops():
    # A stray comma ("Quill, MB") breaks the byline, so "Plover M" reads like
    # "Committee Q"; the kept copy letters no word, so the copy still goes.
    kept = {"text": "Plover M, Quill MB, Reed BP, Wren GR. Widget motion during gizmo "
                    "transfers. Widget Research Society Proceedings, 2031."}
    dropped = {"text": "Plover M, Quill, MB, Reed BP, Wren GR. Widget motion during gizmo "
                       "transfers. Widget Research Society, 2031."}
    assert deduplicate_entries([kept, dropped]) == [kept]


def test_a_slash_listed_course_is_not_covered_by_one_of_its_parts():
    # The longer text is kept, so the listing is the one dedup weighs dropping.
    kept = {"text": "Section Lead: Widget Pharmacology II, Example School of Gizmos, 2031-2033"}
    listing = {"text": "Section Lead: Widget Pharmacology I/II, Example School, 2031-2033"}
    assert deduplicate_entries([kept, listing]) == [kept, listing]


def test_a_slash_listed_course_covers_a_copy_of_one_part():
    listing = {"text": "Section Lead: Widget Pharmacology I/II, Example School, 2031-2033"}
    dropped = {"text": "Section Lead: Widget Pharmacology I, Example School, 2031-2033"}
    assert deduplicate_entries([listing, dropped]) == [listing]


@pytest.mark.parametrize("text, numerals, letters", [
    ("Member, Committee Q, Council on Widgets", set(), {("committee", "Q")}),
    ("Widget Group B", set(), {("group", "B")}),
    ("Widget Group B; Gadget Unit C)", set(), {("group", "B"), ("unit", "C")}),
    ("Widget Group B\tGadgets", set(), {("group", "B")}),          # a tab ends a segment
    ("Widget Group B: Gadgets", set(), {("group", "B")}),
    ("Widget Group B - Gadgets", set(), {("group", "B")}),        # a spaced hyphen
    ("Widget Group B -Gadgets", set(), {("group", "B")}),         # a hyphen after a space
    ("Widget Group B–Gadgets", set(), {("group", "B")}),        # a dash
    ("Widget Memory B-cell study", set(), set()),                  # a hyphen joins a word
    ("Widget Group B and Gadget Group C", set(), {("group", "C")}),  # "and" is no segment end
    ("Chair, A Widget Committee", set(), set()),                   # the article: a word follows
    ("Widget Group B Committee", set(), set()),                    # a word follows
    ("Widget Group B. Gadgets", set(), set()),                     # an initial's period
    ("Widget Compounding I, 2031", {"I"}, set()),                  # "I" is a numeral only
    ("Plover M, Quill MB. Widget study.", set(), set()),           # a byline is blanked
    ("Plover M, Quill, MB. Widget study.", set(), {("plover", "M")}),
    ("Widget Part B, 2031", set(), {("part", "B")}),               # no part letter reader
    ("Widget Part II, 2031", set(), set()),                        # a part numeral is `_part_numbers`'
    ("WIDGET Group b,", set(), set()),                             # lower case is no mark
    ("member of the widget group B,", set(), set()),               # nor after a lower-case word
    ("Widget Seminar II, Section A", {"II"}, {("section", "A")}),
])
def test_series_marks(text, numerals, letters):
    marks = _series_marks(text)
    assert (marks.numerals, marks.letters) == (numerals, letters)


@pytest.mark.parametrize("dropped, kept, other", [
    ("Widget Compounding I, 2031", "Widget Compounding II, 2031", True),
    ("Widget Compounding I, 2031", "Widget Compounding, 2031", True),     # a numeral the kept text lacks
    ("Widget Compounding II, 2031", "Widget Compounding II, Acme, 2031", False),
    ("Committee Q, Council", "Committee R, Council", True),
    ("Committee Q, Council", "Committee R; Committee Q; Council", False),  # the kept text holds it
    ("Committee P, Council", "Committee R; Committee Q; Council", True),
    ("Committee Q, Council", "Committee R, Quality Council", True),       # a word's capital is no letter
    ("Committee P, Council", "Committee R, Committee Q; Council", False),  # a comma listing is blanked
    ("Committee R, Council", "Committee R and Committee Q, Council", False),  # R stands on its own
    ("Hepatitis B, 2031", "Hepatitis B and Hepatitis C, 2031", False),
    ("Hepatitis C, 2031", "Hepatitis B and C, 2031", False),
    ("Committee Q - Council", "Committee R - Council", True),            # a dash ends the segment
    ("Committee Q, Council", "Committee, Council", False),                # no letter on that word
    ("Committee Q, Council", "Gadget Group R, Council", False),           # another word lettered
    ("Committee, Council", "Committee R, Council", False),                # nothing to compare
    ("Widget Pharmacology I/II, 2031", "Widget Pharmacology II, 2031", True),
    ("Widget Pharmacology I, 2031", "Widget Pharmacology I/II, 2031", False),
    ("Widget Pharmacology I/II, 2031", "Widget Pharmacology I and II, 2031", False),
    ("Widget Pharmacology I/II, 2031", "Widget Pharmacology I & II, 2031", False),
    ("Widget Pharmacology I and II, 2031", "Widget Pharmacology II, 2031", True),
])
def test_other_series_member(dropped, kept, other):
    assert _other_series_member(dropped, kept) is other


def _committee(text: str, name, **fields) -> dict:
    return _entry("P", text, committee_name=name, **fields)


def test_a_committee_the_kept_name_places_in_a_department_is_kept():
    # JEFZWH 295: the dropped committee sits in a college, the kept one is the
    # department's; every word of the dropped row is in the kept row.
    kept = _committee("Member, Steering Committee of the Department of Sprockets, "
                      "Acme College of Gizmos, 2030-present",
                      "Steering Committee of the Department of Sprockets", role="Member",
                      start_date="2030", end_date="present")
    dropped = _committee("Member, Steering Committee, Acme College of Gizmos, 2030-present",
                         "Steering Committee", role="Member", start_date="2030", end_date="present")
    assert _kept_both("P", dropped, kept)


def test_a_committee_whose_name_the_kept_name_inserts_a_word_into_is_kept():
    # BLBVPD 94: a word inside the name, not after it.
    kept = _committee("Oct 2029 - Present Sprocket Leads Planning Committee, Gizmo Unit - Acme Hospital",
                      "Sprocket Leads Planning Committee", institution="Gizmo Unit - Acme Hospital",
                      start_date="2029-10", end_date="present")
    dropped = _committee("Oct 2029 - Present Sprocket Leads Committee, Acme Hospital",
                         "Sprocket Leads Committee", institution="Acme Hospital",
                         start_date="2029-10", end_date="present")
    assert _kept_both("P", dropped, kept)


def test_a_committee_row_that_states_the_qualifier_is_still_dropped():
    kept = _committee("Member, Steering Committee of the Department of Sprockets, "
                      "Acme College of Gizmos, 2030-present",
                      "Steering Committee of the Department of Sprockets", role="Member")
    dropped = _committee("Member, Steering Committee, Department of Sprockets, "
                         "Acme College of Gizmos, 2030-present", "Steering Committee", role="Member")
    assert not _kept_both("P", dropped, kept)


def test_a_committee_acronym_in_parentheses_is_not_a_qualifier():
    kept = _committee("Member, Widget Program (WP) Executive Committee, 2028-2031",
                      "Widget Program (WP) Executive Committee", role="Member",
                      start_date="2028", end_date="2031")
    dropped = _committee("2028-2031 Member, Widget Program Executive Committee",
                         "Widget Program Executive Committee", role="Member",
                         start_date="2028", end_date="2031")
    assert not _kept_both("P", dropped, kept)


def test_a_plural_in_the_kept_committee_name_is_not_a_qualifier():
    kept = _committee("2030- Gizmo Access Committees: Widget Workforce Committee, Acme Health",
                      "Gizmo Access Committees: Widget Workforce Committee",
                      start_date="2030", end_date="present")
    dropped = _committee("2030- Gizmo Access - Widget Workforce Committee, Acme Health",
                         "Gizmo Access - Widget Workforce Committee",
                         start_date="2030", end_date="present")
    assert not _kept_both("P", dropped, kept)


_NARROWER_KEPT = {"text": "Member, Gizmo Review Committee for Widgets, 2030",
                  "extracted_fields": {"committee_name": "Gizmo Review Committee for Widgets"}}


@pytest.mark.parametrize("code, dropped_text, name, narrower", [
    ("P", "Member, Gizmo Review Committee, 2030", "Gizmo Review Committee", True),
    ("Q2", "Member, Gizmo Review Committee, 2030", "Gizmo Review Committee", True),
    ("P", "Member, Gizmo Review (GR) Committee, 2030", "Gizmo Review (GR) Committee", True),
    ("P", "Member, Gizmo Review Committee, Widgets, 2030", "Gizmo Review Committee", False),
    # stage 4 spelled the name out: its words count as stated
    ("P", "Member, Gizmo Review Cttee, Widgets, 2030", "Gizmo Review Committee", False),
    ("R", "Member, Gizmo Review Committee, 2030", "Gizmo Review Committee", False),   # no committee name rendered
    (None, "Member, Gizmo Review Committee, 2030", "Gizmo Review Committee", False),
    ("P", "Member, Sprocket Board, 2030", "Sprocket Board", False),                    # not in the kept name
    ("P", "Member, Committee, 2030", "Committee", False),                              # one word: a fragment
    ("P", "Member, Committee Q, 2030", "Committee Q", False),                          # a letter is no word
    ("P", "Member, --, 2030", "--", False),                                            # a name with no words
    ("P", "Member, Gizmo Review Committee, 2030", ["Gizmo Review Committee"], False),  # not a string
    ("P", "Member, Gizmo Review Committee for Widgets", "Gizmo Review Committee for Widgets", False),
    ("P", "Member, Committee for Widgets, 2030", "Committee for Widgets", False),      # added only in front
])
def test_narrower_committee(code, dropped_text, name, narrower):
    dropped = {"text": dropped_text, "extracted_fields": {"committee_name": name}}
    assert _narrower_committee(code, dropped, _NARROWER_KEPT) == (name if narrower else None)


def test_a_kept_committee_name_that_is_not_a_string_is_no_narrower_body():
    dropped = {"text": "Member, Gizmo Review Committee, 2030",
               "extracted_fields": {"committee_name": "Gizmo Review Committee"}}
    kept = {"text": _NARROWER_KEPT["text"],
            "extracted_fields": {"committee_name": ["Gizmo Review Committee for Widgets"]}}
    assert _narrower_committee("P", dropped, kept) is None


@pytest.mark.parametrize("kept_name, narrower", [
    # a segment after the whole dropped name is where the committee sits, or
    # its short name: stage 4 folds a P row's institution in after a comma
    ("Gizmo Review Committee, Acme University", False),
    ("Gizmo Review Committee; Acme University", False),
    ("Gizmo Review Committee - GRC", False),
    ("Gizmo Review Committee — GRC", False),
    # a qualifier inside the segment that completes the dropped name counts
    ("Gizmo Review Planning Committee, Acme University", True),
    ("Gizmo Review Committee of Widgets, Acme University", True),
    ("Gizmo Review Committee-Widgets", True),                   # a hyphen joins, it does not separate
    ("Gizmo, Review Committee for Widgets", True),               # completed in the last segment
    ("Gizmo Review, Acme University", False),                    # never holds the dropped name
])
def test_narrower_committee_reads_the_kept_name_up_to_the_dropped_one(kept_name, narrower):
    name = "Gizmo Review Committee"
    dropped = {"text": "Member, Gizmo Review Committee, 2030", "extracted_fields": {"committee_name": name}}
    kept = {"text": f"Member, {kept_name}, 2030", "extracted_fields": {"committee_name": kept_name}}
    assert _narrower_committee("P", dropped, kept) == (name if narrower else None)


@pytest.mark.parametrize("code, kept_name, fields, narrower", [
    ("Q2", "Gizmo Review Committee of the Widget Society", {"organization": "Widget Society"}, False),
    ("Q2", "Gizmo Review Committee of the Widget Society", {}, True),
    ("Q2", "Gizmo Review Committee of the Widget Society", {"notes": "Widget Society"}, True),
    ("P", "Gizmo Review Committee of the Widget Society", {"institution": "Widget Society"}, False),
    ("P", "Gizmo Review Committee of the Widget Society", {"organization": "Widget Society"}, True),
    ("P", "Gizmo Review Committee of the Widget Society",
     {"formatted_text": "Gizmo Review Committee, Widget Society"}, False),      # stage 5's rendering
    ("P", "Gizmo Review Committee Member", {"role": "Member"}, False),
    ("P", "Gizmo Review Committee Member", {}, True),
])
def test_a_qualifier_the_dropped_row_renders_in_a_field_is_stated(code, kept_name, fields, narrower):
    # Only the fields the code's section renders count: P writes no organization.
    dropped = {"text": "Gizmo Review Committee, 2030",
               "extracted_fields": {"committee_name": "Gizmo Review Committee", **fields}}
    kept = {"text": f"{kept_name}, 2030", "extracted_fields": {"committee_name": kept_name}}
    assert (_narrower_committee(code, dropped, kept) is not None) is narrower


def test_a_committee_whose_kept_name_adds_its_institution_after_a_comma_still_drops():
    # The P row of the review: stage 4 folded the institution into the kept
    # name. Both rows render the same committee.
    kept = _committee("Member, Healthy Widget Executive Committee, Acme University, 2030-2032",
                      "Healthy Widget Executive Committee, Acme University", role="Member",
                      start_date="2030", end_date="2032")
    dropped = _committee("Member, Healthy Widget Executive Committee, 2030-2032",
                         "Healthy Widget Executive Committee", role="Member",
                         start_date="2030", end_date="2032")
    assert not _kept_both("P", dropped, kept)


def test_a_committee_whose_organization_column_states_the_kept_qualifier_still_drops():
    kept = _entry("Q2", "Member, Scientific Widget Committee of the Gizmo Society, 2030-2032",
                  committee_name="Scientific Widget Committee of the Gizmo Society", role="Member",
                  start_date="2030", end_date="2032")
    dropped = _entry("Q2", "Member, Scientific Widget Committee, 2030-2032",
                     committee_name="Scientific Widget Committee", organization="Gizmo Society",
                     role="Member", start_date="2030", end_date="2032")
    assert not _kept_both("Q2", dropped, kept)


# ------------ #666 residual (comment 6318606534): distinct records still dropped

# LFFKVV 1434: an editorial board's row for the shorter of two journal names,
# vouched for by a bare listing of that name under another code.

def _board_row(name: str) -> dict:
    return _entry("Q4B", f"{name}\tReview Editor\t2030-Present", journal_name=name,
                  role="Review Editor", start_date="2030", end_date="present")


def test_a_bare_listing_under_another_code_does_not_vouch_for_a_row_with_a_role():
    kept, dropped = _board_row("Widget Applied Gizmology"), _board_row("Widget Gizmology")
    reviewer_listing = _entry("Q4D", "Widget Gizmology", journal_name="Widget Gizmology")
    assert _kept_both("Q4B", dropped, kept, reviewer_listing)
    # The near miss: the same bare listing in the row's own code still vouches,
    # and so does a row of another code that says the same about the journal.
    board_listing = _entry("Q4B", "Widget Gizmology", journal_name="Widget Gizmology")
    assert not _kept_both("Q4B", dropped, kept, board_listing)
    same_row = dict(_board_row("Widget Gizmology"), taxonomy_code="Q4C")
    assert not _kept_both("Q4B", dropped, kept, same_row)


@pytest.mark.parametrize("text, same_code, same", [
    ("Widgets", True, True),                          # a bare listing in the row's own code
    ("Widgets", False, False),                        # a bare listing under another code
    ("Widgets\tReview Editor\t2019-present", False, True),  # another code, the same row
    ("Widgets\tEditorial Board\t2019", True, False),  # another role
    ("Gizmos", True, False),                          # does not hold the name
])
def test_same_row(text, same_code, same):
    assert _same_row(text, "Widgets\tReview Editor\t2030-present", "Widgets", same_code) is same


# ZASOTV 223/236/241, LFFKVV 1572: a journal listed by its bare name, dropped
# against a line whose fields name no journal or another one, then vouched for
# by a longer journal name holding its word.

def _lead_in() -> dict:
    return _entry("Q4D", "Reviewer for Widgets journals:", journal_name=None)


def _book_review() -> dict:
    return _entry("Q4D", "“Gizmo approach to widgets” by A. Writer. Example Press.\t"
                         "Book Reviewer\t2030",
                  journal_name="Studies in Gizmo Craft - Book Reviewer", year="2030")


@pytest.mark.parametrize("make_kept", [_lead_in, _book_review], ids=["lead-in", "book-review"])
def test_a_bare_journal_name_is_not_carried_by_a_longer_name_holding_its_word(make_kept):
    kept, dropped = make_kept(), _journal("Widgets")
    longer = _journal("Annals of Widgets")
    assert _kept_both("Q4D", dropped, kept, longer)
    # The near miss: a listing of exactly that name still carries it.
    assert not _kept_both("Q4D", dropped, kept, longer, _journal("Widgets"))


def test_a_bare_journal_name_opening_a_reviewed_books_title_is_not_carried_by_it():
    # LFFKVV 1487 (review round): the book's title, split at its colon, was
    # read as a listing of the bare journal name.
    dropped, kept = _journal("Widgets"), _journal("Widgets and Widgetry")
    book = _entry("Q4D", "“Widgets: Gizmos, Sprockets and Cogs” A. Writer. Example Press.\t"
                         "Book Proposal Reviewer\t2030", journal_name=None, year="2030")
    assert _kept_both("Q4D", dropped, kept, book)
    # The near miss: a list that names the journal still carries it.
    assert not _kept_both("Q4D", dropped, kept, book, _entry("Q4D", "Gizmos, Widgets"))


@pytest.mark.parametrize("dropped_text, other_text, carried", [
    ("Widgets", "Annals of Widgets", False),           # a bare name: only a listing carries it
    ("Widgets", "Gizmos, Widgets", True),              # its own list names it
    ("Widgets, Reviewer", "Annals of Widgets", True),  # not bare: named is carried, as before
])
def test_record_carried(dropped_text, other_text, carried):
    other = _entry("Q4D", other_text)
    assert _record_carried(other, "Widgets", "journal_name", "Q4D", dropped_text) is carried


# LFFKVV 112: two societies over the same years, the shorter name's words all
# inside the longer one's, out of order.

def _membership(name: str) -> dict:
    return _entry("I", f"{name}\t2030-Present", organization=name,
                  start_date="2030", end_date="present")


def test_a_membership_of_another_society_is_kept():
    kept = _membership("International Society for Applied Widget Gizmos")
    dropped = _membership("Widget Society on Gizmos")
    assert _kept_both("I", dropped, kept)
    assert _different_institution_for("I", dropped, kept) == "Widget Society on Gizmos"
    # The near miss: in order, the shorter name is the kept one cut short.
    kept = _membership("Widget Society on Gizmos of North Example")
    assert _different_institution_for("I", dropped, kept) is None
    assert not _kept_both("I", dropped, kept)
    assert not _kept_both("I", _membership("Widget Society"), _membership("Widget Society"))


@pytest.mark.parametrize("code, make, name, kept_name", [
    # an acronym moved, and two segments swapped: one society restated
    ("I", _membership, "ASWG - American Society of Widget Gizmos",
     "American Society of Widget Gizmos (ASWG)"),
    ("I", _membership, "American Gizmo Association, Council on Widgets",
     "Council on Widgets, American Gizmo Association"),
    # one award restated with its words reordered
    ("H", lambda name: _entry("H", f"2030 {name}, Acme Cog College", award_name=name,
                              granting_body="Acme Cog College", date="2030"),
     "Gizmo Teaching Award (Dean's)", "Dean's Gizmo Teaching Award"),
])
def test_a_name_restated_with_its_words_reordered_prints_once(code, make, name, kept_name):
    first, second = make(kept_name), make(name)
    document = [first, second]
    assert len(deduplicate_entries([first, second], code=code, document=document)) == 1


def test_an_institution_with_its_words_reordered_is_still_another_one():
    # The near miss: a hospital's word order is part of its name.
    dropped = {"institution": "University of Acme Medical Center"}
    kept = {"institution": "Acme University Medical Center"}
    assert _different_institution("D2", dropped, kept) == "University of Acme Medical Center"
    assert _different_institution("I", {"organization": "University of Acme Medical Center"},
                                  {"organization": "Acme University Medical Center"}) is None


# LTTYWI 175, 179: an undated committee row under one year head beside a row
# of the same committee under another.

def _list_row(index: int, text: str, code: str = "P", **fields) -> dict:
    return {"taxonomy_code": code, "element_idx_start": index, "element_idx_end": index,
            "text": text, "extracted_fields": fields, "hierarchy": ["Administrative Duties"]}


def _committee_rows(dropped_index: int = 11) -> tuple[dict, dict, list[dict]]:
    head = _list_row(10, "2030-2035 Member, Gadget Committee", committee_name="Gadget Committee",
                     role="Member", start_date="2030", end_date="2035")
    dropped = _list_row(dropped_index, "Member, Widget Committee", committee_name="Widget Committee",
                        role="Member")
    between = _list_row(13, "2033-2034 Chair, Sprocket Committee", committee_name="Sprocket Committee",
                        role="Chair", start_date="2033", end_date="2034")
    kept = _list_row(14, "2036-2037 Member, Widget Committee", committee_name="Widget Committee",
                     role="Member", start_date="2036", end_date="2037")
    return dropped, kept, [head, between]


def test_an_undated_row_under_another_year_head_is_another_term():
    dropped, kept, others = _committee_rows()
    assert _kept_both("P", dropped, kept, *others)
    assert _under_another_year_head(dropped, kept, "P", [dropped, kept, *others])
    # Through an undated sibling line of the same group, too.
    sibling = _list_row(11, "Chair, Gadget Committee", code="Q1", role="Chair")
    dropped, kept, others = _committee_rows(dropped_index=12)
    assert _kept_both("P", dropped, kept, sibling, *others)


def test_an_undated_row_after_a_blank_line_is_no_row_of_the_group_above():
    # The near miss: a blank line (element 11) ends the 2030-2035 group, so
    # the undated line heads the rows below it and is the kept row's copy.
    dropped, kept, others = _committee_rows(dropped_index=12)
    assert not _under_another_year_head(dropped, kept, "P", [dropped, kept, *others])
    assert not _kept_both("P", dropped, kept, *others)


def test_an_undated_row_under_the_kept_row_itself_still_drops():
    # The near miss: the undated line is a sub-line of the kept row, so it
    # repeats that row.
    kept = _list_row(10, "2036-2037 Member, Widget Committee", committee_name="Widget Committee",
                     role="Member", start_date="2036", end_date="2037")
    dropped = _list_row(11, "Member, Widget Committee", committee_name="Widget Committee", role="Member")
    assert not _under_another_year_head(dropped, kept, "P", [dropped, kept])
    assert not _kept_both("P", dropped, kept)


@pytest.mark.parametrize("code, fields", [
    ("P", {}),      # a fragment names no record
    ("T", {}),      # a text-rendered code: a T fold
    ("P", {"committee_name": "Widget Committee", "start_date": "2036"}),  # dated by a field
])
def test_an_undated_row_that_names_no_record_or_is_dated_is_left_to_the_other_checks(code, fields):
    dropped, kept, others = _committee_rows()
    dropped = dict(dropped, taxonomy_code=code, extracted_fields=fields)
    kept = dict(kept, taxonomy_code=code)
    assert not _under_another_year_head(dropped, kept, code, [dropped, kept, *others])


def test_a_year_head_spanning_two_lines_heads_the_row_after_its_last_line():
    dropped, kept, (head, between) = _committee_rows(dropped_index=12)
    head = dict(head, element_idx_end=11)
    assert _kept_both("P", dropped, kept, head, between)


def _with(row: dict, **changes) -> dict:
    return {**row, **changes}


@pytest.mark.parametrize("change", [
    # the row above states its year after its name: it heads no group
    lambda dropped, kept, head: (dropped, kept, _with(head, text="Member, Gadget Committee, 2030-2035")),
    # the year-led row above sits in another source section
    lambda dropped, kept, head: (dropped, kept, _with(head, hierarchy=["Other Duties"])),
    # the kept row sits in another source section
    lambda dropped, kept, head: (dropped, _with(kept, hierarchy=["Other Duties"]), head),
    # the kept row's year does not lead, so it has no year head
    lambda dropped, kept, head: (dropped, _with(kept, text="Member, Widget Committee, 2036-2037"), head),
    # no row names its source section
    lambda dropped, kept, head: (_with(dropped, hierarchy=None), _with(kept, hierarchy=None),
                                 _with(head, hierarchy=None)),
])
def test_rows_without_two_year_heads_in_one_section_are_left_to_the_other_checks(change):
    dropped, kept, (head, between) = _committee_rows()
    dropped, kept, head = change(dropped, kept, head)
    assert not _under_another_year_head(dropped, kept, "P", [dropped, kept, head, between])
    assert not _kept_both("P", dropped, kept, head, between)


def test_a_dropped_row_stating_its_own_year_is_left_to_the_date_checks():
    dropped, kept, others = _committee_rows()
    dropped = _with(dropped, text="2036 Member, Widget Committee")
    assert not _under_another_year_head(dropped, kept, "P", [dropped, kept, *others])


@pytest.mark.parametrize("text, heads", [
    ("2036-2037 Member, Widget Committee", True),
    ("• 2036 Member, Widget Committee", True),
    ("Member, Widget Committee, 2036-2037", False),  # its year does not lead
])
def test_year_head_of_a_dated_row_is_the_row_when_its_year_leads(text, heads):
    row = _list_row(14, text, committee_name="Widget Committee")
    assert (_year_head(row, [row]) is row) is heads


# BLBVPD 213, IXJMKS 866: an ad hoc review beside a standing seat on the panel.

def _review(text: str, hierarchy: str = "Review Panels", **fields) -> dict:
    return dict(_entry("Q3", text, **fields), hierarchy=[hierarchy])


@pytest.mark.parametrize("agency", ["Widget Agency", None])
def test_a_standing_seat_beside_an_ad_hoc_review_of_the_panel_is_kept(agency):
    # A row naming the panel and no agency is a review record too.
    kept = _review("2030 Ad hoc Reviewer, Widget Agency Gadget Review Committee, Special Panel",
                   role="Ad hoc Reviewer", agency=agency,
                   panel_name="Gadget Review Committee, Special Panel", start_date="2030")
    dropped = _review("2030 Widget Agency Gadget Review Committee", agency=agency,
                      panel_name="Gadget Review Committee", start_date="2030")
    assert _kept_both("Q3", dropped, kept)
    assert _ad_hoc_apart(dropped, kept, "Q3")


def test_an_agency_under_an_ad_hoc_heading_beside_its_study_section_row_is_kept():
    kept = _review("Widget Agency Gadget Panel, June 2030", "Standing Widget Panels",
                   agency="Widget Agency", role="Gadget Panel Member",
                   panel_name="Widget Agency Gadget Panel", start_date="2030-06")
    dropped = _review("Widget Agency", "Ad Hoc Widget Reviews", agency="Widget Agency")
    assert _kept_both("Q3", dropped, kept)


@pytest.mark.parametrize("dropped, kept", [
    # both ad hoc: by the heading, or by the role
    (_review("Widget Agency", "Ad Hoc Widget Reviews", agency="Widget Agency"),
     _review("Widget Agency Gadget Panel, June 2030", "Ad Hoc Widget Reviews",
             agency="Widget Agency", panel_name="Widget Agency Gadget Panel")),
    (_review("2030 Ad-hoc reviewer, Widget Agency Gadget Panel", role="Ad-hoc reviewer",
             agency="Widget Agency"),
     _review("2030 Ad hoc Reviewer, Widget Agency Gadget Panel, Special Session",
             role="Ad hoc Reviewer", agency="Widget Agency")),
    # neither ad hoc
    (_review("Widget Agency", agency="Widget Agency"),
     _review("Widget Agency Gadget Panel, June 2030", agency="Widget Agency")),
])
def test_two_review_rows_of_one_kind_still_drop(dropped, kept):
    assert not _ad_hoc_apart(dropped, kept, "Q3")
    assert not _kept_both("Q3", dropped, kept)


def test_an_ad_hoc_role_line_with_no_agency_or_panel_or_another_code_is_no_review_record():
    kept = _review("Widget Agency Gadget Panel, June 2030", agency="Widget Agency")
    assert not _ad_hoc_apart(_review("Ad hoc reviewer", role="Ad hoc reviewer"), kept, "Q3")
    ad_hoc = dict(_review("Widget Agency", "Ad hoc Reviewer", agency="Widget Agency"),
                  taxonomy_code="Q2")
    assert not _ad_hoc_apart(ad_hoc, dict(kept, taxonomy_code="Q2"), "Q2")


@pytest.mark.parametrize("entry, ad_hoc", [
    ({"text": "2030 Ad hoc member, Gadget Panel"}, True),
    ({"text": "2030 Gadget Panel", "extracted_fields": {"role": "ad-hoc reviewer"}}, True),
    ({"text": "2030 Gadget Panel", "hierarchy": ["SERVICE", "Ad Hoc Reviews"]}, True),
    ({"text": "2030 Gadget Panel", "extracted_fields": {"role": None}, "hierarchy": [None]}, False),
    ({"text": "2030 Ad Hockey League Panel"}, False),  # "hoc" opening a longer word
])
def test_is_ad_hoc(entry, ad_hoc):
    assert _is_ad_hoc(entry) is ad_hoc


# LTTYWI 297, 307, 311, 351: a teaching award a class votes, beside the award
# of another class or of another year whose class year is the dropped year.

@pytest.mark.parametrize("dropped_text, kept_text", [
    # another class, the same year (311, 351)
    ("Teacher of the Year - (Class of 2031) - Widget College 2031",
     "Teacher of the Year - (Class of 2033) - Widget College 2031"),
    # the same class, another year: the class year vouched for the award year (297)
    ("Teacher of the Year - (Class of 2031) - Widget College 2031",
     "Teacher of the Year - (Class of 2031) - Widget College 2029"),
    # no class named, another year (307)
    ("Teacher of the Year - Widget College, Acme University 2031",
     "Teacher of the Year - (Class of 2031) - Widget College 2029, Acme University"),
])
def test_an_award_of_another_class_or_year_is_kept(dropped_text, kept_text):
    # No fields, so no name check can tell the two apart: only the class year can.
    dropped, kept = _entry("H", dropped_text), _entry("H", kept_text)
    assert _kept_both("H", dropped, kept)
    assert _class_year_apart(dropped_text, kept_text, "H")


@pytest.mark.parametrize("code, dropped_text, kept_text", [
    # the same class and year: a copy
    ("H", "Teacher of the Year - (Class of 2031) - Widget College 2031",
     "Teacher of the Year - (class of 2031) - Widget College, Acme University 2031"),
    # no class named, the same year: nothing tells them apart (262)
    ("H", "Teacher of the Year - Widget College, Acme University 2031",
     "Teacher of the Year - (Class of 2033) - Widget College 2031, Acme University"),
    # a degree's class year is its date
    ("B1", "Doctor of Widgetry, Acme University School of Gizmos, 2031",
     "Doctor of Widgetry, Acme University School of Gizmos, Class of 2031, Dean's List"),
    # the same class and year, the class not the award year: its label is cut from both
    ("H", "Teacher of the Year - (Class of 2031) - Widget College 2029",
     "Teacher of the Year - (Class of 2031) - Acme University Widget College 2029"),
    # the class year is the kept text's one year, so it dates the honor (review round)
    ("H", "Gizmo Honor Society, Acme Widget University, 2030",
     "Gizmo Honor Society, Acme Widget University, Class of 2030"),
    ("H", "Summa Cum Laude, Acme Widget University 2030",
     "Summa Cum Laude, Acme Widget University, Class of 2030"),
])
def test_an_award_of_the_same_class_and_year_or_another_code_still_drops(code, dropped_text, kept_text):
    assert not _class_year_apart(dropped_text, kept_text, code)
    assert not _kept_both(code, _entry(code, dropped_text), _entry(code, kept_text))


# CYOFWJ 40: two awards of one year and one university, their names apart.

def _award_row(name: str, year: str = "1990") -> dict:
    return _entry("H", f"{year} {name}, Acme Gizmo University", award_name=name,
                  date=year, granting_body="Acme Gizmo University")


def _other_award_name_for(code, dropped, kept):
    return _other_award_name(code, dropped["extracted_fields"], kept["extracted_fields"])


def test_an_award_of_another_name_at_the_same_university_is_kept():
    dropped, kept = _award_row("Gizmo Scholar"), _award_row("Jane Roe Scholar")
    assert _kept_both("H", dropped, kept)
    assert _other_award_name_for("H", dropped, kept)
    # The near miss: another listing of the award on its year carries it.
    assert not _kept_both("H", dropped, kept, _award_row("Gizmo Scholar"))


@pytest.mark.parametrize("code, name, kept_name", [
    ("H", "Gizmo Scholar", "Gizmo Scholar Award"),   # reworded, in order
    ("H", "Gizmo Scholar Award", "Gizmo Scholar"),
    ("H", "Gizmo Scholar", "Gizmo Scholar"),
    ("H", "--", "Jane Roe Scholar"),                 # a name with no words
    ("R", "Gizmo Scholar", "Jane Roe Scholar"),      # not an award code
    ("H", "Gizmo Teaching Award (Dean's)", "Dean's Gizmo Teaching Award"),  # its words reordered
])
def test_an_award_whose_name_the_kept_one_holds_in_order_still_drops(code, name, kept_name):
    dropped, kept = _award_row(name), _award_row(kept_name)
    dropped["taxonomy_code"] = kept["taxonomy_code"] = code
    assert not _other_award_name_for(code, dropped, kept)


def test_an_award_name_apart_needs_both_names():
    kept = _award_row("Jane Roe Scholar")
    assert not _other_award_name("H", {"award_name": None}, kept["extracted_fields"])
    assert not _other_award_name("H", {"award_name": "Gizmo Scholar"}, {"award_name": ["x"]})
