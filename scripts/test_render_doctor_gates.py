#!/usr/bin/env python3
"""Self-tests for the pure logic in render_gate_compare.py and
doctor_gate_compare.py -- the parts cheap enough to pin without a corpus.

    python3 scripts/test_render_doctor_gates.py

The controls that actually prove these gates work (determinism holds, a real
mutation is caught, a crashed render/doctor run is refused rather than
compared) need a corpus and are logged in
docs/guides/render-doctor-gates.md instead -- see issue #584. This file
covers the regressions found while building that (the date/timestamp masking
not accidentally masking real content, and the work-dir path normaliser
silently failing on a relative path), and render_gate.py's own pure logic:
the LLM-disable monkeypatch below, --source-dir's uid-to-docx resolver
(#550), whose failure mode is silent -- a wrong resolution renders ANOTHER
CV's contact block into this CV's Personal Data table rather than raising --
and the pieces split out of main() for review on #740: uid discovery and
filtering, artifact precedence, the pre-rmtree output-path check, and the
semantic validation of a rendered docx. What those helpers do when wired
together is scripts/test_render_gate_integration.py's subject; this file
pins each in isolation.

It also covers render_gate_controls.py (#783), the script that runs those
controls, end to end against a fake renderer -- above all that a uid carrying
an owner's name never reaches what it prints or writes to its --summary
file (#2176) -- and build_gate_farm.py's selection logic, without S3.
"""
import contextlib
import io
import json
import os
import re
import shutil
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

import build_gate_farm as bgf  # noqa: E402
import doctor_gate_compare as dgc  # noqa: E402
import render_gate as rg  # noqa: E402
import render_gate_compare as rgc  # noqa: E402
import render_gate_controls as rgctl  # noqa: E402
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


def test_norm_excludes_metrics_so_a_trend_number_never_reads_as_changed():
    # Round-2 review F1: metrics (#816) carries no severity of its own, so a
    # routine denominator shift (honors_rows, appendix_share, ...) must not
    # make norm() -- and therefore CHANGED/PASS/FAIL -- see this uid as
    # regressed when findings are identical.
    a = {"findings": [{"lint": "table_shape", "severity": "WARN"}],
         "metrics": {"honors_rows": 40, "appendix_share": 0.1}}
    b = {"findings": [{"lint": "table_shape", "severity": "WARN"}],
         "metrics": {"honors_rows": 90, "appendix_share": 0.4}}
    assert dgc.norm(a) == dgc.norm(b)


def test_metrics_diff_reports_per_key_old_and_new():
    a = {"metrics": {"honors_rows": 40, "stage3b_fallback_ratio": 0.01}}
    b = {"metrics": {"honors_rows": 90, "stage3b_fallback_ratio": 0.01}}
    assert dgc._metrics_diff(a, b) == [("honors_rows", 40, 90)]


def test_metrics_diff_empty_when_metrics_identical_or_absent():
    a = {"metrics": {"honors_rows": 40}}
    b = {"metrics": {"honors_rows": 40}}
    assert dgc._metrics_diff(a, b) == []
    assert dgc._metrics_diff({}, {}) == []
    # A key present on only one side is a difference too.
    assert dgc._metrics_diff({"metrics": {"x": 1}}, {"metrics": {}}) == [("x", 1, None)]


def _write_report(path, uid, findings, metrics=None):
    path.write_text(json.dumps({
        uid: {"document_uid": uid, "root": "/r", "artifacts": {}, "findings": findings,
              "counts": {}, "worst_severity": None, "metrics": metrics or {}},
        dgc.FAILED_KEY: {},
    }))


def test_doctor_compare_main_passes_when_only_metrics_differ():
    # The whole point of F1: a metrics-only difference must PASS and exit 0,
    # while still telling a human reader what changed.
    with tempfile.TemporaryDirectory() as tmp:
        a_path, b_path = Path(tmp) / "a.json", Path(tmp) / "b.json"
        finding = [{"lint": "table_shape", "severity": "WARN"}]
        _write_report(a_path, "uid1", finding, {"honors_rows": 40})
        _write_report(b_path, "uid1", finding, {"honors_rows": 90})

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = dgc.main([str(a_path), str(b_path)])
        assert rc == 0
        text = out.getvalue()
        assert "CHANGED 0" in text
        assert "PASS - no doctor finding changed" in text
        assert "metrics CHANGED (reported only, never fails the gate): 1 uid(s), 1 key(s)" in text
        assert "uid1 honors_rows: 40 -> 90" in text


def test_doctor_compare_main_still_fails_on_a_findings_change_regardless_of_metrics():
    with tempfile.TemporaryDirectory() as tmp:
        a_path, b_path = Path(tmp) / "a.json", Path(tmp) / "b.json"
        _write_report(a_path, "uid1", [{"lint": "table_shape", "severity": "WARN"}],
                      {"honors_rows": 40})
        _write_report(b_path, "uid1", [{"lint": "table_shape", "severity": "INFO"}],
                      {"honors_rows": 40})

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = dgc.main([str(a_path), str(b_path)])
        assert rc == 1
        text = out.getvalue()
        assert "CHANGED 1" in text
        assert "FAIL" in text
        # metrics are identical here -- no metrics section at all.
        assert "metrics CHANGED" not in text


def test_doctor_compare_main_silent_on_metrics_when_reports_have_none():
    # Legacy/pre-#816 shaped reports (no "metrics" key at all): output must
    # be exactly what it was before metrics existed -- no new section.
    with tempfile.TemporaryDirectory() as tmp:
        a_path, b_path = Path(tmp) / "a.json", Path(tmp) / "b.json"
        a_path.write_text(json.dumps({
            "uid1": {"findings": []}, dgc.FAILED_KEY: {}}))
        b_path.write_text(json.dumps({
            "uid1": {"findings": []}, dgc.FAILED_KEY: {}}))

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = dgc.main([str(a_path), str(b_path)])
        assert rc == 0
        assert "metrics CHANGED" not in out.getvalue()


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
    import ast
    import inspect

    import unified_pipeline.stage_6_word_template as s6

    # The invariant, not one spelling of it: a module-level
    # `from unified_pipeline.llm_client import ..., call_llm, ...` with no
    # alias. A literal-line match broke when the import sweep merged
    # call_llm with LlmUsage into one statement, which binds the same name.
    binds_call_llm = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "unified_pipeline.llm_client"
        and any(alias.name == "call_llm" and alias.asname is None for alias in node.names)
        for node in ast.parse(inspect.getsource(s6)).body
    )
    assert binds_call_llm, (
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


def test_render_gate_source_dir_resolver_respects_uid_boundaries():
    """A shorter uid must not claim a longer uid's docx (#550).

    glob("web05*.docx") also matches web050_cv.docx, and sorted() puts '0'
    (0x30) before '_' (0x5F), so a bare prefix match hands web05 the WRONG
    CV's source document -- the same shape as the 2026-07-15 doctor sweep
    that diagnosed 3 of 25 CVs against another CV's artifacts. Here the
    consequence is worse than a bad diagnosis: web050's name, office
    address, telephone and work email would render into web05's output.
    """
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "web05_cv.docx").write_text("")
        (d / "web050_cv.docx").write_text("")

        assert rg._resolve_source_docx(d, "web05").name == "web05_cv.docx", (
            "uid web05 resolved to a longer uid's docx -- the _uid_owns "
            "boundary rule is not being applied")
        assert rg._resolve_source_docx(d, "web050").name == "web050_cv.docx"


def test_render_gate_source_dir_resolver_returns_none_for_an_uncovered_uid():
    # 65 of the farm's 66 uids have a source docx; web08 has none. That uid
    # must resolve to None so main() falls back to the unmodified
    # run_stage6() call and records "not found" in the index -- not an
    # error, and not a silent render against someone else's document.
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "web09_cv.docx").write_text("")
        assert rg._resolve_source_docx(d, "web08") is None


def test_render_gate_source_dir_resolver_ignores_non_docx_files():
    # The glob is *.docx: a sidecar with the same uid stem (the farm's own
    # per-uid JSON artifacts sit beside CVs in some layouts) is not a source
    # document and must not be handed to python-docx.
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "web05_entries.json").write_text("{}")
        assert rg._resolve_source_docx(d, "web05") is None


def test_render_gate_source_dir_resolver_pins_an_ambiguous_uid_to_one_docx():
    """Two docx both legitimately owned by the uid -- pin which one wins.

    Nothing in the corpus guarantees one file per uid ("... CV.docx" beside
    "... CV (Proof 9-9-99).docx" is exactly this repo's naming), and an
    unpinned choice here is a silently non-deterministic gate arm: two runs of
    the same code could recover contact fields from two different documents.
    Today's rule is sorted()[0], and this test is what makes changing it a
    visible decision rather than a side effect.
    """
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "web05_cv_v2.docx").write_text("")
        (d / "web05_cv.docx").write_text("")

        assert rg._resolve_source_docx(d, "web05").name == "web05_cv.docx", (
            "an ambiguous uid must resolve to the first sorted candidate, "
            "not to whatever order the filesystem happened to hand back")


def test_render_gate_uid_filter_narrows_the_discovered_uids():
    # The filter is the difference between re-rendering 66 CVs and re-rendering
    # the one under investigation, so a filter that silently matched nothing
    # (or everything) would waste an hour or answer the wrong question.
    with tempfile.TemporaryDirectory() as tmp:
        arm = Path(tmp)
        s4 = arm / "stage_4_field_extraction"
        s4.mkdir()
        for uid in ("u1", "u2", "u3"):
            (s4 / f"{uid}_fields.json").write_text("{}")

        assert rg._discover_uids(arm, set()) == ["u1", "u2", "u3"], (
            "an empty filter means every uid in the arm")
        assert rg._discover_uids(arm, {"u2"}) == ["u2"]
        assert rg._discover_uids(arm, {"u2", "u3"}) == ["u2", "u3"]
        (s4 / "u4 _fields.json").write_text("{}")
        assert rg._discover_uids(arm, {"u4 "}) == ["u4 "], (
            "a uid whose filename ends in a space must match its "
            "--uids-file line verbatim, not silently drop out of the run")
        assert rg._discover_uids(arm, {"u4"}) == [], (
            "the space is part of the uid (#732): 'u4' is not 'u4 '")
        assert rg._discover_uids(arm, {"not-in-this-arm"}) == [], (
            "a filter matching nothing must produce nothing -- main() turns "
            "that into a non-zero exit rather than an empty PASS")


def test_render_gate_uid_file_drops_blank_lines_and_trailing_whitespace():
    # Every editor writes a trailing newline, and a uid list is usually
    # hand-assembled -- an empty or space-padded entry would join the filter
    # set, match no uid, and quietly narrow the run.
    with tempfile.TemporaryDirectory() as tmp:
        uids_file = Path(tmp) / "uids.txt"
        uids_file.write_text("u1  \r\n\n   \n  u2\n\n", encoding="utf-8")

        assert rg._uid_filter([], uids_file) == {"u1  ", "  u2"}, (
            "only the line terminator is stripped; whitespace-only lines "
            "are skipped (#732)")
        assert rg._uid_filter(["u9"], None) == {"u9"}, (
            "without --uids-file the positional uids are the filter")
        assert rg._uid_filter([], None) == set(), (
            "no uids and no file means 'all', which is the empty set")


def test_render_gate_input_artifact_precedence_prefers_the_latest_stage():
    # PRECEDENCE is the whole reason two arms are comparable: both must render
    # from the same stage for each uid. Peel the tree back one stage at a time
    # and require the resolver to step to exactly the next entry, never past it.
    with tempfile.TemporaryDirectory() as tmp:
        arm = Path(tmp)
        for stage_dir, pat in rg.PRECEDENCE:
            (arm / stage_dir).mkdir(parents=True)
            (arm / stage_dir / pat.format(uid="u1")).write_text("{}")

        for stage_dir, pat in rg.PRECEDENCE:
            name = pat.format(uid="u1")
            resolved = rg._resolve_input_artifact(arm, "u1")
            assert resolved is not None and resolved.name == name, (
                f"expected {name}, got {resolved}")
            (arm / stage_dir / name).unlink()

        assert rg._resolve_input_artifact(arm, "u1") is None, (
            "a uid with no artifact at any stage must resolve to None so the "
            "index records 'no input artifact' instead of rendering nothing")


def test_render_gate_input_artifact_skips_a_directory_named_like_an_artifact():
    # is_file(), not exists() (review on #589): a DIRECTORY named
    # <uid>_enriched.json would otherwise be handed to the JSON reader instead
    # of falling through to the stage-4 file that is actually there.
    with tempfile.TemporaryDirectory() as tmp:
        arm = Path(tmp)
        (arm / "stage_5_enrichment").mkdir()
        (arm / "stage_4_field_extraction").mkdir()
        (arm / "stage_5_enrichment" / "u1_enriched.json").mkdir()
        (arm / "stage_4_field_extraction" / "u1_fields.json").write_text("{}")

        resolved = rg._resolve_input_artifact(arm, "u1")
        assert resolved is not None and resolved.name == "u1_fields.json", (
            "a directory named like a stage artifact must be skipped, not "
            "selected as this uid's input")


def test_render_gate_output_dir_validation_refuses_to_wipe_an_input_tree():
    """main() rmtree's `out` before every run, so `out` must be provably not an
    input (review on #740). All three destructive relationships are refused --
    equal, out inside an input, an input inside out -- and both paths are
    resolved first, so a ".." detour or a symlink cannot smuggle one past a
    string comparison."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        arm = root / "arm"
        (arm / "stage_4_field_extraction").mkdir(parents=True)

        assert rg._validate_output_dir(arm, arm), "out == input must be refused"
        assert rg._validate_output_dir(arm / "renders", arm), (
            "out inside the input tree must be refused")
        assert rg._validate_output_dir(root, arm), (
            "an out that contains the input tree must be refused")
        assert rg._validate_output_dir(root / "out" / ".." / "arm", arm), (
            "a '..' detour back into the input tree must be refused")

        link = root / "link"
        link.symlink_to(arm, target_is_directory=True)
        assert rg._validate_output_dir(link, arm), (
            "a symlink pointing at the input tree must be refused")

        assert rg._validate_output_dir(root / "out", arm) is None, (
            "a sibling out directory is the normal invocation")
        assert rg._validate_output_dir(root / "out", arm, None) is None, (
            "an omitted --source-dir arrives as None and must be skipped, "
            "not compared")
        assert rg._validate_output_dir(root / "sources" / "sub", arm, root / "sources"), (
            "--source-dir is checked on the same footing as arm_outputs")

        # The wire, not just the helper: _parse_args must exit 2 on a bad
        # `out`, so nothing downstream ever reaches shutil.rmtree.
        with contextlib.redirect_stderr(io.StringIO()):
            try:
                rg._parse_args([str(arm), str(arm)])
            except SystemExit as e:
                assert e.code == 2, f"argparse parser.error exits 2, got {e.code}"
            else:
                raise AssertionError("out == arm_outputs was accepted by _parse_args")


def _make_personal_data_docx(path, labels):
    """A docx whose one table carries `labels` down its first column.

    Mirrors the shape `_validate_rendered_docx` looks for, not a full render:
    the labels are what the WCM template defines and what stage 6's own filler
    matches on, so a table built this way is what a good render leaves behind.
    """
    doc = Document()
    doc.add_paragraph("PERSONAL DATA")
    if labels:
        table = doc.add_table(rows=len(labels), cols=2)
        for row, label in zip(table.rows, labels):
            row.cells[0].text = f"{label.title()}:"
    doc.save(str(path))


def test_render_gate_validates_the_rendered_personal_data_table():
    """A docx can be written, and be a bad render (review on #740).

    dest.is_file() passes on a truncated file and on a document that lost the
    template's tables outright, and the compare step would then fingerprint it
    as a real arm. Checked as invariants, not counts: the six labels the filler
    writes into, matched by substring the way that filler matches them.
    """
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)

        good = d / "good.docx"
        _make_personal_data_docx(good, rg.PERSONAL_DATA_LABELS)
        assert rg._validate_rendered_docx(good) is None, (
            "a docx carrying every PERSONAL DATA label row is a valid render")

        # (i) not a docx at all -- what a truncated or half-written file looks
        # like to python-docx, and what is_file() alone cannot tell apart.
        truncated = d / "truncated.docx"
        truncated.write_text("not a zip archive")
        assert rg._validate_rendered_docx(truncated), "an unreadable docx must be refused"

        # (ii) opens cleanly, but the template's tables are gone.
        no_tables = d / "no_tables.docx"
        _make_personal_data_docx(no_tables, ())
        problem = rg._validate_rendered_docx(no_tables)
        assert problem and "no tables" in problem

        # (iii) the table is there and locatable by its anchor, but a label row
        # the filler writes into has been lost -- the shape a row-count check
        # would catch only by accident and a hard-coded count would false-alarm
        # on the next template revision.
        missing_row = d / "missing_row.docx"
        _make_personal_data_docx(
            missing_row, [x for x in rg.PERSONAL_DATA_LABELS if x != "cell phone"])
        problem = rg._validate_rendered_docx(missing_row)
        assert problem and "cell phone" in problem, (
            f"a dropped PERSONAL DATA label row must name itself, got {problem!r}")


def test_render_gate_rejects_a_non_directory_source_dir():
    """Fail closed (CODING_STANDARDS 5.5): a typo'd --source-dir must not
    silently render as if the flag were omitted. Omitting it is a
    documented, intentional mode; a bad path is not -- and the two produce
    identical output, so a silent fallback would make an entire gate arm
    quietly meaningless."""
    with tempfile.TemporaryDirectory() as tmp:
        arm = Path(tmp) / "arm"
        (arm / "stage_4_field_extraction").mkdir(parents=True)
        out = Path(tmp) / "out"

        # Sanity: the same argv WITHOUT the flag parses, so the failure
        # below is attributable to --source-dir and not to the arm layout.
        ok = rg._parse_args([str(arm), str(out)])
        assert ok.source_dir is None, "--source-dir must default to off (opt-in)"

        # A FILE is not a directory either -- is_dir(), not exists().
        afile = Path(tmp) / "a_file.docx"
        afile.write_text("")

        for bad, why in ((Path(tmp) / "nope", "a non-existent"), (afile, "a file")):
            # argparse writes usage to stderr before raising; swallow it so
            # this file's own output stays the single "ok" line its runner
            # (CI's pipeline-tests job) reads.
            with contextlib.redirect_stderr(io.StringIO()):
                try:
                    rg._parse_args([str(arm), str(out), "--source-dir", str(bad)])
                except SystemExit as e:
                    assert e.code == 2, f"argparse parser.error exits 2, got {e.code}"
                else:
                    raise AssertionError(f"{why} --source-dir was accepted")


# render_gate_controls.py (#783) and the comparison objects it reads (#2176).

# A uid of the shape the corpus really has: the document uid, then the
# original filename, which carries the owner's name. Invented name.
_NAMED_UID = "7QZXJV_Xanthe Quorbel CV (Proof 9-9-99)"
_PLAIN_UID = "QWERTY"
_NAME_PARTS = ("Xanthe", "Quorbel", "xanthe.quorbel", "Proof")
_HASHED_RE = re.compile(r"h:[0-9a-f]{8}")
# Name-shaped tokens a shape test would print: a bare name in uid shape, and
# the leading tokens of "ZQXVWB_CV" and "Zqxvw7_cv".
_NAME_TOKENS = ("QX_ZRV", "ZQXVWB_CV", "Zqxvw7_cv", "Zqxvw7")


def _assert_no_name(text, where):
    for part in _NAME_PARTS:
        assert part not in text, f"{part!r} from a name-bearing uid or CV text reached {where}"


def _masked_named():
    return rgctl.mask_uid(_NAMED_UID, frozenset())


def test_controls_mask_uid_prints_only_allowlisted_generated_uids():
    # A manifest that lists two real ids, a bare 7QZXJV, and "QX_ZRV".
    allowed = frozenset({"QZKMRT", "AB12CD", "7QZXJV", "QX_ZRV"})
    assert rgctl.mask_uid("QZKMRT", allowed) == "QZKMRT"
    assert rgctl.mask_uid("AB12CD", allowed) == "AB12CD"
    # Not in the manifest: hashed, though it has the generated shape.
    assert _HASHED_RE.fullmatch(rgctl.mask_uid("QZKMRT", frozenset()))
    for named in (*_NAME_TOKENS, _NAMED_UID, "ZQXVWB", "web08", "2003_Dr_Xanthe_Quorbel"):
        masked = rgctl.mask_uid(named, allowed)
        assert _HASHED_RE.fullmatch(masked), (named, masked)
    # "QX_ZRV" stays hashed even when a manifest lists it: not the generated shape.
    assert rgctl.mask_uid("ZQXVWB_CV", allowed) != "ZQXVWB"
    assert rgctl.mask_uid("Zqxvw7_cv", allowed) != "Zqxvw7"
    # Stable, and distinct per uid.
    assert rgctl.mask_uid("Qzxv_CV", allowed) == rgctl.mask_uid("Qzxv_CV", frozenset())
    assert rgctl.mask_uid("Qzxv_CV", allowed) != rgctl.mask_uid("Vxqz_CV", allowed)


def test_controls_load_allowed_uids_keeps_only_generated_shapes():
    with tempfile.TemporaryDirectory() as tmp:
        man = Path(tmp) / "manifest.json"
        man.write_text(json.dumps({rgctl.MANIFEST_UIDS_KEY: ["QZKMRT", "QX_ZRV", "Zqxvw7", 7]}))
        assert rgctl.load_allowed_uids(man) == frozenset({"QZKMRT"})
        man.write_text(json.dumps({"codes_by_uid": {"QZKMRT": []}}))
        try:
            rgctl.load_allowed_uids(man)
        except ValueError:
            pass
        else:
            raise AssertionError("a manifest with no document_uids list was accepted")
    assert rgctl.load_allowed_uids(None) == frozenset()


def test_controls_report_never_carries_a_name_bearing_uid_or_cv_text():
    """format_report is the only thing printed and written to the step
    summary; feed it full uids and CV text everywhere a uid or text can sit."""
    docx_name = _NAMED_UID + rgctl.DOCX_SUFFIX
    report = rgctl.GateReport(2, True, ("base", "head"))
    report.controls.append(rgctl.ControlRow("determinism", "ok", True))
    report.render = rgc.RenderComparison(
        common=[docx_name, _PLAIN_UID + rgctl.DOCX_SUFFIX],
        paragraph_changed=[rgc.ParagraphDelta(docx_name, 3, 4, ["Xanthe Quorbel, MD"], [])],
        fingerprint_changed=[docx_name])
    report.doctor = dgc.DoctorComparison(common=[_NAMED_UID, _PLAIN_UID], changed=[_NAMED_UID])
    report.doctor_delta = {_NAMED_UID: ([{"lint": "section_lost", "message": "Xanthe Quorbel lost"}], [])}
    text = rgctl.format_report(report)
    _assert_no_name(text, "format_report")
    assert "7QZXJV" not in text, text
    assert f"| {_masked_named()} | 1 | 0 | yes | 1 | 0 |" in text, text
    assert "| section_lost | 1 | 0 |" in text, text


def _make_rendered_docx(path, work_email, extra_paragraph=None):
    """A render-shaped docx: the PERSONAL DATA table, its Work email cell
    filled with `work_email`, and optionally one more paragraph."""
    doc = Document()
    doc.add_paragraph("PERSONAL DATA")
    table = doc.add_table(rows=len(rg.PERSONAL_DATA_LABELS), cols=2)
    for row, label in zip(table.rows, rg.PERSONAL_DATA_LABELS, strict=True):
        row.cells[0].text = f"{label.title()}:"
        if label == rgctl.WORK_EMAIL_LABEL and work_email:
            row.cells[1].text = work_email
    if extra_paragraph:
        doc.add_paragraph(extra_paragraph)
    doc.save(str(path))


def _fake_render(out, label, leave_stale, with_sources):
    uids = (_NAMED_UID, _PLAIN_UID)
    if not leave_stale:
        shutil.rmtree(out)
        out.mkdir()
    for uid in uids:
        email = "xanthe.quorbel@example.org" if uid == _NAMED_UID and label != "arm_mutated" else ""
        extra = "Xanthe Quorbel, base only" if uid == _NAMED_UID and label == "base" else None
        _make_rendered_docx(out / f"{uid}{rgctl.DOCX_SUFFIX}", email, extra)
    index = {u: {"input": "x"} for u in uids}
    if with_sources:  # the named uid's source is a PDF, so it has no docx
        for u in uids:
            index[u]["source_docx"] = "not found" if u == _NAMED_UID else f"{u}.docx"
    (out / "_render_index.json").write_text(json.dumps(index))


def _fake_runner(leave_stale=False, base_doctor="clean", extra_finding=None, extra_arm="base"):
    """Stands in for the render_gate.py / doctor_gate.py subprocesses. Base
    differs from head on the named uid in both render and doctor; the mutated
    copy loses the work email; the log it writes carries the full uid, as the
    real ones do. `base_doctor` is "clean", "partial" (a "_failed" uid, exit
    1), "absent" (no report, exit 1), "short" (one uid missing, exit 0) or
    "not_dict" (one uid's report is a string, exit 0).
    `extra_finding` is added to the named uid's findings in `extra_arm`'s
    doctor runs ("base", or "head" for both head runs)."""
    def run(script, arm_root, args, log):
        log.write_text(f"[1/2] {_NAMED_UID} OK\n")
        label = Path(arm_root).name
        out = Path(args[1])
        if script == "render_gate.py":
            _fake_render(out, label, leave_stale, "--source-dir" in map(str, args))
            return 0
        findings = [{"lint": "section_lost", "message": "Xanthe Quorbel lost"}] if label == "base" else []
        if extra_finding is not None and label == extra_arm:
            findings = [*findings, extra_finding]
        report = {uid: {"findings": findings if uid == _NAMED_UID else []}
                  for uid in (_NAMED_UID, _PLAIN_UID)}
        mode = base_doctor if label == "base" else "clean"
        if mode == "absent":
            return 1
        failed = {}
        if mode == "partial":
            failed = {_NAMED_UID: f"Traceback ... {_NAMED_UID}"}
            del report[_NAMED_UID]
        elif mode == "short":
            del report[_NAMED_UID]
        elif mode == "not_dict":
            report[_NAMED_UID] = "unreadable"
        out.write_text(json.dumps({**report, dgc.FAILED_KEY: failed}))
        return 1 if failed else 0
    return run


def _make_controls_tree(root, anchor=rgctl.MUTATION_ANCHOR):
    """A farm with both uids, and base/head arms carrying the files the
    controls check for: stage 6, the template, and the mutation's target."""
    farm = root / "farm"
    (farm / "stage_4_field_extraction").mkdir(parents=True)
    for uid in (_NAMED_UID, _PLAIN_UID):
        (farm / "stage_4_field_extraction" / f"{uid}_fields.json").write_text("{}")
    for arm in ("base", "head"):
        a = root / arm
        for rel in (rgctl.STAGE6_REL, rgctl.TEMPLATE_REL, rgctl.MUTATION_FILE):
            (a / rel).parent.mkdir(parents=True, exist_ok=True)
        (a / rgctl.STAGE6_REL).write_text("# stage 6\n")
        (a / rgctl.MUTATION_FILE).write_text(f"        {anchor}\n            pass\n")
        _make_rendered_docx(a / rgctl.TEMPLATE_REL, "")
    return farm


def _run_controls_main(root, runner, extra=()):
    """main() over a fake tree; returns (rc, stdout + stderr, summary)."""
    farm = root / "farm" if (root / "farm").exists() else _make_controls_tree(root)
    summary = root / "summary.md"
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        rc = rgctl.main([str(farm), "--base", str(root / "base"), "--head", str(root / "head"),
                         "--work", str(root / "work"), "--summary", str(summary), *extra],
                        runner=runner)
    return rc, out.getvalue(), summary.read_text() if summary.exists() else ""


def _row(out, control):
    return next(line for line in out.splitlines() if line.startswith(f"| {control}"))


def test_controls_end_to_end_prints_and_summarises_only_masked_uids():
    """The whole run, with a uid that carries a name: every control passes,
    head differs from base, and neither stdout nor the --summary file carries
    the name -- while the subprocess log, which does, stays in --work."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        manifest = root / "manifest.json"
        manifest.write_text(json.dumps({rgctl.MANIFEST_UIDS_KEY: [_PLAIN_UID]}))
        rc, out, summary = _run_controls_main(root, _fake_runner(),
                                              ["--manifest", str(manifest)])
        assert rc == rgctl.EXIT_DIFFERS, out
        assert out == summary
        _assert_no_name(out, "stdout / --summary file")
        assert "7QZXJV" not in out, out
        for control in ("determinism", "crash guard", "real-CV rendering", "mutation"):
            assert _row(out, control).endswith("| PASS |"), _row(out, control)
        assert "doctor base 2/2 exit 0" in _row(out, "real-CV rendering"), out
        assert "predicted 1 (rendered work email); exact match" in out, out
        assert f"| {_masked_named()} | 0 | 1 | yes | 0 | 1 |" in out, out
        assert _NAMED_UID in (root / "work" / "logs" / "render_head_a.log").read_text()
        # The mutation went to a copy; head's own file is untouched.
        assert rgctl.MUTATION_ANCHOR in (root / "head" / rgctl.MUTATION_FILE).read_text()
        assert rgctl.MUTATION_REPLACEMENT in (
            root / "work" / "arm_mutated" / rgctl.MUTATION_FILE).read_text()


def test_controls_fail_when_stale_output_survives_a_render():
    with tempfile.TemporaryDirectory() as tmp:
        rc, out, _ = _run_controls_main(Path(tmp), _fake_runner(leave_stale=True))
        assert rc == rgctl.EXIT_NO_EVIDENCE, out
        row = next(line for line in out.splitlines() if line.startswith("| crash guard"))
        assert "stale docx wiped in 0/4 arms" in row and row.endswith("| FAIL |"), row


def test_controls_fail_when_the_mutation_anchor_is_gone():
    """A mutation that did not apply proves nothing: no anchor, no PASS."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_controls_tree(root, anchor="if work_email:")
        rc, out, _ = _run_controls_main(root, _fake_runner())
        assert rc == rgctl.EXIT_NO_EVIDENCE, out
        row = next(line for line in out.splitlines() if line.startswith("| mutation"))
        assert "anchor matched 0 times" in row and row.endswith("| FAIL |"), row


def test_controls_any_crash_prints_only_the_exception_class():
    """An exception of any class, raised in a render or in format_report
    itself, can carry a full uid in its text. Its message and traceback go to
    a log under --work; stdout, stderr and the --summary file name its class
    only, and the run exits 2."""
    def raising_runner(script, arm_root, args, log):
        raise TypeError(f"cannot render {_NAMED_UID}_fields.json")

    def raising_format(report):
        raise TypeError(f"bad row for {_NAMED_UID}")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        rc, out, summary = _run_controls_main(root, raising_runner)
        assert rc == rgctl.EXIT_NO_EVIDENCE, out
        _assert_no_name(out + summary, "stdout / stderr / --summary file after a crash")
        assert "Traceback" not in out and "7QZXJV" not in out, out
        assert "the run stopped on TypeError" in out and out == summary, out
        assert _NAMED_UID in (root / "work" / rgctl.CRASH_LOG).read_text()

    real_format = rgctl.format_report
    rgctl.format_report = raising_format
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rc, out, summary = _run_controls_main(root, _fake_runner())
    finally:
        rgctl.format_report = real_format
    assert rc == rgctl.EXIT_NO_EVIDENCE, out
    _assert_no_name(out + summary, "stdout / stderr / --summary file after format_report")
    assert "Traceback" not in out and "the run stopped on TypeError" in out, out


def test_controls_a_failed_base_doctor_run_is_no_evidence():
    """Base's doctor run crashing, writing nothing, or skipping a CV is an
    arm failure, not head differing: exit 2, never 1."""
    for mode in ("partial", "absent", "short"):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, _ = _run_controls_main(Path(tmp), _fake_runner(base_doctor=mode))
            assert rc == rgctl.EXIT_NO_EVIDENCE, (mode, out)
            row = _row(out, "real-CV rendering")
            assert "doctor base" in row and row.endswith("| FAIL |"), (mode, row)
            assert "**Controls passed" not in out, (mode, out)
            _assert_no_name(out, f"stdout with base doctor {mode}")


def test_controls_a_per_uid_doctor_report_that_is_not_a_dict_is_no_evidence():
    """A CV whose doctor report is not a dict was not read; compare_reports
    would count it as head differing. It fails the real-CV control: exit 2."""
    with tempfile.TemporaryDirectory() as tmp:
        rc, out, summary = _run_controls_main(Path(tmp), _fake_runner(base_doctor="not_dict"))
    assert rc == rgctl.EXIT_NO_EVIDENCE, out
    row = _row(out, "real-CV rendering")
    assert "doctor base 2/2 exit 0 report not a dict for 1" in row and row.endswith("| FAIL |"), row
    assert "**Controls passed" not in out, out
    _assert_no_name(out + summary, "stdout with a non-dict doctor report")
    assert not rgctl.DoctorRun("base", 0, {"u": [], dgc.FAILED_KEY: {}}).clean_over(1)
    assert rgctl.DoctorRun("base", 0, {"u": {"findings": []}, dgc.FAILED_KEY: {}}).clean_over(1)


def test_controls_a_runner_systemexit_is_no_evidence_but_argparse_exits_pass_through():
    """A runner that calls sys.exit() stops the run like any other crash:
    exit 2 and the class name only. argparse's own SystemExit (a bad flag,
    --help) is the script's intended exit, raised as it is, with no report."""
    def exiting_runner(script, arm_root, args, log):
        sys.exit(f"cannot render {_NAMED_UID}")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        rc, out, summary = _run_controls_main(root, exiting_runner)
        assert rc == rgctl.EXIT_NO_EVIDENCE, out
        assert "the run stopped on SystemExit" in out and out == summary, out
        _assert_no_name(out + summary, "stdout after a runner's SystemExit")
        assert _NAMED_UID in (root / "work" / rgctl.CRASH_LOG).read_text()

    for argv, code in ((["--no-such-flag"], 2), (["--help"], 0)):
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(printed):
            try:
                rgctl.main(argv)
            except SystemExit as e:
                assert e.code == code, (argv, e.code)
            else:
                raise AssertionError(f"{argv} returned instead of exiting")
        assert "Render/doctor gate" not in printed.getvalue(), printed.getvalue()


def _findings_for_lints_that_did_not_run():
    """A lint that raised and a lint whose input would not load, as
    run_doctor itself records them: (crash finding, unreadable finding)."""
    from unified_pipeline.run_doctor import _ready, _run_lint

    def crashing_rule():
        raise TypeError(f"bad entry in {_NAMED_UID}")

    findings = []
    with contextlib.redirect_stderr(io.StringIO()):  # run_doctor logs the crash
        _run_lint("table_shape", crashing_rule, (), findings)
    _ready("section_consistency", unreadable={"stage_4": "JSONDecodeError"},
           findings=findings, stage_4=None)
    return tuple(findings)


def test_controls_a_lint_that_did_not_run_is_no_evidence():
    """run_doctor records a lint that crashed, or whose input would not
    load, as a finding, not a "_failed" entry. In either arm that is no
    evidence (exit 2, the lint named, no uid), never head differing."""
    crash, unreadable = _findings_for_lints_that_did_not_run()
    assert rgctl._lint_not_run(crash) and rgctl._lint_not_run(unreadable)
    for finding, arm in ((crash, "base"), (crash, "head"), (unreadable, "head")):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, summary = _run_controls_main(
                Path(tmp), _fake_runner(extra_finding=finding, extra_arm=arm))
            assert rc == rgctl.EXIT_NO_EVIDENCE, (finding, arm, out)
            row = _row(out, "real-CV rendering")
            doctor = "base" if arm == "base" else "head_a"
            assert (f"doctor {doctor} 2/2 exit 0 lint crashed or input unreadable: "
                    f"{finding['lint']}") in row, row
            assert row.endswith("| FAIL |") and "**Controls passed" not in out, out
            _assert_no_name(out + summary, f"stdout with {finding['lint']} not run in {arm}")
    # A finding that only mentions another lint's crash is a result, not a lint that did not run.
    lookalike = {"lint": "section_lost", "severity": "ERROR", "message": "lint table_shape crashed: TypeError",
                 "evidence": [], "status": "ran", "reason": ""}
    assert not rgctl._lint_not_run(lookalike)
    with tempfile.TemporaryDirectory() as tmp:
        rc, out, _ = _run_controls_main(Path(tmp), _fake_runner(extra_finding=lookalike))
        assert rc == rgctl.EXIT_DIFFERS, out


def test_controls_exit_no_evidence_when_a_comparison_refused_an_arm():
    """Even with every control row passing, a refused or partial comparison
    is no evidence."""
    def report(render, doctor):
        r = rgctl.GateReport(2, False, ("base", "head"))
        r.controls.append(rgctl.ControlRow("determinism", "ok", True))
        r.render, r.doctor = render, doctor
        return r

    both = ["a_wcm.docx", "b_wcm.docx"]
    good_render = rgc.RenderComparison(common=both)
    good_doctor = dgc.DoctorComparison(common=["a", "b"])
    assert rgctl._exit_code(report(good_render, good_doctor)) == rgctl.EXIT_SAME
    changed = rgc.RenderComparison(common=both, fingerprint_changed=["a_wcm.docx"])
    assert rgctl._exit_code(report(changed, good_doctor)) == rgctl.EXIT_DIFFERS
    for render, doctor in (
            (good_render, dgc.DoctorComparison(guard_problems=["arm A: 1 uid(s)"])),
            (rgc.RenderComparison(guard_problems=["arm A failed"]), good_doctor),
            (good_render, dgc.DoctorComparison(common=["a"], missing=["b"])),
            (rgc.RenderComparison(common=["a_wcm.docx"], only_b=["b_wcm.docx"]), good_doctor)):
        r = report(render, doctor)
        assert rgctl._exit_code(r) == rgctl.EXIT_NO_EVIDENCE, (render, doctor)
        assert "**No evidence**" in rgctl.format_report(r)


def test_controls_real_cv_control_fails_on_missing_sources_unless_allowed():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        sources = root / "sources"
        sources.mkdir()
        rc, out, _ = _run_controls_main(root, _fake_runner(), ["--source-dir", str(sources)])
        assert rc == rgctl.EXIT_NO_EVIDENCE, out
        row = _row(out, "real-CV rendering")
        assert "source docx for 1/2" in row and row.endswith("| FAIL |"), row

        rc, out, _ = _run_controls_main(root, _fake_runner(),
                                        ["--source-dir", str(sources), "--allow-missing-sources"])
        assert rc == rgctl.EXIT_DIFFERS, out
        row = _row(out, "real-CV rendering")
        assert "source docx for 1/2 (missing allowed)" in row and row.endswith("| PASS |"), row


def test_controls_refuse_an_arm_that_is_not_a_whole_checkout():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        farm = _make_controls_tree(root)
        (root / "head" / rgctl.TEMPLATE_REL).unlink()
        with contextlib.redirect_stderr(io.StringIO()) as err:
            try:
                rgctl._parse_args([str(farm), "--base", str(root / "base"),
                                   "--head", str(root / "head"), "--work", str(root / "work")])
            except SystemExit as e:
                assert e.code == 2
            else:
                raise AssertionError("an arm without key_files/ was accepted")
        assert "key_files" in err.getvalue()


def test_controls_render_stage_dir_is_run_doctors():
    from unified_pipeline.run_doctor import artifact_layout
    assert artifact_layout(Path(), "u")["stage_6_docx"].parent.name == rgctl.RENDER_STAGE_DIR


def test_render_compare_dirs_reports_each_changed_cv():
    with tempfile.TemporaryDirectory() as tmp:
        a_dir, b_dir = Path(tmp) / "a", Path(tmp) / "b"
        for d, extra in ((a_dir, None), (b_dir, "added line")):
            d.mkdir()
            _make_rendered_docx(d / "u1_wcm.docx", "", extra)
            _make_rendered_docx(d / "u2_wcm.docx", "")
            (d / "_render_index.json").write_text(json.dumps({"u1": {}, "u2": {}}))
        result = rgc.compare_dirs(a_dir, b_dir)
        assert not result.ok and result.common == ["u1_wcm.docx", "u2_wcm.docx"]
        assert [d.name for d in result.paragraph_changed] == ["u1_wcm.docx"]
        assert result.paragraph_changed[0].added == ["added line"]
        assert result.fingerprint_changed == ["u1_wcm.docx"]
        assert rgc.compare_dirs(a_dir, a_dir).ok


def test_doctor_findings_delta_counts_a_repeated_finding_once_per_copy():
    f1, f2 = {"lint": "a", "message": "x"}, {"lint": "b", "message": "y"}
    gained, lost = dgc.findings_delta({"findings": [f1, f2]}, {"findings": [f1, f1]})
    assert gained == [f1] and lost == [f2]
    assert dgc.findings_delta({"findings": [f1]}, {"findings": [f1]}) == ([], [])


def test_doctor_compare_reports_does_not_consume_the_failed_key():
    a = {"u1": {"findings": []}, dgc.FAILED_KEY: {}}
    result = dgc.compare_reports(a, dict(a))
    assert result.ok and result.common == ["u1"] and dgc.FAILED_KEY in a


# build_gate_farm.py (#2176): the selection and layout logic, no S3.

def test_gate_farm_selection_covers_every_code_before_filling():
    codes = {"AAAAAA": frozenset({"A1", "B1"}), "BBBBBB": frozenset({"A1"}),
             "CCCCCC": frozenset({"Z9"}), "DDDDDD": frozenset({"A1", "B1", "C1"}),
             "EEEEEE": frozenset({"B1"})}
    picked = bgf.select_runs(codes, 2)
    assert picked == ["DDDDDD", "CCCCCC"], picked  # the rare Z9 beats more A1/B1
    picked = bgf.select_runs(codes, 4)
    assert set().union(*(codes[u] for u in picked)) == {"A1", "B1", "C1", "Z9"}
    assert len(picked) == 4 and len(set(picked)) == 4
    assert bgf.select_runs(codes, 4) == picked, "the same store must give the same farm"
    assert sorted(bgf.select_runs(codes, 99)) == sorted(codes)


def test_gate_farm_keeps_the_latest_run_of_each_cv():
    """Same upload, or same owner, is the same CV; either key alone misses
    a pair the other catches (two runs of one upload whose stage 4 named the
    owner differently is a real pair in the store)."""
    def run(uid, owner, sha, day):
        return bgf.Candidate(uid, frozenset({"A"}), owner, sha, day, {})

    runs = [run("OLD111", "h1", None, 1), run("NEW222", "h1", None, 2),
            run("SAME01", "h2", "s1", 3), run("SAME02", "h3", "s1", 4),
            run("NONE33", None, None, 1), run("NONE44", None, None, 1)]
    assert [r.uid for r in bgf.distinct_cvs(runs)] == ["NEW222", "NONE33", "NONE44", "SAME02"]


def test_gate_farm_owner_key_hashes_the_name_and_ignores_order_and_case():
    key = bgf.owner_key({"cv_owner": {"name": "Jane A. Doe"}})
    assert key == bgf.owner_key({"cv_owner": {"name": "doe jane a"}})
    assert "jane" not in key.lower() and "doe" not in key.lower()
    assert bgf.owner_key({"cv_owner": None}) is None and bgf.owner_key({}) is None
    assert bgf.section_codes({"entries": [{"taxonomy_code": "S8"}, {}]}) == {"S8", bgf.NO_CODE}


def test_gate_farm_layout_holds_every_render_input_and_no_render_output():
    layout = bgf.farm_layout()
    for stage_dir, pattern in rg.PRECEDENCE:
        assert layout[pattern.format(uid="")] == stage_dir
    assert layout["_entries.json"] == "stage_2_entry_extraction"
    assert rgctl.RENDER_STAGE_DIR not in layout.values()


def test_gate_farm_takes_only_a_generated_run_id_as_a_uid():
    assert bgf.gateable_uid("QZKMRT", "QZKMRT") and bgf.gateable_uid("AB12CD", "AB12CD")
    for run_id, uid in (("QX_ZRV", "QX_ZRV"), ("QZKMRT", "ZQXVWB_CV"), ("QZKMRT", "Zqxvw7_cv"),
                        ("Zqxvw7", "Zqxvw7"), ("QZKMRT", "ZQXVWB"), ("AB1-CD", "AB1-CD")):
        assert not bgf.gateable_uid(run_id, uid), (run_id, uid)


def test_gate_farm_manifest_lists_the_uids_the_gate_may_print():
    chosen = [bgf.Candidate(u, frozenset({"A"}), None, None, 1, {}) for u in ("QZKMRT", "AB12CD")]
    man = bgf.manifest(chosen, {"A", "B"})
    assert man[rgctl.MANIFEST_UIDS_KEY] == ["AB12CD", "QZKMRT"]
    assert man["codes_missing"] == ["B"]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "manifest.json"
        path.write_text(json.dumps(man))
        assert rgctl.load_allowed_uids(path) == frozenset({"AB12CD", "QZKMRT"})


def test_gate_farm_refuses_a_write_flag_it_does_not_mean():
    for argv in (["--dry-run", "--replace"], ["--write", "--local-out", "x"], [],
                 ["--dry-run", "--size", "0"]):
        with contextlib.redirect_stderr(io.StringIO()):
            try:
                bgf._parse_args(argv)
            except SystemExit as e:
                assert e.code == 2, argv
            else:
                raise AssertionError(f"{argv} was accepted")


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
    test_render_gate_source_dir_resolver_respects_uid_boundaries()
    test_render_gate_source_dir_resolver_returns_none_for_an_uncovered_uid()
    test_render_gate_source_dir_resolver_ignores_non_docx_files()
    test_render_gate_source_dir_resolver_pins_an_ambiguous_uid_to_one_docx()
    test_render_gate_uid_filter_narrows_the_discovered_uids()
    test_render_gate_uid_file_drops_blank_lines_and_trailing_whitespace()
    test_render_gate_input_artifact_precedence_prefers_the_latest_stage()
    test_render_gate_input_artifact_skips_a_directory_named_like_an_artifact()
    test_render_gate_output_dir_validation_refuses_to_wipe_an_input_tree()
    test_render_gate_validates_the_rendered_personal_data_table()
    test_render_gate_rejects_a_non_directory_source_dir()
    test_norm_excludes_metrics_so_a_trend_number_never_reads_as_changed()
    test_metrics_diff_empty_when_metrics_identical_or_absent()
    test_metrics_diff_reports_per_key_old_and_new()
    test_doctor_compare_main_passes_when_only_metrics_differ()
    test_doctor_compare_main_silent_on_metrics_when_reports_have_none()
    test_doctor_compare_main_still_fails_on_a_findings_change_regardless_of_metrics()
    test_controls_mask_uid_prints_only_allowlisted_generated_uids()
    test_controls_load_allowed_uids_keeps_only_generated_shapes()
    test_controls_report_never_carries_a_name_bearing_uid_or_cv_text()
    test_controls_end_to_end_prints_and_summarises_only_masked_uids()
    test_controls_fail_when_stale_output_survives_a_render()
    test_controls_fail_when_the_mutation_anchor_is_gone()
    test_controls_any_crash_prints_only_the_exception_class()
    test_controls_a_failed_base_doctor_run_is_no_evidence()
    test_controls_a_per_uid_doctor_report_that_is_not_a_dict_is_no_evidence()
    test_controls_a_runner_systemexit_is_no_evidence_but_argparse_exits_pass_through()
    test_controls_a_lint_that_did_not_run_is_no_evidence()
    test_controls_exit_no_evidence_when_a_comparison_refused_an_arm()
    test_controls_real_cv_control_fails_on_missing_sources_unless_allowed()
    test_controls_refuse_an_arm_that_is_not_a_whole_checkout()
    test_controls_render_stage_dir_is_run_doctors()
    test_render_compare_dirs_reports_each_changed_cv()
    test_doctor_findings_delta_counts_a_repeated_finding_once_per_copy()
    test_doctor_compare_reports_does_not_consume_the_failed_key()
    test_gate_farm_selection_covers_every_code_before_filling()
    test_gate_farm_keeps_the_latest_run_of_each_cv()
    test_gate_farm_owner_key_hashes_the_name_and_ignores_order_and_case()
    test_gate_farm_layout_holds_every_render_input_and_no_render_output()
    test_gate_farm_takes_only_a_generated_run_id_as_a_uid()
    test_gate_farm_manifest_lists_the_uids_the_gate_may_print()
    test_gate_farm_refuses_a_write_flag_it_does_not_mean()
    print("ok")
