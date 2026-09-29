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
from unified_pipeline.stage3b.prompt import _CLASSIFICATION_BATCH_CONTEXT_TEMPLATE

import pytest

# Inclusive: a function at exactly this many lines is still within budget.
_MAX_CLASSIFY_ENTRIES_BATCH_LINES = 250


def test_system_prompt_is_static_and_keeps_json_example_braces() -> None:
    """#50: the system prompt carries no per-batch placeholders (a placeholder
    would make the cache prefix vary per hierarchy group), and its JSON
    example keeps single braces because it is sent verbatim, not .format()ed."""
    assert "{context_str}" not in _CLASSIFICATION_SYSTEM_PROMPT_TEMPLATE
    assert "{taxonomy_ref}" not in _CLASSIFICATION_SYSTEM_PROMPT_TEMPLATE
    assert '{"classifications"' in _CLASSIFICATION_SYSTEM_PROMPT_TEMPLATE
    assert '{{"classifications"' not in _CLASSIFICATION_SYSTEM_PROMPT_TEMPLATE


def test_batch_context_template_has_expected_placeholders_only() -> None:
    """Guards the str.format() contract of the per-batch (user message) block:
    a stray or renamed placeholder would raise or leak "{foo}" to the LLM."""
    formatter = string.Formatter()
    fields = {
        name
        for _, name, _, _ in formatter.parse(_CLASSIFICATION_BATCH_CONTEXT_TEMPLATE)
        if name
    }
    assert fields == {"context_str", "taxonomy_ref"}


def test_batch_context_template_preserves_literal_braces_in_substituted_values() -> None:
    # str.format() does not re-scan substituted values for placeholders, but
    # this pins that contract so a future switch to e.g. chained .format()
    # calls or % formatting can't silently start mangling brace-containing
    # context/taxonomy text.
    rendered = _CLASSIFICATION_BATCH_CONTEXT_TEMPLATE.format(
        context_str="Suggested: {S1: 0.8}", taxonomy_ref="{not a placeholder}"
    )
    assert "Suggested: {S1: 0.8}" in rendered
    assert "{not a placeholder}" in rendered


def test_classify_entries_batch_is_under_ratchet_line_budget() -> None:
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


def _capture_messages(monkeypatch: pytest.MonkeyPatch) -> list[list[dict]]:
    """Stub call_llm where the #522 split put every call site
    (stage3b/classify.py, #496) and return the list of captured message lists."""
    import unified_pipeline.stage3b.classify as stage3b_classify

    captured = []

    def fake_call_llm(**kwargs: object) -> dict:
        captured.append(kwargs["messages"])
        return {
            "content": '{"classifications": []}',
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cost": 0.0,
            "model": "test-model",
        }

    monkeypatch.setattr(stage3b_classify, "call_llm", fake_call_llm)
    return captured


def test_classify_entries_batch_actually_uses_the_hoisted_template(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tests above validate the templates in isolation -- they'd still pass
    even if classify_entries_batch built its own prompt string and never
    touched them. Exercise the real function (LLM call mocked out): the first
    system message is exactly the static prompt and is marked as the end of the
    cached prefix; the second system message is the rendered batch context; the
    user message carries only the entries."""
    import unified_pipeline.stage_3b_entry_classifier as stage_3b

    captured = _capture_messages(monkeypatch)
    context = stage_3b.TaxonomyContext()
    entries = [{"text": "Associate Professor of Medicine, 2020-present", "hierarchy": ["Positions"]}]

    stage_3b.classify_entries_batch(entries, context, taxonomy={})

    static, batch, user = captured[0]
    assert static == {
        "role": "system",
        "content": _CLASSIFICATION_SYSTEM_PROMPT_TEMPLATE,
        "cache_point": True,
    }
    assert batch == {
        "role": "system",
        "content": _CLASSIFICATION_BATCH_CONTEXT_TEMPLATE.format(
            context_str=context.format_context_string(), taxonomy_ref=""
        ),
    }
    assert user["role"] == "user"
    assert user["content"].startswith("Classify these 1 entries:")
    assert "HIERARCHY CONTEXT FOR THIS BATCH" not in user["content"]


def test_classify_entries_batch_renders_real_taxonomy_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    """The taxonomy={} case above only proves {taxonomy_ref} can render as an
    empty string -- it would still pass even if build_taxonomy_codes_for_prompt()
    were bypassed entirely. Exercise a taxonomy with real codes and assert the
    batch-context block contains the exact rendered taxonomy reference (and the
    static block does not)."""
    import unified_pipeline.stage_3b_entry_classifier as stage_3b

    captured = _capture_messages(monkeypatch)
    taxonomy = {
        "codes": [
            {"code": "S1", "label": "Original Research", "purpose": "Peer-reviewed empirical work"},
            {"code": "H", "label": "Honors and Awards", "purpose": "One-time recognitions"},
        ]
    }
    context = stage_3b.TaxonomyContext()
    entries = [{"text": "Best Paper Award, 2021", "hierarchy": ["Honors"]}]

    stage_3b.classify_entries_batch(entries, context, taxonomy=taxonomy)

    expected_taxonomy_ref = stage_3b.build_taxonomy_codes_for_prompt(taxonomy)
    assert "S1: Original Research" in expected_taxonomy_ref
    assert "H: Honors and Awards" in expected_taxonomy_ref
    assert expected_taxonomy_ref in captured[0][1]["content"]
    assert expected_taxonomy_ref not in captured[0][0]["content"]


def _classify_two_groups(monkeypatch: pytest.MonkeyPatch) -> list[list[dict]]:
    """Run two hierarchy groups with different suggested codes (hence different
    format_context_string() and different group-filtered taxonomy subsets)
    through the real classify_entries_batch; return the captured messages."""
    import unified_pipeline.stage_3b_entry_classifier as stage_3b

    captured = _capture_messages(monkeypatch)
    taxonomy = {
        "codes": [
            {"code": "S1", "label": "Original Research", "purpose": "Peer-reviewed empirical work"},
            {"code": "K1", "label": "Formal Teaching", "purpose": "Courses taught"},
        ]
    }
    pubs = stage_3b.TaxonomyContext(
        section={"title": "PUBS_SECTION_MARKER", "taxonomy_options": [{"code": "S1", "confidence": 0.9}]}
    )
    teaching = stage_3b.TaxonomyContext(
        section={"title": "TEACH_SECTION_MARKER", "taxonomy_options": [{"code": "K1", "confidence": 0.9}]}
    )
    stage_3b.classify_entries_batch(
        [{"text": "Smith J. Trial results. NEJM 2020", "hierarchy": ["Pubs"]}], pubs, taxonomy
    )
    stage_3b.classify_entries_batch(
        [{"text": "Lecturer, Pharmacology 101", "hierarchy": ["Teaching"]}], teaching, taxonomy
    )
    return captured


def test_static_prompt_is_byte_identical_across_hierarchy_groups(monkeypatch: pytest.MonkeyPatch) -> None:
    """#50: the cachePoint follows the static block, so that block must not vary
    per hierarchy group. Two groups: the static system messages are
    byte-identical and carry no group content; each group's variable content is
    only in its own batch-context block."""
    captured = _classify_two_groups(monkeypatch)

    static_a, static_b = captured[0][0], captured[1][0]
    assert static_a == static_b
    assert "PUBS_SECTION_MARKER" not in static_a["content"]
    assert "TEACH_SECTION_MARKER" not in static_a["content"]
    ctx_a, ctx_b = captured[0][1]["content"], captured[1][1]["content"]
    assert "PUBS_SECTION_MARKER" in ctx_a and "TEACH_SECTION_MARKER" not in ctx_a
    assert "TEACH_SECTION_MARKER" in ctx_b and "PUBS_SECTION_MARKER" not in ctx_b


def test_bedrock_cache_prefix_is_identical_across_hierarchy_groups(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same as above but through the real Bedrock adapter: the request's
    `system` blocks up to and including the cachePoint are equal for two groups,
    the per-group block follows the cachePoint, and no second cachePoint trails
    the variable block."""
    from unified_pipeline.llm import bedrock

    captured = _classify_two_groups(monkeypatch)

    class _FakeClient:
        def __init__(self) -> None:
            self.calls = []

        def converse(self, **kwargs: object) -> dict:
            self.calls.append(kwargs)
            return {
                "output": {"message": {"content": [{"text": "{}"}]}},
                "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
                "stopReason": "end_turn",
            }

    fake = _FakeClient()
    monkeypatch.setattr(bedrock, "_get_bedrock_client", lambda: fake)
    for messages in captured:
        bedrock._call_bedrock(
            "us.anthropic.claude-haiku-4-5-20251001-v1:0", messages, 0.1,
            response_format={"type": "json_object"}, max_tokens=2000, enable_prompt_caching=True,
        )

    sys_a, sys_b = fake.calls[0]["system"], fake.calls[1]["system"]
    assert len(sys_a) == len(sys_b) == 3
    assert sys_a[:2] == sys_b[:2]  # static block + cachePoint: the cached prefix
    assert sys_a[1] == {"cachePoint": {"type": "default"}}
    assert "PUBS_SECTION_MARKER" in sys_a[2]["text"] and "TEACH_SECTION_MARKER" in sys_b[2]["text"]
    assert sys_a[2] != sys_b[2]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
