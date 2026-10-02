"""The R block must name the meeting that hosted an invited talk.

`event_name` was read nowhere in stage_6_word_template.py, so stage 4 extracted
it and stage 6 dropped it: 2,194 of 2,956 values across 79 of the 100 distinct
corpus CVs reached no part of the rendered document. All of them are R entries,
which never pass through stage 5d, so the loss is entirely in the renderer.

The WCM faculty template fixes the block at three columns -- Title |
Institution/Location | Dates (yyyy) -- so the meeting gets no column of its own
and rides with the venue: "ASMBS 2022 Presidential Grand Rounds, Dallas, TX".

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_presentation_event_name.py -p no:cacheprovider
"""

import sys
from pathlib import Path

from docx import Document

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402


def _venue(**fields):
    """Render one R entry through the real filler and return its middle cell."""
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document(gen.template_path)
    gen.cv_owner_location = None          # skip the LLM geographic classifier
    fields.setdefault("title", "Hepatic Vagotomy in Obese Patients")
    fields.setdefault("year", "2022")
    gen._fill_presentations(
        [{"taxonomy_code": "R", "text": fields["title"], "extracted_fields": fields}])
    for table in gen.doc.tables:
        for row in table.rows:
            cells = [c.text for c in row.cells]
            if cells and cells[0] == fields["title"]:
                return cells[1]
    raise AssertionError("the entry did not render into any R table")


def test_event_name_precedes_the_location():
    # web069, real values: extracted at stage 4 and absent from the render.
    assert _venue(institution="Dallas, TX",
                  event_name="ASMBS 2022 Presidential Grand Rounds") == \
        "ASMBS 2022 Presidential Grand Rounds, Dallas, TX"


def test_event_name_stands_alone_when_no_venue_was_extracted():
    assert _venue(institution="",
                  event_name="Alfredo Lopez-S Lectureship in Nutrition") == \
        "Alfredo Lopez-S Lectureship in Nutrition"


def test_event_name_already_in_the_venue_is_not_repeated():
    assert _venue(institution="SLS 2019, New Orleans, LA",
                  event_name="SLS 2019") == "SLS 2019, New Orleans, LA"


def test_event_name_already_in_the_title_is_not_repeated():
    assert _venue(title="SAGES 2022 keynote: Reimagining the Future of Surgery",
                  institution="Denver, CO", event_name="SAGES 2022") == "Denver, CO"


def test_venue_is_untouched_when_no_event_name_was_extracted():
    assert _venue(institution="Baton Rouge, LA") == "Baton Rouge, LA"


def test_untitled_entry_renders_its_whole_text_not_the_first_150_characters():
    """#983: an R entry with no extracted title renders as its own text;
    `text[:150]` cut it mid-word."""
    text = ("Grand rounds on the Zorblax method for ferrous metallurgy and its applications to "
            "ceremonial bunting standards, with a review of historical pennant practice worldwide")
    assert len(text) > 150
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document(gen.template_path)
    gen.cv_owner_location = None
    gen._fill_presentations([{"taxonomy_code": "R", "text": text,
                              "extracted_fields": {"year": "2022"}}])
    firsts = [row.cells[0].text for table in gen.doc.tables for row in table.rows]
    assert text in firsts


def test_role_leads_the_venue():
    """#475: the speaker's role was never in the R schema, then never rendered."""
    assert _venue(institution="Northgate Institute, Springfield",
                  role="Visiting Professor") == \
        "Visiting Professor, Northgate Institute, Springfield"


def test_role_leads_event_name_and_venue():
    assert _venue(institution="Springfield", event_name="Zorblax Lecture",
                  role="Visiting Professor") == \
        "Visiting Professor, Zorblax Lecture, Springfield"


def test_role_stands_alone_when_no_venue_was_extracted():
    assert _venue(institution="", role="Visiting Professor") == "Visiting Professor"


def test_role_already_in_the_venue_is_not_repeated():
    assert _venue(institution="Visiting Professor, Northgate Institute",
                  role="Visiting Professor") == "Visiting Professor, Northgate Institute"


def test_role_already_in_the_title_is_not_prepended():
    assert _venue(title="Visiting Professor Lecture: Zorblax Methods",
                  institution="Northgate Institute", role="Visiting Professor") == \
        "Northgate Institute"


def test_role_duplicate_check_ignores_case():
    assert _venue(institution="Visiting Professor, Northgate Institute",
                  role="visiting professor") == "Visiting Professor, Northgate Institute"


def test_string_none_role_is_not_rendered():
    assert _venue(institution="Springfield", role="None") == "Springfield"


def _cells(**fields):
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document(gen.template_path)
    gen.cv_owner_location = None
    fields.setdefault("year", "2022")
    gen._fill_presentations(
        [{"taxonomy_code": "R", "text": "2022\t* raw source line, Springfield",
          "extracted_fields": fields}])
    for table in gen.doc.tables:
        for row in table.rows:
            cells = [c.text for c in row.cells]
            if "2022" in cells:
                return cells
    raise AssertionError("row not rendered")


def test_empty_title_keeps_the_raw_text_and_shows_the_role():
    """The LLM emptied title but filled role: the raw entry text must still
    render as the Title (it holds the talk title) and the role must appear."""
    cells = _cells(title="", role="Visiting Professor",
                   location="Northgate Institute, Springfield")
    assert cells[0] == "2022\t* raw source line, Springfield"
    assert cells[1] == "Visiting Professor, Northgate Institute, Springfield"


def test_role_is_stripped_before_it_is_rendered():
    assert _venue(institution="Northgate Institute", role=" Visiting Professor ") == \
        "Visiting Professor, Northgate Institute"


def _rendered_date(**fields):
    """Render one R entry through the real filler and return its Dates cell."""
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document(gen.template_path)
    gen.cv_owner_location = None          # skip the LLM geographic classifier
    fields.setdefault("title", "Example Talk on Widget Calibration")
    gen._fill_presentations(
        [{"taxonomy_code": "R", "text": fields["title"], "extracted_fields": fields}])
    for table in gen.doc.tables:
        for row in table.rows:
            cells = [c.text for c in row.cells]
            if cells and cells[0] == fields["title"]:
                return cells[-1]
    raise AssertionError("the entry did not render into any R table")


def test_a_multi_day_dict_date_renders_as_its_year_not_its_repr():
    """#1233: stage 4 returns `date` as {start_date, end_date} for a multi-day
    event; the whole repr landed in the Dates cell."""
    assert _rendered_date(date={"start_date": "2021-05-17", "end_date": "2021-05-19"}) == "2021"


def test_a_dict_date_spanning_a_new_year_renders_both_years():
    assert _rendered_date(date={"start_date": "2021-12-30", "end_date": "2022-01-02"}) == "2021-2022"


def test_a_string_date_is_unchanged_by_the_dict_handling():
    assert _rendered_date(date="2021-05-17") == "2021"


def test_a_dict_dated_row_sorts_into_date_order_not_to_the_bottom():
    """#1233: the dict date keyed (0, 0, 0), so the row it fixed still landed
    below every dated row. Newest first, wherever the dict-dated row falls."""
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document(gen.template_path)
    gen.cv_owner_location = None          # skip the LLM geographic classifier
    dates = ["2020-03-01", {"start_date": "2021-05-17", "end_date": "2021-05-19"}, "2019-06-01"]
    gen._fill_presentations([
        {"taxonomy_code": "R", "text": f"Example Talk {i}",
         "extracted_fields": {"title": f"Example Talk {i}", "date": date}}
        for i, date in enumerate(dates)])
    rows = [[c.text for c in row.cells] for table in gen.doc.tables for row in table.rows]
    assert [r[-1] for r in rows if r[0].startswith("Example Talk")] == ["2021", "2020", "2019"]
