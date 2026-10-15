"""Tests for the hoisted stage 3b classification system prompt (#522).

classify_entries_batch() used to build its LLM system prompt as an inline
f-string, ~775 lines long, which inflated the function against the
function-size ratchet (scripts/check_function_size.py). The prompt text is
now a module-level ``str.format()`` template so the function body stays
short; these tests guard both the substitution behaviour and the
regression this hoist was meant to fix.
"""
import ast
import json
import string
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest

from unified_pipeline.stage_3b_entry_classifier import (
    _CLASSIFICATION_SYSTEM_PROMPT_TEMPLATE,
)

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
    # call_llm is stubbed at the module that actually calls it: the #522 split
    # moved every classification call site into stage3b/classify.py, so patching
    # the facade's copy would no longer intercept anything (#496).
    import unified_pipeline.stage3b.classify as stage3b_classify
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
    import unified_pipeline.stage3b.classify as stage3b_classify
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


_TAXONOMY_PATH = _SRC / "unified_pipeline" / "core" / "taxonomy_v7.json"


def _taxonomy_label(code: str) -> str:
    codes = json.loads(_TAXONOMY_PATH.read_text())["codes"]
    return next(entry["label"] for entry in codes if entry["code"] == code)


def test_s3_s4_rules_name_the_same_section_as_the_taxonomy() -> None:
    """#1706: rule 29 once defined S3 as chapters and S4 as edited books,
    the reverse of taxonomy_v7.json (S3 "Books", S4 "Chapters") and of
    stage 6, which renders S3 under "Books:" and S4 under "Chapters:". An
    edited book then rendered under Chapters and a chapter under Books."""
    s3 = _rule_block("    - S3 = ", "    - S4 = ")
    s4 = _rule_block("    - S4 = ", "30. TECHNICAL REPORTS")
    assert s3.startswith(f"    - S3 = {_taxonomy_label('S3')}:")
    assert s4.startswith(f"    - S4 = {_taxonomy_label('S4')}:")
    # An edited book is a whole book: S3. A chapter is S4, never S3.
    assert '"(Eds.)"' in s3 and "edited" in s3
    assert "chapter" not in s3.lower()
    assert '"In:"' in s4
    assert "a whole edited book is S3, not S4" in s4
    # A guest-edited journal issue is the Q4B editorial role (taxonomy_v7
    # "Guest Editor (special issue)" mapping), not S3 or S4.
    assert "special issue of a journal is an editorial role" in s4
    assert "Q4B (rule 21)" in s4
    assert "Guest Editor" in _rule_block("    - Q4B = ", "    - Q4C = ")
    # Rule 30's in-press line names the chapter code too.
    assert "S4 for a book chapter" in _CLASSIFICATION_SYSTEM_PROMPT_TEMPLATE
    assert "S3 for a book chapter" not in _CLASSIFICATION_SYSTEM_PROMPT_TEMPLATE
    # Every line of the two definitions is load-bearing (the "(Ed.)" and
    # "eds." cues, "whoever edited the book", "do not make it S3"), so the
    # block is pinned whole; a wording change has to update this on purpose.
    assert " ".join((s3 + s4).split()) == " ".join(_S3_S4_RULES.split())


_S3_S4_RULES = """
    - S3 = Books: a whole book, whether the CV owner wrote it or
      edited it:
      * Books/monographs and textbooks they authored.
      * Books and edited volumes they edited: the owner's name is
        followed by "(Ed.)", "(Eds.)" or "eds.", or the volume is
        "Edited by" the owner.
    - S4 = Chapters: one chapter or contribution in a book, whoever
      edited the book:
      * A chapter title followed by "In:" or "In" and the book's title,
        usually with the book's editors "(Ed.)"/"(Eds.)" and page numbers.
    - The book's editors appearing in a chapter citation do not make
      it S3; a whole edited book is S3, not S4.
    - A guest-edited special issue of a journal is an editorial role,
      not a book or a chapter: Q4B (rule 21).
"""


# ---------------------------------------------------------------------------
# #50: the per-CV layout's {context_str} slot
# ---------------------------------------------------------------------------

def test_per_cv_context_str_numbers_every_group_in_order():
    from unified_pipeline.stage3b.prompt import per_cv_context_str
    text = per_cv_context_str(["  Top-level: ALPHA", "  Top-level: BETA", "  Top-level: GAMMA"])
    assert "in 3 hierarchy groups" in text
    positions = [text.index(f"  GROUP {n}:\n  Top-level: {name}")
                 for n, name in ((1, "ALPHA"), (2, "BETA"), (3, "GAMMA"))]
    assert positions == sorted(positions)


def test_per_cv_context_str_fills_the_template_slot_ahead_of_the_rules():
    """The per-group context stays AHEAD of the rules (#1021 put it after them
    and shifted classification, reverted by #1089)."""
    from unified_pipeline.stage3b.prompt import per_cv_context_str
    rendered = _CLASSIFICATION_SYSTEM_PROMPT_TEMPLATE.format(
        context_str=per_cv_context_str(["  Top-level: ALPHA"]), taxonomy_ref="TAX_MARKER_XYZ")
    assert rendered.index("GROUP 1:") < rendered.index("TAX_MARKER_XYZ") \
        < rendered.index("CV TAXONOMY CLASSIFICATION RULES")


# ---------------------------------------------------------------------------
# #1691: an activity the owner attended is B2, not K4 or S8
# ---------------------------------------------------------------------------

def _taxonomy_code(code: str) -> dict:
    codes = json.loads(_TAXONOMY_PATH.read_text())["codes"]
    return next(entry for entry in codes if entry["code"] == code)


def test_k4_rule_requires_the_owner_to_have_taught_1691():
    """NECRGC: courses, trainings and meetings under "Professional
    Development", "Training" or "Continuing Education" headings were coded
    K4 and rendered as continuing education the owner taught. K4's rule
    named "professional development workshops", the same words B2 claims."""
    k4 = _rule_block("    - K4 = ", "    - K5 = ")
    assert "professional development" not in k4.lower()
    assert "the owner taught" in k4
    assert "Did the owner TEACH practicing professionals" in _CLASSIFICATION_SYSTEM_PROMPT_TEMPLATE
    assert "Is the audience practicing professionals" not in _CLASSIFICATION_SYSTEM_PROMPT_TEMPLATE
    # The T-vs-entry contrast no longer files a bare course name as K4.
    assert '"Causal Inference Workshop, 2019" → K4' not in _CLASSIFICATION_SYSTEM_PROMPT_TEMPLATE


def test_attendance_rule_sends_attended_activities_to_b2_1691():
    rule = _rule_block("39a. ATTENDED, NOT TAUGHT", "40. MENTORING")
    for signal in ('"Attended"', '"Participated in"', '"Completed"', '"Participant"',
                   '"Attendee"', "bare course, program or event name"):
        assert signal in rule, signal
    for heading in ('"Professional', 'Development", "Training"', '"Certifications"',
                    'Education" does not make its entries teaching'):
        assert heading in rule, heading
    # Positive control: a stated teaching role keeps K4.
    assert '"Course Director, Annual Cardiology CME Course, 2019" → K4' in rule
    assert '"Lecturer, ' in rule and "→ K4" in rule
    for attended in ('"Attended the Department Upstander Workshop (April 2021)" → B2',
                     '"2019 <Society> Annual Meeting – Chicago, IL" → B2 (not S8)'):
        assert attended in rule, attended


def test_s8_rule_sends_a_bare_meeting_name_to_b2_1691():
    """HVSKET: five "<society> Annual Meeting – <city>" lines were coded S8
    and rendered as abstracts with no title."""
    s8 = _rule_block("35. CONFERENCE PRESENTATIONS", "36. SYMPOSIUM")
    assert "A meeting ATTENDED is not a presentation" in s8
    assert "a title or an author list" in s8
    assert "→ B2 (rule 39a), never S8" in s8


def test_taxonomy_k4_b2_s8_agree_with_the_attendance_rule_1691():
    k4, b2, s8 = (_taxonomy_code(code) for code in ("K4", "B2", "S8"))
    assert not any("professional development" in rule.lower() for rule in k4["key_rules"])
    assert any("attended" in rule and "→ B2, NOT K4" in rule for rule in k4["key_rules"])
    assert any("attended" in c and "instead of B2" in c for c in k4["common_confusions"])
    assert any("attended" in rule and "→ B2, NOT K4 or S8" in rule for rule in b2["key_rules"])
    assert any("bare meeting name" in rule and "→ B2, never S8" in rule for rule in s8["key_rules"])
    mappings = json.loads(_TAXONOMY_PATH.read_text())["disambiguation_notes"]["teaching_codes"]["mappings"]
    assert "professional development" not in mappings["K4 - CME & Professional Education"]
