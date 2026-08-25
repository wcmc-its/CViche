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
