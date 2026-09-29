"""Stage 3b maps each LLM classification back to the entry it was asked about.

Two defects lived in that mapping, both invisible downstream because a
misassigned code is shaped exactly like a correct one:

- #520: the prompt labels entries with their index in ``batch_entries``, so the
  model echoes those labels back and ``class_by_idx`` is keyed by the ORIGINAL
  index. The lookup used a POSITION within ``entries_with_text`` instead. Those
  agree only when nothing was filtered out — and stage 2 emits empty ``break``
  entries throughout the list deliberately (``filter_extraction_noise`` keeps
  them: "breaks are legitimately empty"), so a batch containing one shifted
  every entry after it onto its neighbour's code and confidence.

- #521: ``class_by_idx`` was built outside the try/except guarding the LLM call,
  so one classification object missing "index" raised out of ``run_stage_3b``
  entirely — failing the whole web run, or letting the CLI continue every later
  stage on unclassified entries.
"""
import json
import logging
import re
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import unified_pipeline.stage_3b_entry_classifier as stage_3b  # noqa: E402
# call_llm is stubbed at the module that actually calls it: the #522 split
# moved every classification call site into stage3b/classify.py, so patching
# the facade's copy would no longer intercept anything (#496).
import unified_pipeline.stage3b.classify as stage3b_classify  # noqa: E402
from unified_pipeline.stage_3b_entry_classifier import (  # noqa: E402
    TaxonomyContext,
    classify_entries_batch,
)

# A minimal, in-memory taxonomy: only the codes these tests actually use.
# Loading the real taxonomy_v7.json (load_taxonomy()) at module scope made
# pytest COLLECTION depend on that file existing on disk and parsing as valid
# JSON -- this suite exists to check index mapping and malformed LLM
# responses, not taxonomy content, so it should never fail to collect
# because an unrelated taxonomy artifact is missing or broken.
TAXONOMY = {"codes": [
    {"code": "H", "label": "Honors & Awards"},
    {"code": "T", "label": "Appendix/Other"},
    {"code": "B1", "label": "Academic Degrees"},
    {"code": "D1", "label": "Academic Appointments"},
    {"code": "F1", "label": "Licensure"},
]}


def _context(codes=("H",)):
    return TaxonomyContext(meta_section={
        "title": "HONORS AND AWARDS",
        "taxonomy_options": [{"code": c, "confidence": 0.9} for c in codes],
    })


def _entry(text):
    return {"text": text, "hierarchy": ["HONORS AND AWARDS"]}


def _break():
    """What stage 2 emits for a blank line — element_type 'break', empty text."""
    return {"text": "", "element_type": "break", "hierarchy": ["HONORS AND AWARDS"]}


def _responder(code_by_index):
    """An LLM that answers using the indices it was given in the prompt."""
    def call(**kwargs):
        return {
            "content": json.dumps({"classifications": [
                {"index": i, "code": c, "confidence": 0.9}
                for i, c in sorted(code_by_index.items())
            ]}),
            "prompt_tokens": 100, "completion_tokens": 20, "cost": 0.001,
        }
    return call


def test_empty_entry_midbatch_does_not_shift_later_classifications(monkeypatch):
    """#520: the exact shape from the issue — [break, A, B].

    The prompt labels A as [1] and B as [2]. Before the fix the lookup asked
    for positions 0 and 1, so A missed entirely and B was handed A's code.
    """
    monkeypatch.setattr(stage3b_classify, "call_llm", _responder({1: "H", 2: "T"}))

    results, _ = classify_entries_batch(
        [_break(), _entry("Dean's Award, 2015"), _entry("Consulting, Acme Corp")],
        _context(), TAXONOMY)

    assert [r["classification_source"] for r in results] == [
        "empty_entry", "llm", "llm"]
    # A keeps its own code; B is not handed A's.
    assert results[1]["taxonomy_code"] == "H"
    assert results[2]["taxonomy_code"] == "T"


def test_every_entry_keeps_its_own_code_with_breaks_interleaved(monkeypatch):
    """Breaks scattered through the batch, each entry given a distinct code.

    Stage 2 sorts breaks into document order, so they land between entries
    rather than grouped — several shifts of different sizes in one batch.
    """
    entries, expected, code_by_index = [], [], {}
    codes = ["H", "T", "B1", "D1", "F1"]
    for i, code in enumerate(codes):
        entries.append(_break())
        expected.append(None)
        entries.append(_entry(f"Entry number {i} text"))
        expected.append(code)
        code_by_index[len(entries) - 1] = code

    monkeypatch.setattr(stage3b_classify, "call_llm", _responder(code_by_index))
    results, _ = classify_entries_batch(entries, _context(), TAXONOMY)

    got = [r["taxonomy_code"] if r["classification_source"] == "llm" else None
           for r in results]
    assert got == expected


def test_prompt_indices_match_the_positions_the_lookup_reads_back(monkeypatch):
    """The producer/consumer contract at the center of #520, asserted
    directly instead of only through its symptom.

    The prompt (the producer) labels each entry "[i]" using i's position in
    ``batch_entries`` -- see ``entries_lines`` in ``_classify_one_batch``.
    The result-mapping loop (the consumer) reads the model's echoed "index"
    back via ``class_by_idx.get(orig_idx)`` using that SAME numbering. This
    test captures the literal prompt text sent to the (stubbed) LLM and
    checks its "[i]" labels equal the batch positions of the non-empty
    entries, then confirms the results the consumer produced line up with
    those same positions -- so a future refactor that reintroduces a
    position-within-entries_with_text lookup fails here, not just via a
    shifted taxonomy_code.
    """
    captured = {}

    def call(**kwargs):
        captured["messages"] = kwargs["messages"]
        return {
            "content": json.dumps({"classifications": [
                {"index": 1, "code": "H", "confidence": 0.9},
                {"index": 2, "code": "T", "confidence": 0.9},
            ]}),
            "prompt_tokens": 100, "completion_tokens": 20, "cost": 0.001,
        }

    monkeypatch.setattr(stage3b_classify, "call_llm", call)

    entries = [_break(), _entry("Dean's Award, 2015"), _entry("Consulting, Acme Corp")]
    results, _ = classify_entries_batch(entries, _context(), TAXONOMY)

    user_message = captured["messages"][1]["content"]
    prompt_indices = [int(m) for m in re.findall(r"^\[(\d+)\]", user_message, re.MULTILINE)]

    # Producer side: the break at position 0 is excluded, so the prompt
    # labels the two real entries with their batch_entries positions, 1
    # and 2 -- not 0 and 1 (which is what a position-within-
    # entries_with_text scheme would have produced).
    assert prompt_indices == [1, 2]

    # Consumer side: the model echoed those same positions back, and the
    # lookup landed each code on the entry that owns that position.
    assert results[1]["taxonomy_code"] == "H"
    assert results[2]["taxonomy_code"] == "T"


@pytest.mark.parametrize("malformed_object", [
    {"code": "T", "confidence": 0.5},   # no "index" -> was KeyError
    "not even a dict",                  # -> was TypeError
], ids=["missing-index", "non-dict"])
def test_single_malformed_classification_object_is_skipped(monkeypatch, caplog, malformed_object):
    """#521, each malformed shape asserted independently: a missing "index"
    key and a non-dict element are different code paths through the
    isinstance/"index in c" guard (KeyError vs TypeError, pre-fix), so each
    gets its own case rather than being proven only in combination."""
    def call(**kwargs):
        return {
            "content": json.dumps({"classifications": [
                {"index": 0, "code": "H", "confidence": 0.9},
                malformed_object,
            ]}),
            "prompt_tokens": 100, "completion_tokens": 20, "cost": 0.001,
        }
    monkeypatch.setattr(stage3b_classify, "call_llm", call)

    with caplog.at_level(logging.WARNING, logger=stage_3b.logger.name):
        results, _ = classify_entries_batch(
            [_entry("Dean's Award, 2015"), _entry("Consulting, Acme Corp")],
            _context(), TAXONOMY)

    # The well-formed one still lands; the other falls back rather than crashing.
    assert results[0]["taxonomy_code"] == "H"
    assert results[0]["classification_source"] == "llm"
    assert results[1]["classification_source"] == "fallback"

    warned = [r for r in caplog.records if "malformed classification" in r.getMessage()]
    assert len(warned) == 1, "malformed object was dropped silently"
    assert "skipped 1" in warned[0].getMessage()


def test_multiple_malformed_classification_objects_in_one_batch_are_all_counted(monkeypatch, caplog):
    """#521, the original regression shape: a missing-index dict AND a
    non-dict value in the SAME batch are both skipped and both counted,
    not just the first one encountered."""
    def call(**kwargs):
        return {
            "content": json.dumps({"classifications": [
                {"index": 0, "code": "H", "confidence": 0.9},
                {"code": "T", "confidence": 0.5},   # no "index" -> was KeyError
                "not even a dict",                  # -> was TypeError
            ]}),
            "prompt_tokens": 100, "completion_tokens": 20, "cost": 0.001,
        }
    monkeypatch.setattr(stage3b_classify, "call_llm", call)

    with caplog.at_level(logging.WARNING, logger=stage_3b.logger.name):
        results, _ = classify_entries_batch(
            [_entry("Dean's Award, 2015"), _entry("Consulting, Acme Corp")],
            _context(), TAXONOMY)

    # The well-formed one still lands; the other falls back rather than crashing.
    assert results[0]["taxonomy_code"] == "H"
    assert results[0]["classification_source"] == "llm"
    assert results[1]["classification_source"] == "fallback"

    warned = [r for r in caplog.records if "malformed classification" in r.getMessage()]
    assert len(warned) == 1, "malformed objects were dropped silently"
    assert "skipped 2" in warned[0].getMessage()


def test_well_formed_batch_is_unchanged(monkeypatch):
    """Control: with no empty entries the two index schemes coincide, so the
    fix must not perturb the case that always worked."""
    monkeypatch.setattr(stage3b_classify, "call_llm", _responder({0: "H", 1: "T", 2: "B1"}))

    results, stats = classify_entries_batch(
        [_entry("Dean's Award, 2015"), _entry("Consulting, Acme"), _entry("MD, 1998")],
        _context(), TAXONOMY)

    assert [r["taxonomy_code"] for r in results] == ["H", "T", "B1"]
    assert all(r["classification_source"] == "llm" for r in results)
    assert stats["llm_classified"] == 3


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
