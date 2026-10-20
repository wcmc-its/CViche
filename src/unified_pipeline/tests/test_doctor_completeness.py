"""mentee_field_incomplete (doctor/lints/completeness.py, #1941).

Synthetic fixtures only: invented mentee names and positions.

    python3 -m pytest src/unified_pipeline/tests/test_doctor_completeness.py -q -p no:cacheprovider
"""
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.doctor.lints.completeness import (  # noqa: E402
    MENTEE_FIELD_EVIDENCE_LIMIT,
    MENTEE_FIELD_LOCATOR_RE,
    lint_mentee_field_incomplete,
)
from unified_pipeline.stage6.sections.mentoring import (  # noqa: E402
    CURRENT_MENTEES_HEADING,
    MENTEE_TABLE_LABELS,
    PAST_MENTEES_HEADING,
)

_FILLED = {
    "Name:": "Avery Example",
    "Site/Position:": "Example University - PhD student",
    "Mentoring Period:": "2015-2019",
    "Project/Accomplishments:": "Squid lens optics",
    "Current Position:": "Assistant Professor, Example College",
    "Type of Supervision:": "Research",
}


def _mentee(**values: str) -> list[list[str]]:
    """One rendered mentee table's rows; keyword ``Current_Position="N/A"``
    sets the "Current Position:" row, ``Site_Position`` "Site/Position:",
    and so on (an unknown keyword raises)."""
    by_key = {label.rstrip(":").replace("/", "_").replace(" ", "_"): label
              for label in MENTEE_TABLE_LABELS}
    cells = {**_FILLED, **{by_key[key]: value for key, value in values.items()}}
    return [[label, cells[label]] for label in MENTEE_TABLE_LABELS]


def _run(current: list, past: list, other_tables: int = 0) -> list[dict]:
    """The lint over a document with ``other_tables`` non-mentee tables, then
    the current and the past mentee tables under their headings."""
    tables = [[["Degree", "Institution"], ["PhD", "Example U"]]] * other_tables
    blocks = [("table", "x")] * other_tables
    blocks += [("p", "Mentees"), ("p", CURRENT_MENTEES_HEADING)] + [("table", "m")] * len(current)
    blocks += [("p", PAST_MENTEES_HEADING + " ")] + [("table", "m")] * len(past)
    return lint_mentee_field_incomplete(tables + current + past, blocks)


def test_a_past_mentee_with_na_current_position_is_one_info_finding_on_its_table():
    findings = _run([_mentee()], [_mentee(), _mentee(Current_Position="N/A", Name="Blake Sample")],
                    other_tables=2)
    assert [f["severity"] for f in findings] == ["INFO"]
    assert findings[0]["lint"] == "mentee_field_incomplete"
    m = MENTEE_FIELD_LOCATOR_RE.match(findings[0]["message"])
    assert (m["table"], m["label"]) == ("4", "Current Position:")  # 2 other + 1 current + 1
    assert "1 of 2 Past Mentees tables" in findings[0]["message"]
    assert findings[0]["evidence"] == ["Blake Sample"]


def test_every_placeholder_reads_unfilled_and_a_real_value_does_not():
    placeholders = ["", "  ", "N/A", "n/a.", "NA", "None", "not applicable", "-", "—", "N.A."]
    past = [_mentee(Current_Position=value) for value in placeholders]
    past.append(_mentee(Current_Position="Nanotechnology Fellow"))
    (finding,) = _run([], past)
    assert f"{len(placeholders)} of {len(past)} Past Mentees tables" in finding["message"]


def test_many_tables_missing_one_field_are_one_finding_and_capped_evidence():
    past = [_mentee(Current_Position="", Name=f"Mentee {n}") for n in range(12)]
    (finding,) = _run([], past)
    assert "12 of 12 Past Mentees tables" in finding["message"]
    assert finding["evidence"] == [f"Mentee {n}" for n in range(MENTEE_FIELD_EVIDENCE_LIMIT)]


def test_each_unfilled_field_is_its_own_finding_per_heading():
    findings = _run([_mentee(Type_of_Supervision="none")],
                    [_mentee(Type_of_Supervision=""), _mentee(Mentoring_Period="N/A")])
    messages = sorted(f["message"] for f in findings)
    assert len(messages) == 3
    assert messages[0].startswith("table 0, row 'Type of Supervision:'")
    assert "1 of 1 Current Mentees tables" in messages[0]
    assert messages[1].startswith("table 1, row 'Type of Supervision:'")
    assert "1 of 2 Past Mentees tables" in messages[1]
    assert messages[2].startswith("table 2, row 'Mentoring Period:'")
    assert "1 of 2 Past Mentees tables" in messages[2]


def test_a_current_mentee_needs_no_current_position_and_projects_are_optional():
    assert _run([_mentee(Current_Position="N/A", Project_Accomplishments="")],
                [_mentee(Project_Accomplishments="N/A")]) == []


def test_without_a_heading_the_past_only_row_is_not_asked_for():
    tables = [_mentee(Current_Position="", Site_Position="n/a")]
    misaligned = [("p", PAST_MENTEES_HEADING), ("table", "m"), ("table", "x")]
    for blocks in (None, [("p", "Mentees"), ("table", "m")], misaligned):
        (finding,) = lint_mentee_field_incomplete(tables, blocks)
        assert finding["message"].startswith("table 0, row 'Site/Position:'")
        assert "1 of 1 mentee tables" in finding["message"]


def test_a_paragraph_between_heading_and_table_keeps_the_heading():
    """An N4 outcome line or a spacing paragraph under Past Mentees does not
    end the Past Mentees group."""
    blocks = [("p", PAST_MENTEES_HEADING), ("p", "An outcome line"), ("p", ""), ("table", "m")]
    (finding,) = lint_mentee_field_incomplete([_mentee(Current_Position="")], blocks)
    assert "1 of 1 Past Mentees tables" in finding["message"]


def test_a_table_that_is_not_a_mentee_table_is_not_read():
    def shaped(edit):
        rows = [row[:] for row in _mentee(Site_Position="")]
        edit(rows)
        return rows
    for rows in (shaped(lambda r: r[0].__setitem__(0, "Name of Committee")),
                 shaped(lambda r: r[2].__setitem__(0, "Dates:")),
                 shaped(lambda r: r.pop()),
                 [[*row, ""] for row in _mentee(Site_Position="")]):
        assert lint_mentee_field_incomplete([rows], None) == []
    assert len(lint_mentee_field_incomplete([_mentee(Site_Position="")], None)) == 1


def test_a_table_naming_no_mentee_is_the_subsection_s_na_and_not_read():
    """A Past Mentees table that is "N/A" throughout is the submitter saying
    the subsection is empty, which WCM asks for: no finding, and not counted."""
    na_table = [[label, "N/A"] for label in MENTEE_TABLE_LABELS]
    assert _run([], [na_table]) == []
    (finding,) = _run([], [na_table, _mentee(Current_Position="")])
    assert "1 of 1 Past Mentees tables" in finding["message"]
