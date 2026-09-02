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

import json
import logging
import re
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# --- band thresholds (provisional; see module docstring) --------------------
BAND_GREEN = 85
BAND_YELLOW = 60


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def clamp(val, lo=0.0, hi=1.0):
    return max(lo, min(hi, val))


def linear_interp(val, lo, hi, out_lo, out_hi):
    """Map val from [lo, hi] -> [out_lo, out_hi] linearly, clamped to the
    output range regardless of direction (out_lo may be > out_hi) -- a caller
    passing a val outside [lo, hi] gets a bounded result, not an extrapolated
    one (#724 review)."""
    if hi == lo:
        return out_lo
    t = (val - lo) / (hi - lo)
    result = out_lo + t * (out_hi - out_lo)
    return clamp(result, min(out_lo, out_hi), max(out_lo, out_hi))


def _load_first(outputs_dir: Path, pattern: str):
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


def _missing_or_unreadable_detail(label: str, reason) -> str:
    """Detail-string fragment for a `_load_first` miss: names an unreadable
    artifact distinctly from a genuinely absent one (#497)."""
    if reason is not None:
        return f"{label} unreadable ({reason})"
    return f"no {label} found"


def _load_docx(outputs_dir: Path):
    """Return (Document, reason). Document is None if unavailable."""
    try:
        from docx import Document
    except ImportError:
        return None, "python-docx not available"
    docx_files = sorted(outputs_dir.glob("*.docx"))
    if not docx_files:
        return None, "no docx found"
    try:
        return Document(docx_files[0]), None
    except Exception as e:  # pragma: no cover - corrupt docx
        return None, f"docx open error: {e}"


# ---------------------------------------------------------------------------
# Hard-fail predicates
#
# The two gates that cap the final score live here as standalone predicates so
# run_doctor's owner_contact_missing / pipeline_errors_present lints report on
# exactly the conditions the scorer caps for, instead of a second definition
# that drifts (#437). The scorers below are their only other caller.
# ---------------------------------------------------------------------------

#: Error text that means a stage broke, not that one lookup came back empty.
FATAL_ERROR_PATTERN = re.compile(
    r"name '\w+' is not defined"
    r"|Traceback \(most recent call last\)"
    r"|NameError:|UnboundLocalError:|KeyError:",
    re.IGNORECASE,
)


def iter_error_fields(obj, path=""):
    """(dotted path, value) for every non-null ``error`` field anywhere in obj."""
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


def _invalid_metadata_result(context: str, invariant: str, **values) -> tuple:
    """Shared (fraction, detail, cap) for a stage-metadata invariant
    violation (#724 review items 10/11): worst-case fraction rather than
    computing a ratio from numbers that cannot be trusted (e.g. negative
    counts, or duplicate_entries exceeding total_entries). Farm: 0 of 66
    classified.json files violate either invariant, so this never fires on
    real output today.
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


# ---------------------------------------------------------------------------
# Dimension scorers  -- each returns (penalty_fraction, detail, hard_fail_cap)
# ---------------------------------------------------------------------------

def score_pipeline_errors(outputs_dir: Path):
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


def score_cv_owner(outputs_dir: Path):
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


def score_t_bucket(outputs_dir: Path):
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


def score_sparse_tables(outputs_dir: Path):
    """Sparse / under-filled tables in the generated docx."""
    doc, reason = _load_docx(outputs_dir)
    if doc is None:
        return 0.5, reason, None

    tables = doc.tables
    total_tables = len(tables)
    if total_tables == 0:
        return 0.0, "no tables in docx", None

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


def score_broken_format(outputs_dir: Path):
    """Raw-tab and prompt-echo (template instruction) artifacts in the docx."""
    doc, reason = _load_docx(outputs_dir)
    if doc is None:
        return 0.5, reason, None

    INSTRUCTION_MARKERS = re.compile(
        r"(please|delete the others|list here|choose one|bedside|e\.g\.,|yyyy-yyyy|\(optional\)|\(Research, clinical)",
        re.IGNORECASE,
    )
    raw_tab_count = echo_count = 0
    for p in doc.paragraphs:
        text = p.text
        if "\t" in text:
            raw_tab_count += 1
        if INSTRUCTION_MARKERS.search(text):
            echo_count += 1

    fraction = clamp(0.6 * (raw_tab_count / 20) + 0.4 * (echo_count / 15))
    detail = f"raw_tab_paragraphs={raw_tab_count}; echo_paragraphs={echo_count}; fraction={fraction:.3f}"
    return fraction, detail, None


def score_field_sparseness(outputs_dir: Path):
    """Entry-level field-extraction sparseness."""
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


def score_duplicate_ratio(outputs_dir: Path):
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
]
TOTAL_WEIGHT = sum(w for _, w, _ in DIMENSIONS)


def band_for(score: int) -> str:
    if score >= BAND_GREEN:
        return "GREEN (ship)"
    if score >= BAND_YELLOW:
        return "YELLOW (human cleanup needed)"
    return "RED (re-run / do-not-deliver)"


def score_run(run_output_dir, run_id: str = None) -> dict:
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

    return {
        "run_id": run_id,
        "totalScore": total_score,
        "band": band_for(total_score),
        "raw_score_before_caps": round(raw, 2),
        "hard_fail_caps_applied": hard_fail_caps,
        "total_weight": TOTAL_WEIGHT,
        "dimensionScores": dimension_scores,
        "flags": flags,
    }


#: The only modes quality_gate accepts. A typo (e.g. "blok") must fail
#: closed, not silently fall through to advisory (#724 review item 3) --
#: this is the single caller's only gate mode value, currently hardcoded to
#: "block" (quality_score.py:_main), never config- or env-driven.
VALID_GATE_MODES = frozenset({"off", "advisory", "block"})


def quality_gate(run_output_dir, run_id: str = None, mode: str = "advisory") -> dict:
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
