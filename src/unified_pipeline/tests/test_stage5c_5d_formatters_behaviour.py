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

import io
import importlib
import inspect
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

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


# ---------------------------------------------------------------------------
# stage_5d: parse_llm_output
# ---------------------------------------------------------------------------

def test_5d_parse_llm_output_well_formed_direct_json():
    id_map = {"CIT-0001": {}, "CIT-0002": {}}
    llm_output = json.dumps({
        "CIT-0001": {"formatted_citation": "Smith JA. Title one. J Med. 2020."},
        "CIT-0002": {"formatted_citation": "Jones MB. Title two. J Med. 2021."},
    })
    parsed = s5d.parse_llm_output(llm_output, id_map, verbose=False)
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
    parsed = s5d.parse_llm_output(llm_output, id_map, verbose=False)
    assert "CIT-0001" in parsed
    assert "CIT-9999" not in parsed


def test_5d_parse_llm_output_missing_id_is_simply_absent():
    id_map = {"CIT-0001": {}, "CIT-0002": {}}
    llm_output = json.dumps({"CIT-0001": {"formatted_citation": "only this came back"}})
    parsed = s5d.parse_llm_output(llm_output, id_map, verbose=False)
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
    parsed = s5d.parse_llm_output(llm_output, id_map, verbose=False)
    assert parsed["CIT-0001"]["formatted_citation"] == "extracted from prose"


def test_5d_parse_llm_output_malformed_json_returns_empty_dict():
    id_map = {"CIT-0001": {}}
    # Has a brace-delimited span but it is not valid JSON inside. verbose=True
    # to also exercise the warning-print branch on the way to the empty dict.
    parsed = s5d.parse_llm_output("{not: valid, json here}", id_map, verbose=True)
    assert parsed == {}


def test_5d_parse_llm_output_no_braces_at_all_returns_empty_dict():
    # Prose response with no JSON braces anywhere: the direct json.loads
    # fails, then re.search finds no {...} span, so parse_llm_output returns
    # empty without ever attempting a second json.loads.
    id_map = {"CIT-0001": {}}
    parsed = s5d.parse_llm_output("Sorry, I cannot format these citations.", id_map, verbose=False)
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
    parsed = s5d.parse_llm_output(llm_output, id_map, verbose=False)
    assert list(parsed.keys()) == ["CIT-0001"]


def test_5d_parse_llm_output_empty_string_returns_empty_dict():
    parsed = s5d.parse_llm_output("", {"CIT-0001": {}}, verbose=False)
    assert parsed == {}


# ---------------------------------------------------------------------------
# stage_5d: call_llm_formatter
# ---------------------------------------------------------------------------

def test_5d_call_llm_formatter_returns_text_and_usage_tuple(monkeypatch):
    monkeypatch.setattr(s5d, "call_llm", lambda **kw: _llm_result('{"CIT-0001": {}}'))
    text, usage = s5d.call_llm_formatter("raw content", verbose=False)
    assert text == '{"CIT-0001": {}}'
    assert usage["model"] == "sentinel-model"
    assert usage["total_tokens"] == 18


def test_5d_call_llm_formatter_exception_arm_returns_none_none(monkeypatch):
    def _boom(**kw):
        raise ValueError("bad request")

    monkeypatch.setattr(s5d, "call_llm", _boom)
    # verbose=True to also exercise the warning-print + traceback branch.
    text, usage = s5d.call_llm_formatter("raw content", verbose=True)
    assert text is None
    assert usage is None


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
    marker = f"/{total} ("
    progress = [line for line in out if "Processing batch" in line and marker in line]
    # The line is never "[N/M]" bracketed (that would match orchestrator.py
    # pattern 3) -- "Processing batch N/M (" is the exact shape checked.
    assert len(progress) == total
    nums = [int(line.split("Processing batch ")[1].split("/")[0]) for line in progress]
    assert nums == list(range(1, total + 1))
    # Each batch's block is one atomic print: "Processing batch..." then "Parsed...".
    for i, line in enumerate(out):
        if "Processing batch" in line and marker in line:
            assert out[i + 1].strip().startswith("Parsed"), out[i:i + 2]


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

    assert "Processing batch" in registered.getvalue()
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
    assert "Processing batch" in out
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
