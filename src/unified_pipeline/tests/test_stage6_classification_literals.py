"""Three hard-coded classification literals misrouted content (#573).

1. Licensure: the NPI/DEA shape+substring tests consumed real state
   licences -- a 10-11 digit licence number became the NPI and any entry
   mentioning "Dean" became the DEA number, dropping the row from the
   Licensure table. Routing is now label first, shape only as a tiebreak
   when no state is present, and a second candidate for a filled slot
   warns instead of silently overwriting.
2. Service: `journal_keywords[:10]` dropped exactly 'neurology', so a
   Neurology peer-review entry stayed in Q2 boards while identical
   Oncology/Cardiology entries rerouted to Q4D Journal Reviewing.
3. C3 (Fellowship Training) had no stage-6 render path at all -- every C3
   entry fell to the Appendix by construction (same class as N2, #529).

All tests drive the real `_fill_*` render paths on a minimal document, the
same pattern as test_stage6_honors_render.py. Fixtures are fictional.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_classification_literals.py -p no:cacheprovider
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from docx import Document  # noqa: E402

from unified_pipeline.stage_6_word_template import (  # noqa: E402
    RENDER_ROUTED_CODES,
    WCMTemplateGenerator,
)
from unified_pipeline.doctor.lints.extraction import (  # noqa: E402
    lint_taxonomy_code_coverage,
)
from unified_pipeline.stage6.formatting import DATE_FORMATS  # noqa: E402


# ---------------------------------------------------------------------------
# 1. Licensure: NPI/DEA routing


def _render_licensure(entries):
    """Drive the real _fill_licensure + _fill_dea_npi path on a minimal doc.

    Returns (licence_rows, dea_cell, npi_cell)."""
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document()
    gen.doc.add_paragraph("F1. LICENSURE")
    lic_table = gen.doc.add_table(rows=1, cols=4)
    for i, header in enumerate(["State", "Number", "Date of issue",
                                "Date of last registration"]):
        lic_table.rows[0].cells[i].text = header
    id_table = gen.doc.add_table(rows=2, cols=2)
    id_table.rows[0].cells[0].text = "DEA number: (optional)"
    id_table.rows[1].cells[0].text = "NPI number: (optional)"
    gen._fill_licensure(entries)
    licence_rows = [[c.text for c in r.cells] for r in lic_table.rows[1:]]
    return licence_rows, id_table.rows[0].cells[1].text, id_table.rows[1].cells[1].text


def _f1(text, state="", number="", **extra_fields):
    fields = {"state_country": state, "license_number": number}
    fields.update(extra_fields)
    return {"taxonomy_code": "F1", "text": text, "extracted_fields": fields}


def test_ten_digit_state_licence_stays_a_licence_row():
    """The old shape-first rule consumed any 10-11 digit licence number as
    the NPI, dropping its row; a stated jurisdiction now disables the shape
    tiebreak."""
    rows, dea, npi = _render_licensure([
        _f1("New York State Medical License 123456",
            state="New York", number="123456"),
        _f1("Michigan Medical License 3512345678",
            state="Michigan", number="3512345678"),
        _f1("NPI: 1234567890", number="1234567890"),
    ])
    assert [r[:2] for r in rows] == [["New York", "123456"],
                                     ["Michigan", "3512345678"]]
    assert npi == "1234567890"
    assert dea == ""


def test_dean_text_is_not_a_dea_number():
    """'DEA' in text.upper() fired on "Dean"; the label match is now on word
    boundaries."""
    rows, dea, npi = _render_licensure([
        _f1("Dean's office - New York license 123456",
            state="New York", number="123456"),
    ])
    assert [r[:2] for r in rows] == [["New York", "123456"]]
    assert dea == ""
    assert npi == ""


def test_labelled_dea_and_npi_route_to_their_slots():
    rows, dea, npi = _render_licensure([
        _f1("DEA registration AB1234567", number="AB1234567"),
        _f1("NPI 1234567890", number="1234567890"),
    ])
    assert rows == []
    assert dea == "AB1234567"
    assert npi == "1234567890"


def test_fused_npi_prefix_still_routes_to_npi_slot():
    """Extraction sometimes fuses the label into the number itself
    ("NPI15180546000", corpus CV HU4DXA). A plain \\bNPI\\b would reject it
    -- a digit after the I is not a word boundary -- and demote a real NPI
    to a state-less licence row."""
    rows, dea, npi = _render_licensure([
        _f1("NPI15180546000", number="NPI15180546000"),
    ])
    assert rows == []
    assert npi == "NPI15180546000"
    assert dea == ""


def test_license_type_field_routes_before_text():
    """`license_type` is the highest-priority label -- it routes even a
    number whose shape matches neither identifier."""
    rows, dea, npi = _render_licensure([
        _f1("Provider identifier 99887", number="99887", license_type="NPI"),
    ])
    assert rows == []
    assert npi == "99887"
    assert dea == ""


def test_license_type_dea_routes_before_text():
    """The 'dea' license_type route, with a number no shape test matches."""
    rows, dea, npi = _render_licensure([
        _f1("Registration 55443", number="55443", license_type="DEA"),
    ])
    assert rows == []
    assert dea == "55443"
    assert npi == ""


def test_unstructured_npi_labelled_entry_stays_out_of_fallback():
    """The fallback's exclude side: an unstructured entry whose text carries a
    real NPI/DEA label must not become a raw-text licence row."""
    rows, dea, npi = _render_licensure([
        {"taxonomy_code": "F1", "text": "NPI number on file",
         "extracted_fields": {}},
    ])
    assert rows == []
    assert npi == ""


def test_shape_tiebreak_applies_only_without_a_state():
    """Unlabelled, state-less entries still classify by shape."""
    rows, dea, npi = _render_licensure([
        _f1("1234567890", number="1234567890"),
        _f1("AB1234567", number="AB1234567"),
    ])
    assert rows == []
    assert npi == "1234567890"
    assert dea == "AB1234567"


def test_second_npi_candidate_warns_and_keeps_first(caplog):
    """A single-valued slot must not be silently overwritten (#573)."""
    with caplog.at_level("WARNING",
                         logger="unified_pipeline.stage6.sections.licensure"):
        rows, dea, npi = _render_licensure([
            _f1("NPI 1234567890", number="1234567890"),
            _f1("NPI 9999999999", number="9999999999"),
        ])
    assert npi == "1234567890"
    assert "second NPI candidate" in caplog.text


def test_license_type_wins_over_a_conflicting_shape_and_missing_number():
    """Conflicting-signal precedence: a state licence number's shape looks
    like an NPI, but an explicit license_type label must still win, even
    when license_number is empty (the label check used to run after the
    'no number' guard, so a label-only entry fell through to KIND_LICENSE
    and vanished entirely -- #573 review)."""
    rows, dea, npi = _render_licensure([
        _f1("Provider identifier on file", state="CA", number="",
            license_type="NPI"),
    ])
    assert rows == []
    assert dea == ""
    # nothing to report (no number), but it must not become a licence row
    assert npi == ""


def test_unstructured_dean_entry_keeps_its_fallback_row():
    """The unstructured-entry guard used the same 'DEA' substring, so a raw
    line mentioning "Dean" was dropped from the document entirely."""
    rows, dea, npi = _render_licensure([
        {"taxonomy_code": "F1", "text": "Dean of Students certificate",
         "extracted_fields": {}},
    ])
    assert rows == [["Dean of Students certificate", "", "", ""]]
    assert dea == ""


def test_none_extracted_fields_does_not_crash():
    """extracted_fields is sometimes explicitly None, not merely absent;
    entry.get('extracted_fields', {}) only supplies {} when the key is
    missing, so a bare .get() default doesn't cover this case."""
    rows, dea, npi = _render_licensure([
        {"taxonomy_code": "F1", "text": "New York Medical License 123456",
         "extracted_fields": None},
    ])
    assert rows == [["New York Medical License 123456", "", "", ""]]
    assert dea == ""
    assert npi == ""


# ---------------------------------------------------------------------------
# 2. Service: journal_keywords[:10] dropped 'neurology'


def _render_service(q2_entries):
    """Drive the real _fill_service reroute + both table writers.

    Returns (board_rows, journal_rows)."""
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document()
    gen.doc.add_paragraph("Service on Boards and/or Committees")
    boards_table = gen.doc.add_table(rows=1, cols=4)
    for i, header in enumerate(["Name of Committee", "Role", "Organization",
                                "Dates"]):
        boards_table.rows[0].cells[i].text = header
    gen.doc.add_paragraph("Journal Reviewing/Ad hoc Reviewing")
    journal_table = gen.doc.add_table(rows=1, cols=2)
    for i, header in enumerate(["Journal / Organization Name", "Dates"]):
        journal_table.rows[0].cells[i].text = header
    gen._fill_service({"Q2": q2_entries})
    boards = [[c.text for c in r.cells] for r in boards_table.rows[1:]]
    journal = [[c.text for c in r.cells] for r in journal_table.rows[1:]]
    return boards, journal


def _q2_reviewer(journal_name):
    return {"taxonomy_code": "Q2", "text": f"Reviewer, {journal_name}",
            "extracted_fields": {"role": "Reviewer",
                                 "organization": journal_name}}


def test_neurology_reviewer_reroutes_to_journal_reviewing():
    """'neurology' was index 10 of the 14-element list, the one term the
    [:10] slice excluded -- these entries stayed in Q2 boards."""
    boards, journal = _render_service([_q2_reviewer("Annals of Neurology")])
    assert boards == []
    assert [r[0] for r in journal] == ["Annals of Neurology"]


def test_oncology_reviewer_still_reroutes():
    """Control: terms inside the old slice keep their behavior."""
    boards, journal = _render_service([_q2_reviewer("Annals of Oncology")])
    assert boards == []
    assert [r[0] for r in journal] == ["Annals of Oncology"]


def test_editorial_board_phrase_still_reroutes():
    """The three role phrases re-listed inline next to the slice now live in
    JOURNAL_ROLE_PHRASES; the predicate must keep consulting them."""
    boards, journal = _render_service([
        {"taxonomy_code": "Q2",
         "text": "Editorial Board, Journal of Fictional Medicine",
         "extracted_fields": {"organization": "Journal of Fictional Medicine"}},
    ])
    assert boards == []
    assert [r[0] for r in journal] == ["Journal of Fictional Medicine"]


def test_board_committee_entry_stays_in_boards_table():
    """Control: BOARD_KEYWORDS still veto the reroute."""
    boards, journal = _render_service([
        {"taxonomy_code": "Q2", "text": "Member, Education Committee",
         "extracted_fields": {"role": "Member",
                              "committee_name": "Education Committee"}},
    ])
    assert journal == []
    assert [r[0] for r in boards] == ["Education Committee"]


def test_multiline_q2_entry_splits_journal_and_board_lines():
    """A mixed multi-line Q2 entry must route each line to the correct
    table, and a trailing bare-date continuation line must stay attached to
    the line it describes rather than defaulting to the board entry -- the
    old default silently orphaned it there even when the described line was
    rerouted to journal reviewing."""
    boards, journal = _render_service([
        {"taxonomy_code": "Q2",
         "text": "John Smith\nReviewer for JAMA\n2024",
         "extracted_fields": {}},
    ])
    assert [r[0] for r in boards] == ["John Smith"]
    assert len(journal) == 1
    assert journal[0][0].startswith("JAMA") or "JAMA" in journal[0][0]
    assert "2024" in journal[0][0]


def test_multiline_q2_reroute_preserves_entry_level_dates():
    """The synthetic per-line entry created for a rerouted journal line must
    not discard the parent entry's own extracted_fields (start/end dates) --
    the old version replaced extracted_fields wholesale with only
    {'organization': jline}."""
    boards, journal = _render_service([
        {"taxonomy_code": "Q2",
         "text": "Ad hoc reviewer, Annals of Neurology\nMember, Education Committee",
         "extracted_fields": {"start_date": "2020", "end_date": "2022",
                              "committee_name": "Education Committee"}},
    ])
    assert journal[0][1] != ""  # dates column is populated, not blank


# ---------------------------------------------------------------------------
# 3. C3 (Fellowship Training) render path


def _render_postdoc(entries_by_code):
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document()
    gen.doc.add_paragraph("C. POSTDOCTORAL TRAINING")
    table = gen.doc.add_table(rows=1, cols=3)
    for i, header in enumerate(["Postdoctoral Training",
                                "Institution and Location", "Dates"]):
        table.rows[0].cells[i].text = header
    gen._fill_postdoc_training(entries_by_code)
    return [[c.text for c in r.cells] for r in table.rows[1:]]


def test_c3_fellowship_renders_in_postdoc_table():
    """C3 must reach the Postdoctoral Training table using its own mm/yy
    date rule (07/18-06/21, not a yyyy fallback). Before this fix, C3 was
    absent from the gather entirely, so fellowships fell to the Appendix
    regardless of confidence -- see test_coverage_lint_accepts_c3 and the
    RENDER_ROUTED_CODES assertion below for the direct Appendix-routing
    regression check."""
    rows = _render_postdoc({
        "C2": [{"taxonomy_code": "C2", "text": "Residency, Pediatrics",
                "extracted_fields": {"training_type": "Residency",
                                     "institution": "Fictional University Hospital",
                                     "start_date": "2015-07",
                                     "end_date": "2018-06"}}],
        "C3": [{"taxonomy_code": "C3", "text": "Fellowship, Neonatology",
                "extracted_fields": {"training_type": "Fellowship",
                                     "institution": "Made-up Children's Hospital",
                                     "start_date": "2018-07",
                                     "end_date": "2021-06"}}],
    })
    fellowship_rows = [r for r in rows if r[0].startswith("Fellowship")]
    assert len(fellowship_rows) == 1
    assert fellowship_rows[0][1].startswith("Made-up Children's Hospital")
    assert fellowship_rows[0][2] == "07/18-06/21"
    # the residency sibling still renders alongside it
    assert any(r[0].startswith("Residency") for r in rows)


def test_c3_has_a_date_format():
    assert DATE_FORMATS["C3"] == "mm/yy"


def test_c3_is_render_routed_not_appendix_bound():
    """The direct negative assertion for the historical bug: a code missing
    from RENDER_ROUTED_CODES falls through to the Appendix by construction
    (#529's class of gap). C3 must be in the set."""
    assert 'C3' in RENDER_ROUTED_CODES


def test_coverage_lint_accepts_c3():
    """The #529 coverage lint reads RENDER_ROUTED_CODES; C3 entries must no
    longer be flagged as appendix-by-construction."""
    stage3b = {"entries": [
        {"taxonomy_code": "C3", "element_type": "entry",
         "text": "Fellowship, Neonatology"},
    ]}
    assert lint_taxonomy_code_coverage(stage3b) == []


def test_none_extracted_fields_does_not_crash_postdoc():
    """extracted_fields=None must not raise -- the postdoc renderer and the
    institution-location resolver it calls both used to default only on a
    missing key, not an explicit None."""
    rows = _render_postdoc({
        "C2": [{"taxonomy_code": "C2", "text": "Residency, Pediatrics",
                "extracted_fields": None}],
    })
    assert len(rows) == 1
    assert rows[0][2] == ""  # no dates extracted, but it didn't crash


def test_list_valued_training_type_does_not_crash():
    """A fused multi-record entry extracts training_type/field_of_study as
    a list, one item per record, not a string (corpus CV FIHL8A) -- must
    join into display text, not crash on a list where a string method
    (.casefold(), tab-splitting) is expected."""
    rows = _render_postdoc({
        "C1": [{"taxonomy_code": "C1", "text": "fellowship entries",
                "extracted_fields": {
                    "training_type": ["Cytopathology fellow", "Pathology chief resident"],
                    "specialty": ["Cytopathology", "Pathology"],
                    "institution": "Fictional Health System",
                }}],
    })
    assert len(rows) == 1
    assert "Cytopathology fellow" in rows[0][0]
    assert "Pathology chief resident" in rows[0][0]


def test_missing_training_type_uses_taxonomy_default():
    """A C2/C3 entry with neither training_type nor title must not render
    as the generic "Postdoctoral" -- that mislabels a residency or
    fellowship."""
    rows = _render_postdoc({
        "C2": [{"taxonomy_code": "C2", "text": "residency entry",
                "extracted_fields": {"institution": "Fictional Hospital"}}],
        "C3": [{"taxonomy_code": "C3", "text": "fellowship entry",
                "extracted_fields": {"institution": "Fictional Hospital"}}],
    })
    types = {r[0] for r in rows}
    assert "Residency" in types
    assert "Fellowship" in types
    assert "Postdoctoral" not in types
