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
owner name caps the final score regardless of the other dimensions.

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
    as ``missing_evidence`` and its emptiness as ``data_complete``; turning
    it into a typed scoring_confidence field on the API schema, the Teams
    card, and the frontend types is #745.

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


# ---------------------------------------------------------------------------
# Dimension scorers  -- each returns (penalty_fraction, detail, hard_fail_cap)
# ---------------------------------------------------------------------------

def score_pipeline_errors(outputs_dir: Path) -> tuple[float, str, int | None]:
    """Pipeline / API errors. Fatal patterns are a hard-fail (cap=40).

    An unreadable stage JSON is itself a pipeline-health signal (#724 review
    item 1) -- a truncated/corrupt artifact is exactly the shape a crashed or
    OOM-killed stage leaves behind -- so it is recorded as a synthetic
    non-fatal error entry rather than silently skipped. It is not fatal by
    itself; FATAL_ERROR_PATTERN still decides that from the parse-error text.
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


def score_t_bucket(outputs_dir: Path) -> tuple[float, str, None]:
    """Share of entries in the stage_3b ``T`` catch-all ('nothing else fits')."""
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
    t_count = code_dist.get("T", 0)
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
        f"T_count={t_count}; total={total}; t_ratio={t_ratio:.4f}; "
        f"t_validation_error={tv_error!r}; fraction={fraction:.3f}"
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

    Headers and footers are not scanned either way: stage 6 never writes to
    them.
    """
    doc, reason = _load_docx(outputs_dir)
    if doc is None:
        return 0.5, reason, None

    raw_tab_paragraphs = echo_count = 0
    for p in doc.paragraphs:
        text = p.text
        if "\t" in text:
            raw_tab_paragraphs += 1
        if INSTRUCTION_MARKERS.search(text):
            echo_count += 1

    raw_tab_cells = _count_raw_tab_cells(doc.tables)
    total_raw_tab = raw_tab_paragraphs + raw_tab_cells

    fraction = clamp(0.6 * (total_raw_tab / 20) + 0.4 * (echo_count / 15))
    detail = (
        f"raw_tab_paragraphs={raw_tab_paragraphs}; raw_tab_cells={raw_tab_cells}; "
        f"echo_paragraphs={echo_count}; fraction={fraction:.3f}"
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
    """
    data, reason = _load_first(outputs_dir, "*_fields.json")
    if data is None:
        return 1.0, _missing_or_unreadable_detail("fields.json", reason), None

    entries = data.get("entries", [])
    total = len(entries)
    if total == 0:
        return 1.0, "no entries", None

    allnull_or_zero = success_count = 0
    for e in entries:
        ef = e.get("extracted_fields", {}) or {}
        cov = e.get("extraction_coverage", {}) or {}
        cov_pct = cov.get("extraction_coverage_percent", None)
        if e.get("extraction_success", False):
            success_count += 1
        all_null = all(v is None for v in ef.values()) if ef else True
        zero_cov = (cov_pct is not None and cov_pct == 0)
        if all_null or zero_cov:
            allnull_or_zero += 1

    success_rate = success_count / total
    a = 0.5 * ((allnull_or_zero / total) / 0.10)
    b = 0.5 * ((1 - success_rate) / 0.10)
    fraction = clamp(a + b)
    detail = (
        f"total_entries={total}; allnull_or_zerocov={allnull_or_zero}; "
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
    if coverage_pct is not None and coverage_pct > 130:
        fraction = clamp(fraction + 0.1)

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
    if no_docx_produced(outputs_dir):
        return 1.0, f"no docx produced; hard-fail cap={NO_OUTPUT_CAP}", NO_OUTPUT_CAP
    return 0.0, "docx present (or absence is ambiguous/unreadable, scored elsewhere)", None


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
]
TOTAL_WEIGHT = sum(w for _, w, _ in DIMENSIONS)


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
