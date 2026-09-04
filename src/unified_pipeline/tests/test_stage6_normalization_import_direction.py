"""CODING_STANDARDS §1.1/§1.2/§1.3 as a check for `stage6/normalization/`.

§1.1 asks for an import test per package. `normalization/` grew a second
module that other modules in it depend on (`publication.py` imports
`citation_matching` and `text`, PR #737), which is the first same-level edge
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
    ("publication", "text"),
})


def _module_files(package: Path) -> list[Path]:
    return sorted(p for p in package.glob("*.py") if p.name != "__init__.py")


def _imported_roots(path: Path) -> set[str]:
    """Every top-level module name this file imports, absolute imports only."""
    tree = ast.parse(path.read_text())
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def _relative_edges(path: Path) -> set[tuple[str, str]]:
    """`from .sibling import x` edges out of this module, as (from, to)."""
    tree = ast.parse(path.read_text())
    edges: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
            edges.add((path.stem, node.module.split(".")[0]))
    return edges


@pytest.mark.parametrize("package", _PURE_PACKAGES)
def test_a_pure_layer_imports_no_io_library(package):
    """§1.2. Data in, data out -- that is what makes these reviewable without
    context and testable without a fixture."""
    offenders = {
        path.name: sorted(_imported_roots(path) & _IO_MODULES)
        for path in _module_files(_STAGE6 / package)
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
    `citation_matching` and `text`, and nothing depends on it. A back-edge
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
    `stage_6_word_template` import this package, so an edge back is a cycle."""
    forbidden = ("formatting", "sections", "stage_6_word_template")
    offenders = {}
    for path in _module_files(_NORMALIZATION):
        source = path.read_text()
        hits = [name for name in forbidden if f"from ..{name}" in source
                or f"import ..{name}" in source]
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
    """
    stage4_names = {"stage4", "stage_4_field_extractor"}
    offenders = {}
    for path in sorted(_STAGE6.rglob("*.py")):
        tree = ast.parse(path.read_text())
        hits = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                hits |= {a.name for a in node.names
                         if stage4_names & set(a.name.split("."))}
            elif isinstance(node, ast.ImportFrom) and node.module:
                if stage4_names & set(node.module.split(".")):
                    hits.add(node.module)
        if hits:
            offenders[str(path.relative_to(_SRC))] = sorted(hits)

    assert offenders == {}
