"""Behaviour coverage for stage_5c_teaching_formatter.py and
stage_5d_citation_formatter.py (issue #704: raise coverage on live-path
pipeline code).

Both modules share one shape: build_raw_content -> call_llm_formatter (which
calls unified_pipeline.llm_client.call_llm) -> parse_llm_output ->
run_stage_5c / run_stage_5d writes a JSON artifact. Every call_llm is stubbed
at the module attribute with monkeypatch; no network call is ever made.

Covers:
- build_raw_content: entry-id assignment, which fields feed the raw text,
  the raw-text fallback when no structured fields are present, empty input.
- parse_llm_output: well-formed output, an id the LLM invented, an id it
  dropped, and garbage/unparseable output.
- call_llm_formatter: the success usage tuple and the exception arm.
- run_stage_5c / run_stage_5d end to end on a tmp_path input JSON: written
  output carries formatted text on the right entries, untouched entries are
  preserved verbatim, and the skip arms (no K entries / no non-enriched
  publications) copy the input straight through.

    python3 -m pytest src/unified_pipeline/tests/test_stage5c_5d_formatters_behaviour.py -p no:cacheprovider

Self-contained: no DB, no network, no real LLM call, no corpus/PII data.
"""

import importlib
import inspect
import io
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline import stage_5c_teaching_formatter as s5c
from unified_pipeline import stage_5d_citation_formatter as s5d


def _llm_result(content: str, model: str = "sentinel-model", cost: float = 0.002) -> dict:
    return {
        "content": content,
        "prompt_tokens": 11,
        "completion_tokens": 7,
        "total_tokens": 18,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "cost": cost,
        "model": model,
    }


# ---------------------------------------------------------------------------
# stage_5c: build_raw_content
# ---------------------------------------------------------------------------

def test_5c_build_raw_content_empty_input_returns_empty_string_and_map():
    raw, id_map = s5c.build_raw_content({})
    assert raw == ""
    assert id_map == {}


def test_5c_build_raw_content_assigns_sequential_ids_in_taxonomy_order():
    # entries_by_k_code is keyed K3 first in the dict, but build_raw_content
    # walks TEACHING_CODES order (K1..K5), not insertion order.
    entries_by_k_code = {
        "K3": [{"text": "admin entry", "extracted_fields": {}}],
        "K1": [{"text": "didactic entry", "extracted_fields": {}}],
    }
    raw, id_map = s5c.build_raw_content(entries_by_k_code)
    # K1 must be numbered first (EC-0001) even though K3 came first in the dict.
    assert list(id_map.keys()) == ["EC-0001", "EC-0002"]
    assert id_map["EC-0001"]["text"] == "didactic entry"
    assert id_map["EC-0002"]["text"] == "admin entry"
    assert raw.index("didactic entry") < raw.index("admin entry")


def test_5c_build_raw_content_joins_structured_fields_and_appends_original():
    entry = {
        "text": "raw free text that differs",
        "extracted_fields": {
            "start_date": "2019",
            "end_date": "2020",
            "course_code": "MED101",
            "course_title": "Intro to Medicine",
            "role": "Lecturer",
            "institution": "Weill Cornell",
            "audience": "1st-year SOM",
        },
    }
    raw, id_map = s5c.build_raw_content({"K1": [entry]})
    assert "[EC-0001]" in raw
    # Structured fields are pipe-joined in a fixed order.
    assert "2019-2020 | MED101: Intro to Medicine | Lecturer | Weill Cornell | 1st-year SOM" in raw
    # Because the built text differs from entry['text'], the original is appended.
    assert "Original: raw free text that differs" in raw


def test_5c_build_raw_content_falls_back_to_raw_text_when_no_structured_fields():
    entry = {"text": "only free text, no fields", "extracted_fields": {}}
    raw, id_map = s5c.build_raw_content({"K2": [entry]})
    assert "[EC-0001] only free text, no fields" in raw
    # No fields were extracted, so there is no "Original:" echo line.
    assert "Original:" not in raw


def test_5c_build_raw_content_course_title_only_and_course_code_only():
    # Exercises the two elif branches: course_title without course_code, and
    # course_code without course_title (as opposed to both-present, tested
    # above).
    title_only = {"text": "t", "extracted_fields": {"course_title": "Anatomy Lab"}}
    code_only = {"text": "t", "extracted_fields": {"course_code": "ANAT200"}}
    raw, _id_map = s5c.build_raw_content({"K1": [title_only, code_only]})
    assert "[EC-0001] Anatomy Lab" in raw
    assert "[EC-0002] ANAT200" in raw


def test_5c_build_raw_content_skips_k_codes_with_no_entries():
    raw, id_map = s5c.build_raw_content({"K1": [], "K4": [{"text": "ce entry"}]})
    assert "Didactic teaching" not in raw
    assert "Continuing education" in raw
    assert list(id_map.keys()) == ["EC-0001"]


# ---------------------------------------------------------------------------
# stage_5c: parse_llm_output
# ---------------------------------------------------------------------------

def test_5c_parse_llm_output_well_formed_extracts_each_entry():
    id_map = {"EC-0001": {}, "EC-0002": {}}
    llm_output = (
        "# Educational Contributions\n\n"
        "## Didactic teaching\n\n"
        "- [EC-0001] **2019-2020** - Intro to Medicine, Lecturer (Weill Cornell)\n"
        "- [EC-0002] **2021** - Advanced Topics, Co-Director (GSPH)\n"
    )
    parsed = s5c.parse_llm_output(llm_output, id_map)
    assert parsed["EC-0001"] == "**2019-2020** - Intro to Medicine, Lecturer (Weill Cornell)"
    assert parsed["EC-0002"] == "**2021** - Advanced Topics, Co-Director (GSPH)"


def test_5c_parse_llm_output_does_not_filter_ids_the_llm_invented():
    # #704 note: unlike stage 5d's parse_llm_output, stage 5c's never checks
    # the extracted id against id_to_entry -- it takes whatever the regex
    # matches. This is current (possibly buggy) behaviour, pinned here: a
    # made-up id from the LLM survives into the returned dict.
    id_map = {"EC-0001": {}}
    llm_output = "- [EC-0001] real entry\n- [EC-9999] entry id the LLM invented\n"
    parsed = s5c.parse_llm_output(llm_output, id_map)
    assert "EC-0001" in parsed
    assert "EC-9999" in parsed, "current (unfiltered) behaviour: invented ids pass through"


def test_5c_parse_llm_output_missing_id_is_simply_absent():
    # The LLM dropped EC-0002 entirely from its response.
    id_map = {"EC-0001": {}, "EC-0002": {}}
    llm_output = "- [EC-0001] only this one came back\n"
    parsed = s5c.parse_llm_output(llm_output, id_map)
    assert "EC-0001" in parsed
    assert "EC-0002" not in parsed


def test_5c_parse_llm_output_garbage_text_yields_empty_dict():
    parsed = s5c.parse_llm_output("this has no bracketed ids at all, just prose.", {"EC-0001": {}})
    assert parsed == {}


# ---------------------------------------------------------------------------
# stage_5c: call_llm_formatter
# ---------------------------------------------------------------------------

def test_5c_call_llm_formatter_returns_text_and_usage_tuple(monkeypatch):
    monkeypatch.setattr(s5c, "call_llm", lambda **kw: _llm_result("- [EC-0001] formatted"))
    text, usage = s5c.call_llm_formatter("raw content here", verbose=False)
    assert text == "- [EC-0001] formatted"
    assert usage["model"] == "sentinel-model"
    assert usage["prompt_tokens"] == 11
    assert usage["completion_tokens"] == 7
    assert usage["total_tokens"] == 18
    assert usage["cost"] == 0.002


def test_5c_call_llm_formatter_exception_arm_returns_none_none(monkeypatch):
    def _boom(**kw):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(s5c, "call_llm", _boom)
    text, usage = s5c.call_llm_formatter("raw content", verbose=False)
    assert text is None
    assert usage is None


def test_5c_call_llm_formatter_llm_outage_propagates(monkeypatch):
    """A provider outage past the budget fails the run (#810) instead of
    taking the (None, None) exception arm above."""
    from unified_pipeline.llm.retry import LLMOutageError

    def outage(**kw):
        raise LLMOutageError("provider down", seconds_waited=1800.0)

    monkeypatch.setattr(s5c, "call_llm", outage)
    with pytest.raises(LLMOutageError):
        s5c.call_llm_formatter("raw content", verbose=False)


# ---------------------------------------------------------------------------
# stage_5c: run_stage_5c end to end
# ---------------------------------------------------------------------------

def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return str(path)


def test_run_stage_5c_formats_k_entries_and_preserves_others(tmp_path, monkeypatch):
    input_data = {
        "document_uid": "ABC123",
        "entries": [
            {
                "taxonomy_code": "K1",
                "text": "didactic raw",
                "extracted_fields": {"role": "Lecturer"},
            },
            {
                "taxonomy_code": "S1",
                "text": "a publication, not teaching",
                "extracted_fields": {"authors": "Smith JA"},
            },
        ],
    }
    input_path = _write_json(tmp_path / "in.json", input_data)
    output_path = str(tmp_path / "out.json")

    def _fake_call_llm(**kw):
        # The K1 entry is the only teaching entry, so build_raw_content
        # numbers it EC-0001.
        return _llm_result("- [EC-0001] **Lecturer** - Intro to Medicine (Weill Cornell)")

    monkeypatch.setattr(s5c, "call_llm", _fake_call_llm)
    result_path = s5c.run_stage_5c(input_path, output_path=output_path, verbose=True)
    assert result_path == output_path

    with open(output_path, encoding="utf-8") as f:
        out = json.load(f)

    k1_entry = out["entries"][0]
    assert k1_entry["extracted_fields"]["formatted_text"] == \
        "**Lecturer** - Intro to Medicine (Weill Cornell)"
    assert k1_entry["extracted_fields"]["formatting_source"] == "stage_5c_llm"

    # The S1 (non-teaching) entry must be byte-for-byte untouched.
    s1_entry = out["entries"][1]
    assert s1_entry["extracted_fields"] == {"authors": "Smith JA"}
    assert "formatted_text" not in s1_entry["extracted_fields"]

    meta = out["stage_5c"]
    assert meta["k_entries_processed"] == 1
    assert meta["entries_formatted"] == 1
    assert meta["model"] == "sentinel-model"


def test_run_stage_5c_no_k_entries_copies_input_through_untouched(tmp_path, monkeypatch):
    input_data = {
        "document_uid": "NOTEACH",
        "entries": [{"taxonomy_code": "S1", "text": "just a pub", "extracted_fields": {}}],
    }
    input_path = _write_json(tmp_path / "in.json", input_data)
    output_path = str(tmp_path / "out.json")

    calls = []
    monkeypatch.setattr(s5c, "call_llm", lambda **kw: calls.append(1) or _llm_result("x"))

    s5c.run_stage_5c(input_path, output_path=output_path, verbose=True)

    with open(output_path, encoding="utf-8") as f:
        out = json.load(f)

    # Skip path: the LLM is never invoked and the data is copied verbatim,
    # with no stage_5c metadata block added.
    assert calls == []
    assert "stage_5c" not in out
    assert out == input_data


def test_run_stage_5c_llm_failure_keeps_original_text_but_still_writes_metadata(tmp_path, monkeypatch):
    input_data = {
        "document_uid": "FAILCASE",
        "entries": [{"taxonomy_code": "K2", "text": "clinical raw", "extracted_fields": {}}],
    }
    input_path = _write_json(tmp_path / "in.json", input_data)
    output_path = str(tmp_path / "out.json")

    def _boom(**kw):
        raise RuntimeError("timeout")

    monkeypatch.setattr(s5c, "call_llm", _boom)
    s5c.run_stage_5c(input_path, output_path=output_path, verbose=True)

    with open(output_path, encoding="utf-8") as f:
        out = json.load(f)

    entry = out["entries"][0]
    assert "formatted_text" not in entry["extracted_fields"]
    meta = out["stage_5c"]
    assert meta["entries_formatted"] == 0
    assert meta["k_entries_processed"] == 1
    assert meta["model"] is None


def test_run_stage_5c_quiet_mode_still_formats_correctly(tmp_path, monkeypatch):
    # Same shape as the main end-to-end test but verbose=False, to exercise
    # the "if verbose:" false branches through run_stage_5c.
    input_data = {
        "document_uid": "QUIET1",
        "entries": [{"taxonomy_code": "K3", "text": "admin raw", "extracted_fields": {}}],
    }
    input_path = _write_json(tmp_path / "in.json", input_data)
    output_path = str(tmp_path / "out.json")

    monkeypatch.setattr(
        s5c, "call_llm",
        lambda **kw: _llm_result("- [EC-0001] **Director** - Course Admin (GSPH)"),
    )
    s5c.run_stage_5c(input_path, output_path=output_path, verbose=False)

    with open(output_path, encoding="utf-8") as f:
        out = json.load(f)
    assert out["entries"][0]["extracted_fields"]["formatted_text"] == \
        "**Director** - Course Admin (GSPH)"


# ---------------------------------------------------------------------------
# stage_5c: one id per stage-4 record, every id tag stripped, post-check (E20)
# ---------------------------------------------------------------------------

def _two_record_entry() -> dict:
    """A K2 entry stage 4 kept as two records of one role (invented values)."""
    first = {"teaching_role": "Simulation Lab Leader", "start_date": "1981", "end_date": "1990"}
    last = {"teaching_role": "Simulation Lab Leader", "start_date": "1995", "end_date": "2001"}
    return {
        "taxonomy_code": "K2",
        "text": "Simulation Lab Leader 1981-1990, 1995-2001",
        "extracted_fields": {**last, "stage4_records": [first, last]},
    }


def test_5c_build_raw_content_gives_each_stage4_record_its_own_id():
    # One id per entry let the model's second line for a fused entry take the
    # NEXT entry's id, shifting every later id (EQGGRB-06).
    later = {"text": "Grand rounds 2003", "extracted_fields": {"start_date": "2003"}}
    raw, id_map = s5c.build_raw_content({"K2": [_two_record_entry(), later]})
    assert list(id_map) == ["EC-0001", "EC-0002", "EC-0003"]
    assert id_map["EC-0001"] is id_map["EC-0002"]
    assert id_map["EC-0003"] is later
    assert "[EC-0001] 1981-1990" in raw
    assert "[EC-0002] 1995-2001" in raw
    # The entry's own text is sent once, after its last record.
    assert raw.count("Original: Simulation Lab Leader") == 1
    assert raw.index("[EC-0002]") < raw.index("Original: Simulation Lab Leader")


def test_5c_entry_records_is_the_fields_for_a_single_record_entry():
    entry = {"text": "t", "extracted_fields": {"role": "Tutor", "stage4_records": [{"role": "Tutor"}]}}
    assert s5c.entry_records(entry) == [entry["extracted_fields"]]


def _two_talk_entry() -> dict:
    """A K4 entry stage 4 kept as two dated talks (invented values)."""
    first = {"activity_title": "Lantern Making", "date": "2011"}
    last = {"activity_title": "Paper Boats", "date": "2013"}
    return {
        "taxonomy_code": "K4",
        "text": "Lantern Making 2011; Paper Boats 2013",
        "extracted_fields": {**last, "stage4_records": [first, last]},
    }


def test_5c_build_raw_content_sends_each_k4_record_its_own_title_and_date():
    # MRJDWE 89: a K4 record (activity_title/date) yielded no K1 fields, so
    # both ids carried the entry's whole text and the entry rendered twice.
    raw, id_map = s5c.build_raw_content({"K4": [_two_talk_entry()]})
    assert list(id_map) == ["EC-0001", "EC-0002"]
    assert "[EC-0001] 2011 | Lantern Making\n" in raw
    assert "[EC-0002] 2013 | Paper Boats\n  Original: Lantern Making 2011; Paper Boats 2013" in raw


def test_5c_build_raw_content_sends_k2_role_and_k3_program_per_record():
    # EQADVR-04: a K3 record used to go out as its bare date range.
    first = {"program_name": "Pond Study Track", "role": "Director", "start_date": "1975", "end_date": "1980"}
    last = {"program_name": "Pond Study Track", "role": "Advisor", "start_date": "1981", "end_date": "1990"}
    k3 = {"text": "Pond Study Track: Director 1975-1980, Advisor 1981-1990",
          "extracted_fields": {**last, "stage4_records": [first, last]}}
    raw, _ = s5c.build_raw_content({"K3": [k3], "K2": [_two_record_entry()]})
    assert "[EC-0001] 1981-1990 | Simulation Lab Leader\n" in raw
    assert "[EC-0003] 1975-1980 | Pond Study Track | Director\n" in raw
    assert "[EC-0004] 1981-1990 | Pond Study Track | Advisor\n" in raw


def test_5c_build_raw_content_single_record_line_is_unchanged():
    # A single-record entry sends the K1 fields and its Original: line, as
    # before E20: its Original: line already holds the title and date.
    entry = {"text": "Kite Day 2006 lecture",
             "extracted_fields": {"activity_title": "Kite Day", "date": "2006", "role": "Lecturer"}}
    raw, _ = s5c.build_raw_content({"K4": [entry]})
    assert "[EC-0001] Lecturer\n  Original: Kite Day 2006 lecture" in raw


def test_5c_entry_records_keeps_one_id_when_a_record_line_is_empty_or_repeated():
    # Each such id would carry the whole entry text, or a copy of another
    # id's line, and stage 6 would render the entry twice.
    bare = {"text": "Two talks", "extracted_fields": {
        "stage4_records": [{"description": "first"}, {"activity_title": "Paper Boats"}]}}
    twins = {"text": "Paper Boats twice", "extracted_fields": {
        "stage4_records": [{"activity_title": "Paper Boats"}, {"activity_title": "Paper Boats"}]}}
    for entry in (bare, twins):
        assert s5c.entry_records(entry) == [entry["extracted_fields"]]
        raw, id_map = s5c.build_raw_content({"K4": [entry]})
        assert list(id_map) == ["EC-0001"]
        assert raw.count(entry["text"]) == 1


def test_run_stage_5c_formats_both_talks_of_a_k4_entry_once_each(tmp_path, monkeypatch):
    reply = ('- [EC-0001] **2011** - "Lantern Making"\n'
             '- [EC-0002] **2013** - "Paper Boats"\n')
    out = _run_5c(tmp_path, monkeypatch, [_two_talk_entry()], reply)
    assert out["entries"][0]["extracted_fields"]["formatted_text"] == \
        '**2011** - "Lantern Making"\n**2013** - "Paper Boats"'


def test_5c_parse_llm_output_strips_every_repeated_id_tag():
    parsed = s5c.parse_llm_output("- [EC-0021] [EC-0021] Board prep course (1990, 1994)\n", {})
    assert parsed["EC-0021"] == "Board prep course (1990, 1994)"


def test_5c_postcheck_accepts_a_faithful_line():
    record = {"activity_title": "Pond Ecology", "role": "Lecturer", "date": "2012-05"}
    line = '**5/2012** - Lecturer, "Pond Ecology" (residents)'
    assert s5c.postcheck_line(line, record, '5/2012 "Pond Ecology" lecture to residents') is None


def test_5c_postcheck_rejects_an_original_echo_line():
    line = 'Workshop - "Rope Knots"\n  - Original: Doe, J. (2015). Rope knots. Workshop.'
    assert s5c.postcheck_line(line, {"date": "2015"}, "Doe, J. (2015). Rope knots. Workshop.") \
        == "original_echo"


def test_5c_postcheck_rejects_a_line_that_drops_a_record_year():
    # XWNZWW-06: stage 4 held the year, the formatted line did not.
    record = {"activity_title": "Supper Club", "role": "Participant", "date": "2004"}
    assert s5c.postcheck_line('Participant, "Supper Club"', record, "Participant in Supper Club 2004") \
        == "year_missing:2004"


def test_5c_postcheck_a_year_range_covers_the_years_inside_it():
    record = {"activity_title": "Science Camp", "date": "1986, 1987, 1988"}
    line = '**1986-1988** - "Science Camp" (high school students)'
    assert s5c.postcheck_line(line, record, '"Science Camp", 1986, 1987 and 1988') is None


def test_5c_postcheck_ignores_a_record_year_the_source_does_not_hold():
    # Stage 4 misread the year (1900); the line carries the source's year.
    record = {"activity_title": "Fern Taxonomy", "role": "Lecturer", "date": "1900-03-02"}
    line = '**2016-03-02** - Lecturer, "Fern Taxonomy"'
    assert s5c.postcheck_line(line, record, '"Fern Taxonomy." Lecturer, March 2, 2016.') is None


def test_5c_postcheck_rejects_a_year_neither_record_nor_source_holds():
    # EQGGRB-06: the line meant for another entry carried its range.
    record = {"activity_title": "Valve Symposium", "role": "Course Director", "date": "2018"}
    line = "**2016-2018** - **Course Director**, Valve Symposium"
    assert s5c.postcheck_line(line, record, "2018 Valve Symposium (Course Director)") \
        == "year_not_in_source:2016"


def test_5c_postcheck_rejects_dates_separated_from_their_titles():
    # DPEHSZ-02: a date list in front of a title list loses the pairing.
    record = {"activities": [
        {"activity_title": "Lichen Basics", "date": "2007-03"},
        {"activity_title": "Moss Survey", "date": "2007-04"},
    ]}
    source = '3/2007 "Lichen Basics" (to residents) 4/2007 "Moss Survey" (to residents)'
    merged = '**3/2007, 4/2007** - "Lichen Basics"; "Moss Survey" (residents)'
    paired = '**3/2007** "Lichen Basics"; **4/2007** "Moss Survey" (residents)'
    assert s5c.postcheck_line(merged, record, source) == "date_not_beside_title"
    assert s5c.postcheck_line(paired, record, source) is None


def test_5c_postcheck_holds_the_line_to_every_activity_year():
    record = {"activities": [
        {"activity_title": "Lichen Basics", "date": "2007"},
        {"activity_title": "Moss Survey", "date": "2008"},
    ]}
    line = '**2007** - "Lichen Basics"; "Moss Survey"'
    assert s5c.postcheck_line(line, record, "Lichen Basics 2007, Moss Survey 2008") == "year_missing:2008"


def test_5c_postcheck_a_single_titled_record_is_its_own_activity():
    record = {"activity_title": "Lichen Basics", "date": "8/2008"}
    line = '**8/2008, 9/2008** - "Lichen Basics"'
    assert s5c.postcheck_line(line, record, "8/2008 Lichen Basics; 9/2008 repeat") == "date_not_beside_title"


def test_5c_postcheck_does_not_judge_a_title_the_model_reworded():
    record = {"activity_title": "Lichen Basics", "date": "8/2008"}
    line = "Introduction to lichens, 9/2008 and 8/2008"
    assert s5c.postcheck_line(line, record, "8/2008 Lichen Basics, repeated 9/2008") is None


def test_5c_postcheck_finds_a_title_through_markdown_and_curly_quotes():
    source = "8/2008 Kid's Ponds; 9/2008 Moss Survey"
    record = {"activities": [
        {"activity_title": "Kid's Ponds", "date": "8/2008"},
        {"activity_title": "Moss Survey", "date": "9/2008"},
    ]}
    italic = {"activities": [
        {"activity_title": "Moss Survey", "date": "8/2008"},
        {"activity_title": "Pond Ecology", "date": "9/2008"},
    ]}
    assert s5c.postcheck_line("**8/2008, 9/2008** - Kid\u2019s Ponds; Moss Survey", record, source) \
        == "date_not_beside_title"
    assert s5c.postcheck_line("**8/2008, 9/2008** - *Moss* Survey; Pond Ecology", italic,
                              "8/2008 Moss Survey; 9/2008 Pond Ecology") == "date_not_beside_title"


def test_5c_postcheck_reads_month_names_in_activity_dates():
    record = {"activities": [
        {"activity_title": "Lichen Basics", "date": "Aug 2008"},
        {"activity_title": "Moss Survey", "date": "Sep 2008"},
    ]}
    line = "**Aug 2008, Sep 2008** - Lichen Basics; Moss Survey"
    assert s5c.postcheck_line(line, record, "Aug 2008 Lichen Basics; Sep 2008 Moss Survey") \
        == "date_not_beside_title"


def test_5c_postcheck_rejects_a_role_the_record_does_not_state():
    # OIYKZE-03 / DPEHSZ-02: 'Attendee' and 'Presenter' with no stage-4 role.
    record = {"activity_title": "Rope Knots", "role": None, "date": "2015"}
    assert s5c.postcheck_line('**2015** - Attendee, "Rope Knots"', record, '2015 "Rope Knots" workshop') \
        == "role_invented:attendee"
    assert s5c.postcheck_line('**2015** - Presenter, "Rope Knots"', record, '2015 "Rope Knots" workshop') \
        == "role_invented:presenter"


def test_5c_postcheck_accepts_a_role_the_source_states():
    record = {"activity_title": "Tide Pools", "role": None, "date": "2005-10"}
    source = '"Tide Pools", copresented with a colleague, Oct. 2005'
    assert s5c.postcheck_line('**2005-10** - Co-presenter, "Tide Pools"', record, source) is None


def test_5c_postcheck_present_in_a_date_range_is_not_a_presenter():
    record = {"activity_title": "Harbor Seminar", "role": None, "start_date": "2009", "end_date": "present"}
    line = "**2009-present** - Presenter, Harbor Seminar"
    assert s5c.postcheck_line(line, record, "Harbor Seminar 2009-present") == "role_invented:presenter"


# ---------------------------------------------------------------------------
# stage_5c prompt (#1345): the prompt states the rules the post-check enforces
# ---------------------------------------------------------------------------

_TWO_TALKS = {"activities": [
    {"activity_title": "Topic A", "date": "2012-06"},
    {"activity_title": "Topic B", "date": "2012-08"},
]}
_TWO_TALKS_SOURCE = '6/2012 "Topic A", School of Medicine (to residents) 8/2012 "Topic B", School of Medicine'


def _prompt_example_line(example: str) -> str:
    """The formatted text the parser would take from one of the prompt's examples."""
    assert example in s5c.EDUCATIONAL_CONTRIBUTIONS_PROMPT
    (line,) = s5c.parse_llm_output(example + "\n", {}).values()
    return line


def test_5c_prompt_dated_items_example_passes_the_postcheck():
    # DPEHSZ 269-297: the prompt's own example of a dated list must be a line
    # the post-check keeps, so following it formats the entry.
    line = _prompt_example_line(s5c.DATED_ITEMS_EXAMPLE)
    assert s5c.postcheck_line(line, _TWO_TALKS, _TWO_TALKS_SOURCE) is None


def test_5c_prompt_merged_dates_example_is_the_shape_the_postcheck_rejects():
    line = _prompt_example_line(s5c.MERGED_DATES_EXAMPLE)
    assert s5c.postcheck_line(line, _TWO_TALKS, _TWO_TALKS_SOURCE) == "date_not_beside_title"


def test_5c_prompt_forbids_by_name_every_role_the_postcheck_rejects():
    # DPEHSZ 177-200 / OIYKZE 274: 'Presenter' and 'Attendee' with no stated role.
    assert {r.lower() for r in s5c.DEFAULT_ROLES_FORBIDDEN} == set(s5c._ROLE_EVIDENCE)
    assert "Do not write " + ", ".join(s5c.DEFAULT_ROLES_FORBIDDEN) in s5c.EDUCATIONAL_CONTRIBUTIONS_PROMPT


def test_5c_prompt_names_a_default_role_only_to_forbid_it():
    # Template D handed the model "Role: Attendee/Participant", and the field
    # list offered both as role examples.
    naming = [line for line in s5c.EDUCATIONAL_CONTRIBUTIONS_PROMPT.splitlines()
              if s5c._ROLE_WORD.search(line)]
    assert len(naming) == 1 and "Never supply a default role" in naming[0]


def test_5c_prompt_says_the_original_line_is_context_not_output():
    # OIYKZE 274: the 'Original:' context line was echoed as a sub-bullet.
    prompt = s5c.EDUCATIONAL_CONTRIBUTIONS_PROMPT.format(raw_content="[EC-0001] x")
    assert "An `Original:` line is the source text of the entry above it" in prompt
    assert "Never copy it into your output" in prompt


def _run_5c(tmp_path, monkeypatch, entries, reply):
    input_path = _write_json(tmp_path / "in.json", {"document_uid": "E20X", "entries": entries})
    output_path = str(tmp_path / "out.json")
    monkeypatch.setattr(s5c, "call_llm", lambda **kw: _llm_result(reply))
    s5c.run_stage_5c(input_path, output_path=output_path, verbose=False)
    with open(output_path, encoding="utf-8") as f:
        return json.load(f)


def test_run_stage_5c_formats_every_record_of_a_multi_record_entry(tmp_path, monkeypatch):
    # EQADVR-04: the first record's tenure used to be dropped.
    reply = ("- [EC-0001] **1981-1990** - Simulation Lab Leader\n"
             "- [EC-0002] **1995-2001** - Simulation Lab Leader\n")
    out = _run_5c(tmp_path, monkeypatch, [_two_record_entry()], reply)
    fields = out["entries"][0]["extracted_fields"]
    assert fields["formatted_text"] == \
        "**1981-1990** - Simulation Lab Leader\n**1995-2001** - Simulation Lab Leader"
    assert out["stage_5c"]["entries_rejected"] == []


def test_run_stage_5c_multi_record_entry_missing_a_line_is_left_unformatted(tmp_path, monkeypatch):
    out = _run_5c(tmp_path, monkeypatch, [_two_record_entry()],
                  "- [EC-0002] **1995-2001** - Simulation Lab Leader\n")
    assert "formatted_text" not in out["entries"][0]["extracted_fields"]
    # A line the reply did not hold is not a post-check rejection.
    assert out["stage_5c"]["entries_rejected"] == []
    assert out["stage_5c"]["entries_formatted"] == 0


def test_run_stage_5c_rejected_line_falls_back_and_is_recorded(tmp_path, monkeypatch):
    entries = [
        {"taxonomy_code": "K4", "element_idx_start": 7, "text": "Participant in Supper Club 2004",
         "extracted_fields": {"activity_title": "Supper Club", "role": "Participant", "date": "2004"}},
        {"taxonomy_code": "K4", "element_idx_start": 9, "text": "2006 Kite Building Day",
         "extracted_fields": {"activity_title": "Kite Building Day", "date": "2006"}},
    ]
    reply = ('- [EC-0001] Participant, "Supper Club"\n'
             "- [EC-0002] **2006** - Kite Building Day\n")
    out = _run_5c(tmp_path, monkeypatch, entries, reply)
    assert "formatted_text" not in out["entries"][0]["extracted_fields"]
    assert "formatting_source" not in out["entries"][0]["extracted_fields"]
    assert out["entries"][1]["extracted_fields"]["formatted_text"] == "**2006** - Kite Building Day"
    assert out["stage_5c"]["entries_formatted"] == 1
    assert out["stage_5c"]["entries_rejected"] == [
        {"element_idx_start": 7, "taxonomy_code": "K4", "reason": "EC-0001:year_missing:2004"}]


# ---------------------------------------------------------------------------
# stage_5d: build_raw_content
# ---------------------------------------------------------------------------

def test_5d_build_raw_content_empty_input_returns_empty_string_and_map():
    raw, id_map = s5d.build_raw_content([])
    assert raw == ""
    assert id_map == {}


def test_5d_build_raw_content_assigns_sequential_ids_by_position():
    entries = [
        {"taxonomy_code": "S1", "text": "first pub"},
        {"taxonomy_code": "S4", "text": "second pub, a book chapter"},
    ]
    raw, id_map = s5d.build_raw_content(entries)
    assert list(id_map.keys()) == ["CIT-0001", "CIT-0002"]
    assert id_map["CIT-0001"]["text"] == "first pub"
    assert id_map["CIT-0002"]["text"] == "second pub, a book chapter"
    assert "(S1: Peer-reviewed research article (journal article))" in raw
    assert "(S4: Book chapter (contributed chapter in edited volume))" in raw


def test_5d_build_raw_content_defaults_missing_code_to_s1_description():
    entries = [{"text": "no taxonomy_code key at all"}]
    raw, _id_map = s5d.build_raw_content(entries)
    assert "(S1: Peer-reviewed research article (journal article))" in raw


@pytest.mark.parametrize("text, sent", [
    # #1570 SIJYJZ 732 shape: the CV's "10." became "10th Annual Meeting".
    ("10. Annual Meeting of the Society, Quilltown, 2004", "Annual Meeting of the Society, Quilltown, 2004"),
    ("3) Rook A. A title. J Med. 2001.", "Rook A. A title. J Med. 2001."),
    ("(12) Rook A. A title. J Med. 2001.", "Rook A. A title. J Med. 2001."),
    ("  7.\tRook A. A title. J Med. 2001.", "Rook A. A title. J Med. 2001."),
    ("123. Rook A. A title. J Med. 2001.", "Rook A. A title. J Med. 2001."),
    # Not a list number: a year, a DOI, a four-digit count, a number mid-line.
    ("2004. Annual Meeting of the Society", "2004. Annual Meeting of the Society"),
    ("10.1000/xyz123 Rook A. A title.", "10.1000/xyz123 Rook A. A title."),
    ("1234. Rook A. A title.", "1234. Rook A. A title."),
    ("Rook A. 10. Annual Meeting", "Rook A. 10. Annual Meeting"),
    ("10 Annual Meeting of the Society", "10 Annual Meeting of the Society"),
])
def test_5d_build_raw_content_sends_the_line_without_its_list_number(text, sent):
    entry = {"taxonomy_code": "S8", "text": text}
    raw, id_map = s5d.build_raw_content([entry])
    assert raw.split("\n")[1] == sent
    assert id_map["CIT-0001"]["text"] == text  # the entry itself keeps its line


def test_5d_build_raw_content_strips_only_the_first_list_number():
    raw, _ = s5d.build_raw_content([{"taxonomy_code": "S1", "text": "4. 5. Rook A. A title."}])
    assert raw.split("\n")[1] == "5. Rook A. A title."


def test_5d_prompt_asks_for_source_authors_not_one_initial_per_author():
    # #1570: "LastName INITIALS" for every author made 5d write an initial the
    # source does not give (QQGKXR 481, 483) and join a bare initials token to
    # the next surname (FLBFRK 25).
    prompt = s5d.CITATION_FORMATTER_PROMPT
    assert "1. Authors: copy each author as the source writes them." in prompt
    assert '"Smith, J.A., M.B. Jones, Brown" becomes Smith JA, Jones MB, Brown' in prompt
    assert "Never add an initial the source does not give" in prompt
    assert "never split one into two" in prompt
    assert "Example: Smith JA, Jones MB, Brown CK" not in prompt
    # The ordinal rule keeps the source's own ordinals: a first wording ("never
    # 10th Annual Meeting") made 5d drop "17th", "53rd" and "72nd" the source
    # states (paid A/B, 2026-10-08).
    assert "Keep every number and ordinal the source states" in prompt
    assert "10th Annual Meeting" not in prompt
    assert "is the CV's list number, not part of the citation" in prompt
    # The template still formats: no stray brace in the new text.
    assert "[CIT-0001] x" in prompt.format(raw_content="[CIT-0001] x")


# ---------------------------------------------------------------------------
# stage_5d: parse_llm_output
# ---------------------------------------------------------------------------

def test_5d_parse_llm_output_well_formed_direct_json():
    id_map = {"CIT-0001": {}, "CIT-0002": {}}
    llm_output = json.dumps({
        "CIT-0001": {"formatted_citation": "Smith JA. Title one. J Med. 2020."},
        "CIT-0002": {"formatted_citation": "Jones MB. Title two. J Med. 2021."},
    })
    parsed = s5d.parse_llm_output(llm_output, id_map)
    assert parsed["CIT-0001"]["formatted_citation"] == "Smith JA. Title one. J Med. 2020."
    assert parsed["CIT-0002"]["formatted_citation"] == "Jones MB. Title two. J Med. 2021."


def test_5d_parse_llm_output_filters_out_an_id_the_llm_invented():
    # Unlike stage 5c, stage 5d's parse_llm_output DOES check membership in
    # id_to_entry, so a made-up id is dropped rather than passed through.
    id_map = {"CIT-0001": {}}
    llm_output = json.dumps({
        "CIT-0001": {"formatted_citation": "real one"},
        "CIT-9999": {"formatted_citation": "the LLM invented this id"},
    })
    parsed = s5d.parse_llm_output(llm_output, id_map)
    assert "CIT-0001" in parsed
    assert "CIT-9999" not in parsed


def test_5d_parse_llm_output_missing_id_is_simply_absent():
    id_map = {"CIT-0001": {}, "CIT-0002": {}}
    llm_output = json.dumps({"CIT-0001": {"formatted_citation": "only this came back"}})
    parsed = s5d.parse_llm_output(llm_output, id_map)
    assert "CIT-0001" in parsed
    assert "CIT-0002" not in parsed


def test_5d_parse_llm_output_extracts_embedded_json_from_prose():
    # First json.loads(whole string) fails (there is surrounding prose), so
    # the function falls back to regex-extracting the {...} span.
    id_map = {"CIT-0001": {}}
    llm_output = (
        "Here is the result:\n"
        + json.dumps({"CIT-0001": {"formatted_citation": "extracted from prose"}})
        + "\nHope that helps!"
    )
    parsed = s5d.parse_llm_output(llm_output, id_map)
    assert parsed["CIT-0001"]["formatted_citation"] == "extracted from prose"


def test_5d_parse_llm_output_malformed_json_returns_empty_dict():
    id_map = {"CIT-0001": {}}
    # Has a brace-delimited span but it is not valid JSON inside.
    parsed = s5d.parse_llm_output("{not: valid, json here}", id_map)
    assert parsed == {}


def test_5d_parse_llm_output_no_braces_at_all_returns_empty_dict():
    # Prose response with no JSON braces anywhere: the direct json.loads
    # fails, then re.search finds no {...} span, so parse_llm_output returns
    # empty without ever attempting a second json.loads.
    id_map = {"CIT-0001": {}}
    parsed = s5d.parse_llm_output("Sorry, I cannot format these citations.", id_map)
    assert parsed == {}


def test_5d_parse_llm_output_embedded_json_also_filters_invented_ids():
    # Forces the fallback (embedded-JSON) branch specifically, and confirms
    # the id_to_entry membership check applies there too, not just on the
    # direct-parse fast path.
    id_map = {"CIT-0001": {}}
    llm_output = (
        "Here you go:\n"
        + json.dumps({
            "CIT-0001": {"formatted_citation": "kept"},
            "CIT-4242": {"formatted_citation": "invented, must be filtered"},
        })
    )
    parsed = s5d.parse_llm_output(llm_output, id_map)
    assert list(parsed.keys()) == ["CIT-0001"]


def test_5d_parse_llm_output_empty_string_returns_empty_dict():
    parsed = s5d.parse_llm_output("", {"CIT-0001": {}})
    assert parsed == {}


# ---------------------------------------------------------------------------
# stage_5d: call_llm_formatter
# ---------------------------------------------------------------------------

def test_5d_call_llm_formatter_returns_text_and_usage_tuple(monkeypatch):
    monkeypatch.setattr(s5d, "call_llm", lambda **kw: _llm_result('{"CIT-0001": {}}'))
    text, usage = s5d.call_llm_formatter("raw content")
    assert text == '{"CIT-0001": {}}'
    assert usage["model"] == "sentinel-model"
    assert usage["total_tokens"] == 18


def test_5d_prompt_keeps_every_author_and_et_al_only_where_the_source_has_it(monkeypatch):
    """#1259: a "first 6, et al." rule cut the CV owner from their own
    citation when they sat seventh or later. The prompt the model receives
    asks for the whole author list, with "et al." only where the source
    writes it."""
    sent = []
    monkeypatch.setattr(s5d, "call_llm", lambda **kw: sent.append(kw) or _llm_result("{}"))
    s5d.call_llm_formatter("raw content")
    prompt = sent[0]["messages"][0]["content"]
    assert "first 6" not in prompt
    assert ('List every author the source lists, in the source\'s order. Never shorten '
            'the list: write "et al." only where the source itself does') in prompt


def test_5d_call_llm_formatter_exception_arm_returns_none_none(monkeypatch):
    def _boom(**kw):
        raise ValueError("bad request")

    monkeypatch.setattr(s5d, "call_llm", _boom)
    text, usage = s5d.call_llm_formatter("raw content")
    assert text is None
    assert usage is None


# ---------------------------------------------------------------------------
# stage_5d: _format_batch / _BatchResult -- id_to_formatted's three states
# (#918 review point #2: None = no parse ran, {} = parsed and found
# nothing, non-empty = parsed. One field, not two.)
# ---------------------------------------------------------------------------

def test_5d_format_batch_id_to_formatted_is_none_when_the_call_raises(monkeypatch):
    monkeypatch.setattr(s5d, "call_llm", lambda **kw: (_ for _ in ()).throw(ValueError("boom")))
    result = s5d._format_batch([{"taxonomy_code": "S1", "text": "x"}])
    assert result.usage is None
    assert result.id_to_formatted is None


def test_5d_format_batch_id_to_formatted_is_none_on_a_billed_empty_response(monkeypatch):
    monkeypatch.setattr(s5d, "call_llm", lambda **kw: _llm_result(""))
    result = s5d._format_batch([{"taxonomy_code": "S1", "text": "x"}])
    assert result.usage is not None
    assert result.id_to_formatted is None


def test_5d_format_batch_id_to_formatted_is_empty_dict_when_parse_finds_nothing(monkeypatch):
    monkeypatch.setattr(s5d, "call_llm", lambda **kw: _llm_result("no braces here at all"))
    result = s5d._format_batch([{"taxonomy_code": "S1", "text": "x"}])
    assert result.usage is not None
    assert result.id_to_formatted == {}


def test_5d_format_batch_id_to_formatted_is_non_empty_when_parse_succeeds(monkeypatch):
    monkeypatch.setattr(
        s5d, "call_llm",
        lambda **kw: _llm_result(json.dumps({"CIT-0001": {"formatted_citation": "cite"}})),
    )
    result = s5d._format_batch([{"taxonomy_code": "S1", "text": "x"}])
    assert result.id_to_formatted == {"CIT-0001": {"formatted_citation": "cite"}}


# ---------------------------------------------------------------------------
# stage_5d: _batch_progress_printer -- the {} vs None distinction on the
# wire (blind verifier on PR #918's rework: `if result.id_to_formatted is
# not None:` mutated to `if result.id_to_formatted:` passed every test --
# a batch that parsed and found nothing (id_to_formatted == {}) stopped
# printing its "Parsed 0 formatted citations" line). Drives the real
# printer built by _batch_progress_printer, not a hand-rolled copy.
# ---------------------------------------------------------------------------

def test_5d_batch_progress_printer_prints_parsed_0_when_a_parse_found_nothing(capsys):
    printer = s5d._batch_progress_printer(1)
    result = s5d._BatchResult(id_to_formatted={}, id_to_entry={"CIT-0001": {}}, usage=None)
    printer(0, result)
    out = capsys.readouterr().out
    assert "Parsed 0 formatted citations" in out


def test_5d_batch_progress_printer_omits_parsed_line_when_no_parse_ran(capsys):
    printer = s5d._batch_progress_printer(1)
    result = s5d._BatchResult(id_to_formatted=None, id_to_entry={"CIT-0001": {}}, usage=None)
    printer(0, result)
    out = capsys.readouterr().out
    assert "Parsed" not in out


# ---------------------------------------------------------------------------
# stage_5d: run_stage_5d end to end
# ---------------------------------------------------------------------------

def test_run_stage_5d_formats_non_enriched_and_preserves_enriched(tmp_path, monkeypatch):
    input_data = {
        "document_uid": "XYZ789",
        "entries": [
            {
                "taxonomy_code": "S1",
                "text": "Smith J. A study of things. 2020.",
                "enrichment_status": "not_found",
                "extracted_fields": {},
            },
            {
                "taxonomy_code": "S1",
                "text": "already enriched via PubMed",
                "enrichment_status": "enriched",
                "extracted_fields": {"formatted_citation": "PUBMED-SOURCED CITATION"},
            },
            {
                "taxonomy_code": "K1",
                "text": "a teaching entry, not a publication at all",
                "extracted_fields": {},
            },
        ],
    }
    input_path = _write_json(tmp_path / "in.json", input_data)
    output_path = str(tmp_path / "out.json")

    def _fake_call_llm(**kw):
        return _llm_result(json.dumps({
            "CIT-0001": {
                "authors": "Smith J",
                "doi": "10.1/xyz",
                "formatted_citation": "Smith J. A study of things. J Test. 2020.",
            }
        }))

    monkeypatch.setattr(s5d, "call_llm", _fake_call_llm)
    result_path = s5d.run_stage_5d(input_path, output_path=output_path, verbose=True)
    assert result_path == output_path

    with open(output_path, encoding="utf-8") as f:
        out = json.load(f)

    formatted = out["entries"][0]["extracted_fields"]
    assert formatted["formatted_citation"] == "Smith J. A study of things. J Test. 2020."
    assert formatted["formatting_source"] == "stage_5d_llm"
    assert formatted["authors"] == "Smith J"
    assert formatted["doi"] == "10.1/xyz"

    # The already-enriched S1 entry must be untouched.
    enriched = out["entries"][1]["extracted_fields"]
    assert enriched == {"formatted_citation": "PUBMED-SOURCED CITATION"}

    # The K1 (non-publication) entry must be untouched too.
    assert out["entries"][2]["extracted_fields"] == {}

    meta = out["stage_5d"]
    assert meta["non_enriched_count"] == 1
    assert meta["formatted_count"] == 1
    assert meta["model"] == "sentinel-model"


def test_run_stage_5d_no_non_enriched_publications_copies_input_through(tmp_path, monkeypatch):
    input_data = {
        "document_uid": "ALLENRICHED",
        "entries": [
            {
                "taxonomy_code": "S1",
                "text": "already enriched",
                "enrichment_status": "enriched",
                "extracted_fields": {},
            }
        ],
    }
    input_path = _write_json(tmp_path / "in.json", input_data)
    output_path = str(tmp_path / "out.json")

    calls = []
    monkeypatch.setattr(s5d, "call_llm", lambda **kw: calls.append(1) or _llm_result("{}"))

    s5d.run_stage_5d(input_path, output_path=output_path, verbose=True)

    with open(output_path, encoding="utf-8") as f:
        out = json.load(f)

    assert calls == []
    assert "stage_5d" not in out
    assert out == input_data


def test_run_stage_5d_does_not_overwrite_an_existing_non_empty_field(tmp_path, monkeypatch):
    # entry already carries a real author string; the LLM's copy-back for
    # individual fields must only fill genuinely empty or 'NONE' fields.
    input_data = {
        "document_uid": "KEEPFIELD",
        "entries": [
            {
                "taxonomy_code": "S1",
                "text": "raw citation text",
                "enrichment_status": "",
                "extracted_fields": {"authors": "Existing Author", "doi": "NONE"},
            }
        ],
    }
    input_path = _write_json(tmp_path / "in.json", input_data)
    output_path = str(tmp_path / "out.json")

    def _fake_call_llm(**kw):
        return _llm_result(json.dumps({
            "CIT-0001": {
                "authors": "LLM Overwrote Author",
                "doi": "10.9/new",
            }
        }))

    monkeypatch.setattr(s5d, "call_llm", _fake_call_llm)
    s5d.run_stage_5d(input_path, output_path=output_path, verbose=False)

    with open(output_path, encoding="utf-8") as f:
        out = json.load(f)

    fields = out["entries"][0]["extracted_fields"]
    # authors was already a real, non-'NONE' value -> preserved.
    assert fields["authors"] == "Existing Author"
    # doi was the 'NONE' sentinel -> the LLM-provided value replaces it.
    assert fields["doi"] == "10.9/new"
    # formatted_citation key was absent from the LLM response, so no
    # formatting_source / formatted_citation is recorded for this entry.
    assert "formatted_citation" not in fields
    assert "formatting_source" not in fields


def _run_5d_on_one_reply(tmp_path, monkeypatch, text, reply):
    """Run stage 5d over one S1 entry whose LLM reply is `reply`; return its fields."""
    input_path = _write_json(tmp_path / "in.json", {
        "document_uid": "PLACEHOLD",
        "entries": [{"taxonomy_code": "S1", "text": text,
                     "enrichment_status": "no_identifier", "extracted_fields": {}}],
    })
    output_path = str(tmp_path / "out.json")
    monkeypatch.setattr(s5d, "call_llm",
                        lambda **kw: _llm_result(json.dumps({"CIT-0001": reply})))
    s5d.run_stage_5d(input_path, output_path=output_path, verbose=False)
    with open(output_path, encoding="utf-8") as f:
        return json.load(f)["entries"][0]["extracted_fields"]


def test_run_stage_5d_never_writes_a_placeholder_title(tmp_path, monkeypatch):
    # #446 (EBYSBC HFAJCC-05): the source lost everything after the author
    # list; the LLM invented "[Title not provided]" for title and citation.
    fields = _run_5d_on_one_reply(
        tmp_path, monkeypatch, "Quill A, Brandt B, Ostrow C,",
        {"authors": "Quill A, Brandt B, Ostrow C", "title": "[Title not provided]",
         "formatted_citation": "Quill A, Brandt B, Ostrow C. [Title not provided]."})
    assert "title" not in fields
    assert fields["formatted_citation"] == "Quill A, Brandt B, Ostrow C."
    assert fields["authors"] == "Quill A, Brandt B, Ostrow C"


def test_run_stage_5d_drops_a_citation_that_was_only_a_placeholder(tmp_path, monkeypatch):
    # #446 (EBYSBC XWNZWW-02): a split-off place name; the placeholder leads.
    fields = _run_5d_on_one_reply(
        tmp_path, monkeypatch, "Ruritania.",
        {"title": "[Title not available]", "formatted_citation": "[Title not available]. Ruritania."})
    assert "title" not in fields
    assert fields["formatted_citation"] == "Ruritania."
    fields = _run_5d_on_one_reply(
        tmp_path, monkeypatch, "Ruritania.",
        {"title": "[Title not available]", "formatted_citation": "[Title not available]."})
    assert fields == {}


def test_run_stage_5d_closes_the_gap_a_mid_citation_placeholder_leaves(tmp_path, monkeypatch):
    # Cutting the title out of the middle leaves ". ."; one period remains.
    fields = _run_5d_on_one_reply(
        tmp_path, monkeypatch, "Quill A. J Imag Stud. 2019.",
        {"title": "[Title not provided]",
         "formatted_citation": "Quill A. [Title not provided]. J Imag Stud. 2019."})
    assert fields["formatted_citation"] == "Quill A. J Imag Stud. 2019."


def test_run_stage_5d_keeps_a_bracketed_title_the_source_carries(tmp_path, monkeypatch):
    fields = _run_5d_on_one_reply(
        tmp_path, monkeypatch, "Quill A. [Erratum]. J Imag Stud. 2019.",
        {"title": "[Erratum]", "formatted_citation": "Quill A. [Erratum]. J Imag Stud. 2019."})
    assert fields["title"] == "[Erratum]"
    assert fields["formatted_citation"] == "Quill A. [Erratum]. J Imag Stud. 2019."


def test_run_stage_5d_processes_multiple_batches_independently(tmp_path, monkeypatch):
    # batch_size=1 forces two separate call_llm_formatter calls; each batch
    # renumbers its own single entry as CIT-0001, so the fake must key off
    # the raw content (not the id) to prove both batches were applied to the
    # right underlying entry.
    input_data = {
        "document_uid": "TWOBATCH",
        "entries": [
            {
                "taxonomy_code": "S1",
                "text": "FIRST_ENTRY_MARKER",
                "enrichment_status": "",
                "extracted_fields": {},
            },
            {
                "taxonomy_code": "S1",
                "text": "SECOND_ENTRY_MARKER",
                "enrichment_status": "",
                "extracted_fields": {},
            },
        ],
    }
    input_path = _write_json(tmp_path / "in.json", input_data)
    output_path = str(tmp_path / "out.json")

    def _fake_call_llm(**kw):
        prompt = kw["messages"][0]["content"]
        if "FIRST_ENTRY_MARKER" in prompt:
            citation = "citation for the first entry"
        else:
            assert "SECOND_ENTRY_MARKER" in prompt
            citation = "citation for the second entry"
        return _llm_result(json.dumps({"CIT-0001": {"formatted_citation": citation}}))

    monkeypatch.setattr(s5d, "call_llm", _fake_call_llm)
    s5d.run_stage_5d(input_path, output_path=output_path, verbose=True, batch_size=1)

    with open(output_path, encoding="utf-8") as f:
        out = json.load(f)

    assert out["entries"][0]["extracted_fields"]["formatted_citation"] == "citation for the first entry"
    assert out["entries"][1]["extracted_fields"]["formatted_citation"] == "citation for the second entry"
    assert out["stage_5d"]["formatted_count"] == 2


# ---------------------------------------------------------------------------
# stage_5d: run_stage_5d on core/batch_pool.map_in_order (#881 step 6)
# ---------------------------------------------------------------------------

_MANY_CITATIONS = [f"CITATION_MARKER_{i}" for i in range(6)]


def _many_citations_input(document_uid: str) -> dict:
    return {
        "document_uid": document_uid,
        "entries": [
            {"taxonomy_code": "S1", "text": marker, "enrichment_status": "", "extracted_fields": {}}
            for marker in _MANY_CITATIONS
        ],
    }


def _citation_index_from_prompt(prompt: str, markers: list[str]) -> int:
    return next(i for i, m in enumerate(markers) if m in prompt)


def _strip_timestamp(data: dict) -> dict:
    data = json.loads(json.dumps(data))  # deep copy
    data.get("stage_5d", {}).pop("timestamp", None)
    return data


def test_5d_stage5d_workers_config_knob_is_read_from_env(monkeypatch):
    # STAGE5D_BATCH_WORKERS itself is bound once, at import time, so it
    # can't observe an env var set by a test -- this pins the reader it's
    # built from instead: workers_from_config("CVICHE_STAGE5D_BATCH_WORKERS").
    monkeypatch.setenv("CVICHE_STAGE5D_BATCH_WORKERS", "7")
    assert s5d.workers_from_config("CVICHE_STAGE5D_BATCH_WORKERS") == 7


def test_run_stage_5d_defaults_to_the_config_knob():
    # Pins the default itself, not just the reader. An `is`/`==` check
    # against STAGE5D_BATCH_WORKERS's own value is not enough: a hardcoded
    # `workers: int = 4` literal would still satisfy `4 is 4` under CPython
    # small-int caching, because the knob's real default also happens to be
    # 4 -- verified round 1 (`4 is s5d.STAGE5D_BATCH_WORKERS` -> True). So
    # this reloads the module with the env knob set to a value (7) nothing
    # would coincidentally equal, and asserts run_stage_5d's default follows
    # it -- a literal default cannot move.
    original = os.environ.get("CVICHE_STAGE5D_BATCH_WORKERS")
    os.environ["CVICHE_STAGE5D_BATCH_WORKERS"] = "7"
    try:
        importlib.reload(s5d)
        default = inspect.signature(s5d.run_stage_5d).parameters["workers"].default
        assert default == 7
    finally:
        # Restore the env var BEFORE the final reload, not after -- monkeypatch's
        # own teardown runs after this function returns, too late to matter here.
        if original is None:
            os.environ.pop("CVICHE_STAGE5D_BATCH_WORKERS", None)
        else:
            os.environ["CVICHE_STAGE5D_BATCH_WORKERS"] = original
        importlib.reload(s5d)
        assert inspect.signature(s5d.run_stage_5d).parameters["workers"].default == s5d.STAGE5D_BATCH_WORKERS


def test_5d_parallel_batches_write_the_same_artifact_as_the_serial_loop(tmp_path, monkeypatch):
    """Batches finish in reverse-of-submission order under the pool; the
    written artifact must not depend on that."""
    in_flight = {"now": 0, "peak": 0}
    lock = threading.Lock()
    # Order-sensitive per-batch cost: 1e16 for the first-submitted batch,
    # 1.0 for every later one. Submitted (left-to-right) order sums to
    # exactly 1e16 (each +1.0 rounds away at that magnitude); reversed
    # (completion) order sums to 1.0000000000000004e16 -- a genuinely
    # different float, not just a coincidence of these particular values
    # (verified: sum([1e16,1,1,1,1,1]) != sum(reversed([...]))).
    costs = {0: 1e16, 1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0, 5: 1.0}

    def slow_early_batches(**kwargs):
        prompt = kwargs["messages"][0]["content"]
        idx = _citation_index_from_prompt(prompt, _MANY_CITATIONS)
        with lock:
            in_flight["now"] += 1
            in_flight["peak"] = max(in_flight["peak"], in_flight["now"])
        # Later-submitted batches answer first: batch 5 sleeps least.
        time.sleep((len(_MANY_CITATIONS) - idx) * 0.01)
        with lock:
            in_flight["now"] -= 1
        return _llm_result(
            json.dumps({"CIT-0001": {"formatted_citation": f"cite {idx}"}}),
            cost=costs[idx],
        )

    input_data = _many_citations_input("MANYCIT1")
    input_path = _write_json(tmp_path / "in.json", input_data)
    monkeypatch.setattr(s5d, "call_llm", slow_early_batches)

    serial_path = str(tmp_path / "serial.json")
    s5d.run_stage_5d(input_path, output_path=serial_path, verbose=False, batch_size=1, workers=1)
    assert in_flight["peak"] == 1  # workers=1 really is the serial loop

    in_flight["peak"] = 0
    parallel_path = str(tmp_path / "parallel.json")
    s5d.run_stage_5d(input_path, output_path=parallel_path, verbose=False, batch_size=1, workers=4)
    assert in_flight["peak"] > 1  # and workers=4 really overlapped

    with open(serial_path, encoding="utf-8") as f:
        serial = json.load(f)
    with open(parallel_path, encoding="utf-8") as f:
        parallel = json.load(f)

    serial, parallel = _strip_timestamp(serial), _strip_timestamp(parallel)
    assert serial == parallel
    # Exact float equality: submission-order summation, not just the final
    # entries list, must match the serial loop.
    assert serial["stage_5d"]["total_cost"] == 1e16
    assert parallel["stage_5d"]["total_cost"] == 1e16


def test_5d_total_cost_equals_the_serial_sum_when_per_batch_costs_differ(tmp_path, monkeypatch):
    costs = {0: 1e16, 1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0, 5: 1.0}

    def slow_early_batches(**kwargs):
        prompt = kwargs["messages"][0]["content"]
        idx = _citation_index_from_prompt(prompt, _MANY_CITATIONS)
        time.sleep((len(_MANY_CITATIONS) - idx) * 0.01)
        return _llm_result(
            json.dumps({"CIT-0001": {"formatted_citation": f"cite {idx}"}}),
            cost=costs[idx],
        )

    input_data = _many_citations_input("COSTSUM1")
    input_path = _write_json(tmp_path / "in.json", input_data)
    monkeypatch.setattr(s5d, "call_llm", slow_early_batches)

    out_path = str(tmp_path / "out.json")
    s5d.run_stage_5d(input_path, output_path=out_path, verbose=False, batch_size=1, workers=4)

    with open(out_path, encoding="utf-8") as f:
        out = json.load(f)

    # The only value that reproduces this exactly is left-to-right
    # summation in SUBMISSION order (a completion-order accumulator would
    # land on 1.0000000000000004e16 instead, since batch 0 -- the huge
    # cost -- finishes LAST here).
    assert out["stage_5d"]["total_cost"] == 1e16


def test_5d_batch_progress_lines_are_monotonic_and_unspliced(tmp_path, monkeypatch, capsys):
    def slow_early_batches(**kwargs):
        prompt = kwargs["messages"][0]["content"]
        idx = _citation_index_from_prompt(prompt, _MANY_CITATIONS)
        time.sleep((len(_MANY_CITATIONS) - idx) * 0.01)
        return _llm_result(json.dumps({"CIT-0001": {"formatted_citation": f"cite {idx}"}}))

    input_data = _many_citations_input("MANYCIT2")
    input_path = _write_json(tmp_path / "in.json", input_data)
    monkeypatch.setattr(s5d, "call_llm", slow_early_batches)

    s5d.run_stage_5d(input_path, output_path=str(tmp_path / "out.json"), verbose=True, batch_size=1, workers=4)

    out = capsys.readouterr().out.splitlines()
    total = len(_MANY_CITATIONS)
    marker = f"/{total}] batches formatted ("
    progress = [line for line in out if marker in line]
    assert len(progress) == total
    nums = [int(line.split("[")[1].split("/")[0]) for line in progress]
    assert nums == list(range(1, total + 1))
    # Each batch's block is one atomic print: "[N/M] batches formatted..." then "Parsed...".
    for i, line in enumerate(out):
        if marker in line:
            assert out[i + 1].strip().startswith("Parsed"), out[i:i + 2]


def test_5d_batch_progress_line_is_read_by_progress_patterns(capsys, progress_patterns):
    # Drive the REAL printer (mrj4001's point #1 on PR #918: a hand-typed
    # literal here can't catch a future reword of the printer's own
    # wording). The patterns come from orchestrator.py's source, pinned in
    # conftest.py's progress_patterns fixture. The line used to match none
    # of them, so the web bar sat at its placeholder for all of 5d.
    printer = s5d._batch_progress_printer(10)
    filler = s5d._BatchResult(id_to_formatted=None, id_to_entry={"CIT-0001": {}}, usage=None)
    five_cited = s5d._BatchResult(
        id_to_formatted=None,
        id_to_entry={f"CIT-{i:04d}": {} for i in range(5)},
        usage=None,
    )
    printer(0, filler)
    printer(0, filler)
    printer(0, five_cited)  # 3rd completion -> "[3/10] batches formatted (5 citations)"
    line = capsys.readouterr().out.splitlines()[-1]
    match = next(m for p in progress_patterns if (m := p.search(line)))
    assert (int(match.group(1)), int(match.group(2))) == (3, 10)
    assert line.endswith("(5 citations)")


def test_5d_batches_run_inside_the_callers_run_id_context(tmp_path, monkeypatch):
    from unified_pipeline.core import prompt_logger

    seen = set()

    def record(**kwargs):
        seen.add(prompt_logger._current_run_id.get())
        prompt = kwargs["messages"][0]["content"]
        idx = _citation_index_from_prompt(prompt, _MANY_CITATIONS)
        return _llm_result(json.dumps({"CIT-0001": {"formatted_citation": f"cite {idx}"}}))

    input_data = _many_citations_input("MANYCIT3")
    input_path = _write_json(tmp_path / "in.json", input_data)
    monkeypatch.setattr(s5d, "call_llm", record)

    token = prompt_logger.set_current_run_id("run-stg5d")
    try:
        s5d.run_stage_5d(input_path, output_path=str(tmp_path / "out.json"), verbose=False, batch_size=1, workers=4)
    finally:
        prompt_logger.reset_current_run_id(token)

    assert seen == {"run-stg5d"}


class _FakeRoutedStdout:
    """Minimal stand-in for orchestrator.py's ``_RoutedStdout`` -- a dict
    keyed by ``threading.get_ident()``. A registered thread's write lands in
    its own capture; any other thread's write lands in ``leak`` instead.
    Isolates the property under test: output must come from the calling
    thread's ``on_result`` callback (via ``_batch_progress_printer``), not
    from a print inside ``_format_batch`` itself.
    """

    def __init__(self, leak):
        self._leak = leak
        self._captures = {}

    def register(self, ident, capture):
        self._captures[ident] = capture

    def write(self, text):
        return self._captures.get(threading.get_ident(), self._leak).write(text)

    def flush(self):
        pass


def test_5d_batch_progress_prints_only_from_the_calling_thread(tmp_path, monkeypatch):
    def slow_early_batches(**kwargs):
        prompt = kwargs["messages"][0]["content"]
        idx = _citation_index_from_prompt(prompt, _MANY_CITATIONS)
        time.sleep((len(_MANY_CITATIONS) - idx) * 0.01)
        return _llm_result(json.dumps({"CIT-0001": {"formatted_citation": f"cite {idx}"}}))

    input_data = _many_citations_input("MANYCIT4")
    input_path = _write_json(tmp_path / "in.json", input_data)
    monkeypatch.setattr(s5d, "call_llm", slow_early_batches)

    registered = io.StringIO()
    leak = io.StringIO()
    fake_stdout = _FakeRoutedStdout(leak)
    fake_stdout.register(threading.get_ident(), registered)

    real_stdout = sys.stdout
    monkeypatch.setattr(sys, "stdout", fake_stdout)
    try:
        s5d.run_stage_5d(input_path, output_path=str(tmp_path / "out.json"), verbose=True, batch_size=1, workers=4)
    finally:
        monkeypatch.setattr(sys, "stdout", real_stdout)

    assert "batches formatted" in registered.getvalue()
    assert leak.getvalue() == ""


def test_5d_workers_one_never_overlaps_and_workers_four_does(tmp_path, monkeypatch):
    in_flight = {"now": 0, "peak": 0}
    lock = threading.Lock()

    def track(**kwargs):
        prompt = kwargs["messages"][0]["content"]
        idx = _citation_index_from_prompt(prompt, _MANY_CITATIONS)
        with lock:
            in_flight["now"] += 1
            in_flight["peak"] = max(in_flight["peak"], in_flight["now"])
        time.sleep(0.02)
        with lock:
            in_flight["now"] -= 1
        return _llm_result(json.dumps({"CIT-0001": {"formatted_citation": f"cite {idx}"}}))

    input_data = _many_citations_input("MANYCIT5")
    input_path = _write_json(tmp_path / "in.json", input_data)
    monkeypatch.setattr(s5d, "call_llm", track)

    s5d.run_stage_5d(input_path, output_path=str(tmp_path / "s1.json"), verbose=False, batch_size=1, workers=1)
    assert in_flight["peak"] == 1

    in_flight["peak"] = 0
    s5d.run_stage_5d(input_path, output_path=str(tmp_path / "s4.json"), verbose=False, batch_size=1, workers=4)
    assert in_flight["peak"] >= 2


def test_5d_a_swallowed_batch_leaves_it_unformatted_others_formatted_serial_and_parallel(tmp_path, monkeypatch):
    # Batch index 2 raises inside call_llm; call_llm_formatter's own
    # `except Exception` swallows it and returns (None, None) -- unchanged
    # by the move to the pool. Every other batch must still be formatted,
    # for both workers=1 (true serial) and workers=4.
    def flaky(**kwargs):
        prompt = kwargs["messages"][0]["content"]
        idx = _citation_index_from_prompt(prompt, _MANY_CITATIONS)
        if idx == 2:
            raise RuntimeError("simulated LLM failure")
        return _llm_result(json.dumps({"CIT-0001": {"formatted_citation": f"cite {idx}"}}))

    input_data = _many_citations_input("MANYCIT6")
    input_path = _write_json(tmp_path / "in.json", input_data)
    monkeypatch.setattr(s5d, "call_llm", flaky)

    for workers, out_name in [(1, "serial.json"), (4, "parallel.json")]:
        out_path = str(tmp_path / out_name)
        s5d.run_stage_5d(input_path, output_path=out_path, verbose=False, batch_size=1, workers=workers)
        with open(out_path, encoding="utf-8") as f:
            out = json.load(f)
        for i, entry in enumerate(out["entries"]):
            if i == 2:
                assert "formatted_citation" not in entry["extracted_fields"], (workers, i)
            else:
                assert entry["extracted_fields"]["formatted_citation"] == f"cite {i}", (workers, i)
        assert out["stage_5d"]["formatted_count"] == len(_MANY_CITATIONS) - 1
        assert out["stage_5d"]["non_enriched_count"] == len(_MANY_CITATIONS)


def test_5d_a_swallowed_batch_logs_the_exception_from_a_pool_thread(tmp_path, monkeypatch, caplog):
    # #810 / round-1 finding: call_llm_formatter's `except Exception` runs
    # inside _format_batch, which is always called with verbose=False --
    # including under workers=4, where it can land on a pool thread. A
    # print there is forbidden, but the failure must not vanish: this pins
    # that logger.exception still carries the exception type and message
    # into the run log, the way the pre-#881 print + traceback did.
    def flaky(**kwargs):
        prompt = kwargs["messages"][0]["content"]
        idx = _citation_index_from_prompt(prompt, _MANY_CITATIONS)
        if idx == 2:
            raise RuntimeError("simulated throttle")
        return _llm_result(json.dumps({"CIT-0001": {"formatted_citation": f"cite {idx}"}}))

    input_data = _many_citations_input("MANYCIT8")
    input_path = _write_json(tmp_path / "in.json", input_data)
    monkeypatch.setattr(s5d, "call_llm", flaky)

    with caplog.at_level(logging.WARNING, logger="unified_pipeline.stage_5d_citation_formatter"):
        s5d.run_stage_5d(
            input_path, output_path=str(tmp_path / "out.json"), verbose=True, batch_size=1, workers=4
        )

    messages = [record.getMessage() for record in caplog.records]
    assert any("RuntimeError" in m and "simulated throttle" in m for m in messages), messages


def test_5d_a_malformed_llm_response_logs_the_parse_warning_from_a_pool_thread(tmp_path, monkeypatch, caplog):
    # Same finding, the other swallowed diagnostic: parse_llm_output's
    # `except json.JSONDecodeError` (a batch whose brace-delimited span
    # isn't valid JSON), also always called with verbose=False from
    # _format_batch.
    def garbled(**kwargs):
        return _llm_result("{not valid json but has a brace span}")

    input_data = {
        "document_uid": "MALFORMED5D",
        "entries": [
            {"taxonomy_code": "S1", "text": "SOLO_ENTRY", "enrichment_status": "", "extracted_fields": {}}
        ],
    }
    input_path = _write_json(tmp_path / "in.json", input_data)
    monkeypatch.setattr(s5d, "call_llm", garbled)

    with caplog.at_level(logging.WARNING, logger="unified_pipeline.stage_5d_citation_formatter"):
        s5d.run_stage_5d(
            input_path, output_path=str(tmp_path / "out.json"), verbose=True, batch_size=1, workers=4
        )

    messages = [record.getMessage() for record in caplog.records]
    assert any("Could not parse LLM JSON output" in m for m in messages), messages


def test_5d_verbose_false_prints_nothing_at_workers_one_and_four(tmp_path, monkeypatch, capsys):
    # r2 m08: `_printer = _batch_progress_printer(len(batches)) if verbose
    # else None` guards the printer's construction, not just its call site
    # -- dropping the `if verbose else None` guard builds the printer
    # unconditionally and every on_result fires regardless of verbose.
    # >=3 batches at both worker counts so the pool path is exercised too.
    def steady(**kwargs):
        prompt = kwargs["messages"][0]["content"]
        idx = _citation_index_from_prompt(prompt, _MANY_CITATIONS)
        return _llm_result(json.dumps({"CIT-0001": {"formatted_citation": f"cite {idx}"}}))

    input_data = _many_citations_input("QUIET5D")
    input_path = _write_json(tmp_path / "in.json", input_data)
    monkeypatch.setattr(s5d, "call_llm", steady)

    for workers, out_name in [(1, "quiet_w1.json"), (4, "quiet_w4.json")]:
        capsys.readouterr()  # drain anything from a previous iteration
        s5d.run_stage_5d(
            input_path, output_path=str(tmp_path / out_name), verbose=False, batch_size=1, workers=workers
        )
        assert capsys.readouterr().out == "", workers


def test_5d_empty_llm_text_with_usage_prints_no_parsed_line_and_logs_nothing(tmp_path, monkeypatch, capsys, caplog):
    # A2: dev gated the parse + "Parsed" line on `if llm_output:` (truthy),
    # not on usage being present. call_llm_formatter can return a real,
    # non-None usage dict alongside an EMPTY llm_output string (the model
    # billed tokens but produced no content) -- that must print no "Parsed"
    # line and log no "Could not parse" warning, matching dev, and the
    # artifact must be identical whether the batch ran serially or pooled.
    def billed_but_empty(**kwargs):
        return _llm_result("")

    input_data = _many_citations_input("EMPTYTXT5D")
    input_path = _write_json(tmp_path / "in.json", input_data)
    monkeypatch.setattr(s5d, "call_llm", billed_but_empty)

    with caplog.at_level(logging.WARNING, logger="unified_pipeline.stage_5d_citation_formatter"):
        s5d.run_stage_5d(
            input_path, output_path=str(tmp_path / "serial.json"), verbose=True, batch_size=1, workers=1
        )
    out = capsys.readouterr().out
    assert "Parsed" not in out
    assert "batches formatted" in out
    assert not [r for r in caplog.records if "Could not parse" in r.getMessage()]

    s5d.run_stage_5d(
        input_path, output_path=str(tmp_path / "parallel.json"), verbose=False, batch_size=1, workers=4
    )
    with open(tmp_path / "serial.json", encoding="utf-8") as f:
        serial = _strip_timestamp(json.load(f))
    with open(tmp_path / "parallel.json", encoding="utf-8") as f:
        parallel = _strip_timestamp(json.load(f))
    assert serial == parallel
    assert serial["stage_5d"]["formatted_count"] == 0

    # Blind verifier on PR #918's rework: changing the accumulation gate
    # from `if result.usage is not None:` to `if result.id_to_formatted is
    # not None:` passed every test -- it drops a batch's cost and tokens
    # whenever the response was billed but empty (id_to_formatted is None
    # in exactly that case), silently undercounting a real per-run cost.
    # _MANY_CITATIONS has 6 markers, batch_size=1 -> 6 batches, each with
    # _llm_result("")'s usage (cost=0.002, prompt_tokens=11,
    # completion_tokens=7): the run total must still carry all six.
    six_batches_cost = 0.0
    for _ in range(len(_MANY_CITATIONS)):
        six_batches_cost += 0.002
    assert serial["stage_5d"]["total_cost"] == six_batches_cost
    assert serial["stage_5d"]["prompt_tokens"] == 11 * len(_MANY_CITATIONS)
    assert serial["stage_5d"]["completion_tokens"] == 7 * len(_MANY_CITATIONS)
    assert serial["stage_5d"]["total_tokens"] == 18 * len(_MANY_CITATIONS)


@pytest.mark.parametrize("workers", [1, 4])
def test_5d_llm_outage_fails_the_stage_while_other_errors_still_degrade(tmp_path, monkeypatch, workers):
    # #810: an outage past the budget must fail the run, serial and on the
    # pool; any other call_llm error is still swallowed to an unformatted batch.
    from unified_pipeline.llm.retry import LLMOutageError

    def flaky(**kwargs):
        idx = _citation_index_from_prompt(kwargs["messages"][0]["content"], _MANY_CITATIONS)
        if idx == 2:
            raise LLMOutageError("provider down", seconds_waited=1800.0)
        return _llm_result(json.dumps({"CIT-0001": {"formatted_citation": f"cite {idx}"}}))

    input_path = _write_json(tmp_path / "in.json", _many_citations_input("OUTAGE5D"))
    monkeypatch.setattr(s5d, "call_llm", flaky)
    with pytest.raises(LLMOutageError):
        s5d.run_stage_5d(input_path, output_path=str(tmp_path / "out.json"),
                         verbose=False, batch_size=1, workers=workers)

    monkeypatch.setattr(s5d, "call_llm", lambda **kw: (_ for _ in ()).throw(RuntimeError("connection reset")))
    assert s5d.call_llm_formatter("raw") == (None, None)


def test_apply_formatted_fields_cuts_a_placeholder_whose_title_ends_in_a_period():
    # The reply's title carries a trailing period the citation does not.
    entry = {"text": "Quill A. J Imag Stud. 2019.", "extracted_fields": {}}
    reply = {"title": "[Title not provided].",
             "formatted_citation": "Quill A. [Title not provided], J Imag Stud, 2019."}
    assert s5d.apply_formatted_fields(entry, reply) is True
    assert "[" not in entry["extracted_fields"]["formatted_citation"]
    assert "title" not in entry["extracted_fields"]


def test_apply_formatted_fields_leaves_the_reply_unchanged():
    # The caller's reply dict is read, never edited.
    entry = {"text": "Ruritania.", "extracted_fields": {}}
    reply = {"title": "[Title not available]", "formatted_citation": "[Title not available]."}
    snapshot = dict(reply)
    assert s5d.apply_formatted_fields(entry, reply) is False
    assert reply == snapshot
