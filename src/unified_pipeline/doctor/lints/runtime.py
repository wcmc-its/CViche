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

The transitive-exclusivity fixpoint returned no exclusive helpers at all: this
domain's only external references are `Dict`/`List`, `_finding`, and the two
scorer names, all of which already live outside `run_doctor.py`. Nothing moved
to `doctor.shared`. Bodies are unmodified; `run_doctor` re-exports the name it
exported before.
"""

from unified_pipeline.quality_score import (
    FATAL_ERROR_PATTERN,
    NO_OUTPUT_CAP,
    STAGE3B_FALLBACK_HARD_FAIL_CAP,
    iter_error_fields,
    stage3b_fallback_ratio_exceeded,
)

from ..shared import _finding


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
    already emit for each artifact individually."""
    if has_stage4 and not has_docx and not has_report:
        return [_finding(
            "no_output", "ERROR",
            f"HARD-FAIL gate 'No output produced': neither a stage-6 docx "
            f"nor its render-warnings report exists for this run — the "
            f"quality score is capped at {NO_OUTPUT_CAP} (RED, do not "
            f"deliver)")]
    return []
