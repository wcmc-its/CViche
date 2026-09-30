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

import dataclasses
import sys
from pathlib import Path

import pytest

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
from unified_pipeline.stage6.sections.licensure import (  # noqa: E402
    IdentifierSet,
    LicenseRecord,
    LicensureEntry,
    LicensureResult,
    _license_record,
    _normalize_licensure_entry,
    _resolve_licensure,
)
from unified_pipeline.stage6.sections.postdoc_training import (  # noqa: E402
    DEFAULT_TRAINING_TYPES,
    FALLBACK_TRAINING_TYPE,
    INSTITUTION_ENRICHMENT_REASON,
    POSTDOC_TRAINING_CODES,
    PostdocTrainingRecord,
    _institution_content,
    _normalize_training_entry,
    _resolve_postdoc_training,
)
from unified_pipeline.stage6.sections.service import (  # noqa: E402
    _is_board_role_title,
    _is_known_org_line,
    _is_q2_journal_reviewer,
    _route_q2_entries,
    _split_q2_lines,
)


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
    """A DEA number still classifies out of the licence table -- #821
    withholds the identifier itself, so the slot renders empty rather than
    the value; the NPI is public and still renders."""
    rows, dea, npi = _render_licensure([
        _f1("DEA registration AB1234567", number="AB1234567"),
        _f1("NPI 1234567890", number="1234567890"),
    ])
    assert rows == []
    assert dea == ""
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
    """The 'dea' license_type route, with a number no shape test matches --
    still classified DEA, so #821 still withholds it (the slot is empty,
    not the raw "55443")."""
    rows, dea, npi = _render_licensure([
        _f1("Registration 55443", number="55443", license_type="DEA"),
    ])
    assert rows == []
    assert dea == ""
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
    """Unlabelled, state-less entries still classify by shape -- the
    DEA-shaped one is still withheld (#821), the NPI-shaped one renders."""
    rows, dea, npi = _render_licensure([
        _f1("1234567890", number="1234567890"),
        _f1("AB1234567", number="AB1234567"),
    ])
    assert rows == []
    assert npi == "1234567890"
    assert dea == ""


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
# 1b. Licensure: the normalization step, pinned on its own
#
# `_fill_licensure` is now a renderer only -- `_normalize_licensure_entry`,
# `_license_record` and `_resolve_licensure` decide everything and touch no
# document (#624 review). These tests call them directly, with no
# WCMTemplateGenerator and no docx object, which is the property the split was
# for. The alias chains they pin are `or` chains, not `.get(key, default)`:
# stage 4 writes an alias as an empty string about as often as it omits the
# key, so an empty string MUST fall through to the next alias. A `.get`
# default would keep the empty string and silently drop the real value.


def test_normalize_prefers_the_first_state_alias():
    """Precedence, all three aliases present and non-empty."""
    entry = _normalize_licensure_entry({
        "extracted_fields": {"state_country": "New York", "state": "NY",
                             "jurisdiction": "New York State"},
    })
    assert isinstance(entry, LicensureEntry)
    assert entry.state == "New York"


def test_normalize_state_alias_falls_through_an_empty_string():
    """The falsy-fallback half of the chain: an alias present but empty must
    not win. This is what an `or` chain does and a `.get(key, default)` does
    not -- the whole reason the chain is written this way."""
    assert _normalize_licensure_entry({
        "extracted_fields": {"state_country": "", "state": "NY"},
    }).state == "NY"
    assert _normalize_licensure_entry({
        "extracted_fields": {"state_country": "", "state": "",
                             "jurisdiction": "Ontario"},
    }).state == "Ontario"
    assert _normalize_licensure_entry({
        "extracted_fields": {"state_country": "", "state": "",
                             "jurisdiction": ""},
    }).state == ""


def test_normalize_number_alias_falls_through_an_empty_string():
    """`license_number` then `number`, same falsy-fallback rule, and the
    result is stringified and stripped."""
    assert _normalize_licensure_entry({
        "extracted_fields": {"license_number": "123456", "number": "999"},
    }).number == "123456"
    assert _normalize_licensure_entry({
        "extracted_fields": {"license_number": "", "number": "  999  "},
    }).number == "999"
    assert _normalize_licensure_entry({
        "extracted_fields": {"number": 123456},
    }).number == "123456"
    assert _normalize_licensure_entry({"extracted_fields": {}}).number == ""


def test_normalize_issue_date_alias_falls_through_an_empty_string():
    assert _normalize_licensure_entry({
        "extracted_fields": {"issue_date": "2020-01-01", "date": "1999"},
    }).issue_date == "2020-01-01"
    assert _normalize_licensure_entry({
        "extracted_fields": {"issue_date": "", "date": "1999"},
    }).issue_date == "1999"


def test_normalize_defaults_every_field_to_empty_string():
    """extracted_fields is sometimes explicitly None, not merely absent, and
    'text' likewise -- the record must still be fully populated."""
    entry = _normalize_licensure_entry({"extracted_fields": None, "text": None})
    assert entry == LicensureEntry()
    assert (entry.state, entry.number, entry.issue_date,
            entry.expiration_date, entry.license_type,
            entry.original_text) == ("", "", "", "", "", "")


def test_normalized_entry_is_frozen():
    """A typed boundary record the renderer cannot edit behind the
    normalizer's back."""
    entry = _normalize_licensure_entry({"extracted_fields": {"state": "NY"}})
    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.state = "NJ"


def test_license_record_formats_dates_and_fills_the_registration_column():
    """expiration_date fills the template's 'Date of last registration'
    column, both dates through the F1 mm/dd/yyyy rule."""
    record = _license_record(LicensureEntry(
        state="New York", number="123456",
        issue_date="2015-07-01", expiration_date="2027-06-30"))
    assert isinstance(record, LicenseRecord)
    assert record.state == "New York"
    assert record.number == "123456"
    assert record.issue_date == "07/01/2015"
    assert record.last_registration_date == "06/30/2027"


def test_license_record_leaves_absent_dates_blank():
    """An empty date is written as '' rather than run through the formatter."""
    record = _license_record(LicensureEntry(state="NY", number="1"))
    assert (record.issue_date, record.last_registration_date) == ("", "")


def test_license_record_falls_back_to_truncated_raw_text():
    """No jurisdiction and no number: the raw line becomes the state cell,
    capped, and the other three columns stay blank."""
    record = _license_record(LicensureEntry(original_text="x" * 150))
    assert record == LicenseRecord(state="x" * 100)


def test_license_record_is_none_when_there_is_nothing_to_render():
    assert _license_record(LicensureEntry()) is None


def test_resolve_licensure_returns_a_typed_result_without_a_document():
    """The whole point of the split: routing, ordering and date formatting
    decided with no WCMTemplateGenerator, no docx object and no stats."""
    result = _resolve_licensure([
        _f1("New York State Medical License 123456",
            state="New York", number="123456", issue_date="2015-07-01"),
        _f1("NPI: 1234567890", number="1234567890"),
        _f1("DEA registration AB1234567", number="AB1234567"),
    ])
    assert isinstance(result, LicensureResult)
    assert isinstance(result.identifiers, IdentifierSet)
    assert result.licenses == (
        LicenseRecord(state="New York", number="123456",
                      issue_date="07/01/2015", last_registration_date=""),
    )
    # #821: the DEA identifier is withheld, never reaches `identifiers.dea`.
    assert result.identifiers == IdentifierSet(dea=None, npi="1234567890")
    assert result.dea_withheld is True


def test_resolve_licensure_leaves_unseen_identifier_slots_none():
    """None, not '' -- _fill_dea_npi is what turns an absent slot into a
    blanked cell, and it must still be able to tell the two apart."""
    result = _resolve_licensure([_f1("NY license 1", state="NY", number="1")])
    assert result.identifiers == IdentifierSet(dea=None, npi=None)


def test_resolve_licensure_normalizes_before_classifying():
    """The state alias resolved by the normalizer is the one that disables
    the shape tiebreak: a 10-digit number under 'jurisdiction' alone must
    stay a licence row, not become the NPI."""
    result = _resolve_licensure([
        {"taxonomy_code": "F1", "text": "Medical License 3512345678",
         "extracted_fields": {"state_country": "", "state": "",
                              "jurisdiction": "Michigan",
                              "license_number": "3512345678"}},
    ])
    assert result.identifiers.npi is None
    assert [r.state for r in result.licenses] == ["Michigan"]


# ---------------------------------------------------------------------------
# 2. Service: journal_keywords[:10] dropped 'neurology'


def _render_service(q2_entries):
    """Drive the real _fill_service reroute + both table writers.

    Returns (board_rows, journal_rows)."""
    doc = Document()
    doc.add_paragraph().add_run("Service on Boards and/or Committees").bold = True
    boards_table = doc.add_table(rows=1, cols=4)
    for i, header in enumerate(["Name of Committee", "Role", "Organization",
                                "Dates"]):
        boards_table.rows[0].cells[i].text = header
    doc.add_paragraph("Journal Reviewing/Ad hoc Reviewing")
    journal_table = doc.add_table(rows=1, cols=2)
    for i, header in enumerate(["Journal / Organization Name", "Dates"]):
        journal_table.rows[0].cells[i].text = header
    # Labels are anchored only if the document had them when assigned (#548).
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = doc
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
# 2b. Service: the Q2 reroute routing, pinned on its own
#
# `_fill_service` is now a dispatcher only -- `_route_q2_entries`,
# `_split_q2_lines` and `_is_q2_journal_reviewer` decide the Q2/Q4D reroute
# and touch no document (#624 review). These tests call them directly, with
# no WCMTemplateGenerator and no docx object, which is the property the
# split was for.


def test_is_q2_journal_reviewer_true_for_specialty_role():
    """role == 'reviewer' plus a JOURNAL_SPECIALTY_KEYWORDS hit reroutes."""
    assert _is_q2_journal_reviewer(
        "reviewer, annals of neurology", "reviewer", "", "") is True


def test_is_q2_journal_reviewer_false_when_board_keyword_present():
    """BOARD_KEYWORDS vetoes the reroute even when a journal signal also
    matches."""
    assert _is_q2_journal_reviewer(
        "ad hoc reviewer, education committee", "reviewer", "", "") is False


def test_is_q2_journal_reviewer_false_with_no_signal():
    assert _is_q2_journal_reviewer(
        "member, education committee", "member", "", "") is False


def test_split_q2_lines_separates_journal_and_board_lines():
    journal_lines, board_lines = _split_q2_lines(
        ["John Smith", "Reviewer for JAMA"])
    assert board_lines == ["John Smith"]
    assert journal_lines == ["Reviewer for JAMA"]


def test_split_q2_lines_extends_previous_line_with_trailing_date():
    """A trailing bare-date line stays attached to the line it describes
    rather than starting a new board-defaulted entry."""
    journal_lines, board_lines = _split_q2_lines(
        ["John Smith", "Reviewer for JAMA", "2024"])
    assert board_lines == ["John Smith"]
    assert journal_lines == ["Reviewer for JAMA 2024"]


def test_split_q2_lines_unclassified_content_line_starts_its_own_item():
    """An unclassified content line -- matching neither pattern list, and
    not a date -- must become its own board_lines item, not be silently
    absorbed into the preceding journal line."""
    journal_lines, board_lines = _split_q2_lines(
        ["Reviewer for JAMA", "Academic Pediatrics Journal Reviewer"])
    assert journal_lines == ["Reviewer for JAMA"]
    assert board_lines == ["Academic Pediatrics Journal Reviewer"]


def test_route_q2_entries_reroutes_single_line_journal_entry():
    rerouted, boards = _route_q2_entries([_q2_reviewer("Annals of Neurology")])
    assert boards == []
    assert len(rerouted) == 1
    assert rerouted[0]["taxonomy_code"] == "Q4D"
    assert rerouted[0]["rerouted_from_q2"] is True


def test_route_q2_entries_keeps_board_entry_unrerouted():
    entry = {"taxonomy_code": "Q2", "text": "Member, Education Committee",
             "extracted_fields": {"role": "Member",
                                  "committee_name": "Education Committee"}}
    rerouted, boards = _route_q2_entries([entry])
    assert rerouted == []
    assert boards == [entry]


def test_route_q2_entries_splits_multiline_entry_and_preserves_dates():
    """The synthetic per-line entry keeps the parent's date fields but not
    its identity fields (organization/committee_name/role/journal_name)."""
    rerouted, boards = _route_q2_entries([
        {"taxonomy_code": "Q2",
         "text": "Ad hoc reviewer, Annals of Neurology\nMember, Education Committee",
         "extracted_fields": {"start_date": "2020", "end_date": "2022",
                              "committee_name": "Education Committee"}},
    ])
    assert len(rerouted) == 1
    assert rerouted[0]["extracted_fields"] == {"start_date": "2020", "end_date": "2022"}
    assert len(boards) == 1
    assert boards[0]["text"] == "Member, Education Committee"


def test_route_q2_entries_does_not_mutate_the_input_entry():
    entry = {"taxonomy_code": "Q2", "text": "Reviewer, Annals of Neurology",
             "extracted_fields": {"role": "Reviewer",
                                  "organization": "Annals of Neurology"}}
    original = dict(entry)
    _route_q2_entries([entry])
    assert entry == original


# ---------------------------------------------------------------------------
# 2c. Service: precise organization detection in
# `_parse_extramural_leadership_lines` (#624 review)
#
# `is_org = any(org.lower() in line_lower for org in known_orgs)` used
# unrestricted substring matching, and known_orgs ended with generic bare
# terms ('Society', 'Association', 'Academy', 'Board', 'Institute',
# 'American College'), so "Board Member" -- plainly a role -- classified as
# an organization. `_is_known_org_line` is the free predicate that replaced
# it: a specific multi-word name/acronym still matches unconditionally, but
# a bare generic term matches only as a whole word and is vetoed entirely
# when the line also carries role vocabulary.

# Mirrors the `role_keywords` list local to
# `_parse_extramural_leadership_lines` (service.py); passed explicitly
# because `_is_known_org_line` takes it as a parameter rather than reading
# a module global.
_ROLE_KEYWORDS = ['member', 'chair', 'reviewer', 'liaison', 'mentor', 'committee',
                  'board', 'council', 'advisor', 'director', 'leader', 'representative']


def test_board_member_is_a_role_line_not_an_org():
    """The reviewer's motivating example: 'board' is a generic org term,
    but 'Board Member' names a role, not an organization."""
    assert _is_known_org_line("board member", _ROLE_KEYWORDS) is False


def test_member_board_of_directors_is_a_role_line():
    assert _is_known_org_line("member, board of directors", _ROLE_KEYWORDS) is False


def test_american_college_of_cardiology_is_an_org():
    assert _is_known_org_line("american college of cardiology", _ROLE_KEYWORDS) is True


def test_american_academy_of_pediatrics_is_an_org():
    """Control: a specific multi-word name already in `_KNOWN_ORG_NAMES`
    keeps matching unconditionally."""
    assert _is_known_org_line("american academy of pediatrics", _ROLE_KEYWORDS) is True


def test_society_of_critical_care_medicine_is_an_org():
    assert _is_known_org_line("society of critical care medicine", _ROLE_KEYWORDS) is True


def test_bare_generic_term_does_not_match_inside_a_longer_word():
    """Whole-word matching: 'associationism' must not match the generic
    'association' term as a mere prefix -- plain substring matching would
    have caught this false positive."""
    assert _is_known_org_line("associationism studies group", _ROLE_KEYWORDS) is False


# ---------------------------------------------------------------------------
# 2d. Service: qualified/variant role titles in `_fill_service_boards`
# (#624 review)
#
# `role.split(', ', 1)` followed by `role_word in BOARD_ROLE_TITLES` only
# recognized an exact base title, so "Chairperson, Ultrasound Committee"
# and "Past President, XYZ Committee" never split -- the whole string
# stayed in the role column. `_is_board_role_title` extends BOARD_ROLE_TITLES
# (still the single source of truth for base titles) with chair variants
# and an explicit qualifier list, whole-phrase matching only.


def test_is_board_role_title_exact_base_title():
    """Control: an exact BOARD_ROLE_TITLES entry still matches."""
    assert _is_board_role_title("chair") is True


def test_is_board_role_title_chair_variant():
    assert _is_board_role_title("chairperson") is True


def test_is_board_role_title_qualified_president():
    assert _is_board_role_title("past president") is True


def test_is_board_role_title_qualified_chair_variant():
    assert _is_board_role_title("interim chairperson") is True


def test_is_board_role_title_rejects_membership_committee():
    """Negative control: 'membership committee' shares the substring
    'member' with the base title 'member', but whole-phrase matching must
    not mistake it for a role title."""
    assert _is_board_role_title("membership committee") is False


def test_is_board_role_title_rejects_unknown_word():
    assert _is_board_role_title("coordinator") is False


def test_fill_service_boards_splits_chairperson_variant():
    """The reviewer's first example, driven through the real
    `_fill_service_boards` render path."""
    boards, _ = _render_service([
        {"taxonomy_code": "Q2", "text": "Chairperson, Ultrasound Committee",
         "extracted_fields": {"role": "Chairperson, Ultrasound Committee"}},
    ])
    assert len(boards) == 1
    committee, role, org, _dates = boards[0]
    assert committee == "Ultrasound Committee"
    assert role == "Chairperson"


def test_fill_service_boards_splits_past_president_variant():
    """The reviewer's second example."""
    boards, _ = _render_service([
        {"taxonomy_code": "Q2", "text": "Past President, XYZ Committee",
         "extracted_fields": {"role": "Past President, XYZ Committee"}},
    ])
    assert len(boards) == 1
    committee, role, org, _dates = boards[0]
    assert committee == "XYZ Committee"
    assert role == "Past President"


def test_fill_service_boards_still_splits_an_exact_base_title():
    """Control: the pre-existing behavior for an exact base title
    ('Chair') must keep splitting."""
    boards, _ = _render_service([
        {"taxonomy_code": "Q2", "text": "Chair, X Committee",
         "extracted_fields": {"role": "Chair, X Committee"}},
    ])
    assert len(boards) == 1
    committee, role, org, _dates = boards[0]
    assert committee == "X Committee"
    assert role == "Chair"


def test_fill_service_boards_leaves_a_comma_free_role_whole():
    """Control: a role with no comma is never split, and falls back to
    'Member' -> committee via the organization fallback since none is
    given here."""
    boards, _ = _render_service([
        {"taxonomy_code": "Q2", "text": "Board Member",
         "extracted_fields": {"role": "Member",
                              "organization": "Regional Health Board"}},
    ])
    assert len(boards) == 1
    committee, role, org, _dates = boards[0]
    assert role == "Member"
    assert committee == "Regional Health Board"


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


# ---------------------------------------------------------------------------
# 3b. Postdoc training: the normalization step, pinned on its own
#
# `_fill_postdoc_training` is now a renderer only -- `_resolve_postdoc_training`
# and `_normalize_training_entry` decide everything and touch no document
# (#624 review). These tests call them directly, with no WCMTemplateGenerator
# and no docx object, which is the property the split was for. The alias
# chains they pin are `or` chains, not `.get(key, default)`: stage 4 writes an
# alias as an empty string about as often as it omits the key, so an empty
# string MUST fall through to the next alias. A `.get` default would keep the
# empty string and render a row with a blank training type instead of the
# taxonomy default.


def test_normalize_prefers_training_type_over_title():
    """Precedence, both aliases present and non-empty."""
    record = _normalize_training_entry(
        {"extracted_fields": {"training_type": "Research Fellow",
                              "title": "Senior Fellow"}}, "C1")
    assert isinstance(record, PostdocTrainingRecord)
    assert record.training_type == "Research Fellow"


def test_normalize_training_type_falls_through_an_empty_string():
    """The falsy-fallback half of the chain: an alias present but empty must
    not win, and must not shadow the taxonomy default either. This is what an
    `or` chain does and a `.get(key, default)` does not -- the whole reason
    the chain is written this way."""
    assert _normalize_training_entry(
        {"extracted_fields": {"training_type": "", "title": "Senior Fellow"}},
        "C1").training_type == "Senior Fellow"
    assert _normalize_training_entry(
        {"extracted_fields": {"training_type": "", "title": ""}},
        "C2").training_type == "Residency"
    # an empty list is falsy too -- a fused multi-record entry can produce one
    assert _normalize_training_entry(
        {"extracted_fields": {"training_type": [], "title": "Chief Resident"}},
        "C2").training_type == "Chief Resident"


def test_normalize_field_of_study_falls_through_an_empty_string():
    """`field_of_study` then `specialty`, same falsy-fallback rule, appended
    to the type rather than replacing it."""
    assert _normalize_training_entry(
        {"extracted_fields": {"training_type": "Fellow",
                              "field_of_study": "Neonatology",
                              "specialty": "Cardiology"}},
        "C3").training_type == "Fellow, Neonatology"
    assert _normalize_training_entry(
        {"extracted_fields": {"training_type": "Fellow",
                              "field_of_study": "", "specialty": "Cardiology"}},
        "C3").training_type == "Fellow, Cardiology"
    assert _normalize_training_entry(
        {"extracted_fields": {"training_type": "Fellow",
                              "field_of_study": "", "specialty": ""}},
        "C3").training_type == "Fellow"


def test_normalize_does_not_duplicate_field_of_study_case_insensitively():
    """"Fellow in CARDIOLOGY" plus field_of_study "cardiology" must not become
    "Fellow in CARDIOLOGY, cardiology". The containment check is casefolded
    because the tab join immediately above routinely changes the casing that
    reaches it -- a case-sensitive check would append the specialty a second
    time. Asked for by name as
    test_field_of_study_is_not_duplicated_case_insensitively (#624 review)."""
    assert _normalize_training_entry(
        {"extracted_fields": {"training_type": "Fellow in CARDIOLOGY",
                              "field_of_study": "cardiology"}},
        "C1").training_type == "Fellow in CARDIOLOGY"
    assert _normalize_training_entry(
        {"extracted_fields": {"training_type": "Fellow\tcardiology",
                              "specialty": "Cardiology"}},
        "C1").training_type == "Fellow, cardiology"


def test_normalize_institution_falls_through_an_empty_cleaned_name():
    """Stage 5b's cleaned name wins, but only when it is non-empty -- an
    empty one must fall through to official_name and then to the raw
    extracted field, not blank the institution."""
    enriched = {"extracted_fields": {"institution": "Duke Medical Center, Durham, NC"},
                "institution_enrichment": {"cleaned_name": "Duke Medical Center"}}
    assert _normalize_training_entry(enriched, "C1").institution == "Duke Medical Center"

    official_only = {"extracted_fields": {"institution": "raw inst"},
                     "institution_enrichment": {"cleaned_name": "",
                                                "official_name": "Fictional Regional Hospital"}}
    assert _normalize_training_entry(official_only, "C1").institution == "Fictional Regional Hospital"

    raw_only = {"extracted_fields": {"institution": "Fictional University"},
                "institution_enrichment": {"cleaned_name": "", "official_name": ""}}
    assert _normalize_training_entry(raw_only, "C1").institution == "Fictional University"


def test_normalize_recovers_a_missing_institution_from_nearby_entries():
    """Third and last institution step, and the one that needs `all_entries`
    -- which is why the normalizer takes it. It runs only when the first two
    produced nothing. The heading's institution sits on a PRECEDING training
    entry of the same block (#1038)."""
    entry = {"text": "Graduate Research Assistant", "hierarchy": ["Training"],
             "element_idx_start": 4, "element_idx_end": 4,
             "extracted_fields": {"training_type": "Fellow"}}
    neighbours = [{"element_idx_start": 3, "taxonomy_code": "C", "hierarchy": ["Training"],
                   "text": "Internship 2001",
                   "extracted_fields": {"institution": "University of Nowhere"}}]
    assert _normalize_training_entry(entry, "C1", neighbours).institution == "University of Nowhere"
    # no neighbours passed at all: still normalizes, just without recovery
    assert _normalize_training_entry(entry, "C1").institution == ""


@pytest.mark.parametrize(("code", "expected"), [
    ("C", "Postdoctoral"),
    ("C1", "Postdoctoral Research"),
    ("C2", "Residency"),
    ("C3", "Fellowship"),
])
def test_normalize_missing_training_type_uses_the_taxonomy_default(code, expected):
    """A C2/C3 entry with neither training_type nor title must not render as
    the generic "Postdoctoral" -- that mislabels a residency or fellowship
    (#573 review). Same assertion as the rendered-table test above, but on
    the rule itself rather than on a Word cell."""
    record = _normalize_training_entry({"extracted_fields": {}}, code)
    assert record.training_type == expected
    assert DEFAULT_TRAINING_TYPES[code] == expected


def test_normalize_unknown_code_uses_the_generic_fallback():
    """A code outside POSTDOC_TRAINING_CODES cannot reach the renderer today,
    but the default must still be a word rather than a blank cell."""
    assert "C9" not in POSTDOC_TRAINING_CODES
    assert _normalize_training_entry(
        {"extracted_fields": {}}, "C9").training_type == FALLBACK_TRAINING_TYPE


def test_normalize_none_extracted_fields_does_not_crash():
    """extracted_fields is sometimes explicitly None, not merely absent --
    the record must still be fully populated with empty strings."""
    record = _normalize_training_entry({"extracted_fields": None}, "C2")
    assert record.training_type == "Residency"
    assert (record.institution, record.location, record.dates) == ("", "", "")
    assert record.location_is_enriched is False


def test_normalize_joins_tabs_and_lists_into_display_text():
    """A tab is the extractor having flattened a table cell boundary; a list
    is a fused multi-record entry (corpus CV FIHL8A). Both are joined before
    any string method sees them."""
    assert _normalize_training_entry(
        {"extracted_fields": {"training_type": "Fellow\tCardiology\t"}},
        "C1").training_type == "Fellow, Cardiology"
    assert _normalize_training_entry(
        {"extracted_fields": {"training_type": ["Cytopathology fellow",
                                                "Pathology chief resident"]}},
        "C1").training_type == "Cytopathology fellow, Pathology chief resident"


def test_normalize_drops_a_location_the_institution_already_names():
    """"Massachusetts General Hospital, Boston, MA" must not gain a second
    Boston. Dropping it in the normalizer is what lets the renderer have no
    de-duplication rule of its own to keep in step with this one."""
    duplicated = {"extracted_fields": {"institution": "Fictional General Hospital, Springfield, MA"},
                  "institution_enrichment": {"city": "Springfield",
                                             "state": "Massachusetts",
                                             "country_code": "US"}}
    assert _normalize_training_entry(duplicated, "C1").location == ""

    fresh = {"extracted_fields": {"institution": "Fictional General Hospital"},
             "institution_enrichment": {"city": "Springfield",
                                        "state": "Massachusetts",
                                        "country_code": "US"}}
    record = _normalize_training_entry(fresh, "C1")
    assert record.location == "Springfield, MA"
    assert record.location_is_enriched is True


def test_normalize_location_match_is_word_boundary_anchored():
    """A short city name must not match inside an unrelated longer word --
    "York" is not already present in "Yorkshire Institute"."""
    entry = {"extracted_fields": {"institution": "Yorkshire Institute"},
             "institution_enrichment": {"city": "York", "state": "New York",
                                        "country_code": "US"}}
    assert _normalize_training_entry(entry, "C1").location == "York, NY"


def test_normalize_formats_dates_with_the_entrys_own_code():
    """C3 gets the mm/yy rule, not a generic yyyy fallback -- the reason the
    routing code is carried per record rather than per table."""
    entry = {"extracted_fields": {"start_date": "2018-07", "end_date": "2021-06"}}
    assert _normalize_training_entry(entry, "C3").dates == "07/18-06/21"
    assert _normalize_training_entry({"extracted_fields": {}}, "C3").dates == ""


def test_normalized_record_is_frozen():
    """A typed boundary record the renderer cannot edit behind the
    normalizer's back."""
    record = _normalize_training_entry({"extracted_fields": {}}, "C1")
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.training_type = "Something else"


def test_resolve_postdoc_training_returns_records_without_a_document():
    """The whole point of the split: gathering, ordering, the taxonomy
    defaults and date formatting decided with no WCMTemplateGenerator, no
    docx object and no stats."""
    records = _resolve_postdoc_training({
        "C2": [{"taxonomy_code": "C2", "text": "Residency",
                "extracted_fields": {"institution": "Fictional Hospital",
                                     "start_date": "2015-07", "end_date": "2018-06"}}],
        "C3": [{"taxonomy_code": "C3", "text": "Fellowship",
                "extracted_fields": {"institution": "Made-up Children's",
                                     "start_date": "2018-07", "end_date": "2021-06"}}],
    })
    assert all(isinstance(r, PostdocTrainingRecord) for r in records)
    # reverse chronological: the fellowship is the more recent of the two
    assert [r.taxonomy_code for r in records] == ["C3", "C2"]
    assert [r.training_type for r in records] == ["Fellowship", "Residency"]
    assert [r.dates for r in records] == ["07/18-06/21", "07/15-06/18"]


def test_resolve_postdoc_training_stamps_the_routing_code_over_the_entrys_own():
    """entries_by_code's key is authoritative: an entry filed under C3 whose
    own taxonomy_code field says C must still get C3's date rule, so a C3
    entry cannot silently fall back to the generic C rule (#573 review)."""
    records = _resolve_postdoc_training({
        "C3": [{"taxonomy_code": "C", "text": "Fellowship",
                "extracted_fields": {"start_date": "2018-07", "end_date": "2021-06"}}],
    })
    assert [r.taxonomy_code for r in records] == ["C3"]
    assert records[0].training_type == "Fellowship"
    assert records[0].dates == "07/18-06/21"
    # the record carries the stamped copy, not the caller's entry
    assert records[0].source_entry["taxonomy_code"] == "C3"


def test_resolve_postdoc_training_does_not_mutate_the_input_entries():
    """The routing code is stamped onto a shallow copy. The caller's stage-4
    entry keeps whatever it arrived with."""
    entry = {"taxonomy_code": "C", "text": "Fellowship", "extracted_fields": {}}
    _resolve_postdoc_training({"C3": [entry]})
    assert entry["taxonomy_code"] == "C"


def test_resolve_postdoc_training_is_empty_when_no_code_matches():
    assert _resolve_postdoc_training({}) == ()
    assert _resolve_postdoc_training({"C9": [{"text": "x"}], "C": []}) == ()


def test_pipeline_comments_reach_the_row_with_the_stamped_code():
    """`source_entry` exists on the record for exactly one reason: it is what
    `_add_table_row_with_mixed_content` reads to attach upstream pipeline
    comments to the row. And it is the STAMPED copy, so the comment names the
    code the entry was routed under (C3) rather than the one the entry itself
    claimed (C) -- dropping the field, or carrying the raw entry, both change
    what a reviewer sees in the document."""
    gen = WCMTemplateGenerator(verbose=False)
    gen.emit_comments = True
    gen.doc = Document()
    gen.doc.add_paragraph("C. POSTDOCTORAL TRAINING")
    gen.doc.add_table(rows=1, cols=3)
    gen._fill_postdoc_training({"C3": [
        {"taxonomy_code": "C", "text": "Fellowship",
         "classification_reasoning": "matched a fellowship heading",
         "extracted_fields": {"institution": "Fictional University"}}]})
    assert [c["text"] for c in gen._comments] == [
        "Classified as C3: matched a fellowship heading"]


def test_institution_content_marks_only_the_enriched_location_as_a_change():
    """Three branches, one per way a location can reach the cell: enriched
    (tracked insertion), extracted (plain text), or absent."""
    enriched = PostdocTrainingRecord(institution="Fictional General Hospital",
                                     location="Springfield, MA",
                                     location_is_enriched=True)
    assert _institution_content(enriched) == [
        ("Fictional General Hospital", False, ""),
        (", Springfield, MA", True, INSTITUTION_ENRICHMENT_REASON),
    ]

    extracted = PostdocTrainingRecord(institution="Fictional University",
                                      location="Columbus, OH")
    assert _institution_content(extracted) == [
        ("Fictional University, Columbus, OH", False, ""),
    ]

    no_location = PostdocTrainingRecord(institution="Fictional University")
    assert _institution_content(no_location) == [("Fictional University", False, "")]


def test_institution_content_renders_a_lone_location():
    """No institution at all: the location stands on its own, still tracked
    when it came from enrichment."""
    assert _institution_content(
        PostdocTrainingRecord(location="Springfield, MA", location_is_enriched=True)
    ) == [("Springfield, MA", True, INSTITUTION_ENRICHMENT_REASON)]
    assert _institution_content(
        PostdocTrainingRecord(location="Columbus, OH")
    ) == [("Columbus, OH", False, "")]


def test_template_without_the_section_does_not_normalize_entries():
    """A template with no POSTDOCTORAL/TRAINING paragraph must return quietly,
    without normalizing a single entry.

    `_normalize_training_entry` reads stage-4 fields and a malformed one can
    raise (a list-valued `location` reaches `location.split(',')` unguarded).
    Hoisting normalization above the section lookup -- which the #624 review
    refactor briefly did -- turns that quiet return into an AttributeError
    that aborts the whole of stage 6 for a template that never had the table.
    `_gather_postdoc_entries` exists to give `_fill_postdoc_training` its
    entry count without that risk; this pins the ordering.
    """
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document()
    gen.doc.add_paragraph("A. BIOGRAPHICAL")  # no postdoc section at all

    gen._fill_postdoc_training({
        "C1": [{"text": "Postdoctoral Fellow, Fictional University",
                "extracted_fields": {"location": ["Boston", "MA"],
                                     "institution": "Fictional University"}}],
    })

    assert gen.stats['tables_populated'] == 0


def test_normalize_keeps_the_location_of_a_city_named_institution():
    """#897: `_normalize_training_entry` blanked `location` whenever the city
    WORD was inside the institution name, so every residency at "New York
    University School of Medicine" rendered without ", New York, NY". Only a
    trailing ", City[, ST]" on the institution string counts as present."""
    enrichment = {"cleaned_name": "Crab Hollow University School of Medicine",
                  "city": "Crab Hollow", "state": "New York", "country_code": "US"}
    kept = _normalize_training_entry(
        {"extracted_fields": {"training_type": "Residency", "specialty": "Pediatrics",
                              "institution": "Crab Hollow University School of Medicine"},
         "institution_enrichment": enrichment}, "C")
    assert kept.institution == "Crab Hollow University School of Medicine"
    assert kept.location == "Crab Hollow, NY"

    # the de-duplication the check was written for still holds when 5b
    # returned no cleaned name and the raw field carries the location
    dup = _normalize_training_entry(
        {"extracted_fields": {"training_type": "Residency",
                              "institution": "Norvale General Hospital, Crab Hollow, NY"},
         "institution_enrichment": {"city": "Crab Hollow", "state": "New York",
                                    "country_code": "US"}}, "C")
    assert dup.institution == "Norvale General Hospital, Crab Hollow, NY"
    assert dup.location == ""
