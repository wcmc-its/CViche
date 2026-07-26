"""Regression guard for issue #443: a stage-6 crash printed PIPELINE COMPLETE
and exited 0, so batches silently under-produced.

Every stage wrapper in ``main`` records its exception as
``all_results['stage_X'] = {'error': ...}`` and continues. Nothing read that key
back, and ``__main__`` called ``main()`` rather than ``sys.exit(main())``, so
the process always exited 0. Two runs of the 2026-07-25 corpus batch (web094,
web147) produced no document and reported success; the batch script only noticed
because it separately checks whether the docx exists.

``failed_stages`` is the aggregation that was missing. The tests below cover it
directly and then run ``main()`` itself with the twelve stage runners stubbed,
because a suite that never executes ``main()`` cannot notice this bug returning
-- and "nobody notices" is the entire nature of the defect.

    python3 -m pytest src/unified_pipeline/tests/test_run_full_pipeline_exit_status.py -p no:cacheprovider

Self-contained: no DB, no network, no LLM.
"""

import ast
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import run_full_pipeline  # noqa: E402

failed_stages = run_full_pipeline.failed_stages


def test_clean_run_has_no_failed_stages():
    all_results = {
        'stage_1b': {'output_file': 'a.json'},
        'stage_2': {'output_file': 'b.json', 'total_entries': 400},
        'stage_3b': {'output_file': 'c.json', 'entries_classified': 400},
        'stage_4': {'output_file': 'd.json', 'entries_extracted': 400},
        'stage_6': {'output_file': 'out.docx', 'duration': 12.0},
    }
    assert failed_stages(all_results) == []


def test_stage_6_crash_is_reported():
    """The #443 case: everything upstream fine, no document, exit 0."""
    all_results = {
        'stage_4': {'output_file': 'd.json'},
        'stage_6': {'error': "'dict' object has no attribute 'replace'"},
    }
    assert failed_stages(all_results) == ['stage_6']


def test_any_stage_error_is_reported_not_just_stage_6():
    """The class fix: all eleven wrappers share the swallow-and-continue shape."""
    for stage in ('stage_1b', 'stage_2', 'stage_3a', 'stage_3b', 'stage_4',
                  'stage_4.5', 'stage_5', 'stage_5b', 'stage_5c', 'stage_5d'):
        assert failed_stages({stage: {'error': 'boom'},
                              'stage_6': {'output_file': 'out.docx'}}) == [stage]


def test_stage_skipped_for_a_missing_prerequisite_is_a_failure():
    """A skip only happens downstream of an earlier failure, so it is not benign."""
    all_results = {
        'stage_2': {'error': 'boom'},
        'stage_3b': {'skipped': 'Stage 2 required'},
        'stage_6': {'skipped': 'Earlier stage output required'},
    }
    assert failed_stages(all_results) == ['stage_2', 'stage_3b', 'stage_6']


def test_stage_6_without_an_output_file_is_a_failure():
    """Stage 6 is the deliverable: no docx means the run produced nothing."""
    assert failed_stages({'stage_6': {'duration': 3.0}}) == ['stage_6']


def test_a_stage_that_never_ran_is_not_a_failure():
    """`--stage 2` leaves the rest out of all_results entirely."""
    assert failed_stages({'stage_2': {'output_file': 'b.json'}}) == []
    assert failed_stages({}) == []


# ---------------------------------------------------------------------------
# End-to-end over main() itself.
#
# Unit-testing failed_stages alone is not enough: it leaves the three lines that
# ARE the fix unguarded (the aggregation call, the banner conditional, and
# `return 1 if failed else 0`). Deleting the return reduces `sys.exit(main())`
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


def _write(path, payload=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload if payload is not None
                               else {'document_uid': UID}))
    return str(path)


def _run_main(tmp_path, monkeypatch, capsys, fail=()):
    """Run main() end to end with every stage runner stubbed. Returns (rc, stdout)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'data/sample_cvs/word').mkdir(parents=True)
    (tmp_path / f'data/sample_cvs/word/{UID}.docx').write_bytes(b'PK\x03\x04fake')

    def boom_if(stage):
        if stage in fail:
            raise RuntimeError(f"simulated {stage} failure")

    def stub(stage, result):
        def _stub(*args, **kwargs):
            boom_if(stage)
            return result() if callable(result) else result
        return _stub

    monkeypatch.setattr(run_full_pipeline, 'get_cv_hierarchy_chunked', stub(
        '1a', lambda: ([{'level': 'H1', 'text': 'Education', 'children': []}],
                       {'extraction_cost': 0.01})))
    monkeypatch.setattr(run_full_pipeline, 'run_stage_1b', stub(
        '1b', lambda: ({'meta': {'total_sections': 3, 'leaf_sections': 2}},
                       _write(_FILES['1b']))))
    monkeypatch.setattr(run_full_pipeline, 'run_stage_2', stub(
        '2', lambda: ({'total_cost': 0.02, 'total_entries': 400},
                      _write(_FILES['2']))))
    monkeypatch.setattr(run_full_pipeline, 'run_stage_3a', stub(
        '3a', lambda: {'output_path': _write(_FILES['3a']),
                       'stats': {'cost': 0.01}, 'node_count': 12}))
    monkeypatch.setattr(run_full_pipeline, 'run_stage_3b', stub(
        '3b', lambda: {'output_path': _write(_FILES['3b']), 'stats': {'cost': 0.01},
                       'total_entries': 400, 'code_distribution': {'A': 3}}))
    monkeypatch.setattr(run_full_pipeline, 'run_stage_4', stub(
        '4', lambda: {'output_path': _write(_FILES['4']),
                      'output': {'total_cost': 0.03, 'total_entries': 400,
                                 'stats': {'extracted': 400}}}))
    monkeypatch.setattr(run_full_pipeline, 'run_stage_4_5', stub(
        '4.5', lambda: _write(_FILES['4.5'], {'research_summary': {
            'method': 'llm', 'm1_score': 0.9, 'summary_length': 500}})))
    monkeypatch.setattr(run_full_pipeline, 'run_stage5', stub(
        '5', lambda: (_write(_FILES['5']), {'enriched': 10})[1]))
    monkeypatch.setattr(run_full_pipeline, 'run_stage5b', stub(
        '5b', lambda: _write(_FILES['5b'],
                             {'institution_enrichment_stats': {'cost': 0.0}})))
    monkeypatch.setattr(run_full_pipeline, 'run_stage_5c', stub(
        '5c', lambda: _write(_FILES['5c'])))
    monkeypatch.setattr(run_full_pipeline, 'run_stage_5d', stub(
        '5d', lambda: _write(_FILES['5d'])))

    def stage6(*args, **kwargs):
        boom_if('6')
        path = tmp_path / _FILES['6']
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'PK\x03\x04fake')
        return str(path)
    monkeypatch.setattr(run_full_pipeline, 'run_stage6', stage6)

    monkeypatch.setattr(sys, 'argv', ['run_full_pipeline.py', UID])
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
    assert "stage_6: simulated 6 failure" in out


def test_a_mid_pipeline_crash_also_exits_non_zero(tmp_path, monkeypatch, capsys):
    """The class fix: not just the terminal stage."""
    rc, out = _run_main(tmp_path, monkeypatch, capsys, fail={'3b'})
    assert rc == 1
    assert "PIPELINE COMPLETE WITH ERRORS" in out
    assert "stage_3b: simulated 3b failure" in out


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


if __name__ == "__main__":
    # Only the fixture-free tests; the main() ones need tmp_path/monkeypatch/capsys.
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn) and not _fn.__code__.co_argcount:
            _fn()
    print("OK")


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


def test_the_inert_model_flag_is_gone(tmp_path, monkeypatch, capsys):
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
