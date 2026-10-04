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

from unified_pipeline.stage6.normalization.pii import CAT_HOME_CONTACT  # noqa: E402
from unified_pipeline.stage6.sections import personal_data as personal_data_module  # noqa: E402
from unified_pipeline.stage_6_word_template import WCMTemplateGenerator, run_stage6  # noqa: E402

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


def _render(tmp_path, entries, original_doc_path=None, cv_owner=None,
            document_uid="TESTPDWB") -> dict:
    gen = WCMTemplateGenerator(verbose=False)
    gen._reconsider_appendix_entries = lambda: None
    ip, op = tmp_path / "in.json", tmp_path / "out.docx"
    payload = {"document_uid": document_uid, "entries": entries}
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
    business number as already-set.

    #821 makes the never-rendering a recorded policy fact rather than an
    accident: a "Home phone:" label is now a policy row (CAT_HOME_CONTACT),
    so `run_pii_pass` records it withheld too."""
    rows, gen = _render(tmp_path, entries=[
        _a("Home phone: 555-111-2222", {"phone": "555-111-2222"}),
    ])

    assert rows.get("office telephone:", "") == ""
    assert rows.get("cell phone:", "") == ""
    assert any(item.category == CAT_HOME_CONTACT for item in gen._pii_result.withheld)


def test_a_reformatted_home_phone_does_not_leak_into_office(tmp_path):
    """Regression: `run_pii_pass` cuts the WHOLE entry text when the #821
    home-contact label IS the entry's only content, leaving `text` empty
    for `_fill_personal_data`'s own 'home'/'office' classification. When
    `extracted_fields['phone']` is reformatted from the raw label line (a
    real corpus shape -- stage 4 normalizes "555.111.2222" to
    "555-111-2222"), `_from_pii_fragment`'s containment check can fail to
    null it upstream too, so nothing catches it: `has_home` reads False on
    the now-empty text and the number falls through to the OFFICE
    telephone row -- worse than the #442-class drop this file otherwise
    guards against. `_label_word_present` (personal_data.py) is the fix:
    it also reads the pass's own pre-strip `_pii_fragments`."""
    rows, gen = _render(tmp_path, entries=[
        _a("Home phone: 555.111.2222", {"phone": "555-111-2222"}),
    ])

    assert rows.get("office telephone:", "") == ""
    assert rows.get("cell phone:", "") == ""
    assert any(item.category == CAT_HOME_CONTACT for item in gen._pii_result.withheld)


def test_a_reformatted_home_address_does_not_leak_into_office(tmp_path):
    """The address-block twin of the phone regression above: stage 4
    enriches "Home Address: 12 Elm St" with a city/state the raw label
    line never had, so the enriched value is not a verbatim substring of
    the cut fragment either."""
    rows, gen = _render(tmp_path, entries=[
        _a("Home Address: 12 Elm St", {"address": "12 Elm St, Rye, NY 10580"}),
    ])

    assert rows.get("home address:", "") == ""
    assert rows.get("office address:", "") == "", (
        "a home address leaked into the office row"
    )
    assert any(item.category == CAT_HOME_CONTACT for item in gen._pii_result.withheld)


# --------------------------------------------------------------------------
# address shapes.
# --------------------------------------------------------------------------

def test_a_structured_address_naming_both_slots_fills_both_rows(tmp_path):
    """#442: a dict naming home and office was forced whole into whichever
    slot the raw text happened to label.

    #821: the home half is now withheld with notice -- a structured dict
    carries no text label at all for `pii.py`'s policy table to match
    ("Contact" is the entry's whole raw text here), so this is exactly the
    path `_fill_personal_data`'s unconditional home_address/home_phone
    guard exists for (see that guard's own comment). Office address is
    unaffected."""
    rows, gen = _render(tmp_path, entries=[
        _a("Contact", {"address": {"home_address": "10 Bank Street, New York, NY",
                                   "office_address": "1300 York Avenue, New York, NY"}}),
    ])

    assert rows.get("home address:", "") == ""
    assert "10 Bank Street" not in rows.get("home address:", "")
    assert rows.get("office address:") == "1300 York Avenue, New York, NY"
    assert any(item.category == CAT_HOME_CONTACT for item in gen._pii_result.withheld)


# --------------------------------------------------------------------------
# #946 item 1: the label nearest the number outranks the block heading, and
# a consumer-domain email is the owner's personal email.
# --------------------------------------------------------------------------

def _home_block(number_label, email="jdoe.cvtest@gmail.com", email_key="personal_email"):
    """A HOME ADDRESS block in the tab-joined shape stage 2 emits for a
    one-row source table: heading, two address lines, a labelled number and
    an email -- the run ZA1VOV shape, synthesized."""
    return _a(f"HOME ADDRESS\t10 Elm Street Apt 2\tRye, NY 10580\t"
              f"{number_label} 212.555.0142\t{email}",
              {"phone": "212.555.0142", email_key: email,
               "address": "10 Elm Street Apt 2, Rye, NY 10580"})


@pytest.mark.parametrize("label", ["(c)", "c:", "c.", "cell.", "mob", "(m)", "m:",
                                   "(C)", "Mobile:"])
def test_a_cell_label_at_the_number_beats_a_home_block_heading(tmp_path, label):
    """Every number here sits in a HOME ADDRESS block and routed to
    home_phone, where #821 withholds it -- the owner's labelled cell phone
    never reached the Cell phone row. The home address itself is still
    withheld."""
    rows, gen = _render(tmp_path, entries=[_home_block(label)])

    assert rows.get("cell phone:") == "212.555.0142", label
    assert rows.get("office telephone:", "") == ""
    assert rows.get("home address:", "") == ""
    assert any(item.category == CAT_HOME_CONTACT for item in gen._pii_result.withheld)


@pytest.mark.parametrize("label", ["(h)", "h:", "Home phone:", ""])
def test_a_home_labelled_or_unlabelled_number_in_a_home_block_stays_withheld(
        tmp_path, label):
    """#821 still applies to a number the owner labelled home, and to one
    carrying no label of its own inside a home block."""
    rows, gen = _render(tmp_path, entries=[_home_block(label)])

    assert rows.get("cell phone:", "") == ""
    assert rows.get("office telephone:", "") == ""
    assert "212.555.0142" not in " ".join(rows.values())
    assert any(item.category == CAT_HOME_CONTACT for item in gen._pii_result.withheld)


def test_a_home_label_at_the_number_beats_a_cell_word_elsewhere_in_the_entry(tmp_path):
    """The nearest label decides in the other direction too: 'Cell' naming
    a DIFFERENT number must not pull an "(h)" number into the Cell row."""
    rows, gen = _render(tmp_path, entries=[
        _a("Contact\tCell: 917-555-0100\t(h) 212-555-0142", {"phone": "212-555-0142"}),
    ])

    assert rows.get("cell phone:", "") == ""
    assert any(item.category == CAT_HOME_CONTACT for item in gen._pii_result.withheld)


# One row per email-routing edge case, rendered end to end: the row the
# address lands in, and that the other email row stays empty.
_WORK, _PERSONAL = "work email:", "personal email:"
_GMAIL, _WCM = "jdoe.cvtest@gmail.com", "zzz9999@med.cornell.edu"


@pytest.mark.parametrize("entry, row, address", [
    # a consumer address in a HOME ADDRESS block is the personal email
    pytest.param(_home_block("(c)"), _PERSONAL, _GMAIL, id="consumer-in-home-block"),
    # ... unless its own label calls it work
    pytest.param(_a(f"Contact\tWork email: {_GMAIL}", {"email": _GMAIL}),
                 _WORK, _GMAIL, id="consumer-labelled-work"),
    # only the label between the previous separator and the address owns it
    pytest.param(_a(f"Office: 1300 York Avenue\t{_GMAIL}", {"email": _GMAIL}),
                 _PERSONAL, _GMAIL, id="work-word-in-another-segment"),
    # an institutional address in a home block stays work
    pytest.param(_home_block("(c)", email=_WCM, email_key="email"),
                 _WORK, _WCM, id="institutional-in-home-block"),
    # a stage-4 work_email key outranks the consumer domain
    pytest.param(_a(f"Contact\t{_GMAIL}", {"work_email": _GMAIL}),
                 _WORK, _GMAIL, id="work-email-key"),
    # no stage-4 email field: the per-entry text fallback finds it, and it
    # routed straight to Work email before whatever labelled it
    pytest.param(_a(f"Office Phone: 212-555-0100\t\t\tHome Email: {_GMAIL}",
                    {"phone": "212-555-0100"}),
                 _PERSONAL, _GMAIL, id="text-fallback-home-email"),
])
def test_an_email_renders_in_the_row_its_domain_key_and_label_choose(
        tmp_path, entry, row, address):
    rows, _gen = _render(tmp_path, entries=[entry])

    other = _PERSONAL if row == _WORK else _WORK
    assert rows.get(row) == address
    assert rows.get(other, "") == "", f"the address rendered in {other!r} too"


@pytest.mark.parametrize("phone, text, expected", [
    ("212.555.0142", "home\t(c) 212.555.0142", personal_data_module._PHONE_LABEL_CELL),
    ("212-555-0142", "(c) +1 212 555 0142", personal_data_module._PHONE_LABEL_CELL),
    ("212.555.0142", "cell 917.555.0100\th: 212.555.0142",
     personal_data_module._PHONE_LABEL_HOME),
    ("212.555.0142", "home address\t212.555.0142", None),
    ("212.555.0142", "(c) 917.555.0100", None),
    # a short extracted value must not pair with a longer number it ends
    ("0142", "(c) 555-0142", None),
    ("212.555.0142; 917.555.0100", "(c) 212.555.0142; (h) 917.555.0100", None),
    # a country code on the extracted value, none in the text
    ("+1 212-555-0142", "(c) 212-555-0142", personal_data_module._PHONE_LABEL_CELL),
    ("212-555-0142", "(c)212-555-0142", personal_data_module._PHONE_LABEL_CELL),
    ("212-555-0142", "cell 917-555-0100\thome: 212-555-0142",
     personal_data_module._PHONE_LABEL_HOME),
    ("212-555-0142", "cell 917-555-0100\thome phone: 212-555-0142",
     personal_data_module._PHONE_LABEL_HOME),
    ("212-555-0142", "cell 917-555-0100\thome telephone: 212-555-0142",
     personal_data_module._PHONE_LABEL_HOME),
    # a cell label written after the number, or combined with home
    ("212-555-0142", "home 212-555-0142 (cell)", personal_data_module._PHONE_LABEL_CELL),
    ("212-555-0142", "home phone: 212-555-0142 (mobile)",
     personal_data_module._PHONE_LABEL_CELL),
    ("212-555-0142", "cell/home: 212-555-0142", personal_data_module._PHONE_LABEL_CELL),
    ("212-555-0142", "mobile/home phone: 212-555-0142",
     personal_data_module._PHONE_LABEL_CELL),
    ("212-555-0142", "home/cell: 212-555-0142", personal_data_module._PHONE_LABEL_CELL),
    # a "(cell)" followed by another number labels that number, not this one
    ("212-555-0142", "(o) 212-555-0142 (cell) 917-555-0100", None),
    # a "(cell)" after a DIFFERENT number does not label this one
    ("212-555-0142", "(h) 212-555-0142; 917-555-0100 (cell)",
     personal_data_module._PHONE_LABEL_HOME),
    # a lone letter after a word, or inside one, is not a label
    ("212-555-0142", "room m: 212-555-0142", None),
    ("212-555-0142", "building c. 212-555-0142", None),
    ("212-555-0142", "abc: 212-555-0142", None),
    ("212-555-0142", "room h: 212-555-0142", None),
    ("212-555-0142", "office\tm. 212-555-0142", personal_data_module._PHONE_LABEL_CELL),
    # a tab or a column separator between the label and the number
    ("212-555-0142", "home\tcell:\t212-555-0142", personal_data_module._PHONE_LABEL_CELL),
    ("212-555-0142", "home | (c) 212-555-0142", personal_data_module._PHONE_LABEL_CELL),
    ("212-555-0142", "cell: 917-555-0100 | h: 212-555-0142",
     personal_data_module._PHONE_LABEL_HOME),
    ("212-555-0142", "home; mobile no. 212-555-0142", personal_data_module._PHONE_LABEL_CELL),
    # trailing parenthetical labels: only cell words, and a tab after is fine
    ("212-555-0142", "home 212-555-0142 (cellular)", personal_data_module._PHONE_LABEL_CELL),
    ("212-555-0142", "home 212-555-0142 (mobile)\tjdoe@gmail.com",
     personal_data_module._PHONE_LABEL_CELL),
    ("212-555-0142", "contact 212-555-0142 (c)", None),
    ("212-555-0142", "contact 212-555-0142 (home)", None),
    # a dict holding two numbers pairs with neither
    ({"cell": "212-555-0142", "office": "917-555-0100"}, "(c) 212-555-0142", None),
    # #1222 (EBYSBC EQGGRB-04): in a run whose numbers all carry a trailing
    # label, "(cell)" labels the number before it
    ("212-555-0142", "tel: 212-555-0142 (cell)\t917-555-0100 (office)",
     personal_data_module._PHONE_LABEL_CELL),
    ("917-555-0100", "tel: 212-555-0142 (cell)\t917-555-0100 (office)", None),
    # ... and "(cell):" with a colon is not a label for the number before it
    ("917-555-0100", "(office): 917-555-0100\t(cell): 212-555-0142", None),
])
def test_nearest_phone_label_pairs_only_the_extracted_number(phone, text, expected):
    parsed = personal_data_module._PhoneNumber.parse(phone)
    assert personal_data_module._nearest_phone_label(parsed, text) == expected


@pytest.mark.parametrize("left, right, expected", [
    ("212.555.0142", "(212) 555-0142", True),
    ("+1 212 555 0142", "212-555-0142", True),
    ("+44 20 7946 0958", "020 7946 0958", False),
    ("20 7946 0958", "+44 20 7946 0958", True),
    # below the seven-digit minimum nothing pairs, even an exact match
    ("55-0142", "55-0142", False),
    ("555-0142", "555-0142", True),
    # ... on either side: a six-digit tail of a seven-digit number
    ("555-0142", "55-0142", False),
    # a longer prefix than a country code is a different number
    ("212-555-0142; 917-555-0100", "917-555-0100", False),
])
def test_phone_number_pairs_across_spellings_and_country_prefixes(left, right, expected):
    phone = personal_data_module._PhoneNumber.parse
    assert phone(left).is_same_number(phone(right)) is expected
    assert phone(right).is_same_number(phone(left)) is expected


def test_the_first_stage4_email_key_decides_the_row(tmp_path):
    """`email` precedes `personal_email`: the institutional address is the
    one read, and it goes to Work."""
    rows, _gen = _render(tmp_path, entries=[
        _a("Contact\tzzz9999@med.cornell.edu",
           {"email": "zzz9999@med.cornell.edu", "personal_email": "jdoe.cvtest@gmail.com"}),
    ])

    assert rows.get("work email:") == "zzz9999@med.cornell.edu"
    assert rows.get("personal email:", "") == ""


@pytest.mark.parametrize("email, work, personal, expected", [
    ("jdoe2.cvtest@gmail.com", None, "jdoe.cvtest@gmail.com", (None, "jdoe.cvtest@gmail.com")),
    ("zzz9998@med.cornell.edu", "zzz9999@med.cornell.edu", None,
     ("zzz9999@med.cornell.edu", None)),
])
def test_route_email_keeps_a_row_first_value(email, work, personal, expected):
    assert personal_data_module._route_email(email, "contact", None, work, personal) == expected


# Spelled out rather than read from the module, so dropping a domain from
# _CONSUMER_EMAIL_DOMAINS fails here.
_CONSUMER_DOMAINS = [
    "gmail.com", "googlemail.com", "yahoo.com", "ymail.com", "yahoo.co.uk",
    "hotmail.com", "hotmail.co.uk", "outlook.com", "live.com", "msn.com",
    "icloud.com", "me.com", "mac.com", "aol.com", "protonmail.com", "proton.me",
    "comcast.net", "verizon.net", "att.net",
]


@pytest.mark.parametrize("domain", _CONSUMER_DOMAINS)
def test_each_consumer_domain_is_a_personal_email(domain):
    assert personal_data_module._is_personal_email(
        personal_data_module._EmailAddress.parse(f"jdoe@{domain}"), "contact", None)


@pytest.mark.parametrize("email, text, field_key, expected", [
    ("zzz9999@med.cornell.edu", "contact\tzzz9999@med.cornell.edu", None, False),
    # a work_email key decides; a personal_email key does not outrank the domain
    ("jdoe.cvtest@gmail.com", "contact", "work_email", False),
    ("jdoe.cvtest@gmail.com", "personal: x", "work_email", False),
    ("zzz9999@med.cornell.edu", "home\tzzz9999@med.cornell.edu", "personal_email", False),
    ("jdoe.cvtest@gmail.com", "contact", "email", True),
    # the address is matched case- and space-insensitively
    (" JDoe.CVTest@Gmail.com ", "contact\tjdoe.cvtest@gmail.com", None, True),
    (" JDoe.CVTest@Gmail.com ", "work email: jdoe.cvtest@gmail.com", None, False),
    # every work word labels it
    ("jdoe.cvtest@gmail.com", "work email: jdoe.cvtest@gmail.com", None, False),
    ("jdoe.cvtest@gmail.com", "office email: jdoe.cvtest@gmail.com", None, False),
    ("jdoe.cvtest@gmail.com", "business email: jdoe.cvtest@gmail.com", None, False),
    ("jdoe.cvtest@gmail.com", "institution: jdoe.cvtest@gmail.com", None, False),
    # ... written after the address too, but only as its own parenthetical
    ("jdoe.cvtest@gmail.com", "jdoe.cvtest@gmail.com (work)", None, False),
    ("jdoe.cvtest@gmail.com", "jdoe.cvtest@gmail.com, office: 1300 york ave", None, True),
    # every separator ends the label zone of the field before it
    ("jdoe.cvtest@gmail.com", "office: 1300 york ave\tjdoe.cvtest@gmail.com", None, True),
    ("jdoe.cvtest@gmail.com", "office: 1300 york ave\njdoe.cvtest@gmail.com", None, True),
    ("jdoe.cvtest@gmail.com", "office: 1300 york ave; jdoe.cvtest@gmail.com", None, True),
    ("jdoe.cvtest@gmail.com", "office: 1300 york ave | jdoe.cvtest@gmail.com", None, True),
    # ... the LAST separator before the address, not the first
    ("jdoe.cvtest@gmail.com", "contact\toffice: 1300 york ave\tjdoe.cvtest@gmail.com",
     None, True),
    # a parenthetical further along labels something else
    ("jdoe.cvtest@gmail.com", "jdoe.cvtest@gmail.com\tfax (office) 212-555-0199", None, True),
    # an address not found in the text is judged on the whole text
    ("jdoe.cvtest@gmail.com", "work email: jdoe.cvtest at gmail", None, False),
])
def test_is_personal_email_reads_the_key_the_domain_and_the_label(
        email, text, field_key, expected):
    parsed = personal_data_module._EmailAddress.parse(email)
    assert personal_data_module._is_personal_email(parsed, text, field_key) is expected


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


@pytest.mark.parametrize("run_id", ["QZKMRT", "AB1CDE"])
def test_a_run_id_is_never_printed_as_the_name(tmp_path, run_id):
    """#457: a web run's uid IS its run id. With no name from cv_owner, the
    A entries or the source, the cover must read as missing, not "Qzkmrt"."""
    _rows, _gen = _render(tmp_path, entries=[], cv_owner={"last_name": run_id},
                          document_uid=run_id)

    assert _rendered_name(tmp_path / "out.docx") == ""


def test_a_run_id_does_not_displace_a_name_cv_owner_supplied(tmp_path):
    _rows, _gen = _render(tmp_path, entries=[], cv_owner={"full_name": "Jane Q. Example"},
                          document_uid="QZKMRT")

    assert _rendered_name(tmp_path / "out.docx") == "Jane Q. Example"


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


def test_run_stage6_forwards_original_doc_path_to_the_fallback(tmp_path):
    """The one line that makes --source-dir do anything, driven end to end.

    Every other test here calls ``WCMTemplateGenerator.generate`` directly and
    scripts/test_render_gate_integration.py stubs stage 6 out, so between them
    nothing exercised ``run_stage6``'s own forwarding: deleting the
    ``original_doc_path=`` argument from its ``generate`` call left the whole
    suite green while making --source-dir inert again, which is the exact
    state #550 exists to fix. This drives the real ``run_stage6``.
    """
    source = tmp_path / "source.docx"
    _make_source_docx(source)
    payload = {"document_uid": "TESTPDWB", "entries": [_a("Personal Data")]}
    ip = tmp_path / "in.json"
    ip.write_text(json.dumps(payload))

    without = tmp_path / "without.docx"
    run_stage6(input_path=str(ip), output_path=str(without), verbose=False)
    assert _personal_data_row_values(without)["work email:"] == "", (
        "no source document was supplied, so the fallback has nothing to recover")

    with_source = tmp_path / "with.docx"
    run_stage6(input_path=str(ip), output_path=str(with_source), verbose=False,
               original_doc_path=str(source))
    rows = _personal_data_row_values(with_source)
    assert rows["work email:"] == "plo4@pitt.edu"
    assert rows["office telephone:"] == "412-623-7764"
    assert "5117 Centre Avenue" in rows["office address:"]


@pytest.mark.parametrize("raw, text, expected_cell, expected_office", [
    # The Indian mobile grouping is 5+5. A pattern whose groups cap at 4
    # digits fails at the "+91 " start, slides forward, and matches "91 9876"
    # out of the middle -- a truncated number rendered as the person's phone.
    ("+91 98765 43210; +91 22 6666 7777",
     "Cell phone: +91 98765 43210; Office phone: +91 22 6666 7777",
     "+91 98765 43210", "+91 22 6666 7777"),
    ("+91-98765-43210; +91-22-6666-7777",
     "Mobile: +91-98765-43210; Work: +91-22-6666-7777",
     "+91-98765-43210", "+91-22-6666-7777"),
    # Same failure without a '+': the leading country digit is dropped.
    ("1-800-555-0199; (212) 555-9999",
     "Mobile: 1-800-555-0199; Work: (212) 555-9999",
     "1-800-555-0199", "(212) 555-9999"),
    ("+44 7700 900123; +44 20 7946 0958",
     "Cell: +44 7700 900123; Office: +44 20 7946 0958",
     "+44 7700 900123", "+44 20 7946 0958"),
])
def test_split_phone_entry_never_renders_a_truncated_number(
        tmp_path, raw, text, expected_cell, expected_office):
    rows, _ = _render(tmp_path, [_a(text, {"phone": raw})])
    assert rows["cell phone:"] == expected_cell
    assert rows["office telephone:"] == expected_office


# --------------------------------------------------------------------------
# #820 round 2: the docx recovery goes through the same protected-data gate
# as the entry path (#820's "Related" note on #550/#730). With --source-dir,
# web198's source table had a "Home Address:" label cell whose VALUE cell was
# a "Birth Place:" line, and the scan rendered that line into the Office
# address cell on both arms.
# --------------------------------------------------------------------------

def _docx_texts(docx_path) -> str:
    doc = Document(str(docx_path))
    return "\n".join(n.text or "" for n in doc.element.body.iter(W_T))


def test_recovered_address_value_that_is_a_birth_place_line_is_withheld(tmp_path):
    """The web198 shape: a label cell an address classifier accepts, a
    value cell that is protected data. Nothing of it reaches the table,
    and the withheld notice + comment name the category."""
    src = tmp_path / "source.docx"
    _make_label_table_docx(src, rows=[
        ("Home Address: 1 Example Street", "Birth Place: Example City, EX"),
    ])
    rows, gen = _render(tmp_path, entries=[_a("Office phone: 212-555-0100",
                                              {"phone": "212-555-0100"})],
                        original_doc_path=str(src))
    rendered = _docx_texts(tmp_path / "out.docx")
    assert "Example City" not in rendered, "a birth place rendered as the Office address"
    assert rows.get("office address:", "") == ""
    # #730: this row is a side-by-side layout (the home address and the
    # birth place are separate cells), so the home cell is the withheld item
    # and the birth-place cell is simply never read into a slot.
    assert [i.category for i in gen._pii_result.withheld] == [CAT_HOME_CONTACT]
    assert gen._pii_result.withheld[0].section_label == "Personal Data"
    assert gen._pii_result.withheld[0].entry_index is None


def test_recovery_stops_at_a_withheld_office_address_row_instead_of_taking_the_next_one(tmp_path):
    """#820 R3 finding 1: web198's source table has a withheld office_address
    row followed by a second, unrelated office_address-classified row.
    Before this fix, the first row's content was withheld in full (leaving
    `office_address` empty) and the `not office_address` guard let the
    SECOND row fill the slot instead -- rendering an unrelated line
    (web198: the next address-classified row's value) as the office
    address. Once a slot's row content has been policy-denied, the slot
    must render EMPTY for the rest of the document, not take whatever
    office_address row comes next."""
    src = tmp_path / "source.docx"
    _make_label_table_docx(src, rows=[
        ("Office Address:", "Place of Birth: Example City, EX"),
        ("Office Address:", "42 Example Ave, Example City, EX 00000"),
    ])
    rows, gen = _render(tmp_path, entries=[], original_doc_path=str(src))
    rendered = _docx_texts(tmp_path / "out.docx")
    assert rows.get("office address:", "") == "", (
        "the office address slot took the SECOND row's value after the "
        "first row's content was withheld"
    )
    assert "42 Example Ave" not in rendered
    assert "Example City" not in rendered
    assert [i.category for i in gen._pii_result.withheld] == ["place of birth"]


def test_recovery_takes_the_next_row_when_the_first_had_nothing_withheld(tmp_path):
    """#820 R4 (verifier round 3 finding 1, MR3c): the guard at
    `personal_data.py` around ``office_address_withheld`` must tell "this
    row supplied no address" apart from "this row's address was withheld" --
    only the latter should stop the scan. A phone-only Office Address row
    parses to an empty address block but withholds nothing (there is no
    protected data in it), so `len(withheld) > withheld_before` stays False
    and `office_address_withheld` must stay False too: the SECOND, genuine
    address row still fills the slot. Dropping that conjunct (stopping on
    ANY empty-address row) would leave the slot empty instead -- see the
    mutant proof in the report."""
    src = tmp_path / "source.docx"
    _make_label_table_docx(src, rows=[
        ("Office Address:", "Phone: 212-555-0100"),
        ("Office Address:", "42 Example Ave, Example City, EX 00000"),
    ])
    rows, gen = _render(tmp_path, entries=[], original_doc_path=str(src))
    assert "42 Example Ave" in rows.get("office address:", ""), (
        "the office address slot stayed empty even though nothing in the "
        "first (phone-only) row was withheld"
    )
    assert gen._pii_result.withheld == []


def test_recovered_address_keeps_its_clean_lines_and_drops_the_protected_one(tmp_path):
    """Line-level, like the entry path's value-level gate: the street lines
    render, the one protected line inside the same cell does not."""
    src = tmp_path / "source.docx"
    _make_label_table_docx(src, rows=[
        ("BUSINESS ADDRESS:", "1 Example Street\nExample City, EX 00000\n"
                              "Date of Birth: 01/02/1970"),
    ])
    rows, gen = _render(tmp_path, entries=[], original_doc_path=str(src))
    assert "1 Example Street" in rows["office address:"]
    assert "01/02/1970" not in _docx_texts(tmp_path / "out.docx")
    assert [i.category for i in gen._pii_result.withheld] == ["date of birth"]


def test_recovered_phone_email_and_name_from_a_protected_row_are_withheld(tmp_path):
    """Every recovered value kind goes through the gate, not just the
    address: an emergency contact's phone and email in a phone/email row,
    and a spouse's name in a name row."""
    src = tmp_path / "source.docx"
    _make_label_table_docx(src, rows=[
        ("Name:", "Wife's name: Pat Example"),
        ("Phone:", "Emergency contact: Pat Example 212-555-0199"),
        ("E-mail:", "Emergency contact: pat.example@example.com"),
    ])
    rows, gen = _render(tmp_path, entries=[], original_doc_path=str(src))
    rendered = _docx_texts(tmp_path / "out.docx")
    assert "Pat Example" not in rendered
    assert "212-555-0199" not in rendered
    assert "pat.example@example.com" not in rendered
    assert sorted(i.category for i in gen._pii_result.withheld) == sorted(
        ["spouse", "emergency contact", "emergency contact"])


def test_recovered_paragraph_email_inside_a_protected_fragment_is_withheld(tmp_path):
    """The paragraph email scan (no table) is gated the same way, and keeps
    looking past a withheld one."""
    src = tmp_path / "source.docx"
    doc = Document()
    doc.add_paragraph("Emergency contact: Pat Example, pat.example@example.com")
    doc.add_paragraph("Work: roe@med.example.edu")
    doc.save(str(src))
    rows, gen = _render(tmp_path, entries=[], original_doc_path=str(src))
    assert rows["work email:"] == "roe@med.example.edu"
    assert "pat.example@example.com" not in _docx_texts(tmp_path / "out.docx")
    assert [i.category for i in gen._pii_result.withheld] == ["emergency contact"]


def test_a_clean_recovery_records_nothing_withheld(tmp_path):
    """Negative control: the positive-control fixture (a real contact block)
    is recovered in full and the withheld list stays empty -- no notice, no
    comment."""
    src = tmp_path / "source.docx"
    _make_source_docx(src)
    rows, gen = _render(tmp_path, entries=[], original_doc_path=str(src))
    assert rows["work email:"] == "plo4@pitt.edu"
    assert gen._pii_result.withheld == []
    assert "withheld" not in _docx_texts(tmp_path / "out.docx")


# --------------------------------------------------------------------------
# #730: a Home-Address-labelled source row is withheld (#821) and never
# becomes the Office address, whichever order it sits in.
# --------------------------------------------------------------------------

_HOME_ROW = ("Home Address:", "12 Elm Street\nRye, NY 10580")
_BUSINESS_ROW = ("Business Address:", "42 Example Ave\nExample City, EX 00000")


@pytest.mark.parametrize("rows", [
    [_HOME_ROW, _BUSINESS_ROW],
    [_BUSINESS_ROW, _HOME_ROW],
], ids=["home-first", "business-first"])
def test_a_home_address_row_never_becomes_the_office_address(tmp_path, rows):
    src = tmp_path / "source.docx"
    _make_label_table_docx(src, rows=rows)
    rendered_rows, gen = _render(tmp_path, entries=[], original_doc_path=str(src))
    rendered = _docx_texts(tmp_path / "out.docx")
    assert "42 Example Ave" in rendered_rows["office address:"]
    assert "12 Elm Street" not in rendered
    assert [i.category for i in gen._pii_result.withheld] == [CAT_HOME_CONTACT]


def test_a_lone_home_address_row_leaves_office_address_empty(tmp_path):
    src = tmp_path / "source.docx"
    _make_label_table_docx(src, rows=[_HOME_ROW])
    rows, gen = _render(tmp_path, entries=[], original_doc_path=str(src))
    assert rows.get("office address:", "") == ""
    assert "12 Elm Street" not in _docx_texts(tmp_path / "out.docx")
    assert [i.category for i in gen._pii_result.withheld] == [CAT_HOME_CONTACT]


@pytest.mark.parametrize("label,expected", [
    ("Home Address:", "home_address"),
    ("HOME ADDRESS:", "home_address"),
    ("Home address (private):", "home_address"),
    ("Residential Address:", "home_address"),
    ("Home/Office Address:", "home_address"),
    ("Business Address:", "office_address"),
    ("Office Address:", "office_address"),
    ("Homepage address:", "office_address"),
    ("Homeland Security Address:", "office_address"),
    ("Homer Address:", "office_address"),
    ("Address:", "office_address"),
    ("Mailing Address:", "office_address"),
    ("Home:", None),
    ("Home page: www.example.org", None),
    ("Residency:", None),
    ("Resident Address:", "office_address"),
])
def test_home_address_label_classification_over_match_probes(label, expected):
    assert personal_data_module._classify_contact_label(label) == expected


@pytest.mark.parametrize("email_line", [
    "Email address:\tjdoe@example.org", "E-mail address: jdoe@example.org",
    "Email: jdoe@example.org",
])
def test_an_email_line_inside_the_business_address_cell_is_not_address_text(
        tmp_path, email_line):
    """#730 comment 2 (web198): with the home row skipped, the Business
    Address cell is reached, and its "Email address:" line must be consumed as
    an email, not kept as the Office address."""
    src = tmp_path / "source.docx"
    _make_label_table_docx(src, rows=[
        _HOME_ROW, ("Business Address:", f"{email_line}\n42 Example Ave")])
    rows, _gen = _render(tmp_path, entries=[], original_doc_path=str(src))
    assert rows["office address:"] == "42 Example Ave"
    assert rows["work email:"] == "jdoe@example.org"


def test_an_address_line_that_merely_mentions_email_is_kept(tmp_path):
    src = tmp_path / "source.docx"
    _make_label_table_docx(src, rows=[
        ("Business Address:", "Email Services Building\n42 Example Ave")])
    rows, _gen = _render(tmp_path, entries=[], original_doc_path=str(src))
    assert rows["office address:"] == "Email Services Building\n42 Example Ave"


# --------------------------------------------------------------------------
# #730 verifier round: home-labelled PHONE/EMAIL rows, side-by-side layouts,
# withheld-count dedup, "Permanent Address:".
# --------------------------------------------------------------------------

@pytest.mark.parametrize("label", [
    "Home Phone:", "Home phone/fax:", "Home Telephone:", "HOME PHONE:",
    "Residential Phone:",
])
def test_a_home_phone_row_fills_no_slot_and_is_withheld(tmp_path, label):
    src = tmp_path / "source.docx"
    _make_label_table_docx(src, rows=[(label, "914-555-0111")])
    rows, gen = _render(tmp_path, entries=[], original_doc_path=str(src))
    assert rows.get("office telephone:", "") == ""
    assert "914-555-0111" not in _docx_texts(tmp_path / "out.docx")
    assert [i.category for i in gen._pii_result.withheld] == [CAT_HOME_CONTACT]


@pytest.mark.parametrize("label", ["Home E-mail:", "Home Email:",
                                   "Home Email Address:"])
def test_a_home_email_row_never_becomes_the_work_email(tmp_path, label):
    src = tmp_path / "source.docx"
    _make_label_table_docx(src, rows=[(label, "jdoe@example.org"),
                                      ("E-mail:", "jdoe@example.edu")])
    rows, gen = _render(tmp_path, entries=[], original_doc_path=str(src))
    assert rows["work email:"] == "jdoe@example.edu"
    assert "jdoe@example.org" not in rows["work email:"]
    assert gen._pii_result.withheld == [], "#821 renders personal email"


@pytest.mark.parametrize("other,slot,expected", [
    ("Business Phone: 212-555-0100", "office telephone:", "212-555-0100"),
    ("Email: jdoe@example.edu", "work email:", "jdoe@example.edu"),
    ("Business Address: 42 Example Ave", "office address:", "42 Example Ave"),
])
def test_a_side_by_side_home_cell_skips_only_its_own_cell(
        tmp_path, other, slot, expected):
    src = tmp_path / "source.docx"
    _make_label_table_docx(src, rows=[("Home Address: 12 Elm St", other)])
    rows, _gen = _render(tmp_path, entries=[], original_doc_path=str(src))
    assert rows[slot] == expected
    assert "12 Elm St" not in _docx_texts(tmp_path / "out.docx")


def test_side_by_side_home_phone_cell_skips_only_its_own_cell(tmp_path):
    src = tmp_path / "source.docx"
    _make_label_table_docx(src, rows=[
        ("Home Phone: 914-555-0111", "Business Phone: 212-555-0100")])
    rows, _gen = _render(tmp_path, entries=[], original_doc_path=str(src))
    assert rows["office telephone:"] == "212-555-0100"
    assert "914-555-0111" not in _docx_texts(tmp_path / "out.docx")


def test_a_repeated_home_address_is_withheld_once_not_twice(tmp_path):
    """Stage 4 already withheld the home address; the docx row repeats it."""
    src = tmp_path / "source.docx"
    _make_label_table_docx(src, rows=[_HOME_ROW, ("Home Phone:", "914-555-0111")])
    _rows, gen = _render(
        tmp_path, entries=[_a("Home Address: 12 Elm Street, Rye, NY 10580")],
        original_doc_path=str(src))
    assert [i.category for i in gen._pii_result.withheld].count(
        CAT_HOME_CONTACT) == 1


@pytest.mark.parametrize("label,expected", [
    ("Permanent Address:", "home_address"),
    ("PERMANENT ADDRESS:", "home_address"),
    ("Home Phone:", "home_phone"),
    ("Home phone/fax:", "home_phone"),
    ("Home Telephone:", "home_phone"),
    ("Home E-mail:", "home_email"),
    ("Home Email Address:", "home_email"),
    ("Permanent Email:", "work_email"),
    ("Permanent Phone:", "office_phone"),
    ("Homepage Phone:", "office_phone"),
    ("Homer Email:", "work_email"),
    ("Home page:", None),
    ("Cell phone:", "office_phone"),
    ("Personal email:", "work_email"),
    ("Mailing Address:", "office_address"),
    ("Permanently Address:", "office_address"),
    ("Business Fax:", None),
    ("Fax:", None),
    ("Business Phone/Fax:", "office_phone"),
    ("Faxon Address:", "office_address"),
    ("Facsimile Address:", "office_address"),
])
def test_home_phone_email_permanent_label_classification(label, expected):
    assert personal_data_module._classify_contact_label(label) == expected


def test_a_business_phone_cell_carrying_its_own_number_beats_the_fax_beside_it(tmp_path):
    """web198's shape once its Home Phone row is skipped: the Business Phone
    number sits in the label cell and cell 1 is a fax line, which must not
    render as the Office telephone."""
    src = tmp_path / "source.docx"
    _make_label_table_docx(src, rows=[
        ("Home Phone: 914-555-0111", "Citizenship: USA"),
        ("Business Phone: 412-555-0100", "Business Fax: 412-555-0199")])
    rows, _gen = _render(tmp_path, entries=[], original_doc_path=str(src))
    assert rows["office telephone:"] == "412-555-0100"
    rendered = _docx_texts(tmp_path / "out.docx")
    assert "412-555-0199" not in rendered and "914-555-0111" not in rendered


def test_a_multiline_phone_label_cell_over_an_empty_cell_is_left_alone(tmp_path):
    """Near-miss for the side-by-side rule: the whole block sits in cell 0
    (Dogan shape) and cell 1 is empty, so nothing is taken from it."""
    src = tmp_path / "source.docx"
    _make_label_table_docx(src, rows=[
        ("Office Phone:\t212-555-0100\nFax:\t212-555-0199", "")])
    rows, _gen = _render(tmp_path, entries=[], original_doc_path=str(src))
    assert rows.get("office telephone:", "") == ""


def test_a_single_line_phone_label_cell_over_an_empty_cell_is_left_alone(tmp_path):
    src = tmp_path / "source.docx"
    _make_label_table_docx(src, rows=[("Business Phone: 212-555-0100", "")])
    rows, _gen = _render(tmp_path, entries=[], original_doc_path=str(src))
    assert rows.get("office telephone:", "") == ""


def test_a_multiline_phone_label_cell_beside_a_filled_cell_is_not_taken_as_one_number(tmp_path):
    src = tmp_path / "source.docx"
    _make_label_table_docx(src, rows=[
        ("Office Phone:\t212-555-0100\nFax:\t212-555-0199", "Room 4B")])
    rows, _gen = _render(tmp_path, entries=[], original_doc_path=str(src))
    assert "Fax" not in rows.get("office telephone:", "")
    assert "212-555-0199" not in _docx_texts(tmp_path / "out.docx")


@pytest.mark.parametrize("label,other", [
    ("Phone: (office)", "212-555-0100"),
    ("Telephone: Work", "212-555-0100"),
    ("Office Phone: ext. 1234", "212-555-0100"),
    ("Phone: 55-0100", "212-555-0100"),
])
def test_a_phone_label_cell_without_a_number_still_reads_cell_one(tmp_path, label, other):
    src = tmp_path / "source.docx"
    _make_label_table_docx(src, rows=[(label, other)])
    rows, _gen = _render(tmp_path, entries=[], original_doc_path=str(src))
    assert rows["office telephone:"] == "212-555-0100"


@pytest.mark.parametrize("label", [
    "Permanent Office Address:", "Permanent Business Address:",
    "Permanent Work Address:", "Office Permanent Address:",
])
def test_a_permanent_office_or_business_address_is_an_office_address(tmp_path, label):
    src = tmp_path / "source.docx"
    _make_label_table_docx(src, rows=[(label, "42 Example Ave\nExample City, EX 00000")])
    rows, gen = _render(tmp_path, entries=[], original_doc_path=str(src))
    assert "42 Example Ave" in rows["office address:"]
    assert gen._pii_result.withheld == []


@pytest.mark.parametrize("label,expected", [
    ("Permanent Office Address:", "office_address"),
    ("Permanent Business Address:", "office_address"),
    ("Permanent Address:", "home_address"),
    ("Permanent Home Address:", "home_address"),
    ("Home/Office Address:", "home_address"),
    ("Permanent Home/Office Address:", "home_address"),
    ("Permanently Office Address:", "office_address"),
])
def test_permanent_precedence_classification(label, expected):
    assert personal_data_module._classify_contact_label(label) == expected


def test_a_seven_digit_local_number_in_the_label_cell_is_a_number(tmp_path):
    """Boundary of the digit-count guard: 7 digits is a phone number."""
    src = tmp_path / "source.docx"
    _make_label_table_docx(src, rows=[("Business Phone: 555-0100", "Room 4B")])
    rows, _gen = _render(tmp_path, entries=[], original_doc_path=str(src))
    assert rows["office telephone:"] == "555-0100"


# #730 round 3: a phone VALUE can carry a home-marked number; drop that
# segment on both the cell-0 (embedded) and cell-1 paths.

@pytest.mark.parametrize("label,other", [
    ("Phone: (o) 212-555-0100; (h) 914-555-0111", "Fax: 212-555-0199"),
    ("Phone: 212-555-0100 (office), 914-555-0111 (home)", "Email: jdoe@example.edu"),
    ("Telephone: 212-555-0100 / Home 914-555-0111", "Fax: 212-555-0199"),
    ("Phone:", "(o) 212-555-0100; (h) 914-555-0111"),
    ("Phone:", "914-555-0111 (home)\n212-555-0100"),
    ("Phone:", "H: 914-555-0111; O: 212-555-0100"),
    ("Phone:", "(h) 914-555-0111 / (o) 212-555-0100"),
])
def test_a_home_marked_number_inside_a_phone_value_is_dropped(tmp_path, label, other):
    src = tmp_path / "source.docx"
    _make_label_table_docx(src, rows=[(label, other)])
    rows, gen = _render(tmp_path, entries=[], original_doc_path=str(src))
    assert "914-555-0111" not in rows.get("office telephone:", "")
    assert "212-555-0100" in rows["office telephone:"]
    assert "914-555-0111" not in _docx_texts(tmp_path / "out.docx")
    assert [i.category for i in gen._pii_result.withheld] == [CAT_HOME_CONTACT]


@pytest.mark.parametrize("label,other", [
    ("Phone: (h) 914-555-0111", "Fax: 212-555-0199"),
    ("Phone: 914-555-0111 (home)", "Fax: 212-555-0199"),
    ("Phone:", "(h) 914-555-0111"),
    ("Phone: 914-555-0111 (h/o)", "Fax: 212-555-0199"),
    ("Phone:", "914-555-0111 (O/H)"),
])
def test_a_phone_value_that_is_only_a_home_number_leaves_the_slot_empty(tmp_path, label, other):
    src = tmp_path / "source.docx"
    _make_label_table_docx(src, rows=[(label, other)])
    rows, gen = _render(tmp_path, entries=[], original_doc_path=str(src))
    assert rows.get("office telephone:", "") == ""
    assert "914-555-0111" not in _docx_texts(tmp_path / "out.docx")
    assert [i.category for i in gen._pii_result.withheld] == [CAT_HOME_CONTACT]


@pytest.mark.parametrize("value", [
    "(hosp) 212-555-0100", "Hospital 212-555-0100", "Homer St 212-555-0100",
    "Hr: 212-555-0100", "(o) 212-555-0100", "212/555-0100", "(212) 555-0100",
    "Reserve 212-555-0100", "212-555-0100 ext. 5", "Shh: 212-555-0100",
    "(o)/(w) 212-555-0100", "212-555-0100,212-555-0199",
    "212-555-0100 / 212-555-0199",
])
def test_phone_values_without_a_home_marker_are_left_untouched(value):
    withheld = []
    assert personal_data_module._drop_home_phone_segments(value, withheld) == value
    assert withheld == []


def test_a_bare_extension_in_the_label_cell_does_not_count_toward_the_number(tmp_path):
    src = tmp_path / "source.docx"
    _make_label_table_docx(src, rows=[("Phone: x1234567", "212-555-0100"),
                                      ("Phone: ext. 1234567", "212-555-0199")])
    rows, _gen = _render(tmp_path, entries=[], original_doc_path=str(src))
    assert rows["office telephone:"] == "212-555-0100"


@pytest.mark.parametrize("value,expected", [
    ("914/555-0111 (home)", None),
    ("Res. 914-555-0111", None),
    ("Res: 914-555-0111; 212-555-0100", "212-555-0100"),
    ("Residence 914-555-0111", None),
])
def test_home_segment_edge_shapes(value, expected):
    assert personal_data_module._drop_home_phone_segments(value, []) == expected


def test_a_home_number_inside_a_business_address_cell_phone_line_is_dropped(tmp_path):
    src = tmp_path / "source.docx"
    _make_label_table_docx(src, rows=[
        ("Business Address:", "42 Example Ave\nPhone: (h) 914-555-0111")])
    rows, gen = _render(tmp_path, entries=[], original_doc_path=str(src))
    assert rows.get("office telephone:", "") == ""
    assert "914-555-0111" not in _docx_texts(tmp_path / "out.docx")
    assert [i.category for i in gen._pii_result.withheld] == [CAT_HOME_CONTACT]


# --------------------------------------------------------------------------
# #732: discover_original_doc=False renders with no source document at all,
# whatever SAMPLE_CV_DIR or the CWD happen to hold.
# --------------------------------------------------------------------------

_DISCOVERABLE_EMAIL = "guess@example.com"


def _plant_discoverable_source(tmp_path, monkeypatch):
    """A <uid>.docx in a fake SAMPLE_CV_DIR, which only the guess can find."""
    sample_dir = tmp_path / "sample_cvs"
    sample_dir.mkdir()
    _make_label_table_docx(sample_dir / "TESTPDWB.docx",
                           [("Work email:", _DISCOVERABLE_EMAIL)])
    monkeypatch.setattr("unified_pipeline.stage_6_word_template.SAMPLE_CV_DIR", sample_dir)


def _render_uid_only(tmp_path, **kwargs) -> dict:
    ip, op = tmp_path / "in.json", tmp_path / "out.docx"
    ip.write_text(json.dumps({"document_uid": "TESTPDWB", "entries": []}))
    run_stage6(str(ip), str(op), verbose=False, **kwargs)
    return _personal_data_row_values(op)


def test_discovery_is_the_default_and_finds_the_sample_dir_docx(tmp_path, monkeypatch):
    _plant_discoverable_source(tmp_path, monkeypatch)

    rows = _render_uid_only(tmp_path)

    assert rows.get("work email:") == _DISCOVERABLE_EMAIL


def test_discover_original_doc_false_skips_the_sample_dir_guess(tmp_path, monkeypatch):
    _plant_discoverable_source(tmp_path, monkeypatch)

    rows = _render_uid_only(tmp_path, discover_original_doc=False)

    assert rows.get("work email:", "") == ""


def test_discover_original_doc_false_still_honours_an_explicit_source(tmp_path, monkeypatch):
    _plant_discoverable_source(tmp_path, monkeypatch)
    explicit = tmp_path / "explicit.docx"
    _make_label_table_docx(explicit, [("Work email:", "explicit@example.com")])

    rows = _render_uid_only(tmp_path, original_doc_path=str(explicit),
                            discover_original_doc=False)

    assert rows.get("work email:") == "explicit@example.com"
