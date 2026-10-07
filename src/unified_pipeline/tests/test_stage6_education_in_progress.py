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

Two levels of test, plus a third added for review round 1 (PR #712) covering
`_fill_education`'s other data-shaping paths and the #659 malformed-input
regression:

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
- `TestDatesAttendedShapes`, `TestRawTextYearFallback`,
  `TestInstitutionLocationEnrichment`, `TestSkipAndNormalizationRules`,
  `TestTemplateSafetyExits`, `TestReverseChronologicalOrder`,
  `TestInProgressMarkerVocabulary`, and `TestFutureYearSignalAlone` are net
  new render-level coverage of pre-existing `_fill_education` behaviour that
  had no test anywhere on dev (review thread 3914996508, items 1-8).
- `TestMalformedEducationEntries` regression-tests #659: a `degree: None` or
  `institution: None` field (real on the 66-CV farm) and a non-mapping
  `extracted_fields` or entry no longer abort the Stage 6 render (review
  thread 3914946593).

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_education_in_progress.py -p no:cacheprovider
"""
import logging
import sys
from datetime import datetime
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest  # noqa: E402
from docx import Document  # noqa: E402
from docx.oxml.ns import qn  # noqa: E402

from unified_pipeline.stage6.formatting import format_date_range  # noqa: E402
from unified_pipeline.stage6.resolution import _get_institution_location  # noqa: E402
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


def _cell_ins_parts(cell):
    """(text, author) for every tracked-insertion (`w:ins`) run in a cell,
    in document order. `_add_track_change_insertion` appends `w:ins`
    directly onto the paragraph element, so plain `cell.text` (which only
    sees direct `w:r` children) never shows this content -- it has to be
    read from the XML."""
    parts = []
    for para in cell.paragraphs:
        for ins in para._p.findall(qn('w:ins')):
            text = ''.join(t.text or '' for t in ins.findall('.//' + qn('w:t')))
            parts.append((text, ins.get(qn('w:author'))))
    return parts


def _first_data_row(gen):
    """The first data row (row index 1) of the Education table."""
    edu_idx = gen._find_paragraph_with_text("EDUCATION")
    assert edu_idx is not None
    table = gen._find_table_after_paragraph(edu_idx)
    assert table is not None
    assert len(table.rows) > 1, "no data row was rendered"
    return table.rows[1]


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


class TestDatesAttendedShapes:
    """`dates_attended` arrives from field extraction in three different
    shapes -- flat `dates_attended_start_date`/`_end_date`, a nested
    `dates_attended` dict, and generic `start_date`/`end_date` -- and
    `_fill_education` tries all three before falling back to an empty cell.
    Render-level coverage the section previously had none of (review thread
    3914996508 item 1)."""

    def test_flat_nested_and_generic_shapes_render_the_same_range(self):
        gen = _generator()
        expected = format_date_range('2015-08-01', '2019-05-01', 'B1')
        assert expected, "fixture dates must actually format to something"
        entries = [
            {
                "text": "MD",
                "extracted_fields": {
                    "degree": "Flat Shape",
                    "institution": "Ohio State University",
                    "dates_attended_start_date": "2015-08-01",
                    "dates_attended_end_date": "2019-05-01",
                },
            },
            {
                "text": "MD",
                "extracted_fields": {
                    "degree": "Nested Shape",
                    "institution": "Ohio State University",
                    "dates_attended": {
                        "start_date": "2015-08-01",
                        "end_date": "2019-05-01",
                    },
                },
            },
            {
                "text": "MD",
                "extracted_fields": {
                    "degree": "Generic Shape",
                    "institution": "Ohio State University",
                    "start_date": "2015-08-01",
                    "end_date": "2019-05-01",
                },
            },
            {
                "text": "MD",
                "extracted_fields": {
                    "degree": "No Dates Shape",
                    "institution": "Ohio State University",
                },
            },
        ]
        gen._fill_education(entries)
        rows = _education_rows(gen)
        by_degree = {row[0]: row for row in rows}

        assert by_degree["Flat Shape"][2] == expected
        assert by_degree["Nested Shape"][2] == expected
        assert by_degree["Generic Shape"][2] == expected
        assert by_degree["No Dates Shape"][2] == ""


class TestRawTextYearFallback:
    """`_extract_year_from_text` recovers a year from raw text when field
    extraction did not parse one, and the result is emitted as tracked
    content attributed "Text Extraction" -- not plain text (review thread
    3914996508 item 2)."""

    def test_year_recovered_from_raw_text_is_tracked_as_text_extraction(self):
        gen = _generator()
        entries = [{
            "text": "MD, graduated December 2015 with honors",
            "extracted_fields": {
                "degree": "Raw Text Year",
                "institution": "Ohio State University",
            },
        }]
        gen._fill_education(entries)
        row = _first_data_row(gen)

        assert row.cells[0].text == "Raw Text Year"
        # Plain cell.text never includes tracked-insertion runs (see
        # `_cell_ins_parts`), so a bare-text assertion here would pass even
        # if the year were dropped entirely -- the tracked content must be
        # read from the XML.
        assert row.cells[3].text == ""
        assert _cell_ins_parts(row.cells[3]) == [("2015", "Text Extraction")]


class TestYearAwardedNeedsANamedDegree:
    """EBYSBC E33 (#1245): a row that names no degree (schooling, a year of
    study, a major alone) took its Year Awarded from the end of the
    attendance range, asserting an award the CV never states. The two
    inferred fallbacks now apply only when a degree is named; a stated
    `year_awarded`/`year` still renders either way."""

    def _row(self, fields, text="Example line"):
        gen = _generator()
        gen._fill_education([{"text": text, "extracted_fields": fields}])
        return _first_data_row(gen)

    def test_a_degreeless_row_has_no_year_awarded_from_its_attendance_end(self):
        row = self._row({
            "degree": None, "major": "Liberal Arts", "institution": "Example College",
            "dates_attended": {"start_date": "1984", "end_date": "1985"},
        })
        assert row.cells[2].text != ""
        assert row.cells[3].text == ""
        assert _cell_ins_parts(row.cells[3]) == []

    def test_a_degreeless_row_has_no_year_awarded_from_raw_text(self):
        row = self._row({"degree": None, "institution": "Example Academy"},
                        text="Example Academy, December 1961")
        assert row.cells[3].text == ""
        assert _cell_ins_parts(row.cells[3]) == []

    def test_a_named_degree_still_falls_back_to_its_attendance_end(self):
        row = self._row({
            "degree": "BA", "institution": "Example College",
            "dates_attended": {"start_date": "1984", "end_date": "1988"},
        })
        assert row.cells[3].text == "1988"

    def test_a_training_title_row_has_no_year_awarded_1415(self):
        """#1415 (RCBKFG GKAQHB 17/20): an internship or residency coded B1
        names a training post, not a degree; its attendance end is not an
        award year, and neither is a year in its raw text."""
        for title in ("Intern (Example Medicine)", "Resident (Example Pathology)",
                      "Residency, Example Surgery", "Postdoctoral Fellow",
                      "Clinical Fellow in Example Care", "Fellowship, Example Care",
                      "Example Internship", "Example Residency (Example Care)"):
            row = self._row({
                "degree": title, "institution": "Example Hospital",
                "dates_attended": {"start_date": "1991", "end_date": "1992"},
            }, text=f"{title} Example Hospital 1991-1992")
            assert row.cells[2].text != "", title
            assert row.cells[3].text == "", title
            assert _cell_ins_parts(row.cells[3]) == [], title

    def test_a_degree_that_merely_mentions_training_still_falls_back(self):
        """Only a cell that OPENS with a training title is refused; a degree
        whose field names a residency, and a society fellowship, keep the
        attendance-end fallback."""
        for degree in ("MD, Internal Medicine Residency Track", "Fellow of Example College",
                       "MS Example Studies Residency"):
            row = self._row({
                "degree": degree, "institution": "Example College",
                "dates_attended": {"start_date": "1984", "end_date": "1988"},
            })
            assert row.cells[3].text == "1988", degree

    def test_a_stated_year_still_renders_on_a_training_title_row(self):
        row = self._row({"degree": "Resident", "institution": "Example Hospital",
                         "year": "1996"})
        assert row.cells[3].text == "1996"

    def test_a_stated_year_renders_without_a_degree(self):
        row = self._row({"degree": None, "major": "Example Studies",
                         "institution": "Example College", "year": "1990"})
        assert row.cells[3].text == "1990"


class TestInstitutionLocationEnrichment:
    """Institution-location enrichment (review thread 3914996508 item 3):
    an enriched location is appended and tracked as "Institution
    Enrichment", a city already present in the institution string is not
    duplicated (`location_already_present`), and a non-enriched location is
    appended as plain (untracked) text."""

    def test_enriched_location_is_appended_and_tracked(self):
        gen = _generator()
        entries = [{
            "text": "MD",
            "extracted_fields": {"degree": "MD", "institution": "Ohio State University"},
            "institution_enrichment": {"city": "Columbus", "state": "Ohio", "country_code": "US"},
        }]
        gen._fill_education(entries)
        row = _first_data_row(gen)

        assert row.cells[1].text == "Ohio State University"
        assert _cell_ins_parts(row.cells[1]) == [(", Columbus, OH", "Institution Enrichment")]

    def test_city_already_in_institution_is_not_duplicated(self):
        gen = _generator()
        entries = [{
            "text": "MD",
            "extracted_fields": {
                "degree": "MD",
                "institution": "University of Pittsburgh, Pittsburgh, PA",
            },
            "institution_enrichment": {"city": "Pittsburgh", "state": "Pennsylvania", "country_code": "US"},
        }]
        gen._fill_education(entries)
        row = _first_data_row(gen)

        assert row.cells[1].text == "University of Pittsburgh, Pittsburgh, PA"
        assert _cell_ins_parts(row.cells[1]) == []

    def test_non_enriched_location_is_appended_as_plain_text(self):
        gen = _generator()
        entries = [{
            "text": "MD",
            "extracted_fields": {
                "degree": "MD",
                "institution": "Ohio State University",
                "location": "Boston, MA",
            },
        }]
        gen._fill_education(entries)
        row = _first_data_row(gen)

        assert row.cells[1].text == "Ohio State University, Boston, MA"
        assert _cell_ins_parts(row.cells[1]) == []


class TestSkipAndNormalizationRules:
    """Skip/normalization business rules (review thread 3914996508 item 4):
    a literal "None" institution string is treated as empty, an entry with
    neither degree nor institution is skipped, `major`/`field_of_study` is
    appended to the degree, and a cleaned institution name from enrichment
    replaces the raw one."""

    def test_institution_literal_none_string_is_treated_as_empty(self):
        gen = _generator()
        entries = [{
            "text": "MD",
            "extracted_fields": {"degree": "MD", "institution": "None"},
        }]
        gen._fill_education(entries)
        row = _first_data_row(gen)

        assert row.cells[0].text == "MD"
        assert row.cells[1].text == ""

    def test_entry_with_neither_degree_nor_institution_is_skipped(self):
        gen = _generator()
        entries = [{"text": "just some prose", "extracted_fields": {}}]
        gen._fill_education(entries)

        assert _education_rows(gen) == []

    def test_major_is_appended_to_degree(self):
        gen = _generator()
        entries = [{
            "text": "MD",
            "extracted_fields": {
                "degree": "MD", "major": "Biology", "institution": "Ohio State University",
            },
        }]
        gen._fill_education(entries)
        row = _first_data_row(gen)

        assert row.cells[0].text == "MD, Biology"

    def test_field_of_study_is_the_major_fallback(self):
        gen = _generator()
        entries = [{
            "text": "MD",
            "extracted_fields": {
                "degree": "MD", "field_of_study": "Biology", "institution": "Ohio State University",
            },
        }]
        gen._fill_education(entries)
        row = _first_data_row(gen)

        assert row.cells[0].text == "MD, Biology"

    def test_cleaned_institution_name_replaces_the_raw_one(self):
        gen = _generator()
        entries = [{
            "text": "MD",
            "extracted_fields": {"degree": "MD", "institution": "Duke Univ (raw)"},
            "institution_enrichment": {"cleaned_name": "Duke University"},
        }]
        gen._fill_education(entries)
        row = _first_data_row(gen)

        assert row.cells[1].text == "Duke University"


class TestTemplateSafetyExits:
    """Template-safety exits (review thread 3914996508 item 5): a missing
    EDUCATION header or a missing table after it must not raise, and a
    normal fill must leave the table's header row text untouched.

    The real template has several OTHER headings whose text also contains
    "education" as a substring ("Other Educational Experiences",
    "EDUCATIONAL CONTRIBUTIONS", ...), so blanking just the B1 header
    paragraph makes `_find_paragraph_with_text("EDUCATION")` fall through to
    one of those instead of returning None -- it would exercise a different,
    unrelated bug (loose substring matching) rather than the guard this test
    targets. The finder methods are faked directly instead, which isolates
    exactly the "not found" contract `_fill_education` guards against.
    """

    def test_missing_education_header_returns_without_raising(self, monkeypatch):
        gen = _generator()
        monkeypatch.setattr(gen, "_find_header_paragraph", lambda _text: None)

        gen._fill_education([{"text": "MD", "extracted_fields": {"degree": "MD"}}])

        assert gen.stats['tables_populated'] == 0

    def test_missing_education_table_returns_without_raising(self, monkeypatch):
        gen = _generator()
        monkeypatch.setattr(gen, "_find_table_after_paragraph", lambda _idx: None)

        gen._fill_education([{"text": "MD", "extracted_fields": {"degree": "MD"}}])

        assert gen.stats['tables_populated'] == 0

    def test_header_row_text_survives_a_normal_fill(self):
        gen = _generator()
        edu_idx = gen._find_paragraph_with_text("EDUCATION")
        table = gen._find_table_after_paragraph(edu_idx)
        header_before = tuple(cell.text for cell in table.rows[0].cells)

        gen._fill_education([{
            "text": "MD",
            "extracted_fields": {"degree": "MD", "institution": "Ohio State University"},
        }])

        header_after = tuple(cell.text for cell in table.rows[0].cells)
        assert header_after == header_before


class TestReverseChronologicalOrder:
    """`sort_entries_reverse_chronological` puts the most recent entry
    first; the existing two-entry render test never asserted row order
    (review thread 3914996508 item 6)."""

    def test_newer_entry_renders_before_older_one(self):
        gen = _generator()
        entries = [
            {
                "text": "BS",
                "extracted_fields": {
                    "degree": "BS Older", "institution": "Ohio State University", "year": "2005",
                },
            },
            {
                "text": "MD",
                "extracted_fields": {
                    "degree": "MD Newer", "institution": "Ohio State University", "year": "2019",
                },
            },
        ]
        gen._fill_education(entries)
        rows = _education_rows(gen)

        assert [row[0] for row in rows] == ["MD Newer", "BS Older"]


class TestInProgressMarkerVocabulary:
    """Full supported marker vocabulary, parametrized (review thread
    3914996508 item 7): 'anticipated', 'to be conferred', and 'to be
    awarded' had no coverage at all before this."""

    @pytest.mark.parametrize("marker", [
        "expected", "anticipated", "in progress", "in-progress", "ongoing",
        "to be conferred", "to be awarded", "pending",
    ])
    def test_each_marker_word_marks_the_degree_in_progress(self, marker):
        gen = WCMTemplateGenerator(verbose=False)
        # A non-future year, so only the marker signal (not the future-year
        # signal) can be responsible for a True result.
        assert gen._degree_is_in_progress(f"MD, {marker} 2020", "2020")


class TestFutureYearSignalAlone:
    """The future-year signal must fire independently of any marker word
    (review thread 3914996508 item 8): the existing 'expected ... 2027' case
    could pass on the marker alone without the future-year rule working at
    all."""

    def test_future_year_alone_is_in_progress_with_no_marker_word(self):
        gen = WCMTemplateGenerator(verbose=False)
        future_year = str(datetime.now().year + 3)
        assert gen._degree_is_in_progress(
            f"MD, Ohio State University, {future_year}", future_year
        )

    def test_render_level_future_year_alone_shows_expected(self):
        gen = _generator()
        future_year = str(datetime.now().year + 3)
        entries = [{
            "text": f"MD, Ohio State University, {future_year}",
            "extracted_fields": {
                "degree": "MD",
                "institution": "Ohio State University",
                "year": future_year,
            },
        }]
        gen._fill_education(entries)
        row = _first_data_row(gen)

        assert row.cells[3].text == f"Expected {future_year}"


class TestMalformedEducationEntries:
    """#659 (review thread 3914946593): stage 4 emits an explicit ``None``
    for a missing degree/institution rather than omitting the key -- real on
    ten and five entries respectively across the 66-CV farm -- and a truthy
    non-mapping `extracted_fields` or entry must degrade gracefully instead
    of aborting the whole Stage 6 render."""

    def test_degree_none_with_major_renders_using_the_major(self):
        gen = _generator()
        entries = [{
            "text": "Biology",
            "extracted_fields": {
                "degree": None,
                "major": "Biology",
                "institution": "Ohio State University",
            },
        }]
        gen._fill_education(entries)  # must not raise
        row = _first_data_row(gen)

        assert row.cells[0].text == "Biology"

    def test_institution_none_with_a_degree_renders(self):
        gen = _generator()
        entries = [{
            "text": "MD",
            "extracted_fields": {"degree": "MD", "institution": None},
        }]
        gen._fill_education(entries)  # must not raise
        row = _first_data_row(gen)

        assert row.cells[0].text == "MD"
        assert row.cells[1].text == ""

    def test_extracted_fields_as_a_list_is_treated_as_no_fields(self):
        gen = _generator()
        entries = [{"text": "some prose", "extracted_fields": ["not", "a", "dict"]}]
        gen._fill_education(entries)  # must not raise

        assert _education_rows(gen) == []

    def test_entry_that_is_not_a_mapping_is_skipped_and_logged(self, caplog):
        gen = _generator()
        entries = [
            {
                "text": "MD",
                "extracted_fields": {"degree": "MD", "institution": "Ohio State University"},
            },
            "not a mapping at all",
        ]
        with caplog.at_level(logging.WARNING):
            gen._fill_education(entries)  # must not raise

        rows = _education_rows(gen)
        assert [row[0] for row in rows] == ["MD"]

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        message = warnings[0].getMessage()
        assert "1" in message  # index of the malformed entry
        assert "str" in message  # its type

    def test_non_string_location_renders_without_raising(self):
        """`_get_institution_location`'s fallback path returns
        `extracted_fields.location` as-is (`stage6/resolution/institution.py`),
        so a non-string `location` (an `int` here) used to raise
        `AttributeError` at `location.split(',')`. `_field_text` on the
        returned location coerces it first. Per D1 (stringify, don't drop),
        the institution cell reads "X, 5" rather than dropping the location."""
        gen = _generator()
        entries = [{
            "text": "MD",
            "extracted_fields": {
                "degree": "MD",
                "institution": "X",
                "year": "2010",
                "location": 5,
            },
        }]
        gen._fill_education(entries)  # must not raise
        row = _first_data_row(gen)

        assert row.cells[0].text == "MD"
        assert row.cells[1].text == "X, 5"


def test_a_location_the_source_already_states_is_not_a_tracked_change():
    """#897 item 3: stage 5b parses ", Ewing, NJ" OUT of "The College of New
    Jersey, Ewing, NJ" and hands it back as city/state, and
    `_get_institution_location` reported that as an enrichment -- so the cell
    rendered the faculty's own text as a tracked insertion with an
    "Institution Enrichment" comment. A location the source states renders
    as plain text; only a location the source lacks is a tracked change."""
    def degree(institution):
        return {
            "text": f"B.S. | {institution} | 2003-2007",
            "taxonomy_code": "B1",
            "extracted_fields": {"degree": "B.S.", "institution": institution,
                                 "start_date": "2003-08", "end_date": "2007-05", "year": "2007"},
            "institution_enrichment": {"cleaned_name": "Norvale College",
                                       "city": "Crab Hollow", "state": "New York",
                                       "country_code": "US"},
        }

    gen = _generator()
    gen._fill_education([degree("Norvale College, Crab Hollow, NY")])
    stated = _first_data_row(gen).cells[1]
    assert stated.text == "Norvale College, Crab Hollow, NY"
    assert _cell_ins_parts(stated) == []

    gen = _generator()
    gen._fill_education([degree("Norvale College")])
    added = _first_data_row(gen).cells[1]
    assert added.text == "Norvale College"
    assert _cell_ins_parts(added) == [(", Crab Hollow, NY", "Institution Enrichment")]


@pytest.mark.parametrize("text, institution, expected", [
    # the source states it, abbreviated or spelt out, in the field or the text
    ("B.S. | Norvale College, Crab Hollow, NY | 2003", "Norvale College, Crab Hollow, NY", False),
    ("B.S. | Norvale College, Crab Hollow, New York | 2003", "Norvale College, Crab Hollow, New York", False),
    ("Residency | Norvale College\nCrab Hollow, NY | 2011", "Norvale College", False),
    # the source lacks it
    ("B.S. | Norvale College | 2003", "Norvale College", True),
    # the city inside the NAME is not the location being stated
    ("B.S. | University of Crab Hollow | 2003", "University of Crab Hollow", True),
])
def test_get_institution_location_reports_enrichment_only_when_the_source_lacks_it(text, institution, expected):
    entry = {"text": text,
             "extracted_fields": {"institution": institution},
             "institution_enrichment": {"city": "Crab Hollow", "state": "New York", "country_code": "US"}}
    assert _get_institution_location(entry) == ("Crab Hollow, NY", expected)
def test_a_city_named_institution_still_gets_its_location():
    """#897: the education cell refused to append the enriched location when
    the city WORD appeared anywhere in the institution name, so a degree
    from "Crab Hollow University" lost its ", Crab Hollow, NY". The shared
    tail predicate only treats a trailing ", City[, ST]" as already present.

    The source states no location here on purpose: since #899 a location the
    source already states is written plain, not as an enrichment tracked
    change, so this fixture must leave the city to stage 5b to remain a
    test of the city-word guard rather than of #899's source check."""
    gen = _generator()
    gen._fill_education([{
        "text": "B.S. | Crab Hollow University | 2003-2007",
        "taxonomy_code": "B1",
        "extracted_fields": {
            "degree": "B.S.",
            "institution": "Crab Hollow University",
            "start_date": "2003-08", "end_date": "2007-05", "year": "2007",
        },
        "institution_enrichment": {
            "cleaned_name": "Crab Hollow University",
            "city": "Crab Hollow", "state": "New York", "country_code": "US",
        },
    }])
    row = _first_data_row(gen)

    # the name is plain text; the location is the enrichment tracked change
    assert row.cells[1].text == "Crab Hollow University"
    assert _cell_ins_parts(row.cells[1]) == [(", Crab Hollow, NY", "Institution Enrichment")]


class TestDisciplineAndStringDates:
    """#1187: the degree cell is "Degree, field of study" from stage 4's
    `discipline`, and a STRING `dates_attended` fills the Dates cell when no
    start/end range exists. Synthetic values only."""

    @staticmethod
    def _render(**fields):
        gen = _generator()
        gen._fill_education([{
            "text": "x",
            "extracted_fields": {"institution": "Quillfeather University", **fields},
        }])
        return _first_data_row(gen)

    def test_discipline_is_appended_to_the_degree(self):
        row = self._render(degree="PhD", discipline="Zymology")
        assert row.cells[0].text == "PhD, Zymology"

    @pytest.mark.parametrize("degree", [
        "PhD in Zymology", "phd, ZYMOLOGY", "Ph.D. (Zym-ology)"])
    def test_discipline_the_degree_already_holds_is_not_repeated(self, degree):
        discipline = "Zym ology" if "Zym-ology" in degree else "Zymology"
        row = self._render(degree=degree, discipline=discipline)
        assert row.cells[0].text == degree

    def test_discipline_is_added_after_major_unless_major_holds_it(self):
        assert self._render(degree="MS", major="Zymology",
                            discipline="Brewing").cells[0].text == "MS, Zymology, Brewing"
        assert self._render(degree="MS", major="Zymology",
                            discipline="zymology").cells[0].text == "MS, Zymology"

    def test_a_blank_or_none_discipline_changes_nothing(self):
        assert self._render(degree="PhD", discipline=None).cells[0].text == "PhD"
        assert self._render(degree="PhD", discipline="  ").cells[0].text == "PhD"

    def test_a_discipline_alone_does_not_create_a_row(self):
        gen = _generator()
        gen._fill_education([{"text": "x", "extracted_fields": {"discipline": "Zymology"}}])
        assert _education_rows(gen) == []

    def test_string_dates_attended_fills_an_empty_dates_cell_verbatim(self):
        row = self._render(degree="PhD", dates_attended="Sept 2001 - May 2005")
        assert row.cells[2].text == "Sept 2001 - May 2005"

    def test_string_dates_attended_yields_to_a_start_end_range(self):
        expected = format_date_range("2001-09-01", "2005-05-01", "B1")
        row = self._render(degree="PhD", dates_attended="1999-2000",
                           start_date="2001-09-01", end_date="2005-05-01")
        assert row.cells[2].text == expected

    def test_a_blank_string_dates_attended_leaves_the_cell_empty(self):
        assert self._render(degree="PhD", dates_attended="  ").cells[2].text == ""

    def test_string_dates_attended_is_formatted_like_other_b1_dates(self):
        row = self._render(degree="PhD", dates_attended="1998-06")
        assert row.cells[2].text == "06/1998"

    def test_string_dates_attended_repeating_the_year_awarded_is_skipped(self):
        row = self._render(degree="PhD", dates_attended="1987", year_awarded="1987")
        assert row.cells[2].text == ""
        assert row.cells[3].text == "1987"


class TestDegreeHonors:
    """#817 (EBYSBC E14): stage 4's off-schema B1 `honors` follows the degree
    in parentheses; before, no column read it. Synthetic values only."""

    @staticmethod
    def _render(**fields):
        return TestDisciplineAndStringDates._render(**fields)

    def test_honors_follow_the_degree_and_discipline(self):
        row = self._render(degree="BA", discipline="Zymology", honors="magna cum laude")
        assert row.cells[0].text == "BA, Zymology (magna cum laude)"

    def test_a_list_of_honors_is_comma_joined(self):
        row = self._render(degree="MD", honors=["Quill Honor Society", " ", 7, "cum laude"])
        assert row.cells[0].text == "MD (Quill Honor Society, cum laude)"

    def test_honors_the_degree_already_holds_are_not_repeated(self):
        row = self._render(degree="BA cum laude", honors="Cum Laude")
        assert row.cells[0].text == "BA cum laude"

    @pytest.mark.parametrize("honors", [None, "", "  ", {"a": "b"}])
    def test_blank_or_unusable_honors_change_nothing(self, honors):
        assert self._render(degree="PhD", honors=honors).cells[0].text == "PhD"

    def test_honors_alone_do_not_create_a_row(self):
        gen = _generator()
        gen._fill_education([{"text": "x", "extracted_fields": {"honors": "cum laude"}}])
        assert _education_rows(gen) == []


class TestDegreeAdvisor:
    """#1245 (X6 RINASX 15, CMTQDR 10): the degree's advisor, under the
    schema's `advisor` or the off-schema `major_professor`/`mentor`, follows
    the degree with its label; before, no column read it. Synthetic values."""

    @staticmethod
    def _render(**fields):
        return TestDisciplineAndStringDates._render(**fields)

    @pytest.mark.parametrize("key,label", [("advisor", "Advisor"),
                                           ("major_professor", "Major professor"),
                                           ("mentor", "Mentor")])
    def test_each_advisor_key_follows_the_degree_with_its_label(self, key, label):
        row = self._render(degree="PhD", discipline="Zymology", **{key: "Q. Example, PhD"})
        assert row.cells[0].text == f"PhD, Zymology; {label}: Q. Example, PhD"

    def test_the_advisor_follows_the_honors(self):
        row = self._render(degree="BA", honors="cum laude", mentor="Q. Example")
        assert row.cells[0].text == "BA (cum laude); Mentor: Q. Example"

    def test_the_schema_key_wins_over_an_off_schema_one(self):
        row = self._render(degree="PhD", advisor="Q. Example", mentor="R. Sample")
        assert row.cells[0].text == "PhD; Advisor: Q. Example"

    def test_an_advisor_the_degree_already_names_is_not_repeated(self):
        row = self._render(degree="PhD (with Q. Example)", major_professor="Q. Example")
        assert row.cells[0].text == "PhD (with Q. Example)"

    @pytest.mark.parametrize("value", [None, "", "  ", ["Q. Example"]])
    def test_a_blank_or_unusable_advisor_changes_nothing(self, value):
        assert self._render(degree="PhD", major_professor=value).cells[0].text == "PhD"

    def test_an_advisor_on_a_degree_less_row_stands_alone(self):
        assert self._render(mentor="Q. Example").cells[0].text == "Mentor: Q. Example"

    def test_an_advisor_alone_does_not_create_a_row(self):
        gen = _generator()
        gen._fill_education([{"text": "x", "extracted_fields": {"mentor": "Q. Example"}}])
        assert _education_rows(gen) == []
