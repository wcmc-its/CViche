"""Regression tests for #550: the original-.docx personal-data fallback

recovers ``email``/``phone``/``address`` from the source document
(``personal_data.py:254-339`` on this ref) and then discards them -- the
PERSONAL DATA table fill reads only ``work_email``/``office_phone``/
``office_address``, and the fallback never wrote back into those names. AST
dataflow in the issue: last *store* to the three slot names is before the
fallback runs; last *load* of the fallback's own ``email``/``phone``/
``address`` locals is inside it. No write-back existed on either name.

Fix is the three ``x = x or recovered`` assignments immediately before the
table fill (``personal_data.py:357-367`` on this ref) -- an empty slot only,
never an overwrite of a value already classified from the entries.

    python3 -m pytest src/unified_pipeline/tests/test_stage6_personal_data_fallback_writeback.py -p no:cacheprovider

Positive control: a minimal .docx fixture holding the real 2054_Opresko_Cv
shape -- a "BUSINESS ADDRESS:" table cell whose value embeds "Phone:" /
"E-mail:" lines (confirmed by reading that source document directly; it is
NOT three separate table rows) -- with empty A-entries, so the only source
for Office address / Work email / Office telephone is the fallback.
Negative control: the same fixture, but the A-entries already carry a work
email / office phone / office address of their own; those must survive
untouched, and the fixture's different values must not appear anywhere.
Third test: a fallback parse failure (unparseable "original_doc_path") is no
longer silent -- it increments the new stats counter, and is no longer
gated behind ``verbose``.
"""

import json
import logging
import sys
from pathlib import Path

from docx import Document

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402

W_T = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"


def _make_source_docx(path: Path) -> None:
    """The 2054_Opresko_Cv shape, read directly off that source document:
    one table, row 0 is a NAME: label, row 1 is a BUSINESS ADDRESS: label
    whose value embeds Phone:/E-mail: lines the fallback pulls back out."""
    doc = Document()
    table = doc.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text = "NAME:"
    table.rows[0].cells[1].text = "Patricia Opresko"
    table.rows[1].cells[0].text = "BUSINESS ADDRESS:"
    table.rows[1].cells[1].text = (
        "University of Pittsburgh\n"
        "5117 Centre Avenue\n"
        "Pittsburgh, PA 15213\n"
        "Phone:\t412-623-7764\n"
        "E-mail:\tplo4@pitt.edu"
    )
    doc.save(str(path))


def _a(text, fields=None, idx=0):
    return {"text": text, "taxonomy_code": "A",
            "extracted_fields": fields or {}, "element_idx_start": idx}


def _personal_data_row_values(docx_path) -> dict:
    """{lowercased row-0 label: row-1 cell text} for the PERSONAL DATA table."""
    doc = Document(str(docx_path))
    for table in doc.tables:
        header_cells = " ".join(c.text for c in table.rows[0].cells).lower()
        if "work email" in header_cells or any(
                "work email" in row.cells[0].text.lower() for row in table.rows):
            return {row.cells[0].text.strip().lower(): row.cells[1].text.strip()
                    for row in table.rows}
    raise AssertionError("PERSONAL DATA table (Work email: row) not found in output")


def _render(tmp_path, entries, original_doc_path=None) -> dict:
    gen = WCMTemplateGenerator(verbose=False)
    gen._reconsider_appendix_entries = lambda: None
    ip, op = tmp_path / "in.json", tmp_path / "out.docx"
    ip.write_text(json.dumps({"document_uid": "TESTPDWB", "entries": entries}))
    gen.generate(str(ip), str(op), research_summary_path=None,
                original_doc_path=original_doc_path)
    return _personal_data_row_values(op), gen


# --------------------------------------------------------------------------
# positive control -- fails on dev: the fallback recovers the values and
# the table fill still reads only the never-written-to originals.
# --------------------------------------------------------------------------

def test_recovered_contact_fields_are_written_back_into_empty_slots(tmp_path):
    source = tmp_path / "source.docx"
    _make_source_docx(source)

    rows, _gen = _render(tmp_path, entries=[], original_doc_path=str(source))

    assert rows.get("office address:", "") != "", (
        "Office address stayed empty -- the fallback's recovered address "
        "was discarded instead of written back (#550)"
    )
    assert "5117 Centre Avenue" in rows.get("office address:", "")
    assert rows.get("work email:", "") != "", (
        "Work email stayed empty -- the fallback's recovered email was "
        "discarded instead of written back (#550)"
    )
    assert rows.get("work email:") == "plo4@pitt.edu"
    assert rows.get("office telephone:", "") != "", (
        "Office telephone stayed empty -- the fallback's recovered phone "
        "was discarded instead of written back (#550)"
    )
    assert "412-623-7764" in rows.get("office telephone:", "")


# --------------------------------------------------------------------------
# negative control -- a value already classified from the A entries is
# never overwritten by a weaker fallback read.
# --------------------------------------------------------------------------

def test_extracted_contact_fields_are_not_overwritten_by_recovered_ones(tmp_path):
    source = tmp_path / "source.docx"
    _make_source_docx(source)  # carries plo4@pitt.edu / 412-623-7764 / 5117 Centre Avenue

    rows, _gen = _render(
        tmp_path,
        entries=[
            _a("Office: 1300 York Avenue, New York, NY",
               {"address": "1300 York Avenue, New York, NY"}),
            _a("Work email: extracted@example.com",
               {"email": "extracted@example.com"}),
            _a("Office phone: 555-000-1111",
               {"phone": "555-000-1111"}),
        ],
        original_doc_path=str(source),
    )

    assert rows.get("office address:", "") == "1300 York Avenue, New York, NY"
    assert rows.get("work email:") == "extracted@example.com"
    assert rows.get("office telephone:") == "555-000-1111"

    # None of the fallback's OWN values leaked in anywhere they didn't belong.
    for leaked in ("plo4@pitt.edu", "412-623-7764", "5117 Centre Avenue"):
        assert leaked not in json.dumps(rows), (
            f"recovered value {leaked!r} overwrote an already-extracted field"
        )


# --------------------------------------------------------------------------
# the logging half -- ungated, and counted.
# --------------------------------------------------------------------------

def test_fallback_parse_failure_is_logged_and_counted_not_silent(tmp_path, caplog):
    unparseable = tmp_path / "not_actually_a_docx.docx"
    unparseable.write_text("this is not a zip file")

    with caplog.at_level(logging.WARNING, logger="unified_pipeline.stage6.sections.personal_data"):
        _rows, gen = _render(tmp_path, entries=[], original_doc_path=str(unparseable))

    assert gen.stats.get("personal_data_fallback_failed") == 1, (
        "a fallback parse failure must increment a stats counter (#550)"
    )
    assert any("personal data" in r.message.lower() for r in caplog.records), (
        "a fallback parse failure must be logged, not silently swallowed (#550)"
    )
