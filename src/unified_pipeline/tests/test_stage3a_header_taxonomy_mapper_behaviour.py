"""Behaviour tests for stage_3a_header_taxonomy_mapper.py (#704 live-path coverage).

Covers format_hierarchy_as_outline (nesting/indent), build_taxonomy_reference_condensed
(category grouping, missing-code skip), map_headers_to_taxonomy (happy path,
malformed JSON, partial JSON missing the "mappings" key, and a hallucinated
taxonomy code), count_nodes, and run_stage_3a end to end writing its output
JSON into tmp_path.

call_llm is imported by name into the module under test
(``from unified_pipeline.llm_client import call_llm``), so every test that
reaches map_headers_to_taxonomy stubs it with
``monkeypatch.setattr(mod, "call_llm", fake)`` -- never the real network
client. The fake returns the exact dict shape the module destructures at its
parse site (content, model, prompt_tokens, completion_tokens, total_tokens,
cost, latency_ms); real taxonomy_v7.json is never loaded here -- run_stage_3a
tests stub mod.load_taxonomy with a small synthetic taxonomy so nothing
depends on the live taxonomy file's contents.
"""
import json
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import unified_pipeline.stage_3a_header_taxonomy_mapper as mod  # noqa: E402


def _fake_call_llm(content: str, *, model: str = "fake-model",
                    prompt_tokens: int = 111, completion_tokens: int = 22,
                    cost: float = 0.0042, latency_ms: int = 2500):
    """Build a stub matching call_llm's normalized return shape exactly."""
    def _call(stage, messages, response_format=None, **kwargs):
        return {
            "content": content,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "cost": cost,
            "latency_ms": latency_ms,
        }
    return _call


def _small_taxonomy() -> dict:
    return {
        "meta": {
            "version": "test-1",
            "total_valid_codes": 2,
            "category_groups": {
                "Service": ["P", "Q2"],
                "Publications": ["S1"],
            },
        },
        "codes": [
            {"code": "P", "label": "Committee Service", "purpose": "Internal committees"},
            {"code": "Q2", "label": "External Service", "purpose": "External memberships"},
            # "S1" is intentionally absent from codes_list to exercise the
            # code-not-in-lookup skip branch.
        ],
    }


# ---------------------------------------------------------------------------
# load_taxonomy -- the real, committed taxonomy_v7.json (static repo data,
# not corpus/PII), read-only.
# ---------------------------------------------------------------------------

def test_load_taxonomy_reads_real_reference_file():
    taxonomy = mod.load_taxonomy()
    assert isinstance(taxonomy, dict)
    assert isinstance(taxonomy.get("codes"), list) and taxonomy["codes"]
    assert "version" in taxonomy.get("meta", {})


# ---------------------------------------------------------------------------
# format_hierarchy_as_outline
# ---------------------------------------------------------------------------

def test_format_hierarchy_as_outline_nests_and_indents_children():
    hierarchy = [
        {"level": "H1", "text": "EDUCATION", "children": [
            {"level": "H2", "text": "Degrees", "children": [
                {"level": "H3", "text": "MD", "children": []},
            ]},
        ]},
    ]
    outline = mod.format_hierarchy_as_outline(hierarchy)
    lines = outline.split("\n")
    assert lines[0] == "[H1] EDUCATION"
    assert lines[1] == "  [H2] Degrees"
    assert lines[2] == "    [H3] MD"


def test_format_hierarchy_as_outline_siblings_at_same_indent_no_extra_lines():
    hierarchy = [
        {"level": "H1", "text": "A", "children": []},
        {"level": "H1", "text": "B", "children": []},
    ]
    outline = mod.format_hierarchy_as_outline(hierarchy)
    # A leaf with no children must not add a recursive blank/child line --
    # exactly one line per sibling.
    assert outline == "[H1] A\n[H1] B"


def test_format_hierarchy_as_outline_defaults_missing_level_and_text():
    # No 'level' key and no 'children' key at all (not just empty) -- both
    # must fall back via .get() rather than raising KeyError.
    outline = mod.format_hierarchy_as_outline([{"text": "Untitled Level"}])
    assert outline == "[H1] Untitled Level"

    outline2 = mod.format_hierarchy_as_outline([{"level": "H2"}])
    assert outline2 == "[H2] "


# ---------------------------------------------------------------------------
# build_taxonomy_reference_condensed
# ---------------------------------------------------------------------------

def test_build_taxonomy_reference_condensed_lists_codes_under_their_group():
    ref = mod.build_taxonomy_reference_condensed(_small_taxonomy())
    lines = ref.split("\n")
    assert "TAXONOMY CODES REFERENCE:" in lines
    assert "=== Service ===" in lines
    p_idx = lines.index("  P: Committee Service")
    assert lines[p_idx + 1] == "       Internal committees"
    q2_idx = lines.index("  Q2: External Service")
    assert lines[q2_idx + 1] == "       External memberships"


def test_build_taxonomy_reference_condensed_skips_code_missing_from_lookup():
    # "S1" is listed under the Publications group but absent from the
    # top-level codes list -- the function must skip it silently rather
    # than KeyError on code_lookup["S1"].
    ref = mod.build_taxonomy_reference_condensed(_small_taxonomy())
    assert "=== Publications ===" in ref
    assert "S1" not in ref


def test_build_taxonomy_reference_condensed_empty_taxonomy_still_has_header():
    ref = mod.build_taxonomy_reference_condensed({})
    assert ref.startswith("TAXONOMY CODES REFERENCE:")
    assert "===" not in ref


# ---------------------------------------------------------------------------
# map_headers_to_taxonomy
# ---------------------------------------------------------------------------

_HIERARCHY = [{"level": "H1", "text": "SERVICE", "children": []}]


def test_map_headers_to_taxonomy_happy_path_returns_parsed_mappings_and_stats(monkeypatch):
    content = json.dumps({
        "mappings": [
            {"title": "SERVICE", "level": "H1",
             "taxonomy_options": [{"code": "P", "confidence": 1.0}],
             "children": []},
        ],
    })
    monkeypatch.setattr(mod, "call_llm", _fake_call_llm(content, prompt_tokens=200,
                                                          completion_tokens=50, cost=0.01,
                                                          latency_ms=4000))
    result, stats = mod.map_headers_to_taxonomy(_HIERARCHY, _small_taxonomy())

    # Only the real parse route produces a "mappings" key with the exact
    # parsed node content -- the fallback (malformed-JSON) route always
    # produces an empty mappings list plus an "error" key, so this also
    # rules out the fallback having been taken.
    assert result["mappings"][0]["title"] == "SERVICE"
    assert result["mappings"][0]["taxonomy_options"] == [{"code": "P", "confidence": 1.0}]
    assert "error" not in result

    assert stats["model"] == "fake-model"
    assert stats["input_tokens"] == 200
    assert stats["output_tokens"] == 50
    assert stats["total_tokens"] == 250
    assert stats["cost"] == 0.01
    assert stats["elapsed_seconds"] == 4.0


def test_map_headers_to_taxonomy_malformed_json_falls_back_to_empty_mappings(monkeypatch):
    monkeypatch.setattr(mod, "call_llm", _fake_call_llm("{not valid json"))
    result, stats = mod.map_headers_to_taxonomy(_HIERARCHY, _small_taxonomy())

    assert result["mappings"] == []
    assert "error" in result
    assert isinstance(result["error"], str) and result["error"]
    # Stats are still derived from the (successful) call_llm response even
    # though the content it returned failed to parse.
    assert stats["total_tokens"] == 133


def test_map_headers_to_taxonomy_partial_json_missing_mappings_key_passes_through_raw(monkeypatch):
    # Valid JSON, but the LLM didn't follow the required top-level shape --
    # json.loads succeeds so no fallback triggers, and the function does
    # NOT synthesize a "mappings" key; the caller (run_stage_3a) is the one
    # that defaults it via .get("mappings", []).
    monkeypatch.setattr(mod, "call_llm", _fake_call_llm(json.dumps({"unexpected": "shape"})))
    result, _stats = mod.map_headers_to_taxonomy(_HIERARCHY, _small_taxonomy())

    assert result == {"unexpected": "shape"}
    assert "mappings" not in result


def test_map_headers_to_taxonomy_hallucinated_code_is_passed_through_unfiltered(monkeypatch):
    # map_headers_to_taxonomy performs zero cross-checking of returned codes
    # against the taxonomy it was given -- "ZZ99" isn't in _small_taxonomy()
    # at all, and it comes back verbatim. Code validation for this artifact
    # happens downstream, in stage_3b's load_stage_3a_mappings/
    # build_mapping_index (see test_stage_3a_artifact_validation.py) --
    # this test documents that stage_3a itself does no such filtering.
    content = json.dumps({
        "mappings": [
            {"title": "SERVICE", "level": "H1",
             "taxonomy_options": [{"code": "ZZ99", "confidence": 1.0}],
             "children": []},
        ],
    })
    monkeypatch.setattr(mod, "call_llm", _fake_call_llm(content))
    result, _stats = mod.map_headers_to_taxonomy(_HIERARCHY, _small_taxonomy())

    assert result["mappings"][0]["taxonomy_options"][0]["code"] == "ZZ99"


# ---------------------------------------------------------------------------
# count_nodes
# ---------------------------------------------------------------------------

def test_count_nodes_counts_nested_tree():
    mappings = [
        {"title": "A", "children": [
            {"title": "A1", "children": []},
            {"title": "A2", "children": [
                {"title": "A2a", "children": []},
            ]},
        ]},
        {"title": "B", "children": []},
    ]
    # A, A1, A2, A2a, B = 5
    assert mod.count_nodes(mappings) == 5


def test_count_nodes_empty_list_is_zero():
    assert mod.count_nodes([]) == 0


def test_count_nodes_missing_children_key_defaults_to_zero_not_raise():
    assert mod.count_nodes([{"title": "solo"}]) == 1


# ---------------------------------------------------------------------------
# run_stage_3a
# ---------------------------------------------------------------------------

def _write_stage_1a(tmp_path, hierarchy):
    path = tmp_path / "in_segmented.json"
    path.write_text(json.dumps({"hierarchy": hierarchy}), encoding="utf-8")
    return path


def test_run_stage_3a_writes_output_json_with_expected_shape(tmp_path, monkeypatch):
    stage_1a_path = _write_stage_1a(tmp_path, [
        {"level": "H1", "text": "SERVICE", "children": [
            {"level": "H2", "text": "Committees", "children": []},
        ]},
    ])
    content = json.dumps({
        "mappings": [
            {"title": "SERVICE", "level": "H1",
             "taxonomy_options": [{"code": "P", "confidence": 1.0}],
             "children": [
                 {"title": "Committees", "level": "H2",
                  "taxonomy_options": [{"code": "P", "confidence": 1.0}],
                  "children": []},
             ]},
        ],
    })
    monkeypatch.setattr(mod, "load_taxonomy", lambda: _small_taxonomy())
    monkeypatch.setattr(mod, "call_llm", _fake_call_llm(content, prompt_tokens=10,
                                                          completion_tokens=5, cost=0.001,
                                                          latency_ms=1000))

    output_dir = tmp_path / "out"
    result = mod.run_stage_3a("synthtest001", stage_1a_path=str(stage_1a_path),
                               output_dir=str(output_dir))

    assert result["document_uid"] == "synthtest001"
    assert result["node_count"] == 2
    assert result["mappings"][0]["title"] == "SERVICE"

    output_path = Path(result["output_path"])
    assert output_path == output_dir / "synthtest001_header_taxonomy.json"
    assert output_path.exists()

    on_disk = json.loads(output_path.read_text(encoding="utf-8"))
    assert on_disk["document_uid"] == "synthtest001"
    assert on_disk["stage"] == "3a"
    assert on_disk["source_file"] == str(stage_1a_path)
    assert on_disk["mappings"][0]["children"][0]["title"] == "Committees"
    assert on_disk["meta"]["model"] == "fake-model"
    assert on_disk["meta"]["taxonomy_version"] == "test-1"
    assert on_disk["meta"]["node_count"] == 2
    assert on_disk["meta"]["stats"]["total_tokens"] == 15
    assert "generated_at" in on_disk["meta"]


def test_run_stage_3a_missing_input_raises_filenotfound(tmp_path):
    missing = tmp_path / "does_not_exist.json"
    with pytest.raises(FileNotFoundError, match=str(missing)):
        mod.run_stage_3a("synthtest002", stage_1a_path=str(missing), output_dir=str(tmp_path))


def test_run_stage_3a_default_input_path_derives_from_document_uid(tmp_path):
    # stage_1a_path=None exercises the auto-detect branch that builds the
    # default path from document_uid under this module's own outputs/
    # directory. A UID this distinctive should never exist on disk, so the
    # call fails fast at the existence check -- before any write -- letting
    # this stay safely inside tmp_path-free territory (nothing is written).
    uid = "no_such_synthetic_uid_704_w2"
    expected = (Path(mod.__file__).parent / "outputs" / "stage_1a_segmentation"
                / f"{uid}_segmented.json")
    assert not expected.exists()
    with pytest.raises(FileNotFoundError, match=str(expected)):
        mod.run_stage_3a(uid)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
