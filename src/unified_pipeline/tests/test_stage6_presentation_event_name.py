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
