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

from docx import Document  # noqa: E402

from unified_pipeline.core.template_boilerplate import is_source_boilerplate  # noqa: E402
from unified_pipeline.stage_6_word_template import (  # noqa: E402
    WCMTemplateGenerator,
    grant_status_rebucket_target,
    segment_already_rendered,
)


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

    gen._add_remaining_to_appendix([
        ("Some grant text | Role: PI | Status: Under review", "M2A", 10.0),
        ("", "T", 0.0),                # empty — must be skipped
        ("CURRICULUM VITAE", "T", 0.0),  # source furniture — must be skipped
    ])

    texts = [p.text for p in gen.doc.paragraphs]
    bullets = [t for t in texts if t.strip().startswith("•")]
    assert bullets == ["• Some grant text | Role: PI | Status: Under review"]
    assert not any("[M2A]" in t for t in texts)


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
        gen, "_insert_reconsidered_segment", lambda text, code: routed.append((text, code))
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
