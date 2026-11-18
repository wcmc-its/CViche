"""#1342: `stage4.source_dates` holds a stored date to what the entry's own
line, and its table's header, state.

    python3 -m pytest src/unified_pipeline/tests/test_stage4_source_dates.py -p no:cacheprovider

Pure and offline. Synthetic entries only.
"""

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from unified_pipeline.core import source_open_year  # noqa: E402
from unified_pipeline.stage4 import coercion, source_dates  # noqa: E402
from unified_pipeline.stage4.schemas import STAGE4_RECORDS_KEY  # noqa: E402
from unified_pipeline.stage4.source_dates import (  # noqa: E402
    OPEN_END_KEPT_CODES,
    OPEN_END_VALUES,
    close_unsupported_open_ends,
    date_completion_column_rows,
)


def _e(text, code="K2", hierarchy=("Teaching Record",), **fields):
    return {"text": text, "taxonomy_code": code, "hierarchy": list(hierarchy),
            "extracted_fields": fields}


def _closed(entry):
    (out,) = close_unsupported_open_ends([entry], STAGE4_RECORDS_KEY)
    return out


def _end(entry):
    return _closed(entry)["extracted_fields"]["end_date"]


# --- an open end the source does not state -----------------------------------

@pytest.mark.parametrize("text, start, end", [
    ("09/2019 to 06/2024 Clinic Tutor, Fern Clinic", "2019-09", "2024-06"),
    ("2019 - 2024 Clinic Tutor, Fern Clinic", "2019", "2024"),
    ("Clinic Tutor, Fern Clinic, 2019–2024", "2019", "2024"),
    ("Clinic Tutor, Fern Clinic, 2019 through 2024", "2019", "2024"),
    # A "month" past December is no month: the year alone is kept.
    ("2019 to 13/2024 Clinic Tutor", "2019", "2024"),
])
def test_an_open_end_takes_the_end_the_text_writes(text, start, end):
    out = _closed(_e(text, start_date=start, end_date="present"))
    assert out["extracted_fields"] == {"start_date": start, "end_date": end}
    assert out["reformatted_fields"]["end_date"] == {
        "original": "present", "reformatted": end,
        "reason": "Replaced an open end the source does not state with the end it writes"}


@pytest.mark.parametrize("text", [
    "Seminar Leader\tGlade Seminar; weekly\t2011",
    "Seminar Leader | Glade Seminar | 2011",
    "Seminar Leader\nGlade Seminar\n(2011).",
])
def test_an_open_end_is_cleared_when_the_last_cell_is_the_start_year_alone(text):
    out = _closed(_e(text, "K3", start_date="2011", end_date="Present"))
    assert out["extracted_fields"] == {"start_date": "2011", "end_date": None}
    assert out["reformatted_fields"]["end_date"]["reason"] == \
        "Cleared an open end the source does not state"


@pytest.mark.parametrize("text", [
    # An open marker in the text: a word, or a dash no digit follows.
    "Seminar Leader\tGlade Seminar, ongoing\t2011",
    "Seminar Leader\tGlade Seminar, taught annually\t2011",
    "Seminar Leader, Glade Seminar, since 2011",
    "2011- Seminar Leader, Glade Seminar",
    "2011 – Seminar Leader, Glade Seminar",
    "Seminar Leader | Glade Seminar | 2011-*",
    "2011‑  Seminar Leader, Glade Seminar\t2011",
    # Every open year stage 6 reads (`source_leaves_year_open`): a dotted
    # month, or month and day, and a slashed two-digit year.
    "2011.04 - Glade Seminar, Seminar Leader",
    "2011.04.02 - Glade Seminar\t2011",
    "4/11- Glade Seminar\t2011",
    "4/02/11- Glade Seminar\t2011",
    # A year other than the stored start is left open, after a closed range.
    "2011-2013 Glade Seminar; Fern Seminar chair 4/15-",
    "2011-2013 Glade Seminar; Fern Seminar chair 2015.04.02 - ",
    "2011-2013 Glade Seminar; Fern Seminar chair 2015\u2015",
    # A year later than the closed range's end: the range is not the last
    # thing the line dates.
    "2011-2013, 2018 Glade Seminar",
    "2011-2013 Glade Seminar (revised 2020)",
    # The year inside prose, or opening the entry: no bare last cell.
    "Docente del corso Glade dal 2011",
    "2011 (first offered), Glade Seminar, Seminar Leader",
    # Two closed ranges from the start year, or one that runs backwards.
    "2011-2013 Glade Seminar; 2011-2015 Fern Seminar",
    "2011-2015 Fern Seminar; 2011-2013 Glade Seminar",
    "2011-2009 Glade Seminar",
    # No year in the text at all.
    "Glade Seminar, Seminar Leader",
])
def test_an_open_end_the_text_may_support_is_kept(text):
    entry = _e(text, start_date="2011", end_date="present")
    assert _closed(entry) is entry


@pytest.mark.parametrize("change", [
    {"hierarchy": ["Current Teaching"]},
    {"hierarchy": ["Teaching"], "context_heading": "Courses taught to date"},
    {"taxonomy_code": "I"},
])
def test_a_heading_marker_or_a_membership_keeps_the_open_end(change):
    entry = {**_e("Glade Society\t2011", start_date="2011", end_date="present"), **change}
    assert _closed(entry) is entry
    assert _end({**entry, "taxonomy_code": "K2", "hierarchy": ["Teaching"],
                 "context_heading": ""}) is None


@pytest.mark.parametrize("fields", [
    {"start_date": "2011", "end_date": "2013"},
    {"start_date": "2011", "end_date": None},
    {"start_date": None, "end_date": "present"},
    {"start_date": "Fall term", "end_date": "present"},
    {"start_date": "2011", "end_date": "present",
     STAGE4_RECORDS_KEY: [{"start_date": "2011", "end_date": "present"}, {"start_date": "2015"}]},
])
def test_only_a_one_record_entry_with_a_dated_start_and_an_open_end_is_read(fields):
    entry = _e("Glade Seminar\t2011", **fields)
    assert _closed(entry) is entry


def test_an_entry_without_fields_passes_through_and_the_input_is_not_mutated():
    entries = [{"text": "Glade Seminar\t2011", "extracted_fields": None},
               _e("Glade Seminar\t2011", start_date="2011", end_date="present")]
    before = copy.deepcopy(entries)
    out = close_unsupported_open_ends(entries, STAGE4_RECORDS_KEY)
    assert entries == before
    assert out[0] is entries[0]
    assert out[1]["extracted_fields"]["end_date"] is None


def test_the_open_year_reader_is_stage_6s_own():
    # One definition (CODING_STANDARDS 1.5): both stages import the function
    # `core.source_open_year` defines, neither keeps a copy.
    from unified_pipeline.stage6.formatting import dates as stage6_dates

    shared = source_open_year.source_leaves_year_open
    assert source_dates.source_leaves_year_open is shared
    assert stage6_dates._source_leaves_year_open is shared


@pytest.mark.parametrize("text, year, is_open", [
    ("2011- Glade Seminar", 2011, True),
    ("4/11- Glade Seminar", 2011, True),
    ("4/02/11- Glade Seminar", 2011, True),
    ("2011.04.02 - Glade Seminar", 2011, True),
    ("Glade Seminar 2011 -\tLeader", 2011, True),
    ("Glade Seminar, 2011 to present", 2011, True),
    ("2011-2013 Glade Seminar", 2011, False),
    ("Award winner, 2011 - Glade Prize", 2011, False),
    ("Glade Seminar (2011)", 2011, False),
    ("Glade Seminar 2011", 2011, False),
])
def test_stage_4_keeps_exactly_the_open_ends_stage_6_reads_as_open(text, year, is_open):
    # The shared table: what the reader says of a line's year is what the
    # stage-4 pass does with a stored "present" on the same line.
    assert source_open_year.source_leaves_year_open(text, year) is is_open
    assert source_dates._leaves_a_year_open(text) is is_open
    entry = _e(f"{text}\t2011", "K3", ("Seminars",), start_date="2011", end_date="present")
    assert (_closed(entry) is entry) is is_open


def test_open_end_values_match_coercion():
    # Hand-kept: source_dates and coercion import nothing from each other.
    assert coercion._OPEN_END_VALUES == OPEN_END_VALUES
    assert frozenset({"I"}) == OPEN_END_KEPT_CODES


# --- a completion column's year ----------------------------------------------

_HEADER = "Duty | Trainee | Field | Complete Date"


def _row(text, row_index, code="N3A", table_index=3, **fields):
    return {"text": text, "taxonomy_code": code, "table_index": table_index,
            "row_index": row_index, "extracted_fields": fields}


def _table(*rows, header=_HEADER, header_code="T"):
    return [_row(header, 0, header_code, content_type="table_header"), *rows]


def _completed(entries):
    return date_completion_column_rows(entries, STAGE4_RECORDS_KEY)


@pytest.mark.parametrize("cell, start", [("2031", "2031"), ("05/2031", "2031-05")])
def test_a_completion_column_year_stored_as_the_start_becomes_the_end(cell, start):
    entries = _table(_row(f"Member | A. Example | Fern Science | {cell}", 1,
                          mentee_name="A. Example", start_date=start))
    before = copy.deepcopy(entries)
    header, row = _completed(entries)
    assert entries == before and header is entries[0]
    assert row["extracted_fields"] == {
        "mentee_name": "A. Example", "start_date": None, "end_date": start}
    assert row["reformatted_fields"] == {
        name: {"original": original, "reformatted": new,
               "reason": "Moved a completion-column year from start_date to end_date"}
        for name, original, new in (("start_date", start, ""), ("end_date", None, start))}


@pytest.mark.parametrize("header", [
    "Duty | Trainee | Field | Completion Year",
    "Year Graduated | Trainee | Field",
])
def test_other_completion_labels_are_read_in_their_own_column(header):
    column = [cell.strip() for cell in header.split("|")].index(
        next(cell.strip() for cell in header.split("|") if "ear" in cell))
    cells = ["Member", "A. Example", "Fern Science", "x"][:len(header.split("|"))]
    cells[column] = "2031"
    entries = _table(_row(" | ".join(cells), 1, start_date="2031"), header=header)
    assert _completed(entries)[1]["extracted_fields"]["end_date"] == "2031"


@pytest.mark.parametrize("header, header_code, text, fields", [
    # No completion column, two of them, or another date column beside it.
    ("Duty | Trainee | Field | Date", "T", "Member | A | B | 2031", {"start_date": "2031"}),
    ("Duty | Completion Date | Field | Complete Date", "T", "Member | 2031 | B | 2031",
     {"start_date": "2031"}),
    ("Duty | Trainee | Start Date | Complete Date", "T", "Member | A | 2029 | 2031",
     {"start_date": "2031"}),
    # Row 0 is a record, not a header row.
    (_HEADER, "N3A", "Member | A | B | 2031", {"start_date": "2031"}),
    # The cell is not the stored start, is not a bare year, or is empty.
    (_HEADER, "T", "Member | A | B | 2031", {"start_date": "2029"}),
    (_HEADER, "T", "Member | A | B | 2029-2031", {"start_date": "2031"}),
    (_HEADER, "T", "Member | A | B | 2031 (expected)", {"start_date": "2031"}),
    (_HEADER, "T", "Member | A | B |", {"start_date": "2031"}),
    # An end is already stored, or the cell count differs from the header's.
    (_HEADER, "T", "Member | A | B | 2031", {"start_date": "2031", "end_date": "2031"}),
    (_HEADER, "T", "Member | A | 2031", {"start_date": "2031"}),
    (_HEADER, "T", "Member | A | B | 2031 | see note", {"start_date": "2031"}),
    # Several records on the row.
    (_HEADER, "T", "Member | A | B | 2031",
     {"start_date": "2031", STAGE4_RECORDS_KEY: [{"start_date": "2030"}, {"start_date": "2031"}]}),
])
def test_a_row_the_header_does_not_date_is_left_alone(header, header_code, text, fields):
    entries = _table(_row(text, 1, **fields), header=header, header_code=header_code)
    assert _completed(entries)[1] is entries[1]


def test_a_completion_label_on_a_row_below_the_header_dates_no_row():
    # A T-coded row further down is a repeated or sub-header line: only row 0
    # says what the table's columns hold.
    plain = "Duty | Trainee | Field | Notes"
    lower = _row(_HEADER, 1, "T", content_type="table_header")
    row = _row("Member | A | B | 2031", 2, start_date="2031")
    entries = _table(lower, row, header=plain)
    assert _completed(entries) == entries and _completed(entries)[2] is row


def test_a_row_of_another_table_or_of_no_table_is_left_alone():
    other = _row("Member | A | B | 2031", 1, table_index=4, start_date="2031")
    loose = {"text": "Member | A | B | 2031", "extracted_fields": {"start_date": "2031"}}
    blank = _row("Member | A | B | 2031", 2, start_date="2031")
    blank["extracted_fields"] = None
    out = _completed(_table(other, loose, blank))
    assert out[1] is other and out[2] is loose and out[3] is blank
