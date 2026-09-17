"""Behaviour tests for Stage 4.5 -- Research Summary Generation (issue #704).

Covers the pure scoring/selection layer (score_entry_seniority,
prioritize_entries, gather_context_entries, is_valid_entry,
format_entry_for_context, build_context_string) and the LLM-driven layer
(score_existing_m1, generate_research_summary, and run_stage_4_5's M1
keep-vs-regenerate decision).

Self-contained: ``call_llm`` is stubbed at the module attribute
(``unified_pipeline.stage_4_5_research_summary.call_llm``) in every test
that reaches it -- no Bedrock/OpenAI, no network. All CV content is
synthetic; nothing under data/, _batch_runs, or any real .docx is read.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage4_5_research_summary_behaviour.py -p no:cacheprovider
"""

import json
import sys
from pathlib import Path

import pytest

# Ensure the repo's ``src`` directory is importable regardless of cwd/rootdir.
_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import unified_pipeline.stage_4_5_research_summary as stage_4_5  # noqa: E402
from unified_pipeline.stage_4_5_research_summary import (  # noqa: E402
    PI_BONUS,
    SENIOR_AUTHOR_BONUS,
    build_context_string,
    format_entry_for_context,
    gather_context_entries,
    is_valid_entry,
    prioritize_entries,
    run_stage_4_5,
    score_entry_seniority,
)


# --- score_entry_seniority ----------------------------------------------------

def test_score_entry_seniority_pi_role_gets_full_bonus():
    entry = {"extracted_fields": {"pi_role": "Principal Investigator"}, "text": "x"}
    assert score_entry_seniority(entry, "M2A") == PI_BONUS


def test_score_entry_seniority_co_pi_gets_partial_bonus():
    entry = {"extracted_fields": {"pi_role": "Co-PI"}, "text": "x"}
    assert score_entry_seniority(entry, "M2A") == PI_BONUS * 0.7


def test_score_entry_seniority_non_pi_role_gets_no_bonus():
    """"Co-Investigator" contains neither "principal investigator" nor
    "co-pi"/"co-principal", so it must fall through to 0.0."""
    entry = {"extracted_fields": {"role": "Co-Investigator"}, "text": "x"}
    assert score_entry_seniority(entry, "M2A") == 0.0


def test_score_entry_seniority_publication_first_author_match():
    entry = {
        "extracted_fields": {
            "authors": "Jane Public, Jones B, Smith J",
            "target_name": "Jane Public",
        },
        "text": "x",
    }
    assert score_entry_seniority(entry, "S1") == SENIOR_AUTHOR_BONUS


def test_score_entry_seniority_publication_last_author_match():
    entry = {
        "extracted_fields": {
            "authors": "Smith J, Jones B, Public Jane",
            "target_name": "Jane Public",
        },
        "text": "x",
    }
    assert score_entry_seniority(entry, "S1") == SENIOR_AUTHOR_BONUS


def test_score_entry_seniority_publication_no_author_match():
    entry = {
        "extracted_fields": {"authors": "Smith J, Jones B", "target_name": "Jane Public"},
        "text": "x",
    }
    assert score_entry_seniority(entry, "S1") == 0.0


def test_score_entry_seniority_ignores_non_grant_non_pub_codes():
    """A code that is neither a grant (M2*) nor S1 never earns a bonus, even
    with a PI-shaped role field."""
    entry = {"extracted_fields": {"pi_role": "Principal Investigator"}, "text": "x"}
    assert score_entry_seniority(entry, "H") == 0.0


@pytest.mark.xfail(strict=True, raises=IndexError, reason="suspected bug: whitespace-only target_name passes the truthiness guard, then .split()[0] raises IndexError (#850)")
def test_score_entry_seniority_whitespace_only_target_name_degrades_to_no_bonus():
    """A noisy extracted_fields.target_name must cost the entry its seniority
    bonus, not crash prioritize_entries / run_stage_4_5 (#850)."""
    entry = {
        "extracted_fields": {"authors": "Smith J, Jones B", "target_name": "   "},
        "text": "x",
    }
    assert score_entry_seniority(entry, "S1") < SENIOR_AUTHOR_BONUS


# --- prioritize_entries --------------------------------------------------------

def test_prioritize_entries_orders_by_combined_score_and_applies_limit():
    entries = [
        {"text": "Old co-I grant", "extracted_fields": {"pi_role": "Co-Investigator", "year": "2005"}},
        {"text": "New PI grant with substantive descriptive narrative text " * 5,
         "extracted_fields": {"pi_role": "PI", "year": "2023"}},
        {"text": "Mid grant", "extracted_fields": {"pi_role": "", "year": "2015"}},
    ]

    result = prioritize_entries(entries, "M2A", limit=2)

    assert len(result) == 2
    assert result[0]["text"].startswith("New PI grant")
    assert result[1]["text"] == "Mid grant"
    assert not any(e["text"] == "Old co-I grant" for e in result)


def test_prioritize_entries_empty_list_returns_empty():
    assert prioritize_entries([], "M2A") == []


def test_prioritize_entries_uses_taxonomy_default_limit_when_unspecified():
    """H's ENTRY_LIMITS cap is 10; feeding exactly 10 entries with no
    explicit ``limit`` must return all 10 (proves the taxonomy-keyed default
    is actually consulted, not some other constant)."""
    entries = [{"text": str(i), "extracted_fields": {"year": str(2000 + i)}} for i in range(10)]
    result = prioritize_entries(entries, "H")
    assert len(result) == 10


def test_prioritize_entries_default_limit_for_unlisted_code():
    """A code absent from ENTRY_LIMITS falls back to ENTRY_LIMITS['default']
    == 5, not to "no limit"."""
    entries = [{"text": str(i), "extracted_fields": {}} for i in range(8)]
    result = prioritize_entries(entries, "Q1")
    assert len(result) == 5


# --- gather_context_entries -----------------------------------------------------

def test_gather_context_entries_skips_low_value_sections_and_keeps_boundary():
    """B1/T are explicitly weighted <= -0.85 and an unrecognized code
    defaults to -0.9 -- all three must be dropped. S2 sits at exactly -0.8,
    which is NOT <= -0.85, so it must survive: this pins the boundary, not
    just "low stuff is skipped"."""
    entries_by_code = {
        "M1": [{"text": "research", "extracted_fields": {}}],
        "B1": [{"text": "edu", "extracted_fields": {}}],
        "T": [{"text": "appendix", "extracted_fields": {}}],
        "UNKNOWNCODE": [{"text": "x", "extracted_fields": {}}],
        "S2": [{"text": "review", "extracted_fields": {}}],
    }

    result = gather_context_entries(entries_by_code)

    codes = [code for code, _entry, _weight in result]
    assert codes == ["M1", "S2"]


def test_gather_context_entries_adds_seniority_bonus_to_weight():
    """A PI grant's weight in the returned tuple must be base_weight (M2A:
    -0.25) + PI_BONUS, not the bare base weight."""
    entries_by_code = {
        "M2A": [{"text": "R01 study", "extracted_fields": {"pi_role": "Principal Investigator"}}],
    }
    result = gather_context_entries(entries_by_code)
    assert len(result) == 1
    _code, _entry, weight = result[0]
    assert weight == stage_4_5.SECTION_WEIGHTS["M2A"] + PI_BONUS


# --- is_valid_entry -------------------------------------------------------------

def test_is_valid_entry_rejects_grant_without_title():
    entry = {"extracted_fields": {"title": ""}, "text": "has text"}
    assert is_valid_entry("M2A", entry) is False


def test_is_valid_entry_rejects_grant_with_literal_none_title():
    entry = {"extracted_fields": {"title": "None"}, "text": "has text"}
    assert is_valid_entry("M2A", entry) is False


def test_is_valid_entry_rejects_publication_without_title():
    entry = {"extracted_fields": {"title": ""}, "text": "has text"}
    assert is_valid_entry("S1", entry) is False


def test_is_valid_entry_rejects_entry_with_no_text():
    entry = {"extracted_fields": {}, "text": "   "}
    assert is_valid_entry("H", entry) is False


def test_is_valid_entry_accepts_grant_with_title_and_text():
    entry = {"extracted_fields": {"title": "Study of X"}, "text": "has text"}
    assert is_valid_entry("M2A", entry) is True


def test_is_valid_entry_accepts_generic_entry_with_text():
    entry = {"extracted_fields": {}, "text": "Award"}
    assert is_valid_entry("H", entry) is True


# --- format_entry_for_context ----------------------------------------------------

def test_format_entry_for_context_publication():
    entry = {"extracted_fields": {"authors": "A, B", "title": "T", "journal": "J", "year": "2020"}, "text": ""}
    assert format_entry_for_context("S1", entry) == "[PUB] A, B. T. J. 2020"


def test_format_entry_for_context_grant():
    entry = {"extracted_fields": {"title": "Grant T", "pi_role": "PI", "agency": "NIH"}, "text": ""}
    assert format_entry_for_context("M2A", entry) == "[GRANT-M2A] Grant T | Role: PI | Agency: NIH"


def test_format_entry_for_context_research_activities():
    entry = {"extracted_fields": {}, "text": "research text"}
    assert format_entry_for_context("M1", entry) == "[RESEARCH] research text"


def test_format_entry_for_context_honor_with_year():
    entry = {"extracted_fields": {"award_name": "Best Award", "year": "2019"}, "text": ""}
    assert format_entry_for_context("H", entry) == "[HONOR] Best Award (2019)"


def test_format_entry_for_context_honor_without_year_omits_parens():
    entry = {"extracted_fields": {"award_name": "Best Award"}, "text": ""}
    assert format_entry_for_context("H", entry) == "[HONOR] Best Award"


def test_format_entry_for_context_bibliometric():
    entry = {"extracted_fields": {}, "text": "h-index 20"}
    assert format_entry_for_context("S0", entry) == "[METRICS] h-index 20"


def test_format_entry_for_context_generic_fallback_uses_bare_code():
    entry = {"extracted_fields": {}, "text": "External leadership text"}
    assert format_entry_for_context("Q1", entry) == "[Q1] External leadership text"


# --- build_context_string --------------------------------------------------------

def test_build_context_string_filters_invalid_entries():
    """An entry that fails is_valid_entry (blank text) contributes nothing,
    even though it is well-formed enough to reach build_context_string."""
    entry = ("H", {"extracted_fields": {}, "text": ""}, -0.1)
    assert build_context_string([entry], max_tokens=100) == ""


def test_build_context_string_drops_entry_that_exceeds_the_char_budget():
    """max_tokens=3 -> max_chars=12; the formatted entry is 14 chars, so it
    must not be included and the result is empty (not truncated text)."""
    entry = ("X", {"extracted_fields": {}, "text": "A" * 10}, -0.1)
    assert build_context_string([entry], max_tokens=3) == ""


def test_build_context_string_stops_before_the_entry_that_would_overflow():
    """max_tokens=7 -> max_chars=28. Each formatted entry is 14 chars. After
    the first entry, the correct running total is 14 + 1 (the "+1 for
    newline" accounting on line 297) = 15; checking the second entry then
    computes 15 + 14 = 29 > 28, so it is dropped and only the first entry
    appears. If the "+1" were ever dropped, the running total would stay at
    14, so 14 + 14 = 28 is NOT > 28 and the second entry would wrongly be
    admitted -- this is the exact boundary that distinguishes the two."""
    e1 = ("X", {"extracted_fields": {}, "text": "A" * 10}, -0.1)
    e2 = ("X", {"extracted_fields": {}, "text": "B" * 10}, -0.2)
    result = build_context_string([e1, e2], max_tokens=7)
    assert result == "[X] " + "A" * 10


def test_build_context_string_admits_entry_that_exactly_fills_the_budget():
    """max_tokens=4 -> max_chars=16, and the single formatted entry is
    exactly 16 chars ("[X] " + 12 A's). The budget check on line 293 is
    ``total_chars + len(formatted) > max_chars: break`` -- with an EXACT
    fill, 0 + 16 > 16 is False, so the entry must be admitted. A ``>=``
    off-by-one there would wrongly break on this exact-fill boundary and
    return an empty string instead."""
    entry = ("X", {"extracted_fields": {}, "text": "A" * 12}, -0.1)
    result = build_context_string([entry], max_tokens=4)
    assert result == "[X] " + "A" * 12


def test_build_context_string_includes_every_entry_that_fits():
    e1 = ("X", {"extracted_fields": {}, "text": "A" * 10}, -0.1)
    e2 = ("X", {"extracted_fields": {}, "text": "B" * 10}, -0.2)
    result = build_context_string([e1, e2], max_tokens=8)
    assert result == "[X] " + "A" * 10 + "\n[X] " + "B" * 10


# --- score_existing_m1 (call_llm stubbed) -----------------------------------------

def test_score_existing_m1_parses_valid_json_response(monkeypatch):
    def fake_call_llm(**_kwargs):
        return {
            "content": json.dumps({"score": 0.85, "reasoning": "Great narrative"}),
            "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
            "cache_read_tokens": 1, "cache_write_tokens": 2, "cost": 0.01,
        }
    monkeypatch.setattr(stage_4_5, "call_llm", fake_call_llm)

    score, reasoning, usage = stage_4_5.score_existing_m1("some content")

    assert score == 0.85
    assert reasoning == "Great narrative"
    assert usage["cost"] == 0.01
    assert usage["cache_read_tokens"] == 1


def test_score_existing_m1_strips_markdown_code_fence(monkeypatch):
    fenced = "```json\n" + json.dumps({"score": 0.55, "reasoning": "ok"}) + "\n```"

    def fake_call_llm(**_kwargs):
        return {
            "content": fenced,
            "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost": 0.0,
        }
    monkeypatch.setattr(stage_4_5, "call_llm", fake_call_llm)

    score, reasoning, _usage = stage_4_5.score_existing_m1("content")

    assert score == 0.55
    assert reasoning == "ok"


def test_score_existing_m1_regex_fallback_when_not_valid_json(monkeypatch):
    """Content that is not parseable JSON at all (no fences either) but
    still contains a "score": <n> substring must be recovered via the
    regex fallback, with a reasoning string distinct from the JSON path's."""
    def fake_call_llm(**_kwargs):
        return {
            "content": 'Sure, here you go "score": 0.42 based on the content.',
            "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost": 0.0,
        }
    monkeypatch.setattr(stage_4_5, "call_llm", fake_call_llm)

    score, reasoning, _usage = stage_4_5.score_existing_m1("content")

    assert score == 0.42
    assert reasoning == "Score extracted from response"


def test_score_existing_m1_returns_zero_when_totally_unparseable(monkeypatch):
    def fake_call_llm(**_kwargs):
        return {
            "content": "no json and no score field anywhere in this text",
            "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost": 0.0,
        }
    monkeypatch.setattr(stage_4_5, "call_llm", fake_call_llm)

    score, reasoning, _usage = stage_4_5.score_existing_m1("content")

    assert score == 0.0
    assert reasoning == "Failed to parse response"


# --- generate_research_summary (call_llm stubbed) ---------------------------------

def test_generate_research_summary_returns_stripped_text_and_usage(monkeypatch):
    def fake_call_llm(**_kwargs):
        return {
            "content": "  Generated summary text about research.  ",
            "prompt_tokens": 20, "completion_tokens": 30, "total_tokens": 50, "cost": 0.02,
        }
    monkeypatch.setattr(stage_4_5, "call_llm", fake_call_llm)

    text, usage = stage_4_5.generate_research_summary("some context", "Jane Doe")

    assert text == "Generated summary text about research."
    assert usage["prompt_tokens"] == 20
    assert usage["cost"] == 0.02


# --- run_stage_4_5 end-to-end (call_llm stubbed) ----------------------------------

def _write_fields_json(tmp_path, document_uid, entries, cv_owner=None):
    data = {"document_uid": document_uid, "entries": entries}
    if cv_owner is not None:
        data["cv_owner"] = cv_owner
    path = tmp_path / "fields.json"
    path.write_text(json.dumps(data))
    return path


def test_run_stage_4_5_keeps_high_scoring_existing_m1(monkeypatch, tmp_path):
    """M1 score >= 0.8 -> the existing text is used verbatim, no generation
    call happens, and original_m1_content stays None (only the regenerate
    branch populates it)."""
    def fake_call_llm(*, max_tokens, **_kwargs):
        if max_tokens == 200:
            return {
                "content": json.dumps({"score": 0.9, "reasoning": "cohesive narrative"}),
                "prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7, "cost": 0.001,
            }
        raise AssertionError("generation must not be attempted when M1 is kept")
    monkeypatch.setattr(stage_4_5, "call_llm", fake_call_llm)

    existing_text = "Existing high quality narrative about cancer research funded by NIH."
    inp = _write_fields_json(
        tmp_path, "TEST01",
        [{"taxonomy_code": "M1", "text": existing_text, "extracted_fields": {}}],
        cv_owner={"first_name": "Jane", "last_name": "Doe"},
    )
    outp = tmp_path / "out.json"

    result_path = run_stage_4_5(str(inp), str(outp), verbose=True)

    out = json.loads(Path(result_path).read_text())
    assert out["research_summary"]["generation_method"] == "existing_content"
    assert out["research_summary"]["text"] == existing_text
    assert out["research_summary"]["m1_score"] == 0.9
    assert out["original_m1_content"] is None
    assert out["context_used"] == {"entry_count": 0, "top_codes": []}


def test_run_stage_4_5_regenerates_low_scoring_m1(monkeypatch, tmp_path):
    """M1 score below 0.8 -> falls through to context gathering + LLM
    generation; the output text must be the GENERATED text (not the
    existing M1 text -- that would mean the fallback/keep route leaked
    through), and original_m1_content must record the low score as the
    reason it was replaced."""
    def fake_call_llm(*, max_tokens, **_kwargs):
        if max_tokens == 200:
            return {
                "content": json.dumps({"score": 0.3, "reasoning": "too keyword-y"}),
                "prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7, "cost": 0.001,
            }
        return {
            "content": "Newly generated narrative summary.",
            "prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30, "cost": 0.005,
        }
    monkeypatch.setattr(stage_4_5, "call_llm", fake_call_llm)

    inp = _write_fields_json(
        tmp_path, "TEST02",
        [
            {"taxonomy_code": "M1", "text": "Cancer. Genomics.", "extracted_fields": {}},
            {"taxonomy_code": "S1", "text": "A paper",
             "extracted_fields": {"authors": "Doe J, Smith A", "title": "A Paper",
                                   "journal": "Nature", "year": "2020", "target_name": "Jane Doe"}},
            {"taxonomy_code": "M2A", "text": "Grant text",
             "extracted_fields": {"title": "R01 Study", "pi_role": "PI", "agency": "NIH", "year": "2022"}},
        ],
        cv_owner={"first_name": "Jane", "last_name": "Doe"},
    )
    outp = tmp_path / "out.json"

    result_path = run_stage_4_5(str(inp), str(outp), verbose=True)

    out = json.loads(Path(result_path).read_text())
    assert out["research_summary"]["generation_method"] == "llm_generated"
    assert out["research_summary"]["text"] == "Newly generated narrative summary."
    assert out["research_summary"]["m1_score"] == 0.3
    assert out["original_m1_content"]["combined_text"] == "Cancer. Genomics."
    assert out["original_m1_content"]["reason_not_used"] == (
        "Score 0.30 below threshold 0.8 - too keyword-y"
    )
    # Context gathering actually ran across all three taxonomy codes.
    assert set(out["context_used"]["top_codes"]) == {"M1", "S1", "M2A"}


def test_run_stage_4_5_skips_scoring_when_no_existing_m1(monkeypatch, tmp_path):
    """No M1 entries at all -> existing_m1_content is empty, so
    score_existing_m1 must never be invoked (a call with max_tokens=200
    would fail the assertion below); generation proceeds directly."""
    def fake_call_llm(*, max_tokens, **_kwargs):
        if max_tokens == 200:
            raise AssertionError("scoring must not run when there is no existing M1 content")
        return {
            "content": "Generated from context only.",
            "prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30, "cost": 0.005,
        }
    monkeypatch.setattr(stage_4_5, "call_llm", fake_call_llm)

    inp = _write_fields_json(
        tmp_path, "TEST03",
        [{"taxonomy_code": "S1", "text": "A paper",
          "extracted_fields": {"authors": "Doe J", "title": "A Paper", "journal": "Nature", "year": "2020"}}],
    )
    outp = tmp_path / "out.json"

    result_path = run_stage_4_5(str(inp), str(outp), verbose=False)

    out = json.loads(Path(result_path).read_text())
    assert out["research_summary"]["generation_method"] == "llm_generated"
    assert out["research_summary"]["text"] == "Generated from context only."
    assert out["research_summary"]["m1_score"] == 0.0
    assert out["original_m1_content"] is None


def test_run_stage_4_5_derives_owner_name_from_document_uid(monkeypatch, tmp_path):
    """With no cv_owner block, the owner name is derived from the second
    underscore-delimited segment of document_uid: the literal substrings
    "js" and "Cv" are stripped (line 456's ``.replace('js', '').replace('Cv',
    '')``), then the result is capitalized, and threaded into the
    generation prompt.

    "JanejsCv" contains "Jane" as a substring even WITHOUT stripping (it
    capitalizes to "Janejscv"), so merely checking ``"Jane" in prompt``
    passes on both the correct and the buggy (unstripped) behaviour and
    proves nothing. Instead assert the exact rendered clause "for Jane."
    (name immediately followed by the sentence's period) is present, and
    that the unstripped form "Janejscv" is absent -- only the intended
    strip-then-capitalize route produces exactly "Jane"."""
    captured = {}

    def fake_call_llm(*, messages, max_tokens, **_kwargs):
        if max_tokens == 200:
            raise AssertionError("no M1 content -- scoring must not run")
        captured["prompt"] = messages[0]["content"]
        return {
            "content": "Summary text.",
            "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost": 0.0,
        }
    monkeypatch.setattr(stage_4_5, "call_llm", fake_call_llm)

    inp = _write_fields_json(
        tmp_path, "2015_JanejsCv_extra",
        [{"taxonomy_code": "S1", "text": "A paper",
          "extracted_fields": {"authors": "Doe J", "title": "A Paper", "journal": "Nature", "year": "2020"}}],
    )
    outp = tmp_path / "out.json"

    run_stage_4_5(str(inp), str(outp), verbose=False)

    assert "for Jane." in captured["prompt"]
    assert "Janejscv" not in captured["prompt"]


def test_run_stage_4_5_raises_file_not_found_for_missing_input(monkeypatch, tmp_path):
    def fake_call_llm(**_kwargs):
        raise AssertionError("call_llm must not be reached for a missing input file")
    monkeypatch.setattr(stage_4_5, "call_llm", fake_call_llm)

    missing_output = tmp_path / "should_not_be_created.json"

    try:
        run_stage_4_5("__no_such_input_704_w3__", str(missing_output), verbose=False)
        raised = False
    except FileNotFoundError as exc:
        raised = True
        assert "__no_such_input_704_w3__" in str(exc)

    assert raised is True
    assert not missing_output.exists()
