"""CODING_STANDARDS.md 6.4 contract test: run_full_pipeline.py's stdout is read
by another process, so its shape is pinned here.

The consumer is ``scripts/run_corpus_batch.sh``. Lines 142-147 of that script
build every metric column of ``summary.tsv`` by grepping this CLI's log::

    sec=$(grep -oE 'Top-level sections: [0-9]+' "$log" | grep -oE '[0-9]+' | tail -1)
    hdr=$(grep -oE 'Total headers: [0-9]+'      "$log" | grep -oE '[0-9]+' | tail -1)
    ent=$(grep -oE 'Entries extracted: [0-9]+'  "$log" | grep -oE '[0-9]+' | tail -1)
    cls=$(grep -oE 'Entries classified: [0-9]+' "$log" | grep -oE '[0-9]+' | tail -1)
    mdl=$(grep -oE '^Models: .*' "$log" | tail -1 | sed 's/^Models: //' | tr '\\t' ' ')

Three properties are load-bearing and none of them were guarded before #780:

1. Each literal is spelled exactly as the script greps it. Rewording one --
   or migrating that ``print()`` to ``logger.*`` -- blanks a column of
   ``summary.tsv`` silently, on every CV of every batch.
2. ``^Models:`` is ANCHORED at line start, so any logging prefix in front of it
   (a timestamp, a level, a logger name) is equivalent to deleting the column.
3. ``tail -1`` takes the LAST occurrence, which is the summary block's. Stage
   narration alone would satisfy a naive substring check.

The patterns are read out of the batch script itself rather than retyped, so
this test fails if either side of the contract moves without the other.

Self-contained: every stage runner is stubbed by the harness this reuses from
``test_run_full_pipeline_exit_status``. No LLM, no network, no real docx.

    python3 -m pytest src/unified_pipeline/tests/test_run_full_pipeline_stdout_contract.py -p no:cacheprovider
"""

import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(Path(__file__).parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent))

from test_run_full_pipeline_exit_status import _run_main  # noqa: E402

_BATCH_SCRIPT = _ROOT / "scripts" / "run_corpus_batch.sh"

# What the script greps, minus its `| grep -oE '[0-9]+'` value-extraction step.
_EXPECTED_BATCH_PATTERNS = {
    'Top-level sections: [0-9]+',
    'Total headers: [0-9]+',
    'Entries extracted: [0-9]+',
    'Entries classified: [0-9]+',
    '^Models: .*',
}
_VALUE_EXTRACTOR = '[0-9]+'
_SUMMARY_BANNER = "PIPELINE COMPLETE"


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
    run_full_pipeline.py (e.g. 'Total headers:' -> 'Headers total:'), or put a
    logging prefix in front of the Models line.
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
    for literal in ('Top-level sections:', 'Total headers:',
                    'Entries extracted:', 'Entries classified:'):
        assert literal in summary, f"{literal!r} is missing from the summary block"


def test_the_values_the_batch_script_would_record(tmp_path, monkeypatch, capsys):
    """Reproduces the script's whole pipeline -- grep, extract, tail -1 -- and
    checks the number it lands on, not merely that a line exists.

    The stub hierarchy is one top-level node with no children, stage 2 reports
    400 entries and stage 3b classifies 400.
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


def test_the_models_line_starts_the_line_it_is_on(tmp_path, monkeypatch, capsys):
    """`grep -oE '^Models: .*'` is anchored: a logging prefix would blank the
    model column even though the text is still on the line.

    Mutant that kills this: emit the models line through logger.info(), whose
    dictConfig format prefixes it with an asctime/level/name.
    """
    monkeypatch.setattr("run_full_pipeline.format_models_used",
                        lambda: "sentinel-model (3 calls)")
    _, out = _run_main(tmp_path, monkeypatch, capsys)
    models_lines = [line for line in out.splitlines() if "Models: " in line]
    assert models_lines, "no Models line at all"
    assert all(line.startswith("Models: ") for line in models_lines), (
        f"the Models line is prefixed: {models_lines!r}")
    assert models_lines[-1] == "Models: sentinel-model (3 calls)"


def test_no_stage_narration_was_migrated_to_the_logger(tmp_path, monkeypatch, capsys):
    """The stale __main__ comment claimed the narration had moved to logger.*.
    It had not, and it must not: these five strings are a wire protocol.

    Mutant that kills this: replace print() with logger.info() in _stage_1a or
    print_summary -- the string then never reaches the captured stdout at all
    under pytest's default (no handler) configuration.
    """
    _, out = _run_main(tmp_path, monkeypatch, capsys)
    for literal in ('Top-level sections:', 'Total headers:', 'Entries extracted:',
                    'Entries classified:', 'Models: '):
        assert literal in out
