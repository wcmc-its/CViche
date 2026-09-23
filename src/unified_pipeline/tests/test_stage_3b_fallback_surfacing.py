"""Fallback-surfacing guards for Stage 3b entry classification (#61).

Stage 3b used to swallow per-batch LLM errors with a one-line print and
silently substitute default codes (``classification_source="fallback"``),
so an invalid model id / IAM gap / outage produced a "successful" run whose
every classification was fake. These tests pin the new behavior: batch
failures are logged as errors with batch context, per-run LLM/fallback
counts are tracked in stats and written to the stage output JSON, partial
batch failures stay non-fatal, and a nonempty run with ZERO successful LLM
classifications raises instead of completing.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage_3b_fallback_surfacing.py -p no:cacheprovider

Self-contained: ``call_llm`` stubbed at the module attribute, no Bedrock/OpenAI.
"""

import json
import logging
import sys
import threading
from pathlib import Path

import pytest

# Ensure the repo's ``src`` directory is importable regardless of cwd/rootdir.
_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import unified_pipeline.stage_3b_entry_classifier as stage_3b  # noqa: E402
# call_llm is stubbed at the module that actually calls it: the #522 split
# moved every classification call site into stage3b/classify.py, so patching
# the facade's copy would no longer intercept anything (#496).
import unified_pipeline.stage3b.classify as stage3b_classify  # noqa: E402
from unified_pipeline.llm.retry import LLMOutageError  # noqa: E402
from unified_pipeline.stage_3b_entry_classifier import (  # noqa: E402
    TaxonomyContext,
    classify_entries_batch,
    load_taxonomy,
)

TAXONOMY = load_taxonomy()


def _entries(texts, hierarchy=("HONORS AND AWARDS",)):
    return [{"text": t, "hierarchy": list(hierarchy)} for t in texts]


def _context(codes=("H",)):
    return TaxonomyContext(meta_section={
        "title": "HONORS AND AWARDS",
        "taxonomy_options": [{"code": c, "confidence": 0.9} for c in codes],
    })


def _ok_response(indices, code="H", model="test-model"):
    return {
        "content": json.dumps({"classifications": [
            {"index": i, "code": code, "confidence": 0.9} for i in indices
        ]}),
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "cost": 0.001,
        "model": model,
    }


def _boom(**kwargs):
    raise RuntimeError("ValidationException: invalid model identifier")


# --- classify_entries_batch: per-batch failure accounting ---------------------

def test_failed_batch_falls_back_and_is_counted(monkeypatch):
    """An LLM error still falls back per entry, but the stats say so."""
    monkeypatch.setattr(stage3b_classify, "call_llm", _boom)

    results, stats = classify_entries_batch(
        _entries(["Dean's Award for Excellence, 2015", "Teaching Prize, 2018"]),
        _context(), TAXONOMY)

    assert [r["classification_source"] for r in results] == ["fallback", "fallback"]
    assert all(r["taxonomy_code"] == "H" for r in results)
    assert stats["llm_batches"] == 1
    assert stats["failed_batches"] == 1
    assert stats["llm_classified"] == 0
    assert stats["fallback_entries"] == 2


def test_llm_outage_error_reraised_not_defaulted(monkeypatch):
    """LLMOutageError (#810) means the provider itself is down, not a
    parse/validation failure -- classify_entries_batch must propagate it
    (not default the batch's codes) so run_stage_3b, and the driver above
    it, fail the run instead of continuing on entries nobody classified."""
    def _outage(**kwargs):
        raise LLMOutageError("provider outage exceeded budget", seconds_waited=1800.0)
    monkeypatch.setattr(stage3b_classify, "call_llm", _outage)

    with pytest.raises(LLMOutageError):
        classify_entries_batch(
            _entries(["Dean's Award for Excellence, 2015"]), _context(), TAXONOMY)


def test_plain_value_error_still_falls_back(monkeypatch):
    """Only LLMOutageError gets the fail-the-run treatment above -- an
    ordinary exception (a malformed response, here a bare ValueError) keeps
    today's per-batch fallback behavior."""
    def _value_error(**kwargs):
        raise ValueError("malformed response")
    monkeypatch.setattr(stage3b_classify, "call_llm", _value_error)

    results, stats = classify_entries_batch(
        _entries(["Dean's Award for Excellence, 2015"]), _context(), TAXONOMY)

    assert results[0]["classification_source"] == "fallback"
    assert stats["failed_batches"] == 1


def test_failed_batch_logs_error_with_batch_info(monkeypatch, caplog):
    """The old print is now a logger error carrying exception + batch context."""
    monkeypatch.setattr(stage3b_classify, "call_llm", _boom)

    with caplog.at_level(logging.ERROR, logger=stage_3b.logger.name):
        classify_entries_batch(
            _entries(["Dean's Award for Excellence, 2015"]), _context(), TAXONOMY)

    records = [r for r in caplog.records
               if "batch classification failed" in r.getMessage()]
    assert len(records) == 1
    rec = records[0]
    assert rec.levelno == logging.ERROR
    assert rec.exc_info is not None
    assert "invalid model identifier" in str(rec.exc_info[1])
    assert "offset 0" in rec.getMessage()
    assert "HONORS AND AWARDS" in rec.getMessage()


def test_entry_with_none_hierarchy_degrades_instead_of_crashing(monkeypatch, caplog):
    """An entry with an explicit "hierarchy": None must not crash
    _classify_one_batch's bare " > ".join(...) calls -- neither the one that
    labels the LLM prompt nor the one inside the except handler (where
    raising would mask the original exception being handled instead of just
    logging it). Route this entry through a FAILING call_llm so the batch
    reaches both guarded call sites in the same run."""
    monkeypatch.setattr(stage3b_classify, "call_llm", _boom)

    entries = [{"text": "Dean's Award for Excellence, 2015", "hierarchy": None}]
    with caplog.at_level(logging.ERROR, logger=stage_3b.logger.name):
        results, stats = classify_entries_batch(entries, _context(), TAXONOMY)

    assert results[0]["classification_source"] == "fallback"
    assert stats["failed_batches"] == 1
    records = [r for r in caplog.records
               if "batch classification failed" in r.getMessage()]
    assert len(records) == 1
    # The except-handler's own hierarchy_path build (classify.py) must have
    # degraded None to its default rather than raising TypeError there,
    # which would have masked the RuntimeError this test actually raises.
    assert "(no hierarchy)" in records[0].getMessage()


def test_all_batches_fail_when_more_than_one_forms(monkeypatch):
    """Every failed_batches assertion elsewhere in the suite tops out at 1,
    always paired with llm_batches of 1, 2, or 3 -- no test drives
    batch_size < len(entries) (so the loop forms MULTIPLE internal batches)
    where every one of them raises. Pin failed_batches == llm_batches for
    llm_batches > 1, not just the trivial 1-of-1 case."""
    monkeypatch.setattr(stage3b_classify, "call_llm", _boom)

    results, stats = classify_entries_batch(
        _entries(["Award A", "Award B", "Award C", "Award D"]),
        _context(), TAXONOMY, batch_size=2,
    )

    assert [r["classification_source"] for r in results] == ["fallback"] * 4
    assert stats["llm_batches"] == 2
    assert stats["failed_batches"] == 2


def test_successful_batch_counts_llm_classified(monkeypatch):
    monkeypatch.setattr(
        stage3b_classify, "call_llm",
        lambda **kw: _ok_response([0, 1], model="test-model"))

    results, stats = classify_entries_batch(
        _entries(["Dean's Award for Excellence, 2015", "Teaching Prize, 2018"]),
        _context(), TAXONOMY)

    assert [r["classification_source"] for r in results] == ["llm", "llm"]
    assert stats["llm_batches"] == 1
    assert stats["failed_batches"] == 0
    assert stats["llm_classified"] == 2
    assert stats["fallback_entries"] == 0
    assert stats["model"] == "test-model"


def test_mixed_success_and_failure_batches_merge_stats(monkeypatch):
    """Two internal batches in ONE classify_entries_batch call: the first
    succeeds, the second raises. Pins the cross-batch aggregation in
    classify_entries_batch itself, distinct from run_stage_3b's per-group
    aggregation (each hierarchy group is its own classify_entries_batch call
    there, so that path never exercises summing multiple _BatchStats within
    a single call)."""
    calls = {"n": 0}

    def call(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _ok_response([0, 1], code="H")
        raise RuntimeError("LLM unavailable")

    monkeypatch.setattr(stage3b_classify, "call_llm", call)

    results, stats = classify_entries_batch(
        _entries([
            "Award A",
            "Award B",
            "Award C",
            "Award D",
        ]),
        _context(),
        TAXONOMY,
        batch_size=2,
    )

    assert calls["n"] == 2

    assert [r["classification_source"] for r in results] == [
        "llm",
        "llm",
        "fallback",
        "fallback",
    ]

    assert stats["llm_batches"] == 2
    assert stats["failed_batches"] == 1
    assert stats["llm_classified"] == 2
    assert stats["fallback_entries"] == 2
    assert stats["entries_classified"] == 4


@pytest.mark.parametrize(
    "response_json",
    [
        {"classifications": None},
        {"classifications": {}},
        {"classifications": "not-a-list"},
    ],
)
def test_invalid_classifications_container_falls_back(monkeypatch, response_json):
    """A malformed *container* (not just a malformed element inside it) must
    not crash the batch. ``{"classifications": None}`` used to reach
    ``for c in classifications`` uncaught (that loop is deliberately outside
    the call_llm try/except) and raise TypeError, escaping
    classify_entries_batch entirely instead of falling back per entry."""
    def call(**kwargs):
        return {
            "content": json.dumps(response_json),
            "prompt_tokens": 40,
            "completion_tokens": 8,
            "cost": 0.0008,
            "model": "test-model",
        }

    monkeypatch.setattr(stage3b_classify, "call_llm", call)

    results, stats = classify_entries_batch(
        _entries(["Award A", "Award B"]),
        _context(),
        TAXONOMY,
        batch_size=2,
    )

    assert len(results) == 2
    assert all(r["classification_source"] == "fallback" for r in results)


def test_omitted_index_falls_back_without_batch_failure(monkeypatch):
    """An entry the LLM skipped is a fallback, but NOT a failed batch."""
    monkeypatch.setattr(stage3b_classify, "call_llm", lambda **kw: _ok_response([0]))

    results, stats = classify_entries_batch(
        _entries(["Dean's Award for Excellence, 2015", "Teaching Prize, 2018"]),
        _context(), TAXONOMY)

    assert [r["classification_source"] for r in results] == ["llm", "fallback"]
    assert stats["failed_batches"] == 0
    assert stats["llm_classified"] == 1
    assert stats["fallback_entries"] == 1


def test_empty_entries_never_attempt_llm(monkeypatch):
    def _fail(**kwargs):
        raise AssertionError("call_llm must not run for empty-text entries")
    monkeypatch.setattr(stage3b_classify, "call_llm", _fail)

    results, stats = classify_entries_batch(
        _entries(["", "   "]), _context(), TAXONOMY)

    assert [r["classification_source"] for r in results] == ["empty_entry"] * 2
    assert stats["llm_batches"] == 0
    assert stats["failed_batches"] == 0
    assert stats["empty_entries"] == 2


# --- run_stage_3b: run-level surfacing ----------------------------------------

def _write_run_fixtures(tmp_path, entries, mappings):
    stage_2 = tmp_path / "entries.json"
    stage_2.write_text(json.dumps({"entries": entries}))
    stage_3a = tmp_path / "header_taxonomy.json"
    stage_3a.write_text(json.dumps({"mappings": mappings}))
    return stage_2, stage_3a


_MAPPINGS = [
    {"title": "HONORS AND AWARDS",
     "taxonomy_options": [{"code": "H", "confidence": 0.9}], "children": []},
    {"title": "MEMBERSHIPS",
     "taxonomy_options": [{"code": "I", "confidence": 0.9}], "children": []},
]

_RUN_ENTRIES = [
    {"element_type": "text", "text": "Dean's Award for Excellence, 2015",
     "hierarchy": ["HONORS AND AWARDS"]},
    {"element_type": "text", "text": "American College of Physicians, 2010-present",
     "hierarchy": ["MEMBERSHIPS"]},
]


def test_run_fails_when_every_batch_fails(monkeypatch, tmp_path):
    """Zero successful LLM classifications in a nonempty run -> raise, no output."""
    monkeypatch.setattr(stage3b_classify, "call_llm", _boom)
    stage_2, stage_3a = _write_run_fixtures(tmp_path, _RUN_ENTRIES, _MAPPINGS)
    out_dir = tmp_path / "out"

    with pytest.raises(RuntimeError, match="zero LLM classifications"):
        stage_3b.run_stage_3b(
            "9999_Doe_Jane_CV",
            stage_2_path=str(stage_2),
            stage_3a_path=str(stage_3a),
            output_dir=str(out_dir),
        )

    assert not (out_dir / "9999_Doe_Jane_CV_classified.json").exists()


def test_run_survives_partial_failure_and_reports_stats(monkeypatch, tmp_path):
    """One failed batch of two: run completes and the output JSON says so."""
    calls = {"n": 0}

    def _first_call_fails(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("ThrottlingException: rate exceeded")
        return _ok_response([0], code="I")

    monkeypatch.setattr(stage3b_classify, "call_llm", _first_call_fails)
    stage_2, stage_3a = _write_run_fixtures(tmp_path, _RUN_ENTRIES, _MAPPINGS)
    out_dir = tmp_path / "out"

    result = stage_3b.run_stage_3b(
        "9999_Doe_Jane_CV",
        stage_2_path=str(stage_2),
        stage_3a_path=str(stage_3a),
        output_dir=str(out_dir),
    )

    output = json.loads(Path(result["output_path"]).read_text())
    cstats = output["meta"]["classification_stats"]
    assert cstats["llm_batches"] == 2
    assert cstats["failed_batches"] == 1
    assert cstats["llm_classified"] == 1
    assert cstats["fallback_entries"] == 1
    assert cstats["fallback_rate"] == 0.5
    assert cstats["had_classification_errors"] is True

    sources = sorted(e["classification_source"] for e in output["entries"])
    assert sources == ["fallback", "llm"]


def test_run_with_zero_entries_does_not_raise_zero_llm_error(monkeypatch, tmp_path):
    """Zero entries (empty content list) is NOT the same state as "entries
    exist but their text is blank" (see test_run_with_only_empty_entries_does_not_fail
    below): here there are no hierarchy groups at all, so llm_batches stays 0
    and the "zero LLM classifications" guard -- which only fires when
    llm_batches > 0 -- must not raise."""
    def _fail(**kwargs):
        raise AssertionError("call_llm must not be called")

    monkeypatch.setattr(stage3b_classify, "call_llm", _fail)

    stage_2, stage_3a = _write_run_fixtures(
        tmp_path,
        [],
        _MAPPINGS,
    )
    out_dir = tmp_path / "out"

    result = stage_3b.run_stage_3b(
        "9999_Doe_Jane_CV",
        stage_2_path=str(stage_2),
        stage_3a_path=str(stage_3a),
        output_dir=str(out_dir),
    )

    output = json.loads(Path(result["output_path"]).read_text())

    assert output["entries"] == []
    assert output["meta"]["classification_stats"]["llm_batches"] == 0
    assert output["meta"]["classification_stats"]["failed_batches"] == 0


def test_run_with_only_empty_entries_does_not_fail(monkeypatch, tmp_path):
    """No LLM call was ever attempted -> nothing failed, run completes."""
    def _fail(**kwargs):
        raise AssertionError("call_llm must not run for empty-text entries")
    monkeypatch.setattr(stage3b_classify, "call_llm", _fail)

    empty_entries = [
        {"element_type": "text", "text": "  ", "hierarchy": ["HONORS AND AWARDS"]},
    ]
    stage_2, stage_3a = _write_run_fixtures(tmp_path, empty_entries, _MAPPINGS)
    out_dir = tmp_path / "out"

    result = stage_3b.run_stage_3b(
        "9999_Doe_Jane_CV",
        stage_2_path=str(stage_2),
        stage_3a_path=str(stage_3a),
        output_dir=str(out_dir),
    )

    output = json.loads(Path(result["output_path"]).read_text())
    cstats = output["meta"]["classification_stats"]
    assert cstats["llm_batches"] == 0
    assert cstats["failed_batches"] == 0
    assert cstats["empty_entries"] == 1
    assert cstats["had_classification_errors"] is False


# --- run_stage_3b: hierarchy groups on a thread pool (#881) --------------------

_MANY_GROUPS = [f"SECTION {i:02d}" for i in range(8)]
_MANY_MAPPINGS = [
    {"title": t, "taxonomy_options": [{"code": "H", "confidence": 0.9}], "children": []}
    for t in _MANY_GROUPS
]
_MANY_ENTRIES = [
    {"element_type": "text", "text": f"Award {i} for section {t}, 2015", "hierarchy": [t]}
    for t in _MANY_GROUPS for i in range(2)
]


def _run(tmp_path, monkeypatch, call, workers):
    monkeypatch.setattr(stage3b_classify, "call_llm", call)
    stage_2, stage_3a = _write_run_fixtures(tmp_path, _MANY_ENTRIES, _MANY_MAPPINGS)
    result = stage_3b.run_stage_3b(
        "9999_Doe_Jane_CV", stage_2_path=str(stage_2), stage_3a_path=str(stage_3a),
        output_dir=str(tmp_path / f"out_w{workers}"), workers=workers,
    )
    artifact = json.loads(Path(result["output_path"]).read_text())
    del artifact["meta"]["generated_at"]  # the only field that legitimately differs run to run
    return artifact


def test_parallel_groups_write_the_same_artifact_as_the_serial_loop(tmp_path, monkeypatch):
    """Groups finish in reverse order under the pool; the artifact must not."""
    import threading
    import time
    in_flight = {"now": 0, "peak": 0}
    lock = threading.Lock()

    def slow_early_sections(**kwargs):
        with lock:
            in_flight["now"] += 1
            in_flight["peak"] = max(in_flight["peak"], in_flight["now"])
        prompt = kwargs["messages"][-1]["content"]
        # Later sections answer first: SECTION 07 sleeps least.
        idx = next(int(t[-2:]) for t in _MANY_GROUPS if t in prompt)
        time.sleep((len(_MANY_GROUPS) - idx) * 0.005)
        with lock:
            in_flight["now"] -= 1
        return _ok_response([0, 1])

    serial = _run(tmp_path, monkeypatch, slow_early_sections, workers=1)
    assert in_flight["peak"] == 1  # workers=1 really is the serial loop
    parallel = _run(tmp_path, monkeypatch, slow_early_sections, workers=4)
    assert in_flight["peak"] > 1  # and workers=4 really overlapped

    assert serial == parallel
    entries = parallel["entries"]
    assert [e["hierarchy"][0] for e in entries] == [t for t in _MANY_GROUPS for _ in range(2)]


def test_group_progress_lines_are_monotonic_and_unspliced(tmp_path, monkeypatch, capsys):
    import time

    def slow_early_sections(**kwargs):
        prompt = kwargs["messages"][-1]["content"]
        idx = next(int(t[-2:]) for t in _MANY_GROUPS if t in prompt)
        time.sleep((len(_MANY_GROUPS) - idx) * 0.005)
        return _ok_response([0, 1])

    _run(tmp_path, monkeypatch, slow_early_sections, workers=4)

    out = capsys.readouterr().out.splitlines()
    progress = [line for line in out if line.startswith("[") and "/8]" in line]
    # orchestrator.py's PROGRESS_PATTERNS read "[N/M]"; N must never go backwards.
    assert [int(line[1:line.index("/")]) for line in progress] == list(range(1, 9))
    # Each group's block is one atomic print: its "Entries:" line follows its header.
    for i, line in enumerate(out):
        if line.startswith("[") and "/8]" in line:
            assert out[i + 1].strip().startswith("Entries: 2"), out[i:i + 2]


def test_group_progress_line_is_read_by_progress_patterns(capsys, progress_patterns):
    """Stage 3b's ``[N/M]`` line, read the way orchestrator.py does (first
    pattern that matches wins): the bar gets (done, total), not the index."""
    printer = stage_3b._group_progress_printer(["Education", "Awards", "Grants > " + "x" * 60])
    printer(2, ([], None, ["  Entries: 2"]))  # index 2 finishes first
    line = capsys.readouterr().out.splitlines()[0]
    match = next(m for p in progress_patterns if (m := p.search(line)))
    assert (int(match.group(1)), int(match.group(2))) == (1, 3)
    assert line == "[1/3] " + ("Grants > " + "x" * 60)[:60] + "..."  # key cut at 60 chars


def test_groups_run_inside_the_callers_run_id_context(tmp_path, monkeypatch):
    from unified_pipeline.core import prompt_logger
    seen = set()

    def record(**kwargs):
        seen.add(prompt_logger._current_run_id.get())
        return _ok_response([0, 1])

    token = prompt_logger.set_current_run_id("run-3b")
    try:
        _run(tmp_path, monkeypatch, record, workers=4)
    finally:
        prompt_logger.reset_current_run_id(token)

    assert seen == {"run-3b"}


class _FakeRoutedStdout:
    """Minimal stand-in for orchestrator.py's ``_RoutedStdout`` -- a dict
    keyed by ``threading.get_ident()``. A registered thread's write lands in
    its own capture; any other thread's write lands in ``leak`` instead,
    exactly like a pod's real stdout would swallow it (#581).
    """

    def __init__(self, leak):
        self._leak = leak
        self._captures = {}

    def register(self, ident, capture):
        self._captures[ident] = capture

    def write(self, text):
        return self._captures.get(threading.get_ident(), self._leak).write(text)

    def flush(self):
        pass


def test_group_progress_prints_only_from_the_calling_thread(tmp_path, monkeypatch):
    """LEAD defect: _classify_group used to print its "[N/M]" block from
    inside _classify_group, which map_in_order runs on a pool thread that is
    never registered with the orchestrator's thread-routed stdout -- those
    lines reached the pod's real stdout instead of the run's progress bar /
    log viewer. Install a fake routed stdout that only registers the main
    (calling) thread and run a real thread pool (workers=4): every "[N/M]"
    line must land in the registered capture, and none may leak.
    """
    import io
    import time

    def slow_early_sections(**kwargs):
        prompt = kwargs["messages"][-1]["content"]
        idx = next(int(t[-2:]) for t in _MANY_GROUPS if t in prompt)
        time.sleep((len(_MANY_GROUPS) - idx) * 0.005)
        return _ok_response([0, 1])

    monkeypatch.setattr(stage3b_classify, "call_llm", slow_early_sections)
    stage_2, stage_3a = _write_run_fixtures(tmp_path, _MANY_ENTRIES, _MANY_MAPPINGS)

    registered = io.StringIO()
    leak = io.StringIO()
    fake_stdout = _FakeRoutedStdout(leak)
    fake_stdout.register(threading.get_ident(), registered)

    real_stdout = sys.stdout
    monkeypatch.setattr(sys, "stdout", fake_stdout)
    try:
        stage_3b.run_stage_3b(
            "9999_Doe_Jane_CV", stage_2_path=str(stage_2), stage_3a_path=str(stage_3a),
            output_dir=str(tmp_path / "out"), workers=4,
        )
    finally:
        monkeypatch.setattr(sys, "stdout", real_stdout)

    def progress_lines(text):
        return [line for line in text.splitlines() if line.startswith("[") and "/8]" in line]

    registered_progress = progress_lines(registered.getvalue())
    leak_progress = progress_lines(leak.getvalue())
    assert [int(line[1:line.index("/")]) for line in registered_progress] == list(range(1, 9))
    assert leak_progress == []
    # Not just the "[N/M]" shape: nothing at all -- a mutant that prints the
    # group body (not just the progress line) from the pool thread must fail
    # this test too, not just the narrower progress-line check above.
    assert leak.getvalue() == ""


def test_no_hierarchy_entries_are_grouped_and_classified(tmp_path, monkeypatch):
    """Entries with no "hierarchy" field group under NO_HIERARCHY_KEY
    (group_entries_by_hierarchy) and must still be classified -- with
    hierarchy=[] context -- not silently dropped. Pins the sentinel
    round-trip between the producer and _classify_group's consumer: a
    literal copy drifting on either side would make `hierarchy_key ==
    NO_HIERARCHY_KEY` false and misclassify with a bogus one-element
    hierarchy instead."""
    monkeypatch.setattr(stage3b_classify, "call_llm", lambda **kw: _ok_response([0]))

    entries = [{"element_type": "text", "text": "Some unattached award, 2020"}]
    stage_2, stage_3a = _write_run_fixtures(tmp_path, entries, [])
    out_dir = tmp_path / "out"

    result = stage_3b.run_stage_3b(
        "9999_Doe_Jane_CV",
        stage_2_path=str(stage_2),
        stage_3a_path=str(stage_3a),
        output_dir=str(out_dir),
    )

    output = json.loads(Path(result["output_path"]).read_text())
    assert len(output["entries"]) == 1
    assert output["entries"][0]["classification_source"] == "llm"
    assert output["meta"]["classification_stats"]["llm_classified"] == 1


def test_suggested_codes_line_shows_overflow_count(tmp_path, monkeypatch, capsys):
    """More than 3 suggested codes: the printed line must say how many are
    hidden, not just show the first 3 as if they were all of them."""
    monkeypatch.setattr(stage3b_classify, "call_llm", lambda **kw: _ok_response([0]))

    mappings = [{
        "title": "HONORS AND AWARDS",
        "taxonomy_options": [{"code": c, "confidence": 0.9} for c in ["H", "H1", "H2", "H3"]],
        "children": [],
    }]
    entries = [{"element_type": "text", "text": "Dean's Award, 2015",
                "hierarchy": ["HONORS AND AWARDS"]}]
    stage_2, stage_3a = _write_run_fixtures(tmp_path, entries, mappings)

    stage_3b.run_stage_3b(
        "9999_Doe_Jane_CV", stage_2_path=str(stage_2), stage_3a_path=str(stage_3a),
        output_dir=str(tmp_path / "out"),
    )

    out = capsys.readouterr().out
    assert "(+1 more)" in out
