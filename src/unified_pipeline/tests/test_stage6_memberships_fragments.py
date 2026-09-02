"""Section I (memberships) newline-blind fix (#476, PR1 of the accuracy wave).

`memberships.py:115` was one of the ten `entry_lines`-based call sites,
gating `_parse_multi_membership_entry` on `len(lines) > 2`. A fully blind
entry (no literal newline at all) always reads as exactly one line under
`entry_lines`, so a "Type1 | Org1 | Date1 | Type2 | Org2 | Date2" entry fused
without a single newline never reached the multi-membership parser and fell
to the single-membership path, which uses `extracted_fields` -- describing
only the first membership.

`_entry_parts` fixes exactly the blind case (entry_lines already returns
more than one part unchanged) by adding '|' as a boundary when the whole
entry is one line. Migrating this call site to `entry_fragments` outright
(tab included) was tried and reverted after reading the actual render-gate
diff: 038WKA and 9 near-duplicate "Jonathan Nahmias" uids carry a single
membership with three tab-separated fields ("Member of the American
Psychiatric Association\\tFebruary 2018 - Present\\tI have attended and
presented..."); `_parse_multi_membership_entry`'s classifier needs a
membership-type part to be <=3 words and a date part to match a strict
numeric pattern, both of which that entry's fields fail, so splitting its
tabs turned one membership into three garbled "organizations" with no
dates. Tab stays unsplit here, matching what `entry_lines` already did.

The call site also gates on the PARSED membership count (`len(memberships)
> 1`), not just the raw part count: a single membership whose fields split
into exactly three parts ("Fellow | American Academy of Pediatrics |
1/1997-present", the farm's actual blind-'|' shape on 1FRABQ and 11 other
uids) crosses the >2 part-count threshold but must still resolve to one
membership.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_memberships_fragments.py -p no:cacheprovider
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from docx import Document  # noqa: E402

from unified_pipeline.core.render_check import entry_lines  # noqa: E402
from unified_pipeline.stage6.sections.memberships import _entry_parts  # noqa: E402
from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402


# Genuinely multi-line, farm-derived shape (2071_Zuschlag_Cv's actual I
# entry): entry_lines already returns >1 part, each carrying its own unsplit
# tabs -- _entry_parts must not touch it further.
MULTILINE_CASES = [
    "2009-2012\t\tStudent Osteopathic Surgical Association\t\t\n\n2009-2012\t\tFlorida Osteopathic Medical Association",
    "Member\nElected Member | Org1\nOrg2 | date1\ndate2",
]

# Single-line (blind) shapes with a tab that must ALSO pass through
# untouched (038WKA's real farm shape).
BLIND_TAB_CASE = (
    "Member of the American Psychiatric Association\tFebruary 2018 – Present"
    "\tI have attended and presented at the American Psychiatric Association "
    "Annual Meeting (see section on presentations) and remain up to date "
    "with their newsletter."
)


def _render_memberships(entries):
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document()
    gen.doc.add_paragraph("I. PROFESSIONAL ORGANIZATIONS AND SOCIETY MEMBERSHIPS")
    table = gen.doc.add_table(rows=1, cols=2)
    for i, header in enumerate(["Organization", "Date (yyyy-yyyy)"]):
        table.rows[0].cells[i].text = header
    gen._fill_memberships(entries)
    return [[c.text for c in r.cells] for r in table.rows[1:]]


# --- (a) no line the old newline split produced is lost ---------------------

def test_multiline_entries_are_returned_unchanged():
    for case in MULTILINE_CASES:
        old = entry_lines(case)
        assert len(old) > 1, f"fixture {case!r} is not actually multi-line"
        assert _entry_parts(case) == old, f"diverged on already-multiline {case!r}"


def test_blind_line_with_a_tab_is_also_returned_whole():
    old = entry_lines(BLIND_TAB_CASE)
    assert old == [BLIND_TAB_CASE]
    assert _entry_parts(BLIND_TAB_CASE) == old


# --- (b) positive control: fails on dev today --------------------------------

def test_positive_control_pipe_blind_multi_membership_entry_gains_both_rows():
    """Today's `entry_lines` returns one opaque part for this text (no
    '\\n'), so `len(lines) > 2` is never true and `_parse_multi_membership_
    entry` never runs; the entry falls to the single-membership branch,
    which uses `extracted_fields` describing only the first membership and
    silently drops the second. Fails on dev."""
    entry = {
        "text": "Member | State Medical Society | 2015-present | Fellow | National Surgical Association | 2018-present",
        "extracted_fields": {
            "organization": "State Medical Society",
            "start_date": "2015",
            "end_date": "present",
        },
    }
    rows = _render_memberships([entry])
    assert len(rows) == 2, f"expected 2 memberships, got {rows}"
    assert rows[0][0] == "Member, State Medical Society"
    assert rows[0][1] == "2015-present"
    assert rows[1][0] == "Fellow, National Surgical Association"
    assert rows[1][1] == "2018-present"


# --- (c) negative controls: unaffected shapes render exactly as today -------

def test_negative_control_single_membership_unchanged():
    entry = {
        "text": "Fellow, American College of Surgeons",
        "extracted_fields": {
            "organization": "American College of Surgeons",
            "membership_type": "Fellow",
            "start_date": "2015",
            "end_date": "Present",
        },
    }
    rows = _render_memberships([entry])
    assert len(rows) == 1
    assert rows[0][0] == "Fellow, American College of Surgeons"


def test_negative_control_farm_pipe_blind_three_field_single_membership_unchanged():
    """1FRABQ's actual shape: one membership whose fields are pipe-joined
    into exactly three parts. `_entry_parts` DOES split it, but the
    len(memberships) > 1 gate keeps it on the single-membership path
    (today's rendering: the organization field alone, membership_type field
    absent, formatted date range) -- unchanged from dev."""
    entry = {
        "text": "Fellow | American Academy of Pediatrics | 1/1997-present",
        "extracted_fields": {
            "organization": "American Academy of Pediatrics",
            "start_date": "1997-01-01",
            "end_date": "present",
        },
    }
    rows = _render_memberships([entry])
    assert len(rows) == 1
    assert rows[0][0] == "American Academy of Pediatrics"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
