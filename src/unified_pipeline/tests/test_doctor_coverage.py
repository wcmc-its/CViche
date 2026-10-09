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


# The deterministic false-positive classes of YUY-SLC (#1588, doctor/PRECISION.md).

_CITATION = ("12. Jane A. Doe, Richard Roe, Mei-Lin Chan, and Omar Q. Example (2019). "
             "Widget torsion in common laboratory gerbils. J Widget Sci 3:1-5.")
_CITATION_5D = ("4. Doe JA, Roe R, Chan ML, Example OQ. Widget torsion in common "
                "laboratory gerbils. J Widget Sci. 2019;3:1-5.")


def test_a_citation_whose_author_list_5d_reformatted_is_silent():
    """Full first names are lost and every surname pair split, but the
    author list renders on the citation's line: its pairs count as rendered."""
    assert _run([_CITATION], _p(_CITATION_5D)) == []


def test_a_citation_whose_title_is_lost_still_warns_past_its_authors():
    rendered = "4. Doe JA, Roe R, Chan ML, Example OQ. Sprocket fatigue. J Widget Sci. 2019;3:1-5."
    assert len(_run([_CITATION], _p(rendered))) == 1


def test_an_author_list_5d_cut_to_et_al_still_counts_as_rendered():
    """5d keeps the first names of a long list and adds "et al.": the last
    names are rendered nowhere, the list still was."""
    source = ("Jane A. Doe, Richard Roe, Mei-Lin Chan, Omar Q. Example, Ada Sample, Bo Li, "
              "Ivan Tester, and Zed Q. Last (2019). Widget torsion in common laboratory gerbils.")
    rendered = "4. Doe JA, Roe R, Chan ML, Example OQ, Sample A, Li B, et al. Widget torsion in common laboratory gerbils."
    assert _run([source], _p(rendered)) == []


def test_an_author_list_rendered_nowhere_together_is_not_waived():
    """The surnames sit on separate rendered lines: the list was not rendered
    as a list, so its pairs are judged like any other."""
    lines = _p("Doe JA, Roe R", "Chan ML", "Example OQ",
               "Widget torsion in common laboratory gerbils. J Widget Sci. 2019;3:1-5.")
    assert len(_run([_CITATION], lines)) == 1


def test_an_author_line_with_a_list_number_and_a_year_is_not_judged():
    names = "7. Jane A. Doe, Richard Roe*, Mei-Lin Chan, and Omar Q. Example (2023)."
    assert _run([names], _p(_CITATION_5D)) == []
    lettered = "b) Jane Doe, Richard Roe, Mei Chan. Widget torsion in gerbils, 2019."
    assert _run([lettered], _p(_CITATION_5D)) == []


def test_a_record_line_is_judged_without_its_links():
    """5d drops a citation's DOI and URL: the record line is judged on its
    words, so a rendered record is silent and a lost one still warns."""
    cited = f"{_CITATION} https://doi.org/10.1234/widget.torsion.5678 HYPERLINK \"https://widgets.example.org/a/b\""
    assert _run([cited], _p(_CITATION_5D)) == []
    assert len(_run([f"{_LOST} https://widgets.example.org/lecture"], _p(_KEPT))) == 1
    long_link = f"{_LOST} https://www.widgets.example.org/lectures/archive/visiting/series/spring/page"
    assert _run([long_link], _p(_LOST)) == []


def test_a_line_that_is_little_but_a_link_keeps_the_link():
    """A contact e-mail or lab website line holds nothing but its link: the
    link is judged, so its loss is reported and its rendering is not."""
    website = "Lab website: HYPERLINK \"https://widgets.example.org/lab/\" https://widgets.example.org/lab/"
    assert len(_run([website], _p(_KEPT))) == 1
    assert _run([website], _p(_KEPT, "Lab website: https://widgets.example.org/lab/")) == []


def test_a_hyperlink_field_code_is_not_read():
    """Word's HYPERLINK field holds the link target ahead of the text it
    shows, often a redirect the document never prints."""
    field = 'Lab: HYPERLINK "https://safelinks.example.com/redirect/widgets/lab" https://widgets.example.org/lab/x'
    assert _run([field], _p("Lab: https://widgets.example.org/lab/x")) == []


_HEADER = "Course Title\tFormat\tRole\tTerm\tEnrollment"
_ROW = "Widget Anatomy\tLecture\tCourse Director\tFall 2019\t42"


def test_a_tabbed_column_header_row_over_its_data_rows_is_not_judged():
    rendered = "2019 - Widget Anatomy, Course Director (Lecture, 42 students)"
    assert _run([_HEADER, _ROW], _p(rendered)) == []


def test_a_tabbed_row_with_no_data_row_after_it_is_judged():
    assert len(_run([_HEADER, _LOST], _p(_LOST))) == 1
    with_digit = _HEADER.replace("Term", "Term 2019")
    findings = _run([with_digit, _ROW], _p("Widget Anatomy, Course Director, Lecture"))
    assert [e for f in findings for e in f["evidence"]] == [with_digit]


def test_a_dea_registration_stage_6_withholds_is_not_judged():
    dea = "Controlled Substance Registration Certificate, Drug Enforcement Administration AB1234567 05/2012-Present"
    assert _run([dea], _p(_KEPT)) == []
    # Without the number stage 6 renders the line, so its loss is judged.
    assert len(_run([dea.replace(" AB1234567", "")], _p(_KEPT))) == 1


def test_a_line_wrapped_mid_title_is_judged_with_the_line_before_it():
    """The tail opens with a bracket or quote it never opened: it is the
    rest of the line above, not a record of its own."""
    head = "Doe JA. Torsion. In Handbook of Widget Anatomy and Sprocket Repair (3rd"
    tail = "Edition), Edited by Roe R, Chan ML, and Example OQ, 2014. Pages twelve to twenty."
    rendered = "1. Doe JA. Torsion. In: Roe R, Chan ML, Example OQ, eds. Handbook of Widget Anatomy and Sprocket Repair. 3rd ed. 2014."
    assert len(_run([tail], _p(rendered))) == 1  # judged alone, the tail warns
    assert _run([head, tail], _p(rendered)) == []
    quoted_tail = "Repair,” co-presented with Richard Roe and Mei-Lin Chan at the gizmo meeting"
    assert _run([head.replace("(3rd", "“Sprocket"), quoted_tail], _p(rendered)) == []


def test_a_lower_case_line_continues_the_one_before_and_a_list_item_does_not():
    findings = _run([_KEPT, "visiting lecturer in comparative widget anatomy at Example Polytechnic"],
                    _p(_KEPT))
    assert [e for f in findings for e in f["evidence"]] == [
        f"{_KEPT} visiting lecturer in comparative widget anatomy at Example Polytechnic"[:100]]
    item = "c) visiting lecturer in comparative widget anatomy at Example Polytechnic"
    assert [e for f in _run([_KEPT, item], _p(_KEPT)) for e in f["evidence"]] == [item]


def test_a_continuation_keeps_the_entry_of_its_first_line():
    tail = "and sprocket repair, a second lost line of the same record"
    findings = _run([_LOST, tail], _p(_KEPT), stage3b=_3b((7, "D3", _LOST)))
    assert [f["message"].split(":")[0] for f in findings] == ["entry 7"]
