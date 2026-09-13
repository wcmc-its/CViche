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
    lint_protected_data_in_output,
)
from unified_pipeline.quality_score import (  # noqa: E402
    CAP_ONLY_GATES,
    DIMENSIONS,
    PROTECTED_DATA_CAP,
    TOTAL_WEIGHT,
    score_protected_data,
    score_run,
)
from unified_pipeline.run_doctor import read_docx_blocks, run_doctor  # noqa: E402
from unified_pipeline.stage6.pii_pass import PII_REDACTED_NOTICE, withheld_comment_text  # noqa: E402
from unified_pipeline.stage6.normalization.pii import WithheldItem  # noqa: E402


def _p(text: str) -> tuple[str, str]:
    return ("p", text)


def _t(text: str) -> tuple[str, str]:
    return ("table", text)


# --------------------------------------------------------------------------
# positive cases, one per shape class
# --------------------------------------------------------------------------

def test_a_labeled_pii_fragment_in_a_body_paragraph_is_flagged():
    """A label reaching an ordinary body paragraph. The finding names the
    POLICY CATEGORY ("spouse"), not the matched text: the same vocabulary
    the notice and the Word comment use, and nothing of the value."""
    blocks = [_p("Husband: Pat Example, MD")]
    findings = lint_protected_data_in_output(blocks)
    assert len(findings) == 1
    assert findings[0]["severity"] == "ERROR"
    assert "(spouse)" in findings[0]["message"]
    assert "Pat Example" not in findings[0]["message"], "value leaked into the finding"
    assert "Husband" not in findings[0]["message"]


def test_a_colonless_fragment_never_puts_part_of_its_value_in_the_finding():
    """Round-2 regression: a label-text excerpt of "Born January 2, 1970"
    was "Born January" -- the birth month in a Teams card."""
    findings = lint_protected_data_in_output([_p("T. APPENDIX"), _p("• Born January 2, 1970")])
    assert len(findings) == 1
    assert "January" not in findings[0]["message"]
    assert "(date of birth)" in findings[0]["message"]


def test_a_labeled_pii_fragment_in_a_table_cell_is_flagged():
    blocks = [_t("Additional Notes:\nDate of Birth: 01/02/1970")]
    findings = lint_protected_data_in_output(blocks)
    assert len(findings) == 1
    assert "01/02/1970" not in findings[0]["message"]


def test_a_bare_ssn_shaped_value_anywhere_is_flagged_once():
    """Label or not -- the SSN value shape alone is the signal (an ALL_CODES
    policy row, so any section). Reported once."""
    blocks = [_p("Reference number on file: 123-45-6789")]
    findings = lint_protected_data_in_output(blocks)
    assert len(findings) == 1
    assert "(social security number)" in findings[0]["message"]
    assert "123-45-6789" not in findings[0]["message"]


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
    """Rendered on EVERY output regardless of source-CV content (#821), in
    the Licensure section -- out of the DEA row's scope by construction
    (PERSONAL_AND_APPENDIX), so no text-match exclusion is needed. The same
    label in the Appendix IS a finding."""
    blocks = [_p("LICENSURE"), _t("DEA number: (optional)")]
    assert lint_protected_data_in_output(blocks) == []
    blocks = [_t("DEA number: (optional)")]
    assert lint_protected_data_in_output(blocks) == []
    blocks = [_p("T. APPENDIX"), _p("• DEA number: AB1234567")]
    assert len(lint_protected_data_in_output(blocks)) == 1


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
    out = _write_docx(tmp_path, ["Marital Status: Married"])
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
    _write_flat_docx(tmp_path, ["Marital Status: Married"])
    fraction, detail, cap = score_protected_data(tmp_path)
    assert cap == 25
    assert fraction == 1.0


def test_quality_score_does_not_cap_a_clean_docx(tmp_path):
    _write_flat_docx(tmp_path, ["Office address: 123 Main St"])
    fraction, detail, cap = score_protected_data(tmp_path)
    assert cap is None
    assert fraction == 0.0


# --------------------------------------------------------------------------
# #820 round 2: scope by section, the notice/comment exemption, and the
# cap-only score gate
# --------------------------------------------------------------------------

def test_ambiguous_label_is_a_finding_in_the_appendix_but_not_in_a_content_section():
    """The lint's scope mirrors the pass: "Children:" is protected data in
    the Appendix (and the Personal Data block) and a book-chapter title in
    Book Chapters."""
    body = [
        _p("BIBLIOGRAPHY"),
        _p("Children: Research, Practice and Policy. Example Press, 2001."),
    ]
    assert lint_protected_data_in_output(body) == []
    appendix = [_p("T. APPENDIX"), _p("• Children: Ann, Bob")]
    findings = lint_protected_data_in_output(appendix)
    assert len(findings) == 1
    assert "Appendix" in findings[0]["message"]
    personal = [_p("PERSONAL DATA"), _t("Gender: Female"), _p("EDUCATION")]
    assert len(lint_protected_data_in_output(personal)) == 1


def test_unambiguous_label_is_a_finding_in_any_section():
    body = [_p("HONORS"), _p("Award; Date of Birth: 01/02/1970")]
    findings = lint_protected_data_in_output(body)
    assert len(findings) == 1
    assert "01/02/1970" not in findings[0]["message"]


def test_the_withheld_notice_paragraph_is_never_a_finding():
    """The notice names categories ("marital status", "family members'
    names"); the lint must skip that paragraph outright, so a category
    name coinciding with a label pattern can never turn the notice into
    a leak finding."""
    blocks = [_p("T. APPENDIX"), _p(f"• {PII_REDACTED_NOTICE}")]
    assert lint_protected_data_in_output(blocks) == []
    # and the notice paragraph stays exempt even when a label-shaped
    # category sits in it after a separator (which WOULD match elsewhere)
    blocks = [_p(f"• {PII_REDACTED_NOTICE}; Marital Status: withheld")]
    assert lint_protected_data_in_output(blocks) == []
    assert len(lint_protected_data_in_output([_p("x; Marital Status: withheld")])) == 1


def test_the_word_comment_text_is_clean_and_lives_outside_the_body(tmp_path):
    """The comment lists categories only; scanned directly it yields no
    finding, and `read_docx_blocks` never sees the comments part at all."""
    text = withheld_comment_text([
        WithheldItem("date of birth", "Personal Data", 0),
        WithheldItem("marital status", "Appendix", 1),
        WithheldItem("social security number", "Appendix", 2),
    ])
    assert lint_protected_data_in_output([_p("T. APPENDIX")] + [_p(line) for line in text.split("\n")]) == []
    out = _write_docx(tmp_path, [f"• {PII_REDACTED_NOTICE}"])
    blocks = read_docx_blocks(str(out))
    assert all("Withheld by" not in t for _, t in blocks)
    assert lint_protected_data_in_output(blocks) == []


def test_protected_data_is_a_cap_only_gate_not_a_dimension():
    """Round-2 finding: the gate was a weight-15 dimension, inflating every
    clean run's raw score (TOTAL_WEIGHT 95 -> 110). It is a cap only."""
    assert score_protected_data not in [scorer for _, _, scorer in DIMENSIONS]
    assert score_protected_data in [gate for _, gate in CAP_ONLY_GATES]
    assert TOTAL_WEIGHT == 95


def test_score_run_caps_a_leaking_docx_red_without_moving_the_raw_score(tmp_path):
    _write_flat_docx(tmp_path, ["Marital Status: Married"])
    leaking = score_run(tmp_path)
    (tmp_path / f"{_UID}_wcm.docx").unlink()
    _write_flat_docx(tmp_path, ["Office address: 123 Main St"])
    clean = score_run(tmp_path)
    assert leaking["raw_score_before_caps"] == clean["raw_score_before_caps"]
    assert leaking["total_weight"] == clean["total_weight"] == 95
    assert len(leaking["dimensionScores"]) == len(clean["dimensionScores"])
    assert PROTECTED_DATA_CAP in leaking["hard_fail_caps_applied"]
    assert leaking["totalScore"] <= PROTECTED_DATA_CAP
    assert leaking["band"].startswith("RED")
    assert any("Protected personal data" in f for f in leaking["flags"])
    assert not any("Protected personal data" in f for f in clean["flags"])


def test_score_protected_data_reads_the_same_blocks_as_the_doctor(tmp_path):
    """#825: the scorer calls the lint on the same body-order blocks, so a
    table-cell leak the doctor reports is the leak the scorer caps on."""
    doc = Document()
    doc.add_paragraph("PERSONAL DATA")
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Office address:"
    table.rows[0].cells[1].text = "01/02/1970"
    doc.add_paragraph("EDUCATION")
    doc.save(str(tmp_path / f"{_UID}_wcm.docx"))
    fraction, detail, cap = score_protected_data(tmp_path)
    assert cap == PROTECTED_DATA_CAP
    doctor_hits = len(lint_protected_data_in_output(
        read_docx_blocks(str(tmp_path / f"{_UID}_wcm.docx"))))
    assert doctor_hits >= 1
    assert f"protected_data_hits={doctor_hits}" in detail
