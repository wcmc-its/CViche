"""Section H (honors) newline-blind fix (#476, PR1 of the accuracy wave).

`honors.py:122` was one of the ten `entry_lines`-based call sites. Its own
per-part loop already disambiguates a tab ("does the last tab part look like
a year?" -- Award\\tYear splits, Award\\tDate\\tDescription rejoins into one
award) and a pipe ("Award | Year") *within a single already-newline-split
line*; the bug entry_lines caused was narrower than the other four sections':
a fully blind entry (no newline AT ALL) never reached that per-part logic as
more than one opaque blob, so a genuinely fused multi-award entry joined only
by '|' silently kept just its first award and dropped the rest (the OLD
`elif '|' in line:` branch reads only `parts[0]`/`parts[1]`, ignoring
anything past the first pipe).

`_entry_parts` fixes exactly that: when `entry_lines` already returns more
than one part (a genuine newline-separated entry), it is returned unchanged
-- migrating those to `entry_fragments` wholesale was tried and reverted
after reading the actual corpus diff it produced (see the function's own
docstring): 2054_Opresko_Cv's "1994 | American Chemical Society Award,\\n..."
already has 2 lines and collided with the pre-existing (separate) "DATE |
AWARD" mislabeling bug in the loop below, adding a spurious duplicate row;
2068_Yount_Cv's single blind entry mixes a tab AND a '|', and splitting the
'|' first defeats the tab-rejoin disambiguation, again producing a spurious
row ("Research.com" on its own). Both are why the fix is scoped to a
single-line, tab-free entry only -- disclosed here, not silently dropped.
There is no farm H entry of that exact shape (the farm's 9 tab-free blind '|'
entries are all "YEAR | Award, Org", already rendered correctly today via the
extracted fields' own single-award path -- this fix changes nothing for
them, hence CHANGED 0 in the render gate); the positive control below is a
synthetic fixture pinning the behaviour for when the farm does grow one.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_honors_fragments.py -p no:cacheprovider
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from docx import Document  # noqa: E402

from unified_pipeline.core.render_check import entry_lines  # noqa: E402
from unified_pipeline.stage6.sections.honors import _entry_parts  # noqa: E402
from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402


# Genuinely multi-line, farm-derived shapes: entry_lines already returns >1
# part for each of these, so _entry_parts must return them byte-identical.
MULTILINE_CASES = [
    # 2054_Opresko_Cv H entry shape: a pipe INSIDE the first of two newline
    # lines. Splitting further on '|' here is what produced the spurious row
    # -- _entry_parts must not touch it.
    "1994 | American Chemical Society Award,\nLehigh Valley Chapter of ACS",
    "1998-1999 | Mentored Investigator Award,\nThe Four Diamonds Fund of the Milton S. Hershey",
    "Award One\nAward Two",
    "2020 AICT Outstanding Article Award\n2018 Prior Award",
]

# Single-line (blind) shapes that must ALSO pass through untouched: a tab
# anywhere in the line (038WKA's 3-tab-field award, 2082_Dr_Scot's
# mid-sentence wrap artifact, 2068_Yount_Cv's tab+pipe combination).
BLIND_TAB_CASES = [
    "Award Name\tOctober 2024\tSelected from a national pool of applicants.",
    "1997 – National Institute for Staff and Organizational Development (NISOD) Award for\tTeaching Excellence. Valencia Community College, Orlando, Florida.",
    "2022\tWorld's Best Social Scientists, Ranking | Research.com",
]


def _render_honors(entries):
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document()
    gen.doc.add_paragraph("H. HONORS AND AWARDS")
    table = gen.doc.add_table(rows=1, cols=3)
    for i, header in enumerate(["Name of award", "Organization", "Date awarded (yyyy)"]):
        table.rows[0].cells[i].text = header
    gen._fill_honors(entries)
    return [[c.text for c in r.cells] for r in table.rows[1:]]


# --- (a) no line the old newline split produced is lost ---------------------

def test_multiline_entries_are_returned_unchanged():
    for case in MULTILINE_CASES:
        old = entry_lines(case)
        assert len(old) > 1, f"fixture {case!r} is not actually multi-line"
        assert _entry_parts(case) == old, f"diverged on already-multiline {case!r}"


def test_blind_lines_with_a_tab_are_also_returned_whole():
    for case in BLIND_TAB_CASES:
        old = entry_lines(case)
        assert old == [case]
        assert _entry_parts(case) == old, f"tab-bearing blind line split: {case!r}"


# --- (b) positive control: fails on dev today --------------------------------

def test_positive_control_pipe_blind_multi_award_entry_gains_both_rows():
    """Today's `elif '|' in line:` branch reads only the first two pipe
    segments of a single-line entry and silently drops the rest -- this is a
    single line (no '\\n'), so `entry_lines` returns exactly one opaque part
    and the multi-award branch (which needs >1 award_lines) never triggers;
    the entry falls to the single-award branch, which renders the whole raw
    text truncated at 150 chars as one garbled row. Fails on dev.
    """
    entry = {
        "text": "Best Teaching Award | 2020 | Excellence in Mentorship Award | 2018",
        "extracted_fields": {},
    }
    rows = _render_honors([entry])
    assert len(rows) == 2, f"expected 2 awards, got {rows}"
    assert rows[0][0] == "Best Teaching Award"
    assert rows[0][2] == "2020"
    assert rows[1][0] == "Excellence in Mentorship Award"
    assert rows[1][2] == "2018"


# --- (c) negative control: a single-part entry is unchanged ------------------

def test_negative_control_single_award_unchanged():
    entry = {
        "text": "2020 AECT Distinguished Service Award, Purdue University",
        "extracted_fields": {
            "award_name": "AECT Distinguished Service Award",
            "granting_body": "Purdue University",
            "date": "2020",
        },
    }
    rows = _render_honors([entry])
    assert len(rows) == 1
    assert rows[0][0] == "AECT Distinguished Service Award"
    assert rows[0][1] == "Purdue University"
    assert rows[0][2] == "2020"


def test_negative_control_farm_pipe_blind_year_first_shape_unchanged():
    """2054_Opresko_Cv's actual shape: a single-line 'YEAR | Award, Org'
    entry. `_entry_parts` DOES split it ('|', no tab), but the multi-award
    branch's own award_name-substring check still routes it back to the
    clean extracted fields, so the rendered row is identical to today's."""
    entry = {
        "text": "1991 | Freshman Chemistry Achievement Award, CRC Press",
        "extracted_fields": {
            "award_name": "Freshman Chemistry Achievement Award",
            "granting_body": "CRC Press",
            "date": "1991",
        },
    }
    rows = _render_honors([entry])
    assert len(rows) == 1
    assert rows[0][0] == "Freshman Chemistry Achievement Award"
    assert rows[0][1] == "CRC Press"
    assert rows[0][2] == "1991"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
