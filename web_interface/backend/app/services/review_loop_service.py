"""The reviewer's corrected copy of the delivered document (#1587).

The corrected copy is a CV. It is stored with the run's other artifacts under
`CORRECTED_PREFIX`, which no download route and no scorer read reaches: those
read `outputs/` only (artifact_service.resolve_artifact,
quality_score_service). It is deleted with the run (storage.delete_run). What
is kept beside it is `doctor/docx_diff.py`'s typed report: block positions,
character counts and template section headings, no CV text.

Reading verdicts from what happened at each review-copy comment, and re-running
the doctor on the corrected copy, are #1654.
"""
import json
import logging
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session

from app.errors import bad_request, conflict
from app.services import (
    quality_score_service,  # noqa: F401  (puts src/ on sys.path for unified_pipeline)
)
from app.services.artifact_service import resolve_artifact
from app.services.upload_validation import _DOCX_READ_ERRORS
from app.storage import get_storage
from unified_pipeline.doctor.docx_diff import (  # noqa: E402  (path set by quality_score_service)
    diff_docx,
    to_report,
)

logger = logging.getLogger(__name__)

#: Where a reviewer's corrected copy and its diff live in the run's storage.
#: Never `outputs/`: downloads and the scorer read only that prefix.
CORRECTED_PREFIX = "corrected/"
#: The delivered document (orchestrator stage 6) and the stage-4 artifact
#: whose entries the diff maps changes to.
DELIVERED_DOCX_SUFFIX = "_wcm.docx"
STAGE4_FIELDS_SUFFIX = "_fields.json"


def corrected_docx_key(run_id: str) -> str:
    return f"{CORRECTED_PREFIX}{run_id}_corrected.docx"


def corrected_diff_key(run_id: str) -> str:
    return f"{CORRECTED_PREFIX}{run_id}_diff.json"


def _artifact_bytes(db: Session, run_id: str, name: str) -> bytes | None:
    """One of the run's artifacts, from the pod's disk or durable storage;
    None when neither has it."""
    resolved = resolve_artifact(db, run_id, name)
    if resolved.local_path is not None:
        return resolved.local_path.read_bytes()
    try:
        return get_storage().get_file(run_id, resolved.storage_key)
    except FileNotFoundError:
        return None


def _stage4_entries(db: Session, run_id: str) -> dict | None:
    """The run's stage-4 JSON, for entry indices; None when absent or
    unreadable (the diff then maps no entries, and says so per change)."""
    raw = _artifact_bytes(db, run_id, f"{run_id}{STAGE4_FIELDS_SUFFIX}")
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        logger.warning("Stage-4 JSON of run %s is unreadable; diffing without entry indices", run_id)
        return None
    return data if isinstance(data, dict) else None


def record_corrected_docx(db: Session, run_id: str, corrected: bytes) -> int:
    """Diff a reviewer's corrected copy against the delivered document, store
    both the copy and the typed diff under CORRECTED_PREFIX, and return how
    many changes the diff found. The caller has already checked access, size
    and file type. Nothing is stored when the diff cannot be made.

    Raises a 409 when the run has no delivered document, and a 400 when the
    corrected file cannot be read as a Word document.
    """
    delivered = _artifact_bytes(db, run_id, f"{run_id}{DELIVERED_DOCX_SUFFIX}")
    if delivered is None:
        raise conflict("This run has no finished document to compare with.")
    with tempfile.TemporaryDirectory(prefix=f"corrected_{run_id}_") as tmp:
        delivered_path, corrected_path = Path(tmp) / "delivered.docx", Path(tmp) / "corrected.docx"
        delivered_path.write_bytes(delivered)
        corrected_path.write_bytes(corrected)
        try:
            diff = diff_docx(delivered_path, corrected_path, _stage4_entries(db, run_id))
        # ValueError: python-docx's "not a Word file" (a zip whose main part
        # is some other content type), as scripts/docx_review_diff.py reads it.
        except (*_DOCX_READ_ERRORS, ValueError) as e:
            logger.warning("Corrected copy for run %s could not be diffed: %s", run_id, type(e).__name__)
            raise bad_request("We couldn't read this Word file. Re-save it in Word and try again.") from e
    storage = get_storage()
    storage.put_file(run_id, corrected_docx_key(run_id), corrected)
    report = json.dumps(to_report(run_id, diff), indent=1, sort_keys=True)
    storage.put_file(run_id, corrected_diff_key(run_id), report.encode("utf-8"))
    return len(diff.changes)


def changes_summary(changes: int) -> str:
    """The uploader's one-line confirmation."""
    return f"{changes} change{'' if changes == 1 else 's'} recorded"
