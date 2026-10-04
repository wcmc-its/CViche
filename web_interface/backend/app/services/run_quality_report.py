"""Run quality + run doctor report for the admin run page, and the
owner-facing "may need cleanup" flag.

Pure assembly over the two artifacts the pipeline already stores per run: the
cached score (quality_score_service) and the doctor report. Every number comes
from the scorer / doctor modules (quality_score.DIMENSIONS, run_doctor's
LINT_PREVALENCE and rank_lints); nothing here measures anything itself.
"""
import re
from dataclasses import dataclass

from app.schemas import (
    DoctorFindingGroup, DoctorSeverityCounts, QualityDimension, QualityGate, RunDoctorReport,
    RunQualityReport,
)
from app.services import quality_score_service as qss
from app.services.quality_score_service import DimensionPoints, ScoreColumns, ScoreSnapshot

from unified_pipeline import quality_score as scorer  # noqa: E402  (path set by quality_score_service)
from unified_pipeline.doctor.shared import STATUS_RAN  # noqa: E402
from unified_pipeline.run_doctor import LINT_PREVALENCE, SEVERITY_ORDER, rank_lints  # noqa: E402

BAND_MEANINGS = {
    qss.BAND_GREEN: "Ship",
    qss.BAND_YELLOW: "Needs human cleanup",
    qss.BAND_RED: "Don't deliver",
}


@dataclass(frozen=True)
class CapSource:
    """The gate behind a hard-fail cap: a short reason and the doctor lint that
    reports the same condition."""
    reason: str
    lint: str


# Keyed by the scorer function so the gate list cannot drift from
# quality_score.DIMENSIONS / CAP_ONLY_GATES (every gate there needs a row; the
# contract test pins that).
_CAP_SOURCE_BY_SCORER = {
    scorer.score_pipeline_errors: CapSource("fatal error in pipeline", "pipeline_errors_present"),
    scorer.score_cv_owner: CapSource("owner name missing", "owner_contact_missing"),
    scorer.score_no_output: CapSource("no document was produced", "no_output"),
    scorer.score_stage3b_fallback_ratio: CapSource(
        "classification fell back to defaults", "stage3b_fallback_ratio"),
    scorer.score_protected_data: CapSource(
        "protected personal data in the output", "protected_data_in_output"),
    scorer.score_under_extracted_records: CapSource(
        "a large entry was only partly read, so its records are missing", "under_extraction"),
    scorer.score_fused_entries: CapSource(
        "several records were fused into one entry", "segmentation"),
    scorer.score_lost_source_table: CapSource(
        "a source table never reached the output", "table_lost"),
    scorer.score_owner_missing_from_citation: CapSource(
        "the CV owner was cut from several of their own citations",
        "owner_missing_from_citation"),
    scorer.score_stage4_group_failures: CapSource(
        "field extraction failed for a group of entries", "stage4_group_failures"),
    scorer.score_llm_fallback_served: CapSource(
        "a backup model answered part of the run", "llm_fallback_served"),
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
    scorer.score_pipeline_errors: RowCopy(
        "Processing ran without errors",
        "Looks through the run's saved records for error messages from CViche or the AI service, "
        "and for any stage the run recorded as failed.",
        "A fatal error caps the score at 40: a stage the run recorded as fatally failed, or an error "
        "message that names a program error, a traceback, or an API error code. Otherwise each error "
        "costs a third of the points, so three errors lose them all.",
        "Rerun the CV. If the error comes back, send the run to the CViche team."),
    scorer.score_cv_owner: RowCopy(
        "Faculty name and contact",
        "Checks that the CV owner's name was found, that a location was worked out, and that some "
        "contact detail was found.",
        "No usable name caps the score at 25. Otherwise a missing inferred location costs 40% of the "
        "points, a missing primary location 30%, and missing contact details 30% (only when the "
        "source CV has an email or phone).",
        "Check that the name and contact block are in the source CV, then rerun, or add them in Word."),
    scorer.score_t_bucket: RowCopy(
        "Entries placed in sections",
        "Measures how many entries ended up in the Appendix catch-all instead of a real section, "
        "ignoring template text, empty placeholder rows and grant goal rows.",
        "Up to 3% of entries in the catch-all costs nothing; all points are lost at 15% or more. "
        "A failed clean-up pass adds a further 20% penalty.",
        "Open the Appendix and move entries to their proper headings."),
    scorer.score_sparse_tables: RowCopy(
        "Tables filled in",
        "Looks for tables where half or more of the cells are empty, and for large empty areas "
        "overall, counting only tables that hold CV content.",
        "A missing document costs half the points; a document with no tables at all costs all of them.",
        "Compare the empty cells with the source CV and fill what is missing. If a whole section is "
        "blank, see the section_lost and dead_sections findings."),
    scorer.score_broken_format: RowCopy(
        "No stray formatting",
        "Counts raw tab characters in the text and table cells, and body paragraphs that still contain "
        "the template's own instruction wording, such as \"please provide\", \"list here\" or \"(optional)\".",
        "Stray tabs cost at most 3 points, reached at 10 tabs. Each 15 leftover instructions cost "
        "about 4 points, up to all of them.",
        "Search the document for stray tab gaps and leftover instruction text, then delete them."),
    scorer.score_field_sparseness: RowCopy(
        "Entry details captured",
        "Checks how many entries came back with no usable details (dates, titles, journals) or with "
        "extraction marked as failed. Entries with nothing to extract are not counted against it.",
        "About 10% of entries empty and 10% failed loses all the points.",
        "Compare the thin entries with the source CV and fill in what is missing."),
    scorer.score_duplicate_ratio: RowCopy(
        "No duplicate entries",
        "Measures the share of entries the classifier flagged as duplicates.",
        "Up to 10% costs nothing; 50% or more loses all the points.",
        "Check that repeated entries are true repeats, delete extra copies, and merge any entry that "
        "was split in two."),
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
    scorer.score_lost_source_table: RowCopy(
        "Source tables read in full",
        "Compares each table in the uploaded CV with the entries read from it, and looks for a table "
        "whose lines mostly never arrived. Needs the original upload.",
        "Caps the score at 84 when the worst table lost 5 or more lines.",
        "Open the source table named in the table_lost finding and re-enter the missing rows."),
    scorer.score_fused_entries: RowCopy(
        "Records kept separate",
        "Counts entries that swallowed 3 or more record-like lines, i.e. several records read as one entry.",
        "Caps the score at 84 when 2 or more entries are fused. One fused entry is common and often "
        "harmless, so it does not cap.",
        "Split the fused entries named in the segmentation finding into one row per record."),
    scorer.score_under_extracted_records: RowCopy(
        "Long entries read in full",
        "Uses the under_extraction finding: a long entry with several records of which under 40% "
        "reached the document.",
        "Caps the score at 84 on any under_extraction finding.",
        "Compare the entry with the source and add the missing records."),
    scorer.score_owner_missing_from_citation: RowCopy(
        "Owner named on their own citations",
        "Reads each publication's line in the document and checks that it names the faculty member "
        "whenever the source CV credits them, including as a member of a study group.",
        "Caps the score at 84 when 3 or more citations leave the faculty member out.",
        "Restore the faculty member's name in the citations named in the owner_missing_from_citation "
        "finding."),
    scorer.score_llm_fallback_served: RowCopy(
        "Usual AI model used throughout",
        "Checks whether the AI service's content filter blocked the usual model on part of this CV, "
        "so a backup model wrote that part.",
        "Caps the score at 84. The call succeeded, so this is not an error; the cap only says that "
        "part came from a model the step was not tuned on.",
        "Read the sections named in the llm_fallback_served finding closely against the source."),
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
        "overlap), or was contained but named something the page no longer shows, so it may be a "
        "different record.",
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
        "The same numbered or bulleted entry appears twice within a few lines of itself in one section.",
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
}

# A fatal cap from a recorded stage failure has no pipeline_errors_present
# finding: that lint scans the stage 2/3b/4 JSON only, and the doctor reports
# the recorded failure as stage_failure_recorded. The cap pointer names that
# finding instead, so it never points at a row the page does not show (Paul,
# 2026-10-02).
FATAL_ERROR_LINT = "pipeline_errors_present"
STAGE_FAILURE_LINT = "stage_failure_recorded"

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


def _doctor_groups(findings: list[dict], cap_lint: str | None) -> list[DoctorFindingGroup]:
    """Collapse the ran findings to one row per lint, rarest lint first."""
    counts: dict[str, int] = {}
    worst: dict[str, str] = {}
    first_message: dict[str, str] = {}
    for f in findings:
        lint, severity = f["lint"], f["severity"]
        counts[lint] = counts.get(lint, 0) + 1
        if lint not in worst or _SEVERITY_RANK[severity] < _SEVERITY_RANK[worst[lint]]:
            worst[lint] = severity
        first_message.setdefault(lint, f.get("message") or lint)
    groups = []
    for lint, count in rank_lints(counts):
        copy = LINT_COPY.get(lint)
        groups.append(DoctorFindingGroup(
            lint=lint, severity=worst[lint],
            message=copy.explanation if copy else first_message[lint],
            title=copy.title if copy else None,
            what_to_do=copy.what_to_do if copy else None,
            count=count, prevalence=LINT_PREVALENCE.get(lint),
            caps_score=lint == cap_lint,
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


def summarize_doctor(payload: object, cap_lint: str | None = None) -> RunDoctorReport | None:
    """The doctor report as the run page shows it; None when ``payload`` is not
    a doctor report."""
    if not isinstance(payload, dict):
        return None
    ran, not_run = _usable_findings(payload)
    groups = _doctor_groups(ran, cap_lint)
    by_severity = {s: sum(1 for g in groups if g.severity == s) for s in SEVERITY_ORDER}
    return RunDoctorReport(
        counts=DoctorSeverityCounts(
            error=by_severity["ERROR"], warn=by_severity["WARN"], info=by_severity["INFO"]),
        findings=groups, not_run=not_run)


def doctor_lint_for_cap(lint: str, doctor_raw: object) -> str:
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
