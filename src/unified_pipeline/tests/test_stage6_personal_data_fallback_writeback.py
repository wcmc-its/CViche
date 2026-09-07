"""Regression tests for #550: the original-.docx personal-data fallback

recovers the office address, office phone and work email from the source
document (``_recover_contact_fields_from_docx``) and then discards them -- the
PERSONAL DATA table fill read only ``work_email``/``office_phone``/
``office_address`` while the fallback wrote a second, unread set of names. AST
dataflow in the issue: last *store* to the three slot names is before the
fallback runs; last *load* of the fallback's own locals is inside it. No
write-back existed on either name.

#550 fixed that with three ``x = x or recovered`` lines. The review that
followed removed the second set of names instead of keeping the bridge, so the
entry classifier, the all-entries email scan and the source-document recovery
now all store into the three slot names directly and there is nothing left to
synchronise. What survives from the bridge is the rule it enforced -- a
recovery fills an EMPTY slot only, never overwrites a value the A entries
already classified -- plus one guard: a recovered email that already equals
``personal_email`` is dropped rather than duplicated into ``work_email``, and
that guard applies only to a value one of the two scans produced.

    python3 -m pytest src/unified_pipeline/tests/test_stage6_personal_data_fallback_writeback.py -p no:cacheprovider

Positive control: a minimal .docx fixture holding the real 2054_Opresko_Cv
shape -- a "BUSINESS ADDRESS:" table cell whose value embeds "Phone:" /
"E-mail:" lines (confirmed by reading that source document directly; it is
NOT three separate table rows) -- with empty A-entries, so the only source
for Office address / Work email / Office telephone is the fallback.
Negative control: the same fixture, but the A-entries already carry a work
email / office phone / office address of their own; those must survive
untouched, and the fixture's different values must not appear anywhere.

The negative control cannot distinguish an assignment's operand order, and no
longer needs to: with one set of names there is no second operand. What it
pins is the ``if not <slot>`` guard on every recovery -- drop one and an
extracted value starts losing to a recovered one, and it fails. A third test
(``..._per_field``) pins that the recoveries are independent: one slot filled
from the entries and two from the document is a real corpus shape, and a
wholesale "assign all three from the fallback" would pass the all-empty and
all-full cases while failing it.

The rest of the file is the review round on #740, one test per objection:
scanning every table and every paragraph rather than the first three / first
twenty, phone shapes that are not US, label classification that does not read
"Business phone:" as an address or "Username:" as a person, a narrowed except
that lets a programming error out, opening the document only when a scan can
add something, and the embedded E-mail line no longer riding along into the
Office address.
"""

import json
import logging
import sys
from pathlib import Path

import pytest
from docx import Document

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage6.sections import personal_data as personal_data_module  # noqa: E402
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


def _make_label_table_docx(path: Path, rows, leading_tables=0) -> None:
    """A source .docx whose contact block is a label/value table.

    ``leading_tables`` puts that many unrelated tables in front of it, which is
    what a CV with a cover or education table above its contact block looks
    like to the scan.
    """
    doc = Document()
    for index in range(leading_tables):
        filler = doc.add_table(rows=1, cols=2)
        filler.rows[0].cells[0].text = f"Education {index + 1}:"
        filler.rows[0].cells[1].text = "PhD, University of Pittsburgh, 1998"
    table = doc.add_table(rows=len(rows), cols=2)
    for row, (label, value) in zip(table.rows, rows):
        row.cells[0].text = label
        row.cells[1].text = value
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


def _rendered_name(docx_path) -> str:
    """The name the "Name:" paragraph carries in the rendered document."""
    doc = Document(str(docx_path))
    for para in doc.paragraphs:
        if para.text.strip().startswith("Name:"):
            return para.text.split(":", 1)[1].strip()
    raise AssertionError("Name: paragraph not found in output")


def _render(tmp_path, entries, original_doc_path=None, cv_owner=None) -> dict:
    gen = WCMTemplateGenerator(verbose=False)
    gen._reconsider_appendix_entries = lambda: None
    ip, op = tmp_path / "in.json", tmp_path / "out.docx"
    payload = {"document_uid": "TESTPDWB", "entries": entries}
    if cv_owner is not None:
        payload["cv_owner"] = cv_owner
    ip.write_text(json.dumps(payload))
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
    cases are both passed by a wrong recovery that assigns the three slots
    together -- a single ``if not work_email:`` guarding all three. This mixed
    case is the one that separates them, and it is the common corpus shape:
    2054_Opresko_Cv's entries supply nothing while its source table supplies
    address/phone/email, but a CV whose entries yield an email and whose
    address only exists in the source table needs BOTH halves.
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
        "the entry-classified work email lost to the document's -- a "
        "recovery is overwriting a populated slot (#550)"
    )
    assert rows.get("personal email:", "") == "", (
        "the document's email was routed into Personal email instead of "
        "being dropped -- a recovered value must fill an empty slot, not "
        "find a different one to occupy (#550)"
    )
    # ... and the two empty ones are still filled from the document.
    assert "5117 Centre Avenue" in rows.get("office address:", ""), (
        "Office address stayed empty even though only Work email was "
        "extracted -- the three recoveries are not independent (#550)"
    )
    assert "412-623-7764" in rows.get("office telephone:", ""), (
        "Office telephone stayed empty even though only Work email was "
        "extracted -- the three recoveries are not independent (#550)"
    )

    # The business-address block's embedded E-mail line is consumed whatever
    # else is already known. It used to be skipped only while the email slot
    # was empty, so in exactly this mixed shape the address cell kept the
    # source table's E-mail line as an address line -- an email address
    # rendered in an address field.
    assert "E-mail:" not in rows.get("office address:", ""), (
        "the address block's embedded E-mail line rode along into Office "
        "address because an email had already been found elsewhere"
    )
    assert "plo4@pitt.edu" not in rows.get("office address:", "")


def test_an_embedded_phone_line_is_consumed_even_when_the_phone_is_known(tmp_path):
    """The Phone: line has the identical shape to the E-mail: line above."""
    source = tmp_path / "source.docx"
    _make_source_docx(source)

    rows, _gen = _render(
        tmp_path,
        entries=[_a("Office phone: 555-000-1111", {"phone": "555-000-1111"})],
        original_doc_path=str(source),
    )

    assert rows.get("office telephone:") == "555-000-1111"
    assert "Phone:" not in rows.get("office address:", ""), (
        "the address block's embedded Phone: line rode along into Office "
        "address because a phone had already been classified from the entries"
    )
    assert "412-623-7764" not in rows.get("office address:", "")


# --------------------------------------------------------------------------
# the logging half -- ungated, counted, and narrowed to the failures a source
# document can actually produce.
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


def test_a_programming_error_in_the_recovery_scan_is_not_counted_as_a_parse_failure(
        tmp_path, monkeypatch):
    """An AttributeError raised inside the scan must reach the caller.

    The recovery used to wrap its whole body in ``except Exception``, so a
    defect introduced anywhere in the table or paragraph scan was reported as
    "could not read the original document" and the renderer carried on with
    incomplete contact data.
    """
    source = tmp_path / "source.docx"
    _make_source_docx(source)

    def _boom(_label):
        raise AttributeError("injected programming error")

    monkeypatch.setattr(personal_data_module, "_classify_contact_label", _boom)

    with pytest.raises(AttributeError, match="injected programming error"):
        _render(tmp_path, entries=[], original_doc_path=str(source))


def test_a_directory_named_docx_is_not_opened_as_a_document(tmp_path):
    """``is_file()``, not ``exists()``: a directory is not a source document."""
    not_a_file = tmp_path / "source.docx"
    not_a_file.mkdir()

    rows, gen = _render(tmp_path, entries=[], original_doc_path=str(not_a_file))

    assert gen.stats.get("personal_data_fallback_failed") is None, (
        "a directory named *.docx was handed to python-docx and counted as a "
        "corrupt source document instead of being rejected as the wrong kind "
        "of path"
    )
    assert rows.get("work email:", "") == ""


# --------------------------------------------------------------------------
# only open the source document when a scan can actually add something.
# --------------------------------------------------------------------------

def _record_document_opens(monkeypatch):
    """Record every path `personal_data` hands to python-docx."""
    opened = []
    real_document = personal_data_module.Document

    def _recording(path, *args, **kwargs):
        opened.append(str(path))
        return real_document(path, *args, **kwargs)

    monkeypatch.setattr(personal_data_module, "Document", _recording)
    return opened


_COMPLETE_CONTACT_ENTRIES = [
    _a("Office: 1300 York Avenue, New York, NY",
       {"address": "1300 York Avenue, New York, NY"}),
    _a("Work email: extracted@example.com", {"email": "extracted@example.com"}),
    _a("Office phone: 555-000-1111", {"phone": "555-000-1111"}),
]


def test_a_complete_record_never_opens_the_source_document(tmp_path, monkeypatch):
    source = tmp_path / "source.docx"
    _make_source_docx(source)
    opened = _record_document_opens(monkeypatch)

    rows, _gen = _render(
        tmp_path,
        entries=list(_COMPLETE_CONTACT_ENTRIES),
        original_doc_path=str(source),
        cv_owner={"full_name": "Patricia Opresko"},
    )

    assert opened == [], (
        "the source document was parsed for a record that already had a "
        f"complete name and all three contact values (opened {opened})"
    )
    assert rows.get("work email:") == "extracted@example.com"


def test_a_record_missing_only_the_name_still_opens_the_source_document(
        tmp_path, monkeypatch):
    """The recovery supplies four things, so the guard must check four."""
    source = tmp_path / "source.docx"
    _make_source_docx(source)
    opened = _record_document_opens(monkeypatch)

    _rows, _gen = _render(
        tmp_path,
        entries=list(_COMPLETE_CONTACT_ENTRIES),
        original_doc_path=str(source),
    )

    assert opened == [str(source)], (
        "a record whose only missing field was the name skipped the source "
        "document, so the table scan that recovers a name never ran"
    )
    assert _rendered_name(tmp_path / "out.docx") == "Patricia Opresko"


# --------------------------------------------------------------------------
# scan every table and every paragraph -- where the contact block sits is a
# layout property of the CV, not a position we get to assume.
# --------------------------------------------------------------------------

def test_contact_table_after_the_first_three_tables_is_still_scanned(tmp_path):
    source = tmp_path / "source.docx"
    _make_label_table_docx(
        source,
        rows=[("NAME:", "Patricia Opresko"),
              ("Office telephone:", "412-623-7764"),
              ("Email:", "plo4@pitt.edu")],
        leading_tables=4,
    )

    rows, _gen = _render(tmp_path, entries=[], original_doc_path=str(source))

    assert rows.get("office telephone:") == "412-623-7764", (
        "the contact table is table 5 of the document and was skipped -- the "
        "scan stopped after the first three tables"
    )
    assert rows.get("work email:") == "plo4@pitt.edu"
    assert _rendered_name(tmp_path / "out.docx") == "Patricia Opresko"


def test_email_after_the_twentieth_paragraph_is_still_recovered(tmp_path):
    source = tmp_path / "source.docx"
    doc = Document()
    for index in range(24):
        doc.add_paragraph(
            f"{index + 1}. Opresko PL. A paper with no contact details. 2019.")
    doc.add_paragraph("Correspondence to: plo4@pitt.edu")
    doc.save(str(source))

    rows, _gen = _render(tmp_path, entries=[], original_doc_path=str(source))

    assert rows.get("work email:") == "plo4@pitt.edu", (
        "the only email in the document is past paragraph 20 and was not "
        "recovered -- the paragraph scan stopped at a fixed count"
    )


# --------------------------------------------------------------------------
# label classification -- 'business' is not "business address" and a label
# ending in "name" is not the person's name.
# --------------------------------------------------------------------------

def test_business_phone_and_business_email_do_not_land_in_the_address(tmp_path):
    source = tmp_path / "source.docx"
    _make_label_table_docx(
        source,
        rows=[("Business phone:", "412-623-7764"),
              ("Business email:", "plo4@pitt.edu"),
              ("Business address:", "University of Pittsburgh\n5117 Centre Avenue")],
    )

    rows, _gen = _render(tmp_path, entries=[], original_doc_path=str(source))

    assert rows.get("office telephone:") == "412-623-7764"
    assert rows.get("work email:") == "plo4@pitt.edu"
    assert "5117 Centre Avenue" in rows.get("office address:", "")
    for not_an_address in ("412-623-7764", "plo4@pitt.edu"):
        assert not_an_address not in rows.get("office address:", ""), (
            f"{not_an_address!r} rendered as the Office address -- 'business' "
            "in the label was read as 'business address'"
        )


@pytest.mark.parametrize("label", ["Username:", "Department name:", "File name:"])
def test_metadata_labels_ending_in_name_are_not_the_person_name(tmp_path, label):
    source = tmp_path / "source.docx"
    _make_label_table_docx(source, rows=[(label, "wcm_service_account")])

    _rows, _gen = _render(tmp_path, entries=[], original_doc_path=str(source))

    assert _rendered_name(tmp_path / "out.docx") != "wcm_service_account", (
        f"{label!r} was read as the person's name"
    )


@pytest.mark.parametrize("label", ["NAME:", "Full name:"])
def test_person_name_labels_are_still_recovered(tmp_path, label):
    source = tmp_path / "source.docx"
    _make_label_table_docx(source, rows=[(label, "Patricia Opresko")])

    _rows, _gen = _render(tmp_path, entries=[], original_doc_path=str(source))

    assert _rendered_name(tmp_path / "out.docx") == "Patricia Opresko"


# --------------------------------------------------------------------------
# phone shapes -- one entry naming both a cell and an office number.
# --------------------------------------------------------------------------

def test_us_cell_and_office_numbers_in_one_entry_reach_their_own_rows(tmp_path):
    rows, _gen = _render(tmp_path, entries=[
        _a("Cell phone: (212) 555-1234; Office phone: (212) 555-9999",
           {"phone": "(212) 555-1234; (212) 555-9999"}),
    ])

    assert rows.get("cell phone:") == "(212) 555-1234"
    assert rows.get("office telephone:") == "(212) 555-9999"


def test_international_cell_and_office_numbers_reach_their_own_rows(tmp_path):
    """+44 and +91 numbers labelled Cell and Office in one entry.

    The two searches recognised a single US shape, so neither number matched
    and both template rows stayed empty while the entry plainly carried them.
    """
    rows, _gen = _render(tmp_path, entries=[
        _a("Cell phone: +44 20 7946 0958; Office phone: +91 22 6666 7777",
           {"phone": "+44 20 7946 0958; +91 22 6666 7777"}),
    ])

    assert rows.get("cell phone:") == "+44 20 7946 0958"
    assert rows.get("office telephone:") == "+91 22 6666 7777"


def test_a_structured_phone_naming_both_slots_fills_both_rows(tmp_path):
    """#450: a dict phone names its own halves and is routed by them."""
    rows, _gen = _render(tmp_path, entries=[
        _a("Home", {"phone": {"cell": "212-555-1234", "office": "212-555-9999"}}),
    ])

    assert rows.get("cell phone:") == "212-555-1234"
    assert rows.get("office telephone:") == "212-555-9999"


def test_a_home_labelled_phone_fills_neither_phone_row(tmp_path):
    """The WCM template has no home-phone row, and squatting in one of the
    two it does have took the office row on web113 and skipped the real
    business number as already-set."""
    rows, _gen = _render(tmp_path, entries=[
        _a("Home phone: 555-111-2222", {"phone": "555-111-2222"}),
    ])

    assert rows.get("office telephone:", "") == ""
    assert rows.get("cell phone:", "") == ""


# --------------------------------------------------------------------------
# address shapes.
# --------------------------------------------------------------------------

def test_a_structured_address_naming_both_slots_fills_both_rows(tmp_path):
    """#442: a dict naming home and office was forced whole into whichever
    slot the raw text happened to label."""
    rows, _gen = _render(tmp_path, entries=[
        _a("Contact", {"address": {"home_address": "10 Bank Street, New York, NY",
                                   "office_address": "1300 York Avenue, New York, NY"}}),
    ])

    assert rows.get("home address:") == "10 Bank Street, New York, NY"
    assert rows.get("office address:") == "1300 York Avenue, New York, NY"


# --------------------------------------------------------------------------
# name precedence.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("case, cv_owner, entries, expected", [
    ("credentials beat full_name",
     {"full_name_with_credentials": "Patricia L. Opresko, PhD\nDate prepared: June 2026",
      "full_name": "Patricia Opresko"},
     [], "Patricia L. Opresko, PhD"),
    ("full_name when there are no credentials",
     {"full_name": "Patricia Opresko"}, [], "Patricia Opresko"),
    ("a LinkedIn slug when cv_owner has neither",
     {}, [_a("https://www.linkedin.com/in/patricia-opresko")], "Patricia Opresko"),
    ("the document uid when nothing else names the person",
     {}, [], "Testpdwb"),
    ("a last name alone is not a complete name",
     {"last_name": "Opresko"}, [], "Testpdwb"),
])
def test_name_precedence(tmp_path, case, cv_owner, entries, expected):
    case_dir = tmp_path / case.replace(" ", "_")
    case_dir.mkdir()

    _rows, _gen = _render(case_dir, entries=entries, cv_owner=cv_owner)

    assert _rendered_name(case_dir / "out.docx") == expected, case


# --------------------------------------------------------------------------
# round 1 (blind verifier): a recovered email that is already personal_email
# must not also duplicate into work_email -- but a work email the A entries
# classified themselves is not a duplicate and must survive.
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


def test_a_work_email_classified_from_the_entries_survives_matching_personal_email(tmp_path):
    """One address given as both the personal and the work email.

    The duplicate guard exists for a value a SCAN produced. When the A entries
    classified both slots themselves the CV said what it meant, and both rows
    render -- which is what the ``work_email = work_email or email`` line
    restored before the two sets of names were collapsed into one.
    """
    rows, _gen = _render(tmp_path, entries=[
        _a("Personal email: shared@med.cornell.edu",
           {"email": "shared@med.cornell.edu"}),
        _a("Work email: shared@med.cornell.edu",
           {"email": "shared@med.cornell.edu"}),
    ])

    assert rows.get("personal email:") == "shared@med.cornell.edu"
    assert rows.get("work email:") == "shared@med.cornell.edu", (
        "the duplicate-email guard cleared a work email the per-entry "
        "classifier had put there itself"
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


# --------------------------------------------------------------------------
# protected personal data, proved at the rendered document rather than on the
# helper: a value stage 4 lifted out of a PII fragment is not contact data.
# --------------------------------------------------------------------------

def test_pii_fragment_values_never_reach_the_rendered_personal_data_table(tmp_path):
    """web07 rendered "Cincinnati, Ohio" as its Office address, taken straight
    out of "PLACE OF BIRTH: Cincinnati, Ohio" by the address catch-all."""
    rows, _gen = _render(tmp_path, entries=[
        _a("PLACE OF BIRTH: Cincinnati, Ohio\t"
           "SPOUSE: Jane Doe jane.doe@example.com",
           {"address": "Cincinnati, Ohio", "email": "jane.doe@example.com"}),
    ])

    rendered = json.dumps(rows)
    assert "Cincinnati" not in rendered, (
        "a place of birth rendered as the Office address"
    )
    assert "jane.doe@example.com" not in rendered, (
        "an email taken out of a spouse fragment rendered as a contact email"
    )
    assert rows.get("office address:", "") == ""
    assert rows.get("work email:", "") == ""
    assert rows.get("personal email:", "") == ""
