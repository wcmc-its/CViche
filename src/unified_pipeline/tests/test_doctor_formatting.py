"""teaching_postcheck (doctor/lints/formatting.py, EBYSBC E20).

Synthetic fixtures only: invented titles, places and years.

    python3 -m pytest src/unified_pipeline/tests/test_doctor_formatting.py -q -p no:cacheprovider
"""
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.doctor.lints.formatting import (
    lint_teaching_postcheck,  # noqa: E402
)


def _k(idx, text, fields, formatted, code="K4", hierarchy=("TEACHING",)):
    """One stage-5d teaching entry that stage 5c formatted."""
    return {"element_idx_start": idx, "taxonomy_code": code, "text": text,
            "hierarchy": list(hierarchy),
            "extracted_fields": {**fields, "formatted_text": formatted,
                                 "formatting_source": "stage_5c_llm"}}


def _run(*entries, meta=None):
    artifact = {"entries": list(entries)}
    if meta is not None:
        artifact["stage_5c"] = meta
    return lint_teaching_postcheck(artifact)


def _reasons(finding):
    return finding["message"].split(": ", 1)[1].split(" -- ")[0]


def test_a_dropped_year_warns():
    entry = _k(7, "2001 Example Workshop, Sample Hall",
               {"activity_title": "Example Workshop", "date": "2001"},
               "Example Workshop (Sample Hall)")
    [finding] = _run(entry)
    assert finding["severity"] == "WARN"
    assert finding["message"].startswith("entry 7 (K4): ")
    assert _reasons(finding) == "year_missing:2001"


def test_a_date_apart_from_its_title_warns():
    text = '8/2008 "Alpha Talk"; 9/2008 "Beta Talk", Sample College'
    fields = {"activities": [{"activity_title": "Alpha Talk", "date": "2008-08"},
                             {"activity_title": "Beta Talk", "date": "2008-09"}]}
    entry = _k(9, text, fields, '8/2008, 9/2008 - "Alpha Talk"; "Beta Talk"')
    [finding] = _run(entry)
    assert (finding["severity"], _reasons(finding)) == ("WARN", "date_not_beside_title")


def test_an_echoed_original_line_warns():
    entry = _k(3, "2015 Example Course, Sample Hall",
               {"activity_title": "Example Course", "date": "2015"},
               "**2015** - Example Course\n  - Original: 2015 Example Course, Sample Hall")
    [finding] = _run(entry)
    assert (finding["severity"], _reasons(finding)) == ("WARN", "original_echo")


def test_a_role_only_reason_is_info():
    entry = _k(4, "2012 Example Seminar, Sample Hall",
               {"activity_title": "Example Seminar", "date": "2012"},
               "**2012** - Attendee, Example Seminar (Sample Hall)")
    [finding] = _run(entry)
    assert (finding["severity"], _reasons(finding)) == ("INFO", "role_invented:attendee")


def test_a_role_named_by_the_section_heading_is_not_invented():
    entry = _k(4, "2012 Example Seminar, Sample Hall",
               {"activity_title": "Example Seminar", "date": "2012"},
               "**2012** - Attendee, Example Seminar (Sample Hall)",
               hierarchy=("TEACHING", "Courses Attended"))
    assert _run(entry) == []


def test_a_role_and_a_dropped_year_together_warn():
    entry = _k(5, "2012 Example Seminar, Sample Hall",
               {"activity_title": "Example Seminar", "date": "2012"},
               "Attendee, Example Seminar (Sample Hall)")
    [finding] = _run(entry)
    assert finding["severity"] == "WARN"
    assert _reasons(finding) == "year_missing:2012"


def test_an_id_tag_left_in_the_line_warns():
    entry = _k(6, "2019 Example Clinic Rounds",
               {"activity_title": "Example Clinic Rounds", "date": "2019"},
               "[EC-0021] **2019** - Example Clinic Rounds")
    [finding] = _run(entry)
    assert (finding["severity"], _reasons(finding)) == ("WARN", "id_tag")


def _two_records(formatted):
    records = [{"teaching_role": "Example Case Leader", "start_date": "1975", "end_date": "2000"},
               {"teaching_role": "Example Case Leader", "start_date": "2005", "end_date": "2012"}]
    return _k(32, "Example Case Leader 1975-2000; 2005-2012",
              {**records[1], "stage4_records": records}, formatted, code="K2")


def test_a_record_left_out_of_a_multi_record_entry_warns():
    [finding] = _run(_two_records("**2005-2012** - Example Case Leader"))
    assert (finding["severity"], _reasons(finding)) == ("WARN", "record_unformatted:1 of 2")


def test_a_multi_record_line_that_shows_every_record_is_clean():
    assert _run(_two_records("**2005-2012** - Example Case Leader (also 1975-2000)")) == []


def test_one_line_per_record_is_checked_line_by_line():
    entry = _two_records("**1975-2000** - Example Case Leader\n**2012** - Example Case Leader")
    [finding] = _run(entry)
    assert _reasons(finding) == "year_missing:2005"


def test_a_faithful_line_and_an_unformatted_entry_are_clean():
    faithful = _k(8, "2014 Example Workshop, Sample Hall",
                  {"activity_title": "Example Workshop", "date": "2014"},
                  "**2014** - Example Workshop (Sample Hall)")
    unformatted = {"element_idx_start": 9, "taxonomy_code": "K4", "text": "2010 Example",
                   "extracted_fields": {"activity_title": "Example", "date": "2010",
                                        "formatted_text": "Example"}}
    other_code = _k(10, "2010 Example", {"title": "Example", "date": "2010"}, "Example", code="S1")
    assert _run(faithful, unformatted, other_code) == []


def test_a_line_5c_rejected_is_reported_at_info():
    meta = {"entries_rejected": [{"element_idx_start": 12, "taxonomy_code": "K1",
                                  "reason": "EC-0003:year_missing:2004"}]}
    [finding] = _run(meta=meta)
    assert finding["severity"] == "INFO"
    assert finding["message"].startswith("entry 12 (K1): stage 5c rejected")
    assert "EC-0003:year_missing:2004" in finding["message"]


def _two_titled_records(formatted, dates=("2001", "2003"), text="2001 Alpha Seminar; 2003 Beta Workshop"):
    records = [{"activity_title": "Alpha Seminar", "date": dates[0]},
               {"activity_title": "Beta Workshop", "date": dates[1]}]
    return _k(40, text, {**records[1], "stage4_records": records}, formatted)


def test_a_record_whose_title_is_left_out_warns_though_every_year_shows():
    [finding] = _run(_two_titled_records("**2001, 2003** - Alpha Seminar"))
    assert (finding["severity"], _reasons(finding)) == ("WARN", "record_unformatted:1 of 2")


def test_a_record_with_half_its_title_words_shown_is_formatted():
    assert _run(_two_titled_records("**2001, 2003** - Alpha Seminar; Beta")) == []


def test_a_record_year_the_source_does_not_state_is_not_required():
    entry = _two_titled_records("Alpha Seminar; Beta Workshop", dates=("2001", "2003"),
                                text="Alpha Seminar; Beta Workshop")
    assert _run(entry) == []


def test_indented_sub_bullets_are_not_record_lines():
    entry = _two_records("**1975-2000** - Example Case Leader\n  - Sample Hall\n"
                         "**2012** - Example Case Leader")
    [finding] = _run(entry)
    assert _reasons(finding) == "year_missing:2005"


def test_an_empty_formatted_text_is_not_read():
    entry = _k(11, "2014 Example Workshop", {"activity_title": "Example Workshop", "date": "2014"}, "")
    assert _run(entry) == []
