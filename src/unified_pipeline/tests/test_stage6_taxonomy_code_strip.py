"""Regression guard for taxonomy-code leaks in rendered bullets (issue #251).

Stage-3b classification codes (M2B, D1, S6, N3A …) sometimes ride at the front
of an entry's raw text. When such an entry falls through to the bullet-render
path, the bracketed code leaked verbatim into the faculty-facing document —
observed in real runs as "• [M2B] Project title: …" and "• [D1] Visiting
Professor …". ``_strip_taxonomy_code`` removes a leading bracketed code before
the bullet is composed. This test pins that behaviour and, importantly, that it
does NOT eat legitimate leading-bracket content.

    python3 -m pytest src/unified_pipeline/tests/test_stage6_taxonomy_code_strip.py -p no:cacheprovider

Self-contained: no DB, no template, no python-docx needed.
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage_6_word_template import _strip_taxonomy_code


def test_strips_leaked_taxonomy_codes():
    assert _strip_taxonomy_code("[M2B] Project title: Measuring Quality") == "Project title: Measuring Quality"
    assert _strip_taxonomy_code("[D1] Visiting Professor, Leiden") == "Visiting Professor, Leiden"
    assert _strip_taxonomy_code("[S6] 19. Ronny FMH, Black MA") == "19. Ronny FMH, Black MA"
    assert _strip_taxonomy_code("  [N3A] Ahmed Hassan") == "Ahmed Hassan"  # leading space tolerated


def test_leaves_legitimate_content_untouched():
    # numbered citation, not a taxonomy code (starts with a digit)
    assert _strip_taxonomy_code("[1] Smith J, et al.") == "[1] Smith J, et al."
    # a code-shaped token mid-line is not a leading prefix
    assert _strip_taxonomy_code("Grant [R01] awarded 2020") == "Grant [R01] awarded 2020"
    # no bracket at all
    assert _strip_taxonomy_code("Chief, Division of Infectious Diseases") == "Chief, Division of Infectious Diseases"
    # empty / falsy
    assert _strip_taxonomy_code("") == ""


if __name__ == "__main__":
    test_strips_leaked_taxonomy_codes()
    test_leaves_legitimate_content_untouched()
    print("OK")
