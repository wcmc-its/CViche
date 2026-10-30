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
    # A line that shows a year, just not the record's, is misstated, not
    # missing its date: it is rejected rather than restored. 'tasting' is in
    # the source and no field, so the entry is not written from its fields
    # either (#1345) and renders unformatted.
    entries = [
        {"taxonomy_code": "K4", "element_idx_start": 7, "text": "Participant in Supper Club tasting 2004",
         "extracted_fields": {"activity_title": "Supper Club", "role": "Participant", "date": "2004"}},
        {"taxonomy_code": "K4", "element_idx_start": 9, "text": "2006 Kite Building Day",
         "extracted_fields": {"activity_title": "Kite Building Day", "date": "2006"}},
    ]
    reply = ('- [EC-0001] **2006** - Participant, "Supper Club"\n'
             "- [EC-0002] **2006** - Kite Building Day\n")
    out = _run_5c(tmp_path, monkeypatch, entries, reply)
    assert "formatted_text" not in out["entries"][0]["extracted_fields"]
    assert "formatting_source" not in out["entries"][0]["extracted_fields"]
    assert out["entries"][1]["extracted_fields"]["formatted_text"] == "**2006** - Kite Building Day"
    assert out["stage_5c"]["entries_formatted"] == 1
    assert out["stage_5c"]["entries_rejected"] == [
        {"element_idx_start": 7, "taxonomy_code": "K4", "reason": "EC-0001:year_missing:2004",
         "renders": "unformatted"}]
    assert out["stage_5c"]["entries_date_restored"] == []


# ---------------------------------------------------------------------------
# stage_5c date restore (#1345): a line with no year gets its record's date
# ---------------------------------------------------------------------------

_SUPPER_CLUB = {"activity_title": "Supper Club", "role": "Participant", "date": "2004"}
_SUPPER_CLUB_SOURCE = "Participant in Supper Club 2004"


def test_5c_restore_puts_the_record_date_in_front_of_a_line_with_no_year():
    # XWNZWW 505/507, XELRLZ K4 674/676/678: every word right, the year left out.
    assert s5c.restore_missing_date('Participant, "Supper Club"', _SUPPER_CLUB, _SUPPER_CLUB_SOURCE) \
        == '**2004** - Participant, "Supper Club"'


def test_5c_restore_sends_a_start_end_range_as_the_line_would():
    record = {"teaching_role": "Simulation Lab Leader", "start_date": "1981", "end_date": "1990"}
    assert s5c.restore_missing_date("Simulation Lab Leader", record,
                                    "Simulation Lab Leader 1981-1990") \
        == "**1981-1990** - Simulation Lab Leader"


def test_5c_restore_leaves_a_line_that_shows_some_other_year():
    assert s5c.restore_missing_date('**2006** - Participant, "Supper Club"', _SUPPER_CLUB,
                                    _SUPPER_CLUB_SOURCE) is None
    # A year the source does hold: putting the range in front would pass the
    # post-check, but the line misstated its dates, it did not leave them out.
    record = {"teaching_role": "Simulation Lab Leader", "start_date": "1981", "end_date": "1990"}
    assert s5c.restore_missing_date("**1990** - Simulation Lab Leader", record,
                                    "Simulation Lab Leader 1981-1990") is None


def test_5c_restore_leaves_a_record_with_no_date():
    assert s5c.restore_missing_date('"Supper Club"', {"activity_title": "Supper Club"},
                                    _SUPPER_CLUB_SOURCE) is None


def test_5c_restore_leaves_a_line_whose_words_are_another_entrys():
    # EQGGRB-06: a line shifted onto the wrong id; a date in front would hide it.
    line = "Faculty Advisor, Springboard Mentoring Program (Health Sciences)"
    assert s5c._words_in_source(line, _SUPPER_CLUB, _SUPPER_CLUB_SOURCE) \
        < s5c._RESTORE_MIN_WORDS_IN_SOURCE
    assert s5c.restore_missing_date(line, _SUPPER_CLUB, _SUPPER_CLUB_SOURCE) is None


def test_5c_restore_needs_the_whole_postcheck_to_pass():
    # OIYKZE 274 on the old prompt: no year AND an invented 'Attendee'.
    record = {"activity_title": "Rope Knots", "role": None, "date": "2015"}
    assert s5c.restore_missing_date('Attendee, "Rope Knots"', record,
                                    'Doe, J. (2015). "Rope Knots" workshop') is None


def test_run_stage_5c_stores_a_restored_line_and_records_it(tmp_path, monkeypatch):
    entries = [
        {"taxonomy_code": "K4", "element_idx_start": 7, "text": _SUPPER_CLUB_SOURCE,
         "extracted_fields": dict(_SUPPER_CLUB)},
        {"taxonomy_code": "K4", "element_idx_start": 9, "text": "2006 Kite Building Day",
         "extracted_fields": {"activity_title": "Kite Building Day", "date": "2006"}},
    ]
    reply = ('- [EC-0001] Participant, "Supper Club"\n'
             "- [EC-0002] **2006** - Kite Building Day\n")
    out = _run_5c(tmp_path, monkeypatch, entries, reply)
    fields = out["entries"][0]["extracted_fields"]
    assert fields["formatted_text"] == '**2004** - Participant, "Supper Club"'
    assert fields["formatting_source"] == "stage_5c_llm"
    assert out["stage_5c"]["entries_formatted"] == 2
    assert out["stage_5c"]["entries_rejected"] == []
    assert out["stage_5c"]["entries_date_restored"] == [
        {"element_idx_start": 7, "taxonomy_code": "K4", "ids": ["EC-0001"]}]


def test_run_stage_5c_restores_one_record_of_a_multi_record_entry(tmp_path, monkeypatch):
    reply = ("- [EC-0001] Simulation Lab Leader\n"
             "- [EC-0002] **1995-2001** - Simulation Lab Leader\n")
    out = _run_5c(tmp_path, monkeypatch, [_two_record_entry()], reply)
    assert out["entries"][0]["extracted_fields"]["formatted_text"] == \
        "**1981-1990** - Simulation Lab Leader\n**1995-2001** - Simulation Lab Leader"
    assert out["stage_5c"]["entries_date_restored"][0]["ids"] == ["EC-0001"]


# ---------------------------------------------------------------------------
# stage_5c fields line (#1345): a rejected entry is written from its stage-4
# fields, not left to render as its raw source line
# ---------------------------------------------------------------------------

def test_5c_fields_line_takes_template_d_with_an_open_end_written_present():
    # UXBHHF K4 196: rejected for an invented 'Attendee'; stage 4 has no role.
    record = {"activity_title": "Noon Kite Series", "role": None,
              "start_date": "2009", "end_date": "current"}
    assert s5c.fields_line(record, "14. Noon Kite Series: 2009-current") \
        == "**2009-Present** - Noon Kite Series"


def test_5c_fields_line_writes_a_role_only_when_the_source_states_it():
    record = {"activity_title": "Rope Knots", "role": "Presenter", "institution": "Sample Hall",
              "date": "1991-06-05"}
    # KHXOUF 1070 shape: stage 4 supplied 'Presenter' for a source that says "presented".
    assert s5c.fields_line(record, '"Rope Knots", presented to Sample Hall, June 5, 1991.') \
        == "**1991-06-05** - Rope Knots (Sample Hall)"
    assert s5c.fields_line(record, 'Presenter: "Rope Knots", Sample Hall, June 5, 1991.') \
        == "**1991-06-05** - Presenter, Rope Knots (Sample Hall)"


def test_5c_fields_line_puts_places_in_the_prompts_order_and_skips_one_the_title_names():
    record = {"activity_title": "Pond Day", "audience": "3rd grade", "location": "Elm School",
              "date": "2015"}
    assert s5c.fields_line(record, "2015 Pond Day, 3rd grade, Elm School") \
        == "**2015** - Pond Day (3rd grade; Elm School)"
    record = {"program_name": "SAMPLE Journal Club", "institution": "SAMPLE", "role": "Director",
              "start_date": "2012", "end_date": "2018"}
    assert s5c.fields_line(record, "e. Director, SAMPLE Journal Club (2012-2018)") \
        == "**2012-2018** - Director, SAMPLE Journal Club"


def test_5c_fields_line_writes_a_k1_course_code_with_its_title():
    record = {"course_code": "BIO 101", "course_title": "Pond Ecology", "date": "2010"}
    assert s5c.fields_line(record, "2010 BIO 101 Pond Ecology") == "**2010** - BIO 101: Pond Ecology"
    # A course title with spaces around it keeps its code.
    record = {"course_code": "MED 101", "course_title": "Anatomy ", "date": "2015"}
    assert s5c.fields_line(record, "MED 101 Anatomy, 2015") == "**2015** - MED 101: Anatomy"


def test_5c_fields_line_is_none_for_a_start_with_no_end():
    # "2009-" (open) and "2009" (one year) are both kept as a start alone;
    # writing "2009" would close an open range.
    assert s5c.fields_line({"activity_title": "Kite seminar", "start_date": "2009"},
                           "2009- Kite seminar") is None


def test_5c_fields_line_is_none_for_a_place_with_its_own_parentheses():
    # DPEHSZ 292 shape: the place's parentheses would nest inside the line's.
    record = {"activity_title": "Kite Talk", "date": "2016-02",
              "institution": "Sample Medical School (KT208 to kite fellows)"}
    assert s5c.fields_line(record, "2/2016 Kite Talk, Sample Medical School "
                                   "(KT208 to kite fellows)") is None


def test_5c_fields_line_is_none_without_a_title_or_with_a_yearless_date():
    # K2 has no title field (XAYKXA 192).
    assert s5c.fields_line({"teaching_role": "Preceptor", "start_date": "2009"},
                           "Preceptor 2009") is None
    # FINSIS 453: stage 4 kept the source's "11/18" as "11-18".
    assert s5c.fields_line({"activity_title": "Kite Event", "date": "11-18"},
                           "11/18 - Kite Event") is None


def test_5c_fields_text_writes_each_record_of_a_multi_record_entry():
    # UXBHHF K4 89 shape: two seminars fused in one raw bullet, 5c's line
    # rejected for an invented 'Participant'.
    records = [{"activity_title": "Kite Physics", "role": "Professional seminar", "date": "2011"},
               {"activity_title": "Pond Optics", "role": "Professional seminar", "date": "2013"}]
    entry = {"text": '4. Professional seminar: "Kite Physics (2011)", "Pond Optics (2013)"',
             "extracted_fields": {**records[1], "stage4_records": records}}
    lines = s5c.fields_text(entry)
    assert lines == ("**2011** - Professional seminar, Kite Physics\n"
                     "**2013** - Professional seminar, Pond Optics")
    # UXBHHF 89 itself says "seminars ... and": the lines say 'seminar' and
    # no 'and', so the raw line renders.
    entry["text"] = '4. Professional seminars: "Kite Physics (2011)" and "Pond Optics (2013)"'
    assert s5c._word_mismatch(lines, entry["text"]) == (["and", "seminars"], ["seminar"])
    assert s5c.fields_text(entry) is None


def test_5c_fields_text_needs_every_source_word():
    # VYRDHN 224 shape: the series name is only in the source text.
    entry = {"text": "Kite Grand Rounds, 02/07/07 - 'Rope Knots'.",
             "extracted_fields": {"activity_title": "Rope Knots", "date": "2007-02-07"}}
    assert s5c._word_mismatch(s5c.fields_line(entry["extracted_fields"], entry["text"]),
                              entry["text"]) == (["grand", "kite", "rounds"], [])
    assert s5c.fields_text(entry) is None


def test_5c_fields_text_matches_whole_words_of_any_length_both_ways():
    # GKAQHB K5 166 shape: 'stem' is inside 'system', 'day' and 'for' are
    # short, and the audience field adds 'students', which the source lacks.
    entry = {"text": "2015 What is the kite system? STEM day for 3rd-5th grade, Elm School",
             "extracted_fields": {"activity_title": "What is the kite system?",
                                  "audience": "3rd-5th grade students", "location": "Elm School",
                                  "date": "2015"}}
    line = s5c.fields_line(entry["extracted_fields"], entry["text"])
    assert line == "**2015** - What is the kite system? (3rd-5th grade students; Elm School)"
    assert s5c._word_mismatch(line, entry["text"]) == (["day", "for", "stem"], ["students"])
    assert s5c.fields_text(entry) is None
    # A negation and an acronym are words too.
    for text, title in (("Not a keynote: Kite Talk, 2015", "keynote: Kite Talk"),
                        ("PBL Kite Rounds 2015", "Kite Rounds"),
                        ("KITE 5210 Kite Medicine 2015", "Kite Medicine")):
        entry = {"text": text, "extracted_fields": {"activity_title": title, "date": "2015"}}
        assert s5c.fields_text(entry) is None, text
    # KHXOUF 1089 shape: a word counts as often as it occurs, so the 'and'
    # between two dates is not found in the title's 'and'.
    assert s5c._word_mismatch("**1987-11-1988-01** - Kites and Ponds",
                              "Kites and Ponds, November 1987 and January 1988") == (["and"], [])


def test_5c_fields_text_needs_the_source_dates():
    # BZZNRL 289 shape: years only the source shows would be lost.
    entry = {"text": "Kite autopsy, 2011, 2013", "extracted_fields": {"activity_title": "Kite autopsy"}}
    assert s5c.fields_line(entry["extracted_fields"], entry["text"]) == "Kite autopsy"
    assert s5c.fields_text(entry) is None
    # So would one of two years when the record holds only the other.
    entry = {"text": "Kite labs 2011, 2013",
             "extracted_fields": {"activity_title": "Kite labs", "date": "2011"}}
    assert s5c.fields_line(entry["extracted_fields"], entry["text"]) == "**2011** - Kite labs"
    assert s5c.fields_text(entry) is None
    # A date with no year ("8/14/156") is a date too.
    assert s5c._dates_differ("Kite Course", "Kite Course 8/14/156")
    # A range stands for the years the source lists one by one.
    assert not s5c._dates_differ("**1993-1995** - Kite labs", "Kite labs 1993, 1994, 1995")


def test_5c_fields_text_counts_month_names_and_open_ends_as_the_date():
    entry = {"text": '"Rope Knots." Kite Club. May 20, 2015. Elm Town.',
             "extracted_fields": {"activity_title": "Rope Knots.", "audience": "Kite Club",
                                  "location": "Elm Town", "date": "2015-05-20"}}
    assert s5c.fields_text(entry) == "**2015-05-20** - Rope Knots. (Kite Club; Elm Town)"


def test_5c_fields_text_shows_no_year_the_source_does_not():
    # RXYBVF 233: stage 4 read "2013-5" as December 2015.
    entry = {"text": "2013-5 Kite Genomics. Chicago, December.",
             "extracted_fields": {"activity_title": "Kite Genomics", "institution": "Chicago",
                                  "date": "2015-12"}}
    assert s5c.postcheck_line(s5c.fields_line(entry["extracted_fields"], entry["text"]),
                              entry["extracted_fields"], entry["text"]) is None
    assert s5c.fields_text(entry) is None


def test_5c_fields_text_needs_every_line_to_pass_the_postcheck():
    # Stage 4's scalars hold the first of two dated activities: the line
    # writes one date, and the post-check holds it to both (DPEHSZ 269).
    fields = {"activity_title": "Kite Club Day", "date": "2007",
              "activities": [{"activity_title": "Kite Club Day", "date": "2007"},
                             {"activity_title": "Kite Club", "date": "2008"}]}
    entry = {"text": "2007 Kite Club Day; 2008 Kite Club", "extracted_fields": fields}
    line = s5c.fields_line(fields, entry["text"])
    assert s5c.postcheck_line(line, fields, entry["text"]) == "year_missing:2008"
    assert s5c.fields_text(entry) is None
    # Its words and dates all match the source, but it echoes the prompt's
    # `Original:` context line.
    entry = {"text": "Original: Kite Day", "extracted_fields": {"activity_title": "Original: Kite Day"}}
    assert s5c.fields_line(entry["extracted_fields"], entry["text"]) == "Original: Kite Day"
    assert s5c.fields_text(entry) is None


def test_run_stage_5c_writes_a_rejected_entry_from_its_fields(tmp_path, monkeypatch):
    # UXBHHF K4 197: the model wrote 'Attendee', which the source never says.
    entries = [{"taxonomy_code": "K4", "element_idx_start": 197,
                "text": "15. Annual Kite Rounds 2013 - current",
                "extracted_fields": {"activity_title": "Annual Kite Rounds", "role": None,
                                     "start_date": "2013", "end_date": "current"}}]
    out = _run_5c(tmp_path, monkeypatch, entries,
                  "- [EC-0001] **2013-Present** - Attendee, Annual Kite Rounds\n")
    fields = out["entries"][0]["extracted_fields"]
    assert fields["formatted_text"] == "**2013-Present** - Annual Kite Rounds"
    assert fields["formatting_source"] == s5c.FIELDS_SOURCE == "stage_4_fields"
    assert out["stage_5c"]["entries_formatted"] == 0
    assert out["stage_5c"]["entries_rejected"] == [
        {"element_idx_start": 197, "taxonomy_code": "K4", "reason": "EC-0001:role_invented:attendee",
         "renders": "stage_4_fields"}]


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


def test_5d_s3_label_covers_edited_books_as_well_as_authored() -> None:
    """#1706: an edited book is S3 (taxonomy_v7 "Books"), so the label
    stage 5d sends with an S3 citation must not say "authored" only."""
    raw, _id_map = s5d.build_raw_content(
        [{"taxonomy_code": "S3", "text": "an edited volume (Eds.)"}])
    assert "(S3: Book (authored or edited))" in raw
    assert "- S3: Book (authored or edited)\n" in s5d.CITATION_FORMATTER_PROMPT


_TAXONOMY_PATH = _SRC / "unified_pipeline" / "core" / "taxonomy_v7.json"

# #1723: the declared mapping from each code's taxonomy_v7.json label to the
# 5d label. Every term must appear in BOTH labels (case-insensitive), so a
# relabel on either side that names a different section fails the test.
_SHARED_LABEL_TERMS = {
    "S1": ("peer-reviewed research article",),
    "S2": ("review", "editorial"),
    "S3": ("book",),
    "S4": ("chapter",),
    "S5": ("non-peer-reviewed research publication",),
    "S6": ("case report",),
    "S7": ("in review", "submitted"),
    "S8": ("abstract", "conference proceeding"),
    "S9": ("other", "media"),
}


def _taxonomy_labels() -> dict[str, str]:
    taxonomy = json.loads(_TAXONOMY_PATH.read_text(encoding="utf-8"))
    return {entry["code"]: entry["label"] for entry in taxonomy["codes"]}


@pytest.mark.parametrize("code", s5d.PUBLICATION_CODES)
def test_5d_label_names_the_same_section_as_the_taxonomy(code) -> None:
    """#1723: S5 was "Monograph" (a book, S3) and S6 "Letter or
    correspondence" (S2), while taxonomy_v7 has S5 Non-peer-reviewed
    Research Publications and S6 Case Reports."""
    taxonomy_label = _taxonomy_labels()[code].lower()
    label = s5d.PUBLICATION_TYPE_DESCRIPTIONS[code]
    for term in _SHARED_LABEL_TERMS[code]:
        assert term in taxonomy_label, (code, term, taxonomy_label)
        assert term in label.lower(), (code, term, label)
    assert f"- {code}: {label}\n" in s5d.CITATION_FORMATTER_PROMPT


def test_5d_s5_and_s6_no_longer_carry_the_wrong_sections() -> None:
    raw, _id_map = s5d.build_raw_content([
        {"taxonomy_code": "S5", "text": "a technical report"},
        {"taxonomy_code": "S6", "text": "a case report"},
    ])
    assert "Monograph" not in raw and "Letter or correspondence" not in raw
    assert "Monograph" not in s5d.CITATION_FORMATTER_PROMPT
    assert "Letter or correspondence" not in s5d.CITATION_FORMATTER_PROMPT
    assert "(S6: Case report or case series)" in raw


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
    # The kept citation matches its own entry's words (#1632).
    id_map = {"CIT-0001": {"text": "Quill A. Lantern moths of Ruritania. J Fabl Lore. 2019."}}
    llm_output = json.dumps({
        "CIT-0001": {"formatted_citation": "Quill A. Lantern moths of Ruritania. J Fabl Lore. 2019."},
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
    id_map = {"CIT-0001": {"text": "Vexley B. Kept citation words here. Fabl Rev. 2004."}}
    llm_output = (
        "Here you go:\n"
        + json.dumps({
            "CIT-0001": {"formatted_citation": "Vexley B. Kept citation words here. Fabl Rev. 2004."},
            "CIT-4242": {"formatted_citation": "invented, must be filtered"},
        })
    )
    parsed = s5d.parse_llm_output(llm_output, id_map)
    assert list(parsed.keys()) == ["CIT-0001"]


# #1632: one entry answered as two ids moves every later citation onto the
# entry before its own. Synthetic entries; the shapes are JUTBUZ 524-530
# (shifted), BCTOGR/SPINJC (a trailing extra id, aligned) and a "b" id.
_SPLIT_BATCH = {
    "CIT-0001": {"text": "Quill A. Lantern moths of Ruritania. J Fabl Lore. 2019;4:10-12."},
    "CIT-0002": {"text": "Quill A, Zorbelt C. Pewter beetles in drizzle. Fabl Entomol. 2018. "
                         "Also: Quill A. Brine dune crickets. Fabl Entomol. 2017."},
    "CIT-0003": {"text": "Vexley B, Quill A. Velvet frogs and fog. Dune Herpetol. 2016;2:5."},
    "CIT-0004": {"text": "Quill A. Bramble wasps in drizzle. Fabl Fauna Bull. 2015."},
}
_SPLIT_CITATIONS = [
    "Quill A. Lantern moths of Ruritania. J Fabl Lore. 2019;4:10-12.",
    "Quill A, Zorbelt C. Pewter beetles in drizzle. Fabl Entomol. 2018.",
    "Quill A. Brine dune crickets. Fabl Entomol. 2017.",
    "Vexley B, Quill A. Velvet frogs and fog. Dune Herpetol. 2016;2:5.",
    "Quill A. Bramble wasps in drizzle. Fabl Fauna Bull. 2015.",
]


def _reply(ids_and_citations):
    return json.dumps({cid: {"formatted_citation": c} for cid, c in ids_and_citations})


def test_5d_parse_llm_output_drops_citations_a_split_reply_shifted():
    ids = [f"CIT-{n:04d}" for n in range(1, 6)]
    parsed = s5d.parse_llm_output(_reply(zip(ids, _SPLIT_CITATIONS, strict=True)), _SPLIT_BATCH)
    # CIT-0003 carries entry 2's second citation and CIT-0004 entry 3's:
    # both fall back to the stage-4 render. CIT-0005 was never sent.
    assert sorted(parsed) == ["CIT-0001", "CIT-0002"]
    assert parsed["CIT-0002"]["formatted_citation"] == _SPLIT_CITATIONS[1]


def test_5d_parse_llm_output_keeps_aligned_citations_beside_an_extra_id():
    aligned = list(zip(_SPLIT_BATCH, [_SPLIT_CITATIONS[i] for i in (0, 1, 3, 4)], strict=True))
    for extra_id in ("CIT-0005", "CIT-0002b"):
        reply = json.loads(_reply(aligned))
        reply[extra_id] = {"formatted_citation": _SPLIT_CITATIONS[2]}
        parsed = s5d.parse_llm_output(json.dumps(reply), _SPLIT_BATCH)
        assert {cid: f["formatted_citation"] for cid, f in parsed.items()} == dict(aligned)


def test_5d_parse_llm_output_checks_nothing_when_every_id_was_sent():
    # Without an unsent id the reply is taken as is, even a citation that
    # shares no word with its entry.
    reply = _reply([("CIT-0001", "Unrelated words entirely.")])
    assert s5d.parse_llm_output(reply, {"CIT-0001": {"text": "Quill A. Lantern moths."}}) \
        == {"CIT-0001": {"formatted_citation": "Unrelated words entirely."}}


def test_5d_parse_llm_output_split_reply_drops_a_reply_with_no_citation():
    reply = json.loads(_reply([("CIT-0001", _SPLIT_CITATIONS[0])]))
    reply["CIT-0002"] = {"authors": "Quill A, Zorbelt C"}
    reply["CIT-0003"] = "not an object"
    reply["CIT-0005"] = "not an object"
    parsed = s5d.parse_llm_output(json.dumps(reply), _SPLIT_BATCH)
    assert sorted(parsed) == ["CIT-0001"]


def test_5d_parse_llm_output_split_reply_drops_a_citation_a_neighbour_matches_better():
    # Same authors and venue: the moved citation holds most of its new
    # entry's words, but all of its own entry's.
    batch = {
        "CIT-0001": {"text": "Quill A, Zorbelt C. Pewter beetles drizzle. Fabl Entomol Glimmer."},
        "CIT-0002": {"text": "Quill A, Zorbelt C. Velvet frogs. Fabl Entomol Glimmer."},
    }
    moved = "Quill A, Zorbelt C. Pewter beetles drizzle. Fabl Entomol Glimmer."
    reply = _reply([("CIT-0001", moved), ("CIT-0002", moved), ("CIT-0003", "Quill A. Velvet frogs.")])
    assert sorted(s5d.parse_llm_output(reply, batch)) == ["CIT-0001"]


def test_5d_parse_llm_output_split_reply_drops_a_citation_no_entry_matches():
    # Its own entry is the best match, but holds too few of its words; a
    # citation of numbers alone has no words to match.
    reply = _reply([("CIT-0001", _SPLIT_CITATIONS[0]), ("CIT-0004", "Larkspur glimmer wobble tallies."),
                    ("CIT-0003", "2016;2:5."), ("CIT-0009", "never sent")])
    assert sorted(s5d.parse_llm_output(reply, _SPLIT_BATCH)) == ["CIT-0001"]


def test_5d_parse_llm_output_split_reply_keeps_a_citation_a_long_neighbour_holds_more_of():
    # The own entry spells its journal out, so it holds 5 of the citation's
    # 8 words; a long neighbour with the same author and topic holds 6. The
    # neighbour holds more of them but is far less like the citation
    # (6 of 20 words against 5 of 12), so the citation stays.
    batch = {
        "CIT-0001": {"text": "Quill A, Vexley B, Thistlewick C, Pennyworthe D, Mothwick E, Brambleton F, "
                             "Zorbelt G. Survey of lantern moths, lore and fog beetles across the northern "
                             "dunes of the valley. J Fabl Entomol. 2008;63(4):2211-19."},
        "CIT-0002": {"text": "Quill A. 2011. Lantern moths. Journal of Fabled Entomology and Lore of "
                             "Ruritania. 41(2):xvii-xx."},
    }
    own = "Quill A. Lantern moths. J Fabl Entomol Lore Rurit. 2004;41(2):xvii-xx."
    reply = _reply([("CIT-0001", batch["CIT-0001"]["text"]), ("CIT-0002", own), ("CIT-0001b", "extra")])
    parsed = s5d.parse_llm_output(reply, batch)
    assert sorted(parsed) == ["CIT-0001", "CIT-0002"]
    assert parsed["CIT-0002"]["formatted_citation"] == own


def test_5d_parse_llm_output_split_reply_keeps_one_of_several_citations_in_its_entry():
    # The own entry holds three citations, so a short neighbour sharing the
    # authors is more like the first one; but the own entry holds all of its
    # words and the neighbour 5 of 6, so it stays.
    batch = {
        "CIT-0001": {"text": "Quill A, Vexley B. Lantern moths in fog. Fabl Entomol. 2019. Also: "
                             "Thistlewick C. Bramble wasps of the northern dunes. Dune Fauna Bull. 2018. "
                             "Also: Pennyworthe D. Velvet frogs and beetles. Dune Herpetol. 2017."},
        "CIT-0002": {"text": "Quill A, Vexley B. Lantern glow. Fabl Entomol. 2015."},
    }
    first = "Quill A, Vexley B. Lantern moths in fog. Fabl Entomol. 2019."
    reply = _reply([("CIT-0001", first), ("CIT-0001b", "Thistlewick C. Bramble wasps."),
                    ("CIT-0002", batch["CIT-0002"]["text"])])
    parsed = s5d.parse_llm_output(reply, batch)
    assert sorted(parsed) == ["CIT-0001", "CIT-0002"]
    assert parsed["CIT-0001"]["formatted_citation"] == first


@pytest.mark.parametrize(("entry_text", "citation", "kept"), [
    ("Moth lore wing.", "Moth lore wing dust gale.", True),  # 3 of 5 words
    ("Moth lore.", "Moth lore wing dust.", True),  # 2 of 4: exactly the floor
    ("Moth lore.", "Moth lore wing dust gale.", False),  # 2 of 5
])
def test_5d_parse_llm_output_split_reply_needs_half_the_citation_words_in_its_entry(entry_text, citation, kept):
    reply = _reply([("CIT-0001", citation), ("CIT-0002", "never sent")])
    parsed = s5d.parse_llm_output(reply, {"CIT-0001": {"text": entry_text}})
    assert ("CIT-0001" in parsed) is kept


def test_5d_parse_llm_output_split_reply_matches_words_across_case_and_accents():
    batch = {"CIT-0001": {"text": "QUILL Å. Lántern Mõths Of Rüritania. Fäbl Lore. 2019."}}
    citation = "Quill A. Lantern moths of Ruritania. Fabl Lore. 2019."
    parsed = s5d.parse_llm_output(_reply([("CIT-0001", citation), ("CIT-0002", "never sent")]), batch)
    assert parsed == {"CIT-0001": {"formatted_citation": citation}}


def test_5d_citation_words_are_folded_letter_runs_of_four_or_more():
    assert s5d._citation_words("Ébène A, ed. Moth lore 2019;12 wing_dust ÅNGSTRÖM fog") \
        == frozenset({"ebene", "moth", "lore", "wing", "dust", "angstrom"})


def test_5d_word_likeness_is_shared_words_over_all_words():
    assert s5d._word_likeness(frozenset({"moth", "lore"}), frozenset({"lore", "wing"})) == pytest.approx(1 / 3)
    assert s5d._word_likeness(frozenset(), frozenset()) == 0.0


def test_5d_parse_llm_output_split_reply_logs_the_unsent_and_dropped_ids(caplog):
    ids = [f"CIT-{n:04d}" for n in range(1, 6)]
    with caplog.at_level(logging.WARNING, logger=s5d.logger.name):
        s5d.parse_llm_output(_reply(zip(ids, _SPLIT_CITATIONS, strict=True)), _SPLIT_BATCH)
    assert [r.getMessage() for r in caplog.records] == [
        "5d reply carried ids the batch never sent (CIT-0005); dropped 2 citation(s) that do not "
        "match their own entry: CIT-0003, CIT-0004"]


def test_run_stage_5d_keeps_the_stage4_render_of_a_shifted_entry(tmp_path, monkeypatch):
    entries = [{"taxonomy_code": "S1", "text": batch_entry["text"],
                "enrichment_status": "no_identifier", "extracted_fields": {}}
               for batch_entry in _SPLIT_BATCH.values()]
    input_path = _write_json(tmp_path / "in.json", {"document_uid": "PLACEHOLD", "entries": entries})
    output_path = str(tmp_path / "out.json")
    ids = [f"CIT-{n:04d}" for n in range(1, 6)]
    monkeypatch.setattr(s5d, "call_llm", lambda **kw: _llm_result(_reply(zip(ids, _SPLIT_CITATIONS, strict=True))))
    s5d.run_stage_5d(input_path, output_path=output_path, verbose=False)
    with open(output_path, encoding="utf-8") as f:
        out = json.load(f)
    citations = [e["extracted_fields"].get("formatted_citation") for e in out["entries"]]
    assert citations == [_SPLIT_CITATIONS[0], _SPLIT_CITATIONS[1], None, None]
    assert out["stage_5d"]["formatted_count"] == 2


def test_5d_parse_llm_output_empty_string_returns_empty_dict():
    parsed = s5d.parse_llm_output("", {"CIT-0001": {}})
    assert parsed == {}


# #2029 (KH2YMW): the reply answered one entry holding two citations with a
# list of two objects; dict() of that list raised and took down the stage.
_NON_OBJECT_ITEMS = [
    [{"formatted_citation": "Quill A. Moth lore. 2019."}, {"formatted_citation": "Quill A. Wing dust. 2020."}],
    "Quill A. Moth lore. 2019.",
    None,
]


@pytest.mark.parametrize("item", _NON_OBJECT_ITEMS, ids=["list", "string", "null"])
def test_5d_parse_llm_output_skips_and_logs_an_item_that_is_not_an_object(item, caplog):
    id_map = {"CIT-0001": {}, "CIT-0002": {}}
    reply = json.dumps({"CIT-0001": item, "CIT-0002": {"formatted_citation": "Brandt B. Fern reed. 2021."}})
    with caplog.at_level(logging.WARNING, logger=s5d.logger.name):
        parsed = s5d.parse_llm_output(reply, id_map)
    assert parsed == {"CIT-0002": {"formatted_citation": "Brandt B. Fern reed. 2021."}}
    assert [r.getMessage() for r in caplog.records] == [
        f"5d reply item CIT-0001 is a {type(item).__name__}, not an object; skipped it"]


def test_5d_parse_llm_output_a_whole_reply_that_is_not_an_object_parses_to_nothing(caplog):
    with caplog.at_level(logging.WARNING, logger=s5d.logger.name):
        parsed = s5d.parse_llm_output(json.dumps([{"CIT-0001": {"formatted_citation": "x"}}]), {"CIT-0001": {}})
    assert parsed == {}
    assert len(caplog.records) == 1


def test_run_stage_5d_formats_the_other_entries_when_one_item_is_not_an_object(tmp_path, monkeypatch):
    texts = ["Quill A. Moth lore. 2019. Quill A. Wing dust. 2020.", "Brandt B. Fern reed. 2021."]
    entries = [{"taxonomy_code": "S1", "text": text, "enrichment_status": "no_identifier", "extracted_fields": {}}
               for text in texts]
    input_path = _write_json(tmp_path / "in.json", {"document_uid": "PLACEHOLD", "entries": entries})
    output_path = str(tmp_path / "out.json")
    reply = json.dumps({"CIT-0001": _NON_OBJECT_ITEMS[0],
                        "CIT-0002": {"formatted_citation": "Brandt B. Fern reed. 2021.", "year": "2021"}})
    monkeypatch.setattr(s5d, "call_llm", lambda **kw: _llm_result(reply))
    s5d.run_stage_5d(input_path, output_path=output_path, verbose=False)
    with open(output_path, encoding="utf-8") as f:
        out = json.load(f)
    assert out["entries"][0]["extracted_fields"] == {}
    assert out["entries"][1]["extracted_fields"]["formatted_citation"] == "Brandt B. Fern reed. 2021."
    assert out["entries"][1]["extracted_fields"]["year"] == "2021"
    assert out["stage_5d"]["formatted_count"] == 1


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
    # that the run log still names the exception type at ERROR, while its
    # message and traceback go to DEBUG only (#639, CODING_STANDARDS §4.7).
    def flaky(**kwargs):
        prompt = kwargs["messages"][0]["content"]
        idx = _citation_index_from_prompt(prompt, _MANY_CITATIONS)
        if idx == 2:
            raise RuntimeError("simulated throttle")
        return _llm_result(json.dumps({"CIT-0001": {"formatted_citation": f"cite {idx}"}}))

    input_data = _many_citations_input("MANYCIT8")
    input_path = _write_json(tmp_path / "in.json", input_data)
    monkeypatch.setattr(s5d, "call_llm", flaky)

    with caplog.at_level(logging.DEBUG, logger="unified_pipeline.stage_5d_citation_formatter"):
        s5d.run_stage_5d(
            input_path, output_path=str(tmp_path / "out.json"), verbose=True, batch_size=1, workers=4
        )

    errors = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(r.getMessage() == "LLM formatting failed: RuntimeError" for r in errors), errors
    assert not any("simulated throttle" in r.getMessage() or r.exc_info for r in errors), errors
    debug = [r for r in caplog.records if r.levelno == logging.DEBUG and r.exc_info]
    assert any("simulated throttle" in str(r.exc_info[1]) for r in debug), debug


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


@pytest.mark.parametrize("field", ["editors", "publisher"])
def test_apply_formatted_fields_copies_editors_and_publisher_onto_an_entry_without_them(field):
    # #481: the reply's editors/publisher used to be dropped, so stage 6's
    # safety net had no value to place. Names are invented.
    entry = {"text": "Quill A. A chapter. In: Rook B, ed. Book. Ruritan Press; 2001.",
             "extracted_fields": {}}
    reply = {"formatted_citation": "Quill A. A chapter. In: Rook B, ed. Book. Ruritan Press; 2001.",
             "editors": "Rook B", "publisher": "Ruritan Press"}
    assert s5d.apply_formatted_fields(entry, reply) is True
    assert entry["extracted_fields"][field] == reply[field]


def test_apply_formatted_fields_keeps_stage_4s_editors_and_publisher():
    # Copied only where the entry has none, like every other copied field.
    entry = {"text": "Quill A. A chapter.",
             "extracted_fields": {"editors": "Brook Rook", "publisher": "Ruritan Press, Zenda"}}
    reply = {"formatted_citation": "Quill A. A chapter.", "editors": "Rook B",
             "publisher": "Ruritan"}
    s5d.apply_formatted_fields(entry, reply)
    assert entry["extracted_fields"]["editors"] == "Brook Rook"
    assert entry["extracted_fields"]["publisher"] == "Ruritan Press, Zenda"


def test_apply_formatted_fields_leaves_the_reply_unchanged():
    # The caller's reply dict is read, never edited.
    entry = {"text": "Ruritania.", "extracted_fields": {}}
    reply = {"title": "[Title not available]", "formatted_citation": "[Title not available]."}
    snapshot = dict(reply)
    assert s5d.apply_formatted_fields(entry, reply) is False
    assert reply == snapshot


# ---------------------------------------------------------------------------
# stage_5d: ungrounded_reason -- a citation must not add authors, initials
# or ordinals the source line lacks (#1570). Names are synthetic.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("source, citation, reason", [
    # FLBFRK 25: a bare initials token "BG" is joined to the next author.
    ("Ash, X, ZQ Birch, W Cole, BG, H Dale, M Eade, and D Fern. A study. Nat Imag (2026).",
     "Ash X, Birch ZQ, Cole W, Dale BG, Eade M, Fern D. A study. Nat Imag. 2026.",
     "author_4:initials_not_in_source"),
    # QQGKXR 481: an author the source gives no initial gains a neighbour's.
    ("Quill, F. Rook, A. Sable, M.E. Tarn. (November 2023). A poster. Imaginary Meeting.",
     "Quill F, Rook A, Sable M, Tarn M. A poster. Imaginary Meeting; 2023 Nov.",
     "author_4:initials_not_in_source"),
    # BCTOGR 1286: an author with no initial in the source gains one.
    ("Quill, Rook, EE and Sable, JA: A study. J Imag, 250:3199, 1983",
     "Quill D, Rook EE, Sable JA. A study. J Imag. 1983;250:3199.",
     "author_1:initials_not_in_source"),
    # XNWSZN 520: a given name becomes two initials.
    ("Quill, Anna K.; Rook, Sven. A method. J Imag 2003.",
     "Quill AK, Rook SR. A method. J Imag. 2003.",
     "author_2:initials_not_in_source"),
    # BCTOGR 2022: an author the source does not name at all.
    ("Quill, EE, Rook, TH, and Sable, J: A scale. Imag Clin 75:293, 1995",
     "Quill EE, Rook TH, Tarn JA. A scale. Imag Clin. 1995;75:293.",
     "author_3:surname_not_in_source"),
    # SIJYJZ 732: the CV's list number "10." becomes a meeting ordinal.
    ("10. Annual Meeting of the Society for Imaginary Studies, Springfield, 2004, \"A talk\".",
     "10th Annual Meeting of the Society for Imaginary Studies. A talk; 2004; Springfield.",
     "ordinal_not_in_source:10th"),
    # ECXGAT 859: a meeting the source does not name gains a numbered one.
    ("Quill A, Rook B. A sampler for viral aerosols.",
     "Quill A, Rook B. A sampler for viral aerosols. IAAR 34th Annual Conference; 2015.",
     "ordinal_not_in_source:34th"),
    # A list number before a meeting is not its ordinal ("48. Annual").
    ("48. Annual Meeting, Imaginary Society, 1986. A talk.",
     "Quill A. A talk. 48th Annual Meeting, Imaginary Society; 1986.",
     "ordinal_not_in_source:48th"),
    # A generational suffix the source does not give.
    ("Quill, W.J.A., Rook, B. A study.", "Quill WJA 2nd, Rook B. A study.",
     "ordinal_not_in_source:2nd"),
    # A surname particle does not hide the author from the check.
    ("van der Quill, A., Rook, B. A study.", "van der Quill AB, Rook B. A study.",
     "author_1:initials_not_in_source"),
    # A surname this short is never found inside another word.
    ("Quill A, Shuman B. A study.", "Quill A, Hu B. A study.", "author_2:surname_not_in_source"),
    # A closing "et al." does not hide the authors before it.
    ("Quill A, R. Rook, et al. A study.", "Quill AB, Rook R, et al. A study.",
     "author_1:initials_not_in_source"),
    # An exact surname is never read through a misspelt one beside it.
    ("Quill, M. and E.F. Quil. A study. J Imag 2001.",
     "Quill M, Quil M. A study. J Imag. 2001.",
     "author_2:initials_not_in_source"),
    # A near-but-different surname is another author: "Ashford" against the
    # source's "Ashworth" is 0.67 similar, under _SURNAME_FUZZY_MIN_RATIO.
    ("Quill, A, and Ashworth, B. A study.", "Quill A, Ashford B. A study.",
     "author_2:surname_not_in_source"),
])
def test_5d_ungrounded_reason_rejects_text_the_source_lacks(source, citation, reason):
    assert s5d.ungrounded_reason(citation, source) == reason


@pytest.mark.parametrize("source, citation", [
    # Initials before the surname, after it, or as given names.
    ("Ash, X, ZQ Birch, Jon Arne Cole. A study. Nat Imag (2026).",
     "Ash X, Birch ZQ, Cole JA. A study. Nat Imag. 2026."),
    ("Quill, J.A., Rook, B. A study. J Imag 2001.", "Quill JA, Rook B. A study. J Imag. 2001."),
    # Initials the source parts from their surname with a comma ("S, Rook").
    ("Quill A, S, Rook. A study.", "Quill A, Rook S. A study."),
    # Initials split by a comma, and a generational suffix between.
    ("Quill, C.,S., Rook Jr., J.M. and Sable, Jr, R. A study.", "Quill CS, Rook JM, Sable R. A study."),
    # Surname and initials written as one word, even where another word
    # holds the surname too; markers after the initials or by the name.
    ("Quill A, Cole-Rook B and RookJM. A study.", "Quill A, Cole-Rook B, Rook JM. A study."),
    ("Quill AEf, Rook JTr& and Sable BCRF. A study.", "Quill AE, Rook JT, Sable BC. A study."),
    ("Quill1 A, Rook 3B, and Sable PEJr. A study.", "Quill A, Rook B, Sable PE. A study."),
    # A surname glued to the word beside it, or behind a one-letter marker.
    ("Quill B, AnnaRook, and MSable W.D. A study.", "Quill B, Rook A, Sable WD. A study."),
    # A surname the source splits ("Mc Rook", "M.C. Rook") or misspells,
    # the first letter included ("Asable" for "Sable").
    ("Quill, A, M.C. Rookery, BL, and Sabel, JR. A study.", "Quill A, McRookery BL, Sable JR. A study."),
    ("Quill, A, and Asable, B. A study.", "Quill A, Sable B. A study."),
    # Accents, apostrophes, particles and hyphens.
    ("Quillé, A, D’Ash, B, van der Birch, C, Cole-Dale, E-F. A study.",
     "Quille A, D'Ash B, van der Birch C, Cole-Dale EF. A study."),
    # "et al." where the source has it; a generational suffix in the citation.
    ("Quill A, R. Rook II, et al. A study.", "Quill A, Rook R 2nd, et al. A study."),
    # An ordinal the source spells out, abbreviates, or numbers.
    ("Quill A (Ed). Imaginary Medicine, Third Edition. 1998.", "Quill A, ed. Imaginary Medicine. 3rd ed. 1998."),
    ("Quill A. Imaginary Medicine. 2ed. 2002.", "Quill A. Imaginary Medicine. 2nd ed. 2002."),
    ("Quill A, in Rook B (eds), Imaginary Medicine, ed 5. 1998.",
     "Quill A. In: Rook B, eds. Imaginary Medicine. 5th ed. 1998."),
    ("Quill A. Manual de Medicina, 2\u00aa Edici\u00f3n. 2017.", "Quill A. Manual de medicina. 2nd ed. 2017."),
    ("Quill A. A talk. 35rd Annual Meeting of the Imaginary Society, 1995.",
     "Quill A. A talk. 35th Annual Meeting of the Imaginary Society; 1995."),
    ("Quill A. A talk. Imaginary Society 124 Annual Meeting, 1996.",
     "Quill A. A talk. Imaginary Society 124th Annual Meeting; 1996."),
    ("Quill A. A talk. Thirty-Fourth Annual Meeting, 1999.", "Quill A. A talk. 34th Annual Meeting; 1999."),
    ("Quill A. A talk. 37th Annual Meeting of the Imaginary Society, 2004.",
     "Quill A. A talk. 37th Annual Meeting of the Imaginary Society; 2004."),
    # A group author makes the segment no Vancouver list: nothing is checked.
    ("WHO Writing Group. A report.", "WHO Writing Group B. A report."),
    ("Great Lakes Imaginary Science Forum. A talk.", "Great Lakes Imaginary Science Forum B. A talk."),
    # Accents fold away even on a surname too short to match loosely.
    ("Quill, A, \u00d1u, B. A study.", "Quill A, Nu B. A study."),
    ("Imaginary Writing Group. A study.", "Imaginary Writing Group, Quill A. A study."),
    # A citation that opens with its title has no author list to check.
    ("A Talk Given; Imaginary Society, 1986", "A talk given. Imaginary Society; 1986."),
])
def test_5d_ungrounded_reason_accepts_a_faithful_citation(source, citation):
    assert s5d.ungrounded_reason(citation, source) is None


def test_5d_ungrounded_reason_exempts_the_cv_owner():
    # A line of the owner's own CV that names nobody is still the owner's.
    owner = s5d.citation_owner({"first_name": "Ann", "middle_name": "B.", "last_name": "Qu\u00efll"})
    assert owner == s5d.CitationOwner("quill", "ab")
    citation = "Quill AB, Rook C. Letter to the editor. Imaginary Gazette. 1991."
    source = "Letter to the Editor, with C. Rook, Imaginary Gazette, 1991."
    assert s5d.ungrounded_reason(citation, source) == "author_1:surname_not_in_source"
    assert s5d.ungrounded_reason(citation, source, owner) is None
    assert s5d.ungrounded_reason("Quill A, Rook C. A letter.", source, owner) is None
    # Initials the owner does not have, and other authors, are still checked.
    assert s5d.ungrounded_reason("Quill AC, Rook C. A letter.", source, owner) == (
        "author_1:surname_not_in_source")
    assert s5d.ungrounded_reason("Quill AB, Rook CD. A letter.", source, owner) == (
        "author_2:initials_not_in_source")


@pytest.mark.parametrize("cv_owner", [None, "Ann Quill", {}, {"last_name": None}, {"last_name": ""}])
def test_5d_citation_owner_is_none_without_a_last_name(cv_owner):
    assert s5d.citation_owner(cv_owner) is None



@pytest.mark.parametrize(("own_text", "neighbour_text"), [
    # Both hold every word: a neighbour must hold MORE of them, so a tie keeps it.
    ("Moth lore wing dust gale fern reed.", "Moth lore wing dust."),
    # Both as like it (2 of 4 words against 3 of 6): a tie in likeness keeps it.
    ("Moth lore.", "Moth lore wing gale fern."),
])
def test_5d_parse_llm_output_split_reply_keeps_a_citation_a_neighbour_only_ties(own_text, neighbour_text):
    batch = {"CIT-0001": {"text": own_text}, "CIT-0002": {"text": neighbour_text}}
    reply = _reply([("CIT-0001", "Moth lore wing dust."), ("CIT-0001b", "never sent")])
    assert sorted(s5d.parse_llm_output(reply, batch)) == ["CIT-0001"]


def test_5d_parse_llm_output_split_reply_drops_a_citation_that_is_not_text():
    reply = _reply([("CIT-0001", ["Moth lore wing dust."]), ("CIT-0001b", "never sent")])
    assert s5d.parse_llm_output(reply, {"CIT-0001": {"text": "Moth lore wing dust."}}) == {}
