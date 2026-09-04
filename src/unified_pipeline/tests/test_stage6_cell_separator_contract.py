"""What `_clean_inline_tabs` does on each side of its upstream invariant.

The function reads "|" and "\\t" as the readers' internal cell separators and
rejoins them as prose. That is right only because a table-shaped record is
supposed to have been routed to a real Word table before it gets here -- and
nothing enforces that: no type, no assertion, only the six call sites'
conventions, which the function's own docstring now lists.

`test_cell_separators.py` already covers the EXPECTED path, residual text
that carries a stray separator. This file covers the other side of the same
invariant:

- the routing that is supposed to keep genuine rows away from here, shown
  where it is actually made (`_is_mentee_record` decides table vs line);
- what happens when it fails anyway -- a real row IS flattened into one
  "cell — cell — cell" paragraph, silently. Pinned so the cost is written
  down rather than discovered in a delivered document, and so that a future
  change of that behaviour has to come here and say so.

    python3 -m pytest src/unified_pipeline/tests/test_stage6_cell_separator_contract.py -p no:cacheprovider

Self-contained: no DB, no template, no python-docx, no PII -- every string
below is synthetic.
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage6.normalization.text import (  # noqa: E402
    _clean_inline_tabs,
)
from unified_pipeline.stage6.sections.mentoring import (  # noqa: E402
    _is_mentee_record,
)


# --------------------------------------------------------------------------
# The routing that keeps genuine rows away from this function
# --------------------------------------------------------------------------

def test_a_mentee_record_is_routed_to_a_table_not_to_this_function():
    """`sections/mentoring.py:203` is one of the six call sites, and it only
    ever sees what `_is_mentee_record` rejected -- a record that names a
    mentee goes to `_create_mentee_table_with_spacing` instead. This is the
    invariant the function's docstring claims, asserted at the point the
    decision is actually made."""
    mentee = {"text": "Jane Roe, PhD candidate",
              "extracted_fields": {"name": "Jane Roe",
                                   "mentoring_period": "2020-2022"}}
    aggregate = {"text": "Mentored 14 residents between 2015 and 2024",
                 "extracted_fields": {}}
    assert _is_mentee_record(mentee), "a named mentee must reach the table path"
    assert not _is_mentee_record(aggregate), (
        "an aggregate summary is the residual text this function is for"
    )


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
