"""Regression guards for the stage-6 funding/appendix fixes (#209, #210, #213).

Grounded in run 89HQVQ: a source CV packed 8 grants into one table cell; only
the first was field-extracted, the other 7 were dumped in the Appendix as
"• [M2A] ..." bullets, and every grant (including "Under review" and "Not
Funded" ones) was bucketed as Current Research Funding.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_funding_appendix.py -p no:cacheprovider

Self-contained: no DB, no LLM calls. The appendix test loads the bundled WCM
template like test_stage6_strip_instruction_box.py does.
"""

import json
import sys
from pathlib import Path

# Ensure the repo's ``src`` directory is importable regardless of cwd/rootdir.
_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from docx import Document  # noqa: E402

from docx.oxml.ns import qn  # noqa: E402

from unified_pipeline.core.template_boilerplate import is_source_boilerplate  # noqa: E402
from unified_pipeline.stage_6_word_template import (  # noqa: E402
    WCMTemplateGenerator,
    grant_status_rebucket_target,
    segment_already_rendered,
)


def _num_level(para):
    """The paragraph's w:ilvl, or None when it carries no list markup.

    Mirrors test_stage6_k_list_bullets.py's helper of the same name -- the
    appendix segments converted to real Word list paragraphs by #483.
    """
    pPr = para._p.pPr
    numPr = pPr.find(qn("w:numPr")) if pPr is not None else None
    if numPr is None:
        return None
    return numPr.find(qn("w:ilvl")).get(qn("w:val"))


# ---------------------------------------------------------------- #210

def test_status_awarded_stays_put():
    target, note = grant_status_rebucket_target("Awarded 2026")
    assert target is None and note is None


def test_status_under_review_goes_pending():
    target, note = grant_status_rebucket_target("Submitted 2026, Under review")
    assert target == "M2C"
    assert "Pending" in note


def test_status_not_funded_goes_pending_with_review_note():
    # Ordering matters: this status also contains "Submitted".
    target, note = grant_status_rebucket_target("Submitted 2025-2026, Not Funded")
    assert target == "M2C"
    assert "confirm" in note.lower()


def test_status_completed_goes_past():
    target, _ = grant_status_rebucket_target("Completed 2023")
    assert target == "M2B"


def test_status_empty_or_none_is_noop():
    assert grant_status_rebucket_target("") == (None, None)
    assert grant_status_rebucket_target(None) == (None, None)


def test_status_awarded_pending_contract_not_moved():
    # "pending" appears, but the grant is awarded — must not move.
    target, _ = grant_status_rebucket_target("Awarded, pending contract")
    assert target is None


# ---------------------------------------------------------------- #575

def test_status_in_review_goes_pending():
    for status in ("In review", "In Review"):
        target, note = grant_status_rebucket_target(status)
        assert target == "M2C", status
        assert "Pending" in note


def test_status_awaiting_or_under_consideration_goes_pending():
    for status in (
        "Awaiting sponsor decision",
        "Awaiting decision",
        "Under consideration",
    ):
        target, note = grant_status_rebucket_target(status)
        assert target == "M2C", status
        assert "Pending" in note


def test_status_awaiting_non_decision_not_moved():
    # "Awaiting" a post-award step (no 'award' substring) is not a pending
    # application — only an awaited *decision* rebuckets.
    for status in ("Awaiting contract execution", "Awaiting IRB approval"):
        target, _ = grant_status_rebucket_target(status)
        assert target is None, status


def test_status_awaiting_award_setup_not_moved():
    # "Awaiting" appears, but the grant is awarded — the 'award' guard holds.
    target, _ = grant_status_rebucket_target("Awaiting award setup")
    assert target is None


# ---------------------------------------------------------------- #209

_FSMB_FIELDS = {
    "title": "Improving Access to Healthcare in Rural and Underserved Area",
    "agency": "Federation of State Medical Boards (FSMB) Foundation",
    "status": "Awarded 2026",
    "pi_name": "Shapiro, M.",
}

_FSMB_SEGMENT = (
    "Federation of State Medical Boards (FSMB) Foundation Grant | Shapiro, M. "
    "(PI), Jung, E. (Co-PI) | Improving Access to Healthcare in Rural and "
    "Underserved Areas | Role: Co-PI | Amount: $75,000 | Status: Awarded 2026."
)

_TEMPLETON_SEGMENT = (
    "John Templeton Foundation Online Funding Inquiry (OFI) | Jung, E. (PI) | "
    "Developing Intellectual Humility in Clinical Judgment | Role: PI | "
    "Amount: $1,400,000 | Status: Submitted 2026, Under review."
)


def test_rendered_segment_detected():
    assert segment_already_rendered(_FSMB_SEGMENT, _FSMB_FIELDS)


def test_unrendered_sibling_not_flagged():
    assert not segment_already_rendered(_TEMPLETON_SEGMENT, _FSMB_FIELDS)


def test_generic_fields_never_match():
    # A shared status string must not mark sibling records as rendered.
    fields = {"status": "Submitted 2026, Under review"}
    assert not segment_already_rendered(_TEMPLETON_SEGMENT, fields)


def test_empty_fields_no_match():
    assert not segment_already_rendered(_FSMB_SEGMENT, {})
    assert not segment_already_rendered(_FSMB_SEGMENT, None)


# ---------------------------------------------------------------- #213

def test_source_boilerplate_positives():
    for text in (
        "CURRICULUM VITAE",
        "Curriculum Vitae",
        "Last Updated - JUN 2026",
        "Updated: 06/2026",
        "Revised January 2024",
        "Page 3 of 12",
    ):
        assert is_source_boilerplate(text), text


def test_source_boilerplate_negatives():
    for text in (
        "Updated the curriculum for the MS4 elective",
        "Revised institutional guidelines for resident supervision",
        "Prepared expert testimony for federal court",
        "CURRICULUM VITAE\nEulho Jung",  # multi-line: real preamble block
        "Curriculum Vitae Committee, School of Medicine",
        "",
        None,
    ):
        assert not is_source_boilerplate(text), text


def test_appendix_renders_without_codes_and_drops_noise():
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document(gen.template_path)
    before = len(gen.doc.paragraphs)

    gen._add_remaining_to_appendix([
        ("Some grant text | Role: PI | Status: Under review", "M2A", 10.0),
        ("", "T", 0.0),                # empty — must be skipped
        ("CURRICULUM VITAE", "T", 0.0),  # source furniture — must be skipped
    ])

    # Only the paragraphs this call added -- the blank template already
    # carries 32 pre-existing ilvl=0 list paragraphs (its own section
    # headings), so filtering the whole document by numPr level would also
    # match those.
    added = gen.doc.paragraphs[before:]
    bullets = [p for p in added if _num_level(p) == "0"]
    assert [p.text for p in bullets] == [
        "Some grant text | Role: PI | Status: Under review"
    ]
    assert not any(p.text.startswith("•") for p in added)
    assert not any("[M2A]" in p.text for p in added)


def test_reconsider_routes_unrendered_siblings_home(monkeypatch):
    """The 89HQVQ failure, end to end (LLM stubbed): of an 8-grant mega-entry
    only FSMB was extracted/rendered. The reconsider pass must route the
    unrendered siblings to their sections — including same-code ones — and
    must not duplicate the already-rendered FSMB anywhere."""
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document(gen.template_path)

    entry = {
        "text": "eight grants in one blob",
        "taxonomy_code": "M2A",
        "extracted_fields": _FSMB_FIELDS,
    }
    gen._appendix_pending = [(entry, 12.0)]

    segments = [
        (_FSMB_SEGMENT, "M2A"),       # already rendered — must vanish
        (_TEMPLETON_SEGMENT, "M2C"),  # cross-code — routed
        ("SDRME Review Paper Grant | Jung, E. (Co-PI) | Metacognitive "
         "Reflection Training | Status: Under review.", "M2A"),  # same-code — routed
    ]
    monkeypatch.setattr(gen, "_reclassify_entry_segments", lambda text, code: segments)

    routed, appended = [], []
    monkeypatch.setattr(
        gen, "_insert_reconsidered_segment",
        # Returns True: a failed insert now falls back to the appendix (#221).
        lambda text, code: routed.append((text, code)) or True,
    )
    monkeypatch.setattr(
        gen, "_add_remaining_to_appendix", lambda remaining: appended.extend(remaining)
    )

    gen._reconsider_appendix_entries()

    assert (_TEMPLETON_SEGMENT, "M2C") in routed
    assert any(code == "M2A" and "SDRME" in text for text, code in routed)
    assert not any("FSMB" in text for text, _ in routed)
    assert not any("FSMB" in item[0] for item in appended)
    assert appended == []


def test_all_noise_batch_creates_no_appendix():
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document(gen.template_path)
    before = len(gen.doc.paragraphs)

    gen._add_remaining_to_appendix([("", "T", 0.0), ("Page 2 of 9", "T", 0.0)])

    assert len(gen.doc.paragraphs) == before
    assert not any("T. APPENDIX" in p.text for p in gen.doc.paragraphs)


# -------------------------------------------------------------- A5IZ6Q (#420)
#
# Wire tests, real generate() against the bundled WCM template: a stage-2
# structurally-recovered table row (#420's `recover_unclaimed_table_rows`)
# that duplicates content its own parent grant entry already carries must
# not ALSO land in the Appendix once the parent renders in the body.
# Synthetic reproduction of the A5IZ6Q incident shape -- one fused M2B grant
# entry plus single-field `recovered_row` siblings sharing its `parent_idx`,
# each one line of the SAME fused text the model's delimiter already
# captured whole.

_RECOVERY_GRANT_TEXT = (
    "Award Source: | Fictional Research Foundation\n"
    "Project title: | Synthetic Tools for Data Curation\n"
    "Annual direct costs: | $15,000.00\n"
    "Duration of support: | 00/2021-00/2022\n"
    "Name of Principal Investigator: | A. Researcher\n"
    "Your percent (%) effort: | 1%"
)

_RECOVERY_OWNER_ENTRY = {"text": "Name: A. Researcher", "taxonomy_code": "A",
                         "extracted_fields": {}, "element_idx_start": 0}

_RECOVERY_GRANT_ENTRY = {
    "text": _RECOVERY_GRANT_TEXT,
    "taxonomy_code": "M2B",
    "element_idx_start": 300,
    "element_idx_end": 302,
    "extracted_fields": {
        "title": "Synthetic Tools for Data Curation",
        "agency": "Fictional Research Foundation",
        "pi_name": "A. Researcher",
        "pi_role": "PI",
        "start_date": "2021",
        "end_date": "2022",
        "total_funding": "$15,000.00",
        "percent_effort": "1%",
    },
}


def _recovered_row(suffix: str, text: str, parent_idx: int = 300) -> dict:
    # `parent_idx=302` (the span's own END, not its start) exercises
    # find_recovered_row_parent's fallback -- A5IZ6Q's own second residual:
    # some LYRASIS rows keyed parent_idx to 244, an index with no entry of
    # its own, inside the grant's 242-244 table span.
    return {"text": text, "taxonomy_code": "T", "recovered_row": True,
            "parent_idx": parent_idx, "element_idx_start": f"300.{suffix}",
            "extracted_fields": {}, "hierarchy": ["Past Funding"]}


def _all_text(doc: Document) -> str:
    lines = [p.text for p in doc.paragraphs]
    for tbl in doc.tables:
        for row in tbl.rows:
            lines.append(" | ".join(c.text for c in row.cells))
    return "\n".join(lines)


def test_recovered_row_duplicate_of_rendered_grant_not_repeated_in_appendix(tmp_path):
    entries = [_RECOVERY_OWNER_ENTRY, _RECOVERY_GRANT_ENTRY,
               _recovered_row("0", "Award Source: | Fictional Research Foundation"),
               _recovered_row("1", "Project title: | Synthetic Tools for Data Curation"),
               _recovered_row("2", "Annual direct costs: | $15,000.00"),
               _recovered_row("3", "Duration of support: | 00/2021-00/2022"),
               _recovered_row("4", "Name of Principal Investigator: | A. Researcher"),
               # parent_idx=302 is the grant entry's element_idx_END, not its
               # start -- only find_recovered_row_parent's span fallback
               # resolves this one back to the grant.
               _recovered_row("5", "Your percent (%) effort: | 1%", parent_idx=302)]

    gen = WCMTemplateGenerator(verbose=False, recover_unrendered_records=False)
    gen._reconsider_appendix_entries = lambda: []
    data = {"document_uid": "T420A", "entries": entries}
    input_path = tmp_path / "in.json"
    output_path = tmp_path / "out.docx"
    input_path.write_text(json.dumps(data))
    gen.generate(str(input_path), str(output_path), research_summary_path=None)

    full_text = _all_text(Document(str(output_path)))

    # The grant rendered in the body...
    assert "Fictional Research Foundation" in full_text
    # ...and appears exactly once: the six recovered rows -- every one of
    # them a verbatim line of the fused entry that already rendered -- must
    # not repeat it in the Appendix.
    assert full_text.count("Fictional Research Foundation") == 1
    assert full_text.count("Your percent (%) effort") == 1
    assert "T. APPENDIX" not in full_text


def test_recovered_row_not_contained_in_parent_still_reaches_appendix(tmp_path):
    """The negative case: a recovered row whose content the parent's own
    text does NOT carry (a row the model's delimiter genuinely skipped --
    #420's backstop exists for exactly this) must still surface. Only a
    row PROVABLY duplicating its parent is suppressed."""
    missed_row = _recovered_row(
        "5", "Non-financial support: | Conference travel support")
    entries = [_RECOVERY_OWNER_ENTRY, _RECOVERY_GRANT_ENTRY, missed_row]

    gen = WCMTemplateGenerator(verbose=False, recover_unrendered_records=False)
    gen._reconsider_appendix_entries = lambda: []
    data = {"document_uid": "T420B", "entries": entries}
    input_path = tmp_path / "in.json"
    output_path = tmp_path / "out.docx"
    input_path.write_text(json.dumps(data))
    gen.generate(str(input_path), str(output_path), research_summary_path=None)

    full_text = _all_text(Document(str(output_path)))
    assert "T. APPENDIX" in full_text
    assert "Conference travel support" in full_text


def test_recovered_row_with_missing_parent_still_reaches_appendix(tmp_path):
    """A recovered row whose `parent_idx` resolves to no surviving entry
    (the parent itself was dropped or never existed) must not be silently
    swallowed -- there is nothing to prove it duplicates."""
    orphan_row = {"text": "Non-financial support: | Conference travel support",
                  "taxonomy_code": "T", "recovered_row": True,
                  "parent_idx": 999, "element_idx_start": "999.0",
                  "extracted_fields": {}, "hierarchy": ["Past Funding"]}
    entries = [_RECOVERY_OWNER_ENTRY, orphan_row]

    gen = WCMTemplateGenerator(verbose=False, recover_unrendered_records=False)
    gen._reconsider_appendix_entries = lambda: []
    data = {"document_uid": "T420C", "entries": entries}
    input_path = tmp_path / "in.json"
    output_path = tmp_path / "out.docx"
    input_path.write_text(json.dumps(data))
    gen.generate(str(input_path), str(output_path), research_summary_path=None)

    full_text = _all_text(Document(str(output_path)))
    assert "T. APPENDIX" in full_text
    assert "Conference travel support" in full_text
