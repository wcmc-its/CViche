"""contact_slot_lost (doctor/lints/contact.py, EBYSBC E25).

Synthetic fixtures only: 555 numbers, invented streets and places.

    python3 -m pytest src/unified_pipeline/tests/test_doctor_contact.py -q -p no:cacheprovider
"""
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.doctor.lints.contact import lint_contact_slot_lost  # noqa: E402

_OFFICE = "(212) 555-0142"
_CELL = "(212) 555-0199"
_STREET = "100 Example Avenue, Suite 4, Sampletown, NY 10001"


def _pd(office_address="", office_phone="", cell_phone="", home_address=""):
    """The rendered Personal Data table, as the doctor's table_rows view reads it."""
    return [[["Office address:", office_address], ["Office telephone:", office_phone],
             ["Work email:", ""], ["Home address:", home_address],
             ["Cell phone:", cell_phone], ["Personal email:", ""]]]


def _a(text, idx=2, **fields):
    return {"entries": [{"element_idx_start": idx, "taxonomy_code": "A", "text": text,
                         "extracted_fields": fields}]}


def _lost(stage4, rows):
    return [f["evidence"] for f in lint_contact_slot_lost(stage4, rows)]


def test_an_office_number_in_the_cell_row_warns():
    stage4 = _a(f"Telephone: {_CELL} (cell) {_OFFICE} (office)", phone=f"{_CELL} (cell); {_OFFICE} (office)")
    findings = lint_contact_slot_lost(stage4, _pd(office_phone=_CELL, cell_phone=_OFFICE))
    assert [f["severity"] for f in findings] == ["WARN"]
    assert findings[0]["message"].startswith("entry 2 (A): ")
    assert findings[0]["evidence"] == ["office phone ending 0142 is in another Personal Data row"]


def test_an_office_number_shown_in_its_row_is_clean():
    stage4 = _a(f"Telephone: {_CELL} (cell) {_OFFICE} (office)", phone=f"{_CELL} (cell); {_OFFICE} (office)")
    assert _lost(stage4, _pd(office_phone=_OFFICE, cell_phone=_CELL)) == []


def test_an_office_number_nowhere_warns():
    stage4 = _a(f"Office: Example Hall\tPhone: {_OFFICE}", phone=_OFFICE)
    assert _lost(stage4, _pd()) == [["office phone ending 0142 is in no Personal Data row"]]


def test_home_cell_and_fax_numbers_are_not_expected():
    for text in (f"Home phone: {_OFFICE}", f"Cell phone:\t{_OFFICE}", f"Fax: {_OFFICE}",
                 f"Ph (h): {_OFFICE}", f"Address: home: {_STREET}\tphone: {_OFFICE}"):
        assert _lost(_a(text, phone=_OFFICE), _pd()) == [], text


def test_a_cell_word_in_the_line_before_does_not_label_the_number():
    stage4 = _a(f"Department of Stem Cell Studies\tPhone: {_OFFICE}", phone=_OFFICE)
    assert _lost(stage4, _pd(cell_phone=_OFFICE)) == [
        ["office phone ending 0142 is in another Personal Data row"]]


def test_the_office_half_of_a_home_and_office_address_warns():
    address = f"Home: 9 Sample Lane, Exampleville, NY 10002; Office: {_STREET}"
    stage4 = _a(f"Home: 9 Sample Lane; Office: {_STREET}", address=address)
    assert _lost(stage4, _pd()) == [["office address is in no Personal Data row"]]
    assert _lost(stage4, _pd(office_address=_STREET)) == []


def test_off_schema_office_keys_are_expected():
    stage4 = _a("Example Hall", office_phone=_OFFICE, street_address="100 Example Avenue",
                city="Sampletown", state="NY", zip_code="10001")
    assert _lost(stage4, _pd()) == [["office phone ending 0142 is in no Personal Data row",
                                     "office address is in no Personal Data row"]]


def test_home_birthplace_and_department_addresses_are_not_expected():
    for text, address in ((f"Home address: {_STREET}", _STREET),
                          ("Born: Sampletown, NY", "Sampletown, NY"),
                          ("Department: Department of Example Studies", "Department of Example Studies")):
        assert _lost(_a(text, address=address), _pd()) == [], text


def test_no_personal_data_table_reports_nothing():
    stage4 = _a(f"Office: Example Hall\tPhone: {_OFFICE}", phone=_OFFICE)
    assert lint_contact_slot_lost(stage4, [[["Degree", "Institution"]]]) == []


def test_entries_of_other_codes_are_not_read():
    stage4 = {"entries": [{"element_idx_start": 4, "taxonomy_code": "D1",
                           "text": f"Phone: {_OFFICE}", "extracted_fields": {"phone": _OFFICE}}]}
    assert lint_contact_slot_lost(stage4, _pd()) == []


def test_a_letter_label_after_the_number_is_not_the_office():
    for label in ("(c)", "(m)", "(f)"):
        text = f"Office: Example Hall\tPhone: {_CELL} {label}"
        assert _lost(_a(text, phone=f"{_CELL} {label}"), _pd()) == [], label


def test_a_label_before_a_semicolon_does_not_label_the_number():
    stage4 = _a(f"Cell phone on request; {_OFFICE}", phone=_OFFICE)
    assert _lost(stage4, _pd()) == [["office phone ending 0142 is in no Personal Data row"]]


def test_a_label_before_the_previous_number_does_not_label_the_next():
    stage4 = _a(f"Cell {_CELL}, {_OFFICE}", phone=_OFFICE)
    assert _lost(stage4, _pd()) == [["office phone ending 0142 is in no Personal Data row"]]


def test_a_label_far_before_the_number_does_not_label_it():
    stage4 = _a(f"Mobile is listed in the department directory {_OFFICE}", phone=_OFFICE)
    assert _lost(stage4, _pd()) == [["office phone ending 0142 is in no Personal Data row"]]


def test_an_unlabelled_number_in_a_home_and_office_block_is_expected():
    text = f"Home: 9 Sample Lane, Exampleville. Office: Example Hall, Room 1200. Phone: {_OFFICE}"
    assert _lost(_a(text, phone=_OFFICE), _pd()) == [
        ["office phone ending 0142 is in no Personal Data row"]]


def test_an_office_label_on_the_value_outranks_the_entry_text():
    stage4 = _a(f"Mobile {_OFFICE}", phone=f"Office: {_OFFICE}")
    assert _lost(stage4, _pd()) == [["office phone ending 0142 is in no Personal Data row"]]


def test_an_extension_alone_is_not_a_number():
    assert _lost(_a("Example Hall", office_phone="ext. 1234"), _pd()) == []


def test_an_address_half_shown_counts_as_shown():
    stage4 = _a("Office: 100 Example Avenue, Sampletown", address="100 Example Avenue, Sampletown")
    assert _lost(stage4, _pd(office_address="100 Example")) == []
    assert _lost(stage4, _pd(office_address="100")) == [["office address is in no Personal Data row"]]


def test_the_office_label_is_not_an_address_word():
    address = "Home: 9 Sample Lane, Exampleville; Office: Room 1200, Example Hall"
    stage4 = _a(address, address=address)
    assert _lost(stage4, _pd(office_address="Room 1200")) == []
