"""CODING_STANDARDS §1.1/§1.2/§1.3 as a check for `stage6/normalization/`.

§1.1 asks for an import test per package. `normalization/` grew a second
module that other modules in it depend on (`publication.py` imports
`citation_matching` and `authors`, PR #737), which is the first same-level edge
in any of the pure stage-6 layers -- `formatting/` and `sorting/` reach
*across* to `parsing/`, but no module in a pure package imported a sibling
before this one. That is worth a gate rather than a sentence in a docstring:

- §1.2, pure layers import no I/O library. Normalization takes data and
  returns data; a `docx`, `boto3`, `requests` or DB import here would mean
  the layer stopped being testable without a fixture.
- §1.1/§1.3, dependencies run one way and the direction is written down. The
  reason `sections/*` may not import each other is that a section can corrupt
  another section's write target (#454). These are pure functions with no
  shared state, so the hazard is not corruption but a cycle -- which fails at
  *load*, taking every stage-6 render with it. Pinning the edge set is what
  keeps the direction a decision instead of an accident.
- The stage-4 boundary this PR deliberately did not cross: `PublicationFields`
  is hand-kept and checked against `stage4/schemas.py` from
  `test_stage6_publication_resolution.py`, precisely so that no module under
  `stage6/` imports `stage4` at runtime. That absence is load-bearing and is
  asserted here rather than assumed.

Every check reads `_absolute_targets` rather than a syntax. The round-4
version of this file matched on shapes -- `node.level == 1 and node.module`
for an edge, the substring `"from ..formatting"` for an upward import -- and
each shape had a legal spelling of its own forbidden edge that it could not
see. Three injections passed all eight checks: `from . import publication` in
`citation_matching.py` (the exact back-edge
`test_nothing_in_normalization_imports_publication` is named for, and it
really does break the package -- `pytest src/unified_pipeline/tests` collects
69 errors under that injection while this file, run alone, said 8 passed),
`import unified_pipeline.stage6.normalization.publication` in the
same file, and `from unified_pipeline.stage6.formatting import dates` in
`text.py`. A gate that passes its own named counterexample is worse than no
gate, because it is also a claim that the edge is checked.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_normalization_import_direction.py -p no:cacheprovider

Self-contained: reads source with `ast`, imports nothing under test.
"""

import ast
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

_STAGE6 = _SRC / "unified_pipeline" / "stage6"
_NORMALIZATION = _STAGE6 / "normalization"

#: Pure layers, per CODING_STANDARDS §2's tree. None of these may import an
#: I/O library; the check below is the §1.2 one, widened from `normalization/`
#: to its four siblings because the same rule covers all five.
_PURE_PACKAGES = ("normalization", "parsing", "resolution", "sorting")

_IO_MODULES = frozenset({
    "docx", "boto3", "requests", "sqlalchemy", "psycopg2", "httpx", "urllib3",
})

#: The intra-package edges `normalization/__init__.py`'s docstring declares.
#: A new one is not forbidden -- it is a decision, and this is where it gets
#: made rather than noticed.
_DECLARED_INTERNAL_EDGES = frozenset({
    ("publication", "citation_matching"),
    ("publication", "authors"),
})

#: Dotted names that an import from inside `normalization/` may not reach.
#: Fully qualified rather than written as the `from ..name` text they usually
#: take, because the resolver below turns every import -- relative at any
#: level, or absolute -- into this form first. `stage_6_word_template` is a
#: module beside `stage6/`, not inside it, which is why its prefix is shorter
#: and why the substring form this replaced ("from ..stage_6_word_template")
#: could never have matched the import it was written against.
_FORBIDDEN_UPWARD = (
    "unified_pipeline.stage6.formatting",
    "unified_pipeline.stage6.sections",
    "unified_pipeline.stage_6_word_template",
)


def _module_files(package: Path) -> list[Path]:
    """The package's modules, without `__init__.py`.

    Used by the two intra-package checks only: `__init__.py` imports every
    sibling by design -- that is what makes it the export surface -- so
    including it would make "nothing imports `publication`" unsatisfiable.
    """
    return sorted(p for p in package.glob("*.py") if p.name != "__init__.py")


def _package_files(package: Path) -> list[Path]:
    """Every source file in the package, `__init__.py` included.

    The checks that do not care about direction inside the package -- no I/O
    library, no edge upward -- apply to `__init__.py` as much as to any other
    file: an `import docx` there is exactly as much of a violation, and it was
    outside the gate while `_module_files` was the only accessor.
    """
    return sorted(package.glob("*.py"))


def _package_dotted(path: Path) -> str:
    """The dotted package a source file lives in, e.g.
    `unified_pipeline.stage6.normalization`."""
    return ".".join(path.resolve().relative_to(_SRC).parts[:-1])


def _absolute_targets(path: Path) -> set[str]:
    """Every dotted name this file imports, resolved to absolute form.

    One resolver for all four checks below, because every one of them was
    previously written against a *syntax* -- `node.level == 1 and node.module`,
    or the substring `from ..formatting` -- and each therefore had a spelling
    of the very edge it forbade that it could not see. Three were found by
    injection (round-5 verification of PR #737):

        from . import publication                     ImportFrom(module=None,
                                                                 level=1)
        import unified_pipeline.stage6.normalization.publication
        from unified_pipeline.stage6.formatting import dates

    The first is exactly what `test_nothing_in_normalization_imports_publication`
    is named for: injected into `citation_matching.py` it makes the package
    fail at import (69 collection errors over the test tree), and the gate
    reported 8 passed.

    `from X import a` yields both `X` and `X.a`, because `ast` cannot tell a
    submodule from an attribute and the checks need whichever it is. A
    relative level resolves against this file's own package: level 1 is that
    package, level 2 its parent, and so on.
    """
    parts = _package_dotted(path).split(".")
    targets: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            targets.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = node.module or ""
            else:
                anchor = parts[: len(parts) - node.level + 1]
                base = ".".join(anchor + ([node.module] if node.module else []))
            if base:
                targets.add(base)
            targets.update(
                f"{base}.{alias.name}" if base else alias.name
                for alias in node.names
                if alias.name != "*"
            )
    return targets


def _reaches(target: str, prefix: str) -> bool:
    """Does this import target name `prefix` or something inside it?"""
    return target == prefix or target.startswith(prefix + ".")


def _imported_roots(path: Path) -> set[str]:
    """Every top-level module name this file imports."""
    return {target.split(".")[0] for target in _absolute_targets(path)}


def _relative_edges(path: Path) -> set[tuple[str, str]]:
    """Edges from this module to a sibling in its own package, as (from, to).

    Sibling-ness is decided on the resolved target, so all four spellings of
    the same edge land here: `from .sibling import x`, `from . import
    sibling`, `import <full.dotted.path>.sibling` and
    `from <full.dotted.path> import sibling`.
    """
    package = _package_dotted(path)
    edges: set[tuple[str, str]] = set()
    for target in _absolute_targets(path):
        if target.startswith(package + "."):
            sibling = target[len(package) + 1:].split(".")[0]
            if sibling != path.stem:
                edges.add((path.stem, sibling))
    return edges


@pytest.mark.parametrize("package", _PURE_PACKAGES)
def test_a_pure_layer_imports_no_io_library(package):
    """§1.2. Data in, data out -- that is what makes these reviewable without
    context and testable without a fixture."""
    offenders = {
        path.name: sorted(_imported_roots(path) & _IO_MODULES)
        for path in _package_files(_STAGE6 / package)
        if _imported_roots(path) & _IO_MODULES
    }

    assert offenders == {}


def test_normalization_has_exactly_the_internal_edges_it_declares():
    """§1.1/§1.3. The package docstring names the edges; this is that claim as
    a check, so adding one is a visible decision and not a side effect."""
    edges = set()
    for path in _module_files(_NORMALIZATION):
        edges |= _relative_edges(path)

    assert edges == _DECLARED_INTERNAL_EDGES


def test_nothing_in_normalization_imports_publication():
    """`publication` is the top of the package's internal order: it depends on
    `citation_matching` and `authors`, and nothing depends on it. A back-edge
    would be an import cycle, which fails at load and takes every stage-6
    render with it -- not at render, where a test might still catch it."""
    importers = [
        path.name
        for path in _module_files(_NORMALIZATION)
        if ("publication" in {to for _, to in _relative_edges(path)})
    ]

    assert importers == []


def test_normalization_does_not_import_upward_out_of_its_layer():
    """The cross-package half of the same rule: `formatting/`, `sections/` and
    `stage_6_word_template` import this package, so an edge back is a cycle.

    Read off resolved import targets rather than off the file text. The
    substring form this replaced (`"from ..formatting" in source`) matched one
    spelling of the edge and missed the absolute one -- `from
    unified_pipeline.stage6.formatting import dates` in `text.py` passed all
    eight checks -- while also being able to fire on a docstring that merely
    named the package.
    """
    offenders = {}
    for path in _package_files(_NORMALIZATION):
        targets = _absolute_targets(path)
        hits = [prefix for prefix in _FORBIDDEN_UPWARD
                if any(_reaches(target, prefix) for target in targets)]
        if hits:
            offenders[path.name] = hits

    assert offenders == {}


def test_no_module_under_stage6_imports_stage4():
    """The boundary `PublicationFields` is hand-kept to avoid crossing.

    `stage6/` has never imported a `stage4` module, and the drift guard in
    test_stage6_publication_resolution.py exists so it still does not have to.
    If this ever becomes deliberate, this test is where the decision is made.

    Checked over the import statements with `ast`, not over the file text: the
    resolver's own docstring names `stage4/schemas.py` as the thing it is kept
    in step with, and a substring check would read that as the coupling it
    exists to forbid.

    Over resolved targets, so `from .. import stage4` -- which carries no
    `node.module` at all and which the previous form did not look at -- counts
    the same as the absolute spelling.
    """
    stage4_names = {"stage4", "stage_4_field_extractor"}
    offenders = {}
    for path in sorted(_STAGE6.rglob("*.py")):
        hits = sorted(target for target in _absolute_targets(path)
                      if stage4_names & set(target.split(".")))
        if hits:
            offenders[str(path.relative_to(_SRC))] = hits

    assert offenders == {}
