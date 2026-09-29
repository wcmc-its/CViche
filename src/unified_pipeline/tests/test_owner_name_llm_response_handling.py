"""LLM-response handling regressions for stage4/owner_name.py.

Three failure modes fixed together here:

1. `extract_cv_owner_name`'s except clause used to catch bare `Exception`,
   which silently converted a real bug (a future TypeError/AttributeError, or
   a bug inside `call_llm` itself) into an ordinary "surname fallback" as if
   it were an expected LLM/parsing failure. The except is now narrowed to
   `(json.JSONDecodeError, KeyError, ValidationError, *RETRYABLE_ERRORS)`;
   anything else must propagate.

2. `infer_cv_owner_location`'s `_query` helper parsed the LLM's location JSON
   without validating its shape. A syntactically-valid-but-wrong reply (e.g.
   "locations" as a bare string instead of a list -- the exact shape
   stage_5b_institution_enrichment.py's own defensive comment already
   anticipates: "primary_location is stored raw from the LLM ... and can come
   back as a bare string instead of the instructed object") is now rejected
   by a pydantic model and treated as "no location found" instead of being
   propagated to stage 5b / stage 6, which call `.get()`/iterate on it
   assuming the instructed shape.

3. `extract_cv_owner_name` itself parsed the LLM's name JSON the same
   unvalidated way (2) used to: `parsed.get('first_name', '').strip()`. A
   syntactically-valid reply with a null-valued field (e.g.
   `{"first_name": null, ...}` -- a plausible reply given the prompt's own
   "if you cannot determine a field, return an empty string" instruction,
   which an LLM can still answer with JSON `null` instead) raised an
   uncaught AttributeError on `.strip()` and failed the whole stage 4 run,
   where the pre-#643 code degraded to the uid-based surname fallback. Now
   validated via `_OwnerNameResponse`, mirroring (2)'s
   `_LocationInferenceResponse` pattern, so this degrades the same way.

Self-contained: `call_llm` stubbed at the module attribute, no Bedrock/OpenAI.
"""

import json
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import unified_pipeline.stage4.owner_name as owner_name  # noqa: E402


def _llm_result(content, total_tokens=10, cost=0.001):
    return {"content": content, "total_tokens": total_tokens, "cost": cost}


# ---------------------------------------------------------------------------
# extract_cv_owner_name: except-clause narrowing
# ---------------------------------------------------------------------------

def test_owner_name_extraction_lets_a_real_bug_propagate(monkeypatch):
    """A programming error inside call_llm must NOT be relabeled as an LLM hiccup."""

    def boom(**kwargs):
        raise TypeError("simulated bug: unexpected keyword argument")

    monkeypatch.setattr(owner_name, "call_llm", boom)

    with pytest.raises(TypeError):
        owner_name.extract_cv_owner_name(
            "2024_Test_CV", [{"text": "John Smith, MD, Professor of Surgery"}]
        )


def test_owner_name_extraction_still_falls_back_on_malformed_json(monkeypatch):
    """An expected LLM/parsing failure still degrades to the uid fallback."""

    monkeypatch.setattr(owner_name, "call_llm", lambda **kwargs: _llm_result("not valid json"))

    result = owner_name.extract_cv_owner_name(
        "2024_Test_CV", [{"text": "John Smith, MD, Professor of Surgery"}]
    )

    assert result["last_name"] == "Test"


def test_owner_name_extraction_falls_back_on_null_valued_field(monkeypatch):
    """A syntactically-valid reply with a null field must not raise.

    The pre-fix code ran `parsed.get('first_name', '').strip()` -- `.get`
    only supplies the default for a *missing* key, so a key present with
    JSON `null` reached `.strip()` on `None` and raised an uncaught
    AttributeError, failing the whole stage 4 run instead of degrading to
    the uid-based surname fallback.
    """
    null_field_reply = json.dumps({
        "first_name": None,
        "middle_name": "",
        "last_name": "Smith",
        "suffix": "",
        "full_name": "John Smith",
        "full_name_with_credentials": "",
    })
    monkeypatch.setattr(owner_name, "call_llm", lambda **kwargs: _llm_result(null_field_reply))

    result = owner_name.extract_cv_owner_name(
        "2024_Test_CV", [{"text": "John Smith, MD, Professor of Surgery"}]
    )

    assert result["last_name"] == "Test"
    assert result["first_name"] == ""


# ---------------------------------------------------------------------------
# infer_cv_owner_location: response-shape validation
# ---------------------------------------------------------------------------

_POSITION_ENTRY = {
    "taxonomy_code": "D1",
    "text": "Professor of Surgery, Weill Cornell Medicine",
    "extracted_fields": {
        "title": "Professor of Surgery",
        "institution": "Weill Cornell Medicine",
        "end_date": "present",
    },
}


def test_malformed_locations_shape_falls_back_instead_of_propagating(monkeypatch):
    """{"locations": "Boston"} (string, not list) must not reach `result`."""

    malformed = json.dumps({
        "locations": "Boston",
        "metro_area": "New York City",
        "primary_location": {
            "institution": "Weill Cornell Medicine",
            "city": "New York",
            "state": "NY",
            "country": "USA",
            "confidence": 0.95,
        },
    })
    monkeypatch.setattr(owner_name, "call_llm", lambda **kwargs: _llm_result(malformed))

    result = owner_name.infer_cv_owner_location([_POSITION_ENTRY])

    assert result["inference_success"] is False
    assert result["locations"] == []
    assert result["primary_location"] is None


def test_wellformed_location_response_is_still_accepted(monkeypatch):
    """The validator must not reject a response that matches the instructed shape."""

    good = json.dumps({
        "locations": [{
            "institution": "Weill Cornell Medicine",
            "city": "New York",
            "state": "NY",
            "country": "USA",
            "confidence": 0.95,
        }],
        "metro_area": "New York City",
        "primary_location": {
            "institution": "Weill Cornell Medicine",
            "city": "New York",
            "state": "NY",
            "country": "USA",
            "confidence": 0.95,
        },
    })
    monkeypatch.setattr(owner_name, "call_llm", lambda **kwargs: _llm_result(good))

    result = owner_name.infer_cv_owner_location([_POSITION_ENTRY])

    assert result["inference_success"] is True
    assert result["primary_location"]["city"] == "New York"
    assert result["locations"][0]["institution"] == "Weill Cornell Medicine"


def test_location_inference_llm_outage_propagates_instead_of_falling_back(monkeypatch):
    """A provider outage past the budget fails the run (#810); a plain error
    still falls back to an unsuccessful inference."""
    from unified_pipeline.llm.retry import LLMOutageError

    def outage(**kwargs):
        raise LLMOutageError("provider down", seconds_waited=1800.0)

    monkeypatch.setattr(owner_name, "call_llm", outage)
    with pytest.raises(LLMOutageError):
        owner_name.infer_cv_owner_location([_POSITION_ENTRY])

    def blip(**kwargs):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(owner_name, "call_llm", blip)
    assert owner_name.infer_cv_owner_location([_POSITION_ENTRY])["inference_success"] is False


# ---------------------------------------------------------------------------
# extract_cv_owner_name: body-tier window on narrative CVs (#457)
# ---------------------------------------------------------------------------

_INVENTED_NAME = "Quillon Vantrell"
_NARRATIVE_FILLER = (
    " has led a translational program for two decades and describes the work "
    "in continuous prose rather than in short header lines."
)


def _name_from_prompt_llm(prompts):
    """Stub call_llm: records each prompt and answers with the invented name
    only if the prompt's Content block actually contains it."""

    def fake(**kwargs):
        prompt = kwargs["messages"][0]["content"]
        prompts.append(prompt)
        if _INVENTED_NAME in prompt:
            return _llm_result(json.dumps({
                "first_name": "Quillon", "last_name": "Vantrell",
                "full_name": _INVENTED_NAME,
            }))
        return _llm_result(json.dumps({}))

    return fake


def _narrative_entries():
    """Every entry is over the 500-char cap, as stage 2 emits for a prose CV."""
    return [
        {"text": _INVENTED_NAME + _NARRATIVE_FILLER * 6},
        {"text": "Later paragraph." + _NARRATIVE_FILLER * 6},
    ]


def test_narrative_cv_with_only_long_entries_still_gets_a_name(monkeypatch):
    prompts = []
    monkeypatch.setattr(owner_name, "call_llm", _name_from_prompt_llm(prompts))
    entries = _narrative_entries()
    assert all(len(e["text"]) > owner_name.OWNER_NAME_ENTRY_MAX_CHARS for e in entries)

    result = owner_name.extract_cv_owner_name("web000", entries)

    assert len(prompts) == 1, "the body tier must run on a narrative CV"
    assert result["last_name"] == "Vantrell"
    assert result["full_name"] == _INVENTED_NAME


def test_narrative_window_is_truncated_not_passed_whole(monkeypatch):
    prompts = []
    monkeypatch.setattr(owner_name, "call_llm", _name_from_prompt_llm(prompts))
    entries = _narrative_entries()

    owner_name.extract_cv_owner_name("web000", entries)

    content = prompts[0].split("Content:\n", 1)[1].split("\n\nReturn JSON", 1)[0]
    assert [len(line) for line in content.split("\n")] == [
        owner_name.OWNER_NAME_ENTRY_MAX_CHARS
    ] * len(entries)


def test_window_with_a_short_entry_still_skips_long_ones(monkeypatch):
    """The pre-#457 window is preserved whenever it selects anything."""
    prompts = []
    monkeypatch.setattr(owner_name, "call_llm", _name_from_prompt_llm(prompts))
    entries = [
        {"text": "Curriculum Vitae"},
        {"text": "LONGENTRYMARKER" + _NARRATIVE_FILLER * 6},
    ]

    owner_name.extract_cv_owner_name("web000", entries)

    assert "Curriculum Vitae" in prompts[0]
    assert "LONGENTRYMARKER" not in prompts[0]


def test_only_blank_entries_still_skip_the_llm_call(monkeypatch):
    prompts = []
    monkeypatch.setattr(owner_name, "call_llm", _name_from_prompt_llm(prompts))

    result = owner_name.extract_cv_owner_name("web000", [{"text": "  "}, {}])

    assert prompts == []
    assert result["last_name"] == ""


def _window_lines(prompts):
    return prompts[0].split("Content:\n", 1)[1].split("\n\nReturn JSON", 1)[0].split("\n")


def test_entry_at_the_char_cap_is_long_and_one_under_is_short(monkeypatch):
    """Pins the `<` boundary: a 499-char entry is a header-style line (kept
    as-is, long entries skipped); a 500-char one is long."""
    cap = owner_name.OWNER_NAME_ENTRY_MAX_CHARS
    prompts = []
    monkeypatch.setattr(owner_name, "call_llm", _name_from_prompt_llm(prompts))

    owner_name.extract_cv_owner_name(
        "web000", [{"text": "s" * (cap - 1)}, {"text": "L" * cap}]
    )
    assert _window_lines(prompts) == ["s" * (cap - 1)]

    prompts.clear()
    owner_name.extract_cv_owner_name("web000", [{"text": "L" * cap}, {"text": "M" * cap}])
    assert _window_lines(prompts) == ["L" * cap, "M" * cap]


def test_window_reads_at_most_the_first_twelve_entries(monkeypatch):
    prompts = []
    monkeypatch.setattr(owner_name, "call_llm", _name_from_prompt_llm(prompts))
    entries = [{"text": f"Line{i}"} for i in range(owner_name.OWNER_NAME_WINDOW_ENTRIES + 3)]

    owner_name.extract_cv_owner_name("web000", entries)

    # the prompt itself is further capped to 10 lines (first_entries[:10]);
    # entries 12+ must not be reachable through the window either way.
    assert _window_lines(prompts) == [f"Line{i}" for i in range(10)]
    assert owner_name._owner_name_window(entries) == [
        f"Line{i}" for i in range(owner_name.OWNER_NAME_WINDOW_ENTRIES)
    ]


def test_narrative_body_name_outranks_side_channel_name(monkeypatch, tmp_path):
    """Precedence change from #457, pinned. On dev a narrative CV had an empty
    window, skipped the body tier, and went straight to the #456 side channel.
    Now the body tier runs first on the truncated prose, and any last_name it
    returns means the side channel is never consulted."""
    body_person = "Ardwin Selcombe"  # e.g. a mentor named in the prose
    header_person = "Marisol Trenholt"  # the real owner, in the page header
    prompts = []

    def fake(**kwargs):
        prompt = kwargs["messages"][0]["content"]
        prompts.append(prompt)
        person = body_person if body_person in prompt else header_person
        first, last = person.split()
        return _llm_result(json.dumps(
            {"first_name": first, "last_name": last, "full_name": person}
        ))

    docx = tmp_path / "cv.docx"
    docx.write_bytes(b"only Path.is_file() is checked")
    monkeypatch.setattr(owner_name, "call_llm", fake)
    monkeypatch.setattr(
        owner_name, "extract_owner_side_channel",
        lambda path: {"sdt_lines": [], "header_lines": [header_person], "footer_lines": []},
    )
    entries = [{"text": "Trained under " + body_person + _NARRATIVE_FILLER * 6}]

    result = owner_name.extract_cv_owner_name("web000", entries, docx_path=str(docx))

    assert result["full_name"] == body_person
    assert len(prompts) == 1, "the side channel is not reached once the body tier names anyone"
