"""Import-surface contract for the stage 4 module split (#498).

`stage_4_field_extractor.py` was 2,578 lines and is decomposed into
`stage4/{schemas,extraction,coercion,owner_name}.py`. The decomposition must
not change what the rest of the repository can import from it, because the
callers are spread across code the split does not touch:

  - `run_full_pipeline.py` and the backend `orchestrator.py` are PARALLEL
    drivers sharing no code, so a name that moves has to be fixed in both, and
    a miss lands silently in the one nobody ran.
  - Three test files under `src/unified_pipeline/tests/` and one under
    `web_interface/backend/tests/` import stage 4 names directly, including
    private ones (`_entry_end_year`, `_owner_affiliation_lines`).

So the rule, same as the stage 6 split (#398): move the implementation wherever
separation of concerns says it belongs, then re-export the name from this
module. Privacy of the underscore names describes intent for new callers; the
existing imports are real and load-bearing, so they are part of the contract.

One deliberate absence: `_LOADED_SCHEMAS`, the lazily-initialised schema cache,
is NOT re-exported. A facade re-export of a mutable global that the owning
module reassigns is a stale second binding -- the facade copy would stay None
forever while `stage4.schemas` populates its own (the #496 lesson). No caller
imported it (measured, see below), so the absence breaks nothing.

Related contract, pinned in `test_owner_affiliation_fallback.py`: a test that
stubs `extract_fields_batch` / `extract_cv_owner_name` /
`infer_cv_owner_location` must rebind them on
`unified_pipeline.stage4.extraction`, whose globals the orchestrating function
actually reads -- not on this facade.

    python3 -m pytest src/unified_pipeline/tests/test_stage4_import_surface.py -p no:cacheprovider

Self-contained: imports only, no DB, no network, no LLM, no PII.
"""

import importlib
import sys
from pathlib import Path

import pytest

# 35 of 39 test files in this directory carry this preamble by hand (see
# conftest.py's own docstring for the measurement and why it stays: the repo
# root lands on sys.path on its own, but src/ does not, and the four files
# that omitted this were the ones that broke when run alone (#451)). conftest
# already does this for collection, but the preamble is left in every file on
# purpose so each one stays runnable standalone -- removing it here only would
# make this file the odd one out for no behaviour change.
_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


#: Public API surface: every non-underscore top-level name defined in
#: `stage_4_field_extractor` on origin/dev @ 5a3090b except `_LOADED_SCHEMAS`
#: (see module docstring), measured on 2026-08-14 by walking the module AST.
#: Underscore-prefixed names live in LEGACY_PRIVATE_IMPORT_SURFACE below --
#: kept importable because existing tests reach them directly, not because
#: they are meant as public API.
#:
#: Adding to this list is fine. REMOVING from it is a breaking change, and it
#: is the one the split is most likely to make by accident.
STAGE4_IMPORT_SURFACE = (
    "DEFAULT_SCHEMA",
    "FIELD_DESCRIPTIONS",
    "FIELD_SCHEMAS",
    "FIELD_SCHEMA_CONFIG_PATH",
    "FIELD_SCHEMA_VERSION",
    "REGEX_PATTERNS",
    "TAXONOMY_LABELS",
    "add_target_names",
    "apply_regex_post_processing",
    "attempt_llm_recovery",
    "build_extraction_prompt",
    "calculate_unextracted_content",
    "coerce_field_value_types",
    "extract_cv_owner_name",
    "extract_fields_batch",
    "extract_fields_from_mapped_entries",
    "extract_initials",
    "find_target_name_in_authors",
    "get_active_schemas",
    "get_field_schema",
    "get_taxonomy_label",
    "infer_cv_owner_location",
    "load_field_schemas_from_config",
    "needs_llm_recovery",
    "normalize_authors_vancouver",
    "normalize_dates",
    "process_cv",
    "run_validation",
)

#: Underscore-prefixed names carried for backward compatibility only: three
#: existing test files import these directly from the pre-split module (see
#: module docstring), so the split has to keep them resolvable even though
#: their leading underscore says "not public API" to any new caller. Tracked
#: separately from STAGE4_IMPORT_SURFACE so a reviewer -- or a future
#: developer widening the public surface -- can see the public/private-compat
#: distinction at a glance instead of it being buried in one flat list of 32.
LEGACY_PRIVATE_IMPORT_SURFACE = (
    "_OWNER_AFFILIATION_FIELDS",
    "_entry_end_year",
    "_get_field_descriptions",
    "_owner_affiliation_lines",
)

_FACADE_MODULE = "unified_pipeline.stage_4_field_extractor"

#: Which module actually owns (defines) each name in the two surfaces above.
#: Everything except `process_cv` and `run_validation` is moved-and-re-exported
#: from one of the four stage4/ submodules; those two are the orchestration
#: entry points and were never moved -- they are still defined directly in the
#: facade, so their "owning module" is the facade itself.
_NAME_TO_OWNING_MODULE = {
    # unified_pipeline/stage4/coercion.py
    "REGEX_PATTERNS": "unified_pipeline.stage4.coercion",
    "apply_regex_post_processing": "unified_pipeline.stage4.coercion",
    "coerce_field_value_types": "unified_pipeline.stage4.coercion",
    "extract_initials": "unified_pipeline.stage4.coercion",
    "normalize_authors_vancouver": "unified_pipeline.stage4.coercion",
    "normalize_dates": "unified_pipeline.stage4.coercion",
    # unified_pipeline/stage4/extraction.py
    "_get_field_descriptions": "unified_pipeline.stage4.extraction",
    "attempt_llm_recovery": "unified_pipeline.stage4.extraction",
    "build_extraction_prompt": "unified_pipeline.stage4.extraction",
    "calculate_unextracted_content": "unified_pipeline.stage4.extraction",
    "extract_fields_batch": "unified_pipeline.stage4.extraction",
    "extract_fields_from_mapped_entries": "unified_pipeline.stage4.extraction",
    "needs_llm_recovery": "unified_pipeline.stage4.extraction",
    # unified_pipeline/stage4/owner_name.py
    "_OWNER_AFFILIATION_FIELDS": "unified_pipeline.stage4.owner_name",
    "_entry_end_year": "unified_pipeline.stage4.owner_name",
    "_owner_affiliation_lines": "unified_pipeline.stage4.owner_name",
    "add_target_names": "unified_pipeline.stage4.owner_name",
    "extract_cv_owner_name": "unified_pipeline.stage4.owner_name",
    "find_target_name_in_authors": "unified_pipeline.stage4.owner_name",
    "infer_cv_owner_location": "unified_pipeline.stage4.owner_name",
    # unified_pipeline/stage4/schemas.py
    "DEFAULT_SCHEMA": "unified_pipeline.stage4.schemas",
    "FIELD_DESCRIPTIONS": "unified_pipeline.stage4.schemas",
    "FIELD_SCHEMAS": "unified_pipeline.stage4.schemas",
    "FIELD_SCHEMA_CONFIG_PATH": "unified_pipeline.stage4.schemas",
    "FIELD_SCHEMA_VERSION": "unified_pipeline.stage4.schemas",
    "TAXONOMY_LABELS": "unified_pipeline.stage4.schemas",
    "get_active_schemas": "unified_pipeline.stage4.schemas",
    "get_field_schema": "unified_pipeline.stage4.schemas",
    "get_taxonomy_label": "unified_pipeline.stage4.schemas",
    "load_field_schemas_from_config": "unified_pipeline.stage4.schemas",
    # defined directly in the facade -- never moved out
    "process_cv": _FACADE_MODULE,
    "run_validation": _FACADE_MODULE,
}


def _assert_symbol_is_reexported_from_its_owner(name):
    """Shared body for both parametrized tests below.

    hasattr() alone passes just as well if the facade reimplements a name
    with its own body instead of re-exporting the real one -- both bugs look
    identical from the caller's side until the two copies drift. So beyond
    "does the facade have this name", also check it is not a second, separate
    definition: either the exact same object as the owning submodule's (for
    the moved names) or still genuinely defined in the facade itself (for
    process_cv/run_validation, which never moved).
    """
    facade = importlib.import_module(_FACADE_MODULE)
    assert hasattr(facade, name), (
        f"'{name}' is no longer importable from stage_4_field_extractor. "
        f"If the split moved it, re-export it from this module -- callers in "
        f"run_full_pipeline.py, the backend orchestrator and the test suites "
        f"import stage 4 names by this path."
    )
    facade_value = getattr(facade, name)
    owning_module_path = _NAME_TO_OWNING_MODULE[name]

    if owning_module_path == _FACADE_MODULE:
        # Never moved: still expected to be defined in the facade itself, not
        # shadowed by an import of a same-named symbol from a submodule.
        actual_module = getattr(facade_value, "__module__", _FACADE_MODULE)
        assert actual_module == _FACADE_MODULE, (
            f"'{name}' was expected to still be defined directly in "
            f"{_FACADE_MODULE}, but it now resolves to something defined in "
            f"'{actual_module}' -- if it moved into a stage4/ submodule on "
            f"purpose, update _NAME_TO_OWNING_MODULE in this test."
        )
        return

    owning_module = importlib.import_module(owning_module_path)
    assert hasattr(owning_module, name), (
        f"'{name}' is re-exported from the facade but {owning_module_path} "
        f"no longer defines it -- the re-export line in stage_4_field_"
        f"extractor.py now points at a stale or renamed symbol."
    )
    owning_value = getattr(owning_module, name)
    assert facade_value is owning_value, (
        f"'{name}' on the facade is a DIFFERENT object than "
        f"{owning_module_path}.{name}. That means the facade defines its own "
        f"copy (a reimplementation or a wrapper) instead of re-exporting the "
        f"real one -- the two will silently drift, and callers/tests that "
        f"patch one will not affect the other (the #496 lesson)."
    )


@pytest.mark.parametrize("name", STAGE4_IMPORT_SURFACE, ids=lambda n: f"public:{n}")
def test_stage4_public_symbol_is_still_importable(name):
    """Each public name the repo could import from stage_4_field_extractor
    must resolve to the same object the owning submodule defines.

    Fails at the point a split moves a symbol out without re-exporting it, or
    re-exports a lookalike instead of the real thing.
    """
    _assert_symbol_is_reexported_from_its_owner(name)


@pytest.mark.parametrize(
    "name", LEGACY_PRIVATE_IMPORT_SURFACE, ids=lambda n: f"legacy-private:{n}"
)
def test_stage4_legacy_private_symbol_is_still_importable(name):
    """Same contract as the public surface, for the underscore-prefixed names
    kept only because existing tests import them directly (see module
    docstring). Kept as a separate test id so a failure here reads as
    "a backward-compat import broke", not "the public API broke".
    """
    _assert_symbol_is_reexported_from_its_owner(name)


def test_the_surface_lists_are_not_silently_empty_or_malformed():
    """Guard the guard.

    A refactor that reduced either tuple to () would make its parametrized
    tests vacuously pass by generating zero cases. Checked structurally --
    non-empty, no duplicate name within a list, no name shared between the
    two lists, and each list actually respects the public/private-compat
    naming split it claims to -- instead of pinning the count to a magic
    number that is redundant with the list itself and has to be
    hand-updated (and could be hand-updated wrong) every time an entry is
    added or removed.
    """
    assert STAGE4_IMPORT_SURFACE, "STAGE4_IMPORT_SURFACE must not be empty"
    assert LEGACY_PRIVATE_IMPORT_SURFACE, "LEGACY_PRIVATE_IMPORT_SURFACE must not be empty"
    assert len(STAGE4_IMPORT_SURFACE) == len(set(STAGE4_IMPORT_SURFACE)), (
        "duplicate name in STAGE4_IMPORT_SURFACE"
    )
    assert len(LEGACY_PRIVATE_IMPORT_SURFACE) == len(set(LEGACY_PRIVATE_IMPORT_SURFACE)), (
        "duplicate name in LEGACY_PRIVATE_IMPORT_SURFACE"
    )
    assert not (set(STAGE4_IMPORT_SURFACE) & set(LEGACY_PRIVATE_IMPORT_SURFACE)), (
        "a name is tracked in both STAGE4_IMPORT_SURFACE and "
        "LEGACY_PRIVATE_IMPORT_SURFACE -- it belongs in exactly one"
    )
    assert all(not n.startswith("_") for n in STAGE4_IMPORT_SURFACE), (
        "STAGE4_IMPORT_SURFACE should hold only public (non-underscore) "
        "names -- move underscore-prefixed names to LEGACY_PRIVATE_IMPORT_SURFACE"
    )
    assert all(n.startswith("_") for n in LEGACY_PRIVATE_IMPORT_SURFACE), (
        "LEGACY_PRIVATE_IMPORT_SURFACE should hold only underscore-prefixed names"
    )
    all_names = set(STAGE4_IMPORT_SURFACE) | set(LEGACY_PRIVATE_IMPORT_SURFACE)
    assert all_names == set(_NAME_TO_OWNING_MODULE), (
        "_NAME_TO_OWNING_MODULE is out of sync with the two surface lists -- "
        "every tracked name needs an owning-module entry, and vice versa"
    )


def test_loaded_schemas_cache_ownership():
    """`_LOADED_SCHEMAS` (the lazily-initialised schema cache) must be owned
    by `unified_pipeline.stage4.schemas` alone.

    Two failure modes, both covered: a re-export appearing on the facade
    (a stale second binding the #496 split left as a lesson -- the facade
    copy would stay None forever while `stage4.schemas` populates its own),
    and the cache disappearing from its owning module entirely (which would
    mean the deliberate-absence rationale in this file's docstring is stale
    and someone renamed or removed the cache without updating it).
    """
    facade = importlib.import_module(_FACADE_MODULE)
    schemas = importlib.import_module("unified_pipeline.stage4.schemas")
    assert not hasattr(facade, "_LOADED_SCHEMAS"), (
        "_LOADED_SCHEMAS is a mutable lazy-init cache owned by "
        "unified_pipeline.stage4.schemas; re-exporting it from the facade "
        "creates a permanently-stale second binding (#496 lesson)."
    )
    assert hasattr(schemas, "_LOADED_SCHEMAS"), (
        "unified_pipeline.stage4.schemas no longer defines _LOADED_SCHEMAS -- "
        "if it was renamed or removed, update this test and the module "
        "docstring's rationale for omitting it from the facade."
    )
