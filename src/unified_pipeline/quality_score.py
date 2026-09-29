#!/usr/bin/env python3
"""
CViche run quality scorer.

Computes a deterministic 0-100 quality score for a completed pipeline run by
reading the stage output artifacts only -- NO additional LLM calls. Intended as
a post-pipeline gate: surface low-quality runs (broken docx, over-use of the
``T`` catch-all class, missing CV owner, pipeline errors, duplicate-entry
fragmentation) before they are delivered.

Usage (CLI):
    python3 quality_score.py <run_output_dir> [run_id]
    python3 quality_score.py <run_output_dir> --gate     # exit 1 if RED

Usage (import):
    from quality_score import score_run, quality_gate
    result = score_run("/path/to/outputs", "ABC123")        # full breakdown
    gate   = quality_gate("/path/to/outputs", mode="advisory")

Scoring model
-------------
Each dimension returns a penalty fraction in [0, 1]. The weighted penalty is
normalized against the total available weight so a perfectly bad run scores 0
and a clean run scores 100::

    raw = 100 * (1 - sum(weight_i * fraction_i) / sum(weight_i))

Two dimensions are hard-fail gates: a fatal pipeline error or a missing CV
owner name caps the final score regardless of the other dimensions. A third
gate -- protected personal data in the rendered docx (#820) -- caps the score
the same way but carries NO weight (``CAP_ONLY_GATES``), so a clean run's raw
score is unchanged by its existence.

The result also says what the score was computed *without*: ``data_complete``
is False and ``missing_evidence`` names each scored artifact that was absent,
unreadable, or ambiguous (and a ``EVIDENCE INCOMPLETE`` flag repeats it), so a
score over an incomplete output directory is recognizable as missing evidence
rather than read as a precise measurement (#724 review item 2). The score
itself is unchanged by it.

Bands (PROVISIONAL -- see calibration note below):
    >= 85  GREEN   ship
    >= 60  YELLOW  human cleanup needed
    <  60  RED     re-run / do-not-deliver

CALIBRATION NOTE (2026-06-02): scored against 8 production runs from S3
(9TUVGW, 0GX6RA, B7TFKA, M2D90G, EVZ1YW, P2ZP1A, 7RHKJQ, VFFDCA). ALL scored
25-40 -- i.e. every current run is RED. Two systemic causes dominate the whole
distribution and must be fixed before the GREEN band is meaningful or the gate
is run in "block" mode:
  1. The stage_3b ``name 'response' is not defined`` bug records an error on
     ~100% of runs, tripping the pipeline-error hard-fail cap (40) on every run
     (so nothing can exceed 40). Fixed alongside this scorer.
  2. Systemic docx raw-tab / prompt-echo artifacts and high duplicate-entry
     ratios penalize every run.
The score IS discriminating within this range, but the absolute bands are not
yet trustworthy. SHIP ADVISORY-ONLY (the default); re-baseline the 85/60
thresholds against fresh runs once causes (1) and (2) are fixed and at least
one human-confirmed clean run exists. Treat GREEN as "no detected problems,"
never "human-verified correct."
"""

from __future__ import annotations

import functools
import json
import logging
import re
import sys
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from docx.document import Document as DocumentType
    from docx.oxml.table import CT_Tc
    from docx.table import Table

# #820/#825: the doctor's protected_data_in_output lint and this module's
# score_protected_data must not diverge, so the scorer CALLS the lint on
# the same body-order blocks (`docx_body_blocks`, the reader
# `run_doctor.read_docx_blocks` wraps) rather than keeping a second scan.
# Safe direction: neither `doctor.shared` nor `doctor.lints.protected_data`
# imports this module, so this does not create the cycle `quality_score ->
# run_doctor -> doctor.lints.enrichment -> quality_score` would (run_doctor.py
# itself is never imported here).
from unified_pipeline.core.template_boilerplate import (
    is_near_template_instruction,
    is_template_instruction,
    is_template_label_line,
    is_unanswered_prompt,
)
from unified_pipeline.doctor.lints.protected_data import lint_protected_data_in_output
from unified_pipeline.doctor.shared import docx_body_blocks
from unified_pipeline.stage_errors import STAGE_ERRORS_SUFFIX, read_stage_errors

logger = logging.getLogger(__name__)

# --- band thresholds (provisional; see module docstring) --------------------
BAND_GREEN = 85
BAND_YELLOW = 60


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
        with open(files[0]) as f:
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
    docx_files = sorted(outputs_dir.glob("*.docx"))
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
#: because score_sparse_tables/score_broken_format's neutral 0.5-with-no-docx
#: fraction is exactly as good as a mediocre-but-real docx). Set BELOW
#: score_cv_owner's 25 and score_pipeline_errors' 40: "nothing to deliver" is
#: more severe than either -- both of those caps still describe a run that
#: produced *something* a human could look at.
NO_OUTPUT_CAP = 20


def no_docx_produced(outputs_dir: Path) -> bool:
    """True only when the run's output directory has NO docx at all -- the
    absent case _load_docx reports as ``"no docx found"``. Deliberately
    narrower than "doc is None": an ambiguous match (more than one *.docx) or
    a present-but-corrupt file is a different failure, already scored by
    score_sparse_tables/score_broken_format's own 0.5 neutral fraction, and
    is not "nothing was produced" the way a genuinely missing file is."""
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
        try:
            with open(json_file) as f:
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


def score_cv_owner(outputs_dir: Path) -> tuple[float, str, int | None]:
    """CV owner name / contact. Missing name is a hard-fail (cap=25)."""
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

    fraction = 0.0
    if not inference_success:
        fraction += 0.4
    if not primary_location:
        fraction += 0.3
    if not any_contact:
        fraction += 0.3

    detail = (
        f"full_name={full_name!r}; inference_success={inference_success}; "
        f"primary_location={'set' if primary_location else 'missing'}; "
        f"any_contact={any_contact}; fraction={fraction:.2f}"
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
    the same body-order blocks (#825: one scan, not two that drift).
    """
    doc, reason = _load_docx(outputs_dir)
    if doc is None:
        return 0.5, reason, None
    hits = len(lint_protected_data_in_output(docx_body_blocks(doc)))
    if hits:
        return 1.0, f"protected_data_hits={hits}; hard-fail cap={PROTECTED_DATA_CAP}", PROTECTED_DATA_CAP
    return 0.0, "protected_data_hits=0", None


#: Same cell split as `core.template_boilerplate._LABEL_PIECE_SPLIT_RE`: a
#: table row reaches stage 3b joined by "|", and a wrapped source line by a
#: tab or newline. `is_unanswered_prompt` itself only splits on "|", so a
#: tab/newline-wrapped row is normalized onto pipes with this before being
#: handed to it (see `_is_placeholder_only_row`).
_PLACEHOLDER_CELL_SPLIT_RE = re.compile(r"[|\t\n]")


def _is_placeholder_only_row(text: str | None) -> bool:
    """True when *text* carries no content an editor could act on (#822
    finding 2, second exclusion): an unfilled template prompt ("N/A",
    "Not Applicable", a known label whose answer cell is one of those), or
    every cell is blank -- a bare ``| |`` row (YTPMZK's own example).

    Reuses `core.template_boilerplate.is_unanswered_prompt` for the first
    case -- the same "answer cell says nothing" check stage 6's own
    `_appendix_drop_reason` already uses -- rather than a second, narrower
    token set that would drift from it (an earlier version of this function
    kept its own ``{"n/a", "not applicable", "none"}`` constant, which
    already disagreed with `is_unanswered_prompt`'s own vocabulary by
    missing "na" and "listed above"; CODING_STANDARDS.md #1.5). A bare-
    separator row has no non-empty cell at all, so it can never satisfy
    `is_unanswered_prompt`'s own "at least one cell says N/A" guard --
    that shape is handled by the explicit check below instead, not folded
    into the reused helper.
    """
    stripped = (text or "").strip()
    if not stripped:
        return False
    pieces = _PLACEHOLDER_CELL_SPLIT_RE.split(stripped)
    if all(not p.strip() for p in pieces):
        return True
    return is_unanswered_prompt("|".join(pieces))


def _has_nothing_to_extract(entry: dict) -> bool:
    """True when stage 4 had no fields to find in *entry* (#427): stage 4
    skipped it (`extraction_skipped`, too short to extract), or its text is a
    placeholder-only row or the WCM template's own words -- the same
    `core.template_boilerplate` helpers `score_t_bucket` uses. Not keyed on
    taxonomy code: stage 4 does extract T entries, so a T miss still counts."""
    text = entry.get("text")
    if not isinstance(text, str):
        text = None
    if entry.get("extraction_skipped") or _is_placeholder_only_row(text):
        return True
    if is_template_instruction(text) or is_near_template_instruction(text):
        return True
    # ponytail: is_template_label_line is Appendix-only and "100%" is a label,
    # so "Clinical | 100%" (a real J effort record) would match; a label-only
    # header row carries no digit. Tighten if a digit-free real record appears.
    return is_template_label_line(text) and not any(c.isdigit() for c in text)


def _goal_claimed_row_ids(entries: list[dict]) -> set[int]:
    """``id()`` of every T entry stage 6 claims into an M2 grant's table as
    its major-goals row (#963/#1002; #822 finding 2). Reuses stage 6's
    `research_support.claim_goal_rows` instead of copying
    `MAJOR_GOALS_LABEL_RE` (CODING_STANDARDS.md §1.5).

    Not a faithful replay of stage 6, in two ways:
    - stage 6 first runs `fill_major_goals_from_text` on stage-4
      `extracted_fields` and skips a row whose goal conflicts with one already
      set; classified.json grants carry no `extracted_fields`, so that guard
      never fires here (making it faithful changed 0 of 215 farm files);
    - it ignores `rendered_grant_ids`, i.e. whether the grant found a
      template slot. A claimed row is scaffolding either way.

    Layering (CODING_STANDARDS.md §1): this reaches past stage6's public
    import surface into `stage6/sections/research_support.py`, judged better
    than a hand-copied regex. The import is function-local because that
    module needs python-docx, which quality_score treats as optional; without
    it this exclusion claims nothing and the other dimensions still run.
    """
    try:
        from unified_pipeline.stage6.sections.research_support import (
            RESEARCH_SUPPORT_SECTIONS,
            claim_goal_rows,
            copy_entries_for_render,
        )
    except ImportError:
        return set()

    grant_codes = {code for code, _header in RESEARCH_SUPPORT_SECTIONS}
    grants = copy_entries_for_render(
        [e for e in entries if e.get("taxonomy_code") in grant_codes])
    # Rows are passed uncopied, on purpose: claim_goal_rows never writes
    # through a row (only through the grant it claims into), and identity
    # here is what lets the caller map a claim back to the ORIGINAL entry.
    t_rows = [e for e in entries if e.get("taxonomy_code") == "T"]
    claimed = claim_goal_rows(grants, t_rows)
    return {id(row) for row, _grant in claimed}


def score_t_bucket(outputs_dir: Path) -> tuple[float, str, None]:
    """Share of entries in the stage_3b ``T`` catch-all ('nothing else fits').

    #822 finding 2: a T entry the pipeline correctly diverted -- template
    scaffolding, a placeholder row, or a grant's own goal statement that
    stage 6 renders into that grant's table -- costs an editor nothing, so it
    should not count as catch-all OVER-USE. Three exclusions, each matched
    against the T entry's own text in the classified.json ``entries`` list
    (not ``meta``, which has no per-entry text to match against):

      1. a grant's own major-goals row that stage 6 claims into that grant's
         table (`_goal_claimed_row_ids`).
      2. template instruction / near-template / label-only text, via the
         same `core.template_boilerplate` helpers stage 6 itself uses to
         drop this text -- no new phrase list (CODING_STANDARDS.md #1.5).
      3. a placeholder-only row (`_is_placeholder_only_row`).

    Each T entry is excluded by at most one of the three (checked in the
    order above -- the goal-claim check runs first because a row can
    otherwise satisfy both it and the template check at once, e.g. the
    template's own major-goals LABEL with real goal text appended; see
    `test_t_bucket_a_row_matching_two_reasons_is_excluded_only_once`), so a
    row matching more than one reason is not double-subtracted.

    The denominator (`total`) is deliberately UNCHANGED: an excluded entry is
    still real output the run produced, and total_entries is what the 3%/
    8%/15% ramp calibrates against as "how much this document contains," not
    "how much of it needs a human." Only the NUMERATOR -- what counts as
    unresolved catch-all -- shrinks. Judgement call (#822): re-scoring the
    farm is what tests whether the ramp still separates good runs from bad
    under this narrower numerator; see the PR description.
    """
    data, reason = _load_first(outputs_dir, "*_classified.json")
    if data is None:
        return 1.0, _missing_or_unreadable_detail("classified.json", reason), None

    meta = data.get("meta", {}) or {}
    code_dist = meta.get("code_distribution", {}) or {}
    total_entries_meta = meta.get("total_entries")
    if total_entries_meta is not None and total_entries_meta < 0:
        return _invalid_metadata_result(
            "t_bucket", "total_entries < 0", total_entries=total_entries_meta)
    if "code_distribution" in meta and total_entries_meta is not None:
        code_dist_sum = sum(code_dist.values())
        if code_dist_sum != total_entries_meta:
            return _invalid_metadata_result(
                "t_bucket", "sum(code_distribution) != total_entries",
                code_distribution_sum=code_dist_sum, total_entries=total_entries_meta)

    total = sum(code_dist.values()) or meta.get("total_entries", 1) or 1
    t_count_raw = code_dist.get("T", 0)

    entries = data.get("entries")
    entries = [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []
    excluded_template = excluded_placeholder = excluded_goal_claim = 0
    if entries:
        goal_claimed_ids = _goal_claimed_row_ids(entries)
        for entry in entries:
            if entry.get("taxonomy_code") != "T":
                continue
            text = entry.get("text")
            if id(entry) in goal_claimed_ids:
                excluded_goal_claim += 1
            elif (is_template_instruction(text) or is_near_template_instruction(text)
                    or is_template_label_line(text)):
                excluded_template += 1
            elif _is_placeholder_only_row(text):
                excluded_placeholder += 1

    t_count = max(t_count_raw - excluded_template - excluded_placeholder - excluded_goal_claim, 0)
    t_ratio = t_count / total

    if t_ratio <= 0.03:
        fraction = 0.0
    elif t_ratio <= 0.08:
        fraction = linear_interp(t_ratio, 0.03, 0.08, 0.0, 0.4)
    elif t_ratio <= 0.15:
        fraction = linear_interp(t_ratio, 0.08, 0.15, 0.4, 0.8)
    else:
        fraction = 1.0

    stats = meta.get("stats", {}) or {}
    tv = stats.get("t_validation", {}) or {}
    tv_error = tv.get("error") or ""
    if tv_error:
        fraction = clamp(fraction + 0.2)

    detail = (
        f"T_count_raw={t_count_raw}; T_excluded_template={excluded_template}; "
        f"T_excluded_placeholder={excluded_placeholder}; "
        f"T_excluded_goal_claim={excluded_goal_claim}; T_count={t_count}; total={total}; "
        f"t_ratio={t_ratio:.4f}; t_validation_error={tv_error!r}; fraction={fraction:.3f}"
    )
    return fraction, detail, None


def score_sparse_tables(outputs_dir: Path) -> tuple[float, str, None]:
    """Sparse / under-filled tables in the generated docx."""
    doc, reason = _load_docx(outputs_dir)
    if doc is None:
        return 0.5, reason, None

    tables = doc.tables
    total_tables = len(tables)
    if total_tables == 0:
        # Not perfect quality (#724 review item 6): the WCM template always
        # renders tables, so a docx with none is not our template's output --
        # worst-case fraction, not a false GREEN. Farm: 0 of 65 rendered docx
        # have zero tables, so this never fires on real output today.
        return 1.0, "no tables in docx (template always renders tables)", None

    total_cells = empty_cells = sparse_count = 0
    for tbl in tables:
        t_total = t_empty = 0
        for row in tbl.rows:
            for cell in row.cells:
                t_total += 1
                if not cell.text.strip():
                    t_empty += 1
        total_cells += t_total
        empty_cells += t_empty
        if t_total > 0 and t_empty / t_total >= 0.5:
            sparse_count += 1

    sparse_table_ratio = sparse_count / total_tables
    global_empty_ratio = empty_cells / total_cells if total_cells else 0.0
    a = 0.6 * (sparse_table_ratio / 0.25)
    b = 0.4 * ((global_empty_ratio - 0.10) / 0.40)
    fraction = clamp(a + b)

    detail = (
        f"total_tables={total_tables}; sparse_tables={sparse_count}; "
        f"sparse_table_ratio={sparse_table_ratio:.3f}; empty_cells={empty_cells}/{total_cells}; "
        f"global_empty_ratio={global_empty_ratio:.3f}; fraction={fraction:.3f}"
    )
    return fraction, detail, None


#: The pristine WCM template's own incidental tab: table 16 row 1 col 0's
#: "Project title:\t\t" label cell (confirmed by scanning
#: `key_files/wcm_cv_template_faculty_october_2022_final.docx` directly). It
#: IS reachable from rendered CV content -- 27 of 65 farm docx contain this
#: exact cell text, and for all 27 it is their ONLY raw-tab cell (#724
#: follow-up review) -- so it is excluded by exact text match, the smallest
#: equivalent of the INSTRUCTION_MARKERS exclusion `score_broken_format`
#: already applies for the same reason: penalizing the template's own
#: boilerplate is not a genuine raw-formatting artifact.
_TEMPLATE_TAB_CELL_TEXT = "Project title:\t\t"


def _count_raw_tab_cells(
        tables: Iterable[Table], _depth: int = 0,
        _seen_tc: set[CT_Tc] | None = None) -> int:
    """Raw-tab paragraphs inside every cell of `tables`, nested tables one
    level deep via `cell.tables` (#724 follow-up review, D7'); the recursion
    is bounded by `_depth` so a table nested inside a table nested inside a
    table is not walked a third level down, matching this docstring. A
    paragraph whose text is exactly `_TEMPLATE_TAB_CELL_TEXT` is excluded:
    it is the template's own boilerplate, not a rendering defect.

    A cell merged across columns (gridSpan) is repeated once per spanned
    column in `row.cells` -- python-docx does not collapse it -- so counting
    every `row.cells` entry would count one physical cell's tab once per
    spanned column. Dedupe by the underlying `w:tc` element so each physical
    cell is visited once (#724 second follow-up review, F3).

    The dedupe set holds the `cell._tc` elements themselves, not `id(...)`
    of them: `id()` alone is a memory address, and without a live reference
    keeping the element's temporary python-docx wrapper alive, a later,
    unrelated cell's wrapper can be allocated at the same freed address and
    collide -- confirmed against a real farm docx, where an `id()`-only set
    silently dropped a genuine tab-containing cell as a false "already seen"
    duplicate. Storing the element itself in the set keeps it alive for the
    whole walk, so identity stays meaningful.
    """
    if _seen_tc is None:
        _seen_tc = set()
    count = 0
    for table in tables:
        for row in table.rows:
            for cell in row.cells:
                tc = cell._tc
                if tc in _seen_tc:
                    continue
                _seen_tc.add(tc)
                for p in cell.paragraphs:
                    if "\t" in p.text and p.text != _TEMPLATE_TAB_CELL_TEXT:
                        count += 1
                if _depth < 1:
                    count += _count_raw_tab_cells(cell.tables, _depth + 1, _seen_tc)
    return count


#: Template instruction text left standing in a rendered CV ("prompt echo").
#: Each alternation is a phrase the pristine WCM template
#: (`key_files/wcm_cv_template_faculty_october_2022_final.docx`) itself uses
#: in an instruction line -- every "please" there is "Please include / list /
#: summarize / annotate / provide / choose / keep / do not / also include",
#: every "e.g.," is "e.g., 50%" / "(e.g., sessions" / "(e.g., drugs", and
#: "bedside" appears only as "(bedside teaching, teaching rounds, ...)" --
#: rather than the bare words. The bare words were the #724 review's item 8:
#: "please" and "e.g.," occur in ordinary academic prose and "bedside" in
#: citation titles. Measured on the 65-docx farm (22,546 body paragraphs)
#: before narrowing: the bare pattern hit 1,312 paragraphs, this one 1,300;
#: the 12 dropped are 10 citation titles containing "bedside", one teaching
#: bullet ("including Bedside Teaching") and one research summary with
#: "e.g.," -- every one legitimate content -- and it gains nothing the bare
#: pattern missed (strict subset). Every one of the template's 20 body
#: instruction paragraphs still matches (pinned by a test that reads the
#: template). Score effect, measured with score_run over all 66 farm uids:
#: the dimension's fraction is clamp(0.6 * tabs/20 + 0.4 * echoes/15) with
#: the clamp on the sum, so an echo count above 15 still counts; the six
#: docx that lost 1-3 false positives drop 0.027-0.080 on this dimension,
#: three totals rise by one point (77->78, 78->79 twice), no band changes.
INSTRUCTION_MARKERS = re.compile(
    r"(please (?:include|list|summarize|annotate|provide|choose|keep|do not|also include)"
    r"|delete the others|list here|choose one"
    r"|bedside teaching, teaching rounds"
    r"|e\.g\., (?:50%|sessions|drugs)"
    r"|yyyy-yyyy|\(optional\)|\(Research, clinical)",
    re.IGNORECASE,
)

#: The WCM template stage 6 renders every CV into (`TEMPLATE_PATH` in
#: `stage_6_word_template.py`). Not imported from there: that module
#: `sys.exit`s at import when python-docx is missing, which would defeat this
#: scorer's graceful degradation. The 2020/2012 files in `key_files/` are not
#: render targets, so only this revision's paragraphs are excluded.
_TEMPLATE_DOCX_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "key_files" / "wcm_cv_template_faculty_october_2022_final.docx"
)


def _normalize_whitespace(text: str) -> str:
    """Collapse whitespace runs to one space and strip the ends."""
    return " ".join(text.split())


@functools.cache
def _template_body_paragraph_texts() -> frozenset[str]:
    """Whitespace-normalized text of every non-blank body paragraph of the
    template stage 6 renders into (#822 finding 1).

    Stage 6 keeps the template's own instruction paragraphs on purpose, so a
    rendered paragraph identical to one of these is not a prompt echo.
    A missing template raises (python-docx's own FileNotFoundError): it is a
    checked-in asset, and an empty set would silently undo the exclusion.
    """
    from docx import Document
    template_doc = Document(_TEMPLATE_DOCX_PATH)
    return frozenset(
        _normalize_whitespace(p.text) for p in template_doc.paragraphs if p.text.strip()
    )


def score_broken_format(outputs_dir: Path) -> tuple[float, str, None]:
    """Raw-tab and prompt-echo (template instruction) artifacts in the docx.

    Two different scans, two different scopes, on purpose:

    - Prompt-echo (``INSTRUCTION_MARKERS``) scans body paragraphs ONLY. A
      #724 follow-up review probe confirmed every marker this pattern
      checks ("please provide", "yyyy-yyyy", "(optional)", "(Research,
      clinical") occurs verbatim in the pristine WCM template's own table cells --
      table 1 row 7 col 0 "If yes, please provide Visa type (Examples: J-1,
      H-1B, E-3, TN, etc.):", table 9 rows 0-1 col 0 "DEA number:
      (optional)" / "NPI number: (optional)", table 13 row 0 col 1 "Date
      (yyyy-yyyy)" (and the same label repeated in tables 19-29), table
      17/18 row 5 col 0 "Type of Supervision (research, clinical, teaching,
      leadership)" -- confirmed against
      `key_files/wcm_cv_template_faculty_october_2022_final.docx` directly,
      independent of any rendered CV. Scanning cells for these markers would
      therefore false-positive on the template's own label text in every
      one of the 65 farm docx (65/65), not catch an echoed-into-content
      defect, so the instruction-marker check stays paragraph-only and is
      deliberately never applied to cells.
    - The raw-tab check DOES scan every paragraph of every table cell
      (nested tables one level deep), in addition to body paragraphs. A raw
      ``\\t`` is not template boilerplate the way the instruction markers
      are -- the pristine template contains exactly one incidental tab, a
      static "Project title:" label row, and it IS reachable from rendered
      CV content: 27 of 65 farm docx contain that exact cell text as their
      only raw-tab cell (#724 follow-up review), so `_count_raw_tab_cells`
      excludes that one cell text by exact match (`_TEMPLATE_TAB_CELL_TEXT`)
      the same way the instruction markers above are excluded from cells --
      versus the instruction markers' dozens of legitimate hits -- so any
      other tab inside a cell is still a meaningful signal of a
      raw-formatting artifact leaking into the docx.
    - A body paragraph that matches ``INSTRUCTION_MARKERS`` is still not
      counted as an echo if its whitespace-normalized text exactly equals
      one of the template's own body paragraphs
      (`_template_body_paragraph_texts`, #822 finding 1). Stage 6 keeps the
      template's 20 instruction paragraphs verbatim in every rendered CV on
      purpose, and every one of them matches ``INSTRUCTION_MARKERS`` by
      construction (that is how the pattern was narrowed) -- without this
      exclusion, every run counts the template's own kept text as a defect.
      A marker hit that is NOT byte-identical to a template paragraph still
      counts: a marker phrase originating in the template's own table cells
      (e.g. "Date (yyyy-yyyy)") landing as a body paragraph, or a template
      instruction line altered by the pipeline before being kept, are both
      still genuine signals, not the template's own untouched text.
    - The same exclusion applies to raw-tab body paragraphs (#822): the
      template's own signature-block and employment lines ("Signature:
      \\t\\t\\t\\t", "Name of Current Employer(s):\\t", ...) carry tabs and
      stage 6 keeps them, so a tabbed paragraph whose whitespace-normalized
      text equals a template body paragraph's is reported as
      ``template_tab_excluded``, not counted. A tabbed line carrying any
      text of its own (a filled-in value) still counts.

    Headers and footers are not scanned either way: stage 6 never writes to
    them.
    """
    doc, reason = _load_docx(outputs_dir)
    if doc is None:
        return 0.5, reason, None

    template_paragraphs = _template_body_paragraph_texts()

    raw_tab_paragraphs = echo_count = template_echo_excluded = template_tab_excluded = 0
    for p in doc.paragraphs:
        text = p.text
        in_template = _normalize_whitespace(text) in template_paragraphs
        if "\t" in text:
            if in_template:
                template_tab_excluded += 1
            else:
                raw_tab_paragraphs += 1
        if INSTRUCTION_MARKERS.search(text):
            if in_template:
                template_echo_excluded += 1
            else:
                echo_count += 1

    raw_tab_cells = _count_raw_tab_cells(doc.tables)
    total_raw_tab = raw_tab_paragraphs + raw_tab_cells

    fraction = clamp(0.6 * (total_raw_tab / 20) + 0.4 * (echo_count / 15))
    detail = (
        f"raw_tab_paragraphs={raw_tab_paragraphs}; raw_tab_cells={raw_tab_cells}; "
        f"template_tab_excluded={template_tab_excluded}; "
        f"echo_paragraphs={echo_count}; template_echo_excluded={template_echo_excluded}; "
        f"fraction={fraction:.3f}"
    )
    return fraction, detail, None


def score_field_sparseness(outputs_dir: Path) -> tuple[float, str, None]:
    """Entry-level field-extraction sparseness.

    Deliberate double-signal, not an accident (#724 review item 9):
    ``success_rate`` measures the extractor (how many entries the extraction
    call reported success on), while allnull_or_zero measures the entries
    (how many carry no usable fields regardless of what the extractor
    claimed). An entry that fails both is meant to weigh on both terms --
    that is the calibration, not a double-count of one failure.

    #427: an entry with nothing to extract (`_has_nothing_to_extract`) counts
    on neither term -- its all-null fields or skipped extraction are correct
    output, not a miss. As in `score_t_bucket`, only the numerators
    shrink; the denominator stays every entry the run produced.
    """
    data, reason = _load_first(outputs_dir, "*_fields.json")
    if data is None:
        return 1.0, _missing_or_unreadable_detail("fields.json", reason), None

    entries = data.get("entries", [])
    total = len(entries)
    if total == 0:
        return 1.0, "no entries", None

    allnull_or_zero = failed_count = nothing_to_extract = 0
    for e in entries:
        if _has_nothing_to_extract(e):
            nothing_to_extract += 1
            continue
        ef = e.get("extracted_fields", {}) or {}
        cov = e.get("extraction_coverage", {}) or {}
        cov_pct = cov.get("extraction_coverage_percent", None)
        if not e.get("extraction_success", False):
            failed_count += 1
        all_null = all(v is None for v in ef.values()) if ef else True
        zero_cov = (cov_pct is not None and cov_pct == 0)
        if all_null or zero_cov:
            allnull_or_zero += 1

    success_rate = 1 - failed_count / total
    a = 0.5 * ((allnull_or_zero / total) / 0.10)
    b = 0.5 * ((1 - success_rate) / 0.10)
    fraction = clamp(a + b)
    detail = (
        f"total_entries={total}; nothing_to_extract={nothing_to_extract}; "
        f"allnull_or_zerocov={allnull_or_zero}; "
        f"success_rate={success_rate:.3f}; fraction={fraction:.3f}"
    )
    return fraction, detail, None


def score_duplicate_ratio(outputs_dir: Path) -> tuple[float, str, None]:
    """Duplicate-entry ratio (de-dup / fragmentation health)."""
    data, reason = _load_first(outputs_dir, "*_classified.json")
    if data is None:
        return 1.0, _missing_or_unreadable_detail("classified.json", reason), None

    meta = data.get("meta", {}) or {}
    total = meta.get("total_entries", 0) or 0
    dup = meta.get("duplicate_entries", 0) or 0
    if total < 0:
        return _invalid_metadata_result(
            "duplicate_ratio", "total_entries < 0", total_entries=total)
    if dup < 0:
        return _invalid_metadata_result(
            "duplicate_ratio", "duplicate_entries < 0", duplicate_entries=dup)
    if dup > total:
        return _invalid_metadata_result(
            "duplicate_ratio", "duplicate_entries > total_entries",
            duplicate_entries=dup, total_entries=total)
    if total == 0:
        return 0.0, "total_entries=0", None

    dup_ratio = dup / total
    if dup_ratio <= 0.10:
        fraction = 0.0
    elif dup_ratio <= 0.30:
        fraction = linear_interp(dup_ratio, 0.10, 0.30, 0.0, 0.4)
    elif dup_ratio <= 0.50:
        fraction = linear_interp(dup_ratio, 0.30, 0.50, 0.4, 0.8)
    else:
        fraction = 1.0

    edata, edata_reason = _load_first(outputs_dir, "*_entries.json")
    coverage_pct = (edata or {}).get("coverage", {}).get("coverage_percentage") if edata else None

    entries_note = f"; {_missing_or_unreadable_detail('entries.json', edata_reason)}" if edata_reason else ""
    detail = (
        f"total_entries={total}; duplicate_entries={dup}; dup_ratio={dup_ratio:.3f}; "
        f"entries_coverage_pct={coverage_pct}; fraction={fraction:.3f}{entries_note}"
    )
    return fraction, detail, None


def score_no_output(outputs_dir: Path) -> tuple[float, str, int | None]:
    """Whether the run produced a rendered docx AT ALL -- 'nothing to
    deliver', the single most severe outcome a run can have (#745). Weight 0:
    this is a pure gate, not a scored dimension -- score_sparse_tables and
    score_broken_format already assign their own (unchanged) 0.5 neutral
    fraction when there is no docx to inspect, so giving this a nonzero
    weight would raise the score of every OTHER run in the corpus (a bigger
    TOTAL_WEIGHT denominator with no matching penalty) purely because this
    dimension was added, not because anything about those runs changed.
    """
    has_docx = not no_docx_produced(outputs_dir)
    if no_output_produced(has_docx=has_docx):
        return 1.0, f"no docx produced; hard-fail cap={NO_OUTPUT_CAP}", NO_OUTPUT_CAP
    return 0.0, "docx present (or absence is ambiguous/unreadable, scored elsewhere)", None


def score_stage3b_fallback_ratio(outputs_dir: Path) -> tuple[float, str, int | None]:
    """Stage 3b's batch-fallback ratio (#810): hard-fail like
    score_pipeline_errors, same reasoning as score_no_output for weight 0 --
    this does not re-score classification quality (score_t_bucket already
    does), it only gates the case where a large share of it never happened.
    """
    data, reason = _load_first(outputs_dir, "*_classified.json")
    exceeded, detail = stage3b_fallback_ratio_exceeded(data)
    if exceeded:
        return 1.0, f"hard-fail cap={STAGE3B_FALLBACK_HARD_FAIL_CAP}: {detail}", \
            STAGE3B_FALLBACK_HARD_FAIL_CAP
    return 0.0, _missing_or_unreadable_detail("classified.json", reason) if data is None \
        else "within threshold", None


# ---------------------------------------------------------------------------
# Dimension registry  -- single source of truth (name, weight, scorer fn)
# ---------------------------------------------------------------------------

DIMENSIONS = [
    ("Pipeline/API errors present (HARD-FAIL gate)", 25, score_pipeline_errors),
    ("CV owner name / contact populated (HARD-FAIL gate)", 15, score_cv_owner),
    ("T-bucket share (stage_3b catch-all over-use)", 15, score_t_bucket),
    ("Sparse / under-filled tables in generated docx", 12, score_sparse_tables),
    ("Broken table / raw formatting artifacts in docx", 10, score_broken_format),
    ("Field-extraction sparseness (entry-level)", 8, score_field_sparseness),
    ("Duplicate-entry ratio (de-dup / fragmentation health)", 10, score_duplicate_ratio),
    ("No rendered output produced at all (HARD-FAIL gate)", 0, score_no_output),
    ("Stage-3b batch-fallback ratio (HARD-FAIL gate)", 0, score_stage3b_fallback_ratio),
]
TOTAL_WEIGHT = sum(w for _, w, _ in DIMENSIONS)

#: The cap a protected-data leak applies (`score_protected_data`).
PROTECTED_DATA_CAP = 25

#: Gates that CAP the final score but carry no weight (#820 round 2): they
#: are not in DIMENSIONS, so TOTAL_WEIGHT and every clean run's raw score
#: are exactly what they were before the gate existed -- a batch scored
#: last month is still comparable to one scored today. Same (fraction,
#: detail, cap) contract as a dimension scorer; only the cap is read.
CAP_ONLY_GATES = [
    ("Protected personal data absent from rendered docx (HARD-FAIL gate)", score_protected_data),
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

    # Normalized so a fully-penalized run scores 0 and a clean run scores 100.
    raw = 100.0 * (1 - penalty / TOTAL_WEIGHT)
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
