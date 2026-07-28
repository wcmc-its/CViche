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

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
