"""Tests for #565: `generate()`'s per-section exception boundary.

Before this fix, `generate()` ran its 21 `_fill_*` calls with no exception
boundary; any one of them raising aborted the entire render -- no docx at
all, even when every other section would have rendered cleanly (#442 lost
two full documents this way). `_render_section` now isolates each section:
a raise costs that section only, is logged with its traceback, and is
recorded onto the render-warnings sidecar at ERROR severity so the failure
is loud rather than a silently incomplete document.

Self-contained: no DB, no network, no PII. Uses the bundled WCM template and
python-docx, synthetic entries only. The LLM-driven appendix-reconsider pass
is neutralized (same idiom as test_m1_appendix_fallback.py) so tests stay
deterministic and credential-free. Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_section_boundary.py -p no:cacheprovider
"""

import json
import logging
import sys
from pathlib import Path

import pytest
from docx import Document

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402
from unified_pipeline.doctor.lints.render import lint_stage6_warnings  # noqa: E402

# The 21-name flat dispatch this replaced (`stage_6_word_template.py:812-833`
# on the pre-fix tree), in the exact order `generate()` called them.
# test_dispatch_wire_preserves_call_order pins this list: dropping a section
# from the table, or reordering one, fails here even though every other
# test in this file would still pass.
PINNED_DISPATCH_ORDER = [
    "_fill_personal_data",
    "_fill_researcher_profiles",
    "_fill_education",
    "_fill_other_education",
    "_fill_postdoc_training",
    "_fill_positions",
    "_fill_licensure",
    "_fill_board_certification",
    "_fill_honors",
    "_fill_memberships",
    "_fill_teaching",
    "_fill_research_summary",
    "_fill_research_support",
    "_fill_patents",
    "_fill_mentoring",
    "_fill_clinical_practice",
    "_fill_leadership",
    "_fill_administrative_activities",
    "_fill_service",
    "_fill_presentations",
    "_fill_bibliography",
]

_APPENDIX_HEADER = "T. APPENDIX"


def _new_generator() -> WCMTemplateGenerator:
    gen = WCMTemplateGenerator(verbose=False)
    # Neutralize the LLM-driven appendix-reconsider pass: keeps every test
    # here deterministic and credential-free (test_m1_appendix_fallback.py
    # idiom).
    gen._reconsider_appendix_entries = lambda: None
    return gen


def _render(gen: WCMTemplateGenerator, tmp_path: Path, entries: list,
            document_uid: str = "TESTAA") -> Path:
    """Drive the real generate() path and return the output docx path."""
    data = {"document_uid": document_uid, "entries": entries}
    ip = tmp_path / "in.json"
    op = tmp_path / "out.docx"
    ip.write_text(json.dumps(data))
    gen.generate(str(ip), str(op), research_summary_path=None)
    return op


def _sidecar(tmp_path: Path, document_uid: str = "TESTAA") -> dict:
    return json.loads((tmp_path / f"{document_uid}_render_warnings.json").read_text())


def _full_text(doc: Document) -> str:
    parts = [p.text for p in doc.paragraphs]
    parts += [c.text for tb in doc.tables for row in tb.rows for c in row.cells]
    return "\n".join(parts)


def _appendix_paragraphs(doc: Document) -> list:
    """Just the T. APPENDIX paragraphs -- a specific slot, not 'somewhere in
    the document' (mirrors test_stage6_passthrough_appendix_exclusion.py)."""
    paragraphs = [p.text for p in doc.paragraphs]
    for i, text in enumerate(paragraphs):
        if text.strip() == _APPENDIX_HEADER:
            return paragraphs[i:]
    return []


def _personal_data_entry() -> dict:
    return {"text": "Name: Jane Q. Public, MD", "taxonomy_code": "A",
            "extracted_fields": {}, "element_idx_start": 0}


# --------------------------------------------------------------- positive control

def test_nonfatal_section_failure_isolated_to_that_section(tmp_path, caplog):
    """The core fix: `_fill_honors` raising costs only Honors. The document
    is still written, a later section's content still reaches its slot, and
    the failure is both logged (with traceback) and recorded at ERROR."""
    gen = _new_generator()

    def _boom(*_a, **_k):
        raise RuntimeError("boom")

    gen._fill_honors = _boom

    entries = [
        _personal_data_entry(),
        {"text": "The DISTINCTIVE_LATER_TOKEN unmapped content",
         "taxonomy_code": "T", "extracted_fields": {}, "element_idx_start": 5},
    ]
    with caplog.at_level(logging.ERROR):
        op = _render(gen, tmp_path, entries)

    assert op.exists(), "one section raising must not cost the whole document"

    # A later section (the appendix, fed by the T-coded entry) still
    # rendered into its specific slot.
    doc = Document(str(op))
    assert "DISTINCTIVE_LATER_TOKEN" in "\n".join(_appendix_paragraphs(doc)), \
        "a section after the failed one did not reach its slot"

    sidecar = _sidecar(tmp_path)
    failures = [w for w in sidecar["warnings"] if w["check"] == "section_render_failed"]
    assert len(failures) == 1
    record = failures[0]
    assert record["section"] == "honors"
    assert record["severity"] == "ERROR"
    assert "boom" in record["message"]
    assert record["evidence"], "evidence must not be empty"
    assert all(len(line) <= 200 for line in record["evidence"])

    # Logged with its traceback (§5.4 -- never just str(exc)), at ERROR.
    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any("honors" in r.getMessage() for r in error_records)
    assert any(r.exc_info is not None for r in error_records), \
        "the failure must be logged with logger.exception (traceback attached)"


# --------------------------------------------------------------- fatal contract

def test_fatal_personal_data_failure_still_raises_no_docx(tmp_path):
    """`_fill_personal_data` is the one deliberate exception to the boundary
    (#565's judgement call): a document with no owner on it is worse than a
    failed run, so its raise must still propagate and no docx is written."""
    gen = _new_generator()

    def _boom(*_a, **_k):
        raise RuntimeError("fatal-boom")

    gen._fill_personal_data = _boom

    with pytest.raises(RuntimeError, match="fatal-boom"):
        _render(gen, tmp_path, [_personal_data_entry()])

    assert not (tmp_path / "out.docx").exists()


# --------------------------------------------------------------- return-value fallbacks

def test_research_summary_failure_falls_back_to_false_m1_reaches_appendix(tmp_path):
    """`_fill_research_summary` raising must not crash the render, and its
    return-value fallback (False) must route M1 entries to the appendix --
    the same #317/C0ZGFW safety net as when there is no summary at all
    (test_m1_appendix_fallback.py), now also triggered by a section raise."""
    gen = _new_generator()

    def _boom(*_a, **_k):
        raise RuntimeError("research-summary-boom")

    gen._fill_research_summary = _boom

    entries = [
        _personal_data_entry(),
        {"text": "The M1_FALLBACK_TOKEN research program ran 2010 to 2014",
         "taxonomy_code": "M1", "extracted_fields": {"narrative": "ran 2010 to 2014"},
         "element_idx_start": 5},
    ]
    op = _render(gen, tmp_path, entries)
    assert op.exists()

    doc = Document(str(op))
    assert "M1_FALLBACK_TOKEN" in _full_text(doc), \
        "M1 entry was dropped when research_summary_rendered did not fall back to False"

    sidecar = _sidecar(tmp_path)
    failures = [w for w in sidecar["warnings"] if w["check"] == "section_render_failed"]
    assert len(failures) == 1
    assert failures[0]["section"] == "research_summary"


def test_passthrough_failure_entries_not_silently_dropped(tmp_path):
    """`_fill_passthrough_sections` raising must not crash the render, and
    its return-value fallback (empty set of consumed ids) must not silently
    drop passthrough-eligible entries -- they still reach the appendix as
    unconsumed content, not nowhere."""
    gen = _new_generator()

    def _boom(*_a, **_k):
        raise RuntimeError("passthrough-boom")

    gen._fill_passthrough_sections = _boom

    entries = [
        _personal_data_entry(),
        {"text": "PASSTHROUGH_FALLBACK_TOKEN Employment Status: Active",
         "taxonomy_code": "E", "extracted_fields": {}, "element_idx_start": 5},
    ]
    op = _render(gen, tmp_path, entries)
    assert op.exists(), "passthrough failing must not cost the whole document"

    doc = Document(str(op))
    assert "PASSTHROUGH_FALLBACK_TOKEN" in "\n".join(_appendix_paragraphs(doc)), \
        "an unconsumed passthrough-eligible entry must still reach the appendix"

    sidecar = _sidecar(tmp_path)
    failures = [w for w in sidecar["warnings"] if w["check"] == "section_render_failed"]
    assert len(failures) == 1
    assert failures[0]["section"] == "passthrough_sections"


# --------------------------------------------------------------- dispatch wire

def test_dispatch_wire_preserves_call_order(tmp_path):
    """Spy every _fill_* in the pinned 21-name list and assert the CALL
    ORDER matches dev exactly. This is the test that kills a section
    silently falling out of the table (m6), or the table being reordered."""
    gen = _new_generator()
    call_order = []

    def _recorder(name, return_value=None):
        def _f(*_a, **_k):
            call_order.append(name)
            return return_value
        return _f

    for name in PINNED_DISPATCH_ORDER:
        return_value = False if name == "_fill_research_summary" else None
        setattr(gen, name, _recorder(name, return_value))

    _render(gen, tmp_path, [])

    assert call_order == PINNED_DISPATCH_ORDER
    assert gen._section_failures == [], "no section here raised; nothing should be recorded"


# --------------------------------------------------------------- two failures

def test_two_section_failures_both_recorded_rest_still_renders(tmp_path):
    """Two independent sections raising must produce two independent
    records naming both labels, with the document still written."""
    gen = _new_generator()

    def _boom(label):
        def _f(*_a, **_k):
            raise RuntimeError(f"boom-{label}")
        return _f

    gen._fill_honors = _boom("honors")
    gen._fill_leadership = _boom("leadership")

    op = _render(gen, tmp_path, [_personal_data_entry()])
    assert op.exists()

    sidecar = _sidecar(tmp_path)
    failures = [w for w in sidecar["warnings"] if w["check"] == "section_render_failed"]
    assert len(failures) == 2
    assert {f["section"] for f in failures} == {"honors", "leadership"}
    assert all(f["severity"] == "ERROR" for f in failures)


# --------------------------------------------------------------- _write_render_warnings_sidecar unit tests

def test_write_render_warnings_sidecar_writes_expected_json(tmp_path):
    """Positive path: the sidecar is written with the exact merged shape
    generate() relies on."""
    gen = _new_generator()
    output_path = str(tmp_path / "out.docx")
    gen._write_render_warnings_sidecar(
        output_path, "TESTAA", [{"check": "x"}], [{"code": "A"}])
    sidecar = json.loads((tmp_path / "TESTAA_render_warnings.json").read_text())
    assert sidecar == {
        "document_uid": "TESTAA",
        "warnings": [{"check": "x"}],
        "dedup_decisions": [{"code": "A"}],
    }


def test_write_render_warnings_sidecar_is_fail_soft(tmp_path, caplog):
    """Negative path: a sidecar write failure (here, a nonexistent parent
    directory) must never raise -- the render already succeeded and must
    not be failed by a sidecar problem. It is logged at ERROR with the
    traceback attached (§5.4): a missing sidecar leaves the doctor unable
    to tell a clean run from a pre-sidecar build, so it must be loud."""
    gen = _new_generator()
    bad_output_path = str(tmp_path / "missing_dir" / "out.docx")
    with caplog.at_level(logging.ERROR):
        gen._write_render_warnings_sidecar(bad_output_path, "TESTAA", [], [])
    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any("could not write render-warnings sidecar" in r.getMessage()
               for r in error_records)
    assert any(r.exc_info is not None for r in error_records), \
        "the sidecar failure must be logged with logger.exception (traceback attached)"


# --------------------------------------------------------------- lint severity path

def test_lint_stage6_warnings_severity_path():
    report = {"warnings": [
        {"message": "x", "severity": "ERROR"},
        {"message": "y"},
    ]}
    findings = lint_stage6_warnings(report)
    assert [f["severity"] for f in findings] == ["ERROR", "WARN"]


def test_lint_stage6_warnings_unknown_severity_falls_back_to_warn():
    report = {"warnings": [{"message": "x", "severity": "CATASTROPHIC"}]}
    findings = lint_stage6_warnings(report)
    assert findings[0]["severity"] == "WARN"
