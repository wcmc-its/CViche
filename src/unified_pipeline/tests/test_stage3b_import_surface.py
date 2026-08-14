"""Import-surface contract for the stage 3b module split (#522).

`stage_3b_entry_classifier.py` (2,560 lines on dev) is decomposed into
`stage3b/{context,io,prompt,classify}.py`. The decomposition must not change
what the rest of the repository can import from the old module, because the
callers are spread across code the split does not touch:

  - `run_full_pipeline.py` and the backend `orchestrator.py` are PARALLEL
    drivers sharing no code, so a name that moves has to be fixed in both,
    and a miss lands silently in the one nobody ran.
  - `stage_3_taxonomy_mapper.py` imports `run_stage_3b` by the BARE module
    name (`from stage_3b_entry_classifier import ...`), relying on
    `sys.path` containing `src/unified_pipeline`.
  - Six test files (five pipeline, one backend) import nine distinct names.

So the rule, same as the stage 6 split (#398/#500): move the implementation
wherever separation of concerns says it belongs, then re-export the name from
this module. Removing a name from the pinned tuple below is a breaking change
and is the one the split is most likely to make by accident.

    python3 -m pytest src/unified_pipeline/tests/test_stage3b_import_surface.py -p no:cacheprovider

Self-contained: imports only, no DB, no network, no LLM, no PII.
"""

import importlib
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


#: Every name imported from `stage_3b_entry_classifier` anywhere in the
#: repository, measured on 2026-08-14 against origin/dev @ 5a3090b by walking
#: the AST of all 441 .py files for `ImportFrom` nodes targeting this module:
#: nine names across nine importing files.
#:
#: `_safe_float` and `_CLASSIFICATION_SYSTEM_PROMPT_TEMPLATE` are private but
#: really imported (backend type-drift tests; prompt pin tests), so they are
#: part of the contract whether or not they should have been. Narrowing the
#: surface is a SEPARATE change from relocating code.
STAGE3B_IMPORT_SURFACE = (
    "TaxonomyContext",
    "_CLASSIFICATION_SYSTEM_PROMPT_TEMPLATE",
    "_safe_float",
    "build_mapping_index",
    "classify_entries_batch",
    "detect_duplicates",
    "load_stage_3a_mappings",
    "load_taxonomy",
    "run_stage_3b",
)

#: Names no file currently imports but which the facade re-exports so that the
#: split does not silently narrow what the module offers: the remainder of its
#: pre-split public surface plus `logger`, which two caplog tests reach for as
#: a module attribute (`stage_3b.logger.name`).
STAGE3B_REEXPORTED_SURFACE = (
    "_BatchStats",
    "_build_taxonomy_ref_for_batch",
    "_classify_one_batch",
    "_normalize_taxonomy_mappings",
    "build_taxonomy_codes_for_prompt",
    "get_taxonomy_context",
    "group_entries_by_hierarchy",
    "logger",
    "reconnect_fragments",
    "validate_t_classifications",
)


@pytest.mark.parametrize("name", STAGE3B_IMPORT_SURFACE + STAGE3B_REEXPORTED_SURFACE)
def test_stage3b_symbol_is_still_importable(name):
    """Each name the repo imports from stage_3b_entry_classifier must resolve.

    Fails at the point a split moves a symbol out without re-exporting it.
    """
    mod = importlib.import_module("unified_pipeline.stage_3b_entry_classifier")
    assert hasattr(mod, name), (
        f"'{name}' is no longer importable from stage_3b_entry_classifier. "
        f"If the split moved it, re-export it from that module -- callers in "
        f"run_full_pipeline.py, the backend orchestrator, "
        f"stage_3_taxonomy_mapper.py and six test files import by this name."
    )


def test_reexports_are_the_same_objects_not_copies():
    """The facade must alias the submodule implementations, not duplicate them.

    A copy would mean a fix (or a monkeypatch) applied at one address silently
    misses the other -- the exact state-splitting failure #496 hit when a
    moved global kept a second life at its old path.
    """
    facade = importlib.import_module("unified_pipeline.stage_3b_entry_classifier")
    classify = importlib.import_module("unified_pipeline.stage3b.classify")
    context = importlib.import_module("unified_pipeline.stage3b.context")

    assert facade.classify_entries_batch is classify.classify_entries_batch
    assert facade.validate_t_classifications is classify.validate_t_classifications
    assert facade.TaxonomyContext is context.TaxonomyContext


def test_classify_module_is_the_single_llm_patch_point():
    """Every stage3b submodule call site of `call_llm` lives in classify.py.

    Tests stub the LLM by rebinding ONE module attribute,
    `unified_pipeline.stage3b.classify.call_llm`. If a second stage3b module
    grew its own `call_llm` import, that patch would silently stop covering
    it (#496) and a "mocked" test could hit the real Bedrock/OpenAI client.
    (The facade keeps its own import solely for the flag-gated
    block-coherence closure in run_stage_3b, which is OFF by default.)
    """
    classify = importlib.import_module("unified_pipeline.stage3b.classify")
    assert hasattr(classify, "call_llm")

    for other in ("context", "io", "prompt"):
        mod = importlib.import_module(f"unified_pipeline.stage3b.{other}")
        assert not hasattr(mod, "call_llm"), (
            f"unified_pipeline.stage3b.{other} has its own 'call_llm' binding; "
            f"stubbing classify.call_llm no longer covers every call site."
        )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
