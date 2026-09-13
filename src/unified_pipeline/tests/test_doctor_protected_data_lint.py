"""Tests for `protected_data_in_output` (#820 piece 3): the doctor lint and
quality-score hard-fail gate that scans the RENDERED docx for protected
personal data, as the last line of defence behind pieces 1 (the detector)
and 2 (the pre-render pass).

Every value below is synthetic -- no real name, date, or number from any
corpus CV.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_doctor_protected_data_lint.py -p no:cacheprovider
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from docx import Document  # noqa: E402

from unified_pipeline.doctor.lints.protected_data import (  # noqa: E402
    _TEMPLATE_DEA_SLOT_TEXT,
    lint_protected_data_in_output,
)
from unified_pipeline.quality_score import score_protected_data  # noqa: E402
from unified_pipeline.run_doctor import read_docx_blocks, run_doctor  # noqa: E402


def _p(text: str) -> tuple[str, str]:
    return ("p", text)


def _t(text: str) -> tuple[str, str]:
    return ("table", text)


# --------------------------------------------------------------------------
# positive cases, one per shape class
# --------------------------------------------------------------------------

def test_a_labeled_pii_fragment_in_a_body_paragraph_is_flagged():
    """web057's real shape: a spouse label reaching a numbered Appendix
    line, which renders as an ordinary body paragraph."""
    blocks = [_p("2. Personal Information:: Husband: Pat Example, MD")]
    findings = lint_protected_data_in_output(blocks)
    assert len(findings) == 1
    assert findings[0]["severity"] == "ERROR"
    assert "Husband:" in findings[0]["message"]
    assert "Pat Example" not in findings[0]["message"], "value leaked into the finding"


def test_a_labeled_pii_fragment_in_a_table_cell_is_flagged():
    blocks = [_t("Additional Notes:\nDate of Birth: 01/02/1970")]
    findings = lint_protected_data_in_output(blocks)
    assert len(findings) == 1
    assert "01/02/1970" not in findings[0]["message"]


def test_a_bare_ssn_shaped_value_anywhere_is_flagged_once():
    """Label or not -- the SSN value shape alone is the signal. Reported
    once, not twice, even though `_pii_fragments` also returns it (as a
    labelless fragment) -- see the placeholder filter in the lint."""
    blocks = [_p("Reference number on file: 123-45-6789")]
    findings = lint_protected_data_in_output(blocks)
    assert len(findings) == 1
    assert "SSN-shaped" in findings[0]["message"]


def test_a_bare_date_inside_the_personal_data_block_is_flagged():
    blocks = [
        _p("PERSONAL DATA"),
        _t("Office address:\n01/02/1970\nWork email:"),
        _p("EDUCATION"),
    ]
    findings = lint_protected_data_in_output(blocks)
    assert len(findings) == 1
    assert "Personal Data block" in findings[0]["message"]


def test_a_bare_date_written_as_month_day_year_is_flagged():
    blocks = [_p("PERSONAL DATA"), _t("Born on file: January 2, 1970")]
    findings = lint_protected_data_in_output(blocks)
    assert any("Personal Data block" in f["message"] for f in findings)


# --------------------------------------------------------------------------
# negative controls
# --------------------------------------------------------------------------

def test_a_bare_date_outside_the_personal_data_block_is_not_flagged():
    """The bare-date check is scoped to the Personal Data block on purpose
    -- a date elsewhere (an award year, an employment start date) is
    ordinary CV content."""
    blocks = [
        _p("PERSONAL DATA"),
        _t("Office address: 123 Main St"),
        _p("HONORS"),
        _p("Received an award on 01/02/1970"),
    ]
    findings = lint_protected_data_in_output(blocks)
    assert findings == []


def test_the_templates_own_blank_dea_slot_is_not_flagged():
    """Rendered on EVERY output regardless of source-CV content (#821) --
    confirmed template boilerplate, excluded by exact text match the same
    way `quality_score._TEMPLATE_TAB_CELL_TEXT` excludes the template's own
    incidental tab."""
    blocks = [_t(_TEMPLATE_DEA_SLOT_TEXT)]
    assert lint_protected_data_in_output(blocks) == []


def test_an_isbn_shaped_number_is_not_flagged():
    """Regression: a five-group ISBN-13 whose third group happens to be 4
    digits wide (3-2-4-3-1) matched the old `\\b...\\b`-guarded SSN shape
    regex on its first three groups -- found on a real bibliography
    (web228) during the #820 corpus scan, 24 hits in one document."""
    blocks = [_p("Chapter in: Some Volume. Madrid, 2019. ISBN: 978-2-1234-567-1.")]
    assert lint_protected_data_in_output(blocks) == []


def test_a_phone_number_is_not_an_ssn_shape():
    blocks = [_p("Office telephone: 212-555-1234")]
    assert lint_protected_data_in_output(blocks) == []


def test_clean_document_has_no_findings():
    blocks = [
        _p("PERSONAL DATA"),
        _t("Office address: 123 Main St\nWork email: person@example.edu"),
        _p("EDUCATION"),
        _p("MD, Example University, 2001"),
    ]
    assert lint_protected_data_in_output(blocks) == []


def test_every_finding_is_error_severity():
    blocks = [
        _p("Married to Pat Example"),
        _p("Reference: 123-45-6789"),
        _p("PERSONAL DATA"),
        _t("01/02/1970"),
    ]
    findings = lint_protected_data_in_output(blocks)
    assert findings, "expected at least one finding to check severity on"
    assert all(f["severity"] == "ERROR" for f in findings)


# --------------------------------------------------------------------------
# end-to-end: a real (minimal) docx, through read_docx_blocks and run_doctor
# --------------------------------------------------------------------------

_UID = "TESTPD"


def _write_docx(tmp_path: Path, paragraphs: list[str]) -> Path:
    """A minimal docx for `read_docx_blocks`/`run_doctor`, under the nested
    `stage_6_wcm_documents/` layout `run_doctor._find_artifact` expects."""
    doc = Document()
    for text in paragraphs:
        doc.add_paragraph(text)
    out_dir = tmp_path / "stage_6_wcm_documents"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"{_UID}_wcm.docx"
    doc.save(str(out))
    return out


def _write_flat_docx(tmp_path: Path, paragraphs: list[str]) -> Path:
    """A minimal docx directly under `tmp_path`, the FLAT layout
    `quality_score._load_docx` globs (`*.docx` in `outputs_dir` itself) --
    a deliberately different convention from `_write_docx` above."""
    doc = Document()
    for text in paragraphs:
        doc.add_paragraph(text)
    out = tmp_path / f"{_UID}_wcm.docx"
    doc.save(str(out))
    return out


def test_end_to_end_through_read_docx_blocks(tmp_path):
    out = _write_docx(tmp_path, ["Married to Pat Example"])
    blocks = read_docx_blocks(str(out))
    findings = lint_protected_data_in_output(blocks)
    assert len(findings) == 1
    assert findings[0]["severity"] == "ERROR"


def test_run_doctor_reports_the_lint_and_caps_nothing_it_does_not_own(tmp_path):
    """`run_doctor` dispatches the lint via LINT_REGISTRY (blocks-only
    input) and reports its finding; it does not itself compute the
    quality-score cap -- that is `score_protected_data`'s job, checked
    separately below (#825: the two must not diverge, not that one calls
    the other)."""
    out = _write_docx(tmp_path, ["Reference number on file: 123-45-6789"])
    payload = run_doctor(tmp_path, "TESTPD", source=None)
    hits = [f for f in payload["findings"] if f["lint"] == "protected_data_in_output"]
    assert len(hits) == 1
    assert hits[0]["severity"] == "ERROR"
    assert payload["worst_severity"] == "ERROR"


def test_quality_score_caps_red_on_a_leaking_docx(tmp_path):
    _write_flat_docx(tmp_path, ["Married to Pat Example"])
    fraction, detail, cap = score_protected_data(tmp_path)
    assert cap == 25
    assert fraction == 1.0


def test_quality_score_does_not_cap_a_clean_docx(tmp_path):
    _write_flat_docx(tmp_path, ["Office address: 123 Main St"])
    fraction, detail, cap = score_protected_data(tmp_path)
    assert cap is None
    assert fraction == 0.0
