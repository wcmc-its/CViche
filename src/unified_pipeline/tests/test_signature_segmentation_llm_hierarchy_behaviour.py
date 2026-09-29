"""Behaviour tests for the LLM-facing half of signature_based_segmentation.py (#704).

Covers: classify_signature_groups_with_llm, normalize_hierarchy_with_llm (and its
inner add_headers_to_list), parse_normalized_hierarchy (and its inner map_headers),
validate_headers_vs_entries (and its inner collect_headers), build_hierarchy_from_
classifications, and segment_cv_with_signatures end to end.

``call_llm`` is stubbed via ``monkeypatch.setattr(sbs, "call_llm", ...)`` on the
module object itself -- no network call, no prompt-log writes. The module is
imported exactly once as ``sbs`` and every function under test is invoked off
that same object, matching how the live path (chunked_chat_hierarchy_extractor.py)
loads this module by bare name via a sys.path insert.

A sibling test packet covers the signature-extraction half of this module
(extract_paragraph_signature, group_by_signature, compute_prominence_score,
rescue_locked_headers, ensure_personal_data_first, remove_duplicate_children);
those are only called here as setup, never asserted on directly.

Untestable without a real network call or corpus: the ``if __name__ == "__main__"``
CLI block (argparse-free but exit()-driven) at the bottom of the module.
"""

import json
import logging
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest
from docx import Document

from unified_pipeline.segmentation import signature_based_segmentation as sbs


# --------------------------------------------------------------------------- helpers

def _fmt_sig(**overrides: object) -> sbs.FormatSignature:
    defaults = dict(
        font_family="Calibri", font_size_pt=12.0, bold=False, italic=False,
        underline="none", all_caps=False, small_caps=False, color="#000000",
        alignment="left", indent_left_in=0.0, indent_right_in=0.0, line_spacing=1.0,
        space_before_pt=0.0, space_after_pt=0.0, has_border_top=False,
        has_border_bottom=False, has_border_left=False, has_border_right=False,
        border_top_width_pt=0.0, border_bottom_width_pt=0.0, background_color=None,
        style_name="Normal", has_mixed_formatting=False,
    )
    defaults.update(overrides)
    return sbs.FormatSignature(**defaults)


def _para(
    text: str, idx: int, total: int, sig: sbs.FormatSignature, *,
    matches_locked: bool = False, case_type: str = "MixedCase", **extra: object,
) -> dict:
    base = {
        "paragraph_index": idx,
        "text": text,
        "text_metadata": {
            "case_type": case_type,
            "tokens": text.split(),
            "tokens_count": len(text.split()),
            "all_caps": sig.all_caps,
            "small_caps": sig.small_caps,
            "single_line": True,
            "matches_locked_header": matches_locked,
        },
        "format_signature": sig,
        "signature_hash": sig.to_hash(),
        "position_in_doc": idx / total if total else 0.0,
        "borders_detail": {"top": None, "bottom": None, "left": None, "right": None},
    }
    base.update(extra)
    return base


# ============================================================ classify_signature_groups_with_llm

def test_classify_signature_groups_with_llm_happy_path(monkeypatch):
    sig_header = _fmt_sig(bold=True, font_size_pt=16.0, all_caps=True, has_border_top=True)
    sig_body = _fmt_sig()
    header_para = _para("EDUCATION", 2, 40, sig_header, matches_locked=True, case_type="ALL_CAPS")
    body_paras = [_para(f"Body line {i}", 5 + i, 40, sig_body) for i in range(6)]
    groups = {"sig_header": [header_para], "sig_body": body_paras}

    captured = {}

    def fake_call_llm(stage, messages, response_format=None, **kwargs):
        captured["stage"] = stage
        captured["messages"] = messages
        captured["response_format"] = response_format
        return {
            "content": json.dumps({
                "classifications": [
                    {"signature_id": "sig_header", "classification": "H1",
                     "confidence": 0.97, "reasoning": "bordered, bold, all caps"},
                    {"signature_id": "sig_body", "classification": "NOT_HEADER",
                     "confidence": 0.88, "reasoning": "high count body text"},
                ]
            }),
            "total_tokens": 500,
            "cost": 0.01,
        }

    monkeypatch.setattr(sbs, "call_llm", fake_call_llm)

    result = sbs.classify_signature_groups_with_llm(groups, max_groups=100)

    assert result == {
        "sig_header": {"level": "H1", "confidence": 0.97, "reasoning": "bordered, bold, all caps"},
        "sig_body": {"level": "NOT_HEADER", "confidence": 0.88, "reasoning": "high count body text"},
    }
    assert captured["stage"] == "segmentation_signature"
    assert captured["response_format"]["json_schema"]["name"] == "signature_classification"
    user_prompt = captured["messages"][1]["content"]
    assert '"signature_id": "sig_header"' in user_prompt
    assert '"count": 6' in user_prompt


def test_classify_signature_groups_with_llm_caps_to_max_groups(monkeypatch):
    sig_top = _fmt_sig(bold=True, has_border_top=True, has_border_bottom=True,
                        font_size_pt=16.0, all_caps=True)
    sig_mid = _fmt_sig(bold=True, font_size_pt=13.0)
    sig_bottom = _fmt_sig()

    groups = {
        "top": [_para("TOP HEADER", 1, 100, sig_top, case_type="ALL_CAPS")],
        "mid": [_para("Mid Header", 3, 100, sig_mid) for _ in range(8)],
        "bottom": [_para(f"Body {i}", 10 + i, 100, sig_bottom) for i in range(40)],
    }
    assert (
        sbs.compute_prominence_score(groups["top"])
        > sbs.compute_prominence_score(groups["mid"])
        > sbs.compute_prominence_score(groups["bottom"])
    )

    seen_ids = {}

    def fake_call_llm(stage, messages, response_format=None, **kwargs):
        user_prompt = messages[1]["content"]
        json_str = user_prompt.split("format signature groups:\n\n", 1)[1]
        json_str = json_str.rsplit("\n\nDocument context:", 1)[0]
        sent_groups = json.loads(json_str)
        seen_ids["ids"] = [g["signature_id"] for g in sent_groups]
        return {
            "content": json.dumps({"classifications": [
                {"signature_id": gid, "classification": "H1" if gid == "top" else "H2",
                 "confidence": 0.9, "reasoning": "stub"}
                for gid in seen_ids["ids"]
            ]}),
            "total_tokens": 10, "cost": 0.0,
        }

    monkeypatch.setattr(sbs, "call_llm", fake_call_llm)

    result = sbs.classify_signature_groups_with_llm(groups, max_groups=2)

    assert seen_ids["ids"] == ["top", "mid"]
    assert result["top"]["level"] == "H1"
    assert result["mid"]["level"] == "H2"
    assert result["bottom"]["level"] == "NOT_HEADER"
    assert result["bottom"]["confidence"] == 0.95
    assert "Auto-classified" in result["bottom"]["reasoning"]


def test_classify_signature_groups_with_llm_malformed_json_raises(monkeypatch):
    sig = _fmt_sig()
    groups = {"only": [_para("Some text", 0, 10, sig)]}

    monkeypatch.setattr(
        sbs, "call_llm",
        lambda *a, **k: {"content": "not-json-at-all", "total_tokens": 5, "cost": 0.0},
    )

    with pytest.raises(json.JSONDecodeError):
        sbs.classify_signature_groups_with_llm(groups)


def test_classify_signature_groups_with_llm_partial_json_missing_key_raises(monkeypatch):
    sig = _fmt_sig()
    groups = {"only": [_para("Some text", 0, 10, sig)]}

    monkeypatch.setattr(
        sbs, "call_llm",
        lambda *a, **k: {
            "content": json.dumps({"classifications": [
                {"signature_id": "only", "classification": "H1"}  # missing confidence/reasoning
            ]}),
            "total_tokens": 5, "cost": 0.0,
        },
    )

    with pytest.raises(KeyError):
        sbs.classify_signature_groups_with_llm(groups)


# ============================================================ normalize_hierarchy_with_llm

def test_normalize_hierarchy_with_llm_happy_path_nests_children(monkeypatch):
    headers = [
        {"text": "Education", "level": "H1", "paragraph_index": 1, "children": []},
        {"text": "PhD Program", "level": "H1", "paragraph_index": 2, "children": []},
    ]
    captured = {}

    def fake_call_llm(stage, messages, response_format=None, **kwargs):
        captured["messages"] = messages
        return {"content": "[H1] Education\n  [H2] PhD Program\n", "total_tokens": 3, "cost": 0.0}

    monkeypatch.setattr(sbs, "call_llm", fake_call_llm)

    result = sbs.normalize_hierarchy_with_llm(headers, pass_number=1)

    assert [h["text"] for h in result] == ["Education"]
    assert result[0]["level"] == "H1"
    assert [c["text"] for c in result[0]["children"]] == ["PhD Program"]
    assert result[0]["children"][0]["level"] == "H2"
    assert result[0]["children"][0]["paragraph_index"] == 2
    assert captured["messages"][1]["content"] == "[H1] Education\n[H1] PhD Program"


def test_normalize_hierarchy_with_llm_llm_failure_falls_back_to_original(monkeypatch, caplog):
    headers = [{"text": "Grants", "level": "H1", "paragraph_index": 5, "children": []}]

    def raising_call_llm(stage, messages, response_format=None, **kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(sbs, "call_llm", raising_call_llm)

    with caplog.at_level(logging.INFO, logger=sbs.__name__):
        result = sbs.normalize_hierarchy_with_llm(headers, pass_number=1)

    assert result is headers
    assert result == [{"text": "Grants", "level": "H1", "paragraph_index": 5, "children": []}]
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert [r.getMessage() for r in errors] == ["GPT normalization failed; using original hierarchy"]
    assert errors[0].exc_info is not None


def test_normalize_hierarchy_with_llm_keeps_cv_text_out_of_info_logs(monkeypatch, caplog):
    """The hierarchy sent to and returned by the LLM is CV text: debug only,
    never at the INFO level production logs at."""
    headers = [{"text": "Zyxwv Qutsr Fellowship", "level": "H1", "paragraph_index": 1, "children": []}]

    def fake_call_llm(stage, messages, response_format=None, **kwargs):
        return {"content": "[H1] Zyxwv Qutsr Fellowship\n", "total_tokens": 3, "cost": 0.0}

    monkeypatch.setattr(sbs, "call_llm", fake_call_llm)

    with caplog.at_level(logging.INFO, logger=sbs.__name__):
        sbs.normalize_hierarchy_with_llm(headers, pass_number=1)

    assert caplog.records
    assert not any("Zyxwv" in r.getMessage() for r in caplog.records)


def test_normalize_hierarchy_with_llm_pass_number_changes_system_prompt(monkeypatch):
    headers = [{"text": "Service", "level": "H1", "paragraph_index": 0, "children": []}]
    captured_prompts = []

    def fake_call_llm(stage, messages, response_format=None, **kwargs):
        captured_prompts.append(messages[0]["content"])
        return {"content": "[H1] Service", "total_tokens": 1, "cost": 0.0}

    monkeypatch.setattr(sbs, "call_llm", fake_call_llm)

    sbs.normalize_hierarchy_with_llm(headers, pass_number=1)
    sbs.normalize_hierarchy_with_llm(headers, pass_number=2)

    assert "SYNTHETIC HEADERS (ONLY [H1])" in captured_prompts[0]
    assert "NO SYNTHETIC HEADERS" in captured_prompts[1]
    assert captured_prompts[0] != captured_prompts[1]


def test_normalize_hierarchy_with_llm_serializes_preexisting_nested_children(monkeypatch):
    """add_headers_to_list must recurse into headers that already carry children --
    only the recursive route can put the child line into the outgoing request."""
    headers = [{
        "text": "Publications", "level": "H1", "paragraph_index": 1,
        "children": [
            {"text": "Peer-Reviewed", "level": "H2", "paragraph_index": 2, "children": []},
        ],
    }]
    captured = {}

    def fake_call_llm(stage, messages, response_format=None, **kwargs):
        captured["prompt"] = messages[1]["content"]
        return {"content": messages[1]["content"], "total_tokens": 3, "cost": 0.0}

    monkeypatch.setattr(sbs, "call_llm", fake_call_llm)

    sbs.normalize_hierarchy_with_llm(headers, pass_number=1)

    assert captured["prompt"] == "[H1] Publications\n  [H2] Peer-Reviewed"


# ============================================================ parse_normalized_hierarchy

def test_parse_normalized_hierarchy_derives_level_from_indent_not_llm_tag():
    # A leading indent on line 1 would be eaten by corrected_text.strip() before
    # splitlines(), so the indented line under test must be the SECOND line --
    # only then does its leading whitespace survive to be measured.
    original = [
        {"text": "Intro", "level": "H1", "paragraph_index": 0,
         "children": [], "format_signature": {}},
        {"text": "Awards", "level": "H1", "paragraph_index": 4,
         "children": [], "format_signature": {}},
    ]
    corrected = "[H1] Intro\n  [H1] Awards"  # LLM tags Awards [H1] but indents 2 spaces

    result = sbs.parse_normalized_hierarchy(corrected, original)

    assert len(result) == 1
    assert result[0]["text"] == "Intro"
    child = result[0]["children"][0]
    assert child["text"] == "Awards"
    assert child["level"] == "H2"
    assert child["paragraph_index"] == 4


def test_parse_normalized_hierarchy_builds_nested_children():
    original = [
        {"text": "Publications", "level": "H1", "paragraph_index": 1,
         "children": [], "format_signature": {}},
        {"text": "Peer-Reviewed", "level": "H1", "paragraph_index": 2,
         "children": [], "format_signature": {}},
        {"text": "Book Chapters", "level": "H1", "paragraph_index": 3,
         "children": [], "format_signature": {}},
    ]
    corrected = "\n".join([
        "[H1] Publications",
        "  [H2] Peer-Reviewed",
        "  [H2] Book Chapters",
    ])

    result = sbs.parse_normalized_hierarchy(corrected, original)

    assert len(result) == 1
    assert result[0]["text"] == "Publications"
    assert [c["text"] for c in result[0]["children"]] == ["Peer-Reviewed", "Book Chapters"]
    assert all(c["level"] == "H2" for c in result[0]["children"])


def test_parse_normalized_hierarchy_unknown_header_becomes_synthetic():
    original = [{"text": "Peer-Reviewed", "level": "H2", "paragraph_index": 2,
                 "children": [], "format_signature": {}}]
    corrected = "[H1] PUBLICATIONS\n  [H2] Peer-Reviewed"

    result = sbs.parse_normalized_hierarchy(corrected, original)

    assert result[0]["text"] == "PUBLICATIONS"
    assert result[0]["paragraph_index"] == -1
    assert result[0]["text_metadata"] == {"synthetic": True}
    assert result[0]["children"][0]["text"] == "Peer-Reviewed"
    assert result[0]["children"][0]["paragraph_index"] == 2


def test_parse_normalized_hierarchy_empty_response_returns_empty_list():
    original = [{"text": "X", "level": "H1", "paragraph_index": 0,
                 "children": [], "format_signature": {}}]
    assert sbs.parse_normalized_hierarchy("", original) == []
    assert sbs.parse_normalized_hierarchy("   \n  \n", []) == []


def test_parse_normalized_hierarchy_skips_unmatched_lines():
    original = [{"text": "Teaching", "level": "H1", "paragraph_index": 6,
                 "children": [], "format_signature": {}}]
    corrected = "Some commentary the LLM was told not to add\n[H1] Teaching"

    result = sbs.parse_normalized_hierarchy(corrected, original)

    assert len(result) == 1
    assert result[0]["text"] == "Teaching"


def test_parse_normalized_hierarchy_maps_headers_finds_nested_originals():
    nested_original = [{
        "text": "Grants", "level": "H1", "paragraph_index": 1, "format_signature": {},
        "children": [
            {"text": "NIH R01", "level": "H2", "paragraph_index": 2,
             "format_signature": {}, "children": []},
        ],
    }]
    corrected = "[H1] Grants\n[H1] NIH R01"

    result = sbs.parse_normalized_hierarchy(corrected, nested_original)

    assert [h["text"] for h in result] == ["Grants", "NIH R01"]
    assert result[1]["paragraph_index"] == 2


def test_parse_normalized_hierarchy_fuzzy_colon_match_when_llm_adds_colon():
    """Reverse direction of the colon-fuzzy-match: here the ORIGINAL header text has
    NO trailing colon but the LLM's corrected line adds one. Only the incoming-side
    strip (``text.rstrip(':')``) makes this match -- breaking just that strip sends
    the line down the synthetic-header branch instead of resolving to the original
    paragraph_index 7."""
    original = [
        {"text": "Honors", "level": "H1", "paragraph_index": 7,
         "children": [], "format_signature": {}},
    ]
    corrected = "[H1] Honors:"  # LLM appends a colon the original text lacks

    result = sbs.parse_normalized_hierarchy(corrected, original)

    assert len(result) == 1
    assert result[0]["text"] == "Honors"  # original text preserved, not "Honors:"
    assert result[0]["paragraph_index"] == 7  # resolved via fuzzy match, not synthetic
    assert result[0].get("text_metadata") != {"synthetic": True}


def test_parse_normalized_hierarchy_blank_lines_deep_indent_and_fuzzy_colon_match():
    original = [
        {"text": "Honors:", "level": "H1", "paragraph_index": 7,
         "children": [], "format_signature": {}},
        {"text": "National Award", "level": "H2", "paragraph_index": 8,
         "children": [], "format_signature": {}},
    ]
    corrected = (
        "[H1] Honors\n"              # no colon here -- must fuzzy-match "Honors:" by stripping it
        "\n"                         # blank line must be skipped without breaking parsing
        "    [H2] National Award\n"  # 4-space indent -> H3, deeper than the H2 cutoff
    )

    result = sbs.parse_normalized_hierarchy(corrected, original)

    assert len(result) == 1
    assert result[0]["text"] == "Honors:"  # original text preserved, not the corrected line's
    assert result[0]["paragraph_index"] == 7  # matched via fuzzy colon-stripped lookup
    child = result[0]["children"][0]
    assert child["text"] == "National Award"
    assert child["level"] == "H3"  # indent=4 -> H3 even though corrected text says [H2]
    assert child["paragraph_index"] == 8


# ============================================================ validate_headers_vs_entries

def test_validate_headers_vs_entries_drops_high_entry_likelihood(monkeypatch, caplog):
    headers = [
        {"text": "EDUCATION", "level": "H1", "children": [
            {"text": "PhD in Biology, State U, 2010", "level": "H2", "children": []},
        ]},
        {"text": "Dr. Jane Smith Memorial Lecture", "level": "H1", "children": []},
    ]

    def fake_call_llm(stage, messages, response_format=None, **kwargs):
        text = (
            "Input line: [H1] EDUCATION\n"
            "Header likelihood: 95%\n"
            "Entry likelihood: 5%\n"
            "Input line: [H2] PhD in Biology, State U, 2010\n"
            "Header likelihood: 10%\n"
            "Entry likelihood: 90%\n"
            "Input line: [H1] Dr. Jane Smith Memorial Lecture\n"
            "Header likelihood: 15%\n"
            "Entry likelihood: 85%\n"
        )
        return {"content": text, "total_tokens": 20, "cost": 0.0}

    monkeypatch.setattr(sbs, "call_llm", fake_call_llm)

    with caplog.at_level(logging.INFO, logger=sbs.__name__):
        result = sbs.validate_headers_vs_entries(headers)

    assert [h["text"] for h in result] == ["EDUCATION"]
    assert result[0]["children"] == []
    # The dropped lines are entries (CV content): named at debug only.
    assert any("2 entries filtered out" in r.getMessage() for r in caplog.records)
    assert not any("Jane Smith" in r.getMessage() for r in caplog.records)


def test_validate_headers_vs_entries_keeps_rescued_headers_without_sending_them(monkeypatch):
    headers = [
        {"text": "MENTORING", "level": "H1", "is_rescued_locked_header": True, "children": []},
        {"text": "Some Entry Line", "level": "H1", "children": []},
    ]
    captured = {}

    def fake_call_llm(stage, messages, response_format=None, **kwargs):
        captured["prompt"] = messages[1]["content"]
        return {
            "content": (
                "Input line: [H1] Some Entry Line\n"
                "Header likelihood: 20%\n"
                "Entry likelihood: 80%\n"
            ),
            "total_tokens": 5, "cost": 0.0,
        }

    monkeypatch.setattr(sbs, "call_llm", fake_call_llm)

    result = sbs.validate_headers_vs_entries(headers)

    assert "MENTORING" not in captured["prompt"]
    assert [h["text"] for h in result] == ["MENTORING"]


def test_validate_headers_vs_entries_empty_input_short_circuits(monkeypatch):
    calls = []
    monkeypatch.setattr(
        sbs, "call_llm",
        lambda *a, **k: calls.append(1) or {"content": "", "total_tokens": 0, "cost": 0.0},
    )

    result = sbs.validate_headers_vs_entries([])

    assert result == []
    assert calls == []


def test_validate_headers_vs_entries_llm_failure_returns_unfiltered_headers(monkeypatch, caplog):
    headers = [{"text": "SERVICE", "level": "H1", "children": []}]

    def raising_call_llm(stage, messages, response_format=None, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(sbs, "call_llm", raising_call_llm)

    with caplog.at_level(logging.INFO, logger=sbs.__name__):
        result = sbs.validate_headers_vs_entries(headers)

    assert result is headers
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert [r.getMessage() for r in errors] == ["Header validation failed; using unfiltered headers"]
    assert errors[0].exc_info is not None


def test_validate_headers_vs_entries_rescued_header_with_children_recurses(monkeypatch):
    """A rescued parent is skipped, but collect_headers/filter_headers must still recurse
    into its (non-rescued) children -- proved by the child actually reaching the LLM
    and actually being dropped per its verdict."""
    headers = [{
        "text": "TEACHING and MENTORING", "level": "H1",
        "is_rescued_locked_header": True,
        "children": [{"text": "Course Instruction", "level": "H2", "children": []}],
    }]
    captured = {}

    def fake_call_llm(stage, messages, response_format=None, **kwargs):
        captured["prompt"] = messages[1]["content"]
        return {
            "content": (
                "Input line: [H2] Course Instruction\n"
                "Header likelihood: 20%\n"
                "Entry likelihood: 80%\n"
            ),
            "total_tokens": 5, "cost": 0.0,
        }

    monkeypatch.setattr(sbs, "call_llm", fake_call_llm)

    result = sbs.validate_headers_vs_entries(headers)

    assert "TEACHING and MENTORING" not in captured["prompt"]
    assert "Course Instruction" in captured["prompt"]
    assert result[0]["text"] == "TEACHING and MENTORING"
    assert result[0]["children"] == []  # child dropped: entry_likelihood 80% > 60


def test_validate_headers_vs_entries_keeps_header_when_no_likelihood_found(monkeypatch):
    headers = [{"text": "AWARDS", "level": "H1", "children": []}]

    def fake_call_llm(stage, messages, response_format=None, **kwargs):
        # A bracket line with no "Input line:" prefix and no likelihood lines at all.
        return {"content": "[H1] AWARDS\n", "total_tokens": 3, "cost": 0.0}

    monkeypatch.setattr(sbs, "call_llm", fake_call_llm)

    result = sbs.validate_headers_vs_entries(headers)

    assert [h["text"] for h in result] == ["AWARDS"]  # no likelihood data -> kept by default
    # The except-fallback route (`return headers`) would satisfy the assertion above
    # too -- it also "keeps" AWARDS by returning the input unchanged. Only the intended
    # filter_headers/remove_duplicate_children route rebuilds fresh dict/list objects
    # (`{**header}` copies), so identity is what actually discriminates the two routes.
    assert result is not headers
    assert result[0] is not headers[0]


def test_validate_headers_vs_entries_malformed_percentage_and_tag_lines_are_ignored(monkeypatch):
    headers = [{"text": "PATENTS", "level": "H1", "children": []}]

    def fake_call_llm(stage, messages, response_format=None, **kwargs):
        # "[X9]" doesn't match the \[H[123]\] tag regex; "N/A" never matches (\d+)%.
        text = "[X9] PATENTS\nHeader likelihood: N/A\nEntry likelihood: N/A\n"
        return {"content": text, "total_tokens": 5, "cost": 0.0}

    monkeypatch.setattr(sbs, "call_llm", fake_call_llm)

    result = sbs.validate_headers_vs_entries(headers)

    assert [h["text"] for h in result] == ["PATENTS"]  # no usable likelihood -> kept by default
    # Same discrimination as the sibling "no likelihood found" test above: the
    # except-fallback route also "keeps" PATENTS by returning `headers` verbatim, so
    # only the object identity proves the parse/filter route actually ran.
    assert result is not headers
    assert result[0] is not headers[0]


def test_validate_headers_vs_entries_bare_bracket_line_with_usable_likelihood_is_dropped(monkeypatch):
    """The secondary parse arm (`line.startswith('[')`, no 'Input line:' prefix) must be
    exercised with a PARSEABLE percentage, not just the no-likelihood-found case covered
    by the sibling tests above. Those two tests are satisfied by the no-likelihood
    default-keep path whether or not this arm exists at all; only a usable percentage
    on a bare bracket line (no 'Input line:' prefix) proves the arm actually parses."""
    headers = [{"text": "AWARDS", "level": "H1", "children": []}]

    def fake_call_llm(stage, messages, response_format=None, **kwargs):
        # No "Input line:" prefix here -- must be picked up by the bare "[" branch.
        return {
            "content": "[H1] AWARDS\nHeader likelihood: 10%\nEntry likelihood: 90%\n",
            "total_tokens": 5, "cost": 0.0,
        }

    monkeypatch.setattr(sbs, "call_llm", fake_call_llm)

    result = sbs.validate_headers_vs_entries(headers)

    # Dropped: entry likelihood 90% > 60 threshold -- reachable only if the bare-"["
    # line was actually matched and its percentages actually parsed.
    assert result == []


def test_validate_headers_vs_entries_boundary_60_percent_is_kept(monkeypatch):
    """The documented threshold is 'entry_likelihood > 60%' (strictly greater). A
    verdict landing exactly on 60% must be KEPT; only an off-by-one (`>= 60`) would
    drop it, and none of the other fixtures exercise this exact boundary value."""
    headers = [{"text": "SOCIETIES", "level": "H1", "children": []}]

    def fake_call_llm(stage, messages, response_format=None, **kwargs):
        return {
            "content": (
                "Input line: [H1] SOCIETIES\n"
                "Header likelihood: 40%\n"
                "Entry likelihood: 60%\n"
            ),
            "total_tokens": 5, "cost": 0.0,
        }

    monkeypatch.setattr(sbs, "call_llm", fake_call_llm)

    result = sbs.validate_headers_vs_entries(headers)

    assert [h["text"] for h in result] == ["SOCIETIES"]  # exactly 60% is NOT > 60 -> kept


def test_validate_headers_vs_entries_wires_remove_duplicate_children(monkeypatch):
    """validate_headers_vs_entries calls remove_duplicate_children on its filtered
    result (this packet's wire, not the helper's own logic, which the sibling packet
    owns). A child sharing its parent's paragraph_index must vanish from the final
    result -- only reachable if that call actually ran."""
    headers = [{
        "text": "SECTION A", "level": "H1", "paragraph_index": 3,
        "children": [
            {"text": "Same Para Child", "level": "H2", "paragraph_index": 3, "children": []},
        ],
    }]

    monkeypatch.setattr(
        sbs, "call_llm",
        # No usable "Input line:"/likelihood pairs at all -> both headers default-keep
        # in filter_headers, so only remove_duplicate_children can drop the child.
        lambda *a, **k: {"content": "", "total_tokens": 0, "cost": 0.0},
    )

    result = sbs.validate_headers_vs_entries(headers)

    assert [h["text"] for h in result] == ["SECTION A"]
    assert result[0]["children"] == []  # duplicate child (same paragraph_index) removed


def test_validate_headers_vs_entries_fuzzy_colon_match_recovers_likelihood(monkeypatch):
    headers = [{"text": "Honors and Awards", "level": "H1", "children": []}]

    def fake_call_llm(stage, messages, response_format=None, **kwargs):
        # A decoy entry first (so the fuzzy loop must skip a non-matching key), then
        # the real one echoed back WITH a trailing colon the original text lacks --
        # exact lookup must fail and fall through to the colon-stripped fuzzy match.
        text = (
            "Input line: [H1] Some Other Header:\n"
            "Header likelihood: 50%\n"
            "Entry likelihood: 50%\n"
            "Input line: [H1] Honors and Awards:\n"
            "Header likelihood: 10%\n"
            "Entry likelihood: 90%\n"
        )
        return {"content": text, "total_tokens": 5, "cost": 0.0}

    monkeypatch.setattr(sbs, "call_llm", fake_call_llm)

    result = sbs.validate_headers_vs_entries(headers)

    assert result == []  # dropped via the fuzzy match, not the (failing) exact match


def test_build_hierarchy_from_classifications_rescued_paragraph_defaults_confidence_and_reasoning():
    """When a rescued paragraph omits classification_confidence/classification_reasoning,
    build_hierarchy_from_classifications must fall back to its own defaults (0.90 and
    'Rescued header') -- the earlier rescued-paragraph test always supplied both fields
    explicitly, so the defaults themselves were never exercised or asserted on."""
    sig = _fmt_sig()
    rescued_para = _para(
        "MENTORING", 3, 10, sig,
        classification="H1",
        is_rescued_locked_header=True,
        # classification_confidence / classification_reasoning deliberately omitted
    )
    groups = {"g_not_header": [rescued_para]}
    classifications = {"g_not_header": {"level": "NOT_HEADER", "confidence": 0.5, "reasoning": "mostly body"}}

    hierarchy = sbs.build_hierarchy_from_classifications(groups, classifications)

    assert [h["text"] for h in hierarchy] == ["MENTORING"]
    assert hierarchy[0]["classification_confidence"] == 0.90
    assert hierarchy[0]["classification_reasoning"] == "Rescued header"


# ============================================================ build_hierarchy_from_classifications

def test_build_hierarchy_from_classifications_orders_and_nests():
    sig = _fmt_sig()
    groups = {
        "g_h2": [_para("Peer-Reviewed", 5, 20, sig)],
        "g_h1": [_para("Publications", 2, 20, sig)],
        "g_h3": [_para("2020", 6, 20, sig)],
    }
    classifications = {
        "g_h1": {"level": "H1", "confidence": 0.9, "reasoning": "top header"},
        "g_h2": {"level": "H2", "confidence": 0.8, "reasoning": "sub header"},
        "g_h3": {"level": "H3", "confidence": 0.7, "reasoning": "sub-sub header"},
    }

    hierarchy = sbs.build_hierarchy_from_classifications(groups, classifications)

    assert len(hierarchy) == 1
    assert hierarchy[0]["text"] == "Publications"
    assert hierarchy[0]["classification_confidence"] == 0.9
    assert [c["text"] for c in hierarchy[0]["children"]] == ["Peer-Reviewed"]
    grandchild = hierarchy[0]["children"][0]["children"][0]
    assert grandchild["text"] == "2020"
    assert grandchild["level"] == "H3"


def test_build_hierarchy_from_classifications_new_h1_resets_the_open_h2():
    """A(H1) B(H2) C(H1) D(H3): D belongs under C, not under B. Without the
    `current_h2 = None` reset when C opens, D would nest under the stale B."""
    sig = _fmt_sig()
    groups = {
        "g_a": [_para("A", 0, 20, sig)],
        "g_b": [_para("B", 1, 20, sig)],
        "g_c": [_para("C", 2, 20, sig)],
        "g_d": [_para("D", 3, 20, sig)],
    }
    classifications = {
        "g_a": {"level": "H1", "confidence": 0.9, "reasoning": "top"},
        "g_b": {"level": "H2", "confidence": 0.8, "reasoning": "sub"},
        "g_c": {"level": "H1", "confidence": 0.9, "reasoning": "top"},
        "g_d": {"level": "H3", "confidence": 0.7, "reasoning": "sub-sub"},
    }

    hierarchy = sbs.build_hierarchy_from_classifications(groups, classifications)

    assert [h["text"] for h in hierarchy] == ["A", "C"]
    a, c = hierarchy
    assert [x["text"] for x in a["children"]] == ["B"]
    assert a["children"][0]["children"] == []
    assert [x["text"] for x in c["children"]] == ["D"]


def test_build_hierarchy_from_classifications_orphan_h2_promoted_to_top():
    sig = _fmt_sig()
    groups = {"g": [_para("Orphan Section", 0, 10, sig)]}
    classifications = {"g": {"level": "H2", "confidence": 0.5, "reasoning": "no parent yet"}}

    hierarchy = sbs.build_hierarchy_from_classifications(groups, classifications)

    assert len(hierarchy) == 1
    assert hierarchy[0]["text"] == "Orphan Section"
    assert hierarchy[0]["level"] == "H2"
    assert hierarchy[0]["children"] == []


def test_build_hierarchy_from_classifications_orphan_h3_promoted_to_top():
    sig = _fmt_sig()
    groups = {"g": [_para("Orphan Sub-sub", 0, 10, sig)]}
    classifications = {"g": {"level": "H3", "confidence": 0.5, "reasoning": "no parent"}}

    hierarchy = sbs.build_hierarchy_from_classifications(groups, classifications)

    assert len(hierarchy) == 1
    assert hierarchy[0]["text"] == "Orphan Sub-sub"
    assert hierarchy[0]["level"] == "H3"


def test_build_hierarchy_from_classifications_includes_rescued_and_drops_unclassified():
    sig = _fmt_sig()
    rescued_para = _para(
        "TEACHING and MENTORING", 3, 10, sig,
        classification="H1",
        classification_confidence=0.9,
        classification_reasoning="Rescued: Matches known CV section header",
        is_rescued_locked_header=True,
    )
    plain_body_para = _para("Just body text", 4, 10, sig)
    groups = {
        "g_not_header": [rescued_para, plain_body_para],
        "g_unclassified": [_para("Never classified", 5, 10, sig)],
    }
    classifications = {
        "g_not_header": {"level": "NOT_HEADER", "confidence": 0.6, "reasoning": "mostly body"},
        # "g_unclassified" deliberately absent
    }

    hierarchy = sbs.build_hierarchy_from_classifications(groups, classifications)

    assert [h["text"] for h in hierarchy] == ["TEACHING and MENTORING"]
    assert hierarchy[0]["is_rescued_locked_header"] is True


def test_build_hierarchy_from_classifications_ignores_unrecognized_level_value():
    """A classification level outside {H1,H2,H3,NOT_HEADER} matches neither branch --
    the group is silently dropped, not defaulted to a header."""
    sig = _fmt_sig()
    groups = {"g": [_para("Weird Group", 0, 10, sig)]}
    classifications = {"g": {"level": "MAYBE_HEADER", "confidence": 0.5, "reasoning": "unexpected value"}}

    hierarchy = sbs.build_hierarchy_from_classifications(groups, classifications)

    assert hierarchy == []


def test_build_hierarchy_from_classifications_h3_nests_directly_under_h1_when_no_h2():
    sig = _fmt_sig()
    groups = {
        "g1": [_para("Grants", 0, 10, sig)],
        "g2": [_para("NIH R01 2020", 1, 10, sig)],
    }
    classifications = {
        "g1": {"level": "H1", "confidence": 0.9, "reasoning": "top"},
        "g2": {"level": "H3", "confidence": 0.7, "reasoning": "no H2 between"},
    }

    hierarchy = sbs.build_hierarchy_from_classifications(groups, classifications)

    assert len(hierarchy) == 1
    assert hierarchy[0]["text"] == "Grants"
    assert [c["text"] for c in hierarchy[0]["children"]] == ["NIH R01 2020"]
    assert hierarchy[0]["children"][0]["level"] == "H3"


# ============================================================ segment_cv_with_signatures (end to end)

def _build_synthetic_cv_docx(path: Path) -> None:
    """3 header levels + a blank paragraph (must be filtered, never counted as a header)."""
    doc = Document()
    doc.add_paragraph("Jane Researcher")
    doc.add_paragraph("")  # blank paragraph: must be dropped, not treated as a header
    doc.add_paragraph("PhD in Biology, State University, 2010")

    edu = doc.add_paragraph()
    edu.add_run("EDUCATION").bold = True  # bold + all-caps text -> H1

    doc.add_paragraph("MD, Example Medical School, 2005")

    pub = doc.add_paragraph()
    run = pub.add_run("PUBLICATIONS")
    run.bold = True
    run.italic = True  # bold + italic + all-caps text -> H1

    peer = doc.add_paragraph()
    peer_run = peer.add_run("Peer-Reviewed")
    peer_run.bold = True
    peer_run.italic = True  # bold + italic, mixed case (not all-caps) -> H2

    orig = doc.add_paragraph()
    orig.add_run("Original Articles").bold = True  # bold only -> H3

    doc.add_paragraph("Smith J. A Paper About Something. Journal X. 2020.")
    doc.save(path)


def _make_end_to_end_call_llm_stub():
    """Dispatches across the module's three call_llm sites by request shape.

    Classification is driven off the FORMAT flags in each group summary (the way the
    real prompt instructs the LLM to decide), not off example text -- so this proves
    the request actually carries usable formatting, not just labels.

    validate_headers_vs_entries and normalize_hierarchy_with_llm (both passes) each
    make a targeted, verifiable change (dropping EDUCATION; regrouping "Original
    Articles" in pass 1; appending a brand-new synthetic "SERVICE" top-level header in
    pass 2) instead of a pure keep-everything/echo. A pure echo+keep-everything stub is
    indistinguishable in the final JSON from that call site's except-fallback route
    (`return headers` unchanged) -- or, for pass 2, from Step 9's call being skipped
    entirely -- these deliberate deltas are what let the e2e assertions prove each
    route actually ran, not just that call_llm was invoked.
    """

    def fake_call_llm(stage, messages, response_format=None, **kwargs):
        system_prompt = messages[0]["content"]
        user_prompt = messages[1]["content"]

        if response_format is not None:  # classify_signature_groups_with_llm
            json_str = user_prompt.split("format signature groups:\n\n", 1)[1]
            json_str = json_str.rsplit("\n\nDocument context:", 1)[0]
            groups = json.loads(json_str)
            classifications = []
            for g in groups:
                fmt = g["format"]
                if fmt["all_caps"] and fmt["bold"]:
                    level = "H1"
                elif fmt["bold"] and fmt["italic"]:
                    level = "H2"
                elif fmt["bold"]:
                    level = "H3"
                else:
                    level = "NOT_HEADER"
                classifications.append({"signature_id": g["signature_id"], "classification": level,
                                         "confidence": 0.9, "reasoning": "stub classification"})
            return {"content": json.dumps({"classifications": classifications}),
                    "total_tokens": 42, "cost": 0.001}

        if "expert classifier" in system_prompt:  # validate_headers_vs_entries
            # Drop EDUCATION (entry likelihood 95% > 60 threshold), keep everything
            # else -- a route probe that forces this call site's try body to raise
            # (falling back to `return headers` unfiltered) leaves EDUCATION in the
            # hierarchy, so the e2e assertions on its absence go red under that probe.
            lines_out = []
            for line in user_prompt.splitlines():
                if line.startswith("[H"):
                    is_education = line.strip() == "[H1] EDUCATION"
                    lines_out.append(f"Input line: {line}")
                    lines_out.append("Header likelihood: 5%" if is_education else "Header likelihood: 100%")
                    lines_out.append("Entry likelihood: 95%" if is_education else "Entry likelihood: 0%")
            return {"content": "\n".join(lines_out), "total_tokens": 10, "cost": 0.0}

        if "SYNTHETIC HEADERS (ONLY [H1])" in system_prompt:  # normalize pass 1
            # Demote "Original Articles" from an H3 nested under Peer-Reviewed to an
            # H2 sibling under PUBLICATIONS. Only the real parse_normalized_hierarchy
            # route can produce this restructuring -- a silent except-fallback here
            # (`return headers` unchanged) would leave it at H3, so the written JSON
            # discriminates the two.
            lines_out = [
                "  [H2] Original Articles" if line.strip() == "[H3] Original Articles" else line
                for line in user_prompt.splitlines()
            ]
            return {"content": "\n".join(lines_out), "total_tokens": 10, "cost": 0.0}

        if "NO SYNTHETIC HEADERS" in system_prompt:  # normalize pass 2 (Step 9)
            # Append a brand-new top-level synthetic header that cannot come from any
            # other call site or from pass 1's input. If Step 9's normalize call were
            # skipped (hierarchy left as pass 1 produced it), "SERVICE" would never
            # appear and top_level_sections would stay at pass 1's count -- this is
            # pass 2's own route signature, distinct from a no-op echo.
            return {"content": user_prompt + "\n[H1] SERVICE", "total_tokens": 10, "cost": 0.0}

        raise AssertionError(f"unrecognized call_llm invocation: {system_prompt[:60]!r}")

    return fake_call_llm


def test_segment_cv_with_signatures_end_to_end(tmp_path, monkeypatch):
    docx_path = tmp_path / "cv.docx"
    _build_synthetic_cv_docx(docx_path)
    monkeypatch.setattr(sbs, "call_llm", _make_end_to_end_call_llm_stub())

    output_path = tmp_path / "out.json"
    result = sbs.segment_cv_with_signatures(str(docx_path), str(output_path))

    assert result["document_uid"] == "cv"
    assert result["meta"]["total_paragraphs"] == 8  # 9 raw paragraphs minus the 1 blank
    # unique_signatures counts distinct FORMAT groups, computed before classification
    # or filtering ever run: 1 plain-body group + EDUCATION (bold+all-caps) +
    # PUBLICATIONS (bold+italic+all-caps) + Peer-Reviewed (bold+italic, mixed case) +
    # Original Articles (bold only, mixed case) = 5 distinct signatures.
    assert result["meta"]["unique_signatures"] == 5
    assert result["meta"]["total_headers"] == 4  # EDUCATION, PUBLICATIONS, Peer-Reviewed, Original Articles
    # top_level_sections is 3, not 2: EDUCATION is dropped by validate_headers_vs_entries
    # (see the stub), and pass 2 (Step 9) appends a brand-new synthetic "SERVICE" top
    # level header -- total_headers above is computed from pre-validate/pre-normalize
    # classifications and stays 4, so this is the only meta field pass 2 moves.
    assert result["meta"]["top_level_sections"] == 3  # synthetic PERSONAL DATA + PUBLICATIONS + SERVICE

    top_texts = [h["text"] for h in result["hierarchy"]]
    # EDUCATION's absence here is the validate_headers_vs_entries route signature: the
    # except-fallback route would have kept it (see stub docstring / test file header).
    # "SERVICE" trailing here is pass 2's own route signature: if Step 9's call were
    # skipped, the hierarchy would stop at ["PERSONAL DATA", "PUBLICATIONS"].
    assert top_texts == ["PERSONAL DATA", "PUBLICATIONS", "SERVICE"]
    service = result["hierarchy"][2]
    assert service["paragraph_index"] == -1  # synthetic: pass 2 introduced this header,
    assert service["text_metadata"] == {"synthetic": True}  # it has no original paragraph
    assert service["children"] == []

    pubs = result["hierarchy"][1]
    # "Original Articles" now sits as an H2 SIBLING of Peer-Reviewed (not nested under
    # it as H3) -- that regrouping is the normalize_hierarchy_with_llm pass-1 route
    # signature; the except-fallback route would have left it nested at H3.
    assert [c["text"] for c in pubs["children"]] == ["Peer-Reviewed", "Original Articles"]
    assert pubs["children"][0]["level"] == "H2"
    assert pubs["children"][1]["level"] == "H2"
    assert pubs["children"][1]["children"] == []

    saved = json.loads(output_path.read_text())
    assert saved == result

    txt_content = output_path.with_suffix(".txt").read_text()
    # Scoped to the "[H#] text" outline format written by write_hierarchy -- the
    # trailing SIGNATURE GROUPS SUMMARY section legitimately lists EVERY signature
    # group (including filtered-out EDUCATION) as a bare "- EDUCATION" example line,
    # so a bare substring check would pass regardless of whether validate ran.
    assert "[H1] EDUCATION" not in txt_content
    assert "  [H2] Original Articles" in txt_content  # regrouped, one indent level (not H3's two)
    assert "[H1] SERVICE" in txt_content  # pass 2's synthetic addition reached the write step


def test_segment_cv_with_signatures_without_output_path_uses_output_manager(monkeypatch, tmp_path):
    """output_path=None routes through OutputManager instead of a caller-given path.

    OutputManager is imported locally inside segment_cv_with_signatures on every call
    (``from ..core.output_manager import OutputManager``), so patching the class on its
    OWN module -- looked up fresh at call time -- redirects it without touching sbs.
    This keeps every write inside tmp_path, never the real repo's outputs/ directory.
    """
    from unified_pipeline.core import output_manager as om_module

    real_output_manager = om_module.OutputManager
    redirected_base = tmp_path / "om_out"
    monkeypatch.setattr(
        om_module, "OutputManager",
        lambda input_path, base_output_dir=None: real_output_manager(input_path, redirected_base),
    )

    docx_path = tmp_path / "cv2.docx"
    _build_synthetic_cv_docx(docx_path)
    monkeypatch.setattr(sbs, "call_llm", _make_end_to_end_call_llm_stub())

    result = sbs.segment_cv_with_signatures(str(docx_path), output_path=None)

    expected_json = redirected_base / "stage_1a_segmentation" / "cv2_segmented.json"
    assert expected_json.exists()
    assert json.loads(expected_json.read_text()) == result
    assert (redirected_base / "stage_1a_segmentation" / "cv2_segmented.txt").exists()

    # File-existence and disk/return-value equality alone are satisfied whether or not
    # the LLM routes actually ran (a self-consistency check, not a correctness one) --
    # this pins the same validate_headers_vs_entries AND pass-2-normalize route
    # signatures as the sibling end-to-end test (EDUCATION dropped, synthetic SERVICE
    # appended) so an OutputManager-specific regression in wiring the real hierarchy
    # through still fails here.
    top_texts = [h["text"] for h in result["hierarchy"]]
    assert top_texts == ["PERSONAL DATA", "PUBLICATIONS", "SERVICE"]


def _build_asymmetric_header_count_docx(path: Path) -> None:
    """10 non-blank paragraphs: 3 distinctly-formatted headers, 7 plain body lines.

    The UNEQUAL 3-vs-7 split matters: with a symmetric split (as in the main synthetic
    CV above, 4 headers / 4 body paragraphs) a broken total_headers computation that
    sums the wrong group set can still coincidentally land on the right number. Here
    the two possible sums (3 vs 7) are different, so only the correct computation
    passes.
    """
    doc = Document()
    alpha = doc.add_paragraph()
    alpha.add_run("ALPHA SECTION").bold = True  # bold + all-caps text -> H1

    doc.add_paragraph("First detail line about something.")
    doc.add_paragraph("Second detail line here.")

    beta = doc.add_paragraph()
    beta_run = beta.add_run("Beta Section")
    beta_run.bold = True
    beta_run.italic = True  # bold + italic, mixed case -> H2

    doc.add_paragraph("Third detail line.")
    doc.add_paragraph("Fourth detail line.")

    gamma = doc.add_paragraph()
    gamma.add_run("Gamma Item").bold = True  # bold only, mixed case -> H3

    doc.add_paragraph("Fifth detail line.")
    doc.add_paragraph("Sixth detail line.")
    doc.add_paragraph("Seventh detail line.")
    doc.save(path)


def test_segment_cv_with_signatures_total_headers_counts_only_header_groups(tmp_path, monkeypatch):
    """meta['total_headers'] sums paragraphs belonging to groups classified as a header
    level (H1/H2/H3), not groups classified NOT_HEADER. With the asymmetric 3-header /
    7-body-paragraph corpus, inverting that condition would sum the 7-paragraph body
    group instead of the three 1-paragraph header groups, producing 7 instead of 3 --
    a value this test can actually tell apart from the correct one."""
    docx_path = tmp_path / "cv_asymmetric.docx"
    _build_asymmetric_header_count_docx(docx_path)
    monkeypatch.setattr(sbs, "call_llm", _make_end_to_end_call_llm_stub())

    output_path = tmp_path / "out_asymmetric.json"
    result = sbs.segment_cv_with_signatures(str(docx_path), str(output_path))

    assert result["meta"]["total_paragraphs"] == 10
    assert result["meta"]["total_headers"] == 3


@pytest.mark.parametrize("call", ["normalize", "validate"])
def test_llm_outage_propagates_instead_of_falling_back(monkeypatch, call):
    """A provider outage past the budget fails the run (#810); the plain-error
    fallbacks are pinned by the llm_failure tests above."""
    from unified_pipeline.llm.retry import LLMOutageError

    headers = [{"text": "Grants", "level": "H1", "paragraph_index": 5, "children": []}]

    def outage(stage, messages, response_format=None, **kwargs):
        raise LLMOutageError("provider down", seconds_waited=1800.0)

    monkeypatch.setattr(sbs, "call_llm", outage)
    with pytest.raises(LLMOutageError):
        if call == "normalize":
            sbs.normalize_hierarchy_with_llm(headers, pass_number=1)
        else:
            sbs.validate_headers_vs_entries(headers)


# ============================================================ _write_hierarchy (#306)

def _recursive_write_hierarchy_reference(f, nodes, depth=0):
    """The pre-#306 nested closure, verbatim, as the byte-identity oracle."""
    for node in nodes:
        f.write(f"{'  ' * depth}[{node['level']}] {node['text']}\n")
        if node.get('children'):
            _recursive_write_hierarchy_reference(f, node['children'], depth + 1)


def test_write_hierarchy_is_byte_identical_to_recursive_original():
    import io
    hierarchy = [
        {"level": "H1", "text": "A", "children": [
            {"level": "H2", "text": "A1", "children": [{"level": "H3", "text": "A1a"}]},
            {"level": "H2", "text": "A2", "children": []},
        ]},
        {"level": "H1", "text": "B"},
    ]
    new, ref = io.StringIO(), io.StringIO()
    sbs._write_hierarchy(new, hierarchy)
    _recursive_write_hierarchy_reference(ref, hierarchy)
    assert new.getvalue() == ref.getvalue()
    assert new.getvalue().startswith("[H1] A\n  [H2] A1\n    [H3] A1a\n")


def test_write_hierarchy_deep_chain_does_not_recurse():
    import io
    root = {"level": "H1", "text": "n0", "children": []}
    tip = root
    for i in range(1, 5000):
        child = {"level": "H2", "text": f"n{i}", "children": []}
        tip["children"].append(child)
        tip = child
    buf = io.StringIO()
    sbs._write_hierarchy(buf, [root])
    assert buf.getvalue().count("\n") == 5000
