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
    ALIGN_GROUP_MAX,
    COVERAGE_EVIDENCE_MAX,
    lint_source_line_coverage,
)
from unified_pipeline.doctor.shared import TABLE_ROW_JOINER  # noqa: E402
from unified_pipeline.stage_4_5_research_summary import (
    GENERATION_METHOD_LLM,  # noqa: E402
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


def test_a_line_is_read_against_its_own_place_not_words_scattered_over_the_document():
    """Every word rendered, but in two places with another record between
    them: not covered. Two consecutive paragraphs are one place (stage 6
    splits a record's title from its description), and so is one line."""
    first, second = "Visiting comparative Polytechnic", "lecturer widget anatomy"  # 3 and 3 of 7 words
    assert len(_run([_LOST], _p(first, _KEPT, second))) == 1
    assert _run([_LOST], _p(first, second)) == []
    assert _run([_LOST], _p(f"{first} {second}")) == []


def test_under_half_the_tokens_warns_and_half_does_not():
    # 8 words of 3+ letters ("in" and "at" are not read), each in one
    # printed line, so of one weight: 4 rendered is half (silent), 3 is 38%.
    line = f"{_LOST} campus"
    half, under = "Visiting lecturer in comparative widget", "Visiting lecturer in comparative"
    assert _run([line], _p(half)) == []
    assert _run([line], _p(under))[0]["message"].endswith("(lowest 38%)")


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
    findings = _run([with_digit, _ROW], _p("2019 - Widget Anatomy, Course Director, Lecture"))
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
    tail = "Edition), Edited by Roe R, Chan ML, and Example OQ, 2014. Pages twelve to twenty, with an index."
    rendered = "1. Doe JA. Torsion. In: Roe R, Chan ML, Example OQ, eds. Handbook of Widget Anatomy and Sprocket Repair. 3rd ed. 2014."
    assert len(_run([tail], _p(rendered))) == 1  # judged alone, the tail warns
    assert _run([head, tail], _p(rendered)) == []
    quoted_tail = "Repair,” co-presented with Richard Roe and Mei-Lin Chan at the gizmo meeting"
    assert _run([head.replace("(3rd", "“Sprocket"), quoted_tail], _p(rendered)) == []


def test_a_lower_case_line_continues_the_one_before_and_a_list_item_does_not():
    tail = "visiting lecturer in comparative widget anatomy at Example Polytechnic during sabbatical"
    findings = _run([_KEPT, tail], _p(_KEPT))
    assert [e for f in findings for e in f["evidence"]] == [f"{_KEPT} {tail}"[:100]]
    item = "c) visiting lecturer in comparative widget anatomy at Example Polytechnic"
    assert [e for f in _run([_KEPT, item], _p(_KEPT)) for e in f["evidence"]] == [item]


def test_a_continuation_keeps_the_entry_of_its_first_line():
    tail = "and sprocket repair, a second lost line of the same record"
    findings = _run([_LOST, tail], _p(_KEPT), stage3b=_3b((7, "D3", _LOST)))
    assert [f["message"].split(":")[0] for f in findings] == ["entry 7"]


# The one-to-one alignment (#1588): facts, rarity weights, one claim per
# rendered token. Synthetic records only.

def _rows(*rows):
    """One rendered Word table, as `blocks` and `table_rows` both read it:
    each row's cells, and the block's lines (each cell, then the row joined)."""
    lines = []
    for row in rows:
        lines += [cell for cell in row if cell]
        if sum(1 for cell in row if cell) > 1:
            lines.append(" | ".join(cell for cell in row if cell))
    return [("table", "\n".join(lines))], [[list(row) for row in rows]]


def test_a_reworded_row_is_silent_months_present_and_unprinted_labels_included():
    """Stage 6 prints a month as a number, "current" as "Present", and no
    label of its own the CV wrote ("Editorial Board", "Board Certified")."""
    source = ["Editorial Board, Journal of Widget Anatomy and Sprocket Repair, spring 2010-current",
              "Board Certified, Example Board of Widget Surgery and Gizmo Repair, September 2002"]
    blocks, rows = _rows(("Journal of Widget Anatomy and Sprocket Repair", "2010-Present"),
                         ("Example Board of Widget Surgery and Gizmo Repair", "09/2002"))
    assert _run(source, blocks, table_rows=rows) == []
    terms = "Lecturer, spring and fall terms, September to December, widget course 2019"
    assert _run([terms], _p("2019 - Widget course, Lecturer")) == []
    current = "Member, Widget Society of Example Polytechnic board, 2010-current"  # 8 tokens: 4 is half
    assert _run([current], _p("Widget Society | 2010-Present")) == []


def test_a_rendered_line_missing_a_year_the_source_line_states_warns():
    source = "Visiting lecturer in comparative widget anatomy at Example Polytechnic, 2015 and 2019"
    assert len(_run([source], _p(f"{_LOST}, 2015"))) == 1
    assert _run([source], _p(f"{_LOST}, 2015, 2019")) == []


def test_a_rendered_range_holds_the_years_inside_it_for_one_line_only():
    """"1998, 2001 and 2004" printed as "1998-2004" is span_count's to
    report; a line read against several rows needs each year stated."""
    source = "Widget Society of Example Polytechnic annual gizmo prize, 1998, 2001 and 2004"
    assert _run([source], _p("Widget Society of Example Polytechnic annual gizmo prize, 1998-2004")) == []
    blocks, rows = _rows(("Name:", "Widget Society of Example Polytechnic annual gizmo prize"),
                         ("Dates:", "1998-2004"), ("Notes:", "Example"))
    assert len(_run([source], blocks, table_rows=rows)) == 1


def test_a_lost_record_is_found_although_a_near_identical_one_rendered():
    """One rendered row cannot vouch for two source records: the record
    whose own row is missing warns, on its own entry, even though every
    word it holds sits in the other's row (the stage-6 dedup class)."""
    kept = "Flywheel design medal (senior category), Example Guild of Gizmo Makers, 2005"
    lost = "Flywheel design medal (junior category), Example Guild of Gizmo Makers, 2005"
    blocks, rows = _rows(("Flywheel design medal (senior category)", "Example Guild of Gizmo Makers", "2005"))
    findings = _run([kept, lost], blocks, table_rows=rows, stage3b=_3b((10, "H", kept), (11, "H", lost)))
    assert [f["message"].split(":")[0] for f in findings] == ["entry 11"]


def test_a_record_listed_twice_in_the_cv_and_rendered_once_is_silent():
    """The same committee under two headings, once with its years: stage 6
    renders it once, and both source lines are that one record."""
    short = "Publications Committee member, Example Society of Widget Surgeons"
    full = "Example Society of Widget Surgeons Publications Committee member (2014-Current)"
    blocks, rows = _rows(("Publications Committee", "Member", "Example Society of Widget Surgeons", "2014-Present"))
    assert _run([short, full], blocks, table_rows=rows,
                stage3b=_3b((5, "Q2", short), (40, "Q2", full))) == []


def test_a_record_laid_out_as_label_value_rows_is_silent_and_its_lost_field_warns():
    """The years are common in this CV (two other records state them), so
    they weigh little: the record's table, not a run of its rows, holds
    its period."""
    source = "2001.09 - 2004.05\tJane Sample, MD, MSc candidate, Experimental Widget Surgery"
    others = ["Widget prize of the Example Society of Gizmo Makers, 2001 and 2004",
              "Sprocket medal of the Example Society of Cog Makers, 2001 and 2004"]
    rendered = _p(*(f"{line[:-14]}, 2001, 2004" for line in others))
    blocks, rows = _rows(("Name:", "Jane Sample"), ("Site/Position:", "MD, MSc candidate - Experimental Widget Surgery"),
                         ("Mentoring Period:", "2001-2004"))
    assert _run([source, *others], [*blocks, *rendered], table_rows=rows) == []
    empty, empty_rows = _rows(("Name:", "Jane Sample"), ("Site/Position:", "MD, MSc candidate - Experimental Widget Surgery"),
                              ("Mentoring Period:", ""))
    assert len(_run([source, *others], [*empty, *rendered], table_rows=empty_rows)) == 1


def test_a_list_fanned_out_one_item_per_row_is_silent():
    source = ("Reviewer for: Journal of Widget Anatomy, Sprocket Letters, Gizmo Quarterly, Annals of Torsion, "
              "Example Review of Cogs, Flywheel Studies, Spindle Reports")
    journals = ("Journal of Widget Anatomy", "Sprocket Letters", "Gizmo Quarterly", "Annals of Torsion",
                "Example Review of Cogs", "Flywheel Studies", "Spindle Reports")
    others = tuple(f"Unrelated Example Board number {word}" for word in
                   ("one", "two", "three", "four", "five", "six", "seven", "eight"))
    blocks, rows = _rows(("Journal / Organization Name", "Dates (yyyy-yyyy)"),
                         *((name,) for name in others + journals))
    assert _run([source], blocks, table_rows=rows) == []


def test_the_research_summary_the_model_wrote_vouches_for_research_activities_only():
    """Stage 4.5 wrote the summary in place of the CV's research activities
    (M1): it renders an M1 line, and two of them at once, but paraphrases
    any other record without rendering it."""
    summary = ("This research program studies comparative widget anatomy and sprocket repair, "
               "with visiting lectures at Example Polytechnic.")
    stage_4_5 = {"research_summary": {"generation_method": GENERATION_METHOD_LLM, "text": summary}}
    assert len(_run([_LOST], _p(_KEPT, summary), stage_4_5=stage_4_5)) == 1
    assert len(_run([_LOST], _p(_KEPT, summary), stage_4_5=stage_4_5, stage3b=_3b((7, "D3", _LOST)))) == 1
    assert _run([_LOST], _p(_KEPT, summary)) == []  # without stage 4.5 the paragraph is any other
    second = "Sprocket repair and comparative widget anatomy research at Example Polytechnic"
    activities = _3b((7, "M1", _LOST), (8, "M1", second))
    assert _run([_LOST, second], _p(_KEPT, summary), stage_4_5=stage_4_5, stage3b=activities) == []


def test_pages_day_dates_and_list_numbers_are_no_facts():
    """A citation's pages are no years, a month/day date names no year, and
    the source's list number is not the rendered one's."""
    citation = "Doe JA, Roe R. Widget torsion in common laboratory gerbils. J Widget Sci. 2010;33:2015-21."
    assert _run([citation], _p("7. Doe JA, Roe R. Widget torsion in common laboratory gerbils. J Widget Sci. 2010.")) == []
    numbered = "142. Widget torsion in gerbils at Example Polytechnic campus"  # 6 words: 3 is half
    assert _run([numbered], _p("7. Widget torsion gerbils")) == []
    talk = "Visiting lecture on comparative widget anatomy, Example Polytechnic, 3/30-4/2/1995"
    assert _run([talk], _p("1995 - Visiting lecture on comparative widget anatomy (Example Polytechnic)")) == []


def test_stage2_text_break_still_reads_the_word_pair_share():
    """stage2_text_break imports `_rendered_share`, `_Output` and
    `_printed_lines`: the alignment leaves them as they were."""
    from unified_pipeline.doctor.lints.coverage import (
        _heading_texts,
        _Output,
        _rendered_share,
    )
    output = _Output(_p("Visiting lecturer in comparative"))
    assert round(_rendered_share(_LOST, "", _heading_texts(None), output), 2) == 0.33


def test_headings_after_a_list_number_or_before_a_legend_and_lead_ins_are_not_judged():
    """The template prints its own headings: a stage-1a heading after a
    list number or before a bracketed legend, a heading's wrapped first
    line, a lead-in ending in a colon, and a capitals line outside every
    entry are not judged. A citation's "... In:", a legend with a year and
    a capitals line inside an entry are."""
    stage1a = {"hierarchy": [{"text": "LECTURES ON WIDGETS AND OTHER GIZMO TOPICS", "children": []},
                             {"text": "MAJOR WIDGET COMMITTEES AND OTHER GIZMO DUTIES", "children": []},
                             {"text": "Editor of widget quarterlies, service on gizmo review panels and boards",
                              "children": []}]}
    not_judged = ["Lectures on Widgets and Other Gizmo Topics (speaker marked with a dagger)",
                  "7.  MAJOR WIDGET COMMITTEES AND OTHER GIZMO DUTIES",
                  "Editor of widget quarterlies, service on gizmo review",
                  "Referee for these widget periodicals and gizmo meetings:",
                  "WIDGET STEWARDSHIP AND GIZMO OUTREACH ACTIVITIES OVERSEAS"]
    entries = _3b((3, "Q4D", " ".join(not_judged[2:4])))
    assert _run(not_judged, _p(_KEPT), stage1a=stage1a, stage3b=entries) == []
    judged = ["Doe JA. Widget torsion in common laboratory gerbils of the north. In:",
              "Lectures on Widgets and Other Gizmo Topics (since 2019, speaker marked)",
              "WIDGET STEWARDSHIP AND GIZMO OUTREACH ACTIVITIES OVERSEAS"]
    findings = _run(judged, _p(_KEPT), stage1a=stage1a, stage3b=_3b((9, "Q2", judged[2])))
    assert sorted(e for f in findings for e in f["evidence"]) == sorted(judged)


def test_a_listing_whose_year_is_inside_another_listings_range_is_that_record():
    """"2001 Occasional referee" and "2001 - 2015 Occasional Referee" of
    one journal are one record, rendered once. Two periods are two posts:
    the "2021-" director whose row is missing warns though the 2010-2021
    co-director row holds every word it has."""
    single = "2001 Occasional referee, Journal of Comparative Widget Studies"
    ranged = "2001 - 2015 Occasional Referee, Journal of Comparative Widget Studies."
    blocks, rows = _rows(("Journal of Comparative Widget Studies", "2001-2015"))
    assert _run([ranged, single], blocks, table_rows=rows,
                stage3b=_3b((5, "Q4D", ranged), (40, "Q4D", single))) == []
    director = "2021-\tDirector, Example Gizmo Repair and Sprocket Clinic"
    co_director = "2010-2021\tco-Director, Example Gizmo Repair and Sprocket Clinic"
    blocks, rows = _rows(("co-Director, Example Gizmo Repair and Sprocket Clinic", "2010-2021"))
    findings = _run([co_director, director], blocks, table_rows=rows,
                    stage3b=_3b((35, "O", co_director), (37, "O", director)))
    assert [f["message"].split(":")[0] for f in findings] == ["entry 37"]


def test_a_record_split_over_two_lines_and_listed_again_is_one_record_on_every_line():
    """The record's title wraps onto a second printed line that opens a
    sentence, and the CV lists the record a second time: stage 6 renders
    it once, and the year it states once vouches for both listings."""
    head = "Design of an open sprocket maintenance and flywheel repair course for regional gizmo technician"
    tail = "Apprentices. Online workshop for the Example Cog Guild, May 2011."
    again = f"2011 {head}"
    # Another workshop for the guild makes the tail's words common, so the
    # second listing, the heavier line, claims the row's year first.
    other = "Online workshop for the Example Cog Guild on spindle safety, 2014"
    blocks, rows = _rows((f"{head} apprentices", "Online workshop for the Example Cog Guild", "2011"),
                         (other[:-6], "2014"))
    assert _run([head, tail, again, other], blocks, table_rows=rows,
                stage3b=_3b((10, "R", f"{head} {tail}"), (20, "R", again), (30, "R", other))) == []


def test_a_citations_pages_the_source_states_as_years_are_held_by_the_rendered_pages():
    """Without a volume's colon before them, a citation's pages read as
    years ("8: 2, 1984-1990, 2003"); stage 6 prints them as pages."""
    source = "Doe JA, Roe R. Widget torsion in common laboratory gerbils. J. Widget Sci., 8: 2, 1984-1990, 2003."
    rendered = "4. Doe JA, Roe R. Widget torsion in common laboratory gerbils. J Widget Sci. 2003;8(2):1984-90."
    assert _run([source], _p(rendered)) == []
    assert len(_run([source.replace("2003", "2004")], _p(rendered))) == 1


def test_a_line_goes_to_its_own_records_rows_not_to_fewer_rows_of_a_neighbours():
    """Two mentees of one site, each laid out as label/value rows; the
    first mentee's rows outnumber its neighbour's, whose table holds most
    of its words. Ranked by fewest rows, the first would take the
    neighbour's table, spend its words and period, and the neighbour, whose
    own table renders it, would be reported: among spans of several units
    the share decides first."""
    site = "Example Polytechnic widget and sprocket research laboratory, Department of Gizmo Engineering"
    first = f"2011-2015\tJane Sample, doctoral student, {site}, thesis on cog torsion"
    second = (f"2011-2015\tRob Roe, doctoral student, {site}, thesis on spindle wear, field trials of flywheel "
              "brakes on tramways, harbour cranes, ferries")
    own, own_rows = _rows(("Name:", "Jane Sample"), ("Position:", "Doctoral student"),
                          ("Site:", "Example Polytechnic widget and sprocket research laboratory"),
                          ("Department:", "Department of Gizmo Engineering"), ("Mentoring Period:", "2011-2015"),
                          ("Project/Accomplishments:", "Thesis on cog torsion"), ("Current Position:", ""))
    neighbour, neighbour_rows = _rows(("Name:", "Rob Roe"), ("Site/Position:", f"Doctoral student - {site}"),
                                      ("Mentoring Period:", "2011-2015"),
                                      ("Project/Accomplishments:", "Thesis on spindle wear"))
    others = [f"Unrelated board number {word} of the regional gizmo makers guild" for word in
              ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")]
    assert _run([first, second, *others], [*own, *neighbour, *_p(*others)], table_rows=own_rows + neighbour_rows,
                stage3b=_3b((10, "N3B", first), (11, "N3B", second))) == []


def _talk_rows(years):
    """A talks table from 2003 to 2016 or past its last year, one talk a
    year: the grand rounds in `years`, an unrelated lecture in every other
    year."""
    rows = []
    for year in range(2003, max(2016, *years) + 2):
        rows.append((str(year), "Grand rounds on widget torsion", "Example Hospital") if year in years
                    else (str(year), f"Lecture on sprocket fatigue, part {year - 2002}", "Gadget College"))
    return _rows(*rows)


def test_a_line_listing_several_periods_is_found_in_one_row_per_period():
    """Stage 6 renders a line that lists one talk's three years as three
    rows of the talks table, far apart in its year order: each row holds
    one of the years, and the line is found in the three together. A year
    whose row is missing still warns, and so does a line of more periods
    than ALIGN_GROUP_MAX, which is not read this way."""
    talk = "Grand rounds on widget torsion, Example Hospital, 2004, 2009 and 2015"
    blocks, rows = _talk_rows({2004, 2009, 2015})
    assert _run([talk], blocks, table_rows=rows) == []
    blocks, rows = _talk_rows({2004, 2009})
    assert len(_run([talk], blocks, table_rows=rows)) == 1
    years = range(2004, 2004 + 3 * (ALIGN_GROUP_MAX + 1), 3)  # three rows apart: no run of rows
    many = f"Grand rounds on widget torsion, Example Hospital, {', '.join(map(str, years))}"
    blocks, rows = _talk_rows(set(years))
    assert len(_run([many], blocks, table_rows=rows)) == 1


def test_a_period_is_not_found_in_a_row_holding_only_its_year_and_a_common_word():
    """The 2009 row of another record holds the year and "lecture", a word
    eight other lines of the CV hold: no word of the line's own record, so
    it vouches for nothing and the 2009 talk warns."""
    talk = "Grand rounds lecture on widget torsion, Example Hospital, 2004 and 2009"
    others = [f"Lecture on sprocket fatigue, part {word}, Gadget College" for word in
              ("one", "two", "three", "four", "five", "six", "seven", "eight")]
    grand_rounds, grand_rounds_rows = _rows(("2004", "Grand rounds lecture on widget torsion", "Example Hospital"))
    lecture, lecture_rows = _rows(("2009", "Lecture"))
    findings = _run([talk, "2009 Lecture", *others], [*grand_rounds, *lecture, *_p(*others)],
                    table_rows=grand_rounds_rows + lecture_rows)
    assert [f["evidence"] for f in findings] == [[talk]]


def test_a_period_is_not_found_in_another_records_row_that_shares_a_year_and_words():
    """The vice-presidency's period is printed only in another guild's
    membership-committee row, whose own source line is a name list and so
    claims nothing: that row is not mostly this line's (ALIGN_GROUP_MIN_OWN),
    and the line warns."""
    society = ("Chair of the Widget Society regional chapter, 2001; vice-president and member of the "
               "membership committee of the Widget Society, 2003-2004")
    guild = "Member, Membership Committee, Sprocket Guild, 2003-2004."
    others = [f"Widget Society annual meeting number {word}, the regional gizmo session" for word in
              ("one", "two", "three", "four", "five", "six")]
    chair, chair_rows = _rows(("Chair, Widget Society regional chapter", "2001"))
    member, member_rows = _rows(("Membership Committee", "Member", "Sprocket Guild", "2003-2004"))
    findings = _run([society, guild, *others], [*chair, *member, *_p(*others)], table_rows=chair_rows + member_rows)
    assert [f["evidence"] for f in findings] == [[society[:100]]]


def test_a_period_is_one_record_group_and_its_two_ends_in_two_rows_are_not_it():
    """"2001-2005" is one record group: a row must state the period. The
    treasurer's two ends printed as two dated rows, years apart in a long
    table, are not that period, and the line warns."""
    line = "Treasurer of the Widget Society, 2001-2005; secretary of the Widget Society, 2009"
    rows = []
    for year in range(2000, 2016):
        role = {2001: "Treasurer", 2005: "Treasurer", 2009: "Secretary"}.get(year)
        rows.append((str(year), role, "Widget Society") if role
                    else (str(year), f"Lecture on sprocket fatigue, part {year - 1999}", "Gadget College"))
    blocks, table_rows = _rows(*rows)
    assert len(_run([line], blocks, table_rows=table_rows)) == 1


def test_a_period_is_found_in_a_row_whose_year_no_other_record_spent():
    """The 2009 talk shares a merged row with a visiting lecture, whose
    line spends that row's year; a terser 2009 row is still free, and the
    talk is found there, not reported for the year the other record took."""
    talk = "Grand rounds on widget torsion and sprocket repair, Example Hospital, 2004 and 2009"
    visit = "Visiting lecture on cog design, Example Hospital, 2009"
    first, first_rows = _rows(("2004", "Grand rounds on widget torsion and sprocket repair", "Example Hospital"))
    merged, merged_rows = _rows(("2009", "Grand rounds on widget torsion and sprocket repair; visiting lecture on cog design",
                                 "Example Hospital"))
    terse, terse_rows = _rows(("2009", "Widget torsion"))
    assert _run([talk, visit], [*first, *merged, *terse], table_rows=first_rows + merged_rows + terse_rows,
                stage3b=_3b((5, "R", talk), (6, "R", visit))) == []


# Sub-points (#1205): stage 6 offers an entry's unrendered prose as a
# tracked-deleted sub-point under it, and the render sidecar records whose
# each one is. A sub-point vouches for its own entry's lines only (BMAMWE
# 1368, #1205 comment 6388379186: another entry's sub-point sharing a note's
# facts and rare words silenced it). Invented text only.

#: Entry 20's sub-point, and entry 30's note, which shares most of its words.
_SUBPOINT = "Featured on the cover of the spring gizmo issue, with an editorial on sprocket safety"
_NOTE = "Cover feature of the spring gizmo issue, with an editorial on sprocket safety"


def _subpoints(*placed, lines=(), placements=True):
    """The docx's sub-points as run_doctor reads them (`subpoint_text`):
    each (element_idx_start, paragraph) a sub-point the render sidecar
    places under that entry, plus `lines` it places under none; without
    `placements`, a sidecar from before the record existed."""
    from unified_pipeline.doctor.shared import subpoint_text
    from unified_pipeline.stage6.supplementary import SubpointPlacement

    records = tuple(SubpointPlacement(idx, "S1", (text,)) for idx, text in placed)
    return subpoint_text([*(text for _, text in placed), *lines], records if placements else None)


#: Entry 20's citation, as the CV and stage 3b have it.
_CITATION_20 = "Doe JA, Roe R. Widget torsion in gerbils. J Widget Sci. 2019;3:1-5."


def _entries_of(*entries):
    """Stage-3b entries, after entry 20: its citation, then its sub-point prose."""
    return _3b(*entries, (20, "S1", f"{_CITATION_20}\t{_SUBPOINT}"))


def test_another_entrys_sub_point_does_not_vouch_for_a_line():
    """The BMAMWE shape: entry 30's note is in neither document, and entry
    20's sub-point holds most of its words. Without the sidecar's record of
    whose each sub-point is, every sub-point counts for every line, as
    `offered_as_subpoint` reads them; with no sub-points read at all, the
    deletion is entry 20's text, as any other deletion is (next tests)."""
    views = {"deleted_blocks": _p(_SUBPOINT), "stage3b": _entries_of((30, "S7", _NOTE))}
    findings = _run([_NOTE], _p(_KEPT), subpoints=_subpoints((20, _SUBPOINT)), **views)
    assert [(f["message"].split(":")[0], f["evidence"]) for f in findings] == [("entry 30", [_NOTE])]
    assert _run([_NOTE], _p(_KEPT), subpoints=_subpoints((20, _SUBPOINT), placements=False), **views) == []
    assert [f["message"].split(":")[0] for f in _run([_NOTE], _p(_KEPT), **views)] == ["entry 30"]


def test_a_lines_own_sub_point_still_vouches_for_it():
    """Entry 30's own sub-point holds its note; so does any sub-point for a
    line no stage-3b entry holds, whose entry is unknown."""
    views = {"deleted_blocks": _p(_SUBPOINT)}
    assert _run([_NOTE], _p(_KEPT), subpoints=_subpoints((30, _SUBPOINT)),
                stage3b=_entries_of((30, "S7", _NOTE)), **views) == []
    assert _run([_NOTE], _p(_KEPT), subpoints=_subpoints((20, _SUBPOINT)), stage3b=_entries_of(), **views) == []


def test_a_line_stage_3b_put_in_two_entries_reads_either_ones_sub_point():
    """Stage 3b's entries overlap: entry 20's text holds entry 30's note too,
    and its sub-point holds it word for word (OIYKZE 242, XWNZWW 730 on the
    farm). The note is each entry's line, so either one's sub-point is its own."""
    stage3b = _3b((30, "S7", _NOTE), (20, "S1", f"{_CITATION_20}\t{_NOTE}"))
    assert _run([_NOTE], _p(_KEPT), deleted_blocks=_p(_NOTE), stage3b=stage3b,
                subpoints=_subpoints((20, _NOTE))) == []
    findings = _run([_NOTE], _p(_KEPT), deleted_blocks=_p(_NOTE), stage3b=_3b((30, "S7", _NOTE)),
                    subpoints=_subpoints((20, _NOTE)))
    assert [f["message"].split(":")[0] for f in findings] == ["entry 30"]


def test_a_deletion_that_is_no_sub_point_vouches_for_the_entries_whose_text_holds_it():
    """The enrichment's w:del is the CV's own line of the citation it
    replaced; it was BMAMWE 1368's actual cover, another citation's, once a
    sub-point took the claims that had held it. It vouches for the stage-3b
    entries whose text holds it, the same record listed twice included, and
    not for another entry's line. A deletion no entry's text holds, such as
    a formatter's fragment, vouches for every line, as before."""
    struck = f"{_CITATION_20} {_SUBPOINT}"
    views = {"deleted_blocks": _p(struck), "subpoints": _subpoints()}

    def found(*entries):
        return [f["message"].split(":")[0] for f in _run([_NOTE], _p(_KEPT), stage3b=_3b(*entries), **views)]

    assert found((20, "S1", struck), (30, "S7", _NOTE)) == ["entry 30"]
    assert found((20, "S1", struck), (30, "S7", f"{_NOTE}\t{struck}")) == []
    assert found((20, "S1", _CITATION_20), (30, "S7", _NOTE)) == []
    assert found((None, "S1", struck), (30, "S7", _NOTE)) == []  # no index: no one's own
    # A table line of nothing but cell joiners is no sub-point's, and no crash;
    # nor is one whose cells hold a sub-point's words and more.
    views["deleted_blocks"] = [("table", f" {TABLE_ROW_JOINER} "), *_p(struck)]
    assert found((20, "S1", struck), (30, "S7", _NOTE)) == ["entry 30"]
    more = f"{_SUBPOINT}, and more"
    views = {"deleted_blocks": [("table", f"{more}{TABLE_ROW_JOINER}{more}")], "subpoints": _subpoints((20, _SUBPOINT))}
    assert found((20, "S1", _SUBPOINT), (30, "S7", _NOTE)) == []


def test_a_sub_point_the_sidecar_places_under_no_entry_vouches_for_no_entrys_line():
    """The sidecar records the render's placements, and this sub-point is in
    none of them (or the sidecar records none): it is no entry's own, so it
    counts only for a line no entry holds."""
    for subpoints in (_subpoints(lines=(_SUBPOINT,)), _subpoints((20, _KEPT), lines=(_SUBPOINT,))):
        views = {"deleted_blocks": _p(_SUBPOINT), "subpoints": subpoints}
        findings = _run([_NOTE], _p(_KEPT), stage3b=_entries_of((30, "S7", _NOTE)), **views)
        assert [f["message"].split(":")[0] for f in findings] == ["entry 30"]
        assert _run([_NOTE], _p(_KEPT), stage3b=_entries_of(), **views) == []


def _sub_point_row(duties, *placements, elsewhere=()):
    """A rendered one-row table and the sub-point row stage 6's writer puts
    under it, spanning both columns; the doctor's three views of it.
    `elsewhere` is (element_idx_start, paragraph) sub-points of the same
    document outside the table."""
    from docx import Document

    from unified_pipeline.doctor.shared import (
        docx_body_blocks,
        docx_subpoint_lines,
        subpoint_text,
    )
    from unified_pipeline.stage6.supplementary import (
        Revision,
        SubpointPlacement,
        write_row_subpoint,
    )

    doc = Document()
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text, table.rows[0].cells[1].text = "Lecturer", "Example College"
    write_row_subpoint(table.rows[0]._tr, duties, Revision(1, "2026-01-01T00:00:00Z"))
    lines = [*docx_subpoint_lines(doc), *(text for _, text in elsewhere)]
    placed = (*placements, *(SubpointPlacement(idx, "K1", (text,)) for idx, text in elsewhere))
    return docx_body_blocks(doc), {"deleted_blocks": docx_body_blocks(doc, deleted=True),
                                   "subpoints": subpoint_text(lines, placed)}


def test_a_sub_point_row_vouches_only_for_its_own_entry_in_every_line_the_table_view_has():
    """A sub-point row spans the table: the deleted view repeats its cell
    per column and joins the copies (TABLE_ROW_JOINER) on one line, the only
    line that holds both of its paragraphs. Every one of them is entry 20's;
    the joined line is the entries' whose own every paragraph is."""
    from unified_pipeline.stage6.supplementary import SubpointPlacement

    duties = ("Supervised the regional sprocket audit", "Trained gizmo apprentices in flywheel repair")
    line = "Supervised the regional sprocket audit and trained gizmo apprentices in flywheel repair, Example Guild"
    blocks, views = _sub_point_row(duties, SubpointPlacement(20, "D1", duties))
    assert " | ".join([" ".join(duties)] * 2) in views["deleted_blocks"][-1][1].split("\n")
    findings = _run([line], blocks, stage3b=_entries_of((30, "D1", line)), **views)
    assert [f["message"].split(":")[0] for f in findings] == ["entry 30"]
    assert _run([line], blocks, stage3b=_3b((20, "D1", line)), **views) == []
    # Another entry's sub-point elsewhere in the document is no part of the row.
    blocks, views = _sub_point_row(duties, SubpointPlacement(20, "D1", duties), elsewhere=((40, _SUBPOINT),))
    assert _run([line], blocks, stage3b=_3b((20, "D1", line)), **views) == []
    # Entry 30 also placed the first paragraph, alone, under half of its line.
    blocks, views = _sub_point_row(duties, SubpointPlacement(20, "D1", duties),
                                   SubpointPlacement(30, "D1", duties[:1]))
    findings = _run([line], blocks, stage3b=_entries_of((30, "D1", line)), **views)
    assert [f["message"].split(":")[0] for f in findings] == ["entry 30"]


def test_a_sub_point_row_whose_second_paragraph_holds_its_first_is_still_one_entrys():
    """The joined line's cell is read longest paragraph first: taking the
    shorter out first would also cut it out of the longer one, and leave the
    row read as no sub-point's, every line's."""
    from unified_pipeline.stage6.supplementary import SubpointPlacement

    duties = ("Trained gizmo apprentices", "Trained gizmo apprentices in flywheel repair at the sprocket audit")
    line = "Trained gizmo apprentices in flywheel repair at the regional sprocket audit, Example Guild"
    blocks, views = _sub_point_row(duties, SubpointPlacement(20, "D1", duties))
    findings = _run([line], blocks, stage3b=_entries_of((30, "D1", line)), **views)
    assert [f["message"].split(":")[0] for f in findings] == ["entry 30"]


def test_the_research_summary_is_no_record_of_a_lines_period():
    """The research summary stage 4.5 wrote vouches for no line but a
    research-activities one (`_Judged.narrative`), nor for one period of a
    line listing several (`_group_covers`)."""
    talk = "Grand rounds on widget torsion, Example Hospital, 2004 and 2009"
    summary = "Grand rounds on widget torsion, Example Hospital, 2009"
    stage_4_5 = {"research_summary": {"generation_method": GENERATION_METHOD_LLM, "text": summary}}
    blocks, rows = _talk_rows({2004})
    findings = _run([talk], [*blocks, *_p(summary)], table_rows=rows, stage_4_5=stage_4_5)
    assert len(findings) == 1
    assert _run([talk], [*blocks, *_p(summary)], table_rows=rows) == []  # an ordinary paragraph is a record


def test_an_entry_index_written_as_a_float_is_the_entrys_index():
    """Older runs wrote element_idx_start as a JSON float."""
    findings = _run([_LOST], _p(_KEPT), stage3b=_3b((7.0, "D3", _LOST)))
    assert [f["message"].split(":")[0] for f in findings] == ["entry 7"]


def test_a_period_in_another_entrys_sub_point_is_not_one_of_a_lines_records():
    """A line listing two periods is found in one unit per period
    (`_group_covers`): the 2004 row is entry 30's, the 2009 talk only entry
    20's sub-point, which is not one of them. Its own sub-point is."""
    talk = "Grand rounds on widget torsion, Example Hospital, 2004 and 2009"
    blocks, rows = _talk_rows({2004})
    views = {"deleted_blocks": _p("Grand rounds on widget torsion, Example Hospital, 2009"), "table_rows": rows,
             "stage3b": _entries_of((30, "R", talk))}
    for owner, found in ((20, ["entry 30"]), (30, [])):
        subpoints = _subpoints((owner, views["deleted_blocks"][0][1]))
        assert [f["message"].split(":")[0] for f in _run([talk], blocks, subpoints=subpoints, **views)] == found


#: Entry 20's sub-point names four of the authors entry 30's citation lists.
_AUTHORS_SUBPOINT = "Presented with Jane Doe, Richard Roe, Mei-Lin Chan and Omar Example at the gizmo meeting"


def test_an_author_list_another_entrys_sub_point_holds_is_not_rendered():
    """A citation's leading author list is left out of its pairs when one
    rendered line holds it (`_rendered_authors_end`): another entry's
    sub-point is not that line. A short title past the list then no longer
    leaves the citation unjudged, and a rendered title alone does not cover
    a list nothing renders."""
    short = "Jane A. Doe, Richard Roe, Mei-Lin Chan, Omar Q. Example. Sprocket wear notes."
    titled = "Jane A. Doe, Richard Roe, Mei-Lin Chan, Omar Q. Example. Widget torsion in common laboratory gerbils of the north."
    blocks = _p(_KEPT, "Widget torsion in common laboratory gerbils of the north")
    for line in (short, titled):
        views = {"deleted_blocks": _p(_AUTHORS_SUBPOINT), "stage3b": _entries_of((30, "S2", line))}
        findings = _run([line], blocks, subpoints=_subpoints((20, _AUTHORS_SUBPOINT)), **views)
        assert [f["message"].split(":")[0] for f in findings] == ["entry 30"], line
        assert _run([line], blocks, subpoints=_subpoints((30, _AUTHORS_SUBPOINT)), **views) == []


def test_stage2_text_break_reads_every_line_and_a_sub_point_only_for_its_own_entry():
    """stage2_text_break builds `_Output` from the accepted view alone, so
    every reader reads all of it, as before; a sub-point line among the
    deleted lines is read by its own entry and by a line no entry holds."""
    from unified_pipeline.doctor.lints.coverage import (
        _DeletedLine,
        _heading_texts,
        _Output,
        _rendered_share,
    )

    headings = _heading_texts(None)
    accepted = _Output(_p("Visiting lecturer in comparative"))
    assert [round(_rendered_share(_LOST, "", headings, accepted.read_by(frozenset(entries))), 2)
            for entries in ((), {7})] == [0.33, 0.33]
    deleted = _Output(_p(_KEPT), [_DeletedLine(0, _LOST, frozenset({7}))])
    views = [deleted.read_by(frozenset(entries)) for entries in ((), {7}, {8}, {7, 8})]
    assert [_rendered_share(_LOST, "", headings, view) for view in views] == [1.0, 1.0, 0.0, 1.0]
    shared = _Output(_p(_KEPT), [_DeletedLine(0, _LOST, None)])
    assert _rendered_share(_LOST, "", headings, shared.read_by(frozenset({8}))) == 1.0
