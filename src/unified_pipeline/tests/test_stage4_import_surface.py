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

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


#: Every top-level name defined in `stage_4_field_extractor` on origin/dev
#: @ 5a3090b except `_LOADED_SCHEMAS` (see module docstring), measured on
#: 2026-08-14 by walking the module AST. The subset the repo actually imports
#: (measured the same day by grepping every .py in the worktree, archive/
#: included) is: process_cv, coerce_field_value_types, needs_llm_recovery,
#: extract_cv_owner_name, add_target_names, _entry_end_year,
#: _owner_affiliation_lines, extract_fields_batch, infer_cv_owner_location,
#: extract_fields_from_mapped_entries. The full set is pinned anyway: open PR
#: branches may import names the measurement day did not see.
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
    "_OWNER_AFFILIATION_FIELDS",
    "_entry_end_year",
    "_get_field_descriptions",
    "_owner_affiliation_lines",
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


@pytest.mark.parametrize("name", STAGE4_IMPORT_SURFACE)
def test_stage4_symbol_is_still_importable(name):
    """Each name the repo could import from stage_4_field_extractor must resolve.

    Fails at the point a split moves a symbol out without re-exporting it.
    """
    mod = importlib.import_module("unified_pipeline.stage_4_field_extractor")
    assert hasattr(mod, name), (
        f"'{name}' is no longer importable from stage_4_field_extractor. "
        f"If the split moved it, re-export it from this module -- callers in "
        f"run_full_pipeline.py, the backend orchestrator and the test suites "
        f"import stage 4 names by this path."
    )


def test_the_surface_list_is_not_silently_empty():
    """Guard the guard.

    A refactor that reduced the tuple to () would make every test above
    vacuously pass by generating zero cases. Pin the count measured on dev.
    """
    assert len(STAGE4_IMPORT_SURFACE) == 32, (
        "the pinned stage 4 import surface changed size -- if that is "
        "intentional, update the count and say why in the commit message"
    )


def test_loaded_schemas_cache_is_not_reexported():
    """The absence is deliberate; a re-export appearing here means someone
    reintroduced the stale-second-binding hazard this split avoided."""
    mod = importlib.import_module("unified_pipeline.stage_4_field_extractor")
    assert not hasattr(mod, "_LOADED_SCHEMAS"), (
        "_LOADED_SCHEMAS is a mutable lazy-init cache owned by "
        "unified_pipeline.stage4.schemas; re-exporting it from the facade "
        "creates a permanently-stale second binding (#496 lesson)."
    )


def test_facade_and_extraction_share_one_orchestrator_binding():
    """The facade's re-export must BE the extraction module's function.

    If someone replaces the re-export with a wrapper, tests that rebind
    extraction internals on `stage4.extraction` would silently diverge from
    callers entering through the facade.
    """
    facade = importlib.import_module("unified_pipeline.stage_4_field_extractor")
    extraction = importlib.import_module("unified_pipeline.stage4.extraction")
    assert facade.extract_fields_from_mapped_entries is (
        extraction.extract_fields_from_mapped_entries
    )


if __name__ == "__main__":
    for _n in STAGE4_IMPORT_SURFACE:
        test_stage4_symbol_is_still_importable(_n)
    test_the_surface_list_is_not_silently_empty()
    test_loaded_schemas_cache_is_not_reexported()
    test_facade_and_extraction_share_one_orchestrator_binding()
    print("OK")
