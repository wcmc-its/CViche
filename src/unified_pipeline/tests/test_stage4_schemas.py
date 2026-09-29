"""Regression tests for `unified_pipeline.stage4.schemas` (PR #643 review pass).

Covers the config-loading fixes made in response to code review on the #498
split PR:

  - the config path resolves to the real file now that the module lives one
    directory deeper than the pre-split `stage_4_field_extractor.py`
  - config schemas MERGE over the built-in FIELD_SCHEMAS defaults instead of
    wholesale-replacing them (codes absent from the config file must keep
    their built-in schema, not silently disappear)
  - a malformed config file raises SchemaConfigurationError with a message
    naming what was malformed, instead of a bare AttributeError
  - get_active_schemas() returns a copy callers cannot use to mutate the
    process-wide cache
  - unknown taxonomy codes fall straight to DEFAULT_SCHEMA (the dead
    single-character parent-code fallback, e.g. "S1 -> S", was removed
    because FIELD_SCHEMAS never defined bare-letter parent codes)

Self-contained: imports only, no DB, no network, no LLM, no PII.
"""
import json
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage4 import schemas as schemas_mod  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_schema_cache():
    """`_LOADED_SCHEMAS` is a lazily-initialised module-level cache; reset it
    around every test so tests don't leak state into each other."""
    schemas_mod._LOADED_SCHEMAS = None
    yield
    schemas_mod._LOADED_SCHEMAS = None


def test_default_config_path_resolves_to_the_real_file():
    """The config file lives at unified_pipeline/config/, not
    unified_pipeline/stage4/config/ -- Path(__file__).parent needs an extra
    .parent now that schemas.py is one directory deeper than the pre-split
    stage_4_field_extractor.py."""
    assert schemas_mod.FIELD_SCHEMA_CONFIG_PATH.exists(), (
        f"{schemas_mod.FIELD_SCHEMA_CONFIG_PATH} does not exist -- the "
        f"config path did not account for schemas.py moving into stage4/"
    )


def test_load_field_schemas_from_config_missing_file_returns_none():
    result = schemas_mod.load_field_schemas_from_config("/no/such/path/schemas.json")
    assert result is None


def test_config_schemas_merge_over_builtin_defaults(tmp_path, monkeypatch):
    """A config file that only covers SOME codes must not blank out the
    built-in schema for codes it omits (the reviewer's "M2/N3/Q4 silently
    disabled" bug)."""
    config_path = tmp_path / "partial_schemas.json"
    config_path.write_text(json.dumps({
        "version": schemas_mod.FIELD_SCHEMA_VERSION,
        "schemas": {
            "A": {
                "fields": {
                    "name": {"extract": True},
                    "email": {"extract": True},
                    "phone": {"extract": False},
                },
            },
        },
    }))

    config_schemas = schemas_mod.load_field_schemas_from_config(str(config_path))
    assert set(config_schemas.keys()) == {"A"}
    assert config_schemas["A"]["fields"] == ["name", "email"]

    # monkeypatch, not assignment: a bare assignment leaked this partial
    # config into every later test in the session, which then read F2 from
    # the built-in FIELD_SCHEMAS instead of the real file (#897 found it).
    monkeypatch.setattr(schemas_mod, "FIELD_SCHEMA_CONFIG_PATH", config_path)
    active = schemas_mod.get_active_schemas()

    # Overridden code reflects the config file.
    assert active["A"]["fields"] == ["name", "email"]
    # Codes the config file never mentions keep their built-in default --
    # this used to disappear entirely (fell through to DEFAULT_SCHEMA).
    assert active["M2"]["fields"] == schemas_mod.FIELD_SCHEMAS["M2"]["fields"]
    assert active["N3"]["fields"] == schemas_mod.FIELD_SCHEMAS["N3"]["fields"]
    assert active["Q4"]["fields"] == schemas_mod.FIELD_SCHEMAS["Q4"]["fields"]


def test_malformed_config_raises_schema_configuration_error(tmp_path):
    config_path = tmp_path / "bad_fields_value.json"
    config_path.write_text(json.dumps({
        "version": "1.1",
        "schemas": {"A": {"fields": True}},
    }))
    with pytest.raises(schemas_mod.SchemaConfigurationError, match="A"):
        schemas_mod.load_field_schemas_from_config(str(config_path))


def test_malformed_field_entry_raises_schema_configuration_error(tmp_path):
    config_path = tmp_path / "bad_field_entry.json"
    config_path.write_text(json.dumps({
        "version": "1.1",
        "schemas": {"A": {"fields": {"name": True}}},
    }))
    with pytest.raises(schemas_mod.SchemaConfigurationError, match="name"):
        schemas_mod.load_field_schemas_from_config(str(config_path))


def test_version_mismatch_logs_warning_but_does_not_raise(tmp_path, caplog):
    config_path = tmp_path / "old_version.json"
    config_path.write_text(json.dumps({
        "version": "0.9",
        "schemas": {"A": {"fields": {"name": {"extract": True}}}},
    }))
    with caplog.at_level("WARNING"):
        result = schemas_mod.load_field_schemas_from_config(str(config_path))
    assert result is not None
    assert any("version mismatch" in rec.message.lower() for rec in caplog.records)


def test_get_active_schemas_returns_a_copy_not_the_live_cache():
    active = schemas_mod.get_active_schemas()
    active["A"]["fields"].append("mutated_by_caller")

    active_again = schemas_mod.get_active_schemas()
    assert "mutated_by_caller" not in active_again["A"]["fields"]


def test_unknown_taxonomy_code_falls_back_to_default_schema():
    """FIELD_SCHEMAS only defines S0-S9, never bare "S" -- an unrecognized
    code must land on DEFAULT_SCHEMA directly rather than through a dead
    parent-code lookup that never actually matched anything."""
    assert schemas_mod.get_field_schema("S10") == schemas_mod.DEFAULT_SCHEMA
    assert schemas_mod.get_field_schema("ZZ") == schemas_mod.DEFAULT_SCHEMA


def test_get_field_schema_exact_match_still_works():
    """A code the active schemas know returns that code's schema, never
    DEFAULT_SCHEMA. Compared against the ACTIVE schemas: the previous
    `== FIELD_SCHEMAS["S1"]` only held because the merge test above had
    leaked a partial config path, so S1 was falling back to the built-in
    table -- with the real config file loaded the two legitimately differ."""
    schema = schemas_mod.get_field_schema("S1")
    assert schema != schemas_mod.DEFAULT_SCHEMA
    assert schema["fields"] == schemas_mod.get_active_schemas()["S1"]["fields"]


def test_f2_asks_the_llm_for_the_specialty():
    """#897: `specialty` was `extract: false` in field_schemas_v1.1.json, so
    the F2 prompt never asked for it and "American Board of Pediatrics,
    Certification in General Pediatrics" came back as the board alone --
    two certifications from one board rendered as identical rows. The
    prompt's field list is `get_field_schema('F2')['fields']`, so this pins
    the wire, not the JSON."""
    fields = schemas_mod.get_field_schema("F2")["fields"]
    assert "specialty" in fields
    assert "certifying_board" in fields


@pytest.mark.parametrize("code", ["M2A", "M2B", "M2C"])
def test_grant_buckets_ask_the_llm_for_status_and_notes(code):
    """#982: web39's three withdrawn grants lost that status because the M2A/M2B/M2C
    schemas never asked for it. Read off the ACTIVE (config-merged) schema, the
    list the prompt's "Fields to Extract" line is built from -- and the config
    file, not the built-in table, is what wins at runtime."""
    fields = schemas_mod.get_field_schema(code)["fields"]
    assert "status" in fields and "notes" in fields
    assert {"status", "notes"} <= set(schemas_mod.FIELD_DESCRIPTIONS[code])


def test_invited_presentation_schema_asks_the_llm_for_the_speaker_role():
    """#475: web160's 11 "Visiting Professor" roles were dropped at stage 4
    because R had no `role` field. Read off the ACTIVE (config-merged) schema,
    which is what the prompt's field list is built from."""
    assert "role" in schemas_mod.get_field_schema("R")["fields"]
    assert "role" in schemas_mod.FIELD_DESCRIPTIONS["R"]
