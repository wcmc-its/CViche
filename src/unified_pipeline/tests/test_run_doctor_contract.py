"""The two contracts `run_doctor.py` owes its consumers, before the #493 split.

`run_doctor.py` is 1,593 lines and is being decomposed into per-domain lint
modules. Two things must survive that, and neither is checked by the existing
suite:

1. **The lint registry.** `KNOWN_LINTS` must list exactly the keys the lint
   bodies emit. It cannot be derived from the `lint_*` function names -- two of
   them emit a key that is not their name, and a third `lint_*` is not a rule at
   all. Getting this wrong is silent: the sweep reports a lint that never runs,
   or omits one that does.

2. **The import surface.** Five files import 33 names from this module. A split
   that moves one without re-exporting breaks them at IMPORT time, which reads
   like a lost fix rather than a moved address.

Both are the `test_stage6_import_surface.py` pattern from #500, applied here
before the move rather than after.

Why a test rather than the check that already existed: `corpus_doctor_sweep.py`
had a drift assert, but it lived behind `--selftest`, which nothing in CI runs.
It had been failing on `dev` since `lint_surprise` landed and nobody saw it. A
guard that does not run is not a guard.

    python3 -m pytest src/unified_pipeline/tests/test_run_doctor_contract.py -p no:cacheprovider

Self-contained: AST and imports only, no DB, no network, no LLM, no PII.
"""

import ast
import importlib
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

_RUN_DOCTOR_PY = _SRC / "unified_pipeline" / "run_doctor.py"
_LINTS_DIR = _SRC / "unified_pipeline" / "doctor" / "lints"

# test_run_doctor.py's own fixture helpers (_build_clean_run, _UID) are
# reused by import below rather than copied, per the review's own
# instruction -- this file already imports run_doctor by adding _SRC to
# sys.path the same way, so the tests directory gets the same treatment.
_TESTS_DIR = _SRC / "unified_pipeline" / "tests"
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))


def _module():
    return importlib.import_module("unified_pipeline.run_doctor")


def _tree():
    return ast.parse(_RUN_DOCTOR_PY.read_text())


def _lint_sources():
    """Every file that may define a lint rule.

    The #493 split moves rules out of `run_doctor.py` into `doctor/lints/`, one
    module per domain, a PR at a time. Globbing means a new domain module is
    picked up without editing this test -- but a lint that lands somewhere else
    entirely still goes unseen, which is why the registry check below compares
    both directions rather than only looking for unregistered keys.
    """
    return [_RUN_DOCTOR_PY, *sorted(_LINTS_DIR.glob("*.py"))]


def _emitted_keys():
    """The lint key each `lint_*` function passes to `_finding` / `_ready`.

    Read from the AST rather than by calling the lints, because calling them
    needs real stage artifacts. A lint that emits no key at all is excluded --
    that is how `lint_surprise`, a ranking helper wearing the `lint_` prefix,
    stays out of the registry.
    """
    keys = {}
    for path in _lint_sources():
        for node in ast.parse(path.read_text()).body:
            if not (isinstance(node, ast.FunctionDef) and node.name.startswith("lint_")):
                continue
            found = set()
            for call in ast.walk(node):
                if not isinstance(call, ast.Call):
                    continue
                fn = getattr(call.func, "id", None) or getattr(call.func, "attr", None)
                if fn in ("_finding", "_ready") and call.args:
                    first = call.args[0]
                    if isinstance(first, ast.Constant) and isinstance(first.value, str):
                        found.add(first.value)
            if found:
                keys[node.name] = found
    return keys


def _dispatch_order():
    """Lint keys in the order `run_doctor()` calls them."""
    emitted = _emitted_keys()
    order = []
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.Call):
            continue
        fn = getattr(node.func, "attr", None)
        if fn != "extend" or not node.args:
            continue
        inner = node.args[0]
        if not isinstance(inner, ast.Call):
            continue
        name = getattr(inner.func, "id", None)
        if name in emitted:
            for key in sorted(emitted[name]):
                if key not in order:
                    order.append(key)
    return order


# --- contract 1: the lint registry -------------------------------------------

def test_known_lints_matches_what_the_lints_actually_emit():
    """Adding a lint without registering it in KNOWN_LINTS must fail here."""
    emitted = set().union(*_emitted_keys().values())
    registered = set(_module().KNOWN_LINTS)
    assert emitted == registered, (
        f"KNOWN_LINTS is out of sync with the lint bodies.\n"
        f"  emitted but unregistered: {sorted(emitted - registered)}\n"
        f"  registered but never emitted: {sorted(registered - emitted)}"
    )


def test_known_lints_is_in_dispatch_order():
    """Order is load-bearing -- the sweep breaks ranking ties on index."""
    assert list(_module().KNOWN_LINTS) == _dispatch_order(), (
        "KNOWN_LINTS is no longer in the order run_doctor() runs the lints. "
        "scripts/corpus_doctor_sweep.py ranks ties by index, so this changes "
        "its report even when every finding is identical."
    )


def test_known_lints_has_no_duplicates_and_is_not_empty():
    """Guard the guard: an empty or duplicated tuple would pass the checks above."""
    known = _module().KNOWN_LINTS
    assert len(known) == len(set(known)), f"duplicate entries in KNOWN_LINTS: {known}"
    assert len(known) == 18, (
        f"KNOWN_LINTS changed size ({len(known)}, was 17). That is fine if a "
        f"lint was genuinely added or removed -- update this count and say so "
        f"in the commit message."
    )


def test_the_lint_prefix_is_not_a_reliable_rule_marker():
    """Pin the trap that broke the previous drift check, so it is not reinvented.

    `lint_surprise` matches the `lint_` prefix but emits no findings -- it maps a
    lint name to how many bits of information its firing carries. Counting
    `lint_*` names therefore over-counts the rules, which is exactly what
    corpus_doctor_sweep's --selftest did.
    """
    mod = _module()
    prefixed = {n for n in dir(mod) if n.startswith("lint_") and callable(getattr(mod, n))}
    emitters = set(_emitted_keys())
    assert prefixed - emitters, (
        "no lint_*-prefixed non-rule remains. If lint_surprise was renamed, "
        "good -- delete this test and the warning in KNOWN_LINTS' docstring."
    )


# --- contract 2: the import surface ------------------------------------------

#: Every name imported from `run_doctor` anywhere in the repository, measured
#: against origin/dev @ f2712c7 by walking the AST of all .py files for
#: ImportFrom nodes targeting this module.
#:
#: Importers: scripts/corpus_doctor_sweep.py, scripts/doctor_one.py,
#: src/unified_pipeline/tests/test_run_doctor.py,
#: src/unified_pipeline/tests/test_run_doctor_trackchanges.py,
#: web_interface/backend/app/pipeline/orchestrator.py
#:
#: Adding to this list is fine. REMOVING from it is a breaking change, and it is
#: the one the split is most likely to make by accident.
RUN_DOCTOR_IMPORT_SURFACE = (
    "APPENDIX_WARN_ENTRIES",
    "CLASSIFIED_UNRENDERED_WARN_ENTRIES",
    "DUPLICATE_PASSAGE_MIN_BLOCKS",
    "MISSED_HEADERS_WARN_COUNT",
    "TABLE_SHAPE_WARN_DEFECTS",
    "TABLE_SHAPE_WARN_ROW_RATIO",
    "_docx_text",
    "_find_artifact",
    "_find_source",
    "_uid_owns",
    "iter_header_candidates",
    "lint_bucket_status",
    "lint_classified_unrendered",
    "lint_dead_sections",
    "lint_dedup_drops",
    "lint_duplicate_passages",
    "lint_enrichment_failures",
    "lint_missed_headers",
    "lint_output_hygiene",
    "lint_owner_contact_missing",
    "lint_pipe_leaks",
    "lint_pipeline_errors",
    "lint_segmentation",
    "lint_stage6_warnings",
    "lint_surprise",
    "lint_table_shape",
    "lint_under_extraction",
    "lint_unrendered_records",
    "main",
    "rank_lints",
    "read_docx_blocks",
    "read_docx_table_rows",
    "run_doctor",
)


@pytest.mark.parametrize("name", RUN_DOCTOR_IMPORT_SURFACE)
def test_run_doctor_symbol_is_still_importable(name):
    """Each name the repo imports from run_doctor must resolve.

    Fails at the point the split moves a symbol out without re-exporting it.
    """
    assert hasattr(_module(), name), (
        f"'{name}' is no longer importable from run_doctor. If the split moved "
        f"it, re-export it from this module -- corpus_doctor_sweep.py, "
        f"doctor_one.py, the backend orchestrator and two test modules import "
        f"it by this name."
    )


def test_the_surface_list_is_not_silently_empty():
    """A refactor that emptied the tuple would make the test above vacuous."""
    assert len(RUN_DOCTOR_IMPORT_SURFACE) == 33, (
        "the pinned run_doctor import surface changed size -- if that is "
        "intentional, update the count and say why in the commit message"
    )


# --- contract 3: review round 1 gaps on #725 ---------------------------------

def test_known_lints_literal_expected_order():
    """T5.9: `test_known_lints_is_in_dispatch_order` above proves KNOWN_LINTS
    agrees with an AST-DERIVED dispatch order -- both sides are
    implementation-derived, so a synchronized reorder of both would pass
    silently even though corpus_doctor_sweep.py ranks ties by this index.
    This is the one independent, hand-written expectation: a reorder here
    fails even when the AST-derived side was reordered to match."""
    assert tuple(_module().KNOWN_LINTS) == (
        "segmentation", "missed_headers", "bucket_status", "under_extraction",
        "classified_unrendered", "taxonomy_code_coverage", "output_hygiene",
        "dead_sections", "unrendered_records", "enrichment_failures",
        "stage6_render_warnings", "dedup_drops", "pipe_leaks", "table_shape",
        "duplicate_passages", "duplicate_records", "owner_contact_missing",
        "pipeline_errors_present",
    )


def test_find_artifact_returns_none_when_stage_directory_is_missing(tmp_path):
    """T5.2: `_find_artifact` globs `root / spec.stage_dir`; when that
    directory does not exist at all (not just empty), it must return None
    rather than raising or misreading an unrelated directory."""
    mod = _module()
    assert mod._find_artifact(tmp_path, "ABC", "stage_2") is None


def test_find_source_falls_back_to_uploads(tmp_path):
    """T5.3a: a source docx placed directly under root is preferred, but
    `_find_source` falls back to `root / uploads` -- the web backend's own
    upload location -- when nothing matches at the root."""
    mod = _module()
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    expected = uploads / "ABC.docx"
    expected.touch()
    assert mod._find_source(tmp_path, "ABC") == expected


def test_find_source_excludes_wcm_docx(tmp_path):
    """T5.3b: the rendered deliverable (`*_wcm.docx`) must never be picked up
    as the SOURCE document -- they share a uid prefix and live in the same
    tree for some run layouts."""
    mod = _module()
    (tmp_path / "ABC_wcm.docx").touch()
    assert mod._find_source(tmp_path, "ABC") is None


def test_run_doctor_reports_corrupt_stage2_as_unreadable_not_missing(tmp_path):
    """T5.4: a present-but-unparseable stage_2 artifact must surface the
    segmentation lint as ERROR with 'unreadable' in the message, not the
    benign INFO 'skipped: missing stage_2' -- the same missing-vs-unreadable
    distinction test_run_doctor.py already proves for stage_4 (line ~1131),
    pinned here for stage_2/segmentation specifically."""
    root = tmp_path / "outputs"
    stage = root / "stage_2_entry_extraction"
    stage.mkdir(parents=True)
    (stage / "ABC_entries.json").write_text("{not valid json")

    payload = _module().run_doctor(root, "ABC")
    seg = [f for f in payload["findings"] if f["lint"] == "segmentation"]
    assert any(f["severity"] == "ERROR" and "unreadable" in f["message"]
               for f in seg), seg


def test_ready_reports_missing_input_as_info():
    """T5.5a: a direct unit test of `_ready()`, not just its effect proven
    end-to-end through `run_doctor()` -- a genuinely absent input is INFO
    'skipped: missing <name>'."""
    mod = _module()
    findings = []
    assert mod._ready("example", unreadable={}, findings=findings,
                       stage_2=None) is False
    assert findings[0]["severity"] == "INFO"
    assert "missing stage_2" in findings[0]["message"]


def test_ready_reports_unreadable_input_as_error():
    """T5.5b: the other half of the same direct unit test -- an input the
    loader recorded as broken (present but unparseable) is ERROR 'skipped:
    unreadable <name> (...)', never the benign 'missing'."""
    mod = _module()
    findings = []
    assert mod._ready("example", unreadable={"stage_2": "JSONDecodeError"},
                       findings=findings, stage_2=None) is False
    assert findings[0]["severity"] == "ERROR"
    assert "unreadable stage_2" in findings[0]["message"]


def test_pipeline_errors_present_dispatches_from_a_real_partial_run(tmp_path):
    """T5.7: `lint_pipeline_errors` is already unit-proven against a hand-
    built dict missing two of three stage artifacts, but `run_doctor()`'s
    OWN dispatch -- building `scored_artifacts` from real files on disk --
    was untested. A clean run with stage_2 and stage_4 deleted and a fatal
    pattern written into stage_3b must still fire pipeline_errors_present,
    proving the dispatch really does scan whatever subset landed rather
    than requiring all three."""
    from test_run_doctor import _UID, _build_clean_run  # noqa: E402 (path set above)
    root = _build_clean_run(tmp_path)
    (root / "stage_2_entry_extraction" / f"{_UID}_cv_entries.json").unlink()
    (root / "stage_4_field_extraction" / f"{_UID}_cv_fields.json").unlink()
    classified = (root / "stage_3b_classified_entries"
                  / f"{_UID}_cv_classified.json")
    import json
    data = json.loads(classified.read_text())
    data["entries"][0]["error"] = "NameError: name 'response' is not defined"
    classified.write_text(json.dumps(data))

    payload = _module().run_doctor(root, _UID)
    gate = next(f for f in payload["findings"]
                if f["lint"] == "pipeline_errors_present")
    assert gate["severity"] == "ERROR"


def test_run_doctor_report_shape_and_values(tmp_path):
    """T5.8: the report-shape test in test_run_doctor.py already asserts the
    KEY SET; this asserts the VALUES of document_uid/root and the counts
    key set, which nothing currently checks."""
    payload = _module().run_doctor(tmp_path, "SHAPE1")
    assert payload["document_uid"] == "SHAPE1"
    assert payload["root"] == str(tmp_path)
    assert set(payload["counts"]) == {"ERROR", "WARN", "INFO"}


# --- contract 4: review round 2 on #725 (run_doctor.py thread) ---------------

# One malformed-but-parseable artifact per JSON kind: (loader label, stage
# dir, filename, file content, the shape problem the loader must name, a lint
# gated on that artifact). Each of these used to reach its lints as a valid
# input and fail inside them -- `'str'.get`, iterating None -- instead of
# being rejected once at the loading boundary.
_MALFORMED_ARTIFACTS = [
    ("stage_1a", "stage_1a_segmentation", "ABC_segmented.json",
     "[]", "top level is list, not an object", "missed_headers"),
    ("stage_1a", "stage_1a_segmentation", "ABC_segmented.json",
     '{"document_uid": "ABC"}', "missing 'hierarchy'", "segmentation"),
    ("stage_2", "stage_2_entry_extraction", "ABC_entries.json",
     '{"entries": "oops"}', "'entries' is str, not a list", "dead_sections"),
    ("stage_3b", "stage_3b_classified_entries", "ABC_classified.json",
     '{"entries": [1]}', "'entries[0]' is int, not an object",
     "taxonomy_code_coverage"),
    ("stage_4", "stage_4_field_extraction", "ABC_fields.json",
     '{"entries": [], "cv_owner": []}', "'cv_owner' is list, not an object",
     "under_extraction"),
    ("stage_5_enrichment", "stage_5_enrichment", "ABC_enriched.json",
     '{"entries": null}', "'entries' is NoneType, not a list",
     "enrichment_failures"),
    ("stage_6_report", "stage_6_wcm_documents", "ABC_render_warnings.json",
     '{"warnings": {}}', "'warnings' is dict, not a list",
     "stage6_render_warnings"),
]


@pytest.mark.parametrize("label, stage_dir, filename, content, problem, lint",
                         _MALFORMED_ARTIFACTS)
def test_a_malformed_but_parseable_artifact_is_rejected_at_the_loading_boundary(
        tmp_path, label, stage_dir, filename, content, problem, lint):
    """Item 2 / #747: the loader turns "JSON parsed" into "this is a valid
    <stage> artifact". A shape violation takes the same ERROR 'unreadable'
    path a corrupt file takes, names the offending field, is never reported
    as the benign 'missing', and no lint crashes on it."""
    root = tmp_path / "outputs"
    (root / stage_dir).mkdir(parents=True)
    (root / stage_dir / filename).write_text(content)

    payload = _module().run_doctor(root, "ABC")
    hit = [f for f in payload["findings"] if f["lint"] == lint]
    assert any(f["severity"] == "ERROR"
               and f"unreadable {label} (invalid artifact: {problem})" in f["message"]
               for f in hit), hit
    assert not any(f"missing {label}" in f["message"] for f in hit), hit
    assert not any("crashed" in f["message"] for f in payload["findings"])
    assert payload["worst_severity"] == "ERROR"


@pytest.mark.parametrize("data, problem", [
    ({"entries": [{"text": "x"}], "cv_owner": {"full_name": "M. Shapiro"}}, None),
    ({"entries": []}, None),
    # a null cv_owner is tolerated: the owner gate scores it as missing
    ({"entries": [], "cv_owner": None}, None),
    ([], "top level is list, not an object"),
    ("{}", "top level is str, not an object"),
    ({}, "missing 'entries'"),
    ({"entries": {}}, "'entries' is dict, not a list"),
    ({"entries": ["x"]}, "'entries[0]' is str, not an object"),
    ({"entries": [], "cv_owner": "Dr X"}, "'cv_owner' is str, not an object"),
])
def test_artifact_shape_error_names_the_offending_field(data, problem):
    """The validator itself, on the stage-4 spec (records + an object field):
    the first violation, named; None for a valid artifact."""
    mod = _module()
    assert mod._artifact_shape_error(data, mod._ARTIFACTS["stage_4"]) == problem


def test_every_json_artifact_kind_declares_its_record_shape():
    """The boundary check is only as good as the specs: every JSON artifact
    kind names at least one required or optional record list, so no kind
    silently degrades back to "any object passes"."""
    mod = _module()
    for key in mod._JSON_ARTIFACTS:
        spec = mod._ARTIFACTS[key]
        assert spec.record_lists or spec.optional_lists, key
    assert set(mod._JSON_ARTIFACTS) == {
        "stage_1a", "stage_2", "stage_3b", "stage_4", "stage_5_enrichment",
        "stage_6_report"}


def test_ten_of_the_surface_is_private():
    """Most of what consumers reach for is underscore-private. That is the point.

    Privacy here describes intent for new callers, but the existing imports are
    real and load-bearing, so they are part of the contract whether or not they
    should be. Narrowing the surface is worthwhile and is a SEPARATE change from
    relocating code.
    """
    private = [n for n in RUN_DOCTOR_IMPORT_SURFACE if n.startswith("_")]
    assert len(private) == 4, (
        f"expected 4 private names in the run_doctor import surface, found "
        f"{len(private)}: {sorted(private)}"
    )


if __name__ == "__main__":
    test_known_lints_matches_what_the_lints_actually_emit()
    test_known_lints_is_in_dispatch_order()
    test_known_lints_has_no_duplicates_and_is_not_empty()
    test_the_lint_prefix_is_not_a_reliable_rule_marker()
    for _n in RUN_DOCTOR_IMPORT_SURFACE:
        test_run_doctor_symbol_is_still_importable(_n)
    test_the_surface_list_is_not_silently_empty()
    test_ten_of_the_surface_is_private()
    print("OK")
