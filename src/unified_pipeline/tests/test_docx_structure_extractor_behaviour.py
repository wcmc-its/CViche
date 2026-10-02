"""Behaviour tests for the docx_structure_extractor reader (issue #704 round 2).

Covers the full reader surface *not* already exercised by
test_docx_structure_extractor_tracked_changes.py: get_paragraph_text (plain
multi-run join), get_cell_text (multi-paragraph join), extract_paragraph_metadata
(style/outline, bold-representative, italic/underline/size, alignment, indent,
list numbering), _is_date_column, split_merged_cells_in_row (no-split /
double-newline / aligned-line / date-column-padding branches),
extract_table_metadata (gridSpan/merged-cell repeat behaviour), get_table_first_cell_text,
looks_like_section_header (confidence tiers), flatten_table_to_text (skip_first_row),
extract_unified_elements (document-order interleaving, unified_idx contiguity,
table_header/table_content/table element types), extract_docx_structure
(top-level shape), normalize_style_name, and create_simplified_layout_json
(skip_empty on/off).

Pure python-docx, no network: this module never calls an LLM, so there is
nothing to stub. All fixture .docx files are built in memory with
Document()/add_paragraph/add_table and saved to tmp_path only where a real
path is required (extract_unified_elements / extract_docx_structure take a
path, not a Document).

Untestable: the
`if __name__ == '__main__'` guard -- no network/corpus fixture is available
and covering it would only re-test print()/json.dump plumbing already
exercised indirectly through extract_docx_structure/create_simplified_layout_json.

    python3 -m pytest src/unified_pipeline/tests/test_docx_structure_extractor_behaviour.py -p no:cacheprovider
"""

import json
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from docx import Document  # noqa: E402
from docx.enum.text import WD_ALIGN_PARAGRAPH  # noqa: E402
from docx.oxml import parse_xml  # noqa: E402
from docx.oxml.ns import nsdecls  # noqa: E402
from docx.shared import Inches, Pt  # noqa: E402

from unified_pipeline.core.docx_structure_extractor import (  # noqa: E402
    _is_date_column,
    _fold_orphan_date_tail,
    _is_date_only_text,
    create_simplified_layout_json,
    extract_docx_structure,
    extract_paragraph_metadata,
    extract_table_metadata,
    extract_unified_elements,
    flatten_table_to_text,
    get_cell_text,
    get_paragraph_text,
    get_table_first_cell_text,
    looks_like_section_header,
    normalize_style_name,
    split_merged_cells_in_row,
)


# --------------------------------------------------------------------------
# get_paragraph_text / get_cell_text
# --------------------------------------------------------------------------


def test_get_paragraph_text_joins_plain_multi_run():
    doc = Document()
    para = doc.add_paragraph()
    para.add_run("Hello ")
    para.add_run("World")
    para.add_run("!")

    assert get_paragraph_text(para) == "Hello World!"


def test_get_paragraph_text_tab_and_break_together():
    # Covers the w:tab and w:br/w:cr branches in the same walk as a plain
    # run, distinct from the sibling file's tab_char-parameterized test.
    doc = Document()
    para = doc.add_paragraph()
    para.add_run("a")
    para.add_run().add_tab()
    run = para.add_run("b")
    run.add_break()  # <w:br/>
    para.add_run("c")

    assert get_paragraph_text(para) == "a b\nc"


def test_get_cell_text_joins_multiple_paragraphs():
    doc = Document()
    table = doc.add_table(rows=1, cols=1)
    cell = table.rows[0].cells[0]
    cell.paragraphs[0].add_run("Line one")
    cell.add_paragraph("Line two")

    assert get_cell_text(cell) == "Line one\nLine two"


# --------------------------------------------------------------------------
# extract_paragraph_metadata
# --------------------------------------------------------------------------


def test_extract_paragraph_metadata_heading_style_and_outline_level():
    doc = Document()
    para = doc.add_paragraph("Section Heading", style="Heading 2")

    meta = extract_paragraph_metadata(para, 0)

    assert meta["style"] == "Heading 2"
    assert meta["outline_level"] == 2
    assert meta["text"] == "Section Heading"


def test_extract_paragraph_metadata_bold_all_run():
    doc = Document()
    para = doc.add_paragraph()
    run = para.add_run("Bold text")
    run.bold = True

    meta = extract_paragraph_metadata(para, 0)

    assert meta["bold"] is True


def test_extract_paragraph_metadata_bold_partial_uses_first_run_only():
    # extract_paragraph_metadata's own docstring says font properties come
    # "from first run (representative)" -- a later bold run does not flip
    # the paragraph-level flag. Documented behaviour, not a bug.
    doc = Document()
    para = doc.add_paragraph()
    first = para.add_run("not bold ")
    first.bold = False
    second = para.add_run("bold")
    second.bold = True

    meta = extract_paragraph_metadata(para, 0)

    assert meta["bold"] is False


def test_extract_paragraph_metadata_italic_underline_size():
    doc = Document()
    para = doc.add_paragraph()
    run = para.add_run("styled")
    run.italic = True
    run.underline = True
    run.font.size = Pt(14)

    meta = extract_paragraph_metadata(para, 0)

    assert meta["italic"] is True
    assert meta["underline"] is True
    assert meta["font_size"] == 14.0


def test_extract_paragraph_metadata_alignment():
    doc = Document()
    para = doc.add_paragraph("Centered")
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER

    meta = extract_paragraph_metadata(para, 0)

    assert meta["alignment"] == "center"


def test_extract_paragraph_metadata_unmapped_alignment_falls_back_to_left():
    doc = Document()
    para = doc.add_paragraph("Distributed")
    para.alignment = WD_ALIGN_PARAGRAPH.DISTRIBUTE  # not in the 4-entry map

    meta = extract_paragraph_metadata(para, 0)

    assert meta["alignment"] == "left"


def test_extract_paragraph_metadata_indent():
    doc = Document()
    para = doc.add_paragraph("Indented")
    para.paragraph_format.left_indent = Inches(0.5)
    para.paragraph_format.first_line_indent = Inches(0.25)

    meta = extract_paragraph_metadata(para, 0)

    assert meta["indent_left"] == 0.5
    assert meta["indent_first"] == 0.25


def test_extract_paragraph_metadata_list_numbering():
    doc = Document()
    para = doc.add_paragraph("List item")
    pPr = para._p.get_or_add_pPr()
    num_pr = parse_xml(
        f'<w:numPr {nsdecls("w")}><w:ilvl w:val="1"/><w:numId w:val="5"/></w:numPr>'
    )
    pPr.append(num_pr)

    meta = extract_paragraph_metadata(para, 0)

    assert meta["list_level"] == 1
    assert meta["num_fmt"] == "numbered"


# --------------------------------------------------------------------------
# _is_date_column
# --------------------------------------------------------------------------


def test_is_date_column_true_at_half_threshold():
    # 2 of 4 lines contain a year == exactly the 0.5 threshold -> True.
    assert _is_date_column(["1999", "2000", "abc", "def"]) is True


def test_is_date_column_false_below_threshold():
    # 1 of 4 lines contains a year, below the 0.5 threshold -> False.
    assert _is_date_column(["1999", "abc", "def", "ghi"]) is False


def test_is_date_column_empty_list():
    assert _is_date_column([]) is False


# --------------------------------------------------------------------------
# split_merged_cells_in_row
# --------------------------------------------------------------------------


def test_split_merged_cells_no_split_needed():
    row = [{"text": "short", "row": 0, "col": 0}]

    assert split_merged_cells_in_row(row) == [row]


def test_split_merged_cells_empty_row_returns_as_is():
    assert split_merged_cells_in_row([]) == [[]]


def test_split_merged_cells_double_newline_substantial():
    long_a = "A" * 60
    long_b = "B" * 60
    row = [{"text": f"{long_a}\n\n{long_b}", "row": 0, "col": 0}]

    out = split_merged_cells_in_row(row, min_chars=50, min_newlines=2)

    assert [r[0]["text"] for r in out] == [long_a, long_b]


def test_split_merged_cells_at_min_chars_boundary_exact_no_split():
    # A segment of exactly min_chars length fails the strict "len(s) >
    # min_chars" has_substantial check, and the second segment is only 1
    # char so has_multiple_items (which requires len(s) >= 2 for every
    # segment) also fails -- neither \n\n branch fires, and the row is too
    # short (2 lines, below the >= 3 aligned-line threshold) to trigger the
    # second pass either, so the row comes back completely unchanged.
    exact = "A" * 50
    row = [{"text": f"{exact}\n\nB", "row": 0, "col": 0}]

    out = split_merged_cells_in_row(row, min_chars=50, min_newlines=2)

    assert out == [row]


def test_split_merged_cells_double_newline_short_multi_item_preserves_row_col():
    row = [{"text": "MBA\n\nBS", "row": 2, "col": 0}]

    out = split_merged_cells_in_row(row)

    assert out == [
        [{"text": "MBA", "row": 2, "col": 0}],
        [{"text": "BS", "row": 2, "col": 0}],
    ]


def test_split_merged_cells_double_newline_below_threshold_no_split():
    # Only one non-empty segment after the \n\n split, and it is short --
    # neither has_substantial nor has_multiple_items fires, so the row is
    # returned unchanged (including the trailing \n\n).
    row = [{"text": "A\n\n", "row": 0, "col": 0}]

    assert split_merged_cells_in_row(row) == [row]


def test_split_merged_cells_aligned_lines_with_date_column_padding():
    # Two cells share a 3-line count (triggers aligned splitting); the third
    # cell has only 2 date-shaped lines and gets padded to match.
    row = [
        {"text": "Role1\nRole2\nRole3", "row": 0, "col": 0},
        {"text": "Site1\nSite2\nSite3", "row": 0, "col": 1},
        {"text": "1998\n2005", "row": 0, "col": 2},
    ]

    out = split_merged_cells_in_row(row, min_chars=50, min_newlines=2)

    assert [c["text"] for c in out[0]] == ["Role1", "Site1", "1998"]
    assert [c["text"] for c in out[1]] == ["Role2", "Site2", "2005"]
    assert [c["text"] for c in out[2]] == ["Role3", "Site3", ""]
    # The padded date column must go through the "splits is not None"
    # branch, which preserves row/col metadata -- not the separate
    # date-distribution fallback for cells with no recorded split (which
    # only ever emits a bare {"text": ...}). Assert the full dict so a
    # mutant that disables the second-pass padding (falling through to that
    # bare-text fallback) is caught even though the text values still match.
    assert out[0][2] == {"text": "1998", "row": 0, "col": 2}
    assert out[1][2] == {"text": "2005", "row": 0, "col": 2}
    assert out[2][2] == {"text": "", "row": 0, "col": 2}


def test_split_merged_cells_newline_count_alone_triggers_split():
    # Segment "a\nb\nc" is only 5 chars (well under min_chars=50) but
    # contains 2 internal newlines (== min_newlines=2), so has_substantial
    # must fire via the "s.count('\n') >= min_newlines" clause alone, not
    # via length. The second segment "X" is 1 char, which fails
    # has_multiple_items's "all segments >= 2 chars" requirement, so that
    # sibling clause cannot be the one causing the split -- isolates the
    # newline-count branch specifically (distinct from the existing
    # min_chars-length fixtures above, none of which have a short,
    # newline-only-qualifying segment).
    row = [{"text": "a\nb\nc\n\nX", "row": 0, "col": 0}]

    out = split_merged_cells_in_row(row, min_chars=50, min_newlines=2)

    assert [r[0]["text"] for r in out] == ["a\nb\nc", "X"]


def test_split_merged_cells_date_distribution_fallback_for_unsplit_cell():
    # Cell 0's "MBA\n\nBS" triggers the double-newline first pass
    # (max_splits=2 from THAT pass); cell 1 ("1998\n2005") has no \n\n at
    # all, so its own cell_splits entry is set to None back on the very
    # first pass (not via the aligned-line second pass, which never runs
    # here because max_splits already came from the first pass -- the
    # `if max_splits == 1:` guard skips it). Cell 1 only ends up split at
    # all through the separate "no split for this cell - try to distribute
    # dates" fallback, a different code path from the aligned-line padding
    # branch already covered above (which requires the second pass to run).
    row = [
        {"text": "MBA\n\nBS", "row": 0, "col": 0},
        {"text": "1998\n2005", "row": 0, "col": 1},
    ]

    out = split_merged_cells_in_row(row)

    assert out[0][0]["text"] == "MBA"
    assert out[1][0]["text"] == "BS"
    # The date-distribution fallback emits a bare {"text": ...} with no
    # row/col metadata -- unlike the aligned-line padding branch, which
    # explicitly preserves row/col (see the dict-equality assertions above).
    # Assert the full dict so a mutant that disables this fallback (falling
    # through to duplicate the whole original cell at split_idx==0 and blank
    # it elsewhere) is caught even though it would still produce SOME text.
    assert out[0][1] == {"text": "1998"}
    assert out[1][1] == {"text": "2005"}


def test_split_merged_cells_double_newline_uneven_segment_counts():
    # Two cells both split on \n\n but into a DIFFERENT number of
    # substantial segments (3 vs 2) -- max_splits becomes 3 (the larger),
    # and cell 1 (only 2 segments) must hit the "this cell has fewer splits
    # than max - use empty for extras" branch at split_idx=2, not the
    # date-distribution fallback (it was never None -- it has a recorded
    # split list, just a shorter one) and not a duplicated/padded value.
    # No existing fixture has two \n\n-split cells of differing segment
    # counts in the same row.
    seg_a = ["A" * 60, "B" * 60, "C" * 60]
    seg_b = ["X" * 60, "Y" * 60]
    row = [
        {"text": "\n\n".join(seg_a), "row": 0, "col": 0},
        {"text": "\n\n".join(seg_b), "row": 0, "col": 1},
    ]

    out = split_merged_cells_in_row(row, min_chars=50, min_newlines=2)

    assert [c["text"] for c in out[0]] == [seg_a[0], seg_b[0]]
    assert [c["text"] for c in out[1]] == [seg_a[1], seg_b[1]]
    # Row/col metadata is preserved for the still-splitting cell 0, but the
    # exhausted cell 1 gets a bare blank dict with no row/col -- pins the
    # "fewer splits than max" branch's exact shape, not just its text.
    assert out[2][0] == {"text": seg_a[2], "row": 0, "col": 0}
    assert out[2][1] == {"text": ""}


def test_split_merged_cells_aligned_lines_requires_exact_target_count():
    # Two cells share a 3-line count (target_count=3 via the aligned-line
    # pass). A third cell has FOUR lines -- MORE than target_count, not
    # fewer -- and is not a date column, so it must fail the aligned pass's
    # `line_counts[cell_idx] == target_count` membership test and fall
    # through to the "no split for this cell" branch, which uses the whole
    # original (unsplit) cell only at split_idx==0 and blanks it elsewhere.
    # A `==` -> `>=` mutant would instead accept this cell into the aligned
    # split and silently DROP its 4th line ("z"), since the row only emits
    # target_count (3) split rows. No existing fixture has a cell with MORE
    # lines than the aligned target.
    row = [
        {"text": "a\nb\nc", "row": 0, "col": 0},
        {"text": "d\ne\nf", "row": 0, "col": 1},
        {"text": "w\nx\ny\nz", "row": 0, "col": 2},
    ]

    out = split_merged_cells_in_row(row, min_chars=50, min_newlines=2)

    assert [c["text"] for c in out[0]] == ["a", "d", "w\nx\ny\nz"]
    # The unsplit cell is carried over as the ORIGINAL dict (row/col intact),
    # not rebuilt as a bare {"text": ...}.
    assert out[0][2] == {"text": "w\nx\ny\nz", "row": 0, "col": 2}
    assert [c["text"] for c in out[1]] == ["b", "e", ""]
    assert [c["text"] for c in out[2]] == ["c", "f", ""]


def test_split_merged_cells_two_line_aligned_cells_not_split():
    # Two cells share a 2-line count. The source comment is explicit that
    # the aligned-line pass exists to "avoid splitting single-line or
    # 2-line content" -- count_freq only counts line counts >= 3, so a pair
    # of matching 2-line cells must NOT trigger a split even though they
    # satisfy every other condition (2+ cells, equal line counts) that a
    # >= 2 floor would accept. Pins the literal ">= 3" boundary, distinct
    # from the existing 3-line-cell fixture above.
    row = [
        {"text": "a\nb", "row": 0, "col": 0},
        {"text": "c\nd", "row": 0, "col": 1},
    ]

    assert split_merged_cells_in_row(row, min_chars=50, min_newlines=2) == [row]


def _all_lines(rows):
    return sorted(
        line.strip() for r in rows for c in r for line in c["text"].split("\n") if line.strip()
    )


def test_split_merged_cells_date_column_never_drops_surplus_lines_612():
    # #612 shape (invented text): cell 0 holds a blank paragraph, so the \n\n
    # pass claims max_splits=2 from cell 0 alone; the aligned-line pass never
    # runs. The 4-line title cell is unsplit and the 4-line date cell is
    # distributed by the date-column fallback, which used to emit only the
    # first max_splits (2) dates and drop the other two.
    row = [
        {"text": "Alpha unit one\nAlpha unit two\n\nAlpha unit three", "row": 0, "col": 0},
        {"text": "Rank A\nRank B\nRank C\nRank D", "row": 0, "col": 1},
        {"text": "2011 - Present\n2009 - 2011\n2007 - 2009\n2000 - Present", "row": 0, "col": 2},
    ]

    out = split_merged_cells_in_row(row)

    assert _all_lines(out) == _all_lines([row])
    assert [r[2]["text"] for r in out] == [
        "2011 - Present",
        "2009 - 2011\n2007 - 2009\n2000 - Present",
    ]


def test_split_merged_cells_aligned_date_column_longer_than_target_keeps_surplus_612():
    # Aligned-line pass (two 3-line cells => target 3) beside a 4-line date
    # column: the old `padded_lines[:target_count]` truncation dropped line 4.
    row = [
        {"text": "a\nb\nc", "row": 0, "col": 0},
        {"text": "d\ne\nf", "row": 0, "col": 1},
        {"text": "2001\n2002\n2003\n2004", "row": 0, "col": 2},
    ]

    out = split_merged_cells_in_row(row)

    assert _all_lines(out) == _all_lines([row])
    assert [r[2]["text"] for r in out] == ["2001", "2002", "2003\n2004"]


# --------------------------------------------------------------------------
# extract_table_metadata
# --------------------------------------------------------------------------


def test_extract_table_metadata_basic_shape():
    doc = Document()
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "R0C0"
    table.cell(0, 1).text = "R0C1"
    table.cell(1, 0).text = "R1C0"
    table.cell(1, 1).text = "R1C1"

    meta = extract_table_metadata(table, idx="table_0")

    assert meta["idx"] == "table_0"
    assert meta["rows"] == 2
    assert meta["cols"] == 2
    assert [c["text"] for c in meta["data"][1]] == ["R1C0", "R1C1"]


def test_extract_table_metadata_gridspan_emits_merged_cell_once():
    # python-docx repeats the merged _Cell at every grid column it spans;
    # the reader emits each distinct <w:tc> once (#1229), with col counting
    # logical cells. `cols` stays the layout-grid width.
    doc = Document()
    table = doc.add_table(rows=2, cols=3)
    a = table.cell(0, 0)
    a.text = "Merged Header"
    a.merge(table.cell(0, 1))
    table.cell(1, 0).text = "x"
    table.cell(1, 1).text = "y"
    table.cell(1, 2).text = "z"

    meta = extract_table_metadata(table, idx="table_0")

    assert meta["cols"] == 3
    row0 = meta["data"][0]
    assert [(c["col"], c["text"]) for c in row0] == [(0, "Merged Header"), (1, "")]
    assert [c["text"] for c in meta["data"][1]] == ["x", "y", "z"]


def test_extract_table_metadata_gridspan_stacked_cell_not_split_into_subrows():
    # A merged cell stacking three lines beside a one-line cell used to be
    # counted as two aligned 3-line cells by split_merged_cells_in_row (#612).
    doc = Document()
    table = doc.add_table(rows=1, cols=3)
    merged = table.cell(0, 0).merge(table.cell(0, 1))
    merged.text = "2001 - 2002\nA12345\n(Doe J, Example University)"
    table.cell(0, 2).text = "Example title"

    row = extract_table_metadata(table, idx="table_0")["data"][0]

    assert len(row) == 2
    assert len(split_merged_cells_in_row(row)) == 1


def test_extract_table_metadata_vertical_merge_still_repeats_down_rows():
    # A vMerge continuation is a distinct <w:tc> in its own row; only
    # same-row gridSpan repeats are collapsed.
    doc = Document()
    table = doc.add_table(rows=2, cols=2)
    top = table.cell(0, 0)
    top.text = "Spans down"
    top.merge(table.cell(1, 0))
    table.cell(0, 1).text = "r0"
    table.cell(1, 1).text = "r1"

    data = extract_table_metadata(table, idx="table_0")["data"]

    assert [c["text"] for c in data[0]] == ["Spans down", "r0"]
    assert [c["text"] for c in data[1]] == ["Spans down", "r1"]


def test_extract_table_metadata_falls_back_to_itertext_and_lets_its_errors_propagate(monkeypatch):
    # Reproduces the "cell.text returns empty for malformed/complex XML"
    # scenario named in the source comment. get_cell_text only walks
    # cell.paragraphs, so text sitting directly under <w:tc> (outside any
    # <w:p>) is invisible to it -- cell_text starts "" and itertext() over
    # the whole cell element recovers it.
    from docx.oxml.table import CT_Tc

    doc = Document()
    table = doc.add_table(rows=1, cols=1)
    cell = table.rows[0].cells[0]
    cell._tc.append(
        parse_xml(f'<w:hiddenMarker {nsdecls("w")}>HIDDEN_TEXT</w:hiddenMarker>')
    )
    assert get_cell_text(cell) == ""  # confirm the primary walk sees nothing

    assert extract_table_metadata(table, idx="table_0")["data"][0][0]["text"] == "HIDDEN_TEXT"

    # #611: a failure inside the fallback is a bug, not an empty cell --
    # it must surface instead of being swallowed by a bare except.
    def boom(self, *args, **kwargs):
        raise RuntimeError("simulated itertext failure")

    monkeypatch.setattr(CT_Tc, "itertext", boom)
    with pytest.raises(RuntimeError, match="simulated itertext failure"):
        extract_table_metadata(table, idx="table_0")


# --------------------------------------------------------------------------
# looks_like_section_header
# --------------------------------------------------------------------------


def test_looks_like_section_header_all_caps_short_line():
    is_header, confidence = looks_like_section_header("PUBLICATIONS")

    assert is_header is True
    assert confidence == 1.0


def test_looks_like_section_header_multi_line_text_is_judged_by_its_first_line():
    # A table cell whose first line is the header and whose later lines are
    # entry text: only the first line is scored, so the citation-shaped
    # remainder (et al., a year in parentheses) must not reject it.
    is_header, confidence = looks_like_section_header("PUBLICATIONS\nSmith J, et al. Paper (2024).")

    assert (is_header, confidence) == (True, 1.0)


def test_looks_like_section_header_title_case_short_line():
    # "Research Interests" stacks five separate bonuses: keyword (+0.4,
    # matches "research"/"research interests"), title-case <=6 words (+0.2),
    # 1-5 word short-text (+0.1), and 1-2 word known-keyword (+0.2) -- pinned
    # to the exact sum (not just ">= the 0.4 threshold") so a mutant that
    # drops or double-counts any one bonus is caught.
    is_header, confidence = looks_like_section_header("Research Interests")

    assert is_header is True
    assert confidence == pytest.approx(0.9)


def test_looks_like_section_header_keyword_only_at_exact_threshold():
    # A CV keyword ("grants"/"funding") inside a long (>6-word), non-title-case,
    # non-all-caps sentence earns ONLY the +0.4 keyword bonus -- every other
    # bonus's guard (<=6 words, <=5 words, <=2 words, isupper, endswith ':')
    # is false. This lands exactly ON the is_header threshold, so it also
    # pins the ">= 0.4" boundary itself (a ">" mutant would flip this to
    # False while leaving every other test in the file green).
    text = "Grants and funding for the research program done here"
    assert len(text.split()) == 9  # confirms none of the <=N word bonuses apply

    is_header, confidence = looks_like_section_header(text)

    assert (is_header, confidence) == (True, 0.4)


def test_looks_like_section_header_long_sentence_not_header():
    long_sentence = (
        "This is a very long sentence describing a thing that happened "
        "over several years in great and unnecessary detail for a CV entry."
    )
    is_header, confidence = looks_like_section_header(long_sentence)

    assert is_header is False
    assert confidence == 0.0


def test_looks_like_section_header_length_cap_rejects_long_keyword_text():
    # Over the literal 100-char cap, but otherwise scores well past the 0.4
    # threshold on the keyword bonus alone ("research" appears 15 times) --
    # this isolates the length guard as the deciding condition, unlike the
    # existing long_sentence test above, which scores 0.0 for the unrelated
    # reason that it carries no CV keyword at all and would stay under
    # threshold even with the cap removed.
    text = "Research " * 15
    assert len(text.strip()) > 100  # confirms the cap is actually engaged

    is_header, confidence = looks_like_section_header(text)

    assert (is_header, confidence) == (False, 0.0)


def test_looks_like_section_header_date_range_rejected_despite_keyword():
    # "Education 2020-2024" carries a CV keyword ("education") that alone
    # scores well past the 0.4 threshold (keyword +0.4, short-text +0.1,
    # short-known-keyword +0.2 = 0.7), so the date-pattern guard must reject
    # it before any bonus is computed, not merely reduce the score.
    is_header, confidence = looks_like_section_header("Education 2020-2024")

    assert (is_header, confidence) == (False, 0.0)


def test_looks_like_section_header_citation_year_parenthetical_rejected():
    # "Publications (2024)" carries a CV keyword ("publications") that alone
    # scores well past the 0.4 threshold if the citation guard is skipped
    # (keyword +0.4, short-text +0.1, short-known-keyword +0.2 = 0.7 -- the
    # exact score a deleted citation-reject loop would produce), so the
    # "(2024)" citation-year-parenthetical pattern must reject it before any
    # bonus is computed. Distinct from the existing date-RANGE rejection
    # test above, which pins a different regex in the same function (the
    # date_patterns loop, not citation_patterns).
    is_header, confidence = looks_like_section_header("Publications (2024)")

    assert (is_header, confidence) == (False, 0.0)


def test_looks_like_section_header_et_al_citation_rejected_despite_keyword():
    # "et al." is a citation marker, not a header signal -- this text also
    # carries a CV keyword ("research") that alone would score 0.5 with the
    # citation guard skipped (keyword +0.4, short-text +0.1), so the
    # citation_patterns loop must be the deciding condition here, not the
    # date_patterns loop (no year/date substring appears at all in this text).
    is_header, confidence = looks_like_section_header("Smith J, et al. Research")

    assert (is_header, confidence) == (False, 0.0)


def test_looks_like_section_header_numbered_heading_scores_low():
    # "1. Introduction" fails the title-case check (first word starts with
    # a digit, not an uppercase letter) and carries no CV keyword, so it
    # lands well under the 0.4 threshold despite reading like a heading.
    is_header, confidence = looks_like_section_header("1. Introduction")

    assert is_header is False
    assert confidence == 0.1


def test_looks_like_section_header_empty_text():
    assert looks_like_section_header("") == (False, 0.0)


# --------------------------------------------------------------------------
# get_table_first_cell_text
# --------------------------------------------------------------------------


def test_get_table_first_cell_text_plain():
    doc = Document()
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "First Cell"

    assert get_table_first_cell_text(table) == "First Cell"


def test_get_table_first_cell_text_no_rows():
    doc = Document()
    table = doc.add_table(rows=0, cols=0)

    assert get_table_first_cell_text(table) == ""


def test_get_table_first_cell_text_row_with_no_cells():
    from docx.oxml.ns import qn

    doc = Document()
    table = doc.add_table(rows=1, cols=1)
    tr = table.rows[0]._tr
    for tc in list(tr.findall(qn("w:tc"))):
        tr.remove(tc)

    assert get_table_first_cell_text(table) == ""


def test_get_table_first_cell_text_falls_back_to_itertext():
    # Same "hidden text outside any <w:p>" shape as the extract_table_metadata
    # fallback test: get_cell_text sees nothing, itertext() over the whole
    # cell element recovers it.
    doc = Document()
    table = doc.add_table(rows=1, cols=1)
    cell = table.rows[0].cells[0]
    cell._element.append(
        parse_xml(f'<w:hiddenMarker {nsdecls("w")}>HIDDEN_FIRST_CELL</w:hiddenMarker>')
    )

    assert get_table_first_cell_text(table) == "HIDDEN_FIRST_CELL"


# --------------------------------------------------------------------------
# flatten_table_to_text
# --------------------------------------------------------------------------


_FLATTEN_TABLE_DATA = {
    "data": [
        [{"text": "Header"}, {"text": ""}],
        [{"text": "Row1A"}, {"text": "Row1B"}],
        [{"text": ""}, {"text": "   "}],
        [{"text": "Row3A"}, {"text": ""}],
    ]
}


def test_flatten_table_to_text_skip_first_row_false():
    out = flatten_table_to_text(_FLATTEN_TABLE_DATA, skip_first_row=False)

    assert out == "Header\nRow1A\tRow1B\nRow3A"


def test_flatten_table_to_text_skip_first_row_true():
    out = flatten_table_to_text(_FLATTEN_TABLE_DATA, skip_first_row=True)

    assert out == "Row1A\tRow1B\nRow3A"


# --------------------------------------------------------------------------
# extract_unified_elements
# --------------------------------------------------------------------------


def _build_unified_fixture_docx(path):
    doc = Document()
    doc.add_paragraph("CURRICULUM VITAE")
    doc.add_paragraph("")  # empty paragraph, between the intro and the tables

    # Table 1: header-detected (first cell "PUBLICATIONS"), 2 cols so it
    # does NOT hit the single-column-per-row "explode into paragraphs" path.
    t1 = doc.add_table(rows=2, cols=2)
    t1.cell(0, 0).text = "PUBLICATIONS"
    t1.cell(0, 1).text = ""
    t1.cell(1, 0).text = "Smith J. Paper title (2024)."
    t1.cell(1, 1).text = "2024"

    # Table 2: no header-like first cell ("2020" is a bare year, not a
    # header per looks_like_section_header) -> emitted as one "table" element.
    t2 = doc.add_table(rows=2, cols=2)
    t2.cell(0, 0).text = "2020"
    t2.cell(0, 1).text = "Some Award"
    t2.cell(1, 0).text = "2021"
    t2.cell(1, 1).text = "Another Award"

    doc.save(str(path))


def test_extract_unified_elements_orders_types_and_indices(tmp_path):
    docx_path = tmp_path / "unified_fixture.docx"
    _build_unified_fixture_docx(docx_path)

    result = extract_unified_elements(str(docx_path))
    elements = result["elements"]

    unified_idxs = [e["unified_idx"] for e in elements]
    assert unified_idxs == list(range(len(elements)))  # contiguous, document order

    types = [e["type"] for e in elements]
    assert types == ["paragraph", "empty", "table_header", "table_content", "table"]

    assert elements[0]["text"] == "CURRICULUM VITAE"
    assert elements[2]["text"] == "PUBLICATIONS"
    assert "Smith J." in elements[3]["text"]
    assert "2020" in elements[4]["text"] and "Another Award" in elements[4]["text"]

    meta = result["meta"]
    assert meta["num_elements"] == len(elements)
    assert meta["num_paragraphs"] == 1
    assert meta["num_tables"] == 2
    assert meta["num_table_headers"] == 1
    assert meta["num_empty"] == 1


def test_extract_unified_elements_no_header_table_splits_merged_cells(tmp_path):
    # A table with no header-like first cell is emitted as one "table"
    # element -- but each row still goes through split_merged_cells_in_row
    # first, so a double-newline cell beside an aligned two-line cell
    # becomes two rows in the element's text, not one row with embedded
    # newlines.
    doc = Document()
    t = doc.add_table(rows=2, cols=2)
    t.cell(0, 0).text = "MBA\n\nBS"
    t.cell(0, 1).text = "1998\n2005"
    t.cell(1, 0).text = "2021"
    t.cell(1, 1).text = "Another Award"
    docx_path = tmp_path / "no_header_merged.docx"
    doc.save(str(docx_path))

    elements = extract_unified_elements(str(docx_path))["elements"]

    assert [e["type"] for e in elements] == ["table"]
    assert elements[0]["rows"] == 3
    assert elements[0]["text"] == "MBA | 1998\nBS | 2005\n2021 | Another Award"


def test_extract_unified_elements_single_column_table_explodes_to_paragraphs(tmp_path):
    # Documented in the source (#208): a single-column table is a layout
    # box, not tabular data, so every non-empty cell paragraph becomes its
    # own "paragraph" element rather than a "table"/"table_header" element.
    doc = Document()
    table = doc.add_table(rows=1, cols=1)
    cell = table.rows[0].cells[0]
    cell.paragraphs[0].add_run("First line")
    cell.add_paragraph("Second line")
    docx_path = tmp_path / "single_col.docx"
    doc.save(str(docx_path))

    result = extract_unified_elements(str(docx_path))

    types = [e["type"] for e in result["elements"]]
    assert types == ["paragraph", "paragraph"]
    texts = [e["text"] for e in result["elements"]]
    assert texts == ["First line", "Second line"]
    assert result["meta"]["num_tables"] == 1
    assert result["meta"]["num_paragraphs"] == 2


def test_extract_unified_elements_para_idx_is_a_doc_paragraphs_position_after_a_layout_table(tmp_path):
    # #609: exploded cell paragraphs used to advance para_idx, so every
    # body paragraph after a single-column table pointed past its own
    # doc.paragraphs slot. They now carry None, and body para_idx stays aligned.
    doc = Document()
    doc.add_paragraph("Before")
    cell = doc.add_table(rows=1, cols=1).rows[0].cells[0]
    cell.paragraphs[0].add_run("Cell one")
    cell.add_paragraph("Cell two")
    doc.add_paragraph("After")
    docx_path = tmp_path / "layout_then_body.docx"
    doc.save(str(docx_path))

    elements = extract_unified_elements(str(docx_path))["elements"]

    assert [(e["text"], e["para_idx"], e["idx"]) for e in elements] == [
        ("Before", 0, 0), ("Cell one", None, None), ("Cell two", None, None), ("After", 1, 1),
    ]
    assert [e["unified_idx"] for e in elements] == [0, 1, 2, 3]
    paragraphs = Document(str(docx_path)).paragraphs
    assert [paragraphs[e["para_idx"]].text for e in elements if e["para_idx"] is not None] == ["Before", "After"]


def test_extract_unified_elements_single_column_vertical_merge_dedupes_and_skips_empty(tmp_path):
    # A vertically-merged single-column table repeats the SAME cell object
    # across rows (like gridSpan repeats it across columns) -- the reader
    # must visit it once (seen_cells), and an empty paragraph inside a cell
    # must be skipped rather than emitted as a blank "paragraph" element.
    doc = Document()
    table = doc.add_table(rows=2, cols=1)
    top = table.cell(0, 0)
    top.paragraphs[0].add_run("Only line")
    top.add_paragraph("")  # empty paragraph inside the cell -> skipped
    bottom = table.cell(1, 0)
    top.merge(bottom)  # vertical merge: row 1's cell object == row 0's
    docx_path = tmp_path / "single_col_vmerge.docx"
    doc.save(str(docx_path))

    result = extract_unified_elements(str(docx_path))

    texts = [e["text"] for e in result["elements"]]
    assert texts == ["Only line"]  # not duplicated, empty paragraph dropped
    assert result["meta"]["num_paragraphs"] == 1


def _build_subheader_fixture_docx(path):
    """One table exercising every sub-header-scanning branch below the
    table's own header row: a header row whose first cell carries substantial
    remaining content after its header line, a row with an embedded \\n\\n
    header inside one cell, an over-80-char plain content row, a row-level
    sub-header (own row, no embedding), and a plain trailing content row."""
    doc = Document()
    table = doc.add_table(rows=5, cols=2)

    header_cell = table.cell(0, 0)
    header_cell.paragraphs[0].text = "K. EXTRAMURAL FUNDING"
    header_cell.add_paragraph(
        "Society of Example Program Directors research award for outstanding contribution."
    )
    table.cell(0, 1).text = "2020"

    embedded_cell = table.cell(1, 0)
    embedded_cell.paragraphs[0].text = "Some free text before section break."
    embedded_cell.add_paragraph("")  # blank paragraph -> the \n\n separator
    embedded_cell.add_paragraph("SUBSECTION HEADER")
    embedded_cell.add_paragraph("Detail line 1")
    embedded_cell.add_paragraph("Detail line 2")
    table.cell(1, 1).text = ""

    table.cell(2, 0).text = "A" * 90  # long single-line cell, no header
    table.cell(2, 1).text = "short"

    table.cell(3, 0).text = "PRESENTATIONS"  # row-level sub-header
    table.cell(3, 1).text = ""

    table.cell(4, 0).text = "Talk one at Conference X"  # plain content row
    table.cell(4, 1).text = "New York"

    doc.save(str(path))


def test_extract_unified_elements_scans_table_rows_for_subheaders(tmp_path):
    docx_path = tmp_path / "subheaders.docx"
    _build_subheader_fixture_docx(docx_path)

    result = extract_unified_elements(str(docx_path))
    elements = result["elements"]

    unified_idxs = [e["unified_idx"] for e in elements]
    assert unified_idxs == list(range(len(elements)))

    header_texts = [e["text"] for e in elements if e["type"] == "table_header"]
    assert header_texts == ["K. EXTRAMURAL FUNDING", "SUBSECTION HEADER", "PRESENTATIONS"]
    assert result["meta"]["num_table_headers"] == 3

    content_texts = [e["text"] for e in elements if e["type"] == "table_content"]
    # Row 0's remaining content is captured under the table-level header.
    assert any("Society of Example" in t for t in content_texts)
    # Row 0 has 2 cells, so the "len(row_0) > 1" pairing branch must fire:
    # the OTHER row-0 cell ("2020") is paired onto the same content row via
    # " | ", not dropped. Pin the exact first content chunk (not just a
    # substring) -- a substring check on "Society of Example" alone
    # is satisfied equally by the else-branch (single-cell synthetic row,
    # which drops "2020" entirely), so it cannot tell the two branches apart.
    assert content_texts[0] == (
        "Society of Example Program Directors research award for "
        "outstanding contribution. | 2020\n"
        "Some free text before section break."
    )
    # The embedded header's own remaining lines and the long over-80-char
    # row both land in accumulated content emitted before "PRESENTATIONS".
    pre_presentations = content_texts[1]
    assert "Detail line 1" in pre_presentations
    assert "A" * 90 in pre_presentations
    # The final plain content row is flushed after the last header.
    assert any("Talk one at Conference X" in t and "New York" in t for t in content_texts)


# --------------------------------------------------------------------------
# extract_unified_elements -- pre-LLM DOB/SSN value scrub (#847)
# --------------------------------------------------------------------------


def test_extract_unified_elements_scrubs_dob_and_ssn_values(tmp_path):
    # Positive cases, all SYNTHETIC: a colon DOB, a colonless DOB, an SSN,
    # and a DOB label+value that sits inside a single table cell -- every
    # one keeps its label text and only the value is replaced.
    doc = Document()
    doc.add_paragraph("Date of Birth: 01/02/1970")
    doc.add_paragraph("Born on 05/06/1975 in Example City")
    doc.add_paragraph("SSN: 123-45-6789")
    table = doc.add_table(rows=1, cols=1)
    table.cell(0, 0).text = "Date of Birth: 03/04/1980"
    docx_path = tmp_path / "pre_llm_scrub_positive.docx"
    doc.save(str(docx_path))

    result = extract_unified_elements(str(docx_path))
    elements = result["elements"]

    texts = [e["text"] for e in elements]
    assert texts == [
        "Date of Birth: [withheld]",
        "Born on [withheld] in Example City",
        "SSN: [withheld]",
        "Date of Birth: [withheld]",
    ]
    # No value digit survives in any scrubbed element.
    for t in texts:
        assert not any(c.isdigit() for c in t)


def test_extract_unified_elements_scrubs_dob_and_ssn_in_table_cells(tmp_path):
    # Round 2 (#847): a 3-COLUMN table, unlike the single-column table
    # above (which the extractor EXPLODES into paragraph elements, never
    # exercising the "data" cell loop at all -- the earlier, single-column
    # fixture made the "skip table cells" mutant survive). This fixture
    # asserts on `element["data"]` cell text directly, which is what
    # stage 2 actually reads (`cell.get("text")`), plus a label-cell /
    # value-cell pair on its own row.
    doc = Document()
    table = doc.add_table(rows=2, cols=3)
    table.cell(0, 0).text = "Note"
    table.cell(0, 1).text = "SSN: 123-45-6789"
    table.cell(0, 2).text = "Other"
    table.cell(1, 0).text = "Date of Birth:"
    table.cell(1, 1).text = "01/02/1970"
    table.cell(1, 2).text = "Unrelated"
    docx_path = tmp_path / "pre_llm_scrub_table_cells.docx"
    doc.save(str(docx_path))

    elements = extract_unified_elements(str(docx_path))["elements"]
    table_elements = [e for e in elements if e.get("data")]
    assert table_elements, "fixture must produce at least one element with cell data"

    all_cell_texts = [
        cell.get("text", "") if isinstance(cell, dict) else str(cell)
        for el in table_elements
        for row in el["data"]
        for cell in row
    ]
    assert "Note" in all_cell_texts
    assert "Other" in all_cell_texts
    assert "Unrelated" in all_cell_texts
    assert "SSN: [withheld]" in all_cell_texts
    assert "Date of Birth:" in all_cell_texts
    assert "[withheld]" in all_cell_texts
    # No raw value survives anywhere in the cell data.
    assert not any("123-45-6789" in t or "01/02/1970" in t for t in all_cell_texts)


def test_extract_unified_elements_pre_llm_scrub_leaves_other_text_untouched(tmp_path):
    # Negative cases: nothing that merely LOOKS numeric or date-adjacent,
    # without a DOB/SSN label or shape, is touched -- and element count and
    # every non-matching element's text are unchanged.
    doc = Document()
    doc.add_paragraph("Publications:")
    doc.add_paragraph("Smith J. Date: 2015. A study of examples.")
    doc.add_paragraph("Grant number R01-CA123456 funded 1999.")
    doc.add_paragraph("Reference number 123456789 on file.")
    docx_path = tmp_path / "pre_llm_scrub_negative.docx"
    doc.save(str(docx_path))

    result = extract_unified_elements(str(docx_path))
    texts = [e["text"] for e in result["elements"]]

    assert texts == [
        "Publications:",
        "Smith J. Date: 2015. A study of examples.",
        "Grant number R01-CA123456 funded 1999.",
        "Reference number 123456789 on file.",
    ]
    assert result["meta"]["num_elements"] == 4


def test_extract_unified_elements_pre_llm_scrub_element_count_unchanged(tmp_path):
    # Scrubbing a value must never add or remove an element -- stage 2's
    # element indices depend on the count and order staying identical.
    docx_path = tmp_path / "unified_fixture_for_count.docx"
    _build_unified_fixture_docx(docx_path)
    baseline_count = len(extract_unified_elements(str(docx_path))["elements"])

    doc = Document(str(docx_path))
    doc.add_paragraph("Date of Birth: 01/02/1970")
    doc.add_paragraph("SSN: 123-45-6789")
    doc.save(str(docx_path))

    result = extract_unified_elements(str(docx_path))
    assert len(result["elements"]) == baseline_count + 2
    assert [e["unified_idx"] for e in result["elements"]] == list(
        range(len(result["elements"]))
    )


def test_extract_unified_elements_pre_llm_scrub_is_idempotent(tmp_path):
    # An already-scrubbed value (e.g. a doc round-tripped through a prior
    # run) has no digits left to find, so a second extraction is a no-op.
    doc = Document()
    doc.add_paragraph("Date of Birth: [withheld]")
    docx_path = tmp_path / "already_scrubbed.docx"
    doc.save(str(docx_path))

    result = extract_unified_elements(str(docx_path))
    assert result["elements"][0]["text"] == "Date of Birth: [withheld]"


def test_extract_unified_elements_scrubs_dob_in_the_cell_below_a_label(tmp_path):
    # Round 3 (#847 residual): a two-row FORM table -- a label row, its
    # value directly BELOW it in the same column, not beside it in the
    # same row. Two columns (not one), so the table is NOT exploded into
    # paragraph elements and this actually exercises `data`'s row grid.
    doc = Document()
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Date of Birth:"
    table.cell(0, 1).text = "Note"
    table.cell(1, 0).text = "01/02/1970"
    table.cell(1, 1).text = "Unrelated"
    docx_path = tmp_path / "pre_llm_scrub_cell_below.docx"
    doc.save(str(docx_path))

    elements = extract_unified_elements(str(docx_path))["elements"]
    table_elements = [e for e in elements if e.get("data")]
    all_cell_texts = [
        cell.get("text", "") if isinstance(cell, dict) else str(cell)
        for el in table_elements
        for row in el["data"]
        for cell in row
    ]
    assert "Date of Birth:" in all_cell_texts
    assert "Note" in all_cell_texts
    assert "Unrelated" in all_cell_texts
    assert "[withheld]" in all_cell_texts
    assert not any("01/02/1970" in t for t in all_cell_texts)


def test_extract_unified_elements_leaves_the_cell_below_a_non_dob_label_alone(tmp_path):
    # Negative control: the label row's OWN value is not a bare label ("Note"
    # has no colon), so `pre_llm_bare_label_category` never fires and the
    # cell below is untouched -- an ordinary two-row table is not affected.
    doc = Document()
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Note"
    table.cell(0, 1).text = "Year"
    table.cell(1, 0).text = "See below"
    table.cell(1, 1).text = "1970"
    docx_path = tmp_path / "pre_llm_scrub_cell_below_negative.docx"
    doc.save(str(docx_path))

    elements = extract_unified_elements(str(docx_path))["elements"]
    all_cell_texts = [
        cell.get("text", "") if isinstance(cell, dict) else str(cell)
        for el in elements if el.get("data")
        for row in el["data"]
        for cell in row
    ]
    assert "1970" in all_cell_texts


def test_extract_unified_elements_scrubs_dob_in_the_next_paragraph(tmp_path):
    # Round 3 (#847 residual): a label-only paragraph ("Date of Birth:")
    # with its value as the NEXT paragraph in the unified stream, not on
    # the same line -- `redact_pre_llm_values`'s in-text lookahead cannot
    # see across a paragraph boundary, only within one string.
    doc = Document()
    doc.add_paragraph("Date of Birth:")
    doc.add_paragraph("01/02/1970")
    doc.add_paragraph("SSN:")
    doc.add_paragraph("123-45-6789")
    docx_path = tmp_path / "pre_llm_scrub_next_paragraph.docx"
    doc.save(str(docx_path))

    texts = [e["text"] for e in extract_unified_elements(str(docx_path))["elements"]]
    assert texts == [
        "Date of Birth:",
        "[withheld]",
        "SSN:",
        "[withheld]",
    ]


def test_extract_unified_elements_leaves_the_next_paragraph_alone_when_label_has_no_colon(tmp_path):
    # Negative control: "Education" is not a bare DOB/SSN label (no colon,
    # no match), so the following paragraph -- which happens to start with
    # a year -- is left untouched.
    doc = Document()
    doc.add_paragraph("Education")
    doc.add_paragraph("2010 - MD, Example University")
    docx_path = tmp_path / "pre_llm_scrub_next_paragraph_negative.docx"
    doc.save(str(docx_path))

    texts = [e["text"] for e in extract_unified_elements(str(docx_path))["elements"]]
    assert texts == ["Education", "2010 - MD, Example University"]


def test_extract_unified_elements_next_paragraph_scrub_element_count_unchanged(tmp_path):
    docx_path = tmp_path / "unified_fixture_for_count_next_para.docx"
    _build_unified_fixture_docx(docx_path)
    baseline_count = len(extract_unified_elements(str(docx_path))["elements"])

    doc = Document(str(docx_path))
    doc.add_paragraph("Date of Birth:")
    doc.add_paragraph("01/02/1970")
    doc.save(str(docx_path))

    result = extract_unified_elements(str(docx_path))
    assert len(result["elements"]) == baseline_count + 2
    assert [e["unified_idx"] for e in result["elements"]] == list(
        range(len(result["elements"]))
    )


def test_extract_text_from_docx_carries_the_pre_llm_scrub(tmp_path):
    # Stage 1a's chunk builder reads extract_text_from_docx, which wraps
    # extract_unified_elements -- confirm the scrub survives that wrapper,
    # since it is one of stage 1a/1b/2's three callers (#847 scout Q1).
    try:
        from unified_pipeline.segmentation.chunked_chat_hierarchy_extractor import (
            extract_text_from_docx,
        )
    except ImportError:
        pytest.skip("chunked_chat_hierarchy_extractor not importable in this env")

    doc = Document()
    doc.add_paragraph("Date of Birth: 01/02/1970")
    docx_path = tmp_path / "stage1a_scrub.docx"
    doc.save(str(docx_path))

    lines = extract_text_from_docx(str(docx_path))
    assert lines == ["Date of Birth: [withheld]"]


def test_extract_text_from_docx_carries_the_cell_below_scrub(tmp_path):
    # Reader-level regression (#847 residual round 4): a two-row FORM
    # table's pre-LLM scrub (_scrub_pre_llm_pii_column) used to update only
    # the below-cell's OWN `data["text"]`, never the table element's
    # pre-flattened `text` field -- the field stage 1a's chunk builder
    # (extract_text_from_docx, wrapping extract_unified_elements) actually
    # reads. The raw DOB survived here even though `data` was clean.
    try:
        from unified_pipeline.segmentation.chunked_chat_hierarchy_extractor import (
            extract_text_from_docx,
        )
    except ImportError:
        pytest.skip("chunked_chat_hierarchy_extractor not importable in this env")

    doc = Document()
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Date of Birth:"
    table.cell(0, 1).text = "Note"
    table.cell(1, 0).text = "01/02/1970"
    table.cell(1, 1).text = "Unrelated"
    docx_path = tmp_path / "stage1a_scrub_cell_below.docx"
    doc.save(str(docx_path))

    lines = extract_text_from_docx(str(docx_path))
    assert len(lines) == 1
    assert "01/02/1970" not in lines[0]
    assert "[withheld]" in lines[0]
    # ...and the same element's per-cell `data` (get_element_text's reader).
    table_element = next(e for e in extract_unified_elements(str(docx_path))["elements"] if e.get("data"))
    assert not any("01/02/1970" in c["text"] for row in table_element["data"] for c in row)


def test_extract_unified_elements_scrubs_every_child_date_under_a_children_label(tmp_path):
    # #847 residual round 4: redact_pre_llm_values used to take only the
    # FIRST value match inside a label's span -- a "Children:" line naming
    # more than one child's date left every date after the first one
    # reaching the LLM unscrubbed.
    doc = Document()
    doc.add_paragraph("Children: Ann (01/02/2010), Bob (03/04/2012), Cy (05/06/2014)")
    docx_path = tmp_path / "pre_llm_scrub_multiple_children.docx"
    doc.save(str(docx_path))

    texts = [e["text"] for e in extract_unified_elements(str(docx_path))["elements"]]
    assert texts == ["Children: Ann ([withheld]), Bob ([withheld]), Cy ([withheld])"]


def test_extract_unified_elements_cell_below_scrub_skips_a_row_already_resolved(tmp_path):
    # Negative control (#847 residual round 4): the label's OWN row already
    # carries its value beside it ("Date of Birth:" | "01/02/1970"), so
    # `_scrub_pre_llm_pii_row` already withholds it same-row. The row BELOW
    # is an unrelated field ("Appointed 2001") and must be left alone --
    # without the same-row guard this used to become "Appointed [withheld]".
    doc = Document()
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Date of Birth:"
    table.cell(0, 1).text = "01/02/1970"
    table.cell(1, 0).text = "Appointed 2001"
    table.cell(1, 1).text = "Note"
    docx_path = tmp_path / "pre_llm_scrub_cell_below_row_resolved.docx"
    doc.save(str(docx_path))

    elements = extract_unified_elements(str(docx_path))["elements"]
    all_cell_texts = [
        cell.get("text", "") if isinstance(cell, dict) else str(cell)
        for el in elements if el.get("data")
        for row in el["data"]
        for cell in row
    ]
    assert "Appointed 2001" in all_cell_texts


def test_extract_unified_elements_cell_below_scrub_skips_a_row_already_resolved_full_date(tmp_path):
    # Same negative control as above, but the cell below OPENS with a whole
    # date ("03/04/1999"), so the cross-boundary shape check alone would
    # take it; only the same-row-resolved guard blocks it.
    doc = Document()
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Date of Birth:"
    table.cell(0, 1).text = "01/02/1970"
    table.cell(1, 0).text = "03/04/1999"
    table.cell(1, 1).text = "Note"
    docx_path = tmp_path / "pre_llm_scrub_cell_below_row_resolved_full_date.docx"
    doc.save(str(docx_path))

    elements = extract_unified_elements(str(docx_path))["elements"]
    all_cell_texts = [
        cell.get("text", "") if isinstance(cell, dict) else str(cell)
        for el in elements if el.get("data")
        for row in el["data"]
        for cell in row
    ]
    assert "03/04/1999" in all_cell_texts


def test_extract_unified_elements_next_paragraph_scrub_leaves_an_unrelated_year_range_alone(tmp_path):
    # Negative control (#847 residual round 4): a blank "Date of Birth:"
    # label paragraph followed by an unrelated education line starting
    # with a bare year -- the bare-year fallback in the DOB value shape
    # must not fire at this lower-confidence, cross-paragraph position
    # (it used to turn "1990-1994 BA, Example College" into
    # "[withheld]-1994 BA, Example College").
    doc = Document()
    doc.add_paragraph("Date of Birth:")
    doc.add_paragraph("1990-1994 BA, Example College")
    docx_path = tmp_path / "pre_llm_scrub_next_paragraph_year_range.docx"
    doc.save(str(docx_path))

    texts = [e["text"] for e in extract_unified_elements(str(docx_path))["elements"]]
    assert texts == ["Date of Birth:", "1990-1994 BA, Example College"]


def test_extract_unified_elements_cell_below_scrub_leaves_a_bare_year_alone(tmp_path):
    # Negative control (#847 residual round 4): the label's own row has NO
    # value beside it (so the same-row-resolved guard does not apply), and
    # the cell below is a BARE year, not a whole date -- the cell-below
    # scrub must require a full date at this cross-boundary position, same
    # as the next-paragraph case above.
    doc = Document()
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Date of Birth:"
    table.cell(0, 1).text = "Note"
    table.cell(1, 0).text = "2001"
    table.cell(1, 1).text = "Unrelated"
    docx_path = tmp_path / "pre_llm_scrub_cell_below_bare_year.docx"
    doc.save(str(docx_path))

    elements = extract_unified_elements(str(docx_path))["elements"]
    all_cell_texts = [
        cell.get("text", "") if isinstance(cell, dict) else str(cell)
        for el in elements if el.get("data")
        for row in el["data"]
        for cell in row
    ]
    assert "2001" in all_cell_texts


# --------------------------------------------------------------------------
# extract_docx_structure
# --------------------------------------------------------------------------


def test_extract_docx_structure_top_level_shape(tmp_path):
    doc = Document()
    doc.add_paragraph("Intro paragraph")
    doc.add_paragraph("")
    doc.add_table(rows=1, cols=2)
    docx_path = tmp_path / "structure_fixture.docx"
    doc.save(str(docx_path))

    struct = extract_docx_structure(str(docx_path))

    assert set(struct.keys()) == {"doc_path", "elements", "meta"}
    assert struct["doc_path"] == str(docx_path)
    assert set(struct["meta"].keys()) == {
        "num_elements", "num_paragraphs", "num_tables", "num_empty",
    }
    assert struct["meta"] == {
        "num_elements": 3, "num_paragraphs": 1, "num_tables": 1, "num_empty": 1,
    }
    # Table idx is namespaced by body-wide position ("table_2": two
    # paragraphs consumed idx 0 and 1 first), distinct from table_index.
    table_elem = struct["elements"][2]
    assert table_elem["idx"] == "table_2"
    assert table_elem["table_index"] == 0
    assert table_elem["type"] == "table"


# --------------------------------------------------------------------------
# normalize_style_name
# --------------------------------------------------------------------------


def test_normalize_style_name_heading_level():
    assert normalize_style_name("Heading 2") == {
        "role": "heading", "level": 2, "original": "Heading 2",
    }


def test_normalize_style_name_title_defaults_to_level_1():
    assert normalize_style_name("Title") == {
        "role": "heading", "level": 1, "original": "Title",
    }


def test_normalize_style_name_list_and_bullet():
    assert normalize_style_name("List Bullet") == {
        "role": "list", "original": "List Bullet",
    }


def test_normalize_style_name_bullet_only_keyword():
    # Isolates the "'bullet' in style_lower" alternative from "'list' in
    # style_lower" -- this style name has no substring "list" at all, unlike
    # the "List Bullet" fixture above which satisfies both keywords at once
    # and so cannot tell them apart.
    assert normalize_style_name("Bullet") == {"role": "list", "original": "Bullet"}


def test_normalize_style_name_normal():
    assert normalize_style_name("Normal") == {"role": "normal", "original": "Normal"}


def test_normalize_style_name_unknown_is_custom():
    assert normalize_style_name("SomeExoticStyle") == {
        "role": "custom", "original": "SomeExoticStyle",
    }


# --------------------------------------------------------------------------
# create_simplified_layout_json
# --------------------------------------------------------------------------


def _simplified_fixture_structure():
    return {
        "elements": [
            {
                "idx": 0, "type": "paragraph", "text": "Section Heading",
                "style": "Heading 1", "list_level": None, "bold": False,
                "indent_left": 0.0,
            },
            {"idx": 1, "type": "empty", "text": "", "is_empty": True},
            {
                "idx": 2, "type": "paragraph", "text": "Bold indented text",
                "style": "Normal", "list_level": 2, "bold": True,
                "indent_left": 0.5,
            },
            {
                "idx": 3, "type": "table", "rows": 2, "cols": 2,
                "data": [
                    [{"text": "R0C0"}, {"text": "R0C1"}],
                    [{"text": "R1C0"}, {"text": "R1C1"}],
                ],
            },
        ]
    }


def test_create_simplified_layout_json_skip_empty_true():
    simplified = create_simplified_layout_json(_simplified_fixture_structure(), skip_empty=True)

    idxs = [e["idx"] for e in simplified]
    assert 1 not in idxs  # the empty paragraph is dropped entirely

    heading = simplified[0]
    assert heading == {"idx": 0, "text": "Section Heading", "role": "heading", "level": 1}

    bold_para = next(e for e in simplified if e["idx"] == 2)
    assert bold_para["role"] == "normal"
    assert bold_para["bold"] is True
    assert bold_para["list_level"] == 2
    assert bold_para["indent"] == 0.5


def test_create_simplified_layout_json_skip_empty_false_keeps_placeholder():
    simplified = create_simplified_layout_json(_simplified_fixture_structure(), skip_empty=False)

    placeholder = next(e for e in simplified if e["idx"] == 1)
    assert placeholder == {"idx": 1, "type": "empty"}


def test_create_simplified_layout_json_table_preview_is_first_row():
    simplified = create_simplified_layout_json(_simplified_fixture_structure(), skip_empty=True)

    table_elem = next(e for e in simplified if e["idx"] == 3)
    assert table_elem["type"] == "table"
    assert table_elem["rows"] == 2
    assert table_elem["cols"] == 2
    assert table_elem["preview"] == [{"text": "R0C0"}, {"text": "R0C1"}]


def test_create_simplified_layout_json_raises_on_an_unhandled_element_type():
    # #614: a type it has no branch for used to be dropped with no trace.
    structure = {"elements": [{"idx": 0, "type": "table_content", "text": "Row"}]}
    with pytest.raises(ValueError, match="table_content"):
        create_simplified_layout_json(structure)


def _reader_view(docx_path):
    """(element text, per-cell data) for every element, plus the
    extract_text_from_docx lines -- both fields an LLM reader can see."""
    from unified_pipeline.segmentation.chunked_chat_hierarchy_extractor import (
        extract_text_from_docx,
    )
    elements = extract_unified_elements(str(docx_path))["elements"]
    view = [(e.get("text", ""), [[c.get("text", "") for c in row] for row in e.get("data") or []])
            for e in elements]
    return view, extract_text_from_docx(str(docx_path))


# #847 residual: a blank "Date of Birth:" label never reaches into a next
# paragraph or cell below that does not OPEN with its value. Each expected
# view is origin/dev's output for the same synthetic docx.
def test_next_paragraph_scrub_leaves_a_labelled_or_prose_date_alone(tmp_path):
    for idx, following in enumerate(
        ["Date of Appointment: 07/01/2005", "Appointed Assistant Professor 07/01/2005"]
    ):
        doc = Document()
        doc.add_paragraph("Date of Birth:")
        doc.add_paragraph(following)
        docx_path = tmp_path / f"next_paragraph_control_{idx}.docx"
        doc.save(str(docx_path))
        assert _reader_view(docx_path) == (
            [("Date of Birth:", []), (following, [])], ["Date of Birth:", following]
        )


@pytest.mark.parametrize("neighbour, below", [
    ("", "Appointed 07/01/2005"),
    ("Rank", "Date of Appointment: 07/01/2005"),
])
def test_cell_below_scrub_leaves_a_labelled_or_prose_date_alone(tmp_path, neighbour, below):
    doc = Document()
    table = doc.add_table(rows=2, cols=2)
    for (r, c), text in {(0, 0): "Date of Birth:", (0, 1): neighbour,
                         (1, 0): below, (1, 1): "Dept"}.items():
        table.cell(r, c).text = text
    docx_path = tmp_path / "cell_below_control.docx"
    doc.save(str(docx_path))
    flat = f"Date of Birth: | {neighbour}".rstrip() + f"\n{below} | Dept"
    assert _reader_view(docx_path) == (
        [(flat, [["Date of Birth:", neighbour], [below, "Dept"]])], [flat]
    )


def test_next_element_scrub_reaches_only_the_first_cell_of_a_table(tmp_path):
    doc = Document()
    doc.add_paragraph("Date of Birth:")
    table = doc.add_table(rows=2, cols=2)
    for (r, c), text in {(0, 0): "Appointed 07/01/2005", (0, 1): "07/01/2005",
                         (1, 0): "08/01/2010", (1, 1): "Promoted"}.items():
        table.cell(r, c).text = text
    docx_path = tmp_path / "next_element_table_control.docx"
    doc.save(str(docx_path))
    flat = "Appointed 07/01/2005 | 07/01/2005\n08/01/2010 | Promoted"
    assert _reader_view(docx_path) == (
        [("Date of Birth:", []),
         (flat, [["Appointed 07/01/2005", "07/01/2005"], ["08/01/2010", "Promoted"]])],
        ["Date of Birth:", flat],
    )


def test_next_element_scrub_takes_a_date_opening_a_table(tmp_path):
    doc = Document()
    doc.add_paragraph("Date of Birth:")
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "01/02/1970"
    table.cell(0, 1).text = "03/04/1999"
    docx_path = tmp_path / "next_element_table_positive.docx"
    doc.save(str(docx_path))
    flat = "[withheld] | 03/04/1999"
    assert _reader_view(docx_path) == (
        [("Date of Birth:", []), (flat, [["[withheld]", "03/04/1999"]])], ["Date of Birth:", flat]
    )


# #488: every table element's `text` is built with the same per-row join as the
# stage-2 row entries (join_row_cells), so the #418 whole-table-parent dedup
# finds the parent's lines in those rows.
@pytest.mark.parametrize("header_rows", [
    [],
    [["Honors and Awards", ""]],
    [["Honors and Awards", ""], ["Teaching", ""]],
])
def test_table_element_text_uses_the_stage2_row_join(tmp_path, header_rows):
    body = [["Visiting Program\n- Training course", "Fictional City", "2024 to present"],
            ["Another Entry", "Imaginary Place", "2020 to 2022"]]
    rows = header_rows + body
    doc = Document()
    table = doc.add_table(rows=len(rows), cols=3)
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            table.cell(r, c).text = text
    docx_path = tmp_path / "row_join.docx"
    doc.save(str(docx_path))

    elements = [e for e in extract_unified_elements(str(docx_path))["elements"]
                if e["type"] in ("table", "table_content")]
    joined = "\n".join(e["text"] for e in elements)
    assert "Visiting Program | Fictional City | 2024 to present\n- Training course" in joined
    assert "Another Entry | Imaginary Place | 2020 to 2022" in joined


# --------------------------------------------------------------------------
# Orphan date rows (#259): a date column with more \n\n segments than the
# name column must not leave bare-date rows, and a multi-column row must not
# lose its date cell to the embedded-header branch.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "May 2019", "07/2008 \u2013 06/2013", "October Issue 2025", "October 13, 2016",
    "2024-2025", "08/2025 \u2013 Present", "Sept. 2019", "2020",
])
def test_is_date_only_text_accepts_date_expressions(text):
    assert _is_date_only_text(text)


@pytest.mark.parametrize("text", [
    "Johns Hopkins University, 1991",  # a year inside a name is not a date
    "Award 1991", "May", "no digits here", "",
])
def test_is_date_only_text_rejects_text_with_words_or_no_year(text):
    assert not _is_date_only_text(text)


def test_split_merged_cells_folds_overflow_dates_into_last_named_row():
    row = [
        {"text": "Lecture Alpha\n\nLecture Beta", "row": 1, "col": 0},
        {"text": "May 2019\n\nJune 2019\n\nJuly 2019\n\nAugust 2019", "row": 1, "col": 1},
    ]

    out = split_merged_cells_in_row(row)

    assert len(out) == 2
    assert [c["text"] for c in out[0]] == ["Lecture Alpha", "May 2019"]
    assert [c["text"] for c in out[1]] == [
        "Lecture Beta", "June 2019\nJuly 2019\nAugust 2019",
    ]
    # Metadata of the anchor row survives the fold.
    assert out[1][1]["row"] == 1 and out[1][1]["col"] == 1
    # No row is a date on its own.
    assert all(c["text"] for r in out for c in r[:1])


def test_split_merged_cells_fold_leaves_tail_rows_that_are_not_pure_dates():
    # The overflow row's date-column text ("To be determined") is not a date, so
    # it is a real value and the tail is not folded.
    row = [
        {"text": "Lecture Alpha\n\nLecture Beta", "row": 0, "col": 0},
        {"text": "May 2019\n\nJune 2019\n\nTo be determined", "row": 0, "col": 1},
    ]

    out = split_merged_cells_in_row(row)

    assert [[c["text"] for c in r] for r in out] == [
        ["Lecture Alpha", "May 2019"],
        ["Lecture Beta", "June 2019"],
        ["", "To be determined"],
    ]


def test_split_merged_cells_fold_keeps_overflow_row_with_a_non_date_cell_separate():
    # 3 columns: the overflow row ['', 'July 2019', 'Room 3'] has a date in cell 1
    # but real text ('Room 3') in cell 2, so it is not date-only and must stay
    # its own row. Folding on "any cell is a date" would merge 'Room 3' into
    # the anchor row.
    row = [
        {"text": "Lecture Alpha\n\nLecture Beta", "row": 0, "col": 0},
        {"text": "May 2019\n\nJune 2019\n\nJuly 2019", "row": 0, "col": 1},
        {"text": "Room 1\n\nRoom 2\n\nRoom 3", "row": 0, "col": 2},
    ]

    out = split_merged_cells_in_row(row)

    assert [[c["text"] for c in r] for r in out] == [
        ["Lecture Alpha", "May 2019", "Room 1"],
        ["Lecture Beta", "June 2019", "Room 2"],
        ["", "July 2019", "Room 3"],
    ]


def test_split_merged_cells_fold_does_not_touch_single_cell_rows():
    # No blank sibling cell -> not an orphan date row, so "MBA" / "1998" stay
    # two rows exactly as before.
    row = [{"text": "MBA\n\n1998", "row": 0, "col": 0}]

    out = split_merged_cells_in_row(row)

    assert [r[0]["text"] for r in out] == ["MBA", "1998"]


def test_split_merged_cells_fold_keeps_role_held_over_two_stints_as_two_rows():
    # #886: a single (unsplit) role cell beside a two-stint date cell is a
    # deliberate continuation row -- its blank cell is not split padding, so the
    # second date is not an orphan and must not be folded into the first.
    row = [
        {"text": "Research Fellow", "row": 0, "col": 0},
        {"text": "2019-2020\n\n2021-2022", "row": 0, "col": 1},
    ]

    out = split_merged_cells_in_row(row)

    assert [[c["text"] for c in r] for r in out] == [
        ["Research Fellow", "2019-2020"], ["", "2021-2022"],
    ]


def test_fold_orphan_date_tail_needs_a_named_row_to_fold_into():
    # Every row is date-only (blank cell 0 is split padding), so there is no
    # anchor row: nothing to fold into, rows are returned as given.
    rows = [
        [{"text": "", "col": 0}, {"text": "2019", "col": 1}],
        [{"text": "", "col": 0}, {"text": "2020", "col": 1}],
    ]

    assert _fold_orphan_date_tail(rows, {0}) == rows


def test_fold_orphan_date_tail_into_an_empty_anchor_cell_adds_no_leading_newline():
    rows = [
        [{"text": "Lecture Alpha", "col": 0}, {"text": "", "col": 1}],
        [{"text": "", "col": 0}, {"text": "2019", "col": 1}],
        [{"text": "", "col": 0}, {"text": "2020", "col": 1}],
    ]

    out = _fold_orphan_date_tail(rows, {0})

    assert out == [[{"text": "Lecture Alpha", "col": 0}, {"text": "2019\n2020", "col": 1}]]


def test_extract_unified_elements_multicolumn_row_with_embedded_header_keeps_dates(tmp_path):
    # Row 1's first cell holds several \n\n-separated activities, one of which
    # ("Clinical Teaching") reads like a section header; its second cell holds the
    # dates. The embedded-header branch used to emit only cell 0 and drop every
    # date; the row must instead fall through to the merged-cell split.
    doc = Document()
    t = doc.add_table(rows=2, cols=2)
    t.cell(0, 0).text = "Didactic Teaching"
    t.cell(0, 1).text = "Dates"
    t.cell(1, 0).text = (
        "Lecturer, Anatomy Course, Example University, Example City\n\n"
        "Clinical Teaching\nWeekly review of cases with residents\n\n"
        "Course Director, Pathology Course, Example Medical Center, Example City"
    )
    t.cell(1, 1).text = "March 2011\n\nJune 2012\n\nOctober 2013"
    docx_path = tmp_path / "embedded_header_multicol.docx"
    doc.save(str(docx_path))

    elements = extract_unified_elements(str(docx_path))["elements"]

    content = " ".join(e["text"] for e in elements if e["type"] == "table_content")
    for date in ("March 2011", "June 2012", "October 2013"):
        assert date in content
    assert "Lecturer, Anatomy Course" in content
    # Judgement call (#259): a multi-column row no longer takes the embedded-header
    # branch, so the embedded "Clinical Teaching" is NOT emitted as a table_header;
    # it stays as text in a content row. Pinned so a change is deliberate.
    headers = [e["text"] for e in elements if e["type"] == "table_header"]
    assert "Clinical Teaching" not in headers
    assert "Clinical Teaching" in content


def test_extract_unified_elements_single_cell_embedded_header_still_splits(tmp_path):
    # Guard is scoped to multi-column rows: single-content-cell rows keep the
    # embedded-header behaviour (an embedded header becomes a table_header).
    doc = Document()
    t = doc.add_table(rows=2, cols=2)
    t.cell(0, 0).text = "Research Summary"
    t.cell(0, 1).text = ""
    t.cell(1, 0).text = "Some prose about the work.\n\nEducation and Degrees\n2005 BS Example University"
    t.cell(1, 1).text = ""
    docx_path = tmp_path / "embedded_header_single.docx"
    doc.save(str(docx_path))

    elements = extract_unified_elements(str(docx_path))["elements"]

    assert "Education and Degrees" in [e["text"] for e in elements if e["type"] == "table_header"]


# --------------------------------------------------------------------------
# Column-header row flag (#424). All text below is synthetic.
# --------------------------------------------------------------------------


def _two_row_table(header_cells, data_cells):
    doc = Document()
    table = doc.add_table(rows=2, cols=len(header_cells))
    for col, text in enumerate(header_cells):
        table.cell(0, col).text = text
    for col, text in enumerate(data_cells):
        table.cell(1, col).text = text
    return doc, table


def _mark_repeat_header(table, val=None):
    attr = "" if val is None else f' w:val="{val}"'
    tr_pr = table.rows[0]._tr.get_or_add_trPr()
    tr_pr.append(parse_xml(f"<w:tblHeader {nsdecls('w')}{attr}/>"))


def test_column_label_row_is_flagged_as_header_row():
    _, table = _two_row_table(
        ["Title", "Institution/Location", "Dates"], ["Example Role", "Example Org", "Example"]
    )
    assert extract_table_metadata(table, "t")["header_row"] is True


def test_word_repeat_header_row_is_flagged_even_without_label_vocabulary():
    _, table = _two_row_table(["Alpha", "Beta"], ["Gamma", "Delta"])
    assert "header_row" not in extract_table_metadata(table, "t")
    _mark_repeat_header(table)
    assert extract_table_metadata(table, "t")["header_row"] is True


def test_word_repeat_header_switched_off_is_not_a_header_marker():
    _, table = _two_row_table(["Alpha", "Beta"], ["Gamma", "Delta"])
    _mark_repeat_header(table, val="0")
    assert "header_row" not in extract_table_metadata(table, "t")


@pytest.mark.parametrize(
    "header_cells",
    [
        ["Role", "Organization", "Dates 2019"],  # a digit means a record
        ["Name:", "Example"],  # label|value form row: 50% vocabulary, still a value row
        ["Title", ""],  # a single filled cell
        ["Example Person", "Example Lab"],  # no label vocabulary
    ],
)
def test_record_like_row_zero_is_not_flagged(header_cells):
    _, table = _two_row_table(header_cells, ["x"] * len(header_cells))
    assert "header_row" not in extract_table_metadata(table, "t")


def test_two_word_committee_chair_row_zero_is_the_known_false_positive():
    # 50% column-label vocabulary, two filled cells, no digit: flagged. The same
    # residual class #736's appendix filter documents; pinned so a change to
    # the rule is visible.
    _, table = _two_row_table(["Committee", "Chair"], ["x", "y"])
    assert extract_table_metadata(table, "t")["header_row"] is True


def test_one_row_table_is_never_flagged():
    doc = Document()
    table = doc.add_table(rows=1, cols=3)
    for col, text in enumerate(["Title", "Institution", "Dates"]):
        table.cell(0, col).text = text
    assert "header_row" not in extract_table_metadata(table, "t")


def test_unified_no_header_table_keeps_flagged_row_zero_unsplit_and_marked(tmp_path):
    # Row 0 would split on "\n\n" as a data row; flagged, it stays one row at
    # index 0 so stage 2's skip lands on it. The element carries the flag.
    doc = Document()
    t = doc.add_table(rows=2, cols=2)
    t.cell(0, 0).text = "Title\n\nInstitution"
    t.cell(0, 1).text = "Dates\n\nLocation"
    t.cell(1, 0).text = "Example Award"
    t.cell(1, 1).text = "Example Org"
    path = tmp_path / "flagged.docx"
    doc.save(str(path))

    elements = extract_unified_elements(str(path))["elements"]

    assert [e["type"] for e in elements] == ["table"]
    assert elements[0]["header_row"] is True
    assert elements[0]["rows"] == 2
    assert [c["text"] for c in elements[0]["data"][0]] == ["Title\n\nInstitution", "Dates\n\nLocation"]


def test_unified_unflagged_table_element_has_no_header_row_key(tmp_path):
    docx_path = tmp_path / "unified_fixture.docx"
    _build_unified_fixture_docx(docx_path)
    elements = extract_unified_elements(str(docx_path))["elements"]
    assert all("header_row" not in e for e in elements)


def test_main_writes_both_json_files_as_readable_utf8(tmp_path, monkeypatch):
    from unified_pipeline.core import docx_structure_extractor as mod

    doc = Document()
    doc.add_paragraph("José Muñoz")
    docx_path = tmp_path / "cv.docx"
    doc.save(str(docx_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["docx_structure_extractor.py", str(docx_path)])

    mod.main()

    for name in ("cv_structure.json", "cv_layout.json"):
        raw = (tmp_path / name).read_bytes().decode("utf-8")
        assert "José Muñoz" in raw
        assert "\\u00e9" not in raw
        assert json.loads(raw)
