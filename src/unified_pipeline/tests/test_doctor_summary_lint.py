"""Tests for doctor/lints/summary.py: claims the generated research summary
makes that the CV does not support (#1554).

Fixtures are invented: no name, institution, funder line or sentence here
comes from a CV.

    python3 -m pytest src/unified_pipeline/tests/test_doctor_summary_lint.py -p no:cacheprovider
"""

import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.doctor.lints.summary import (  # noqa: E402
    CLAIM_FUNDER,
    CLAIM_FUNDING,
    CLAIM_MENTORING,
    CLAIM_PENDING,
    lint_summary_unsupported_claim,
)
from unified_pipeline.run_doctor import run_doctor  # noqa: E402
from unified_pipeline.stage_4_5_research_summary import (
    GENERATION_METHOD_LLM,  # noqa: E402
)

_PENDING_SENTENCE = "I currently have an application under review on widget folding."
_FUNDING_SENTENCE = "This work is supported by institutional and foundation resources."
_MENTORING_SENTENCE = "I have mentored trainees in widget science."
_NIH_SENTENCE = "Earlier, I led NIH-funded projects on gadget assembly."


def _summary(*sentences, method=GENERATION_METHOD_LLM):
    return {"research_summary": {"text": " ".join(sentences), "generation_method": method}}


def _entry(code, text="Invented entry text.", hierarchy=(), **fields):
    return {"taxonomy_code": code, "text": text, "hierarchy": list(hierarchy),
            "extracted_fields": fields}


def _stage4(*entries):
    return {"entries": list(entries)}


def _claims(findings):
    return [f["evidence"][0] for f in findings]


# ----------------------------------------------------------- pending applications

def test_pending_claim_warns_when_no_grant_is_pending():
    (finding,) = lint_summary_unsupported_claim(
        _summary(_PENDING_SENTENCE), _stage4(_entry("M2A", title="Widget study")))
    assert finding["lint"] == "summary_unsupported_claim"
    assert finding["severity"] == "WARN"
    assert finding["evidence"] == [f"claim={CLAIM_PENDING}", f"sentence={_PENDING_SENTENCE}"]


def test_pending_claim_is_supported_by_a_pending_grant():
    for pending in (_entry("M2C", title="Widget renewal"),
                    _entry("M2A", title="Widget renewal", status="Submitted"),
                    _entry("M2A", title="Widget renewal", hierarchy=("Funding", "Pending"))):
        assert lint_summary_unsupported_claim(_summary(_PENDING_SENTENCE), _stage4(pending)) == []


def test_a_pending_patent_is_not_a_pending_grant():
    patent = _entry("M2D", title="Widget press", status="Pending", hierarchy=("Patents pending",))
    (finding,) = lint_summary_unsupported_claim(_summary(_PENDING_SENTENCE),
                                                _stage4(patent, _entry("M2B")))
    assert _claims([finding]) == [f"claim={CLAIM_PENDING}"]


def test_pending_language_without_an_application_or_about_a_patent_is_not_a_claim():
    stage4 = _stage4(_entry("M2B", title="Old widget grant"))
    for sentence in ("Two manuscripts on widget folding are under review.",
                     "A patent application on the widget press is pending."):
        assert lint_summary_unsupported_claim(_summary(sentence), stage4) == []


# ----------------------------------------------------------- funding

def test_funding_claim_warns_when_the_cv_lists_only_patents():
    (finding,) = lint_summary_unsupported_claim(
        _summary(_FUNDING_SENTENCE), _stage4(_entry("M2D", title="Widget press")))
    assert _claims([finding]) == [f"claim={CLAIM_FUNDING}"]


def test_funding_claim_is_supported_by_any_grant():
    for code in ("M2A", "M2B", "M2C"):
        stage4 = _stage4(_entry(code, title="Widget grant"))
        found = lint_summary_unsupported_claim(_summary(_FUNDING_SENTENCE), stage4)
        assert f"claim={CLAIM_FUNDING}" not in _claims(found)


def test_a_review_role_sentence_is_not_a_funding_claim():
    sentence = "Funding and service have included a seat on a study section."
    assert lint_summary_unsupported_claim(_summary(sentence), _stage4(_entry("P"))) == []


# ----------------------------------------------------------- mentoring

def test_mentoring_claim_warns_when_the_cv_lists_no_mentoring():
    stage4 = _stage4(_entry("K1", "Lectures on widgets to medical students."))
    (finding,) = lint_summary_unsupported_claim(_summary(_MENTORING_SENTENCE), stage4)
    assert _claims([finding]) == [f"claim={CLAIM_MENTORING}"]


def test_training_others_is_a_mentoring_claim():
    sentence = "This informs the training of fellow widget scientists."
    (finding,) = lint_summary_unsupported_claim(_summary(sentence), _stage4(_entry("P")))
    assert _claims([finding]) == [f"claim={CLAIM_MENTORING}"]


def test_mentoring_claim_is_supported_by_a_mentee_or_mentoring_words():
    for entry in (_entry("N3A", "A. Student, 2020-present."),
                  _entry("K2", "Research mentor to two summer students."),
                  _entry("S8", "Abstract at the annual trainee research day.")):
        assert lint_summary_unsupported_claim(_summary(_MENTORING_SENTENCE), _stage4(entry)) == []


def test_research_activities_mentor_line_does_not_support_a_mentoring_claim():
    stage4 = _stage4(_entry("M1", "Postdoctoral fellow, widget lab. Mentor: B. Advisor."))
    (finding,) = lint_summary_unsupported_claim(_summary(_MENTORING_SENTENCE), stage4)
    assert _claims([finding]) == [f"claim={CLAIM_MENTORING}"]


# ----------------------------------------------------------- named funders

def test_funder_the_cv_never_names_warns():
    stage4 = _stage4(_entry("M2B", "Widget grant, State Widget Society.", title="Widget grant"))
    (finding,) = lint_summary_unsupported_claim(_summary(_NIH_SENTENCE), stage4)
    assert finding["evidence"] == [f"claim={CLAIM_FUNDER}", f"sentence={_NIH_SENTENCE}"]
    assert "NIH" in finding["message"]


def test_funder_is_supported_by_its_name_an_institute_or_the_source():
    supported = (
        (_stage4(_entry("M2B", "R01, National Institutes of Health.")), None),
        (_stage4(_entry("M2B", "R01, NCI.")), None),
        (_stage4(_entry("M2B", "Widget grant.")), ["Sponsor: NIH"]),
    )
    for stage4, source_lines in supported:
        assert lint_summary_unsupported_claim(_summary(_NIH_SENTENCE), stage4, source_lines) == []


def test_cdc_is_supported_by_its_singular_spelling():
    sentence = "I consulted with the CDC on widget safety."
    stage4 = _stage4(_entry("R", "Invited speaker, Center for Disease Control."), _entry("M2B"))
    assert lint_summary_unsupported_claim(_summary(sentence), stage4) == []


# ----------------------------------------------------------- what is judged

def test_only_a_generated_summary_is_judged():
    stage4 = _stage4(_entry("P"))
    for stage4_5 in (_summary(_PENDING_SENTENCE, method="existing_content"),
                     _summary(_PENDING_SENTENCE, method=None),
                     _summary(method=GENERATION_METHOD_LLM), {}):
        assert lint_summary_unsupported_claim(stage4_5, stage4) == []


def test_one_finding_per_claim_kind_naming_each_sentence():
    text = (_PENDING_SENTENCE, "A second application is pending with a foundation.",
            _MENTORING_SENTENCE)
    findings = lint_summary_unsupported_claim(_summary(*text), _stage4(_entry("M2A")))
    assert _claims(findings) == [f"claim={CLAIM_PENDING}", f"claim={CLAIM_MENTORING}"]
    assert len(findings[0]["evidence"]) == 3


def test_summary_unsupported_claim_runs_from_the_registry(tmp_path):
    for stage_dir, name, payload in (
            ("stage_4_field_extraction", "ABCDEF_fields.json", _stage4(_entry("M2A"))),
            ("stage_4_5_research_summary", "ABCDEF_research_summary.json",
             _summary(_PENDING_SENTENCE))):
        (tmp_path / stage_dir).mkdir()
        (tmp_path / stage_dir / name).write_text(json.dumps(payload))
    report = run_doctor(tmp_path, "ABCDEF")
    assert [f["lint"] for f in report["findings"]].count("summary_unsupported_claim") == 1
