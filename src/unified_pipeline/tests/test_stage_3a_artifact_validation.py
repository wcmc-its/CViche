"""Stage 3b trusts the stage-3a artifact as if it were schema-validated (#558).

Stage 3a writes ``mappings`` from a raw ``json.loads()`` of an LLM response
issued with ``response_format={"type": "json_object"}`` and no schema.
Nine downstream subscripts (``o["code"]``, ``o["confidence"]``) had no
guard, so a malformed option -- missing "code", a stringified confidence,
or a bare string where an object was expected -- raised uncaught and lost
the entire run. ``load_stage_3a_mappings`` now normalises the tree once, at
load, before any of those sites see it.

Tests go through ``load_stage_3a_mappings(path)`` itself (a real file on
disk), not the normaliser directly -- the whole point of the defect is that
the real call site bypassed every guard.
"""
import json
import logging
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import unified_pipeline.stage_3b_entry_classifier as stage_3b  # noqa: E402
from unified_pipeline.stage_3b_entry_classifier import (  # noqa: E402
    build_mapping_index,
    load_stage_3a_mappings,
)


def _write(tmp_path, mappings):
    path = tmp_path / "stage_3a.json"
    path.write_text(json.dumps({"mappings": mappings}))
    return path


def test_option_missing_code_is_dropped_not_raised(tmp_path, caplog):
    path = _write(tmp_path, [
        {"title": "PERSONAL DATA", "level": "H1", "children": [], "taxonomy_options": [
            {"confidence": 0.9},  # no "code" -- the exact reproduction from the issue
            {"code": "A", "confidence": 0.8},
        ]},
    ])
    with caplog.at_level(logging.WARNING, logger=stage_3b.logger.name):
        data = load_stage_3a_mappings(path)
    node = data["mappings"][0]
    assert [o["code"] for o in node["taxonomy_options"]] == ["A"]
    assert any("dropping malformed taxonomy_options" in r.getMessage() for r in caplog.records)

    # And the wire this feeds -- build_mapping_index / get_primary_codes -- no longer raises.
    index = build_mapping_index(data["mappings"])
    assert index["PERSONAL DATA"]["taxonomy_options"][0]["code"] == "A"


def test_stringified_confidence_is_coerced_to_float(tmp_path):
    path = _write(tmp_path, [
        {"title": "H", "level": "H1", "children": [], "taxonomy_options": [
            {"code": "A", "confidence": "0.9"},  # the ValueError reproduction from the issue
        ]},
    ])
    data = load_stage_3a_mappings(path)
    conf = data["mappings"][0]["taxonomy_options"][0]["confidence"]
    assert conf == 0.9
    assert isinstance(conf, float)


def test_bare_string_option_is_dropped_not_type_errored(tmp_path, caplog):
    path = _write(tmp_path, [
        {"title": "H", "level": "H1", "children": [], "taxonomy_options": [
            "not-an-object",  # the TypeError reproduction from the issue
            {"code": "B", "confidence": 0.5},
        ]},
    ])
    with caplog.at_level(logging.WARNING, logger=stage_3b.logger.name):
        data = load_stage_3a_mappings(path)
    assert [o["code"] for o in data["mappings"][0]["taxonomy_options"]] == ["B"]


def test_malformed_children_treated_as_empty_not_recursed_into(tmp_path):
    path = _write(tmp_path, [
        {"title": "H", "level": "H1", "children": "not-a-list", "taxonomy_options": []},
    ])
    data = load_stage_3a_mappings(path)
    assert data["mappings"][0]["children"] == []
    # build_mapping_index's own "if children:" + recursive call must not choke on it either.
    build_mapping_index(data["mappings"])


def test_well_formed_mappings_pass_through_unchanged(tmp_path):
    """Positive control: normalisation must be a no-op on clean input --
    this is the expected result on the current corpus (censused at zero
    malformed options across all 99 local artifacts, per the issue body)."""
    mappings = [
        {"title": "PERSONAL DATA", "level": "H1", "taxonomy_options": [
            {"code": "A", "confidence": 1.0},
        ], "children": [
            {"title": "Sub", "level": "H2", "taxonomy_options": [
                {"code": "B", "confidence": 0.85},
            ], "children": []},
        ]},
    ]
    path = _write(tmp_path, mappings)
    data = load_stage_3a_mappings(path)
    assert data["mappings"] == mappings


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
