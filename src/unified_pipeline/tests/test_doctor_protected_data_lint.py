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

import pytest  # noqa: E402
from docx import Document  # noqa: E402
from docx.oxml import parse_xml  # noqa: E402
from docx.oxml.ns import nsdecls  # noqa: E402

from unified_pipeline.doctor.lints.protected_data import (  # noqa: E402
    _INDEPENDENT_SHAPES,
    _shape_hits,
    lint_protected_data_in_output,
)
from unified_pipeline.doctor.shared import docx_body_blocks  # noqa: E402
from unified_pipeline.quality_score import (  # noqa: E402
    CAP_ONLY_GATES,
    DIMENSIONS,
    PROTECTED_DATA_CAP,
    TOTAL_WEIGHT,
    score_protected_data,
    score_run,
)
from unified_pipeline.run_doctor import read_docx_blocks, run_doctor  # noqa: E402
from unified_pipeline.stage6.normalization.pii import WithheldItem  # noqa: E402
from unified_pipeline.stage6.pii_pass import (  # noqa: E402
    PII_REDACTED_NOTICE,
    withheld_comment_text,
)
from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402


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


def test_a_home_address_label_in_personal_data_is_flagged():
    """#821: home address/phone is a policy row (CAT_HOME_CONTACT). A label
    and its value on the SAME "line" (no `\\n` between them, as free text
    or an Appendix bullet renders it) is caught by the generic scan, same
    as every other category."""
    blocks = [_p("PERSONAL DATA"), _t("Home address: 1 Example St")]
    findings = lint_protected_data_in_output(blocks)
    assert len(findings) == 1
    assert "(home address / phone)" in findings[0]["message"]
    assert "1 Example St" not in findings[0]["message"]


def test_a_home_address_value_on_the_table_rows_own_next_line_is_flagged():
    """#821 regression: the Personal Data TABLE renders a row as
    "<label>:\\n<value>\\n" (`read_docx_blocks` dumps a table row's cells
    `\\n`-joined), and `\\n` is one of `_pii_matches`' own hard fragment
    boundaries -- the generic scan's label span stops right at the label,
    never reaching a value on the very next line, so it alone reported
    zero findings for this shape (a real leak on one farm CV, matched
    nothing under the generic scan by itself). `_home_contact_value_leaked`
    is the dedicated probe that closes it."""
    blocks = [_p("PERSONAL DATA"),
              _t("Home address:\n1 Example St, Springfield, ST 00000\n"
                 "Cell phone:\n555-111-2222")]
    findings = lint_protected_data_in_output(blocks)
    assert len(findings) == 1
    assert "(home address / phone)" in findings[0]["message"]
    assert "1 Example St" not in findings[0]["message"]


def test_an_empty_home_address_row_followed_by_another_label_is_not_flagged():
    """The table-row probe's own negative control: an empty "Home address:"
    row is immediately followed by the next row's bare label ("Cell
    phone:") in the SAME dump shape -- that label has no digit, so it must
    not be misread as the home address's own value."""
    blocks = [_p("PERSONAL DATA"),
              _t("Home address:\nCell phone:\n555-111-2222")]
    assert lint_protected_data_in_output(blocks) == []


def test_an_office_address_is_not_flagged():
    """Negative control paired with the home-address test above -- #821
    left the office address/phone rows unaffected."""
    blocks = [_p("PERSONAL DATA"), _t("Office address: 1 Example St")]
    assert lint_protected_data_in_output(blocks) == []


@pytest.mark.parametrize("section", ["PERSONAL DATA", "T. APPENDIX"])
def test_1426_a_current_address_line_naming_no_workplace_is_flagged(section):
    """#1426 (NDMRSO MQJAVH 8): a verbatim "Current Address" line with a street
    and no workplace word is the owner's home address."""
    findings = lint_protected_data_in_output(
        [_p(section), _p("Current Address\t12 Sample Lane\tExampleton, ZZ 00000")])
    assert len(findings) == 1
    assert "(home address / phone)" in findings[0]["message"]
    assert "Sample Lane" not in findings[0]["message"]


@pytest.mark.parametrize("line", [
    "Current Address: Department of Example, 12 Sample Lane",
    "Current Address: 12 Sample Lane, Suite 400, Exampleton",
    "Current Address: Example University, 12 Sample Lane",
    "Office Address: 12 Sample Lane",
    "Current Address: Exampleton, ZZ",
])
def test_1426_a_current_address_at_a_workplace_or_with_no_street_is_not_flagged(line):
    assert lint_protected_data_in_output([_p("T. APPENDIX"), _p(line)]) == []


def test_a_real_dea_value_reaching_licensure_is_flagged():
    """#821 regression guard: `stage6/sections/licensure.py`'s
    `_fill_dea_npi` never writes a value into this cell any more, so this
    only fires if that changes back. Gated on the block also naming "DEA"
    (see `_DEA_LABEL_PRESENT_RE`'s own comment) so a same-shaped STATE
    licence number in the Licensure section's OTHER table is not a false
    positive -- see the negative control right below."""
    blocks = [_p("LICENSURE"), _t("DEA number: (optional)\nAB1234567")]
    findings = lint_protected_data_in_output(blocks)
    assert len(findings) == 1
    assert "(DEA number)" in findings[0]["message"]
    assert "AB1234567" not in findings[0]["message"]


def test_a_state_licence_number_of_another_shape_is_not_flagged():
    """The Licensure-section probe without a "DEA" label in the block is the
    real DEA format only (two letters, seven digits -- see
    `_DEA_NUMBER_VALUE_RE`), so an ordinary state licence table must not
    false-positive on a licence number of any other shape, nor on a nine-letter
    word (#1217 narrowed this from "any DEA-shaped number": a bare one is now
    a finding, see the tests below)."""
    for row in ("New York\tAB123456\t03/2019\t03/2021",
                "New York\t1234567890\t03/2019\t03/2021",
                "Wisconsin\t12345A, B\t03/2019\t03/2021",
                "New York\tXAB12345678\t03/2019\t03/2021"):
        assert lint_protected_data_in_output([_p("LICENSURE"), _t(row)]) == [], row


def test_a_bare_dea_shaped_number_in_a_licence_row_is_flagged_without_a_label():
    """#1217: the row names no "DEA" -- its first column is a state, or the
    agency spelled out, or the number sits fused in another credential's
    cell -- so the label-gated probe above never saw it."""
    for row in ("New York\tAB1234567\t03/2019\t03/2021",
                "Drug Enforcement Administration\tAB1234567\t2011\tPresent",
                "Example State\t#12345A, 1234567890, #AB1234567\t2005\tPresent"):
        findings = lint_protected_data_in_output([_p("LICENSURE"), _t(row)])
        assert len(findings) == 1, row
        assert "(DEA number)" in findings[0]["message"]
        assert "AB1234567" not in findings[0]["message"]


def test_a_bare_dea_shaped_number_is_flagged_under_the_real_templates_header():
    blocks = [_p("LICENSURE, BOARD CERTIFICATION"),
              _t("Example State\tAB1234567\t2005\tPresent")]
    assert len(lint_protected_data_in_output(blocks)) == 1


def test_a_bare_dea_shaped_number_outside_licensure_is_not_flagged():
    """Scope: the probe is Licensure-only. A grant or compound code of the same
    shape elsewhere is ordinary content."""
    blocks = [_p("HONORS"), _t("Example Award\tAB1234567\t2019")]
    assert lint_protected_data_in_output(blocks) == []


def test_a_real_dea_value_reaching_the_real_templates_licensure_section_is_flagged():
    """#821 R2 F2 regression guard: an exact `section == "licensure"` probe
    never fired on any real render -- the bundled WCM template's own header
    paragraph is "LICENSURE, BOARD CERTIFICATION", which `_output_section_
    header`/`_norm` fold to "licensure, board certification", not the bare
    word the OTHER test above's fabricated `_p("LICENSURE")` fixture uses.
    Drives the real template through `_fill_licensure`, the same pattern
    `test_stage6_licensure_render.py` uses, so a future header-wording
    change breaks THIS test instead of shipping the probe dead again.

    `_fill_licensure` withholds the DEA number (#821), so the value is
    hand-edited back into the slot afterward -- the same regression
    `doctor_one.py`, run over a hand-edited farm docx, proved this probe
    must catch."""
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document(gen.template_path)
    gen._fill_licensure([
        {"taxonomy_code": "F1", "text": "DEA registration AB1234567",
         "extracted_fields": {"license_number": "AB1234567"}},
    ])
    dea_npi_table = None
    for table in gen.doc.tables:
        for row in table.rows:
            if 'dea number' in row.cells[0].text.lower():
                dea_npi_table = table
                break
        if dea_npi_table:
            break
    assert dea_npi_table is not None, "template's DEA/NPI table not found"
    for row in dea_npi_table.rows:
        if 'dea number' in row.cells[0].text.lower():
            row.cells[1].text = "AB1234567"

    findings = lint_protected_data_in_output(docx_body_blocks(gen.doc))
    dea_findings = [f for f in findings if "(DEA number)" in f["message"]]
    # `_table_lines` (doctor/shared.py) emits BOTH the per-cell line and,
    # for any row with more than one non-empty cell, a second "label |
    # value" joined line -- a real leaked value in a two-cell row is always
    # seen twice, the same duplication the ticket's own doctor_one.py probe
    # observed for the pre-existing home-address finding (2 findings for
    # one real leak). Not `>= 1`: pin the exact count so a change to that
    # duplication (or to this probe) is visible here.
    assert len(dea_findings) == 2
    assert all("AB1234567" not in f["message"] for f in dea_findings)


def test_the_real_templates_licensure_section_is_not_flagged_when_dea_is_withheld():
    """Negative control / #821 policy proof on the SAME real template: DEA
    withheld exactly as `_fill_licensure` leaves it (empty slot, no value
    written) fires no finding at all."""
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document(gen.template_path)
    gen._fill_licensure([
        {"taxonomy_code": "F1", "text": "DEA registration AB1234567",
         "extracted_fields": {"license_number": "AB1234567"}},
    ])
    assert lint_protected_data_in_output(docx_body_blocks(gen.doc)) == []


def _licence_number_cell(gen):
    for table in gen.doc.tables:
        for row in table.rows:
            if row.cells[0].text == "Example State":
                return row.cells[1]
    raise AssertionError("rendered licence row not found")


def test_a_dea_number_in_a_rendered_licence_row_is_flagged_and_the_fixed_render_is_clean():
    """#1217, on the real template. The fused-number entry is rendered by
    `_fill_licensure` (which now cuts the DEA token out): that document is
    clean. Hand-editing the token back into the Number cell -- the state of
    the pre-fix documents -- is a finding. Two, as in the test above:
    `_table_lines` emits the per-cell line and the joined row line."""
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document(gen.template_path)
    gen._fill_licensure([
        {"taxonomy_code": "F1",
         "text": "Licensed Physician, Example State | #12345A\n #AB1234567",
         "extracted_fields": {"state_country": "Example State",
                             "license_number": "#12345A, #AB1234567"}},
    ])
    assert lint_protected_data_in_output(docx_body_blocks(gen.doc)) == []

    _licence_number_cell(gen).text = "#12345A, #AB1234567"
    findings = lint_protected_data_in_output(docx_body_blocks(gen.doc))
    assert len(findings) == 2
    assert all("(DEA number)" in f["message"] for f in findings)


def test_quality_score_caps_red_on_a_bare_dea_number_in_a_licence_table(tmp_path):
    """The scorer calls the same scan (#825): the leak the doctor reports
    is the leak the score caps on."""
    doc = Document()
    doc.add_paragraph("LICENSURE, BOARD CERTIFICATION")
    table = doc.add_table(rows=1, cols=4)
    for cell, text in zip(table.rows[0].cells,
                          ("Example State", "AB1234567", "2005", "Present")):
        cell.text = text
    doc.add_paragraph("EDUCATION")
    doc.save(str(tmp_path / f"{_UID}_wcm.docx"))
    fraction, detail, cap = score_protected_data(tmp_path)
    assert cap == PROTECTED_DATA_CAP


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
    """Rendered on EVERY output regardless of source-CV content, in the
    Licensure section. The generic scan skips it by construction (DEA is a
    PERSONAL_AND_APPENDIX row, Licensure is scanned at ALL_CODES); the
    #821 Licensure-specific probe (`_DEA_VALUE_RE`) also skips it, because
    "(optional)" is one character short of the 9-char shape it looks for --
    see `test_a_real_dea_value_reaching_licensure_is_flagged` for the case
    where a real value DOES reach this section. The same label in the
    Appendix IS a finding (that row's own scope reaches the Appendix)."""
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


def test_1071_dob_label_parenthetical_and_race_ethnicity_are_findings():
    """#1071: the doctor scans with the same policy rows, so both label
    shapes are findings in the Appendix; the race/ethnicity row keeps its
    Personal Data / Appendix scope, and a bare "Race:" stays clean."""
    appendix = [_p("T. APPENDIX"), _p("• Birth Date (01/02/1970):"),
                _p("• Date of Birth (mm/dd/yyyy): 01/02/1970"),
                _p("• Race/Ethnicity: Example"), _p("• Race and Ethnicity: Example"),
                _p("• Race: reporting practices in clinical trials")]
    messages = [f["message"] for f in lint_protected_data_in_output(appendix)]
    assert [m.split("(")[1].split(")")[0] for m in messages] == [
        "date of birth", "date of birth", "ethnicity", "ethnicity"]
    body = [_p("HONORS"), _p("Birth Date (01/02/1970):"), _p("Race/Ethnicity: Example")]
    assert [f["message"].split("(")[1].split(")")[0]
            for f in lint_protected_data_in_output(body)] == ["date of birth"]


def test_1223_unlabelled_family_prose_is_a_finding_in_the_appendix_only():
    """#1223: the lint reads the same policy rows, so the labelless family
    shapes the pass now cuts are findings if one ever reaches the Appendix --
    and a title that merely starts the same way is not."""
    appendix = [_p("T. APPENDIX"), _p("Married (Pat), two children (Kim and Lee)"),
                _p("Kim born [withheld]"), _p("2 children (Kim and Lee)"),
                _p("Children - A Review of the Literature"), _p("Married couples (n=40)")]
    messages = [f["message"] for f in lint_protected_data_in_output(appendix)]
    assert [m.split("(")[1].split(")")[0] for m in messages] == [
        "spouse", "date or place of birth", "children / dependents"]
    assert all("Pat" not in m and "Kim" not in m for m in messages)
    body = [_p("HONORS"), _p("Married (Pat), two children (Kim and Lee)")]
    assert lint_protected_data_in_output(body) == []


def test_1223_a_dash_family_label_is_a_finding_in_the_personal_data_block_only_or_under_a_family_label():
    """#1223: the Personal Data block holds no titles, so the dash form of a
    children / spouse label is a finding there; in the Appendix it needs a
    `Family` label ahead of it, and a title is left alone."""
    personal = [_p("PERSONAL DATA"), _t("Spouse- Pat\nChildren - Kim (1971)")]
    assert [f["message"].split("(")[1].split(")")[0]
            for f in lint_protected_data_in_output(personal)] == ["spouse", "children / dependents"]
    under_family = [_p("T. APPENDIX"), _p("Family:\nChildren- Kim (1971)")]
    assert [f["message"].split("(")[1].split(")")[0]
            for f in lint_protected_data_in_output(under_family)] == ["family", "children / dependents"]
    title = [_p("T. APPENDIX"), _p("Children - A Review of the Literature")]
    assert lint_protected_data_in_output(title) == []
    body = [_p("HONORS"), _p("Spouse- Pat")]
    assert lint_protected_data_in_output(body) == []


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
    clean run's raw score (TOTAL_WEIGHT 100 -> 115). It is a cap only."""
    assert score_protected_data not in [scorer for _, _, scorer in DIMENSIONS]
    assert score_protected_data in [gate for _, gate in CAP_ONLY_GATES]
    assert TOTAL_WEIGHT == 100


def test_score_run_caps_a_leaking_docx_red_without_moving_the_raw_score(tmp_path):
    _write_flat_docx(tmp_path, ["Marital Status: Married"])
    leaking = score_run(tmp_path)
    (tmp_path / f"{_UID}_wcm.docx").unlink()
    _write_flat_docx(tmp_path, ["Office address: 123 Main St"])
    clean = score_run(tmp_path)
    assert leaking["raw_score_before_caps"] == clean["raw_score_before_caps"]
    assert leaking["total_weight"] == clean["total_weight"] == 100
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


# --------------------------------------------------------------------------
# #1223 (EBYSBC class E2): the independent check. The scan above calls the
# withhold's own matcher, so it was blind to every shape the withhold missed.
# These fixtures copy the SHAPES of the missed leaks (label, separator,
# spacing) with invented values. Lint-level tests assert COUNTS, which hold
# whether or not `pii.py` later learns a shape (a span the matcher scan
# reports is never reported again by the independent shapes); the category
# each shape names is asserted on `_shape_hits`, which never calls `pii.py`.
# --------------------------------------------------------------------------

def _shape_categories(text: str) -> list[str]:
    return [category for category, _ in _shape_hits(text, [])]


def test_1223_family_rows_recovered_into_the_appendix_are_findings():
    """EQADVR-01's shapes: a "Married:" colon row naming the spouse, and a
    grandchildren row whose label ends in a spaced en dash."""
    married, grandchildren = "Married: Pat Example Lee, Esq.", "Grandchildren \u2013 Ann Beth, Cy, Dee, Eli, Fay"
    assert _shape_categories(married) == ["marital status"]
    assert _shape_categories(grandchildren) == ["family"]
    appendix = [_p("T. APPENDIX"), _p('From "TEACHING":'), _p(married), _p(grandchildren),
                _p(f"[{PII_REDACTED_NOTICE}]")]
    findings = lint_protected_data_in_output(appendix)
    assert len(findings) == 2
    assert all(f["severity"] == "ERROR" for f in findings)
    assert all("Pat" not in f["message"] and "Ann" not in f["message"] for f in findings)


def test_1223_a_home_labelled_number_in_the_office_telephone_cell_is_one_finding():
    """MRJDWE-01's shape: an "(h)" tag after a number in a Personal Data cell.
    The table block repeats the cell in its joined-row line; one leak is one
    finding, and the "(w)" number beside it is not a finding."""
    personal = [_p("PERSONAL DATA"),
                _t("Office address:\n12 Example Road, Sample City, ST 00000\n"
                   "Office address: | 12 Example Road, Sample City, ST 00000\n"
                   "Office telephone:\n555-201-0123 (h); 555-201-0456 (w)\n"
                   "Office telephone: | 555-201-0123 (h); 555-201-0456 (w)\n"
                   "Home address:\nCell phone:\nPersonal email:"),
                _p("EDUCATION")]
    findings = lint_protected_data_in_output(personal)
    assert len(findings) == 1
    assert "(home address / phone)" in findings[0]["message"]
    assert "555" not in findings[0]["message"]
    office_only = [_p("PERSONAL DATA"), _t("Office telephone:\n555-201-0456 (w)"), _p("EDUCATION")]
    assert lint_protected_data_in_output(office_only) == []


def test_1223_home_labels_before_or_after_a_phone_or_street_address():
    for text in ("TELEPHONE: (555) 201-0123 (work) (555) 201-0456 (FAX) (555) 201-0789 (home)",
                 "\u2022 Home Phone:        555-201-0999",
                 "(H) 555.201.0999",
                 "Phone: 555-201-0123 home, 555-201-0456 office",
                 "Residence: 48 Sample Hill Road, Example Town",
                 "12 Example Circle (home)"):
        assert _shape_categories(text) == ["home address / phone"], text


def test_1223_child_counts_names_and_birth_lines_are_shapes():
    """MQSUIC-12's shape (a small head count ending its fragment, invented
    values here), names or dates after a family label, and birth labels
    followed by a date or a place -- at one start, the longer reading wins."""
    assert _shape_categories("Nationality: Sampleland: ; three children") == ["children / dependents"]
    assert _shape_categories("Caring for my 3 sons.") == ["children / dependents"]
    assert _shape_categories("Children- Kim (2/2011), Lee (5/2013)") == ["children / dependents"]
    assert _shape_categories("Daughter (1990)") == ["children / dependents"]
    assert _shape_categories("Wife - Pat Example") == ["spouse"]
    assert _shape_categories("married to Pat Example in 1990") == ["marital status"]
    assert _shape_categories("Great-grandchildren: 4") == ["family"]
    assert _shape_categories("Born: March 3, 1950") == ["date of birth"]
    assert _shape_categories("born in 1950") == ["date of birth"]
    assert _shape_categories("DOB 01/02/1970") == ["date of birth"]
    assert _shape_categories("Place of birth: Sampletown, Ohio") == ["place of birth"]
    assert _shape_categories("Born in Sampletown") == ["place of birth"]
    appendix = [_p("T. APPENDIX"), _p("Nationality: Sampleland: ; three children")]
    assert len(lint_protected_data_in_output(appendix)) == 1


def test_1223_titles_institutions_and_research_text_are_not_findings():
    """The negative controls the shapes were tuned on: none carries a family,
    birth or home VALUE after its keyword."""
    clean = ["1. Example Children\u2019s Hospital, Sample City",
             "Example Children's Clinic, Sample Division",
             "Children - A Review of the Literature",
             "Children - The Forgotten Patients",
             "Introduction to Neurology. New York: Example Press & Sons: Hoboken",
             "Five children with asthma were enrolled in the pilot",
             "A cohort of 40 children.",
             "Second Born Sample Stories. Example Magazine. 2011 Mar.",
             "An example study of very low birth weight infants",
             "2012-2013 - Scholar in Residence, Example High School",
             "Child-Parent Psychotherapy: A Sample Program",
             "Children-Centered Practice in Sample Clinics",
             "Son-in-law: none listed",
             "Office telephone: 555-201-0456 (w)",
             "Home Health Care, Sample County",
             "Married couples (n=40)",
             f"[{PII_REDACTED_NOTICE}]"]
    for text in clean:
        assert _shape_hits(text, []) == [], text
    assert lint_protected_data_in_output([_p("T. APPENDIX")] + [_p(t) for t in clean]) == []


def test_1223_the_independent_shapes_read_only_the_personal_data_block_and_the_appendix():
    body = [_p("HONORS"), _p("Grandchildren \u2013 Ann Beth, Cy"),
            _p("BIBLIOGRAPHY"), _p("Contact: 555-201-0789 (home)")]
    assert lint_protected_data_in_output(body) == []


def test_1223_a_span_the_matcher_scan_reported_is_not_reported_twice():
    """`Husband:` is a label the withhold's matcher knows; the independent
    shape matches the same text and must not add a second finding."""
    assert _shape_categories("Husband: Pat Example") == ["spouse"]
    findings = lint_protected_data_in_output([_p("T. APPENDIX"), _p("Husband: Pat Example")])
    assert len(findings) == 1
    assert "does not recognise" not in findings[0]["message"]


def test_1223_a_tracked_deletion_is_scanned_too():
    """A value struck through as a tracked deletion is still in the file and
    on the page in Word's markup view."""
    blocks = [_p("T. APPENDIX"), _p("Family")]
    deleted = [_p(""), _p("Grandchildren \u2013 Ann Beth, Cy")]
    assert lint_protected_data_in_output(blocks) == []
    findings = lint_protected_data_in_output(blocks, deleted)
    assert len(findings) == 1
    assert "tracked deletion" in findings[0]["message"]
    # the same value both struck through and re-inserted is one leak
    both = [_p("T. APPENDIX"), _p("Grandchildren \u2013 Ann Beth, Cy")]
    assert len(lint_protected_data_in_output(both, deleted)) == 1


def test_1223_stage_4_locates_the_entry_and_never_changes_the_count():
    """The evidence names the source entry index (what the autopsy labels and
    the harness match on); without stage 4 the same findings are unlocated.
    A spouse line under a spaced dash: since #1223 the withhold's matcher
    claims "Married: <name>" itself, and a matcher hit is not located."""
    appendix = [_p("T. APPENDIX"), _p("Spouse \u2013 Pat Example Lee, Esq."),
                _p("Grandchildren \u2013 Ann Beth, Cy, Dee")]
    stage_4 = {"entries": [
        {"element_idx_start": 3, "text": "Born: [withheld]; Sampletown, Ohio"},
        {"element_idx_start": 4, "text": "Spouse -  Pat Example Lee, Esq."},
        {"element_idx_start": 6, "text": "Grandchildren - Ann Beth, Cy, Dee"},
        {"element_idx_start": 9, "text": None}]}
    located = lint_protected_data_in_output(appendix, None, stage_4)
    assert [f["evidence"] for f in located] == [["entry 4"], ["entry 6"]]
    unlocated = lint_protected_data_in_output(appendix)
    assert [f["evidence"] for f in unlocated] == [[], []]
    assert [f["message"] for f in located] == [f["message"] for f in unlocated]


def test_1223_locating_needs_a_distinctive_value_and_tolerates_a_tail_edit():
    """A two-letter value is in too many entries to name one; a long value is
    matched on its head, so a renderer's edit at its tail keeps the entry;
    at most three entries are named."""
    long_rendered = "Grandchildren \u2013 Annabelle Beth, Cyrus Dee, Eliza Fay, Gideon Hart, Ivy Kay"
    long_source = "Grandchildren: Annabelle Beth, Cyrus Dee, Eliza Fay, Gideon Hart, Ivy K."
    stage_4 = {"entries": [{"element_idx_start": 2, "text": "Grandchildren - Bo"},
                           {"element_idx_start": 7, "text": long_source}]}
    short = [_p("T. APPENDIX"), _p("Grandchildren \u2013 Bo")]
    assert [f["evidence"] for f in lint_protected_data_in_output(short, None, stage_4)] == [[]]
    edited = [_p("T. APPENDIX"), _p(long_rendered)]
    assert [f["evidence"] for f in lint_protected_data_in_output(edited, None, stage_4)] == [["entry 7"]]
    many = {"entries": [{"element_idx_start": i, "text": long_source} for i in range(5)]}
    assert [f["evidence"] for f in lint_protected_data_in_output(edited, None, many)] == [
        ["entry 0", "entry 1", "entry 2"]]


def test_1223_a_misaligned_deleted_view_fails_loudly():
    with pytest.raises(ValueError):
        lint_protected_data_in_output([_p("T. APPENDIX"), _p("x")], [_p("")])


def _write_docx_with_deletion(path: Path, kept: list[str], deleted: str) -> None:
    doc = Document()
    for text in kept:
        doc.add_paragraph(text)
    doc.add_paragraph()._p.append(parse_xml(
        f'<w:del {nsdecls("w")} w:id="1" w:author="a" w:date="2026-07-25T00:00:00Z">'
        f'<w:r><w:delText>{deleted}</w:delText></w:r></w:del>'))
    doc.save(str(path))


def test_1223_run_doctor_and_the_scorer_read_the_deleted_view_and_agree(tmp_path):
    """Wiring: run_doctor hands the lint the deleted view and stage 4 (the
    finding is located), and `score_protected_data` counts the same hit."""
    import json
    deleted = "Grandchildren \u2013 Ann Beth, Cy, Dee"
    out_dir = tmp_path / "stage_6_wcm_documents"
    out_dir.mkdir()
    _write_docx_with_deletion(out_dir / f"{_UID}_wcm.docx", ["T. APPENDIX"], deleted)
    stage4 = tmp_path / "stage_4_field_extraction"
    stage4.mkdir()
    (stage4 / f"{_UID}_fields.json").write_text(json.dumps({
        "document_uid": _UID, "cv_owner": {"full_name": "Sam Sample"},
        "entries": [{"element_idx_start": 5, "text": deleted}]}))
    payload = run_doctor(tmp_path, _UID, source=None)
    hits = [f for f in payload["findings"] if f["lint"] == "protected_data_in_output"]
    assert len(hits) == 1
    assert hits[0]["evidence"] == ["entry 5"]

    flat = tmp_path / "flat"
    flat.mkdir()
    _write_docx_with_deletion(flat / f"{_UID}_wcm.docx", ["T. APPENDIX"], deleted)
    fraction, detail, cap = score_protected_data(flat)
    assert cap == PROTECTED_DATA_CAP
    assert "protected_data_hits=1" in detail


# --------------------------------------------------------------------------
# NDMRSO ND1: an institutional or tax ID number recovered into the Appendix
# (ATUVAL element 1, MQJAVH element 6) -- invented values here
# --------------------------------------------------------------------------

_ND1_INSTITUTIONAL, _ND1_TAX = "institutional ID number", "tax ID number"


@pytest.mark.parametrize("text, category", [
    ("UFID #: 1234-5678", _ND1_INSTITUTIONAL),
    ("CWID abc2001", _ND1_INSTITUTIONAL),
    ("EMPLID: 1234567", _ND1_INSTITUTIONAL),
    ("Employee ID Number: abc1234", _ND1_INSTITUTIONAL),
    ("Employee ID No.: 1234567", _ND1_INSTITUTIONAL),
    ("Employee ID: | 1234567", _ND1_INSTITUTIONAL),
    ("Employee ID: E-1234567", _ND1_INSTITUTIONAL),
    ("Employee Identification Number: 1234567", _ND1_INSTITUTIONAL),
    ("Staff ID: 1234567", _ND1_INSTITUTIONAL),
    ("Student No. 1234 5678", _ND1_INSTITUTIONAL),
    ("Faculty ID 1234567", _ND1_INSTITUTIONAL),
    ("Personnel No. 1234567", _ND1_INSTITUTIONAL),
    ("Payroll Number: 1234567", _ND1_INSTITUTIONAL),
    ("Badge ID - 1234567", _ND1_INSTITUTIONAL),
    ("University ID\t12345678", _ND1_INSTITUTIONAL),
    ("University Identification: 12345678", _ND1_INSTITUTIONAL),
    ("Institution ID: 12345678", _ND1_INSTITUTIONAL),
    ("Institutional ID: 12345678", _ND1_INSTITUTIONAL),
    ("Campus ID: 12345678", _ND1_INSTITUTIONAL),
    ("EIN Number\t12-345-6789", _ND1_TAX),
    ("FEIN: 12-3456789", _ND1_TAX),
    ("Federal Tax ID No.: 12-3456789", _ND1_TAX),
    ("Tax Payer ID: 12-3456789", _ND1_TAX),
    ("Taxpayer Identification Number: 12-3456789", _ND1_TAX),
    ("Employer Identification Number - 12-3456789", _ND1_TAX),
    ("TIN 123456789", _ND1_TAX),
    ("ITIN: 912-34-5678", _ND1_TAX),
])
def test_nd1_an_id_number_is_an_independent_shape_and_a_finding(text, category):
    """The shape takes the whole line, label and value, the way the withhold
    does, so the matcher scan's span claims it and one leak is one finding."""
    assert _shape_categories(text) == [category]
    assert [m.group() for pattern, _ in _INDEPENDENT_SHAPES
            for m in pattern.finditer(text)] == [text]
    findings = lint_protected_data_in_output([_p("T. APPENDIX"), _p(text)])
    assert len(findings) == 1
    assert f"({category})" in findings[0]["message"]
    assert "does not recognise" not in findings[0]["message"]
    assert not any(ch.isdigit() for ch in findings[0]["message"])


@pytest.mark.parametrize("text", [
    "NPI: 1234567890",
    "ORCID: 0000-0002-1234-5678",
    "PMID: 12345678",
    "Example Board of Internal Medicine, ID #: 123456",
    "Example Fund at Sample University #1234567",
    "Grant No. R01 CA123456",
    "Sample University No. 3 Hospital",
    "Student Number: 12",
    "Tax ID:",
    "Employee Assistance Program 2019",
    "Student Nov2019",
    "University Idaho1234",
    "UFIDA1234",
    "TINY1234 sensor",
    "Einstein 1234",
    "Latin 12345",
    "Crystal clear or tin ear: a study",
])
def test_nd1_public_identifiers_and_grant_numbers_are_not_id_findings(text):
    assert _shape_hits(text, []) == []
    assert lint_protected_data_in_output([_p("T. APPENDIX"), _p(text)]) == []


def test_nd1_an_id_number_in_a_body_section_is_a_finding_too():
    """The withhold rows are every-code rows, so the matcher scan reports one
    in any section; the independent shapes stay in Personal Data/Appendix."""
    findings = lint_protected_data_in_output([_p("HONORS"), _p("Employee ID: 1234567")])
    assert len(findings) == 1
    assert "(institutional ID number)" in findings[0]["message"]


def test_nd1_a_leaked_tax_id_caps_the_score_red(tmp_path):
    _write_flat_docx(tmp_path, ["EIN Number\t12-345-6789"])
    fraction, _detail, cap = score_protected_data(tmp_path)
    assert cap == PROTECTED_DATA_CAP
    assert fraction == 1.0


# #1223 (NDMRSO, class E2): a bare "Birth" label before a whole date, and a
# spelled-out child count after a marital-status cut, recovered into the
# Appendix. Both scored GREEN while they rendered. Synthetic values.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Birth: March 4, 1970 in Exampleville, Examplestan",   # BNYLDF idx 24's shape
    "BIRTH March 4, 1970: Exampleville",                   # HUOGDE idx 8's, as rendered
    "BIRTH 04/03/1970",
    "• Birth – 4 March 1970",
    "Citizenship: Examplestan\nBirth: 03/04/1970",   # a table block's next line
])
def test_1223_ndmrso_a_bare_birth_label_before_a_whole_date_is_a_shape(text):
    assert _shape_categories(text) == ["date of birth"]


@pytest.mark.parametrize("text", [
    "Birth: A History of Midwifery, 2019",
    "Birth cohort 1990 follow-up study",
    "Birth 2019 Symposium, Example City",
    "Very preterm birth 12 March 2019 outcomes",
    "Outcomes of preterm birth: 03/15/2019 cohort",
    "Birthplace of an Idea, Example Press",
    "Example Children's Oncology Group meeting, 03/04/2015",
])
def test_1223_ndmrso_birth_titles_and_bare_years_are_not_shapes(text):
    assert _shape_hits(text, []) == []


@pytest.mark.parametrize("line", [
    "Birth: March 4, 1970 in Exampleville, Examplestan",
    "BIRTH March 4, 1970: Exampleville",
    "five children",   # a spelled-out count left behind by a marital-status cut
])
def test_1223_ndmrso_the_appendix_leaks_are_findings_and_cap_the_score_red(tmp_path, line):
    appendix = [_p("T. APPENDIX"), _p(line)]
    findings = lint_protected_data_in_output(appendix)
    assert len(findings) == 1
    assert "1970" not in findings[0]["message"] and "Exampleville" not in findings[0]["message"]
    _write_flat_docx(tmp_path, ["T. APPENDIX", line])
    _fraction, _detail, cap = score_protected_data(tmp_path)
    assert cap == PROTECTED_DATA_CAP
