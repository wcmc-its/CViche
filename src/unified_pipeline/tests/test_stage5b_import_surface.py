"""Import-surface contract for the stage 5b module split (#523).

`stage_5b_institution_enrichment.py` is decomposed into
`unified_pipeline/stage5b/` (normalize.py, cache.py, lookup.py), with the old
module staying as the entry point. Per the stage 6 precedent
(test_stage6_import_surface.py, #500), the decomposition must not change what
the rest of the repository can import from the old path:

  - `run_full_pipeline.py` and the backend `orchestrator.py` are PARALLEL
    drivers sharing no code; both do
    `from unified_pipeline.stage_5b_institution_enrichment import run_stage5b`.
  - `tests/test_stage_5b_institution_cache_key.py` imports
    `_institution_cache_key` (the #582 regression guard).
  - `web_interface/backend/tests/test_llm_type_drift_hardening.py` imports
    `_build_owner_context`.
  - `tests/test_stage_artifact_model_provenance.py` reaches
    `lookup_institutions_llm` and `run_stage5b` as module attributes.

`call_llm` is deliberately ABSENT from this surface. It was importable from
the old module only as a side effect of its own import; after the split it
lives in `stage5b/lookup.py`, and a re-export here would be a trap -- patching
the old path's `call_llm` would no longer affect the lookup (the #496
mock-binding lesson). Dropping it makes a stale patch target fail loudly at
`monkeypatch.setattr` instead of silently patching nothing.

    python3 -m pytest src/unified_pipeline/tests/test_stage5b_import_surface.py -p no:cacheprovider

Self-contained: imports only, no DB, no network, no LLM, no PII.
"""

import importlib
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


#: Every module-level name that existed on `stage_5b_institution_enrichment`
#: on origin/dev @ 5a3090b before the split -- not just the names currently
#: imported elsewhere, because stage-5b consumers are mostly tests and the two
#: pipeline drivers, and a name that drops out of the surface fails at IMPORT
#: time in whichever driver nobody ran (the stage 6 lesson).
#:
#: Adding to this list is fine. REMOVING from it is a breaking change.
STAGE5B_IMPORT_SURFACE = (
    "BATCH_SIZE",
    "CACHE_FILE",
    "INSTITUTION_CACHE",
    "INSTITUTION_CODES",
    "INSTITUTION_SYSTEM_PROMPT",
    "OLD_CACHE_FILE",
    "OUTPUT_DIR",
    "_build_context_string",
    "_build_owner_context",
    "_institution_cache_key",
    "_owner_context_hash",
    "enrich_entry_with_result",
    "format_location",
    "is_likely_internal_unit",
    "load_institution_cache",
    "lookup_institutions_llm",
    "main",
    "normalize_institution_name",
    "run_stage5b",
    "save_institution_cache",
)


@pytest.mark.parametrize("name", STAGE5B_IMPORT_SURFACE)
def test_stage5b_symbol_is_still_importable(name):
    """Each name the repo could import from the old path must resolve.

    Fails at the point a split moves a symbol out without re-exporting it.
    """
    mod = importlib.import_module("unified_pipeline.stage_5b_institution_enrichment")
    assert hasattr(mod, name), (
        f"'{name}' is no longer importable from stage_5b_institution_enrichment. "
        f"If the split moved it, re-export it from that module -- callers in "
        f"run_full_pipeline.py, the backend orchestrator, and the test suite "
        f"import stage 5b names by this path."
    )


def test_institution_cache_facade_tracks_reassignment():
    """The old path must serve the cache module's CURRENT dict, not a snapshot.

    load_institution_cache() REASSIGNS the global (``global`` + rebind). A
    static `from ...cache import INSTITUTION_CACHE` alias in the facade would
    freeze the pre-load dict, and readers of the old path would silently see
    stale state while cache.py reads and writes the new one -- exactly the
    state-splitting failure the #496 split hit with its mutable globals.
    """
    facade = importlib.import_module("unified_pipeline.stage_5b_institution_enrichment")
    cache = importlib.import_module("unified_pipeline.stage5b.cache")
    original = cache.INSTITUTION_CACHE
    try:
        cache.INSTITUTION_CACHE = {"sentinel|deadbeef00000000": None}
        assert facade.INSTITUTION_CACHE is cache.INSTITUTION_CACHE, (
            "stage_5b_institution_enrichment.INSTITUTION_CACHE is a stale "
            "snapshot -- it must delegate to stage5b.cache's current binding"
        )
    finally:
        cache.INSTITUTION_CACHE = original


def test_call_llm_is_not_on_the_facade():
    """A patchable-looking `call_llm` on the old path would patch nothing.

    The real binding the lookup uses lives in `unified_pipeline.stage5b.lookup`;
    keeping a decoy on the facade would let `monkeypatch.setattr` succeed while
    the production code path calls the unpatched original.
    """
    mod = importlib.import_module("unified_pipeline.stage_5b_institution_enrichment")
    assert not hasattr(mod, "call_llm"), (
        "call_llm reappeared on stage_5b_institution_enrichment -- patch "
        "targets must point at unified_pipeline.stage5b.lookup instead"
    )


def test_the_surface_list_is_not_silently_empty():
    """Guard the guard: () would make the parametrized tests vacuously pass."""
    assert len(STAGE5B_IMPORT_SURFACE) == 20, (
        "the pinned stage 5b import surface changed size -- if that is "
        "intentional, update the count and say why in the commit message"
    )


if __name__ == "__main__":
    for _n in STAGE5B_IMPORT_SURFACE:
        test_stage5b_symbol_is_still_importable(_n)
    test_institution_cache_facade_tracks_reassignment()
    test_call_llm_is_not_on_the_facade()
    test_the_surface_list_is_not_silently_empty()
    print("OK")
