"""Guard: the doctor's docx readers must see tracked-change INSERTIONS (#249).

Stage 6 inserts LLM-enriched content (research summaries, reformatted citations)
as tracked changes (``<w:ins>``). python-docx's ``.text`` skips those runs, so
the old readers under-reported what rendered and false-flagged content as
'unrendered' (classified_unrendered, unrendered_records, dead_sections). The
readers now read every ``w:t`` under an element — including inside ``<w:ins>``,
excluding deleted ``<w:delText>`` — i.e. the accepted-changes view a reader sees.

    python3 -m pytest src/unified_pipeline/tests/test_run_doctor_trackchanges.py -p no:cacheprovider

Self-contained: no DB, no template. Requires only python-docx.
"""

import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls

from unified_pipeline.run_doctor import _docx_text, read_docx_blocks, run_doctor


def test_docx_text_includes_insertions_excludes_deletions():
    p = parse_xml(
        f'<w:p {nsdecls("w")}>'
        '<w:r><w:t>plain </w:t></w:r>'
        '<w:ins><w:r><w:t>inserted</w:t></w:r></w:ins>'
        '<w:del><w:r><w:delText> deleted</w:delText></w:r></w:del>'
        '</w:p>'
    )
    # inserted text is present (was invisible before); deleted text is not
    assert _docx_text(p) == "plain inserted"


def test_docx_text_default_view_drops_tabs_and_breaks():
    """The lints' view is text only; whitespace elements are opt-in (#461)."""
    p = parse_xml(
        f'<w:p {nsdecls("w")}><w:r><w:t>a</w:t><w:tab/><w:br/><w:t>b</w:t></w:r></w:p>'
    )
    assert _docx_text(p) == "ab"


def test_docx_text_with_whitespace_renders_tab_break_and_cr():
    p = parse_xml(
        f'<w:p {nsdecls("w")}><w:r><w:t>a</w:t><w:tab/><w:t>b</w:t><w:br/>'
        '<w:t>c</w:t><w:cr/><w:t>d</w:t></w:r></w:p>'
    )
    assert _docx_text(p, with_whitespace=True) == "a\tb\nc\nd"


def test_docx_text_with_whitespace_skips_tab_stops_deletions_and_page_breaks():
    p = parse_xml(
        f'<w:p {nsdecls("w")}><w:pPr><w:tabs><w:tab w:val="left" w:pos="720"/></w:tabs></w:pPr>'
        '<w:r><w:t>a</w:t><w:br w:type="page"/></w:r>'
        '<w:del><w:r><w:tab/><w:br/><w:delText>x</w:delText></w:r></w:del>'
        '<w:ins><w:r><w:tab/><w:t>b</w:t></w:r></w:ins></w:p>'
    )
    assert _docx_text(p, with_whitespace=True) == "a\tb"


def test_docx_text_empty_paragraph():
    p = parse_xml(f'<w:p {nsdecls("w")}></w:p>')
    assert _docx_text(p) == ""


def _tracked_changes_run(root, uid, cv_owner):
    """Minimal run: a populated stage-4 artifact plus a stage-6 docx whose whole
    body is a tracked INSERTION."""
    stage4 = root / "stage_4_field_extraction"
    stage4.mkdir(parents=True)
    (stage4 / f"{uid}_fields.json").write_text(json.dumps(
        {"document_uid": uid, "cv_owner": cv_owner, "entries": []}))

    doc = Document()
    doc.add_paragraph()._p.append(parse_xml(
        f'<w:ins {nsdecls("w")} w:id="1" w:author="a" w:date="2026-07-25T00:00:00Z">'
        '<w:r><w:t>Miriam Shapiro, M.D. — Curriculum Vitae</w:t></w:r></w:ins>'))
    out = root / "stage_6_wcm_documents"
    out.mkdir(parents=True)
    doc.save(out / f"{uid}_wcm.docx")
    return out / f"{uid}_wcm.docx"


def test_hard_fail_gates_do_not_fire_on_tracked_changes(tmp_path):
    """The #437 hard-fail lints decide from the stage-4 JSON, never from the
    rendered docx, so a document delivered entirely as tracked insertions cannot
    make them report an undeliverable run -- the false-positive shape this file
    exists to pin."""
    root = tmp_path / "outputs"
    docx = _tracked_changes_run(root, "TRACKED", {
        "first_name": "Miriam", "last_name": "Shapiro",
        "full_name": "Miriam Shapiro"})
    # not vacuous: the docx really does carry its text inside <w:ins>
    assert any("Miriam Shapiro" in text for _, text in read_docx_blocks(str(docx)))

    payload = run_doctor(root, "TRACKED")
    gate_lints = ("owner_contact_missing", "pipeline_errors_present")
    assert not [f for f in payload["findings"] if f["lint"] in gate_lints], \
        "tracked changes must not manufacture a hard-fail finding"
    assert payload["counts"]["ERROR"] == 0

    # ...and the lint is not merely inert on this fixture: the same
    # tracked-changes document with an emptied cv_owner still reports ERROR.
    empty = tmp_path / "empty-owner"
    _tracked_changes_run(empty, "TRACKED", {"first_name": "", "last_name": "",
                                            "full_name": ""})
    broken = run_doctor(empty, "TRACKED")
    assert broken["worst_severity"] == "ERROR"
    assert [f["lint"] for f in broken["findings"] if f["severity"] == "ERROR"] \
        == ["owner_contact_missing"]


if __name__ == "__main__":
    test_docx_text_includes_insertions_excludes_deletions()
    test_docx_text_default_view_drops_tabs_and_breaks()
    test_docx_text_with_whitespace_renders_tab_break_and_cr()
    test_docx_text_with_whitespace_skips_tab_stops_deletions_and_page_breaks()
    test_docx_text_empty_paragraph()
    print("OK")
