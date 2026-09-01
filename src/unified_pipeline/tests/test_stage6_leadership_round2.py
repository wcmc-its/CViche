"""Section O (leadership) round-2 review fix: PR #625, thread 3850828607.

`_fill_leadership` used to locate its section with a two-step search:
first the substring "INSTITUTIONAL LEADERSHIP", then -- if that failed --
the bare substring "Leadership". The real WCM template
(key_files/wcm_cv_template_faculty_october_2022_final.docx) has several
other bold headings that contain "Leadership" as a substring and appear
before or after the real O section: "Clinical Leadership" (para 104),
"Leadership and mentoring in programs" (para 138), and "Leadership in
Extramural Organizations" (para 168). If the primary search ever failed to
find the canonical header, the loose fallback could bind Section O's table
to one of those near-miss headings instead, silently writing leadership
rows into the wrong section of the document.

The reviewer asked for an exact canonical-header match that fails closed
(renders nothing) rather than binding to the wrong heading. These tests
pin both halves of that fix:

- the canonical header, taken verbatim from the real template, still binds
  and renders (the HARD SAFETY GATE "still renders" proof); and
- a near-miss heading that the old loose fallback would have matched does
  NOT bind -- the wrong table is left untouched and nothing is inserted.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_leadership_round2.py -p no:cacheprovider
"""

import sys
from pathlib import Path

from docx import Document

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402
from unified_pipeline.stage6.formatting import format_date_range  # noqa: E402

# The exact string is asserted independently of the leadership module's own
# _LEADERSHIP_SECTION_HEADER constant -- if that constant itself were typo'd,
# comparing against a copy of itself would never catch it. This is a second,
# hand-transcribed copy of the paragraph 156 text in the real template.
CANONICAL_HEADER = "INSTITUTIONAL LEADERSHIP ACTIVITIES"

# A heading the old loose "Leadership" substring fallback would have matched
# (it is bold in the real template, same as the canonical header), but which
# is a completely different section.
NEAR_MISS_HEADING = "Clinical Leadership"


def _real_template_generator():
    """A generator loaded against the actual WCM template, verbose off."""
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document(gen.template_path)
    return gen


def _fake_doc_generator(heading_text: str):
    """A generator over a minimal hand-built doc: one bold heading paragraph
    followed by a 3-column table that already has a sentinel data row.

    Deliberately does NOT contain the canonical header anywhere -- this
    stands in for the "primary search failed" case where the old code fell
    through to its loose substring fallback.
    """
    gen = WCMTemplateGenerator(verbose=False)
    doc = Document()
    heading = doc.add_paragraph()
    run = heading.add_run(heading_text)
    run.bold = True
    table = doc.add_table(rows=1, cols=3)
    table.rows[0].cells[0].text = "Role(s)/Position"
    table.rows[0].cells[1].text = "Institution/Location"
    table.rows[0].cells[2].text = "Dates"
    sentinel = table.add_row()
    sentinel.cells[0].text = "SENTINEL-DO-NOT-TOUCH"
    gen.doc = doc
    return gen, table


def _leadership_entry():
    return {
        "text": "Chair, Faculty Council, Weill Cornell Medicine, 2018-2022",
        "extracted_fields": {
            "leadership_role": "Chair, Faculty Council",
            "institution": "Weill Cornell Medicine",
            "start_date": "2018",
            "end_date": "2022",
        },
        "taxonomy_code": "O",
    }


class TestExactCanonicalHeaderStillRenders:
    """HARD SAFETY GATE: a realistic valid input (the real template's exact
    canonical header) must still render after the fix."""

    def test_canonical_header_binds_and_entry_renders(self):
        gen = _real_template_generator()
        entry = _leadership_entry()

        gen._fill_leadership([entry])

        section_idx = gen._find_paragraph_exact(CANONICAL_HEADER)
        assert section_idx is not None, (
            "canonical header missing from the real template -- test setup is stale"
        )
        table = gen._find_table_after_paragraph(section_idx)
        assert table is not None

        data_rows = [tuple(cell.text for cell in row.cells) for row in table.rows[1:]]
        expected_dates = format_date_range("2018", "2022", "O")
        assert data_rows == [("Chair, Faculty Council", "Weill Cornell Medicine", expected_dates)]

        # And the render actually happened, not merely "a row exists somewhere".
        assert gen.stats["tables_populated"] == 1
        assert gen.stats["entries_inserted"] == 1


class TestNearMissHeadingDoesNotBind:
    """Regression pin for thread 3850828607: a heading that only loosely
    contains "Leadership" must not capture Section O's table."""

    def test_near_miss_heading_is_not_bound(self):
        gen, wrong_table = _fake_doc_generator(NEAR_MISS_HEADING)
        entry = _leadership_entry()

        gen._fill_leadership([entry])

        # The wrong table was never touched: still exactly the header row
        # plus the untouched sentinel row.
        rows = [tuple(cell.text for cell in row.cells) for row in wrong_table.rows]
        assert rows == [
            ("Role(s)/Position", "Institution/Location", "Dates"),
            ("SENTINEL-DO-NOT-TOUCH", "", ""),
        ]

        # Fail closed, not fail open: nothing was rendered anywhere.
        assert gen.stats["tables_populated"] == 0
        assert gen.stats["entries_inserted"] == 0

    def test_exact_match_helper_rejects_the_near_miss(self):
        # Narrower unit-level pin on the same fix: _find_paragraph_exact
        # (what _fill_leadership now calls) must not treat the near-miss
        # heading as the canonical header, even though the old substring
        # search would have.
        gen, _ = _fake_doc_generator(NEAR_MISS_HEADING)
        assert gen._find_paragraph_exact(CANONICAL_HEADER) is None
        assert gen._find_paragraph_with_text("Leadership") is not None, (
            "test setup sanity check: the near-miss heading should still be "
            "findable by the old loose substring search"
        )


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
