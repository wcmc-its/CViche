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
    RenderedText,
    _bare_name_inside_longer_name,
    _dates_compatible,
    _drop_is_safe,
    _is_verbatim_copy,
    _kept_fused_beyond_recovery,
    _needs_render_check,
    _segments,
    unverified_drop_rendered,
)
from unified_pipeline.stage6.render_check import UNRENDERED_MIN_RECORD_LINES, _record_lines  # noqa: E402
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


@pytest.mark.xfail(
    reason="#666 residual, prose only: the dropped mentee opens the kept "
           "entry and the kept entry still carries its text, so a copy that "
           "merely carries extra detail (a duplicate) and a copy fused with a "
           "sibling look the same in text. The tab-cell shape of this defect "
           "is reported for a render check (test_sub_record_inside_fused_tab_row_flagged); prose has "
           "no segment count to compare. Flip to a plain assertion when "
           "stage 2 stops fusing the two mentees.",
    strict=True,
)
def test_distinct_mentees_fused_into_prose_entry_kept():
    smith = {"text": "Mentor: John Smith, PhD Candidate, Dept of Biology"}
    fused = {"text": "Mentor: John Smith, PhD Candidate, Dept of Biology and "
                     "Maria Garcia, MS Candidate, Dept of Chemistry, "
                     "Weill Cornell Medicine, 2019-2023"}
    result = deduplicate_entries([smith, fused])
    assert result == [smith, fused]


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


# ------------- #666: containment must not vouch for a record nothing re-checks
#
# The verbatim branch still drops (dev behaviour), but `_needs_render_check`
# flags the two shapes it cannot vouch for and `deduplicate_entries` reports
# those drops through `unverified=`, so stage 6 can check each against the
# rendered document (`unverified_drop_rendered`).

_FUSED_GRANT_ROW = (
    "Director, Northern Lights Wellness Initiative (Recovery Act Program)\t"
    "Harbor Screening\tDr Vale (1 of 8 Centers) $594,000\t"
    "Winter Outreach\tDrs Vale & Moss (1 of 3 Centers) $867,000\t"
    "Peer Coaching\tDr Moss (1 of 5 Centers) $153,000")
# Two cells, so its title (the text before the first tab) is two words and the
# different-titles safety check in `deduplicate_entries` does not apply.
_SUB_GRANT = "Winter Outreach\tDrs Vale & Moss (1 of 3 Centers) $867,000"


def _unverified_of(entries):
    unverified: list[dict] = []
    kept = deduplicate_entries(list(entries), unverified=unverified)
    return kept, unverified


def test_sub_record_inside_fused_tab_row_flagged():
    """web26 M2A shape: a tab-joined single physical line has ONE record line,
    so the recovery pass never re-checks it, and its renderer writes only the
    head record. The sub-record is still dropped, and reported."""
    kept, sub = {"text": _FUSED_GRANT_ROW}, {"text": _SUB_GRANT}
    assert _drop_is_safe(sub, kept) is True
    assert _needs_render_check(sub, kept) is True
    assert _unverified_of([kept, sub]) == ([kept], [sub])


def test_unverified_list_is_optional_and_each_drop_is_reported_once():
    kept, sub = {"text": _FUSED_GRANT_ROW}, {"text": _SUB_GRANT}
    assert deduplicate_entries([kept, sub]) == [kept]
    kept_after, unverified = _unverified_of([kept, sub, dict(sub)])
    assert kept_after == [kept]
    assert len(unverified) == 2  # each drop is reported once


def test_copy_that_opens_the_fused_row_is_not_flagged():
    """The head record is what the renderer writes, so its copy is a true
    duplicate even when the kept entry fuses siblings after it."""
    head = "Director, Northern Lights Wellness Initiative (Recovery Act Program)"
    kept = {"text": "2021-2024\t" + _FUSED_GRANT_ROW}
    assert _unverified_of([kept, {"text": head}]) == ([kept], [])


def test_dated_copy_that_opens_the_fused_row_is_not_flagged():
    """The leading date is stripped from BOTH texts before the head check."""
    head = "Director, Northern Lights Wellness Initiative (Recovery Act Program)"
    kept = {"text": "2021-2024\t" + _FUSED_GRANT_ROW}
    assert _unverified_of([kept, {"text": "2021-2024\t" + head}]) == ([kept], [])


def test_sub_record_inside_kept_with_record_lines_is_not_flagged():
    """A kept entry with >= UNRENDERED_MIN_RECORD_LINES record lines IS
    re-verified line by line by the #221/#225 pass."""
    rows = [f"Member | Committee on Subterranean Balloon Safety Standards "
            f"{name} | Guild of Meandering Auditors | 2013-2016"
            for name in ("Alpha", "Bravo", "Charlie")]
    kept = {"text": "\n".join(rows)}
    assert _drop_is_safe({"text": rows[1]}, kept) is True
    assert _needs_render_check({"text": rows[1]}, kept) is False


@pytest.mark.parametrize("dropped", [
    "Chief Fellow",                      # fewer than 4 significant words
    "Department of Psychiatry",
])
def test_short_fragment_of_a_fused_row_is_not_flagged(dropped):
    kept = "Duke Health\tDepartment of Psychiatry\tChief Fellow\tDurham, NC\tJuly 2012"
    assert _kept_fused_beyond_recovery(dropped, kept) is False
    assert _needs_render_check({"text": dropped}, {"text": kept}) is False


@pytest.mark.parametrize("dropped, kept, unsafe", [
    ("Cortex", "Cerebral Cortex", True),
    ("Nature", "Nature Genetics", True),
    ("Journal of Neuroscience", "European Journal of Neuroscience", True),
    ("Cortex", "Brain, Cortex, Neuron", False),                  # a list item of its own
    ("American Widget Association", "American Widget Association, 1987-present", False),
    ("American Widget Association", "American Widget Association Jun 2014 - date", False),
    ("Political Behavior", "Political Behavior (twice)", False),  # parenthetical annotation
    ("Department of Psychiatry", "Department of Psychiatry\t737 West Street", False),
    ("Cortex", "Cerebral Cortex\tCortex", False),               # one piece is the name alone
    ("2020", "DATE Nov 09, 2020", False),                        # no alphabetic word: not a name
    ("Widget Studies Quarterly", "Widget Studies Quarterly International", True),  # at the 3-word limit
    ("Ana Cruz 2001", "Ana Cruz 2001 Summer Research Fellow, Waisman", False),  # a dated fragment
    ("Sea Ice", "Arctic Sea Ice", True),                         # every word is 3 letters
])
def test_bare_name_inside_longer_name(dropped, kept, unsafe):
    assert _bare_name_inside_longer_name(dropped, kept) is unsafe


@pytest.mark.parametrize("separator", [";", ".", ":", "|", "\n", "\t", ",", "\u201c", "\u201d", '"', "\u2013", "\u2014"])
def test_every_name_piece_separator_isolates_a_name(separator):
    """Each character the piece regex splits on lets the name stand alone."""
    kept = f"Cerebral Cortex{separator}Cortex"
    assert _bare_name_inside_longer_name("Cortex", kept) is False


def test_a_character_that_is_not_a_separator_does_not_isolate_a_name():
    assert _bare_name_inside_longer_name("Cortex", "Cerebral Cortex/Cortex") is True


@pytest.mark.parametrize("annotation", [
    "[twice]", "[2014-2016]", "(twice)",
])
def test_bracketed_and_parenthetical_annotations_are_not_extensions(annotation):
    assert _bare_name_inside_longer_name(
        "Political Behavior", f"Political Behavior {annotation}") is False


@pytest.mark.parametrize("word", [
    "present", "current", "ongoing", "date", "now", "to", "through", "until",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "sept", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
])
def test_every_annotation_word_leaves_a_name_unextended(word):
    """"American Widget Association 1987-present": no comma isolates the
    annotation, so each word is what keeps the name from reading as longer."""
    assert _bare_name_inside_longer_name(
        "American Widget Association", f"American Widget Association 1987-{word}") is False


def test_a_word_that_is_not_an_annotation_extends_the_name():
    assert _bare_name_inside_longer_name(
        "American Widget Association", "American Widget Association 1987-Affiliates") is True


def test_segments_split_on_tab_pipe_and_newline_and_drop_empty_cells():
    assert _segments("a\tb|c\nd") == ["a", "b", "c", "d"]
    assert _segments("\ta\t \t\tb\t") == ["a", "b"]
    assert _segments("   ") == []


def test_newline_separated_cells_count_as_segments():
    """A kept entry of newline-separated lines fuses records the same way."""
    kept = "Head line\nAlpha Bravo Charlie Delta\nThird line"
    assert _kept_fused_beyond_recovery("Alpha Bravo Charlie Delta", kept) is True


def test_distinct_journals_with_a_shared_word_are_dropped_and_reported():
    """Q4D shape: a reviewer list where one journal's name is a word-aligned
    part of another's. Containment says nothing about which the CV meant, so
    the drop is reported for the render check."""
    long, short = {"text": "Cerebral Cortex"}, {"text": "Cortex"}
    assert _unverified_of([long, short]) == ([long], [short])


def test_name_with_only_a_date_annotation_is_dropped_unreported():
    long = {"text": "American Widget Association, 1987-present"}
    short = {"text": "American Widget Association"}
    assert _unverified_of([long, short]) == ([long], [])


def test_a_bare_name_longer_than_the_name_limit_is_not_this_shape():
    assert _bare_name_inside_longer_name("Alpha Beta Gamma Delta", "Alpha Beta Gamma Delta Epsilon") is False


def test_fused_row_thresholds_are_exact():
    """Four significant words and two more segments than the dropped text are
    the smallest shape that counts; one word or one segment fewer is not."""
    base = "Alpha Bravo Charlie Delta"
    two_more = f"Head cell\t{base}\tThird cell"
    assert _kept_fused_beyond_recovery(base, two_more) is True
    assert _kept_fused_beyond_recovery("Alpha Bravo Charlie", "Head cell\tAlpha Bravo Charlie\tThird cell") is False
    assert _kept_fused_beyond_recovery(base, f"Head cell\t{base}") is False
    # Extra segments are measured against the dropped text's own cells: a
    # multi-cell copy of most of the kept row is a duplicate, not a sub-record.
    row = "Widget Society\tChair\tBoston\t2015-2018"
    assert _kept_fused_beyond_recovery(row, f"Header\t{row}") is False


def test_pipe_separated_cells_count_as_segments():
    kept = ("Director, Northern Lights Initiative | Harbor Screening Pilot Grant | "
            "Winter Outreach Pilot Grant | Peer Coaching Pilot Grant")
    assert _kept_fused_beyond_recovery("Winter Outreach Pilot Grant Extra", kept) is True


@pytest.mark.parametrize("dropped, kept", [
    ("of the", "Board of the Directors"),        # no significant word at all
    ("2020", "Class of 2020"),                   # digits only: a year is not a name
    ("UK", "UK Biobank"),                        # under 3 letters: an acronym stub
])
def test_bare_name_gate_ignores_non_names(dropped, kept):
    assert _bare_name_inside_longer_name(dropped, kept) is False


def test_fanned_out_record_is_never_flagged():
    from unified_pipeline.stage6.fan_out import FANNED_OUT_FROM
    kept = {"text": _FUSED_GRANT_ROW, "extracted_fields": {"a": 1}}
    sub = {"text": _SUB_GRANT, "extracted_fields": {"a": 1}, FANNED_OUT_FROM: 1}
    assert _needs_render_check(sub, kept) is False


def test_non_verbatim_drop_is_never_flagged():
    kept = {"text": _FUSED_GRANT_ROW}
    reworded = {"text": "Winter Outreach PI: Drs Vale and Moss, 1 of 3 Centers, $867,000 total"}
    assert _needs_render_check(reworded, kept) is False


# ------------- #666: does a dropped entry's text already reach the document?

def _rendered(*lines):
    return RenderedText(list(lines))


@pytest.mark.parametrize("text, lines, expected", [
    # A name of up to two significant words: rendered only as a piece of its own.
    ("Cortex", ["Cerebral Cortex"], False),
    ("Cortex", ["Brain, Cortex, Neuron"], True),
    ("Journal of Neuroscience", ["European Journal of Neuroscience"], False),
    ("Journal of Neuroscience", ["Journal of Neuroscience"], True),
    ("The Psychiatric Interview",
     ["2023\u20132024 \u2013 \u201cThe Psychiatric Interview\u201d \u2013 Co-Creator"], True),
    ("Political Behavior", ["Political Behavior (twice)"], True),
    ("Political Behavior", ["Political Behavior [twice]"], True),
    ("Cortex", ["Neuron; Cortex; Brain"], True),
    ("Cortex", ["Neuron: Cortex"], True),
    ("Cortex", ["Neuron. Cortex"], True),
    ("Cortex", ["Neuron | Cortex"], True),
    ("Cortex", ["Neuron\tCortex"], True),
    ("Cortex", ["Neuron \u2014 Cortex"], True),
])
def test_short_names_are_rendered_only_as_a_piece_of_their_own(text, lines, expected):
    assert unverified_drop_rendered(text, _rendered(*lines)) is expected


def test_three_word_text_is_rendered_by_a_line_carrying_it_verbatim():
    """RENDERED_NAME_MAX_TOKENS is two: a three-word phrase inside a longer
    rendered line (a seminar title) is already rendered."""
    line = "February 16, 2009 - Congress of Neurological Surgeons Cerebral Vasospasm Management Luncheon Seminar"
    assert unverified_drop_rendered("Cerebral Vasospasm Management", _rendered(line)) is True
    assert unverified_drop_rendered("Cerebral Vasospasm Management",
                                    _rendered("Cerebral Palsy Care")) is False


def test_every_segment_must_sit_in_one_line_for_a_verbatim_hit():
    row = "SNIS Annual Meeting | 2022 | Moderator: Presidential Address and Luminary Lecture"
    text = "SNIS Annual Meeting\t\t2022\tModerator: Presidential Address and Luminary Lecture"
    assert unverified_drop_rendered(text, _rendered(row)) is True
    # Same segments spread over separate lines: no single line carries them all,
    # and no single line carries enough of the words either.
    assert unverified_drop_rendered(text, _rendered(
        "SNIS Annual Meeting", "2022", "Moderator: Presidential Address and Luminary Lecture")) is False


def test_a_long_segment_alone_does_not_vouch_for_a_multi_segment_row():
    """"Associate Editor" appears on every editorial row; it must not render
    the row for a journal that appears nowhere."""
    text = "Neurosurgery\t\t2014-present\tAssociate Editor"
    lines = ["Associate Editor, Biomedical Research International", "2011-2013",
             "Associate Editor of Excellence Award for Neurosurgery"]
    assert unverified_drop_rendered(text, _rendered(*lines)) is False


def test_token_overlap_threshold_is_exact():
    """13 of 20 significant words (0.65) in one line renders the text; 12 do not."""
    words = [f"w{chr(97 + i)}x" for i in range(20)]
    text = " ".join(words)
    assert unverified_drop_rendered(text, _rendered(" ".join(words[:13]))) is True
    assert unverified_drop_rendered(text, _rendered(" ".join(words[:12]))) is False


def test_overlap_counts_years_and_short_acronyms_as_words():
    text = "ISNR Stroke 2011 2014 Associate Editor"
    assert unverified_drop_rendered(text, _rendered("Stroke Associate Editor")) is False
    assert unverified_drop_rendered(text, _rendered("ISNR Stroke 2011 2014 Associate")) is True


def test_overlap_is_per_line_not_pooled():
    text = "alpha bravo charlie delta echo foxtrot golf hotel india juliet"
    assert unverified_drop_rendered(text, _rendered(
        "alpha bravo charlie delta", "echo foxtrot golf hotel", "india juliet")) is False


def test_rendered_text_add_extends_every_index():
    rendered = _rendered("Cerebral Cortex")
    text = "Direct Services PI: Dr Vale $594,000"
    assert unverified_drop_rendered(text, rendered) is False
    rendered.add("Direct Services | PI: Dr Vale | $594,000")
    assert unverified_drop_rendered(text, rendered) is True
    assert unverified_drop_rendered("Neuron", rendered) is False
    rendered.add("Brain; Neuron")
    assert unverified_drop_rendered("Neuron", rendered) is True


def test_a_kept_entry_with_exactly_the_minimum_record_lines_is_reverified_not_flagged():
    """UNRENDERED_MIN_RECORD_LINES is the count at which the #221 pass takes
    over: exactly that many record lines is covered, one fewer is not."""
    row = ("Member | Committee on Subterranean Balloon Safety Standards | "
           "Guild of Meandering Auditors | 2013-2016")
    dropped = "Chair of the Panel on Improbable Weights and Measures"
    covered = "\n".join([row, row.replace("Member", "Chair"), "x\ty\tz\t" + dropped])
    assert len(_record_lines(covered)) == UNRENDERED_MIN_RECORD_LINES
    assert _kept_fused_beyond_recovery(dropped, covered) is False
    one_row = "\n".join([row, "x\ty\tz\t" + dropped])
    assert _kept_fused_beyond_recovery(dropped, one_row) is True


def test_a_name_split_across_two_pieces_is_not_a_longer_name():
    """The dropped words straddle a separator: no kept piece holds them."""
    assert _bare_name_inside_longer_name("Alpha Beta", "Alpha; Beta Gamma") is False
    assert _bare_name_inside_longer_name("Alpha Beta", "Alpha Beta Gamma") is True


def test_empty_or_blank_dropped_text_is_not_a_verbatim_copy():
    assert _is_verbatim_copy("", "anything at all") is False
    assert _is_verbatim_copy(" \t ", "anything at all") is False
    assert _is_verbatim_copy("at all", "anything At All") is True


def test_a_segment_rendered_in_other_letters_is_verbatim_in_one_line():
    """Squashed segments sit in one line even where the word tokens differ
    ("Widget Works" against "WidgetWorks") and the order is not the text's."""
    text = "Acme Widget Works\tAlpha Beta"
    line = "Alpha Beta and AcmeWidgetWorks"
    assert unverified_drop_rendered(text, _rendered(line)) is True


def test_name_lookup_ignores_case():
    assert unverified_drop_rendered("CORTEX", _rendered("Brain, Cortex")) is True
    assert unverified_drop_rendered("cortex", _rendered("Brain, CORTEX")) is True
