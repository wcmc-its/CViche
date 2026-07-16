"""Coverage metric must not report merged content as lost (see compute_metrics).

Stage 2 legitimately merges adjacent source content into one entry, inserting
text mid-line. The source line stops being a contiguous substring even though
the entry is a superset of it. Requiring verbatim containment scored real CVs
at 96.0% (web053) and 67.5% (web057) on the 2026-07-15 corpus with nothing
actually lost.

    python3 -m pytest src/unified_pipeline/tests/test_segmentation_coverage.py -p no:cacheprovider
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.segmentation_regression import compute_metrics  # noqa: E402


def _metrics(source_lines, entry_texts):
    entries = [{"text": t, "element_type": "paragraph", "element_idx_start": i}
               for i, t in enumerate(entry_texts)]
    return compute_metrics(source_lines, {"hierarchy": []}, {"entries": entries})


def test_midline_merge_is_not_lost():
    """The real web053 case: stage 2 injects the institution mid-line."""
    source = ["\t\t1984-1989\t\t\t\tB.S.\t (Biology)"]
    entries = ["1984-1989    B.S. University of Utah (Biology)"]
    m = _metrics(source, entries)
    assert m["lost_lines"] == []
    assert m["text_coverage_pct"] == 100.0


def test_genuinely_absent_line_is_still_lost():
    source = ["Professor with Tenure, Department of Pediatrics and Genetics"]
    entries = ["Some completely unrelated entry about grants"]
    m = _metrics(source, entries)
    assert len(m["lost_lines"]) == 1


def test_line_is_not_covered_by_tokens_scattered_across_entries():
    """Coverage is per-entry: unrelated entries must not jointly cover a line."""
    source = ["Assistant Professor of Radiation Oncology"]
    entries = ["Assistant Professor", "of Radiation Oncology"]
    m = _metrics(source, entries)
    assert len(m["lost_lines"]) == 1
