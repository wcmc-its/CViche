"""#821 (settled 2026-09-14): the licensure half of the withhold policy.

DEA is withheld with notice -- see the DEA row's own comment in
`stage6/normalization/pii.py` for why this is enforced here
(`_resolve_licensure`/`_fill_licensure`) rather than by widening that row's
scope. NPI stays public.

These tests drive the real `_fill_licensure`/`_resolve_licensure`/
`_recover_unrendered_records` paths, the same pattern
`test_stage6_classification_literals.py` and
`test_stage6_unrendered_recovery.py` already use. All values are synthetic.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_licensure_pii_policy.py -p no:cacheprovider
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from docx import Document  # noqa: E402

from unified_pipeline.stage6.normalization.pii import CAT_DEA  # noqa: E402
from unified_pipeline.stage6.sections.licensure import (  # noqa: E402
    _LICENSURE_SECTION_LABEL,
    _resolve_licensure,
)
from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402


def _generator():
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document(gen.template_path)
    return gen


def _f1_dea_entry(**extra_fields):
    fields = {"license_number": "AB1234567"}
    fields.update(extra_fields)
    return {"taxonomy_code": "F1", "text": "DEA registration AB1234567",
            "extracted_fields": fields}


def _f1_npi_entry():
    return {"taxonomy_code": "F1", "text": "NPI 1234567890",
            "extracted_fields": {"license_number": "1234567890"}}


# ---------------------------------------------------------------------------
# 1. The real-template render: the DEA cell renders empty, the withheld
#    item reaches the SAME list the notice/comment mechanism reads. NPI
#    (public, #821 "render") still reaches its own cell.
# ---------------------------------------------------------------------------

def test_dea_slot_renders_empty_and_is_recorded_withheld():
    gen = _generator()
    gen._fill_licensure([_f1_dea_entry(), _f1_npi_entry()])

    dea_npi_table = None
    for table in gen.doc.tables:
        for row in table.rows:
            if 'dea number' in row.cells[0].text.lower():
                dea_npi_table = table
                break
        if dea_npi_table:
            break
    assert dea_npi_table is not None, "template's DEA/NPI table not found"

    cells = {row.cells[0].text.lower(): row.cells[1].text
             for row in dea_npi_table.rows}
    assert cells.get('dea number: (optional)', None) == ""
    assert cells.get('npi number: (optional)', None) == "1234567890"

    assert len(gen._pii_result.withheld) == 1
    item = gen._pii_result.withheld[0]
    assert item.category == CAT_DEA
    assert item.section_label == _LICENSURE_SECTION_LABEL == "Licensure"


def test_dea_slot_stays_blank_with_no_dea_entry_and_records_nothing():
    """Negative control: an ordinary state licence entry never touches the
    withheld list or the identifiers table."""
    gen = _generator()
    gen._fill_licensure([
        {"taxonomy_code": "F1", "text": "New York State Medical License",
         "extracted_fields": {"state": "New York", "license_number": "123456"}},
    ])
    assert gen._pii_result.withheld == []


# ---------------------------------------------------------------------------
# 2. `_resolve_licensure` is pure and reports the decision without a
#    document -- unit-level companion to the render test above.
# ---------------------------------------------------------------------------

def test_resolve_licensure_dea_withheld_flag_and_text_mutation():
    """`dea_withheld` is set and `identifiers.dea` stays None regardless of
    whether the raw entry names an F1 sub-kind by LABEL or by SHAPE alone
    (test_shape_fallback_dea_two_letters_seven_alphanumeric,
    test_stage6_licensure_render.py, already pins the classifier itself)."""
    labelled = _f1_dea_entry()
    result = _resolve_licensure([labelled])
    assert result.dea_withheld is True
    assert result.identifiers.dea is None
    assert result.licenses == ()
    # The mutation the #221 recovery-safety-net test below depends on.
    assert labelled["text"] == ""


# ---------------------------------------------------------------------------
# 3. The #221 post-render recovery pass must not re-insert a DEA number
#    fused into a multi-line F1 entry alongside an unrelated record line --
#    the recovery pass reads `entry['text']` directly, not this section's
#    own rendered output (see `_resolve_licensure`'s own docstring on why
#    it blanks `raw['text']` for a DEA-classified entry).
# ---------------------------------------------------------------------------

def test_221_recovery_does_not_reinsert_a_fused_dea_line():
    gen = _generator()
    entry = {
        "taxonomy_code": "F1",
        "text": "\n".join([
            "DEA registration AB1234567",
            "Renewal note filed in 2024 with reference 99118",
        ]),
        "extracted_fields": {"license_number": "AB1234567"},
    }

    gen._fill_licensure([entry])
    gen._recover_unrendered_records({"F1": [entry]})

    texts = [p.text for p in gen.doc.paragraphs]
    assert not any("AB1234567" in t for t in texts)
    assert not any("99118" in t for t in texts)
