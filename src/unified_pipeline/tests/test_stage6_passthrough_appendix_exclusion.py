"""Wire test: a passthrough entry the writer actually wrote must not also
duplicate into the Appendix (#294, covering E and G).

Before this fix, `_fill_employment_status` and `_fill_hospital_affiliation`
selected their input by hierarchy rather than taxonomy code, so their entries
kept whatever taxonomy code stage 3b gave them (typically T) and `generate()`
routed every T-coded entry to the Appendix regardless of whether a
passthrough writer had already placed it -- an accepted entry rendered
TWICE. The fix makes each writer report exactly which entry dicts it
actually wrote (not merely matched), and `generate()` excludes those specific
objects, by identity, from the unmapped-entries pool that feeds the
Appendix. An entry a writer MATCHED but did not write (E: a label that names
no known template row, #571; G: text too short to be treated as a real
affiliation line) is not in that list, so it still reaches the Appendix
exactly as before.

These tests drive the real `generate()` path (not just `_fill_passthrough_sections`
in isolation, as test_stage6_employment_status_routing.py does) against the
real committed WCM template, the same way test_m1_appendix_fallback.py does
for the M1/appendix interaction. Synthetic entries only, no PII.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_passthrough_appendix_exclusion.py -p no:cacheprovider
"""

import json
import sys
from pathlib import Path

from docx import Document

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage6.sections.appendix import REASON_RENDERER_DECLINED  # noqa: E402
from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402

_APPENDIX_HEADER = "T. APPENDIX"


def _full_text(doc) -> str:
    """Every rendered line, paragraphs then table cells -- good enough to ask
    'does this text appear ANYWHERE in the rendered document'."""
    parts = [p.text for p in doc.paragraphs]
    parts += [c.text for tb in doc.tables for row in tb.rows for c in row.cells]
    return "\n".join(parts)


def _appendix_text(doc) -> str:
    """Just the T. APPENDIX content. Appendix entries are written as
    paragraphs only (`_fill_appendix` / `_add_remaining_to_appendix`), never
    into a table cell, so slicing `doc.paragraphs` from the header onward is
    exact -- unlike `_full_text`, it can't be fooled by a table (E/G/J's own
    output) that renders earlier in the document than the appendix paragraph
    but later in a naive paragraphs-then-tables concatenation.
    """
    paragraphs = [p.text for p in doc.paragraphs]
    for i, text in enumerate(paragraphs):
        if text.strip() == _APPENDIX_HEADER:
            return "\n".join(paragraphs[i:])
    return ""


def _render(tmp_path, entries) -> tuple[Document, dict]:
    """Returns the rendered Document and the `<uid>_render_warnings.json`
    sidecar actually written to disk, so a test can check both what the
    document shows and what generate() reported about it (#531)."""
    gen = WCMTemplateGenerator(verbose=False)
    # Neutralize the LLM-driven appendix-reconsider pass: isolates the
    # consumed-entry exclusion under test and keeps the render deterministic
    # and credential-free, same as test_m1_appendix_fallback.py.
    gen._reconsider_appendix_entries = lambda: None
    data = {"document_uid": "TESTEG", "entries": entries}
    input_path = tmp_path / "in.json"
    output_path = tmp_path / "out.docx"
    input_path.write_text(json.dumps(data))
    gen.generate(str(input_path), str(output_path), research_summary_path=None)
    sidecar = json.loads((tmp_path / "TESTEG_render_warnings.json").read_text())
    return Document(str(output_path)), sidecar


def _appendix_diversion_count(sidecar: dict, code: str) -> int:
    """Sum of `count` over this sidecar's `appendix_diversion` warnings for
    *code* -- 0 when the code produced none."""
    return sum(w["count"] for w in sidecar["warnings"]
               if w.get("check") == "appendix_diversion" and w["code"] == code)


_OWNER_ENTRY = {
    "text": "Name: Jane Q. Public, MD", "taxonomy_code": "A",
    "extracted_fields": {}, "element_idx_start": 0,
}


def test_accepted_employment_entry_not_duplicated_refused_entry_stays_in_appendix(tmp_path):
    entries = [
        _OWNER_ENTRY,
        {  # Accepted: "Name of Current Employer(s)" names a known row (#571).
            "text": "Name of Current Employer(s): DISTINCTIVE_E_ACCEPTED_EMPLOYER",
            "taxonomy_code": "T", "hierarchy": ["E. EMPLOYMENT STATUS"],
            "extracted_fields": {}, "element_idx_start": 1,
        },
        {  # Refused: no known row names "Favorite Color" (#571's own class).
            "text": "Favorite Color: DISTINCTIVE_E_REFUSED_LABEL",
            "taxonomy_code": "T", "hierarchy": ["E. EMPLOYMENT STATUS"],
            "extracted_fields": {}, "element_idx_start": 2,
        },
    ]
    doc, sidecar = _render(tmp_path, entries)
    full, appendix = _full_text(doc), _appendix_text(doc)

    assert "DISTINCTIVE_E_ACCEPTED_EMPLOYER" in full, "accepted entry did not render at all"
    assert "DISTINCTIVE_E_ACCEPTED_EMPLOYER" not in appendix, (
        "accepted E entry duplicated into the Appendix -- #294 regression")
    assert "DISTINCTIVE_E_REFUSED_LABEL" in appendix, (
        "refused E entry (label matches no template row) must still reach the Appendix")
    # #531: the appendix_diversion count for T must reflect only the refused
    # entry -- the accepted (passthrough-consumed) one must not inflate it.
    assert _appendix_diversion_count(sidecar, "T") == 1


def test_accepted_affiliation_entry_not_duplicated_refused_entry_stays_in_appendix(tmp_path):
    entries = [
        _OWNER_ENTRY,
        {  # Accepted: long enough to be a real affiliation line (writer's
           # only per-entry admission gate; see module docstring).
            "text": "Member, DISTINCTIVE_G_ACCEPTED_AFFIL Research Institute",
            "taxonomy_code": "T", "hierarchy": ["G. INSTITUTIONAL/HOSPITAL AFFILIATION"],
            "extracted_fields": {}, "element_idx_start": 3,
        },
        {  # Refused: <= 5 chars, below the writer's own admission threshold.
            "text": "QQ1",
            "taxonomy_code": "T", "hierarchy": ["G. INSTITUTIONAL/HOSPITAL AFFILIATION"],
            "extracted_fields": {}, "element_idx_start": 4,
        },
    ]
    doc, sidecar = _render(tmp_path, entries)
    full, appendix = _full_text(doc), _appendix_text(doc)

    assert "DISTINCTIVE_G_ACCEPTED_AFFIL" in full, "accepted entry did not render at all"
    assert "DISTINCTIVE_G_ACCEPTED_AFFIL" not in appendix, (
        "accepted G entry duplicated into the Appendix -- #294 regression")
    assert "QQ1" in appendix, (
        "refused G entry (too short to be routed) must still reach the Appendix")
    # #531: same exclusion, checked through the new per-code count.
    assert _appendix_diversion_count(sidecar, "T") == 1


def test_refused_g_entry_reason_is_renderer_declined_accepted_produces_no_warning(tmp_path):
    """#531-R2 finding F2: an unconsumed G-CODED entry (not just T-coded, as
    the two tests above use) reads as `renderer_declined`, never
    `no_render_route` -- `_fill_passthrough_sections` IS G's renderer and
    declined this entry (too short), it is not that no section routes G at
    all. The accepted G entry produces no appendix_diversion warning.

    Also pins r11 (#531-R2 finding F-R2-3 / #531-R3 task 3): the exact
    passthrough-refusal message text, singular case ("1 entry ... refused
    by..."). `test_refused_g_entries_plural_message` below is the plural
    companion. Mutant r11 (`if False:` disabling the E/G/J-specific message
    branch in `_diversion_message`) falls through to the generic
    `_REASON_TEXT[REASON_RENDERER_DECLINED]` string ("no research summary
    rendered") instead -- FAILING the message assertion here.
    """
    entries = [
        _OWNER_ENTRY,
        {  # Accepted, G-coded this time.
            "text": "Member, DISTINCTIVE_G2_ACCEPTED_AFFIL Research Institute",
            "taxonomy_code": "G", "hierarchy": ["G. INSTITUTIONAL/HOSPITAL AFFILIATION"],
            "extracted_fields": {}, "element_idx_start": 5,
        },
        {  # Refused: <= 5 chars, below the writer's own admission threshold.
            "text": "Q2",
            "taxonomy_code": "G", "hierarchy": ["G. INSTITUTIONAL/HOSPITAL AFFILIATION"],
            "extracted_fields": {}, "element_idx_start": 6,
        },
    ]
    _doc, sidecar = _render(tmp_path, entries)
    diversions = [w for w in sidecar["warnings"]
                  if w.get("check") == "appendix_diversion" and w["code"] == "G"]
    assert [(w["reason"], w["count"]) for w in diversions] == [
        (REASON_RENDERER_DECLINED, 1)]
    assert diversions[0]["message"] == (
        "G: 1 entry diverted to the Appendix — refused by the passthrough "
        "writer for G (source section label did not match)")


def test_refused_g_entries_plural_message(tmp_path):
    """r11 (#531-R3 task 3) plural companion to the singular pin above: two
    refused G entries read as one warning, count 2, "entries"/no verb-
    agreement pronoun issue."""
    entries = [
        _OWNER_ENTRY,
        {  # Refused #1: <= 5 chars, below the writer's own admission threshold.
            "text": "Q2",
            "taxonomy_code": "G", "hierarchy": ["G. INSTITUTIONAL/HOSPITAL AFFILIATION"],
            "extracted_fields": {}, "element_idx_start": 5,
        },
        {  # Refused #2: same reason, a distinct entry.
            "text": "Q3",
            "taxonomy_code": "G", "hierarchy": ["G. INSTITUTIONAL/HOSPITAL AFFILIATION"],
            "extracted_fields": {}, "element_idx_start": 6,
        },
    ]
    _doc, sidecar = _render(tmp_path, entries)
    diversions = [w for w in sidecar["warnings"]
                  if w.get("check") == "appendix_diversion" and w["code"] == "G"]
    assert [(w["reason"], w["count"]) for w in diversions] == [
        (REASON_RENDERER_DECLINED, 2)]
    assert diversions[0]["message"] == (
        "G: 2 entries diverted to the Appendix — refused by the passthrough "
        "writer for G (source section label did not match)")


def test_n1_n2_entries_render_and_do_not_duplicate_into_the_appendix(tmp_path):
    """#529: N1/N2 now dispatch through `RENDER_ROUTED_CODES`, the same
    mechanism N3A/N3B already use -- `generate()`'s unmapped-code sweep
    (`mapped_codes = set(RENDER_ROUTED_CODES)`) must exclude both codes at
    the source, or every entry `_fill_mentoring` already rendered would also
    land a second time in the Appendix.

    #840 gave N2 its own training-grants renderer -- a code that used to be
    Appendix-diverted now renders, so this also pins that neither code
    produces an `appendix_diversion` warning once its writer has claimed it
    (`_render` returns the `(doc, sidecar)` pair since #531)."""
    entries = [
        _OWNER_ENTRY,
        {"text": "DISTINCTIVE_N1_LEADERSHIP_LINE", "taxonomy_code": "N1",
         "extracted_fields": {"role": "DISTINCTIVE_N1_LEADERSHIP_LINE"},
         "element_idx_start": 5},
        {"text": "DISTINCTIVE_N2_GRANT_AGENCY", "taxonomy_code": "N2",
         "extracted_fields": {"agency": "DISTINCTIVE_N2_GRANT_AGENCY"},
         "element_idx_start": 6},
    ]
    doc, sidecar = _render(tmp_path, entries)
    full, appendix = _full_text(doc), _appendix_text(doc)

    assert "DISTINCTIVE_N1_LEADERSHIP_LINE" in full, "N1 entry did not render at all"
    assert "DISTINCTIVE_N1_LEADERSHIP_LINE" not in appendix, (
        "N1 entry duplicated into the Appendix -- #529 regression")
    assert "DISTINCTIVE_N2_GRANT_AGENCY" in full, "N2 entry did not render at all"
    assert "DISTINCTIVE_N2_GRANT_AGENCY" not in appendix, (
        "N2 entry duplicated into the Appendix -- #529 regression")
    assert _appendix_diversion_count(sidecar, "N1") == 0
    assert _appendix_diversion_count(sidecar, "N2") == 0


def _grant_table_cells(doc) -> list[str]:
    """Every value cell of the rendered grant tables (their first label is Award Source)."""
    return [row.cells[1].text for tb in doc.tables
            if tb.rows and tb.rows[0].cells[0].text.startswith("Award Source:")
            for row in tb.rows]


def test_claimed_goals_row_renders_in_its_grant_and_leaves_the_appendix(tmp_path):
    """#958: a T goals row inside a grant's source-element range is the grant's
    goal. `_fill_research_support` reports it, and `generate()` must keep it out
    of the Appendix by identity, like a passthrough-consumed entry. A goals row
    inside no grant's range is not claimed and still reaches the Appendix."""
    entries = [
        _OWNER_ENTRY,
        {"text": "Award Source: | Example Fund\nProject title: | Example Corridor Study",
         "taxonomy_code": "M2B", "element_idx_start": 10, "element_idx_end": 12,
         "extracted_fields": {"agency": "Example Fund", "title": "Example Corridor Study",
                              "start_date": "01/2019", "end_date": "12/2020"}},
        {"text": "The major goals of this project are: | DISTINCTIVE_CLAIMED_GOAL",
         "taxonomy_code": "T", "element_type": "table_row", "recovered_row": True,
         "parent_idx": 12, "element_idx_start": "12.1", "extracted_fields": {}},
        {"text": "The major goals of this project are: | DISTINCTIVE_UNCLAIMED_GOAL",
         "taxonomy_code": "T", "element_type": "table_row", "recovered_row": True,
         "parent_idx": 40, "element_idx_start": "40.1", "extracted_fields": {}},
    ]
    doc, sidecar = _render(tmp_path, entries)
    appendix = _appendix_text(doc)

    assert "DISTINCTIVE_CLAIMED_GOAL" in _grant_table_cells(doc)
    assert "DISTINCTIVE_CLAIMED_GOAL" not in appendix, (
        "claimed goals row duplicated into the Appendix -- #958 regression")
    assert "DISTINCTIVE_UNCLAIMED_GOAL" in appendix
    assert "DISTINCTIVE_UNCLAIMED_GOAL" not in "\n".join(_grant_table_cells(doc))
    assert _appendix_diversion_count(sidecar, "T") == 1
