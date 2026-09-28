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

from unified_pipeline.stage_6_word_template import (  # noqa: E402
    deduplicate_entries,
    recovered_row_duplicates_parent,
)


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
                    "balloon safety inspections across member lodges"}
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


# ------------------------------------------------------- known open gap, #666

@pytest.mark.xfail(
    reason="#666: dates are invisible to both the fused-blob fallback in "
           "_drop_is_safe and the recovery pass's token-overlap check "
           "(digit-blind _RENDER_TOKEN_RE), so two distinct multi-line "
           "committee terms differing only by year can be silently dropped. "
           "Flip to a plain assertion once #666 lands a corpus-verified fix.",
    strict=True,
)
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


@pytest.mark.xfail(
    reason="#666: verbatim-containment branch of _drop_is_safe fires on "
           "prose with no record-shaped lines at all, so the #221/#225 "
           "recovery pass never even looks at either entry (0 record lines "
           "on both sides). Two distinct mentees fused into one un-split "
           "entry: one mentee's text is a literal substring of the other. "
           "Flip to a plain assertion once #666 lands a corpus-verified fix.",
    strict=True,
)
def test_distinct_mentees_fused_into_prose_entry_kept():
    smith = {"text": "Mentor: John Smith, PhD Candidate, Dept of Biology"}
    fused = {"text": "Mentor: John Smith, PhD Candidate, Dept of Biology and "
                     "Maria Garcia, MS Candidate, Dept of Chemistry, "
                     "Weill Cornell Medicine, 2019-2023"}
    result = deduplicate_entries([smith, fused])
    assert result == [smith, fused]


@pytest.mark.xfail(
    reason="#666: subset-containment branch of _drop_is_safe is fooled by "
           "narrative prose that mentions a prior, distinct record's "
           "identifying nouns/years in passing (successor-committee "
           "framing) -- full token subset containment holds even though "
           "the two DSMB memberships are different trials, different "
           "terms. Committee/service codes are outside "
           "DATE_AWARE_DEDUP_CODES, so require_date_overlap doesn't help "
           "here either. Flip to a plain assertion once #666 lands a "
           "corpus-verified fix.",
    strict=True,
)
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


# --------------------------------------- recovered-row/parent duplicates (A5IZ6Q)
#
# `recover_unclaimed_table_rows` (stage 2, #420) emits one entry per table row
# no delimiter claimed, independently of whatever delimiter DID claim the
# surrounding table. A wide table-level delimiter spanning several element
# indices (a whole grant's label/value block) and the individual rows inside
# it can both survive as separate entries: the fused parent classifies into a
# render-routed code (e.g. M2B) and renders structurally, while each
# single-field recovered row is too sparse to classify as anything but T and
# is otherwise a verbatim duplicate of content the reader already saw.
# `deduplicate_entries` above never sees the pair -- parent and row land in
# different taxonomy-code groups, and dedup only ever compares within one.

def test_recovered_row_duplicate_of_parent_detected():
    parent = {"text": "Award Source: | Fictional Research Foundation\n"
                      "Project title: | Synthetic Tools for Data Curation\n"
                      "Duration of support: | 00/2021-00/2022"}
    row = {"text": "Award Source: | Fictional Research Foundation",
           "recovered_row": True, "parent_idx": 100}
    assert recovered_row_duplicates_parent(row, parent)


def test_recovered_row_not_contained_in_parent_not_flagged():
    # The row the model's delimiter genuinely skipped -- not present in the
    # parent's own text at all -- must go through the normal path unchanged.
    parent = {"text": "Award Source: | Fictional Research Foundation\n"
                      "Project title: | Synthetic Tools for Data Curation"}
    row = {"text": "Non-financial support: | Conference travel",
           "recovered_row": True, "parent_idx": 100}
    assert not recovered_row_duplicates_parent(row, parent)


def test_non_recovered_row_never_flagged_even_if_contained():
    # Only the stage-2 backstop's own output carries `recovered_row` -- an
    # ordinary model-attested entry that happens to be a text subset of
    # another must go through the normal Jaccard/containment dedup instead,
    # not this parent-linked shortcut.
    parent = {"text": "Award Source: | Fictional Research Foundation\n"
                      "Project title: | Synthetic Tools for Data Curation"}
    row = {"text": "Award Source: | Fictional Research Foundation"}
    assert not recovered_row_duplicates_parent(row, parent)


def test_recovered_row_with_no_resolvable_parent_not_flagged():
    # parent_idx pointed nowhere (lookup miss) -- caller passes None. Content
    # loss risk is on the "flag it" side, not this one, so the safe default
    # is to leave the row in the normal appendix/recovery path.
    row = {"text": "Award Source: | Fictional Research Foundation",
           "recovered_row": True, "parent_idx": 999}
    assert not recovered_row_duplicates_parent(row, None)


def test_recovered_row_reformatted_whitespace_still_matches():
    # _squash normalizes whitespace/case -- a recovered row's line-splitting
    # must not be defeated by incidental spacing differences from the parent.
    parent = {"text": "Duration of support: |   00/2021-00/2022  \n"
                      "Name of Principal Investigator: | A. Researcher"}
    row = {"text": "duration of support: | 00/2021-00/2022",
           "recovered_row": True, "parent_idx": 100}
    assert recovered_row_duplicates_parent(row, parent)
