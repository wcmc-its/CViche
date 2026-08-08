"""Regression test for issue #466: the Q3 study-section identifier never rendered.

Stage 4 emits the study-section name in `panel_name` on every Q3 entry, but no
code in stage 6 ever read the field -- `grep -c panel_name` over
stage_6_word_template.py returned 0. So every Grant Reviewing row rendered as a
bare agency ("Reviewer | NIH | 2018-2020"), losing the identifier that is the
entire content of the line. 279 corpus Q3 entries across 35 CVs carry one.

The fix appends rather than reorders: agency and panel_name are co-populated on
286 of 380 corpus Q3 entries, so preferring panel_name would evict the agency on
269 of them -- trading one omission for another. `test_agency_is_not_evicted`
below is the guard for that, and it is the test that fails if someone
"simplifies" the fix into a precedence swap.

Text is read by walking w:t nodes, NOT Paragraph.text: python-docx returns only
runs that are direct children of a paragraph, so anything inside a <w:ins>
tracked change is invisible and the read under-reports by 11-19% (#461).

    python3 -m pytest src/unified_pipeline/tests/test_stage6_q3_panel_name.py -p no:cacheprovider
"""

import json
import sys
from pathlib import Path

from docx import Document

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402

W_T = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"

AGENCY = "NIH"
PANEL = "Nutrition Study Section Ad Hoc"


def _all_text(docx_path) -> str:
    """Every w:t in the document, including runs inside tracked changes (#461)."""
    doc = Document(str(docx_path))
    return "\n".join(n.text or "" for n in doc.element.body.iter(W_T))


def _entry(code, fields, idx=5):
    return {"text": f"{code} source line", "taxonomy_code": code,
            "extracted_fields": fields, "element_idx_start": idx}


def _render(tmp_path, entries) -> str:
    gen = WCMTemplateGenerator(verbose=False)
    # Neutralize the LLM-driven appendix-reconsider pass: isolates the routing
    # under test and keeps the render deterministic + credential-free.
    gen._reconsider_appendix_entries = lambda: None
    ip, op = tmp_path / "in.json", tmp_path / "out.docx"
    ip.write_text(json.dumps({"document_uid": "TESTQ3", "entries": entries}))
    gen.generate(str(ip), str(op), research_summary_path=None)
    return _all_text(op)


def test_q3_study_section_identifier_renders(tmp_path):
    """The bug: the panel name reached the document nowhere at all."""
    text = _render(tmp_path, [
        _entry("Q3", {"agency": AGENCY, "panel_name": PANEL,
                      "role": "Reviewer", "start_date": "2018", "end_date": "2020"}),
    ])
    assert PANEL in text, "Q3 study-section identifier was dropped"


def test_agency_is_not_evicted(tmp_path):
    """The fix must ADD the panel name, not replace the agency with it.

    This is the guard against 'simplifying' the fix to a precedence swap
    (`panel_name or agency`), which reads more cleanly and silently drops the
    agency on 269 corpus entries.
    """
    text = _render(tmp_path, [
        _entry("Q3", {"agency": AGENCY, "panel_name": PANEL,
                      "role": "Reviewer", "start_date": "2018", "end_date": "2020"}),
    ])
    assert PANEL in text, "panel name missing"
    assert AGENCY in text, "agency was evicted by the panel name"
    assert f"{AGENCY} - {PANEL}" in text, "agency and panel not combined in one cell"


def test_panel_name_only_is_still_rendered(tmp_path):
    """81 corpus Q3 entries have no agency; the panel must stand alone, with no
    stray separator."""
    text = _render(tmp_path, [
        _entry("Q3", {"panel_name": PANEL, "role": "Reviewer",
                      "start_date": "2018", "end_date": "2020"}),
    ])
    assert PANEL in text
    assert f"- {PANEL}" not in text, "leading separator emitted with no agency"


def test_no_duplication_when_agency_already_contains_panel(tmp_path):
    """Guard on the containment check: don't render 'X - X'."""
    combined = f"{AGENCY} {PANEL}"
    text = _render(tmp_path, [
        _entry("Q3", {"agency": combined, "panel_name": PANEL,
                      "role": "Reviewer", "start_date": "2018", "end_date": "2020"}),
    ])
    assert f"{combined} - {PANEL}" not in text, "panel name duplicated into the cell"


def test_non_q3_codes_are_untouched(tmp_path):
    """The code gate: panel_name is absent from all corpus Q1/Q4A/Q4B/Q4C
    entries, so the render for those codes must not change even if the field
    somehow appears."""
    text = _render(tmp_path, [
        _entry("Q4B", {"journal_name": "Journal of Widgets", "panel_name": PANEL,
                       "role": "Associate Editor", "start_date": "2019", "end_date": "2021"}),
    ])
    assert "Journal of Widgets" in text
    assert PANEL not in text, "panel_name leaked into a non-Q3 row"
