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

import sys
from pathlib import Path

# Ensure the repo's ``src`` directory is importable regardless of cwd/rootdir.
_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest  # noqa: E402
from docx import Document  # noqa: E402

from docx.oxml.ns import qn  # noqa: E402

from unified_pipeline.core.template_boilerplate import is_source_boilerplate  # noqa: E402
from unified_pipeline.stage_6_word_template import (  # noqa: E402
    WCMTemplateGenerator,
    grant_status_rebucket_target,
    parse_reclassified_segments,
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


# ---------------------------------------------------------------- #264

_PROSE_REPLY = (
    "**All segments are retained under M2B: the grant details, project goals "
    "and associated publications directly relate to the funded project.\n"
    "M2B: Synthetic Grant Alpha | Example Agency | 2010-2012"
)


def test_parse_reclassified_segments_well_formed():
    reply = ("M2B: Synthetic Grant Alpha | Example Agency | 2010-2012\n"
             "K2: Participate in clinical teaching conferences\n"
             "KEEP: Attending Physician, Example Hospital, 2004-Present\n"
             "q4a: Editorial board member, Example Journal")
    assert parse_reclassified_segments(reply, "M2B") == [
        ("Synthetic Grant Alpha | Example Agency | 2010-2012", "M2B"),
        ("Participate in clinical teaching conferences", "K2"),
        ("Attending Physician, Example Hospital, 2004-Present", "M2B"),
        ("Editorial board member, Example Journal", "Q4A"),
    ]


_LIVE_SHAPE_REPLY = (
    "Here is the analysis of the CV content:\n\n"
    "M2B: R01 XX000000 Synthetic Study of Signaling, Example Institute, 2010-2015, PI\n"
    "M2B: Project goals: characterize pathway X\n"
    "M2B: Associated publications: Doe J et al 2014\n"
    "K2: Participate in clinical teaching conferences\n\n"
    "**Rationale:**\n"
    "- All segments remain classified as **M2B** because:\n"
    "  - Both grants have clearly defined end dates\n"
    "- No segments meet the threshold of **clearly** belonging elsewhere\n\n"
    "> **Note:** If your institution treats these as active, reclassify them."
)


def test_parse_reclassified_segments_drops_commentary_keeps_code_lines():
    """The real reply shape (preamble + code lines + rationale + note): only
    the code lines are segments. Refusing the whole reply over the preamble
    sent 13 of 18 live replies back to the appendix (#264)."""
    assert parse_reclassified_segments(_LIVE_SHAPE_REPLY, "M2B") == [
        ("R01 XX000000 Synthetic Study of Signaling, Example Institute, 2010-2015, PI", "M2B"),
        ("Project goals: characterize pathway X", "M2B"),
        ("Associated publications: Doe J et al 2014", "M2B"),
        ("Participate in clinical teaching conferences", "K2"),
    ]


def test_parse_reclassified_segments_prose_line_is_dropped_not_a_segment():
    """Commentary with a colon used to parse as a segment whose 'code' was the
    sentence, then rendered as an appendix bullet (#264)."""
    assert parse_reclassified_segments(_PROSE_REPLY, "M2B") == [
        ("Synthetic Grant Alpha | Example Agency | 2010-2012", "M2B")]


@pytest.mark.parametrize("reply", [
    # commentary only, no valid code line: nothing usable
    "All segments are retained under M2B: the grant details, project goals relate.",
    "Here is the analysis of the CV content:\n\n**Rationale:** all stay put.",
    "Note: segments are retained under M2B and the grant details relate.",
    "M2Z: Synthetic Grant Alpha, Example Agency, 2010-2012",
])
def test_parse_reclassified_segments_no_code_line_is_none(reply):
    assert parse_reclassified_segments(reply, "M2B") is None


@pytest.mark.parametrize("prefix", ["ALL", "Note", "M2Z", "**All segments", "> **Note"])
def test_parse_reclassified_segments_non_taxonomy_prefix_never_a_segment(prefix):
    reply = (f"{prefix}: segments are retained under M2B and details relate.\n"
             "M2B: Synthetic Grant Alpha, Example Agency, 2010-2012")
    assert parse_reclassified_segments(reply, "M2B") == [
        ("Synthetic Grant Alpha, Example Agency, 2010-2012", "M2B")]


def test_parse_reclassified_segments_keep_short_and_empty_edges():
    reply = ("  K1: Synthetic course lecture series, Example University  \n"
             "KEEP: Synthetic Grant Alpha, Example Agency, 2010-2012\n"
             "K2: too short\n")
    # KEEP resolves to the original code; a segment of <=10 chars is dropped;
    # surrounding whitespace on a line is stripped.
    assert parse_reclassified_segments(reply, "M2B") == [
        ("Synthetic course lecture series, Example University", "K1"),
        ("Synthetic Grant Alpha, Example Agency, 2010-2012", "M2B")]
    # KEEP with an unknown original code has no home: code None.
    assert parse_reclassified_segments(
        "KEEP: Synthetic Grant Alpha, Example Agency", "?") == [
        ("Synthetic Grant Alpha, Example Agency", None)]
    # Nothing usable -> None, not [].
    assert parse_reclassified_segments("K2: too short", "M2B") is None
    assert parse_reclassified_segments("no colons here", "M2B") is None


def test_reclassify_prose_reply_never_reaches_document(monkeypatch):
    """End to end with call_llm stubbed to return commentary with no code
    line: the original entry text is what lands in the appendix, never the
    model's prose."""
    import unified_pipeline.stage_6_word_template as st6

    monkeypatch.setattr(st6, "call_llm", lambda **kw: {"content": (
        "**All segments are retained under M2B: the grant details, project "
        "goals and associated publications directly relate to the project.")})
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document(gen.template_path)
    original = "Synthetic Grant Alpha, Example Agency, 2010-2012, funded project"
    gen._appendix_pending = [({"text": original, "taxonomy_code": "M2B",
                               "extracted_fields": {}}, 10.0)]

    gen._reconsider_appendix_entries()

    texts = [p.text for p in gen.doc.paragraphs]
    assert not any("All segments are retained" in t or "grant details" in t
                   for t in texts)
    assert any(original in t for t in texts)


def test_reclassify_live_shape_reply_renders_segments_without_commentary(monkeypatch):
    """End to end with the real reply shape: the code lines are used and none
    of the preamble / rationale / note text reaches the document."""
    import unified_pipeline.stage_6_word_template as st6

    monkeypatch.setattr(st6, "call_llm", lambda **kw: {"content": _LIVE_SHAPE_REPLY})
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document(gen.template_path)
    original = "Synthetic Study of Signaling, Example Institute, 2010-2015, PI; project goals"
    gen._appendix_pending = [({"text": original, "taxonomy_code": "M2B",
                               "extracted_fields": {}}, 10.0)]

    gen._reconsider_appendix_entries()

    joined = "\n".join(p.text for p in gen.doc.paragraphs)
    for leak in ("Here is the analysis", "Rationale", "If your institution",
                 "clearly", "threshold"):
        assert leak not in joined
    # Text only the PARSED segments carry: the fallback that keeps the
    # original entry text can never produce it, so this pins the call site.
    assert "R01 XX000000" in joined
    assert "Associated publications: Doe J et al 2014" in joined
    assert gen.stats["appendix_segments_reconsidered"] >= 1


def test_reclassify_keep_line_routes_under_original_code(monkeypatch):
    """End to end: a 'KEEP: ...' segment of an M2B entry routes as M2B (the
    original code), not as '?' / the appendix (#209, #264)."""
    import unified_pipeline.stage_6_word_template as st6

    monkeypatch.setattr(st6, "call_llm", lambda **kw: {"content": (
        "KEEP: Synthetic Grant Beta, Example Agency, 2011-2013, completed")})
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document(gen.template_path)
    gen._appendix_pending = [({"text": "blob of text", "taxonomy_code": "M2B",
                               "extracted_fields": {}}, 10.0)]
    routed = []
    monkeypatch.setattr(
        gen, "_insert_reconsidered_segment",
        lambda text, code: routed.append((text, code)) or True)
    appended = []
    monkeypatch.setattr(
        gen, "_add_remaining_to_appendix", lambda r: appended.extend(r) or [])

    gen._reconsider_appendix_entries()

    assert routed == [
        ("Synthetic Grant Beta, Example Agency, 2011-2013, completed", "M2B")]
    assert appended == []


@pytest.mark.parametrize("line", [
    "- K2: Participate in clinical teaching conferences",
    "* K2: Participate in clinical teaching conferences",
    "\u2022 K2: Participate in clinical teaching conferences",
    "> K2: Participate in clinical teaching conferences",
    "# K2: Participate in clinical teaching conferences",
    "1. K2: Participate in clinical teaching conferences",
    "**K2:** Participate in clinical teaching conferences",
    "- **K2:** Participate in clinical teaching conferences",
])
def test_parse_reclassified_segments_strips_markdown_prefix(line):
    assert parse_reclassified_segments(line, "M2B") == [
        ("Participate in clinical teaching conferences", "K2")]


def test_parse_reclassified_segments_markdown_prefix_commentary_still_dropped():
    reply = ("- **Note:** everything stays put and details relate.\n"
             "1. Rationale: grants are complete.\n"
             "- KEEP: Synthetic Grant Alpha, Example Agency, 2010-2012")
    assert parse_reclassified_segments(reply, "M2B") == [
        ("Synthetic Grant Alpha, Example Agency, 2010-2012", "M2B")]


def test_all_noise_batch_creates_no_appendix():
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document(gen.template_path)
    before = len(gen.doc.paragraphs)

    gen._add_remaining_to_appendix([("", "T", 0.0), ("Page 2 of 9", "T", 0.0)])

    assert len(gen.doc.paragraphs) == before
    assert not any("T. APPENDIX" in p.text for p in gen.doc.paragraphs)
