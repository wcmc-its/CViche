"""Import-surface contract for the stage 6 module split (#398).

`stage_6_word_template.py` is 9,530 lines and is being decomposed into
submodules. The decomposition must not change what the rest of the repository
can import from it, because the callers are spread across code that does not
get touched by the split:

  - `run_full_pipeline.py` and the backend `orchestrator.py` are PARALLEL
    implementations sharing no code, so a name that moves has to be fixed in
    both, and a miss lands silently in the one nobody ran.
  - `run_doctor.py` and the corpus tooling import render helpers directly.
  - Eight open PRs carry regression tests that import these names. If a symbol
    moves without a re-export, those tests fail at IMPORT time during rework and
    look like the fix was lost, when the fix is fine and only the address
    changed.

So the rule for every split PR is: move the implementation wherever separation
of concerns says it belongs, then re-export the name from this module. A
one-line `from .stage6.text import _phone_cell_text` at the bottom of
`stage_6_word_template.py` keeps every existing caller working.

**Fifteen of these names are private** (leading underscore). That is deliberate,
not an oversight in this test: privacy here describes intent for new callers,
but the existing imports are real and load-bearing, so they are part of the
contract whether or not they should have been. Narrowing the surface is
worthwhile, but it is a SEPARATE change from relocating code -- doing both at
once means a failure cannot be attributed to either.

    python3 -m pytest src/unified_pipeline/tests/test_stage6_import_surface.py -p no:cacheprovider

Self-contained: imports only, no DB, no network, no LLM, no PII.
"""

import ast
import importlib
import subprocess
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


#: Every name imported from `stage_6_word_template` anywhere in the repository,
#: measured against origin/dev @ 5e3d8fd on 2026-07-28 by walking the AST of all
#: 350 .py files for `ImportFrom` nodes targeting this module, then re-measured
#: on 2026-09-29 (#667): `test_live_import_walk_is_pinned_by_the_manifest`
#: below found 11 live names the hand-captured snapshot had missed. That test
#: re-runs the walk on every CI run, so this tuple can no longer go stale
#: silently.
#:
#: Adding to this list is fine. REMOVING from it is a breaking change, and it is
#: the one the split is most likely to make by accident.
STAGE6_IMPORT_SURFACE = (
    "DEDUP_FUSED_BLOB_RECORD_LINES",
    "GEO_SCOPE_FAILURE_STAT",
    "PII_REDACTED_NOTICE",
    "RECLASSIFY_FAILURE_STAT",
    "RENDER_ROUTED_CODES",
    "RETIRED_TAXONOMY_CODES",
    "TAXONOMY_TO_SECTION",
    "TEMPLATE_PATH",
    "WCMTemplateGenerator",
    "_TAXONOMY_WARNED_CONFUSIONS",
    "_address_cell_text",
    "_clean_inline_tabs",
    "_committee_cell_text",
    "_dates_overlap_or_match",
    "_drop_is_safe",
    "_get_cleaned_institution_name",
    "_labels_its_own_address_slots",
    "_labels_its_own_phone_slots",
    "_parse_date_components",
    "_phone_cell_text",
    "_pii_fragments",
    "_record_lines",
    "_squash",
    "_strip_taxonomy_code",
    "deduplicate_entries",
    "extract_sort_date",
    "format_date_for_section",
    "grant_status_rebucket_target",
    "normalize_retired_code",
    "parse_reclassified_segments",
    "rendered_extraction_coverage",
    "run_stage6",
    "segment_already_rendered",
    "split_fused_citation_entries",
)

#: Same, for the shared render helpers. The live walk (#667) found four more
#: live imports than the original single-name pin, `entry_lines` among them --
#: the earlier note that it was "deliberately absent" predates its landing.
RENDER_CHECK_IMPORT_SURFACE = (
    "CELL_SEPARATOR",
    "entry_fragments",
    "entry_lines",
    "rejoin_wrapped_row",
    "wrapped_row_text",
)


@pytest.mark.parametrize("name", STAGE6_IMPORT_SURFACE)
def test_stage6_symbol_is_still_importable(name):
    """Each name the repo imports from stage_6_word_template must resolve.

    Fails at the point a split moves a symbol out without re-exporting it.
    """
    mod = importlib.import_module("unified_pipeline.stage_6_word_template")
    assert hasattr(mod, name), (
        f"'{name}' is no longer importable from stage_6_word_template. "
        f"If the split moved it, re-export it from this module -- callers in "
        f"run_full_pipeline.py, the backend orchestrator, run_doctor.py and "
        f"eight open PRs import it by this name."
    )


@pytest.mark.parametrize("name", RENDER_CHECK_IMPORT_SURFACE)
def test_render_check_symbol_is_still_importable(name):
    mod = importlib.import_module("unified_pipeline.core.render_check")
    assert hasattr(mod, name), (
        f"'{name}' is no longer importable from core.render_check."
    )


#: Names relocated by the #398 residue split into `stage6/dedup.py` and
#: `stage6/render_check.py`. Each is pinned twice: it must still resolve from
#: `stage_6_word_template` (the public surface -- 7 of these are also in
#: STAGE6_IMPORT_SURFACE above, the rest are covered only here), and it must
#: resolve in its new home, so a later re-shuffle inside `stage6/` cannot
#: silently strand either address.
RELOCATED_BY_398_RESIDUE = {
    "unified_pipeline.stage6.dedup": (
        "DEDUP_FULL_CONTAINMENT_MIN_TOKENS",
        "DEDUP_FUSED_BLOB_RECORD_LINES",
        "_STOP_WORDS",
        "_drop_is_safe",
        "_entry_signature_words",
        "_entry_title_words",
        "_significant_words",
        "deduplicate_entries",
    ),
    "unified_pipeline.stage6.render_check": (
        "RECORD_DATE_LINE_MIN_CHARS",
        "RENDER_PIECE_MIN_CHARS",
        "RENDER_PIECE_WINDOW",
        "RENDER_TOKEN_MIN_COUNT",
        "RENDER_TOKEN_OVERLAP",
        "RETIRED_TAXONOMY_CODES",
        "UNRENDERED_MIN_RECORD_LINES",
        "_COLUMN_HEADER_WORDS",
        "_IDENTIFYING_FIELDS",
        "_MONTH_WORDS",
        "_RECORD_DATE_PREFIX_RE",
        "_RENDER_TOKEN_RE",
        "_entry_pieces",
        "_is_column_header_row",
        "_looks_like_record",
        "_norm",
        "_record_lines",
        "_record_rendered",
        "_value_is_datelike",
        "normalize_retired_code",
        "segment_already_rendered",
    ),
}

#: Names imported from a `stage6/` home that `stage_6_word_template` does NOT
#: re-export, so they cannot sit in RELOCATED_BY_398_RESIDUE (whose test asserts
#: the legacy address resolves to the same object). Found by the live walk (#667).
HOME_ONLY_IMPORTS = {
    "unified_pipeline.stage6.dedup": ("_companion_title", "_dates_compatible", "_different_institution",
                                      "_distinct_bare_names", "_lists_name",
                                      "_names_a_sibling", "_names_record", "_other_journal_same_row",
                                      "_place_only_event", "_record_name", "_title_only_fragment",
                                      "recovered_row_already_rendered","_row_residue"),
}

_RELOCATED_CASES = [
    (home, name)
    for home, names in sorted(RELOCATED_BY_398_RESIDUE.items())
    for name in names
]


@pytest.mark.parametrize("home,name", _RELOCATED_CASES)
def test_relocated_name_still_importable_from_stage6_module(home, name):
    """The old address is a facade over the new one, not a stale duplicate.

    hasattr() alone only proves both modules expose *a* name -- it would
    still pass if stage_6_word_template.py kept its own copy of the
    implementation while stage6/ grew a second, independent one. Comparing
    identity proves it is the SAME object: a straight re-export, one
    implementation, two addresses.
    """
    legacy = importlib.import_module("unified_pipeline.stage_6_word_template")
    new = importlib.import_module(home)
    assert hasattr(legacy, name), (
        f"'{name}' moved to {home} but is no longer re-exported from "
        f"stage_6_word_template -- existing callers import it by the old name."
    )
    assert getattr(legacy, name) is getattr(new, name), (
        f"'{name}' resolves in both stage_6_word_template and {home}, but to "
        f"two DIFFERENT objects -- stage_6_word_template is not re-exporting "
        f"the {home} implementation, it has a stale duplicate."
    )


@pytest.mark.parametrize("home,name", _RELOCATED_CASES)
def test_relocated_name_resolves_in_its_new_home(home, name):
    mod = importlib.import_module(home)
    assert hasattr(mod, name), f"'{name}' is not defined in {home}."


def test_the_relocated_surface_is_not_silently_empty():
    assert len(_RELOCATED_CASES) == 29, (
        "the #398-residue relocation pin changed size -- if that is "
        "intentional, update the count and say why in the commit message"
    )


#: Methods the test suite calls on a WCMTemplateGenerator INSTANCE, measured the
#: same way. This is a second contract, distinct from the module surface above:
#: those names are imported, these are reached for as class attributes, and the
#: module-level pin does not cover them.
#:
#: Learned the hard way. The first split moved `_set_table_border` out of the
#: class and `test_stage6_table_border.py` broke -- it was calling
#: `WCMTemplateGenerator._set_table_border(None, table)`, passing None for self.
#: Every module-surface test above still passed, because the symbol remained
#: importable; only the class attribute had gone.
#:
#: A split that moves one of these SHOULD fail here. That is the point: the
#: failure forces a deliberate choice -- update the callers, or leave a thin
#: delegating method behind -- instead of surfacing during PR rework, where it
#: looks like a lost fix rather than a moved address.
#:
#: `_extract_year_from_text` was REMOVED from this tuple by the parsing split.
#: The measurement that built the list counted `self._extract_year_from_text(...)`
#: inside the generator itself, which is an internal call, not a test reaching
#: through the class. Re-checked by name across dev and all nine open PR branches:
#: the only reference outside stage_6_word_template.py was this list. Removing a
#: name here is otherwise a breaking change -- the bar is proving no caller exists,
#: not finding it inconvenient.
STAGE6_CLASS_SURFACE = (
    "_add_remaining_to_appendix",
    "_add_track_change_insertion",
    "_add_track_change_pair",
    "_add_word_comment",
    "_degree_is_in_progress",
    "_extract_organization_from_award",
    "_fill_honors",
    "_fill_licensure",
    "_fill_positions",
    "_fill_research_summary",
    "_finalize_comments",
    "_merge_grouped_appointments",
    "_reconsider_appendix_entries",
    "_recover_unrendered_records",
    "_remove_instruction_box",
    "_split_award_year",
    "generate",
)


@pytest.mark.parametrize("name", STAGE6_CLASS_SURFACE)
def test_generator_method_is_still_on_the_class(name):
    """Each method the tests call on an instance must remain a class attribute."""
    mod = importlib.import_module("unified_pipeline.stage_6_word_template")
    assert hasattr(mod.WCMTemplateGenerator, name), (
        f"WCMTemplateGenerator.{name} is gone. If the split moved it out, either "
        f"update the callers under src/unified_pipeline/tests/ or leave a thin "
        f"delegating method -- {name} is called on an instance by the suite."
    )


def test_the_surface_list_is_not_silently_empty():
    """Guard the guard.

    A refactor that reduced these tuples to () would make every test above
    vacuously pass by generating zero cases. Pin the counts measured on dev.
    """
    assert len(STAGE6_IMPORT_SURFACE) == 34, (
        "the pinned stage 6 import surface changed size -- if that is "
        "intentional, update the count and say why in the commit message"
    )
    assert len(RENDER_CHECK_IMPORT_SURFACE) == 5
    assert len(STAGE6_CLASS_SURFACE) == 17


def test_private_names_are_a_deliberate_part_of_the_contract():
    """Document, in executable form, that most of this surface is private.

    If this ratio shifts a lot, someone is either narrowing the surface (good,
    but should be its own PR) or widening it (worth noticing).
    """
    private = [n for n in STAGE6_IMPORT_SURFACE if n.startswith("_")]
    assert len(private) == 15, (
        f"expected 15 private names in the stage 6 import surface, found "
        f"{len(private)}: {sorted(private)}"
    )


@pytest.mark.parametrize("home,name", sorted(
    (home, name) for home, names in HOME_ONLY_IMPORTS.items() for name in names
))
def test_home_only_import_resolves_in_its_home(home, name):
    assert hasattr(importlib.import_module(home), name), (
        f"'{name}' is not defined in {home}."
    )


#: Modules whose importers the live walk checks, and the manifest each one's
#: imported names must be a subset of. A module absent here is out of scope:
#: the other `stage6/` submodules are new homes with no legacy address to keep
#: stable, so a caller importing from them is not a contract this file pins.
LIVE_WALK_MANIFESTS = {
    "unified_pipeline.stage_6_word_template": frozenset(STAGE6_IMPORT_SURFACE),
    "unified_pipeline.core.render_check": frozenset(RENDER_CHECK_IMPORT_SURFACE),
    **{
        home: frozenset(names) | frozenset(HOME_ONLY_IMPORTS.get(home, ()))
        for home, names in RELOCATED_BY_398_RESIDUE.items()
    },
}

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_DIR_NAME = "src"
WILDCARD = "*"


def _tracked_python_files() -> list[str]:
    """Every git-tracked .py path, repo-relative. Fails loudly outside a checkout.

    Deliberately not skipped when git is unavailable: CI runs in a checkout, and
    a silent skip is how the hand-captured manifest went unverified. Untracked
    and gitignored trees (notably `archive/`) are NOT walked -- they are not
    part of what ships, and a caller there is invisible to this test.
    """
    try:
        out = subprocess.run(
            ["git", "ls-files", "*.py"],
            cwd=_REPO_ROOT, capture_output=True, text=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        pytest.fail(
            f"cannot list tracked files with `git ls-files` in {_REPO_ROOT}: {exc}"
        )
    return out.split()


def _dotted_module_of(rel_path: str) -> list[str]:
    """Dotted parts of a repo-relative .py path (`__init__` stays last).

    Files under `src/` are importable relative to it; anything else keeps its
    full path, which only matters for resolving that file's relative imports.
    """
    parts = Path(rel_path).with_suffix("").parts
    if parts and parts[0] == _SRC_DIR_NAME:
        parts = parts[1:]
    return list(parts)


def _target_module(node: ast.ImportFrom, file_parts: list[str]) -> str:
    """Absolute dotted module a `from ... import` node targets.

    The last part is the module's own name (or `__init__`), so dropping it
    yields the containing package for a plain module and a package alike.
    """
    if not node.level:
        return node.module or ""
    package = file_parts[:-1]
    base = package[: len(package) - (node.level - 1)]
    return ".".join([*base, *(node.module.split(".") if node.module else [])])


def _in_scope_imports(
    tree: ast.AST, rel: str
) -> set[tuple[str, str, str]]:
    """(file, target module, original name) for each in-scope `ImportFrom`.

    Aliases resolve to the original name (`import x as y` pins `x`). A wildcard
    is recorded as `*`, which no manifest contains, so it fails the subset
    check by design rather than being skipped. Plain `import a.b` and attribute
    access (`s6.name`, `from unified_pipeline import stage_6_word_template as
    s6`) are OUT OF SCOPE: only `ImportFrom` names are collected.
    """
    file_parts = _dotted_module_of(rel)
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        target = _target_module(node, file_parts)
        if target in LIVE_WALK_MANIFESTS:
            found.update((rel, target, alias.name) for alias in node.names)
    return found


def _live_imported_names() -> set[tuple[str, str, str]]:
    found = set()
    for rel in _tracked_python_files():
        tree = ast.parse((_REPO_ROOT / rel).read_text(encoding="utf-8"), filename=rel)
        found |= _in_scope_imports(tree, rel)
    return found


def _unpinned(found: set[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    return sorted(t for t in found if t[2] not in LIVE_WALK_MANIFESTS[t[1]])


def test_live_import_walk_is_pinned_by_the_manifest():
    """A new caller of an unpinned name fails here, naming the (file, name) pair.

    The manifests above were captured by hand once. This re-runs that walk over
    every tracked .py file so a PR that starts importing a new stage 6 name
    cannot pass without the name being added -- which is the moment to decide
    whether the split must keep it importable.
    """
    missing = _unpinned(_live_imported_names())
    assert not missing, (
        "imported from a stage 6 module but absent from its pinned manifest -- "
        "add the name to the matching tuple in this file (adding is safe):\n"
        + "\n".join(f"  {f}: {mod}.{name}" for f, mod, name in missing)
    )


def test_live_import_walk_reaches_every_scoped_module():
    """Guard the guard: a walk that found nothing would pass vacuously."""
    reached = {mod for _, mod, _ in _live_imported_names()}
    # Pinned by name, not by LIVE_WALK_MANIFESTS: dropping a key must fail here.
    scope = {
        "unified_pipeline.stage_6_word_template",
        "unified_pipeline.core.render_check",
        "unified_pipeline.stage6.dedup",
        "unified_pipeline.stage6.render_check",
    }
    assert set(LIVE_WALK_MANIFESTS) == scope
    assert reached == scope


def test_tracked_file_listing_fails_loudly_outside_a_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "_REPO_ROOT", tmp_path)
    # Catch BaseException so a pytest.skip() here FAILS instead of skipping.
    with pytest.raises(BaseException) as raised:
        _tracked_python_files()
    assert raised.type is pytest.fail.Exception


def test_live_walk_flags_unpinned_aliased_relative_and_wildcard_imports():
    """Drive the failure arm: each import shape below must be reported unpinned."""
    code = (
        "from unified_pipeline.stage_6_word_template import run_stage6, brand_new as b\n"
        "from unified_pipeline.stage_6_word_template import *\n"
        "from ...stage_6_word_template import another_new\n"
        "from ..render_check import _norm, fresh_one\n"
    )
    found = _in_scope_imports(
        ast.parse(code), "src/unified_pipeline/stage6/sections/x.py"
    )
    assert [(m.rsplit(".", 1)[-1], n) for _, m, n in _unpinned(found)] == [
        ("render_check", "fresh_one"),
        ("stage_6_word_template", WILDCARD),
        ("stage_6_word_template", "another_new"),
        ("stage_6_word_template", "brand_new"),
    ]
    # A package's own `__init__.py`: `.dedup` is a sibling submodule of the package.
    init_found = _in_scope_imports(
        ast.parse("from .dedup import fresh_x\n"),
        "src/unified_pipeline/stage6/__init__.py",
    )
    assert _unpinned(init_found) == [
        ("src/unified_pipeline/stage6/__init__.py", "unified_pipeline.stage6.dedup", "fresh_x")
    ]


if __name__ == "__main__":
    for _n in STAGE6_IMPORT_SURFACE:
        test_stage6_symbol_is_still_importable(_n)
    for _n in RENDER_CHECK_IMPORT_SURFACE:
        test_render_check_symbol_is_still_importable(_n)
    for _n in STAGE6_CLASS_SURFACE:
        test_generator_method_is_still_on_the_class(_n)
    for _home, _n in _RELOCATED_CASES:
        test_relocated_name_still_importable_from_stage6_module(_home, _n)
        test_relocated_name_resolves_in_its_new_home(_home, _n)
    test_the_relocated_surface_is_not_silently_empty()
    test_live_import_walk_is_pinned_by_the_manifest()
    test_live_import_walk_reaches_every_scoped_module()
    test_the_surface_list_is_not_silently_empty()
    test_private_names_are_a_deliberate_part_of_the_contract()
    print("OK")
