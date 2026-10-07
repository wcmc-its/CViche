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

The weights sum to 100, so a dimension of weight w costs exactly w * fraction
points of the raw score.

Two dimensions are hard-fail gates: a fatal pipeline error or a missing CV
owner name caps the final score regardless of the other dimensions. A third
gate -- protected personal data in the rendered docx (#820) -- caps the score
the same way but carries NO weight (``CAP_ONLY_GATES``), so a clean run's raw
score is unchanged by its existence. A fourth, also cap-only, keeps a run in
which a stage-4 extraction group failed outright out of GREEN (#1174). A
call the content-filter fallback model served does not cap (#1174, Paul
2026-10-05): it succeeded, so the doctor's `llm_fallback_served` WARN records
it and the score does not.

Eight more cap-only gates (#822) cover content the pipeline lost or garbled,
a thing no weighted dimension measures: an under-extracted entry, several fused
entries, a lost source table, the CV owner cut from several of their own
citations, a grant list cut in the wrong place, a grant application rendered
as an award, several headers or labels rendered as records, and several group
headers whose lines render without them. Each caps a run
at ``CONTENT_LOSS_CAP``, just under GREEN, so a run with a verified loss cannot
read "ship". They are the doctor's own signals, called
rather than re-derived, restricted to the ones a batch hand-checked as real: a
lint feeds this cap only while `doctor/PRECISION.md` records its precision at
80% or more on 20 or more hits (Paul's decision on #822, 2026-10-02).

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
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from docx.document import Document as DocumentType
    from docx.oxml.table import CT_Tc
    from docx.table import Table
    from docx.text.paragraph import Paragraph

# #820/#825: the doctor's protected_data_in_output lint and this module's
# score_protected_data must not diverge, so the scorer CALLS the lint on
# the same body-order blocks (`docx_body_blocks`, the reader
# `run_doctor.read_docx_blocks` wraps) rather than keeping a second scan.
# Safe direction: neither `doctor.shared` nor `doctor.lints.protected_data`
# imports this module, so this does not create the cycle `quality_score ->
# run_doctor -> doctor.lints.enrichment -> quality_score` would (run_doctor.py
# itself is never imported here).
from unified_pipeline.core.template_boilerplate import (
    is_foreign_template_instruction,
    is_near_template_instruction,
    is_template_instruction,
    is_template_label_line,
    is_unanswered_prompt,
)
from unified_pipeline.doctor.lints.extraction import (
    lint_grant_boundary,
    lint_grant_bucket,
    lint_under_extraction,
)
from unified_pipeline.doctor.lints.protected_data import lint_protected_data_in_output
from unified_pipeline.doctor.lints.render import (
    lint_group_header_context,
    lint_junk_or_header_row,
    lint_owner_missing_from_citation,
)
from unified_pipeline.doctor.shared import (
    _cell_text,
    _docx_text,
    docx_body_blocks,
    docx_table_rows,
)
from unified_pipeline.llm_provenance import (
    FALLBACK_SERVED_KEY,
    PROMPT_LOG_RESPONSE_SUFFIX,
    STAGE4_5_FALLBACK_CALLS_KEY,
    STAGE4_ENTRY_FALLBACK_KEY,
)
from unified_pipeline.segmentation_regression import (
    count_mega_entries,
    find_lost_blocks,
    iter_source_block_lines,
)
from unified_pipeline.stage4.error_codes import NO_MATCHING_EXTRACTION
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


#: The cap the content-loss gates apply (#822): one point under GREEN, so a run
#: with a verified loss cannot read "ship" but is not pushed toward RED -- none
#: of these signals says the document is undeliverable, only that source
#: content did not reach it. Derived from BAND_GREEN so the two cannot drift.
#: Not shared with `STAGE4_GROUP_FAILURE_CAP` (#1174): that is a separate
#: policy for a separate signal that happens to land on the same value, and
#: retuning one should not silently move the other.
CONTENT_LOSS_CAP = BAND_GREEN - 1

#: Fused entries cap a run only at this count. One fused entry is common and
#: can be harmless (batch IPXFBA: CTXOTY's single fused entry kept every
#: mentee); the runs that lost records had several (EKGTXD 4, PBSGQZ 3 flagged).
#: A threshold fitted to one batch: revisit with more data.
MEGA_ENTRIES_CAP_MIN = 2

#: A lost source table caps a run only when its worst table lost this many
#: lines. The doctor's own `table_lost` floor is 3 lines, and the short lost
#: tables the corpus shows are template labels (#1102's noise class); the one
#: loss verified in batch IPXFBA (TALVAE) was far larger. Fitted to one batch.
LOST_TABLE_CAP_MIN_LINES = 5

#: The owner cut from this many of their own citations caps a run (Paul's
#: decision on #822, 2026-10-02). `owner_missing_from_citation` measured 27 of
#: 29 hits real on the 63-run EBYSBC/s7ab/pilot farm (doctor/PRECISION.md, M3);
#: one or two cut citations are left to the finding alone, the decision's own
#: starting count, to be re-set from the harness.
OWNER_MISSING_CITATIONS_CAP_MIN = 3

#: `grant_boundary` findings that cap a run (#1226): a grant list whose stage-2
#: cut slipped, so grants render with a neighbour's title, PI or dates. Three,
#: not one: a slipped cut carries down the list (ZCTARO/KUUKNJ 13, CXRYCF 10,
#: CTWLTR 8, DXAGUS 6, VGHNZD 5, VYRDHN 4 in the stored runs), while a lone
#: hit is one grant's edge, and both false positives measured so far sit on
#: runs with 1 or 2 hits (RXYBVF 502, VYNARH 648). At 3 or more: 59 of 59
#: hand-checked or matched to a verified finding (doctor/PRECISION.md, M4-cap).
GRANT_BOUNDARY_CAP_MIN = 3

#: `grant_bucket` application-as-award findings that cap a run (#1343): one,
#: because a single application rendered as a funded award already misstates
#: the faculty member's funding. 30 of 30 (ZDCXIV-01 and its RCBKFG re-run
#: FLYBMX, one CV; doctor/PRECISION.md, M4-cap). The lint's other shape (a
#: Current grant whose end date has passed) does not cap: 2 judged hits, and
#: it reads today's date, so rescoring a stored run next year would move it.
GRANT_APPLICATION_AS_AWARD_CAP_MIN = 1

#: `junk_or_header_row` findings that cap a run (EBYSBC E8/E10/E29): a group
#: header, lead-in label or date fragment rendered as a record. No content is
#: lost, but the document needs cleanup. Five: one to three such rows take a
#: minute to delete, and the lint's out-of-sample false positives (batch
#: NDMRSO) all sit on runs with 1 to 3 hits (EHGXAL 3 of 3 false, UYQRUN 2 of
#: 3, TVZDVF 1 of 3, REOYVH and SVYSGY 1 of 1). At 5 or more: 100 of 105
#: (95%) over the EBYSBC farm and NDMRSO; no RCBKFG run reaches 5
#: (doctor/PRECISION.md, M4-cap).
JUNK_ROWS_CAP_MIN = 5

#: `group_header_context` WARN findings that cap a run (X6 E8; Paul approved
#: feeding the cap 2026-10-07): a society, employer or course line coded as a
#: record, whose lines render without its name (`children_lost_header`), or a
#: run of bare roles rendered without what they were held in
#: (`role_without_holder`). One finding is one header or one run of roles,
#: however many rows it names. Only the WARN shapes count: the INFO shapes
#: (a lead line coded unlike its list, a role that lost a block's dates) are
#: n=22 and n=21 on two CVs. Four, not one: a header coded as a record
#: repeats down a CV's society and employer lists, while at 3 two runs would
#: cap on one true hit beside two partials (SDEBQJ, ZQJVRN). At 4 or more:
#: 95 of 99 true over the stored `analysis` runs (KHXOUF 106 false; UYQRUN
#: 176 and IZJADE/WYMVGU 479 partial), and 4 runs move out of GREEN, each
#: hand-read (doctor/PRECISION.md, X6-header-cap).
GROUP_HEADER_CAP_MIN = 4

#: Subdirectory of the scored directory that holds the run's original uploaded
#: .docx. Optional: `score_lost_source_table` reads the source to find tables
#: that never reached stage 2, and is simply not evaluated without it.
SOURCE_DOCX_SUBDIR = "source"


def score_under_extracted_records(outputs_dir: Path) -> tuple[float, str, int | None]:
    """Cap-only gate: a large multi-record entry whose stage-4 extraction covered
    under 40% of it, so its other records vanish (#822). The doctor's
    `under_extraction` lint, called as is. In batch IPXFBA its 4 findings were
    all true positives, but only 2 lost records outright (MYAXRH, ZGNARO); the
    other 2 (EKGTXD) were garbled or recovered by stage 6, and that run's lost
    records reach the cap through the fused-entries gate. All 3 runs carrying a
    finding had verified loss somewhere. Outside IPXFBA a finding can fire with
    nothing lost; any finding still caps (a judgement call)."""
    data, reason = _load_first(outputs_dir, "*_fields.json")
    if data is None:
        return 0.0, f"{_missing_or_unreadable_detail('fields.json', reason)}; not evaluated", None
    findings = len(lint_under_extraction(data))
    if findings:
        return 1.0, f"under_extraction_findings={findings}; cap={CONTENT_LOSS_CAP}", CONTENT_LOSS_CAP
    return 0.0, "under_extraction_findings=0", None


def score_fused_entries(outputs_dir: Path) -> tuple[float, str, int | None]:
    """Cap-only gate: stage 2 fused several records into one entry, in
    MEGA_ENTRIES_CAP_MIN or more entries (#822). Counted by the same function
    as the doctor's `mega_entries` flag; 7 of the 9 flagged entries in batch
    IPXFBA were real, which is why the cap needs a count and not one entry."""
    data, reason = _load_first(outputs_dir, "*_entries.json")
    if data is None:
        return 0.0, f"{_missing_or_unreadable_detail('entries.json', reason)}; not evaluated", None
    fused = count_mega_entries(data.get("entries", []))
    if fused >= MEGA_ENTRIES_CAP_MIN:
        return 1.0, f"mega_entries={fused}; cap={CONTENT_LOSS_CAP}", CONTENT_LOSS_CAP
    return 0.0, f"mega_entries={fused}", None


def _source_block_lines(outputs_dir: Path) -> list[tuple[int, str]] | None:
    """The source docx's lines tagged by table, or None when no usable source
    was supplied under SOURCE_DOCX_SUBDIR (absent, ambiguous or unreadable)."""
    from docx.opc.exceptions import PackageNotFoundError
    from lxml.etree import XMLSyntaxError

    candidates = sorted((outputs_dir / SOURCE_DOCX_SUBDIR).glob("*.docx"))
    if len(candidates) != 1:
        if candidates:
            logger.warning("quality_score found multiple source docx in %s: %s",
                           outputs_dir, ", ".join(c.name for c in candidates))
        return None
    try:
        return iter_source_block_lines(str(candidates[0]))
    except (OSError, zipfile.BadZipFile, KeyError, ValueError, PackageNotFoundError,
            XMLSyntaxError) as e:
        logger.warning("quality_score could not read source docx %s (%s: %s)",
                       candidates[0], type(e).__name__, e)
        return None


def score_lost_source_table(outputs_dir: Path) -> tuple[float, str, int | None]:
    """Cap-only gate: a source table whose text stage 2 mostly lost, at
    LOST_TABLE_CAP_MIN_LINES or more lost lines (#822). The primitive behind the
    doctor's `table_lost` lint. Needs the source docx (SOURCE_DOCX_SUBDIR): the
    scorer's other artifacts cannot show a table the reader never saw (TALVAE,
    batch IPXFBA: a 39-line nested table, the one verified `table_lost`)."""
    data, reason = _load_first(outputs_dir, "*_entries.json")
    if data is None:
        return 0.0, f"{_missing_or_unreadable_detail('entries.json', reason)}; not evaluated", None
    block_lines = _source_block_lines(outputs_dir)
    if block_lines is None:
        return 0.0, "no readable source docx; not evaluated", None
    worst = max((len(b["lost_lines"]) for b in find_lost_blocks(block_lines, data)), default=0)
    if worst >= LOST_TABLE_CAP_MIN_LINES:
        return 1.0, f"worst_lost_table_lines={worst}; cap={CONTENT_LOSS_CAP}", CONTENT_LOSS_CAP
    return 0.0, f"worst_lost_table_lines={worst}", None


def _content_loss_count_gate(label: str, count: int,
                             minimum: int) -> tuple[float, str, int | None]:
    """A cap-only gate's verdict on a doctor lint's finding count: the
    content-loss cap at `minimum` findings or more, else nothing."""
    if count >= minimum:
        return 1.0, f"{label}={count}; cap={CONTENT_LOSS_CAP}", CONTENT_LOSS_CAP
    return 0.0, f"{label}={count}", None


def _load_fields_and_docx(outputs_dir: Path) -> tuple[dict, DocumentType] | str:
    """Stage 4's fields and the rendered docx, or the "not evaluated" detail
    naming the first one that is absent or unreadable."""
    data, reason = _load_first(outputs_dir, "*_fields.json")
    if data is None:
        return f"{_missing_or_unreadable_detail('fields.json', reason)}; not evaluated"
    doc, reason = _load_docx(outputs_dir)
    if doc is None:
        return f"{reason}; not evaluated"
    return data, doc


def score_owner_missing_from_citation(outputs_dir: Path) -> tuple[float, str, int | None]:
    """Cap-only gate: the source credits the CV owner on a publication and its
    own rendered bibliography line does not name them, on
    OWNER_MISSING_CITATIONS_CAP_MIN or more citations (#822, #1259). The
    doctor's `owner_missing_from_citation` lint over stage 4 and the rendered
    docx, called as is."""
    loaded = _load_fields_and_docx(outputs_dir)
    if isinstance(loaded, str):
        return 0.0, loaded, None
    data, doc = loaded
    return _content_loss_count_gate(
        "owner_missing_citations",
        len(lint_owner_missing_from_citation(data, docx_body_blocks(doc))),
        OWNER_MISSING_CITATIONS_CAP_MIN)


def score_grant_boundary(outputs_dir: Path) -> tuple[float, str, int | None]:
    """Cap-only gate: a grant list stage 2 cut in the wrong place, so grants
    render with each other's details, on GRANT_BOUNDARY_CAP_MIN or more grants
    (#1226). The doctor's `grant_boundary` lint over stage 4, called as is."""
    data, reason = _load_first(outputs_dir, "*_fields.json")
    if data is None:
        return 0.0, f"{_missing_or_unreadable_detail('fields.json', reason)}; not evaluated", None
    return _content_loss_count_gate("grant_boundary_findings", len(lint_grant_boundary(data)),
                                    GRANT_BOUNDARY_CAP_MIN)


def score_grant_application_as_award(outputs_dir: Path) -> tuple[float, str, int | None]:
    """Cap-only gate: a grant the CV files under an applications heading,
    rendered as current or completed funding, on
    GRANT_APPLICATION_AS_AWARD_CAP_MIN or more grants (#1343). The doctor's
    `grant_bucket` lint over stage 4 and the rendered docx, with its end-date
    shape off (`check_end_date=False`): that shape reads today's date."""
    loaded = _load_fields_and_docx(outputs_dir)
    if isinstance(loaded, str):
        return 0.0, loaded, None
    data, doc = loaded
    return _content_loss_count_gate(
        "applications_rendered_as_awards",
        len(lint_grant_bucket(data, docx_body_blocks(doc), check_end_date=False)),
        GRANT_APPLICATION_AS_AWARD_CAP_MIN)


def score_group_header_context(outputs_dir: Path) -> tuple[float, str, int | None]:
    """Cap-only gate: lines a group header's context never reached (X6 E8),
    on GROUP_HEADER_CAP_MIN or more WARN findings. The doctor's
    `group_header_context` lint over stage 4 and the rendered docx's table
    rows and blocks, called as is; its INFO findings do not count."""
    loaded = _load_fields_and_docx(outputs_dir)
    if isinstance(loaded, str):
        return 0.0, loaded, None
    data, doc = loaded
    findings = lint_group_header_context(data, docx_table_rows(doc), docx_body_blocks(doc))
    return _content_loss_count_gate(
        "group_header_warns", sum(1 for f in findings if f["severity"] == "WARN"),
        GROUP_HEADER_CAP_MIN)


def score_junk_rows(outputs_dir: Path) -> tuple[float, str, int | None]:
    """Cap-only gate: stage-4 entries that are no record of their own (a group
    header, a lead-in label, a date fragment, a dateless repeat of a dated
    appointment) rendered as records, on JUNK_ROWS_CAP_MIN or more entries
    (EBYSBC E8/E10/E29). The doctor's `junk_or_header_row` lint over stage 4
    and the rendered docx's table rows and blocks, called as is."""
    loaded = _load_fields_and_docx(outputs_dir)
    if isinstance(loaded, str):
        return 0.0, loaded, None
    data, doc = loaded
    return _content_loss_count_gate(
        "junk_or_header_rows",
        len(lint_junk_or_header_row(data, docx_table_rows(doc), docx_body_blocks(doc))),
        JUNK_ROWS_CAP_MIN)


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
    if (is_template_instruction(text) or is_near_template_instruction(text)
            or is_foreign_template_instruction(text)):
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
                    or is_foreign_template_instruction(text)
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


#: A table at least this share empty counts as sparse.
SPARSE_TABLE_EMPTY_SHARE = 0.5


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

    template_cells = _template_cell_texts()
    total_cells = empty_cells = sparse_count = scored_tables = 0
    for tbl in tables:
        texts = [_normalize_whitespace(_cell_text(cell)) for row in tbl.rows for cell in row.cells]
        filled = [t for t in texts if t]
        # #452: a table holding nothing but the template's own cell text is
        # scaffolding for a section the source never had -- the blank
        # template scored the full 12-point penalty on 31 such tables. A
        # section emptied by a misroute is the doctor's `section_lost`.
        if all(t in template_cells for t in filled):
            continue
        scored_tables += 1
        total_cells += len(texts)
        empty_cells += len(texts) - len(filled)
        if len(texts) - len(filled) >= SPARSE_TABLE_EMPTY_SHARE * len(texts):
            sparse_count += 1

    if scored_tables == 0:
        return 0.0, f"total_tables={total_tables}; no table carries CV content", None
    sparse_table_ratio = sparse_count / scored_tables
    global_empty_ratio = empty_cells / total_cells if total_cells else 0.0
    a = 0.6 * (sparse_table_ratio / 0.25)
    b = 0.4 * ((global_empty_ratio - 0.10) / 0.40)
    fraction = clamp(a + b)

    detail = (
        f"total_tables={total_tables}; scored_tables={scored_tables}; sparse_tables={sparse_count}; "
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
                    text = _paragraph_text(p)
                    if "\t" in text and text != _TEMPLATE_TAB_CELL_TEXT:
                        count += 1
                if _depth < 1:
                    count += _count_raw_tab_cells(cell.tables, _depth + 1, _seen_tc)
    return count


#: The most the raw-tab half of `score_broken_format` may take, as a fraction
#: of the dimension: 0.3 of its 10 points is 3 points, reached at 10 raw tabs
#: (Paul's decision on #822, 2026-10-02). Raw tabs are cosmetic; uncapped, they
#: cost VVRTUC (batch EBYSBC) all 10 points while no run lost a point for a
#: lost record.
RAW_TAB_MAX_FRACTION = 0.3

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


def _paragraph_text(paragraph: Paragraph) -> str:
    """The accepted-changes text of one paragraph, tabs and line breaks kept
    as python-docx's ``.text`` renders them (#461). ``Paragraph.text`` skips
    runs inside ``<w:ins>`` -- where stage 6 writes enriched citations,
    institution locations and the research summary -- so it under-reports
    what the deliverable contains; this is the reader the doctor's lints use
    (`doctor.shared._docx_text`), not a second walker."""
    return _docx_text(paragraph._p, with_whitespace=True)


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
        _normalize_whitespace(_paragraph_text(p))
        for p in template_doc.paragraphs if _paragraph_text(p).strip()
    )


@functools.cache
def _template_cell_texts() -> frozenset[str]:
    """Whitespace-normalized text of every non-blank table cell of the
    template stage 6 renders into (#452): labels, column headers and
    placeholders like "DEA number: (optional)". Missing template raises, as
    `_template_body_paragraph_texts` does."""
    from docx import Document
    template_doc = Document(_TEMPLATE_DOCX_PATH)
    return frozenset(
        text for tbl in template_doc.tables for row in tbl.rows for cell in row.cells
        if (text := _normalize_whitespace(_cell_text(cell)))
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
        text = _paragraph_text(p)
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

    raw_tab_fraction = min(0.6 * (total_raw_tab / 20), RAW_TAB_MAX_FRACTION)
    fraction = clamp(raw_tab_fraction + 0.4 * (echo_count / 15))
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
    ("Pipeline/API errors present (HARD-FAIL gate)", 25, score_pipeline_errors),
    ("CV owner name / contact populated (HARD-FAIL gate)", 15, score_cv_owner),
    ("T-bucket share (stage_3b catch-all over-use)", 15, score_t_bucket),
    ("Sparse / under-filled tables in generated docx", 12, score_sparse_tables),
    ("Broken table / raw formatting artifacts in docx", 10, score_broken_format),
    ("Field-extraction sparseness (entry-level)", 13, score_field_sparseness),
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
#:
#: ORDER MATTERS for the caps that sit at the same value (84): the run
#: page's "why is this capped" pointer (`run_quality_report.cap_source`) names
#: the FIRST flag at the binding cap, and flags are written in this order. They
#: are listed most specific first: the lost-table gate measures the loss against
#: the delivered docx; the fused-entries gate counts records swallowed in the
#: extraction (7 of 9 flagged entries real); the under-extraction gate fires on
#: any finding, including ones that lost nothing; the owner-missing gate names
#: citations whose credit is gone but whose record is on the page; the grant-boundary,
#: grant-application, junk-row and group-header gates (#1226, #1343, E8, X6 E8)
#: name rows that render with wrong details, without their header's context,
#: or should not render at all; the stage-4 gate (#1174)
#: reports that a call failed and was retried and measures no loss. So a run
#: that trips several is pointed at the signal most likely to name what it
#: actually lost (batch IPXFBA: EKGTXD fires under-extraction and fused, and
#: PBSGQZ fires fused and stage-4; the verified loss in both is the fused entry).
CAP_ONLY_GATES = [
    ("Protected personal data absent from rendered docx (HARD-FAIL gate)", score_protected_data),
    ("Source table lost before extraction (CAP-ONLY gate)", score_lost_source_table),
    ("Source records fused: several entries swallowed multiple records (CAP-ONLY gate)",
     score_fused_entries),
    ("Source records lost: entry under-extracted (CAP-ONLY gate)", score_under_extracted_records),
    ("CV owner cut from their own citations (CAP-ONLY gate)", score_owner_missing_from_citation),
    ("Grant details shifted between grants (CAP-ONLY gate)", score_grant_boundary),
    ("Grant applications rendered as awards (CAP-ONLY gate)", score_grant_application_as_award),
    ("Headers or labels rendered as records (CAP-ONLY gate)", score_junk_rows),
    ("Rows lost the group header above them (CAP-ONLY gate)", score_group_header_context),
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
