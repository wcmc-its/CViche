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
from pathlib import Path

from app.storage import get_storage

logger = logging.getLogger(__name__)

# Make the pipeline package importable (src/unified_pipeline). The orchestrator
# already does this at import time; repeat defensively so this service works
# regardless of import order.
_SRC = Path(__file__).resolve().parents[4] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage_errors import STAGE_ERRORS_SUFFIX  # noqa: E402

# Artifacts the scorer reads (see quality_score.py dimension scorers), plus the
# orchestrator's stage-error record (#745), mirrored to outputs/ like the rest.
_NEEDED_SUFFIXES = ("_classified.json", "_fields.json", "_entries.json", ".docx",
                    STAGE_ERRORS_SUFFIX)

CACHE_KEY = "quality_score.json"


def get_cached_score(run_id: str) -> dict | None:
    """Return the cached score dict for a run, or None if not computed/available."""
    try:
        return json.loads(get_storage().get_file(run_id, CACHE_KEY))
    except Exception:
        return None


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
            result = score_run(str(tmp), run_id)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        storage.put_file(run_id, CACHE_KEY, json.dumps(result).encode("utf-8"))
        return result
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("Quality score compute failed for run %s: %s", run_id, e)
        return None
