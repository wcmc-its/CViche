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
    content_pin_code,
    is_note_not_record,
    leaf_header_code,
    mentee_pin_code,
    pinned_header_code,
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
])
def test_no_new_pin_is_flipped_back_by_the_reasoning_check(code, text, ctx):
    from unified_pipeline.core.validators.reasoning_consistency_checker import (
        apply_reasoning_corrections,
    )
    (out,), n = apply_header_pin([_row(code, text, hierarchy=("SECTION", "Example grant review"))], ctx)
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


# --- #1695: a per-article citation-metrics line is S0 -----------------------------

_VENUE = "Example Membrane Letters"


@pytest.mark.parametrize("code,text", [
    ("S1", f"1. Quorlenby et al. (2031) {_VENUE} JIF: 7.071 cited 12 times"),
    ("S2", f"12) Vashtrel et al., 2031b, {_VENUE}, JIF 4.1"),
    ("S1", f"\u2022 Pemberwick and Zandquist (2031) {_VENUE} impact factor 3.2"),
    ("Q4C", f"Pemberwick & Zandquist (2031): {_VENUE} IF: 3.2"),
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
    ("K5", ("ADVISING", "Undergraduate Research Students")),   # teaching, no accomplishments word
    ("D1", ("MENTORING", "Trainees")),
    ("S1", STUDENT_HEADING),                                     # a publication stays a publication
    ("H", ("PROFESSIONAL ACCOMPLISHMENTS",)),                    # no mentee word in the heading
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
    ("FUNDING", "Grants as Comentor"),
    ("FUNDING", "Grants as Sponsor"),
    ("FUNDING", "Grants as  Mentor"),
    ("GRANT SUPPORT", "GRANTS, FELLOWSHIPS", "Mentor or Co-mentor"),        # 2087 shape
    ("GRANT SUPPORT", " Sponsor: "),
    ("GRANT SUPPORT", "Mentor or Sponsor or Co-mentor"),
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
