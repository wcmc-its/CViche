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

_MAX_CLASSIFY_ENTRIES_BATCH_LINES = 250


def test_template_substitutes_both_placeholders():
    rendered = _CLASSIFICATION_SYSTEM_PROMPT_TEMPLATE.format(
        context_str="CTX_MARKER_XYZ", taxonomy_ref="TAX_MARKER_XYZ"
    )
    assert "CTX_MARKER_XYZ" in rendered
    assert "TAX_MARKER_XYZ" in rendered
    assert "{context_str}" not in rendered
    assert "{taxonomy_ref}" not in rendered


def test_template_json_example_renders_with_single_braces():
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
    assert span < _MAX_CLASSIFY_ENTRIES_BATCH_LINES, (
        f"classify_entries_batch is {span} lines, expected under "
        f"{_MAX_CLASSIFY_ENTRIES_BATCH_LINES}"
    )


if __name__ == "__main__":
    test_template_substitutes_both_placeholders()
    test_template_json_example_renders_with_single_braces()
    test_classify_entries_batch_is_under_ratchet_line_budget()
    print("ok")
