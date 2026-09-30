"""Tests for the hoisted stage 3b classification system prompt (#522).

classify_entries_batch() used to build its LLM system prompt as an inline
f-string, ~775 lines long, which inflated the function against the
function-size ratchet (scripts/check_function_size.py). The prompt text is
now a module-level ``str.format()`` template so the function body stays
short; these tests guard both the substitution behaviour and the
regression this hoist was meant to fix.
"""
import ast
import string
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage_3b_entry_classifier import (
    _CLASSIFICATION_SYSTEM_PROMPT_TEMPLATE,
)

import pytest

# Inclusive: a function at exactly this many lines is still within budget.
_MAX_CLASSIFY_ENTRIES_BATCH_LINES = 250


def test_template_substitutes_both_placeholders():
    rendered = _CLASSIFICATION_SYSTEM_PROMPT_TEMPLATE.format(
        context_str="CTX_MARKER_XYZ", taxonomy_ref="TAX_MARKER_XYZ"
    )
    assert "CTX_MARKER_XYZ" in rendered
    assert "TAX_MARKER_XYZ" in rendered
    assert "{context_str}" not in rendered
    assert "{taxonomy_ref}" not in rendered


def test_template_escapes_json_braces_for_str_format():
    rendered = _CLASSIFICATION_SYSTEM_PROMPT_TEMPLATE.format(
        context_str="CTX_MARKER_XYZ", taxonomy_ref="TAX_MARKER_XYZ"
    )
    assert '{"classifications"' in rendered


def test_template_has_expected_placeholders_only():
    """Guards the str.format() contract itself: if an edit to the template
    text accidentally introduces/removes a `{placeholder}`, classify_entries_batch's
    .format(context_str=..., taxonomy_ref=...) call would raise (extra
    placeholder) or silently leave a stray "{foo}" in the LLM prompt (typo'd
    placeholder name) -- catch it here instead."""
    formatter = string.Formatter()
    fields = {
        name
        for _, name, _, _ in formatter.parse(_CLASSIFICATION_SYSTEM_PROMPT_TEMPLATE)
        if name
    }
    assert fields == {"context_str", "taxonomy_ref"}


def test_template_preserves_literal_braces_in_substituted_values():
    # str.format() does not re-scan substituted values for placeholders, but
    # this pins that contract so a future switch to e.g. chained .format()
    # calls or % formatting can't silently start mangling brace-containing
    # context/taxonomy text.
    rendered = _CLASSIFICATION_SYSTEM_PROMPT_TEMPLATE.format(
        context_str="Suggested: {S1: 0.8}", taxonomy_ref="{not a placeholder}"
    )
    assert "Suggested: {S1: 0.8}" in rendered
    assert "{not a placeholder}" in rendered


def test_classify_entries_batch_is_under_ratchet_line_budget():
    # classify_entries_batch lives in stage3b/classify.py since the #522 split.
    module_path = _SRC / "unified_pipeline" / "stage3b" / "classify.py"
    tree = ast.parse(module_path.read_text())
    func = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "classify_entries_batch"
        ),
        None,
    )
    assert func is not None, (
        "classify_entries_batch not found in stage3b/classify.py -- "
        "was it renamed or moved? Update this test's target if so."
    )
    span = func.end_lineno - func.lineno + 1
    assert span <= _MAX_CLASSIFY_ENTRIES_BATCH_LINES, (
        f"classify_entries_batch is {span} lines, expected at most "
        f"{_MAX_CLASSIFY_ENTRIES_BATCH_LINES}"
    )


def test_classify_entries_batch_actually_uses_the_hoisted_template(monkeypatch):
    """The tests above validate the template in isolation -- they'd still pass
    even if classify_entries_batch built its own prompt string and never
    touched _CLASSIFICATION_SYSTEM_PROMPT_TEMPLATE. Exercise the real
    function (LLM call mocked out) and assert the system message it sends
    is exactly the template rendered with the same values."""
    import unified_pipeline.stage_3b_entry_classifier as stage_3b
    # call_llm is stubbed at the module that actually calls it: the #522 split
    # moved every classification call site into stage3b/classify.py, so patching
    # the facade's copy would no longer intercept anything (#496).
    import unified_pipeline.stage3b.classify as stage3b_classify

    captured = {}

    def fake_call_llm(**kwargs):
        captured["messages"] = kwargs["messages"]
        return {
            "content": '{"classifications": []}',
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cost": 0.0,
            "model": "test-model",
        }

    monkeypatch.setattr(stage3b_classify, "call_llm", fake_call_llm)

    context = stage_3b.TaxonomyContext()
    entries = [{"text": "Associate Professor of Medicine, 2020-present", "hierarchy": ["Positions"]}]

    stage_3b.classify_entries_batch(entries, context, taxonomy={})

    system_message = next(m["content"] for m in captured["messages"] if m["role"] == "system")
    expected = _CLASSIFICATION_SYSTEM_PROMPT_TEMPLATE.format(
        context_str=context.format_context_string(),
        taxonomy_ref="",
    )
    assert system_message == expected


def test_classify_entries_batch_renders_real_taxonomy_reference(monkeypatch):
    """The taxonomy={} case above only proves {taxonomy_ref} can render as an
    empty string -- it would still pass even if build_taxonomy_codes_for_prompt()
    were bypassed entirely. Exercise a taxonomy with real codes and assert the
    system message contains the exact rendered taxonomy reference."""
    import unified_pipeline.stage_3b_entry_classifier as stage_3b
    import unified_pipeline.stage3b.classify as stage3b_classify

    captured = {}

    def fake_call_llm(**kwargs):
        captured["messages"] = kwargs["messages"]
        return {
            "content": '{"classifications": []}',
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cost": 0.0,
            "model": "test-model",
        }

    monkeypatch.setattr(stage3b_classify, "call_llm", fake_call_llm)

    taxonomy = {
        "codes": [
            {"code": "S1", "label": "Original Research", "purpose": "Peer-reviewed empirical work"},
            {"code": "H", "label": "Honors and Awards", "purpose": "One-time recognitions"},
        ]
    }
    context = stage_3b.TaxonomyContext()
    entries = [{"text": "Best Paper Award, 2021", "hierarchy": ["Honors"]}]

    stage_3b.classify_entries_batch(entries, context, taxonomy=taxonomy)

    system_message = next(m["content"] for m in captured["messages"] if m["role"] == "system")
    expected_taxonomy_ref = stage_3b.build_taxonomy_codes_for_prompt(taxonomy)
    assert "S1: Original Research" in expected_taxonomy_ref
    assert "H: Honors and Awards" in expected_taxonomy_ref
    expected = _CLASSIFICATION_SYSTEM_PROMPT_TEMPLATE.format(
        context_str=context.format_context_string(),
        taxonomy_ref=expected_taxonomy_ref,
    )
    assert system_message == expected


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


def _rule_block(start_marker: str, end_marker: str) -> str:
    text = _CLASSIFICATION_SYSTEM_PROMPT_TEMPLATE
    start = text.index(start_marker)
    return text[start:text.index(end_marker, start)]


def test_s7_rules_mean_in_review_only_matching_its_template_header():
    """#1166: stage 6 renders S7 under the WCM template's "In review"
    header, and taxonomy_v7.json defines S7 as not-yet-accepted
    manuscripts. The rules once also sent gray literature, preprints and
    op-eds to S7, so a published dissertation rendered as "In review"."""
    for s7 in (_rule_block("   - S7 = ", "   - Q2 = "),
               _rule_block("    - S7 = ", "31. CASE REPORTS")):
        assert "under review" in s7
        for gray in ("White papers", "policy briefs", "Preprints (arXiv"):
            assert gray not in s7
    s5 = _rule_block("   - S5 = ", "   - S7 = ")
    for gray in ("White papers", "Preprints", "dissertation"):
        assert gray in s5
    assert "or S7 (gray lit)" not in _CLASSIFICATION_SYSTEM_PROMPT_TEMPLATE
    # The A/B on #1173 left 28 "in press" entries in S7 while only the
    # section-6 summary said otherwise; rule 30 must say it too.
    assert '"in press"' in _rule_block("    - S7 = ", "31. CASE REPORTS")
