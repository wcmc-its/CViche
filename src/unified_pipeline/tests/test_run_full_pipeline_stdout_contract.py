"""CODING_STANDARDS.md 6.4 contract test: run_full_pipeline.py's stdout is read
by another process, so its shape is pinned here.

The consumer is ``scripts/run_corpus_batch.sh``. Lines 152-163 of that script
build every metric column of ``summary.tsv`` by grepping this CLI's log::

    sec=$(grep -oE 'Top-level sections: [0-9]+' "$log" | grep -oE '[0-9]+' | tail -1)
    hdr=$(grep -oE 'Total headers: [0-9]+'      "$log" | grep -oE '[0-9]+' | tail -1)
    ent=$(grep -oE 'Entries extracted: [0-9]+'  "$log" | grep -oE '[0-9]+' | tail -1)
    cls=$(grep -oE 'Entries classified: [0-9]+' "$log" | grep -oE '[0-9]+' | tail -1)
    dft=$(grep -oE 'Entries defaulted: [0-9]+'  "$log" | grep -oE '[0-9]+' | tail -1)
    mdl=$(grep -oE '^Models: .*' "$log" | tail -1 | sed 's/^Models: //' | tr '\\t' ' ')

Three properties are load-bearing and none of them were guarded before #780:

1. Each literal is spelled exactly as the script greps it. Rewording one blanks
   a column of ``summary.tsv`` silently, on every CV of every batch.
2. ``^Models:`` is ANCHORED at line start, so any logging prefix in front of it
   (a timestamp, a level, a logger name) is equivalent to deleting the column.
3. ``tail -1`` takes the LAST occurrence, which is the summary block's. Stage
   narration alone would satisfy a naive substring check.

Since #780 the narration is ``logger.info`` rather than ``print``, which makes a
fourth property load-bearing: ``configure_cli_logging()`` has to put those
records on stdout formatted as ``%(message)s`` and nothing else, with the
diagnostics on stderr. A formatter change is now the way this contract breaks,
so the tests below assert on both streams and one of them runs the real process
and compares its whole stdout to a golden.

The patterns are read out of the batch script itself rather than retyped, so
this test fails if either side of the contract moves without the other.

Self-contained: every stage runner is stubbed by the harness this reuses from
``test_run_full_pipeline_exit_status``. No LLM, no network, no real docx.

    python3 -m pytest src/unified_pipeline/tests/test_run_full_pipeline_stdout_contract.py -p no:cacheprovider
"""

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(Path(__file__).parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent))

from test_run_full_pipeline_exit_status import (  # noqa: E402
    _run_main,
    _run_main_streams,
    restore_logging_after_each_test,  # noqa: F401  (autouse fixture, used by import)
)

_BATCH_SCRIPT = _ROOT / "scripts" / "run_corpus_batch.sh"
_CLI = _ROOT / "run_full_pipeline.py"

# What the script greps, minus its `| grep -oE '[0-9]+'` value-extraction step.
_EXPECTED_BATCH_PATTERNS = {
    'Top-level sections: [0-9]+',
    'Total headers: [0-9]+',
    'Entries extracted: [0-9]+',
    'Entries classified: [0-9]+',
    'Entries defaulted: [0-9]+',
    '^Models: .*',
}
_VALUE_EXTRACTOR = '[0-9]+'
_SUMMARY_BANNER = "PIPELINE COMPLETE"
_METRIC_LITERALS = ('Top-level sections:', 'Total headers:',
                    'Entries extracted:', 'Entries classified:',
                    'Entries defaulted:')


def _batch_patterns():
    """Every regex scripts/run_corpus_batch.sh greps out of this CLI's stdout."""
    script = _BATCH_SCRIPT.read_text()
    found = set(re.findall(r"grep -oE '([^']+)'", script))
    return found - {_VALUE_EXTRACTOR}


def test_the_batch_script_still_greps_the_patterns_this_test_pins():
    """Guards the guard: if run_corpus_batch.sh changes what it looks for, the
    assertions below would silently start proving nothing."""
    assert _batch_patterns() == _EXPECTED_BATCH_PATTERNS


def test_every_batch_grep_matches_this_cli_s_stdout(tmp_path, monkeypatch, capsys):
    """The contract itself: each pattern the consumer greps finds a match.

    Mutant that kills this: reword any one of the four literals in
    run_full_pipeline.py (e.g. 'Total headers:' -> 'Headers total:'), or give
    the narration handler a formatter with a prefix in it.
    """
    rc, out = _run_main(tmp_path, monkeypatch, capsys)
    assert rc == 0
    for pattern in sorted(_EXPECTED_BATCH_PATTERNS):
        assert re.search(pattern, out, re.MULTILINE), (
            f"scripts/run_corpus_batch.sh greps {pattern!r} and found nothing; "
            "that column of summary.tsv would be empty for every CV")


def test_the_metric_literals_appear_in_the_summary_block(tmp_path, monkeypatch, capsys):
    """`tail -1` takes the summary occurrence, not the stage narration, so the
    summary is the copy that has to be there.

    Mutant that kills this: delete the four Processing Stats lines from
    print_summary() and keep only the per-stage narration.
    """
    _, out = _run_main(tmp_path, monkeypatch, capsys)
    assert _SUMMARY_BANNER in out
    summary = out.split(_SUMMARY_BANNER, 1)[1]
    for literal in _METRIC_LITERALS:
        assert literal in summary, f"{literal!r} is missing from the summary block"


def test_the_values_the_batch_script_would_record(tmp_path, monkeypatch, capsys):
    """Reproduces the script's whole pipeline -- grep, extract, tail -1 -- and
    checks the number it lands on, not merely that a line exists.

    The stub hierarchy is one top-level node with no children, stage 2 reports
    400 entries and stage 3b classifies 400 with 0 fallbacks (#810).
    """
    _, out = _run_main(tmp_path, monkeypatch, capsys)

    def column(pattern):
        matches = re.findall(pattern, out, re.MULTILINE)
        assert matches, f"no match for {pattern!r}"
        return re.findall(_VALUE_EXTRACTOR, matches[-1])[-1]  # the script's tail -1

    assert column('Top-level sections: [0-9]+') == '1'
    assert column('Total headers: [0-9]+') == '1'
    assert column('Entries extracted: [0-9]+') == '400'
    assert column('Entries classified: [0-9]+') == '400'
    assert column('Entries defaulted: [0-9]+') == '0'


def test_stage_3b_narration_reports_llm_classified_not_total_when_some_default(
        tmp_path, monkeypatch, caplog):
    """#810: a run where some entries fell back to a default code must log
    'Entries classified:' as the LLM-classified count, not `total_entries`
    (which includes the fallbacks) -- and the new 'Entries defaulted:' line
    must carry the fallback count. Unit-level, not the full harness: this
    drives `_stage_3b` directly so the case doesn't need wiring a fallback
    count through every other stage's stub.

    Mutant that kills this: revert to logging `result['total_entries']`, or
    drop the new 'Entries defaulted:' line.
    """
    import logging

    import run_full_pipeline as r

    monkeypatch.setattr(r, "run_stage_3b", lambda *, document_uid, stage_3a_path: {
        "output_path": "c.json",
        "stats": {"cost": 0.01, "llm_classified": 490, "fallback_entries": 10},
        "total_entries": 500, "code_distribution": {"A": 3},
    })
    ctx = r.PipelineContext(cv_path=tmp_path / "sentinel_uid.docx", document_uid="sentinel_uid",
                            outputs={"2": "b.json", "3a": "a.json"})

    with caplog.at_level(logging.INFO, logger="run_full_pipeline"):
        result = r._stage_3b(ctx)

    messages = [rec.getMessage() for rec in caplog.records]
    assert "  Entries classified: 490" in messages
    assert "  Entries defaulted: 10" in messages
    assert result.stats["entries_classified"] == 490
    assert result.stats["fallback_entries"] == 10


def test_stage_3b_narration_reports_zero_defaulted_on_the_clean_case(
        tmp_path, monkeypatch, caplog):
    """The negative case for the test above: no fallbacks still prints the
    line (as '0'), rather than omitting it -- the batch script's grep and
    the contract tests above both require the line to always be present."""
    import logging

    import run_full_pipeline as r

    monkeypatch.setattr(r, "run_stage_3b", lambda *, document_uid, stage_3a_path: {
        "output_path": "c.json",
        "stats": {"cost": 0.01, "llm_classified": 400, "fallback_entries": 0},
        "total_entries": 400, "code_distribution": {},
    })
    ctx = r.PipelineContext(cv_path=tmp_path / "sentinel_uid.docx", document_uid="sentinel_uid",
                            outputs={"2": "b.json", "3a": "a.json"})

    with caplog.at_level(logging.INFO, logger="run_full_pipeline"):
        result = r._stage_3b(ctx)

    messages = [rec.getMessage() for rec in caplog.records]
    assert "  Entries classified: 400" in messages
    assert "  Entries defaulted: 0" in messages
    assert result.stats["fallback_entries"] == 0


def test_the_models_line_starts_the_line_it_is_on(tmp_path, monkeypatch, capsys):
    """`grep -oE '^Models: .*'` is anchored: a logging prefix would blank the
    model column even though the text is still on the line.

    Mutant that kills this: drop the `message_only` formatter from
    configure_cli_logging() and let the narration handler use `plain`, whose
    format prefixes every record with an asctime/level/name.
    """
    monkeypatch.setattr("run_full_pipeline.format_models_used",
                        lambda: "sentinel-model (3 calls)")
    _, out = _run_main(tmp_path, monkeypatch, capsys)
    models_lines = [line for line in out.splitlines() if "Models: " in line]
    assert models_lines, "no Models line at all"
    assert all(line.startswith("Models: ") for line in models_lines), (
        f"the Models line is prefixed: {models_lines!r}")
    assert models_lines[-1] == "Models: sentinel-model (3 calls)"


# -- #780: the narration is logger.info now, so the split is what is pinned ---


def test_the_cli_makes_no_print_calls_at_all():
    """The reviewer's ask on #780, checked structurally rather than by eye: the
    narration goes through the logger, all of it.

    Not a substring search -- an AST walk, so the word `print(` inside a comment
    or a docstring cannot satisfy it and cannot break it either.

    Mutant that kills this: put any one print() back.
    """
    tree = ast.parse(_CLI.read_text())
    prints = [node.lineno for node in ast.walk(tree)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id == 'print']
    assert prints == [], f"print() calls left in run_full_pipeline.py at lines {prints}"


def test_each_parsed_line_reaches_stdout_with_nothing_in_front_of_it(
    tmp_path, monkeypatch, capsys
):
    """Substring matching would pass on `2026-09-10 INFO x   Total headers: 3`,
    and the batch script's `grep -oE 'Total headers: [0-9]+'` would too -- but
    the anchored Models grep would not, and a prefixed log line is the failure
    #780 had to avoid. Anchored here for all five.

    Mutant that kills this: give the narration handler the `plain` formatter.
    """
    _, out = _run_main(tmp_path, monkeypatch, capsys)
    for literal in _METRIC_LITERALS:
        assert re.search(rf"(?m)^  {re.escape(literal)} \d+$", out), (
            f"{literal!r} never appears as a bare, two-space-indented line")
    assert re.search(r"(?m)^Models: \S", out), "the Models line is prefixed or missing"


def test_the_stage_failure_warning_goes_to_stderr_not_to_the_parsed_stdout(
    tmp_path, monkeypatch, capsys
):
    """A stage failure is a diagnostic: WARNING and above leave by stderr, which
    is where the traceback already went. stdout stays the parsed stream.

    Mutant that kills this: emit the warning through logger.info, or drop the
    `not_narration` filter so every record lands on both handlers.
    """
    run = _run_main_streams(tmp_path, monkeypatch, capsys, fail={'6'})
    assert run.rc == 1
    assert "Warning: Stage 6 failed" in run.err
    assert "Warning: Stage 6 failed" not in run.out
    assert "Continuing with remaining stages" not in run.out
    # ...and the summary the batch script reads is still on stdout.
    assert "PIPELINE COMPLETE WITH ERRORS" in run.out


def test_the_narration_is_not_copied_onto_stderr_as_well(tmp_path, monkeypatch, capsys):
    """The narration must land on exactly one stream. Duplicating it doubles
    every line of a batch log, which redirects both into one file, and the
    script's `tail -1` then reads whichever copy came last.

    Mutant that kills this: delete the `not_narration` filter from the stderr
    handler, or set propagate: False without it.
    """
    run = _run_main_streams(tmp_path, monkeypatch, capsys)
    assert run.rc == 0
    for literal in (*_METRIC_LITERALS, "Models: ", _SUMMARY_BANNER):
        assert literal not in run.err, f"{literal!r} was duplicated onto stderr"


# -- the same split, in a real process ---------------------------------------

# A driver rather than the CLI itself: stubbing stage 1a is what makes the whole
# of stdout deterministic, so it can be compared byte for byte instead of
# grepped. It configures logging exactly as __main__ does.
_GOLDEN_DRIVER = '''
import sys
sys.path.insert(0, {root!r})
sys.argv = ["run_full_pipeline.py", "2097_Upton_Cv", "--stage", "1a"]
import run_full_pipeline as r
r._OUTPUTS_ROOT = r.Path('src/unified_pipeline/outputs')  # keep artifacts under the cwd (#490)
r.get_cv_hierarchy_chunked = lambda *, cv_path: (
    [{{"level": "H1", "text": "Education", "children": []}}],
    {{"extraction_cost": 0.01}})
r.configure_cli_logging()
sys.exit(r.main())
'''

_OUT = 'src/unified_pipeline/outputs/stage_1a_segmentation/2097_Upton_Cv_segmented.json'
_GOLDEN_STDOUT = [
    "=" * 80,
    "CV PROCESSING PIPELINE - STAGE 1A ONLY",
    "=" * 80,
    "Input: data/sample_cvs/word/2097_Upton_Cv.docx",
    "Document UID: 2097_Upton_Cv",
    "",
    "=" * 80,
    "STAGE 1A: HIERARCHY EXTRACTION",
    "=" * 80,
    "",
    "",
    "Stage 1a Complete",
    f"  Output: {_OUT}",
    "  Top-level sections: 1",
    "  Total headers: 1",
    "  Cost: $0.0100",
    "  Time: <duration>",
    "",
    "=" * 80,
    "PIPELINE COMPLETE",
    "=" * 80,
    "Document: 2097_Upton_Cv",
    "Models: none (no LLM calls recorded)",
    "",
    "Outputs:",
    f"  Stage 1a: {_OUT}",
    "",
    "Timing:",
    "  Stage 1a: <duration>",
    "  Total:    <duration>",
    "",
    "Costs:",
    "  Stage 1a: $0.0100",
    "  Total:    $0.0100",
    "",
    "Processing Stats:",
    "  Top-level sections: 1",
    "  Total headers: 1",
    "",
]


def _elapsed_normalised(line):
    """The only value that moves between runs is how long the stage took."""
    return re.sub(r"\d+(?:\.\d+)?s$", "<duration>", line)


def test_the_real_process_writes_exactly_these_lines_to_stdout(tmp_path):
    """The whole of stdout, byte for byte, from a real `python3` process with
    the real dictConfig -- not capsys, and not a substring.

    capsys tests cannot see a handler wired to the true `sys.stdout`, a
    `flush()` that never happens, or a stray write from an import. This one can:
    anything at all that reaches stdout and is not in this list fails it.

    Mutant that kills this: add a level or a timestamp to the narration
    formatter; send the narration to stderr; add a print() back.
    """
    (tmp_path / 'data/sample_cvs/word').mkdir(parents=True)
    (tmp_path / 'data/sample_cvs/word/2097_Upton_Cv.docx').write_bytes(b'PK\x03\x04fake')
    driver = tmp_path / 'golden_driver.py'
    driver.write_text(_GOLDEN_DRIVER.format(root=str(_ROOT)))
    env = {k: v for k, v in os.environ.items()
           if not (k.startswith('AWS_') or k in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY'))}

    proc = subprocess.run([sys.executable, str(driver)], cwd=tmp_path, env=env,
                          capture_output=True, text=True, timeout=120)

    assert proc.returncode == 0, f"driver failed; stderr:\n{proc.stderr[-2000:]}"
    assert [_elapsed_normalised(line)
            for line in proc.stdout.split('\n')] == _GOLDEN_STDOUT + ['']
