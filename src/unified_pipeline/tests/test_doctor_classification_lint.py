"""Tests for doctor/lints/classification.py: stage-3b codes against their own
headings, text and siblings (EBYSBC E11/E30).

Fixtures are invented: no name, institution or citation here comes from a CV.

    python3 -m pytest src/unified_pipeline/tests/test_doctor_classification_lint.py -p no:cacheprovider
"""

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.doctor.lints.classification import (  # noqa: E402
    S5_MIN_SIBLINGS,
    SECTION_CONSISTENCY_EVIDENCE_MAX,
    lint_pinned_heading_empty,
    lint_section_consistency,
)
from unified_pipeline.stage3b.header_pin import HEADER_PIN_MIN_CONFIDENCE  # noqa: E402

_ARTICLE = "Quill AB, Brook CD. A trial of things. J Invented Med. 2019;12(3):101-110."
_GUIDELINE = "Panel EF, Quill AB. Practice guideline for widgets. J Invented Med. 2021;14(1):82-113."


def _entry(text, code, *headings, idx=10):
    return {"element_idx_start": idx, "text": text, "taxonomy_code": code,
            "hierarchy": list(headings)}


def _run(*entries):
    return lint_section_consistency({"entries": list(entries)})


def _shapes(findings):
    return [f["message"].split(":")[0] for f in findings]


def test_training_row_filed_as_appointment_is_flagged():
    findings = _run(
        _entry("2001-2004 Resident, Internal Medicine, Invented General Hospital", "D3",
               "Appointments", idx=14),
        _entry("2004-2007 Clinical Fellow in Cardiology, Invented University", "D1",
               "Appointments", idx=15))
    assert _shapes(findings) == ["training_as_appointment"]
    assert findings[0]["severity"] == "WARN"
    assert "2 entries under 'Appointments' coded D1, D3" in findings[0]["message"]
    assert findings[0]["evidence"][0].startswith("entry 14 (D3): ")


def test_a_ranked_training_title_filed_as_appointment_is_flagged():
    """QITQWH-02, RNKYST-04: 'Chief Resident' and 'Senior Resident' rows are
    training too; the rank word before the title must not hide them."""
    findings = _run(
        _entry("2003-2004 Chief Resident, Internal Medicine, Invented General Hospital", "D3",
               "Appointments", idx=14),
        _entry("2002-2003 Senior Resident, Internal Medicine, Invented General Hospital", "D3",
               "Appointments", idx=15))
    assert _shapes(findings) == ["training_as_appointment"]
    assert "2 entries under 'Appointments'" in findings[0]["message"]


def test_training_row_coded_as_training_and_a_faculty_title_are_quiet():
    assert _run(
        _entry("2001-2004 Resident, Internal Medicine, Invented General Hospital", "C",
               "Training"),
        _entry("2010-present Professor of Medicine, Invented University", "D1",
               "Appointments"),
        _entry("2012-present Residency Program Director, Invented Hospital", "D2",
               "Appointments")) == []


def test_training_row_filed_as_degree_is_flagged_1415():
    """#1415 (RCBKFG GKAQHB 17/20): an internship and a residency coded B1
    render in Academic Degrees, absent from the training table."""
    findings = _run(
        _entry("Intern (Example Medicine)\tInvented General Hospital 1991-1992", "B1",
               "Education", idx=17),
        _entry("Resident (Example Pathology) 1993-1996\tInvented Institute", "B1",
               "Education", idx=20))
    assert _shapes(findings) == ["training_as_degree"]
    assert findings[0]["severity"] == "WARN"
    assert "2 entries under 'Education' coded B1" in findings[0]["message"]
    assert "C (training)" in findings[0]["message"]


def test_a_degree_row_and_a_training_program_director_coded_b1_are_quiet():
    assert _run(
        _entry("MD, Invented University, 1990", "B1", "Education"),
        _entry("PhD in Example Studies, Invented University, 1995 (Dissertation: 'A title')",
               "B1", "Education"),
        _entry("Residency Program Director course, Invented Hospital", "B1",
               "Education")) == []


def test_board_certification_filed_as_membership_is_flagged():
    findings = _run(_entry("ABIM, Internal Medicine 2001, 2011", "I", "Post Graduate Experience"))
    assert _shapes(findings) == ["board_certification_misfiled"]


def test_board_named_as_a_funder_or_award_giver_is_quiet():
    assert _run(
        _entry("ABIM, Internal Medicine 2001", "F2", "Certifications"),
        _entry("National Board of Medical Examiners Stemmler Fund | Quill, A. (PI)", "M2C",
               "Grants"),
        _entry("2018 ABIM Foundation Professionalism Award", "H", "Honors")) == []


def test_life_support_course_filed_as_licence_is_flagged():
    findings = _run(_entry("ACLS/BLS 2021-2023", "F1", "Credentials"))
    assert _shapes(findings) == ["life_support_as_license"]
    assert _run(_entry("ACLS/BLS 2021-2023", "B2", "Credentials")) == []


def test_grant_review_heading_rows_not_coded_q3_are_flagged():
    findings = _run(
        _entry("2017 Pilot Grant Program, Invented Foundation", "Q2", "Reviewer", "Grants",
               idx=66),
        _entry("2018 Seed Awards, Invented Institute", "Q3", "Reviewer", "Grants", idx=67))
    assert _shapes(findings) == ["grant_review_not_q3"]
    assert "1 entry under 'Reviewer > Grants' coded Q2" in findings[0]["message"]


def test_grant_review_heading_journal_reviews_and_talks_are_quiet():
    assert _run(
        _entry("Reviewer, Journal of Invented Studies", "Q2", "Grant Review Service"),
        _entry("2013 Invited speaker, Invented Symposium", "R", "Service as grant reviewer")) == []


def test_courses_attended_filed_as_teaching_is_flagged():
    findings = _run(_entry("2019 Invented Society Annual Meeting, Springfield", "K4",
                           "CME and Educational Courses Attended"))
    assert _shapes(findings) == ["courses_attended_as_teaching"]
    assert _run(_entry("1. Consult service, 1991", "K2", "Attending rotations")) == []


def test_thesis_committee_block_filed_as_committee_is_flagged():
    findings = _run(_entry("Student Example (Biology, Advisor: Mentor Example, 2013-2015)", "P",
                           "Thesis/Dissertation Committees", "Master Thesis Committees"))
    assert _shapes(findings) == ["thesis_committee_as_committee"]
    assert _run(_entry("Student Example (Biology, 2013-2015)", "N3B",
                       "Thesis/Dissertation Committees")) == []


def test_cross_reference_line_or_bare_url_filed_as_a_record_is_flagged():
    findings = _run(
        _entry("Honors listed elsewhere - see Section 9", "H", "Invented Record", idx=154),
        _entry("http://example.org/widgets/page.html", "K4", "Appendix", idx=641))
    assert sorted(_shapes(findings)) == ["cross_reference_as_record"] * 2
    assert _run(_entry("http://example.org/widgets/page.html", "T", "Appendix")) == []


def test_bare_url_coded_s0_is_quiet_but_a_cross_reference_coded_s0_is_not():
    # S0 (Researcher Profile & Bibliometric Summary) lists a bare profile or
    # bibliography URL among its typical entries.
    assert _run(_entry("https://example.org/bibliography/widgets/public/", "S0",
                       "Bibliography")) == []
    findings = _run(_entry("Publications - see Appendix 2", "S0", "Bibliography"))
    assert _shapes(findings) == ["cross_reference_as_record"]


def test_cross_reference_inside_a_record_is_quiet():
    assert _run(_entry("1987-92 NIH R01 (see above), total $500,000", "M2B", "Grants")) == []


def test_journal_article_coded_s5_among_peer_reviewed_siblings_is_flagged():
    siblings = [_entry(_ARTICLE, "S1", "Publications", "Reviews and Guidelines", idx=i)
                for i in range(S5_MIN_SIBLINGS)]
    findings = _run(*siblings,
                    _entry(_GUIDELINE, "S5", "Publications", "Reviews and Guidelines", idx=229))
    assert _shapes(findings) == ["journal_article_as_report"]
    assert findings[0]["evidence"] == [f"entry 229 (S5): {_GUIDELINE[:80]}"]


def test_journal_article_coded_s5_under_a_peer_reviewed_heading_is_flagged():
    findings = _run(_entry(_GUIDELINE, "S5", "Publications", "Peer-Reviewed Publications"))
    assert _shapes(findings) == ["journal_article_as_report"]


def test_s5_under_a_non_peer_reviewed_heading_or_among_reports_is_quiet():
    reports = [_entry("Invented Agency. A report on widgets. 2001.", "S5", "Other Writing", idx=i)
               for i in range(S5_MIN_SIBLINGS)]
    assert _run(
        _entry(_GUIDELINE, "S5", "Publications", "Non-Peer-Reviewed Publications"),
        _entry(_GUIDELINE, "S5", "Other Articles (Non-refereed Journals)"),
        *reports,
        _entry(_GUIDELINE, "S5", "Other Writing", idx=99)) == []


def test_unclassified_siblings_do_not_dilute_the_peer_reviewed_share():
    # 5 S1 and the S5 itself: 5 of 6 classified siblings are articles. Ten
    # unclassified (T) rows under the same heading must not drag that share
    # under the bar.
    siblings = [_entry(_ARTICLE, "S1", "Writing", idx=i) for i in range(S5_MIN_SIBLINGS)]
    unclassified = [_entry("Invented note", "T", "Writing", idx=40 + i) for i in range(10)]
    findings = _run(*siblings, *unclassified, _entry(_GUIDELINE, "S5", "Writing", idx=99))
    assert _shapes(findings) == ["journal_article_as_report"]


def test_s5_without_volume_and_pages_among_articles_is_quiet():
    siblings = [_entry(_ARTICLE, "S1", "Bibliography", idx=i) for i in range(S5_MIN_SIBLINGS)]
    assert _run(*siblings,
                _entry("Quill AB. A simulator manual. Invented Repository, 2023.", "S5",
                       "Bibliography", idx=99)) == []


def test_one_finding_per_shape_and_heading_with_capped_evidence():
    rows = [_entry(f"{2000 + i}-{2001 + i} Resident, Surgery, Invented Hospital", "D3",
                   "Appointments", idx=20 + i) for i in range(5)]
    other_heading = _entry("1999-2000 Intern, Surgery, Invented Hospital", "D3", "Employment",
                           idx=40)
    findings = _run(*rows, other_heading)
    assert len(findings) == 2
    by_heading = {f["message"].split("'")[1]: f for f in findings}
    assert len(by_heading["Appointments"]["evidence"]) == SECTION_CONSISTENCY_EVIDENCE_MAX
    assert "5 entries" in by_heading["Appointments"]["message"]
    assert by_heading["Employment"]["evidence"][0].startswith("entry 40 (D3): ")


def test_entries_without_a_heading_or_code_do_not_crash():
    assert _run({"element_idx_start": 1, "text": "ACLS 2020"},
                {"element_idx_start": 2, "text": "", "taxonomy_code": "F1",
                 "hierarchy": None}) == []


def test_evidence_cap_is_three_entries():
    # The cap is the doctor's usual 3, not whatever the constant says.
    assert SECTION_CONSISTENCY_EVIDENCE_MAX == 3
    rows = [_entry(f"{2000 + i}-{2001 + i} Resident, Surgery, Invented Hospital", "D3",
                   "Appointments", idx=20 + i) for i in range(5)]
    findings = _run(*rows)
    assert [e.split(":")[0] for e in findings[0]["evidence"]] == [
        "entry 20 (D3)", "entry 21 (D3)", "entry 22 (D3)"]


def test_codes_in_the_message_are_sorted():
    # Six codes under one grant-review heading, given in reverse order: an
    # unsorted set would print them in hash order.
    codes = ["Q4A", "Q2", "Q1", "P", "O", "I"]
    rows = [_entry(f"{2010 + i} Invented Seed Grant Program", code, "Grant Review", idx=i)
            for i, code in enumerate(codes)]
    findings = _run(*rows)
    assert _shapes(findings) == ["grant_review_not_q3"]
    assert "6 entries under 'Grant Review' coded I, O, P, Q1, Q2, Q4A;" in findings[0]["message"]


def test_grant_review_row_coded_as_membership_is_flagged():
    findings = _run(_entry("2016 Invented Foundation Pilot Awards", "I", "Grant Review"))
    assert _shapes(findings) == ["grant_review_not_q3"]
    assert "coded I;" in findings[0]["message"]


def test_diplomate_line_filed_as_membership_is_flagged():
    findings = _run(_entry("Diplomate, Invented Board of Widget Medicine", "I", "Memberships"))
    assert _shapes(findings) == ["board_certification_misfiled"]


def test_entry_with_no_heading_names_no_heading_in_the_message():
    findings = _run(_entry("see Section 4", "H"))
    assert "under '(no heading)' coded H" in findings[0]["message"]


def test_entry_with_no_code_is_reported_with_an_empty_code():
    findings = _run({"element_idx_start": 5, "text": "see Section 4",
                     "taxonomy_code": None, "hierarchy": ["Honors"]})
    assert findings[0]["evidence"] == ["entry 5 (): see Section 4"]


def test_volume_and_pages_after_a_semicolon_or_comma_alone_is_a_citation():
    # Neither citation has the 'vol(issue):page' form; each has only the
    # '; vol:page' or ', vol: page' form.
    semicolon = "Panel EF. A guideline on widgets. Invented Rev. 2006; 3:1250-1258."
    comma = "Panel EF. A guideline on gadgets. Invented Rev, 6: 869-871, 2009."
    for text in (semicolon, comma):
        siblings = [_entry(_ARTICLE, "S1", "Writing", idx=i) for i in range(S5_MIN_SIBLINGS)]
        findings = _run(*siblings, _entry(text, "S5", "Writing", idx=99))
        assert _shapes(findings) == ["journal_article_as_report"], text


def test_volume_issue_and_pages_alone_is_a_citation():
    # '14(1):82' with no ';' or ',' before the volume.
    text = "Panel EF. A guideline on sprockets. Invented Rev 14(1):82-113 (2021)."
    siblings = [_entry(_ARTICLE, "S1", "Writing", idx=i) for i in range(S5_MIN_SIBLINGS)]
    findings = _run(*siblings, _entry(text, "S5", "Writing", idx=99))
    assert _shapes(findings) == ["journal_article_as_report"]


def test_s5_among_s2_siblings_is_flagged():
    siblings = [_entry(_ARTICLE, "S2", "Writing", idx=i) for i in range(S5_MIN_SIBLINGS)]
    findings = _run(*siblings, _entry(_GUIDELINE, "S5", "Writing", idx=99))
    assert _shapes(findings) == ["journal_article_as_report"]


def test_not_peer_reviewed_parent_heading_quiets_s5_under_a_plain_leaf():
    path = ("Non-Peer-Reviewed Works", "Guidelines")
    siblings = [_entry(_ARTICLE, "S1", *path, idx=i) for i in range(S5_MIN_SIBLINGS)]
    assert _run(*siblings, _entry(_GUIDELINE, "S5", *path, idx=99)) == []


def test_s5_sibling_count_and_article_share_bounds_are_inclusive():
    # Exactly S5_MIN_SIBLINGS classified siblings (the S5 included) fires.
    siblings = [_entry(_ARTICLE, "S1", "Writing", idx=i) for i in range(S5_MIN_SIBLINGS - 1)]
    assert _shapes(_run(*siblings, _entry(_GUIDELINE, "S5", "Writing", idx=99))) == [
        "journal_article_as_report"]
    # Exactly a 60% article share (3 of 5) fires.
    rows = [_entry(_ARTICLE, "S1", "Writing", idx=i) for i in range(3)]
    rows.append(_entry("Invented Agency. A report on widgets. 2001.", "S5", "Writing", idx=50))
    assert _shapes(_run(*rows, _entry(_GUIDELINE, "S5", "Writing", idx=99))) == [
        "journal_article_as_report"]


def test_a_bare_journal_coded_editorial_board_among_reviewer_journals_is_flagged():
    """RCBKFG JJUQDF 342-346: a batch with no lead-in coded the bare names
    of reviewed journals Q4C beside the Q4D ones."""
    path = ("Editorial Activities",)
    reviewed = _entry("Journal of Invented Widgets", "Q4D", *path, idx=330)
    board = [_entry(f"Invented Review of Sprockets {n}", "Q4C", *path, idx=342 + n)
             for n in range(2)]
    findings = _run(reviewed, *board)
    assert _shapes(findings) == ["reviewer_journal_as_editorial_board"]
    assert "entries under 'Editorial Activities' coded Q4C" in findings[0]["message"]
    assert findings[0]["evidence"][0] == "entry 342 (Q4C): Invented Review of Sprockets 0"


@pytest.mark.parametrize("text, heading, sibling_code", [
    ("Member, Editorial Board, Invented Review", "Editorial Activities", "Q4D"),
    ("Associate Editor, Invented Review", "Editorial Activities", "Q4D"),
    ("Member, Invented Newsletter Consultant Panel", "Editorial Activities", "Q4D"),
    ("Invented Review of Sprockets", "Editorial Boards", "Q4D"),
    ("Invented Review of Sprockets", "Editorial Activities", "Q4C"),
])
def test_an_editorial_board_entry_with_a_role_or_board_context_is_quiet(text, heading,
                                                                       sibling_code):
    sibling = _entry("Journal of Invented Widgets", sibling_code, heading, idx=330)
    assert _run(sibling, _entry(text, "Q4C", heading, idx=342)) == []


# --- #1691: courses and meetings attended, coded K4 or S8 (NECRGC) ----------

def test_attended_list_under_professional_development_is_flagged_1691():
    """HVSKET's shape: three courses and a bare meeting line under
    "Professional Development", coded K4 and S8."""
    findings = _run(
        _entry("2021 Invented Inclusion Workshop by the Office of Example Education", "K4",
               "Professional Development", idx=94),
        _entry("2019 Invented Society Annual Meeting – Example City, ST", "S8",
               "Professional Development", idx=98),
        _entry("2017 Bold Example Leadership Conference", "K4",
               "Professional Development", idx=103),
        _entry("2018-2019 Leadership in Example Medicine Program", "B2",
               "Professional Development", idx=99))
    assert _shapes(findings) == ["attended_as_teacher"]
    assert "3 entries under 'Professional Development' coded K4, S8" in findings[0]["message"]
    assert findings[0]["message"].endswith("the source points to B2 (attended, not taught or presented)")
    assert findings[0]["severity"] == "WARN"


def test_attendance_opening_is_flagged_under_any_heading_1691():
    """LFFKVV's shape: "Participated in" / "Attended the" lines coded K4
    under a heading that names no attendance."""
    findings = _run(
        _entry("Participated in the Department of Example Summer Series on Inclusion", "K4",
               "INCLUSION ACTIVITIES", idx=608),
        _entry("Attended the Example Speaker Series “Time and Health” (April 2021)", "K4",
               "INCLUSION ACTIVITIES", idx=610))
    assert _shapes(findings) == ["attended_as_teacher"]
    assert "2 entries" in findings[0]["message"]


def test_subheading_paragraph_coded_t_gives_the_attendance_context_1691():
    """YMDTEE's shape: "Career Development" was a paragraph stage 1b did not
    take as a heading; the courses after it carry only the parent heading."""
    sub = _entry("Career Development____________", "T", "EMS EXPERIENCE", idx=192)
    course = _entry("Example Medicine Basic Research Skills Workshop   2009-2010", "K4",
                    "EMS EXPERIENCE", idx=193)
    assert _shapes(_run(sub, course)) == ["attended_as_teacher"]
    # Without the sub-heading, nothing names attendance.
    assert _run(course) == []
    # Only an unclassified paragraph is a sub-heading: a short record coded
    # B2 that names a career development program is not.
    program = _entry("Career Development Program", "B2", "EMS EXPERIENCE", idx=192)
    assert _run(program, course) == []
    # A later sub-heading under the same path replaces it.
    teaching = _entry("Teaching", "T", "EMS EXPERIENCE", idx=196)
    assert _run(sub, teaching, dict(course, element_idx_start=197)) == []


@pytest.mark.parametrize("text, code, heading", [
    # A teaching role, a quoted talk title, or an author list.
    ("Course Director, Annual Example CME Course, 2019", "K4", "Continuing Education"),
    ("Lecturer, Example Board Review, 2020", "K4", "Professional Development"),
    ("“Managing Example Disease in Clinic”, Example CME Day, 2019", "K4", "Training"),
    ("Quill AB, Brook CD. Outcomes of things. Invented Society Annual Meeting, 2019", "S8",
     "Certifications"),
    ("Quill, Ann & Brook, Carl (2020). Outcomes of things. Invented Society Meeting.", "S8",
     "Continuing Education"),
    ("2007 Participant/Presenter, Invented Research Summit", "S8", "Training"),
    # A heading that names teaching lists what the owner taught.
    ("Example Sunrise Session, Invented Society Annual Meeting", "S8",
     "Formal Teaching of Peers (e.g., CME and other continuing education courses)"),
    ("2015-present National Trainer, Example Psychotherapy Program", "K4",
     "National Teaching and Training"),
    # PR #1829 review: the stage-6 refusal shares this carve-out.
    ("Example Sepsis Update, 2019", "K4", "CME Lectures Given"),
    ("Example Sepsis Update, 2019", "K4", "Continuing Medical Education - Teaching"),
    # A parent heading that names training does not reach a grants list.
    ("Example Symposium talk, 2021", "S8", "Research and Training"),
    # B2 is the right code: nothing to flag.
    ("2019 Invented Society Annual Meeting – Example City, ST", "B2", "Professional Development"),
])
def test_taught_authored_or_correctly_coded_entries_are_quiet_1691(text, code, heading):
    headings = (heading, "Co-Investigator on Grants") if heading == "Research and Training" \
        else (heading,)
    assert _run(_entry(text, code, *headings)) == []


@pytest.mark.parametrize("heading", ["Continuing Education - CME Lectures Attended",
                                     "Professional Development: Grand Rounds Attended",
                                     "Training - Conferences and Talks Attended"])
def test_attendance_heading_is_no_teaching_carve_out_1691(heading):
    """PR #1829 review: a heading that names attendance is no teaching
    carve-out (`heading_names_teaching_role`), whatever role word it also
    has. S8 keeps `courses_attended_as_teaching` (K codes only) out of it."""
    findings = _run(_entry("Example Sepsis Update, 2019", "S8", heading))
    assert _shapes(findings) == ["attended_as_teacher"]


@pytest.mark.parametrize("heading", ["CME Lectures Attended", "Grand Rounds Attended"])
def test_k4_under_an_attended_heading_is_flagged_once_1691(heading):
    """The heading's attendance word is `courses_attended_as_teaching`'s;
    `attended_as_teacher` does not repeat it."""
    findings = _run(_entry("Example Sepsis Update, 2019", "K4", heading))
    assert _shapes(findings) == ["courses_attended_as_teaching"]


@pytest.mark.parametrize("text", [
    "Medicine Grand Rounds, Example Hospital, 2018",
    "Invited to participate, Example Leadership Program, 2018",
])
def test_grand_rounds_or_invitation_under_ce_is_flagged_1691(text):
    """PR #1829 review: "grand rounds" and "invited" name a role in a
    heading only."""
    for heading in ("Certifications / Continuing Education", "Professional Development"):
        assert _shapes(_run(_entry(text, "K4", heading))) == ["attended_as_teacher"]


def test_talks_given_heading_is_quiet_1691():
    assert _run(_entry("Example Sepsis Update, 2019", "S8", "Continuing Education - Talks Given")) == []
    assert _shapes(_run(_entry("Example Sepsis Update, 2019", "S8",
                               "Continuing Education - Talks Attended"))) == ["attended_as_teacher"]


def test_lint_reads_the_leaf_heading_only_1691():
    """A teaching ancestor over an attendance leaf is no carve-out."""
    findings = _run(_entry("Example Sepsis Update, 2019", "S8", "Invited Talks",
                           "Professional Development"))
    assert _shapes(findings) == ["attended_as_teacher"]


def test_award_coded_as_grant_under_an_honors_heading_is_flagged_1577():
    """#1577 (YUYVIG UVZNIC 117-126, ZDOAZO 333): an award line under an
    honors heading, coded M2A with no grant marker, renders as a one-year
    Current Research Funding table with no title and no PI."""
    findings = _run(
        _entry("2004 Independent Investigator Award, Invented Institute of Examples", "M2A",
               "Awards, Honors, and Membership in Honorary Societies", idx=120),
        _entry("2009 Invented Mentoring Career Award", "M2B",
               "Awards, Honors, and Membership in Honorary Societies", idx=122))
    assert _shapes(findings) == ["honor_as_funding"]
    assert findings[0]["severity"] == "WARN"
    assert "2 entries under 'Awards, Honors, and Membership in Honorary Societies' coded M2A, M2B" \
        in findings[0]["message"]
    assert findings[0]["evidence"][0].startswith("entry 120 (M2A): ")


@pytest.mark.parametrize("code", ["M2", "M2C"])
def test_every_grant_code_under_an_honors_heading_is_flagged_1577(code):
    assert _shapes(_run(_entry("2007 Invented Consensus Group Grant, Invented Fund", code,
                               "Post-Degree Honors and Awards"))) == ["honor_as_funding"]


@pytest.mark.parametrize("text", [
    "Mentored Clinical Scientist Development Award (K08), Invented Institute 1998",  # number
    "1992-1994 Invented Career Development Award, Invented Foundation",  # two-ended period
    "Invented Foundation $2,500 Small Grant Award",  # an amount
    "Principal Investigator. Invented Society Research Grant. 2019.",  # PI role at the head
])
def test_award_with_a_grant_marker_under_an_honors_heading_is_quiet_1577(text):
    """The markers stage 3b's #1618 H pin keeps a grant code for."""
    assert _run(_entry(text, "M2A", "Honors and Awards")) == []


@pytest.mark.parametrize("heading", [
    "Grant Awards", "Funded Awards", "Other Support Awards", "Contract Awards", "Research Awards",
    "Investigator Awards", "Current Awards", "Past awards", "Pending Awards", "Completed Awards",
    "Ongoing Awards", "Active Awards",
])
def test_award_under_a_heading_that_names_funding_is_quiet_1577(heading):
    assert _run(_entry("2015 Invented Seed Award", "M2B", heading)) == []


@pytest.mark.parametrize("heading", ["Honors", "Awards", "Prizes", "Professional Recognition",
                                     "Distinctions", "Honours and Awards"])
def test_every_honors_word_names_an_honors_heading_1577(heading):
    assert _shapes(_run(_entry("2015 Invented Seed Award", "M2B", heading))) == ["honor_as_funding"]


def test_honors_heading_is_read_on_the_leaf_for_a_grant_code_1577():
    """A grants list nested under an honors parent is the author's grants."""
    assert _run(_entry("2015 Invented Seed Award", "M2B", "Honors and Awards", "Grants")) == []
    assert _run(_entry("2015 Invented Seed Award", "H", "Honors and Awards")) == []
    assert _run(_entry("2015 Invented Seed Award", "M2B", "Other Activities")) == []


@pytest.mark.parametrize("text", [
    "2017: Elected Fellow of the Invented Society for Cell Examples",
    "Elected to Fellowship, Invented Diseases Society - 1994",
    "Named Fellow of the Invented Heart Association (FIHA)",
    "2007 Inducted as a Fellow in the Invented Psychological Association.",
    "• Elected Associate Fellow, Invented Aerospace Association: 2011",
    "2024-\t\tElected Fellow, Invented Association for Examples",
    "Oct. 2015 Elected Honorary Fellow, Invented Royal College of Examples",
    "Elected Distinguished Fellow, Invented Society of Examples",
])
def test_elected_fellowship_coded_as_membership_is_reported_under_any_heading_1577(text):
    """#1577 (YUYVIG IZIXVF 55, XNWSZN 53; NECRGC LWMSPJ, ZASOTV): an
    election to a fellowship renders as 'Fellow, <body> yyyy-Present' in
    Memberships and is missing from Honors. INFO: the stage-3b prompt codes a
    society fellowship I, and that taxonomy call is still open."""
    findings = _run(_entry(text, "I", "Professional Memberships"))
    assert _shapes(findings) == ["fellowship_as_membership"]
    assert findings[0]["severity"] == "INFO"


@pytest.mark.parametrize("text", [
    "2024 Fellow, Invented College of Medical Examples (ICME)",
    "Fellow of the Invented College of Radiology, 2009",
    "Fellow 2018-present\tInvented College of Surgeons",
    "Fellow (2014), Invented Association",
    "1988     Founding Fellow, Invented Informatics Association",
    "2024 Distinguished Fellow of the Invented Psychiatric Association",
    "2009 Invented Gastroenterological Association Fellow",
    "2018-Present Fellow, Invented College of Examples",
    "Honorary Fellow, Invented Royal College of Examples",
    "Associate Fellow, Invented Aerospace Association",
    "Fellow in the Invented Heart Association, 2002",
    "Invented Society Fellow 2004",
    "Invented College Fellow 2004",
    "Invented Academy Fellow 2004",
    "Invented Institute Fellow 2004",
])
def test_fellowship_title_under_an_honors_path_is_reported_1577(text):
    """MVUREJ 44: the honors word may sit on a parent heading."""
    findings = _run(_entry(text, "I", "Professional Honors & Recognition", "National"))
    assert _shapes(findings) == ["fellowship_as_membership"]
    assert findings[0]["severity"] == "INFO"
    assert "1 entry under 'Professional Honors & Recognition > National' coded I" \
        in findings[0]["message"]


@pytest.mark.parametrize("text, code, headings", [
    # UVZNIC 131: the leaf names memberships, so the author put it there.
    ("2017 Fellow, Invented College of Examples", "I",
     ("Awards, Honors, and Membership in Honorary Societies",)),
    ("2006-present Fellow of the Invented Academy of Pediatric Examples", "I",
     ("Professional Certifications",)),
    # No honors word on the path, and no election.
    ("Fellow, Invented College of Examples", "I", ("Professional Societies",)),
    # An honors parent over a leaf that names memberships or credentials.
    ("Fellow, Invented College of Examples", "I", ("Honors and Awards", "Honorary Societies")),
    ("Fellow, Invented College of Examples", "I", ("Honors and Awards", "Memberships")),
    ("Fellow, Invented College of Examples", "I", ("Honors and Awards", "Organizations")),
    ("Fellow, Invented College of Examples", "I", ("Honors and Awards", "Affiliations")),
    ("Fellow, Invented College of Examples", "I", ("Honors and Awards", "Certifications")),
    ("Fellow, Invented College of Examples", "I", ("Honors and Awards", "Licensure")),
    ("Fellow, Invented College of Examples", "I", ("Service",)),
    # A fellowship programme, a training title, a member.
    ("1988-1991 Invented Society Fellowship", "I", ("Honors",)),
    ("Research Fellow, Invented Institute", "I", ("Honors",)),
    ("Member, Invented Society of Examples", "I", ("Honors",)),
    # "Fellow" not followed by a comma, of/in, a bracket or a digit: an award
    # or programme named for fellows, not a fellowship title.
    ("2015 Fellow Travel Award, Invented Society of Examples", "I", ("Honors and Awards",)),
    ("Fellow - Invented Leadership Programme, 2012", "I", ("Honors and Awards",)),
    # An acronym before "Fellow": a training or funding fellowship as often
    # as an honorific one.
    ("NIH Fellow 1998", "I", ("Honors and Awards",)),
    ("AHA Fellow", "I", ("Honors and Awards",)),
    ("INVA Fellow, June 2012", "I", ("Honors and Awards",)),
    # Already Honors, or a different code.
    ("Elected Fellow, Invented Society of Examples", "H", ("Honors",)),
    ("Elected Fellow, Invented Society of Examples", "Q1", ("Service",)),
    # An election mid-line, inside a membership row (CYOFWJ 65).
    ("Invented Academy of Examples (Active Member; elected fellow 2010)", "I",
     ("Professional Societies",)),
])
def test_fellowship_shapes_that_are_quiet_1577(text, code, headings):
    assert _run(_entry(text, code, *headings)) == []


# --- pinned_heading_empty (#1949) ------------------------------------------

def _node(title, code, confidence, *children, alternative=None):
    options = [{"code": code, "confidence": confidence}]
    if alternative:
        options.append({"code": alternative, "confidence": round(1 - confidence, 2)})
    return {"title": title, "taxonomy_options": options, "children": list(children)}


_MENTORING_3A = {"mappings": [
    _node("MENTORING", "N3", 0.5, _node("Leadership and mentoring in programs", "N1", 1.0),
          alternative="N1")]}
_N1_PATH = ("MENTORING", "Leadership and mentoring in programs")
_DIRECTOR = "2018-present Director, Invented Scholars Training Program"
_CO_DIRECTOR = "2016-2020 Co-Director, Invented Summer Research Program"


def _pinned(stage3a, *entries):
    return lint_pinned_heading_empty({"entries": list(entries)}, stage3a)


def test_n1_heading_whose_rows_all_went_to_o_is_reported_1949():
    findings = _pinned(
        _MENTORING_3A,
        _entry("Describe activity; include dates", "T", *_N1_PATH, idx=266),
        _entry(_DIRECTOR, "O", *_N1_PATH, idx=268),
        _entry(_CO_DIRECTOR, "O", *_N1_PATH, idx=270))
    assert len(findings) == 1
    assert findings[0]["lint"] == "pinned_heading_empty"
    assert findings[0]["severity"] == "INFO"
    assert ("2 entries under 'MENTORING > Leadership and mentoring in programs' coded O"
            in findings[0]["message"])
    assert "N1 (stage 3a mapped" in findings[0]["message"]
    assert findings[0]["evidence"][0].startswith("entry 268 (O): ")


def test_one_row_coded_the_heading_code_keeps_it_quiet_1949():
    findings = _pinned(_MENTORING_3A, _entry(_DIRECTOR, "N1", *_N1_PATH),
                       _entry(_CO_DIRECTOR, "O", *_N1_PATH))
    assert findings == []


def test_the_code_elsewhere_in_the_cv_fills_the_slot_1949():
    findings = _pinned(_MENTORING_3A, _entry(_DIRECTOR, "O", *_N1_PATH),
                       _entry(_CO_DIRECTOR, "N1", "MENTORING"))
    assert findings == []


@pytest.mark.parametrize("code", ["N3A", "N4"])
def test_a_row_in_the_same_family_keeps_it_quiet_1949(code):
    """M2B under a heading mapped M2A is a grant-status call, not an empty section."""
    findings = _pinned(_MENTORING_3A, _entry(_DIRECTOR, "O", *_N1_PATH),
                       _entry(_CO_DIRECTOR, code, *_N1_PATH))
    assert findings == []


def test_a_heading_holding_only_notes_is_quiet_1949():
    findings = _pinned(_MENTORING_3A, _entry("Describe activity; include dates", "T", *_N1_PATH))
    assert findings == []


def test_child_code_fills_a_parent_mapping_1949():
    """MENTORING mapped N3 is filled by N3A/N3B rows: N3 has no slot of its own."""
    stage3a = {"mappings": [_node("MENTORING", "N3", 0.97)]}
    findings = _pinned(stage3a, _entry("2019-2021 Invented Student, MD candidate", "N3A", "MENTORING"),
                       _entry("2020 Invented Fellow, postdoctoral", "N3B", "MENTORING"))
    assert findings == []


def test_confident_parent_does_not_judge_rows_under_its_sub_heading_1949():
    """Only the entry's own heading counts: a confident parent over an
    unmapped sub-heading whose rows got other codes is quiet."""
    stage3a = {"mappings": [_node("MENTORING", "N1", 0.99, {"title": "Current mentees"})]}
    findings = _pinned(stage3a, _entry("2021 Invented Student", "K2", "MENTORING", "Current mentees"))
    assert findings == []


def test_heading_below_the_pin_floor_is_quiet_1949():
    stage3a = {"mappings": [_node("MENTORING", "N3", 0.5, _node(
        "Leadership and mentoring in programs", "N1", HEADER_PIN_MIN_CONFIDENCE - 0.01))]}
    findings = _pinned(stage3a, _entry(_DIRECTOR, "O", *_N1_PATH))
    assert findings == []


def test_heading_rows_and_unmapped_headings_are_ignored_1949():
    header = {**_entry("Leadership and mentoring in programs", "O", *_N1_PATH), "element_type": "header"}
    elsewhere = _entry("2018 Member, Invented Committee", "P", "COMMITTEES")
    assert _pinned(_MENTORING_3A, header, elsewhere) == []


def test_malformed_stage3a_options_are_dropped_not_raised_1949():
    stage3a = {"mappings": [{"title": "MENTORING", "taxonomy_options": [
        "N1", {"confidence": 1.0}, {"code": "N1", "confidence": "high"}]}]}
    assert _pinned(stage3a, _entry(_DIRECTOR, "O", "MENTORING")) == []
    assert stage3a["mappings"][0]["taxonomy_options"][0] == "N1", "the loaded artifact is not mutated"
