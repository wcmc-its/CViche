"""stage3b/header_pin.py (#312): a confidently mapped section header beats a
model answer in a named set of confusions, and nothing else.

Self-contained: no LLM, no network, synthetic text only.
"""
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage3b.context import TaxonomyContext  # noqa: E402
from unified_pipeline.stage3b.header_pin import (  # noqa: E402
    HEADER_PIN_MIN_CONFIDENCE,
    apply_header_pin,
    pinned_header_code,
)


def _node(title, code, confidence=1.0):
    return {"title": title, "taxonomy_options": [{"code": code, "confidence": confidence}]}


def _ctx(meta=None, section=None, subsection=None):
    return TaxonomyContext(meta_section=meta, section=section, subsection=subsection)


R_CTX = _ctx(meta=_node("INVITED PRESENTATIONS", "R"), section=_node("National", "R"))


def _entry(code, source="llm", reasoning="professional education (K4)"):
    return {"text": "Workshop faculty, Example Society", "taxonomy_code": code,
            "taxonomy_confidence": 0.95, "classification_reasoning": reasoning,
            "classification_source": source}


# --- pinned_header_code -------------------------------------------------------

def test_top_level_header_pins():
    assert pinned_header_code(_ctx(meta=_node("INSTITUTIONAL LEADERSHIP ACTIVITIES", "O"))) == "O"


def test_sublabel_under_agreeing_parent_pins():
    assert pinned_header_code(R_CTX) == "R"


def test_bare_scope_label_alone_does_not_pin():
    # A teaching CV's "Local": stage 3a maps it to R with no parent to say why.
    assert pinned_header_code(_ctx(meta=_node("Local", "R"))) is None


def test_scope_label_under_disagreeing_parent_does_not_pin():
    ctx = _ctx(meta=_node("BIBLIOGRAPHY", "S1", 0.5), section=_node("International", "R"))
    assert pinned_header_code(ctx) is None


def test_scope_label_under_a_different_confident_code_does_not_pin():
    ctx = _ctx(meta=_node("TEACHING", "K1", 1.0), section=_node("Local", "R"))
    assert pinned_header_code(ctx) is None


def test_below_the_confidence_floor_does_not_pin():
    below = HEADER_PIN_MIN_CONFIDENCE - 0.01
    assert pinned_header_code(_ctx(meta=_node("INVITED PRESENTATIONS", "R", below))) is None
    assert pinned_header_code(_ctx(meta=_node("INVITED PRESENTATIONS", "R", HEADER_PIN_MIN_CONFIDENCE))) == "R"


def test_no_context_does_not_pin():
    assert pinned_header_code(_ctx()) is None
    assert pinned_header_code(_ctx(meta={"title": "X", "taxonomy_options": []})) is None


def test_top_option_is_the_highest_confidence_not_the_first():
    node = {"title": "INVITED PRESENTATIONS", "taxonomy_options": [
        {"code": "S8", "confidence": 0.02}, {"code": "R", "confidence": 0.98}]}
    assert pinned_header_code(_ctx(meta=node)) == "R"


# --- apply_header_pin ---------------------------------------------------------

@pytest.mark.parametrize("wrong", ["K1", "K2", "K4", "H"])
def test_recodes_each_overridable_confusion_under_r(wrong):
    (out,), n = apply_header_pin([_entry(wrong)], R_CTX)
    assert n == 1
    assert out["taxonomy_code"] == "R"
    assert out["pre_pin_code"] == wrong
    assert out["pre_pin_reasoning"] == "professional education (K4)"
    assert out["classification_source"] == "llm"


@pytest.mark.parametrize("right", ["S8", "S1", "Q2", "M2B", "T", "R"])
def test_leaves_every_other_model_answer_under_r(right):
    entry = _entry(right)
    (out,), n = apply_header_pin([entry], R_CTX)
    assert n == 0
    assert out == entry


def test_o_header_recodes_course_director_rows_only():
    ctx = _ctx(meta=_node("INSTITUTIONAL LEADERSHIP ACTIVITIES", "O"))
    out, n = apply_header_pin([_entry("K3"), _entry("K4"), _entry("K1"), _entry("P")], ctx)
    assert n == 2
    assert [e["taxonomy_code"] for e in out] == ["O", "O", "K1", "P"]


def test_fallback_and_empty_entries_are_not_model_answers():
    out, n = apply_header_pin([_entry("K4", source="fallback"), _entry("K4", source="empty_entry")], R_CTX)
    assert n == 0
    assert [e["taxonomy_code"] for e in out] == ["K4", "K4"]


def test_rewritten_reasoning_cannot_flip_the_code_back():
    from unified_pipeline.core.validators.reasoning_consistency_checker import check_reasoning_consistency
    (out,), _ = apply_header_pin([_entry("K4")], R_CTX)
    assert not check_reasoning_consistency(out).has_conflict


def test_unpinnable_group_is_returned_unchanged():
    entries = [_entry("K4")]
    out, n = apply_header_pin(entries, _ctx(meta=_node("TEACHING", "K1", 0.4)))
    assert n == 0 and out == entries


def test_input_entries_are_not_mutated():
    entry = _entry("K4")
    apply_header_pin([entry], R_CTX)
    assert entry["taxonomy_code"] == "K4" and "pre_pin_code" not in entry
