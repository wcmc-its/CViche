"""The review loop's server half (#1587): verdicts on the doctor's findings,
and the reviewer's corrected copy of the delivered document.

Verdicts are asked per group (one lint and message shape), over exactly the
findings the run page's Fix list shows (`run_quality_report.verdict_groups`,
behind #1589's precision gate). A submitted verdict must name one of those
groups: the client only echoes back what the verdict-groups endpoint gave it,
so anything else is refused, and the group's count is taken from the server's
own grouping, never from the client.

The corrected copy is a CV. It is stored with the run's other artifacts under
`CORRECTED_PREFIX`, which no download route and no scorer read reaches: those
read `outputs/` only (artifact_service.resolve_artifact,
quality_score_service). It is deleted with the run (storage.delete_run). What
is kept beside it is `doctor/docx_diff.py`'s typed report: block positions,
character counts and template section headings, no CV text.
"""
import json
import logging
import tempfile
from collections.abc import Iterable
from pathlib import Path

from sqlalchemy.orm import Session

from app.errors import bad_request, conflict, validation_error
from app.models import FeedbackVerdict
from app.schemas import FeedbackVerdictSubmit, VerdictGroup
from app.services.artifact_service import resolve_artifact
from app.services.quality_score_service import get_doctor_report
from app.services.run_quality_report import verdict_groups
from app.services.upload_validation import _DOCX_READ_ERRORS
from app.storage import get_storage
from unified_pipeline.doctor.docx_diff import diff_docx, to_report  # noqa: E402  (path set by quality_score_service)

logger = logging.getLogger(__name__)

#: Where a reviewer's corrected copy and its diff live in the run's storage.
#: Never `outputs/`: downloads and the scorer read only that prefix.
CORRECTED_PREFIX = "corrected/"
#: The delivered document (orchestrator stage 6) and the stage-4 artifact
#: whose entries the diff maps changes to.
DELIVERED_DOCX_SUFFIX = "_wcm.docx"
STAGE4_FIELDS_SUFFIX = "_fields.json"

GroupKey = tuple[str, str | None]


def corrected_docx_key(run_id: str) -> str:
    return f"{CORRECTED_PREFIX}{run_id}_corrected.docx"


def corrected_diff_key(run_id: str) -> str:
    return f"{CORRECTED_PREFIX}{run_id}_diff.json"


# ------------------------------------------------------------------ verdicts

def shown_verdict_groups(run_id: str) -> list[VerdictGroup]:
    """The verdict groups the run's stored doctor report gives; empty when it
    has none (never checked, or storage is local-only)."""
    return verdict_groups(get_doctor_report(run_id))


def check_verdicts(verdicts: Iterable[FeedbackVerdictSubmit],
                   groups: Iterable[VerdictGroup]) -> dict[GroupKey, int]:
    """Each verdict's group and that group's finding count, keyed by
    (lint, shape). A 422 for a group the run does not show, or for one
    answered twice."""
    shown = {(g.lint, g.shape): g.count for g in groups}
    counts: dict[GroupKey, int] = {}
    for verdict in verdicts:
        key = (verdict.lint, verdict.shape)
        if key not in shown:
            raise validation_error("A verdict names a group of findings this run does not show.")
        if key in counts:
            raise validation_error("A group of findings was given two verdicts.")
        counts[key] = shown[key]
    return counts


def verdict_rows(feedback_id: int, run_id: str, verdicts: Iterable[FeedbackVerdictSubmit],
                 counts: dict[GroupKey, int]) -> list[FeedbackVerdict]:
    """The rows to store for checked verdicts (`check_verdicts`' counts)."""
    return [FeedbackVerdict(feedback_id=feedback_id, run_id=run_id, lint=v.lint, shape=v.shape,
                            finding_count=counts[(v.lint, v.shape)], verdict=v.verdict.value)
            for v in verdicts]


# ------------------------------------------------------------ corrected copy

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
            raise bad_request("We couldn't read this Word file. Re-save it in Word and try again.")
    storage = get_storage()
    storage.put_file(run_id, corrected_docx_key(run_id), corrected)
    report = json.dumps(to_report(run_id, diff), indent=1, sort_keys=True)
    storage.put_file(run_id, corrected_diff_key(run_id), report.encode("utf-8"))
    return len(diff.changes)


def changes_summary(changes: int) -> str:
    """The uploader's one-line confirmation."""
    return f"{changes} change{'' if changes == 1 else 's'} recorded"
