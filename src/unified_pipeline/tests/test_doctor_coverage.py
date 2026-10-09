"""source_line_coverage (doctor/lints/coverage.py, #1588).

Synthetic fixtures only: invented titles, people and places.

    python3 -m pytest src/unified_pipeline/tests/test_doctor_coverage.py -q -p no:cacheprovider
"""
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.doctor.lints.coverage import (  # noqa: E402
    COVERAGE_EVIDENCE_MAX,
    lint_source_line_coverage,
)

#: Seven words, six pairs: judged.
_LOST = "Visiting lecturer in comparative widget anatomy at Example Polytechnic"
_KEPT = "Founding member of the regional gizmo research consortium"


def _p(*texts):
    """Rendered blocks, as the doctor's blocks view reads them."""
    return [("p", text) for text in texts]


def _3b(*entries):
    """Stage-3b entries: (element_idx_start, taxonomy_code, text)."""
    return {"entries": [{"element_idx_start": idx, "taxonomy_code": code, "text": text}
                        for idx, code, text in entries]}


def _run(source, blocks, **views):
    return lint_source_line_coverage(source, blocks, **views)


def test_a_source_line_absent_from_the_output_warns_on_its_entry():
    findings = _run([_KEPT, _LOST], _p(_KEPT), stage3b=_3b((4, "D3", _KEPT), (7, "D3", _LOST)))
    assert [(f["severity"], f["message"].split(":")[0]) for f in findings] == [("INFO", "entry 7")]
    assert findings[0]["evidence"] == [_LOST]
    assert findings[0]["message"].endswith("(lowest 0%)")


def test_a_line_with_no_stage3b_entry_is_one_finding_with_no_index():
    findings = _run([_LOST], _p(_KEPT), stage3b=_3b((4, "D3", _KEPT)))
    assert [f["message"].split(":")[0] for f in findings] == ["no stage-3b entry"]


def test_a_rendered_line_is_silent_whatever_the_word_order_and_punctuation():
    rendered = "Example Polytechnic | Visiting lecturer, comparative widget anatomy"
    assert _run([_LOST], _p(rendered)) == []


def test_a_citation_reformatted_by_5d_is_silent():
    source = "Doe, J.A., Roe, R. (1999). Widget torsion in common laboratory gerbils. J Widget Sci 3:1-5."
    rendered = "1. Doe JA, Roe R. Widget torsion in common laboratory gerbils. J Widget Sci. 1999;3:1-5."
    assert _run([source], _p(rendered)) == []


def test_text_in_the_appendix_or_a_tracked_deletion_is_accounted_for():
    assert _run([_LOST], _p("T. APPENDIX", _LOST)) == []
    assert _run([_LOST], _p(_KEPT), deleted_blocks=_p(_LOST)) == []


def test_the_pairs_are_read_one_output_line_at_a_time():
    """Every word rendered, but no two neighbours on one rendered line: not covered."""
    scattered = "Visiting comparative anatomy Polytechnic\nlecturer widget Example"
    assert len(_run([_LOST], _p(scattered))) == 1
    assert _run([_LOST], _p(scattered.replace("\n", " "))) == []


def test_under_half_the_pairs_warns_and_half_does_not():
    # 7 words of 3+ letters ("in" and "at" are not read), 6 pairs: the first 4
    # words cover 3 of them (50%, silent); the first 3 cover 2 (33%).
    half, third = "Visiting lecturer in comparative widget", "Visiting lecturer in comparative"
    assert _run([_LOST], _p(half)) == []
    assert _run([_LOST], _p(third))[0]["message"].endswith("(lowest 33%)")


def test_short_lines_headings_and_boilerplate_are_not_judged():
    short = "Widget anatomy lecturer, Example Polytechnic"  # 5 words, 4 pairs
    heading = "Invited lectures at national and international widget meetings"
    stage1a = {"hierarchy": [{"text": "INVITED LECTURES AT NATIONAL AND INTERNATIONAL WIDGET MEETINGS:",
                              "children": []}]}
    assert _run([short, heading], _p(_KEPT), stage1a=stage1a) == []
    assert len(_run([heading], _p(_KEPT))) == 1  # the same heading without 1a is judged


def test_an_author_list_on_its_own_line_is_not_judged():
    names = "Jane A. Doe, Richard Roe, Mei-Lin Chan, Omar Q. Example, and Ada Sample"
    assert _run([names], _p("Doe JA, Roe R, Chan M, Example OQ, Sample A")) == []


def test_personal_data_entries_and_protected_values_are_not_judged():
    home = "Home residence at forty two quiet meadow lane in Sampletown"
    birth = "Date of Birth: January 1, 1960, born in the town of Sampleville"
    assert _run([home], _p(_KEPT), stage3b=_3b((1, "A", home))) == []
    assert _run([birth], _p(_KEPT)) == []


def test_lines_of_one_entry_share_a_finding_and_quote_at_most_three():
    lines = [f"{_LOST} number {word}" for word in ("one", "two", "three", "four")]
    findings = _run(lines, _p(_KEPT), stage3b=_3b((9, "Q2", " ".join(lines))))
    assert len(findings) == 1
    assert findings[0]["message"].startswith("entry 9: 4 source line(s)")
    assert len(findings[0]["evidence"]) == COVERAGE_EVIDENCE_MAX


def test_a_source_paragraph_is_judged_one_printed_line_at_a_time():
    paragraph = f"{_KEPT}\n{_LOST}"
    findings = _run([paragraph], _p(_KEPT))
    assert [e for f in findings for e in f["evidence"]] == [_LOST]
