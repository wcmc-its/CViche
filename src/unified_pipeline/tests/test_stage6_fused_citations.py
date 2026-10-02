"""Fused-citation un-fusing (#208 in the bibliography): when several source
citations collapse into one entry, stage 5d formats the whole block into a
single newline-separated ``formatted_citation``. _fill_bibliography then
renders ONE numbered item followed by unnumbered ``<w:br/>`` continuation
lines (the "no numbering on some pubs at the end" symptom, HNFLBA S8/entry
122 = 15 abstracts fused). split_fused_citation_entries splits each non-blank
line into its own entry so the caller numbers them individually. A line that 5d
joined with " | " instead of newlines (#1237) is split the same way, but only
when every piece is citation-shaped.

Fixtures are fictional. Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_fused_citations.py -p no:cacheprovider
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage_6_word_template import split_fused_citation_entries  # noqa: E402


def _pub(fc, source="stage_5d_llm", **extra):
    fields = {"formatted_citation": fc, "formatting_source": source}
    return {"extracted_fields": fields, **extra}


def _citations(entries):
    return [e["extracted_fields"]["formatted_citation"] for e in entries]


def test_fused_block_splits_into_one_entry_per_line():
    fused = _pub(
        "Alpha B, Gamma D. First study. Journal One; 2025 May; Nowhere, ZZ.\n"
        "\n"
        "Delta E, Zeta F. Second study. Journal Two; 2024 June; Elsewhere, YY.\n"
        "\n"
        "Eta G. Third study. Journal Three; 2023 July; Somewhere, XX."
    )
    out = split_fused_citation_entries([fused])
    assert _citations(out) == [
        "Alpha B, Gamma D. First study. Journal One; 2025 May; Nowhere, ZZ.",
        "Delta E, Zeta F. Second study. Journal Two; 2024 June; Elsewhere, YY.",
        "Eta G. Third study. Journal Three; 2023 July; Somewhere, XX.",
    ]


def test_single_citation_passes_through_untouched():
    solo = _pub("Solo A. Only paper. Journal Z; 2025; Here, HH.")
    out = split_fused_citation_entries([solo])
    assert out == [solo]  # identity: no wrapping, no field churn


def test_non_5d_multiline_is_not_split():
    # Only stage-5d LLM citations carry the fused-block shape; a parts-built
    # citation with an accidental newline must NOT be split.
    weird = _pub("A. Title.\nContinued.", source="parts")
    assert split_fused_citation_entries([weird]) == [weird]


def test_continuation_lines_drop_block_provenance_but_first_keeps_it():
    fused = _pub(
        "Alpha B. First. J1; 2025; A, AA.\nDelta E. Second. J2; 2024; B, BB.",
        classification_reasoning="looks like an abstract block",
        enrichment_status="enriched",
        text="RAW SOURCE BLOCK",
    )
    out = split_fused_citation_entries([fused])
    assert len(out) == 2
    # first line keeps the block-level provenance
    assert out[0]["classification_reasoning"] == "looks like an abstract block"
    assert out[0]["enrichment_status"] == "enriched"
    assert out[0]["text"] == "RAW SOURCE BLOCK"
    # continuation line is a distinct record: no replayed comment / track-change
    assert "classification_reasoning" not in out[1]
    assert out[1]["enrichment_status"] == ""
    assert out[1]["text"] == ""


def test_blank_lines_do_not_create_empty_entries():
    fused = _pub("Only A. Real. J; 2025; X, XX.\n\n\n")
    # one real line + trailing blanks -> not >=2 lines -> passes through
    assert split_fused_citation_entries([fused]) == [fused]


def test_control_character_inside_one_citation_is_not_split():
    # #742: str.splitlines() also breaks on \x0b, \x0c, \x1c-\x1e, \x85,
    # U+2028 and U+2029, so a stray control character (the #552 input class)
    # inside a single LLM-written citation used to become two numbered
    # entries. Only \r\n, \r and \n are line breaks here.
    solo = _pub(
        "Doe J, Smith A. A stu\x0bdy without enrichment. J 2020.\x85"
        "More text\x1cwith embedded controls."
    )
    out = split_fused_citation_entries([solo])
    assert len(out) == 1
    assert out == [solo]


def test_bare_cr_separated_block_still_splits():
    # A fused block whose two citations are separated only by a bare \r (no
    # following \n) must still split into two entries: \r is a real line
    # break the normalization handles explicitly, not one of the control
    # characters #742 excludes.
    fused = _pub(
        "Alpha B. First study. Journal One; 2025; Nowhere, ZZ.\r"
        "Delta E. Second study. Journal Two; 2024; Elsewhere, YY."
    )
    out = split_fused_citation_entries([fused])
    assert _citations(out) == [
        "Alpha B. First study. Journal One; 2025; Nowhere, ZZ.",
        "Delta E. Second study. Journal Two; 2024; Elsewhere, YY.",
    ]


def test_non_str_formatted_citation_does_not_raise():
    # Stage 4 stores raw LLM JSON and does not guarantee formatted_citation is
    # a string; a dict/list/int must be treated as absent, not crash .splitlines().
    non_str = _pub({"text": "Alpha B. First study."})
    assert split_fused_citation_entries([non_str]) == [non_str]


def test_pipe_joined_block_splits_into_one_entry_per_citation():
    # #1237: 5d joined several citations on ONE line with " | " instead of
    # one per line, so the newline split never fired and they rendered as a
    # single numbered item.
    fused = _pub(
        "Alpha B, Gamma D. First talk. Example Society Meeting; 2025 May; Nowhere, ZZ."
        " | Delta E, Zeta F. Second talk. Example Society Meeting; 2024 June; Elsewhere, YY."
        " | Eta G. Third talk. Example Society Meeting; 2023 July; Somewhere, XX."
    )
    out = split_fused_citation_entries([fused])
    assert _citations(out) == [
        "Alpha B, Gamma D. First talk. Example Society Meeting; 2025 May; Nowhere, ZZ.",
        "Delta E, Zeta F. Second talk. Example Society Meeting; 2024 June; Elsewhere, YY.",
        "Eta G. Third talk. Example Society Meeting; 2023 July; Somewhere, XX.",
    ]


def test_pipe_split_continuations_drop_block_provenance():
    # The pipe split reuses the newline split's provenance rule: the first
    # citation keeps the block-level comment, enrichment and raw text; the
    # rest are distinct records and must not replay them.
    fused = _pub(
        "Alpha B. First talk. Example Meeting; 2025; A, AA."
        " | Delta E. Second talk. Example Meeting; 2024; B, BB.",
        classification_reasoning="looks like a presentation block",
        enrichment_status="enriched",
        text="RAW SOURCE BLOCK",
    )
    out = split_fused_citation_entries([fused])
    assert len(out) == 2
    assert out[0]["classification_reasoning"] == "looks like a presentation block"
    assert out[0]["enrichment_status"] == "enriched"
    assert out[0]["text"] == "RAW SOURCE BLOCK"
    assert "classification_reasoning" not in out[1]
    assert out[1]["enrichment_status"] == ""
    assert out[1]["text"] == ""


def test_citation_with_a_pipe_in_its_title_is_not_split():
    # A single citation that legitimately contains " | ": neither half of its
    # title is a whole citation (no year, no closing period), so it stays one.
    solo = _pub("Doe J. Home | Archive of examples. Example Journal; 2025; 4(2):10-12.")
    assert split_fused_citation_entries([solo]) == [solo]


def test_pipe_line_is_split_only_if_every_piece_is_citation_shaped():
    # All-or-nothing: the second piece has a year but no closing period, so the
    # pipe is judged to belong to one citation and nothing is split.
    mixed = _pub(
        "Alpha B. First talk. Example Meeting; 2025; A, AA."
        " | Delta E. Second talk. Example Meeting; 2024; B, BB"
    )
    assert split_fused_citation_entries([mixed]) == [mixed]
    # And the converse: a piece with a closing period but no year.
    no_year = _pub(
        "Alpha B. First talk. Example Meeting; 2025; A, AA."
        " | Delta E. Second talk. Example Meeting; B, BB."
    )
    assert split_fused_citation_entries([no_year]) == [no_year]


def test_non_5d_pipe_joined_citation_is_not_split():
    # Same gate as the newline split: only stage-5d LLM citations are un-fused.
    weird = _pub(
        "Alpha B. First. J1; 2025; A, AA. | Delta E. Second. J2; 2024; B, BB.",
        source="parts",
    )
    assert split_fused_citation_entries([weird]) == [weird]


def test_newline_and_pipe_joins_in_one_block_both_split():
    # A block may mix the two joiners: split on newlines first, then split any
    # line that is itself pipe-joined.
    fused = _pub(
        "Alpha B. First talk. Example Meeting; 2025; A, AA.\n"
        "Delta E. Second talk. Example Meeting; 2024; B, BB."
        " | Eta G. Third talk. Example Meeting; 2023; C, CC."
    )
    out = split_fused_citation_entries([fused])
    assert _citations(out) == [
        "Alpha B. First talk. Example Meeting; 2025; A, AA.",
        "Delta E. Second talk. Example Meeting; 2024; B, BB.",
        "Eta G. Third talk. Example Meeting; 2023; C, CC.",
    ]


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
