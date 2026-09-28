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
    # Tab-joined, not " | " -- mirrors A5IZ6Q's own shape, where this last
    # multi-cell line came from a different extraction path than the rows
    # above it, and exercises the cell-separator normalization in
    # recovered_row_duplicates_parent (the recovered row below still uses
    # " | ", recover_unclaimed_table_rows' own convention).
    "Name of Principal Investigator: | A. Researcher\t"
    "Your percent (%) effort:\t1%"
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


def test_recovered_row_contained_in_parent_but_field_not_rendered_still_reaches_appendix(tmp_path):
    """The exact gap a blind review of the original A5IZ6Q fix caught: a
    recovered row can be a VERBATIM substring of its parent's raw stage-2
    text -- `recovered_row_duplicates_parent`'s whole signal -- while the
    field it carries never reaches a render slot at all. Stage 6's grant
    table is fixed-slot (CLAUDE.md "Stage 6 drops unnamed fields") and only
    folds `grant_number` into the Award Source cell when the entry's
    `extracted_fields` actually carries it. Here the parent's raw text has a
    grant-number line -- `recover_unclaimed_table_rows` captured it into the
    fused blob -- but `extracted_fields` does not, so no renderer ever sees
    it. Dropping the recovered row on raw-text containment alone would be
    the exact content loss the drop exists to avoid; it must still reach the
    Appendix."""
    grant_text = (
        "Award Source: | Fictional Research Foundation\n"
        "Project title: | Synthetic Tools for Data Curation\n"
        "Grant number: | R01-ZZ98765\n"
        "Duration of support: | 00/2021-00/2022"
    )
    grant_entry = {
        "text": grant_text,
        "taxonomy_code": "M2B",
        "element_idx_start": 400,
        "element_idx_end": 400,
        "extracted_fields": {
            "title": "Synthetic Tools for Data Curation",
            "agency": "Fictional Research Foundation",
            "start_date": "2021",
            "end_date": "2022",
            # No grant_number key -- the renderer never receives it, even
            # though the raw text above carries the exact same line.
        },
    }
    row = {"text": "Grant number: | R01-ZZ98765", "taxonomy_code": "T",
           "recovered_row": True, "parent_idx": 400,
           "element_idx_start": "400.0", "extracted_fields": {},
           "hierarchy": ["Past Funding"]}
    entries = [_RECOVERY_OWNER_ENTRY, grant_entry, row]

    gen = WCMTemplateGenerator(verbose=False, recover_unrendered_records=False)
    gen._reconsider_appendix_entries = lambda: []
    data = {"document_uid": "T420D", "entries": entries}
    input_path = tmp_path / "in.json"
    output_path = tmp_path / "out.docx"
    input_path.write_text(json.dumps(data))
    gen.generate(str(input_path), str(output_path), research_summary_path=None)

    full_text = _all_text(Document(str(output_path)))
    assert "T. APPENDIX" in full_text
    assert "R01-ZZ98765" in full_text
    # The grant itself still rendered, and did not gain a grant-number cell.
    assert "Fictional Research Foundation" in full_text


# ---------------------------------------------------- A5IZ6Q round 2 (blind review)
#
# The blind review of round 1 found two more gaps, both in
# `recovered_row_content_rendered` (stage6/dedup.py): it searched the WHOLE
# rendered document rather than the parent's own render (cross-entry
# vouching), and it matched a squashed value as a plain substring rather than
# a whole token/cell (so a short numeric value like "5%" could match inside
# an unrelated "25%"). Both wire tests below run the real `generate()`.

def test_recovered_row_from_one_grant_not_vouched_by_a_different_grants_render(tmp_path):
    """The exact round-2 repro: two M2B grants, A and B. A's own
    extracted_fields carry dates and percent_effort and render normally. B's
    RAW text also carries a duration line and a percent-effort line -- so
    `recover_unclaimed_table_rows` splits them out as B's own recovered
    rows -- but B's `extracted_fields` carry only title and agency, so B's
    OWN grant table never renders either value (label only, blank cell).

    The pre-round-2 code searched the whole document for B's rows' values:
    it found A's rendered "2019-2020" (a token match against B's duration
    row) and A's rendered "25%" (a squashed-substring match against B's own
    "5%" -- '5%' is literally contained in '25%') and wrongly concluded both
    of B's rows had already rendered. Neither actually reached the document
    anywhere: B's own table has blank cells for both fields. Both recovered
    rows must survive to the Appendix."""
    grant_a = {
        "text": ("Award Source: | Aurora Foundation\n"
                 "Project title: | Alpha Sequencing Initiative\n"
                 "Duration of support: | 00/2019-00/2020\n"
                 "Your percent (%) effort: | 25%"),
        "taxonomy_code": "M2B",
        "element_idx_start": 600,
        "element_idx_end": 600,
        "extracted_fields": {
            "title": "Alpha Sequencing Initiative",
            "agency": "Aurora Foundation",
            "start_date": "2019",
            "end_date": "2020",
            "percent_effort": "25%",
        },
    }
    grant_b = {
        "text": ("Award Source: | Borealis Institute\n"
                 "Project title: | Beta Imaging Cohort\n"
                 "Duration of support: | 00/2019-00/2020\n"
                 "Your percent (%) effort: | 5%"),
        "taxonomy_code": "M2B",
        "element_idx_start": 700,
        "element_idx_end": 700,
        "extracted_fields": {
            "title": "Beta Imaging Cohort",
            "agency": "Borealis Institute",
            # No start_date/end_date/percent_effort: B's own table renders
            # neither value, even though the raw text above (and B's own
            # recovered-row siblings below) carries them verbatim.
        },
    }
    row_b_duration = {"text": "Duration of support: | 00/2019-00/2020",
                      "taxonomy_code": "T", "recovered_row": True,
                      "parent_idx": 700, "element_idx_start": "700.0",
                      "extracted_fields": {}, "hierarchy": ["Past Funding"]}
    row_b_effort = {"text": "Your percent (%) effort: | 5%",
                    "taxonomy_code": "T", "recovered_row": True,
                    "parent_idx": 700, "element_idx_start": "700.1",
                    "extracted_fields": {}, "hierarchy": ["Past Funding"]}
    entries = [_RECOVERY_OWNER_ENTRY, grant_a, grant_b, row_b_duration, row_b_effort]

    gen = WCMTemplateGenerator(verbose=False, recover_unrendered_records=False)
    gen._reconsider_appendix_entries = lambda: []
    data = {"document_uid": "T946A", "entries": entries}
    input_path = tmp_path / "in.json"
    output_path = tmp_path / "out.docx"
    input_path.write_text(json.dumps(data))
    gen.generate(str(input_path), str(output_path), research_summary_path=None)

    full_text = _all_text(Document(str(output_path)))
    # Both grants rendered in the body.
    assert "Alpha Sequencing Initiative" in full_text
    assert "Beta Imaging Cohort" in full_text
    # B's own recovered rows were NOT dropped -- they reached the Appendix
    # (_clean_inline_tabs rejoins the raw " | " cell separator as " — " for
    # display), proving they were never actually found rendered anywhere.
    assert "T. APPENDIX" in full_text
    assert "Duration of support: — 00/2019-00/2020" in full_text
    assert "Your percent (%) effort: — 5%" in full_text


def test_recovered_row_value_coincidentally_matching_parent_render_still_gated_by_raw_text(tmp_path):
    """The parent-scoping gate (`recovered_row_duplicates_parent`) stays
    necessary even after round 2 scopes `recovered_row_content_rendered` to
    one block: it is the ONLY check confirming the row's raw text actually
    came from THIS parent, rather than merely resolving (via `parent_idx`)
    to an entry whose own render happens to carry a matching value by
    coincidence. Here `grant_entry`'s raw stage-2 text never mentions percent
    effort at all, so the row below is not a genuine A5IZ6Q duplicate of
    it -- but `grant_entry`'s `extracted_fields` legitimately carries
    percent_effort "5%", which DOES render in its own table. A row dropped
    on content-match alone (skipping the raw-text gate) would lose content
    that was never proven to be this parent's own duplicate."""
    grant_entry = {
        "text": ("Award Source: | Fictional Research Foundation\n"
                 "Project title: | Synthetic Tools for Data Curation\n"
                 "Duration of support: | 00/2021-00/2022"),
        "taxonomy_code": "M2B",
        "element_idx_start": 500,
        "element_idx_end": 500,
        "extracted_fields": {
            "title": "Synthetic Tools for Data Curation",
            "agency": "Fictional Research Foundation",
            "start_date": "2021",
            "end_date": "2022",
            "percent_effort": "5%",  # renders even though the raw text above
                                     # never mentions percent effort at all
        },
    }
    mismatched_row = {"text": "Your percent (%) effort: | 5%",
                      "taxonomy_code": "T", "recovered_row": True,
                      "parent_idx": 500, "element_idx_start": "500.0",
                      "extracted_fields": {}, "hierarchy": ["Past Funding"]}
    entries = [_RECOVERY_OWNER_ENTRY, grant_entry, mismatched_row]

    gen = WCMTemplateGenerator(verbose=False, recover_unrendered_records=False)
    gen._reconsider_appendix_entries = lambda: []
    data = {"document_uid": "T946B", "entries": entries}
    input_path = tmp_path / "in.json"
    output_path = tmp_path / "out.docx"
    input_path.write_text(json.dumps(data))
    gen.generate(str(input_path), str(output_path), research_summary_path=None)

    full_text = _all_text(Document(str(output_path)))
    assert "T. APPENDIX" in full_text
    # The value rendered once from the grant table's own row-join ("_all_text"
    # joins each table row's cells with " | ")...
    assert full_text.count("Your percent (%) effort: | 5%") == 1
    # ...and the row was NOT dropped: it also reached the Appendix bullet
    # (_clean_inline_tabs rejoins " | " as " — " for display there).
    assert full_text.count("Your percent (%) effort: — 5%") == 1
