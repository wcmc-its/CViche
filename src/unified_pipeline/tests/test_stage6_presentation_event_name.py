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


def test_empty_title_keeps_the_rest_of_the_raw_text_and_shows_the_role():
    """The LLM emptied title but filled role: what the source line says beyond
    role, venue and year may be the talk title (#475), so it still renders --
    without the leading year the Dates cell shows (EBYSBC E23)."""
    cells = _cells(title="", role="Visiting Professor",
                   location="Northgate Institute, Springfield")
    assert cells[0] == "* raw source line, Springfield"
    assert cells[1] == "Visiting Professor, Northgate Institute, Springfield"


def _untitled_row(text, **fields):
    """Render one title-less R entry and return its cells."""
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document(gen.template_path)
    gen.cv_owner_location = None
    gen._fill_presentations(
        [{"taxonomy_code": "R", "text": text, "extracted_fields": fields}])
    for table in gen.doc.tables:
        for row in table.rows:
            cells = [c.text for c in row.cells]
            if cells[-1] and cells[-1] == fields["date"][:4]:
                return cells
    raise AssertionError("row not rendered")


def test_untitled_talk_with_event_name_is_titled_by_the_event_not_the_source_line():
    """EBYSBC MRJDWE-08: a numbered 'N. YYYY. <series>. <venue>' line filled the
    Title cell, repeating the venue and year of the next two cells."""
    cells = _untitled_row("4. 2018. Zorblax Seminar series. Northgate Diabetes Center",
                          date="2018", event_name="Zorblax Seminar series",
                          location="Northgate Diabetes Center")
    assert cells == ["Zorblax Seminar series", "Northgate Diabetes Center", "2018"]


def test_untitled_talk_is_titled_by_role_and_event_name():
    cells = _untitled_row("2021 Fern Graduate Program, Annual Retreat Presenter, CO",
                          date="2021", role="Presenter",
                          event_name="Fern Graduate Program, Annual Retreat", location="CO")
    assert cells == ["Presenter, Fern Graduate Program, Annual Retreat", "CO", "2021"]


def test_untitled_talk_with_role_only_drops_venue_and_trailing_date():
    cells = _untitled_row("Visiting Professor, Northgate School\t  March  1976",
                          date="1976-03", role="Visiting Professor",
                          location="Northgate School")
    assert cells == ["Visiting Professor", "Northgate School", "1976"]


def test_untitled_talk_drops_the_leading_year_and_the_venue_from_the_source_line():
    """EBYSBC GJXIWD-04: '<year> Invited Speaker - <program>, <venue>' with no
    role field -- the year and venue already fill their own cells."""
    cells = _untitled_row("2019 Invited Speaker - Zorblax Program, Northgate University, CO",
                          date="2019", event_name="Zorblax Program",
                          location="Northgate University, CO")
    assert cells == ["Invited Speaker - Zorblax Program", "Northgate University, CO", "2019"]


def test_untitled_talk_with_only_a_venue_leaves_the_title_blank():
    """EBYSBC RNKYST-06: the line is year and venue only; both have cells."""
    cells = _untitled_row("2003: Northgate University", date="2003",
                          location="Northgate University")
    assert cells == ["", "Northgate University", "2003"]


def test_leading_year_is_kept_when_the_dates_cell_does_not_show_it():
    """No date field: the year in the line is the only place the year renders."""
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document(gen.template_path)
    gen.cv_owner_location = None
    gen._fill_presentations([{"taxonomy_code": "R",
                              "text": "2010 Northgate School: Widget Studies",
                              "extracted_fields": {}}])
    firsts = [row.cells[0].text for table in gen.doc.tables for row in table.rows]
    assert "2010 Northgate School: Widget Studies" in firsts


def test_venue_is_removed_only_as_a_whole_word():
    """A two-letter state venue must not cut into a word that starts with it."""
    cells = _untitled_row("2020 Colloquium on Copper Widgets, CO", date="2020", location="CO")
    assert cells == ["Colloquium on Copper Widgets", "CO", "2020"]


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


def test_untitled_talk_drops_a_closing_date_with_its_weekday_and_no_stranded_stop():
    cells = _untitled_row("Zorblax Network Conference, Springfield, ZZ. Saturday July 24, 2010.",
                          date="2010", location="Springfield, ZZ")
    assert cells == ["Zorblax Network Conference", "Springfield, ZZ", "2010"]


def test_untitled_talk_leaves_no_stranded_full_stop_after_the_venue():
    cells = _untitled_row("2020 Invited Speaker, Fern Center, Online.", date="2020",
                          location="Fern Center, Online")
    assert cells == ["Invited Speaker", "Fern Center, Online", "2020"]


def test_closing_date_is_kept_when_the_dates_cell_shows_another_year():
    cells = _untitled_row("Zorblax Lecture, Fern Hall, March 1999", date="2001",
                          location="Fern Hall")
    assert cells[0] == "Zorblax Lecture, March 1999"


def test_venue_inside_a_phrase_is_kept():
    """Cutting the venue out of 'hosted by <venue>' would leave the phrase
    dangling; only a venue set off as its own segment is removed."""
    cells = _untitled_row("2020 Fern Symposium hosted by Northgate University (online)",
                          date="2020", location="Northgate University")
    assert cells[0] == "Fern Symposium hosted by Northgate University (online)"


def test_list_number_is_dropped_from_a_kept_source_line():
    cells = _untitled_row("(7) Zorblax workshop talk, Northgate Hall", date="1990",
                          location="Northgate Hall")
    assert cells[0] == "Zorblax workshop talk"


def test_joining_words_alone_do_not_keep_the_source_line():
    """'at' is all that is left once role and venue are out: the role alone
    is the title, not a line that repeats the venue."""
    cells = _untitled_row("2015 Invited Speaker at Northgate University", date="2015",
                          role="Invited Speaker", location="Northgate University")
    assert cells == ["Invited Speaker", "Northgate University", "2015"]


def test_an_untitled_last_split_record_is_titled_from_its_own_line():
    """#1445 (EOAHMI BRUSUZ 265): the last of stage 4's records carries the
    parent's whole line, both talks, and an untitled one was titled with it,
    so the first talk printed twice."""
    from unified_pipeline.stage4.schemas import FIELD_SCHEMAS, STAGE4_RECORDS_KEY
    from unified_pipeline.stage6.fan_out import fan_out_multi_record_entries
    records = [{"title": None, "location": "Ashby, Varnoria", "event_name": "Zqhubble Institute"},
               {"title": None, "location": "Kestrel, Japan", "event_name": "2nd Zqriken Symposium"}]
    entry = {"taxonomy_code": "R", "element_idx_start": 5,
             "text": "Zqhubble Institute (Ashby, Varnoria) 2nd Zqriken Symposium (Kestrel, Japan)",
             "extracted_fields": {**records[-1], STAGE4_RECORDS_KEY: records}}
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document(gen.template_path)
    gen.cv_owner_location = None
    gen._fill_presentations(fan_out_multi_record_entries(
        [entry], FIELD_SCHEMAS, records_key=STAGE4_RECORDS_KEY))
    rows = sorted([c.text for c in row.cells] for table in gen.doc.tables for row in table.rows
                  if any("Zq" in c.text for c in row.cells))
    assert rows == [["2nd Zqriken Symposium", "Kestrel, Japan", ""],
                    ["Zqhubble Institute", "Ashby, Varnoria", ""]]


def test_an_open_ended_talk_renders_its_range_not_its_start_year():
    """#1346 (OTBUCZ 116/119/125): a course given "2006 to present" has
    start_date 2006 and end_date present; the Dates cell showed only 2006."""
    assert _rendered_date(start_date="2006", end_date="present") == "2006-Present"


def test_a_closed_multi_year_range_renders_both_years():
    assert _rendered_date(start_date="1981-01-09", end_date="1983-05-20") == "1981-1983"


def test_a_multi_day_talk_within_one_year_still_renders_one_year():
    assert _rendered_date(start_date="2014-09-27", end_date="2014-10-02") == "2014"


def test_an_end_date_extends_a_year_given_under_date():
    assert _rendered_date(date="2015", end_date="2017") == "2015-2017"


def test_a_string_none_end_date_is_ignored():
    assert _rendered_date(start_date="2006", end_date="None") == "2006"


def test_a_dict_date_does_not_take_a_second_end_date():
    assert _rendered_date(date={"start_date": "2021-12-30", "end_date": "2022-01-02"},
                          end_date="2022-01-02") == "2021-2022"


def test_a_talk_dated_only_under_additional_dates_renders_them():
    """#1245 (X6 VPMMFM 531): `date` null and three dates under the
    off-schema `additional_dates`; the Dates cell was empty."""
    assert _rendered_date(date=None, additional_dates="1999-09-30; 1999-10-14; 2000-01-05") \
        == "1999, 2000"


def test_a_further_date_follows_the_talks_own_date_unless_it_repeats_it():
    assert _rendered_date(date="2018", additional_dates="2018-05; 2020") == "2018, 2020"


def test_a_talk_without_further_dates_keeps_its_own_cell():
    assert _rendered_date(date="2018") == "2018"

