"""Fused-citation un-fusing (#208 in the bibliography): when several source
citations collapse into one entry, stage 5d formats the whole block into a
single newline-separated ``formatted_citation``. _fill_bibliography then
renders ONE numbered item followed by unnumbered ``<w:br/>`` continuation
lines (the "no numbering on some pubs at the end" symptom, HNFLBA S8/entry
122 = 15 abstracts fused). split_fused_citation_entries splits each non-blank
line into its own entry so the caller numbers them individually.

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


def test_non_str_formatted_citation_does_not_raise():
    # Stage 4 stores raw LLM JSON and does not guarantee formatted_citation is
    # a string; a dict/list/int must be treated as absent, not crash .splitlines().
    non_str = _pub({"text": "Alpha B. First study."})
    assert split_fused_citation_entries([non_str]) == [non_str]


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
