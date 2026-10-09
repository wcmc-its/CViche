#!/usr/bin/env python3
"""
CViche run quality scorer.

Computes a deterministic 0-100 quality score for a completed pipeline run by
reading the stage output artifacts only -- NO additional LLM calls. Intended as
a post-pipeline gate: surface runs that need cleanup before they are delivered.

Usage (CLI):
    python3 quality_score.py <run_output_dir> [run_id]
    python3 quality_score.py <run_output_dir> --gate     # exit 1 if RED

Usage (import):
    from quality_score import score_run, quality_gate
    result = score_run("/path/to/outputs", "ABC123")        # full breakdown
    gate   = quality_gate("/path/to/outputs", mode="advisory")

Scoring model
-------------
Each dimension returns a penalty fraction in [0, 1], and a dimension of
weight w costs w * fraction points::

    raw   = 100 - sum(weight_i * fraction_i)
    final = min(raw, *caps)

Since #1595 the score is built from the run doctor's findings
(``score_doctor_findings``, weight 40): each WARN or ERROR finding costs its
lint's hand-checked precision (``doctor/PRECISION.md``) times the minutes its
kind of defect typically takes to fix (``LINT_FIX_MINUTES``), and the
estimated minutes set the penalty. A run with no readable doctor report was
not checked and is capped out of GREEN (``NOT_CHECKED_CAP``, #1593). The seven
weighted dimensions the score had before (pipeline errors, owner contact,
T-bucket share, sparse tables, raw formatting, field sparseness, duplicate
ratio) did not track verified defects on either labelled batch, so their
weights are retired; the ones that carry a hard-fail cap keep it at weight 0.

Hard-fail caps, unchanged: no rendered output (20), a missing CV owner name
(25), protected personal data in the docx (25, ``CAP_ONLY_GATES``), a fatal
pipeline error or a stage-3b batch-fallback ratio over threshold (40). A
stage-4 extraction group that failed keeps a run out of GREEN (#1174). The
nine content-loss caps at 84 (#822) are retired: their lints are doctor
findings, so they now lower the estimate instead of flipping the band.

The result also says what the score was computed *without*: ``data_complete``
is False and ``missing_evidence`` names each scored artifact that was absent,
unreadable, or ambiguous (and a ``EVIDENCE INCOMPLETE`` flag repeats it), so a
score over an incomplete output directory is recognizable as missing evidence
rather than read as a precise measurement (#724 review item 2). Of those
artifacts only the doctor report moves the score: without it the run is capped
at NOT_CHECKED_CAP.

Bands:
    >= 85  GREEN   ship: about 3 minutes of estimated cleanup or less
                   (GREEN_MAX_CLEANUP_MINUTES), and checked by the doctor
    >= 60  YELLOW  human cleanup needed
    <  60  RED     re-run / do-not-deliver: reached only through a hard-fail cap

The 85/60 lines date from June 2026; what GREEN means in minutes was set on
labelled batches (see GREEN_MAX_CLEANUP_MINUTES). The fix minutes are #822's
strawman unit costs, not measured, so treat the minutes as a ranking and GREEN
as "nothing the doctor reliably flags", never "human-verified correct".
"""

from __future__ import annotations

import json
import logging
import re
import sys
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from docx.document import Document as DocumentType

# #820/#825: the doctor's protected_data_in_output lint and this module's
# score_protected_data must not diverge, so the scorer CALLS the lint on
# the same body-order blocks (`docx_body_blocks`, the reader
# `run_doctor.read_docx_blocks` wraps) rather than keeping a second scan.
# Safe direction: neither `doctor.shared` nor `doctor.lints.protected_data`
# imports this module, so this does not create the cycle `quality_score ->
# run_doctor -> doctor.lints.enrichment -> quality_score` would (run_doctor.py
# itself is never imported here).
from unified_pipeline.doctor.lints.protected_data import lint_protected_data_in_output
from unified_pipeline.doctor.precision import load_ledger
from unified_pipeline.doctor.shared import (
    docx_body_blocks,
)
from unified_pipeline.llm_provenance import (
    FALLBACK_SERVED_KEY,
    PROMPT_LOG_RESPONSE_SUFFIX,
    STAGE4_5_FALLBACK_CALLS_KEY,
    STAGE4_ENTRY_FALLBACK_KEY,
)
from unified_pipeline.stage4.error_codes import NO_MATCHING_EXTRACTION
from unified_pipeline.stage_errors import STAGE_ERRORS_SUFFIX, read_stage_errors

logger = logging.getLogger(__name__)

# --- band thresholds (provisional; see module docstring) --------------------
BAND_GREEN = 85
BAND_YELLOW = 60

#: The suffix of the run doctor's report (`<uid>_doctor.json`), which the
#: doctor-findings dimension reads (#1595).
DOCTOR_REPORT_SUFFIX = "_doctor.json"

#: The review copy stage 7 writes beside the clean document (#1543): the same
#: document with the doctor's findings as Word comments. Never scored: a
#: second ``*.docx`` would make the docx evidence ambiguous (#1595). Mirrors
#: ``app.services.artifact_service.REVIEW_DOCX_SUFFIX``, which a backend test
#: pins equal: that module cannot import the pipeline package.
REVIEW_DOCX_SUFFIX = "_wcm_review.docx"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def clamp(val, lo=0.0, hi=1.0):
    return max(lo, min(hi, val))


def linear_interp(val: float, lo: float, hi: float, out_lo: float, out_hi: float) -> float:
    """Map val from [lo, hi] -> [out_lo, out_hi] linearly, clamped to the
    output range regardless of direction (out_lo may be > out_hi) -- a caller
    passing a val outside [lo, hi] gets a bounded result, not an extrapolated
    one (#724 review)."""
    if hi == lo:
        return out_lo
    t = (val - lo) / (hi - lo)
    result = out_lo + t * (out_hi - out_lo)
    return clamp(result, min(out_lo, out_hi), max(out_lo, out_hi))


def _load_first(outputs_dir: Path, pattern: str) -> tuple[dict | None, str | None]:
    """Load the first JSON artifact matching pattern.

    Returns ``(data, reason)``. ``data`` is ``None`` when nothing usable was
    loaded; ``reason`` then tells the misses apart: ``None`` means no file
    matched `pattern` at all (genuinely absent); a string starting with
    "ambiguous:" means more than one file matched and none was loaded --
    both live callers (quality_score_service.py, per-run S3 key filter into a
    fresh temp dir; scripts/score_one.py, per-uid glob into a fresh temp dir)
    build a directory holding at most one file per pattern, so this guards
    against a mis-pointed directory rather than a path either caller
    exercises (#724 review); any other string means a file *matched* but
    failed to parse -- e.g. the truncated JSON a crashed or OOM-killed stage
    leaves mid-write (present but unreadable, #497). Mirrors
    run_doctor._load_json's absent-vs-unreadable distinction.
    """
    files = sorted(outputs_dir.glob(pattern))
    if not files:
        return None, None
    if len(files) > 1:
        names = ", ".join(f.name for f in files)
        reason = f"ambiguous: {len(files)} files match {pattern} ({names})"
        logger.warning("quality_score found multiple candidates for %s: %s", pattern, names)
        return None, reason
    try:
        with open(files[0], encoding="utf-8") as f:
            return json.load(f), None
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        reason = f"{type(e).__name__}: {e}"
        logger.warning("quality_score could not read %s (%s)", files[0], reason)
        return None, reason


def _missing_or_unreadable_detail(label: str, reason: str | None) -> str:
    """Detail-string fragment for a `_load_first` miss: names an unreadable
    artifact distinctly from a genuinely absent one (#497)."""
    if reason is not None:
        return f"{label} unreadable ({reason})"
    return f"no {label} found"


def _load_docx(outputs_dir: Path) -> tuple[DocumentType | None, str | None]:
    """Return (Document, reason). Document is None if unavailable.

    Mirrors _load_first's ambiguous-match guard (#724 review): more than one
    *.docx in the directory is not loaded rather than silently picking one.

    Catches only what python-docx raises for a file that is present but not
    a readable Word document (each probed, 2026-09-04): ``BadZipFile`` for
    garbage, truncated or empty bytes; ``KeyError`` for a zip missing
    ``[Content_Types].xml`` or ``word/document.xml``; ``XMLSyntaxError``
    for a corrupt ``document.xml``; ``ValueError`` for a package whose main
    part is not a Word document (python-docx ``api.py``: "is not a Word
    file"); ``OSError`` for a directory or an unreadable file. Anything
    else is a programming error and propagates, the same rule #724 review
    item 12 set for ``_load_first``.
    """
    try:
        from docx import Document
        from lxml.etree import XMLSyntaxError
    except ImportError:
        return None, "python-docx not available"
    docx_files = sorted(p for p in outputs_dir.glob("*.docx")
                        if not p.name.endswith(REVIEW_DOCX_SUFFIX))
    if not docx_files:
        return None, "no docx found"
    if len(docx_files) > 1:
        names = ", ".join(f.name for f in docx_files)
        reason = f"ambiguous: {len(docx_files)} files match *.docx ({names})"
        logger.warning("quality_score found multiple docx candidates: %s", names)
        return None, reason
    try:
        return Document(docx_files[0]), None
    except (OSError, zipfile.BadZipFile, KeyError, ValueError, XMLSyntaxError) as e:
        reason = f"docx open error: {type(e).__name__}: {e}"
        logger.warning("quality_score could not read %s (%s)", docx_files[0], reason)
        return None, reason


#: Every artifact score_run reads, as (label, glob pattern). The evidence
#: inventory behind ``data_complete`` (#724 review item 2) walks exactly this
#: list plus the docx, so a new dimension that reads a new artifact must add
#: it here or its absence will not be reported as missing evidence.
SCORED_JSON_ARTIFACTS = (
    ("fields.json", "*_fields.json"),
    ("classified.json", "*_classified.json"),
    ("entries.json", "*_entries.json"),
    ("doctor report", f"*{DOCTOR_REPORT_SUFFIX}"),
)
#: Number of artifacts the inventory checks: the JSON patterns above plus the docx.
SCORED_ARTIFACT_COUNT = len(SCORED_JSON_ARTIFACTS) + 1


def missing_evidence(outputs_dir: Path) -> list[str]:
    """One entry per scored artifact that could not be loaded, in
    SCORED_JSON_ARTIFACTS order then the docx; empty when every artifact
    loaded (#724 review item 2).

    Each entry is the same absent / unreadable / ambiguous wording the
    dimension details use (`_missing_or_unreadable_detail`, `_load_docx`),
    so a reader can tell a directory that genuinely has no stage-3b output
    from a truncated classified.json from a scorer pointed at the wrong
    directory. This is the artifact-health signal the score itself does not
    carry: every dimension still scores a missing artifact the way it did
    before (0.5 for a docx, 1.0 for a JSON), so a run with absent evidence
    can look like a precisely measured bad run. score_run exposes this list
    as ``missing_evidence`` and its emptiness as ``data_complete``, and
    both are carried as typed fields to the admin API schema, the Teams
    card, and the admin runs view (#745).

    Loads each artifact once more, the way every dimension does (fields.json
    and classified.json are each already read by two dimensions, the docx by
    two); the scorer is offline and the artifacts are small.
    """
    missing: list[str] = []
    for label, pattern in SCORED_JSON_ARTIFACTS:
        data, reason = _load_first(outputs_dir, pattern)
        if data is None:
            missing.append(_missing_or_unreadable_detail(label, reason))
    doc, reason = _load_docx(outputs_dir)
    if doc is None:
        missing.append(f"docx: {reason}")
    return missing


# ---------------------------------------------------------------------------
# Hard-fail predicates
#
# The two gates that cap the final score live here as standalone predicates so
# run_doctor's owner_contact_missing / pipeline_errors_present lints report on
# exactly the conditions the scorer caps for, instead of a second definition
# that drifts (#437). The scorers below are their only other caller.
# ---------------------------------------------------------------------------

#: Error text that means a stage broke, not that one lookup came back empty.
#: The exception-type-name alternation was added for #724 (T2.5: the review
#: named NameError/UnboundLocalError/KeyError as too narrow -- ValidationException
#: was one of the review's suggested examples, not a measured farm count; the
#: farm's scored artifacts have 0 ValidationException hits. Those three named
#: patterns are now subsumed by the general alternation and kept implicitly).
#: It was first written case-insensitive (`\b\w+(?:Error|Exception)\b`) but
#: that matched ordinary prose containing a trailing "...error"/"...exception"
#: substring case-insensitively -- on the farm it fired on the OpenAI API's
#: own `'type': 'invalid_request_error'` envelope text inside
#: meta.stats.t_validation.error (uid L7IAKW), a real stage failure but not
#: an exception *type name*. Follow-up review (2026-09-02) made the
#: exception-name branch case-sensitive (`(?-i:...)`, requiring a capital
#: first letter and exact-case Error/Exception) so lowercase prose like "the
#: war on terror" or a lowercase "keyerror" no longer matches it, and added
#: an explicit, still case-insensitive, API-envelope branch so the
#: genuine L7IAKW failure (an OpenAI 400) stays fatal by name rather than by
#: accident of the broad heuristic. Residual gap the pattern still cannot
#: see: a stage failure recorded as a raw Python exception message with no
#: type name in it, e.g. `'int' object is not iterable` (1 of the 66 scored
#: farm uids, ODAWYA_2002_Holtz, carries exactly this in
#: meta.stats.t_validation.error and stays non-fatal) -- driving this from
#: structured stage error metadata instead of exception text is #745.
FATAL_ERROR_PATTERN = re.compile(
    r"name '\w+' is not defined"
    r"|Traceback \(most recent call last\)"
    r"|(?-i:\b[A-Z]\w*(?:Error|Exception)\b)"
    r"|invalid_request_error"
    r"|Error code: \d{3}",
    re.IGNORECASE,
)


def iter_error_fields(obj: object, path: str = "") -> list[tuple[str, str]]:
    """(dotted path, value) for every non-null ``error`` field anywhere in obj.

    Every non-null ``error`` key counts, with no allowlist for "benign"
    metadata: scouted against the 66-CV farm (#724 review item 4), the 66
    uids' scored artifacts (fields.json, classified.json, entries.json)
    carry 5 non-null error fields, all at ``meta.stats.t_validation.error``
    (6 across the whole stage_3b_classified_entries directory, which has 98
    files, more than the 66 scored uids) -- every one a real stage error
    (an LLM API error envelope, a `name 'response' is not defined`
    NameError, or a bare `'int' object is not iterable` TypeError with no
    exception-type name in the message) -- no schema in this pipeline emits
    an ``error`` key for anything else. If a stage ever adds one, an
    allowlist belongs here, keyed on the dotted path's stage prefix.
    """
    results = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            cur = f"{path}.{k}"
            if k == "error" and v:
                results.append((cur, str(v)))
            results.extend(iter_error_fields(v, cur))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            results.extend(iter_error_fields(item, f"{path}[{i}]"))
    return results


def _invalid_metadata_result(
        context: str, invariant: str, **values: int) -> tuple[float, str, None]:
    """Shared (fraction, detail, cap) for a stage-metadata invariant
    violation (#724 review items 10/11): worst-case fraction rather than
    computing a ratio from numbers that cannot be trusted (e.g. negative
    counts, or duplicate_entries exceeding total_entries). Farm: 0 of 65
    classified.json files in the scored population violate either invariant
    (0 of 98 in the whole stage_3b_classified_entries dir), so this never
    fires on real output today.
    """
    values_str = ", ".join(f"{k}={v}" for k, v in values.items())
    detail = f"invalid metadata: {invariant} ({values_str})"
    logger.warning("quality_score %s: %s", context, detail)
    return 1.0, detail, None


def cv_owner_name_missing(fields_data) -> bool:
    """True when a fields.json ``cv_owner`` block carries no usable name --
    the condition behind score_cv_owner's hard-fail cap of 25."""
    cv_owner = (fields_data or {}).get("cv_owner", {}) or {}
    full_name = (cv_owner.get("full_name") or "").strip()
    first_name = (cv_owner.get("first_name") or "").strip()
    last_name = (cv_owner.get("last_name") or "").strip()
    return (not full_name) and (not first_name or not last_name)


#: The hard-fail cap for a run that produced no rendered docx at all (#745
#: comment, 2026-09-12: web204 lost stage 6 to #812 and scored 88 GREEN
#: because the then-weighted docx dimensions scored a missing docx no worse
#: than a mediocre real one). Set BELOW
#: score_cv_owner's 25 and score_pipeline_errors' 40: "nothing to deliver" is
#: more severe than either -- both of those caps still describe a run that
#: produced *something* a human could look at.
NO_OUTPUT_CAP = 20


def no_docx_produced(outputs_dir: Path) -> bool:
    """True only when the run's output directory has NO docx at all -- the
    absent case _load_docx reports as ``"no docx found"``. Deliberately
    narrower than "doc is None": an ambiguous match (more than one *.docx) or
    a present-but-corrupt file is a different failure (``missing_evidence``
    names it), not "nothing was produced" the way a genuinely missing file is."""
    _, reason = _load_docx(outputs_dir)
    return reason == "no docx found"


def no_output_produced(has_docx: bool, has_report: bool = False) -> bool:
    """The single hard-fail predicate for 'nothing to deliver' (round-2 N4):
    no docx AND no render-warnings report either -- ``doctor.lints.runtime
    .lint_no_output`` and ``score_no_output`` below both call this instead of
    each inlining their own version of the condition, so they cannot drift
    apart the way #825 flagged for the other two hard-fail gates.

    The score's ``outputs_dir`` is a flat directory `scripts/score_one.py`
    collects (mirroring `quality_score_service.py`'s production copy) that
    never receives the render-warnings JSON at all (its NEEDED_SUFFIXES list
    has no `_render_warnings.json`) -- `score_no_output` therefore has no
    report visibility and MUST pass ``has_report=False`` explicitly rather
    than guessing, which degenerates this predicate to docx-absence alone for
    that caller. That is not a second definition: stage 6 always saves the
    docx before the report (`stage_6_word_template.py`), so 'report without
    docx' cannot happen on a real run and both callers agree in practice; if
    the score ever gains report visibility, passing the real value here is
    the only change needed to make it agree by construction too."""
    return (not has_docx) and (not has_report)


#: #810 decision 4: stage 3b's own batch-fallback ratio. `lint_pipeline_errors`
#: / `score_pipeline_errors` above can only see a failure recorded as an
#: `error` STRING; the batch-fallback path (`stage3b/classify.py:258-271`)
#: records it as NUMBERS instead (`meta.stats.failed_batches`,
#: `fallback_entries`) and is invisible to that scan -- web30 lost 41 of 83
#: classification batches (510 of 1019 entries defaulted) and scored 91
#: GREEN, doctor WARN only. #61's own rule (fail only when EVERY batch
#: failed) didn't fire either: a handful of batches classified late in the
#: outage window was enough to clear it.
#:
#: 5% is a starting calibration, not a measured p75/p90 -- no such baseline
#: exists yet for this metric (unlike MISSED_HEADERS_WARN_COUNT and its
#: siblings, which were fit to a scored corpus). It is chosen only to sit an
#: order of magnitude under the outage's own ratios (41/83=49%, 510/1019=50%)
#: and the clean re-run's (0/80, 0/1037), so a PARTIAL outage like this one
#: trips it well before #61's all-or-nothing bar. Revisit once more
#: outage/clean run pairs exist to fit against.
STAGE3B_FALLBACK_RATIO_THRESHOLD = 0.05

#: Hard-fail like score_pipeline_errors: a batch-fallback run and a run with
#: a fatal `error` string are the same failure class (a stage produced
#: meaningless output that looks real), so they share a cap.
STAGE3B_FALLBACK_HARD_FAIL_CAP = 40


def stage3b_fallback_ratios(stage_3b_data: dict | None) -> dict[str, float]:
    """The two ratios STAGE3B_FALLBACK_RATIO_THRESHOLD is compared against,
    by name -- ``{}`` when the artifact is absent or lacks the counters
    (an artifact from before c6402bf added them, or a denominator of 0).
    The single source both `stage3b_fallback_ratio_exceeded` (the gate) and
    the doctor's `metrics` block (#816, reporting the number even when it
    does not trip the gate) read, so the two cannot compute it two different
    ways (§1.5)."""
    stats = ((stage_3b_data or {}).get("meta") or {}).get("stats") or {}
    ratios: dict[str, float] = {}
    failed_batches, llm_batches = stats.get("failed_batches"), stats.get("llm_batches")
    if (isinstance(failed_batches, (int, float)) and isinstance(llm_batches, (int, float))
            and llm_batches):
        ratios["failed_batches/llm_batches"] = failed_batches / llm_batches
    fallback_entries, entries_classified = (stats.get("fallback_entries"),
                                            stats.get("entries_classified"))
    if (isinstance(fallback_entries, (int, float))
            and isinstance(entries_classified, (int, float)) and entries_classified):
        ratios["fallback_entries/entries_classified"] = fallback_entries / entries_classified
    return ratios


def stage3b_fallback_ratio_exceeded(stage_3b_data: dict | None) -> tuple[bool, str | None]:
    """True + a detail string when either ratio from `stage3b_fallback_ratios`
    exceeds STAGE3B_FALLBACK_RATIO_THRESHOLD -- (False, None) when neither
    ratio is computable (missing keys, an old-shaped artifact) or both are
    within threshold. Never raises on a malformed/absent artifact."""
    ratios = stage3b_fallback_ratios(stage_3b_data)
    if not ratios:
        return False, None
    name, ratio = max(ratios.items(), key=lambda kv: kv[1])
    if ratio <= STAGE3B_FALLBACK_RATIO_THRESHOLD:
        return False, None
    stats = ((stage_3b_data or {}).get("meta") or {}).get("stats") or {}
    return True, (
        f"{name}={ratio:.1%} exceeds {STAGE3B_FALLBACK_RATIO_THRESHOLD:.0%} "
        f"(failed_batches={stats.get('failed_batches')}, "
        f"llm_batches={stats.get('llm_batches')}, "
        f"fallback_entries={stats.get('fallback_entries')}, "
        f"entries_classified={stats.get('entries_classified')})")


#: A run in which a stage-4 extraction group failed outright cannot score GREEN
#: (#1174). The cap sits one point under the GREEN band, so the run reads
#: YELLOW ("human cleanup needed") and the owner-facing "may need cleanup" flag
#: (`cap` set) comes on. It is derived from BAND_GREEN so the two cannot drift.
#: Deliberately NOT a graded cap by share of entries lost: nothing measured
#: supports a threshold (see docs/RUN_DOCTOR_SCORING.md), and a run whose
#: failed group the recovery pass rescued is flagged here too, because the
#: rescued entries were read on a different prompt and can carry wrong values.
STAGE4_GROUP_FAILURE_CAP = BAND_GREEN - 1


@dataclass(frozen=True)
class Stage4GroupFailures:
    """What stage 4 recorded about taxonomy groups whose extraction call failed.

    ``failed_batches`` is stage 4's own ``stats.failed_batches`` (batches with
    at least one failed group); the entry counts come from the entries'
    ``extraction_error``. ``stats.extraction_failed`` is not used: it counts
    entries still unextracted AFTER the recovery pass, so it reads 0 when every
    entry of a failed group was rescued.
    """

    failed_batches: int
    entries_failed: int
    entries_rescued: int
    entries_by_code: dict[str, int]
    errors: dict[str, int]

    @property
    def entries_unrecovered(self) -> int:
        return self.entries_failed - self.entries_rescued

    def summary(self) -> str:
        codes = ",".join(f"{code}:{n}" for code, n in self.entries_by_code.items())
        errors = ",".join(f"{error}:{n}" for error, n in self.errors.items())
        return (
            f"failed_batches={self.failed_batches}; entries_failed={self.entries_failed} "
            f"(rescued={self.entries_rescued}, unrecovered={self.entries_unrecovered}); "
            f"taxonomy_codes={codes or 'none'}; errors={errors or 'none'}")


def _is_group_failure(entry: object) -> bool:
    """Whether the entry carries the error of a failed extraction-group call.

    Every `extraction_error` except NO_MATCHING_EXTRACTION counts, not an
    allowlist of today's three codes: a code stage 4 adds later is then counted
    rather than silently missed, which is the gap this gate closes."""
    if not isinstance(entry, dict):
        return False
    error = entry.get("extraction_error")
    return bool(error) and error != NO_MATCHING_EXTRACTION


def _failed_batch_count(stage_4_data: dict) -> int:
    stats = stage_4_data.get("stats")
    count = stats.get("failed_batches") if isinstance(stats, dict) else 0
    return count if isinstance(count, int) and count > 0 else 0


def stage4_group_failures(stage_4_data: dict | None) -> Stage4GroupFailures | None:
    """The failed extraction groups recorded in a stage-4 artifact, or None
    when it records none (or the artifact is absent or not the expected shape).

    A group's call failing (an invalid reply, a timeout, a provider error such
    as a content filter) leaves ``extraction_error`` on every entry of the
    group. The recovery pass then retries those entries; one it fills in gains
    ``extraction_success=True`` and KEEPS its ``extraction_error``, so
    ``extraction_success`` alone cannot tell a rescued entry from one that was
    never touched (#1174: a rescued group read as a clean run). Never raises on
    a malformed artifact.

    The single source both `score_stage4_group_failures` (the cap) and the
    doctor's `stage4_group_failures` lint read, so the two cannot disagree
    about what counts (§1.5)."""
    if not isinstance(stage_4_data, dict):
        return None
    entries = stage_4_data.get("entries")
    failed = [e for e in entries if _is_group_failure(e)] if isinstance(entries, list) else []
    failed_batches = _failed_batch_count(stage_4_data)
    if not failed and not failed_batches:
        return None
    return Stage4GroupFailures(
        failed_batches=failed_batches,
        entries_failed=len(failed),
        entries_rescued=sum(1 for e in failed if e.get("extraction_success")),
        entries_by_code=dict(sorted(
            Counter(str(e.get("taxonomy_code") or "?") for e in failed).items())),
        errors=dict(sorted(Counter(str(e["extraction_error"]) for e in failed).items())),
    )


#: How a stage-4.5 call is named as a section in a finding.
_STAGE4_5_SECTION = "research summary"


@dataclass(frozen=True)
class FallbackServedCall:
    """One section whose LLM call the content-filter fallback served.

    ``count`` is the entries of a stage-4 taxonomy group, 1 for a stage-4.5
    call, or the calls of one prompt-log purpose; ``unit`` says which.
    ``section`` is the taxonomy code, "research summary (<call>)", or
    "calls" for a prompt-log count.
    """

    stage: str
    section: str
    model: str
    count: int
    unit: str

    def describe(self) -> str:
        return f"stage {self.stage} {self.section} on {self.model} ({self.count} {self.unit})"


def _stage4_fallback_served(stage_4_data: object) -> list[FallbackServedCall]:
    entries = stage_4_data.get("entries") if isinstance(stage_4_data, dict) else None
    served = Counter(
        (str(e.get("taxonomy_code") or "?"), str(e[STAGE4_ENTRY_FALLBACK_KEY]))
        for e in entries or [] if isinstance(e, dict) and e.get(STAGE4_ENTRY_FALLBACK_KEY))
    return [FallbackServedCall("4", code, model, n, "entries")
            for (code, model), n in sorted(served.items())]


def _stage4_5_fallback_served(stage_4_5_data: object) -> list[FallbackServedCall]:
    calls = stage_4_5_data.get(STAGE4_5_FALLBACK_CALLS_KEY) if isinstance(stage_4_5_data, dict) else None
    return [
        FallbackServedCall("4.5", f"{_STAGE4_5_SECTION} ({c.get('call')})", str(c.get("model")), 1, "call")
        for c in calls or [] if isinstance(c, dict) and c.get("model")]


#: The prompt-log purpose whose fallback-served calls the stage-4.5 artifact
#: already lists one by one, so the prompt-log count would only repeat them.
_PROMPT_LOG_PURPOSES_RECORDED_ELSEWHERE = frozenset({"stage_4_5"})


def prompt_log_fallback_served(log_dir: Path | None) -> list[FallbackServedCall]:
    """Fallback-served calls counted per stage from one run's prompt-log
    response records (#1174): the record every `call_llm` writes, so it covers
    the calls stages 4 and 4.5 do not stamp in their artifacts (stages 1a-3b,
    5b-6, stage-4 recovery, owner-name and location calls). Stage-4 group
    calls are counted here too, beside their per-section findings.

    Empty when ``log_dir`` is None or absent. A record that does not parse is
    logged and skipped: one torn log file must not hide the others.
    Undercounts a run that resumed on another pod, whose earlier logs are
    only in storage."""
    if log_dir is None or not log_dir.is_dir():
        return []
    served: Counter[tuple[str, str]] = Counter()
    for path in log_dir.glob(f"*{PROMPT_LOG_RESPONSE_SUFFIX}"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            model = record["response"].get(FALLBACK_SERVED_KEY)
            purpose = str(record.get("purpose") or "?")
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            logger.warning("skipping unreadable prompt log %s (%s): %s", path.name, type(e).__name__, e)
            continue
        if model and purpose not in _PROMPT_LOG_PURPOSES_RECORDED_ELSEWHERE:
            served[(purpose, str(model))] += 1
    return [FallbackServedCall(purpose.removeprefix("stage_").replace("_", "."), "calls", model, n, "calls")
            for (purpose, model), n in sorted(served.items())]


def llm_fallback_served(stage_4_data: dict | None,
                        stage_4_5_data: dict | None) -> list[FallbackServedCall]:
    """The sections whose call the content-filter fallback served, as stages 4
    and 4.5 recorded them (#1174); empty when none, or when the artifacts are
    absent, from before the record existed, or not the expected shape. Never
    raises on a malformed artifact.

    A served call is a SUCCESS, so no error marker records it: stage 4 stamps
    ``llm_fallback_model`` on the entries of the taxonomy group the fallback
    answered, stage 4.5 lists the calls under ``llm_fallback_calls``. The
    other stages' calls are counted from the prompt logs by
    `prompt_log_fallback_served`.

    Read by the doctor's `llm_fallback_served` lint only. The score does not
    cap on it (#1174, Paul 2026-10-05): a served call reached the run only
    after its reply parsed and validated, so it is provenance, not a defect;
    a call the fallback could not answer is a failed group or a recorded
    stage failure, which the score does cap."""
    return _stage4_fallback_served(stage_4_data) + _stage4_5_fallback_served(stage_4_5_data)


# ---------------------------------------------------------------------------
# Dimension scorers  -- each returns (penalty_fraction, detail, hard_fail_cap)
# ---------------------------------------------------------------------------

def _structured_stage_errors(outputs_dir: Path) -> tuple[list[str], list[str], list[str]]:
    """(all, fatal, invalid) locations from the drivers' stage-error records (#745).

    The record is authoritative for its own ``fatal`` flag, whatever the
    message says -- that is the point: a stage failure whose text names no
    exception type is invisible to FATAL_ERROR_PATTERN. A run without the file
    (clean, or older than it) contributes nothing here, and the pattern scan in
    score_pipeline_errors is unchanged, so such a run scores exactly as before.

    A file that is not valid JSON is skipped here because score_pipeline_errors'
    ``*.json`` loop already counts it as unreadable; valid JSON of the wrong
    shape is returned in ``invalid`` for the caller to count the same way.
    """
    located: list[str] = []
    fatal: list[str] = []
    invalid: list[str] = []
    for errors_file in sorted(outputs_dir.glob(f"*{STAGE_ERRORS_SUFFIX}")):
        try:
            records = read_stage_errors(errors_file)
        except json.JSONDecodeError:
            continue  # already counted as unreadable by the *.json loop
        except (OSError, ValueError) as e:
            logger.warning("quality_score could not read %s (%s)", errors_file, e)
            invalid.append(f"{errors_file.name}: unreadable")
            continue
        for record in records:
            location = (f"{errors_file.name}: stage {record.stage}: "
                        f"{record.exception_type}: {record.message!r}")
            located.append(location)
            if record.fatal:
                fatal.append(location)
    return located, fatal, invalid


def score_pipeline_errors(outputs_dir: Path) -> tuple[float, str, int | None]:
    """Pipeline / API errors. Fatal patterns are a hard-fail (cap=40).

    An unreadable stage JSON is itself a pipeline-health signal (#724 review
    item 1) -- a truncated/corrupt artifact is exactly the shape a crashed or
    OOM-killed stage leaves behind -- so it is recorded as a synthetic
    non-fatal error entry rather than silently skipped. It is not fatal by
    itself; FATAL_ERROR_PATTERN still decides that from the parse-error text.

    A stage the driver recorded as failed (``*_stage_errors.json``, #745) is
    fatal by its record's ``fatal`` flag, not by pattern; see
    _structured_stage_errors.
    """
    nonnull_errors = 0
    fatal_locations = []
    unreadable_files = []

    for json_file in sorted(outputs_dir.glob("*.json")):
        if json_file.name.endswith(DOCTOR_REPORT_SUFFIX):
            continue  # the doctor's report quotes errors; it records none of its own
        try:
            with open(json_file, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
            reason = str(e)
            logger.warning("quality_score could not read %s (%s)", json_file, reason)
            path = f"{json_file.name}: unreadable"
            unreadable_files.append(path)
            nonnull_errors += 1
            if FATAL_ERROR_PATTERN.search(reason):
                fatal_locations.append(f"{path}: {reason!r}")
            continue
        for path, val in iter_error_fields(data, json_file.name):
            nonnull_errors += 1
            if FATAL_ERROR_PATTERN.search(val):
                fatal_locations.append(f"{path}: {val!r}")

    recorded, recorded_fatal, invalid_records = _structured_stage_errors(outputs_dir)
    nonnull_errors += len(recorded) + len(invalid_records)
    fatal_locations.extend(recorded_fatal)
    unreadable_files.extend(invalid_records)

    fatal_hit = bool(fatal_locations)
    hard_fail_cap = 40 if fatal_hit else None
    fraction = 1.0 if fatal_hit else clamp(nonnull_errors / 3.0)
    detail = (
        f"nonnull_error_fields={nonnull_errors}; fatal_pattern={'YES' if fatal_hit else 'NO'}; "
        f"fatal_locations={fatal_locations[:3]}"
    )
    if unreadable_files:
        detail += f"; unreadable_files={unreadable_files[:3]}"
    return fraction, detail, hard_fail_cap


# Stage 4 files contact under whatever key name the LLM picked -- the extraction
# call runs with response_format=json_object and no schema. Across the 100-CV
# corpus it emitted institutional_email (18), personal_email (9), fax (7),
# primary_email (4), home_address, office_address, work_phone, home_phone, cell,
# mobile_phone_primary, secondary_phone ... none of which the literal
# ("email", "phone", "address") tuple matched. Two CVs were scored "no contact
# found" while plainly carrying contact details (#427).
#
# Matching the key NAME rather than an allowlist of exact keys is deliberate:
# the vocabulary is unbounded, so an allowlist goes stale on the next CV that
# invents a variant. stage_6_word_template.py reads the same fields by hand and
# has the same exposure.
_CONTACT_KEY_RE = re.compile(
    r"email|phone|address|\b(?:cell|fax|mobile|telephone)\b", re.IGNORECASE)


# An email or phone number anywhere in the stage-2 source text: evidence the
# CV HAS contact details for stage 4 to find (#427). A CV whose PERSONAL DATA
# block is empty labels is an input gap, not an extraction miss, and is not
# penalized. The phone shape is a standalone token so DOIs, PMIDs and grant
# numbers (which carry 3-3-4 digit runs inside "/" or "." tokens) don't match.
# The area code needs its own separator (or parentheses): a six-digit
# certification number followed by a year ("#123456 2005") is not a
# phone number, and without that rule it read as one (#822, batch IPXFBA).
# ponytail: email/phone only; a source whose ONLY contact is a postal address
# (2 of the 126 corpus CVs) reads as blank, so a missed address goes
# unpenalized. Add an address shape if that case shows up as a miss.
_SOURCE_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_SOURCE_PHONE_RE = re.compile(
    r"(?<![\w/.\-])(?:\+\d{1,3}[ .-]?)?(?:\(\d{3}\)[ .-]?|\d{3}[ .-])\d{3}[ .-]\d{4}(?!\d)")


def _source_has_contact(outputs_dir: Path) -> bool | None:
    """Whether the stage-2 entry text carries an email or phone number;
    None when entries.json is absent or unreadable (the caller then keeps
    the penalty -- no evidence the source is blank)."""
    data, _ = _load_first(outputs_dir, "*_entries.json")
    if data is None:
        return None
    text = "\n".join(str(e.get("text") or "") for e in data.get("entries") or [])
    return bool(_SOURCE_EMAIL_RE.search(text) or _SOURCE_PHONE_RE.search(text))


def score_cv_owner(outputs_dir: Path) -> tuple[float, str, int | None]:
    """CV owner name / contact. Missing name is a hard-fail (cap=25).
    Missing contact is penalized only when the source has some (#427).

    The detail's `stage4_contact_field` says whether any stage-4 contact
    field holds a value. It was `any_contact` until batch EBYSBC (#822), which
    read as "no contact anywhere" on BZZNRL, MUHLLD and GJXIWD, whose docx
    shows a work email: the email is inside a positions entry's text, which
    the docx prints, while the contact block stays empty (class E10)."""
    data, reason = _load_first(outputs_dir, "*_fields.json")
    if data is None:
        return 1.0, _missing_or_unreadable_detail("fields.json", reason), 25

    if cv_owner_name_missing(data):
        return 1.0, "cv_owner name empty; hard-fail cap=25", 25

    full_name = ((data.get("cv_owner", {}) or {}).get("full_name") or "").strip()
    cv_loc = data.get("cv_owner_location", {}) or {}
    inference_success = cv_loc.get("inference_success", False)
    primary_location = cv_loc.get("primary_location")

    any_contact = False
    for e in data.get("entries", []):
        ef = e.get("extracted_fields", {}) or {}
        if any(v is not None and _CONTACT_KEY_RE.search(k) for k, v in ef.items()):
            any_contact = True
            break

    source_contact = None if any_contact else _source_has_contact(outputs_dir)

    fraction = 0.0
    if not inference_success:
        fraction += 0.4
    if not primary_location:
        fraction += 0.3
    if not any_contact and source_contact is not False:
        fraction += 0.3

    detail = (
        f"full_name={full_name!r}; inference_success={inference_success}; "
        f"primary_location={'set' if primary_location else 'missing'}; "
        f"stage4_contact_field={any_contact}; source_contact={source_contact}; "
        f"fraction={fraction:.2f}"
    )
    return clamp(fraction), detail, None


def score_protected_data(outputs_dir: Path) -> tuple[float, str, int | None]:
    """Protected personal data (date of birth, SSN, other #820 label/value
    shapes) reaching the rendered docx. A cap-only gate (#820 round 2), NOT
    a scored dimension: it never moves the raw score of a clean run, so
    every batch scored before it existed stays comparable; a leaking run is
    capped to 25 -- same cap as `score_cv_owner`, and for the same reason:
    withholding what this run produced is exactly the "cannot be delivered"
    question that gate already caps.

    The findings are run_doctor's own `protected_data_in_output` lint over
    the same body-order blocks and their tracked-deletion view (#825: one
    scan, not two that drift; #1223: the deletions are scanned too).
    """
    doc, reason = _load_docx(outputs_dir)
    if doc is None:
        return 0.5, reason, None
    hits = len(lint_protected_data_in_output(docx_body_blocks(doc),
                                             docx_body_blocks(doc, deleted=True)))
    if hits:
        return 1.0, f"protected_data_hits={hits}; hard-fail cap={PROTECTED_DATA_CAP}", PROTECTED_DATA_CAP
    return 0.0, "protected_data_hits=0", None


# ---------------------------------------------------------------------------
# The doctor-findings dimension (#1595): the score is built from the doctor's
# own findings, each weighted by how often its lint is right and by how long
# the defect it names takes to fix by hand.
# ---------------------------------------------------------------------------

#: Typical minutes to fix one finding by hand, by the kind of defect its lint
#: names. These are #822's strawman unit costs (a source record lost: retype
#: it; an entry in the wrong place: cut and paste it; ...), asserted, not
#: fitted: no review data times a fix yet (#1587 collects it). Refit them
#: against reviewer correction times once that data exists.
FIX_MINUTES_PROTECTED_DATA = 5.0
FIX_MINUTES_CONTACT_BLOCK = 2.0
FIX_MINUTES_RECORD_LOST = 1.0
FIX_MINUTES_MISPLACED = 0.5
FIX_MINUTES_FIELD_WRONG = 0.3
FIX_MINUTES_DUPLICATE = 0.2
FIX_MINUTES_FORMATTING = 0.1
#: A finding that reports how the run went (a fallback model, a retried
#: call, a failed stage) rather than a defect a reviewer fixes in the
#: document. Its cost reaches the score through the defects it leaves, or
#: through the hard-fail caps.
FIX_MINUTES_NONE = 0.0

#: Each doctor lint's fix minutes. Every lint in `run_doctor.KNOWN_LINTS` has
#: a row (a test pins it), so a new lint states what its finding costs.
LINT_FIX_MINUTES = {
    "protected_data_in_output": FIX_MINUTES_PROTECTED_DATA,
    **dict.fromkeys((
        "owner_contact_missing", "contact_slot_lost",
    ), FIX_MINUTES_CONTACT_BLOCK),
    **dict.fromkeys((
        "classified_unrendered", "dead_sections", "dedup_drops", "multi_record_coverage",
        "orphaned_fragments", "section_lost", "segmentation", "segmentation_collapse",
        "stage4_unplaced_items", "table_lost", "under_extraction", "unrendered_records",
    ), FIX_MINUTES_RECORD_LOST),
    **dict.fromkeys((
        "bucket_status", "grant_bucket", "group_header_context", "invented_records",
        "missed_headers", "offschema_fields", "owner_attribution", "section_consistency",
        "stage6_render_warnings", "taxonomy_code_coverage", "teaching_postcheck",
    ), FIX_MINUTES_MISPLACED),
    **dict.fromkeys((
        "citation_field_dropped", "citation_grounding", "date_cell_shape",
        "enrichment_pubtype_mismatch", "etal_added", "fanout_cell_residue", "grant_boundary",
        "implausible_year", "owner_missing_from_citation", "pubmed_title_truncated",
        "record_boundary", "role_consistency", "shattered_prose", "span_count",
        "split_child_unsourced", "stage4_group_failures", "summary_unsupported_claim",
        "wrong_start_date", "year_not_in_source",
    ), FIX_MINUTES_FIELD_WRONG),
    **dict.fromkeys((
        "duplicate_passages", "duplicate_records", "identical_rendered_rows", "junk_or_header_row",
    ), FIX_MINUTES_DUPLICATE),
    **dict.fromkeys((
        "date_only_lines", "llm_refusal_in_output", "output_hygiene", "pipe_leaks",
        "python_repr_in_output", "table_shape",
    ), FIX_MINUTES_FORMATTING),
    **dict.fromkeys((
        "enrichment_failures", "llm_fallback_served", "no_output", "pipeline_errors_present",
        "research_summary_call_failed", "stage3b_fallback_ratio", "stage3b_second_pass_error",
        "stage_failure_recorded",
    ), FIX_MINUTES_NONE),
}

#: A lint missing from LINT_FIX_MINUTES (a report written by a newer doctor)
#: costs what a wrong field does, the most common kind of finding.
DEFAULT_FIX_MINUTES = FIX_MINUTES_FIELD_WRONG

#: The doctor severities a finding counts at, as a multiplier. INFO findings
#: are notes the run page does not show as problems, so they cost nothing; the
#: WARN count was the signal that tracked defects on batch YUYVIG (#1595).
SEVERITY_WEIGHT = {"ERROR": 1.0, "WARN": 1.0, "INFO": 0.0}

#: The precision a lint is given when `doctor/PRECISION.md` records no
#: hand-checked verdict for it: a stated prior, not a measurement. On the
#: EBYSBC fit set the rank correlation moved by under 0.02 between 0.25 and
#: 0.75, so the value is not load-bearing there.
UNMEASURED_PRECISION_PRIOR = 0.5

#: The doctor's per-finding status for a lint that ran (`doctor.shared.STATUS_RAN`).
#: A skipped or unreadable lint's placeholder finding names no defect.
_DOCTOR_STATUS_RAN = "ran"

#: The points the doctor-findings dimension can take. It is the only weighted
#: dimension, so no estimate of cleanup time alone can take a run below
#: YELLOW: RED is left to the hard-fail caps, as Paul decided for the
#: content-loss caps on #822 (2026-10-02, "nothing below YELLOW").
DOCTOR_FINDINGS_WEIGHT = 100 - BAND_YELLOW

#: The estimated cleanup a GREEN run may need, in minutes. Set on the fit set
#: (batches EBYSBC, s7ab and pilot, 62 runs with verified defects; #1595): 48
#: of the 62 carry a verified HIGH, so GREEN can only be honest if it is rare.
#: Under 3.5 minutes, 5 runs and none with a HIGH; under 4, 8 runs and 1 with a
#: HIGH; under 5, 14 and 5. Goal 4 in docs/RUN_DOCTOR_SCORING.md asks for under
#: 1 in 10 GREEN runs with a HIGH, so 3. Refit on each labelled batch.
GREEN_MAX_CLEANUP_MINUTES = 3.0

#: The estimated minutes at which the dimension takes half its weight. The
#: penalty fraction is ``minutes / (minutes + DOCTOR_HALF_WEIGHT_MINUTES)``: 0
#: with no counted finding, rising ever more slowly and never reaching 1, so
#: more cleanup always scores lower and long runs stay apart (an exponential
#: with the same GREEN line rounds every run over about 28 minutes to 60).
#: Derived so that GREEN_MAX_CLEANUP_MINUTES costs exactly the points between
#: 100 and the GREEN line: 5 minutes with the constants above.
DOCTOR_HALF_WEIGHT_MINUTES = GREEN_MAX_CLEANUP_MINUTES * (
    DOCTOR_FINDINGS_WEIGHT - (100 - BAND_GREEN)) / (100 - BAND_GREEN)

#: The cap on a run with no readable doctor report (#1593: IXJMKS scored 97
#: GREEN with none). The score is built from the doctor's findings, so a run
#: the doctor did not check has no estimate and cannot read "ship".
NOT_CHECKED_CAP = BAND_GREEN - 1

#: How many lints the detail string names, costliest first.
_DETAIL_TOP_LINTS = 5


@dataclass(frozen=True)
class LintCost:
    """One lint's share of a run's estimated cleanup."""
    lint: str
    findings: int
    precision: float
    minutes: float


def lint_precision_weight(lint: str) -> float:
    """The lint's hand-checked precision from `doctor/PRECISION.md`, or
    UNMEASURED_PRECISION_PRIOR when nothing was judged."""
    measured = load_ledger().get(lint)
    if measured is None or measured.precision is None:
        return UNMEASURED_PRECISION_PRIOR
    return measured.precision


def estimate_cleanup(findings: Iterable[object]) -> list[LintCost]:
    """Each lint's estimated cleanup minutes over a doctor report's findings,
    costliest first: per finding, precision x severity weight x fix minutes.

    A finding counts only if its lint ran and its severity is one
    SEVERITY_WEIGHT weights; anything else in the list (a malformed entry, a
    skipped lint's placeholder) is ignored.
    """
    counts: Counter[str] = Counter()
    weighted: Counter[str] = Counter()
    for finding in findings:
        if not isinstance(finding, dict) or not isinstance(finding.get("lint"), str):
            continue
        if finding.get("status", _DOCTOR_STATUS_RAN) != _DOCTOR_STATUS_RAN:
            continue
        severity = SEVERITY_WEIGHT.get(finding.get("severity"), 0.0)
        if not severity:
            continue
        counts[finding["lint"]] += 1
        weighted[finding["lint"]] += severity
    costs = []
    for lint, n in counts.items():
        precision = lint_precision_weight(lint)
        minutes = precision * weighted[lint] * LINT_FIX_MINUTES.get(lint, DEFAULT_FIX_MINUTES)
        costs.append(LintCost(lint, n, precision, minutes))
    return sorted(costs, key=lambda c: (-c.minutes, c.lint))


def score_doctor_findings(outputs_dir: Path) -> tuple[float, str, int | None]:
    """The run's estimated cleanup, from its doctor report (#1595).

    The penalty fraction is ``minutes / (minutes + DOCTOR_HALF_WEIGHT_MINUTES)``
    over `estimate_cleanup`'s total. With no readable report the run was not
    checked: no penalty, and NOT_CHECKED_CAP keeps it out of GREEN.
    """
    report, reason = _load_first(outputs_dir, f"*{DOCTOR_REPORT_SUFFIX}")
    findings = report.get("findings") if isinstance(report, dict) else None
    if not isinstance(findings, list):
        why = _missing_or_unreadable_detail("doctor report", reason) if report is None \
            else "doctor report has no findings list"
        return 0.0, f"{why}: not checked; cap={NOT_CHECKED_CAP}", NOT_CHECKED_CAP
    costs = estimate_cleanup(findings)
    minutes = sum(c.minutes for c in costs)
    top = "; ".join(f"{c.lint} {c.findings}x p={c.precision:.2f} {c.minutes:.1f}min"
                    for c in costs[:_DETAIL_TOP_LINTS])
    detail = (f"estimated_cleanup_minutes={minutes:.1f} from "
              f"{sum(c.findings for c in costs)} findings" + (f" ({top})" if top else ""))
    return minutes / (minutes + DOCTOR_HALF_WEIGHT_MINUTES), detail, None


#: The WCM template stage 6 renders every CV into (`TEMPLATE_PATH` in
#: `stage_6_word_template.py`). Not imported from there: that module
#: `sys.exit`s at import when python-docx is missing, which would defeat this
#: scorer's graceful degradation. Read by `doctor.shared`'s template-text index.
_TEMPLATE_DOCX_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "key_files" / "wcm_cv_template_faculty_october_2022_final.docx"
)


def score_no_output(outputs_dir: Path) -> tuple[float, str, int | None]:
    """Whether the run produced a rendered docx AT ALL -- 'nothing to
    deliver', the single most severe outcome a run can have (#745). Weight 0:
    a pure gate, not a scored dimension.
    """
    has_docx = not no_docx_produced(outputs_dir)
    if no_output_produced(has_docx=has_docx):
        return 1.0, f"no docx produced; hard-fail cap={NO_OUTPUT_CAP}", NO_OUTPUT_CAP
    return 0.0, "docx present (or absence is ambiguous/unreadable, scored elsewhere)", None


def score_stage3b_fallback_ratio(outputs_dir: Path) -> tuple[float, str, int | None]:
    """Stage 3b's batch-fallback ratio (#810): hard-fail like
    score_pipeline_errors, and weight 0 like score_no_output -- it does not
    score classification quality, it only gates the case where a large share
    of it never happened.
    """
    data, reason = _load_first(outputs_dir, "*_classified.json")
    exceeded, detail = stage3b_fallback_ratio_exceeded(data)
    if exceeded:
        return 1.0, f"hard-fail cap={STAGE3B_FALLBACK_HARD_FAIL_CAP}: {detail}", \
            STAGE3B_FALLBACK_HARD_FAIL_CAP
    return 0.0, _missing_or_unreadable_detail("classified.json", reason) if data is None \
        else "within threshold", None


def score_stage4_group_failures(outputs_dir: Path) -> tuple[float, str, int | None]:
    """Stage 4's failed extraction groups (#1174): a cap-only gate like
    `score_protected_data`, NOT a scored dimension -- it carries no weight, so
    a run without a failed group scores exactly what it did before the gate
    existed. A missing or unreadable ``*_fields.json`` is quiet here: the
    owner and sparseness dimensions already penalise that absence in full."""
    data, reason = _load_first(outputs_dir, "*_fields.json")
    failures = stage4_group_failures(data)
    if failures is not None:
        return 1.0, failures.summary(), STAGE4_GROUP_FAILURE_CAP
    if data is None:
        return 0.0, _missing_or_unreadable_detail("fields.json", reason), None
    return 0.0, "no failed extraction group", None


# ---------------------------------------------------------------------------
# Dimension registry  -- single source of truth (name, weight, scorer fn)
# ---------------------------------------------------------------------------

DIMENSIONS = [
    ("Doctor findings: estimated cleanup, precision-weighted", DOCTOR_FINDINGS_WEIGHT,
     score_doctor_findings),
    # Retired to weight 0 by #1595, kept for their caps: on the two labelled
    # batches (EBYSBC/s7ab/pilot, 62 runs; YUYVIG, 37) the pipeline-error
    # penalty was 0 on every run, and the owner/contact penalty correlated
    # with the verified defects' cost at rank -0.16 and -0.08 (wrong sign).
    # docs/RUN_DOCTOR_SCORING.md has the table for all seven retired weights.
    ("Pipeline/API errors present (HARD-FAIL gate)", 0, score_pipeline_errors),
    ("CV owner name / contact populated (HARD-FAIL gate)", 0, score_cv_owner),
    ("No rendered output produced at all (HARD-FAIL gate)", 0, score_no_output),
    ("Stage-3b batch-fallback ratio (HARD-FAIL gate)", 0, score_stage3b_fallback_ratio),
]
TOTAL_WEIGHT = sum(w for _, w, _ in DIMENSIONS)

#: The cap a protected-data leak applies (`score_protected_data`).
PROTECTED_DATA_CAP = 25

#: Gates that CAP the final score but carry no weight (#820 round 2). Same
#: (fraction, detail, cap) contract as a dimension scorer; only the cap is
#: read. #1595 retired the nine content-loss caps at 84 (#822): each was a
#: doctor lint, so its findings now lower the score through
#: `score_doctor_findings` in proportion to their precision and fix time
#: instead of flipping the band. A cap stays only for a must-fix class with a
#: measured precision: protected personal data (RED, Paul on #822). The
#: stage-4 failed-group cap is #1174's separate decision and is unchanged.
CAP_ONLY_GATES = [
    ("Protected personal data absent from rendered docx (HARD-FAIL gate)", score_protected_data),
    ("Stage-4 extraction group failed (caps below GREEN)", score_stage4_group_failures),
]


def band_for(score: int) -> str:
    if score >= BAND_GREEN:
        return "GREEN (ship)"
    if score >= BAND_YELLOW:
        return "YELLOW (human cleanup needed)"
    return "RED (re-run / do-not-deliver)"


def score_run(run_output_dir: str | Path, run_id: str | None = None) -> dict:
    """Score a run's output directory. Returns the full breakdown dict."""
    outputs_dir = Path(run_output_dir)
    if not outputs_dir.exists():
        raise FileNotFoundError(f"Output directory not found: {run_output_dir}")

    if run_id is None:
        json_files = sorted(outputs_dir.glob("*.json"))
        run_id = json_files[0].name.split("_")[0] if json_files else "UNKNOWN"

    dimension_scores = []
    penalty = 0.0
    hard_fail_caps = []
    flags = []

    for name, weight, scorer in DIMENSIONS:
        fraction, detail, cap = scorer(outputs_dir)
        penalty += weight * fraction
        dimension_scores.append({
            "name": name,
            "score": round(weight * (1 - fraction), 2),
            "max": weight,
            "penalty": round(weight * fraction, 2),
            "detail": detail,
        })
        if cap is not None:
            hard_fail_caps.append(cap)
            flags.append(f"HARD-FAIL cap={cap}: {name} (fraction={fraction:.2f})")

    for name, gate in CAP_ONLY_GATES:
        fraction, detail, cap = gate(outputs_dir)
        if cap is not None:
            hard_fail_caps.append(cap)
            flags.append(f"HARD-FAIL cap={cap}: {name} ({detail})")

    # A dimension of weight w costs w * fraction points. Not normalized by
    # TOTAL_WEIGHT (#1595): the weights sum to 40, so that no weighted penalty
    # alone takes a run below YELLOW.
    raw = 100.0 - penalty
    final = raw
    for cap in hard_fail_caps:
        final = min(final, cap)
    total_score = round(max(0.0, final))

    if not flags:
        flags.append("No hard-fail caps triggered")

    # Artifact health (#724 review item 2): does not move the score -- the
    # dimensions already scored each absence -- but names what the score was
    # computed without, so "25 RED" on an empty directory reads as missing
    # evidence, not a measured result.
    missing = missing_evidence(outputs_dir)
    if missing:
        flags.append(
            f"EVIDENCE INCOMPLETE ({len(missing)} of {SCORED_ARTIFACT_COUNT} artifacts): "
            + "; ".join(missing))

    return {
        "run_id": run_id,
        "totalScore": total_score,
        "band": band_for(total_score),
        "raw_score_before_caps": round(raw, 2),
        "hard_fail_caps_applied": hard_fail_caps,
        "total_weight": TOTAL_WEIGHT,
        "dimensionScores": dimension_scores,
        "flags": flags,
        "data_complete": not missing,
        "missing_evidence": missing,
    }


#: The only modes quality_gate accepts. A typo (e.g. "blok") must fail
#: closed, not silently fall through to advisory (#724 review item 3) --
#: this is the single caller's only gate mode value, currently hardcoded to
#: "block" (quality_score.py:_main), never config- or env-driven.
VALID_GATE_MODES = frozenset({"off", "advisory", "block"})


def quality_gate(run_output_dir: str | Path, run_id: str | None = None, mode: str = "advisory") -> dict:
    """
    Run the scorer and return a gate verdict.

    mode:
      "off"      -> always PASS (scorer not consulted for gating)
      "advisory" -> compute verdict but never block (default)
      "block"    -> RED runs fail the gate (gate_passed=False)

    Raises ValueError for any other mode -- validated before scoring runs, so
    a misconfigured caller fails fast instead of silently defaulting to
    advisory (#724 review item 3).

    Returns the score_run() dict augmented with 'verdict' and 'gate_passed'.
    Callers decide what to do with gate_passed; this function never raises on a
    low score.
    """
    if mode not in VALID_GATE_MODES:
        raise ValueError(
            f"invalid quality_gate mode: {mode!r}; must be one of {sorted(VALID_GATE_MODES)}")

    result = score_run(run_output_dir, run_id)
    score = result["totalScore"]
    if score >= BAND_GREEN:
        verdict = "PASS"
    elif score >= BAND_YELLOW:
        verdict = "REVIEW"
    else:
        verdict = "BLOCK"

    if mode == "off":
        gate_passed = True
    elif mode == "block":
        gate_passed = verdict != "BLOCK"
    else:  # advisory
        gate_passed = True

    result["verdict"] = verdict
    result["gate_mode"] = mode
    result["gate_passed"] = gate_passed
    return result


def _main(argv) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    flags = {a for a in argv[1:] if a.startswith("--")}
    if not args:
        print("Usage: python3 quality_score.py <run_output_dir> [run_id] [--gate]", file=sys.stderr)
        return 2

    run_output_dir = args[0]
    run_id = args[1] if len(args) > 1 else None

    if "--gate" in flags:
        result = quality_gate(run_output_dir, run_id, mode="block")
        print(json.dumps(result, indent=2))
        # Non-zero exit on a blocking verdict so CI / pipeline steps can fail.
        return 0 if result["gate_passed"] else 1

    print(json.dumps(score_run(run_output_dir, run_id), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
