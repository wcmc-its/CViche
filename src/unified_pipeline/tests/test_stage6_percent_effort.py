"""J. PERCENT EFFORT AND INSTITUTIONAL RESPONSIBILITIES (#260).

`_fill_percent_effort` writes parsed source-table rows into the template's
five FIXED rows (Teaching, Clinical, Administrative, Research, Total),
matched by label, never adding or clearing rows. J has no taxonomy code of
its own on the live path, so its source table -- including its own
column-header row and its `Total | 100% |` row -- reaches stage 3b as
ordinary T-coded entries sharing the J hierarchy (confirmed against two real
S3 runs). A coarser "all cells are template labels" filter would drop
`Total | 100% |` (#260's own recorded trap), so the header row is recognized
by a POSITIVE signature instead (an unmapped activity cell plus row text
naming the columns), and everything else is matched by activity synonym.

Most tests here drive the real `generate()` path against the committed WCM
template, the same way test_stage6_passthrough_appendix_exclusion.py does
for E/G (#294) -- so the Appendix-exclusion wiring is under test, not just
the parser. The one exception is the "no J table" case, which needs a
template `generate()` can't produce (the committed template always has one),
so it drives `_fill_percent_effort` directly against a synthetic document,
the same way test_stage6_employment_status_routing.py's stub-generator tests
do for E.

Synthetic entries only, no PII. Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_percent_effort.py -p no:cacheprovider
"""

import json
import logging
import sys
from pathlib import Path

import docx
from docx import Document

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage6.normalization import _clean_inline_tabs  # noqa: E402
from unified_pipeline.stage6.sections.passthrough import PassthroughSection  # noqa: E402
from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402

_HIERARCHY = ["J. PERCENT EFFORT AND INSTITUTIONAL RESPONSIBILITIES"]
_APPENDIX_HEADER = "T. APPENDIX"

_OWNER_ENTRY = {
    "text": "Name: Jane Q. Public, MD", "taxonomy_code": "A",
    "extracted_fields": {}, "element_idx_start": 0,
}


def _j_entry(text: str, idx: int) -> dict:
    return {
        "text": text, "taxonomy_code": "T", "hierarchy": _HIERARCHY,
        "extracted_fields": {}, "element_idx_start": idx,
    }


def _full_text(doc) -> str:
    parts = [p.text for p in doc.paragraphs]
    parts += [c.text for tb in doc.tables for row in tb.rows for c in row.cells]
    return "\n".join(parts)


def _appendix_text(doc) -> str:
    """Same exact-paragraph-match approach as
    test_stage6_passthrough_appendix_exclusion.py -- appendix entries are
    always paragraphs, never table cells, so this can't be fooled by the J
    table (which renders earlier in the document than the appendix)."""
    paragraphs = [p.text for p in doc.paragraphs]
    for i, text in enumerate(paragraphs):
        if text.strip() == _APPENDIX_HEADER:
            return "\n".join(paragraphs[i:])
    return ""


def _find_j_table(doc):
    for table in doc.tables:
        if not table.rows:
            continue
        header_cells = [c.text.upper() for c in table.rows[0].cells]
        if any("PERCENT EFFORT" in c for c in header_cells):
            return table
    return None


def _j_rows_by_label(doc) -> dict[str, list[str]]:
    table = _find_j_table(doc)
    assert table is not None, "J table not found in rendered document"
    return {row.cells[0].text.strip(): [c.text for c in row.cells] for row in table.rows}


def _render(tmp_path, entries) -> Document:
    gen = WCMTemplateGenerator(verbose=False)
    # Neutralize the LLM-driven appendix-reconsider pass, same as the E/G
    # and M1 appendix tests: deterministic, credential-free.
    gen._reconsider_appendix_entries = lambda: None
    data = {"document_uid": "TESTJE", "entries": entries}
    input_path = tmp_path / "in.json"
    output_path = tmp_path / "out.docx"
    input_path.write_text(json.dumps(data))
    gen.generate(str(input_path), str(output_path), research_summary_path=None)
    return Document(str(output_path))


def test_percent_cell_rows_render_by_label_and_synonym_not_duplicated(tmp_path):
    """Confirmed real-run shapes with a '%' cell, one per synonym class."""
    entries = [
        _OWNER_ENTRY,
        _j_entry("Teaching | 10% | Yes", 1),
        _j_entry("Clinical Care | 70% | Yes", 2),          # synonym -> Clinical
        _j_entry("Administration | 5% | No", 3),            # synonym -> Administrative
        _j_entry("Research | 15% | Yes", 4),
    ]
    doc = _render(tmp_path, entries)
    rows = _j_rows_by_label(doc)

    assert rows["Teaching"] == ["Teaching", "10%", "Yes"]
    assert rows["Clinical"] == ["Clinical", "70%", "Yes"]
    assert rows["Administrative"] == ["Administrative", "5%", "No"]
    assert rows["Research"] == ["Research", "15%", "Yes"]

    # The Appendix renders pipe-joined cells with " — ", not raw "|"
    # (`_clean_inline_tabs`), so compare against that rendered form -- the
    # raw text is never present either way and would be a vacuous check.
    appendix = _appendix_text(doc)
    for source_text in ("Teaching | 10% | Yes", "Clinical Care | 70% | Yes",
                        "Administration | 5% | No", "Research | 15% | Yes"):
        rendered = _clean_inline_tabs(source_text)
        assert rendered not in appendix, f"{rendered!r} duplicated into the Appendix"


def test_bare_integer_rows_render_with_percent_sign_appended(tmp_path):
    """Confirmed real-run shapes with no '%' cell at all."""
    entries = [
        _OWNER_ENTRY,
        _j_entry("Teaching | 5 | Yes", 1),
        _j_entry("Clinical | 40 | Yes", 2),
        _j_entry("Administrative | 15 | Yes", 3),
        _j_entry("Research | 40 | No", 4),
    ]
    doc = _render(tmp_path, entries)
    rows = _j_rows_by_label(doc)

    assert rows["Teaching"] == ["Teaching", "5%", "Yes"]
    assert rows["Clinical"] == ["Clinical", "40%", "Yes"]
    assert rows["Administrative"] == ["Administrative", "15%", "Yes"]
    assert rows["Research"] == ["Research", "40%", "No"]

    appendix = _appendix_text(doc)
    for source_text in ("Teaching | 5 | Yes", "Clinical | 40 | Yes",
                        "Administrative | 15 | Yes", "Research | 40 | No"):
        assert _clean_inline_tabs(source_text) not in appendix


def test_total_row_survives_and_is_not_duplicated(tmp_path):
    """#260's own recorded trap: a coarser filter ate this exact row."""
    entries = [_OWNER_ENTRY, _j_entry("Total | 100% |", 1)]
    doc = _render(tmp_path, entries)
    rows = _j_rows_by_label(doc)

    assert rows["Total"][:2] == ["Total", "100%"]
    assert _clean_inline_tabs("Total | 100% |") not in _appendix_text(doc)


def test_source_column_header_row_consumed_not_written_not_duplicated(tmp_path):
    """Both real-run header wordings: consumed (excluded from the Appendix)
    but nothing is written for them -- they are not CV content."""
    header_a = ("Weill Cornell Activity (Current or Anticipated) | "
                "Percent Effort (%) | Does the activity involve Weill Cornell "
                "students/researchers? (Yes/No)")
    header_b = ("Current percent effort | Percent effort % | Does the "
                "activity involve WMC students/researchers? (Yes/No)")
    entries = [_OWNER_ENTRY, _j_entry(header_a, 1), _j_entry(header_b, 2)]
    doc = _render(tmp_path, entries)

    appendix = _appendix_text(doc)
    assert _clean_inline_tabs(header_a) not in appendix, "header row duplicated into the Appendix"
    assert _clean_inline_tabs(header_b) not in appendix, "header row duplicated into the Appendix"

    # Nothing was written FOR the header rows: the table's own header cells
    # are exactly the template's original text, never overwritten.
    table = _find_j_table(doc)
    assert [c.text for c in table.rows[0].cells] == [
        "Weill Cornell Activity (Current or Anticipated)",
        "Percent Effort (%)",
        "Does the activity involve Weill Cornell students/research trainees? (Yes/No)",
    ]


def test_unknown_activity_row_not_written_stays_appendix_bound(tmp_path):
    entries = [_OWNER_ENTRY, _j_entry("Volunteer Work | 25% | No", 1)]
    doc = _render(tmp_path, entries)

    assert _clean_inline_tabs("Volunteer Work | 25% | No") in _appendix_text(doc)
    table = _find_j_table(doc)
    # No row was touched: every non-Total percent cell is still blank.
    for row in table.rows[1:]:
        if row.cells[0].text.strip() != "Total":
            assert row.cells[1].text == ""


class _StubGenerator(PassthroughSection):
    """Just enough of WCMTemplateGenerator to drive `_fill_percent_effort`
    directly, for the one case `generate()` can't produce: no J table at
    all (the committed template always has one)."""

    def __init__(self, doc):
        self.doc = doc
        self.verbose = False
        self.stats = {"entries_inserted": 0, "tables_populated": 0}

    def _find_paragraph_with_text(self, search_text):
        for i, para in enumerate(self.doc.paragraphs):
            if search_text.lower() in para.text.lower():
                return i
        return None

    def _find_header_paragraph(self, search_text):
        search = search_text.lower()
        for i, para in enumerate(self.doc.paragraphs):
            text = para.text.strip()
            if len(text) < 3 or search not in text.lower():
                continue
            if text.isupper() or (para.runs and para.runs[0].bold):
                return i
        return None

    def _find_table_after_paragraph(self, para_idx):
        from docx.table import Table
        target_elem = self.doc.paragraphs[para_idx]._element
        body_elements = list(self.doc.element.body)
        para_body_idx = body_elements.index(target_elem)
        for i in range(para_body_idx + 1, len(body_elements)):
            if body_elements[i].tag.endswith("tbl"):
                return Table(body_elements[i], self.doc)
        return None


def test_no_j_table_logs_and_skips_without_raising(caplog):
    doc = docx.Document()
    doc.add_paragraph("J. PERCENT EFFORT AND INSTITUTIONAL RESPONSIBILITIES")
    doc.add_paragraph("No table follows this header at all.")
    generator = _StubGenerator(doc)

    with caplog.at_level(logging.WARNING):
        consumed = generator._fill_percent_effort([_j_entry("Teaching | 10% | Yes", 1)])

    assert consumed == []
    assert generator.stats["entries_inserted"] == 0
