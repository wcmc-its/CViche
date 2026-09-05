#!/usr/bin/env python3
"""Stage flat S3 run outputs into the stage_* layout run_doctor expects, run the
doctor over a set of representative runs, and aggregate findings by lint so you
can rank defect classes by how many DISTINCT CVs each affects.

    PYTHONPATH=src python3 scripts/corpus_doctor_sweep.py <corpus_dir> \
        <run_id>[,<run_id>...] [--out sweep.json]

corpus_dir holds `<run_id>/outputs/<uid>_*.json` + `<uid>_wcm.docx` AND the
original upload at `<run_id>/input/<uid>.docx` (durably archived by the backend
since 2026-06-02; sync the whole run dir, not just outputs/). Pass ONE run per
distinct CV (pick reps with scripts/corpus_distinct_cvs.py first) so the
aggregate counts distinct CVs, not runs. The source docx is staged into the run
root so the segmentation/missed_headers lints run (they skip only for runs
predating the input-archiving feature).

Duplicate-uid policy: this script does not trust the caller to have actually
picked one run per distinct CV -- `sweep()` tracks uid -> run_id itself.
First run wins; a later run_id for a uid already reported is recorded under
`duplicates`, never added to `reports`, and never double-counted by
`aggregate()`.
"""
import argparse
import json
import logging
import shutil
import sys
import traceback
from collections import Counter
from pathlib import Path
from typing import TypedDict

from unified_pipeline.run_doctor import KNOWN_LINTS, SEVERITY_ORDER, _uid_owns, run_doctor

logger = logging.getLogger(__name__)

# S3-flat suffix -> the stage_* dir run_doctor._ARTIFACTS globs (kept in sync
# with that map; a suffix run_doctor stops reading just goes unused here).
SUFFIX_DIR = {
    "_segmented.json": "stage_1a_segmentation",
    "_entries.json": "stage_2_entry_extraction",
    "_classified.json": "stage_3b_classified_entries",
    "_fields.json": "stage_4_field_extraction",
    "_enriched.json": "stage_5_enrichment",
    "_wcm.docx": "stage_6_wcm_documents",
    "_render_warnings.json": "stage_6_wcm_documents",
}


# --- records this script hands to its callers and to --out (§8.1) ---------

class Finding(TypedDict):
    """One run_doctor finding, exactly as `doctor.shared._finding()` builds
    it -- the contract `aggregate()` validates every finding against
    (T2.10). Pinned to the real builder by
    `test_finding_contract_matches_doctor_shared_finding`, so the two cannot
    drift apart silently. `aggregate()` reads lint/severity/message;
    `evidence` is required because the builder always sets it, and a
    finding without it did not come from the builder."""
    lint: str
    severity: str
    message: str
    evidence: list[str]


# Declaration order, for messages that name the missing key.
FINDING_KEYS: tuple[str, ...] = tuple(Finding.__annotations__)


class DoctorReport(TypedDict):
    """The part of `run_doctor()`'s return this script reads. The real
    report also carries document_uid/root/artifacts/counts/worst_severity,
    which pass through to --out untouched."""
    findings: list[Finding]


class LintRow(TypedDict):
    """One row of `aggregate()`'s ranking: distinct-CV counts for a lint."""
    lint: str
    cvs_affected: int
    cvs_error: int
    cvs_ran: int


class RunFailure(TypedDict):
    """What `sweep()` records under `failures[run_id]` for a run it could not
    doctor. `exception` is the class name, so an operator (or a test) can
    tell a `PermissionError` from an `AmbiguousSourceDocxError` without
    parsing `traceback`."""
    exception: str
    error: str
    traceback: str


class SweepRunError(Exception):
    """Base for the operational per-run failures `sweep()` records and moves
    past (T1.6): a run whose layout, uid, or source docx is wrong is that
    run's problem, not the sweep's. Anything else the sweep's own staging
    code raises -- a `TypeError`, a `KeyError` -- is a bug in this script
    and propagates."""


class MultipleUidsInRunError(SweepRunError):
    """A run directory's artifacts name more than one distinct uid."""


def _find_uid(outputs_dir: Path):
    """The uid whose artifacts populate `outputs_dir`, or `None` if empty.

    Searched over every registered `SUFFIX_DIR` suffix (T1.4/T2.7), not just
    `_fields.json`/`_wcm.docx` -- a run that crashed before stage 4 legitimately
    has only an earlier-stage artifact (e.g. `_segmented.json`), and the old
    two-suffix search reported that run as having no uid at all. Raises if the
    directory's files name more than one uid: a malformed or partially synced
    run must never have `sweep()` silently pick one.
    """
    uids = {f.name[: -len(suffix)]
            for suffix in SUFFIX_DIR
            for f in outputs_dir.glob(f"*{suffix}")}
    if len(uids) > 1:
        raise MultipleUidsInRunError(
            f"{outputs_dir}: {len(uids)} distinct uids present, expected one: "
            f"{sorted(uids)}")
    return next(iter(uids), None)


def _resolve_outputs_dir(run_root: Path) -> Path:
    """The dir holding this run's per-uid artifacts, tolerating an
    already-flat layout.

    `<run_root>/outputs` when present, else `run_root` itself. Returns the
    flat path even when it doesn't exist so the caller's `_find_uid is None`
    branch reports the run uniformly (this never raises).
    """
    nested = run_root / "outputs"
    return nested if nested.is_dir() else run_root


def _relink(link: Path, src: Path) -> None:
    """Point `link` at `src`, unconditionally replacing any existing link.

    Both failure modes of a bare `if not link.exists(): symlink_to()` bite here:
    mode A -- a broken symlink reports exists()=False yet symlink_to still raises
    FileExistsError (the link path is still there); mode B (the bad one) -- a
    live link from an earlier sweep of a DIFFERENT run under the same uid gets
    silently reused, so run_doctor lints the old run's artifacts under the new
    run's name. `work` persists across invocations (`.doctor_stage`), so mode B
    fires whenever a representative run is re-picked. Unlink first, always.
    """
    link.unlink(missing_ok=True)
    link.symlink_to(src.resolve())


class AmbiguousSourceDocxError(SweepRunError):
    """More than one file in a candidate dir passes `_uid_owns` for a uid,
    and none is named exactly `<uid>.docx`."""


def _find_source_docx(cand_dir: Path, uid: str):
    """The uid's original-upload docx in `cand_dir`, or `None` if absent.

    Prefers the canonical `<uid>.docx` name; otherwise the sole candidate
    that passes `run_doctor._uid_owns` (the same prefix-boundary guard that
    closed the traced 2026-07-15 misattribution -- `web05` must not match
    `web050_...`). More than one such candidate is never resolved silently
    (T1.3/T2.7): raise so `sweep()` records the run as a failure naming
    every candidate, instead of picking `sorted(cands)[0]` and staging the
    wrong CV without any error.
    """
    exact = cand_dir / f"{uid}.docx"
    if exact.is_file():
        return exact
    cands = sorted(p for p in cand_dir.glob(f"{uid}*.docx")
                    if not p.name.endswith("_wcm.docx") and _uid_owns(p.name, uid))
    if len(cands) > 1:
        raise AmbiguousSourceDocxError(
            f"uid={uid!r} in {cand_dir}: {len(cands)} candidate source docx "
            f"files and none is named exactly {uid}.docx: "
            f"{[p.name for p in cands]}")
    return cands[0] if cands else None


class StagingRootNotOwnedError(SweepRunError):
    """`work/<uid>` contains an entry this script did not create as a symlink."""


def _clear_staged_root(root: Path) -> None:
    """Remove `root`, refusing if anything under it isn't a symlink or a plain
    directory this function's own layout created.

    `work` persists across invocations (`.doctor_stage`), so without this a
    stale symlink from an earlier sweep of a DIFFERENT run for the same uid
    can outlive the current run's stage() call and get linted as if it
    belonged to it (T1.1/T2.1). Everything `stage()` writes under `root` is
    either a directory it made or a symlink it created -- a plain file here
    means something else wrote into this tree, so refuse rather than delete
    it silently.
    """
    for entry in root.rglob("*"):
        if entry.is_symlink() or entry.is_dir():
            continue
        raise StagingRootNotOwnedError(
            f"{root}: refusing to remove {entry} -- not a symlink or a "
            "directory, not something this script staged")
    shutil.rmtree(root)


def stage(run_root: Path, uid: str, work: Path) -> Path:
    """Symlink the flat outputs into `<work>/<uid>/stage_*/` and return the root.

    `run_root` is `<corpus>/<run_id>` -- both the outputs dir (nested or
    flat, see `_resolve_outputs_dir`) and the archived-source `input/` dir
    are derived from it here, once, so a flat-layout run's source docx is
    looked up at `<run_root>/input`, not `<run_root>.parent/input`
    (T1.2/T2.7's ask).

    Rebuilt from scratch on every call (see `_clear_staged_root`) so a suffix
    absent from THIS run's outputs never leaves behind a symlink staged by an
    earlier run for the same uid.
    """
    outputs_dir = _resolve_outputs_dir(run_root)
    root = work / uid
    if root.exists():
        _clear_staged_root(root)
    root.mkdir(parents=True, exist_ok=True)
    for suffix, stagedir in SUFFIX_DIR.items():
        src = outputs_dir / f"{uid}{suffix}"
        if not src.exists():
            continue
        d = root / stagedir
        d.mkdir(parents=True, exist_ok=True)
        _relink(d / src.name, src)
    # Source docx: the original upload is durably archived at runs/<id>/input/
    # (since 2026-06-02, commit 8358c0b). Symlink it into root so _find_source
    # picks it up and the segmentation/missed_headers lints (1-2) can run.
    for cand_dir in (run_root / "input", outputs_dir):
        if not cand_dir.is_dir():
            continue
        src = _find_source_docx(cand_dir, uid)
        if src:
            _relink(root / src.name, src)
            break
    return root


class RunIdEscapesCorpusError(SweepRunError):
    """`run_id` resolves outside `corpus_dir` (e.g. contains `..`)."""


def _resolve_run_root(corpus_dir: Path, run_id: str) -> Path:
    """`corpus_dir / run_id`, refusing a `run_id` that would resolve outside
    `corpus_dir` (T1.5) -- e.g. `run_id="../other-directory"`.
    """
    root = corpus_dir / run_id
    resolved = root.resolve()
    try:
        resolved.relative_to(corpus_dir.resolve())
    except ValueError as e:
        raise RunIdEscapesCorpusError(
            f"run_id={run_id!r} resolves outside corpus_dir={corpus_dir}: "
            f"{resolved}") from e
    return root


def _record_failure(failures: dict[str, RunFailure], run_id: str, phase: str,
                    e: BaseException) -> None:
    """Log `e` with its traceback and file it under `failures[run_id]`.

    Called from inside an `except` block only: `logger.exception` and
    `traceback.format_exc()` both read the exception being handled.
    """
    logger.exception("%s failed for run_id=%s", phase, run_id)
    failures[run_id] = {"exception": type(e).__name__, "error": str(e),
                        "traceback": traceback.format_exc()}


def sweep(corpus_dir: Path, run_ids, work: Path):
    """Doctor each run; one bad run is reported and skipped, never aborts the rest.

    Returns (reports, failures, skipped, duplicates). `failures[run_id]`
    carries the exception class name, its message, and the full traceback
    text -- the operator's one lead into which lint raised, at which line,
    on which artifact -- and is also logged via logger.exception so it
    survives in the process log even when the caller does not persist
    --out. `skipped` lists run_ids with no staged artifacts at all (a
    distinct, non-exceptional case from a run_doctor crash).

    Two catches, deliberately different in width (T1.6). Around staging,
    only the failures that are THIS RUN's problem are recorded: the typed
    `SweepRunError`s this script raises for a bad layout, uid, or source
    docx, and `OSError` from the filesystem it symlinks through. A
    `TypeError` or `KeyError` there is a bug in the sweep itself and
    propagates so it is fixed, not filed as a run failure. Around
    `run_doctor()` the catch is broad on purpose: a lint crashing on one
    CV's artifacts is exactly what #563 is about, and it must not lose the
    reports already computed for the other runs.

    Duplicate-uid policy (T2.2): the caller contract (module docstring) is
    ONE run per distinct CV, but nothing previously enforced it -- two
    run_ids for the same uid would both land in `reports`, keyed separately,
    and `aggregate()` would double-count that CV's findings. First run wins:
    `duplicates[run_id] = {"uid": ..., "first_run_id": ...}` for every later
    run_id sharing an already-reported uid; it is never added to `reports`
    and never reaches `aggregate()`.
    """
    reports = {}
    failures: dict[str, RunFailure] = {}
    skipped = []
    duplicates = {}
    run_id_by_uid = {}
    for run_id in run_ids:
        try:
            run_root = _resolve_run_root(corpus_dir, run_id)
            uid = _find_uid(_resolve_outputs_dir(run_root))
            if not uid:
                logger.warning("%s: no artifacts found, skipping", run_id)
                skipped.append(run_id)
                continue
            if uid in run_id_by_uid:
                first_run_id = run_id_by_uid[uid]
                logger.warning("%s: uid=%s already reported by run_id=%s, "
                                "skipping duplicate", run_id, uid, first_run_id)
                duplicates[run_id] = {"uid": uid, "first_run_id": first_run_id}
                continue
            root = stage(run_root, uid, work)
        except (SweepRunError, OSError) as e:
            _record_failure(failures, run_id, "staging", e)
            continue
        try:
            reports[run_id] = run_doctor(root, uid)
        except Exception as e:  # noqa: BLE001 - one lint crash must not lose the sweep (#563)
            _record_failure(failures, run_id, "run_doctor", e)
            continue
        run_id_by_uid[uid] = run_id
    if failures:
        logger.warning("%d run(s) failed and were dropped from the sweep: %s. "
                        "Prevalence counts are over the %d that succeeded.",
                        len(failures), list(failures), len(reports))
    return reports, failures, skipped, duplicates


# The lints run_doctor always considers (skipped ones emit a "skipped: missing"
# INFO; a lint that runs clean emits nothing -- so ran = ALL - skipped, not the
# set of lints that happened to fire).
#
# Imported, not hardcoded (#268). The local copy this replaces had drifted, and
# the drift check that was supposed to catch it could not: it counted `lint_*`
# names off the module, which over-counts by one because `lint_surprise` is a
# ranking helper rather than a rule. That assert has been failing on dev ever
# since lint_surprise landed, and nothing runs --selftest in CI, so nobody saw
# it. Importing the canonical tuple removes both the copy and the heuristic.
ALL_LINTS = list(KNOWN_LINTS)


class MalformedReportError(Exception):
    """A report handed to aggregate() is not a dict carrying a `findings`
    list -- the shape `DoctorReport` names."""


class MalformedFindingError(MalformedReportError):
    """A report's finding is not a `Finding`: not a dict, missing a required
    key, or a field's value isn't in the registry it's supposed to come
    from."""


def _validate_finding(run_id: str, f: object) -> None:
    """Fail loudly (T1.8/T2.6/T2.10) on a finding aggregate() cannot trust:
    not a dict, a `Finding` key absent, an unregistered severity, or a lint
    name outside ALL_LINTS -- rather than either raising an opaque
    KeyError deeper in aggregate() or silently mis-ranking/dropping it.
    """
    if not isinstance(f, dict):
        raise MalformedFindingError(
            f"run_id={run_id!r}: finding is a {type(f).__name__}, not a "
            f"dict: {f!r}")
    for field in FINDING_KEYS:
        if field not in f:
            raise MalformedFindingError(
                f"run_id={run_id!r}: finding missing required field "
                f"{field!r}: {f}")
    if f["severity"] not in SEVERITY_ORDER:
        raise MalformedFindingError(
            f"run_id={run_id!r}: field=severity value={f['severity']!r} "
            f"not in SEVERITY_ORDER={SEVERITY_ORDER}")
    if f["lint"] not in ALL_LINTS:
        raise MalformedFindingError(
            f"run_id={run_id!r}: field=lint value={f['lint']!r} not in "
            f"ALL_LINTS")


def _validate_report(run_id: str, rep: object) -> list[Finding]:
    """The report's findings, validated (T2.10), or MalformedReportError.

    A report is whatever `run_doctor` returned for one run -- or, under a
    test's monkeypatch, whatever the fake returned -- so the boundary
    checks it is a dict with a `findings` list before the loop in
    `aggregate()` reads it, naming the run instead of raising a bare
    KeyError mid-aggregation.
    """
    if not isinstance(rep, dict):
        raise MalformedReportError(
            f"run_id={run_id!r}: report is a {type(rep).__name__}, not a dict")
    if "findings" not in rep:
        raise MalformedReportError(
            f"run_id={run_id!r}: report has no 'findings' key: "
            f"keys={sorted(rep)}")
    findings = rep["findings"]
    if not isinstance(findings, list):
        raise MalformedReportError(
            f"run_id={run_id!r}: 'findings' is a {type(findings).__name__}, "
            f"not a list")
    for f in findings:
        _validate_finding(run_id, f)
    return findings


def aggregate(reports: dict[str, DoctorReport]) -> list[LintRow]:
    """Per lint: how many distinct reps have a real (>=WARN) finding, and ERROR."""
    warn = Counter()
    error = Counter()
    ran = Counter()  # reps where the lint actually ran (input present, not skipped)
    for run_id, rep in reports.items():
        seen_warn, seen_err, skipped = set(), set(), set()
        for f in _validate_report(run_id, rep):
            lint, sev = f["lint"], f["severity"]
            if sev == "INFO" and "skipped: missing" in f["message"]:
                skipped.add(lint)
            elif sev == "WARN":
                seen_warn.add(lint)
            elif sev == "ERROR":
                seen_err.add(lint)
        for lint in seen_warn | seen_err:
            warn[lint] += 1
        for lint in seen_err:
            error[lint] += 1
        for lint in ALL_LINTS:
            if lint not in skipped:
                ran[lint] += 1
    # warn[] already counts every rep with a WARN-or-ERROR finding; error[] is a
    # subset. Rank by affected-CV count, ERROR count as tiebreak, then catalog order.
    lints = sorted(ALL_LINTS,
                   key=lambda lint: (-warn[lint], -error[lint], ALL_LINTS.index(lint)))
    return [
        {"lint": lint, "cvs_affected": warn[lint], "cvs_error": error[lint],
         "cvs_ran": ran[lint]}
        for lint in lints
    ]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("corpus_dir")
    ap.add_argument("run_ids", help="comma-separated representative run_ids")
    ap.add_argument("--work", default=None, help="staging dir (default: <corpus_dir>/.doctor_stage)")
    ap.add_argument("--out", default=None, help="write full JSON report here")
    ap.add_argument("--allow-partial", action="store_true",
                     help="exit 0 if at least one run produced a report, even "
                          "though others failed, were skipped, or were duplicates "
                          "(default: any incomplete run exits 1)")
    args = ap.parse_args(argv)

    corpus_dir = Path(args.corpus_dir)
    run_ids = [r.strip() for r in args.run_ids.split(",") if r.strip()]
    if not run_ids:
        ap.error("run_ids must contain at least one run id")

    # Validate --out's parent BEFORE the sweep: the write happens only after every
    # run_doctor call, so a typo'd dir would otherwise discard the whole sweep.
    if args.out and not Path(args.out).parent.is_dir():
        ap.error(f"--out directory does not exist: {Path(args.out).parent}")

    work = Path(args.work) if args.work else corpus_dir / ".doctor_stage"
    work.mkdir(parents=True, exist_ok=True)

    reports, failures, skipped, duplicates = sweep(corpus_dir, run_ids, work)
    ranking = aggregate(reports)
    n = len(reports)

    # stdout below is the human-readable ranking table -- the script's actual
    # product (see the module docstring) -- deliberately print(), not logger:
    # diagnostics (the warnings sweep() already logged) belong on stderr, this
    # is the report the operator reads. Do not migrate this block to logging.
    print(f"\nDoctor sweep: {n} distinct CVs\n")
    print(f"{'lint':24s} {'CVs affected':>13s} {'(of which ERROR)':>17s} {'ran on':>8s}")
    for row in ranking:
        print(f"{row['lint']:24s} {row['cvs_affected']:>10d}/{n:<2d} "
              f"{row['cvs_error']:>15d} {row['cvs_ran']:>8d}/{n}")

    if args.out:
        Path(args.out).write_text(json.dumps(
            {"n_cvs": n, "ranking": ranking, "reports": reports,
             "failures": failures, "skipped": skipped,
             "duplicates": duplicates}, indent=2, ensure_ascii=False),
            encoding="utf-8")
        print(f"\n-> {args.out}")

    # A sweep that doctored nothing is a failure, not a pass (§5.5) -- without
    # this, every run_id failing or being skipped still printed an all-zero
    # ranking table and exited 0.
    if not reports:
        return 1
    # Fail closed by default (T2.4): any requested run that failed, was
    # skipped, or was a duplicate means the sweep is incomplete, so CI
    # should not report success on it. --allow-partial opts back into the
    # old "at least one report" tolerance for a deliberately partial corpus.
    incomplete = bool(failures) or bool(skipped) or bool(duplicates)
    if incomplete and not args.allow_partial:
        return 1
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(levelname)s %(name)s: %(message)s")
    sys.exit(main())
