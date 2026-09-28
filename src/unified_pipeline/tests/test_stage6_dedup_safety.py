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
    find_recovered_row_parent,
    recovered_row_content_rendered,
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


def test_recovered_row_pipe_vs_tab_separator_still_matches():
    # A5IZ6Q's second cell-boundary gap: recover_unclaimed_table_rows always
    # renders a recovered row's own text with " | " between label and value,
    # but the fused parent's multi-cell line for the SAME cell can instead
    # be tab-joined (stage_2_entry_extraction.py's "\t".join(full_text_parts))
    # -- a plain _squash containment check misses this, since '|' isn't
    # whitespace and survives squashing on the row's side only.
    parent = {"text": "Name of Principal Investigator: | A. Researcher\t"
                      "Your role*\toversight\tYour percent (%) effort:\t1%"}
    row = {"text": "Your percent (%) effort: | 1%",
           "recovered_row": True, "parent_idx": 100}
    assert recovered_row_duplicates_parent(row, parent)


# ------------------------------ recovered_row_content_rendered (blind review)
#
# `recovered_row_duplicates_parent` above only scopes a candidate to its
# parent's raw stage-2 text -- it is not proof the row's content reached a
# render slot, because stage 6's section renderers are fixed-slot and drop
# any field they do not name. A blind review of the original A5IZ6Q fix
# caught callers treating scoping alone as sufficient; this is the second,
# mandatory gate that closes it.
#
# Round 2 (blind review): the round-1 version of this gate took a flat
# `rendered_lines` list -- the WHOLE document -- so an entirely unrelated
# entry's rendered line could vouch for a row it had nothing to do with.
# `recovered_row_content_rendered` now also takes `parent` and is scoped, via
# `_parent_rendered_block`, to a single rendered BLOCK (one paragraph, or one
# top-level table): a list of `rendered_blocks`, of which every test below
# supplies exactly one unless the test says otherwise. `_PARENT` is a fixture
# with one identifying field (agency) long enough to anchor
# `_parent_rendered_block` to that block; every block below carries that same
# agency text as its anchor line unless a test is specifically about the
# no-anchor/wrong-block case.

_PARENT = {"extracted_fields": {"agency": "Fictional Research Foundation"}}
_ANCHOR_LINE = "Fictional Research Foundation"


def test_recovered_row_content_rendered_verbatim_value_match():
    row = {"text": "Award Source: | Fictional Research Foundation"}
    block = ["Award Source:", "Fictional Research Foundation"]
    assert recovered_row_content_rendered(row, _PARENT, [block])


def test_recovered_row_content_rendered_label_alone_does_not_count():
    # The label renders on every grant row whether or not the value does
    # (_create_grant_table writes all 8 labels unconditionally) -- matching
    # the label text alone would vouch for a value that never rendered.
    row = {"text": "Non-financial support: | Conference travel fund"}
    block = [_ANCHOR_LINE, "Non-financial support:"]  # value cell blank
    assert not recovered_row_content_rendered(row, _PARENT, [block])


def test_recovered_row_content_rendered_reformatted_date_still_matches():
    # Stage 6 reformats a bare-year duration ("00/2021-00/2022" ->
    # "2021-2022") -- a verbatim match would miss this even though the
    # content did render, so every distinguishing (>=4 char) token must be
    # checked, together, within the SAME rendered line.
    row = {"text": "Duration of support: | 00/2021-00/2022"}
    block = [_ANCHOR_LINE, "Duration of support:", "2021-2022"]
    assert recovered_row_content_rendered(row, _PARENT, [block])


def test_recovered_row_content_rendered_short_value_not_verifiable():
    # A bare grant identifier the renderer never received (no matching
    # extracted_fields key) has no verbatim match and no token long enough
    # to check -- this must read as NOT confirmed, never as "assume
    # rendered": that direction of error is exactly the content loss this
    # check exists to prevent.
    row = {"text": "Grant number: | R01-ZZ98765"}
    block = ["Award Source:", _ANCHOR_LINE]
    assert not recovered_row_content_rendered(row, _PARENT, [block])


def test_recovered_row_content_rendered_scattered_tokens_do_not_count():
    # Both distinguishing tokens exist in the block, but never together on
    # the same rendered line -- a coincidental combination must not count as
    # this row's own value having rendered.
    row = {"text": "Duration of support: | 00/2021-00/2022"}
    block = [_ANCHOR_LINE, "Duration of support:", "2021-Present",
             "unrelated 2022 entry"]
    assert not recovered_row_content_rendered(row, _PARENT, [block])


def test_recovered_row_content_rendered_empty_value_not_confirmed():
    row = {"text": "Non-financial support: | "}
    block = ["Non-financial support:", _ANCHOR_LINE]
    assert not recovered_row_content_rendered(row, _PARENT, [block])


# ---------------------------------------------------- parent-scoping (round 2)
#
# The core round-2 finding: a value must be confirmed against the PARENT's
# OWN rendered block, never the whole document. These fixtures put the
# matching text in a DIFFERENT block from the one the parent's own
# identifying fields resolve to.

def test_recovered_row_content_rendered_ignores_a_different_entrys_block():
    # The exact A5IZ6Q round-2 shape: grant A's own rendered block legitimately
    # carries a percent-effort value; grant B's recovered row must be checked
    # against grant B's OWN block only, never A's, even though A's block is
    # right there in `rendered_blocks`.
    parent_b = {"extracted_fields": {"agency": "Borealis Institute",
                                     "title": "Beta Imaging Cohort"}}
    row = {"text": "Your percent (%) effort: | 5%"}
    block_a = ["Aurora Foundation", "Alpha Sequencing Initiative",
               "Your percent (%) effort:", "25%"]
    block_b = ["Borealis Institute", "Beta Imaging Cohort",
               "Your percent (%) effort:", ""]  # B's own value cell is blank
    assert not recovered_row_content_rendered(row, parent_b, [block_a, block_b])


def test_recovered_row_content_rendered_no_anchor_never_falls_back_to_whole_document():
    # A parent with no identifying field long enough to anchor a block (a
    # declined grant that never rendered, or extracted_fields with nothing
    # but generic/short values) must resolve to NO block -- never silently
    # widen the search back to the whole document, which is the round-2 bug.
    parent = {"extracted_fields": {"status": "Pending"}}  # too generic/short
    row = {"text": "Award Source: | Fictional Research Foundation"}
    block = ["Award Source:", "Fictional Research Foundation"]  # value IS here
    assert not recovered_row_content_rendered(row, parent, [block])


def test_recovered_row_content_rendered_whole_value_not_matched_inside_longer_number():
    # Round 2's second finding: a plain squashed-substring check matches '5%'
    # inside '25%'. The match must be token-aligned even WITHIN the correctly
    # scoped block.
    row = {"text": "Your percent (%) effort: | 5%"}
    block = [_ANCHOR_LINE, "Your percent (%) effort:", "25%"]
    assert not recovered_row_content_rendered(row, _PARENT, [block])


# ------------------------------------------------- mutation guards (round 2)
#
# Each fixture below is designed so the specific mutant named in its comment
# flips the assertion -- not just any change to the function under test.

def test_recovered_row_content_rendered_three_char_run_is_not_a_qualifying_token():
    # Kills: _VALUE_TOKEN_RE widened to {3,}. Under the real {4,} regex, the
    # 3-char fragment "xyz" is never extracted, so only the qualifying
    # "2024" token is checked and it matches. Under a {3,} mutant, "xyz"
    # would ALSO be required on the same line and isn't there, flipping this
    # to False.
    row = {"text": "Label: | xyz 2024"}
    block = [_ANCHOR_LINE, "2024"]
    assert recovered_row_content_rendered(row, _PARENT, [block])


def test_recovered_row_content_rendered_value_uses_full_remainder_after_first_separator():
    # Kills: _recovered_row_value's maxsplit=1 dropped. The row's raw text
    # carries a SECOND separator inside the value itself -- with
    # maxsplit=1, the value is everything after the FIRST separator
    # ("xyz | UniqueToken2024"), so the qualifying token survives. Splitting
    # on every separator instead would truncate the value to "xyz" (3 chars,
    # no qualifying token at all), which can never match anything.
    row = {"text": "Label: | xyz | UniqueToken2024"}
    block = [_ANCHOR_LINE, "uniquetoken2024"]
    assert recovered_row_content_rendered(row, _PARENT, [block])


def test_recovered_row_content_rendered_no_separator_falls_back_to_whole_text():
    # Kills: _recovered_row_value's no-separator fallback changed to ''. A
    # recovered row's text is documented as always "Label: | Value", but the
    # fallback exists for a malformed row with no separator at all -- it
    # must return the WHOLE text as the value, not silently blank it out.
    row = {"text": "StandaloneValue2024"}
    block = [_ANCHOR_LINE, "StandaloneValue2024"]
    assert recovered_row_content_rendered(row, _PARENT, [block])


def test_recovered_row_content_rendered_no_qualifying_token_never_matches_any_line():
    # Kills: `bool(tokens) and` dropped from the token-fallback return.
    # "5%" has no verbatim match anywhere in the block AND extracts zero
    # >=4-char tokens -- `all(token in line for token in [])` is vacuously
    # True for every line, so a broken guard would confirm this against ANY
    # non-empty line, not just one that actually carries the value.
    row = {"text": "Your percent (%) effort: | 5%"}
    block = [_ANCHOR_LINE, "Unrelated content here"]
    assert not recovered_row_content_rendered(row, _PARENT, [block])


def test_recovered_row_content_rendered_token_extraction_is_case_folded():
    # Kills: `.lower()` dropped before `_VALUE_TOKEN_RE.findall`. The
    # renderer's reformatted value is "ab2024" (case-normalized, prefix
    # stripped -- the same shape as the date-reformatting case above). With
    # `.lower()`, the value's "AB2024" segment folds into one 6-char token
    # "ab2024" that matches. Without it, the token regex (lowercase-only)
    # can only see the digit tail "2024" -- 4 chars, still long enough to
    # extract -- but it sits glued to the letters in "ab2024" with no
    # separator, so the token-aligned boundary check correctly refuses to
    # match it mid-token, flipping this to False.
    row = {"text": "Label: | 00/AB2024"}
    block = [_ANCHOR_LINE, "ab2024"]
    assert recovered_row_content_rendered(row, _PARENT, [block])


# ---------------------------------------- find_recovered_row_parent (A5IZ6Q)
#
# A5IZ6Q's own second residual: some of the LYRASIS grant's recovered rows
# keyed `parent_idx` to 244, an index with NO entry of its own -- the grant's
# whole label/value block is one table entry spanning element_idx_start 242
# to element_idx_end 244, and a row recovered from anywhere in that range
# carries whichever index it structurally sits at, not necessarily 242.

def test_find_parent_direct_hit():
    parent = {"element_idx_start": 242, "element_idx_end": 244, "text": "..."}
    by_idx = {"242": parent}
    assert find_recovered_row_parent(242, by_idx, [parent]) is parent


def test_find_parent_direct_hit_preferred_over_a_wider_span():
    # Kills: the direct `entries_by_element_idx` hit dropped from
    # find_recovered_row_parent, falling straight through to the span loop.
    # test_find_parent_direct_hit above can't catch that mutant: its own
    # single candidate is ALSO the span covering 242, so removing the direct
    # hit still finds the same object via the span fallback. Here a parent_idx
    # that is BOTH a direct hit AND numerically inside a different, wider
    # span must resolve to the direct entry -- the span fallback exists only
    # for indices with no entry of their own.
    direct_entry = {"element_idx_start": 242, "element_idx_end": 242, "text": "direct"}
    wide_span_entry = {"element_idx_start": 200, "element_idx_end": 300, "text": "wide"}
    by_idx = {"242": direct_entry, "200": wide_span_entry}
    found = find_recovered_row_parent(242, by_idx, [wide_span_entry])
    assert found is direct_entry


def test_find_parent_falls_back_to_containing_span():
    parent = {"element_idx_start": 242, "element_idx_end": 244, "text": "..."}
    by_idx = {"242": parent}
    # 244 has no entry of its own -- only the span entry [242, 244] covers it.
    assert find_recovered_row_parent(244, by_idx, [parent]) is parent


def test_find_parent_outside_every_span_is_none():
    parent = {"element_idx_start": 242, "element_idx_end": 244, "text": "..."}
    assert find_recovered_row_parent(500, {"242": parent}, [parent]) is None


def test_find_parent_non_numeric_idx_is_none():
    parent = {"element_idx_start": 242, "element_idx_end": 244, "text": "..."}
    assert find_recovered_row_parent("table_5", {}, [parent]) is None


def test_find_parent_span_fallback_still_gated_by_containment():
    # The span fallback only WIDENS which entry gets compared against --
    # recovered_row_duplicates_parent's verbatim check is the real gate, so a
    # row that merely falls in the same numeric range but isn't actually in
    # the parent's text is still left alone.
    parent = {"element_idx_start": 242, "element_idx_end": 244,
             "text": "Award Source: | Fictional Research Foundation"}
    row = {"text": "Non-financial support: | Conference travel",
           "recovered_row": True, "parent_idx": 244}
    found = find_recovered_row_parent(244, {"242": parent}, [parent])
    assert found is parent
    assert not recovered_row_duplicates_parent(row, found)
