"""#384: validate_and_correct_taxonomy_code maps unknown codes to T (Appendix).

That fallback is the right safety behavior, but it was silent -- a hallucinated
code, or a valid code missing from VALID_TAXONOMY_CODES (the sets have drifted,
see #383), sent real CV content to the Appendix with no signal. This guards that
the unknown-code path now logs at ERROR while still returning the safe T.

Self-contained: no DB, no FastAPI, no LLM call. Run with:
    python3 -m pytest src/unified_pipeline/tests/test_validate_taxonomy_code_logging.py -p no:cacheprovider
"""
import logging
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.core.taxonomy_mapper_v2 import (  # noqa: E402
    validate_and_correct_taxonomy_code,
)


def test_unknown_code_maps_to_T_and_logs_error(caplog):
    with caplog.at_level(logging.ERROR):
        code, reason = validate_and_correct_taxonomy_code(
            "S15", {"label": "Widgets", "entries": []}, context="Pass 1"
        )
    assert code == "T"                      # safe fallback preserved
    assert "INVALID CODE S15" in reason
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert errors, "unknown code must be logged at ERROR so Appendix routing is visible"
    assert "S15" in errors[0].getMessage()


def test_valid_code_is_silent(caplog):
    with caplog.at_level(logging.ERROR):
        code, reason = validate_and_correct_taxonomy_code("S1", {"label": "Publications"})
    assert code == "S1"
    assert reason == ""
    assert not [r for r in caplog.records if r.levelno == logging.ERROR]
