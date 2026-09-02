"""Section D (positions) tab/pipe child rows (#476, PR1 of the accuracy wave).

`positions.py` is not an `entry_lines` call site -- it never called the
ten-times-duplicated newline splitter the issue is about. What it has instead
is a narrower loss of the same shape: a D1/D2/D3 entry whose text carries a
tab-joined, bullet-prefixed child appointment after its header row renders
only the header. The confirmed instance on the corpus farm is the -DAZFA D2
entry below (`stage_5_enrichment/-DAZFA_..._enriched.json`): stage 4 extracted
fields for the header only, and the two child promotions --
"Attending Physician | 07/2002 - 06/2003" and "Attending Physician & Assistant
Director | 06/2003 - 12/2006" -- rendered nowhere. `_tab_joined_child_
fragments` (positions.py) recovers them from `entry_fragments`, gated on a
literal tab in the raw text.

MNZ7IA and ZZLKMA render the same Borman source pre-split by stage 4 into
three separate D2 entries (no tab in any one entry's text) and are the model
for what -DAZFA's rendering should now look like; `test_negative_control_
already_split_entry_is_unchanged` pins that this fix does not touch them.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_positions_fragments.py -p no:cacheprovider
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from docx import Document  # noqa: E402

from unified_pipeline.core.render_check import entry_fragments, entry_lines  # noqa: E402
from unified_pipeline.stage6.sections.positions import (  # noqa: E402
    _tab_joined_child_fragments,
)
from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402


# The exact -DAZFA D2 entry text (stage_5_enrichment JSON), en-dash and bullet
# glyph verbatim.
BORMAN_D2_TEXT = (
    "Attending Physician, Department of Emergency Medicine | Lincoln Hospital, Bronx, NY | 07/2002 – 12/2006"
    "\t• Attending Physician | 07/2002 – 06/2003"
    "\t• Attending Physician & Assistant Director | 06/2003 – 12/2006"
)

# Farm-derived shapes with no tab-joined child (newline-only or pipe-only, or
# a stage-4-already-split header/child that carries a bullet but no tab).
NO_CHILD_CASES = [
    "Program\tOchsner Clinic Foundation\t2009 - 2018",
    "Role | Organization | 2013 - present",
    "Attending Physician | Lincoln Hospital, Bronx, NY | 07/2023 – Present",
    # MNZ7IA/ZZLKMA's own already-split child entry: a bullet immediately
    # followed by a pipe-date, but NO tab -- the gate this function's own
    # docstring names as the reason it must not fire here.
    "• Attending Physician | 07/2002 – 06/2003",
]


def _render_positions(entries):
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document()
    gen.doc.add_paragraph("D. POSITIONS")
    gen.doc.add_paragraph("Hospital Appointments")
    table = gen.doc.add_table(rows=1, cols=3)
    for i, header in enumerate(["Title", "Institution/Location", "Dates"]):
        table.rows[0].cells[i].text = header
    gen._fill_positions({"D2": entries})
    return [[c.text for c in r.cells] for r in table.rows[1:]]


def _borman_entry():
    return {
        "text": BORMAN_D2_TEXT,
        "taxonomy_code": "D2",
        "extracted_fields": {
            "title": "Attending Physician, Department of Emergency Medicine",
            "institution": "Lincoln Hospital, Bronx, NY",
            "start_date": "2002-07",
            "end_date": "2006-12",
        },
    }


# --- (a) no content the old split saw is lost or fabricated -----------------

def test_tab_joined_child_fragments_recovers_exactly_the_two_children():
    children = _tab_joined_child_fragments(BORMAN_D2_TEXT)
    assert children == [
        ("Attending Physician", ("07/2002", "06/2003")),
        ("Attending Physician & Assistant Director", ("06/2003", "12/2006")),
    ]
    # Every entry_lines "line" -- here there is exactly one, the whole blob,
    # since the text carries no literal newline -- is still fully accounted
    # for: nothing in the recovered children is absent from entry_fragments.
    assert entry_lines(BORMAN_D2_TEXT) == [BORMAN_D2_TEXT]
    frags = entry_fragments(BORMAN_D2_TEXT)
    for title, (start, end) in children:
        assert any(title in f for f in frags)
        assert any(start in f for f in frags)
        assert any(end in f for f in frags)


def test_no_tab_no_children_even_with_a_leading_bullet():
    """The gate is a literal tab, not just bullet-then-date-range -- this is
    what keeps MNZ7IA/ZZLKMA's already-split entries (a bullet-prefixed
    title immediately followed by its own pipe-date, no tab) untouched."""
    for case in NO_CHILD_CASES:
        assert _tab_joined_child_fragments(case) == [], f"unexpected children for {case!r}"


# --- (b) positive control: fails on dev today --------------------------------

def test_positive_control_borman_entry_gains_both_child_rows():
    rows = _render_positions([_borman_entry()])
    assert len(rows) == 3, f"expected parent + 2 children, got {rows}"

    parent, child1, child2 = rows
    assert parent[0] == "Attending Physician, Department of Emergency Medicine"
    assert parent[2] == "07/02-12/06"

    assert child1[0] == "Attending Physician"
    assert child1[2] == "07/02-06/03"
    assert child2[0] == "Attending Physician & Assistant Director"
    assert child2[2] == "06/03-12/06"

    # institution/location inherited verbatim from the parent row
    assert child1[1] == parent[1] == "Lincoln Hospital, Bronx, NY"
    assert child2[1] == parent[1]

    # each row appears exactly once
    titles = [r[0] for r in rows]
    assert len(titles) == len(set(titles)) == 3


# --- (c) negative control: a single-part entry is unchanged ------------------

def test_negative_control_single_part_entry_unchanged():
    entry = {
        "text": "Attending Physician | Weill Cornell Medicine, New York, NY | 07/2020 – Present",
        "taxonomy_code": "D2",
        "extracted_fields": {
            "title": "Attending Physician",
            "institution": "Weill Cornell Medicine, New York, NY",
            "start_date": "2020-07",
            "end_date": "Present",
        },
    }
    rows = _render_positions([entry])
    assert len(rows) == 1
    assert rows[0][0] == "Attending Physician"
    assert rows[0][2] == "07/20-Present"


def test_negative_control_already_split_entry_is_unchanged():
    """MNZ7IA/ZZLKMA's shape: the child was already promoted to its own D2
    entry by stage 4 (institution None, own start/end dates, bullet still in
    the raw text but no tab). Must render as exactly one row, not two."""
    entry = {
        "text": "• Attending Physician | 07/2002 – 06/2003",
        "taxonomy_code": "D2",
        "extracted_fields": {
            "title": "Attending Physician",
            "institution": None,
            "start_date": "2002-07",
            "end_date": "2003-06",
        },
    }
    rows = _render_positions([entry])
    assert len(rows) == 1
    assert rows[0][0] == "Attending Physician"
    assert rows[0][2] == "07/02-06/03"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
