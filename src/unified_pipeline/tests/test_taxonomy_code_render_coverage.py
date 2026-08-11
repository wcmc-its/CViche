"""Guard for taxonomy codes stage 3b/3a can assign that stage 6 cannot render (#529).

taxonomy_v7.json is the live source both stage_3a_header_taxonomy_mapper.py
and stage_3b_entry_classifier.py load their code catalog from (confirmed by
grep -- core/taxonomy_mapper_v2.py's separate VALID_TAXONOMY_CODES is a stale,
unused-on-the-live-path set of an older taxonomy version, see #383/#384).

A code in that catalog with no render route falls to T. APPENDIX "by
construction, regardless of confidence or content" (N2, #529).
run_doctor's lint_taxonomy_code_coverage catches this per run, on real
classification output; this test catches a NEW gap before any CV hits it,
same shape as test_retired_code_normalization.py's
test_every_retired_target_has_a_render_route.

Self-contained: no DB, no FastAPI app, no LLM, no template. Run with:

    python3 -m pytest src/unified_pipeline/tests/test_taxonomy_code_render_coverage.py -p no:cacheprovider
"""
import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage_6_word_template import RENDER_ROUTED_CODES  # noqa: E402
from unified_pipeline.doctor.lints.extraction import (  # noqa: E402
    _RENDERED_BUT_NOT_IN_RENDER_ROUTED_CODES,
)

_TAXONOMY_PATH = _SRC / "unified_pipeline" / "core" / "taxonomy_v7.json"

# Known, already-tracked gaps as of 2026-08-11 -- codes with NO render route
# at all, as opposed to _RENDERED_BUT_NOT_IN_RENDER_ROUTED_CODES (which DO
# render, just also duplicate into the appendix -- a different defect, see
# lint_taxonomy_code_coverage's docstring). Each has its own issue, so this
# test isn't the place to relitigate WHY they're unmapped -- it exists only
# to catch the set growing without anyone noticing. Update this set (and
# file/link an issue) when a real new gap is found; do not widen it just to
# make CI pass without doing that.
_KNOWN_GAPS = frozenset({
    'J',                                # #260 -- percent-effort rows have no TAXONOMY_TO_SECTION entry
    'N2',                               # #529 -- this issue; needs a placement decision
    'M2', 'M4', 'M4A', 'M4B', 'M4C',    # #291 -- parked, needs a WCM-format decision (status-aware routing)
    'N1', 'N3',                         # no separately-filed issue found; same open question as N2
})


def test_taxonomy_catalog_matches_known_gaps_exactly():
    """If this fails because the actual gap set grew, a code was added to
    taxonomy_v7.json (or lost its renderer) without a render route -- file
    an issue before touching _KNOWN_GAPS. If it fails because the set
    shrank, a fix landed -- remove the code(s) here and close the tracking
    issue."""
    codes = {c["code"] for c in json.loads(_TAXONOMY_PATH.read_text())["codes"]}
    routed = RENDER_ROUTED_CODES | _RENDERED_BUT_NOT_IN_RENDER_ROUTED_CODES | {'M1', 'T'}
    actual_gaps = codes - routed
    assert actual_gaps == _KNOWN_GAPS


if __name__ == "__main__":
    test_taxonomy_catalog_matches_known_gaps_exactly()
    print("ok")
