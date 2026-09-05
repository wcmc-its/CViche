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

The transitive-exclusivity fixpoint returned no exclusive helpers at all: this
domain's only external references are `Dict`/`List`, `_finding`, and the two
scorer names, all of which already live outside `run_doctor.py`. Nothing moved
to `doctor.shared`. Bodies are unmodified; `run_doctor` re-exports the name it
exported before.
"""

from unified_pipeline.quality_score import FATAL_ERROR_PATTERN, iter_error_fields

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
