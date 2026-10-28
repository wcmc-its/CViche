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
    CLAIM_DESIGN_DETAIL,
    CLAIM_ENTRY_ROLE,
    CLAIM_FUNDER,
    CLAIM_FUNDER_LINK,
    CLAIM_FUNDING,
    CLAIM_GRANT_STATUS,
    CLAIM_LEAD_ROLE,
    CLAIM_MENTORING,
    CLAIM_NOT_RECEIVED,
    CLAIM_PENDING,
    CLAIM_ROLE_SPAN,
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
    sentence = f"I currently serve as PI on {_TITLE}."
    ended_that_year = _stage4(_grant(start_date="2015-01", end_date="2018-06", role="PI"), _cv_year(2018))
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), ended_that_year)) == {
        CLAIM_GRANT_STATUS: [sentence]}


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


def test_funder_matches_by_initials_punctuation_or_a_known_spelling():
    for sentence, agency in ((f"I hold a National Widget Institute-funded award on {_TITLE}.", "NWI"),
                             (f"I hold an N.I.H./NHLBI-funded award on {_TITLE}.", "N.I.H/NHLBI"),
                             (f"I hold a PCORI-funded award on {_TITLE}.",
                              "Patient Centered Outcomes Research Institute"),
                             (f"I hold a Widget Foundation-funded award on {_TITLE}.", "WF")):
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
    for entry in (_entry("M1", "2010-present Widget study. Faculty mentored analysis of folding data."),
                  _entry("M1", "2009-present Gadget study, a project with dental students C. and D."),
                  _entry("K1", "Faculty advisor for five student projects on gadgets."),
                  _entry("K1", "Faculty Advisor and Investigator, undergraduate widget research."),
                  _entry("K1", "Supervised three medical students' theses.")):
        assert lint_summary_unsupported_claim(_summary(_MENTORING_SENTENCE), _stage4(entry)) == [], entry
    for own in (_entry("D3", "Research assistant, Widget Lab. Supervisor: C. D., Ph.D."),
                _entry("A1", "PhD, Widget Science, 2010. Thesis advisor: Prof. E. F."),
                _entry("A1", "MS, Gadget Studies, 2006. Research advisor: G. H."),
                _entry("P", "Member, Dean's Faculty Advisory Group, 2019-present.")):
        (finding,) = lint_summary_unsupported_claim(_summary(_MENTORING_SENTENCE), _stage4(own))
        assert _claims([finding]) == [f"claim={CLAIM_MENTORING}"], own


def test_presence_findings_stay_warn_and_per_claim_findings_info():
    text = (_MENTORING_SENTENCE, f"I currently serve as PI on {_TITLE}.")
    stage4 = _stage4(_grant("M2B", start_date="2010", end_date="2012", role="PI"), _cv_year(2026))
    findings = lint_summary_unsupported_claim(_dated_summary(*text), stage4)
    assert [(f["severity"], f["evidence"][0]) for f in findings] == [
        ("WARN", f"claim={CLAIM_MENTORING}"), ("INFO", f"claim={CLAIM_GRANT_STATUS}")]


_SHORT_TITLE = "Gadget Torque Calibration"  # three title words: the mention is the whole title


def test_a_two_word_title_needs_both_words():
    stage4 = _stage4(_grant("M2B", title="Gadget Torque", start_date="2010", end_date="2012"), _cv_year(2026))
    sentence = "I currently study gadget design across many sites."
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), stage4)) == {}


def test_a_status_word_far_after_the_grant_or_in_a_new_clause_is_not_its_status():
    stage4 = _stage4(_grant("M2B", title=_SHORT_TITLE), _cv_year(2026))
    for sentence in (f"I contributed to {_SHORT_TITLE}, and completed a study of sprockets.",
                     f"I contributed to {_SHORT_TITLE} with colleagues from many distant institutions "
                     "who are currently retired."):
        assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), stage4)) == {}, sentence


def test_the_nearest_status_word_before_the_grant_outranks_one_after_it():
    stage4 = _stage4(_grant("M2B", title=_SHORT_TITLE, start_date="2010", end_date="2012"), _cv_year(2026))
    sentence = f"Earlier in my career I helped run {_SHORT_TITLE}, currently archived."
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), stage4)) == {}


def test_a_title_word_that_is_a_status_word_is_the_titles():
    title = "Widget Folding Dynamics Ongoing Cohort"
    stage4 = _stage4(_grant("M2B", title=title, start_date="2010", end_date="2014"), _cv_year(2026))
    sentence = f"My {title} study ended in 2014."
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), stage4)) == {}


def test_a_count_just_before_the_awards_counts_a_declined_one():
    awards = [_grant("M2B", title=f"{year} Widget Fellowship Training Award", agency="Widget Fund",
                     **({"status": "Declined"} if year == "Second" else {}))
              for year in ("First", "Second")]
    sentence = "I held two Widget Fellowship Training Awards funded by the Widget Fund."
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), _stage4(*awards))) == {
        CLAIM_NOT_RECEIVED: [sentence]}
    said = "I held three Widget Fellowship Training Awards funded by the Widget Fund, one of which I declined."
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(said), _stage4(*awards))) == {}


def test_a_declined_award_in_a_sentence_about_no_funding_is_not_judged():
    stage4 = _stage4(_grant("M2B", title=_SHORT_TITLE, status="Not funded"))
    sentence = f"I studied {_SHORT_TITLE} with colleagues."
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), stage4)) == {}


def test_funder_list_scope_stops_at_the_next_role_or_an_unfunded_application():
    sentence = (f"I serve on an NIH-funded study of {_TITLE}, and as Site PI on {_OTHER_TITLE}, "
                "funded by Gadget Charities.")
    stage4 = _stage4(_grant(agency="NIH"), _grant(title=_OTHER_TITLE, agency="Gadget Charities"))
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), stage4)) == {}
    pending = f"My application on {_SHORT_TITLE}, funded by Gadget Charities, is pending."
    stage4 = _stage4(_grant("M2C", title=_SHORT_TITLE, agency="Widget Fund"))
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(pending), stage4)) == {}


def test_x_funded_credits_only_the_grant_it_names_next():
    sentence = f"I hold an NIH-funded award on {_SHORT_TITLE} and Sprocket Lever Housings."
    stage4 = _stage4(_grant(title=_SHORT_TITLE, agency="NIH"),
                     _grant(title="Sprocket Lever Housings", agency="Widget Fund"))
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), stage4)) == {}


def test_funder_matches_with_its_punctuation_squeezed_out():
    sentence = f"I hold a U.S. DOE-funded award on {_TITLE}."
    stage4 = _stage4(_grant(agency="U.S.DOE Office of Science"))
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), stage4)) == {}


def test_words_inside_a_longer_grants_name_are_not_a_grant_of_their_own():
    """Held-out web205: "advanced prostate cancer" inside a registry's full name."""
    sentence = ("As PI of the Widget Registry of Advanced Gadget Assembly Outcomes, funded by the "
                "Gadget Consortium, I study widgets.")
    stage4 = _stage4(_grant(title="Widget Registry of Advanced Gadget Assembly Outcomes", agency="Gadget Consortium"),
                     _grant(title="Widget Registry Outcomes Network", agency="Widget Fund"),
                     _grant(title="Advanced Gadget Assembly", agency="Other Fund"))
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), stage4)) == {}


def test_a_role_claim_does_not_reach_past_a_comma():
    sentence = f"I contributed to {_OTHER_TITLE}, on which I served as PI, and to Marine Pylon Corrosion."
    stage4 = _stage4(_grant(title=_OTHER_TITLE, role="PI"), _grant(title="Marine Pylon Corrosion", role="Co-I"))
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), stage4)) == {}


def test_two_listed_grants_are_not_chained_into_one_by_a_title_spanning_both():
    """WPJHYT-05: a third grant's title overlapping both listed ones must not
    make the unfunded one an alternative of the funded one."""
    sentence = ("I completed projects translating stem widget therapy, testing spray gadget delivery, "
                "funded by Gadget Charities.")
    stage4 = _stage4(_grant("M2B", title="Translating Stem Widget Therapy", start_date="2018", end_date="2020"),
                     _grant("M2B", title="Widget Therapy Testing Spray", agency="Other Fund",
                            start_date="2018", end_date="2020"),
                     _grant("M2B", title="Testing Spray Gadget Delivery", agency="Gadget Charities",
                            start_date="2018", end_date="2020"))
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), stage4)) == {
        CLAIM_FUNDER_LINK: [sentence]}


# ----------------------------------------------------------- per-claim checks, residual (#1554)

def test_a_current_claim_on_the_cvs_current_research_is_supported():
    """IZIXVF (clean): a "current" claim over a grant that ended before the CV
    restates the CV's own present research statement when an undated
    research-activities entry holds the naming words. Without that entry,
    with a dated one that ended, with an entry that is no research statement
    or holds only some of the words, the claim is still reported; so is
    "completed", and a claim on an agency's grants."""
    sentence = f"Ongoing projects now cover {_TITLE}."
    ended = _grant("M2B", start_date="2010", end_date="2015", role="PI")
    undated_research = _entry("M1", f"Present aims: {_TITLE}, and sprocket studies.")
    assert _per_claim(lint_summary_unsupported_claim(
        _dated_summary(sentence), _stage4(ended, undated_research, _cv_year(2026)))) == {}
    unsupported = (
        _entry("M1", f"{_TITLE}, 2010-2015."),
        _entry("K1", f"Lectures on {_TITLE}.", start_date="2016", end_date="present"),
        _entry("M1", "Present aims: widget folding in gadget models."),
    )
    for stage4 in (_stage4(ended, _cv_year(2026)), *(_stage4(ended, other, _cv_year(2026)) for other in unsupported)):
        assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), stage4)) == {
            CLAIM_GRANT_STATUS: [sentence]}
    completed = f"I completed {_TITLE}."
    undated = _grant("M2B")
    assert _per_claim(lint_summary_unsupported_claim(
        _dated_summary(completed), _stage4(undated, undated_research, _cv_year(2026)))) == {
        CLAIM_GRANT_STATUS: [completed]}
    agency = "My current Widget Gadget Foundation studies examine sprockets."
    funded = _grant("M2B", title=_OTHER_TITLE, agency="Widget Gadget Foundation", start_date="2010", end_date="2015")
    assert _per_claim(lint_summary_unsupported_claim(
        _dated_summary(agency), _stage4(funded, undated_research, _cv_year(2026)))) == {CLAIM_GRANT_STATUS: [agency]}
    assert _per_claim(lint_summary_unsupported_claim(_summary(sentence), _stage4(ended, undated_research))) == {}


def test_a_research_row_split_off_the_grants_own_text_does_not_support_current():
    """D9PLBD: stage 2 split a grant's "Project title" row into an undated
    research-activities entry. That row is the grant's own text, not a
    present research statement, so a "current" claim over the ended grant is
    still reported; a statement of its own still supports it."""
    sentence = f"My current investigations examine {_TITLE}."
    grant = _entry("M2B", f"Seed award: widget effects\nProject title: | {_TITLE}\nDuration: | 2021-2022",
                   title=_TITLE, start_date="2021-07-01", end_date="2022-06-30")
    row = _entry("M1", f"Project title: | {_TITLE}")
    assert _per_claim(lint_summary_unsupported_claim(
        _dated_summary(sentence), _stage4(grant, row, _cv_year(2026)))) == {CLAIM_GRANT_STATUS: [sentence]}
    statement = _entry("M1", f"My present aims: {_TITLE}.")
    assert _per_claim(lint_summary_unsupported_claim(
        _dated_summary(sentence), _stage4(grant, row, statement, _cv_year(2026)))) == {}


def test_completed_against_a_study_its_own_line_says_runs_on_or_closed_early():
    """MFJRKW-09 and HVSKET-03: the owner's dates ended, but the line says the
    study runs on under another PI, or closed early for lack of enrollment.
    A line saying the study closed as planned, a design word ("open-label"),
    an agent "active against" something or low enrollment with no closing
    states nothing against "completed"; "previously" is about the owner's
    part, and "current" is not judged by these words."""
    sentence = f"I was PI on a completed study of {_TITLE}."
    dates = {"start_date": "2003", "end_date": "2006", "role": "PI"}
    running = [f"The study {subject} {word} under a successor PI."
               for subject in ("is", "is currently", "is still", "are", "remains", "remain", "still", "currently")
               for word in ("active", "ongoing", "open", "enrolling", "recruiting")]
    closed = [f"Accrual {verb} early." for verb in ("closed", "terminated", "stopped", "halted", "ended",
                                                    "discontinued")]
    closed += [f"Accrual closed {reason} widget shortages." for reason in ("prematurely amid", "due to",
                                                                         "because of", "for lack of")]
    for note in (*running, *closed):
        grant = _entry("M2B", f"{_TITLE}. {note}", title=_TITLE, **dates)
        assert _per_claim(lint_summary_unsupported_claim(
            _dated_summary(sentence), _stage4(grant, _cv_year(2026)))) == {CLAIM_GRANT_STATUS: [sentence]}, note
    for words in ("The widget arm closed to accrual in 2006 as planned.", "An open-label, active-comparator design.",
                  "The trial is open-label.", "The gel is active against sprocket rust.",
                  "Low enrollment of coastal sites was expected."):
        finished = _entry("M2B", f"{_TITLE}. {words}", title=_TITLE, **dates)
        assert _per_claim(lint_summary_unsupported_claim(
            _dated_summary(sentence), _stage4(finished, _cv_year(2026)))) == {}, words
    runs_on = _entry("M2B", f"{_TITLE}. The study is currently active under a successor PI.", title=_TITLE, **dates)
    previously = f"I previously served as PI on {_TITLE}."
    assert _per_claim(lint_summary_unsupported_claim(
        _dated_summary(previously), _stage4(runs_on, _cv_year(2026)))) == {}
    current = _entry("M2A", f"{_TITLE}. The study is currently active.", title=_TITLE, start_date="2024",
                     end_date="2029", role="PI")
    assert _per_claim(lint_summary_unsupported_claim(
        _dated_summary(f"I currently lead {_TITLE}."), _stage4(current, _cv_year(2026)))) == {}


def test_earlier_against_a_publication_in_press():
    """QZCHAG-13: "earlier" for the CV's newest, in-press paper. A dated paper,
    a talk that mentions a paper in press, a current claim, or completed
    research whose paper is in press is left alone."""
    in_press = _entry("S1", f"Roe J. {_TITLE}. Widget Journal (in press).", title=_TITLE, year="in press")
    for sentence in (f"Before that, my earlier studies covered {_TITLE}.", f"I previously studied {_TITLE}.",
                     f"I formerly studied {_TITLE}.", f"My previous studies covered {_TITLE}."):
        assert _per_claim(lint_summary_unsupported_claim(
            _dated_summary(sentence), _stage4(in_press, _cv_year(2026)))) == {CLAIM_GRANT_STATUS: [sentence]}, sentence
    sentence = f"Before that, my earlier studies covered {_TITLE}."
    completed = f"I completed a study of {_TITLE}, now in press."
    assert _per_claim(lint_summary_unsupported_claim(
        _dated_summary(completed), _stage4(in_press, _cv_year(2026)))) == {}
    dated = _entry("S1", f"Roe J. {_TITLE}. Widget Journal 2015.", title=_TITLE, year="2015")
    talk = _entry("R", f"{_TITLE}. Widget Society, 2014; the paper is in press.", title=_TITLE, date="2014")
    for stage4 in (_stage4(dated, _cv_year(2026)), _stage4(talk, _cv_year(2026))):
        assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), stage4)) == {}
    current = f"My current studies cover {_TITLE} in new settings."
    assert _per_claim(lint_summary_unsupported_claim(
        _dated_summary(current), _stage4(in_press, _cv_year(2026)))) == {}


_LONG_TITLE = ("A Phase 2 multicenter randomized double-blind placebo-controlled study of the efficacy, safety and "
               "tolerability of widget gel and lever oil in adults for the relief of sprocket tremor with gadget "
               "stiffness, cog fatigue and pulley strain after gadget joint repair at coastal clinics")


def test_a_status_word_reaches_a_long_trial_title_by_eight_of_its_words():
    """HVSKET-03: a protocol title is never restated whole; eight of its words
    name it for a status claim, five do not. The funder check keeps the share rule."""
    grant = _entry("M2B", f"{_LONG_TITLE}. Accrual stopped early for slow recruitment.", title=_LONG_TITLE,
                   start_date="2017", end_date="2020", role="Coinvestigator", agency="Lever Trust")
    status = ("I was a coinvestigator on a completed multicenter, double-blind study of the efficacy of "
              "widget gel for sprocket tremor following gadget joint repair.")
    assert _per_claim(lint_summary_unsupported_claim(
        _dated_summary(status), _stage4(grant, _cv_year(2026)))) == {CLAIM_GRANT_STATUS: [status]}
    five_words = "I was a coinvestigator on a completed multicenter study of widget gel for gadget joint repair."
    assert _per_claim(lint_summary_unsupported_claim(
        _dated_summary(five_words), _stage4(grant, _cv_year(2026)))) == {}
    funder = ("I worked on a multicenter, double-blind study of the efficacy of widget gel for sprocket "
              "tremor following gadget joint repair, funded by Gadget Charities.")
    assert _per_claim(lint_summary_unsupported_claim(
        _dated_summary(funder), _stage4(grant, _cv_year(2026)))) == {}


def test_design_words_alone_name_no_long_trial_title():
    """A long protocol title is not named by its design words: a completed
    trial described in protocol language is not the current trial whose
    title shares that language, unless the sentence holds three of the
    current trial's own words."""
    current = _entry("M2B", "Current protocol.", start_date="2024", end_date="2029", role="Site PI",
                     title=("A multicenter double-blind placebo-controlled parallel-group study to evaluate the "
                            "efficacy, safety and tolerability of widget gel in adults with sprocket rust of the "
                            "lever housing under coastal salt exposure at harbour depots"))
    done = _entry("M2B", "Leverol for Gadget Valve Wear.", title="Leverol for Gadget Valve Wear", start_date="2015",
                  end_date="2017", role="Site PI")
    design = ("multicenter double-blind placebo-controlled parallel-group study to evaluate the efficacy, safety "
              "and tolerability")
    # Two of the current trial's own words, so each design word counts.
    sentence = f"I completed a {design} of widget gel in adults for gadget valve wear."
    assert _per_claim(lint_summary_unsupported_claim(
        _dated_summary(sentence), _stage4(current, done, _cv_year(2026)))) == {}
    named = f"I completed a {design} of widget gel in adults with sprocket rust."
    assert _per_claim(lint_summary_unsupported_claim(
        _dated_summary(named), _stage4(current, done, _cv_year(2026)))) == {CLAIM_GRANT_STATUS: [named]}
    short = _entry("M2B", "Placebo Safety of Widget Gel.", title="Placebo Safety of Widget Gel", start_date="2024",
                   end_date="2029")
    uncapped = "I completed a placebo safety study of widget gel."
    assert _per_claim(lint_summary_unsupported_claim(
        _dated_summary(uncapped), _stage4(short, _cv_year(2026)))) == {CLAIM_GRANT_STATUS: [uncapped]}


def test_eight_words_in_a_row_name_a_long_title_and_seven_do_not():
    words = ["Widget", "Gadget", "Sprocket", "Lever", "Pulley", "Torque", "Bracket", "Hinge"]
    title = " ".join(words + ["Spindle", "Ratchet", "Flange", "Gasket", "Washer", "Rivet", "Grommet", "Sleeve",
                              "Collar", "Bushing"])
    current = _entry("M2B", f"{title}.", title=title, start_date="2024", end_date="2029")
    eight = f"I completed a study of {' '.join(words)} wear."
    assert _per_claim(lint_summary_unsupported_claim(
        _dated_summary(eight), _stage4(current, _cv_year(2026)))) == {CLAIM_GRANT_STATUS: [eight]}
    seven = f"I completed a study of {' '.join(words[:7])} wear."
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(seven), _stage4(current, _cv_year(2026)))) == {}


def test_a_funder_the_cv_names_only_as_a_collaborator():
    """DYLJXC-16: stage 4 put the collaborating body in the grant's agency; the
    line itself names it after a collaboration cue. A text that names it as a
    funder anywhere, or no text naming it, leaves the sentence alone; the
    findings keep the summary's order."""
    sentence = f"I was Co-PI on a Lever Alliance-funded study of {_TITLE}."
    grant = _entry("M2B", f"Agency award in collaboration with the Lever Alliance. {_TITLE}.",
                   title=_TITLE, agency="Agency, Lever Alliance", role="Co-PI", start_date="2016", end_date="2018")
    service = _entry("Q2", "Joint gauge survey, collaborating with the Lever Alliance.")
    assert _per_claim(lint_summary_unsupported_claim(
        _dated_summary(sentence), _stage4(grant, service))) == {CLAIM_FUNDER_LINK: [sentence]}
    funded = _entry("M2B", "Pilot award funded by the Lever Alliance.", title="Pilot award")
    assert _per_claim(lint_summary_unsupported_claim(
        _dated_summary(sentence), _stage4(grant, service, funded))) == {}
    unnamed = "I lead Sprocket Society-funded work on several topics."
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(unnamed), _stage4(grant, service))) == {}
    other = _grant(title=_OTHER_TITLE, agency="Lever Trust", role="PI")
    linked = f"I lead {_OTHER_TITLE}, funded by Gadget Charities."
    assert _per_claim(lint_summary_unsupported_claim(
        _dated_summary(sentence, linked), _stage4(grant, service, other))) == {CLAIM_FUNDER_LINK: [sentence, linked]}


def test_a_funder_named_by_its_words_on_the_grant_line_holds():
    sentence = f"I led {_SHORT_TITLE}, funded by the Widget Lever Foundation."
    grant = _entry("M2B", f"{_SHORT_TITLE}. Supported by the Foundation for Widget and Lever Studies.",
                   title=_SHORT_TITLE, role="PI", start_date="2016", end_date="2018")
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), _stage4(grant))) == {}
    other = _entry("M2B", f"{_SHORT_TITLE}. Supported by the Sprocket Trust.", title=_SHORT_TITLE, role="PI",
                   start_date="2016", end_date="2018")
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), _stage4(other))) == {
        CLAIM_FUNDER_LINK: [sentence]}


def test_a_collaborators_words_split_on_the_grant_line_do_not_make_it_a_funder():
    """DYLJXC-16's shape: the grant line's cue names the body with a date run
    into its name, so the rest of its words stand outside the cue. Its words
    are not its name: the body is still named only as a collaborator."""
    sentence = f"I was Co-PI on a Gadget Lever Alliance-funded study of {_TITLE}."
    grant = _entry("M2B", f"Agency award in collaboration with the Gadget Lever 2016-2018 Alliance. {_TITLE}, a "
                          "gadget lever study.", title=_TITLE, agency="Agency, Gadget Lever Alliance", role="Co-PI",
                   start_date="2016", end_date="2018")
    service = _entry("Q2", "Site visit, collaborating with the Gadget Lever Alliance.")
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), _stage4(grant, service))) == {
        CLAIM_FUNDER_LINK: [sentence]}


def test_a_funder_named_by_name_outside_a_collaboration_cue_is_a_funder():
    """A grant line naming the funder by its acronym, or by a known funder's
    other spelling, funds by it, although another entry names it after a
    collaboration cue. A partnership is a collaboration cue too."""
    full = "I led Widget Folding Dynamics in Gadget Assembly Lines, funded by the National Widget Institute."
    by_acronym = _entry("M2B", f"{_TITLE}. Agency: NWI.", title=_TITLE, agency="NWI", role="PI",
                        start_date="2016", end_date="2018")
    talk = _entry("R", "Talk in collaboration with the National Widget Institute, 2017.")
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(full), _stage4(by_acronym, talk))) == {}
    nih = "I led Widget Folding Dynamics in Gadget Assembly Lines, funded by the NIH."
    spelled = _entry("M2B", f"{_TITLE}. National Institutes of Health award.", title=_TITLE,
                     agency="National Institutes of Health", role="PI", start_date="2016", end_date="2018")
    workshop = _entry("Q2", "Workshop in collaboration with NIH, 2017.")
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(nih), _stage4(spelled, workshop))) == {}
    partner = f"I was Co-PI on a Lever Alliance-funded study of {_TITLE}."
    grant = _entry("M2B", f"Agency award in partnership with the Lever Alliance. {_TITLE}.", title=_TITLE,
                   agency="Agency, Lever Alliance", role="Co-PI", start_date="2016", end_date="2018")
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(partner), _stage4(grant))) == {
        CLAIM_FUNDER_LINK: [partner]}


def test_a_shared_lead_role_still_gives_the_lead_role():
    """XNWSZN-15, hand read: a founder "jointly with" a colleague, a
    co-director, a role directed "along with" someone, or a sole role next to
    a co-chair still founded, directs or led the work, so the claim holds;
    so do the role words of a president, chief or organizer. A role naming no
    lead is still reported. A claim does not reach across the comma before a
    quoted title."""
    held = (
        (f"I founded {_TITLE} with widget makers.",
         _entry("M2A", f'"{_TITLE}." Founder jointly with Dr. Roe.', title=_TITLE, pi_role="Founder")),
        (f"I direct {_TITLE} across three sites.", _grant(role="Co-Director")),
        (f"I direct {_TITLE} across three sites.",
         _entry("M2A", f"{_TITLE}. Clinic directed along with Dr. Roe.", title=_TITLE, role="Director")),
        (f"I led {_TITLE} across the state.", _grant(role="PI; Co-Chair, Steering Committee")),
        (f"I led {_TITLE} across the state.", _grant(role="Co-Lead")),
        (f'I founded a multi-site study, "{_TITLE}," with widget makers.', _grant(role="Member")),
        *((f"I led {_TITLE} across the state.", _grant(role=role))
          for role in ("President", "Chief Engineer", "Organizer", "Organiser")),
    )
    for sentence, grant in held:
        assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), _stage4(grant))) == {}, sentence
    member = f"I led {_TITLE} across the state."
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(member), _stage4(_grant(role="Member")))) == {
        CLAIM_LEAD_ROLE: [member]}


_EVENT = "Gadget Lever Forum"


def _lead_findings(sentence, *entries):
    return _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), _stage4(*entries)))


def test_a_lead_role_on_a_named_entry_needs_that_role_on_an_entry_holding_the_name():
    """A founding and a directing claim on an event the CV names the owner the
    founder of, never the director. A director entry, a director's heading, a
    co-director entry, a founding claim alone, or a name no entry holds leaves
    the sentence alone."""
    talks = (_entry("R", f"Gadget talk at the inaugural {_EVENT}, 2019; role: founder and speaker.",
                    role="Founder and Speaker"),
             _entry("R", f"Widget talk, {_EVENT}, 2019; role: speaker.", role="Speaker"))
    sentence = f"I founded and directed the {_EVENT} each spring."
    assert _lead_findings(sentence, *talks) == {CLAIM_ENTRY_ROLE: [sentence]}
    assert _lead_findings(sentence, *talks, _entry("O", f"Director, {_EVENT}, 2019-present.")) == {}
    assert _lead_findings(sentence, *talks, _entry("O", f"{_EVENT}, 2019-present.", hierarchy=("Directorships",))) == {}
    assert _lead_findings(sentence, *talks, _entry("O", f"Co-Director, {_EVENT}, 2019-present.")) == {}
    assert _lead_findings(f"I founded the {_EVENT}.", *talks) == {}
    assert _lead_findings("I founded and directed the Marine Sprocket Torque Workshop.", *talks) == {}


_LONG_EVENT = "Coastal Gadget Lever Torque Forum"


def test_an_entry_naming_the_event_its_own_way_gives_its_role():
    """OKRTPJ-13, hand read: the talks name the event in full and the owner its
    founder; a leadership line names it without one word and gives founder
    and director, so neither claim is reported. A line sharing too few of the
    name's words gives nothing."""
    talks = (_entry("R", f"Widget talk, {_LONG_EVENT}, 2019. Founder and speaker.", role="Founder and Speaker"),)
    sentence = f"I founded and direct the {_LONG_EVENT}."
    assert _lead_findings(sentence, *talks) == {CLAIM_ENTRY_ROLE: [sentence]}
    # The role is in the line's field alone: an entry's fields are read too.
    leadership = _entry("O", "1st Gadget Lever Torque meeting, 2019.", leadership_role="Founder and Director")
    assert _lead_findings(sentence, *talks, leadership) == {}
    too_few = _entry("O", "Gadget Lever club, 2019. Director.", leadership_role="Director")
    assert _lead_findings(sentence, *talks, too_few) == {CLAIM_ENTRY_ROLE: [sentence]}


def test_each_lead_verb_reads_its_own_role_words():
    """A head directs; an establisher, creator or co-founder founded; a
    president, chief or organizer led. A member did none of these."""
    cases = (("direct", "Head"), ("founded", "Established"), ("founded", "Creator"), ("founded", "Co-Founder"),
             ("founded", "Cofounder"), ("led", "President"), ("led", "Chief"), ("led", "Organizer"))
    for verb, role in cases:
        claim = f"I {verb} the {_EVENT} each spring."
        assert _lead_findings(claim, _entry("O", f"{role}, {_EVENT}, 2019-present.")) == {}, role
        assert _lead_findings(claim, _entry("O", f"Member, {_EVENT}, 2019-present.")) == {
            CLAIM_ENTRY_ROLE: [claim]}, verb


def test_an_entry_sharing_part_of_the_name_neither_holds_it_nor_gives_its_role():
    """With no entry holding the whole name, nothing is judged; and a short
    name's role comes only from an entry holding all of its words."""
    sentence = f"I founded and directed the {_EVENT} each spring."
    club = _entry("O", "Director, Gadget Lever club, 2019-present.")
    assert _lead_findings(sentence, club) == {}
    talks = (_entry("R", f"Gadget talk at the inaugural {_EVENT}, 2019; role: founder and speaker.",
                    role="Founder and Speaker"),)
    assert _lead_findings(sentence, *talks, club) == {CLAIM_ENTRY_ROLE: [sentence]}


def test_led_needs_any_lead_role_and_a_name_needs_three_title_words():
    led = f"I led the {_EVENT} for a decade."
    assert _lead_findings(led, _entry("O", f"Chair, {_EVENT}, 2010-2020.")) == {}
    assert _lead_findings(led, _entry("O", f"Member, {_EVENT}, 2010-2020.")) == {CLAIM_ENTRY_ROLE: [led]}
    short = "I direct the Widget Rounds every month."
    assert _lead_findings(short, _entry("R", "Widget Rounds, 2019. Speaker.")) == {}
    for unnamed, holder in ((f"I led several {_EVENT} sessions.", f"Member, {_EVENT}, 2010-2020."),
                            (f"I led coastal {_EVENT} sessions.", f"Member, coastal {_EVENT}, 2010-2020.")):
        assert _lead_findings(unnamed, _entry("O", holder)) == {}, unnamed
    founded = f"I founded the {_EVENT} in 2010."
    assert _lead_findings(founded, _entry("O", f"Chair, {_EVENT}, 2010-2020.")) == {CLAIM_ENTRY_ROLE: [founded]}


def test_a_pi_role_dated_before_the_earliest_grant_that_could_give_it():
    """WWRSAL-02: a PI role "since" a year before the earliest grant the owner
    may lead. An earlier or same-year PI grant, or no dated grant, leaves it
    alone; a co-investigator grant, a pending application or an undated PI
    grant does not date a PI role."""
    sentence = "I have worked on widget folding as Principal Investigator since 2007."
    later = _grant(start_date="2010-06", agency="Widget Fund")
    co_i = _grant(title=_OTHER_TITLE, start_date="2005", role="Co-Investigator")
    pending = _grant("M2C", title="Marine Pylon Corrosion Assays", start_date="2004", role="PI")
    undated = _grant(title="Lever Housing Torque Survey", role="PI")
    assert _per_claim(lint_summary_unsupported_claim(
        _dated_summary(sentence), _stage4(later, co_i, pending, undated))) == {CLAIM_ROLE_SPAN: [sentence]}
    for earlier_start in ("2006", "2007"):
        earlier = _grant(title=_OTHER_TITLE, start_date=earlier_start, role="PI")
        assert _per_claim(lint_summary_unsupported_claim(
            _dated_summary(sentence), _stage4(later, earlier))) == {}, earlier_start
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), _stage4(_grant()))) == {}


def test_a_shared_pi_span_is_not_judged_and_a_short_start_year_dates_a_pi_grant():
    """A co-, sub- or site PI's span claims no lead role, so the PI grants do
    not date it (nor, as before, an MPI's). A start date written mm/dd/yy
    dates its grant as stage 6 reads it. With no summary date, a start year
    has no century, and nothing is judged."""
    later = _grant(start_date="2012", role="PI", agency="Widget Fund")
    for role in ("Co-PI", "co-PI", "Co PI", "Site PI", "site-PI", "Sub-PI", "Co-Principal Investigator", "MPI"):
        shared = f"I have studied widget folding as {role} since 2007."
        assert _per_claim(lint_summary_unsupported_claim(_dated_summary(shared), _stage4(later))) == {}, role
    sentence = "I have worked on widget folding as Principal Investigator since 2007."
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), _stage4(later))) == {
        CLAIM_ROLE_SPAN: [sentence]}
    short = _grant(title=_OTHER_TITLE, start_date="9/1/07", role="PI")
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), _stage4(later, short))) == {}
    short_later = _grant(title=_OTHER_TITLE, start_date="9/1/09", role="PI")
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), _stage4(short_later))) == {
        CLAIM_ROLE_SPAN: [sentence]}
    assert _per_claim(lint_summary_unsupported_claim(_summary(sentence), _stage4(later))) == {}


def test_a_design_agent_another_grants_line_holds():
    """TJDPJE-01: one trial given another trial's co-therapy. The named trial's
    own line holding the agent, no line holding it, a comparator that is no
    agent, or an agent far past the trial's name leaves it alone."""
    trial = _grant("M2B", title="WGT (Widgetamab in Gadget Valve Stenosis)", start_date="2012", end_date="2012")
    other = _grant("M2B", title="SPROCKET (Phase III trial of sprocketin and leverol versus placebo for gadget wear)",
                   start_date="1995", end_date="1996")
    sentence = "I took part in the WGT trial of widgetamab in combination with leverol in gadget valve stenosis."
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), _stage4(trial, other))) == {
        CLAIM_DESIGN_DETAIL: [sentence]}
    own = _entry("M2B", "WGT (Widgetamab and leverol in Gadget Valve Stenosis).",
                 title="WGT (Widgetamab in Gadget Valve Stenosis)", start_date="2012", end_date="2012")
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), _stage4(own, other))) == {}
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(sentence), _stage4(trial))) == {}
    for held in ("I took part in the WGT trial of widgetamab in combination with placebo in gadget valve stenosis.",
                 "I took part in the WGT trial of widgetamab in gadget valve stenosis and, in separate bench "
                 "work over many years, sprocketin combined with leverol.",
                 "I took part in the WGT trial of widgetamab plus leverol in gadget valve stenosis."):
        assert _per_claim(lint_summary_unsupported_claim(_dated_summary(held), _stage4(trial, other))) == {}, held
    combined = "I took part in the WGT trial of widgetamab combined with leverol in gadget valve stenosis."
    assert _per_claim(lint_summary_unsupported_claim(_dated_summary(combined), _stage4(trial, other))) == {
        CLAIM_DESIGN_DETAIL: [combined]}
