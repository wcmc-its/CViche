"""Per-run quality score: compute from persisted outputs, cache, and read.

Advisory only. Reuses the deterministic scorer in
``src/unified_pipeline/quality_score.py`` (no extra LLM calls) and works off the
canonical durable ``runs/{id}/outputs/`` artifacts, so it does not depend on the
pod-local stage-subdir layout. The result is cached at ``runs/{id}/quality_score.json``.

Note: like run-output downloads, this only finds artifacts when the storage
backend is S3 (the deployed mode). In pure-local mode outputs are not mirrored
to storage, so there is nothing to score from here.
"""

import json
import logging
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.config_loader import current_image_tag
from app.models import Run
from app.services.artifact_service import REVIEW_DOCX_SUFFIX
from app.storage import get_storage

logger = logging.getLogger(__name__)

# Make the pipeline package importable (src/unified_pipeline). The orchestrator
# already does this at import time; repeat defensively so this service works
# regardless of import order.
_SRC = Path(__file__).resolve().parents[4] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.quality_score import DOCTOR_REPORT_SUFFIX  # noqa: E402
from unified_pipeline.stage_errors import STAGE_ERRORS_SUFFIX  # noqa: E402

# The run doctor report's file suffix (orchestrator._doctor_report). The score
# is built from it (#1595), so it is one of the artifacts copied below.
DOCTOR_SUFFIX = DOCTOR_REPORT_SUFFIX

# Artifacts the scorer reads (see quality_score.py dimension scorers), plus the
# orchestrator's stage-error record (#745), mirrored to outputs/ like the rest.
_NEEDED_SUFFIXES = ("_classified.json", "_fields.json", "_entries.json", ".docx",
                    STAGE_ERRORS_SUFFIX, DOCTOR_SUFFIX)

CACHE_KEY = "quality_score.json"

# The cached score's record of the image whose scorer computed it (#1239): the
# executing image for the orchestrator's post-run score, the backend's for an
# admin rescore. Null when that image was built without a tag.
IMAGE_TAG_KEY = "image_tag"

# The cached score's record of the doctor's outcome when it was computed
# (#1593). Only a stored report makes a score checked (DOCTOR_RAN). With none
# beside it the run was never checked -- IXJMKS scored 97 GREEN that way -- so
# it is also marked data_complete false, and the status says why when the
# orchestrator knew: the doctor raised (DOCTOR_FAILED) or CVICHE_RUN_DOCTOR=0
# switched it off (DOCTOR_SKIPPED). DOCTOR_NOT_CHECKED is "no report, reason
# unknown": its pod died first, the report never reached storage, or an admin
# rescored a run that never had one.
DOCTOR_STATUS_KEY = "doctor_status"
DOCTOR_RAN = "ran"
DOCTOR_FAILED = "failed"
DOCTOR_SKIPPED = "skipped"
DOCTOR_NOT_CHECKED = "not_checked"
# What a score cached between #1601 and the ran/failed/skipped split called a
# run whose report was stored.
LEGACY_DOCTOR_CHECKED = "checked"

# Why a score is not checked, per status, in the words the Teams card uses.
DOCTOR_UNCHECKED_REASONS = {
    DOCTOR_FAILED: "the doctor failed",
    DOCTOR_SKIPPED: "the doctor is switched off",
    DOCTOR_NOT_CHECKED: "no doctor report",
}


def doctor_status(status: object) -> str | None:
    """A cached score's doctor status, the pre-split "checked" read as
    DOCTOR_RAN; None when absent (a cache from before #1601) or unknown."""
    if status == LEGACY_DOCTOR_CHECKED:
        return DOCTOR_RAN
    if status == DOCTOR_RAN or status in DOCTOR_UNCHECKED_REASONS:
        return str(status)
    return None


def unchecked_reason(status: object) -> str | None:
    """Why the doctor did not check a score with this status; None when it
    did, or when the cache does not say."""
    return DOCTOR_UNCHECKED_REASONS.get(doctor_status(status) or "")


def _outcome_status(has_report: bool, outcome: str | None) -> str:
    """The status to cache: DOCTOR_RAN only when a report is stored; without
    one, the orchestrator's reason when it gave one, else DOCTOR_NOT_CHECKED
    (a doctor that ran but whose report never reached storage included)."""
    if has_report:
        return DOCTOR_RAN
    if outcome is not None and outcome in DOCTOR_UNCHECKED_REASONS:
        return outcome
    return DOCTOR_NOT_CHECKED

BAND_GREEN = "GREEN"
BAND_YELLOW = "YELLOW"
BAND_RED = "RED"


def get_cached_score(run_id: str) -> dict | None:
    """Return the cached score dict for a run, or None if not computed/available."""
    try:
        return json.loads(get_storage().get_file(run_id, CACHE_KEY))
    except Exception:
        return None


def load_cached_score(run_id: str) -> object | None:
    """The cached score for a run, parsed; None when none was cached.

    Unlike get_cached_score this lets storage and parse errors propagate, so a
    caller that must tell "never scored" from "could not read" (the backfill)
    can.
    """
    try:
        return json.loads(get_storage().get_file(run_id, CACHE_KEY))
    except FileNotFoundError:
        return None


def compute_and_cache_score(run_id: str, doctor_outcome: str | None = None) -> dict | None:
    """Score a completed run from its persisted outputs and cache the result.

    The score is built from the doctor report (#1595) and records the
    doctor's status (DOCTOR_STATUS_KEY), so the orchestrator runs the doctor
    first and passes ``doctor_outcome`` (DOCTOR_RAN / DOCTOR_FAILED /
    DOCTOR_SKIPPED); None, as from an admin rescore, means not known. A run
    with no stored report is capped out of GREEN by the scorer itself.
    Best-effort: returns None on any failure and never raises.
    """
    try:
        from unified_pipeline.quality_score import score_run

        storage = get_storage()
        keys = storage.list_files(run_id, "outputs/")
        # The annotated copy (#1388) is a second .docx; the scorer would call the
        # pair ambiguous and skip every docx-reading dimension.
        wanted = [k for k in keys
                  if k.endswith(_NEEDED_SUFFIXES) and not k.endswith(REVIEW_DOCX_SUFFIX)]
        if not wanted:
            logger.info("No scorable outputs in storage for run %s", run_id)
            return None

        tmp = Path(tempfile.mkdtemp(prefix=f"score_{run_id}_"))
        try:
            for key in wanted:
                (tmp / Path(key).name).write_bytes(storage.get_file(run_id, key))
            result = score_run(str(tmp), run_id)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        status = _outcome_status(any(k.endswith(DOCTOR_SUFFIX) for k in keys), doctor_outcome)
        result[DOCTOR_STATUS_KEY] = status
        if status != DOCTOR_RAN:
            result["data_complete"] = False

        cached = {**result, IMAGE_TAG_KEY: current_image_tag()}
        storage.put_file(run_id, CACHE_KEY, json.dumps(cached).encode("utf-8"))
        return result
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("Quality score compute failed for run %s: %s", run_id, e)
        return None


# ---------------------------------------------------------------------------
# Typed view of the cached score, and the runs.quality_* columns derived from it
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DimensionPoints:
    """One weighted scorer dimension: points earned out of ``weight``."""
    name: str
    weight: int
    points: float


@dataclass(frozen=True)
class ScoreSnapshot:
    """The parts of a cached ``quality_score.json`` the app reads, parsed once
    so callers never ``.get()`` through the scorer's dict (CODING_STANDARDS §8.1).

    ``earned`` is the weighted total before any hard-fail cap;
    ``caps_triggered`` is every cap a failing gate carried, whether or not it
    lowered the score; ``flags`` are the scorer's human-readable flag lines.
    """
    total: int
    earned: float
    caps_triggered: tuple[int, ...]
    dimensions: tuple[DimensionPoints, ...]
    flags: tuple[str, ...]
    data_complete: bool | None
    doctor_status: str | None = None


@dataclass(frozen=True)
class ScoreColumns:
    """Values for ``runs.quality_score`` / ``quality_band`` / ``quality_cap``."""
    quality_score: int | None
    quality_band: str | None
    quality_cap: int | None


def _as_int(value: object) -> int | None:
    """``value`` as an int if it is a real number (bool excluded), else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _parse_dimensions(raw: object) -> tuple[DimensionPoints, ...]:
    """Weighted dimensions only: the zero-weight gates carry no points."""
    dims = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        weight = _as_int(entry.get("max"))
        points = entry.get("score")
        if (weight and isinstance(entry.get("name"), str)
                and isinstance(points, (int, float)) and not isinstance(points, bool)):
            dims.append(DimensionPoints(entry["name"], weight, float(points)))
    return tuple(dims)


def parse_score(raw: object) -> ScoreSnapshot | None:
    """Parse a cached score dict; None when it has no usable ``totalScore``."""
    if not isinstance(raw, dict):
        return None
    total = _as_int(raw.get("totalScore"))
    if total is None:
        return None
    earned = raw.get("raw_score_before_caps")
    caps = raw.get("hard_fail_caps_applied")
    flags = raw.get("flags")
    return ScoreSnapshot(
        total=total,
        earned=float(earned) if isinstance(earned, (int, float)) and not isinstance(earned, bool)
        else float(total),
        caps_triggered=tuple(c for c in (_as_int(v) for v in caps) if c is not None)
        if isinstance(caps, list) else (),
        dimensions=_parse_dimensions(raw.get("dimensionScores")),
        flags=tuple(f for f in flags if isinstance(f, str)) if isinstance(flags, list) else (),
        data_complete=raw.get("data_complete") if isinstance(raw.get("data_complete"), bool) else None,
        doctor_status=doctor_status(raw.get(DOCTOR_STATUS_KEY)),
    )


def band_key(total: int) -> str:
    """GREEN / YELLOW / RED for a final score, on the scorer's own thresholds."""
    from unified_pipeline.quality_score import BAND_GREEN as GREEN_MIN
    from unified_pipeline.quality_score import BAND_YELLOW as YELLOW_MIN

    if total >= GREEN_MIN:
        return BAND_GREEN
    if total >= YELLOW_MIN:
        return BAND_YELLOW
    return BAND_RED


def binding_cap(snapshot: ScoreSnapshot) -> int | None:
    """The cap that actually lowered the score: the lowest triggered cap, and
    only when it sits below the uncapped weighted total (a cap at or above the
    total changed nothing)."""
    if not snapshot.caps_triggered:
        return None
    lowest = min(snapshot.caps_triggered)
    return lowest if lowest < snapshot.earned else None


def score_columns(raw: object) -> ScoreColumns:
    """Map a cached score dict to the three ``runs`` columns (all None when the
    dict holds no usable score). Pure."""
    snapshot = parse_score(raw)
    if snapshot is None:
        return ScoreColumns(None, None, None)
    return ScoreColumns(snapshot.total, band_key(snapshot.total), binding_cap(snapshot))


def persist_score_columns(db: Session, run_id: str, raw: object) -> None:
    """Write the run's quality_* columns from a freshly computed score.

    Shared by the orchestrator (run end) and the admin rescore endpoint. Call it
    on the thread that owns ``db``. Best-effort: the columns are a denormalised
    copy of the cached score, so a failure logs a warning and rolls back
    without failing the run or the request.
    """
    cols = score_columns(raw)
    if cols.quality_score is None:
        return
    try:
        db.query(Run).filter(Run.id == run_id).update({
            Run.quality_score: cols.quality_score,
            Run.quality_band: cols.quality_band,
            Run.quality_cap: cols.quality_cap,
        })
        db.commit()
    except Exception:
        logger.warning("Could not persist quality score columns for run %s", run_id, exc_info=True)
        db.rollback()


def get_doctor_report(run_id: str) -> object | None:
    """The run doctor's ``<uid>_doctor.json`` as mirrored to durable storage
    under ``outputs/``, parsed; None when absent or unreadable.

    Like the score, it is only mirrored when the storage backend is S3.
    """
    try:
        storage = get_storage()
        keys = [k for k in storage.list_files(run_id, "outputs/") if k.endswith(DOCTOR_SUFFIX)]
        if not keys:
            return None
        return json.loads(storage.get_file(run_id, sorted(keys)[0]))
    except Exception:
        logger.warning("Could not read the doctor report for run %s", run_id, exc_info=True)
        return None
