"""Contract tests for scripts/corpus_doctor_sweep.py (#563).

Before this fix, `sweep()` caught every per-run `run_doctor` exception, kept
only `str(e)`, and returned a bare `reports` dict -- the traceback (which
lint raised, at which line, on which artifact) was discarded, `main()`
returned 0 unconditionally, and the `--out` payload had no way to say a run
was dropped. `scripts/doctor_one.py` already gets this right for the
single-run case (`logger.exception`, non-zero exit on failure); this pins
the sweep script to the same contract.

Run with:
    python3 -m pytest src/unified_pipeline/tests/test_corpus_doctor_sweep.py -p no:cacheprovider
"""

import importlib.util
import json
import logging
import sys
import types
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# The REAL finding builder every doctor lint goes through -- fixtures below
# are built with it so they are the production shape by construction, not a
# hand-copy that can drift from it.
from unified_pipeline.doctor.shared import _finding  # noqa: E402


def _load_cli():
    spec = importlib.util.spec_from_file_location(
        "corpus_doctor_sweep_cli", _ROOT / "scripts" / "corpus_doctor_sweep.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_run(corpus_dir: Path, run_id: str, uid: str) -> None:
    """A run dir with just enough for `_find_uid` to resolve `uid`."""
    outputs = corpus_dir / run_id / "outputs"
    outputs.mkdir(parents=True)
    (outputs / f"{uid}_fields.json").write_text("{}", encoding="utf-8")


def _make_skipped_run(corpus_dir: Path, run_id: str) -> None:
    """A run dir with no artifacts at all -- `_find_uid` returns None."""
    (corpus_dir / run_id / "outputs").mkdir(parents=True)


def test_stage_prunes_symlinks_a_prior_run_left_for_the_same_uid(tmp_path):
    """T1.1/T2.1: `work/<uid>` persists across invocations. Stage uid `aaa111`
    from a run that has `_entries.json`, then stage it again from a run that
    doesn't -- the second call must not leave the first run's `_entries.json`
    symlink behind.
    """
    cli = _load_cli()
    corpus = tmp_path / "corpus"
    work = tmp_path / "work"
    uid = "aaa111"

    run1 = corpus / "run1" / "outputs"
    run1.mkdir(parents=True)
    (run1 / f"{uid}_fields.json").write_text("{}", encoding="utf-8")
    (run1 / f"{uid}_entries.json").write_text("{}", encoding="utf-8")
    cli.stage(run1, uid, work)
    assert (work / uid / "stage_2_entry_extraction" / f"{uid}_entries.json").exists()

    run2 = corpus / "run2" / "outputs"
    run2.mkdir(parents=True)
    (run2 / f"{uid}_fields.json").write_text("{}", encoding="utf-8")
    cli.stage(run2, uid, work)

    assert not (work / uid / "stage_2_entry_extraction").exists(), (
        "run2 has no _entries.json -- the stale symlink from run1 must be gone")
    assert (work / uid / "stage_4_field_extraction" / f"{uid}_fields.json").resolve() == (
        run2 / f"{uid}_fields.json").resolve()


def test_stage_finds_source_docx_under_flat_layout_input_dir(tmp_path):
    """T1.2/T2.7: a flat-layout run (no outputs/ subdir) still has its source
    docx at <run_root>/input/<uid>.docx, a sibling of the jsons themselves,
    not <corpus>/input.
    """
    cli = _load_cli()
    uid = "aaa111"
    run_root = tmp_path / "corpus" / "run1"
    run_root.mkdir(parents=True)
    (run_root / f"{uid}_fields.json").write_text("{}", encoding="utf-8")
    input_dir = run_root / "input"
    input_dir.mkdir()
    (input_dir / f"{uid}.docx").write_text("source", encoding="utf-8")
    work = tmp_path / "work"

    root = cli.stage(run_root, uid, work)

    staged_source = root / f"{uid}.docx"
    assert staged_source.is_symlink(), "source docx must be staged for a flat-layout run"
    assert staged_source.resolve() == (input_dir / f"{uid}.docx").resolve()


# The destinations run_doctor's own `_ARTIFACTS` (run_doctor.py:459-467)
# actually globs, as literals -- not read back off `cli.SUFFIX_DIR`. A test
# that builds its expectation from the same dict the code under test uses
# passes even if that dict maps a suffix to a directory run_doctor never
# looks at; only a literal, independently-sourced expectation catches that.
_EXPECTED_SUFFIX_DIRS = {
    "_segmented.json": "stage_1a_segmentation",
    "_entries.json": "stage_2_entry_extraction",
    "_classified.json": "stage_3b_classified_entries",
    "_fields.json": "stage_4_field_extraction",
    "_enriched.json": "stage_5_enrichment",
    "_wcm.docx": "stage_6_wcm_documents",
    "_render_warnings.json": "stage_6_wcm_documents",
}


def test_stage_links_every_suffix_and_the_source_docx(tmp_path):
    """T2.3/T2.9: the central integration point between the S3-flat corpus
    and run_doctor -- one call to stage() covering every registered
    SUFFIX_DIR suffix plus the archived source docx, asserting every
    expected destination exists and points at the right file. None of the
    other tests in this file exercise stage()'s full suffix loop.
    """
    cli = _load_cli()
    assert set(cli.SUFFIX_DIR) == set(_EXPECTED_SUFFIX_DIRS), (
        "this test does not cover every suffix SUFFIX_DIR registers")
    uid = "aaa111"
    run_root = tmp_path / "corpus" / "run1"
    outputs = run_root / "outputs"
    outputs.mkdir(parents=True)
    for suffix in cli.SUFFIX_DIR:
        (outputs / f"{uid}{suffix}").write_text(f"content for {suffix}", encoding="utf-8")
    input_dir = run_root / "input"
    input_dir.mkdir()
    (input_dir / f"{uid}.docx").write_text("source", encoding="utf-8")
    work = tmp_path / "work"

    root = cli.stage(run_root, uid, work)

    for suffix, stagedir in _EXPECTED_SUFFIX_DIRS.items():
        dest = root / stagedir / f"{uid}{suffix}"
        assert dest.is_symlink(), f"missing staged link for suffix {suffix!r}"
        assert dest.resolve() == (outputs / f"{uid}{suffix}").resolve()

    source_link = root / f"{uid}.docx"
    assert source_link.is_symlink()
    assert source_link.resolve() == (input_dir / f"{uid}.docx").resolve()


def test_stage_prefers_exact_uid_docx_and_rejects_a_prefix_decoy(tmp_path):
    """T1.3: `web05` must not resolve to `web050.docx` -- the traced
    2026-07-15 misattribution. With only the decoy present, no source is
    staged; with the canonical `web05.docx` also present, that one wins.
    """
    cli = _load_cli()
    uid = "web05"
    run_root = tmp_path / "corpus" / "run1"
    input_dir = run_root / "input"
    input_dir.mkdir(parents=True)
    (run_root / f"{uid}_fields.json").write_text("{}", encoding="utf-8")
    (input_dir / "web050.docx").write_text("decoy", encoding="utf-8")
    work = tmp_path / "work"

    root = cli.stage(run_root, uid, work)
    assert list(root.glob("*.docx")) == [], (
        "the web050 prefix-decoy must not be staged as web05's source at all")

    (input_dir / f"{uid}.docx").write_text("real", encoding="utf-8")
    root2 = cli.stage(run_root, uid, work)
    staged = root2 / f"{uid}.docx"
    assert staged.is_symlink()
    assert staged.resolve() == (input_dir / f"{uid}.docx").resolve()


def test_sweep_records_failure_when_source_docx_candidates_are_ambiguous(tmp_path, monkeypatch):
    """T1.3/T2.7: two files both pass `_uid_owns` for the same uid and
    neither is the canonical name -- sweep() must record a failure naming
    the candidates, never pick one silently.
    """
    cli = _load_cli()
    corpus = tmp_path / "corpus"
    uid = "aaa111"
    _make_run(corpus, "amb1", uid)
    input_dir = corpus / "amb1" / "input"
    input_dir.mkdir(parents=True)
    (input_dir / f"{uid}_v1.docx").write_text("a", encoding="utf-8")
    (input_dir / f"{uid}_v2.docx").write_text("b", encoding="utf-8")
    work = tmp_path / "work"

    reports, failures, skipped, duplicates = cli.sweep(corpus, ["amb1"], work)

    assert reports == {}
    assert skipped == []
    assert "amb1" in failures
    assert failures["amb1"]["exception"] == "AmbiguousSourceDocxError"
    assert "AmbiguousSourceDocxError" in failures["amb1"]["traceback"]
    assert f"{uid}_v1.docx" in failures["amb1"]["error"]
    assert f"{uid}_v2.docx" in failures["amb1"]["error"]


def test_clear_staged_root_refuses_a_stray_regular_file(tmp_path):
    """r3924634631 item 1 (correction): `_clear_staged_root`'s refusal
    fires on ANY entry under `work/<uid>` that is not a symlink or a
    directory -- not only a directory this script itself created. A stray
    regular file left there by something else must abort staging with
    `StagingRootNotOwnedError`, recorded as that run's failure, and the
    file itself must be left in place, not removed.
    """
    cli = _load_cli()
    corpus = tmp_path / "corpus"
    uid = "aaa111"
    _make_run(corpus, "run1", uid)
    work = tmp_path / "work"
    root = work / uid
    root.mkdir(parents=True)
    stray = root / "not_mine.txt"
    stray.write_text("someone else wrote this", encoding="utf-8")

    reports, failures, skipped, duplicates = cli.sweep(corpus, ["run1"], work)

    assert reports == {}
    assert skipped == []
    assert "run1" in failures
    assert failures["run1"]["exception"] == "StagingRootNotOwnedError"
    assert stray.exists() and stray.read_text(encoding="utf-8") == "someone else wrote this", (
        "the stray file must be left in place, not removed")


def test_find_uid_is_found_via_an_early_stage_only_suffix(tmp_path):
    """T1.4/T2.7: a run that crashed before stage 4 has only
    `_segmented.json` -- _find_uid must not report it as empty.
    """
    cli = _load_cli()
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    (outputs / "aaa111_segmented.json").write_text("{}", encoding="utf-8")
    assert cli._find_uid(outputs) == "aaa111"


def test_find_uid_rejects_a_run_dir_naming_two_uids(tmp_path):
    cli = _load_cli()
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    (outputs / "aaa111_segmented.json").write_text("{}", encoding="utf-8")
    (outputs / "bbb222_fields.json").write_text("{}", encoding="utf-8")
    try:
        cli._find_uid(outputs)
    except cli.MultipleUidsInRunError as e:
        assert "aaa111" in str(e) and "bbb222" in str(e)
    else:
        raise AssertionError("expected MultipleUidsInRunError")


def test_sweep_records_failure_for_a_run_id_escaping_corpus_dir(tmp_path):
    """T1.5: run_id "../x" must not resolve outside corpus_dir."""
    cli = _load_cli()
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    work = tmp_path / "work"

    reports, failures, skipped, duplicates = cli.sweep(corpus, ["../x"], work)

    assert reports == {}
    assert skipped == []
    assert "../x" in failures
    assert failures["../x"]["exception"] == "RunIdEscapesCorpusError"
    assert "RunIdEscapesCorpusError" in failures["../x"]["traceback"]


def test_finding_contract_matches_doctor_shared_finding():
    """T2.10: the sweep's `Finding` record is the shape `_finding()` in
    doctor/shared.py actually emits -- same keys, no more, no fewer -- so
    a key added or dropped on either side fails here, not as a silent
    `_validate_finding` rejection of every real report.
    """
    cli = _load_cli()
    real = _finding("pipe_leaks", "WARN", "x")
    assert set(cli.FINDING_KEYS) == set(real)
    assert set(cli.Finding.__annotations__) == set(real)


@pytest.mark.parametrize("missing", ["lint", "severity", "message", "evidence"])
def test_aggregate_raises_on_missing_required_field(missing):
    """Every `Finding` key is required -- including `evidence`, which
    `_finding()` always sets, so its absence means the finding did not
    come from the builder."""
    cli = _load_cli()
    f = _finding("pipe_leaks", "WARN", "x")
    del f[missing]
    reports = {"r1": {"findings": [f]}}
    with pytest.raises(cli.MalformedFindingError, match=rf"run_id='r1'.*{missing}"):
        cli.aggregate(reports)


def test_aggregate_raises_on_unknown_severity():
    cli = _load_cli()
    reports = {"r1": {"findings": [_finding("pipe_leaks", "CRITICAL", "x")]}}
    with pytest.raises(cli.MalformedFindingError, match=r"run_id='r1'.*CRITICAL"):
        cli.aggregate(reports)


def test_aggregate_raises_on_unknown_lint():
    cli = _load_cli()
    reports = {"r1": {"findings": [_finding("not_a_real_lint", "WARN", "x")]}}
    with pytest.raises(cli.MalformedFindingError, match=r"run_id='r1'.*not_a_real_lint"):
        cli.aggregate(reports)


def test_aggregate_raises_on_report_without_findings():
    """T2.10: a report lacking `findings` (here: a fake run_doctor's return)
    used to be a bare KeyError inside aggregate()'s loop. The boundary now
    names the run and the keys it did see.
    """
    cli = _load_cli()
    reports = {"r1": {"document_uid": "aaa111", "note": "café"}}
    with pytest.raises(cli.MalformedReportError,
                       match=r"run_id='r1'.*no 'findings' key.*document_uid"):
        cli.aggregate(reports)


@pytest.mark.parametrize("bad_report", [
    "not a dict at all",
    {"findings": {"lint": "pipe_leaks"}},
    {"findings": None},
], ids=["report-is-a-str", "findings-is-a-dict", "findings-is-None"])
def test_aggregate_raises_when_report_or_findings_has_the_wrong_type(bad_report):
    cli = _load_cli()
    with pytest.raises(cli.MalformedReportError, match=r"run_id='r1'"):
        cli.aggregate({"r1": bad_report})


def test_aggregate_raises_on_a_non_dict_finding():
    cli = _load_cli()
    reports = {"r1": {"findings": ["pipe_leaks WARN x"]}}
    with pytest.raises(cli.MalformedFindingError, match=r"run_id='r1'.*not a dict"):
        cli.aggregate(reports)


def test_aggregate_ranks_by_affected_count_and_tracks_skip_vs_ran():
    """Ported from the deleted _selftest (T2.9): rep A has a pipe_leaks WARN
    and a skipped segmentation; rep B has a pipe_leaks ERROR and nothing
    skipped.
    """
    cli = _load_cli()
    reports = {
        "A": {"findings": [
            _finding("pipe_leaks", "WARN", "x"),
            _finding("segmentation", "INFO", "skipped: missing source"),
        ]},
        "B": {"findings": [
            _finding("pipe_leaks", "ERROR", "y"),
        ]},
    }
    rank = {r["lint"]: r for r in cli.aggregate(reports)}
    assert rank["pipe_leaks"]["cvs_affected"] == 2, "both reps have a >=WARN pipe_leaks finding"
    assert rank["pipe_leaks"]["cvs_error"] == 1, "only rep B is ERROR"
    assert rank["pipe_leaks"]["cvs_ran"] == 2, "pipe_leaks ran on both (never skipped)"
    assert rank["segmentation"]["cvs_ran"] == 1, "segmentation skipped on A, ran (clean) on B"
    assert rank["segmentation"]["cvs_affected"] == 0
    assert cli.aggregate(reports)[0]["lint"] == "pipe_leaks", "ranked first by affected count"


def test_skip_detection_matches_what_run_doctor_actually_emits(tmp_path):
    """T1.7/T2.5: a finding has no structured skip status (#750), so
    aggregate() depends on the wording `_ready()` in run_doctor.py emits.
    This runs the REAL run_doctor on an empty root -- every registered lint
    skips -- through aggregate(), so a rewording on either side fails here
    instead of silently counting every skipped lint as 'ran clean'.

    no_output (#745) is the one exception, not a rewording drift: it is
    dispatched by hand on artifact PATHS rather than `_ready()`-checked
    content, and its condition ('reached stage 4 but produced no output')
    is simply False on a root with no stage 4 either -- so it emits NOTHING
    at all here, neither a skip note nor a real finding. aggregate() (round-2
    N5) special-cases it: 'ran' for no_output reads `artifacts.stage_4`
    directly rather than the generic 'not explicitly skipped' rule, so an
    incomplete run that never reached stage 4 is not counted as having run
    this lint either.
    """
    from unified_pipeline.run_doctor import run_doctor

    cli = _load_cli()
    rep = run_doctor(tmp_path, "aaa111")

    real_lints = set(cli.ALL_LINTS) - {"no_output"}
    assert {f["lint"] for f in rep["findings"]} == real_lints, (
        "every registered lint but no_output must skip on an empty root")
    assert {f["severity"] for f in rep["findings"]} == {"INFO"}
    assert all(f["message"].startswith(cli.SKIPPED_MISSING_PREFIX)
               for f in rep["findings"]), "the sweep's prefix must be what _ready() emits"
    assert rep["artifacts"]["stage_4"] is None, "an empty root never reached stage 4"

    rows = cli.aggregate({"r1": rep})
    expected_ran = {lint: 0 for lint in cli.ALL_LINTS}
    assert {r["lint"]: r["cvs_ran"] for r in rows} == expected_ran, (
        "a skipped lint -- and no_output on a run that never reached stage 4 "
        "-- must not count as having run")
    assert {r["cvs_affected"] for r in rows} == {0}


def test_no_output_counts_as_ran_once_stage_4_actually_happened(tmp_path):
    """The mirror of the test above: a rep whose stage 4 DID produce output
    (so no_output's own precondition held, even though this synthetic rep
    reports no finding for it) must count as 'ran' for aggregate()'s
    no_output row -- the round-2 N5 fix reads `artifacts.stage_4`, not the
    generic 'not explicitly skipped' rule, so this must not regress to
    always-0 either."""
    cli = _load_cli()
    rep = {
        "artifacts": {"stage_4": str(tmp_path / "X_fields.json"), "stage_2": None},
        "findings": [],
    }
    rows = cli.aggregate({"r1": rep})
    ran = {r["lint"]: r["cvs_ran"] for r in rows}
    assert ran["no_output"] == 1
    # unaffected lints keep the generic "not explicitly skipped -> ran" rule,
    # unchanged by the no_output special case -- an empty findings list means
    # nothing was marked skipped, so every other lint still counts as ran.
    assert ran["segmentation"] == 1


def test_skip_detection_requires_prefix_not_mere_substring():
    """Pins SKIPPED_MISSING_PREFIX matching to `.startswith`, not `in`: a
    message that merely CONTAINS the prefix mid-string is not a skip.
    """
    cli = _load_cli()
    reports = {"r1": {"findings": [
        _finding("pipe_leaks", "INFO", "not skipped: missing anything, ran fine"),
    ]}}
    rank = {r["lint"]: r for r in cli.aggregate(reports)}
    assert rank["pipe_leaks"]["cvs_ran"] == 1, (
        "a message carrying the prefix mid-string must not be counted as skipped")
    assert rank["pipe_leaks"]["cvs_affected"] == 0


def test_resolve_outputs_dir_tolerates_nested_and_flat_layouts_and_never_raises(tmp_path):
    """Ported from the deleted _selftest (T2.9)."""
    cli = _load_cli()
    (tmp_path / "nested" / "outputs").mkdir(parents=True)
    (tmp_path / "flat").mkdir()
    assert cli._resolve_outputs_dir(tmp_path / "nested") == tmp_path / "nested" / "outputs"
    assert cli._resolve_outputs_dir(tmp_path / "flat") == tmp_path / "flat"
    assert cli._resolve_outputs_dir(tmp_path / "absent") == tmp_path / "absent"


def test_sweep_records_a_second_run_for_the_same_uid_as_a_duplicate(tmp_path, monkeypatch):
    """T2.2: two run_ids sharing a uid must produce one report, first run
    wins, and the second is listed under duplicates -- never double counted.
    """
    cli = _load_cli()
    corpus = tmp_path / "corpus"
    _make_run(corpus, "run1", "aaa111")
    _make_run(corpus, "run2", "aaa111")
    work = tmp_path / "work"

    monkeypatch.setattr(cli, "run_doctor", lambda root, uid: {"findings": []})

    result = cli.sweep(corpus, ["run1", "run2"], work)

    assert isinstance(result, cli.SweepResult)
    assert result._fields == ("reports", "failures", "skipped", "duplicates"), (
        "named fields, in the order main() and the --out payload use them")
    assert set(result.reports) == {"run1"}
    assert result.failures == {}
    assert result.skipped == []
    assert result.duplicates == {"run2": {"uid": "aaa111", "first_run_id": "run1"}}
    reports, failures, skipped, duplicates = result
    assert (reports, failures, skipped, duplicates) == tuple(result), (
        "positional unpacking, which every other test here uses, still works")


def test_sweep_failure_carries_traceback_and_good_run_still_reported(tmp_path, monkeypatch):
    """Positive control: on dev, `sweep()` returns one bare dict, not a 3-tuple,
    so `reports, failures, skipped = sweep(...)` raises ValueError immediately
    -- this test fails against dev's sweep() before it even reaches the
    assertions below, and dev's `failures[run_id]` is a plain string with no
    traceback even if you unpack around that.
    """
    cli = _load_cli()
    corpus = tmp_path / "corpus"
    _make_run(corpus, "good1", "aaa111")
    _make_run(corpus, "bad1", "bbb222")
    work = tmp_path / "work"

    def fake_run_doctor(root, uid):
        if uid == "bbb222":
            raise ValueError("boom: lint_x could not read artifact")
        return {"findings": []}

    monkeypatch.setattr(cli, "run_doctor", fake_run_doctor)

    reports, failures, skipped, duplicates = cli.sweep(corpus, ["good1", "bad1"], work)

    assert "good1" in reports, "the good run's report must still be produced"
    assert skipped == []
    assert set(failures) == {"bad1"}
    assert failures["bad1"]["exception"] == "ValueError"
    assert failures["bad1"]["error"] == "boom: lint_x could not read artifact"
    assert "ValueError" in failures["bad1"]["traceback"]
    assert "boom: lint_x could not read artifact" in failures["bad1"]["traceback"]
    assert "in fake_run_doctor" in failures["bad1"]["traceback"], (
        "must be a real stack trace, not just the message repeated")


def test_sweep_lets_a_bug_in_its_own_staging_code_propagate(tmp_path, monkeypatch):
    """T1.6: the per-run catch around staging is for expected failures
    (typed SweepRunError, OSError). A TypeError raised by stage() itself is
    a bug in this script -- it must fail loudly, not be filed under
    failures[run_id] as if the run were bad.
    """
    cli = _load_cli()
    corpus = tmp_path / "corpus"
    _make_run(corpus, "run1", "aaa111")
    _make_run(corpus, "run2", "bbb222")
    doctored = []

    def broken_stage(run_root, uid, work):
        raise TypeError("unsupported operand: a bug in stage()")

    monkeypatch.setattr(cli, "stage", broken_stage)
    monkeypatch.setattr(cli, "run_doctor",
                         lambda root, uid: doctored.append(uid) or {"findings": []})

    with pytest.raises(TypeError, match="a bug in stage"):
        cli.sweep(corpus, ["run1", "run2"], tmp_path / "work")
    assert doctored == [], "the bug must abort before any run is doctored"


def test_sweep_records_an_oserror_from_staging_and_continues(tmp_path, monkeypatch):
    """T1.6: a filesystem failure while staging one run is that run's
    problem -- recorded with its class name, and the next run still runs.
    """
    cli = _load_cli()
    corpus = tmp_path / "corpus"
    _make_run(corpus, "locked", "aaa111")
    _make_run(corpus, "good1", "bbb222")
    work = tmp_path / "work"

    def stage_or_deny(run_root, uid, work):
        if uid == "aaa111":
            raise PermissionError(13, "Permission denied", str(work / uid))
        return work / uid

    monkeypatch.setattr(cli, "stage", stage_or_deny)
    monkeypatch.setattr(cli, "run_doctor", lambda root, uid: {"findings": []})

    reports, failures, skipped, duplicates = cli.sweep(corpus, ["locked", "good1"], work)

    assert set(reports) == {"good1"}
    assert set(failures) == {"locked"}
    assert failures["locked"]["exception"] == "PermissionError"
    assert "Permission denied" in failures["locked"]["error"]
    assert "in stage_or_deny" in failures["locked"]["traceback"]


@pytest.mark.parametrize("exc", [
    TypeError("lint_x: 'NoneType' object is not subscriptable"),
    KeyError("stage_4"),
    RuntimeError("doctor exploded"),
], ids=lambda e: type(e).__name__)
def test_sweep_records_a_run_doctor_crash_of_any_class(tmp_path, monkeypatch, exc):
    """#563's own case, kept broad on purpose: a lint crashing on one CV's
    artifacts -- whatever the exception class -- is that run's failure and
    the other runs' reports survive. Only the sweep's own staging code is
    held to the narrow catch.
    """
    cli = _load_cli()
    corpus = tmp_path / "corpus"
    _make_run(corpus, "bad1", "aaa111")
    _make_run(corpus, "good1", "bbb222")

    def fake_run_doctor(root, uid):
        if uid == "aaa111":
            raise exc
        return {"findings": []}

    monkeypatch.setattr(cli, "run_doctor", fake_run_doctor)

    reports, failures, skipped, duplicates = cli.sweep(corpus, ["bad1", "good1"], tmp_path / "work")

    assert set(reports) == {"good1"}
    assert failures["bad1"]["exception"] == type(exc).__name__
    assert "in fake_run_doctor" in failures["bad1"]["traceback"]


def _partial_setup_a_failure(corpus: Path, cli: types.ModuleType, monkeypatch: pytest.MonkeyPatch) -> str:
    _make_run(corpus, "good1", "aaa111")
    _make_run(corpus, "bad1", "bbb222")

    def fake_run_doctor(root, uid):
        if uid == "bbb222":
            raise ValueError("boom")
        return {"findings": []}

    monkeypatch.setattr(cli, "run_doctor", fake_run_doctor)
    return "good1,bad1"


def _partial_setup_skipped_only(corpus: Path, cli: types.ModuleType, monkeypatch: pytest.MonkeyPatch) -> str:
    _make_run(corpus, "good1", "aaa111")
    _make_skipped_run(corpus, "skip1")
    monkeypatch.setattr(cli, "run_doctor", lambda root, uid: {"findings": []})
    return "good1,skip1"


def _partial_setup_duplicate_only(corpus: Path, cli: types.ModuleType, monkeypatch: pytest.MonkeyPatch) -> str:
    _make_run(corpus, "good1", "aaa111")
    _make_run(corpus, "dup1", "aaa111")
    monkeypatch.setattr(cli, "run_doctor", lambda root, uid: {"findings": []})
    return "good1,dup1"


@pytest.mark.parametrize("setup", [
    _partial_setup_a_failure, _partial_setup_skipped_only, _partial_setup_duplicate_only,
], ids=["failure", "skipped-only", "duplicate-only"])
def test_main_returns_nonzero_on_partial_failure_by_default(tmp_path, monkeypatch, setup):
    """T2.4: main() used to return 0 whenever at least one run succeeded,
    even with other requested runs failed -- CI could report success on an
    incomplete corpus. Fail closed by default now.

    Parametrized over the three ways a sweep can be incomplete with no
    outright failure: a failed run (positive control), a skipped-only run,
    and a duplicate-only run -- a mutant reading only
    `bool(result.failures)` still exits non-zero on the failure case but
    wrongly exits 0 on the other two.
    """
    cli = _load_cli()
    corpus = tmp_path / "corpus"
    run_ids = setup(corpus, cli, monkeypatch)

    assert cli.main([str(corpus), run_ids]) != 0


def test_main_exits_zero_on_partial_failure_with_allow_partial(tmp_path, monkeypatch):
    cli = _load_cli()
    corpus = tmp_path / "corpus"
    _make_run(corpus, "good1", "aaa111")
    _make_run(corpus, "bad1", "bbb222")

    def fake_run_doctor(root, uid):
        if uid == "bbb222":
            raise ValueError("boom")
        return {"findings": []}

    monkeypatch.setattr(cli, "run_doctor", fake_run_doctor)

    assert cli.main([str(corpus), "good1,bad1", "--allow-partial"]) == 0


def test_main_returns_nonzero_when_every_run_fails_or_is_skipped(tmp_path, monkeypatch):
    """#5.5: nothing to report is a failure, not a pass -- dev returns 0 here."""
    cli = _load_cli()
    corpus = tmp_path / "corpus"
    _make_run(corpus, "bad1", "bbb222")
    _make_skipped_run(corpus, "skip1")

    def fake_run_doctor(root, uid):
        raise RuntimeError("doctor exploded")

    monkeypatch.setattr(cli, "run_doctor", fake_run_doctor)

    assert cli.main([str(corpus), "bad1,skip1"]) != 0


def test_out_payload_contains_failures_and_skipped(tmp_path, monkeypatch):
    cli = _load_cli()
    corpus = tmp_path / "corpus"
    _make_run(corpus, "good1", "aaa111")
    _make_run(corpus, "bad1", "bbb222")
    _make_skipped_run(corpus, "skip1")
    out = tmp_path / "sweep.json"

    def fake_run_doctor(root, uid):
        if uid == "bbb222":
            raise ValueError("boom")
        return {"findings": []}

    monkeypatch.setattr(cli, "run_doctor", fake_run_doctor)

    assert cli.main([str(corpus), "good1,bad1,skip1", "--out", str(out),
                      "--allow-partial"]) == 0
    payload = json.loads(out.read_text(encoding="utf-8"))

    assert "failures" in payload and "bad1" in payload["failures"]
    assert payload["failures"]["bad1"]["error"] == "boom"
    assert "traceback" in payload["failures"]["bad1"]
    assert "skipped" in payload and payload["skipped"] == ["skip1"]


def test_out_payload_is_written_as_utf8_with_literal_non_ascii(tmp_path, monkeypatch):
    """T2.8: write_text must use encoding="utf-8" explicitly, and the JSON
    itself must carry non-ASCII text literally (ensure_ascii=False), not
    \\uXXXX-escaped.
    """
    cli = _load_cli()
    corpus = tmp_path / "corpus"
    _make_run(corpus, "good1", "aaa111")
    out = tmp_path / "sweep.json"

    monkeypatch.setattr(cli, "run_doctor",
                         lambda root, uid: {"findings": [], "note": "café"})

    assert cli.main([str(corpus), "good1", "--out", str(out)]) == 0

    raw = out.read_bytes()
    assert "café".encode("utf-8") in raw, "non-ASCII must be written as literal UTF-8 bytes"
    assert b"\\u" not in raw, "ensure_ascii=False means no \\uXXXX escaping"


def test_diagnostics_go_through_the_logger_not_bare_stderr_prints(tmp_path, monkeypatch, capsys, caplog):
    """The old `print(..., file=sys.stderr)` sites at :107/:114-117 must be
    gone: nothing reaches the process's stderr stream directly any more (the
    module never calls `logging.basicConfig`, so a bare `logger.warning` here
    is only visible to caplog, not to capsys) -- and stdout carries only the
    ranking table, the script's one deliberate print() block.
    """
    cli = _load_cli()
    corpus = tmp_path / "corpus"
    _make_run(corpus, "good1", "aaa111")
    _make_run(corpus, "bad1", "bbb222")
    _make_skipped_run(corpus, "skip1")

    def fake_run_doctor(root, uid):
        if uid == "bbb222":
            raise ValueError("boom")
        return {"findings": []}

    monkeypatch.setattr(cli, "run_doctor", fake_run_doctor)

    with caplog.at_level(logging.WARNING):
        rc = cli.main([str(corpus), "good1,bad1,skip1", "--allow-partial"])
    assert rc == 0

    captured = capsys.readouterr()
    assert captured.err == "", "diagnostics must not bypass logging onto stderr"
    assert "!!" not in captured.out, "the old bare diagnostic marker must be gone"
    assert "Doctor sweep: 1 distinct CVs" in captured.out
    assert "lint" in captured.out and "CVs affected" in captured.out

    assert "skip1: no artifacts found, skipping" in caplog.text
    assert "run_doctor failed for run_id=bad1" in caplog.text
    assert "1 run(s) failed and were dropped" in caplog.text
