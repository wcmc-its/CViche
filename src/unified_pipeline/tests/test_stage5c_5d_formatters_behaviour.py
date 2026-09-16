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

import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline import stage_5c_teaching_formatter as s5c
from unified_pipeline import stage_5d_citation_formatter as s5d


def _llm_result(content: str, model: str = "sentinel-model") -> dict:
    return {
        "content": content,
        "prompt_tokens": 11,
        "completion_tokens": 7,
        "total_tokens": 18,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "cost": 0.002,
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
