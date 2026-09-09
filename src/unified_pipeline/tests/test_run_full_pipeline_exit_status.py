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

import pytest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import run_full_pipeline  # noqa: E402
from unified_pipeline.core import prompt_logger  # noqa: E402

failed_stages = run_full_pipeline.failed_stages
StageResult = run_full_pipeline.StageResult


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
        return {'output_path': _write(_FILES['3b']), 'stats': {'cost': 0.01},
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
            'method': 'llm', 'm1_score': 0.9, 'summary_length': 500}})

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
        return _write(_FILES['5c'])

    def stage_5d(*, input_path, verbose):
        calls.record('5d', input_path=input_path, verbose=verbose)
        boom_if('5d')
        return _write(_FILES['5d'])

    def stage_6(*, input_path, verbose):
        calls.record('6', input_path=input_path, verbose=verbose)
        boom_if('6')
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


def _run_main(tmp_path, monkeypatch, capsys, fail=(), stage5b_writer=None,
              argv=None, calls=None, stage5_path=None, setup=None):
    """Run main() end to end with every stage runner stubbed. Returns (rc, stdout).

    ``stage5b_writer``, if given, replaces the default stage 5b output (a valid
    ``institution_enrichment_stats`` payload) so a test can point run_stage5b at
    a missing or corrupt file without touching any other stage. ``setup`` runs
    after the tmp repo layout exists and before main(), so a test can plant a
    stale artifact from a previous run.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'data/sample_cvs/word').mkdir(parents=True)
    (tmp_path / _DOCX).write_bytes(b'PK\x03\x04fake')
    if setup is not None:
        setup()

    _install_stubs(monkeypatch, calls if calls is not None else _Calls(),
                   set(fail), stage5b_writer, stage5_path, tmp_path)
    monkeypatch.setattr(sys, 'argv', argv or ['run_full_pipeline.py', UID])
    capsys.readouterr()
    rc = run_full_pipeline.main()
    return rc, capsys.readouterr().out


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
    monkeypatch.chdir(tmp_path)
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


# -- r3960618825 / r3965516071: stage 4 gets the resolved path ---------------
# Mutant that kills this: pass f"{document_uid}.docx" again.


def test_stage_4_receives_the_resolved_cv_path(tmp_path, monkeypatch, capsys):
    calls = _Calls()
    rc, _ = _run_main(tmp_path, monkeypatch, capsys, calls=calls)
    assert rc == 0
    assert calls.kwargs['4'] == {'docx_path': _DOCX}, (
        "stage 4 must be handed the path resolve_cv_path_for_run produced")


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
    assert calls.kwargs['6'] == {'input_path': str(_FILES['5d']), 'verbose': True}


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
    assert calls.kwargs['6'] == {'input_path': str(_FILES['5c']), 'verbose': True}, (
        "stage 6 took an input this run did not produce")
    assert "stage_5d: RuntimeError" in out


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
    assert calls.kwargs['6'] == {'input_path': str(_FILES['4']), 'verbose': True}
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
    source = Path(stage_5_pubmed_enrichment.__file__).read_text()
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
    """0.01 (1a) + 0.02 (2) + 0.01 (3a) + 0.01 (3b) + 0.03 (4) + 0.0 (5b)."""
    _, out = _run_main(tmp_path, monkeypatch, capsys)
    assert "  Total:    $0.0800" in out


# -- r3960726469 #7: the OS-level exit status -------------------------------


def test_the_cli_exits_non_zero_as_a_subprocess(tmp_path):
    """main()'s return value is not the production contract -- the process exit
    status is, and that is what scripts/run_corpus_batch.sh records. Runs with
    every LLM credential stripped from the environment, over a garbage .docx, so
    stage 1a fails inside python-docx before any model can be reached.

    Mutant that kills this: `main()` instead of `sys.exit(main())` in __main__.
    """
    (tmp_path / 'data/sample_cvs/word').mkdir(parents=True)
    (tmp_path / _DOCX).write_bytes(b'not a docx at all')

    env = {k: v for k, v in os.environ.items()
           if not (k.startswith('AWS_') or k in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY'))}
    proc = subprocess.run(
        [sys.executable, str(_ROOT / "run_full_pipeline.py"), UID],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)

    assert proc.returncode == 1, (
        f"exit status {proc.returncode}; stdout tail:\n{proc.stdout[-2000:]}")
    assert "PIPELINE COMPLETE WITH ERRORS" in proc.stdout


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
    monkeypatch.chdir(tmp_path)
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

    monkeypatch.chdir(tmp_path)

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
    monkeypatch.chdir(tmp_path)
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
