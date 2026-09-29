"""Put ``src/`` on sys.path for every test in this directory.

35 of the 39 test files carry this preamble by hand::

    _SRC = Path(__file__).resolve().parents[2]
    if str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))

The repo root lands on ``sys.path`` on its own (the ``__init__.py`` chain stops
pytest's basedir walk there), but ``src/`` does not — so ``import
unified_pipeline`` needs it added explicitly. The four files that omitted the
preamble only passed because an alphabetically-earlier file had already mutated
``sys.path`` as a side effect, and failed when run alone (#451)::

    $ python3 -m pytest src/unified_pipeline/tests/test_stage6_render_drops.py
    E   ModuleNotFoundError: No module named 'unified_pipeline'

conftest.py is imported before collection, so this fixes all four and makes the
per-file preamble redundant. The preambles are left in place: they are harmless,
removing 35 of them would touch every test file for no behaviour change, and
they keep each file runnable as a plain script via its ``__main__`` tail.
"""

import re
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


# orchestrator.PROGRESS_PATTERNS turns a stage's printed progress line into the
# web progress bar, so stages 2/3b/5d each pin their printer against it. Not
# imported: orchestrator.py's import permanently replaces process-wide
# sys.stdout with _RoutedStdout and pulls sqlalchemy/app.models into these
# DB-free pipeline tests (mrj4001's point #1 on PR #918). So read its source
# and pin it verbatim -- a change to the list fails here and forces a re-check
# of every stage's claim, rather than a progress bar silently changing.
_ORCHESTRATOR_PATH = _SRC.parent / "web_interface" / "backend" / "app" / "pipeline" / "orchestrator.py"
_PROGRESS_PATTERNS_SOURCE = (
    "PROGRESS_PATTERNS = [\n"
    '    # "Processing section 5 of 10" or "Processing 5/10"\n'
    "    re.compile(r'(?:Processing|Extracting|Mapping|Classifying|Enriching)"
    r"\s+(?:section\s+)?(\d+)\s*(?:of|/)\s*(\d+)', re.IGNORECASE)," "\n"
    '    # "Section 5/10" or "Entry 5/10"\n'
    "    re.compile(r'(?:Section|Entry|Item|Chunk|Node|Header|Publication|Grant|Position)"
    r"\s*(\d+)\s*(?:of|/)\s*(\d+)', re.IGNORECASE)," "\n"
    '    # "[5/10]" format\n'
    r"    re.compile(r'\[(\d+)\s*/\s*(\d+)\]')," "\n"
    '    # "5 of 10 sections" or "5 of 10 entries"\n'
    "    re.compile(r'(\\d+)\\s+of\\s+(\\d+)\\s+(?:sections?|entries?|items?|chunks?|nodes?|"
    "headers?|publications?|grants?|positions?)', re.IGNORECASE),\n"
    "]"
)


@pytest.fixture(scope="session")
def progress_patterns() -> list[re.Pattern]:
    source = _ORCHESTRATOR_PATH.read_text(encoding="utf-8")
    start = source.index("PROGRESS_PATTERNS = [")
    block = source[start:source.index("\n]", start) + len("\n]")]
    assert block == _PROGRESS_PATTERNS_SOURCE, (
        "orchestrator.PROGRESS_PATTERNS' source changed -- re-check the stage "
        "2/3b/5d progress-line tests' claims, then update the pin above"
    )
    # Compiled from the block just read, not hand-retyped: pattern plus an
    # optional trailing re.IGNORECASE flag, per re.compile(r'...', ...) call.
    calls = re.findall(r"re\.compile\(r'((?:[^'\\]|\\.)*)'(?:,\s*(re\.[A-Z]+))?\)", block)
    assert len(calls) == 4, "expected exactly 4 re.compile(...) calls in the block"
    return [re.compile(p, getattr(re, flag.split(".")[1]) if flag else 0) for p, flag in calls]
