"""Artifact resolution, ownership and JSON-preview generation for a run's
output files.

Extracted from app/api/steps.py (PR #780 review, r3965813607) so the HTTP
layer stays thin: every filesystem lookup for a run's outputs goes through
`resolve_artifact`, the single resolver, instead of each caller building its
own `output_dir / filename` path (r3965749610).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import Log, Step
from app.schemas import OutputPreview
from app.storage import get_storage

logger = logging.getLogger(__name__)

# Public (#701 run_queue.WorkToken validates a Valkey work token's run_id
# against this same pattern -- one definition of "what a run_id may look
# like", CODING STANDARDS section 1.5).
RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Preview bounds (#780 review r3965770586): a large pipeline artifact must not
# be expanded into an unbounded response. Parsing stops at the row cap -- it
# never materializes the whole document into `rows` first and slices after.
PREVIEW_MAX_ROWS = 200
PREVIEW_CELL_MAX_CHARS = 200

# web_interface/outputs/{run_id}/ -- the per-run directory, owned by
# construction (candidate 1). Both roots are computed once, from this
# module's own __file__: web_interface/backend/app/services/artifact_service.py
# is 4 parents from web_interface/ and 5 from the repo root, the same depth
# app/api/steps.py used to be at (services/ and api/ are siblings under app/).
_WEB_OUTPUTS_ROOT = (Path(__file__).parent.parent.parent.parent / "outputs").resolve()

# src/unified_pipeline/outputs/ -- shared across every run the pod has
# processed (candidate 2). Ownership here is NOT proven by containment alone;
# see resolve_artifact.
_PIPELINE_OUTPUTS_ROOT = (
    Path(__file__).parent.parent.parent.parent.parent / "src" / "unified_pipeline" / "outputs"
).resolve()


@dataclass(frozen=True)
class ResolvedArtifact:
    """Where a (run_id, filename) pair resolves to, if anywhere.

    local_path is None when the artifact was not found (or not owned) on this
    pod's local filesystem -- that is not itself an error; callers fall back
    to durable storage using storage_key, or 404 if that also misses.
    """
    local_path: Path | None
    storage_key: str
    basename: str


def validate_run_id(run_id: str) -> None:
    """Reject a run_id that could act as anything but a single path segment.

    Today the router already guarantees this -- `{run_id}` is a plain path
    param, so Starlette compiles it to `[^/]+` and a slash-bearing value 404s
    before the handler runs -- and check_run_access then requires an exact DB
    match. This pins that invariant at the point of use so a later
    `{run_id:path}` (or a lookup that stops being exact) cannot silently make
    traversal reachable.
    """
    if not RUN_ID_RE.match(run_id):
        logger.warning("[SECURITY] Blocked malformed run_id: %r", run_id)
        raise HTTPException(status_code=400, detail="Invalid run ID")


#: The finished document with the run doctor's findings as Word comments
#: (#1388), beside the stage-6 ``<uid>_wcm.docx``. Not ``_wcm.docx``-suffixed,
#: so nothing that looks for the clean document by suffix picks it up.
REVIEW_DOCX_SUFFIX = "_wcm_review.docx"


def is_staff_only_artifact(name: str) -> bool:
    """Stage JSON, and the review copy carrying the doctor's findings, which
    the run page shows to admins and staff only."""
    return is_json_artifact(name) or name.lower().endswith(REVIEW_DOCX_SUFFIX)


def is_json_artifact(name: str) -> bool:
    """Normalized JSON classification (#780 review r3965801468): one rule,
    used for both authorization (the admin gate) and file-type dispatch, so
    the two can never disagree on a name like `FOO.JSON`."""
    return Path(name).suffix.lower() == ".json"


def _owned_basenames_for_run(db: Session, run_id: str) -> set[str]:
    """Every basename this run's steps have ever recorded in output_files.

    This is the exact artifact-to-run relationship (#780 review r3965760129,
    r3965862896#2): the shared src/unified_pipeline/outputs/ tree is written
    by every run the pod has processed, so containment alone doesn't prove
    ownership, and a filename prefix (run `RUN123` matching `RUN1234...`)
    isn't exact. Step.output_files (models.py) is the only persisted
    per-run artifact record, so membership in it is the ownership check.
    """
    names: set[str] = set()
    steps = db.query(Step).filter(Step.run_id == run_id).all()
    for step in steps:
        if not step.output_files:
            continue
        try:
            files = json.loads(step.output_files)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning(
                "Malformed output_files for run=%s step=%s: %s", run_id, step.step_number, exc
            )
            continue
        if not isinstance(files, list):
            logger.warning(
                "Malformed output_files for run=%s step=%s: not a list", run_id, step.step_number
            )
            continue
        for f in files:
            if isinstance(f, str):
                names.add(Path(f).name)
    return names


def resolve_artifact(db: Session, run_id: str, filename: str) -> ResolvedArtifact:
    """The single resolver for every run-artifact filesystem lookup.

    Validates run_id and filename, then normalizes to `Path(filename).name`
    for BOTH local and storage lookups (#780 review r3965808294) -- the UI
    only ever links basenames from Step.output_files (see OutputFiles.tsx:
    `file.split('/').pop()` before building every /data and /json link, and
    `onOpenJson(filename)` passes that same basename), so a caller-supplied
    subpath component is never a legitimate artifact reference.

    Raises HTTPException(400) for malformed input (non-printable, absolute,
    traversal). A local miss is NOT an exception -- it is local_path=None, so
    callers can fall back to durable storage before deciding on a 404.
    """
    validate_run_id(run_id)

    if not filename.isprintable():
        logger.warning("[SECURITY] Blocked non-printable filename (run: %s)", run_id)
        raise HTTPException(status_code=400, detail="Invalid filename")

    if filename.startswith("/"):
        logger.warning(
            "[SECURITY] Blocked absolute path in file request: %s (run: %s)",
            filename, run_id,
        )
        raise HTTPException(status_code=400, detail="Invalid filename")

    if ".." in filename:
        logger.warning(
            "[SECURITY] Blocked path traversal attempt: %s (run: %s)",
            filename, run_id,
        )
        raise HTTPException(status_code=400, detail="Invalid filename")

    basename = Path(filename).name
    storage_key = f"outputs/{basename}"

    # Candidate 1: web_interface/outputs/{run_id}/ -- owned by construction.
    output_dir = (_WEB_OUTPUTS_ROOT / run_id).resolve()
    candidate = (output_dir / basename).resolve()
    if candidate.is_relative_to(output_dir) and candidate.exists():
        return ResolvedArtifact(local_path=candidate, storage_key=storage_key, basename=basename)

    # Candidate 2: shared src/unified_pipeline/outputs/ (search all stage
    # subdirectories) -- gated on exact ownership, never a prefix.
    if _PIPELINE_OUTPUTS_ROOT.is_dir() and basename in _owned_basenames_for_run(db, run_id):
        for stage_dir in _PIPELINE_OUTPUTS_ROOT.iterdir():
            if stage_dir.is_dir():
                candidate = (stage_dir / basename).resolve()
                if candidate.is_relative_to(_PIPELINE_OUTPUTS_ROOT) and candidate.exists():
                    return ResolvedArtifact(
                        local_path=candidate, storage_key=storage_key, basename=basename
                    )

    return ResolvedArtifact(local_path=None, storage_key=storage_key, basename=basename)


def get_step_with_logs(
    db: Session, run_id: str, step_number: int
) -> tuple[Step | None, list[Log]]:
    """Fetch a step and its ordered logs in one place (#780 review
    r3965813607 -- moves 2 of the 3 db.query( sites out of api/steps.py)."""
    step = (
        db.query(Step)
        .filter(Step.run_id == run_id, Step.step_number == step_number)
        .first()
    )
    if not step:
        return None, []
    logs = (
        db.query(Log)
        .filter(Log.run_id == run_id, Log.step_number == step_number)
        .order_by(Log.timestamp)
        .all()
    )
    return step, logs


def _capped_cell(value: object) -> str:
    return str(value)[:PREVIEW_CELL_MAX_CHARS]


def parse_json_to_preview(data: object) -> OutputPreview | None:
    """Parse JSON data into an OutputPreview, capped at PREVIEW_MAX_ROWS rows
    and PREVIEW_CELL_MAX_CHARS per cell (#780 review r3965770586).

    Raises whatever AttributeError/TypeError/KeyError a malformed shape
    produces -- callers (generate_preview_from_path) narrow-catch those, they
    are not swallowed here (#780 review r3965796995).
    """
    if isinstance(data, list):
        return _preview_from_list(data)
    if isinstance(data, dict):
        return _preview_from_dict(data)
    return None


def _preview_from_list(data: list) -> OutputPreview:
    if len(data) == 0:
        return OutputPreview(headers=[], rows=[], truncated=False, total_rows=0)

    headers = list(data[0].keys()) if isinstance(data[0], dict) else ["value"]
    total = len(data)
    rows = []
    for item in data[:PREVIEW_MAX_ROWS]:
        if isinstance(item, dict):
            rows.append([_capped_cell(item.get(h, "")) for h in headers])
        else:
            rows.append([_capped_cell(item)])

    return OutputPreview(
        headers=headers, rows=rows, truncated=total > PREVIEW_MAX_ROWS, total_rows=total
    )


_SECTION_KEYS = ["publications", "education", "positions", "grants"]


def _preview_from_dict(data: dict) -> OutputPreview | None:
    found_sections = [k for k in _SECTION_KEYS if k in data and isinstance(data[k], list)]
    if found_sections:
        return _preview_from_sections(data, found_sections)

    for value in data.values():
        if isinstance(value, list) and len(value) > 0:
            return _preview_from_single_list_value(value)

    # Fallback: key/value pairs.
    items = list(data.items())
    total = len(items)
    rows = [[_capped_cell(k), _capped_cell(v)] for k, v in items[:PREVIEW_MAX_ROWS]]
    return OutputPreview(
        headers=["Key", "Value"], rows=rows, truncated=total > PREVIEW_MAX_ROWS, total_rows=total
    )


def _preview_from_sections(data: dict, found_sections: list[str]) -> OutputPreview:
    """Comprehensive multi-section output -- combine every section into one
    table. Row materialization stops at PREVIEW_MAX_ROWS; the field-name scan
    below only collects header names (bounded by field cardinality, not row
    count), so it doesn't reintroduce the unbounded-memory shape."""
    all_fields: set[str] = set()
    total = 0
    for section_key in found_sections:
        for item in data[section_key]:
            total += 1
            if isinstance(item, dict):
                all_fields.update(item.keys())

    sorted_fields = sorted(all_fields)
    headers = ["Section", "Index"] + sorted_fields

    rows = []
    for section_key in found_sections:
        for idx, item in enumerate(data[section_key]):
            if len(rows) >= PREVIEW_MAX_ROWS:
                break
            if isinstance(item, dict):
                row = [section_key, str(idx + 1)]
                row.extend([_capped_cell(item.get(field, "")) for field in sorted_fields])
                rows.append(row)
        if len(rows) >= PREVIEW_MAX_ROWS:
            break

    return OutputPreview(
        headers=headers, rows=rows, truncated=total > PREVIEW_MAX_ROWS, total_rows=total
    )


def _preview_from_single_list_value(value: list) -> OutputPreview:
    total = len(value)
    if isinstance(value[0], dict):
        headers = list(value[0].keys())
        rows = [
            [_capped_cell(item.get(h, "")) for h in headers] for item in value[:PREVIEW_MAX_ROWS]
        ]
    else:
        headers = ["value"]
        rows = [[_capped_cell(item)] for item in value[:PREVIEW_MAX_ROWS]]
    return OutputPreview(
        headers=headers, rows=rows, truncated=total > PREVIEW_MAX_ROWS, total_rows=total
    )


def generate_preview_from_path(file_path: Path) -> OutputPreview | None:
    """Generate a preview of a JSON file at an already-resolved local path.

    Narrowed except (#780 review r3965796995): only malformed-input shapes
    (bad JSON, bad encoding, unexpected types while parsing) become a logged
    None. Anything else -- a genuine programming error -- propagates so it
    doesn't masquerade as a clean "no preview available" response.
    """
    if not file_path.exists() or not is_json_artifact(file_path.name):
        return None

    try:
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)
        return parse_json_to_preview(data)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, AttributeError, TypeError, KeyError) as exc:
        logger.warning("Error generating preview from %s: %s", file_path, exc)
        return None


def generate_preview(db: Session, run_id: str, filename: str) -> OutputPreview | None:
    """Generate a preview of a run-owned JSON artifact by filename, routed
    through the single resolver (#780 review r3965749610 -- this used to
    build `output_dir / filename` directly, bypassing resolve_artifact's
    traversal/ownership checks)."""
    resolved = resolve_artifact(db, run_id, filename)
    if not is_json_artifact(resolved.basename):
        return None
    if resolved.local_path is not None:
        return generate_preview_from_path(resolved.local_path)
    # Durable storage fallback, as the JSON viewer does: once the pod that ran
    # the step is recycled, the artifact exists only in S3. A missing object is
    # "no preview"; any other storage error propagates (#936 -- an outage must
    # not read as a missing file).
    try:
        raw = get_storage().get_file(run_id, resolved.storage_key)
    except FileNotFoundError:
        return None
    try:
        return parse_json_to_preview(json.loads(raw))
    except (json.JSONDecodeError, UnicodeDecodeError, AttributeError, TypeError, KeyError) as exc:
        logger.warning("Error generating preview from storage %s/%s: %s", run_id, resolved.storage_key, exc)
        return None
