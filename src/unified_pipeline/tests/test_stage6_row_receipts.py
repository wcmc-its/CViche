"""Row receipts (#534): an entry counts as written only while a row a body
section wrote for it is still in the document body.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_row_receipts.py -p no:cacheprovider
"""

import json
import sys
import zipfile
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from docx import Document  # noqa: E402
from docx.oxml.ns import qn  # noqa: E402
from lxml import etree  # noqa: E402

from unified_pipeline.doctor.lints.render import (  # noqa: E402
    lint_renderer_row_receipts,
)
from unified_pipeline.stage6.row_receipts import (  # noqa: E402
    LINTED_SECTIONS,
    LOST_TEXT_CHARS,
    DiscardingLedger,
    RowReceiptLedger,
)
from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402


def _entry(idx, text="Fictional Award, Imaginary Society, 2001", code="H"):
    return {"element_idx_start": idx, "taxonomy_code": code, "text": text}


def _table():
    return Document().add_table(rows=1, cols=2)


def _ledger(section="honors"):
    ledger = RowReceiptLedger()
    ledger.begin_section(section)
    return ledger


def _only(receipts):
    assert len(receipts) == 1
    return receipts[0]


def _counts(receipt):
    return receipt.given, receipt.written, receipt.elsewhere, len(receipt.lost)


def test_a_row_still_in_the_body_is_written():
    table, ledger, entry = _table(), _ledger(), _entry(1)
    ledger.wrote(table.add_row()._tr, entry)
    assert _counts(_only(ledger.receipts({"honors": [entry]}))) == (1, 1, 0, 0)


def test_a_row_written_then_removed_is_lost():
    """The GNONHA shape (#454): a second filler clears the table the first
    one wrote, so the row was written but is not in the document."""
    table, ledger, entry = _table(), _ledger("service"), _entry(7, text="Fictional  Board,\nChair")
    row = table.add_row()
    ledger.wrote(row._tr, entry)
    table._tbl.remove(row._tr)
    receipt = _only(ledger.receipts({"service": [entry]}))
    assert _counts(receipt) == (1, 0, 0, 1)
    assert [(lost.code, lost.element_idx_start, lost.text) for lost in receipt.lost] == [
        ("H", 7, "Fictional Board, Chair")]


def test_a_set_aside_entry_is_elsewhere_not_lost():
    ledger, lost, aside = _ledger(), _entry(1), _entry(3)
    ledger.set_aside(aside)
    receipt = _only(ledger.receipts({"honors": [lost, aside]}))
    assert _counts(receipt) == (2, 0, 1, 1)
    assert [entry.element_idx_start for entry in receipt.lost] == [1]


def test_a_row_the_appendix_or_no_section_wrote_is_elsewhere_not_written():
    """The Appendix writer and the recovery passes after the sections both
    record through the shared comment hook; neither is the section's row."""
    table, ledger = _table(), RowReceiptLedger()
    in_appendix, recovered = _entry(1), _entry(2)
    ledger.begin_section("appendix")
    ledger.wrote(table.add_row()._tr, in_appendix)
    ledger.end_section()
    ledger.wrote(table.add_row()._tr, recovered)
    assert _counts(_only(ledger.receipts({"honors": [in_appendix, recovered]}))) == (
        2, 0, 2, 0)


def test_rows_attach_to_the_entry_begin_entry_marked_until_the_section_ends():
    """`first`'s own row is later wiped; a row written after its section
    ended, with no `begin_entry`, must not be credited to it."""
    table, ledger, first, second = _table(), _ledger(), _entry(1), _entry(2)
    ledger.begin_entry(second)
    ledger.wrote(table.add_row()._tr)
    ledger.begin_entry(first)
    own_row = table.add_row()
    ledger.wrote(own_row._tr)
    ledger.end_section()
    table._tbl.remove(own_row._tr)
    ledger.begin_section("honors")
    ledger.wrote(table.add_row()._tr)  # no cursor: belongs to nobody
    receipt = _only(ledger.receipts({"honors": [first, second]}))
    assert _counts(receipt) == (2, 1, 0, 1)
    assert [lost.element_idx_start for lost in receipt.lost] == [1]


def test_a_derived_copy_vouches_for_its_source_by_element_index():
    """A renderer that splits or reroutes writes a copy of the entry; the
    copy carries the source's element_idx_start, so the source is written."""
    table, ledger, source = _table(), _ledger("service"), _entry(5)
    ledger.wrote(table.add_row()._tr, dict(source, text="one line of it"))
    assert _counts(_only(ledger.receipts({"service": [source]}))) == (1, 1, 0, 0)


def test_only_listed_sections_are_linted_and_text_is_capped():
    ledger = _ledger()
    entry = _entry(1, text="x" * (LOST_TEXT_CHARS + 50))
    receipts = ledger.receipts({"honors": [entry], "presentations": [entry]})
    assert [r.linted for r in receipts] == ["honors" in LINTED_SECTIONS, False]
    assert len(receipts[0].lost[0].text) == LOST_TEXT_CHARS
    assert receipts[0].to_json()["lost"][0]["element_idx_start"] == 1


def test_534_licensure_and_board_certification_receipts_are_linted():
    """Both sections record every row they write and set aside what they
    write none for on purpose (#2084), so their receipts are linted; an
    unlisted section's still is not."""
    receipts = _ledger().receipts({"licensure": [], "board_certification": [], "presentations": []})
    assert [(r.section, r.linted) for r in receipts] == [
        ("licensure", True), ("board_certification", True), ("presentations", False)]


def test_the_discarding_default_records_nothing():
    """A generator built without `__init__` writes through this one."""
    table, ledger, entry = _table(), DiscardingLedger(), _entry(1)
    ledger.begin_section("honors")
    ledger.begin_entry(entry)
    ledger.wrote(table.add_row()._tr)
    ledger.wrote(table.add_row()._tr, entry)
    ledger.set_aside(entry)
    assert _counts(_only(ledger.receipts({"honors": [entry]}))) == (1, 0, 0, 1)


def test_a_table_row_index_matches_a_derived_copy_too():
    """A table-sourced entry's position is a string ("96.3": element 96,
    row 3); a renderer's copy of it (`{**entry, ...}`, service's Q2
    reroutes) vouches for it the same as an int index does."""
    table, ledger, source = _table(), _ledger("service"), _entry("96.3")
    ledger.wrote(table.add_row()._tr, {**source, "taxonomy_code": "Q3"})
    assert _counts(_only(ledger.receipts({"service": [source]}))) == (1, 1, 0, 0)
    assert ledger.receipts({"service": [_entry("96.4")]})[0].lost[0].element_idx_start == "96.4"


def test_534_end_entry_stops_crediting_the_marked_entry():
    """After `end_entry`, a row written without an entry belongs to no one,
    so the entry whose own row is gone stays lost."""
    entry, table = _entry(1), _table()
    ledger = _ledger()
    ledger.begin_entry(entry)
    ledger.end_entry()
    ledger.wrote(table.add_row()._tr)
    receipt = _only(ledger.receipts({"honors": [entry]}))
    assert [lost.element_idx_start for lost in receipt.lost] == [1]


def test_elements_for_an_entry_are_its_own_in_write_order():
    """#1205: the sub-point pass anchors a degree's prose only under a row
    recorded for that entry. Another entry sharing its element index (a
    fan-out sibling, a derived copy) records rows that are not its own, and
    a row written with no entry belongs to no one."""
    table, ledger, entry = _table(), _ledger("education"), _entry(19, code="B1")
    sibling = dict(entry)
    first, second, unowned, siblings = (table.add_row()._tr for _ in range(4))
    ledger.wrote(first, entry)
    ledger.wrote(siblings, sibling)
    ledger.wrote(unowned)
    ledger.end_section()
    ledger.wrote(second, entry)  # outside any section: still the entry's own
    assert [row is mine for row, mine in zip(ledger.elements_for(entry), (first, second),
                                             strict=True)] == [True, True]
    assert ledger.elements_for(sibling) == [siblings]
    assert ledger.elements_for(_entry(19, code="B1")) == []
    assert DiscardingLedger().elements_for(entry) == []


_W_TEXT_TAGS = (qn("w:t"), qn("w:delText"))
_WCM_SOURCE_HEADINGS = ("PERSONAL DATA", "EMPLOYMENT STATUS", "LICENSURE, BOARD CERTIFICATION",
                        "INSTITUTIONAL/HOSPITAL AFFILIATION", "HONORS, AWARDS", "BIBLIOGRAPHY")
_PRINTED_NPI = "NPI Number 1234567893"


def _body_paragraph_texts(docx_path: Path) -> list[str]:
    """Every body paragraph's text, table cells included, read from the raw
    XML: `w:t` inside a `w:ins` and `w:delText` count, which python-docx's
    `paragraph.text` skips."""
    with zipfile.ZipFile(docx_path) as docx:
        body = etree.fromstring(docx.read("word/document.xml")).find(qn("w:body"))
    return ["".join(t.text or "" for t in p.iter(*_W_TEXT_TAGS)) for p in body.iter(qn("w:p"))]


def _wcm_source(path: Path) -> Path:
    """A synthetic WCM-format source whose filled E block also prints an NPI line."""
    doc = Document()
    for heading in _WCM_SOURCE_HEADINGS:
        doc.add_paragraph(heading)
        if heading == "EMPLOYMENT STATUS":
            doc.add_paragraph("Name of Current Employer(s): Imaginary Medical College")
            doc.add_paragraph(_PRINTED_NPI)
    doc.save(str(path))
    return path


def _wire_entries() -> list[dict]:
    def entry(idx, code, text, **fields):
        return {"element_idx_start": idx, "taxonomy_code": code, "text": text, "extracted_fields": fields,
                "hierarchy": ["LICENSURE, BOARD CERTIFICATION"] if code in ("F1", "F2") else []}
    return [
        entry(0, "A", "Name: Pat Q. Example, MD"),
        {**entry(1, "T", "Name of Current Employer(s): Imaginary Medical College"),
         "hierarchy": ["EMPLOYMENT STATUS"]},
        entry(10, "S0", "[S0] ORCID\t0000-0002-1111-2222"),
        entry(11, "S0", "Invented Scholar profile page"),
        entry(30, "F1", _PRINTED_NPI, npi_number="1234567893"),
        entry(31, "F1", "NPI Number 2345678903", npi_number="2345678903"),
        entry(41, "F2", "Imaginary Board of Pretend Medicine, 2005",
              board="Imaginary Board of Pretend Medicine", certification_date="2005"),
        entry(60, "H", "Name of award\tOrganization\tDate awarded"),
        entry(61, "H", "Pretend Honor Award, Organization of Invented Things (1999)"),
        entry(62, "H", "2018 Teaching Award, Example University", award_name="Teaching Award",
              granting_body="Example University", date="2018"),
    ]


def test_534_generate_receipts_s0_bullets_header_rows_copied_lines_and_lints_f1_f2(tmp_path):
    """Wire test through `generate()` (#534): the S0 bullets are written, the
    yearless honors header row is set aside and the award line the header
    check drops stays lost, the NPI line the copied E block prints is
    elsewhere and the one nothing prints stays lost, and Licensure and Board
    Certification are linted, so the doctor reports exactly the two real
    losses. What the document holds is read from its XML, w:ins included."""
    gen = WCMTemplateGenerator(verbose=False)
    gen._reconsider_appendix_entries = lambda: None  # the LLM pass; no credential, deterministic
    input_path, output_path = tmp_path / "in.json", tmp_path / "out.docx"
    input_path.write_text(json.dumps({"document_uid": "TEST34", "entries": _wire_entries()}))
    gen.generate(str(input_path), str(output_path), research_summary_path=None,
                 original_doc_path=str(_wcm_source(tmp_path / "source.docx")))
    sidecar = json.loads((tmp_path / "TEST34_render_warnings.json").read_text())
    receipts = {r["section"]: r for r in sidecar["row_receipts"]}

    def counts(section):
        r = receipts[section]
        return r["linted"], r["written"], r["elsewhere"], [e["element_idx_start"] for e in r["lost"]]

    assert counts("researcher_profiles") == (False, 2, 0, [])
    assert counts("honors") == (True, 1, 1, [61])
    assert counts("licensure") == (True, 0, 1, [31])
    assert counts("board_certification") == (True, 1, 0, [])
    paragraphs = _body_paragraph_texts(output_path)
    assert {"ORCID: 0000-0002-1111-2222", "Invented Scholar profile page", _PRINTED_NPI} <= set(paragraphs)
    assert not any("2345678903" in p or "Pretend Honor Award" in p for p in paragraphs)
    assert sorted(f["message"].split(":")[0] for f in lint_renderer_row_receipts(sidecar)) == [
        "taxonomy code F1", "taxonomy code H"]
