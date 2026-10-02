"""Run quality + run doctor report for the admin run page, and the
owner-facing "may need cleanup" flag.

Pure assembly over the two artifacts the pipeline already stores per run: the
cached score (quality_score_service) and the doctor report. Every number comes
from the scorer / doctor modules (quality_score.DIMENSIONS, run_doctor's
LINT_PREVALENCE and rank_lints); nothing here measures anything itself.
"""
from dataclasses import dataclass

from app.schemas import (
    DoctorFindingGroup, DoctorSeverityCounts, QualityDimension, RunDoctorReport,
    RunQualityReport,
)
from app.services import quality_score_service as qss
from app.services.quality_score_service import ScoreColumns, ScoreSnapshot

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
_CAP_FLAG_PREFIX = "HARD-FAIL cap={cap}: "

# Plain-English line per doctor lint, for people who do not know the code
# names. Every KNOWN_LINTS entry needs one (the contract test pins that); a
# lint without one falls back to the doctor's own first message.
LINT_EXPLANATIONS = {
    "segmentation": "Parts of the source CV were lost or split wrongly when it was broken into entries.",
    "missed_headers": "Source section headers were not recognized as sections.",
    "bucket_status": "Grants were filed under a funding status that does not match their source text.",
    "under_extraction": "Large entries were only partly read, so most of their records are missing.",
    "classified_unrendered": "Entries were classified but never reached the output document.",
    "taxonomy_code_coverage": "Entries were classified into a category the template has no section for.",
    "stage3b_fallback_ratio": "Many entries were classified by default after classification batches failed.",
    "output_hygiene": "Minor formatting artifacts in the output, such as stray category codes or a large appendix.",
    "dead_sections": "Template sections were left empty although the source has content for them.",
    "unrendered_records": "Records inside combined entries were dropped when the document was written.",
    "section_lost": "Entries are missing from the section where they belong.",
    "enrichment_failures": "PubMed lookups failed, so some citations carry only what the CV said.",
    "stage6_render_warnings": "The document builder logged warnings or failed to render a section.",
    "dedup_drops": "De-duplication may have removed records that were distinct, not duplicates.",
    "pipe_leaks": "Raw pipe characters from unformatted entries appear in the document.",
    "table_shape": "Some tables have misplaced or empty columns.",
    "duplicate_passages": "The same source passage was written to the document more than once.",
    "duplicate_records": "A numbered or bulleted record repeats within the same section.",
    "protected_data_in_output": "Protected personal data appears in the output document.",
    "invented_records": "Output entries come from the template's own scaffolding, not from the CV.",
    "wrong_start_date": "An entry shows a start date with 'Present' although its text gives an end date.",
    "table_lost": "Tables in the source CV were mostly lost.",
    "date_only_lines": "Lines in the output hold only a date.",
    "stage3b_second_pass_error": "A classification clean-up pass failed, so catch-all entries were left as they were.",
    "offschema_fields": "Some extracted details were filed under a name no part of the document reads, so they are missing from it.",
    "implausible_year": "Some dates are a century off, most likely because the CV gave a two-digit year.",
    "stage4_group_failures": "Reading a group of entries failed and was retried separately, so those entries may be incomplete or hold wrong values.",
    "owner_contact_missing": "The CV owner's name could not be identified, so the document cannot be filed under anyone.",
    "pipeline_errors_present": "A stage failed with an error, so part of the output is missing.",
    "no_output": "The run produced no document.",
}

_SEVERITY_RANK = {severity: i for i, severity in enumerate(SEVERITY_ORDER)}


def cap_source(snapshot: ScoreSnapshot) -> CapSource | None:
    """The gate behind the cap that lowered the score, if it can be told.

    The scorer records a cap as a number plus a flag line naming the gate, so
    the gate is recovered from the flag whose cap matches. None when no cap
    applied or no flag matches (a score cached by an older scorer build).
    """
    cap = qss.binding_cap(snapshot)
    if cap is None:
        return None
    prefix = _CAP_FLAG_PREFIX.format(cap=cap)
    for flag in snapshot.flags:
        if not flag.startswith(prefix):
            continue
        rest = flag[len(prefix):]
        for name, source in CAP_SOURCE_BY_GATE_NAME.items():
            if rest.startswith(name):
                return source
    return None


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
    return [
        DoctorFindingGroup(
            lint=lint, severity=worst[lint],
            message=LINT_EXPLANATIONS.get(lint) or first_message[lint],
            count=count, prevalence=LINT_PREVALENCE.get(lint),
            caps_score=lint == cap_lint,
        )
        for lint, count in rank_lints(counts)
    ]


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


def build_run_quality_report(run_id: str, score_raw: object, doctor_raw: object) -> RunQualityReport:
    """Assemble the admin report; the score and doctor parts degrade to null
    independently."""
    snapshot = qss.parse_score(score_raw)
    if snapshot is None:
        return RunQualityReport(run_id=run_id, doctor=summarize_doctor(doctor_raw))
    cap = qss.binding_cap(snapshot)
    source = cap_source(snapshot)
    band = qss.band_key(snapshot.total)
    return RunQualityReport(
        run_id=run_id,
        score=snapshot.total,
        band=band,
        band_meaning=BAND_MEANINGS[band],
        cap=cap,
        cap_reason=source.reason if source else None,
        cap_lint=source.lint if source else None,
        earned=round(snapshot.earned),
        total_weight=sum(d.weight for d in snapshot.dimensions),
        data_complete=snapshot.data_complete,
        dimensions=[QualityDimension(name=d.name, weight=d.weight, points=d.points)
                    for d in snapshot.dimensions],
        doctor=summarize_doctor(doctor_raw, source.lint if source else None),
    )
