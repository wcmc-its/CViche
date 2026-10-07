"""Regression guard for issue #156: appointment fragmentation + expected degrees.

Two data-integrity classes surfaced by run I5NKUG, both fixable at render time
(Stage 6) without re-running any paid LLM stage:

(A) GROUPED-APPOINTMENT FRAGMENTATION (D1/D2/D3) — a CV lists one employer with
    a date range and several titles beneath it, but field extraction splits the
    title, institution and date range into separate entries, so Stage 6 renders
    title-less "(Present)" rows AND date-less title rows for the same job.

(B) EXPECTED / IN-PROGRESS DEGREE shown as awarded — a degree marked "expected
    <year>" (or with a future year) is placed in the Year Awarded column as if
    it had already been conferred.

These tests run Stage 6 on the committed I5NKUG field-extraction fixture and
assert GENERAL properties (no title-less / date-less appointment rows where the
group supplies the info; expected degrees are not shown as plain conferred
years) — not the specific person's strings.

Pure render test: no DB, no FastAPI app, no Bedrock. ``call_llm`` is stubbed so
the run is free and deterministic. Run with:

    python3 -m pytest src/unified_pipeline/tests/test_appointment_degree_fragmentation.py -p no:cacheprovider
"""

import sys
from pathlib import Path

import pytest

# Make the repo's ``src`` importable regardless of cwd / rootdir.
_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "I5NKUG_fields.json"

docx = pytest.importorskip("docx", reason="python-docx not importable in this env")
from unified_pipeline import stage_6_word_template as s6  # noqa: E402

# --- helpers ---------------------------------------------------------------

def _iter_blocks(doc):
    """Yield paragraphs and tables in document order."""
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    for child in doc.element.body.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, doc)
        elif isinstance(child, CT_Tbl):
            yield Table(child, doc)


def _tables_by_heading(doc):
    """Map the nearest preceding heading text -> its table (data rows only)."""
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    out = {}
    heading = ""
    for block in _iter_blocks(doc):
        if isinstance(block, Paragraph):
            if block.text.strip():
                heading = block.text.strip()
        elif isinstance(block, Table):
            rows = []
            for r in block.rows[1:]:  # skip the header row
                rows.append([c.text.replace("\n", " ").strip() for c in r.cells])
            out.setdefault(heading, rows)
    return out


def _find_table(tables, *needles):
    needles = tuple(n.lower() for n in needles)
    for heading, rows in tables.items():
        hl = heading.lower()
        if any(n in hl for n in needles):
            return rows
    return None


@pytest.fixture(scope="module")
def rendered_tables(tmp_path_factory, monkeypatch_module):
    """Run Stage 6 on the I5NKUG fixture once; return heading->rows mapping."""
    assert FIXTURE.exists(), f"missing committed fixture: {FIXTURE}"

    # Stub the only LLM call in Stage 6 (geographic-scope classification) so the
    # render is free, offline and deterministic.
    def _fake_call_llm(*args, **kwargs):
        return {"content": '{"scope": "National"}', "usage": {}, "cost": 0.0}

    monkeypatch_module.setattr(s6, "call_llm", _fake_call_llm)

    out_dir = tmp_path_factory.mktemp("stage6_out")
    out_path = out_dir / "I5NKUG_wcm.docx"
    s6.run_stage6(str(FIXTURE), str(out_path), verbose=False)
    assert out_path.exists()

    return _tables_by_heading(docx.Document(str(out_path)))


@pytest.fixture(scope="module")
def monkeypatch_module():
    # module-scoped monkeypatch (pytest's built-in is function-scoped)
    from _pytest.monkeypatch import MonkeyPatch
    mp = MonkeyPatch()
    yield mp
    mp.undo()


# --- (A) appointment fragmentation -----------------------------------------

def test_hospital_appointments_have_no_titleless_or_dateless_rows(rendered_tables):
    """Every Hospital Appointment (D2) row must carry both a title and dates.

    The fixture's D2 section is the canonical fragmentation case: WCMC's three
    current roles were split from their "02/13-Present" employer line, and NYP's
    'Staff Nurse' was split from its 'Medical/Surgical Unit (07/04-04/10)' line.
    After the merge, no row may be title-less or date-less.
    """
    rows = _find_table(rendered_tables, "hospital appointment")
    assert rows, "Hospital Appointments table not found / empty"

    for title, institution, dates in ((r + ["", "", ""])[:3] for r in rows):
        assert title.strip(), (
            f"title-less Hospital Appointment row (institution={institution!r}, "
            f"dates={dates!r}) — the group supplies a title for this employer"
        )
        assert dates.strip(), (
            f"date-less Hospital Appointment row (title={title!r}) — the group "
            f"supplies a date range for this employer"
        )


def test_wcmc_current_appointment_carries_titles_and_present_range(rendered_tables):
    """The current employer's roles must each show their title + the live range.

    GENERAL property phrased through the fixture: the employer that is current
    ("Present") must appear as one row per role, each row carrying BOTH the role
    title AND the employer's live "<start>-Present" date range — never a blank
    title row standing in for the employer.
    """
    rows = _find_table(rendered_tables, "hospital appointment")
    assert rows, "Hospital Appointments table not found / empty"

    present_rows = [r for r in rows if "present" in (r[2] if len(r) > 2 else "").lower()]
    assert present_rows, "expected at least one current ('Present') appointment row"

    for title, institution, dates in ((r + ["", "", ""])[:3] for r in present_rows):
        assert title.strip(), "current appointment row is missing its role title"
        # The live range must be a real start->Present span, not a bare 'Present'
        # standing in for a stripped-out employer header.
        assert "-present" in dates.lower().replace(" ", ""), (
            f"current row dates {dates!r} should be a '<start>-Present' range"
        )

    # The three distinct current WCMC roles must all survive as separate rows.
    present_titles = {r[0].strip().lower() for r in present_rows}
    assert len(present_titles) >= 3, (
        f"expected the 3 distinct current roles as separate rows, got: {present_titles}"
    )


def test_no_appointment_row_is_a_bare_present_placeholder(rendered_tables):
    """No appointment table may contain a title-less '...-Present' row.

    A title-less row whose only content is a date range is the fragmentation
    fingerprint: an employer header that lost its roles. Check all three
    position tables.
    """
    for heading in ("academic appointment", "hospital appointment",
                    "other professional"):
        rows = _find_table(rendered_tables, heading)
        if not rows:
            continue
        for r in rows:
            r = (r + ["", "", ""])[:3]
            title, _institution, dates = r
            if dates.strip() and not title.strip():
                pytest.fail(
                    f"title-less dated row in '{heading}' table: dates={dates!r}"
                )


# --- (B) expected / in-progress degree -------------------------------------

def test_expected_degree_not_shown_as_plain_conferred_year(rendered_tables):
    """A degree marked 'expected'/future must not occupy Year Awarded as a year.

    GENERAL property: the in-progress doctoral degree in the fixture (expected
    May 2026) must be flagged (e.g. 'Expected 2026'), never rendered as a bare
    conferred year like '2026'. Conferred degrees keep their plain year.
    """
    rows = _find_table(rendered_tables, "academic degree", "education")
    assert rows, "Education / Academic Degrees table not found / empty"

    # Find the in-progress degree row by its degree text.
    doctoral = [r for r in rows if "doctor" in r[0].lower()]
    assert doctoral, "expected the in-progress doctoral degree row in the fixture"

    for r in doctoral:
        year_cell = r[-1].strip().lower()
        assert year_cell != "2026", (
            "in-progress degree shown as a plain conferred year '2026'"
        )
        assert ("expected" in year_cell or "anticipated" in year_cell
                or "progress" in year_cell), (
            f"in-progress degree year cell {r[-1]!r} should be marked anticipated"
        )

    # Conferred degrees must remain plain years (no false positives).
    conferred = [r for r in rows if "master" in r[0].lower()
                 or "bachelor" in r[0].lower()]
    for r in conferred:
        yc = r[-1].strip().lower()
        assert "expected" not in yc and "anticipated" not in yc, (
            f"conferred degree {r[0]!r} wrongly flagged in-progress: {r[-1]!r}"
        )


# --- unit coverage of the merge helper (fast, no docx) ---------------------

def test_merge_helper_general_header_children_and_adjacent_pair():
    """Direct unit test of the merge rules on a synthetic, person-agnostic group."""
    gen = s6.WCMTemplateGenerator

    def entry(idx, title, inst, start, end):
        return {
            "element_idx_start": idx,
            "taxonomy_code": "D2",
            "extracted_fields": {
                "title": title, "institution": inst,
                "start_date": start, "end_date": end,
            },
        }

    entries = [
        # Header (employer + dates, no title) + two title-only children.
        entry(1, None, "Acme Hospital", "2015-01", "present"),
        entry(2, "Attending Physician", "Acme Hospital", None, None),
        entry(3, "Section Chief", "Acme Hospital", None, None),
        # Adjacent pair in the reverse order: title-only then dated-no-title.
        entry(4, "Resident", "Beta Clinic", None, None),
        entry(5, None, "Beta Clinic", "2010-07", "2014-06"),
    ]
    merged = gen._merge_grouped_appointments(entries)

    # No surviving row may be title-less or date-less.
    for e in merged:
        f = e["extracted_fields"]
        assert (f.get("title") or "").strip(), f"title-less survivor: {f}"
        assert f.get("start_date") or f.get("end_date"), f"date-less survivor: {f}"

    by_title = {e["extracted_fields"]["title"]: e["extracted_fields"] for e in merged}
    assert by_title["Attending Physician"]["start_date"] == "2015-01"
    assert by_title["Attending Physician"]["end_date"] == "present"
    assert by_title["Section Chief"]["start_date"] == "2015-01"
    assert by_title["Resident"]["start_date"] == "2010-07"
    assert by_title["Resident"]["end_date"] == "2014-06"
    # The two bare employer/dates rows were absorbed, not left dangling.
    assert len(merged) == 3


def test_merge_helper_preserves_complete_and_orphan_rows():
    """A complete row and a lone title-only row are left untouched (no fabrication)."""
    gen = s6.WCMTemplateGenerator

    entries = [
        {"element_idx_start": 1, "taxonomy_code": "D3",
         "extracted_fields": {"title": "Consultant", "institution": "Gamma Inc",
                              "start_date": "2018-01", "end_date": "2019-01"}},
        {"element_idx_start": 2, "taxonomy_code": "D3",
         "extracted_fields": {"title": "Volunteer", "institution": None,
                              "start_date": None, "end_date": None}},
    ]
    merged = gen._merge_grouped_appointments(entries)
    assert len(merged) == 2  # nothing merged, nothing dropped
    titles = {e["extracted_fields"]["title"] for e in merged}
    assert titles == {"Consultant", "Volunteer"}
    # The orphan title-only row is NOT given fabricated dates.
    volunteer = next(e for e in merged if e["extracted_fields"]["title"] == "Volunteer")
    assert not volunteer["extracted_fields"].get("start_date")


def test_degree_in_progress_detection_is_general():
    """_degree_is_in_progress fires on markers and future years, not on past years."""
    gen = s6.WCMTemplateGenerator(verbose=False)
    assert gen._degree_is_in_progress("PhD, Biology, expected May 2030", "2030")
    assert gen._degree_is_in_progress("MD, anticipated 2031", "2031")
    assert gen._degree_is_in_progress("MD, in progress", "")
    # Future year alone (no marker) is still in-progress.
    assert gen._degree_is_in_progress("Doctor of Medicine", "2099")
    # Conferred past degrees must NOT be flagged.
    assert not gen._degree_is_in_progress("MD, May 2009", "2009")
    assert not gen._degree_is_in_progress("BS, 2004", "2004")
