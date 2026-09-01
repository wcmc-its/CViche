"""Regression guard for issue #549: word-boundary the B1 in-progress markers.

`_degree_is_in_progress` (`stage6/sections/education.py`) decided whether the
B1 "Year Awarded" cell prints a bare year or "Expected <year>". It matched its
marker vocabulary with a bare substring test (`marker in text`), so a
CONFERRED degree could render as unconferred: 'present' is a substring of
"presented" / "presentation" / "presently", and 'candidate' is an ordinary
noun in award names ("Candidate for Honors"). The fix word-bounds every
remaining marker and drops 'candidate' and 'present' outright, per the real
local corpus read recorded in the PR body (0 real 'candidate' hits across 175
B1 entries; the sole 'present' hit is a literal "2022-Present" date-range
value, not degree-status prose).

Two levels of test:

- `TestDegreeIsInProgressWordBoundary` calls `_degree_is_in_progress`
  directly with the issue's own six example lines (verbatim from the issue
  body) plus the dropped-marker cases.
- `TestEducationTableRenderPositiveControl` renders the real WCM template's
  Education table through `_fill_education` with one genuinely in-progress
  entry and one conferred-but-substring-matching entry side by side, so the
  test proves the render *path* (not just the isolated predicate) tells the
  two apart -- the inverted-predicate positive control the issue asks for:
  reverting the source fix flips both entries to "Expected", which is the
  render-level signal the corpus render-gate A/B would need to see this
  section move at all (#547 blind-spots).

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_education_in_progress.py -p no:cacheprovider
"""
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from docx import Document  # noqa: E402

from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402


def _generator():
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document(gen.template_path)
    return gen


def _education_rows(gen):
    """Data rows (as text tuples) of the table under the EDUCATION header."""
    edu_idx = gen._find_paragraph_with_text("EDUCATION")
    assert edu_idx is not None, "template lost its EDUCATION header"
    table = gen._find_table_after_paragraph(edu_idx)
    assert table is not None
    return [tuple(cell.text for cell in row.cells) for row in table.rows[1:]]


class TestDegreeIsInProgressWordBoundary:
    """Issue #549's own six example lines, run against the real function."""

    def setup_method(self):
        self.gen = WCMTemplateGenerator(verbose=False)

    def test_thesis_presented_is_not_in_progress(self):
        # 'present' was a bare substring of "presented" -- conferred degree,
        # must not render as Expected.
        assert not self.gen._degree_is_in_progress(
            "MD, Weill Cornell Medical College, 2009; thesis presented with honors",
            "2009",
        )

    def test_candidate_for_honors_is_not_in_progress(self):
        # 'candidate' was a bare substring inside an award name, not a degree
        # status marker.
        assert not self.gen._degree_is_in_progress(
            "BA, Candidate for Honors in Chemistry, 2005", "2005"
        )

    def test_pending_thesis_defense_is_in_progress(self):
        # 'pending' is a genuine whole-word marker; kept and still fires.
        assert self.gen._degree_is_in_progress(
            "MPH, 2013, pending thesis defense completed", "2013"
        )

    def test_presidential_scholar_is_not_in_progress(self):
        # Unaffected by the fix either way -- no marker substring at all.
        assert not self.gen._degree_is_in_progress(
            "PhD 2011, Presidential Scholar", "2011"
        )

    def test_plain_conferred_degree_is_not_in_progress(self):
        assert not self.gen._degree_is_in_progress("MD, May 2009", "2009")

    def test_expected_future_degree_is_still_in_progress(self):
        # The genuine positive case must keep working.
        assert self.gen._degree_is_in_progress(
            "PhD, Biology, expected May 2027", "2027"
        )

    def test_bare_word_present_is_no_longer_a_marker_at_all(self):
        # Judgement call recorded in the PR body: 'present' is dropped
        # entirely, not just bounded, because its one real corpus occurrence
        # is a "2019-Present" date-range value, not degree-status prose. Even
        # a whole-word "present" in degree text must not flip the cell.
        assert not self.gen._degree_is_in_progress(
            "PhD, Biology, present tense description of research", "2020"
        )

    def test_bare_word_candidate_is_no_longer_a_marker_at_all(self):
        # Judgement call: 'candidate' is dropped entirely -- zero real
        # corpus B1 hits, and it collides with ordinary award-name prose.
        assert not self.gen._degree_is_in_progress(
            "PhD, candidate presented no other markers", "2020"
        )

    def test_in_progress_and_ongoing_still_match_as_whole_words(self):
        assert self.gen._degree_is_in_progress("MD, in progress", "")
        assert self.gen._degree_is_in_progress("MD, in-progress", "")
        assert self.gen._degree_is_in_progress("PhD, ongoing", "")

    def test_substring_neighbors_of_kept_markers_do_not_fire(self):
        # 'pending' bounded: a word that merely contains it must not match.
        assert not self.gen._degree_is_in_progress(
            "MD, appending a note about coursework", "2010"
        )


class TestEducationTableRenderPositiveControl:
    """Inverted-predicate positive control: the render path, not just the
    isolated predicate, must tell a genuine in-progress degree apart from a
    conferred one whose raw text merely contains a marker substring.
    """

    def test_render_distinguishes_in_progress_from_substring_false_positive(self):
        gen = _generator()
        entries = [
            {
                "element_idx_start": 1,
                "taxonomy_code": "B1",
                "text": (
                    "MD, Weill Cornell Medical College, 2009; thesis "
                    "presented with honors"
                ),
                "extracted_fields": {
                    "degree": "MD",
                    "institution": "Weill Cornell Medical College",
                    "year": "2009",
                },
            },
            {
                "element_idx_start": 2,
                "taxonomy_code": "B1",
                "text": "PhD, Biology, ongoing, Cornell University",
                "extracted_fields": {
                    "degree": "PhD",
                    "institution": "Cornell University",
                    "year": "2027",
                },
            },
        ]
        gen._fill_education(entries)
        rows = _education_rows(gen)
        by_degree = {row[0]: row for row in rows}

        # The genuinely in-progress PhD keeps its Expected tag ...
        assert by_degree["PhD"][3] == "Expected 2027"
        # ... but the conferred MD -- previously flipped by the bare
        # 'present' substring inside "presented" -- prints a plain year.
        assert by_degree["MD"][3] == "2009"
