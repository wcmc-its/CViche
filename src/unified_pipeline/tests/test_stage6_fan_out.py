"""#983: a stage-4 list of sibling records under a key the schema does not
define is expanded to one entry per record, so each record renders as its own
row instead of the whole entry falling back to raw, tab-joined text.

Three layers, because the pure helper alone would not catch a call site that
stopped calling it or passed it the wrong schema lookup:

* `fan_out_multi_record_entries` on plain dicts (the rules);
* `WCMTemplateGenerator._group_entries_by_code` (the call site);
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

def test_each_record_becomes_its_own_entry_with_its_fields_and_paragraph():
    out = _fan_out([_honors_entry(_AWARDS_TEXT, _AWARDS)])

    assert [e["extracted_fields"]["award_name"] for e in out] == [
        "Alpha Honor Society", "Beta Honor Society", "Gamma Honor Society"]
    assert all("awards" not in e["extracted_fields"] for e in out)
    # each child carries its own paragraph of the source, verbatim and tab-free
    assert out[1]["text"] == "Beta Honor Society, Example University"
    assert out[0]["text"] == "1986    Alpha Honor Society, Example University"
    assert out[1]["fanned_out_from"] == {"key": "awards", "index": 1, "of": 3}
    assert all(e["taxonomy_code"] == "H" for e in out)


def test_shared_scalars_are_inherited_and_an_empty_value_does_not_erase_them():
    entry = {"taxonomy_code": "B1",
             "text": "Master of Science 2015 Example University\tDoctor of Philosophy 2018 Example University",
             "extracted_fields": {
                 "institution": "Example University",
                 "degrees": [{"degree": "Master of Science", "year": "2015", "institution": None},
                             {"degree": "Doctor of Philosophy", "year": "2018"}]}}
    out = _fan_out([entry])

    assert [(e["extracted_fields"]["degree"], e["extracted_fields"]["institution"])
            for e in out] == [("Master of Science", "Example University"),
                              ("Doctor of Philosophy", "Example University")]


def test_a_leading_paragraph_is_the_parents_own_entry_and_children_do_not_inherit_it():
    # stage 4 put the first role in the scalars and only the REST in the list:
    # one paragraph more than records, the first one the parent's own
    entry = {"taxonomy_code": "O",
             "text": "2012- Co-Leader, Example Program\t2015-2022 Leader, Sample Program\t2022- Deputy Director",
             "extracted_fields": {
                 "leadership_role": "Co-Leader, Example Program", "institution": "Example Center",
                 "start_date": "2012", "end_date": None,
                 "additional_roles": [
                     {"leadership_role": "Leader, Sample Program", "start_date": "2015", "end_date": "2022"},
                     {"leadership_role": "Deputy Director", "start_date": "2022", "end_date": "present"}]}}
    out = _fan_out([entry])

    assert [e["extracted_fields"].get("leadership_role") for e in out] == [
        "Co-Leader, Example Program", "Leader, Sample Program", "Deputy Director"]
    assert out[0]["text"] == "2012- Co-Leader, Example Program"    # its own paragraph, not the whole text
    assert "additional_roles" not in out[0]["extracted_fields"]
    assert out[1]["text"] == "2015-2022 Leader, Sample Program"
    assert "institution" not in out[1]["extracted_fields"]   # not the parent's context


def test_every_paragraph_of_the_text_goes_to_exactly_one_entry():
    """The property a fan-out must keep: nothing dropped, nothing repeated."""
    entry = {"taxonomy_code": "O",
             "text": "2012- Co-Leader, Example Program\t2015-2022 Leader, Sample Program\t2022- Deputy Director",
             "extracted_fields": {
                 "leadership_role": "Co-Leader, Example Program", "start_date": "2012",
                 "additional_roles": [
                     {"leadership_role": "Leader, Sample Program", "start_date": "2015"},
                     {"leadership_role": "Deputy Director", "start_date": "2022"}]}}
    out = _fan_out([entry])

    assert "\t".join(e["text"] for e in out) == entry["text"]


def test_text_that_does_not_split_into_one_paragraph_per_record_is_left_whole():
    # 2 records, 4 paragraphs: which paragraph belongs to which record is a guess
    entry = _honors_entry("1988 Alpha Honor Society\tExample University\tBeta Honor Society\tSecond Note",
                          _AWARDS[:2])
    assert _fan_out([entry]) == [entry]


def test_paragraphs_in_the_wrong_order_are_left_whole():
    # the right count, but each record's words are in the OTHER record's paragraph
    text = "Beta Honor Society, Example University\tAlpha Honor Society, Example University"
    entry = _honors_entry(text, _AWARDS[:2])
    assert _fan_out([entry]) == [entry]


def test_a_record_with_a_value_the_schema_cannot_show_is_left_whole():
    # `chapter` is not an S3 field: the chapter titles survive only in the raw text
    entry = {"taxonomy_code": "S3",
             "text": "Example Handbook of Testing (1995)\tSmith, J. Chapter One\tJones, K. Chapter Two",
             "extracted_fields": {"title": "Example Handbook of Testing", "chapters_authored": [
                 {"authors": "Smith, J.", "chapter": "Chapter One"},
                 {"authors": "Jones, K.", "chapter": "Chapter Two"}]}}
    assert _fan_out([entry]) == [entry]


def test_records_that_hold_only_a_date_are_parts_not_siblings():
    entry = {"taxonomy_code": "K4", "text": "2020- Example Program\t04/22/2020\t04/29/2020",
             "extracted_fields": {"activity_title": "Example Program", "sessions": [
                 {"date": "2020-04-22"}, {"date": "2020-04-29"}]}}
    assert _fan_out([entry]) == [entry]


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


def test_two_lists_in_one_entry_are_left_whole():
    # one text cannot be paired with two lists
    entry = _honors_entry("Alpha Honor Society\tBeta Honor Society", _AWARDS[:2],
                          fellowships=[{"award_name": "Alpha Honor Society"},
                                       {"award_name": "Beta Honor Society"}])
    assert _fan_out([entry]) == [entry]


def test_a_code_with_no_schema_of_its_own_is_left_alone():
    entry = _honors_entry(_AWARDS_TEXT, _AWARDS)
    assert fan_out_multi_record_entries([entry], lambda code: ()) == [entry]


def test_a_grants_nested_sub_awards_are_not_split_into_grants():
    entry = {"taxonomy_code": "M2A", "text": "Grant Title\tSub Alpha 100\tSub Beta 200",
             "extracted_fields": {"title": "Grant Title", "sub_awards": [
                 {"title": "Sub Alpha", "total_funding": "100"},
                 {"title": "Sub Beta", "total_funding": "200"}]}}
    assert _fan_out([entry]) == [entry]


# --- the call site: _group_entries_by_code -----------------------------------

def test_the_call_site_fans_out_a_known_code_and_skips_a_code_without_a_schema():
    gen = WCMTemplateGenerator(verbose=False)
    fanned = gen._group_entries_by_code([_honors_entry(_AWARDS_TEXT, _AWARDS)])
    assert len(fanned["H"]) == 3

    # a code the schema registry does not know would get the DEFAULT schema
    # (text/date/description) if the lookup asked stage 4 for it naively
    unknown = {"taxonomy_code": "ZZ9", "text": "Alpha thing\tBeta thing",
               "extracted_fields": {"things": [{"description": "Alpha thing"},
                                               {"description": "Beta thing"}]}}
    kept = gen._group_entries_by_code([unknown])
    assert [e.get("fanned_out_from") for e in kept["ZZ9"]] == [None]


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
        {"committee_name": "Alpha Committee", "role": "Chair",
         "start_date": "2016", "end_date": "present"},
        {"committee_name": "Beta Committee", "role": "Member",
         "start_date": "2016", "end_date": "present"}]}}
    doc = _render(tmp_path, [_OWNER_ENTRY, entry])
    rows = _rows(doc)

    assert ["Alpha Committee", "Chair", "2016-Present"] in rows
    assert ["Beta Committee", "Member", "2016-Present"] in rows
    assert not [c for c in _all_cells(doc) if "Committee" in c and "\t" in c]


def test_generate_repeats_no_row_for_a_leading_paragraph_entry(tmp_path):
    """A leading paragraph gives the parent its own row; each record's row
    appears once, and no row is the whole entry's text again."""
    text = ("2012- Co-Leader, Example Program, Example Center\t"
            "2015-2022 Leader, Sample Program, Example Center\t"
            "2022- Deputy Director, Example Center")
    entry = {"taxonomy_code": "O", "text": text, "extracted_fields": {
        "leadership_role": "Co-Leader, Example Program", "institution": "Example Center",
        "start_date": "2012", "end_date": None,
        "additional_roles": [
            {"leadership_role": "Leader, Sample Program", "institution": "Example Center",
             "start_date": "2015", "end_date": "2022"},
            {"leadership_role": "Deputy Director", "institution": "Example Center",
             "start_date": "2022", "end_date": "present"}]}}
    cells = _all_cells(_render(tmp_path, [_OWNER_ENTRY, entry]))

    for role in ("Co-Leader, Example Program", "Leader, Sample Program", "Deputy Director"):
        assert len([c for c in cells if role in c]) == 1, role
    assert not [c for c in cells if "\t" in c and "Example Center" in c]
