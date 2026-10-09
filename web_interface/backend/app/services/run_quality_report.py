"""Run quality + run doctor report for the admin run page, and the
owner-facing "may need cleanup" flag.

Pure assembly over the two artifacts the pipeline already stores per run: the
cached score (quality_score_service) and the doctor report. Every number comes
from the scorer / doctor modules (quality_score.DIMENSIONS, run_doctor's
LINT_PREVALENCE and rank_lints); nothing here measures anything itself.
"""
import re
from dataclasses import dataclass, field

from app.schemas import (
    DoctorFindingGroup,
    DoctorFindingInstance,
    DoctorSeverityCounts,
    FixConfidence,
    FixEffort,
    FixListGroup,
    FixListItem,
    FixListProblem,
    QualityDimension,
    QualityGate,
    RunDoctorReport,
    RunFixList,
    RunQualityReport,
)
from app.services import quality_score_service as qss
from app.services.quality_score_service import (
    DimensionPoints,
    ScoreColumns,
    ScoreSnapshot,
)
from unified_pipeline import (
    quality_score as scorer,  # noqa: E402  (path set by quality_score_service)
)
from unified_pipeline.doctor import precision as lint_precision  # noqa: E402
from unified_pipeline.doctor.blind_spots import blind_spots  # noqa: E402
from unified_pipeline.doctor.lints.render import CITATION_EVIDENCE_CHARS  # noqa: E402
from unified_pipeline.doctor.shared import STATUS_RAN  # noqa: E402
from unified_pipeline.run_doctor import (  # noqa: E402
    LINT_PREVALENCE,
    SEVERITY_ORDER,
    rank_lints,
)
from unified_pipeline.stage4.schemas import TAXONOMY_LABELS  # noqa: E402

BAND_MEANINGS = {
    qss.BAND_GREEN: "Ship",
    qss.BAND_YELLOW: "Needs human cleanup",
    qss.BAND_RED: "Don't deliver",
}


@dataclass(frozen=True)
class CapSource:
    """The gate behind a hard-fail cap: a short reason and the doctor lint that
    reports the same condition, None when no lint does (the doctor itself did
    not check the run)."""
    reason: str
    lint: str | None


# Keyed by the scorer function so the gate list cannot drift from
# quality_score.DIMENSIONS / CAP_ONLY_GATES (every gate there needs a row; the
# contract test pins that).
_CAP_SOURCE_BY_SCORER = {
    scorer.score_doctor_findings: CapSource("the doctor did not check this run", None),
    scorer.score_pipeline_errors: CapSource("fatal error in pipeline", "pipeline_errors_present"),
    scorer.score_cv_owner: CapSource("owner name missing", "owner_contact_missing"),
    scorer.score_no_output: CapSource("no document was produced", "no_output"),
    scorer.score_stage3b_fallback_ratio: CapSource(
        "classification fell back to defaults", "stage3b_fallback_ratio"),
    scorer.score_protected_data: CapSource(
        "protected personal data in the output", "protected_data_in_output"),
    scorer.score_stage4_group_failures: CapSource(
        "field extraction failed for a group of entries", "stage4_group_failures"),
}

# Gate name (as it appears in the scorer's flags) -> its CapSource.
CAP_SOURCE_BY_GATE_NAME = {
    name: _CAP_SOURCE_BY_SCORER[fn]
    for name, fn in (
        *((n, f) for n, _w, f in scorer.DIMENSIONS),
        *scorer.CAP_ONLY_GATES,
    )
    if fn in _CAP_SOURCE_BY_SCORER
}

# The scorer writes a cap flag as "HARD-FAIL cap=<N>: <gate name> ...".
_CAP_FLAG_RE = re.compile(r"HARD-FAIL cap=(\d+): ")

@dataclass(frozen=True)
class RowCopy:
    """The run page's plain wording for one score row: its label, what it
    checks, how it loses points or caps, and what to do when it does."""
    label: str
    checks: str
    scoring: str
    if_lost: str


# Wording approved by Paul, 2026-10-02 (redesign v3 "PR 4" copy draft). Keyed
# by the scorer function, like _CAP_SOURCE_BY_SCORER: every row in
# quality_score.DIMENSIONS / CAP_ONLY_GATES needs one (the contract test pins
# that).
_ROW_COPY_BY_SCORER = {
    # Wording approved by Paul, 2026-10-08 (#1595). The score's one
    # weighted row since #1595.
    scorer.score_doctor_findings: RowCopy(
        "Problems the checker found",
        "Adds up the problems the Run Doctor flagged as warnings, each weighted by how often that "
        "check has been right and by the minutes that kind of problem usually takes to fix.",
        "About 3 minutes of estimated cleanup or less keeps the run at Ship. More lowers the "
        "score toward 60, never below it. A run the Run Doctor did not check is capped at 84.",
        "Work through the Run Doctor findings below, most costly first."),
    # The next two rows' scoring sentences: approved by Paul, 2026-10-08
    # (#1595; their points were retired, only the cap remains); the rest is
    # Paul's 2026-10-02 wording.
    scorer.score_pipeline_errors: RowCopy(
        "Processing ran without errors",
        "Looks through the run's saved records for error messages from CViche or the AI service, "
        "and for any stage the run recorded as failed.",
        "A fatal error caps the score at 40: a stage the run recorded as fatally failed, or an error "
        "message that names a program error, a traceback, or an API error code.",
        "Rerun the CV. If the error comes back, send the run to the CViche team."),
    scorer.score_cv_owner: RowCopy(
        "Faculty name and contact",
        "Checks that the CV owner's name was found, that a location was worked out, and that some "
        "contact detail was found.",
        "No usable name caps the score at 25.",
        "Check that the name and contact block are in the source CV, then rerun, or add them in Word."),
    scorer.score_no_output: RowCopy(
        "A document was produced",
        "Checks that a Word document was written at all.",
        "No document caps the score at 20, the lowest of all caps.",
        "Rerun the CV. Nothing can be delivered until a document exists."),
    scorer.score_stage3b_fallback_ratio: RowCopy(
        "Classification completed normally",
        "Checks whether more than 5% of the classification batches, or more than 5% of the entries, "
        "were filed under a default category because the classifier failed.",
        "Caps the score at 40, the same as a fatal error.",
        "Rerun the CV. Treat the document as unreliable until a clean run, because entries may be in "
        "the wrong sections."),
    scorer.score_protected_data: RowCopy(
        "No protected personal data",
        "Scans the finished document for protected details such as date of birth, social security "
        "number, passport, visa status, home address, and DEA number.",
        "Caps the score at 25.",
        "Remove the data in Word and rerun. Do not send the document on until it is gone. The Run "
        "Doctor finding names the category and the section but never the value."),
    scorer.score_stage4_group_failures: RowCopy(
        "Field extraction ran cleanly",
        "Checks whether stage 4 failed to read a whole group of entries, even if a retry later filled "
        "them in.",
        "Caps the score at 84, one point under Ship, so the run reads \"Needs human cleanup\".",
        "Check the entries in the sections named in the finding against the source CV, because "
        "retried entries can carry wrong values."),
}

_GATES = (*((n, f) for n, _w, f in scorer.DIMENSIONS), *scorer.CAP_ONLY_GATES)

# Gate name (as the scorer writes it) -> its plain wording.
ROW_COPY_BY_GATE_NAME = {name: _ROW_COPY_BY_SCORER[fn] for name, fn in _GATES
                         if fn in _ROW_COPY_BY_SCORER}

# The weight-0 rows: they carry no points, so the page lists one only when its
# cap fired (Paul, 2026-10-02).
_ZERO_WEIGHT_GATE_NAMES = (
    *(n for n, w, _f in scorer.DIMENSIONS if w == 0),
    *(n for n, _f in scorer.CAP_ONLY_GATES),
)


@dataclass(frozen=True)
class LintCopy:
    """The run page's plain wording for one doctor lint."""
    title: str
    explanation: str
    what_to_do: str


# Wording approved by Paul, 2026-10-02. Every KNOWN_LINTS entry needs one (the
# contract test pins that); a lint without one falls back to the doctor's own
# first message.
LINT_COPY = {
    "segmentation": LintCopy(
        "Source CV cut up wrongly",
        "Some source text was lost when the CV was split into entries, or entries were fused, "
        "repeated or empty.",
        "Compare the output with the source CV for missing passages and for entries that bundle "
        "several records."),
    "missed_headers": LintCopy(
        "Section headers not recognised",
        "Header-looking lines in the source were not treated as sections, so what sits under them "
        "may be misfiled.",
        "Check the quoted header lines and move any misfiled entries under the right section."),
    "bucket_status": LintCopy(
        "Grant under wrong funding heading",
        "A grant's status (for example, completed) does not match the funding heading it was "
        "written under, or it appears under none.",
        "Move the grant to the heading that matches its status."),
    "under_extraction": LintCopy(
        "Big entry mostly unread",
        "A long entry with several records had less than 40% of its content reach the document, "
        "so most of its records are missing.",
        "Compare the entry with the source and add the missing records."),
    "classified_unrendered": LintCopy(
        "Entries missing from document",
        "Entries that were classified do not appear anywhere in the finished document (Appendix "
        "and \"T\" entries are not checked).",
        "Copy the missing entries from the source CV into the right sections."),
    "taxonomy_code_coverage": LintCopy(
        "Entries sent to Appendix",
        "Some entries were given a category the document builder has no section for, so they land "
        "in the Appendix by design.",
        "Review the Appendix and move these entries to a suitable section by hand."),
    "stage3b_fallback_ratio": LintCopy(
        "Classification fell back",
        "More than 5% of classification batches or entries were filed under a default category "
        "because the classifier failed.",
        "Rerun the CV, and do not deliver this document."),
    "output_hygiene": LintCopy(
        "Stray codes or Appendix clutter",
        "Category codes such as \"[M2A]\" leaked into the text, boilerplate was placed in the "
        "Appendix, or the finding reports how many entries the Appendix holds.",
        "Delete any bracketed codes and any template or boilerplate lines in the Appendix."),
    "dead_sections": LintCopy(
        "Empty section despite source",
        "A section with substantial source content is empty in the document.",
        "Fill the section from the matching part of the source CV."),
    "unrendered_records": LintCopy(
        "Records dropped from entry",
        "Individual records inside a combined entry are missing from the document.",
        "Copy the missing records from the source CV."),
    "section_lost": LintCopy(
        "Entries missing from section",
        "A category's entries leave no trace in the section where they belong; they were lost or "
        "placed elsewhere, such as the Appendix.",
        "Find the entries (often in the Appendix) and move them, or add them from the source."),
    "enrichment_failures": LintCopy(
        "PubMed lookup failed",
        "Some publications could not be looked up in PubMed, so their citations hold only what the "
        "CV said.",
        "Check the listed citations for missing journal, volume or page details."),
    "stage6_render_warnings": LintCopy(
        "Document builder warnings",
        "The step that writes the Word document logged its own warnings or failed to write a section.",
        "Read the message and check the named section. If a section failed to render, rerun the CV."),
    "dedup_drops": LintCopy(
        "Distinct entries removed as duplicates",
        "An entry removed as a duplicate was either not nearly contained in the copy kept (under 90% "
        "overlap), named a date, part or number the kept copy lacks, or was contained but named "
        "something the page no longer shows or a different rank, so it may be a different record.",
        "Compare the quoted dropped text with the kept entry and restore anything that was different."),
    "pipe_leaks": LintCopy(
        "Raw pipe characters in text",
        "Lines in the document still carry the raw pipe-character field separators from the source, "
        "or several citations were fused into one numbered line.",
        "Reformat or split the quoted lines (Appendix lines are not checked)."),
    "table_shape": LintCopy(
        "Honors table rows misshaped",
        "Honors and awards tables have rows with a very long name cell, an empty date beside a year "
        "in the name, a bare state abbreviation as the organization, an organization cut out of the "
        "name, or one award split over several rows.",
        "Tidy the listed rows in the awards table."),
    "duplicate_passages": LintCopy(
        "Repeated passage",
        "Two or more consecutive blocks of text containing a year appear twice in the document.",
        "Delete the repeated passage."),
    "duplicate_records": LintCopy(
        "Repeated numbered entry",
        "The same numbered or bulleted entry appears twice within a few lines of itself in one "
        "section, or one article or grant is listed twice anywhere in the document.",
        "Delete the repeat."),
    "protected_data_in_output": LintCopy(
        "Protected personal data in document",
        "A date of birth, social security number or similar protected detail appears in the "
        "document. The finding names the category, never the value.",
        "Remove it in Word and rerun before sending anything on."),
    "invented_records": LintCopy(
        "Entry not in the original CV",
        "An entry was built from the template's own placeholder text, or a licence entry came from "
        "template instructions the faculty member never filled in.",
        "Check the entry against the source CV and delete it if it does not belong."),
    "wrong_start_date": LintCopy(
        "Date shows Present wrongly",
        "An entry has a start date and no end date, although its text gives one year range, so it "
        "prints \"start-Present\".",
        "Correct the dates from the source text."),
    "table_lost": LintCopy(
        "Source table mostly lost",
        "One or more tables in the source CV were mostly lost when it was read.",
        "Compare the quoted lines with the source table and re-enter what is missing."),
    "date_only_lines": LintCopy(
        "Lines holding only a date",
        "Paragraphs that are just a date, usually a date column split away from its entry.",
        "Join each date back to its entry or delete it."),
    "stage3b_second_pass_error": LintCopy(
        "Classification clean-up failed",
        "A clean-up pass after classification failed, so catch-all entries were left where they were.",
        "Look through the Appendix for entries that belong in sections."),
    "offschema_fields": LintCopy(
        "Extracted details not written",
        "Details were stored under a field name that nothing in the document reads, so they never "
        "reach the output.",
        "Check the quoted entry in the document and add the missing record or fact."),
    "implausible_year": LintCopy(
        "Year probably wrong century",
        "A date field holds a year before 1930 (or 10 or more years before the owner's earliest "
        "degree) that the entry's text never states, most likely a two-digit year read as 19xx, "
        "or a year after 2100.",
        "Check the date against the source and correct it."),
    "stage4_group_failures": LintCopy(
        "A group of entries failed",
        "Reading a group of entries failed. A retry may have filled them in, but they can be "
        "incomplete or wrong.",
        "Check the entries in the named categories against the source CV."),
    "python_repr_in_output": LintCopy(
        "Raw data code in document",
        "Program data, such as curly braces around quoted field names, was written into the "
        "document instead of a formatted value.",
        "Replace the quoted text with the proper value from the source."),
    "llm_refusal_in_output": LintCopy(
        "AI reply instead of CV text",
        "The document contains the AI model's reply asking for more information, not CV content.",
        "Delete the passage, write the section from the source, then tell the CViche team."),
    "llm_fallback_served": LintCopy(
        "Backup AI model wrote part of this",
        "The usual AI model was blocked by the content filter on part of this CV, and a backup "
        "model answered. Each finding names the section.",
        "Read the named sections closely against the source CV."),
    "stage_failure_recorded": LintCopy(
        "A processing step failed",
        "A step of the run failed and recorded it, so what that step makes is missing. Example: the "
        "research summary when stage 4.5 fails.",
        "Check the named part of the document. Rerun the CV, and send the run to the CViche team if "
        "it fails again."),
    "owner_missing_from_citation": LintCopy(
        "Owner's name missing from own citation",
        "The source CV names the faculty member on a publication (as an author, a group member or "
        "a co-presenter), but its citation in the document does not, usually because the author "
        "list was cut to the first six names and \"et al.\".",
        "Copy the full author list, with the faculty member's name, from the source CV."),
    "orphaned_fragments": LintCopy(
        "Line not joined to its entry",
        "A line in the source CV continues the entry next to it, but could not be safely "
        "joined to that entry, so its text may be missing from the document.",
        "Check the named entries against the source CV and add any missing text."),
    "etal_added": LintCopy(
        "Co-authors cut to \"et al.\"",
        "A citation lists its first authors and then \"et al.\", although the source CV lists "
        "every author.",
        "Copy the full author list from the source CV."),
    "multi_record_coverage": LintCopy(
        "Several records read as one",
        "One source entry lists several records (roles, dates, talks or mentees), but only one was "
        "extracted, so the others are missing or squeezed into one row.",
        "Compare the quoted entry with the source and add each missing record as its own row."),
    "year_not_in_source": LintCopy(
        "Year not in the source",
        "A date field holds a year the entry's text never states, in four digits or in two, so it "
        "came from somewhere else, often the entry before it, and may be wrong.",
        "Check the date against the source and correct it."),
    "date_cell_shape": LintCopy(
        "Date reads wrong",
        "A date prints in a form the CV does not use: a range ending in Present that the CV never "
        "says is ongoing, a stored value such as 2003-04-2005-09, or a range whose two ends are the "
        "same, such as 2013-2013.",
        "Correct the date from the source CV."),
    "junk_or_header_row": LintCopy(
        "Heading printed as a record",
        "A line that only introduces the records below it (an institution, a label ending in a "
        "colon, a stray date) or repeats a dated appointment prints as a record of its own.",
        "Delete the quoted row, and give the records under it the institution or dates it carried."),
    "teaching_postcheck": LintCopy(
        "Teaching line reworded wrongly",
        "A rewritten teaching line no longer matches the CV: a date sits apart from its title, a "
        "year is missing or wrong, a role was added, one of several records was left out, or a raw "
        "copy of the source was printed under it.",
        "Compare the quoted line with the source CV and correct it in Word."),
    "contact_slot_lost": LintCopy(
        "Office contact missing",
        "An office phone number or office address in the CV is not in the Office row of Personal "
        "Data: it is in another row, such as Cell phone, or nowhere in the document.",
        "Copy the office phone or address from the source CV into its Office row."),
    "pubmed_title_truncated": LintCopy(
        "PubMed title cut short",
        "A citation's title was replaced with PubMed's, and PubMed's title stops mid-phrase, "
        "usually just before an italic gene or organism name, so the citation's title is "
        "incomplete.",
        "Restore the full title from the source CV."),
    "enrichment_pubtype_mismatch": LintCopy(
        "Citation replaced with a correction notice",
        "A citation was matched to a PubMed correction, retraction or concern notice about the "
        "paper rather than the paper itself, so it shows the notice's title, authors and pages.",
        "Restore the paper's own citation from the source CV."),
    "section_consistency": LintCopy(
        "Entry filed in the wrong section",
        "An entry was given a section its own heading or text contradicts: a residency listed as an "
        "appointment, a board certification as a membership, a grant review as a committee, a "
        "course attended as teaching, or a published journal article as a non-peer-reviewed report.",
        "Move the quoted entries to the section their source heading names."),
    "segmentation_collapse": LintCopy(
        "Source sections not found",
        "Almost none of the source CV's section headings were found, so its entries were sorted "
        "without knowing which section they came from.",
        "Check every section of the output against the source CV, or fix the headings in the "
        "source and rerun."),
    "owner_contact_missing": LintCopy(
        "Owner name not found",
        "No usable CV owner name was found, or the extracted-fields file is missing, so the document "
        "cannot be filed under anyone.",
        "Make sure the name is clear at the top of the source CV and rerun, or add the name in Word."),
    "pipeline_errors_present": LintCopy(
        "Fatal processing error",
        "A stage recorded a fatal error (a program error, traceback or API error code), so part of "
        "the output is missing.",
        "Rerun the CV, and send the run to the CViche team if it fails again."),
    "no_output": LintCopy(
        "No document produced",
        "The run got as far as field extraction but wrote neither a Word document nor its render report.",
        "Rerun the CV."),
    "grant_boundary": LintCopy(
        "Grant details shifted between grants",
        "A grant's lines were split at the wrong place, so a grant may show another grant's title, "
        "principal investigator, effort or dates, or appear as two partial grants.",
        "Compare each flagged grant with the source CV and move the details back to the right grant."),
    "grant_bucket": LintCopy(
        "Grant under the wrong funding heading",
        "A grant is listed as current or completed funding although the CV files it as an "
        "application, or is listed as current although its end date has passed.",
        "Move the grant to the funding heading the source CV gives it."),
    # Wording approved by Paul, 2026-10-05 (#1174).
    "research_summary_call_failed": LintCopy(
        "Research summary not written by the AI",
        "Every AI model refused or failed to answer for the Research Activities summary. Either "
        "the section has no summary, or the CV's own research text was not checked and a new "
        "summary was written in its place.",
        "Write or check the Research Activities summary from the source CV."),
    "span_count": LintCopy(
        "Separate years shown as one range",
        "The CV lists separate years or terms for a record, but the document shows one "
        "continuous range from the first to the last.",
        "Replace the range with the years the source CV gives."),
    "role_consistency": LintCopy(
        "A grant shows the wrong principal investigator or role",
        "A grant table does not match the source CV on who led the grant: your role is reversed, "
        "your role says PI but the principal investigator is blank, the PI is also listed as a "
        "co-investigator, a collaborator is shown as the PI, or you are shown only as a "
        "co-investigator on a grant the CV lists you first on.",
        "Check the principal investigator and your role on each flagged grant against the "
        "source CV and correct them."),
    "fanout_cell_residue": LintCopy(
        "Leftover text in a split record's row",
        "A CV line that lists several roles or terms was split into one row each, and a row "
        "prints text from the other rows (their years, their role, or a cut-off year such as "
        "'93') in its organization or committee column.",
        "Delete the leftover text from the quoted rows, and fill in the organization from the "
        "source CV if it has one."),
    "identical_rendered_rows": LintCopy(
        "Rows that read the same",
        "Two or more rows in one table are identical, although the CV lists different dates "
        "or details for them, or one item is shown twice: once on its own and again inside "
        "another row.",
        "Add the date or detail that tells each flagged row apart, or delete the repeated item."),
    "split_child_unsourced": LintCopy(
        "A split entry shows a place or dates from elsewhere",
        "A line of the CV that was split into several records shows, on one of them, an "
        "institution the line does not name, or dates the line gives only to another record.",
        "Check the institution and dates of each flagged record against the source CV."),
    "record_boundary": LintCopy(
        "A line of one entry opens the next",
        "A labelled line that belongs to one entry, such as a trainee's current position, "
        "starts the next entry instead, so one entry lacks it and the next shows it.",
        "Move each flagged line back to the entry above it, as the source CV orders them."),
    "citation_field_dropped": LintCopy(
        "Citation leaves out its title, link or \"...\"",
        "A citation in the document leaves out something the source CV gives to identify the "
        "item: its title, its web link, or the \"...\" showing that the source left some "
        "authors out, so the shorter list reads as complete.",
        "Add the title or link from the source CV, or put the \"...\" back in the author list."),
    "group_header_context": LintCopy(
        "Rows lost the heading they sat under",
        "The CV groups some lines under a society, an employer, a course or a dated block. "
        "The rows for those lines don't show it: an organization cell is empty, a role "
        "appears on its own, a lead line sits apart from its list, or a role has no dates.",
        "Add the society, institution, course or dates from the line above to each flagged "
        "row, and delete a lead line that shows as a row of its own."),
    "stage4_unplaced_items": LintCopy(
        "Records read but not placed",
        "While reading a group of entries, some records came back that could not be matched "
        "to any entry, so they are not in the document.",
        "Check the entries in the named categories against the source CV and add any missing "
        "records."),
    # Proposed in #1554's PR; awaiting Paul's approval.
    "summary_unsupported_claim": LintCopy(
        "Research summary claims something the CV does not list",
        "The Research Activities paragraph was written for you from your CV, and it mentions "
        "something your CV does not list: an application under review, funding, mentoring, "
        "or a named funder such as NIH.",
        "Read the Research Activities paragraph and delete or correct each flagged sentence, "
        "since you sign this document."),
    # Proposed in #1573's PR; awaiting Paul's approval.
    "owner_attribution": LintCopy(
        "Someone else's work shown as yours",
        "Some items in the document may belong to other people: publications whose authors "
        "do not include you, or people listed as your mentees under a heading for grant "
        "applicants you reviewed or for laboratory staff.",
        "Check each flagged item against the source CV, and delete it or move it to the "
        "section where it belongs."),
    # Wording approved by Paul, 2026-10-08 (#1583).
    "shattered_prose": LintCopy(
        "Paragraphs broken at their printed lines",
        "Some paragraphs from your CV were broken at their printed lines. They appear as "
        "short bullets, one per line, or as one paragraph with words from a neighbouring "
        "column, such as a date, role or place, mixed into the sentence.",
        "Check each flagged paragraph against your CV: join its lines into one paragraph, "
        "and move any date, role or place that landed inside the sentence back to its entry."),
    "appointment_title_overlong": LintCopy(
        "Duties written into an appointment title",
        "An appointment's title holds more than the role: a sentence or more about the duties, "
        "such as an effort share or what the role was for.",
        "Keep only the role in the Title column, and delete the duties or move them out of "
        "the title."),
}

# A fatal cap from a recorded stage failure has no pipeline_errors_present
# finding: that lint scans the stage 2/3b/4 JSON only, and the doctor reports
# the recorded failure as stage_failure_recorded. The cap pointer names that
# finding instead, so it never points at a row the page does not show (Paul,
# 2026-10-02).
FATAL_ERROR_LINT = "pipeline_errors_present"
STAGE_FAILURE_LINT = "stage_failure_recorded"

#: Lints that are a review-copy comment only: never a run-page row, so no
#: LINT_COPY wording. citation_grounding (#1570) is right about half the time
#: (doctor/PRECISION.md, YUY-CG), too often to show as a problem; Paul,
#: 2026-10-08: "share the possible citation as a comment" instead.
#: source_line_coverage (#1588) is a source line the document may have lost:
#: held out on YUYVIG, 37% of a hand-checked sample wholly missing and 67%
#: missing at least a role or description (PRECISION.md, YUY-SLC), under the
#: 50% bar of #1625, so it is a review-notes item, never a run-page row.
#: grant_facts (#1588) is a grant number or amount a grant table shows that
#: its CV entry lacks, or one the entry states that the document lacks: new,
#: and measured on four held-out CVs only (PRECISION.md, GF-1), so it is a
#: comment on the grant's table, never a run-page row, until a run-page
#: wording is approved.
REVIEW_COPY_ONLY_LINTS = frozenset({"citation_grounding", "source_line_coverage", "grant_facts"})

_SEVERITY_RANK = {severity: i for i, severity in enumerate(SEVERITY_ORDER)}


def _fired_gates(snapshot: ScoreSnapshot) -> list[tuple[int, str]]:
    """(cap, gate name) for every cap flag whose gate is known, in flag order."""
    fired = []
    for flag in snapshot.flags:
        match = _CAP_FLAG_RE.match(flag)
        if not match:
            continue
        rest = flag[match.end():]
        name = next((n for n in CAP_SOURCE_BY_GATE_NAME if rest.startswith(n)), None)
        if name is not None:
            fired.append((int(match.group(1)), name))
    return fired


def cap_source(snapshot: ScoreSnapshot) -> CapSource | None:
    """The gate behind the cap that lowered the score, if it can be told.

    The scorer records a cap as a number plus a flag line naming the gate, so
    the gate is recovered from the first flag whose cap matches. None when no
    cap applied or no flag matches (a score cached by an older scorer build).
    """
    cap = qss.binding_cap(snapshot)
    if cap is None:
        return None
    return next((CAP_SOURCE_BY_GATE_NAME[name] for c, name in _fired_gates(snapshot) if c == cap),
                None)


def fired_zero_weight_gates(snapshot: ScoreSnapshot) -> list[QualityGate]:
    """The weight-0 rows whose cap fired, whether or not that cap was the one
    that lowered the score."""
    gates = []
    for cap, name in _fired_gates(snapshot):
        if name not in _ZERO_WEIGHT_GATE_NAMES:
            continue
        copy = ROW_COPY_BY_GATE_NAME.get(name)
        gates.append(QualityGate(
            name=name, cap=cap, lint=CAP_SOURCE_BY_GATE_NAME[name].lint,
            label=copy.label if copy else None, checks=copy.checks if copy else None,
            scoring=copy.scoring if copy else None, if_lost=copy.if_lost if copy else None))
    return gates


def _dimension(d: DimensionPoints) -> QualityDimension:
    """One weighted row with its plain wording; a row named by an older scorer
    build has no wording and shows its technical name."""
    copy = ROW_COPY_BY_GATE_NAME.get(d.name)
    return QualityDimension(
        name=d.name, weight=d.weight, points=d.points,
        label=copy.label if copy else None, checks=copy.checks if copy else None,
        scoring=copy.scoring if copy else None, if_lost=copy.if_lost if copy else None,
        can_cap=d.name in CAP_SOURCE_BY_GATE_NAME)


def columns_need_cleanup(cols: ScoreColumns) -> bool:
    """The owner-facing rule: the band is not green, or a cap applied. False
    when the run has no score."""
    if cols.quality_score is None:
        return False
    return cols.quality_band != qss.BAND_GREEN or cols.quality_cap is not None


#: Instances listed per lint row. A lint can fire hundreds of times on one CV
#: (output_hygiene); the row's count still says how many there were.
MAX_INSTANCES_SHOWN = 25

# ponytail: the section is read off the doctor's own message prefix, not a
# structured field, so it also works on every doctor report already stored. A
# lint that words its location another way (protected_data_in_output says
# "found in <section>" inside its message) shows no section, only its detail.
# Upgrade path: give `_finding` a `code` field, read it here first, and keep
# these patterns for old reports. Each is anchored at the message start;
# `drop` is the span removed from the detail once `code` names a known section.
_CODE = r"(?P<code>[A-Z][A-Z0-9]*)"
_LOCATION_PREFIX_RES = (
    re.compile(rf"^(?P<drop>entry [^\s:]+ \({_CODE}\):\s*)"),  # "entry 42 (D1): ..."
    re.compile(rf"^(?P<drop>taxonomy code {_CODE}:\s*)"),  # "taxonomy code D1: ..."
    # offschema_fields: "3 B1 entries: `x` is outside ..." -> "3 entries: `x` is outside ...";
    # stage 6's self-check words the same finding after its own prefix.
    re.compile(rf"^(?:stage 6 self-check: )?\d+(?P<drop> {_CODE}) entr(?:y|ies): "),
    # section_lost: "F1: 1 entry absent from the LICENSURE section ..."
    re.compile(rf"^(?P<drop>{_CODE}:\s+)\d+ entr(?:y|ies) absent from "),
    # stage6_render_warnings: "stage 6 self-check: T: 6 entries diverted ..." and
    # "stage 6 self-check: K4 (Clinical teaching): No visible ..."
    re.compile(rf"^(?P<drop>stage 6 self-check: {_CODE}(?: \([^)]*\))?:\s*)"),
)
# The doctor's issue references, "(#1243)": meaningful to developers, noise on the run page.
_ISSUE_REF_RE = re.compile(r"\s*\(#\d+\)")
# Evidence that opens with one of the doctor's own locators ("entry 16", "row 3:
# ...", "block 4 repeats at 9: ...", "element_idx_start 43", "stage 5.2 ...") or
# is a bare known taxonomy code is the doctor's note, not text quoted from the CV.
_EVIDENCE_NOTE_RE = re.compile(r"^(?:entry|row|blocks?|element_idx_start|stage) \d")
#: Lengths the doctor cuts quoted text to without marking the cut: `[:100]` in
#: most lints, `[:120]` in the extraction/formatting ones, and the citation
#: lints' own constant. A quote exactly this long was almost surely cut.
DOCTOR_EVIDENCE_CAPS = frozenset({100, 120, CITATION_EVIDENCE_CHARS})
TRUNCATION_MARK = "\u2026"


def _section_and_detail(message: str) -> tuple[str | None, str]:
    """The CV section a known taxonomy-code prefix names, and the message
    without that prefix (unchanged when no known code is found)."""
    for pattern in _LOCATION_PREFIX_RES:
        match = pattern.match(message)
        if match is None:
            continue
        section = TAXONOMY_LABELS.get(match.group("code"))
        if section is not None:
            start, end = match.span("drop")
            return section, message[:start] + message[end:]
        return None, message
    return None, message


def _quotes_and_notes(evidence: object, detail: str) -> tuple[list[str], list[str]]:
    """Split the doctor's evidence into CV quotes and its own diagnostic notes.
    Blank items and quotes the detail already shows verbatim are dropped; a
    quote cut at a doctor cap gets an ellipsis."""
    quotes: list[str] = []
    notes: list[str] = []
    for item in evidence if isinstance(evidence, list) else []:
        text = str(item)
        if not text.strip():
            continue
        if _EVIDENCE_NOTE_RE.match(text) or text in TAXONOMY_LABELS:
            notes.append(text)
        elif text not in detail:
            quotes.append(text + TRUNCATION_MARK if len(text) in DOCTOR_EVIDENCE_CAPS else text)
    return quotes, notes


def _instance(finding: dict) -> DoctorFindingInstance:
    """One finding as the run page lists it: the CV section its taxonomy code
    names, the message without that prefix, and its evidence."""
    section, message = _section_and_detail(str(finding.get("message") or finding["lint"]))
    detail = _ISSUE_REF_RE.sub("", message).strip()
    quotes, notes = _quotes_and_notes(finding.get("evidence"), detail)
    return DoctorFindingInstance(
        severity=finding["severity"], section=section, detail=detail, quotes=quotes, notes=notes)


def _shown_instances(findings: list[dict]) -> list[DoctorFindingInstance]:
    """One lint's findings, worst first and otherwise in report order, capped."""
    ordered = sorted(findings, key=lambda f: _SEVERITY_RANK[f["severity"]])  # stable: keeps report order
    return [_instance(f) for f in ordered[:MAX_INSTANCES_SHOWN]]


def _doctor_groups(findings: list[dict], cap_lint: str | None) -> list[DoctorFindingGroup]:
    """Collapse the ran findings to one row per lint, rarest lint first."""
    by_lint: dict[str, list[dict]] = {}
    for f in findings:
        by_lint.setdefault(f["lint"], []).append(f)
    groups = []
    for lint, count in rank_lints({lint: len(fs) for lint, fs in by_lint.items()}):
        copy = LINT_COPY.get(lint)
        instances = _shown_instances(by_lint[lint])
        groups.append(DoctorFindingGroup(
            lint=lint, severity=instances[0].severity,  # worst first, so the row's worst
            message=copy.explanation if copy else by_lint[lint][0].get("message") or lint,
            title=copy.title if copy else None,
            what_to_do=copy.what_to_do if copy else None,
            count=count, prevalence=LINT_PREVALENCE.get(lint),
            caps_score=lint == cap_lint,
            instances=instances,
        ))
    return groups


def _usable_findings(payload: dict) -> tuple[list[dict], int]:
    """The doctor's ran findings with a known shape, and how many lints did
    not run (skipped for a missing artifact, or an unreadable one)."""
    ran, not_run = [], 0
    for f in payload.get("findings") if isinstance(payload.get("findings"), list) else []:
        if not isinstance(f, dict) or not isinstance(f.get("lint"), str):
            continue
        if f.get("status", STATUS_RAN) != STATUS_RAN:
            not_run += 1
        elif f.get("severity") in _SEVERITY_RANK:
            ran.append(f)
    return ran, not_run


# --- the Fix list (#1589) -----------------------------------------------------
#
# The run page's default doctor view, for the person fixing the CV: findings
# grouped by where they are in the document, merged per entry, in plain words
# only. The full per-lint list above stays as the Diagnostics tab.

#: Only these reach the Fix list; INFO findings fire on most runs and stay in
#: Diagnostics.
FIX_LIST_SEVERITIES = frozenset({"ERROR", "WARN"})
#: "High" confidence: hand-checked right at least this often, on at least this
#: many findings, so one lucky check cannot earn it. Below it, and at or above
#: doctor/precision.py's IN_PLACE_MIN_PRECISION, reads "medium". Both read the
#: gate's rows (`load_gate_ledger`: in-sample and held-out verdicts combined).
HIGH_CONFIDENCE_MIN_PRECISION = 0.80
HIGH_CONFIDENCE_MIN_JUDGED = 10

CONFIDENCE_HIGH: FixConfidence = "high"
CONFIDENCE_MEDIUM: FixConfidence = "medium"
CONFIDENCE_UNMEASURED: FixConfidence = "unmeasured"

EFFORT_QUICK: FixEffort = "quick"  # delete or retype one thing
EFFORT_MINUTES: FixEffort = "minutes"  # copy or move text from the source CV
EFFORT_LONGER: FixEffort = "longer"  # re-enter many records, or rerun the CV

#: Estimated effort per lint, from its LINT_COPY "what to do" (#1589; Paul
#: approved the labels 2026-10-08). A lint not listed reads EFFORT_MINUTES.
LINT_EFFORT: dict[str, FixEffort] = {
    **dict.fromkeys((
        "junk_or_header_row", "duplicate_records", "duplicate_passages", "date_only_lines",
        "output_hygiene", "pipe_leaks", "wrong_start_date", "date_cell_shape",
        "implausible_year", "year_not_in_source", "span_count", "fanout_cell_residue",
        "identical_rendered_rows", "python_repr_in_output", "table_shape", "contact_slot_lost",
        "bucket_status", "grant_bucket", "pubmed_title_truncated", "invented_records",
        "record_boundary",
    ), EFFORT_QUICK),
    **dict.fromkeys((
        "under_extraction", "table_lost", "dead_sections", "segmentation",
        "segmentation_collapse", "stage3b_fallback_ratio", "stage3b_second_pass_error",
        "stage4_group_failures", "stage_failure_recorded", "pipeline_errors_present",
        "no_output", "owner_contact_missing", "llm_fallback_served", "llm_refusal_in_output",
        "protected_data_in_output",
    ), EFFORT_LONGER),
}

#: The precision gate's ledger rows, keyed by (lint, message shape).
GateRows = dict[lint_precision.ShapeKey, lint_precision.LintPrecision]

_ENTRY_INDEX_RE = re.compile(r"^entry (\d+)\b")
# Document order: the WCM template's sections follow TAXONOMY_LABELS' order.
_SECTION_RANK = {label: i for i, label in reversed(list(enumerate(TAXONOMY_LABELS.values())))}


@dataclass
class _FixDraft:
    """A Fix-list item while the findings about its entry are gathered."""
    entry: int | None
    position: int  # the first finding's place in the report
    section: str | None = None
    problems: dict[str, FixListProblem] = field(default_factory=dict)  # by lint: one line per lint
    quotes: list[str] = field(default_factory=list)


def _confidence(lint: str, message: str, rows: GateRows) -> FixConfidence:
    """The band of the ledger row that measured findings like this one: the
    same row (`finding_precision`) the gate judged it by."""
    entry = lint_precision.finding_precision(lint, message, rows)
    if entry is None or entry.precision is None:
        return CONFIDENCE_UNMEASURED
    if entry.precision >= HIGH_CONFIDENCE_MIN_PRECISION and entry.judged >= HIGH_CONFIDENCE_MIN_JUDGED:
        return CONFIDENCE_HIGH
    return CONFIDENCE_MEDIUM


def _fix_list_drafts(ran: list[dict], rows: GateRows) -> tuple[list[_FixDraft], int]:
    """One draft per entry (findings that name none get one each), and how many
    ERROR/WARN findings were held back for Diagnostics."""
    drafts: dict[tuple[str, int], _FixDraft] = {}
    held_back = 0
    for position, finding in enumerate(ran):
        lint = finding["lint"]
        if finding["severity"] not in FIX_LIST_SEVERITIES:
            continue
        copy = LINT_COPY.get(lint)
        message = str(finding.get("message") or "")
        # The review copy's gate (#1639): a finding it keeps off the text is
        # kept off the Fix list too, so the two views agree.
        if copy is None or not lint_precision.shown_in_place(lint, message, rows):
            held_back += 1
            continue
        section, _detail = _section_and_detail(message)
        match = _ENTRY_INDEX_RE.match(message)
        entry = int(match.group(1)) if match else None
        key = ("entry", entry) if entry is not None else ("finding", position)
        draft = drafts.setdefault(key, _FixDraft(entry=entry, position=position))
        draft.section = draft.section or section  # an entry's other findings may name it
        if lint not in draft.problems or (_SEVERITY_RANK[finding["severity"]]
                                          < _SEVERITY_RANK[draft.problems[lint].severity]):
            draft.problems[lint] = FixListProblem(
                severity=finding["severity"], title=copy.title, what_to_do=copy.what_to_do,
                confidence=_confidence(lint, message, rows), effort=LINT_EFFORT.get(lint, EFFORT_MINUTES))
        # The detail is not shown here, so a quote it repeats is kept.
        quotes, _notes = _quotes_and_notes(finding.get("evidence"), "")
        draft.quotes.extend(q for q in quotes if q not in draft.quotes)
    return list(drafts.values()), held_back


def _document_order(draft: _FixDraft) -> tuple:
    """Findings naming no section first (they are about the whole document),
    then sections in template order, then entries in source order, then the
    findings that name no entry in report order."""
    if draft.section is None:
        section_rank = -1
    else:
        section_rank = _SECTION_RANK.get(draft.section, len(_SECTION_RANK))
    entry_rank = (0, draft.entry) if draft.entry is not None else (1, draft.position)
    return section_rank, entry_rank


def build_fix_list(ran: list[dict], rows: GateRows) -> tuple[list[FixListGroup], int]:
    """The Fix list's groups, every item listed (Paul, 2026-10-09: no cap), and
    the findings held back for Diagnostics."""
    drafts, held_back = _fix_list_drafts(ran, rows)
    drafts.sort(key=_document_order)
    groups: list[FixListGroup] = []
    for draft in drafts:
        item = FixListItem(
            problems=sorted(draft.problems.values(), key=lambda p: _SEVERITY_RANK[p.severity]),
            quotes=draft.quotes)
        if groups and groups[-1].section == draft.section:
            groups[-1].items.append(item)
        else:
            groups.append(FixListGroup(section=draft.section, items=[item]))
    return groups, held_back


def summarize_doctor(payload: object, cap_lint: str | None = None,
                     rows: GateRows | None = None) -> RunDoctorReport | None:
    """The doctor report as the run page shows it; None when ``payload`` is not
    a doctor report. ``rows`` defaults to the gate's ledger (`load_gate_ledger`)."""
    if not isinstance(payload, dict):
        return None
    ran, not_run = _usable_findings(payload)
    shown = [f for f in ran if f["lint"] not in REVIEW_COPY_ONLY_LINTS]
    groups = _doctor_groups(shown, cap_lint)
    by_severity = {s: sum(1 for g in groups if g.severity == s) for s in SEVERITY_ORDER}
    fix_list, held_back = build_fix_list(
        shown, lint_precision.load_gate_ledger() if rows is None else rows)
    return RunDoctorReport(
        counts=DoctorSeverityCounts(
            error=by_severity["ERROR"], warn=by_severity["WARN"], info=by_severity["INFO"]),
        findings=groups, not_run=not_run,
        fix_list=fix_list, fix_list_held_back=held_back,
        not_checked=[spot.sentence for spot in blind_spots()])


def build_owner_fix_list(doctor_raw: object) -> RunFixList | None:
    """The Fix list and not-checked list alone, for the run's owner (#1589);
    None when no doctor report was stored."""
    doctor = summarize_doctor(doctor_raw)
    if doctor is None:
        return None
    return RunFixList(
        fix_list=doctor.fix_list, not_checked=doctor.not_checked)


def doctor_lint_for_cap(lint: str | None, doctor_raw: object) -> str | None:
    """The doctor row a cap points at: ``lint``, except a fatal-error cap whose
    run has no pipeline_errors_present finding but a stage_failure_recorded one
    (see STAGE_FAILURE_LINT)."""
    if lint != FATAL_ERROR_LINT or not isinstance(doctor_raw, dict):
        return lint
    fired = {f["lint"] for f in _usable_findings(doctor_raw)[0]}
    if FATAL_ERROR_LINT not in fired and STAGE_FAILURE_LINT in fired:
        return STAGE_FAILURE_LINT
    return lint


def build_run_quality_report(run_id: str, score_raw: object, doctor_raw: object) -> RunQualityReport:
    """Assemble the admin report; the score and doctor parts degrade to null
    independently."""
    snapshot = qss.parse_score(score_raw)
    if snapshot is None:
        return RunQualityReport(run_id=run_id, doctor=summarize_doctor(doctor_raw))
    cap = qss.binding_cap(snapshot)
    source = cap_source(snapshot)
    cap_lint = doctor_lint_for_cap(source.lint, doctor_raw) if source else None
    band = qss.band_key(snapshot.total)
    return RunQualityReport(
        run_id=run_id,
        score=snapshot.total,
        band=band,
        band_meaning=BAND_MEANINGS[band],
        cap=cap,
        cap_reason=source.reason if source else None,
        cap_lint=cap_lint,
        earned=round(snapshot.earned),
        total_weight=sum(d.weight for d in snapshot.dimensions),
        data_complete=snapshot.data_complete,
        dimensions=[_dimension(d) for d in snapshot.dimensions],
        gates_fired=fired_zero_weight_gates(snapshot),
        doctor=summarize_doctor(doctor_raw, cap_lint),
    )
