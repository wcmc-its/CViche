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

from unified_pipeline.doctor.lints.extraction import (  # noqa: E402
    _RENDERED_BUT_NOT_IN_RENDER_ROUTED_CODES,
)
from unified_pipeline.stage_6_word_template import RENDER_ROUTED_CODES  # noqa: E402

_TAXONOMY_PATH = _SRC / "unified_pipeline" / "core" / "taxonomy_v7.json"

# Known, already-tracked gaps as of 2026-08-11 -- codes with NO render route
# at all, as opposed to _RENDERED_BUT_NOT_IN_RENDER_ROUTED_CODES (which DO
# render via a writer that has no code dispatch, see
# lint_taxonomy_code_coverage's docstring). Each has its own issue, so this
# test isn't the place to relitigate WHY they're unmapped -- it exists only
# to catch the set growing without anyone noticing. Update this set (and
# file/link an issue) when a real new gap is found; do not widen it just to
# make CI pass without doing that.
_KNOWN_GAPS = frozenset({
    # 'J' is NOT here: #260 gave it a passthrough render route
    # (stage6/sections/passthrough.py's _fill_percent_effort), so it now
    # belongs in _RENDERED_BUT_NOT_IN_RENDER_ROUTED_CODES below, same as E/G.
    # 'N1'/'N2' are NOT here: #529 gave both a render route (mentoring.py's
    # _fill_program_leadership/_fill_training_grants), routed the same way
    # N3A/N3B already were, via RENDER_ROUTED_CODES.
    # 'M4'/'M4A'/'M4B'/'M4C' are NOT here: #291 removed them from the taxonomy
    # (clinical trials file as M2A/M2B).
    'M2',                                # parent container code; 3b assigns M2A/M2B/M2C
    # 'N3' has no code route, but 3b does assign it: stage 6 resolves each
    # N3 that names a mentee or states a year to N3A/N3B per entry
    # (`_route_bare_mentee`, #1574), and the rest still reach the Appendix.
    'N3',
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


def test_render_exceptions_still_wired_into_generate():
    """_RENDERED_BUT_NOT_IN_RENDER_ROUTED_CODES exempts E, G, J from the
    coverage lint because the passthrough writer renders them outside
    the RENDER_ROUTED_CODES dispatch table (see extraction.py's comment on
    this set). J joined E and G at #260, dispatched from the same
    `_fill_passthrough_sections(` call this test already pins -- there is no
    separate hook to add a second assertion for. If that writer's hook is
    ever removed from generate() -- e.g. during a stage-6 decomposition --
    the exemption goes stale and this lint silently stops catching what
    would then be a real gap (review on #588).

    This doesn't prove the codes still render (that needs a real CV, which
    is what the corpus doctor sweep is for) -- only that the two call sites
    the exemption depends on still exist.
    """
    src = (_SRC / "unified_pipeline" / "stage_6_word_template.py").read_text()
    assert "self._fill_passthrough_sections(" in src, (
        "E/G/J's passthrough hook is gone from generate() -- if they no "
        "longer render, remove them from "
        "_RENDERED_BUT_NOT_IN_RENDER_ROUTED_CODES so the coverage lint "
        "catches the gap"
    )
    assert "N4" in RENDER_ROUTED_CODES and "N4" not in _RENDERED_BUT_NOT_IN_RENDER_ROUTED_CODES, (
        "N4 renders via _fill_mentoring and must be routed, not exempted -- "
        "an exempted N4 duplicates into the Appendix (#587)"
    )


if __name__ == "__main__":
    test_taxonomy_catalog_matches_known_gaps_exactly()
    test_render_exceptions_still_wired_into_generate()
    print("ok")
