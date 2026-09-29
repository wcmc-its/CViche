"""#983: a stage-4 list of sibling records under a key the schema does not
define is expanded to one entry per record, so each record renders as its own
row instead of the whole entry falling back to raw, tab-joined, truncated text.

Two layers, because the pure helper alone would not catch a call site that
stopped calling it:

* `fan_out_multi_record_entries` on plain dicts (the rules);
* the real `generate()` path against the committed WCM template (the wire).

Synthetic entries only, no PII.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_fan_out.py -p no:cacheprovider
"""

import json
import sys
from pathlib import Path

from docx import Document

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage4.schemas import get_field_schema  # noqa: E402
from unified_pipeline.stage6.dedup import deduplicate_entries  # noqa: E402
from unified_pipeline.stage6.fan_out import fan_out_multi_record_entries  # noqa: E402
from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402


def _schema_fields(code):
    return get_field_schema(code)["fields"]


def _fan_out(entries):
    return fan_out_multi_record_entries(entries, _schema_fields)


def _honors_entry(text, awards, **scalars):
    return {"taxonomy_code": "H", "text": text,
            "extracted_fields": {"awards": awards, **scalars}}


_AWARDS = [
    {"award_name": "Alpha Honor Society", "granting_body": "Example University", "date": "1986"},
    {"award_name": "Beta Honor Society", "granting_body": "Example University", "date": "1986"},
    {"award_name": "Gamma Honor Society", "granting_body": "Example University", "date": "1986"},
]
_AWARDS_TEXT = ("1986    Alpha Honor Society, Example University\t"
                "Beta Honor Society, Example University\t"
                "Gamma Honor Society, Example University")


# --- the rules ---------------------------------------------------------------

def test_each_record_becomes_its_own_entry_with_its_fields_and_segment():
    out = _fan_out([_honors_entry(_AWARDS_TEXT, _AWARDS)])

    assert [e["extracted_fields"]["award_name"] for e in out] == [
        "Alpha Honor Society", "Beta Honor Society", "Gamma Honor Society"]
    assert all("awards" not in e["extracted_fields"] for e in out)
    # one tab segment per record: each child carries its own, tab-free
    assert out[1]["text"] == "Beta Honor Society, Example University"
    assert out[1]["fanned_out_from"] == {"key": "awards", "index": 1, "of": 3}
    assert all(e["taxonomy_code"] == "H" for e in out)


def test_text_is_built_from_the_record_when_segments_do_not_match_the_count():
    # 2 records, 3 tab segments (a name split across a paragraph break)
    text = "1988    Delta Omega, National Honorary, University of\tPittsburgh\tSecond Award"
    awards = [
        {"award_name": "Delta Omega, National Honorary", "granting_body": "University of Pittsburgh", "date": "1988"},
        {"award_name": "Second Award", "granting_body": None, "date": "1988"},
    ]
    out = _fan_out([_honors_entry(text, awards)])

    assert len(out) == 2
    assert "\t" not in out[0]["text"]
    assert out[0]["text"] == "Delta Omega, National Honorary, University of Pittsburgh, 1988"


def test_shared_scalars_are_inherited_and_an_empty_value_does_not_erase_them():
    entry = {"taxonomy_code": "B1", "text": "Example University\tM.S. 2015\tPh.D. 2018",
             "extracted_fields": {
                 "institution": "Example University",
                 "degrees": [{"degree": "M.S.", "year": "2015", "institution": None},
                             {"degree": "Ph.D.", "year": "2018"}]}}
    out = _fan_out([entry])

    assert [(e["extracted_fields"]["degree"], e["extracted_fields"]["institution"])
            for e in out] == [("M.S.", "Example University"), ("Ph.D.", "Example University")]


def test_a_parent_that_already_holds_the_first_record_stays_and_children_do_not_inherit_it():
    # stage 4 put the first role in the scalars and only the REST in the list
    entry = {"taxonomy_code": "O",
             "text": "2012- Co-Leader, Program\t2015-2022 Leader\t2022- Deputy",
             "extracted_fields": {
                 "leadership_role": "Co-Leader, Program", "institution": "Example Center",
                 "start_date": "2012", "end_date": None,
                 "additional_roles": [
                     {"leadership_role": "Leader", "start_date": "2015", "end_date": "2022"},
                     {"leadership_role": "Deputy", "start_date": "2022", "end_date": "present"}]}}
    out = _fan_out([entry])

    assert [e["extracted_fields"].get("leadership_role") for e in out] == [
        "Co-Leader, Program", "Leader", "Deputy"]
    assert out[0]["text"] == entry["text"]          # the first record keeps its own text
    assert "institution" not in out[1]["extracted_fields"]   # not the first record's context


def test_a_parent_holding_only_shared_dates_is_replaced_not_kept():
    # one date range stated for the list: context, not a record of its own
    entry = {"taxonomy_code": "P", "text": "2018-2022  Alpha Committee\tBeta Committee",
             "extracted_fields": {"start_date": "2018", "end_date": "2022", "entries": [
                 {"committee_name": "Alpha Committee", "role": "Chair", "start_date": "2018", "end_date": "2022"},
                 {"committee_name": "Beta Committee", "role": "Member", "start_date": "2018", "end_date": "2022"}]}}
    out = _fan_out([entry])

    assert [e["extracted_fields"]["committee_name"] for e in out] == ["Alpha Committee", "Beta Committee"]


def test_a_parent_whose_dates_differ_from_the_records_is_kept_as_the_first_record():
    entry = {"taxonomy_code": "I", "text": "2007- Example Network\t-Candidate Member, 2007-2009\t-Full Member, 2009-",
             "extracted_fields": {"organization": "Example Network", "membership_type": "Full Member",
                                  "start_date": "2007", "end_date": "present", "additional_roles": [
                 {"role": "Candidate Member", "start_date": "2007", "end_date": "2009"},
                 {"role": "Full Member", "start_date": "2009", "end_date": "present"}]}}
    out = _fan_out([entry])

    assert len(out) == 3
    assert out[0]["extracted_fields"]["organization"] == "Example Network"   # the parent, unchanged
    assert out[1]["extracted_fields"] == {"role": "Candidate Member", "start_date": "2007", "end_date": "2009"}


def test_lists_that_are_not_sibling_records_are_left_alone():
    schema_key = next(iter(_schema_fields("H")))
    cases = {
        "one record only": {"awards": [{"award_name": "Solo"}]},
        "a schema field": {schema_key: [{"award_name": "A"}, {"award_name": "B"}]},
        "no key in the schema": {"things": [{"colour": "red"}, {"colour": "blue"}]},
        "not dicts": {"awards": ["A", "B"]},
    }
    for name, fields in cases.items():
        entry = {"taxonomy_code": "H", "text": "A\tB", "extracted_fields": fields}
        assert _fan_out([entry]) == [entry], name


def test_a_grants_nested_sub_awards_are_not_split_into_grants():
    entry = {"taxonomy_code": "M2A", "text": "Grant Title\tSub Alpha 100\tSub Beta 200",
             "extracted_fields": {"title": "Grant Title", "sub_awards": [
                 {"sub_title": "Sub Alpha", "total_funding": "100"},
                 {"sub_title": "Sub Beta", "total_funding": "200"}]}}
    assert _fan_out([entry]) == [entry]


def test_text_the_records_do_not_carry_is_kept_verbatim_as_a_residual_entry():
    text = ("2022-present  Alpha Committee\tBeta Committee\t"
            "Chaired the special search for the endowed chair in 2019 and 2020")
    entry = {"taxonomy_code": "P", "text": text, "extracted_fields": {"entries": [
        {"committee_name": "Alpha Committee", "role": "Member", "start_date": "2022", "end_date": "present"},
        {"committee_name": "Beta Committee", "role": "Member", "start_date": "2022", "end_date": "present"}]}}
    out = _fan_out([entry])

    assert len(out) == 3
    residual = out[-1]
    assert residual["fanned_out_from"] == {"residual": True}
    assert residual["extracted_fields"] == {}
    assert residual["text"] == "Chaired the special search for the endowed chair in 2019 and 2020"


def test_an_entry_the_records_do_not_account_for_at_all_is_left_whole():
    entry = {"taxonomy_code": "P",
             "text": "Prose about service on a number of college bodies over many years",
             "extracted_fields": {"entries": [{"committee_name": "Zzz"}, {"committee_name": "Yyy"}]}}
    assert _fan_out([entry]) == [entry]


def test_dedup_never_pairs_a_residual_with_a_record_it_repeats():
    child = {"taxonomy_code": "P", "text": "Alpha Committee member 2020",
             "extracted_fields": {"committee_name": "Alpha Committee"}}
    residual = {"taxonomy_code": "P", "text": "Alpha Committee member 2020 chaired the search",
                "extracted_fields": {}, "fanned_out_from": {"residual": True}}
    assert deduplicate_entries([child, residual]) == [child, residual]


# --- the wire: generate() ----------------------------------------------------

_OWNER_ENTRY = {"text": "Name: Jane Q. Public, MD", "taxonomy_code": "A",
                "extracted_fields": {}, "element_idx_start": 0}


def _render(tmp_path, entries):
    gen = WCMTemplateGenerator(verbose=False)
    gen._reconsider_appendix_entries = lambda: None   # no LLM; deterministic
    input_path = tmp_path / "in.json"
    output_path = tmp_path / "out.docx"
    input_path.write_text(json.dumps({"document_uid": "TESTEG", "entries": entries}))
    gen.generate(str(input_path), str(output_path), research_summary_path=None)
    return Document(str(output_path))


def _rows(doc):
    return [[c.text for c in row.cells] for table in doc.tables for row in table.rows]


def _all_cells(doc):
    return [cell for row in _rows(doc) for cell in row] + [p.text for p in doc.paragraphs]


def test_generate_renders_each_honor_as_its_own_row_without_tabs(tmp_path):
    doc = _render(tmp_path, [_OWNER_ENTRY, _honors_entry(_AWARDS_TEXT, _AWARDS)])
    rows = _rows(doc)

    for award in _AWARDS:
        assert [award["award_name"], "Example University", "1986"] in rows
    assert not [c for c in _all_cells(doc) if "Honor Society" in c and "\t" in c]


def test_generate_renders_each_committee_as_its_own_row_with_role_and_dates(tmp_path):
    text = "2016- present  Alpha Committee, Example School\tBeta Committee, Example School"
    entry = {"taxonomy_code": "P", "text": text, "extracted_fields": {"entries": [
        {"committee_name": "Alpha Committee", "role": "Chair", "institution": "Example School",
         "start_date": "2016", "end_date": "present"},
        {"committee_name": "Beta Committee", "role": "Member", "institution": "Example School",
         "start_date": "2016", "end_date": "present"}]}}
    doc = _render(tmp_path, [_OWNER_ENTRY, entry])
    rows = _rows(doc)

    assert ["Alpha Committee", "Chair", "2016-Present"] in rows
    assert ["Beta Committee", "Member", "2016-Present"] in rows
    assert not [c for c in _all_cells(doc) if "Committee" in c and "\t" in c]


def test_generate_keeps_a_residual_line_and_the_record_it_repeats(tmp_path):
    text = ("2022-present  Alpha Committee\tBeta Committee\t"
            "Chaired the special search for the endowed chair in 2019 and 2020")
    entry = {"taxonomy_code": "P", "text": text, "extracted_fields": {"entries": [
        {"committee_name": "Alpha Committee", "role": "Member", "start_date": "2022", "end_date": "present"},
        {"committee_name": "Beta Committee", "role": "Member", "start_date": "2022", "end_date": "present"}]}}
    cells = _all_cells(_render(tmp_path, [_OWNER_ENTRY, entry]))

    assert any("Chaired the special search" in c for c in cells), "residual text was dropped"
    assert "Alpha Committee" in cells and "Beta Committee" in cells


def test_generate_does_not_cut_a_raw_service_entry_mid_word(tmp_path):
    long_text = "Served as a member of the Long Named Advisory Council " * 6
    entry = {"taxonomy_code": "Q1", "text": long_text.strip(), "extracted_fields": {}}
    cells = _all_cells(_render(tmp_path, [_OWNER_ENTRY, entry]))

    assert any(long_text.strip() in c for c in cells)


def test_generate_strips_the_date_prefix_before_joining_tab_parts_in_other_service(tmp_path):
    """The service fallback removes a leading date with a regex that consumes
    the tab after it; joining the parts first left a stray "; " in front of the
    cell (the 2082 corpus CV, found by the render A/B)."""
    entry = {"taxonomy_code": "Q4", "text": "2016-23\tLibrary Representative",
             "extracted_fields": {"service_type": "Library Representative",
                                  "start_date": "2016", "end_date": "2023"}}
    cells = _all_cells(_render(tmp_path, [_OWNER_ENTRY, entry]))

    assert any("Library Representative" in c for c in cells)
    assert not any(c.startswith(";") for c in cells)


def test_generate_strips_the_reviewer_prefix_before_joining_tab_parts(tmp_path):
    entry = {"taxonomy_code": "Q4D", "text": "Reviewer\tJournal of Examples", "extracted_fields": {}}
    cells = _all_cells(_render(tmp_path, [_OWNER_ENTRY, entry]))

    assert "Journal of Examples" in cells
    assert not any(c.startswith(";") for c in cells)

