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
    lint_section_consistency,
)

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
