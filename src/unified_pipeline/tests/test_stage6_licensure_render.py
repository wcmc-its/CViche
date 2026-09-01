"""Wire tests for the two #575 fixes, driven through the real render paths,
plus the licensure half of #658 (bare substring label matching).

1. `_fill_licensure` formats F1 dates through `format_date_for_section`. A
   month-only issue date ("March 2019") must land in the Licensure table as
   "03/2019" — not the fabricated "03/01/2019" the old fallback produced — and
   a full-precision date must keep its day.

2. `_fill_research_support` rebuckets a grant by its own status string via
   `grant_status_rebucket_target`. An M2A grant whose status reads "In review"
   must render under Pending Funding, not Current Research Funding — the same
   symptom #210 fixed for the literal "Under review".

3. `_classify_licensure_entry`'s `license_type` label check, and
   `_fill_dea_npi`'s template-row label check, used bare `'npi' in label` /
   `'dea' in label` substring tests -- so a label containing "dea" or "npi"
   as a run of characters inside an unrelated word (e.g. "Idea Number")
   misclassified. Bounded the same way #573 already bounded the raw-text
   check for "Dean".

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
from unified_pipeline.stage6.sections.licensure import (  # noqa: E402
    KIND_DEA,
    KIND_LICENSE,
    KIND_NPI,
    _classify_licensure_entry,
)


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
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document(gen.template_path)

    entry = {**_IN_REVIEW_GRANT, "extracted_fields": dict(_IN_REVIEW_GRANT["extracted_fields"])}
    gen._fill_research_support({"M2A": [entry]})

    assert entry.get("reclassification_note"), "status rebucket left no note"
    assert "Pending (M2C)" in entry["reclassification_note"]

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


# ---------------------------------------------------------------------------
# 3. #658: bounded label matching in licensure classification


def test_license_type_label_embedded_dea_substring_does_not_misclassify():
    """A `license_type` value with "dea" embedded in an unrelated word must
    not route to the DEA slot. Bare `'dea' in label` matched "dea" inside
    "Idea" the same way the pre-#573 raw-text check matched it inside
    "Dean"."""
    kind = _classify_licensure_entry(
        state="New York", license_number="123456",
        license_type="Idea for renewal", original_text="")
    assert kind == KIND_LICENSE


def test_license_type_label_embedded_npi_substring_does_not_misclassify():
    kind = _classify_licensure_entry(
        state="New York", license_number="123456",
        license_type="Alnpine board license", original_text="")
    assert kind == KIND_LICENSE


def test_license_type_label_still_classifies_a_real_npi():
    """Control: a genuine whole-word "NPI" label still routes to the NPI
    slot -- the fix bounds the match, it does not remove it."""
    kind = _classify_licensure_entry(
        state="", license_number="1234567890",
        license_type="NPI", original_text="")
    assert kind == KIND_NPI


def test_license_type_label_still_classifies_a_real_dea():
    kind = _classify_licensure_entry(
        state="", license_number="AB1234567",
        license_type="DEA registration", original_text="")
    assert kind == KIND_DEA


def _dea_npi_table(gen):
    gen.doc.add_paragraph("Identifiers")
    table = gen.doc.add_table(rows=3, cols=2)
    table.rows[0].cells[0].text = "DEA number: (optional)"
    # A row whose label contains "dea" only as a substring of an unrelated
    # word -- the finder above still locates this table via row 0's own
    # "DEA number" text, but this row must not itself be treated as the
    # DEA row.
    table.rows[1].cells[0].text = "Idea Reference Number"
    table.rows[2].cells[0].text = "NPI number: (optional)"
    return table


def test_fill_dea_npi_does_not_write_into_an_embedded_substring_row():
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document()
    table = _dea_npi_table(gen)

    gen._fill_dea_npi("AB1234567", "1234567890")

    assert table.rows[0].cells[1].text == "AB1234567"
    assert table.rows[1].cells[1].text == "", (
        "the 'Idea Reference Number' row was written to -- 'dea' matched as "
        "a bare substring of 'Idea'"
    )
    assert table.rows[2].cells[1].text == "1234567890"
