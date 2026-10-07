"""The CV owner's name, read from a run's stage-4 ``*_fields.json``.

Stage 4 infers who the CV belongs to and writes it as ``cv_owner`` in its
fields JSON. The run view reads that object live (PipelineViewer.tsx
loadCvInsights); the admin runs list needs it as a filterable column, so it is
persisted on ``runs.cv_owner_name`` when stage 4 completes and by
scripts/backfill_cv_owner_name.py for earlier runs. Both callers share the
functions here so the live path and the backfill can never disagree on which
file or which name field wins.
"""
import json
import logging

from sqlalchemy.orm import Session

from app.models import Run
from app.services import artifact_service
from app.storage import get_storage

logger = logging.getLogger(__name__)

# Stage 4's fields file is the one output whose name contains this marker
# (same rule as loadCvInsights).
FIELDS_JSON_MARKER = "_fields.json"

# The pipeline stage whose output carries ``cv_owner``.
CV_OWNER_STAGE_ID = "4"

CV_OWNER_NAME_MAX_LENGTH = Run.cv_owner_name.type.length


def find_fields_json_name(output_files_json: str | None) -> str | None:
    """The basename of the ``*_fields.json`` in a step's ``output_files`` column
    (a JSON array of paths), or None when absent or unparseable."""
    if not output_files_json:
        return None
    try:
        paths = json.loads(output_files_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(paths, list):
        return None
    for path in paths:
        if isinstance(path, str) and FIELDS_JSON_MARKER in path:
            return path.rsplit("/", 1)[-1]
    return None


def extract_cv_owner_name(fields: object) -> str | None:
    """``cv_owner.full_name``, falling back to ``"first_name last_name"``.

    Stripped; None when the file has no usable owner. Pure: takes the parsed
    fields JSON, does no I/O.
    """
    if not isinstance(fields, dict):
        return None
    owner = fields.get("cv_owner")
    if not isinstance(owner, dict):
        return None
    full_name = owner.get("full_name")
    name = full_name.strip() if isinstance(full_name, str) else ""
    if not name:
        first = owner.get("first_name")
        last = owner.get("last_name")
        parts = [p.strip() for p in (first, last) if isinstance(p, str) and p.strip()]
        name = " ".join(parts)
    return name[:CV_OWNER_NAME_MAX_LENGTH] or None


def load_fields_json(db: Session, run_id: str, filename: str) -> object | None:
    """Parse a run's fields JSON through the same resolver the data-json
    endpoint uses: the pod's local filesystem first, then durable storage
    (S3 in prod). None when the file is in neither place."""
    resolved = artifact_service.resolve_artifact(db, run_id, filename)
    if resolved.local_path is not None:
        with open(resolved.local_path, encoding="utf-8") as f:
            return json.load(f)
    try:
        return json.loads(get_storage().get_file(run_id, resolved.storage_key))
    except FileNotFoundError:
        return None


def read_cv_owner_name(db: Session, run_id: str, output_files_json: str | None) -> str | None:
    """The run's CV owner name from its stage-4 output, or None if unavailable."""
    filename = find_fields_json_name(output_files_json)
    if filename is None:
        return None
    return extract_cv_owner_name(load_fields_json(db, run_id, filename))
