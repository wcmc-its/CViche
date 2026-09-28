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
    recovered_row_already_rendered,
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
