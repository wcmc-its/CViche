"""Wire tests for the two #575 fixes, driven through the real render paths.

1. `_fill_licensure` formats F1 dates through `format_date_for_section`. A
   month-only issue date ("March 2019") must land in the Licensure table as
   "03/2019" — not the fabricated "03/01/2019" the old fallback produced — and
   a full-precision date must keep its day.

2. `_fill_research_support` rebuckets a grant by its own status string via
   `grant_status_rebucket_target`. An M2A grant whose status reads "In review"
   must render under Pending Funding, not Current Research Funding — the same
   symptom #210 fixed for the literal "Under review". The move's note is read
   off the rendered comment rather than off the caller's entry, because M2
   classifies on its own copies of the records and leaves the input alone.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_licensure_render.py -p no:cacheprovider

Self-contained: no DB, no LLM calls. Loads the bundled WCM template like
test_stage6_funding_appendix.py does.
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from docx import Document  # noqa: E402

from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402


_LICENSE_ENTRY = {
    "text": "New York State Medical License 123456, issued March 2019",
    "extracted_fields": {
        "state": "New York",
        "license_number": "123456",
        "issue_date": "March 2019",       # month-only: no day stated
        "expiration_date": "2021-06-30",  # full precision: day must survive
    },
}


def _rendered_license_row(doc):
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text for c in row.cells]
            if len(cells) >= 4 and cells[0] == "New York" and cells[1] == "123456":
                return cells
    return None


def test_licensure_month_only_issue_date_is_not_given_a_day():
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document(gen.template_path)

    gen._fill_licensure([_LICENSE_ENTRY])

    cells = _rendered_license_row(gen.doc)
    assert cells is not None, "license row was not rendered into any table"
    assert cells[2] == "03/2019", f"issue date fabricated a day: {cells[2]!r}"
    assert cells[3] == "06/30/2021", f"stated day was lost: {cells[3]!r}"


_IN_REVIEW_GRANT = {
    "text": "Metacognitive Reflection Training | Role: PI | Status: In review",
    "taxonomy_code": "M2A",
    "extracted_fields": {
        "title": "Metacognitive Reflection Training Study",
        "agency": "SDRME Foundation",
        "pi_role": "Principal Investigator",
        "status": "In review",
        "start_date": "07/2025",
        "end_date": "06/2030",  # future end date: date inference must not move it
    },
}


def _body_index(doc, element):
    return list(doc.element.body).index(element)


def test_in_review_grant_renders_under_pending_funding():
    gen = WCMTemplateGenerator(verbose=False, emit_comments=True)
    gen.doc = Document(gen.template_path)

    entry = {**_IN_REVIEW_GRANT, "extracted_fields": dict(_IN_REVIEW_GRANT["extracted_fields"])}
    submitted = {**entry, "extracted_fields": dict(entry["extracted_fields"])}
    gen._fill_research_support({"M2A": [entry]})

    notes = [c["text"] for c in gen._comments if c["author"] == "Reclassification"]
    assert notes, "status rebucket left no reclassification comment"
    assert any("Pending (M2C)" in note for note in notes), notes
    assert entry == submitted, "rendering wrote back into the caller's own record"

    grant_table = None
    for table in gen.doc.tables:
        if "Metacognitive Reflection Training Study" in table._element.xml:
            grant_table = table
            break
    assert grant_table is not None, "grant table was not rendered"

    # The template orders the headers Current -> Past -> Pending, so rendering
    # after the Pending Funding paragraph proves the grant left Current.
    pending_idx = gen._find_paragraph_with_text("Pending Funding")
    assert pending_idx is not None
    pending_pos = _body_index(gen.doc, gen.doc.paragraphs[pending_idx]._element)
    table_pos = _body_index(gen.doc, grant_table._tbl)
    assert table_pos > pending_pos, (
        "grant with status 'In review' rendered before the Pending Funding "
        "header — it stayed in Current Research Funding"
    )
