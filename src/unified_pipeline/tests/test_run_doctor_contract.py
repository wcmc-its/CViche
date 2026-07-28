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


def _module():
    return importlib.import_module("unified_pipeline.run_doctor")


def _tree():
    return ast.parse(_RUN_DOCTOR_PY.read_text())


def _emitted_keys():
    """The lint key each `lint_*` function passes to `_finding` / `_ready`.

    Read from the AST rather than by calling the lints, because calling them
    needs real stage artifacts. A lint that emits no key at all is excluded --
    that is how `lint_surprise`, a ranking helper wearing the `lint_` prefix,
    stays out of the registry.
    """
    keys = {}
    for node in _tree().body:
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
    assert len(known) == 16, (
        f"KNOWN_LINTS changed size ({len(known)}, was 16). That is fine if a "
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
