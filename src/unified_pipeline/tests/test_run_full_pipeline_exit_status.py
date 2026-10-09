"""Regression guard for issue #443: a stage-6 crash printed PIPELINE COMPLETE
and exited 0, so batches silently under-produced.

Every stage runs inside ``run_stage``, which records its exception as
``ctx.results['stage_X'].error`` and continues. Nothing read that back, and
``__main__`` called ``main()`` rather than ``sys.exit(main())``, so the process
always exited 0. Two runs of the 2026-07-25 corpus batch (web094, web147)
produced no document and reported success; the batch script only noticed because
it separately checks whether the docx exists.

``failed_stages`` is the aggregation that was missing. The tests below cover it
directly and then run ``main()`` itself with the twelve stage runners stubbed,
because a suite that never executes ``main()`` cannot notice this bug returning
-- and "nobody notices" is the entire nature of the defect.

    python3 -m pytest src/unified_pipeline/tests/test_run_full_pipeline_exit_status.py -p no:cacheprovider

The stubs take the production keyword arguments by name and record them, so an
orchestration call that changes shape is a TypeError here rather than a silent
pass (#780 review: `*args, **kwargs` stubs could not detect a wrong call).

Self-contained: no DB, no network, no LLM. The one subprocess test runs the CLI
with the LLM credentials stripped from its environment, so it cannot reach a
model even if a stage tried.
"""

import ast
import contextvars
import hashlib
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import pytest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import run_full_pipeline  # noqa: E402
from unified_pipeline.core import prompt_logger  # noqa: E402

failed_stages = run_full_pipeline.failed_stages
StageResult = run_full_pipeline.StageResult


class _Streams(NamedTuple):
    """One in-process run of main(): its return value and both output streams."""

    rc: int
    out: str
    err: str


@pytest.fixture(autouse=True)
def restore_logging_after_each_test():
    """configure_cli_logging() installs handlers on real, process-wide loggers.
    Without this they leak into every test that runs after -- the narration
    handler still holds the previous test's capsys stream, so the next test's
    assertions read output that was never emitted in it (#780).

    Also imported by test_run_full_pipeline_stdout_contract, which runs the same
    harness.
    """
    cli = logging.getLogger(run_full_pipeline.logger.name)
    root = logging.getLogger()
    saved = (cli.handlers[:], cli.level, cli.propagate, root.handlers[:], root.level)
    yield
    cli.handlers[:], cli.level, cli.propagate, root.handlers[:], root.level = saved


def _configure_cli_logging_keeping_pytests_handlers():
    """What __main__ does, minus the collateral damage to the test run.

    dictConfig() removes every handler already on the root logger, and pytest's
    caplog handler is one of them, so a plain call would blank caplog.records
    for the rest of the test. The borrowed handlers go back afterwards; the
    fixture above undoes the whole thing at teardown.
    """
    root = logging.getLogger()
    borrowed = root.handlers[:]
    run_full_pipeline.configure_cli_logging()
    for handler in borrowed:
        if handler not in root.handlers:
            root.addHandler(handler)


def _enter_tmp_repo(tmp_path, monkeypatch):
    """Run from an empty directory whose outputs tree is its own.

    The CLI anchors _OUTPUTS_ROOT on the script's location (#490), so chdir
    alone no longer isolates a test from the real repo's outputs. Pinning the
    root back to the cwd-relative spelling keeps every artifact under tmp_path.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(run_full_pipeline, '_OUTPUTS_ROOT', _OUT)


def _repo_outputs_snapshot():
    """Every path under the real repo outputs tree (a worktree's is a symlink
    to the shared corpus farm), so a test can prove it wrote nothing there."""
    root = _ROOT / 'src' / 'unified_pipeline' / 'outputs'
    return sorted(os.path.join(d, n) for d, dirs, files in os.walk(root, followlinks=True)
                  for n in dirs + files)


@pytest.fixture(scope='module', autouse=True)
def nothing_in_this_module_writes_the_real_outputs_tree():
    """#490: the CLI's root is script-anchored, so chdir no longer isolates a
    test. Any test here that forgets to redirect it lands in the real tree."""
    before = _repo_outputs_snapshot()
    yield
    assert _repo_outputs_snapshot() == before


def _result(stage, **kwargs):
    return StageResult(stage=stage, **kwargs)


def _real_docx(tmp_path, name="out.docx"):
    """A stage-6 output that exists on disk, which is what counts as delivered."""
    path = tmp_path / name
    path.write_bytes(b"PK\x03\x04fake")
    return str(path)


def test_clean_run_has_no_failed_stages(tmp_path):
    all_results = {
        'stage_1b': _result('1b', output_file='a.json'),
        'stage_2': _result('2', output_file='b.json', stats={'total_entries': 400}),
        'stage_3b': _result('3b', output_file='c.json', stats={'entries_classified': 400}),
        'stage_4': _result('4', output_file='d.json', stats={'entries_extracted': 400}),
        'stage_6': _result('6', output_file=_real_docx(tmp_path), duration_seconds=12.0),
    }
    assert failed_stages(all_results) == []


def test_stage_6_crash_is_reported():
    """The #443 case: everything upstream fine, no document, exit 0."""
    all_results = {
        'stage_4': _result('4', output_file='d.json'),
        'stage_6': _result('6', error="AttributeError: 'dict' object has no attribute 'replace'"),
    }
    assert failed_stages(all_results) == ['stage_6']


def test_any_stage_error_is_reported_not_just_stage_6(tmp_path):
    """The class fix: all eleven wrappers share the swallow-and-continue shape."""
    docx = _real_docx(tmp_path)
    for stage in ('stage_1b', 'stage_2', 'stage_3a', 'stage_3b', 'stage_4',
                  'stage_4.5', 'stage_5', 'stage_5b', 'stage_5c', 'stage_5d'):
        assert failed_stages({stage: _result(stage, error='boom'),
                              'stage_6': _result('6', output_file=docx)}) == [stage]


def test_stage_skipped_for_a_missing_prerequisite_is_a_failure():
    """A skip only happens downstream of an earlier failure, so it is not benign."""
    all_results = {
        'stage_2': _result('2', error='boom'),
        'stage_3b': _result('3b', skipped_reason='Stage 2 required'),
        'stage_6': _result('6', skipped_reason='Earlier stage output required'),
    }
    assert failed_stages(all_results) == ['stage_2', 'stage_3b', 'stage_6']


def test_stage_6_without_an_output_file_is_a_failure():
    """Stage 6 is the deliverable: no docx means the run produced nothing."""
    assert failed_stages({'stage_6': _result('6', duration_seconds=3.0)}) == ['stage_6']


def test_a_stage_that_never_ran_is_not_a_failure():
    """`--stage 2` leaves the rest out of all_results entirely."""
    assert failed_stages({'stage_2': _result('2', output_file='b.json')}) == []
    assert failed_stages({}) == []


# -- r3960719620: a recorded path is not a delivered document ---------------
# Mutant that kills these two: drop `Path(output_file).is_file()` from
# `_produced_a_file` and check only that the string is non-empty -- exactly the
# pre-review implementation.


def test_stage6_nonexistent_output_is_failure(tmp_path):
    missing = tmp_path / "missing.docx"
    assert failed_stages({'stage_6': _result('6', output_file=str(missing))}) == ['stage_6']


def test_stage6_directory_is_not_valid_output(tmp_path):
    output_dir = tmp_path / "output.docx"
    output_dir.mkdir()
    assert failed_stages({'stage_6': _result('6', output_file=str(output_dir))}) == ['stage_6']


# ---------------------------------------------------------------------------
# End-to-end over main() itself.
#
# Unit-testing failed_stages alone is not enough: it leaves the three lines that
# ARE the fix unguarded (the aggregation call, the banner conditional, and
# `return result.exit_code`). Deleting the return reduces `sys.exit(main())`
# to `sys.exit(None)` and restores the exact #443 behaviour -- always exit 0 --
# so a suite that does not run main() cannot notice the bug coming back, which
# is the very failure this issue is about.
#
# The twelve stage runners are stubbed, so this touches no LLM, no network and
# no real docx.
# ---------------------------------------------------------------------------

UID = "testcv"

# Synthetic per-stage costs the stubs report (#1177): distinct so a stage
# dropped from the total changes the sum.
_STAGE_4_5_COST = 0.04
_STAGE_5C_COST = 0.05
_STAGE_5D_COST = 0.06
_STAGE_6_COST = 0.07
_OUT = Path("src/unified_pipeline/outputs")
_FILES = {
    '1a': _OUT / 'stage_1a_segmentation' / f'{UID}_segmented.json',
    '1b': _OUT / 'stage_1b_hierarchy_mapping' / f'{UID}_hierarchy_mapped.json',
    '2': _OUT / 'stage_2_entry_extraction' / f'{UID}_entries.json',
    '3a': _OUT / 'stage_3a_header_mappings' / f'{UID}_header_taxonomy.json',
    '3b': _OUT / 'stage_3b_classified_entries' / f'{UID}_classified.json',
    '4': _OUT / 'stage_4_field_extraction' / f'{UID}_fields.json',
    '4.5': _OUT / 'stage_4_5_research_summary' / f'{UID}_research_summary.json',
    '5': _OUT / 'stage_5_enrichment' / f'{UID}_enriched.json',
    '5b': _OUT / 'stage_5b_institution_enrichment' / f'{UID}_institution_enriched.json',
    '5c': _OUT / 'stage_5c_teaching_formatted' / f'{UID}_teaching_formatted.json',
    '5d': _OUT / 'stage_5d_citation_formatted' / f'{UID}_citation_formatted.json',
    '6': _OUT / 'stage_6_wcm_documents' / f'{UID}_wcm.docx',
}
_DOCX = f'data/sample_cvs/word/{UID}.docx'


def _write(path, payload=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload if payload is not None
                               else {'document_uid': UID}))
    return str(path)


class _Calls:
    """What each stage was called with, and in what order."""

    def __init__(self):
        self.order = []
        self.kwargs = {}

    def record(self, stage, **kwargs):
        self.order.append(stage)
        self.kwargs[stage] = kwargs


def _install_stubs(monkeypatch, calls, fail, stage5b_writer, stage5_path, tmp_path):
    """Replace the twelve stage runners with argument-checking stubs.

    Each stub takes the production call's keyword arguments BY NAME: a renamed
    or dropped kwarg raises TypeError inside the stage instead of passing.
    """
    def boom_if(stage):
        if stage in fail:
            raise RuntimeError(f"simulated {stage} failure")

    def stage_1a(*, cv_path):
        calls.record('1a', cv_path=cv_path)
        boom_if('1a')
        return ([{'level': 'H1', 'text': 'Education', 'children': []}],
                {'extraction_cost': 0.01})

    def stage_1b(*, docx_path, hierarchy_json_path):
        calls.record('1b', docx_path=docx_path, hierarchy_json_path=hierarchy_json_path)
        boom_if('1b')
        return ({'meta': {'total_sections': 3, 'leaf_sections': 2}}, _write(_FILES['1b']))

    def stage_2(*, docx_path, hierarchy_json_path):
        calls.record('2', docx_path=docx_path, hierarchy_json_path=hierarchy_json_path)
        boom_if('2')
        return ({'total_cost': 0.02, 'total_entries': 400}, _write(_FILES['2']))

    def stage_3a(*, document_uid):
        calls.record('3a', document_uid=document_uid)
        boom_if('3a')
        return {'output_path': _write(_FILES['3a']), 'stats': {'cost': 0.01},
                'node_count': 12}

    def stage_3b(*, document_uid, stage_3a_path):
        calls.record('3b', document_uid=document_uid, stage_3a_path=stage_3a_path)
        boom_if('3b')
        # llm_classified/fallback_entries (#810): all 400 got a real code,
        # none defaulted -- the plain, no-outage case every other stub here
        # assumes too.
        return {'output_path': _write(_FILES['3b']),
                'stats': {'cost': 0.01, 'llm_classified': 400, 'fallback_entries': 0},
                'total_entries': 400, 'code_distribution': {'A': 3}}

    def stage_4(*, docx_path):
        calls.record('4', docx_path=docx_path)
        boom_if('4')
        return {'output_path': _write(_FILES['4']),
                'output': {'total_cost': 0.03, 'total_entries': 400,
                           'stats': {'extracted': 400}}}

    def stage_4_5(*, input_path, verbose):
        calls.record('4.5', input_path=input_path, verbose=verbose)
        boom_if('4.5')
        return _write(_FILES['4.5'], {'research_summary': {
            'method': 'llm', 'm1_score': 0.9, 'summary_length': 500},
            'total_cost': _STAGE_4_5_COST})

    def stage_5(*, stage4_path, verbose):
        calls.record('5', stage4_path=stage4_path, verbose=verbose)
        boom_if('5')
        written = _write(Path(stage5_path) if stage5_path else _FILES['5'])
        return {'enriched': 10, 'output_path': written}

    def stage_5b(*, input_path, verbose):
        calls.record('5b', input_path=input_path, verbose=verbose)
        boom_if('5b')
        if stage5b_writer is not None:
            return stage5b_writer()
        return _write(_FILES['5b'], {'institution_enrichment_stats': {'cost': 0.0}})

    def stage_5c(*, input_path, verbose):
        calls.record('5c', input_path=input_path, verbose=verbose)
        boom_if('5c')
        return _write(_FILES['5c'], {'stage_5c': {'total_cost': _STAGE_5C_COST}})

    def stage_5d(*, input_path, verbose):
        calls.record('5d', input_path=input_path, verbose=verbose)
        boom_if('5d')
        return _write(_FILES['5d'], {'stage_5d': {'total_cost': _STAGE_5D_COST}})

    def stage_6(*, input_path, verbose, original_doc_path, llm_usage, repair_protected_data,
                supplementary_subpoints):
        calls.record('6', input_path=input_path, verbose=verbose,
                     original_doc_path=original_doc_path,
                     repair_protected_data=repair_protected_data,
                     supplementary_subpoints=supplementary_subpoints)
        boom_if('6')
        llm_usage.add({'cost': _STAGE_6_COST})
        path = tmp_path / _FILES['6']
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'PK\x03\x04fake')
        return str(path)

    for name, stub in (('get_cv_hierarchy_chunked', stage_1a),
                       ('run_stage_1b', stage_1b),
                       ('run_stage_2', stage_2),
                       ('run_stage_3a', stage_3a),
                       ('run_stage_3b', stage_3b),
                       ('run_stage_4', stage_4),
                       ('run_stage_4_5', stage_4_5),
                       ('run_stage5', stage_5),
                       ('run_stage5b', stage_5b),
                       ('run_stage_5c', stage_5c),
                       ('run_stage_5d', stage_5d),
                       ('run_stage6', stage_6)):
        monkeypatch.setattr(run_full_pipeline, name, stub)


def _run_main_streams(tmp_path, monkeypatch, capsys, fail=(), stage5b_writer=None,
                      argv=None, calls=None, stage5_path=None, setup=None,
                      make_docx=True) -> _Streams:
    """Run main() end to end with every stage runner stubbed. Returns both streams.

    ``stage5b_writer``, if given, replaces the default stage 5b output (a valid
    ``institution_enrichment_stats`` payload) so a test can point run_stage5b at
    a missing or corrupt file without touching any other stage. ``setup`` runs
    after the tmp repo layout exists and before main(), so a test can plant a
    stale artifact from a previous run. ``make_docx=False`` leaves the source
    document off disk, for the stages that never open it.
    """
    _enter_tmp_repo(tmp_path, monkeypatch)
    (tmp_path / 'data/sample_cvs/word').mkdir(parents=True)
    if make_docx:
        (tmp_path / _DOCX).write_bytes(b'PK\x03\x04fake')
    if setup is not None:
        setup()

    _install_stubs(monkeypatch, calls if calls is not None else _Calls(),
                   set(fail), stage5b_writer, stage5_path, tmp_path)
    monkeypatch.setattr(sys, 'argv', argv or ['run_full_pipeline.py', UID])
    # The narration only reaches a stream once the CLI's handlers exist, and
    # `ext://sys.stdout` has to resolve to the capsys stream that is already in
    # place -- so this is configured here, inside the test, not at import.
    _configure_cli_logging_keeping_pytests_handlers()
    capsys.readouterr()
    rc = run_full_pipeline.main()
    captured = capsys.readouterr()
    return _Streams(rc=rc, out=captured.out, err=captured.err)


def _run_main(*args, **kwargs):
    """(rc, stdout) for the tests that only assert on the parsed stream."""
    run = _run_main_streams(*args, **kwargs)
    return run.rc, run.out


def test_healthy_run_exits_zero_and_prints_the_plain_banner(tmp_path, monkeypatch, capsys):
    rc, out = _run_main(tmp_path, monkeypatch, capsys)
    assert rc == 0, "a fully successful run must stay exit 0"
    assert "PIPELINE COMPLETE" in out
    assert "WITH ERRORS" not in out
    assert "Failed stages:" not in out


def test_stage_6_crash_exits_non_zero_and_says_so(tmp_path, monkeypatch, capsys):
    """The #443 acceptance criterion, end to end: this run produced no document
    and used to print PIPELINE COMPLETE and exit 0."""
    rc, out = _run_main(tmp_path, monkeypatch, capsys, fail={'6'})
    assert rc == 1, "a run that produced no document must not exit 0"
    assert "PIPELINE COMPLETE WITH ERRORS" in out
    assert "Failed stages:" in out
    assert "stage_6: RuntimeError: simulated 6 failure" in out


def test_a_mid_pipeline_crash_also_exits_non_zero(tmp_path, monkeypatch, capsys):
    """The class fix: not just the terminal stage."""
    rc, out = _run_main(tmp_path, monkeypatch, capsys, fail={'3b'})
    assert rc == 1
    assert "PIPELINE COMPLETE WITH ERRORS" in out
    assert "stage_3b: RuntimeError: simulated 3b failure" in out


def test_the_failure_reason_names_the_exception_type(tmp_path, monkeypatch, capsys):
    """`str(e)` alone drops the type, and the reason line is all the operator gets.

    The real #443 crash recorded `'dict' object has no attribute 'replace'` --
    a message that never names AttributeError. The executor stores
    `f"{type(e).__name__}: {e}"` so the summary says what went wrong, not just
    where.
    """
    _, out = _run_main(tmp_path, monkeypatch, capsys, fail={'5c'})
    reason = next(line for line in out.splitlines() if "stage_5c:" in line)
    assert "RuntimeError" in reason, f"exception type missing from {reason!r}"


_STAGE_ERRORS = _OUT / 'stage_errors' / f'{UID}_stage_errors.json'


def test_a_stage_failure_writes_a_structured_stage_error_record(
        tmp_path, monkeypatch, capsys):
    """#745: the CLI's catch-and-continue boundary records the failure where
    quality_score reads it, not only in the printed summary."""
    from unified_pipeline.stage_errors import StageError, read_stage_errors
    _run_main(tmp_path, monkeypatch, capsys, fail={'3b'})
    assert read_stage_errors(tmp_path / _STAGE_ERRORS) == [
        StageError('3b', 'RuntimeError', 'simulated 3b failure', fatal=True)]


def test_a_clean_run_writes_no_stage_error_record(tmp_path, monkeypatch, capsys):
    _run_main(tmp_path, monkeypatch, capsys)
    assert not (tmp_path / _STAGE_ERRORS).exists()


def test_a_rerun_that_succeeds_clears_the_stage_error(tmp_path, monkeypatch, capsys):
    """A stale failure from an earlier run of the same uid must not keep
    capping the score once that stage succeeds."""
    from unified_pipeline.stage_errors import read_stage_errors
    _run_main(tmp_path, monkeypatch, capsys, fail={'4'})
    assert [e.stage for e in read_stage_errors(tmp_path / _STAGE_ERRORS)] == ['4']
    _enter_tmp_repo(tmp_path, monkeypatch)
    capsys.readouterr()
    _install_stubs(monkeypatch, _Calls(), set(), None, None, tmp_path)
    assert run_full_pipeline.main() == 0
    assert read_stage_errors(tmp_path / _STAGE_ERRORS) == []


def test_a_rerun_that_skips_a_stage_keeps_its_earlier_failure(tmp_path, monkeypatch, capsys):
    """A stage skipped for a missing prerequisite did not succeed, so it must
    not clear its own earlier record: erasing it would uncap the score of a
    run whose stage 4 still has not produced output."""
    from unified_pipeline.stage_errors import read_stage_errors
    _run_main(tmp_path, monkeypatch, capsys, fail={'4'})
    _enter_tmp_repo(tmp_path, monkeypatch)
    capsys.readouterr()
    _install_stubs(monkeypatch, _Calls(), {'3b'}, None, None, tmp_path)
    run_full_pipeline.main()
    assert sorted(e.stage for e in read_stage_errors(tmp_path / _STAGE_ERRORS)) == ['3b', '4']


def test_entrypoint_propagates_the_return_value_to_the_exit_status():
    """The last link: `main()` on its own discards the return value and the
    process exits 0 again, which is the whole bug. pytest imports this module
    rather than running it as __main__, so the wire is checked structurally --
    an AST call node, not a substring, so it cannot be satisfied by the text
    appearing in a comment or a docstring."""
    tree = ast.parse((_ROOT / "run_full_pipeline.py").read_text())
    guards = [node for node in tree.body
              if isinstance(node, ast.If) and "__name__" in ast.unparse(node.test)]
    assert guards, "run_full_pipeline.py has no `if __name__ == '__main__'` block"
    calls = [ast.unparse(n) for guard in guards for n in ast.walk(guard)
             if isinstance(n, ast.Call)]
    assert "sys.exit(main())" in calls, (
        f"__main__ must call sys.exit(main()); found {calls}. "
        "A bare main() discards the exit status and every run reports success.")


# -- r3960595385 / r3965457325: stage 1a is inside the failure boundary ------
# Mutant that kills this: call get_cv_hierarchy_chunked outside run_stage(),
# the way stage 1a used to run. The exception then escapes main(), pytest sees
# RuntimeError instead of a return value, and no summary is printed.


def test_stage_1a_failure_is_recorded_and_still_prints_the_summary(
    tmp_path, monkeypatch, capsys
):
    """Segmentation failing is a failed run, not a crashed process: it must
    reach failed_stages(), the summary, and the exit code like any other stage."""
    rc, out = _run_main(tmp_path, monkeypatch, capsys, fail={'1a'})
    assert rc == 1
    assert "PIPELINE COMPLETE WITH ERRORS" in out
    assert "stage_1a: RuntimeError: simulated 1a failure" in out
    assert "Traceback (most recent call last)" not in out, (
        "the traceback belongs to the logger, not to the parsed stdout")


def test_a_stage_1a_failure_skips_everything_downstream(tmp_path, monkeypatch, capsys):
    """No stage invents an input the run never produced."""
    calls = _Calls()
    rc, out = _run_main(tmp_path, monkeypatch, capsys, fail={'1a'}, calls=calls)
    assert rc == 1
    assert calls.order == ['1a'], f"downstream stages ran anyway: {calls.order}"
    assert "stage_1b: Stage 1a required" in out


# -- r3960614222 / r3965512224: one complete dependency registry -------------
# Mutant that kills this: restore the old prerequisites dict that stopped at
# stage 4 -- every parameter case from '4.5' down then reports no missing
# prerequisite at all.


@pytest.mark.parametrize("stage, prerequisite", [
    ("1b", "1a"), ("2", "1b"), ("3a", "1a"), ("3b", "2"), ("4", "3b"),
    ("4.5", "4"), ("5", "4"), ("5b", "4"), ("5c", "4"), ("5d", "4"), ("6", "4"),
])
def test_every_stage_names_its_missing_prerequisite(stage, prerequisite, tmp_path, monkeypatch):
    _enter_tmp_repo(tmp_path, monkeypatch)
    ok, missing_file, required_stage = run_full_pipeline.check_prerequisites(stage, UID)
    assert ok is False, f"--stage {stage} validated nothing with an empty outputs tree"
    assert required_stage == prerequisite
    assert missing_file == str(run_full_pipeline.get_output_paths(UID)[prerequisite])


def test_the_dependency_registry_covers_every_stage():
    """A stage the registry does not name would silently skip validation."""
    assert set(run_full_pipeline.STAGE_DEPENDENCIES) == set(run_full_pipeline.get_stage_order())
    paths = run_full_pipeline.get_output_paths(UID)
    for stage in run_full_pipeline.get_stage_order():
        if stage == '3':
            continue  # the --stage 3 alias has no output of its own
        assert stage in paths, f"no expected output path for stage {stage}"


# -- #490: output paths do not depend on the launch directory ----------------
# Mutant that kills these: _OUTPUTS_ROOT = Path('src/unified_pipeline/outputs')
# (the cwd-relative spelling), or the literal path back in _stage_1a.

_SCRIPT_DIR = Path(run_full_pipeline.__file__).resolve().parent


def test_output_paths_stay_under_the_repo_from_any_launch_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    paths = run_full_pipeline.get_output_paths(UID)
    for stage, path in paths.items():
        assert path.is_absolute(), f"stage {stage} path follows the cwd: {path}"
        assert _SCRIPT_DIR in path.parents, f"stage {stage} left the repo: {path}"
        assert tmp_path not in path.parents


def test_the_outputs_root_is_not_resolved_through_a_worktree_symlink(tmp_path):
    """A worktree symlinks src/unified_pipeline/outputs at a shared farm; only
    the script's own directory may be resolved, or the farm link is bypassed.

    Built for real: a copy of the script under a tmp repo whose outputs dir is
    a symlink, run in a subprocess, so the root must keep the link.

    Mutant that kills this: `.resolve()` on the joined outputs path.
    """
    farm = tmp_path / 'farm'
    farm.mkdir()
    repo = tmp_path / 'repo'
    (repo / 'src' / 'unified_pipeline').mkdir(parents=True)
    (repo / 'src' / 'unified_pipeline' / 'outputs').symlink_to(farm)
    script = repo / 'run_full_pipeline.py'
    script.write_text((_SCRIPT_DIR / 'run_full_pipeline.py').read_text())
    probe = ('import sys; sys.path.insert(0, %r); '
             'import run_full_pipeline as r; print(r._OUTPUTS_ROOT)' % str(_SCRIPT_DIR / 'src'))

    proc = subprocess.run([sys.executable, '-c', probe], cwd=repo, capture_output=True,
                          text=True, timeout=120,
                          env={**os.environ, 'PYTHONPATH': str(repo)})

    assert proc.returncode == 0, proc.stderr[-2000:]
    root = Path(proc.stdout.strip().splitlines()[-1])
    assert root == repo.resolve() / 'src' / 'unified_pipeline' / 'outputs'
    assert root.is_symlink() and root.resolve() == farm.resolve()


def test_the_stage_error_record_is_under_the_anchored_root_not_the_cwd(tmp_path, monkeypatch):
    """Mutant that kills this: stage_errors_path(Path('src/unified_pipeline/outputs'), ...)
    (the cwd-relative spelling) in the failure recorder."""
    launch_dir, root = tmp_path / 'elsewhere', tmp_path / 'root'
    launch_dir.mkdir()
    monkeypatch.chdir(launch_dir)
    monkeypatch.setattr(run_full_pipeline, '_OUTPUTS_ROOT', root)
    ctx = run_full_pipeline.PipelineContext(cv_path=Path(f'{UID}.docx'), document_uid=UID)

    def _crash(_ctx):
        raise RuntimeError('boom')

    run_full_pipeline.run_stage(ctx, '4', _crash)

    assert (root / 'stage_errors' / f'{UID}_stage_errors.json').is_file()
    assert list(launch_dir.iterdir()) == [], "the stage-error record followed the cwd"


def test_stage_1a_writes_under_the_outputs_root_not_the_cwd(tmp_path, monkeypatch):
    launch_dir, root = tmp_path / 'elsewhere', tmp_path / 'root'
    launch_dir.mkdir()
    monkeypatch.chdir(launch_dir)
    monkeypatch.setattr(run_full_pipeline, '_OUTPUTS_ROOT', root)
    monkeypatch.setattr(run_full_pipeline, 'get_cv_hierarchy_chunked',
                        lambda *, cv_path: ([], {'extraction_cost': 0}))
    ctx = run_full_pipeline.PipelineContext(cv_path=Path(f'{UID}.docx'), document_uid=UID)

    result = run_full_pipeline._stage_1a(ctx)

    assert Path(result.output_file) == root / 'stage_1a_segmentation' / f'{UID}_segmented.json'
    assert Path(result.output_file).is_file()
    assert list(launch_dir.iterdir()) == [], "stage 1a wrote into the launch directory"


# -- r3960618825 / r3965516071: stage 4 gets the resolved path ---------------
# Mutant that kills this: pass f"{document_uid}.docx" again.


def test_stage_4_receives_the_resolved_cv_path(tmp_path, monkeypatch, capsys):
    calls = _Calls()
    rc, _ = _run_main(tmp_path, monkeypatch, capsys, calls=calls)
    assert rc == 0
    assert calls.kwargs['4'] == {'docx_path': _DOCX}, (
        "stage 4 must be handed the path resolve_cv_path_for_run produced")


def test_stage_6_receives_the_resolved_cv_path(tmp_path, monkeypatch, capsys):
    """#550: the personal-data fallback reopens the source .docx, and the only
    way stage 6 learns where it is on this driver is this kwarg -- the
    SAMPLE_CV_DIR guess inside generate() resolves for a uid-style run from
    the repo root and for nothing else (a corpus batch on a path, a worktree)."""
    calls = _Calls()
    rc, _ = _run_main(tmp_path, monkeypatch, capsys, calls=calls)
    assert rc == 0
    assert calls.kwargs['6']['original_doc_path'] == _DOCX


@pytest.mark.parametrize("env, repair", [(None, False), ("0", False), ("1", True)])
def test_stage_6_repairs_protected_data_only_under_the_flag(tmp_path, monkeypatch, capsys, env, repair):
    """#1389: the CLI reads CVICHE_RUN_REPAIR the way the web driver does, so a
    corpus batch measures the document the web app would deliver."""
    if env is None:
        monkeypatch.delenv("CVICHE_RUN_REPAIR", raising=False)
    else:
        monkeypatch.setenv("CVICHE_RUN_REPAIR", env)
    calls = _Calls()
    rc, _ = _run_main(tmp_path, monkeypatch, capsys, calls=calls)
    assert rc == 0
    assert calls.kwargs['6']['repair_protected_data'] is repair


@pytest.mark.parametrize("env, subpoints", [(None, False), ("0", False), ("1", True)])
def test_stage_6_writes_sub_points_only_under_the_flag(tmp_path, monkeypatch, capsys, env, subpoints):
    """#1205: the CLI reads CVICHE_SUPPLEMENTARY_SUBPOINTS as the web driver does."""
    if env is None:
        monkeypatch.delenv("CVICHE_SUPPLEMENTARY_SUBPOINTS", raising=False)
    else:
        monkeypatch.setenv("CVICHE_SUPPLEMENTARY_SUBPOINTS", env)
    calls = _Calls()
    rc, _ = _run_main(tmp_path, monkeypatch, capsys, calls=calls)
    assert rc == 0
    assert calls.kwargs['6']['supplementary_subpoints'] is subpoints


def test_a_context_whose_path_and_uid_disagree_is_rejected(tmp_path):
    """Stage 4 finds its input from Path(docx_path).stem, so the two identities
    have to agree; this fails loudly instead of extracting another CV."""
    with pytest.raises(ValueError) as excinfo:
        run_full_pipeline.PipelineContext(
            cv_path=Path("data/sample_cvs/word/someone_else.docx"),
            document_uid=UID)
    assert "someone_else" in str(excinfo.value) and UID in str(excinfo.value)


# -- r3960726469 #5: the stubs assert the orchestration call -----------------
# Mutant that kills this: rename any of the asserted kwargs in run_full_pipeline
# (e.g. stage4_path -> path) -- the keyword-only stub raises TypeError.


def test_every_stage_receives_the_orchestration_arguments_it_expects(
    tmp_path, monkeypatch, capsys
):
    calls = _Calls()
    rc, _ = _run_main(tmp_path, monkeypatch, capsys, calls=calls)
    assert rc == 0
    assert calls.kwargs['1a'] == {'cv_path': _DOCX}
    assert calls.kwargs['1b'] == {'docx_path': _DOCX,
                                  'hierarchy_json_path': str(_FILES['1a'])}
    assert calls.kwargs['2'] == {'docx_path': _DOCX,
                                 'hierarchy_json_path': str(_FILES['1b'])}
    assert calls.kwargs['3a'] == {'document_uid': UID}
    assert calls.kwargs['3b'] == {'document_uid': UID,
                                  'stage_3a_path': str(_FILES['3a'])}
    assert calls.kwargs['4'] == {'docx_path': _DOCX}
    assert calls.kwargs['4.5'] == {'input_path': str(_FILES['4']), 'verbose': True}
    assert calls.kwargs['5'] == {'stage4_path': str(_FILES['4']), 'verbose': True}
    assert calls.kwargs['5b'] == {'input_path': str(_FILES['5']), 'verbose': True}
    assert calls.kwargs['5c'] == {'input_path': str(_FILES['5b']), 'verbose': True}
    assert calls.kwargs['5d'] == {'input_path': str(_FILES['5c']), 'verbose': True}
    assert calls.kwargs['6'] == {'input_path': str(_FILES['5d']), 'verbose': True,
                                 'original_doc_path': _DOCX, 'repair_protected_data': False,
                                 'supplementary_subpoints': False}


# -- r3960726469 #6: stage order --------------------------------------------
# Mutant that kills these: iterate _STAGE_RUNNERS in definition order without
# get_stage_order(), or make should_run('3') return True for every stage.


def test_full_pipeline_stage_order(tmp_path, monkeypatch, capsys):
    calls = _Calls()
    rc, _ = _run_main(tmp_path, monkeypatch, capsys, calls=calls)
    assert rc == 0
    assert calls.order == ['1a', '1b', '2', '3a', '3b', '4', '4.5',
                           '5', '5b', '5c', '5d', '6']


def test_stage3_runs_both_3a_and_3b(tmp_path, monkeypatch, capsys):
    """--stage 3 is the one composite alias; nothing else may run."""
    calls = _Calls()

    def plant():
        _write(_FILES['1a'], {'document_uid': UID, 'hierarchy': [], 'meta': {}})
        _write(_FILES['2'])

    rc, _ = _run_main(tmp_path, monkeypatch, capsys, calls=calls, setup=plant,
                      argv=['run_full_pipeline.py', UID, '--stage', '3'])
    assert calls.order == ['3a', '3b']
    assert rc == 0


# -- r3960631269 / r3965527262 / r3960726469 #3: no stale artifacts ----------
# Mutant that kills this: restore stage 6's `glob(f"*{uid}*_citation_formatted
# .json")` -> candidates[0] search ahead of the in-memory input.


def test_stage6_does_not_use_stale_artifact(tmp_path, monkeypatch, capsys):
    """A previous run left a 5d artifact for this same uid on disk. This run's
    5d fails, so stage 6 must render 5c's output FROM THIS RUN -- rendering the
    stale file and reporting success is the #780 defect."""
    calls = _Calls()

    def plant_stale():
        _write(_FILES['5d'], {'document_uid': UID, 'stale': 'from an earlier run'})

    rc, out = _run_main(tmp_path, monkeypatch, capsys, fail={'5d'}, calls=calls,
                        setup=plant_stale)
    assert rc == 1, "the run had a failed stage"
    assert calls.kwargs['6'] == {'input_path': str(_FILES['5c']), 'verbose': True,
                                 'original_doc_path': _DOCX, 'repair_protected_data': False,
                                 'supplementary_subpoints': False}, (
        "stage 6 took an input this run did not produce")
    assert "stage_5d: RuntimeError" in out


def test_full_run_never_reads_a_stale_artifact_from_disk(tmp_path, monkeypatch, capsys):
    """A full run resolves inputs ONLY from what it produced itself.

    Stage 4's and stage 5d's artifacts from a previous run are sitting at their
    exact expected paths. This run's stage 4 fails, so every stage below it has
    no input -- and must say so rather than quietly picking up the stale files
    and rendering a document out of last week's data.

    Mutant that kills this: delete `if self.target_stage is None: return None`
    from PipelineContext.best_input, so a full run falls through to the same
    disk lookup that --stage mode uses. Stages 4.5 through 6 then run on the
    stale artifacts and the run reports success.
    """
    calls = _Calls()

    def plant_stale():
        _write(_FILES['4'], {'document_uid': UID, 'stale': 'from an earlier run'})
        _write(_FILES['5d'], {'document_uid': UID, 'stale': 'from an earlier run'})

    rc, out = _run_main(tmp_path, monkeypatch, capsys, fail={'4'}, calls=calls,
                        setup=plant_stale)

    assert rc == 1
    assert calls.order == ['1a', '1b', '2', '3a', '3b', '4'], (
        f"a stage below the failure ran on a stale artifact: {calls.order}")
    for stage in ('4.5', '5', '5b', '5c', '5d', '6'):
        assert stage not in calls.kwargs, f"stage {stage} was called anyway"
    assert "stage_4: RuntimeError: simulated 4 failure" in out
    for line in ("stage_4.5: Stage 4 required",
                 "stage_5: Stage 4 required",
                 "stage_5b: Stage 5 or 4 required",
                 "stage_5c: Stage 5b, 5, or 4 required",
                 "stage_5d: Stage 5c, 5b, 5, or 4 required",
                 "stage_6: Stage 5d, 5c, 5b, 5, or 4 required"):
        assert line in out, f"missing skip reason: {line!r}"


def test_standalone_stage_6_uses_the_exact_expected_path(tmp_path, monkeypatch, capsys):
    """Filesystem discovery is confined to intentional --stage execution, and
    even there it is the stage's exact expected path -- never a substring glob
    that a differently-named file for the same uid can win."""
    calls = _Calls()

    def plant():
        _write(_FILES['4'])
        # Matches `*testcv*_teaching_formatted.json`; is NOT 5c's expected path.
        _write(_FILES['5c'].parent / f'stale_{UID}_teaching_formatted.json')

    rc, _ = _run_main(tmp_path, monkeypatch, capsys, calls=calls, setup=plant,
                      argv=['run_full_pipeline.py', UID, '--stage', '6'])
    assert calls.order == ['6']
    assert calls.kwargs['6'] == {'input_path': str(_FILES['4']), 'verbose': True,
                                 'original_doc_path': _DOCX, 'repair_protected_data': False,
                                 'supplementary_subpoints': False}
    assert rc == 0


# -- r3960726469 #2: stage 5's returned path is authoritative ----------------
# Mutant that kills this: rebuild the path as
# f"src/unified_pipeline/outputs/stage_5_enrichment/{uid}_enriched.json".


def test_stage5_uses_actual_returned_output_path(tmp_path, monkeypatch, capsys):
    elsewhere = _OUT / 'stage_5_enrichment' / 'somewhere_else.json'
    calls = _Calls()
    rc, out = _run_main(tmp_path, monkeypatch, capsys, calls=calls,
                        stage5_path=elsewhere)
    assert rc == 0
    assert f"  Stage 5:  {elsewhere}" in out
    assert calls.kwargs['5b']['input_path'] == str(elsewhere), (
        "stage 5b must consume the file stage 5 actually wrote")


def test_run_stage5_reports_the_path_it_wrote():
    """The production contract behind the test above: run_stage5's own return
    value carries the path, so the CLI never has to rebuild it."""
    from unified_pipeline import stage_5_pubmed_enrichment
    source = Path(stage_5_pubmed_enrichment.__file__).read_text(encoding='utf-8')
    assert "result['output_path'] = str(output_path)" in source


# ------------------------------------------------------------------ #444
# Model provenance. Every run used to be stamped "Model: gpt-5.1" -- a model it
# never used -- while the work ran on Bedrock Sonnet/Haiku, and --model was
# inert. The summary now reports what actually served the calls.


def test_summary_reports_the_models_that_actually_ran(tmp_path, monkeypatch, capsys):
    """Sentinel, not the shipped config: a test asserting the banner equals
    whatever llm_config.yaml says would pass against a hardcoded string too."""
    sentinel = "sentinel-model-4242"
    monkeypatch.setattr(run_full_pipeline, "format_models_used",
                        lambda: f"{sentinel} (7 calls)")
    _, out = _run_main(tmp_path, monkeypatch, capsys)
    assert f"Models: {sentinel} (7 calls)" in out


def test_no_model_is_named_that_the_pipeline_did_not_call(tmp_path, monkeypatch, capsys):
    """The specific #444 regression: a hardcoded OpenAI id on a Bedrock run."""
    monkeypatch.setattr(run_full_pipeline, "format_models_used",
                        lambda: "us.anthropic.claude-sonnet-4-6 (3 calls)")
    _, out = _run_main(tmp_path, monkeypatch, capsys)
    assert "gpt-" not in out, "a model the run never called is named in its own summary"


def test_the_inert_model_flag_is_gone():
    """--model set a variable that reached no stage function. Removed rather
    than wired: each stage resolves its own model from llm_config.yaml, and
    stage_3b is deliberately on a different one."""
    source = (_ROOT / "run_full_pipeline.py").read_text()
    assert "--model" not in source, "the inert flag is back"


def test_models_used_counts_real_calls_not_configuration():
    """The accumulator records the model the response came back with."""
    from unified_pipeline import llm_client
    before = llm_client.models_used()
    with llm_client._MODELS_USED_LOCK:
        llm_client._MODELS_USED["test-model-x"] += 2
    after = llm_client.models_used()
    assert after.get("test-model-x", 0) - before.get("test-model-x", 0) == 2
    assert "test-model-x (2 calls)" in llm_client.format_models_used()
    with llm_client._MODELS_USED_LOCK:
        del llm_client._MODELS_USED["test-model-x"]


def test_format_models_used_is_honest_when_nothing_was_called():
    from unified_pipeline import llm_client
    assert llm_client.format_models_used(), "must never render as an empty string"


# ------------------------------------------------------------------ #489
# Stage 5b cost bookkeeping. A missing or corrupt stage 5b output used to be
# swallowed by a bare `except Exception: pass`, so the run reported cost 0.0 --
# identical to a CV that genuinely had nothing left to enrich.

def test_stage5b_genuine_zero_cost_is_not_flagged_unknown(tmp_path, monkeypatch, capsys):
    """A CV with nothing left to enrich costs nothing; that must stay ordinary
    success, not get swept into the same bucket as a read failure."""
    rc, out = _run_main(tmp_path, monkeypatch, capsys)
    assert rc == 0
    assert "unknown" not in out.lower()


def test_stage5b_corrupt_output_flags_cost_as_unknown(tmp_path, monkeypatch, capsys, caplog):
    """The #489 case: malformed JSON from stage 5b must not silently read as
    $0.00 -- it has to be visibly distinct from a genuine zero."""
    def write_corrupt():
        path = _FILES['5b']
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not valid json")
        return str(path)

    caplog.set_level(logging.WARNING)
    rc, out = _run_main(tmp_path, monkeypatch, capsys, stage5b_writer=write_corrupt)
    assert rc == 0, "institution enrichment itself succeeded; only the cost read failed"
    assert "Cost: unknown (failed to read institution enrichment stats)" in out
    assert "Stage 5b: unknown (institution enrichment stats unreadable)" in out
    assert "Stage 5b: $0.0000" not in out
    assert any("stage 5b cost" in record.message.lower() for record in caplog.records), (
        "the read failure must be logged, naming the path and the exception")


def test_stage5b_missing_output_file_flags_cost_as_unknown(tmp_path, monkeypatch, capsys, caplog):
    """Same shape, the other failure mode named in #489: run_stage5b reports a
    path but never writes it (crash, permissions, disk full)."""
    def missing_file():
        return str(_FILES['5b'])  # never created

    caplog.set_level(logging.WARNING)
    rc, out = _run_main(tmp_path, monkeypatch, capsys, stage5b_writer=missing_file)
    assert rc == 0
    assert "Cost: unknown (failed to read institution enrichment stats)" in out
    assert "Stage 5b: unknown (institution enrichment stats unreadable)" in out
    assert any("stage 5b cost" in record.message.lower() for record in caplog.records)


# -- r3960638058 / r3965532462: one exception policy ------------------------
# Mutant that kills this: drop logger.exception() from run_stage() and print the
# traceback with traceback.print_exc() again.


def test_every_stage_failure_is_logged_with_its_traceback(tmp_path, monkeypatch, capsys, caplog):
    caplog.set_level(logging.ERROR)
    rc, out = _run_main(tmp_path, monkeypatch, capsys, fail={'5c', '6'})
    assert rc == 1
    logged = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert {r.getMessage() for r in logged} == {"Stage 5c failed", "Stage 6 failed"}
    assert all(r.exc_info is not None for r in logged), (
        "logger.exception carries the traceback; logger.error alone does not")
    assert "Traceback (most recent call last)" not in out, (
        "traceback.print_exc() writes into the stdout the batch script parses")


# -- r3960659881 / r3960666423: derived cost --------------------------------
# Mutant that kills this: reintroduce a `total_cost +=` accumulator that omits
# one stage.


def test_total_cost_is_the_sum_of_every_stage_result():
    results = {
        'stage_1a': _result('1a', cost=0.01),
        'stage_2': _result('2', cost=0.02),
        'stage_5b': _result('5b', cost=None),      # unknown, not zero
        'stage_5c': _result('5c'),                 # no cost of its own
    }
    result = run_full_pipeline.PipelineResult(results=results,
                                              total_duration_seconds=1.0, failed=[])
    assert result.total_cost == pytest.approx(0.03)
    assert result.exit_code == 0
    assert run_full_pipeline.PipelineResult(
        results=results, total_duration_seconds=1.0, failed=['stage_5c']).exit_code == 1


def test_the_reported_total_cost_matches_the_stage_lines(tmp_path, monkeypatch, capsys):
    """0.01 (1a) + 0.02 (2) + 0.01 (3a) + 0.01 (3b) + 0.03 (4) + 0.04 (4.5)
    + 0.0 (5b) + 0.05 (5c) + 0.06 (5d) + 0.07 (6)."""
    _, out = _run_main(tmp_path, monkeypatch, capsys)
    assert "  Total:    $0.3000" in out


def test_the_costs_block_lists_every_llm_stage_with_its_own_cost(tmp_path, monkeypatch, capsys):
    """#1177: stages 4.5, 5c, 5d and 6 call the LLM but were missing from the
    Costs block and from the total (22% low over 40 runs)."""
    _, out = _run_main(tmp_path, monkeypatch, capsys)
    costs_block = out.split("Costs:", 1)[1].split("Processing Stats:", 1)[0]
    for label, cost in (("Stage 4.5:", _STAGE_4_5_COST), ("Stage 5c:", _STAGE_5C_COST),
                        ("Stage 5d:", _STAGE_5D_COST), ("Stage 6: ", _STAGE_6_COST)):
        assert f"  {label} ${cost:.4f}" in costs_block


def test_an_unreadable_formatter_output_is_unknown_cost_not_zero(tmp_path, caplog):
    """Same #489 shape for the stages whose cost is read back from their output:
    an unreadable file is not a genuine $0.00."""
    corrupt = tmp_path / 'stage_5c.json'
    corrupt.write_text("{not valid json")

    caplog.set_level(logging.INFO)
    cost = run_full_pipeline._read_stage_cost(str(corrupt), '5c', 'stage_5c')

    assert cost is None
    assert any("stage 5c cost" in r.getMessage().lower() for r in caplog.records)
    run_full_pipeline._print_costs(run_full_pipeline.PipelineResult(
        results={'stage_5c': _result('5c', cost=cost)}, total_duration_seconds=1.0, failed=[]))
    assert "Stage 5c: unknown (output cost unreadable)" in caplog.text
    assert "Stage 5c: $0.0000" not in caplog.text


# Every module that imports call_llm, and the stage whose cost line must cover it.
# A new call_llm module has to be added here, which forces the question
# "does a driver report its cost?" (#1177).
_CALL_LLM_MODULE_STAGE = {
    'segmentation/chunked_chat_hierarchy_extractor.py': '1a',
    'segmentation/signature_based_segmentation.py': '1a',
    'stage_2_entry_extraction.py': '2',
    'stage_3a_header_taxonomy_mapper.py': '3a',
    'stage_3b_entry_classifier.py': '3b',
    'stage3b/classify.py': '3b',
    'stage_4_field_extractor.py': '4',
    'stage4/extraction.py': '4',
    'stage4/owner_name.py': '4',
    'stage_4_5_research_summary.py': '4.5',
    'stage5b/lookup.py': '5b',
    'stage_5c_teaching_formatter.py': '5c',
    'stage_5d_citation_formatter.py': '5d',
    'stage_6_word_template.py': '6',
}
# Import call_llm but no driver runs them (only standalone scripts reach them).
_CALL_LLM_UNWIRED_MODULES = frozenset({
    'core/personal_info_extractor.py',
    'core/candidate_surfacer.py',
})


def _modules_importing_call_llm():
    package = Path(__file__).resolve().parents[1]
    found = set()
    for path in package.rglob('*.py'):
        relative = path.relative_to(package)
        if relative.parts[0] == 'tests' or relative.as_posix() == 'llm_client.py':
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if (isinstance(node, ast.ImportFrom)
                    and any(alias.name == 'call_llm' for alias in node.names)):
                found.add(relative.as_posix())
    return found


def test_every_stage_module_that_calls_the_llm_is_in_the_cli_cost_list():
    modules = _modules_importing_call_llm()
    unmapped = modules - set(_CALL_LLM_MODULE_STAGE) - _CALL_LLM_UNWIRED_MODULES
    assert not unmapped, (
        f"{sorted(unmapped)} import call_llm: map each to its stage in "
        "_CALL_LLM_MODULE_STAGE so its cost is reported (#1177)")
    missing = {stage for module, stage in _CALL_LLM_MODULE_STAGE.items()
               if module in modules} - set(run_full_pipeline._COST_REPORTING_STAGES)
    assert not missing, f"stages {sorted(missing)} call the LLM but are not in _COST_REPORTING_STAGES"


# -- r3960726469 #7: the OS-level exit status -------------------------------


# A driver rather than the script: the outputs root is anchored on the script's
# own location (#490), so cwd=tmp_path alone would write into the real repo
# tree. Redirecting it needs a hook before main(); the driver takes the same
# route the stdout-contract harness does, and repeats __main__'s two calls
# (the `sys.exit(main())` wire is pinned structurally by
# test_entrypoint_propagates_the_return_value_to_the_exit_status). Chosen over
# an env-var override so production gains no new configuration surface.
_CLI_DRIVER = '''
import sys
sys.path.insert(0, {root!r})
sys.argv = ["run_full_pipeline.py", *{args!r}]
import run_full_pipeline as r
r._OUTPUTS_ROOT = r.Path('src/unified_pipeline/outputs')  # under the cwd (#490)
r.configure_cli_logging()
sys.exit(r.main())
'''


def _run_cli(tmp_path, *args):
    """Run the CLI as a subprocess, with every LLM credential stripped from
    its environment so it cannot reach a model even if a stage tried, and its
    outputs root redirected under tmp_path."""
    env = {k: v for k, v in os.environ.items()
           if not (k.startswith('AWS_') or k in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY'))}
    driver = tmp_path / 'cli_driver.py'
    driver.write_text(_CLI_DRIVER.format(root=str(_ROOT), args=list(args)))
    before = _repo_outputs_snapshot()
    proc = subprocess.run([sys.executable, str(driver)],
                          cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert _repo_outputs_snapshot() == before, "the subprocess wrote into the real outputs tree"
    return proc


def test_the_cli_exits_non_zero_as_a_subprocess(tmp_path):
    """main()'s return value is not the production contract -- the process exit
    status is, and that is what scripts/run_corpus_batch.sh records. Runs over a
    garbage .docx, so stage 1a fails inside python-docx before any model can be
    reached.

    Mutant that kills this: `main()` instead of `sys.exit(main())` in __main__.
    """
    (tmp_path / 'data/sample_cvs/word').mkdir(parents=True)
    (tmp_path / _DOCX).write_bytes(b'not a docx at all')

    proc = _run_cli(tmp_path, UID)

    assert proc.returncode == 1, (
        f"exit status {proc.returncode}; stdout tail:\n{proc.stdout[-2000:]}")
    assert "PIPELINE COMPLETE WITH ERRORS" in proc.stdout


def test_tracebacks_go_to_stderr_and_never_into_the_parsed_stdout(tmp_path):
    """The stream split the __main__ block configures, checked on the real
    process rather than asserted in a comment.

    stdout is the contract stream: scripts/run_corpus_batch.sh greps it, and a
    Python traceback in it is noise at best. The logger's handler therefore
    streams to stderr, where the traceback for every failed stage lands.

    Mutant that kills this: put the dictConfig handler back on
    "ext://sys.stdout".
    """
    (tmp_path / 'data/sample_cvs/word').mkdir(parents=True)
    (tmp_path / _DOCX).write_bytes(b'not a docx at all')

    proc = _run_cli(tmp_path, UID)

    assert "Traceback (most recent call last)" not in proc.stdout, (
        "a traceback reached the stream run_corpus_batch.sh parses")
    assert "Traceback (most recent call last)" in proc.stderr, (
        "the stage failure's traceback went nowhere")
    assert "ERROR" in proc.stderr and "Stage 1a failed" in proc.stderr
    # The contract lines are still on stdout, not swept into stderr with it.
    assert "PIPELINE COMPLETE WITH ERRORS" in proc.stdout
    assert proc.stdout.count("Models: ") == 1


@pytest.mark.parametrize("args", [
    ("nonexistent.pdf",),                    # full run
    ("nonexistent.pdf", "--stage", "1a"),    # segmentation reads the docx
    ("nonexistent.pdf", "--stage", "1b"),    # so does hierarchy mapping
    ("nonexistent.pdf", "--stage", "2"),     # and entry extraction
])
def test_a_cv_that_cannot_be_resolved_is_named_as_such(args, tmp_path):
    """`run_full_pipeline.py nonexistent.pdf` used to die on the stem/uid guard
    -- an accurate message about the wrong thing. The first argument not
    resolving to a file is its own error, on every run that opens the document.

    Mutant that kills this: drop the _cv_is_missing() check from main() and let
    PipelineContext.__post_init__ raise.
    """
    proc = _run_cli(tmp_path, *args)

    assert proc.returncode == 1
    # The message is a diagnostic, so since #780's print()->logger migration it
    # goes to stderr, where every diagnostic goes. stdout is the parsed stream
    # and must not carry it. Both still land in a batch log (`> "$log" 2>&1`).
    assert "Error: CV not found: nonexistent.pdf" in proc.stderr
    assert "data/sample_cvs/word/" in proc.stderr
    assert "Error: CV not found" not in proc.stdout
    assert "ValueError" not in proc.stdout and "Traceback" not in proc.stderr
    assert "CV PROCESSING PIPELINE" not in proc.stdout, (
        "the run banner printed for a run that cannot start")


def test_a_standalone_stage_that_never_reads_the_docx_runs_without_it(
    tmp_path, monkeypatch, capsys
):
    """Only stages 1a/1b/2 open the source document; everything below works from
    the JSON an earlier run left behind. `--stage 5b` on a stage-4 artifact is a
    legitimate rerun and must not be blocked because the .docx has since moved.

    Mutant that kills this: apply _cv_is_missing() unconditionally, ignoring
    _STAGES_READING_THE_DOCX.
    """
    calls = _Calls()

    def plant():
        _write(_FILES['4'])

    rc, out = _run_main(tmp_path, monkeypatch, capsys, calls=calls, setup=plant,
                        make_docx=False,
                        argv=['run_full_pipeline.py', UID, '--stage', '5b'])

    assert not (tmp_path / _DOCX).exists(), "the fixture defeated its own point"
    assert rc == 0
    assert calls.order == ['5b']
    assert calls.kwargs['5b'] == {'input_path': str(_FILES['4']), 'verbose': True}
    assert "Error: CV not found" not in out


# ------------------------------------------------------------------ #686
# The CLI never scoped its prompt-log writes: run_full_pipeline.py had zero
# references to set_current_run_id/prompt_logger, so every CLI run's
# transcripts landed in the same shared, flat prompt_logs/ directory.
# resolve_cv_path_for_run() -- the setup helper main() calls once per run in
# place of resolve_cv_path() -- scopes this process to the run, and
# @_scope_prompt_logger_per_run (applied to main() itself, not called inside
# it, so main()'s own line count never changes) runs main() inside a fresh
# contextvars.Context copy so that scope is discarded -- no explicit reset,
# no module-level state -- once main() returns or raises.
#
# The two helper-level tests below call resolve_cv_path_for_run() directly,
# outside of any copied context, so (unlike a real run) the ContextVar.set()
# it makes is NOT automatically undone when the test function returns --
# each wraps its own assertions in a copied context of its own and resets
# that context's token explicitly, so nothing leaks into later tests.


def test_resolve_cv_path_for_run_scopes_prompt_logger_to_a_hashed_id(
    tmp_path, monkeypatch
):
    """The scope id is a hash of document_uid, not document_uid itself --
    see the next test for why."""
    assert prompt_logger._current_run_id.get() is None, "leaked from another test"
    _enter_tmp_repo(tmp_path, monkeypatch)
    (tmp_path / "data/sample_cvs/word").mkdir(parents=True)
    (tmp_path / _DOCX).write_bytes(b"PK\x03\x04fake")

    def _check():
        cv_path, document_uid = run_full_pipeline.resolve_cv_path_for_run(UID)
        assert document_uid == UID
        scope_id = prompt_logger._current_run_id.get()
        assert scope_id == hashlib.sha256(UID.encode()).hexdigest()[:run_full_pipeline._RUN_SCOPE_ID_LEN]
        assert prompt_logger._RUN_ID_RE.match(scope_id)

    contextvars.copy_context().run(_check)
    assert prompt_logger._current_run_id.get() is None


def test_resolve_cv_path_for_run_survives_a_document_uid_too_long_for_the_regex(
    tmp_path, monkeypatch
):
    """document_uid routinely exceeds prompt_logger._RUN_ID_RE's 10-character
    cap -- this very module's own docstring uses '2097_Upton_Cv' (13 chars)
    as the canonical example uid. Calling set_current_run_id(document_uid)
    directly raises ValueError; resolve_cv_path_for_run must derive a scope
    id that always satisfies the regex instead."""
    long_uid = "2097_Upton_Cv"
    assert not prompt_logger._RUN_ID_RE.match(long_uid), (
        "fixture uid no longer exercises the length trap -- pick a longer one")
    with pytest.raises(ValueError):
        prompt_logger.set_current_run_id(long_uid)

    _enter_tmp_repo(tmp_path, monkeypatch)

    def _check():
        cv_path, document_uid = run_full_pipeline.resolve_cv_path_for_run(long_uid)
        assert document_uid == long_uid
        scope_id = prompt_logger._current_run_id.get()
        assert prompt_logger._RUN_ID_RE.match(scope_id), (
            f"derived scope id {scope_id!r} does not satisfy prompt_logger._RUN_ID_RE")

    contextvars.copy_context().run(_check)
    assert prompt_logger._current_run_id.get() is None


def test_full_run_scopes_prompt_logger_and_resets_it_when_main_finishes(
    tmp_path, monkeypatch, capsys
):
    """End to end over main() itself -- not just the helper -- the same
    argument this file already makes for failed_stages just above: a suite
    that only unit-tests resolve_cv_path_for_run() could not notice the
    @_scope_prompt_logger_per_run decorator ever getting detached from
    main()."""
    calls = []
    real_set = run_full_pipeline.set_current_run_id

    def spy_set(run_id):
        calls.append(run_id)
        return real_set(run_id)

    monkeypatch.setattr(run_full_pipeline, "set_current_run_id", spy_set)
    assert prompt_logger._current_run_id.get() is None, "leaked from another test"

    rc, out = _run_main(tmp_path, monkeypatch, capsys)

    assert rc == 0
    assert calls == [hashlib.sha256(UID.encode()).hexdigest()[:run_full_pipeline._RUN_SCOPE_ID_LEN]], (
        "main() must scope prompt_logger to this run's document uid")
    assert prompt_logger._current_run_id.get() is None, (
        "main() did not reset prompt_logger's run scope after finishing")


def test_prompt_logger_resets_when_main_raises(tmp_path, monkeypatch, capsys):
    """The decorator's copied context is discarded on the way OUT of main(),
    however main() leaves -- so an exception cannot strand the run scope on the
    caller's context.

    Mutant that kills this: drop @_scope_prompt_logger_per_run and call
    set_current_run_id() in main()'s own context.
    """
    assert prompt_logger._current_run_id.get() is None, "leaked from another test"
    _enter_tmp_repo(tmp_path, monkeypatch)
    (tmp_path / 'data/sample_cvs/word').mkdir(parents=True)
    (tmp_path / _DOCX).write_bytes(b'PK\x03\x04fake')

    def explode(ctx):
        assert prompt_logger._current_run_id.get() is not None, (
            "the scope must be set while the pipeline runs, or this proves nothing")
        raise RuntimeError("simulated pipeline explosion")

    monkeypatch.setattr(run_full_pipeline, 'run_pipeline', explode)
    monkeypatch.setattr(sys, 'argv', ['run_full_pipeline.py', UID])

    with pytest.raises(RuntimeError, match="simulated pipeline explosion"):
        run_full_pipeline.main()

    assert prompt_logger._current_run_id.get() is None, (
        "an exception left prompt_logger scoped to a finished run")


# ---- #306: hierarchy walkers must be iterative -----------------------------------

def _nested_hierarchy():
    return [
        {"level": "H1", "text": "A", "children": [
            {"level": "H2", "text": "A1", "children": [{"level": "H3", "text": "A1a"}]},
            {"level": "H2", "text": "A2", "children": []},
        ]},
        {"level": "H1", "text": "B"},
        {"level": "H1", "text": "C", "children": [{"level": "H2", "text": "C1", "children": []}]},
        {"text": "no-level"},  # level defaults to H1
        {"level": "H2", "children": []},  # text defaults to ""
    ]


def _deep_chain(depth):
    root = {"level": "H1", "text": "n0", "children": []}
    tip = root
    for i in range(1, depth):
        child = {"level": "H2", "text": f"n{i}", "children": []}
        tip["children"].append(child)
        tip = child
    return [root]


def _recursive_count(nodes):
    return len(nodes) + sum(_recursive_count(n.get("children", [])) for n in nodes)


def _recursive_lines(nodes, depth=0):
    out = []
    for n in nodes:
        out.append(f"{'  ' * depth}[{n.get('level', 'H1')}] {n.get('text', '')}")
        out.extend(_recursive_lines(n.get("children", []), depth + 1))
    return out


def test_count_headers_matches_recursive_reference():
    h = _nested_hierarchy()
    assert run_full_pipeline._count_headers(h) == _recursive_count(h) == 9
    assert run_full_pipeline._count_headers([]) == 0


def test_count_headers_deep_chain_does_not_recurse():
    assert run_full_pipeline._count_headers(_deep_chain(5000)) == 5000


def test_hierarchy_lines_match_recursive_reference():
    h = _nested_hierarchy()
    assert run_full_pipeline._hierarchy_lines(h) == _recursive_lines(h)


def test_hierarchy_lines_honours_starting_depth():
    h = _nested_hierarchy()
    assert run_full_pipeline._hierarchy_lines(h, depth=1) == _recursive_lines(h, depth=1)
    assert run_full_pipeline._hierarchy_lines(h, depth=1)[0] == "  [H1] A"


def test_hierarchy_lines_deep_chain_does_not_recurse():
    lines = run_full_pipeline._hierarchy_lines(_deep_chain(5000))
    assert len(lines) == 5000
    assert lines[-1].endswith("[H2] n4999")
