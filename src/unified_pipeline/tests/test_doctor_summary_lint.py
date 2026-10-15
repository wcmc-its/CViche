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
    CLAIM_FUNDER_LINK,
    CLAIM_FUNDING,
    CLAIM_GRANT_STATUS,
    CLAIM_LEAD_ROLE,
    CLAIM_MENTORING,
    CLAIM_NOT_RECEIVED,
    CLAIM_PENDING,
    lint_summary_unsupported_claim,
    summary_sentences,
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


# ----------------------------------------------------------- sentence splitting

def test_sentences_split_at_end_punctuation_before_a_capital():
    assert summary_sentences("I study widgets.  My work spans gadgets! Is it new? yes. Last.") == [
        "I study widgets.", "My work spans gadgets!", "Is it new? yes.", "Last."]


def test_an_abbreviation_does_not_end_a_sentence():
    """#1592: the YUYVIG audit judged halves of sentences cut at these."""
    cases = {
        "dotted acronym": "I trained in the U.S. Army Medical Corps.",
        "lowercase dotted": "I study gadgets, e.g. Widget folding.",
        "degree": "I hold a Ph.D. And an M.D. From two schools.",
        "middle initial": "I worked with Jane Q. Roe on widgets.",
        "title": "I trained at St. Widget Hospital with Dr. Roe.",
        "citation": "As Roe et al. Showed, widgets fold.",
    }
    for why, sentence in cases.items():
        assert summary_sentences(f"{sentence} Then I moved.") == [sentence, "Then I moved."], why
    # A company suffix ends its sentence as often as not, so it still splits.
    assert summary_sentences("It was funded by Widget Inc. I then moved.") == [
        "It was funded by Widget Inc.", "I then moved."]


def test_an_abbreviation_split_no_longer_hides_a_funding_claim_from_its_sentence():
    """The finding quotes the whole sentence, not the piece after the abbreviation."""
    sentence = "My U.S. Widget Agency work is funded by grants."
    (finding,) = lint_summary_unsupported_claim(_summary(sentence), _stage4(_entry("P")))
    assert finding["evidence"] == [f"claim={CLAIM_FUNDING}", f"sentence={sentence}"]


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


# ----------------------------------------------------------- per-claim checks (#1554, YUYVIG/NECRGC)

_WRITTEN = "2026-10-01T09:00:00"
_TITLE = "Widget Folding Dynamics in Gadget Assembly Lines"
_OTHER_TITLE = "Sprocket Torque Calibration for Lever Housings"


def _dated_summary(*sentences, written=_WRITTEN):
    summary = _summary(*sentences)
    summary["research_summary"]["generation_timestamp"] = written
    return summary


def _per_claim(findings):
    """{claim kind: sentences} of the INFO (per-claim) findings."""
    return {f["evidence"][0].removeprefix("claim="): [e.removeprefix("sentence=") for e in f["evidence"][1:]]
            for f in findings if f["severity"] == "INFO"}


def _grant(code="M2A", title=_TITLE, **fields):
    return _entry(code, f"{title}.", title=title, **fields)


def _cv_year(year):
    """A non-grant entry dating the CV to `year`."""
    return _entry("K1", f"Widget lectures, {year}.", year=str(year))


def test_current_claim_on_an_ended_grant_is_reported_at_info():
    sentence = f"I currently serve as PI on {_TITLE}."
    findings = lint_summary_unsupported_claim(
        _dated_summary(sentence), _stage4(_grant("M2B", start_date="2015", end_date="2020", role="PI"),
                                          _cv_year(2026)))
    (finding,) = findings
    assert finding["severity"] == "INFO"
    assert finding["evidence"] == [f"claim={CLAIM_GRANT_STATUS}", f"sentence={sentence}"]


def test_status_claims_against_the_grants_record():
    open_grant = _grant(start_date="2021", end_date="present")
    ended = _grant("M2B", start_date="2015", end_date="2019")
    undated = _grant("M2B")
    under_current_heading = _grant("M2B", hierarchy=("Funding", "Current Support"))
    reported = (
        (f"My completed work includes {_TITLE}.", open_grant),            # SPINJC-05
        (f"I completed {_TITLE}.", undated),                               # FMIGLR-07
        (f"I currently lead {_TITLE}.", undated),                          # RLADNC-10
        (f"I continue work on {_TITLE}.", ended),                          # ZDOAZO-01
        (f"My earlier work included {_TITLE}.", under_current_heading),
    )
    for sentence, grant in reported:
        found = _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence),
                                                          _stage4(grant, _cv_year(2026))))
        assert found == {CLAIM_GRANT_STATUS: [sentence]}, sentence
    held = (
        (f"I currently serve on {_TITLE}.", open_grant),
        (f"I completed {_TITLE}.", ended),
        (f"I currently serve on {_TITLE}.", under_current_heading),
        (f"My work on {_TITLE} is described here.", undated),  # no status word
    )
    for sentence, grant in held:
        assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence),
                                                         _stage4(grant, _cv_year(2026)))) == {}, sentence


def test_month_precision_end_date_has_ended_in_the_summary_year():
    """UAURFU-05: a grant ending in February is no longer current in October."""
    sentence = f"I currently serve as PI on {_TITLE}."
    stage4 = _stage4(_grant(start_date="2021-03", end_date="2026-02-28", role="PI"), _cv_year(2026))
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), stage4)) == {
        CLAIM_GRANT_STATUS: [sentence]}
    stage4 = _stage4(_grant(start_date="2021-03", end_date="2026-12-31", role="PI"), _cv_year(2026))
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), stage4)) == {}


def test_an_old_cv_is_judged_as_of_its_own_date_for_current_and_now_for_completed():
    """HXBPCT-17 / YSXCFU: a 2018 CV's 2017-2020 grant was current then, and is completed now."""
    grant = _grant(start_date="2017", end_date="2020", role="PI")
    for sentence in (f"I currently serve as PI on {_TITLE}.", f"I completed {_TITLE}."):
        stage4 = _stage4(grant, _cv_year(2018))
        assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), stage4)) == {}, sentence


def test_status_is_not_judged_without_a_summary_date():
    sentence = f"I currently serve as PI on {_TITLE}."
    stage4 = _stage4(_grant("M2B", start_date="2015", end_date="2020", role="PI"))
    assert _per_claim(lint_summary_unsupported_claim(_summary(sentence), stage4)) == {}


def test_a_status_word_framing_a_topic_or_in_the_title_is_not_a_claim():
    ended = _grant("M2B", start_date="2010", end_date="2014")
    titled = _grant("M2B", title="Current Widget Folding Dynamics in Gadget Assembly",
                    start_date="2010", end_date="2014")
    for sentence, grant in (
            (f"My current research investigates {_TITLE}.", ended),       # NJHLUR, XNWSZN
            (f"This work continuous with {_TITLE}.", ended),               # not a status word
            ("I studied Current Widget Folding Dynamics in Gadget Assembly.", titled)):
        stage4 = _stage4(grant, _cv_year(2026))
        assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), stage4)) == {}, sentence


def test_a_status_word_after_the_grant_in_a_new_clause_is_not_its_status():
    sentence = f"I contributed to {_TITLE}, and completed a study of sprockets."
    stage4 = _stage4(_grant("M2B"), _cv_year(2026))
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), stage4)) == {}


def test_two_grants_named_by_the_same_words_are_alternatives():
    """A renewal and its parent: the sentence is right if it is right about either."""
    sentence = f"I currently serve as PI on {_TITLE}."
    stage4 = _stage4(_grant("M2B", start_date="2010", end_date="2015", role="PI"),
                     _grant("M2A", title=f"{_TITLE} Renewal", start_date="2016", end_date="present",
                            role="PI"),
                     _cv_year(2026))
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), stage4)) == {}


def test_status_against_every_grant_of_an_agency_named_with_a_funding_word():
    """SPINJC-05: "several completed <agency> studies" when that agency's grants are all current."""
    sentence = "I also consult on several completed St. Widget Hospital studies of gadgets."
    current = [_grant(title=t, agency="St. Widget Hospital", start_date="2024", end_date="present")
               for t in (_TITLE, _OTHER_TITLE)]
    assert _per_claim(lint_summary_unsupported_claim(
        _dated_summary(sentence), _stage4(*current, _cv_year(2026)))) == {CLAIM_GRANT_STATUS: [sentence]}
    one_completed = [current[0], _grant("M2B", title=_OTHER_TITLE, agency="St. Widget Hospital",
                                        start_date="2010", end_date="2012")]
    assert _per_claim(lint_summary_unsupported_claim(
        _dated_summary(sentence), _stage4(*one_completed, _cv_year(2026)))) == {}
    post = "I currently direct a clinic at St. Widget Hospital on weekends."
    assert _per_claim(lint_summary_unsupported_claim(
        _dated_summary(post), _stage4(_grant("M2B", agency="St. Widget Hospital"), _cv_year(2026)))) == {}


def test_counting_a_declined_award_is_reported():
    """ATRKVV-02: "three consecutive" awards over a year the CV marks DECLINED."""
    sentence = "My training was supported by three consecutive Widget Fellowship Training Awards."
    awards = [_grant("M2B", title=f"{year} Year Widget Fellowship Training Award", agency="Widget Fund",
                     **({"status": "DECLINED"} if year == "Second" else {}))
              for year in ("First", "Second", "Third")]
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), _stage4(*awards))) == {
        CLAIM_NOT_RECEIVED: [sentence]}
    said = "My training was supported by Widget Fellowship Training Awards, one of which I declined."
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(said), _stage4(*awards))) == {}
    uncounted = "My training was supported by Widget Fellowship Training Awards."
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(uncounted), _stage4(*awards))) == {}


def test_a_declined_award_named_alone_is_reported():
    sentence = f"This work was funded by {_TITLE}."
    stage4 = _stage4(_grant("M2B", hierarchy=("Grants", "Submitted, Not Funded")))
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), stage4)) == {
        CLAIM_NOT_RECEIVED: [sentence]}


def test_funded_by_credits_every_grant_of_the_list_before_it():
    """WPJHYT-05: three projects "funded by" the charity that funds two of them."""
    third = "Marine Corrosion Assays for Coastal Pylons"
    sentence = (f"I completed projects on {_TITLE}, {_OTHER_TITLE}, and {third}, "
                "funded by Gadget Charities.")
    done = {"start_date": "2018", "end_date": "2020"}
    grants = (_grant("M2B", agency="Gadget Charities", **done),
              _grant("M2B", title=_OTHER_TITLE, agency="Gadget Charities", **done))
    unnamed = _grant("M2B", title=third, **done)
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), _stage4(*grants, unnamed))) == {
        CLAIM_FUNDER_LINK: [sentence]}
    named = _grant("M2B", title=third, agency="Gadget Charities", **done)
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), _stage4(*grants, named))) == {}


def test_funder_link_holds_for_each_grant_with_its_own_funder():
    sentence = (f"I serve on {_TITLE}, funded by the Widget Foundation, and on {_OTHER_TITLE}, "
                "funded by the Sprocket Trust.")
    stage4 = _stage4(_grant(agency="Widget Foundation"), _grant(title=_OTHER_TITLE, agency="Sprocket Trust"))
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), stage4)) == {}


def test_pre_funded_phrase_credits_the_grant_after_it():
    sentence = f"I lead an NIH-funded study of {_TITLE}."
    for agency in ("NIH/NIAMS", "National Institutes of Health"):
        stage4 = _stage4(_grant(agency=agency, role="PI"))
        assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), stage4)) == {}, agency
    stage4 = _stage4(_grant(agency="Widget Foundation", role="PI"))
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), stage4)) == {
        CLAIM_FUNDER_LINK: [sentence]}


def test_funder_matches_by_initials_or_punctuation():
    for sentence, agency in ((f"I hold a National Widget Institute-funded award on {_TITLE}.", "NWI"),
                             (f"I hold an N.I.H./NHLBI-funded award on {_TITLE}.", "N.I.H/NHLBI")):
        stage4 = _stage4(_grant(agency=agency))
        assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), stage4)) == {}, agency


def test_funder_link_is_not_judged_for_shared_funding_or_unfunded_grants():
    shared = f"NIH- and NSF-funded projects include {_TITLE}."
    assert _per_claim(lint_summary_unsupported_claim(
        _dated_summary(shared), _stage4(_grant(agency="NIH")))) == {}
    pending = f"My application on {_TITLE}, funded by Gadget Charities, is pending."
    assert _per_claim(lint_summary_unsupported_claim(
        _dated_summary(pending), _stage4(_grant("M2C", agency="Widget Fund")))) == {}
    far = f"I study {_TITLE} and many other things across several sites, funded by Gadget Charities."
    assert _per_claim(lint_summary_unsupported_claim(
        _dated_summary(far), _stage4(_grant(agency="Widget Fund")))) == {}


def test_lead_role_claim_needs_a_lead_role_on_the_grant():
    """FCAAUV-07: "I led" over grant lines that give no role."""
    reported = ((f"I led {_TITLE} across the state.", {}),
                (f"I serve as Principal Investigator on {_TITLE}.", {"role": "Co-Investigator"}),
                (f"I serve as PI on {_TITLE}.", {"role": "Site PI"}))
    for sentence, role in reported:
        stage4 = _stage4(_grant(**role))
        assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), stage4)) == {
            CLAIM_LEAD_ROLE: [sentence]}, sentence
    held = ((f"I led {_TITLE} across the state.", {"role": "Program Director"}),
            (f"I serve as PI on {_TITLE}.", {"pi_role": "Principle Investigator"}),
            (f"I serve as PI on {_TITLE}.", {"role": "MPI"}),
            (f"I contributed to {_TITLE}, on which I served as PI, and more.", {"role": "Co-I"}),
            (f"She is a leading investigator in {_TITLE}.", {}),
            (f"I serve as Site PI on {_TITLE}.", {"role": "Site PI"}))
    for sentence, role in held:
        stage4 = _stage4(_grant(**role))
        assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), stage4)) == {}, sentence


def test_a_short_or_scattered_title_names_no_grant():
    stage4 = _stage4(_grant("M2B", title="Widgets", start_date="2010", end_date="2012"),
                     _grant("M2B", start_date="2010", end_date="2012"), _cv_year(2026))
    sentence = ("I currently study widgets with several colleagues from three other departments, "
                "folding paper models across many distinct sites, and track dynamics of many unrelated "
                "kinds throughout every single season.")
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), stage4)) == {}


def test_owner_mentoring_in_research_activities_or_advising_supports_a_mentoring_claim():
    """HBALTD and HNQBOI: the lint called a mentoring sentence unsupported."""
    for entry in (_entry("M1", "2010-present Widget study. Faculty mentored project with resident A. B."),
                  _entry("K1", "Faculty advisor for five student projects on gadgets."),
                  _entry("K1", "Supervised three medical students' theses.")):
        assert lint_summary_unsupported_claim(_summary(_MENTORING_SENTENCE), _stage4(entry)) == [], entry
    supervisor = _entry("D3", "Research assistant, Widget Lab. Supervisor: C. D., Ph.D.")
    (finding,) = lint_summary_unsupported_claim(_summary(_MENTORING_SENTENCE), _stage4(supervisor))
    assert _claims([finding]) == [f"claim={CLAIM_MENTORING}"]


def test_presence_findings_stay_warn_and_per_claim_findings_info():
    text = (_MENTORING_SENTENCE, f"I currently serve as PI on {_TITLE}.")
    stage4 = _stage4(_grant("M2B", start_date="2010", end_date="2012", role="PI"), _cv_year(2026))
    findings = lint_summary_unsupported_claim(_dated_summary(*text), stage4)
    assert [(f["severity"], f["evidence"][0]) for f in findings] == [
        ("WARN", f"claim={CLAIM_MENTORING}"), ("INFO", f"claim={CLAIM_GRANT_STATUS}")]
