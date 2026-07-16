"""Guard for retired-taxonomy-code normalization in Stage 6.

Stage-3b still tags some patent entries with the retired ``M3`` code. ``M3`` has
no entry in ``TAXONOMY_TO_SECTION`` and the patents renderer only pulls the
``M2D`` group, so those entries were silently dropped at render (corpus doctor
sweep 2026-07-15: web055 lost 9 patent filings). ``normalize_retired_code``
rewrites the retired code to its live equivalent before grouping.

Self-contained: no DB, no FastAPI app, no LLM, no template. Run with:

    python3 -m pytest src/unified_pipeline/tests/test_retired_code_normalization.py -p no:cacheprovider
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage_6_word_template import (  # noqa: E402
    RETIRED_TAXONOMY_CODES,
    TAXONOMY_TO_SECTION,
    normalize_retired_code,
)


def test_m3_normalizes_to_m2d_and_preserves_original():
    entry = {"taxonomy_code": "M3", "text": "US Patent 1234567, inventors: ..."}
    assert normalize_retired_code(entry) == "M2D"
    assert entry["taxonomy_code"] == "M2D"
    assert entry["taxonomy_code_original"] == "M3"


def test_m4a_m4b_clinical_trials_normalize_to_grants():
    for old, live in [("M4A", "M2A"), ("M4B", "M2B")]:
        e = {"taxonomy_code": old, "text": "Phase I trial ..."}
        assert normalize_retired_code(e) == live
        assert e["taxonomy_code"] == live
        assert e["taxonomy_code_original"] == old


def test_live_code_is_untouched():
    entry = {"taxonomy_code": "H", "text": "Some honor"}
    assert normalize_retired_code(entry) == "H"
    assert entry["taxonomy_code"] == "H"
    assert "taxonomy_code_original" not in entry


def test_missing_code_defaults_to_t():
    assert normalize_retired_code({}) == "T"


def test_every_retired_target_has_a_render_route():
    # A rename is only safe if the live target actually renders.
    for old, live in RETIRED_TAXONOMY_CODES.items():
        assert live in TAXONOMY_TO_SECTION, f"{old}->{live} has no render route"


if __name__ == "__main__":
    test_m3_normalizes_to_m2d_and_preserves_original()
    test_live_code_is_untouched()
    test_missing_code_defaults_to_t()
    test_every_retired_target_has_a_render_route()
    print("ok")
