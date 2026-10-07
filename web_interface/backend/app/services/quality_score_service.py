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

from app.models import Run
from app.storage import get_storage
from app.storage.base import RunStorage

logger = logging.getLogger(__name__)

# Make the pipeline package importable (src/unified_pipeline). The orchestrator
# already does this at import time; repeat defensively so this service works
# regardless of import order.
_SRC = Path(__file__).resolve().parents[4] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.quality_score import SOURCE_DOCX_SUBDIR  # noqa: E402
from unified_pipeline.stage_errors import STAGE_ERRORS_SUFFIX  # noqa: E402

# Artifacts the scorer reads (see quality_score.py dimension scorers), plus the
# orchestrator's stage-error record (#745), mirrored to outputs/ like the rest.
_NEEDED_SUFFIXES = ("_classified.json", "_fields.json", "_entries.json", ".docx",
                    STAGE_ERRORS_SUFFIX)

# The original upload, archived under input/ (upload.py). The scorer reads it
# only for the lost-source-table gate (#822); a run whose original is not a
# single .docx (a PDF upload) simply skips that gate.
_SOURCE_PREFIX = "input/"
_SOURCE_SUFFIX = ".docx"

CACHE_KEY = "quality_score.json"

# The run doctor report's file suffix (orchestrator._doctor_report).
DOCTOR_SUFFIX = "_doctor.json"

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


def _stage_source_docx(storage: RunStorage, run_id: str, dest: Path) -> None:
    """Copy the run's original .docx to ``dest/SOURCE_DOCX_SUBDIR`` for the
    scorer's lost-source-table gate. Best-effort: with no single .docx under
    input/ (or on a storage error, logged) the gate is just not evaluated."""
    try:
        keys = [k for k in storage.list_files(run_id, _SOURCE_PREFIX)
                if k.lower().endswith(_SOURCE_SUFFIX)]
        if len(keys) != 1:
            return
        source_dir = dest / SOURCE_DOCX_SUBDIR
        source_dir.mkdir()
        (source_dir / Path(keys[0]).name).write_bytes(storage.get_file(run_id, keys[0]))
    except Exception:
        logger.warning("Could not stage the source docx for run %s", run_id, exc_info=True)


def compute_and_cache_score(run_id: str) -> dict | None:
    """Score a completed run from its persisted outputs and cache the result.

    Best-effort: returns None on any failure and never raises.
    """
    try:
        from unified_pipeline.quality_score import score_run

        storage = get_storage()
        keys = storage.list_files(run_id, "outputs/")
        wanted = [k for k in keys if k.endswith(_NEEDED_SUFFIXES)]
        if not wanted:
            logger.info("No scorable outputs in storage for run %s", run_id)
            return None

        tmp = Path(tempfile.mkdtemp(prefix=f"score_{run_id}_"))
        try:
            for key in wanted:
                (tmp / Path(key).name).write_bytes(storage.get_file(run_id, key))
            _stage_source_docx(storage, run_id, tmp)
            result = score_run(str(tmp), run_id)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        storage.put_file(run_id, CACHE_KEY, json.dumps(result).encode("utf-8"))
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
