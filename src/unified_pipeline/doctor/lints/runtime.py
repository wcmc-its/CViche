"""Lints for the run itself rather than anything it produced (#493).

One responsibility: whether the pipeline actually completed. Every other
domain reads an artifact as a statement about the CV -- how the source was
cut, what was extracted from it, what reached the page. This one reads the
artifacts as a statement about the RUN, and it is the only lint that does not
care what the document says.

`lint_pipeline_errors` is the quality score's cap-40 HARD-FAIL gate: a fatal
pattern (NameError, traceback) recorded in an `error` field of the JSON the
deployed scorer globs means a stage died mid-run, so whatever that stage owned
is missing from the output and no finding about the output can be trusted. It
walks the artifacts with the scorer's own pattern and iterator
(`quality_score.FATAL_ERROR_PATTERN` / `iter_error_fields`) so the doctor and
the score cannot disagree about whether the gate fired.

The finding it emits is keyed `pipeline_errors_present`, not the function
name -- the `lint_` prefix is not a rule marker, which is why `KNOWN_LINTS` is
an explicit tuple rather than derived from names.

`lint_no_output` (#745) is dispatched by hand, like `lint_owner_contact_missing`
in the `enrichment` module: a run that reached stage 4 but produced neither a
stage-6 docx nor its render-warnings sidecar has nothing to deliver, which the
render lints' individual "skipped: missing" INFOs report too quietly to see
at a glance.

`lint_stage3b_fallback_ratio` (#810) is the same shape for a failure
`lint_pipeline_errors` cannot see: stage 3b's own batch-fallback path records
its failure as NUMBERS (`meta.stats.failed_batches`/`fallback_entries`), never
an `error` string, so a partial Bedrock outage that defaulted half a run's
classifications passed this gate clean. It reads the ratio from
`quality_score.stage3b_fallback_ratio_exceeded`, the same reason
`lint_pipeline_errors` reads its pattern from the scorer.

`lint_stage4_group_failures` (#1174) is the same shape for stage 4: a taxonomy
group whose extraction call failed (an invalid reply, a timeout, a provider
error such as a content filter) leaves `extraction_error` on its entries and
`stats.failed_batches` on the artifact, never an `error` key, so
`lint_pipeline_errors` cannot see it, and the recovery pass hides it further by
refilling the entries. It is a WARN, not an ERROR: its score cap stops short
of RED (`quality_score.STAGE4_GROUP_FAILURE_CAP`).

`lint_llm_fallback_served` (#1174) reports the success the lints above cannot
see: a Sonnet-5 call that ended content_filtered and was answered by the
fallback model. Nothing failed, so nothing marks an error; stage 4 stamps the
entries of the group and stage 4.5 lists the calls. A WARN that caps
nothing: the call's reply parsed and validated before it reached the run, so
the score does not cap on it (#1174, Paul 2026-10-05); the finding is the
provenance record of which model answered.

`lint_stage_failure_recorded` (#1174) reports what `lint_pipeline_errors`
cannot: a stage the driver recorded as failed in the stage-error record
(#745), which the quality score reads for its fatal gate but the doctor never
did. It is how a missing stage-4.5 research summary is told apart from a CV
with no research content. The web driver records a stage-4.5 failure
non-fatal and carries on (#1174), so for that stage it is a WARN.

`lint_research_summary_call_failed` (#1174) is the failure stage 4.5 survives
on its own: a call that raised on every model tried, recorded in the stage's
artifact rather than the stage-error record because the stage itself did not
raise.

The transitive-exclusivity fixpoint returned no exclusive helpers at all: this
domain's only external references are `Dict`/`List`, `_finding`, and the two
scorer names, all of which already live outside `run_doctor.py`. Nothing moved
to `doctor.shared`. Bodies are unmodified; `run_doctor` re-exports the name it
exported before.
"""

from unified_pipeline.llm_provenance import (
    STAGE4_5_CALL_FAILURES_KEY,
    STAGE4_5_CALL_SUMMARY,
)
from unified_pipeline.quality_score import (
    FATAL_ERROR_PATTERN,
    NO_OUTPUT_CAP,
    STAGE3B_FALLBACK_HARD_FAIL_CAP,
    STAGE4_GROUP_FAILURE_CAP,
    FallbackServedCall,
    iter_error_fields,
    llm_fallback_served,
    no_output_produced,
    stage3b_fallback_ratio_exceeded,
    stage4_group_failures,
)
from unified_pipeline.stage_errors import CallFailure, StageError

from ..shared import _finding

#: The stage id the drivers record for the research-summary stage.
STAGE_4_5_ID = "4.5"

#: The template section the stage-4.5 research summary fills, as a reader finds it.
RESEARCH_ACTIVITIES_SECTION = "Research Activities"


# --------------------------------------------------------------------------
# The cap-40 HARD-FAIL gate: a stage died mid-run.


def lint_pipeline_errors(artifacts: dict[str, dict]) -> list[dict]:
    """The quality score's cap-40 hard-fail gate: an ``error`` field somewhere
    in the run's artifacts carries a fatal pattern (NameError, traceback), so a
    stage died mid-run and whatever it owned is missing from the output. The
    pattern and the walk are the scorer's own
    (quality_score.FATAL_ERROR_PATTERN / iter_error_fields).

    ``artifacts`` is keyed by stage label and the caller narrows it to exactly
    the JSON the deployed scorer reads, so the cap this finding names is the
    cap those artifacts actually produce."""
    fatal: list[str] = []
    for label in sorted(artifacts):
        for path, value in iter_error_fields(artifacts[label], label):
            if FATAL_ERROR_PATTERN.search(value):
                fatal.append(f"{path}: {value[:120]}")
    if not fatal:
        return []
    return [_finding(
        "pipeline_errors_present", "ERROR",
        f"HARD-FAIL gate 'Pipeline/API errors present': {len(fatal)} fatal "
        f"error field(s) recorded in the run artifacts — the quality score is "
        f"capped at 40 (RED, do not deliver)",
        fatal[:5])]


# --------------------------------------------------------------------------
# The cap-40 HARD-FAIL gate: stage 3b's own batch-fallback ratio (#810).


def lint_stage3b_fallback_ratio(stage_3b: dict) -> list[dict]:
    """The quality score's stage3b_fallback_ratio hard-fail gate: a large
    share of stage 3b's classification batches failed and fell back to
    default codes, or a large share of its entries carry a default code --
    recorded as NUMBERS (`meta.stats.failed_batches`/`fallback_entries`),
    never an `error` string, so `lint_pipeline_errors` above cannot see it
    (#810 -- web30 lost 41 of 83 batches and scored 91 GREEN, doctor WARN
    only). Missing counters (an artifact from before this stat existed) is
    not a finding, not a crash -- `stage3b_fallback_ratio_exceeded` returns
    False on them."""
    exceeded, detail = stage3b_fallback_ratio_exceeded(stage_3b)
    if not exceeded:
        return []
    return [_finding(
        "stage3b_fallback_ratio", "ERROR",
        f"HARD-FAIL gate 'stage-3b fallback ratio': {detail} — the quality "
        f"score is capped at {STAGE3B_FALLBACK_HARD_FAIL_CAP} (RED, do not "
        f"deliver)",
        [detail])]


# --------------------------------------------------------------------------
# Stage 3b's two second-pass LLM stages that degrade silently (#818).

#: `meta.stats` keys of the two stage-3b second passes, in pipeline order.
#: Both stages catch their own exception, return the entries untouched and
#: record `error` -- the run continues, so the failure is a WARN, not a gate.
STAGE3B_SECOND_PASS_KEYS = ("t_validation", "fragment_reconnection")


def lint_stage3b_second_pass_errors(stage_3b: dict) -> list[dict]:
    """A stage-3b second pass (`t_validation`, `fragment_reconnection`)
    recorded a non-null `error` in `meta.stats`: it failed and left every
    entry as the first pass classified it, so the T entries it should have
    reclassified or reconnected are still T. `lint_pipeline_errors` only sees
    such an error when its text matches FATAL_ERROR_PATTERN (a raw
    `'int' object is not iterable` does not -- #745), and even then only as a
    score cap, never naming the pass. WARN: the run completes and the first
    pass's output is intact (the error blocks nothing the scorer already
    penalises through t_ratio). Error text is truncated like
    `lint_pipeline_errors`'s evidence."""
    meta = stage_3b.get("meta") if isinstance(stage_3b, dict) else None
    stats = meta.get("stats") if isinstance(meta, dict) else None
    if not isinstance(stats, dict):
        return []
    errored = []
    for key in STAGE3B_SECOND_PASS_KEYS:
        section = stats.get(key)
        error = section.get("error") if isinstance(section, dict) else None
        if error:
            errored.append(f"meta.stats.{key}.error: {str(error)[:120]}")
    if not errored:
        return []
    return [_finding(
        "stage3b_second_pass_error", "WARN",
        f"{len(errored)} stage-3b second pass(es) errored and left their "
        f"entries unchanged (T entries stay T / fragments stay unreconnected)",
        errored)]


# --------------------------------------------------------------------------
# The cap-84 gate: a stage-4 extraction group failed (#1174).


def lint_stage4_group_failures(stage_4: dict) -> list[dict]:
    """The quality score's stage-4 group-failure gate: an extraction call for a
    taxonomy group failed, so its entries were written empty and the recovery
    pass had to retry them. `quality_score.stage4_group_failures` reads the
    artifact for both, so the doctor reports the gate rather than a second
    definition of it.

    WARN, not ERROR: the cap keeps the run out of GREEN (84) but does not put
    it in the RED do-not-deliver band, and a group the recovery pass rescued
    is still a delivered document. Evidence names the taxonomy codes, so the
    reader knows which section to check. An artifact with no such failure, or
    from before the stage recorded one, is not a finding."""
    failures = stage4_group_failures(stage_4)
    if failures is None:
        return []
    codes = ", ".join(f"{code}={n}" for code, n in failures.entries_by_code.items())
    errors = ", ".join(f"{error}={n}" for error, n in failures.errors.items())
    evidence = [
        f"taxonomy codes (entries): {codes or 'none'}",
        f"errors (entries): {errors or 'none'}",
        f"stats.failed_batches={failures.failed_batches}",
    ]
    return [_finding(
        "stage4_group_failures", "WARN",
        f"stage-4 extraction failed for a taxonomy group: {failures.entries_failed} "
        f"entries lost their first extraction, the recovery pass rescued "
        f"{failures.entries_rescued} and left {failures.entries_unrecovered} without "
        f"extracted fields — the quality score is capped at "
        f"{STAGE4_GROUP_FAILURE_CAP} (never GREEN)",
        evidence)]


# --------------------------------------------------------------------------
# The cap-20 HARD-FAIL gate: no stage-6 output at all (#745).


def lint_no_output(has_stage4: bool, has_docx: bool, has_report: bool) -> list[dict]:
    """The quality score's no_output hard-fail gate: a run that reached
    stage 4 (so classification/extraction actually happened) but produced
    NEITHER a stage-6 docx nor its render-warnings sidecar has nothing to
    deliver -- the single most severe outcome a run can have (#745 comment,
    2026-09-12: web204 lost stage 6 to #812, scored 88 GREEN, and the doctor
    only logged the absence at INFO).

    This is dispatched by hand from `run_doctor()`, not through
    `LINT_REGISTRY`, because its three booleans are artifact PATHS, not
    loaded artifact CONTENT -- the registry's `_ready()` convention is for
    the latter. It runs alongside, not instead of, the ordinary 'skipped:
    missing stage_6_docx'/'missing stage_6_report' INFO the render lints
    already emit for each artifact individually. The absence condition itself
    is `quality_score.no_output_produced` (round-2 N4), the same predicate
    `score_no_output` calls, so the two hard-fail gates cannot drift apart on
    what counts as 'nothing to deliver'."""
    if has_stage4 and no_output_produced(has_docx=has_docx, has_report=has_report):
        return [_finding(
            "no_output", "ERROR",
            f"HARD-FAIL gate 'No output produced': neither a stage-6 docx "
            f"nor its render-warnings report exists for this run — the "
            f"quality score is capped at {NO_OUTPUT_CAP} (RED, do not "
            f"deliver)")]
    return []


# --------------------------------------------------------------------------
# A call the content-filter fallback served (#1174).


def lint_llm_fallback_served(stage_4: dict, stage_4_5: dict | None = None,
                             prompt_log_calls: list[FallbackServedCall] | None = None) -> list[dict]:
    """A Sonnet-5 call ended content_filtered and the fallback model answered
    it, so the output of that section came from a model the stage was not
    tuned on. One finding per section, so the evidence names which section to
    check.

    WARN, and the quality score does not cap on it (#1174): the call succeeded
    and its reply parsed, so this records provenance, not a defect.
    ``stage_4_5`` is optional because a run may have no research-summary
    artifact; an artifact from before the stage recorded provenance is not a
    finding. ``prompt_log_calls`` (`prompt_log_fallback_served`) adds one
    finding per stage for the calls no artifact records."""
    return [
        _finding(
            "llm_fallback_served", "WARN",
            f"stage {call.stage} {call.section}: the content filter blocked the "
            f"primary model and {call.model} answered ({call.count} {call.unit}); "
            f"the call succeeded, so the quality score is not capped",
            [call.describe()])
        for call in llm_fallback_served(stage_4, stage_4_5) + list(prompt_log_calls or [])]


# --------------------------------------------------------------------------
# A stage the driver recorded as failed (#1174, #745).


def _stage_failure_message(record: StageError) -> str:
    """What the failed stage owned, in the reader's terms."""
    cause = f"{record.exception_type}: {record.message[:120]}"
    if record.stage == STAGE_4_5_ID:
        return (f"stage 4.5 failed ({cause}): the {RESEARCH_ACTIVITIES_SECTION} section has no "
                f"research summary because the stage raised, not because the CV has no research content")
    return f"stage {record.stage} failed ({cause}): whatever it owned is missing from the output"


def lint_stage_failure_recorded(records: list[StageError]) -> list[dict]:
    """A stage the driver recorded as failed in ``<uid>_stage_errors.json``
    (#745). `quality_score.score_pipeline_errors` reads the same record and
    treats a fatal one as the cap-40 hard fail, which `lint_pipeline_errors`
    (an `error`-field scan) never saw, so a run could score RED with a clean
    doctor. A fatal record is an ERROR, like that gate; a non-fatal one is a
    WARN. An empty list (a clean run, or one from before the record) is not a
    finding."""
    return [
        _finding(
            "stage_failure_recorded", "ERROR" if record.fatal else "WARN",
            _stage_failure_message(record) + (
                " — the quality score treats a fatal stage failure as a hard "
                "fail (RED, do not deliver)" if record.fatal else ""),
            [f"stage={record.stage}", f"exception={record.exception_type}",
             f"fatal={record.fatal}"])
        for record in records]


# --------------------------------------------------------------------------
# A stage-4.5 call that failed on every model tried (#1174).


def _call_failure_message(failure: CallFailure) -> str:
    """What the failed stage-4.5 call cost the Research Activities section."""
    if failure.call == STAGE4_5_CALL_SUMMARY:
        consequence = f"the {RESEARCH_ACTIVITIES_SECTION} section has no research summary"
    else:
        consequence = (f"the CV's own {RESEARCH_ACTIVITIES_SECTION} text was not scored, "
                       f"so a summary was generated in its place")
    return (f"stage 4.5 {failure.call} call failed on every model tried ({failure.exception_type}, "
            f"stop_reason={failure.stop_reason!r}): {consequence}")


def lint_research_summary_call_failed(stage_4_5: dict) -> list[dict]:
    """A stage-4.5 LLM call that raised on every model tried, which the stage
    recorded under ``llm_call_failures`` and carried on past (#1174): a
    failed M1 relevance call leaves the CV's own Research Activities text
    unscored (a summary is generated instead), a failed generation call
    leaves the summary empty. One WARN per call, naming the section. It does
    not cap the score: the summary is one optional section. An artifact
    without the record, from before it, or malformed is not a finding."""
    raw = stage_4_5.get(STAGE4_5_CALL_FAILURES_KEY) if isinstance(stage_4_5, dict) else None
    failures = [CallFailure.from_record(r) for r in raw] if isinstance(raw, list) else []
    return [
        _finding("research_summary_call_failed", "WARN", _call_failure_message(failure),
                 [f"call={failure.call}", f"exception={failure.exception_type}",
                  f"stop_reason={failure.stop_reason}"])
        for failure in failures if failure is not None]
