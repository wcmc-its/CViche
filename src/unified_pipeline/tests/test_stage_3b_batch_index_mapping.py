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
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import unified_pipeline.stage_3b_entry_classifier as stage_3b  # noqa: E402
from unified_pipeline.stage_3b_entry_classifier import (  # noqa: E402
    TaxonomyContext,
    classify_entries_batch,
    load_taxonomy,
)

TAXONOMY = load_taxonomy()


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
    monkeypatch.setattr(stage_3b, "call_llm", _responder({1: "H", 2: "T"}))

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

    monkeypatch.setattr(stage_3b, "call_llm", _responder(code_by_index))
    results, _ = classify_entries_batch(entries, _context(), TAXONOMY)

    got = [r["taxonomy_code"] if r["classification_source"] == "llm" else None
           for r in results]
    assert got == expected


def test_malformed_classification_object_does_not_kill_the_run(monkeypatch, caplog):
    """#521: an object with no "index" is skipped, not raised past run_stage_3b."""
    def call(**kwargs):
        return {
            "content": json.dumps({"classifications": [
                {"index": 0, "code": "H", "confidence": 0.9},
                {"code": "T", "confidence": 0.5},   # no "index" -> was KeyError
                "not even a dict",                  # -> was TypeError
            ]}),
            "prompt_tokens": 100, "completion_tokens": 20, "cost": 0.001,
        }
    monkeypatch.setattr(stage_3b, "call_llm", call)

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
    monkeypatch.setattr(stage_3b, "call_llm", _responder({0: "H", 1: "T", 2: "B1"}))

    results, stats = classify_entries_batch(
        [_entry("Dean's Award, 2015"), _entry("Consulting, Acme"), _entry("MD, 1998")],
        _context(), TAXONOMY)

    assert [r["taxonomy_code"] for r in results] == ["H", "T", "B1"]
    assert all(r["classification_source"] == "llm" for r in results)
    assert stats["llm_classified"] == 3


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
