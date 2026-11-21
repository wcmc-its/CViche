"""doctor/lints/model_review.py: lint 14bi, the model reading the source beside the output (#2172).

No model is called: `call` is a stub with call_llm's signature. Synthetic text only.
"""

import json
import threading

import pytest

from unified_pipeline.doctor.lints import model_review as mr
from unified_pipeline.doctor.shared import STATUS_SKIPPED
from unified_pipeline.llm_client import LlmUsage

OUTPUT = "[O1] EDUCATION\n[O2.0] | Degree | Institution | Dates |\n[O2.1] | MD | Example School |  |"


def _entry(idx, text, hierarchy=("EDUCATION",), etype="paragraph"):
    return {"element_idx_start": idx, "element_idx_end": idx, "element_type": etype,
            "text": text, "hierarchy": list(hierarchy)}


def _stage2(*entries):
    return {"document_uid": "TEST01", "entries": list(entries)}


class _Stub:
    """call_llm's shape. ``replies`` maps a source tag ("[E3]") to the reply for
    the chunk that holds it, since the calls after the first run concurrently;
    a reply that is an exception is raised."""

    def __init__(self, replies):
        self.replies = replies
        self.calls = []
        self._lock = threading.Lock()

    def __call__(self, stage, messages, response_format=None, **kwargs):
        with self._lock:
            self.calls.append((stage, messages))
        user = messages[-1]["content"]
        reply = next(r for tag, r in self.replies.items() if tag in user)
        if isinstance(reply, Exception):
            raise reply
        return {"content": reply, "cost": 0.01, "prompt_tokens": 100, "completion_tokens": 10,
                "cache_read_tokens": 80, "cache_write_tokens": 0}


def _reply(*claims):
    return json.dumps({"findings": list(claims)})


def _claim(idx, kind="wrong_value", output="MD | Example School | 2010", source="MD, Example School, 2009"):
    return {"idx": idx, "kind": kind, "severity": "high", "confidence": "high",
            "source_quote": source, "output_quote": output, "output_block": "O2.1",
            "why": "The output gives 2010; the CV says 2009."}


# --- chunking -------------------------------------------------------------------

def test_chunks_follow_top_level_headings_and_name_the_path():
    stage2 = _stage2(
        _entry(0, "EDUCATION", etype="header"),
        _entry(1, "MD, Example School, 2009", hierarchy=("EDUCATION", "Degrees")),
        _entry(2, "", etype="break"),
        _entry(3, "GRANTS", hierarchy=("GRANTS",), etype="header"),
        _entry(4.1, "R01 Example\tgrant", hierarchy=("GRANTS",)))
    first, second = mr.review_chunks(stage2)
    assert first.top == "EDUCATION" and second.top == "GRANTS"
    assert first.lines == ["[E0] ## EDUCATION", "(heading path: EDUCATION > Degrees)",
                           "[E1] MD, Example School, 2009"]
    assert second.lines == ["[E3] ## GRANTS", "[E4.1] R01 Example\tgrant"]
    assert second.idxs == frozenset({3, 4})


def test_a_long_section_is_cut_at_the_entry_ceiling(monkeypatch):
    monkeypatch.setattr(mr, "CHUNK_MAX_ENTRIES", 2)
    chunks = mr.review_chunks(_stage2(*(_entry(i, f"Course {i}") for i in range(5))))
    assert [sorted(c.idxs) for c in chunks] == [[0, 1], [2, 3], [4]]


# --- the model's reply ------------------------------------------------------------

@pytest.mark.parametrize("text", [
    '{"findings": []}',
    'Here you are:\n```json\n{"findings": []}\n```',
    'Sure. {"findings": []} Done.',
])
def test_a_reply_parses_bare_fenced_or_wrapped(text):
    assert mr.parse_reply(text) == []


def test_a_raw_tab_inside_a_quote_still_parses():
    """A CV's tab layout reaches the quotes as raw tabs, which strict JSON refuses."""
    assert mr.parse_reply('{"findings": [{"why": "a\tb"}]}') == [{"why": "a\tb"}]


def test_a_reply_with_no_findings_list_is_none():
    assert mr.parse_reply("I could not do that.") is None
    assert mr.parse_reply('{"result": []}') is None


# --- one claim -> one finding --------------------------------------------------------

def test_a_claim_becomes_an_info_finding_the_scorer_can_locate():
    chunk = mr.review_chunks(_stage2(_entry(12, "MD, Example School, 2009")))[0]
    finding = mr.claim_finding(_claim(["E12"]), chunk)
    assert finding["lint"] == mr.MODEL_REVIEW_LINT and finding["severity"] == "INFO"
    assert finding["message"] == f"{mr.KIND_LABELS['wrong_value']}: The output gives 2010; the CV says 2009."
    # Output quote first (where a comment can sit), then the CV's, then the entry
    # index doctor_vs_autopsy.py matches labels by.
    assert finding["evidence"] == ["MD | Example School | 2010", "MD, Example School, 2009", "entry 12"]


@pytest.mark.parametrize("kind", sorted(mr.KIND_LABELS))
def test_a_lost_detail_or_a_duplicate_is_warn_and_reads_its_own_precision_row(kind):
    """MR-P1: only the kinds over the WARN bar count toward the score, and each
    reads the PRECISION.md shape row of its label, so rewording a label cannot
    silently drop it back to the lint's pooled row."""
    from unified_pipeline.doctor.precision import finding_precision

    chunk = mr.review_chunks(_stage2(_entry(12, "MD, Example School, 2009")))[0]
    finding = mr.claim_finding(_claim(["E12"], kind=kind), chunk)
    warn = kind in {"field_lost", "duplicated"}
    assert finding["severity"] == ("WARN" if warn else "INFO")
    row = finding_precision(finding["lint"], finding["message"])
    assert (row.shape == mr.KIND_LABELS[kind]) is warn and (row.measured == "MR-P1") is warn


def test_an_absent_output_quote_is_dropped_and_a_bare_index_is_read():
    chunk = mr.review_chunks(_stage2(_entry(7, "Example Award, 2015")))[0]
    finding = mr.claim_finding(_claim([7], kind="record_lost", output="ABSENT", source="Example Award, 2015"), chunk)
    assert finding["evidence"] == ["Example Award, 2015", "entry 7"]


@pytest.mark.parametrize("claim", [
    _claim(["E99"]),                      # names no element of its chunk
    _claim(["E12"], kind="looks_odd"),    # a kind the lint does not know
    _claim("not an index"),
])
def test_a_claim_the_lint_cannot_place_is_dropped(claim):
    chunk = mr.review_chunks(_stage2(_entry(12, "MD, Example School, 2009")))[0]
    assert mr.claim_finding(claim, chunk) is None


# --- the lint ---------------------------------------------------------------------------

def test_off_is_a_skip_and_calls_nothing():
    [skip] = mr.lint_model_review(_stage2(_entry(1, "x")), OUTPUT, None, None)
    assert skip["status"] == STATUS_SKIPPED and skip["reason"] == "off"


def test_an_empty_output_document_is_a_skip():
    stub = _Stub({})
    [skip] = mr.lint_model_review(_stage2(_entry(1, "x")), "  ", stub, LlmUsage())
    assert skip["status"] == STATUS_SKIPPED and not stub.calls


def test_each_chunk_is_one_call_on_the_review_stage_and_its_cost_is_counted(monkeypatch):
    monkeypatch.setattr(mr, "CHUNK_MAX_ENTRIES", 1)
    stub = _Stub({"[E1]": _reply(_claim(["E1"])), "[E2]": _reply(),
                  "[E3]": _reply(_claim(["E3"], kind="duplicated"))})
    usage = LlmUsage()
    findings = mr.lint_model_review(
        _stage2(_entry(1, "MD, Example School, 2009"), _entry(2, "BS"), _entry(3, "PhD")), OUTPUT, stub, usage)
    assert [f["evidence"][-1] for f in findings] == ["entry 1", "entry 3"]  # chunk order
    assert {stage for stage, _ in stub.calls} == {mr.MODEL_REVIEW_STAGE}
    system, user = stub.calls[0][1]
    assert system["role"] == "system" and system["content"].startswith(mr.REVIEW_PROMPT)
    assert system["content"].endswith(OUTPUT)  # the whole output, behind the prompt, for the cache
    assert "[E1] MD, Example School, 2009" in user["content"]
    assert usage.cost == pytest.approx(0.03) and usage.cache_read_tokens == 240


def test_a_first_call_that_raises_stops_the_review():
    """An outage or a model the role may not invoke would fail every call."""
    stub = _Stub({"[E1]": RuntimeError("AccessDenied"), "[E2]": _reply()})
    findings = mr.lint_model_review(
        _stage2(_entry(1, "a", hierarchy=("A",)), _entry(2, "b", hierarchy=("B",))), OUTPUT, stub, LlmUsage())
    assert [f["status"] for f in findings] == [STATUS_SKIPPED]
    assert "the review call failed (RuntimeError" in findings[0]["message"]
    assert len(stub.calls) == 1


def test_a_later_failure_or_unparseable_reply_is_named_and_the_rest_still_count():
    stub = _Stub({"[E1]": _reply(), "[E2]": RuntimeError("Throttling"), "[E3]": "no json here",
                  "[E4]": _reply(_claim(["E4"]))})
    stage2 = _stage2(*(_entry(i, f"record {i}", hierarchy=(f"S{i}",)) for i in range(1, 5)))
    findings = mr.lint_model_review(stage2, OUTPUT, stub, LlmUsage())
    assert findings[0]["evidence"][-1] == "entry 4"
    not_checked = findings[-1]
    assert not_checked["message"].startswith(f"{mr.NOT_CHECKED_PREFIX}2 of 4 source chunks not checked")
    assert any(label.startswith("RuntimeError") for label in not_checked["evidence"])
    assert "unparseable reply" in not_checked["evidence"]


def test_a_cv_past_the_call_ceiling_is_checked_up_to_it_and_says_so(monkeypatch):
    monkeypatch.setattr(mr, "MAX_CHUNKS", 2)
    stub = _Stub({"[E": _reply()})
    stage2 = _stage2(*(_entry(i, f"record {i}", hierarchy=(f"S{i}",)) for i in range(1, 5)))
    findings = mr.lint_model_review(stage2, OUTPUT, stub, LlmUsage())
    assert len(stub.calls) == 2
    assert findings[-1]["message"] == (f"{mr.NOT_CHECKED_PREFIX}2 of 4 source chunks not checked "
                                       f"(only the first 2 are reviewed)")


def test_the_prompt_fingerprint_is_the_templates():
    import hashlib
    assert hashlib.sha256(mr.REVIEW_PROMPT.encode("utf-8")).hexdigest() == mr.REVIEW_PROMPT_SHA256
