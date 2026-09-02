"""Regression tests for #550: the original-.docx personal-data fallback

recovers ``email``/``phone``/``address`` from the source document
(``personal_data.py:267-381`` on this ref) and then discards them -- the
PERSONAL DATA table fill reads only ``work_email``/``office_phone``/
``office_address``, and the fallback never wrote back into those names. AST
dataflow in the issue: last *store* to the three slot names is before the
fallback runs; last *load* of the fallback's own ``email``/``phone``/
``address`` locals is inside it. No write-back existed on either name.

Fix is the three ``x = x or recovered`` assignments immediately before the
table fill (``personal_data.py:404-406`` on this ref) -- an empty slot only,
never an overwrite of a value already classified from the entries. Round 1
(a blind verifier's findings) added a fourth guard right above those three
assignments: a recovered ``email`` that already equals ``personal_email`` is
dropped rather than written into ``work_email`` too, and a merged-cell-aware
label/value read in the table scan (``personal_data.py:274-289``) so a
gridSpan label ("Professional Address:" spanning two columns) is not
mistaken for its own value.

    python3 -m pytest src/unified_pipeline/tests/test_stage6_personal_data_fallback_writeback.py -p no:cacheprovider

Positive control: a minimal .docx fixture holding the real 2054_Opresko_Cv
shape -- a "BUSINESS ADDRESS:" table cell whose value embeds "Phone:" /
"E-mail:" lines (confirmed by reading that source document directly; it is
NOT three separate table rows) -- with empty A-entries, so the only source
for Office address / Work email / Office telephone is the fallback.
Negative control: the same fixture, but the A-entries already carry a work
email / office phone / office address of their own; those must survive
untouched, and the fixture's different values must not appear anywhere.

What that negative control does and does not pin, stated plainly because a
blind verifier read it as stronger than it is: it CANNOT distinguish
``work_email = work_email or email`` from ``work_email = email or
work_email``. The operand order there is unobservable by construction, not
merely untested -- ``email``/``phone``/``address`` are initialised from
``work_email``/``office_phone``/``office_address`` ("Legacy variable names",
``personal_data.py:250-252`` on this ref) and are only ever reassigned under
an ``if not <name>`` guard, so the two operands are either equal or exactly
one of them is empty. The negative control pins the invariant that makes
that true -- if a future edit drops one of those guards, an extracted value
starts losing to a recovered one and this test fails. A fourth test
(``..._per_field``) pins that the three assignments are independent: one
slot filled from the entries and two from the document is a real corpus
shape, and a wholesale "assign all three from the fallback" write-back would
pass the all-empty and all-full cases while failing it.

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


def test_write_back_is_per_field_not_wholesale(tmp_path):
    """One slot classified from the entries, two recovered from the document.

    The all-empty (positive control) and all-populated (negative control)
    cases are both passed by a wrong write-back that assigns the three slots
    together -- ``work_email, office_phone, office_address = email, phone,
    address``, or a single ``if not work_email:`` guarding all three. This
    mixed case is the one that separates them, and it is the common corpus
    shape: 2054_Opresko_Cv's entries supply nothing while its source table
    supplies address/phone/email, but a CV whose entries yield an email and
    whose address only exists in the source table needs BOTH halves.
    """
    source = tmp_path / "source.docx"
    _make_source_docx(source)  # plo4@pitt.edu / 412-623-7764 / 5117 Centre Avenue

    rows, _gen = _render(
        tmp_path,
        entries=[_a("Work email: extracted@example.com",
                    {"email": "extracted@example.com"})],
        original_doc_path=str(source),
    )

    # The one extracted slot wins ...
    assert rows.get("work email:") == "extracted@example.com", (
        "the entry-classified work email lost to the document's -- the "
        "write-back is overwriting a populated slot (#550)"
    )
    assert rows.get("personal email:", "") == "", (
        "the document's email was routed into Personal email instead of "
        "being dropped -- a recovered value must fill an empty slot, not "
        "find a different one to occupy (#550)"
    )
    # ... and the two empty ones are still filled from the document.
    assert "5117 Centre Avenue" in rows.get("office address:", ""), (
        "Office address stayed empty even though only Work email was "
        "extracted -- the three write-backs are not independent (#550)"
    )
    assert "412-623-7764" in rows.get("office telephone:", ""), (
        "Office telephone stayed empty even though only Work email was "
        "extracted -- the three write-backs are not independent (#550)"
    )

    # Recorded, not endorsed: the business-address block parser strips an
    # embedded "Phone:" line only while `phone` is still empty and an
    # embedded "E-mail:" line only while `email` is still empty
    # (personal_data.py:312 and :321 -- byte-identical to origin/dev, this
    # PR does not touch either line). So in exactly this mixed shape the
    # address cell keeps the source table's E-mail line as an address line,
    # while the Phone line is correctly lifted out. It is a pre-existing
    # heuristic quirk that the #550 write-back makes visible for the first
    # time, no corpus uid exhibits it today (the render gate's three CHANGED
    # uids all have empty contact slots), and fixing the parser is outside
    # this issue's scope -- but it must not become silent again, so it is
    # pinned here and disclosed in the PR body rather than asserted away.
    assert "E-mail:" in rows.get("office address:", ""), (
        "the address block's embedded E-mail line is no longer riding along "
        "into Office address -- if that was fixed deliberately, delete this "
        "assertion and the matching PR-body/#550 disclosure with it"
    )


# --------------------------------------------------------------------------
# the logging half -- ungated, and counted.
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# round 1 (blind verifier): a recovered email that is already personal_email
# must not also duplicate into work_email.
# --------------------------------------------------------------------------

def test_recovered_email_matching_personal_email_is_not_duplicated_into_work_email(tmp_path):
    source = tmp_path / "source.docx"
    doc = Document()
    doc.add_paragraph("Curriculum Vitae")
    doc.add_paragraph("Personal Data")
    doc.add_paragraph("Email: shared@med.cornell.edu")
    doc.save(str(source))

    # The only A entry names its own text "Personal Data" (the section
    # header), so the per-entry classifier routes its email to
    # personal_email, not work_email -- the XLYVYA_sample_vasquez_cv shape.
    rows, _gen = _render(
        tmp_path,
        entries=[
            _a("Curriculum Vitae\tPersonal Data\tJane Doe\t"
               "Email: shared@med.cornell.edu",
               {"institutional_email": "shared@med.cornell.edu"}),
        ],
        original_doc_path=str(source),
    )

    assert rows.get("personal email:") == "shared@med.cornell.edu"
    assert rows.get("work email:", "") == "", (
        "the fallback's paragraph scan recovered the same address already "
        "routed to personal_email and duplicated it into work_email (#550 "
        "round 1, XLYVYA_sample_vasquez_cv)"
    )


# --------------------------------------------------------------------------
# round 1 (blind verifier): a gridSpan-merged label cell must not be read as
# its own value.
# --------------------------------------------------------------------------

def test_recovered_address_survives_a_gridspan_merged_label_cell(tmp_path):
    source = tmp_path / "source.docx"
    doc = Document()
    table = doc.add_table(rows=1, cols=3)
    table.rows[0].cells[0].text = "Professional Address:"
    # python-docx returns the SAME cell object for every column a merge
    # spans -- row.cells[0] and row.cells[1] are now identical, exactly the
    # NSUJZG_2027_Eil_Robert shape (label merged across columns 0-1, value
    # in column 2).
    table.rows[0].cells[0].merge(table.rows[0].cells[1])
    table.rows[0].cells[2].text = "2720 S Moody Ave KCRB 2006, Portland, OR 97201"
    doc.save(str(source))

    rows, _gen = _render(tmp_path, entries=[], original_doc_path=str(source))

    assert rows.get("office address:", "") != "professional address:", (
        "the merged label cell was read as its own value -- the Office "
        "address row now shows the bare label instead of the address "
        "(#550 round 1, NSUJZG_2027_Eil_Robert)"
    )
    assert "2720 S Moody Ave" in rows.get("office address:", "")


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
