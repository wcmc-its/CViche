"""
Tests for Stage 4 field-value type coercion (coerce_field_value_types).

Stage 4 stores raw LLM JSON, so a field the prompt asks for as a string can come
back as a list of strings. Downstream stages call string/number operations on the
value and crash the whole run. These tests pin the conservative coercion contract:
list-of-scalars is joined to a string; everything else (numbers, dicts,
list-of-dicts, None, strings) is left untouched.

Fast unit tests -- no LLM calls, no DB, no sample CV.
"""
import re
import sys
from pathlib import Path

import pytest

# Add src/ to path so unified_pipeline is importable.
# tests/ -> backend/ -> web_interface/ -> project_root/ -> src/
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))

from unified_pipeline.stage_4_field_extractor import coerce_field_value_types


def test_list_of_strings_is_joined():
    out = coerce_field_value_types({
        "narrative": ["Adverse Drug Events in Pediatrics", "Pediatric Residency Training"],
        "training_type": ["Cytopathology fellow", "Pathology chief resident"],
    })
    assert out["narrative"] == "Adverse Drug Events in Pediatrics; Pediatric Residency Training"
    assert out["training_type"] == "Cytopathology fellow; Pathology chief resident"


def test_numeric_fields_stay_numeric():
    # year/volume are legitimately numeric -- coercion must not stringify them,
    # or downstream int comparisons / format specs would break.
    out = coerce_field_value_types({"year": 2024, "volume": 12, "issue": 3})
    assert out["year"] == 2024 and isinstance(out["year"], int)
    assert out["volume"] == 12 and isinstance(out["volume"], int)
    assert out["issue"] == 3 and isinstance(out["issue"], int)


def test_structured_values_are_left_untouched():
    # list-of-dicts (e.g. locations) and dicts are structured -- their consumers
    # expect the shape, so coercion must skip them.
    locations = [{"city": "NYC"}, {"city": "Boston"}]
    primary = {"institution": "WCM", "city": "NYC"}
    out = coerce_field_value_types({"locations": locations, "primary_location": primary})
    assert out["locations"] == locations
    assert out["primary_location"] == primary


def test_none_and_plain_string_untouched():
    out = coerce_field_value_types({"degree": None, "title": "A single string"})
    assert out["degree"] is None
    assert out["title"] == "A single string"


def test_list_drops_empty_and_none_items():
    out = coerce_field_value_types({"specialty": ["Cytopathology", "", None, "Pathology"]})
    assert out["specialty"] == "Cytopathology; Pathology"


def test_empty_list_becomes_empty_string():
    assert coerce_field_value_types({"x": []})["x"] == ""


def test_numbers_in_list_are_stringified_in_join():
    # A scalar list may contain numbers; they should join without crashing.
    assert coerce_field_value_types({"codes": [1, 2, 3]})["codes"] == "1; 2; 3"


def test_non_dict_input_raises_type_error():
    # Declared return type is Dict[str, Any] -- silently handing back a
    # non-dict input would violate that contract for any caller that skips
    # its own type check. Callers on an untrusted-JSON boundary (e.g. LLM
    # output) must validate/guard before calling this function.
    with pytest.raises(TypeError):
        coerce_field_value_types(None)
    with pytest.raises(TypeError):
        coerce_field_value_types("not a dict")
    with pytest.raises(TypeError):
        coerce_field_value_types(["not", "a", "dict"])


def test_coerced_values_survive_downstream_operations():
    # Regression for the actual crash sites: after coercion these must not raise.
    out = coerce_field_value_types({
        "narrative": ["bullet one", "bullet two"],          # stage_6 .strip()/.lower()
        "training_type": ["fellow", "resident"],            # stage_5b ", ".join([...])
        "authors": ["Smith J", "Jones M"],                  # stage_4 normalize_authors regex
    })
    # stage_6_word_template.py:3944 / :7422 -> len(narrative.strip())
    assert len(out["narrative"].strip()) > 0
    # stage_5b_institution_enrichment.py:235 -> ", ".join(parts)
    ", ".join([out["training_type"], out["narrative"]])
    # stage_4_field_extractor.py:1605 -> re.search over authors string
    assert re.search(r"\w+", out["authors"]) is not None
