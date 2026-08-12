#!/usr/bin/env python3
"""Self-tests for the pure logic in render_gate_compare.py and
doctor_gate_compare.py -- the parts cheap enough to pin without a corpus.

    python3 scripts/test_render_doctor_gates.py

The controls that actually prove these gates work (determinism holds, a real
mutation is caught, a crashed render/doctor run is refused rather than
compared) need a corpus and are logged in
docs/guides/render-doctor-gates.md instead -- see issue #584. This file only
covers the two regressions found while building that: the date/timestamp
masking not accidentally masking real content, and the work-dir path
normaliser silently failing on a relative path.
"""
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# unified_pipeline is normally on the path via PYTHONPATH=<arm>/src (see
# render_gate.py's own docstring) -- add it here too so this file's own
# "runnable standalone" contract holds without the caller setting that up
# just to run the (otherwise pipeline-independent) self-tests.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import doctor_gate_compare as dgc  # noqa: E402
import render_gate_compare as rgc  # noqa: E402

from docx import Document  # noqa: E402


def _make_docx(path, text, tracked_change_date=None):
    """A one-paragraph docx, optionally with a w:date-stamped tracked insert."""
    doc = Document()
    doc.add_paragraph(text)
    doc.save(path)
    if tracked_change_date is None:
        return
    # python-docx has no tracked-changes API; stamp a w:date attribute
    # directly the way stage 6 does (ins.set(qn('w:date'), ...)).
    from lxml import etree
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml")
    root = etree.fromstring(xml)
    p = next(root.iter(W + "p"))
    ins = etree.SubElement(p, W + "ins")
    ins.set(W + "date", tracked_change_date)
    r = etree.SubElement(ins, W + "r")
    t = etree.SubElement(r, W + "t")
    t.text = "tracked insert"
    new_xml = etree.tostring(root)
    # Rewrite the zip with the patched document.xml.
    tmp = str(path) + ".tmp"
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w") as zout:
        for item in zin.infolist():
            data = new_xml if item.filename == "word/document.xml" else zin.read(item.filename)
            zout.writestr(item, data)
    os.replace(tmp, path)


def test_fingerprint_masks_render_timestamp_but_not_real_content():
    with tempfile.TemporaryDirectory() as tmp:
        a = Path(tmp) / "a.docx"
        b = Path(tmp) / "b.docx"
        # Same visible content, different tracked-change timestamps (exactly
        # what two renders of the identical input produce a few seconds
        # apart) -- must fingerprint identically.
        _make_docx(a, "Date of preparation: February 1, 2026",
                  tracked_change_date="2026-02-01T10:00:00Z")
        _make_docx(b, "Date of preparation: February 1, 2026",
                  tracked_change_date="2026-02-01T10:00:07Z")
        assert rgc._fingerprint(a) == rgc._fingerprint(b), (
            "two renders differing only in render-time timestamps must "
            "fingerprint identically")

        # Real content difference must still be caught.
        c = Path(tmp) / "c.docx"
        _make_docx(c, "Date of preparation: February 1, 2026 -- EXTRA TEXT",
                  tracked_change_date="2026-02-01T10:00:00Z")
        assert rgc._fingerprint(a) != rgc._fingerprint(c), (
            "masking timestamps must not also mask a real content change"
        )


def test_check_render_failures_trips_on_either_arm():
    with tempfile.TemporaryDirectory() as tmp:
        a_dir, b_dir = Path(tmp) / "a", Path(tmp) / "b"
        a_dir.mkdir()
        b_dir.mkdir()
        (a_dir / "_render_index.json").write_text(json.dumps({"uid1": {"input": "x"}}))
        (b_dir / "_render_index.json").write_text(
            json.dumps({"uid1": {"error": "RuntimeError: boom"}}))
        problems = rgc._check_render_failures(a_dir, b_dir)
        assert problems, "a recorded render failure must trip the guard"
        assert any("uid1" in p for p in problems)


def test_check_render_failures_clear_when_both_clean():
    with tempfile.TemporaryDirectory() as tmp:
        a_dir, b_dir = Path(tmp) / "a", Path(tmp) / "b"
        a_dir.mkdir()
        b_dir.mkdir()
        (a_dir / "_render_index.json").write_text(json.dumps({"uid1": {"input": "x"}}))
        (b_dir / "_render_index.json").write_text(json.dumps({"uid1": {"input": "x"}}))
        # "clean" now also means the index's success entry has a matching
        # DOCX on disk (review on #589) -- an index that says success with
        # no corresponding file must trip the guard, not read as clear.
        (a_dir / "uid1_wcm.docx").touch()
        (b_dir / "uid1_wcm.docx").touch()
        assert rgc._check_render_failures(a_dir, b_dir) == []


def test_check_render_failures_trips_when_index_and_docx_disagree():
    with tempfile.TemporaryDirectory() as tmp:
        a_dir, b_dir = Path(tmp) / "a", Path(tmp) / "b"
        a_dir.mkdir()
        b_dir.mkdir()
        # Index claims success for uid1, but no DOCX was actually written --
        # the exact "0 == 0" gap the file-set comparison alone can't see
        # (review on #589, point at render_gate_compare.py:69).
        (a_dir / "_render_index.json").write_text(json.dumps({"uid1": {"input": "x"}}))
        (b_dir / "_render_index.json").write_text(json.dumps({"uid1": {"input": "x"}}))
        (b_dir / "uid1_wcm.docx").touch()
        problems = rgc._check_render_failures(a_dir, b_dir)
        assert problems, "index success with no DOCX on disk must trip the guard"
        assert any("uid1" in p and "no DOCX on disk" in p for p in problems)


def test_norm_matches_relative_and_absolute_work_dirs_identically():
    # The exact bug found building this gate: an earlier version of this
    # regex required a leading "/" before the work-dir segment, so a
    # relative invocation ("scratch/doctor_a_work/...") never matched and
    # every CV read as changed.
    report = {
        "artifacts": {
            "stage_4": "scratch/doctor_a_work/uid1/stage_4_field_extraction/uid1_fields.json"
        }
    }
    report_abs = {
        "artifacts": {
            "stage_4": "/Users/x/worktrees/cviche/scratch/doctor_b_work/uid1/"
                       "stage_4_field_extraction/uid1_fields.json"
        }
    }
    assert dgc.norm(report) == dgc.norm(report_abs), (
        "a relative and an absolute path to the same logical artifact must "
        "normalise identically"
    )
    assert "<WORK>" in dgc.norm(report)
    assert "doctor_a_work" not in dgc.norm(report)


def test_norm_still_distinguishes_real_content_differences():
    a = {"findings": [{"lint": "table_shape", "severity": "WARN"}]}
    b = {"findings": [{"lint": "table_shape", "severity": "INFO"}]}
    assert dgc.norm(a) != dgc.norm(b)


def test_doctor_failure_guard_trips_on_either_arm():
    assert dgc._failure_guard_problems({"uid1": "boom"}, {})
    assert dgc._failure_guard_problems({}, {"uid1": "boom"})
    assert dgc._failure_guard_problems({}, {}) == []


def test_doctor_failure_guard_trips_on_malformed_failed_value():
    # "_failed" must be a dict -- a malformed doctor_gate.py output (review
    # on #589) must be a loud gate failure, not a silent pass-through.
    assert dgc._failure_guard_problems(["not", "a", "dict"], {})
    assert dgc._failure_guard_problems({}, "also not a dict")


def test_doctor_compare_identical_count_uses_uid_intersection():
    # The exact bug from review on #589: len(a) - len(diff) overcounts
    # "identical" when the UID sets differ. A=3 UIDs, B=2 UIDs, no common
    # changes -- only 2 CVs were actually compared.
    a = {"u1": {"findings": []}, "u2": {"findings": []}, "u3": {"findings": []}}
    b = {"u1": {"findings": []}, "u2": {"findings": []}}
    common = set(a) & set(b)
    diff = [u for u in common if dgc.norm(a[u]) != dgc.norm(b[u])]
    assert len(common) - len(diff) == 2, "only the 2 UIDs present in both arms were compared"


def test_render_gate_llm_monkeypatch_still_intercepts_stage6():
    """render_gate.py neutralizes LLM calls via `s6.call_llm = _no_llm` --
    this only works because stage_6_word_template.py binds call_llm as a
    module-level name via `from ... import call_llm`, which is what makes it
    resolvable (and reassignable) as `s6.call_llm`. If a future refactor
    switches to `import unified_pipeline.llm_client as llm_client` +
    `llm_client.call_llm(...)`, this monkeypatch stops intercepting anything
    with no error -- a "deterministic" gate run could silently make a real
    LLM call (review on #589)."""
    import inspect
    import unified_pipeline.stage_6_word_template as s6

    src = inspect.getsource(s6)
    assert "from unified_pipeline.llm_client import call_llm" in src, (
        "stage_6_word_template.py's call_llm import style changed -- "
        "render_gate.py's LLM-disable monkeypatch needs updating to match"
    )

    original = s6.call_llm
    calls = []

    def _sentinel(*a, **kw):
        calls.append(1)
        raise RuntimeError("sentinel")

    s6.call_llm = _sentinel
    try:
        try:
            s6.call_llm()
            raise AssertionError("expected RuntimeError from the sentinel")
        except RuntimeError as e:
            assert "sentinel" in str(e)
        assert calls, "reassigning s6.call_llm did not intercept the call"
    finally:
        s6.call_llm = original


if __name__ == "__main__":
    test_fingerprint_masks_render_timestamp_but_not_real_content()
    test_check_render_failures_trips_on_either_arm()
    test_check_render_failures_clear_when_both_clean()
    test_check_render_failures_trips_when_index_and_docx_disagree()
    test_norm_matches_relative_and_absolute_work_dirs_identically()
    test_norm_still_distinguishes_real_content_differences()
    test_doctor_failure_guard_trips_on_either_arm()
    test_doctor_failure_guard_trips_on_malformed_failed_value()
    test_doctor_compare_identical_count_uses_uid_intersection()
    test_render_gate_llm_monkeypatch_still_intercepts_stage6()
    print("ok")
