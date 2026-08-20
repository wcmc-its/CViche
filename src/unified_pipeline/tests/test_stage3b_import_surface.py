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

The paragraph above is historical context, not the contract: it was measured
once by hand and will drift as the repo grows. The contract itself --
"every name any file imports from this module is in STAGE3B_IMPORT_SURFACE"
-- is re-derived from the actual AST of the repository by
`test_declared_surface_covers_every_repo_import` below, on every run.

Self-contained: imports only, no DB, no network, no LLM, no PII.
"""

import ast
import importlib
import sys
import types
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


#: Every name imported from `stage_3b_entry_classifier` anywhere in the
#: repository. Historically measured on 2026-08-14 against origin/dev @
#: 5a3090b by walking the AST of all 441 .py files for `ImportFrom` nodes
#: targeting this module (nine names across nine importing files); now
#: re-verified on every test run by `test_declared_surface_covers_every_repo_import`
#: instead of trusted as a one-time measurement.
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

#: Every re-exported symbol mapped to the stage3b submodule that owns its
#: implementation, so the facade's copy of it can be checked for object
#: IDENTITY (not just presence) against the submodule's. `run_stage_3b` and
#: `logger` are excluded: both are defined directly on the facade rather than
#: aliased from a submodule, so there is no second object for either to
#: duplicate.
STAGE3B_REEXPORT_IMPLEMENTATIONS = {
    "TaxonomyContext": "context",
    "build_mapping_index": "context",
    "get_taxonomy_context": "context",
    "_normalize_taxonomy_mappings": "io",
    "_safe_float": "io",
    "load_stage_3a_mappings": "io",
    "load_taxonomy": "io",
    "_CLASSIFICATION_SYSTEM_PROMPT_TEMPLATE": "prompt",
    "build_taxonomy_codes_for_prompt": "prompt",
    "_BatchStats": "classify",
    "_build_taxonomy_ref_for_batch": "classify",
    "_classify_one_batch": "classify",
    "classify_entries_batch": "classify",
    "detect_duplicates": "classify",
    "group_entries_by_hierarchy": "classify",
    "reconnect_fragments": "classify",
    "validate_t_classifications": "classify",
}

#: Both spellings callers use for the facade module in a `from ... import`
#: statement: dotted (`unified_pipeline.stage_3b_entry_classifier`, the
#: normal case) and bare (`stage_3b_entry_classifier`, what
#: `stage_3_taxonomy_mapper.py` uses because it relies on `sys.path`
#: containing `src/unified_pipeline`).
_STAGE3B_FACADE_MODULE_NAMES = frozenset(
    {"unified_pipeline.stage_3b_entry_classifier", "stage_3b_entry_classifier"}
)


def _stage3b_facade_import_names(root: Path) -> set:
    """Every name any `.py` file under `root` imports via
    `from <facade> import name`, across both spellings of the facade module.

    Walks the AST (not a regex/grep) so an aliased import
    (`import classify_entries_batch as classify_batch`) still records the
    name AS IMPORTED, and so a `from <facade> import *` -- which can't name a
    symbol statically -- fails loudly instead of silently contributing
    nothing to the discovered set.
    """
    names = set()
    for path in root.rglob("*.py"):
        if ".git" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in _STAGE3B_FACADE_MODULE_NAMES:
                for alias in node.names:
                    if alias.name == "*":
                        raise AssertionError(
                            f"{path}: 'from ... import *' targets the stage3b "
                            f"facade and can't be checked against the "
                            f"declared import surface -- name the import "
                            f"explicitly."
                        )
                    names.add(alias.name)
    return names


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


def test_declared_surface_covers_every_repo_import():
    """STAGE3B_IMPORT_SURFACE must cover every name the repo actually imports.

    Re-derives the "who imports what" fact from the AST instead of trusting
    the historical comment above it. A caller that adds
    `from stage_3b_entry_classifier import some_new_name` and forgets to
    update the tuple fails here -- it would otherwise pass silently, since
    `test_stage3b_symbol_is_still_importable` only walks the DECLARED names,
    never discovers new ones.
    """
    repo_root = _SRC.parent
    discovered = _stage3b_facade_import_names(repo_root)
    undeclared = discovered - set(STAGE3B_IMPORT_SURFACE)
    assert not undeclared, (
        f"These names are imported from stage_3b_entry_classifier somewhere "
        f"in the repo but are missing from STAGE3B_IMPORT_SURFACE: "
        f"{sorted(undeclared)}. Add them to the tuple and confirm the facade "
        f"still re-exports them."
    )


def test_undeclared_import_is_detected_by_the_scanner(tmp_path):
    """Negative case: an untracked legacy import must fail the contract.

    Writes a synthetic caller that imports a name the contract doesn't
    declare and proves the scanner both discovers it and disagrees with
    STAGE3B_IMPORT_SURFACE -- i.e. that `test_declared_surface_covers_every_repo_import`
    would fail on it, rather than passing by construction because the
    scanner only ever sees the real (currently-clean) repository.
    """
    caller = tmp_path / "some_untracked_caller.py"
    caller.write_text(
        "from unified_pipeline.stage_3b_entry_classifier import totally_new_symbol\n"
    )

    discovered = _stage3b_facade_import_names(tmp_path)

    assert discovered == {"totally_new_symbol"}
    assert discovered - set(STAGE3B_IMPORT_SURFACE) == {"totally_new_symbol"}


def _assert_same_object(facade_value, impl_value, label):
    """Identity, not equality: a copy would mean a fix (or a monkeypatch)
    applied at one address silently misses the other -- the exact
    state-splitting failure #496 hit when a moved global kept a second life
    at its old path.
    """
    assert facade_value is impl_value, (
        f"facade's {label} is not the same object as the submodule's -- the "
        f"facade is holding a copy, not an alias."
    )


@pytest.mark.parametrize(
    "name,submodule", sorted(STAGE3B_REEXPORT_IMPLEMENTATIONS.items())
)
def test_reexports_are_the_same_objects_not_copies(name, submodule):
    """The facade must alias every submodule implementation, not duplicate it."""
    facade = importlib.import_module("unified_pipeline.stage_3b_entry_classifier")
    impl = importlib.import_module(f"unified_pipeline.stage3b.{submodule}")
    _assert_same_object(getattr(facade, name), getattr(impl, name), name)


def test_identity_check_catches_a_duplicated_implementation():
    """Negative case: prove _assert_same_object fails on a copy, not a move.

    Two distinct function objects with identical behavior are what a
    duplicated (rather than relocated) implementation looks like.
    """

    def original():
        return None

    def duplicate():
        return None

    with pytest.raises(AssertionError):
        _assert_same_object(original, duplicate, "example")


def _assert_call_llm_not_leaked(module_name, module):
    assert not hasattr(module, "call_llm"), (
        f"unified_pipeline.stage3b.{module_name} has its own 'call_llm' "
        f"binding; stubbing classify.call_llm no longer covers every call "
        f"site."
    )


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
        _assert_call_llm_not_leaked(other, mod)


def test_single_llm_patch_point_check_catches_a_leaked_binding():
    """Negative case: prove _assert_call_llm_not_leaked fails on a real leak."""
    leaked = types.SimpleNamespace(call_llm=lambda *a, **k: None)
    with pytest.raises(AssertionError):
        _assert_call_llm_not_leaked("poisoned", leaked)


def _stage3b_modules_importing_call_llm(package_dir: Path) -> set:
    """Every stage3b package file that imports `call_llm` from `llm_client`,
    matched on the name AS IMPORTED (not the local alias) and across both
    relative (`from ..llm_client import call_llm`) and absolute
    (`from unified_pipeline.llm_client import call_llm`) spellings -- so
    `from ..llm_client import call_llm as invoke_model` still counts.
    """
    importers = set()
    for path in sorted(package_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module in ("llm_client", "unified_pipeline.llm_client")
                and any(alias.name == "call_llm" for alias in node.names)
            ):
                importers.add(path.name)
    return importers


def test_call_llm_is_imported_only_by_classify_module():
    """AST-level check that an aliased import can't bypass the hasattr check above.

    `from ..llm_client import call_llm as invoke_model` would pass
    `hasattr(mod, "call_llm")` in the test above -- the attribute exists
    under the name `invoke_model`, not `call_llm` -- while still creating a
    second, unstubbed LLM call site. This scans the import statement itself
    and matches on the name as imported, so aliasing can't hide it.
    """
    package_dir = _SRC / "unified_pipeline" / "stage3b"
    importers = _stage3b_modules_importing_call_llm(package_dir)
    assert importers == {"classify.py"}, (
        f"Expected only classify.py to import call_llm from llm_client; "
        f"found: {sorted(importers)}. A second import site -- even under a "
        f"different local name -- creates a second patch point tests "
        f"silently stop covering (#496)."
    )
