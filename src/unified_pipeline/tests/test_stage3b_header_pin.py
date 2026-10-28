"""stage3b/header_pin.py (#312): a confidently mapped section header beats a
model answer in a named set of confusions, and nothing else.

Self-contained: no LLM, no network, synthetic text only.
"""
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage3b.context import TaxonomyContext  # noqa: E402
from unified_pipeline.stage3b.header_pin import (  # noqa: E402
    HEADER_PIN_MIN_CONFIDENCE,
    apply_header_pin,
    apply_non_seat_pins,
    content_pin_code,
    is_note_not_record,
    is_reviewer_lead_in,
    leaf_header_code,
    mentee_pin_code,
    non_seat_pin_code,
    pinned_header_code,
    someone_elses_pin_code,
)


def _node(title, code, confidence=1.0):
    return {"title": title, "taxonomy_options": [{"code": code, "confidence": confidence}]}


def _ctx(meta=None, section=None, subsection=None):
    return TaxonomyContext(meta_section=meta, section=section, subsection=subsection)


R_CTX = _ctx(meta=_node("INVITED PRESENTATIONS", "R"), section=_node("National", "R"))


def _entry(code, source="llm", reasoning="professional education (K4)"):
    return {"text": "Workshop faculty, Example Society", "taxonomy_code": code,
            "taxonomy_confidence": 0.95, "classification_reasoning": reasoning,
            "classification_source": source}


# --- pinned_header_code -------------------------------------------------------

def test_top_level_header_pins():
    assert pinned_header_code(_ctx(meta=_node("INSTITUTIONAL LEADERSHIP ACTIVITIES", "O"))) == "O"


def test_sublabel_under_agreeing_parent_pins():
    assert pinned_header_code(R_CTX) == "R"


@pytest.mark.parametrize("label", ["Local", "Regional", "National", "International", " national "])
def test_bare_scope_label_alone_does_not_pin(label):
    # A teaching CV's "Local": stage 3a maps it to R with no parent to say why.
    assert pinned_header_code(_ctx(meta=_node(label, "R"))) is None


def test_scope_label_under_disagreeing_parent_does_not_pin():
    ctx = _ctx(meta=_node("BIBLIOGRAPHY", "S1", 0.5), section=_node("International", "R"))
    assert pinned_header_code(ctx) is None


def test_scope_label_under_a_different_confident_code_does_not_pin():
    ctx = _ctx(meta=_node("TEACHING", "K1", 1.0), section=_node("Local", "R"))
    assert pinned_header_code(ctx) is None


def test_below_the_confidence_floor_does_not_pin():
    below = HEADER_PIN_MIN_CONFIDENCE - 0.01
    assert pinned_header_code(_ctx(meta=_node("INVITED PRESENTATIONS", "R", below))) is None
    assert pinned_header_code(_ctx(meta=_node("INVITED PRESENTATIONS", "R", HEADER_PIN_MIN_CONFIDENCE))) == "R"


def test_most_specific_level_must_reach_the_floor_even_if_its_parent_does():
    ctx = _ctx(meta=_node("INVITED PRESENTATIONS", "R", 1.0), section=_node("Invited talks", "R", 0.6))
    assert pinned_header_code(ctx) is None


def test_subsection_is_the_most_specific_level():
    ctx = _ctx(meta=_node("INVITED PRESENTATIONS", "R", 1.0), section=_node("Talks", "R", 1.0),
               subsection=_node("Grand rounds", "R", 0.6))
    assert pinned_header_code(ctx) is None


def test_no_context_does_not_pin():
    assert pinned_header_code(_ctx()) is None
    assert pinned_header_code(_ctx(meta={"title": "X", "taxonomy_options": []})) is None


def test_top_option_is_the_highest_confidence_not_the_first():
    node = {"title": "INVITED PRESENTATIONS", "taxonomy_options": [
        {"code": "S8", "confidence": 0.02}, {"code": "R", "confidence": 0.98}]}
    assert pinned_header_code(_ctx(meta=node)) == "R"


# --- apply_header_pin ---------------------------------------------------------

@pytest.mark.parametrize("wrong", ["K1", "K2", "K4", "H"])
def test_recodes_each_overridable_confusion_under_r(wrong):
    (out,), n = apply_header_pin([_entry(wrong)], R_CTX)
    assert n == 1
    assert out["taxonomy_code"] == "R"
    assert out["pre_pin_code"] == wrong
    assert out["pre_pin_reasoning"] == "professional education (K4)"
    assert out["classification_source"] == "llm"


@pytest.mark.parametrize("right", ["S1", "Q2", "M2B", "T", "R", "K3"])
def test_leaves_every_other_model_answer_under_r(right):
    entry = _entry(right)
    (out,), n = apply_header_pin([entry], R_CTX)
    assert n == 0
    assert out == entry


def test_o_header_recodes_course_director_rows_only():
    ctx = _ctx(meta=_node("INSTITUTIONAL LEADERSHIP ACTIVITIES", "O"))
    out, n = apply_header_pin([_entry("K3"), _entry("K4"), _entry("K1"), _entry("P")], ctx)
    assert n == 2
    assert [e["taxonomy_code"] for e in out] == ["O", "O", "K1", "P"]


def test_fallback_and_empty_entries_are_not_model_answers():
    out, n = apply_header_pin([_entry("K4", source="fallback"), _entry("K4", source="empty_entry")], R_CTX)
    assert n == 0
    assert [e["taxonomy_code"] for e in out] == ["K4", "K4"]


def test_rewritten_reasoning_cannot_flip_the_code_back():
    from unified_pipeline.core.validators.reasoning_consistency_checker import (
        apply_reasoning_corrections,
        check_reasoning_consistency,
    )
    reasoning = "CME workshop for practicing professionals; K4 is appropriate"
    # Guard the fixture: the checker must parse this as a K4 claim against code R,
    # or the assertions below cannot fail whatever the rewrite does.
    assert check_reasoning_consistency(_entry("R", reasoning=reasoning)).has_conflict
    (out,), _ = apply_header_pin([_entry("K4", reasoning=reasoning)], R_CTX)
    assert not check_reasoning_consistency(out).has_conflict
    (corrected,), _stats = apply_reasoning_corrections([out], min_confidence=0.80)
    assert corrected["taxonomy_code"] == "R"


def test_unpinnable_group_is_returned_unchanged():
    entries = [_entry("K4")]
    out, n = apply_header_pin(entries, _ctx(meta=_node("TEACHING", "K1", 0.4)))
    assert n == 0 and out == entries


def test_input_entries_are_not_mutated():
    entry = _entry("K4")
    before = dict(entry)
    apply_header_pin([entry], R_CTX)
    assert entry == before


def test_pin_reasoning_is_a_plain_sentence_for_the_optional_word_comment():
    # Stage 6 shows classification_reasoning as a Word comment when the user opts in.
    (out,), _ = apply_header_pin([_entry("K4")], R_CTX)
    text = out["classification_reasoning"]
    assert "pin" not in text.lower() and "mapped" not in text.lower()
    assert text.endswith(".") and "category R" in text and "K4" in text


# --- EBYSBC E11 (#312): S8 under an R pin -------------------------------------

def _row(code, text, confidence=0.6, hierarchy=("SECTION",)):
    return {"text": text, "taxonomy_code": code, "taxonomy_confidence": confidence,
            "classification_reasoning": "model answer", "classification_source": "llm",
            "hierarchy": list(hierarchy)}


def test_unsure_author_less_s8_under_r_is_recoded():
    talk = _row("S8", '"Signals in an example tissue." Example Society Annual Meeting, Springfield, 2031.')
    (out,), n = apply_header_pin([talk], R_CTX)
    assert n == 1 and out["taxonomy_code"] == "R" and out["pre_pin_code"] == "S8"


@pytest.mark.parametrize("text", [
    "Doe, J.A., Roe, K. Signals in an example tissue. Example Society Meeting, 2031.",
    "Doe JA, Roe K. Signals in an example tissue. Example Society Meeting, 2031.",
    "Doe J et al. Signals in an example tissue. Example Society Meeting, 2031.",
    '"Signals in an example tissue" (poster), Example Society Meeting, 2031.',
    "Signals in an example tissue. Example Student Research Forum, 2031 (Finalist).",
])
def test_s8_with_an_author_list_or_contributed_marker_stays(text):
    entry = _row("S8", text)
    (out,), n = apply_header_pin([entry], R_CTX)
    assert n == 0 and out == entry


def test_confident_s8_under_r_stays():
    entry = _row("S8", '"Signals in an example tissue." Example Society Meeting, 2031.', confidence=0.85)
    (out,), n = apply_header_pin([entry], R_CTX)
    assert n == 0 and out == entry


def test_surname_with_bare_initials_and_comma_is_an_author_list():
    # Only the "Doe JA," alternative matches: no initial carries a period, no "et al".
    entry = _row("S8", "Doe JA, Roe BC: Signals in an example tissue. Example Society Meeting, 2031")
    (out,), n = apply_header_pin([entry], R_CTX)
    assert n == 0 and out == entry


@pytest.mark.parametrize("confidence", [None, "high", ""])
def test_s8_with_an_unreadable_confidence_counts_as_certain(confidence):
    entry = _row("S8", '"Signals in an example tissue." Example Society Meeting, 2031.', confidence=confidence)
    (out,), n = apply_header_pin([entry], R_CTX)
    assert n == 0 and out == entry


def test_a_city_with_initials_is_not_an_author_list():
    talk = _row("S8", "Example Society Meeting, Washington, D.C.; 2031. Signals in an example tissue")
    (out,), _ = apply_header_pin([talk], R_CTX)
    assert out["taxonomy_code"] == "R"


def test_author_less_s8_without_an_r_pin_stays():
    entry = _row("S8", '"Signals in an example tissue." Example Society Meeting, 2031.')
    (out,), n = apply_header_pin([entry], _ctx(meta=_node("ABSTRACTS", "S8")))
    assert n == 0 and out == entry


# --- EBYSBC E11 (#312): Q2 under a Q3 sub-heading -------------------------------

GRANT_CTX = _ctx(meta=_node("EXAMPLE SERVICE", "Q2", 0.5), section=_node("Example grant review", "Q3"))


def test_leaf_code_ignores_a_disagreeing_parent():
    assert pinned_header_code(GRANT_CTX) is None
    assert leaf_header_code(GRANT_CTX) == "Q3"
    below = _ctx(section=_node("Example grant review", "Q3", HEADER_PIN_MIN_CONFIDENCE - 0.01))
    assert leaf_header_code(below) is None
    assert leaf_header_code(_ctx()) is None


@pytest.mark.parametrize("text,hierarchy", [
    ("2031 Proposal review, Example Foundation", ("EXAMPLE SERVICE", "Example grant review")),
    ("2031 Example Pilot Program, Example University", ("REVIEWER", "Grants")),
    ("2031 Ad hoc reviewer, Example Research Council", ("SERVICE", "Peer review")),
    ("2031 Reviewer, Example Award grant review committee", ("SERVICE", "Peer review")),
])
def test_grant_review_rows_under_a_q3_sub_heading_become_q3(text, hierarchy):
    (out,), n = apply_header_pin([_row("Q2", text, hierarchy=hierarchy)], GRANT_CTX)
    assert n == 1 and out["taxonomy_code"] == "Q3"


@pytest.mark.parametrize("text,hierarchy", [
    ("2031 External Advisory Committee, Example Training Program", ("SERVICE", "Example grant review")),
    ("2031 Member, Example Board Scientific Committee", ("SERVICE", "Example grant review")),
    ("2031 Co-Chair, Example Session, Example Meeting", ("SERVICE", "Example grant review")),
    ("2031 Reviewer, all abstracts, Example Annual Meeting", ("SERVICE", "Example grant review")),
    ("2031 Example Seed Program, Example University", ("SERVICE", "Peer review")),
])
def test_committee_rows_and_rows_with_no_review_evidence_stay_q2(text, hierarchy):
    entry = _row("Q2", text, hierarchy=hierarchy)
    (out,), n = apply_header_pin([entry], GRANT_CTX)
    assert n == 0 and out == entry


def test_q3_sub_heading_overrides_only_q2():
    entry = _row("Q1", "2031 Chair, Example grant review panel", hierarchy=("SERVICE", "Example grant review"))
    (out,), n = apply_header_pin([entry], GRANT_CTX)
    assert n == 0 and out == entry


# --- #1938: an O or K3 answer under an N1 sub-heading ---------------------------

N1_HEADING = ("MENTORING", "Leadership and mentoring in programs")
N1_CTX = _ctx(meta=_node("MENTORING", "N3", 0.5), section=_node("Leadership and mentoring in programs", "N1"))


def test_n1_leaf_pins_under_a_disagreeing_parent():
    assert pinned_header_code(N1_CTX) is None
    assert leaf_header_code(N1_CTX) == "N1"


@pytest.mark.parametrize("code", ["O", "K3"])
@pytest.mark.parametrize("text", [
    "2031-present Director, Example Fellowship Program, Example Hospital",
    "2031-present Assistant Designated Institutional Officer, Example Hospital",
    "2031 Faculty, Example T32 Training Program",
])
def test_o_or_k3_under_an_n1_sub_heading_becomes_n1(code, text):
    (out,), n = apply_header_pin([_row(code, text, confidence=0.8, hierarchy=N1_HEADING)], N1_CTX)
    assert n == 1 and out["taxonomy_code"] == "N1" and out["pre_pin_code"] == code
    assert "category N1" in out["classification_reasoning"]


@pytest.mark.parametrize("code", ["N3A", "N3B", "N4", "N2", "D1", "K1", "Q2"])
def test_n1_sub_heading_leaves_mentee_and_other_codes_alone(code):
    entry = _row(code, "2031 Example Trainee, MD: Example research project", hierarchy=N1_HEADING)
    (out,), n = apply_header_pin([entry], N1_CTX)
    assert n == 0 and out == entry


def test_n1_sub_heading_below_the_floor_does_not_pin():
    below = _ctx(meta=_node("MENTORING", "N3", 0.5),
                 section=_node("Leadership and mentoring in programs", "N1", HEADER_PIN_MIN_CONFIDENCE - 0.01))
    entry = _row("O", "2031-present Director, Example Fellowship Program", hierarchy=N1_HEADING)
    (out,), n = apply_header_pin([entry], below)
    assert n == 0 and out == entry


def test_o_under_a_non_n1_heading_is_untouched():
    ctx = _ctx(meta=_node("MENTORING", "N3"), section=_node("Mentees", "N3B"))
    entry = _row("O", "2031-present Director, Example Fellowship Program", hierarchy=("MENTORING", "Mentees"))
    (out,), n = apply_header_pin([entry], ctx)
    assert n == 0 and out == entry


def test_n1_pin_skips_a_fallback_answer():
    entry = {**_row("O", "2031-present Director, Example Fellowship Program", hierarchy=N1_HEADING),
             "classification_source": "fallback"}
    (out,), n = apply_header_pin([entry], N1_CTX)
    assert n == 0 and out == entry


# --- #1977: an O answer under an L3 sub-heading ----------------------------------

L3_HEADING = ("CLINICAL PRACTICE, INNOVATION, and LEADERSHIP", "Clinical Leadership")
L3_CTX = _ctx(meta=_node("CLINICAL PRACTICE, INNOVATION, and LEADERSHIP", "L", 0.6),
              section=_node("Clinical Leadership", "L3"))


def test_o_under_an_l3_sub_heading_becomes_l3():
    entry = _row("O", "2031-present Vice Chair for Education, Department of Example, Example Hospital",
                 confidence=0.8, hierarchy=L3_HEADING)
    (out,), n = apply_header_pin([entry], L3_CTX)
    assert n == 1 and out["taxonomy_code"] == "L3" and out["pre_pin_code"] == "O"
    assert "category L3" in out["classification_reasoning"]


@pytest.mark.parametrize("code", ["K3", "P", "L1", "L2", "Q2", "T"])
def test_l3_sub_heading_leaves_other_codes_alone(code):
    entry = _row(code, "2031-present Example clinical role, Example Hospital", hierarchy=L3_HEADING)
    (out,), n = apply_header_pin([entry], L3_CTX)
    assert n == 0 and out == entry


def test_l3_sub_heading_below_the_floor_does_not_pin():
    below = _ctx(meta=_node("CLINICAL PRACTICE, INNOVATION, and LEADERSHIP", "L", 0.6),
                 section=_node("Clinical Leadership", "L3", HEADER_PIN_MIN_CONFIDENCE - 0.01))
    entry = _row("O", "2031-present Vice Chair for Education, Example Hospital", hierarchy=L3_HEADING)
    (out,), n = apply_header_pin([entry], below)
    assert n == 0 and out == entry


# --- YUYVIG (#1577): an unsure grant answer under an H pin --------------------------

H_CTX = _ctx(meta=_node("Awards and Honors", "H"))
HONORS = ("Awards and Honors",)


@pytest.mark.parametrize("code", ["M2", "M2A", "M2B", "M2C"])
@pytest.mark.parametrize("text", [
    "2031 Example Investigator Award, Example Alliance for Research",
    "2031 Example Consensus Group Grant, Example Fund",
    "Example Foundation Young Investigator Award (2031)",
    "• Designed experiments as P.I. that led Example Agency to sponsor a study",
])
def test_unsure_grant_code_without_a_grant_marker_under_h_becomes_h(code, text):
    (out,), n = apply_header_pin([_row(code, text, confidence=0.55, hierarchy=HONORS)], H_CTX)
    assert n == 1 and out["taxonomy_code"] == "H" and out["pre_pin_code"] == code


@pytest.mark.parametrize("text", [
    "2031 Example Agency R01 Grant, Example signals in tissue",                 # activity code
    "Supported by Example training grant 2T32GM000000-11, 2031",                # grant number
    "Example Student Traineeship Grant (EXMPL31H0), 2031",                      # award id
    "2031 Example Research and Creative Grant funded for $1,000",               # amount
    "2031 Example Seed Grant, 5000 USD",                                        # amount
    "Example Grant-in-Aid, 2031-2033",                                          # two-ended period
    "Example Career Development Award, May 2031 to May 2033",
    "Example Fellow, supported 7/2031-6/2033",
    "Example Scholar Award, fall 2031-spring 2033",
    "Example Pilot Award 2031-33",
    "Example Pilot Award 2031 - present",
    "Principal Investigator. Example Society Research Grant, 2031",             # PI role at the head
    "2031 Co-Investigator, Example Foundation Grant",
    "PI, Example Foundation Grant, 2031",
])
def test_unsure_grant_code_with_a_grant_marker_under_h_stays(text):
    entry = _row("M2A", text, confidence=0.55, hierarchy=HONORS)
    (out,), n = apply_header_pin([entry], H_CTX)
    assert n == 0 and out == entry


@pytest.mark.parametrize("text", [
    "Pi Kappa Example Award, 2031",                 # "Pi" is not a PI role
    "Example COVID-19 Response Award, 2031",        # a hyphenated name is not a grant id
])
def test_lookalike_markers_do_not_keep_a_grant_code(text):
    (out,), n = apply_header_pin([_row("M2A", text, confidence=0.55, hierarchy=HONORS)], H_CTX)
    assert n == 1 and out["taxonomy_code"] == "H"


def test_h_pin_breaks_a_tie_and_does_not_overrule_a_firm_grant_answer():
    text = "2031 Example Investigator Award, Example Alliance for Research"
    (at_ceiling,), _ = apply_header_pin([_row("M2A", text, confidence=0.80, hierarchy=HONORS)], H_CTX)
    assert at_ceiling["taxonomy_code"] == "H"
    firm = _row("M2A", text, confidence=0.85, hierarchy=HONORS)
    (out,), n = apply_header_pin([firm], H_CTX)
    assert n == 0 and out == firm


@pytest.mark.parametrize("code,text", [
    ("I", "2031 Fellow, Example College of Examples"),     # a fellowship stays the model's call
    ("M2D", "2031 Example dataset deposit"),
    ("K4", "2031 Example teaching award lecture"),
])
def test_h_pin_recodes_only_grant_codes(code, text):
    entry = _row(code, text, confidence=0.55, hierarchy=HONORS)
    (out,), n = apply_header_pin([entry], H_CTX)
    assert n == 0 and out == entry


@pytest.mark.parametrize("ctx", [
    _ctx(meta=_node("Awards and Honors", "H", HEADER_PIN_MIN_CONFIDENCE - 0.01)),
    _ctx(meta=_node("Awards and Grants", "M2", 1.0), section=_node("Honors", "H", 1.0)),
    _ctx(meta=_node("Research Support", "M2A", 1.0)),
])
def test_no_h_pin_leaves_an_unsure_grant_code(ctx):
    entry = _row("M2A", "2031 Example Investigator Award", confidence=0.55, hierarchy=HONORS)
    (out,), n = apply_header_pin([entry], ctx)
    assert n == 0 and out == entry


def test_h_pin_is_not_flipped_back_by_the_reasoning_or_grant_status_checks():
    from unified_pipeline.core.validators.grant_status_corrector import (
        apply_grant_status_corrections,
    )
    from unified_pipeline.core.validators.reasoning_consistency_checker import (
        apply_reasoning_corrections,
    )
    entry = {**_row("M2A", "2031 Example Investigator Award", confidence=0.55, hierarchy=HONORS),
             "classification_reasoning": "an investigator award is research funding (M2A)"}
    (out,), _ = apply_header_pin([entry], H_CTX)
    (corrected,), _ = apply_reasoning_corrections([{**out, "taxonomy_confidence": 0.95}], min_confidence=0.80)
    (status,), _ = apply_grant_status_corrections([corrected])
    assert out["taxonomy_code"] == corrected["taxonomy_code"] == status["taxonomy_code"] == "H"


# --- #1708: an unsure S8 with a PMID or DOI under an S1 pin ----------------------

S1_CTX = _ctx(meta=_node("BIBLIOGRAPHY", "S1"), section=_node("Example refereed articles", "S1"))
JOURNALS = ("BIBLIOGRAPHY", "Example refereed articles")
_ARTICLE = ("Quillforth JA, Bravantel BC. Example outcomes after example surgery: Conference Proceedings of the "
            "Example Planning Meeting 2031. Example J Med. 2032 41:210-226. PMID: 99000001")


def _s8(text, confidence=0.65):
    return _row("S8", text, confidence=confidence, hierarchy=JOURNALS)


def test_unsure_s8_journal_article_under_s1_becomes_s1():
    # The title names a meeting; the 17-page journal citation is what decides it.
    (out,), n = apply_header_pin([_s8(_ARTICLE)], S1_CTX)
    assert n == 1 and out["taxonomy_code"] == "S1" and out["pre_pin_code"] == "S8"


@pytest.mark.parametrize("ident", [
    "PMID 99000001",
    "PMID:\u00a099000001",
    "https://pubmed.ncbi.nlm.nih.gov/99000001/",
    "pmid: 99000001",
    "doi: 10.99999/ab12",
    "https://doi.org/10.99999/ab12",
])
def test_each_publication_id_shape_lets_the_s1_pin_take(ident):
    text = f"Quillforth JA. Example signals in tissue. Example J Med. 2031;12(3):340-52. {ident}"
    (out,), n = apply_header_pin([_s8(text)], S1_CTX)
    assert n == 1 and out["taxonomy_code"] == "S1"


@pytest.mark.parametrize("text", [
    "Quillforth JA. Example signals in tissue. Example J Med. 2031;12(3):340-52.",          # no PMID or DOI
    "Quillforth JA. Example signals in tissue. Example J Med. 2031;12(3):340-52. PMID pending",
    "Quillforth JA. Example signals. Example J Med. 2031;12:340. PMC 99000001",            # a PMCID alone
])
def test_s8_without_a_publication_id_stays(text):
    entry = _s8(text)
    (out,), n = apply_header_pin([entry], S1_CTX)
    assert n == 0 and out == entry


@pytest.mark.parametrize("text", [
    # A one- or two-page item is a meeting abstract, marker or not.
    "Quillforth JA. Example signals. Abstract presented at Example Society Meeting. Example J Med. 2031;12:340. PMID: 99000001",
    "Quillforth JA. Example signals (EX123). Example J Med. 2031, 12: 616-617. DOI: 10.99999/ex.2031.002",
    "Quillforth JA. Example signals. Example J Med. 2031;12:4418-9. PMID: 99000001",
    # No page range, and it names a meeting or a supplement.
    "Quillforth JA. Example signals. Example J Med. 2031;12 (Suppl 1). PMID: 99000001",
    "Quillforth JA. Example signals. Example J Med, 12 (7 Supplement) 81, 2031. PMID: 99000001",
    "Quillforth JA. Example signals. Poster, Example Society Meeting. DOI: 10.99999/ex.2031.003",
    "Quillforth JA. Example signals. Example Society Meeting abstracts. PMID: 99000001",
    "Quillforth JA. Example signals. Presented at the Example Society Meeting. PMID: 99000001",
])
def test_short_or_meeting_item_with_a_publication_id_stays_s8(text):
    entry = _s8(text)
    (out,), n = apply_header_pin([entry], S1_CTX)
    assert n == 0 and out == entry


@pytest.mark.parametrize("text", [
    # Three pages or more decide it even when the row says "abstract".
    "Quillforth JA. Example signals. Example J Med. 2031;12:340-342. Abstract not available. PMID: 99000001",
    "Quillforth JA. Example signals. In: Example Book. PP. 100-112. Abstract. DOI: 10.99999/ex.2031.004",
    "Quillforth JA. Example signals. Abstract. Example J Med. 2031, p.\u00a0100\u2014104. PMID: 99000001",
    "Quillforth JA. Example signals. Abstract. Example J Med. 2031;12:100 \u2013 102. PMID: 99000001",
    # The longest range decides: an erratum's two pages do not make the article short.
    "Quillforth JA. Example signals. Example J Med. 2031;12:100-112. Erratum: 2032;13:5-6. PMID: 99000001",
    # No page range and no meeting marker.
    "Quillforth JA. Example signals. Example J Med. 2031;12:e0001. PMID: 99000001",
    # A journal named "Proc <Society>" and a title's "Proceedings of" are not a proceedings venue.
    "Quillforth JA. Example signals. Proc Example Acad Sci. 2031;12(3):130-139. PMID: 99000001",
    "Quillforth JA. Proceedings of the Example Meeting. Example J Med. 2031;12:130-139. PMID: 99000001",
])
def test_journal_article_shapes_with_a_publication_id_become_s1(text):
    (out,), n = apply_header_pin([_s8(text)], S1_CTX)
    assert n == 1 and out["taxonomy_code"] == "S1"


@pytest.mark.parametrize("text", [
    "Quillforth JA. Example signals. Example Annu Symp Proc. 2031; 2031: 731-739. PMID: 99000001",
    "Quillforth JA. Example signals. Example Meeting Proceedings 2031, 2031: 604-12. PMID: 99000001",
    "Quillforth JA. Example signals. example symposium, 2031: 804-816. PMID: 99000001",
    "Quillforth JA. Example signals. Annu Int Conf Example Soc. 2031;2031:1-4. PMID: 99000001",
    "Quillforth JA. Example signals. Conf Proc Example Soc. 2031;2031:100-104. PMID: 99000001",
    "Quillforth JA. Example signals. Lecture Notes in Example Studies 2031, 9000: 811-825. DOI: 10.99999/ex.5",
])
def test_proceedings_venue_keeps_s8(text):
    entry = _s8(text)
    (out,), n = apply_header_pin([entry], S1_CTX)
    assert n == 0 and out == entry


def test_s1_pin_breaks_a_tie_and_does_not_overrule_a_firm_s8():
    (at_ceiling,), _ = apply_header_pin([_s8(_ARTICLE, confidence=0.80)], S1_CTX)
    assert at_ceiling["taxonomy_code"] == "S1"
    for confidence in (0.81, 0.90):
        firm = _s8(_ARTICLE, confidence=confidence)
        (out,), n = apply_header_pin([firm], S1_CTX)
        assert n == 0 and out == firm


def test_s8_with_no_text_under_s1_stays():
    entry = {**_s8(_ARTICLE), "text": None}
    (out,), n = apply_header_pin([entry], S1_CTX)
    assert n == 0 and out == entry


@pytest.mark.parametrize("code", ["S2", "S6", "S7", "R"])
def test_s1_pin_recodes_only_s8(code):
    entry = _row(code, _ARTICLE, confidence=0.55, hierarchy=JOURNALS)
    (out,), n = apply_header_pin([entry], S1_CTX)
    assert n == 0 and out == entry


@pytest.mark.parametrize("ctx", [
    _ctx(meta=_node("BIBLIOGRAPHY", "S1", HEADER_PIN_MIN_CONFIDENCE - 0.01)),
    _ctx(meta=_node("BIBLIOGRAPHY", "S8", 1.0), section=_node("Papers", "S1", 1.0)),   # mixed levels
    _ctx(meta=_node("ABSTRACTS", "S8")),
    _ctx(meta=_node("Example mixed heading", "S2", 0.6)),
])
def test_no_s1_pin_leaves_an_unsure_s8(ctx):
    entry = _s8(_ARTICLE)
    (out,), n = apply_header_pin([entry], ctx)
    assert n == 0 and out == entry


def test_s1_pin_is_not_flipped_back_by_the_reasoning_check():
    from unified_pipeline.core.validators.reasoning_consistency_checker import (
        apply_reasoning_corrections,
    )
    entry = {**_s8(_ARTICLE), "classification_reasoning": "conference proceedings of a meeting (S8)"}
    (out,), _ = apply_header_pin([entry], S1_CTX)
    (corrected,), _ = apply_reasoning_corrections([{**out, "taxonomy_confidence": 0.95}], min_confidence=0.80)
    assert out["taxonomy_code"] == corrected["taxonomy_code"] == "S1"


# --- EBYSBC E11 (#312): content pins --------------------------------------------

NO_PIN_CTX = _ctx(meta=_node("MISCELLANEOUS", "T", 0.4))


@pytest.mark.parametrize("code,text,hierarchy,expected", [
    ("K4", "2031 Example Society Annual Meeting, Springfield", ("CME Courses Attended",), "B2"),
    ("K4", "Attended the Example Review Course, 2031", ("Education",), "B2"),
    ("D3", "2031-2032 Intern, Example Medicine, Example School of Medicine", ("Appointments",), "C"),
    ("D3", "2031-2033 Junior and Senior Resident, Example Medicine, Example Hospital", ("Appointments",), "C"),
    ("D1", "Chief Resident, Example Medicine, Example Hospital", ("Appointments",), "C"),
    ("D3", "2031-2034 Postdoctoral Fellow in Example Medicine, Example University", ("Appointments",), "C"),
    ("D3", "2031\tPost-doctoral fellow\tExample University", ("Appointments",), "C"),
    ("D3", "2031-2033 Postdoctoral Research Fellow, Example University", ("Appointments",), "C"),
    ("D3", "2031-2033 Clinical Fellow, Example College of Medicine", ("Appointments",), "C"),
    ("D1", "2031-2033 Research Fellow in Example Medicine, Example School of Medicine", ("Appointments",), "C"),
    # A veterinary residency at a college or hospital is still training (#1581).
    ("D2", "2031-2033 Resident Veterinarian: Example Animal Hospital, Example College", ("Experience",), "C"),
    # A trainee row naming its supervisor is training, not a faculty-advisor role (#1581).
    ("D3", "2031-2033 Postdoctoral Fellow, Example University, Faculty Advisor: Dr. Doe", ("Appointments",), "C"),
    ("D3", "2031-2033 Postdoctoral Fellow (faculty advisor: Dr Doe), Example University", ("Appointments",), "C"),
    ("D1", "Clinical Fellow, Example Hospital, faculty advisors J Doe", ("Appointments",), "C"),
    ("D3", "2031 Intern, Example University (Advisor: Prof. Doe)", ("Appointments",), "C"),
    ("D3", "2031-2033 Postdoctoral Fellow, Example University, faculty advisor, J. Doe", ("Appointments",), "C"),
    ("K4", "Example Society Meeting, 2031 (attendee)", ("Teaching",), "B2"),
    ("K4", "Attendance at the Example Review Course, 2031", ("Teaching",), "B2"),
    ("F1", "ACLS certification, 2031-2033", ("Credentials",), "B2"),
    ("F1", "Basic Life Support provider card, 2031-2033", ("Licensure",), "B2"),
    ("F1", "BCLS certification, 2031-2033", ("Licensure",), "B2"),
    ("F1", "2031 Teacher certificate, Example State Department of Education", ("Licensure",), "B2"),
    ("F1", "Licensed as a teacher, Example State, 2031", ("Licensure",), "B2"),
    ("S2", "Wrote an invited commentary for Example Journal (see publication #12).", ("Honors",), "T"),
    ("S3", "Wrote an invited example chapter (publication #13)", ("Honors",), "T"),
])
def test_content_pins_recode_whatever_the_heading(code, text, hierarchy, expected):
    (out,), n = apply_header_pin([_row(code, text, hierarchy=hierarchy)], NO_PIN_CTX)
    assert n == 1 and out["taxonomy_code"] == expected and out["pre_pin_code"] == code


@pytest.mark.parametrize("code,text,hierarchy", [
    ("K4", "2031 Example Symposium (Course Director), Springfield", ("CME Courses Attended",)),
    ("K4", "2031 Example Workshop for practicing clinicians", ("Teaching",)),
    ("D1", "2031-present Faculty Fellow, Example Center", ("Appointments",)),
    ("D3", "2031-2034 Senior Staff Fellow, Example Institute", ("Appointments",)),
    ("D3", "Postdoctoral Research Mentor, Example Center, 2031", ("Experience",)),
    ("D1", "2031 Teaching Fellow, Example University", ("Appointments",)),
    # Owner-attended forms only: a physician "attending", an audience count, and a
    # bare "attendance" figure describe a course the owner taught (K4 stays).
    ("K4", "2031 Example lecture on valve disease (120 attendees)", ("Teaching",)),
    ("K4", "Example clinic attending for medical students, 2031", ("Teaching",)),
    ("K4", "2031 Example review course, course attendance 300", ("Teaching",)),
    # A training title on a row that is not a position keeps its code.
    ("H", "Resident Example Prize, Example Hospital, 2031", ("Honors",)),
    ("K4", "Fellow education day, Example Hospital, 2031", ("Teaching",)),
    # An honorific fellowship and a bare research-fellow appointment are not training.
    ("D1", "Fellow, Example College of Physicians, 2031", ("Appointments",)),
    ("D1", "2031 Fellow, the Example Academy of Sciences", ("Appointments",)),
    ("D1", "Fellow, Example Rhythm Society, 2031", ("Appointments",)),
    ("D3", "2031-2034 Research Fellow, Example Institute", ("Appointments",)),
    # Bare "CLS" is a laboratory scientist licence, not life support.
    ("F1", "Clinical Laboratory Scientist (CLS) License, Example State, 2031", ("Licensure",)),
    ("D3", "Example research fellow, Example Institute, 2031", ("Pregraduate research",)),
    ("H", "2031 Fellow of the Example College", ("Honors",)),
    ("D3", "2031 Fellow of the Example Society", ("Appointments",)),
    ("D3", "2031-present Senior Fellow, Example Policy Institute", ("Appointments",)),
    ("D3", "2031-present Senior Research Fellow, Example Policy Institute", ("Appointments",)),
    ("D1", "2031-2034 Resident Director, Example College House", ("Appointments",)),
    # A fellow title qualified as a faculty-advisor role, and a resident professional
    # outside a college, university, hospital or school, are positions (#1581).
    ("D1", "2031-present Fellow (undergraduate faculty advisor), Example College", ("Appointments",)),
    ("D1", "2031-present Fellow, faculty advisor to undergraduates, Example House", ("Appointments",)),
    ("D1", "2031-present Fellow, faculty advisor, Example House", ("Appointments",)),
    ("D1", "2031-present Fellow (resident faculty adviser), Example College", ("Appointments",)),
    ("D3", "Resident Veterinarian, Example Farm, Springfield, 2031-2033", ("Experience",)),
    ("F1", "Example State Medical License, Example Teaching Hospital, 2031", ("Licensure",)),
    ("F1", "Example State Medical License #12345, 2031", ("Licensure",)),
    ("S2", "Doe J. Example rhythms. Example Journal 2031;1:1-2.", ("Publications",)),
    ("N3B", "Example Student, Ph.D., 2031, Publication #15.", ("Mentees",)),
])
def test_content_pins_leave_other_rows(code, text, hierarchy):
    entry = _row(code, text, hierarchy=hierarchy)
    assert content_pin_code(entry) is None
    (out,), n = apply_header_pin([entry], NO_PIN_CTX)
    assert n == 0 and out == entry


@pytest.mark.parametrize("code,text,ctx", [
    ("S8", '"Signals in an example tissue." Example Society Meeting, 2031.', R_CTX),
    ("Q2", "2031 Ad hoc reviewer, Example Research Council", GRANT_CTX),
    ("K4", "Attended the Example Review Course, 2031 (professional education K4)", NO_PIN_CTX),
    ("D3", "2031-2032 Intern, Example Medicine, Example Hospital", NO_PIN_CTX),
    ("F1", "Basic Life Support provider card, 2031-2033", NO_PIN_CTX),
    ("S2", "Wrote an invited example article (see publication #12).", NO_PIN_CTX),
    ("S1", "2. Quorlenby et al. (2031) Example Membrane Letters JIF: 9.9 cited 3 times", NO_PIN_CTX),
    ("K1", '"I learned so much in this course; the lectures were clear."', NO_PIN_CTX),
])
def test_no_new_pin_is_flipped_back_by_the_reasoning_check(code, text, ctx):
    from unified_pipeline.core.validators.reasoning_consistency_checker import (
        apply_reasoning_corrections,
    )
    hierarchy = ("SECTION", "Example grant review", "Student Evaluations")
    (out,), n = apply_header_pin([_row(code, text, hierarchy=hierarchy)], ctx)
    assert n == 1
    (corrected,), _ = apply_reasoning_corrections([{**out, "taxonomy_confidence": 0.95}], min_confidence=0.80)
    assert corrected["taxonomy_code"] == out["taxonomy_code"]


def test_header_pin_wins_over_a_content_pin():
    entry = _row("K4", "Attended: example workshop", hierarchy=("INVITED PRESENTATIONS",))
    (out,), _ = apply_header_pin([entry], R_CTX)
    assert out["taxonomy_code"] == "R"


def test_fallback_entries_get_no_content_pin():
    entry = {**_row("F1", "Basic Life Support, 2031"), "classification_source": "fallback"}
    (out,), n = apply_header_pin([entry], NO_PIN_CTX)
    assert n == 0 and out == entry


# --- #1731: a chief resident or chief fellow is C, not a position or leadership role ---

@pytest.mark.parametrize("code,text,hierarchy", [
    # NJHLUR 25: D1/D3 in all four fix rolls; the non-breaking hyphen hid the date.
    ("D1", "2031‑32 Chief Resident B Example Medicine, Example Hospital, Example Center",
     ("ACADEMIC APPOINTMENTS",)),
    ("D3", "2031‑32 Chief Resident B Example Medicine, Example Hospital, Example Center",
     ("ACADEMIC APPOINTMENTS",)),
    ("D2", "2031‑32 Chief Resident B Example Medicine, Example Hospital, Example Center",
     ("ACADEMIC APPOINTMENTS",)),
    # IO4DEA 190.1: "Chief Residency" is not the trainee pin's "resident" word.
    ("D2", "Chief Residency, Example Medicine | Example Cancer Center\nExample City | 07/2031-06/2032",
     ("PERSONAL DATA",)),
    # web40 173: under an institutional-service heading the model answered O.
    ("O", "2031 – 2032 Chief Resident – Department of Example Medicine",
     ("Administrative Service", "Institutional Service")),
    ("L3", "July 2031 - June 2032 Chief Resident, Example Clinic, Example Hospital", ("Leadership",)),
    ("O", "Chief Fellow, Example Fellowship Program, 2031-2032", ("Leadership",)),
    ("D2", "• 2031-present: chief resident, Example Hospital", ("Appointments",)),
    # A month/year range; O, so the older trainee pin (D1/D2/D3 only) cannot answer first.
    ("O", "07/2031 – 07/2032 Chief Resident, Example Department", ("Leadership",)),
])
def test_chief_trainee_line_is_recoded_to_c_1731(code, text, hierarchy):
    entry = _row(code, text, confidence=0.75, hierarchy=hierarchy)
    assert content_pin_code(entry)[0] == "C"
    (out,), n = apply_header_pin([entry], NO_PIN_CTX)
    assert n == 1 and out["taxonomy_code"] == "C" and out["pre_pin_code"] == code


@pytest.mark.parametrize("code,text", [
    # RVOPTG 60: the staff appointment opens the line; Chief Resident comes later.
    ("D2", "2031-2032 Attending Staff Physician and Chief Resident, Example Hospital"),
    ("L3", "Chief, Mental Hygiene Clinic, Example Hospital, 2031-2032"),
    ("O", "Chief, Example Residency Program, 2031-2032"),
    ("O", "Chief Residents Council, Example Hospital, 2031"),
    # A committee line keeps its P: P is outside the overridable codes.
    ("P", "2031-2032 Chief Residency Selection Committee, Example Hospital"),
    # A code outside D1/D2/D3/L3/O keeps the model's answer.
    ("H", "2031 Chief Resident, Example Hospital"),
    ("K4", "2031 - 2032 Chief Resident Rounds, 8 interns, 1 hour/day"),
])
def test_chief_trainee_pin_leaves_other_lines_1731(code, text):
    entry = _row(code, text, confidence=0.75)
    assert content_pin_code(entry) is None
    (out,), n = apply_header_pin([entry], NO_PIN_CTX)
    assert n == 0 and out == entry


def test_chief_trainee_pin_is_not_flipped_back_by_the_reasoning_check_1731():
    from unified_pipeline.core.validators.reasoning_consistency_checker import (
        apply_reasoning_corrections,
    )
    (out,), n = apply_header_pin([_row("O", "2031 – 2032 Chief Resident – Example Medicine")], NO_PIN_CTX)
    assert n == 1
    (corrected,), _ = apply_reasoning_corrections([{**out, "taxonomy_confidence": 0.95}], min_confidence=0.80)
    assert corrected["taxonomy_code"] == "C"


# --- #1695: a per-article citation-metrics line is S0 -----------------------------

_VENUE = "Example Membrane Letters"


@pytest.mark.parametrize("code,text", [
    ("S1", f"1. Quorlenby et al. (2031) {_VENUE} JIF: 7.071 cited 12 times"),
    ("S2", f"12) Vashtrel et al., 2031b, {_VENUE}, JIF 4.1"),
    ("S1", f"\u2022 Pemberwick and Zandquist (2031) {_VENUE} impact factor 3.2"),
    ("Q4C", f"Pemberwick & Zandquist (2031): {_VENUE} IF: 3.2"),
    ("Q4D", f"Pemberwick et al. (2031) {_VENUE} JIF: 3.2 cited 2 times"),
    ("Q4", f"Pemberwick et al. (2031) {_VENUE} JIF: 3.2"),
    ("S1", f"- Quorlenby et al. (2031) {_VENUE} IF=3.2"),
    ("S1", f"* Quorlenby et al. (2031) {_VENUE} Impact Factor 3.2"),
    ("S7", f"Thornvalle-Quorlenby et al. 2031. {_VENUE} cited 1,204 times"),
    ("S1", f"Van Quorlenby et al. (2031) {_VENUE} times cited 9"),
    ("S1", f"De La Quorlenby et al. (2031) {_VENUE} JIF: 2.0"),
    ("S1", f"Vashtrel, et al. (2031) {_VENUE} Citations: 4"),
    ("S1", f"O\u2019Vashtrel et al. (2031) {_VENUE} Citation= 4"),
    ("S1", f"D'Vashtrel et al (2031) {_VENUE} JIF: 1.0"),
    ("S1", f"Quorlenby (2031) {_VENUE} JIF: 1.2"),
    ("S2", f"Quorlenby et al. (1987) {_VENUE} JIF: 1.2"),
    # Eight words of venue (the "&" is not a word) is still a journal name.
    ("S1", "Quorlenby et al. (2031) Example Membrane & Letters of Science and Light Today JIF: 1.0"),
])
def test_metrics_stub_is_recoded_to_s0(code, text):
    # A firm 0.95 answer is recoded too: the shape, not the model's doubt, decides it.
    (out,), n = apply_header_pin([_row(code, text, confidence=0.95)], NO_PIN_CTX)
    assert n == 1 and out["taxonomy_code"] == "S0" and out["pre_pin_code"] == code


def test_metrics_stub_under_a_pinned_journals_heading_is_s0():
    stub = _row("S1", f"3. Quorlenby et al. (2031) {_VENUE} JIF: 9.9 cited 3 times", 0.95, JOURNALS)
    (out,), n = apply_header_pin([stub], S1_CTX)
    assert n == 1 and out["taxonomy_code"] == "S0"


@pytest.mark.parametrize("code,text", [
    # A full citation that also prints its impact factor keeps the model's code.
    ("S1", f"Quorlenby A, Vashtrel B. Signals in an example membrane. {_VENUE}. 2031;4:12-19. (IF=3.2)"),
    ("S1", f"Quorlenby et al. (2031) Signals in an example membrane under light. {_VENUE} 4:12 JIF: 3.2"),
    # A short title is told apart from a journal name by the volume and pages after it.
    ("S1", f"Quorlenby et al. (2031). Membrane signals. {_VENUE} 4:12-19. JIF: 3.2"),
    ("S1", f"Quorlenby et al. (2031) Signals in an example membrane under polarised light. {_VENUE} JIF: 3.2"),
    ("S1", "Quorlenby et al. (2031) Example Membrane & Letters of Science and Light Today Online JIF: 1.0"),
    ("S1", f"Quorlenby et al. (2031) {_VENUE}"),
    ("Q4C", f"2031- Editor, {_VENUE}. IF: 3.2"),
    ("Q4D", f"Associate Editor, 2031-present, {_VENUE} (Impact Factor: 3.2)"),
    ("Q4C", f"Section Editor, 2031\u2013Present, {_VENUE} (JIF 5.1)"),
    ("Q1", f"Quorlenby et al. (2031) {_VENUE} JIF: 9.9"),
    ("H", f"Quorlenby et al. (2031) {_VENUE} JIF: 9.9"),
    ("S0", f"Quorlenby et al. (2031) {_VENUE} JIF: 9.9"),
])
def test_metrics_pin_leaves_other_rows(code, text):
    entry = _row(code, text)
    assert content_pin_code(entry) is None or content_pin_code(entry)[0] == code
    (out,), n = apply_header_pin([entry], NO_PIN_CTX)
    assert n == 0 and out == entry


def test_metrics_pin_reads_no_code_as_no_answer():
    entry = _row(None, f"Quorlenby et al. (2031) {_VENUE} JIF: 9.9")
    assert content_pin_code(entry) is None


# --- #1695 part 2: a student evaluation of the owner's teaching is T ----------------

STUDENT_EVALUATIONS = ("Appendix III Student Evaluations",)


def test_quoted_student_comments_under_student_evaluations_become_t():
    group = [_row("K1", text, hierarchy=STUDENT_EVALUATIONS) for text in (
        '"Dr. Example made the material clear. Best instructor I have had."',
        '"I learned so much in this course."',
        '"Great teaching! I would have liked to be challenged slightly more."',
    )]
    out, n = apply_header_pin(group, NO_PIN_CTX)
    assert n == 3 and all(e["taxonomy_code"] == "T" and e["pre_pin_code"] == "K1" for e in out)


@pytest.mark.parametrize("code,text,hierarchy", [
    ("K1", "The course was effectively organized 9", (*STUDENT_EVALUATIONS, "Student Self-Evaluation")),
    ("K4", "N802 Example Grant Writing", STUDENT_EVALUATIONS),
    ("K1", "EX305 Example Statistics STUDENT SATISFACTION EVALUATIONS - Fall 2031 24 .36 4.38",
     ("Course Evaluations",)),
    ("K1", "Teaching evaluations from 2031-present: overall instructor rating 4.56/5.00.",
     ("Teaching Awards and Evaluations",)),
    ("K1", "Student evaluations for Example Course averaged 4.7/5.", ("Courses Taught and Student Evaluations",)),
    ("K1", "Example Course, 2031: mean 4.7", ("Appendix B: Teaching Evaluations",)),
    ("K1", "Example Course, 2031: mean 4.7", ("Student Course Evaluations",)),
    ("K2", "Resident rating of bedside teaching, 2031: 4.8/5", ("Students' Evaluations of Teaching",)),
    ("K1", "Example Course, 2031: mean 4.7", ("TEACHING", "Evaluations of my teaching")),
])
def test_teaching_code_under_an_evaluations_heading_becomes_t(code, text, hierarchy):
    (out,), n = apply_header_pin([_row(code, text, confidence=0.75, hierarchy=hierarchy)], NO_PIN_CTX)
    assert n == 1 and out["taxonomy_code"] == "T" and out["pre_pin_code"] == code


@pytest.mark.parametrize("code,text,hierarchy", [
    # An evaluations committee or policy is service; a singular "evaluation" heading
    # names an activity the owner may lead, not a survey of students.
    ("K3", "Chair, 2031-present", ("Teaching Evaluations Committee",)),
    ("K3", "Member, 2031", ("Course Evaluations Policy",)),
    ("K3", "Teaching evaluations redesign working group, chair, 2031", ("Teaching Evaluations Committee",)),
    ("K3", "Led redesign of the example clerkship assessment, 2031", ("Curriculum and Course Evaluation",)),
    ("K1", "Example Course, Lecturer, 2031", ("Teaching",)),
    ("K1", "Example Course, Lecturer, 2031", ("Teaching", "Self-Evaluation")),
    # Only a teaching code moves: an award under the same heading stays the model's.
    ("H", "Example Teaching Award, 2031", ("Teaching Awards and Evaluations",)),
    ("P", "Member, Teaching Evaluations Committee, 2031", ("Service",)),
    ("T", '"A great course."', STUDENT_EVALUATIONS),
])
def test_evaluations_pin_leaves_other_rows(code, text, hierarchy):
    entry = _row(code, text, hierarchy=hierarchy)
    assert content_pin_code(entry) is None
    (out,), n = apply_header_pin([entry], NO_PIN_CTX)
    assert n == 0 and out == entry


@pytest.mark.parametrize("heading", [
    "Courses Taught and Student Evaluations",
    "Teaching Activities and Evaluations",
    "Teaching and Evaluations",
    "Teaching Awards and Evaluations",
    "Student Evaluations and Courses Taught",
])
@pytest.mark.parametrize("code,text", [
    ("K1", "Example Pharmacology 101, Course Director, 2031-2033"),
    ("K1", "EX305 Example Statistics, Lecturer, Fall 2031 (evaluations 4.6/5)"),
    ("K2", "Example Clerkship, attending preceptor for third-year students, 2031"),
])
def test_course_rows_under_a_combined_evaluations_heading_stay_teaching(heading, code, text):
    # A combined heading lists the courses as well as their evaluations: a course
    # with its role and year, even one quoting its score, is still a course taught.
    entry = _row(code, text, confidence=0.75, hierarchy=(heading,))
    assert content_pin_code(entry) is None
    (out,), n = apply_header_pin([entry], NO_PIN_CTX)
    assert n == 0 and out == entry


def test_evaluations_pin_applies_under_a_pinned_teaching_header():
    # A K1-pinned "TEACHING" header does not override K, so the header pin passes
    # and the content pin still decides: the evaluation moves, the course stays.
    teaching_ctx = _ctx(meta=_node("TEACHING", "K1", 1.0), section=_node("Courses and Student Evaluations", "K1", 1.0))
    assert pinned_header_code(teaching_ctx) == "K1"
    hierarchy = ("TEACHING", "Courses and Student Evaluations")
    course = _row("K1", "Example Pharmacology 101, Lecturer, 2031", confidence=0.75, hierarchy=hierarchy)
    summary = _row("K1", "Student evaluations for 2031 averaged 4.6/5.", confidence=0.75, hierarchy=hierarchy)
    appendix = _row("K1", '"Best instructor I have had."', confidence=0.75,
                    hierarchy=("TEACHING", "Appendix III Student Evaluations"))
    out, n = apply_header_pin([course, summary, appendix], teaching_ctx)
    assert n == 2
    assert [e["taxonomy_code"] for e in out] == ["K1", "T", "T"]
    assert out[0] == course and out[1]["pre_pin_code"] == "K1" and out[2]["pre_pin_code"] == "K1"


# --- #1251: a mentee's accomplishments are N4 ----------------------------------

STUDENT_HEADING = ("STUDENT ACCOMPLISHMENTS",)


def _mentee_group(*rows):
    """A named-mentee line followed by `rows` (code, text), all under STUDENT_HEADING."""
    mentee = _row("N3B", "Alex Example (PhD Committee Chair):", hierarchy=STUDENT_HEADING)
    return [mentee, *(_row(code, text, hierarchy=STUDENT_HEADING) for code, text in rows)]


def test_awards_after_a_named_mentee_become_n4():
    group = _mentee_group(("H", "Awarded the Example Foundation Dissertation Fellowship (2031)"),
                          ("K5", "2031 Example Leadership Program Participant"),
                          ("K4", "Example Transdisciplinary Training Program, 2031"))
    out, n = apply_header_pin(group, NO_PIN_CTX)
    assert n == 3
    assert [e["taxonomy_code"] for e in out] == ["N3B", "N4", "N4", "N4"]
    assert [e.get("pre_pin_code") for e in out[1:]] == ["H", "K5", "K4"]


def test_a_student_awards_heading_with_no_mentee_line_stays_the_owners():
    # web210 "Student/Trainee Awards": the owner's own awards from training.
    group = [_row("H", text, hierarchy=("Student/Trainee Awards",))
             for text in ("Example University Doctoral Fellowship, 2031", "Second Prize, Example Essay Contest, 2030")]
    out, n = apply_header_pin(group, NO_PIN_CTX)
    assert n == 0 and out == group


def test_rows_before_the_mentee_line_are_left_alone():
    first = _row("H", "Example Society Award, 2031", hierarchy=STUDENT_HEADING)
    out, n = apply_header_pin([first, *_mentee_group()], NO_PIN_CTX)
    assert n == 0 and out[0]["taxonomy_code"] == "H"


@pytest.mark.parametrize("code,heading", [
    # 2082_Dr_Scot: each row names its own student; no mentee line above.
    ("H", ("Teaching", "Undergraduate Honor Students’ Achievements")),
    ("D1", ("Teaching", "Undergraduate Honor Students’ Achievements")),
    # 2068_Yount_Cv: a doctoral student's paper award.
    ("H", ("TEACHING", "TEACHING AND MENTORING", "MASTERS STUDENT TRAINEES")),
    # 2068_Yount_Cv 975: a singular leaf under a plural parent.
    ("H", ("TEACHING", "TEACHING AND MENTORING", "DOCTORAL TRAINEES (current and completed)",
           "Doctoral Student Paper Awards")),
    ("H", ("Mentees",)),
    ("H", ("Advisees",)),
    ("H", ("Fellows mentored",)),
    # farm 2082 621 (#1573): a student's degree and law school, not the owner's education.
    ("B1", ("Teaching", "Undergraduate Honor Students’ Achievements: (NIU Only)")),
])
def test_a_plural_mentee_heading_pins_without_a_mentee_line(code, heading):
    entry = _row(code, "Example Student, 2031. Winner of the Example Thesis Prize.", hierarchy=heading)
    assert mentee_pin_code(entry, after_mentee=False)[0] == "N4"


@pytest.mark.parametrize("code,heading", [
    ("H", ("Student/Trainee Awards",)),                  # singular: the owner's own (web210)
    ("D1", ("MENTORING", "Trainees")),                   # a position needs an accomplishments word
    ("K1", ("Teaching", "Medical Students")),            # the owner's teaching of students
    ("S1", ("Doctoral Students",)),                      # a publication stays a publication
    # The owner's teaching award: the plural names the people taught.
    ("H", ("TEACHING", "Report of Local Teaching and Training", "Teaching of Students in Courses")),
    # A plural above the row's parent is a section title, not the row's owner.
    ("H", ("Trainees", "Program Leadership", "Awards")),
    # The owner's own degree: a singular, a teaching level, or a plural too far up (#1573).
    ("B1", ("Education", "Student Awards and Degrees")),
    ("B1", ("Teaching", "Teaching of Medical Students")),
    ("B1", ("Trainees", "Education", "Doctoral Degree")),
])
def test_a_plural_mentee_heading_keeps_the_other_guards(code, heading):
    assert mentee_pin_code(_row(code, "Example row, 2031", hierarchy=heading), after_mentee=False) is None


@pytest.mark.parametrize("code,heading", [
    ("K5", ("ADVISING", "Undergraduate Research Students")),   # teaching, no accomplishments word
    ("D1", ("MENTORING", "Trainees")),
    ("S1", STUDENT_HEADING),                                     # a publication stays a publication
    ("H", ("PROFESSIONAL ACCOMPLISHMENTS",)),                    # no mentee word in the heading
    ("B1", STUDENT_HEADING),                                     # education needs the plural level (#1573)
])
def test_mentee_pin_needs_both_the_heading_and_the_code(code, heading):
    entry = _row(code, "Example row, 2031", hierarchy=heading)
    assert mentee_pin_code(entry, after_mentee=True) is None


def test_a_fallback_mentee_line_still_opens_the_group():
    mentee = {**_row("N3A", "Alex Example (advisee):", hierarchy=STUDENT_HEADING), "classification_source": "fallback"}
    award = _row("H", "Example Student Research Award, 2031", hierarchy=STUDENT_HEADING)
    out, n = apply_header_pin([mentee, award], NO_PIN_CTX)
    assert n == 1 and out[1]["taxonomy_code"] == "N4"


def test_a_named_residents_project_award_under_research_becomes_n4():
    # KDAZOM "Medical Residency Research": the line opens with the resident's name and degree.
    entry = _row("H", 'Jordan Example, M.D., Dermatology: "An example assay." 2nd place, resident competition, 2031',
                 hierarchy=("Medical Residency Research",))
    (out,), n = apply_header_pin([entry], NO_PIN_CTX)
    assert n == 1 and out["taxonomy_code"] == "N4"


@pytest.mark.parametrize("text,heading", [
    ("Jordan Example, MD Faculty Teaching Award, 2031", ("HONORS AND AWARDS",)),   # eponymous award
    ("Jordan Example, MD Faculty Teaching Award, 2031", ("Research Awards",)),
    ("Example Society Young Investigator Award, 2031", ("Medical Residency Research",)),
    ("Jordan Example, B.S. with Distinction, 2031", ("PUBLICATIONS",)),
])
def test_named_lead_needs_a_research_heading_without_an_honors_word(text, heading):
    assert mentee_pin_code(_row("H", text, hierarchy=heading), after_mentee=False) is None


def test_mentee_pin_is_not_flipped_back_by_the_reasoning_check():
    from unified_pipeline.core.validators.reasoning_consistency_checker import (
        apply_reasoning_corrections,
    )
    out, _ = apply_header_pin(_mentee_group(("H", "Recipient of the Example Memorial Scholarship")), NO_PIN_CTX)
    (corrected,), _ = apply_reasoning_corrections([{**out[1], "taxonomy_confidence": 0.95}], min_confidence=0.80)
    assert corrected["taxonomy_code"] == "N4"


def test_header_pin_wins_over_the_mentee_pin():
    entry = _row("H", "Example Society Award, 2031", hierarchy=STUDENT_HEADING)
    out, _ = apply_header_pin([*_mentee_group(), entry], R_CTX)
    assert out[-1]["taxonomy_code"] == "R"


# --- #1710: a grant under the owner's mentor or sponsor role is N2 ---------------

MENTEE_GRANT = "Example Institute (F32EX000001) Example trainee project. PI: Alex Example. Role: Mentor. 2031-2033"


@pytest.mark.parametrize("hierarchy", [
    ("GRANT SUPPORT", "Grant Support as Mentor"),                          # UAURFU shape
    ("GRANTS/FELLOWSHIPS", "Previous", "As sponsor or primary mentor"),     # LWMSPJ shape
    ("Grant History", "Grants as Mentor"),
    ("FUNDING", "Grants as Primary Mentor"),
    ("FUNDING", "Grants as Primary  Mentor"),
    ("FUNDING", "Grants as Co-Mentor"),
    ("FUNDING", "Grants as Co Mentor"),
    ("FUNDING", "Grants as Comentor"),
    ("FUNDING", "Grants as Sponsor"),
    ("FUNDING", "Grants as  Mentor"),
    ("GRANT SUPPORT", "GRANTS, FELLOWSHIPS", "Mentor or Co-mentor"),        # 2087 shape
    ("GRANT SUPPORT", " Sponsor: "),
    ("GRANT SUPPORT", "Mentor or Sponsor or Co-mentor"),
    ("GRANT SUPPORT", "Mentor or  Co-mentor"),
    ("GRANT SUPPORT", "Mentor and Sponsor"),
    ("Grant Support as Mentor", "Current"),                                # any level names the role
])
@pytest.mark.parametrize("code", ["M2", "M2A", "M2B", "M2C"])
def test_a_grant_under_a_mentor_role_heading_becomes_n2(code, hierarchy):
    entry = _row(code, MENTEE_GRANT, confidence=0.9, hierarchy=hierarchy)
    (out,), n = apply_header_pin([entry], NO_PIN_CTX)
    assert n == 1 and out["taxonomy_code"] == "N2" and out["pre_pin_code"] == code
    assert out["classification_reasoning"].startswith("A grant listed under the owner's mentor or sponsor role")


@pytest.mark.parametrize("hierarchy", [
    ("GRANT SUPPORT", "Grant Support as Investigator"),
    ("GRANTS", "As Principal Investigator"),
    ("GRANT SUPPORT", "Advisor and Mentor"),            # 2087: the group also holds the owner's Co-I grants
    ("GRANT SUPPORT", "MENTORED GRANTS"),               # BYFQBG: the owner's own grants sit above it
    ("Career Awards as Mentored Investigator",),        # the owner's own K award
    ("SPONSORED RESEARCH", "Completed (Funded)"),
    ("GRANT SUPPORT", "Extramural Sponsorship"),
    ("PROFESSIONAL ACTIVITIES", "Co-Investigator or Sponsor on Grants"),
    ("GRANT SUPPORT", "Overseas Sponsor Grants"),
    ("GRANT SUPPORT", "Mentor or Co-mentor Awards"),
    ("FUNDING", "Grants as Advisor"),
    ("GRANT SUPPORT", "Industry Sponsor"),               # a funder, not the owner's role
    ("GRANT SUPPORT", "Mentor Program Sponsor"),
    ("FUNDING", "Grants as Primary Investigator (Mentor)"),   # the owner's own grants
    ("FUNDING", "Office of Mentor Development"),                # "of", not "as"
    ("GRANT SUPPORT", "Mentor and Co-Investigator"),            # mixed: the owner's Co-I grants too
])
def test_a_grant_under_another_heading_keeps_its_code(hierarchy):
    entry = _row("M2B", MENTEE_GRANT, confidence=0.9, hierarchy=hierarchy)
    (out,), n = apply_header_pin([entry], NO_PIN_CTX)
    assert n == 0 and out == entry


def test_a_mentee_row_under_a_mentor_heading_is_left_alone():
    entry = _row("N3B", "Alex Example, PhD student, 2031-2033", hierarchy=("MENTORING", "As Primary Mentor"))
    (out,), n = apply_header_pin([entry], NO_PIN_CTX)
    assert n == 0 and out == entry


def test_header_pin_wins_over_the_mentor_grant_pin():
    # An honors heading's "Awards as Mentor" lists the owner's own mentoring awards.
    entry = _row("M2B", "Example Society Outstanding Mentor Award", hierarchy=("HONORS", "Awards as Mentor"))
    (out,), _ = apply_header_pin([entry], _ctx(meta=_node("HONORS", "H")))
    assert out["taxonomy_code"] == "H"


def test_mentor_grant_pin_is_not_flipped_back_by_the_reasoning_check():
    from unified_pipeline.core.validators.reasoning_consistency_checker import (
        apply_reasoning_corrections,
    )
    entry = _row("M2B", MENTEE_GRANT, confidence=0.9, hierarchy=("GRANT SUPPORT", "Grant Support as Mentor"))
    (out,), _ = apply_header_pin([entry], NO_PIN_CTX)
    (corrected,), _ = apply_reasoning_corrections([{**out, "taxonomy_confidence": 0.95}], min_confidence=0.80)
    assert corrected["taxonomy_code"] == "N2"


# --- is_note_not_record ---------------------------------------------------------

@pytest.mark.parametrize("text", [
    "http://example.org/program.html",
    "  www.example.org/about  ",
    "Mentoring Prizes - see section 7",
    "Example label: (see above)",
])
def test_notes_that_are_not_records(text):
    assert is_note_not_record(text)


def test_running_header_is_a_note_only_against_the_opening_line():
    assert is_note_not_record("Jane Example, MD", opening_line="Jane Example, MD")
    assert not is_note_not_record("Jane Example, MD", opening_line="Curriculum Vitae")
    assert not is_note_not_record("Jane Example, MD")


@pytest.mark.parametrize("text", [
    "",
    "2031-33 Example Foundation grant (see above), total $1,000.",
    "Wrote an invited commentary for Example Journal (see publication #12).",
    "Example Society, see http://example.org for details",
])
def test_records_with_a_pointer_or_url_inside_are_not_notes(text):
    assert not is_note_not_record(text)


# --- #1413: a journal after a reviewer lead-in in an earlier batch is Q4D -------

EDITORIAL_HEADING = ("EDITORIAL ACTIVITIES",)
REVIEWER_LEAD_IN = "Reviewer for the following journals and conferences:"


def _reviewer_group(*rows, lead_in=REVIEWER_LEAD_IN, hierarchy=EDITORIAL_HEADING):
    """A lead-in line, one Q4D journal, then `rows` (code, text, confidence)."""
    return [
        _row("Q4D", lead_in, confidence=0.85, hierarchy=hierarchy),
        _row("Q4D", "Example Journal of Medicine", confidence=0.95, hierarchy=hierarchy),
        *(_row(code, text, confidence=conf, hierarchy=hierarchy) for code, text, conf in rows),
    ]


# Two entries per call: the lead-in and the first journal went in call 1, the rows after them later.
_TWO_PER_CALL = 2


def test_unsure_board_answer_after_an_unseen_reviewer_lead_in_becomes_q4d():
    group = _reviewer_group(("Q4C", "Example Health Services Review", 0.5),
                            ("Q4C", "The Example Journal of Care", 0.5))
    out, n = apply_header_pin(group, NO_PIN_CTX, batch_size=_TWO_PER_CALL)
    assert n == 2
    assert [e["taxonomy_code"] for e in out] == ["Q4D", "Q4D", "Q4D", "Q4D"]
    assert [e.get("pre_pin_code") for e in out[2:]] == ["Q4C", "Q4C"]
    assert all(e["classification_source"] == "llm" for e in out)


def test_reviewer_pin_needs_the_batch_size():
    # No batch size: the group went to the model in one call, which held the lead-in.
    group = _reviewer_group(("Q4C", "Example Health Services Review", 0.5))
    out, n = apply_header_pin(group, NO_PIN_CTX)
    assert n == 0 and out == group


def test_board_answer_in_the_lead_ins_own_batch_stays():
    group = _reviewer_group(("Q4C", "Example Health Services Review", 0.5))
    out, n = apply_header_pin(group, NO_PIN_CTX, batch_size=len(group))
    assert n == 0 and out == group


@pytest.mark.parametrize("row", [
    ("Q4C", "Example Health Services Review", 0.9),                # a confident answer
    ("Q4C", "Associate Editor, Example Health Services Review", 0.5),  # names a role
    ("Q4C", "Example Society editorial board", 0.5),
    ("Q4A", "Example Health Services Review", 0.5),                # not the board code
])
def test_reviewer_pin_leaves_a_firm_or_role_naming_answer(row):
    group = _reviewer_group(row)
    out, n = apply_header_pin(group, NO_PIN_CTX, batch_size=_TWO_PER_CALL)
    assert n == 0 and out == group


def test_reviewer_pin_leaves_rows_under_a_board_heading():
    group = _reviewer_group(("Q4C", "Example Health Services Review", 0.5),
                            hierarchy=("Editorial Boards",))
    out, n = apply_header_pin(group, NO_PIN_CTX, batch_size=_TWO_PER_CALL)
    assert n == 0 and out == group


def test_a_confident_non_editorial_row_ends_the_reviewer_list():
    group = _reviewer_group(("I", "Example Society of Medicine", 0.9),
                            ("Q4C", "Example Health Services Review", 0.5))
    out, n = apply_header_pin(group, NO_PIN_CTX, batch_size=_TWO_PER_CALL)
    assert n == 0 and out == group


def test_an_unsure_non_editorial_row_does_not_end_the_reviewer_list():
    # GINAAP coded "Society for Medical Decision Making" I at 0.6 inside the list.
    group = _reviewer_group(("I", "Example Society of Medicine", 0.6),
                            ("Q4C", "Example Health Services Review", 0.5))
    out, n = apply_header_pin(group, NO_PIN_CTX, batch_size=_TWO_PER_CALL)
    assert n == 1 and out[-1]["taxonomy_code"] == "Q4D" and out[2] == group[2]


def test_a_confident_board_heading_row_ends_the_reviewer_list():
    # PR #1830 review: the board heading is a row, not a lead-in; it is Q4 so it
    # does not leave the editorial family, yet the journal after it is a board seat.
    group = [
        _row("Q4D", "Reviewer for the following journals:", confidence=0.85, hierarchy=EDITORIAL_HEADING),
        _row("Q4D", "Journal A", confidence=0.95, hierarchy=EDITORIAL_HEADING),
        _row("Q4C", "Editorial Board Memberships", confidence=0.9, hierarchy=EDITORIAL_HEADING),
        _row("Q4C", "Journal B", confidence=0.7, hierarchy=EDITORIAL_HEADING),
    ]
    out, n = apply_header_pin(group, NO_PIN_CTX, batch_size=_TWO_PER_CALL)
    assert n == 0 and out == group


@pytest.mark.parametrize("row", [
    # Reads as a heading, no role word: one row per section noun.
    *(("Q4D", f"Journal Service {noun}", 0.9) for noun in (
        "Memberships", "Activities", "Positions", "Roles", "Appointments", "Services", "Responsibilities.")),
    ("Q4D", "Associate Editor, Example Review", 0.9),  # names a role
])
def test_a_confident_editorial_row_naming_a_role_or_heading_ends_the_reviewer_list(row):
    group = _reviewer_group(row, ("Q4C", "Example Health Services Review", 0.5))
    out, n = apply_header_pin(group, NO_PIN_CTX, batch_size=_TWO_PER_CALL)
    assert n == 0 and out == group


@pytest.mark.parametrize("row", [
    ("Q4D", "Example Journal of Care", 0.95),          # a confident journal name: still the list
    ("Q4D", "Health Services Research", 0.95),         # a section noun, but not the last word
    ("Q4C", "Editorial Board Memberships", 0.6),       # an unsure heading row changes nothing
    ("Q4D", "Journal Service Activities", 0.8),        # at the gate is not above it
])
def test_a_plain_or_unsure_editorial_row_keeps_the_reviewer_list(row):
    group = _reviewer_group(row, ("Q4C", "Example Health Services Review", 0.5))
    out, n = apply_header_pin(group, NO_PIN_CTX, batch_size=_TWO_PER_CALL)
    assert n == 1 and out[-1]["taxonomy_code"] == "Q4D"


def test_a_fallback_row_does_not_end_the_reviewer_list():
    # Not a model answer: its confidence says nothing about where the list ends.
    fallback = {**_row("I", "Example Society of Medicine", confidence=0.9, hierarchy=EDITORIAL_HEADING),
                "classification_source": "fallback"}
    group = [*_reviewer_group(), fallback,
             _row("Q4C", "Example Health Services Review", confidence=0.5, hierarchy=EDITORIAL_HEADING)]
    out, n = apply_header_pin(group, NO_PIN_CTX, batch_size=_TWO_PER_CALL)
    assert n == 1 and out[-1]["taxonomy_code"] == "Q4D"


def test_another_lead_in_ends_the_reviewer_list():
    group = [*_reviewer_group(), _row("Q4C", "Editorial board positions:", confidence=0.9,
                                      hierarchy=EDITORIAL_HEADING),
             _row("Q4C", "Example Health Services Review", confidence=0.5, hierarchy=EDITORIAL_HEADING)]
    out, n = apply_header_pin(group, NO_PIN_CTX, batch_size=_TWO_PER_CALL)
    assert n == 0 and out == group


def test_a_board_lead_in_opens_no_reviewer_list():
    group = _reviewer_group(("Q4C", "Example Health Services Review", 0.5),
                            lead_in="Editorial board member and reviewer for:")
    out, n = apply_header_pin(group, NO_PIN_CTX, batch_size=_TWO_PER_CALL)
    assert n == 0 and out == group


@pytest.mark.parametrize("text", [
    REVIEWER_LEAD_IN,
    "Ad-Hoc Reviewer for:",
    "  Ongoing reviewer for:  ",
    "Reviewer for epidemiology journals:",
])
def test_reviewer_lead_ins(text):
    assert is_reviewer_lead_in(text)


@pytest.mark.parametrize("text", [
    "In review (manuscripts submitted or in preparation):",           # a publication list
    "Submitted, under review:",
    "Example Symposium Review, Example University:",
    "Editorial board member and reviewer for:",                        # names a board role
    "Ad hoc Reviewer",                                                  # no colon: not a lead-in
    "Reviewer for the Example Journal of Medicine, Example Society of Medicine, and others since 2031:",
])
def test_not_reviewer_lead_ins(text):
    assert not is_reviewer_lead_in(text)


def test_reviewer_pin_is_not_flipped_back_by_the_reasoning_check():
    from unified_pipeline.core.validators.reasoning_consistency_checker import (
        apply_reasoning_corrections,
    )
    group = _reviewer_group(("Q4C", "Example Health Services Review", 0.5))
    out, _ = apply_header_pin(group, NO_PIN_CTX, batch_size=_TWO_PER_CALL)
    (corrected,), _ = apply_reasoning_corrections([{**out[-1], "taxonomy_confidence": 0.95}], min_confidence=0.80)
    assert corrected["taxonomy_code"] == "Q4D"



# --- #1573: rows a heading says are someone else's -------------------------------

ACK_LEAD = "ACKNOWLEDGMENTS (selected)"
COLLAPSED = ("Personal Data",)  # a run whose headings stage 1b lost (ECXGAT placed none)


def _codes(out):
    return [e["taxonomy_code"] for e in out]


def test_papers_under_an_acknowledgements_lead_line_go_to_the_appendix():
    # ECXGAT 2154-2204: other groups' papers listed under an acknowledgements line.
    group = [_row("T", ACK_LEAD, hierarchy=COLLAPSED),
             _row("S1", "Doe, J., and A. Roe. 2031. An example virus study. J. Example Biol. 12, 34-56.",
                  confidence=0.9, hierarchy=COLLAPSED),
             _row("S4", "Roe, A. 2032. An example chapter, in: Example Methods. Example Press.",
                  confidence=0.9, hierarchy=COLLAPSED)]
    out, n = apply_header_pin(group, NO_PIN_CTX)
    assert n == 2 and _codes(out) == ["T", "T", "T"]
    assert [e.get("pre_pin_code") for e in out[1:]] == ["S1", "S4"]
    assert "acknowledgements" in out[1]["classification_reasoning"]


@pytest.mark.parametrize("heading", [
    ("Acknowledgments", "Journal articles"),
    ("PUBLICATIONS", "C. Acknowledgements"),
])
def test_a_paper_under_an_acknowledgements_level_goes_to_the_appendix(heading):
    paper = _row("S1", "Doe J. An example study. Example J. 2031;1:2-9.", hierarchy=heading)
    (out,), n = apply_header_pin([paper], NO_PIN_CTX)
    assert n == 1 and out["taxonomy_code"] == "T"


@pytest.mark.parametrize("heading", [
    ("PUBLICATIONS", "Peer-reviewed articles (acknowledging grant support marked *)"),
    ("PUBLICATIONS", "Reviewer Acknowledgements"),
])
def test_the_owners_paper_under_a_heading_that_only_mentions_acknowledgements_stays(heading):
    paper = _row("S1", "Owner A, Doe J. An example study. Example J. 2031;1:2-9.", hierarchy=heading)
    out, n = apply_header_pin([paper], NO_PIN_CTX)
    assert n == 0 and _codes(out) == ["S1"]


def test_an_acknowledgements_list_ends_at_a_row_it_does_not_head():
    # A dated teaching row after the acknowledged papers: the papers below it are the owner's.
    group = [_row("T", ACK_LEAD, hierarchy=COLLAPSED),
             _row("K1", "2031 Lecturer, Example Course, Example University", hierarchy=COLLAPSED),
             _row("S1", "Owner A. An example study. Example J. 2031;1:2-9.", hierarchy=COLLAPSED)]
    out, n = apply_header_pin(group, NO_PIN_CTX)
    assert n == 0 and _codes(out) == ["T", "K1", "S1"]


def test_a_new_lead_line_ends_the_acknowledgements_list():
    group = [_row("T", ACK_LEAD, hierarchy=COLLAPSED),
             _row("T", "PUBLICATIONS", hierarchy=COLLAPSED),
             _row("S1", "Owner A. An example study. Example J. 2031;1:2-9.", hierarchy=COLLAPSED)]
    out, n = apply_header_pin(group, NO_PIN_CTX)
    assert n == 0 and _codes(out) == ["T", "T", "S1"]


@pytest.mark.parametrize("lead", [
    "Reviewers:", "Grant reviewing (partial list)", "Applicants", "Example Foundation Panel",
    "Technical staff", "Laboratory technicians", "Lab managers",
])
def test_a_mentee_row_under_a_reviewing_or_staff_lead_line_is_not_a_mentee(lead):
    group = [_row("T", lead, hierarchy=COLLAPSED),
             _row("N3B", "Alex Example \u2013 An example proposal.", hierarchy=COLLAPSED)]
    out, n = apply_header_pin(group, NO_PIN_CTX)
    assert n == 1 and _codes(out) == ["T", "T"]


def test_staff_under_a_staff_heading_are_not_mentees():
    # IZIXVF 284-305: a laboratory's technical staff, coded N3B and N3.
    heading = ("4. Laboratory Members: Students and Staff.",
               "B. Laboratory Technicians and Managers.")
    group = [_row("N3B", "1. Alex Example, Laboratory technician", hierarchy=heading),
             _row("N3", "2. Sam Example, Lab manager", hierarchy=heading)]
    out, n = apply_header_pin(group, NO_PIN_CTX)
    assert n == 2 and _codes(out) == ["T", "T"]
    assert "reviewed or employed" in out[0]["classification_reasoning"]


def test_grant_review_applicants_under_a_review_lead_line_are_not_mentees():
    # ECXGAT 1288-1299: the lead line, two P rows, then applicants coded N3B.
    group = [_row("T", "Internal pilot grant review (selected)", hierarchy=COLLAPSED),
             _row("P", "Alex Example – An example proposal (18 Dec-2031).", hierarchy=COLLAPSED),
             _row("N3B", "Sam Example – Another example proposal (18 Dec-2031).", hierarchy=COLLAPSED),
             _row("N3A", "Kim Example – A third example proposal (18 Dec-2031).", hierarchy=COLLAPSED)]
    out, n = apply_header_pin(group, NO_PIN_CTX)
    assert n == 2 and _codes(out) == ["T", "P", "T", "T"]


def test_a_thesis_under_a_bare_reviewer_lead_line_is_not_a_mentee():
    # DYLJXC 401-403: a thesis the owner reviewed for another university.
    group = [_row("Q4D", "Reviewer:", hierarchy=("Service", "International")),
             _row("N3B", 'Alex Example 2031 "An example thesis title" Example University',
                  hierarchy=("Service", "International"))]
    out, n = apply_header_pin(group, NO_PIN_CTX)
    assert n == 1 and _codes(out) == ["Q4D", "T"]


@pytest.mark.parametrize("heading", [
    ("MENTORING", "Research Trainees (annual progress review)"),   # names trainees
    ("TEACHING", "Trainees", "Technicians the owner trained"),        # staff the owner trained
    ("MENTORING", "Postdoctoral fellows and staff"),
    ("MENTORING", "Supervision of research staff"),                    # kept: supervision is mentoring work
    ("MENTORING", "Dissertation committees and staff"),
    # stage 1b's hierarchy drift: a student-thesis list under a sibling review level (wave-1 2030_*)
    ("SERVICE", "Professional Service", "Manuscript Reviewer"),
    ("SERVICE", "Professional Service", "Review Panels and Advisory Boards"),
])
def test_the_owners_mentee_under_a_heading_that_mentions_review_or_staff_stays(heading):
    mentee = _row("N3B", "2031 Alex Example. An example thesis title.", hierarchy=heading)
    out, n = apply_header_pin([mentee], NO_PIN_CTX)
    assert n == 0 and _codes(out) == ["N3B"]


def test_a_mentee_under_a_trainee_lead_line_stays():
    group = [_row("T", "Thesis review committees", hierarchy=COLLAPSED),
             _row("N3B", "2031 Alex Example. An example thesis title.", hierarchy=COLLAPSED)]
    out, n = apply_header_pin(group, NO_PIN_CTX)
    assert n == 0 and _codes(out) == ["T", "N3B"]


def test_a_review_list_ends_at_a_row_it_does_not_head():
    # web26: a "Reviewer:" line far above a postdoc list, with a training row in between.
    group = [_row("Q4D", "Reviewer (about eighty manuscripts)", hierarchy=COLLAPSED),
             _row("C", "2031-2032 Example Fellowship, Example University", hierarchy=COLLAPSED),
             _row("N3B", "2031-2032 Alex Example, PhD (Example University)", hierarchy=COLLAPSED)]
    out, n = apply_header_pin(group, NO_PIN_CTX)
    assert n == 0 and _codes(out) == ["Q4D", "C", "N3B"]


def test_a_mentee_row_is_never_a_lead_line():
    # An undated named-mentee line naming review does not head the rows after it.
    group = [_row("N3B", "Alex Example, grant reviewer", hierarchy=COLLAPSED),
             _row("N3B", "2031 Sam Example. An example thesis title.", hierarchy=COLLAPSED)]
    out, n = apply_header_pin(group, NO_PIN_CTX)
    assert n == 0 and _codes(out) == ["N3B", "N3B"]


def test_a_dated_or_numbered_line_is_not_a_lead_line():
    for lead in ("Grant review panel, 2031", "1. Grant review panel"):
        group = [_row("Q3", lead, hierarchy=COLLAPSED),
                 _row("N3B", "Alex Example – An example proposal.", hierarchy=COLLAPSED)]
        out, n = apply_header_pin(group, NO_PIN_CTX)
        assert n == 0 and _codes(out) == ["Q3", "N3B"], lead


def test_a_long_line_is_not_a_lead_line():
    long_lead = "Grant review panel for the example program of the example foundation and its many partners"
    group = [_row("T", long_lead, hierarchy=COLLAPSED),
             _row("N3B", "Alex Example – An example proposal.", hierarchy=COLLAPSED)]
    out, n = apply_header_pin(group, NO_PIN_CTX)
    assert n == 0 and _codes(out) == ["T", "N3B"]


ABSTRACTS = ("PUBLICATIONS", "Abstracts")


@pytest.mark.parametrize("text", [
    # ULHCAP 1415: a trainee's abstract that names the prize it won.
    "Doe, JD. An example antidote study. Winner, Example Early Career Prize. "
    "Example Symposium 2031. Oral abstract.",
    "Doe J, Roe K. An example study. Best Poster, Example Meeting 2031.",
])
def test_an_abstract_naming_its_award_under_an_abstracts_heading_is_an_abstract(text):
    (out,), n = apply_header_pin([_row("H", text, confidence=0.75, hierarchy=ABSTRACTS)], NO_PIN_CTX)
    assert n == 1 and out["taxonomy_code"] == "S8" and out["pre_pin_code"] == "H"
    assert out["classification_reasoning"].startswith("An abstract listed under the CV's abstracts")


@pytest.mark.parametrize("code,text,heading", [
    ("H", "Best Abstract Award, Example Society Annual Meeting, 2031", ABSTRACTS),       # the owner's own award
    ("H", "Doe, JD. An example study. Winner, Example Prize 2031.", ("HONORS AND AWARDS",)),
    ("K4", "Doe, JD. An example study. Example Symposium 2031.", ABSTRACTS),            # not an H
])
def test_an_award_line_or_another_heading_keeps_its_code(code, text, heading):
    out, n = apply_header_pin([_row(code, text, hierarchy=heading)], NO_PIN_CTX)
    assert n == 0 and _codes(out) == [code]


def test_someone_elses_pins_are_not_flipped_back_by_the_reasoning_check():
    from unified_pipeline.core.validators.reasoning_consistency_checker import (
        apply_reasoning_corrections,
    )
    group = [_row("T", ACK_LEAD, hierarchy=COLLAPSED),
             _row("S1", "Doe, J. 2031. An example study. J. Example Biol. 12, 34-56.", hierarchy=COLLAPSED),
             _row("N3B", "1. Alex Example, Laboratory technician", hierarchy=("Technical Staff",)),
             _row("H", "Doe, JD. An example study. Winner, Example Prize 2031.", hierarchy=ABSTRACTS)]
    out, _ = apply_header_pin(group, NO_PIN_CTX)
    corrected, _ = apply_reasoning_corrections([{**e, "taxonomy_confidence": 0.95} for e in out[1:]],
                                               min_confidence=0.80)
    assert _codes(corrected) == ["T", "T", "S8"]


def test_someone_elses_pin_code_reads_the_lead_it_is_given():
    staff = _row("N3B", "Alex Example, Example role", hierarchy=COLLAPSED)
    assert someone_elses_pin_code(staff, "") is None
    assert someone_elses_pin_code(staff, "Technical staff")[0] == "T"
    assert someone_elses_pin_code(staff, "Grant applicants reviewed")[0] == "T"


# --- #1573 review round: where a lead line's list ends, and what may be a lead line ---

OTHER_PAPER = "Doe J, Roe K. Another example study. Example J. 2031;1:2-9."
OWN_PAPER = "Owner A, Doe J. An example study. Example J. 2032;3:4-9."
OWN_STUDENT = "2031 Alex Example, PhD student. An example thesis title."


@pytest.mark.parametrize("heading", [
    "PUBLICATIONS (2010-2032)",                     # dated
    "5. Publications",                              # numbered
    "C. Peer-reviewed publications",                # lettered
    "Publications in peer reviewed journals listed in reverse chronological order, most recent first",
])
def test_an_acknowledgements_list_ends_at_a_section_heading_that_is_not_a_lead_line(heading):
    group = [_row("T", ACK_LEAD, hierarchy=COLLAPSED), _row("S1", OTHER_PAPER, hierarchy=COLLAPSED),
             _row("T", heading, hierarchy=COLLAPSED), _row("S1", OWN_PAPER, hierarchy=COLLAPSED)]
    out, n = apply_header_pin(group, NO_PIN_CTX)
    assert n == 1 and _codes(out) == ["T", "T", "T", "S1"]


@pytest.mark.parametrize("heading", ["5. Mentoring", "MENTORING (2015-2032)"])
def test_a_review_list_ends_at_a_section_heading_that_is_not_a_lead_line(heading):
    group = [_row("T", "Grant Review Committees", hierarchy=COLLAPSED),
             _row("Q3", "2031 Member, Example Study Section", hierarchy=COLLAPSED),
             _row("T", heading, hierarchy=COLLAPSED), _row("N3B", OWN_STUDENT, hierarchy=COLLAPSED)]
    out, n = apply_header_pin(group, NO_PIN_CTX)
    assert n == 0 and _codes(out) == ["T", "Q3", "T", "N3B"]


@pytest.mark.parametrize("record", [
    ("Q3", "Ad hoc grant reviewer, Example Foundation"),
    ("Q4D", "Reviewer, Example Journal"),
    ("Q3", "Grant reviewer; Example Foundation"),
])
def test_a_line_punctuated_as_a_record_is_not_a_lead_line(record):
    group = [_row(*record, hierarchy=COLLAPSED), _row("N3B", OWN_STUDENT, hierarchy=COLLAPSED)]
    out, n = apply_header_pin(group, NO_PIN_CTX)
    assert n == 0 and _codes(out) == [record[0], "N3B"]


@pytest.mark.parametrize("lead", [
    ("T", "Technical staff, laboratory"),               # a note line may hold a comma
    ("Q3", "Grant review panels, internal:"),           # so may a line that ends in a colon
])
def test_a_note_or_colon_line_with_a_comma_is_still_a_lead_line(lead):
    group = [_row(*lead, hierarchy=COLLAPSED),
             _row("N3B", "Alex Example \u2013 An example proposal.", hierarchy=COLLAPSED)]
    out, n = apply_header_pin(group, NO_PIN_CTX)
    assert n == 1 and _codes(out) == [lead[0], "T"]


@pytest.mark.parametrize("group,expected", [
    # undated, short staff rows: each moved row would otherwise read as a new lead line
    ([("T", "Technical staff"), ("N3B", "Alex Example, lab assistant"),
      ("N3B", "Sam Example, lab assistant"), ("N3", "Kim Example, lab assistant")], ["T", "T", "T", "T"]),
    # an undated acknowledged book, then two more acknowledged papers
    ([("T", ACK_LEAD), ("S3", "Doe J. An example book. Example Press."),
      ("S1", OTHER_PAPER), ("S1", OTHER_PAPER)], ["T", "T", "T", "T"]),
    ([("T", "Internal grant review (partial list)"), ("N3B", "Alex Example - An example proposal"),
      ("N3B", "Sam Example - Another example proposal")], ["T", "T", "T"]),
])
def test_a_row_the_pin_moves_does_not_split_its_list(group, expected):
    out, _ = apply_header_pin([_row(code, text, hierarchy=COLLAPSED) for code, text in group], NO_PIN_CTX)
    assert _codes(out) == expected


def test_a_review_list_runs_on_through_its_service_rows():
    group = [_row("T", "Grant reviewing (partial list)", hierarchy=COLLAPSED),
             _row("Q3", "2031 Example Foundation pilot awards", hierarchy=COLLAPSED),
             _row("N3B", "Alex Example \u2013 An example proposal (2031).", hierarchy=COLLAPSED)]
    out, n = apply_header_pin(group, NO_PIN_CTX)
    assert n == 1 and _codes(out) == ["T", "Q3", "T"]


@pytest.mark.parametrize("service_code", ["P", "Q2"])
def test_an_acknowledgements_list_ends_at_a_service_row(service_code):
    group = [_row("T", ACK_LEAD, hierarchy=COLLAPSED),
             _row(service_code, "2031 Member, Example Committee", hierarchy=COLLAPSED),
             _row("S1", OWN_PAPER, hierarchy=COLLAPSED)]
    out, n = apply_header_pin(group, NO_PIN_CTX)
    assert n == 0 and _codes(out) == ["T", service_code, "S1"]


def test_a_mentees_abstract_under_a_students_level_is_the_mentees():
    # The mentee pin (#1251) is read before the abstracts rule: the row is the trainee's.
    abstract = _row("H", "Doe J, Roe K. An example title. First place, Example Meeting 2031.",
                    hierarchy=("Trainees", "Abstracts presented by students"))
    (out,), n = apply_header_pin([abstract], NO_PIN_CTX)
    assert n == 1 and out["taxonomy_code"] == "N4"


def test_an_acknowledged_metrics_line_is_someone_elses_not_the_owners_bibliometric():
    stub = _row("S1", "Doe 2031 Example Journal. Times cited 12", hierarchy=("ACKNOWLEDGEMENTS",))
    assert content_pin_code(stub)[0] == "S0"
    (out,), n = apply_header_pin([stub], NO_PIN_CTX)
    assert n == 1 and out["taxonomy_code"] == "T"


@pytest.mark.parametrize("text", [
    "Winner, AHA Abstract Achievement Award, 2031",
    "Recipient, AHA Travel Award for abstract, 2031",
    "Finalist, ATS. Abstract Scholarship 2031",
    "Selected, ASH Abstract Achievement Award, 2031",   # an acronym, not initials: no period or comma after it
])
def test_an_award_line_with_an_award_word_or_a_societys_acronym_keeps_its_h(text):
    out, n = apply_header_pin([_row("H", text, hierarchy=ABSTRACTS)], NO_PIN_CTX)
    assert n == 0 and _codes(out) == ["H"]


def test_an_abstracts_parent_over_an_honors_leaf_keeps_the_h():
    award = _row("H", "Doe, JD. An example study. Example Prize 2031.", hierarchy=("ABSTRACTS", "Honors and awards"))
    out, n = apply_header_pin([award], NO_PIN_CTX)
    assert n == 0 and _codes(out) == ["H"]


@pytest.mark.parametrize("heading", [
    ("MENTORING", "Staff"),
    ("Mentees", "Research staff"),
    ("Supervised personnel", "Technicians"),
])
def test_staff_under_a_mentoring_parent_stay_mentees(heading):
    out, n = apply_header_pin([_row("N3B", "1. Alex Example, Laboratory technician", hierarchy=heading)], NO_PIN_CTX)
    assert n == 0 and _codes(out) == ["N3B"]


@pytest.mark.parametrize("code,heading", [
    ("S1", ("Acknowledgements and Publications",)),
    ("S9", ("Acknowledgements in the press",)),
    ("S1", ("PUBLICATIONS", "Acknowledgments of support (grants)")),
])
def test_a_level_that_says_more_than_acknowledgements_keeps_its_papers(code, heading):
    out, n = apply_header_pin([_row(code, OWN_PAPER, hierarchy=heading)], NO_PIN_CTX)
    assert n == 0 and _codes(out) == [code]


def test_a_lead_line_that_says_more_than_acknowledgements_keeps_its_papers():
    group = [_row("T", "Acknowledgements and publications", hierarchy=COLLAPSED),
             _row("S1", OWN_PAPER, hierarchy=COLLAPSED)]
    out, n = apply_header_pin(group, NO_PIN_CTX)
    assert n == 0 and _codes(out) == ["T", "S1"]


def test_a_staff_list_ends_at_a_service_row():
    # Only a reviewing line's list runs on through service rows (ECXGAT 1289-1291).
    group = [_row("T", "Technical staff", hierarchy=COLLAPSED),
             _row("Q2", "2031 Member, Example Committee", hierarchy=COLLAPSED),
             _row("N3B", OWN_STUDENT, hierarchy=COLLAPSED)]
    out, n = apply_header_pin(group, NO_PIN_CTX)
    assert n == 0 and _codes(out) == ["T", "Q2", "N3B"]


def test_a_review_list_ends_at_a_publication_row():
    group = [_row("T", "Grant reviewing (partial list)", hierarchy=COLLAPSED),
             _row("S1", OWN_PAPER, hierarchy=COLLAPSED),
             _row("N3B", OWN_STUDENT, hierarchy=COLLAPSED)]
    out, n = apply_header_pin(group, NO_PIN_CTX)
    assert n == 0 and _codes(out) == ["T", "S1", "N3B"]


# --- #1580: a Q2 or P row that is no seat on a body ---------------------------

def _service(code, text, *heading):
    return {"text": text, "taxonomy_code": code, "taxonomy_confidence": 0.75,
            "classification_reasoning": "external advisory service (Q2)", "classification_source": "llm",
            "hierarchy": list(heading or ("Professional Service",)), "element_idx_start": 95}


@pytest.mark.parametrize("entry", [
    # a consultancy coded Q2 (HBALTD 38, EYTHNB 459)
    _service("Q2", "2031-present  Consultant, Example Health Rhythms"),
    _service("Q2", "Consultantships with several pharmaceutical companies, 2031 to present.", "Consultantships"),
    # a company's advisory board (UVZNIC 102, AOJEQD 46), coded Q2 or P (WBZIDP 47)
    _service("Q2", "2031  Scientific Advisory Board, Example Pharmaceuticals, Inc., Example City"),
    _service("Q2", "2031  Scientific Advisory Board, Example Biosciences, Example City"),
    _service("P", "2031 Founder, Chair of the Scientific Advisory Board, Example Pharma.",
             "Professional Honors"),
    # expert work in litigation (UVZNIC 98, XNWSZN 1256, ZASOTV 184)
    _service("Q2", "2031  Expert Opinion, Example & Example, LLP, Litigation regarding an example drug",
             "Consulting Appointments"),
    _service("Q2", "Expert Witness for an Example Patent Case, Example Litigation\t2031-present", "Consulting"),
    # a company under a consulting heading, with no consulting word on its line (EJPAIA 175)
    _service("Q2", "Example Biologics Corporation, Example City, 2031 to 2032",
             "Scientific Advisory Boards and Consultantships"),
    # a year in parentheses alone does not make a consultancy a citation
    _service("Q2", "Consultant, Example Health Rhythms (2031)"),
])
def test_consulting_is_another_professional_position(entry):
    assert non_seat_pin_code(entry)[0] == "D3"


@pytest.mark.parametrize("entry", [
    # a consultant's seat on a society's named committee
    _service("Q2", "Consultant, Example Society Ethics Committee, 2031"),
    # an advisory board of a public body, society, foundation or school stays a board
    _service("Q2", "2031  Scientific Advisory Board, Example Foundation, Inc."),
    _service("Q2", "2031  Advisory Board, Example Institute of Health"),
    # a company named without a company word cannot be told from a school's program (EJPAIA 181)
    _service("Q2", "Example University Research Program, Advisory Board, 2031",
             "Scientific Advisory Boards and Consultantships"),
    # testimony stays Q2 (the owner's decision, 2026-10-27), and so does expert work outside litigation
    _service("Q2", "Consulted and testified on Example Bill 31-1234, 2031", "Community Involvement"),
    _service("Q2", "Expert Witness, Example Agency hearings, 2031-32"),
    # an institution's own consulting project coded P names no company
    _service("P", "Consultant\tExample Health System\tExample Quality Project\t2031", "Departmental"),
    # a board of directors is a seat, not consulting
    _service("Q2", "2031 - 2032  Example Board of Directors"),
    # a public body's, a hospital's or a blood bank's advisory board is no company's
    _service("Q2", "2031  Scientific Advisory Board, Example Children's Hospital, Inc."),
    _service("Q2", "2031  Example Inc. Medical Advisory Board, Example Blood Bank"),
    _service("Q2", "United States Example Convention, Inc., Example Advisory Committee, 2031"),
    # consulting inside a body, a seat or an office, a citation, or a list of talks
    _service("Q2", "Consultant, Example Technical Advisory Group 2031"),
    _service("Q2", "Example Health Coalition, consultant 2031"),
    _service("Q2", "2031 Member, External Consultant to the Example Department"),
    _service("Q2", "Consultant: Example Foundation, 2031. President: Example Association, 2032-"),
    _service("Q2", "Doe, J. (2031) A consulting career. Interview with Example Consulting."),
    _service("Q2", "12. Example Pharma Consultant Focus Group; Example City 2031", "Invited Presentations"),
    # a meeting or a body named after consultants is not the owner consulting
    _service("Q2", "Example Center Consultants\u2019 Conference (2031 - present)"),
    _service("Q2", "Example Consultant\u2019s Forum, 2031"),
    _service("Q2", "\u201cExample Care.\u201d Example Regional Consultants Collaborative, Example City, 2031"),
    # litigation that names no expert or consulting work
    _service("Q2", "Example Bar Association Litigation Section, 2031"),
    # a company's board of directors is a seat
    _service("Q2", "Example Pharma Inc. Board of Directors, 2031"),
    # only Q2 and P are read
    _service("D3", "2031-present  Consultant, Example Health Rhythms"),
    _service("Q1", "2031  Scientific Advisory Board, Example Pharmaceuticals, Inc."),
])
def test_a_seat_or_a_public_board_stays_where_the_model_put_it(entry):
    assert non_seat_pin_code(entry) is None


@pytest.mark.parametrize("entry", [
    # under a heading that names attendance (LTTYWI 1084-1101, coded Q2 by T-validation)
    _service("Q2", "31. Annual Meeting of the Example College, Example City, September 26-27, 2031.",
             "Scientific Meetings Attended"),
    _service("Q2", "36. World Congress of Example Studies, Example City, September 23-27, 2031.",
             "Scientific Meetings Attended"),
    # on a line that opens as attendance (MQLKPQ 109, LWMSPJ 342)
    _service("Q2", "Attendee Example Appreciation Day 2031, 2032", "Affiliations and Activities"),
    _service("P", "Participant, Example Program Directors Meeting, 12/11-12/14, Example City", "Other Service"),
    _service("Q2", "2031 Participant, Example Workshop on Methods, Example City", "Other Service"),
    # the event named by the attendance heading only, or by a heading of attendance (MEHOQM 143)
    _service("Q2", "36. World Congres of Example Studies, Example City, September 23-27, 2031.",
             "Scientific Meetings Attended"),
    _service("Q2", "Example Society Annual Meeting: 2031, 2032", "Convention & Symposium Attendance"),
    # a place's initials are no author list (LTTYWI 1099)
    _service("Q2", "Annual Meeting of the Example College, Example City DC, September 19-20, 2031.",
             "Scientific Meetings Attended"),
    _service("Q2", "Annual Meeting of the Example College, Example, D.C., 2031; Satellite Session, Example, D.C.",
             "Scientific Meetings Attended"),
])
def test_a_meeting_the_owner_attended_is_other_education(entry):
    assert non_seat_pin_code(entry)[0] == "B2"


@pytest.mark.parametrize("entry", [
    # an attended meeting the owner chaired, organized or spoke at
    _service("Q2", "Chair, Annual Meeting of the Example Society, Example City, 2031", "Meetings Attended"),
    _service("Q2", "Organizer, Example Workshop on Methods, 2031", "Meetings Attended"),
    _service("Q2", "Participant and member of Planning Committee, Example Experts Meeting, 2031", "Conferences"),
    # an author list is the owner's talk or abstract
    _service("Q2", "Doe J, Roe K. An example title. Annual Meeting of the Example Society, 2031.", "Meetings Attended"),
    _service("Q2", "Doe J et al. An example title. Annual Meeting of the Example Society, 2031.", "Meetings Attended"),
    _service("Q2", "Panelist, Example Annual Meeting, 2031", "Meetings Attended"),
    _service("Q2", "2031 Participant, Example Health Day - provided screenings to patients", "Public Service"),
    # no event on the line or on the attendance heading (another heading's event does not count)
    _service("Q2", "Example Society of Example Studies, 2031", "Attendance"),
    _service("Q2", "Participant, Example Data Working Party, 2031", "Conferences"),
    _service("Q2", "Participant, Example Workgroup on Data Standards, 2031", "Other Service"),
])
def test_an_attended_meeting_with_a_part_the_owner_played_stays(entry):
    assert non_seat_pin_code(entry) is None


@pytest.mark.parametrize("entry", [
    # a P consultancy that names a company (WBZIDP 222's shape coded P)
    _service("P", "2031 Consultant, Example Therapeutics", "Other Activities"),
    # a company under a nearest heading that names consulting, its line naming no body (BYFQBG 186)
    _service("Q2", "Example Ltd,      SAB   2031 \u2013 present", "Consultancies/Scientific Advisory Board"),
    # a consulting role with more than its dates on the line
    _service("Q2", "Senior Consultant | 2031 - 2032", "Professional Organizations"),
    # a company named with a plural noun (web244 797.2)
    _service("Q2", "2031 | Example Centers, Inc. | Review of research protocols", "Professional Consulting"),
])
def test_more_consulting_is_another_professional_position(entry):
    assert non_seat_pin_code(entry)[0] == "D3"


@pytest.mark.parametrize("entry", [
    # a line that is only the role and its dates: one record of a committee roster (MEZVNA 124.5, 124.10)
    _service("Q2", "Consultant | 2031 - 2032", "Professional Organizations"),
    _service("Q2", "Consultant      . | 2031 - 2033", "Professional Organizations"),
    _service("Q2", "Consultant, Jan 2031 - present", "Professional Organizations"),
    _service("Q2", "Consultant, September 2029 to 2031", "Professional Organizations"),
    _service("Q2", "Consultant since 2031 (current)", "Professional Organizations"),
    _service("Q2", "Consultant until 2031", "Professional Organizations"),
    # a company named under a heading whose consulting word is a level up (the leaf names committees)
    _service("Q2", "Example Biologics Corporation, 2031", "Consultancies and Committees", "Committees"),
    # a company's board or committee under a consulting heading is a seat
    _service("Q2", "Example Pharma Inc. Board of Directors, 2031", "Consulting"),
    _service("Q2", "2031 | Example Pharma, Inc. | Data Monitoring Committee for an example vaccine",
             "Professional Consulting"),
    # an editor is an office
    _service("Q2", "Consulting Editor, Example Journal of Methods, 2031"),
    # a public body named in the plural is no company
    _service("P", "2031 Consultant, Example City Health and Hospitals Corporation", "Other Activities"),
    _service("Q2", "Example City Health and Hospitals Corporation, 2031", "Consulting"),
])
def test_a_bare_role_a_seat_or_a_public_body_is_not_consulting(entry):
    assert non_seat_pin_code(entry) is None


@pytest.mark.parametrize("entry", [
    # advocacy and a day of service stay Q2 (the owner's decision on #1580)
    _service("Q2", "Participant, Example Society Hill Day, 2031", "Other Service"),
    _service("Q2", "2031 Participant, Example Society Advocacy Day", "Other Service"),
    _service("Q2", "Participant, Example Day of Service, 2031", "Community Service"),
    # a talk, an abstract or a guideline the owner worked on is a part played (SEKQUI 264)
    _service("Q2", "Attended the Example Society Annual Meeting; gave a talk on example methods, 2031", "Other Service"),
    _service("Q2", "Participant, Example Annual Meeting, oral abstract, 2031", "Other Service"),
    _service("Q2", "2031 Participant, Example Consensus Conference on Methods", "National Leadership Positions"),
    _service("Q2", "2031 Participant, Example Conference 2.0 \u2013 revisions to the Example Training Guidelines",
             "National Leadership Positions"),
    _service("Q2", "Example Society Annual Meeting, poster session judge, 2031", "Meetings Attended"),
    _service("Q2", "2031 Attended the Example Annual Meeting; poster on example methods", "Other Service"),
    # an attendance heading above a leaf that names something else
    _service("Q2", "Example Society Annual Meeting, 2031", "Meetings Attended", "National Committees"),
])
def test_advocacy_a_part_played_or_another_leaf_is_no_attended_meeting(entry):
    assert non_seat_pin_code(entry) is None


def test_a_day_the_cv_lists_under_meetings_attended_is_attended():
    # The CV's own attendance heading outranks the advocacy word (OQIDBN 775's shape).
    entry = _service("Q2", "2031-2033 Federal Capitol Hill Day", "Continuing Education", "Meetings Attended, National")
    assert non_seat_pin_code(entry)[0] == "B2"


def test_apply_non_seat_pins_keeps_the_models_answer_and_counts_each_recode():
    consulting = _service("Q2", "2031-present  Consultant, Example Health Rhythms")
    seat = _service("Q2", "Member, Example Society Ethics Committee, 2031")
    out, stats = apply_non_seat_pins([consulting, seat])
    assert _codes(out) == ["D3", "Q2"]
    assert out[0]["pre_pin_code"] == "Q2" and "(category D3)" in out[0]["classification_reasoning"]
    assert out[1] is seat and consulting["taxonomy_code"] == "Q2"
    assert stats["corrections_applied"] == 1
    assert stats["correction_details"] == [{"element_idx": 95, "correction": {
        "from": "Q2", "to": "D3", "reason": non_seat_pin_code(consulting)[1]}}]
    assert stats["correction_details"][0]["correction"]["reason"].startswith("Consulting")
    assert non_seat_pin_code(_service("Q2", "Attendee Example Day 2031"))[1].startswith("A meeting the owner attended")
