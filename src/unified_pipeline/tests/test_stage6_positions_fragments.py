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

Groups (d) and (e) cover the rest of the section's repair work rather than the
fragment scanner: that one record yields exactly one rendered row and one
increment of `entries_inserted`, and that the two passes which rewrite stage-4
output -- institution propagation and appointment merging -- refuse to act
without evidence rather than falling back on document adjacency.

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
    _institution_from_raw_text,
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


def _positions_generator():
    """A generator whose document holds just the D2 anchor and an empty
    three-column table, ready for `_fill_positions`."""
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document()
    gen.doc.add_paragraph("D. POSITIONS")
    gen.doc.add_paragraph("Hospital Appointments")
    table = gen.doc.add_table(rows=1, cols=3)
    for i, header in enumerate(["Title", "Institution/Location", "Dates"]):
        table.rows[0].cells[i].text = header
    return gen, table


def _render_positions(entries):
    gen, table = _positions_generator()
    gen._fill_positions({"D2": entries})
    return [[c.text for c in r.cells] for r in table.rows[1:]]


def _position_entry(idx, title, institution, hierarchy, start="", end=""):
    """A minimal D2 record: document position, title, employer and the source
    heading path propagation reads."""
    return {
        "element_idx_start": idx,
        "taxonomy_code": "D2",
        "hierarchy": hierarchy,
        "extracted_fields": {
            "title": title,
            "institution": institution,
            "start_date": start,
            "end_date": end,
        },
    }


def _institution_of(entry):
    return (entry.get("extracted_fields") or {}).get("institution") or ""


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


# --- (d) the rendering layer owns the row count ------------------------------

def test_entries_inserted_counts_one_per_rendered_row():
    """Pins the row-count invariant: `entries_inserted` is incremented exactly
    once per physical row, child rows included.

    A parent with two recovered children must leave the counter at 3. One
    (children not counted) or five (children counted at two levels) both mean
    the statistic no longer describes the document.
    """
    gen, table = _positions_generator()
    gen._fill_positions({"D2": [_borman_entry()]})
    assert len(table.rows) - 1 == 3
    assert gen.stats["entries_inserted"] == 3


# --- (e) the repair passes fail closed ---------------------------------------

def test_institution_propagation_stops_at_unrelated_entries():
    """Pins that a carried-forward employer stops at a source-structure
    boundary instead of running to the end of the code list.

    Document order alone used to keep an employer in scope for every later
    record, so a record under a different source heading inherited it. The
    record under the other heading must stay empty, and so must the record
    after it -- the carry is dropped at the boundary, not resumed across it.
    """
    parent = _position_entry(1, "Chief of Service", "Lincoln Hospital",
                             ["Hospital Appointments"], "2001", "2004")
    sibling = _position_entry(2, "Attending Physician", "", ["Hospital Appointments"])
    unrelated = _position_entry(3, "Course Director", "", ["Teaching"])
    after = _position_entry(4, "Preceptor", "", ["Hospital Appointments"])

    WCMTemplateGenerator._propagate_institution_to_subentries(
        [parent, sibling, unrelated, after])

    assert _institution_of(sibling) == "Lincoln Hospital"
    assert _institution_of(unrelated) == ""
    assert _institution_of(after) == ""


def test_raw_text_employer_candidate_must_name_an_employer():
    """Pins the last-resort employer scan against City/State-shaped text.

    It used to take any fragment containing a "City, ST" pattern wherever it
    sat, so a bare location, a "Surname, Forename", or a whole date-prefixed
    record line became the Institution cell of a record that had no employer.
    A candidate must name something in front of a location tail, and must
    carry no year.
    """
    employer = "Attending Physician\tLincoln Hospital, Bronx, NY\t07/2002"
    assert _institution_from_raw_text(employer) == "Lincoln Hospital, Bronx, NY"

    for text in (
        "Attending Physician\tBronx, NY",                       # bare location
        "Reviewer\tMarblegate, Quenby",                         # a person
        "Aug 2019-Dec 2023, Associate Director, Institute of "
        "Speculative Metrics, Norvale University, Crab Hollow, ZQ\n"
        "Jan 2024-Present, Vice Chair",                         # record lines
    ):
        assert _institution_from_raw_text(text) == "", f"accepted {text!r}"


def test_merge_needs_employer_evidence_not_adjacency():
    """Pins that the merge refuses a pair whose employers disagree.

    A title-only row next to a bare dates row used to take that row's dates
    and delete it whatever the two said about their employers, which invents
    an appointment the CV never claimed. Two employers with no word in common
    must leave both rows standing and the titled row date-less.
    """
    dated = _position_entry(1, "", "Quexley College", ["Hospital Appointments"],
                            "2019-09", "2021-06")
    titled = _position_entry(2, "Program Director", "Norvale University Medical College",
                             ["Hospital Appointments"])

    merged = WCMTemplateGenerator._merge_grouped_appointments([dated, titled])

    assert len(merged) == 2
    # the titled row keeps its own employer, and gains no dates from the other
    assert _institution_of(titled) == "Norvale University Medical College"
    assert not (titled["extracted_fields"].get("start_date")
                or titled["extracted_fields"].get("end_date"))


def test_merge_refuses_a_pair_that_names_no_employer_at_all():
    """Pins the other half of the same rule: a missing employer is not a match.

    Two records that neither name an employer nor inherited one from a parent
    row used to merge on adjacency alone, because the header test read two
    unknown employers as equal.
    """
    dated = _position_entry(1, "", "", ["Hospital Appointments"], "2002-07", "2006-12")
    titled = _position_entry(2, "Attending Physician", "", ["Hospital Appointments"])

    merged = WCMTemplateGenerator._merge_grouped_appointments([dated, titled])

    assert len(merged) == 2
    assert not (titled["extracted_fields"].get("start_date")
                or titled["extracted_fields"].get("end_date"))


def test_merge_still_joins_a_sub_unit_of_the_same_employer():
    """The other half of the same rule: a record naming a sub-unit of the
    header's employer is still one appointment with it, so requiring evidence
    does not cost the merge the case it was written for.
    """
    dated = _position_entry(1, "", "Lincoln Hospital", ["Hospital Appointments"],
                            "2002-07", "2006-12")
    titled = _position_entry(
        2, "Attending Physician",
        "Lincoln Hospital, Department of Emergency Medicine",
        ["Hospital Appointments"])

    merged = WCMTemplateGenerator._merge_grouped_appointments([dated, titled])

    assert len(merged) == 1
    assert merged[0]["extracted_fields"]["title"] == "Attending Physician"
    assert merged[0]["extracted_fields"]["start_date"] == "2002-07"
    assert merged[0]["extracted_fields"]["end_date"] == "2006-12"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
