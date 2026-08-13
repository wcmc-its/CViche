"""Tests for the hoisted stage 3b classification system prompt (#522).

classify_entries_batch() used to build its LLM system prompt as an inline
f-string, ~775 lines long, which inflated the function against the
function-size ratchet (scripts/check_function_size.py). The prompt text is
now a module-level ``str.format()`` template so the function body stays
short; these tests guard both the substitution behaviour and the
regression this hoist was meant to fix.
"""
import ast
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


def test_classify_entries_batch_is_under_ratchet_line_budget():
    module_path = _SRC / "unified_pipeline" / "stage_3b_entry_classifier.py"
    tree = ast.parse(module_path.read_text())
    func = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "classify_entries_batch"
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

    monkeypatch.setattr(stage_3b, "call_llm", fake_call_llm)

    context = stage_3b.TaxonomyContext()
    entries = [{"text": "Associate Professor of Medicine, 2020-present", "hierarchy": ["Positions"]}]

    stage_3b.classify_entries_batch(entries, context, taxonomy={})

    system_message = next(m["content"] for m in captured["messages"] if m["role"] == "system")
    expected = _CLASSIFICATION_SYSTEM_PROMPT_TEMPLATE.format(
        context_str=context.format_context_string(),
        taxonomy_ref="",
    )
    assert system_message == expected


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
