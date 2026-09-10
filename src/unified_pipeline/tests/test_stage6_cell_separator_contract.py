"""`_clean_inline_tabs`: where it is called from, and what each caller decided.

The function reads "|" and "\\t" as the readers' internal cell separators and
rejoins them as prose. Nothing enforces that a table-shaped record was routed
to a real Word table first -- no type, no assertion, only the six call sites'
conventions. Those conventions are asserted here rather than described in the
function's docstring, so that moving or adding a call site fails a test
instead of leaving a paragraph quietly wrong.

`test_cell_separators.py` covers the EXPECTED path, residual text carrying a
stray separator. This file covers the routing and the fallback.

    python3 -m pytest src/unified_pipeline/tests/test_stage6_cell_separator_contract.py -p no:cacheprovider

Self-contained: no DB, no template document, no PII -- every string below is
synthetic. The section classes are imported to drive the routing tests, which
pull in python-docx as an import but build no document.
"""

import ast
import sys
from pathlib import Path

from docx import Document

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage6.normalization.rendering import (  # noqa: E402
    _clean_inline_tabs,
)
from unified_pipeline.stage6.sections.clinical_practice import (  # noqa: E402
    ClinicalPracticeSection,
)
from unified_pipeline.stage6.sections.mentoring import (  # noqa: E402
    MentoringSection,
    _is_mentee_record,
)
from unified_pipeline.stage6.sections.passthrough import (  # noqa: E402
    PassthroughSection,
)

_PACKAGE = _SRC / "unified_pipeline"

# Every call site, as (module path under `src/unified_pipeline`, enclosing
# function) -> what that caller has already decided before calling. Six, and
# not one contract: a table-backed section reaches this function only on a
# fallback, while teaching, N4, S0 and the appendix have no table in the WCM
# template at all, so a row-shaped record arrives there by construction.
_CALL_SITES = {
    ("stage_6_word_template.py", "_insert_bulleted_entry"):
        "shared bullet helper -- its own callers are pinned by "
        "_BULLET_FALLBACKS below",
    ("stage_6_word_template.py", "_insert_reconsidered_segment"):
        "a re-routed appendix segment, and _recover_unrendered_records, "
        "which passes a dateless multi-cell row here on purpose rather "
        "than leave the record unrendered",
    ("stage_6_word_template.py", "_unconsumed_personal_data_batch"):
        "A entries no Personal Data slot consumed; its PII scan reads the "
        "RAW text, because this call destroys the fragment boundaries",
    ("stage6/sections/appendix.py", "_filter_unmapped_entries"):
        "entries no section renderer claimed -- no table by construction",
    ("stage6/sections/mentoring.py", "_insert_mentoring_summaries"):
        "N3A/N3B are what _is_mentee_record rejected; N4 is unscreened, "
        "the template having no N4 table to screen against",
    ("stage6/sections/researcher_profiles.py", "_fill_researcher_profiles"):
        "S0 identifier lines; the template has no heading or table for them",
}

# Every function that calls `_insert_bulleted_entry`, asserted below. Two are
# fallbacks taken when a table-backed section cannot use its table; teaching is
# not a fallback -- it bullets every entry, the template having no teaching
# table at all.
_BULLET_FALLBACKS = {
    ("stage6/sections/clinical_practice.py", "_insert_multiline_as_bullets"),
    ("stage6/sections/passthrough.py", "_fill_hospital_affiliation"),
    ("stage6/sections/teaching.py", "_insert_teaching_entry"),
}


def _callers_of(name: str) -> set[tuple[str, str]]:
    """Every (module, enclosing function) that calls `name` in the package."""
    found: set[tuple[str, str]] = set()
    for path in sorted(_PACKAGE.rglob("*.py")):
        if "tests" in path.parts or "__pycache__" in path.parts:
            continue
        rel = path.relative_to(_PACKAGE).as_posix()
        stack: list[str] = []

        def walk(node):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    stack.append(child.name)
                    walk(child)
                    stack.pop()
                    continue
                if isinstance(child, ast.Call):
                    func = child.func
                    called = (func.id if isinstance(func, ast.Name)
                              else getattr(func, "attr", None))
                    if called == name and stack:
                        found.add((rel, stack[-1]))
                walk(child)

        walk(ast.parse(path.read_text()))
    return found


# --------------------------------------------------------------------------
# Who calls this function, and what they guarantee
# --------------------------------------------------------------------------

def test_the_call_sites_are_exactly_the_six_this_file_documents():
    """The upstream contract the review asks for (items 8 and 17), as an
    assertion rather than a paragraph: three rounds of review each corrected
    a wrong sentence about these call sites. Adding, moving or removing one
    now fails here, and `_CALL_SITES` is where the answer is written down."""
    assert _callers_of("_clean_inline_tabs") == set(_CALL_SITES)


def test_the_bullet_helper_has_exactly_these_three_calling_functions():
    """`_insert_bulleted_entry` is shared, so its own contract is empty and
    its callers' is what matters. Two are table-backed fallbacks, pinned
    below; the third bullets unconditionally."""
    assert _callers_of("_insert_bulleted_entry") == _BULLET_FALLBACKS


class _Cell:
    def __init__(self, text):
        self.text = text


class _Row:
    def __init__(self, texts):
        self.cells = [_Cell(t) for t in texts]


class _Table:
    """A header row is all these tests read; nothing is written to it."""

    def __init__(self, header):
        self.rows = [_Row(header)]


class _ClinicalProbe(ClinicalPracticeSection):
    """Records the bullets `_fill_clinical_practice_l1` would insert."""

    def __init__(self, table):
        self.verbose = False
        self.stats = {'tables_populated': 0, 'entries_inserted': 0}
        self.bullets: list[str] = []
        self._table = table

    def _find_paragraph_exact(self, text):
        return 0

    def _find_table_after_paragraph(self, idx):
        return self._table

    def _insert_bulleted_entry(self, insert_idx, text, entry=None, **kwargs):
        self.bullets.append(text)


class _PassthroughProbe(PassthroughSection):
    """Records the bullets `_fill_hospital_affiliation` would insert."""

    def __init__(self, table):
        self.verbose = False
        self.stats = {'tables_populated': 0, 'entries_inserted': 0}
        self.bullets: list[str] = []
        self._table = table

    def _find_paragraph_with_text(self, text):
        return 0

    def _find_table_after_paragraph(self, idx):
        return self._table

    def _insert_bulleted_entry(self, insert_idx, text, entry=None, **kwargs):
        self.bullets.append(text)


_CLINICAL_ENTRY = {"text": "Attending physician | Weill Cornell | 2019-2024",
                   "extracted_fields": {}}
_AFFILIATION_ENTRY = {"hierarchy": ["INSTITUTIONAL/HOSPITAL AFFILIATION"],
                      "text": "Attending | NewYork-Presbyterian | 2019-"}


def test_the_clinical_fallback_fires_on_a_missing_table_and_on_a_rejected_one():
    """Not "only when the table was not found": a table that IS found and
    then fails `_clinical_header_match` (a funding table) routes the same
    entries to the same bullet path. Both branches, because the difference
    between them is what the docstring got wrong twice."""
    for table in (None, _Table(["Award Source", "Amount", "Period"])):
        probe = _ClinicalProbe(table)
        probe._fill_clinical_practice_l1([_CLINICAL_ENTRY])
        assert probe.bullets == ["Attending physician | Weill Cornell | 2019-2024"]
        assert probe.stats['tables_populated'] == 0


def test_the_affiliation_fallback_fires_only_on_a_rejected_table():
    """The opposite shape to clinical practice, and the one round 3 stated
    backwards: `passthrough.py`'s bullet arm is nested INSIDE `if table and
    table.rows`, so it is reached only when a table was found and its first
    cell failed the hospital/affiliation/primary check. With no table the
    section renders nothing at all."""
    rejected = _PassthroughProbe(_Table(["Course", "Role"]))
    rejected._fill_hospital_affiliation([_AFFILIATION_ENTRY])
    assert rejected.bullets == ["Attending | NewYork-Presbyterian | 2019-"]

    missing = _PassthroughProbe(None)
    missing._fill_hospital_affiliation([_AFFILIATION_ENTRY])
    assert missing.bullets == []
    assert missing.stats['entries_inserted'] == 0


def test_a_mentee_record_is_routed_to_a_table_not_to_this_function():
    """`mentoring.py`'s N3A/N3B feed only ever carries what
    `_is_mentee_record` rejected -- a record that names a mentee goes to
    `_create_mentee_table_with_spacing` instead."""
    mentee = {"text": "Jane Roe, PhD candidate",
              "extracted_fields": {"name": "Jane Roe",
                                   "mentoring_period": "2020-2022"}}
    aggregate = {"text": "Mentored 14 residents between 2015 and 2024",
                 "extracted_fields": {}}
    assert _is_mentee_record(mentee), "a named mentee must reach the table path"
    assert not _is_mentee_record(aggregate), (
        "an aggregate summary is the residual text this function is for"
    )


class _MentoringProbe(MentoringSection):
    """Stand-in for `WCMTemplateGenerator`: records what each entry became.

    `_mentoring_anchors` / `_paragraph_element` (#739) resolve anchors as
    lxml elements off `self.doc.paragraphs[...]._element`, so this probe
    carries a real minimal `Document` -- paragraph 0 is "MENTORING" -- rather
    than a sentinel; only the collaborators `_fill_mentoring` actually
    reaches on the N4 path are otherwise stubbed.
    """

    def __init__(self):
        self.verbose = False
        self.stats = {'tables_populated': 0, 'entries_inserted': 0}
        self.lines: list[str] = []
        self.tabled: list[dict] = []
        self.doc = Document()
        self.doc.add_paragraph("MENTORING")

    def _find_paragraph_exact(self, text):
        return 0 if text == "MENTORING" else None

    def _find_paragraph_with_text(self, text):
        return None

    def _find_table_after_paragraph(self, idx):
        return None

    def _create_mentee_table_with_spacing(self, record, anchor):
        self.tabled.append(record.source_entry if record else None)
        return None

    def _insert_mentoring_line(self, text, anchor, entry=None):
        self.lines.append(text)
        return None


def test_an_n4_entry_reaches_this_function_however_mentee_shaped_it_is():
    """The third feed into the same call site: `mentoring.py` partitions
    only its N3A/N3B lists with `_is_mentee_record` and passes `n4_entries`
    through untouched. N4 has no table anywhere in the WCM template, so a
    mentee-shaped N4 row is flattened into prose here by construction, not
    by a routing failure."""
    entry = {
        "taxonomy_code": "N4",
        "text": "Jane Roe | Postdoctoral Fellow | 07/2020-06/2022 | Thesis title",
        "extracted_fields": {"name": "Jane Roe",
                             "mentoring_period": "07/2020-06/2022"},
    }
    assert _is_mentee_record(entry), (
        "the entry must be one the N3A/N3B screening would have tabled"
    )

    probe = _MentoringProbe()
    probe._fill_mentoring({"N4": [entry]})

    assert probe.tabled == [], "N4 never reaches the mentee-table path"
    assert probe.lines == [
        "Jane Roe — Postdoctoral Fellow — 07/2020-06/2022 — Thesis title"
    ]


# --------------------------------------------------------------------------
# The expected path: residual text carrying a stray separator
# --------------------------------------------------------------------------

def test_residual_text_with_no_separator_is_returned_unchanged():
    assert _clean_inline_tabs("Mentored 14 residents") == "Mentored 14 residents"


def test_a_label_value_pair_becomes_a_colon():
    assert _clean_inline_tabs("ORCID\t0000-0002-1825-0097") == (
        "ORCID: 0000-0002-1825-0097"
    )


def test_a_blank_template_row_collapses_so_callers_can_drop_it():
    assert _clean_inline_tabs("|  |  |") == ""


# --------------------------------------------------------------------------
# The fallback path: a genuine row arrives anyway
# --------------------------------------------------------------------------

def test_a_genuine_table_row_is_flattened_into_prose():
    """The failure mode the review names. Column structure is not recovered
    and no warning is emitted -- the row becomes one em-dash-joined line and
    renders as a single bullet."""
    row = "Jane Roe | Postdoctoral Fellow | 07/2020-06/2022 | Thesis title"
    assert _clean_inline_tabs(row) == (
        "Jane Roe — Postdoctoral Fellow — 07/2020-06/2022 — Thesis title"
    )


def test_a_header_row_is_flattened_the_same_way_as_a_data_row():
    """Nothing here distinguishes a column-header row from a data row, so a
    table that reaches this path renders its headings as another bullet."""
    assert _clean_inline_tabs("Name | Site | Period | Project") == (
        "Name — Site — Period — Project"
    )


def test_a_row_carrying_both_separators_splits_on_pipes_first():
    """Order matters and is not obvious from the code: the pipe pass runs
    first, so a tab surviving inside a cell becomes the label/value colon of
    the WHOLE line, not of that cell."""
    assert _clean_inline_tabs("A | B\tC | D") == "A — B: C — D"


def test_an_empty_cell_is_dropped_rather_than_kept_as_a_gap():
    """Column alignment is lost with it: a reader cannot tell from the
    output which column was blank."""
    assert _clean_inline_tabs("Jane Roe |  | 2022") == "Jane Roe — 2022"


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_"):
            _fn()
            print(f"ok  {_name}")
    print("all checks passed")
