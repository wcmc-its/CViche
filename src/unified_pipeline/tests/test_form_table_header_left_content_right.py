"""#811 round 2: three regressions in the round-1 fix for form-style
label|value table rows, found by review (`verify-C-811`, findings 1/3/4/5).

Round 1 made ANY row whose first cell scores as header-like AND has a
non-blank trailing cell into content-only, dropping the header signal. That
over-fired on three shapes round 1's own test suite never exercised:

1. A horizontally merged (gridSpan) single-cell header row at index >= 1.
   python-docx repeats a gridSpan cell once per spanned column, so
   `row.cells` returns the SAME cell 2-3x -- round 1's predicate read that
   as "a label with a non-blank value cell" and demoted a real section
   header (e.g. "GRANTS" spanning 3 columns) to content ("GRANTS | GRANTS |
   GRANTS"). Fixed by deduping `row.cells` by `_tc` identity inside
   `extract_table_metadata`.
2. A "header-left / content-right" row: a REAL section header (all-caps,
   no colon, e.g. "MAILING ADDRESS") with distinct content in a trailing
   cell. Round 1 demoted it to content-only, losing the header signal
   entirely. Now it keeps the `table_header` AND has its content recovered.
   A colon-terminated form label (round 1's actual target shape, e.g.
   "Name:") stays content-only.
3. The table-level row-0 header check (a different code path from the
   per-row walk) never got round 1's fix at all: a 2-column `NAME: | value`
   row-0 stayed a `table_header` with the value dropped.

Also covers the round-1 test-coverage gap where a whitespace-only trailing
cell was never distinguished from a genuinely blank one.

Deterministic, no LLM, no DB, no network, no PII (synthetic values only).
Run with:

    python3 -m pytest src/unified_pipeline/tests/test_form_table_header_left_content_right.py -p no:cacheprovider
"""
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]  # src/unified_pipeline
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from docx import Document  # noqa: E402

from core.docx_structure_extractor import extract_unified_elements  # noqa: E402


def _build_merged_subheader_docx(path: str) -> None:
    """Row 0: single-cell header "SECTION A" (enters the table-level header
    path). Row 1: a 3-column row horizontally merged into ONE cell, "GRANTS"
    -- python-docx repeats this cell 3x in `row.cells`. Row 2: an ordinary
    3-column content row so the table has something after the merged row."""
    doc = Document()
    table = doc.add_table(rows=3, cols=3)

    header_cell = table.cell(0, 0).merge(table.cell(0, 1)).merge(table.cell(0, 2))
    header_cell.text = "SECTION A"

    merged_row1 = table.cell(1, 0).merge(table.cell(1, 1)).merge(table.cell(1, 2))
    merged_row1.text = "GRANTS"

    table.cell(2, 0).text = "2020"
    table.cell(2, 1).text = "Synthetic Award"
    table.cell(2, 2).text = "Synthetic Institution"

    doc.save(path)


def test_merged_gridspan_subheader_row_stays_table_header(tmp_path):
    """Positive case: a horizontally merged single-cell row at index >= 1
    ("GRANTS", spanning 3 columns) stays a table_header -- the gridSpan
    duplication must not be misread as a label|value form row."""
    docx_path = str(tmp_path / "merged_subheader.docx")
    _build_merged_subheader_docx(docx_path)

    elements = extract_unified_elements(docx_path)["elements"]
    table_headers = [e for e in elements if e.get("type") == "table_header"]
    table_contents = [e for e in elements if e.get("type") == "table_content"]
    content_text = "\n".join(e["text"] for e in table_contents)

    assert any(e["text"] == "GRANTS" for e in table_headers)
    # The dedup must not turn "GRANTS" into a duplicated-value content row.
    assert "GRANTS | GRANTS" not in content_text


def _build_header_left_content_right_docx(path: str) -> None:
    """Row 0: single-cell header "SECTION B". Row 1: a real section header
    with distinct content on the right ("MAILING ADDRESS" | address -- no
    colon, high confidence). Row 2: a colon-terminated form label with
    distinct content (the round-1 shape) for contrast, must stay
    content-only. Row 3: a colon-terminated ALL-CAPS label with distinct
    content, to prove the colon check (not casing) drives the split."""
    doc = Document()
    table = doc.add_table(rows=4, cols=2)

    header_cell = table.cell(0, 0).merge(table.cell(0, 1))
    header_cell.text = "SECTION B"

    table.cell(1, 0).text = "MAILING ADDRESS"
    table.cell(1, 1).text = "1 Synthetic Way"

    table.cell(2, 0).text = "Office Phone:"
    table.cell(2, 1).text = "555-0100"

    table.cell(3, 0).text = "CONTACT PHONE:"
    table.cell(3, 1).text = "555-0199"

    doc.save(path)


def test_header_left_content_right_row_keeps_header_and_recovers_content(tmp_path):
    """Positive case: "MAILING ADDRESS" (no colon, confidence 0.6 >=
    HEADER_LEFT_CONTENT_RIGHT_MIN_CONFIDENCE) keeps its table_header AND its
    value cell is recovered into table_content."""
    docx_path = str(tmp_path / "header_left_content_right.docx")
    _build_header_left_content_right_docx(docx_path)

    elements = extract_unified_elements(docx_path)["elements"]
    table_headers = [e for e in elements if e.get("type") == "table_header"]
    table_contents = [e for e in elements if e.get("type") == "table_content"]
    content_text = "\n".join(e["text"] for e in table_contents)

    assert any(e["text"] == "MAILING ADDRESS" for e in table_headers)
    assert "1 Synthetic Way" in content_text


def _build_duplicate_text_two_cells_docx(path: str) -> None:
    """Two DISTINCT (unmerged) cells that happen to carry the SAME text --
    not a gridSpan duplicate, just a row whose "value" cell repeats the
    label. Must not be read as distinct right-hand content."""
    doc = Document()
    table = doc.add_table(rows=2, cols=2)

    header_cell = table.cell(0, 0).merge(table.cell(0, 1))
    header_cell.text = "SECTION D"

    table.cell(1, 0).text = "MAILING ADDRESS"
    table.cell(1, 1).text = "MAILING ADDRESS"  # distinct cell, identical text

    doc.save(path)


def test_duplicate_text_in_a_distinct_cell_is_not_recovered_content(tmp_path):
    """Negative case: a trailing cell that is a genuinely separate cell (not
    a gridSpan duplicate) but repeats the label's own text must NOT satisfy
    the header-left/content-right "distinct content" requirement -- the row
    stays content-only, same as any other label|value row with no real
    value, rather than emitting a bogus "MAILING ADDRESS" header paired with
    duplicated "MAILING ADDRESS" content."""
    docx_path = str(tmp_path / "duplicate_text_two_cells.docx")
    _build_duplicate_text_two_cells_docx(docx_path)

    elements = extract_unified_elements(docx_path)["elements"]
    table_headers = [e for e in elements if e.get("type") == "table_header"]

    assert not any(e["text"] == "MAILING ADDRESS" for e in table_headers)


def _build_below_confidence_floor_docx(path: str) -> None:
    """A non-colon, mixed-case label whose looks_like_section_header
    confidence is 0.5 (verified directly against the function) -- above the
    0.4 "is a header at all" threshold, but below
    HEADER_LEFT_CONTENT_RIGHT_MIN_CONFIDENCE (0.6). "Prior appointments held"
    scores: keyword substring "appointments" (+0.4) + short/1-5-words
    (+0.1) = 0.5; it is not ALL CAPS and not every word is Title-Case, so
    neither of those bonuses apply."""
    doc = Document()
    table = doc.add_table(rows=2, cols=2)

    header_cell = table.cell(0, 0).merge(table.cell(0, 1))
    header_cell.text = "SECTION E"

    table.cell(1, 0).text = "Prior appointments held"
    table.cell(1, 1).text = "Distinct Value Text"

    doc.save(path)


def test_below_confidence_floor_row_stays_content_only(tmp_path):
    """Negative case: "Prior appointments held" (no colon, confidence 0.5 <
    the 0.6 HEADER_LEFT_CONTENT_RIGHT_MIN_CONFIDENCE floor) must NOT take
    the header-left/content-right path -- it stays content-only like any
    other round-1 label|value row, proving the confidence floor (not just
    the colon check) gates the new path."""
    docx_path = str(tmp_path / "below_confidence_floor.docx")
    _build_below_confidence_floor_docx(docx_path)

    elements = extract_unified_elements(docx_path)["elements"]
    table_headers = [e for e in elements if e.get("type") == "table_header"]
    table_contents = [e for e in elements if e.get("type") == "table_content"]
    content_text = "\n".join(e["text"] for e in table_contents)

    assert not any(e["text"] == "Prior appointments held" for e in table_headers)
    assert "Prior appointments held" in content_text
    assert "Distinct Value Text" in content_text


def test_colon_terminated_label_stays_content_only_even_if_all_caps(tmp_path):
    """Negative case: "CONTACT PHONE:" is ALL CAPS with a high confidence
    score, but it ends in a colon -- a form label, not a real header, so it
    must stay content-only (no table_header emitted for it), same as the
    lowercase-initial "Office Phone:" case."""
    docx_path = str(tmp_path / "header_left_content_right.docx")
    _build_header_left_content_right_docx(docx_path)

    elements = extract_unified_elements(docx_path)["elements"]
    table_headers = [e for e in elements if e.get("type") == "table_header"]
    table_contents = [e for e in elements if e.get("type") == "table_content"]
    content_text = "\n".join(e["text"] for e in table_contents)

    assert not any(e["text"] == "CONTACT PHONE:" for e in table_headers)
    assert not any(e["text"] == "Office Phone:" for e in table_headers)
    assert "CONTACT PHONE:" in content_text
    assert "555-0199" in content_text
    assert "Office Phone:" in content_text
    assert "555-0100" in content_text


def _build_row0_form_label_docx(path: str) -> None:
    """Row 0 (table-level header path): "NAME:" | value -- a form label, not
    a real table header. Row 1: a normal 2-column content row for contrast."""
    doc = Document()
    table = doc.add_table(rows=2, cols=2)

    table.cell(0, 0).text = "NAME:"
    table.cell(0, 1).text = "Synthetic Person"

    table.cell(1, 0).text = "Office Address:"
    table.cell(1, 1).text = "123 Synthetic Ave"

    doc.save(path)


def test_row_zero_form_label_is_content_not_table_header(tmp_path):
    """Positive case: a table's OWN row 0 ("NAME:" | value) is a form label,
    not a table header -- the table-level header check (a different code
    path from the per-row walk) must apply the same rule."""
    docx_path = str(tmp_path / "row0_form_label.docx")
    _build_row0_form_label_docx(docx_path)

    elements = extract_unified_elements(docx_path)["elements"]
    table_headers = [e for e in elements if e.get("type") == "table_header"]
    table_contents = [e for e in elements if e.get("type") == "table_content"]
    content_text = "\n".join(e["text"] for e in table_contents)

    assert not any(e["text"] == "NAME:" for e in table_headers)
    assert "NAME:" in content_text
    assert "Synthetic Person" in content_text
    # Row 1 ("Office Address:") must still be recovered as content too.
    assert "Office Address:" in content_text
    assert "123 Synthetic Ave" in content_text


def _build_row0_header_left_content_right_docx(path: str) -> None:
    """Row 0 is itself a real, non-colon section header with distinct
    content on the right ("STAFF POSITION" | title, confidence 0.6) -- out
    of this ticket's scope (only row-0 FORM LABELS get the new content
    treatment). It must keep today's existing table-level header behavior
    unchanged: table_header emitted for row 0, its own trailing content not
    separately recovered."""
    doc = Document()
    table = doc.add_table(rows=2, cols=2)

    table.cell(0, 0).text = "STAFF POSITION"
    table.cell(0, 1).text = "Distinguished Fellow"

    table.cell(1, 0).text = "Office Address:"
    table.cell(1, 1).text = "123 Synthetic Ave"

    doc.save(path)


def test_row_zero_header_left_content_right_shape_is_unaffected(tmp_path):
    """Negative case: row 0 being a real (non-colon, high-confidence)
    section header with distinct trailing content must NOT be reclassified
    as a form label -- it stays a table_header exactly as before this
    ticket, since row-0 header-left/content-right recovery is out of scope."""
    docx_path = str(tmp_path / "row0_header_left_content_right.docx")
    _build_row0_header_left_content_right_docx(docx_path)

    elements = extract_unified_elements(docx_path)["elements"]
    table_headers = [e for e in elements if e.get("type") == "table_header"]

    assert any(e["text"] == "STAFF POSITION" for e in table_headers)


def _build_row0_multiline_embedded_header_docx(path: str) -> None:
    """Regression shape found by the corpus scan (web206): row 0's cell 0 is
    a real, non-colon header whose looks_like_section_header confidence is
    BELOW the header-left/content-right floor (0.4 <= conf < 0.6) -- "Senior
    research fellow" (verified directly against the function: 0.5, same
    band as web206's actual "Assistant Professor of Instruction"). Its cell
    also contains an embedded "\\n\\n" second job title further down, and
    row 0's OTHER cell carries a non-blank date range. `row0_is_form_label`
    must stay False here (no colon) so row 0 is NOT rerouted into the
    per-row walk's separate `\\n\\n`-embedded-header splitter, which builds
    single-cell synthetic rows and would silently drop the date range in
    cell 1."""
    doc = Document()
    table = doc.add_table(rows=2, cols=2)

    table.cell(0, 0).text = (
        "Senior research fellow\n"
        "Department of Synthetic Studies\n\n"
        "Junior research fellow"
    )
    table.cell(0, 1).text = "2022-Present\n\nFall 2020"

    table.cell(1, 0).text = "Office Address:"
    table.cell(1, 1).text = "123 Synthetic Ave"

    doc.save(path)


def test_row0_low_confidence_header_keeps_its_other_cell(tmp_path):
    """Negative case (regression guard, #811 round 2 web206): a row-0 header
    below the header-left/content-right confidence floor, with an embedded
    "\\n\\n" second header in the same cell, must not lose row 0's OTHER
    cell (the date range) -- it must appear somewhere in the element
    stream, not be silently dropped by the per-row walk's embedded-header
    splitter."""
    docx_path = str(tmp_path / "row0_multiline_embedded_header.docx")
    _build_row0_multiline_embedded_header_docx(docx_path)

    elements = extract_unified_elements(docx_path)["elements"]
    all_text = "\n".join(e.get("text", "") for e in elements)

    assert "Senior research fellow" in all_text
    assert "2022-Present" in all_text
    assert "Fall 2020" in all_text


def _build_whitespace_only_value_docx(path: str) -> None:
    doc = Document()
    table = doc.add_table(rows=2, cols=2)

    header_cell = table.cell(0, 0).merge(table.cell(0, 1))
    header_cell.text = "SECTION C"

    table.cell(1, 0).text = "Certification:"
    table.cell(1, 1).text = "   "  # whitespace-only, not truly blank

    doc.save(path)


def test_whitespace_only_value_cell_stays_table_header(tmp_path):
    """Negative case: a trailing cell containing only whitespace must be
    treated the same as a blank cell -- "Certification:" stays a
    table_header (kills the mutant that drops `.strip()` in
    row_has_nonblank_value_cells)."""
    docx_path = str(tmp_path / "whitespace_value.docx")
    _build_whitespace_only_value_docx(docx_path)

    elements = extract_unified_elements(docx_path)["elements"]
    table_headers = [e for e in elements if e.get("type") == "table_header"]
    table_contents = [e for e in elements if e.get("type") == "table_content"]
    content_text = "\n".join(e["text"] for e in table_contents)

    assert any(e["text"] == "Certification:" for e in table_headers)
    assert "Certification:" not in content_text
