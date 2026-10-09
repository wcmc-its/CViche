"""The reviewer's corrected copy of the delivered document (#1587).

The corrected copy is a CV. It is stored with the run's other artifacts under
`CORRECTED_PREFIX`, which no download route and no scorer read reaches: those
read `outputs/` only (artifact_service.resolve_artifact,
quality_score_service). It is deleted with the run (storage.delete_run). What
is kept beside it is `doctor/docx_diff.py`'s typed report: block positions,
character counts and template section headings, no CV text.

Once the copy is stored, `review_corrected_copy` adds two more (#1654):

- the verdicts: what happened at each comment of the run's review copy, read
  from the corrected copy (`doctor/comment_fate.py`), one per finding with its
  lint, shape, severity and entry index (the review copy's comment map,
  review_comments.CommentFinding). No CV text.
- the doctor re-run on the corrected copy, over the run's own stored
  artifacts, and its findings counted against the delivered run's report.
"""
import io
import json
import logging
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from sqlalchemy.orm import Session

from app.errors import bad_request, conflict
from app.services import (
    quality_score_service,  # noqa: F401  (puts src/ on sys.path for unified_pipeline)
)
from app.services.artifact_service import REVIEW_DOCX_SUFFIX, resolve_artifact
from app.services.quality_score_service import DOCTOR_SUFFIX
from app.services.review_comments import (
    COMMENT_AUTHOR,
    REVIEW_NOTES_TITLE,
    CommentFinding,
    comment_map_path,
    lint_of_flag,
)
from app.services.upload_validation import _DOCX_READ_ERRORS
from app.storage import get_storage
from unified_pipeline.doctor.comment_fate import comment_fates  # noqa: E402
from unified_pipeline.doctor.docx_diff import (  # noqa: E402  (path set by quality_score_service)
    diff_docx,
    to_report,
)
from unified_pipeline.doctor.precision import (  # noqa: E402
    REVIEW_VERDICTS,
    finding_shape,
)
from unified_pipeline.doctor.shared import STATUS_RAN  # noqa: E402
from unified_pipeline.stage6.formatting import is_cviche_box  # noqa: E402
from unified_pipeline.stage_errors import (  # noqa: E402
    STAGE_ERRORS_SUFFIX,
    stage_errors_path,
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


def corrected_verdicts_key(run_id: str) -> str:
    return f"{CORRECTED_PREFIX}{run_id}_verdicts.json"


def corrected_doctor_key(run_id: str) -> str:
    return f"{CORRECTED_PREFIX}{run_id}_doctor.json"


def corrected_doctor_compare_key(run_id: str) -> str:
    return f"{CORRECTED_PREFIX}{run_id}_doctor_compare.json"


#: Where a verdict report's finding for each comment came from: the review
#: copy's comment map, or (a copy written before the map) the lint its
#: wording names, with no shape, severity or entry index.
FINDINGS_FROM_MAP = "comment_map"
#: run_doctor's artifact key for the stage-6 document, which the re-run reads
#: from the corrected copy instead.
DOCTOR_DOCX_ARTIFACT = "stage_6_docx"
FINDINGS_FROM_WORDING = "comment_wording"
#: Said in the verdict report when the comments' fate cannot be read.
NO_REVIEW_COPY_NOTE = "The run has no review copy, so there are no comments to follow."
NO_COMMENTS_NOTE = ("The uploaded copy holds no CViche comments: it is the clean document, or a "
                    "review copy with every comment deleted. A dismissed comment cannot be told "
                    "from one never there, so unchanged text is unknown, never not_a_problem.")


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


# --- after the upload (#1654): verdicts and the doctor re-run ------------------

@dataclass(frozen=True)
class FindingVerdict:
    """One review-copy comment's finding and what the reviewer did there."""
    comment_id: int
    lint: str | None
    shape: str | None
    severity: str | None
    entry_index: int | None
    verdict: str


def review_corrected_copy(db: Session, run_id: str, corrected: bytes) -> None:
    """Store the verdicts and the doctor's re-run for the run's corrected
    copy, once `record_corrected_docx` has stored it. Best-effort, each on its
    own: the copy and its diff are already stored, and a failure here is
    logged, never raised to the uploader."""
    for name, store in (("verdicts", store_verdicts), ("doctor re-run", store_corrected_doctor)):
        try:
            store(db, run_id, corrected)
        except Exception:  # noqa: BLE001 -- best-effort after a stored upload; logged with its traceback
            logger.exception("Corrected copy of run %s: %s failed", run_id, name)


def _comment_findings(db: Session, run_id: str, review: Path) -> tuple[dict[int, CommentFinding], str]:
    """The finding each review-copy comment marks, by comment id, and where
    that came from (FINDINGS_FROM_MAP or FINDINGS_FROM_WORDING)."""
    raw = _artifact_bytes(db, run_id, comment_map_path(Path(f"{run_id}{REVIEW_DOCX_SUFFIX}")).name)
    if raw is not None:
        rows = [CommentFinding(**row) for row in json.loads(raw)]
        return {row.comment_id: row for row in rows}, FINDINGS_FROM_MAP
    return ({c.comment_id: CommentFinding(c.comment_id, lint_of_flag(c.text), None, None, None)
             for c in Document(str(review)).comments},
            FINDINGS_FROM_WORDING)


def comment_verdicts(db: Session, run_id: str, corrected: bytes) -> dict:
    """The verdict report: one FindingVerdict per review-copy comment, their
    counts, and a note when the comments' fate cannot be read."""
    report: dict = {"uid": run_id, "comments_tracked": False, "findings_from": None, "note": None,
                    "counts": dict.fromkeys(REVIEW_VERDICTS, 0), "findings": []}
    review = _artifact_bytes(db, run_id, f"{run_id}{REVIEW_DOCX_SUFFIX}")
    if review is None:
        return {**report, "note": NO_REVIEW_COPY_NOTE}
    with tempfile.TemporaryDirectory(prefix=f"verdicts_{run_id}_") as tmp:
        review_path, corrected_path = Path(tmp) / "review.docx", Path(tmp) / "corrected.docx"
        review_path.write_bytes(review)
        corrected_path.write_bytes(corrected)
        fates = comment_fates(review_path, corrected_path, COMMENT_AUTHOR)
        findings, source = _comment_findings(db, run_id, review_path)
    verdicts = []
    for fate in fates.fates:
        finding = findings.get(fate.comment_id) or CommentFinding(fate.comment_id, None, None, None, None)
        verdicts.append(FindingVerdict(fate.comment_id, finding.lint, finding.shape, finding.severity,
                                       finding.entry_index, fate.verdict))
    counts = Counter(v.verdict for v in verdicts)
    return {**report, "comments_tracked": fates.comments_tracked, "findings_from": source,
            "note": None if fates.comments_tracked else NO_COMMENTS_NOTE,
            "counts": {verdict: counts[verdict] for verdict in REVIEW_VERDICTS},
            "findings": [asdict(v) for v in verdicts]}


def store_verdicts(db: Session, run_id: str, corrected: bytes) -> dict:
    """Compute the verdict report and store it under CORRECTED_PREFIX."""
    report = comment_verdicts(db, run_id, corrected)
    get_storage().put_file(run_id, corrected_verdicts_key(run_id),
                           json.dumps(report, indent=1, sort_keys=True).encode("utf-8"))
    return report


def _without_review_notes(docx: bytes) -> bytes:
    """The document without the review copy's closing review-notes box,
    which the delivered document never had; stage 6's own boxes stay, as
    the doctor read them on the delivered run."""
    doc = Document(io.BytesIO(docx))
    for tbl in list(doc.element.body.iterchildren(qn("w:tbl"))):
        table = Table(tbl, doc)
        if is_cviche_box(table) and table.cell(0, 0).paragraphs[0].text.startswith(REVIEW_NOTES_TITLE):
            tbl.getparent().remove(tbl)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def doctor_on_corrected(db: Session, run_id: str, corrected: bytes) -> dict:
    """The doctor's report on the corrected copy, read with the run's own
    stored artifacts in place of what stage 6 wrote. An artifact the run no
    longer has leaves its lints skipped, as on any run."""
    from unified_pipeline.run_doctor import artifact_layout, run_doctor

    with tempfile.TemporaryDirectory(prefix=f"corrected_doctor_{run_id}_") as tmp:
        root = Path(tmp)
        layout = artifact_layout(root, run_id)
        for key, path in layout.items():
            raw = (_without_review_notes(corrected) if key == DOCTOR_DOCX_ARTIFACT
                   else _artifact_bytes(db, run_id, path.name))
            if raw is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
        errors = _artifact_bytes(db, run_id, f"{run_id}{STAGE_ERRORS_SUFFIX}")
        if errors is not None:
            stage_errors_path(root, run_id).parent.mkdir(parents=True, exist_ok=True)
            stage_errors_path(root, run_id).write_bytes(errors)
        source = _source_docx(run_id, root)
        return run_doctor(root, run_id, source=source)


def _source_docx(run_id: str, root: Path) -> Path | None:
    """The run's uploaded CV when it is a docx, written under ``root``; None
    for a PDF upload (its converted docx is not stored) or none stored."""
    try:
        raw = get_storage().get_file(run_id, f"input/{run_id}.docx")
    except FileNotFoundError:
        return None
    path = root / "uploads" / f"{run_id}.docx"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


def compare_doctor(delivered: dict, corrected: dict) -> dict:
    """The delivered run's findings against the corrected copy's, counted per
    lint and shape: how many cleared, persist (the same message in both) and
    are new. A lint either report did not run (skipped or unreadable) is
    listed apart, never counted as cleared."""
    findings = [p.get("findings") if isinstance(p.get("findings"), list) else []
                for p in (delivered, corrected)]
    not_run = sorted({f["lint"] for fs in findings for f in fs if f.get("status", STATUS_RAN) != STATUS_RAN})
    by_message: list[Counter[tuple[str, str | None, str]]] = []
    for fs in findings:
        by_message.append(Counter(
            (f["lint"], finding_shape(f["lint"], str(f.get("message") or "")), str(f.get("message") or ""))
            for f in fs if f.get("status", STATUS_RAN) == STATUS_RAN and f["lint"] not in not_run))
    before, after = by_message
    rows: dict[tuple[str, str | None], dict[str, int]] = {}
    for (lint, shape, message) in before.keys() | after.keys():
        row = rows.setdefault((lint, shape), {"delivered": 0, "corrected": 0, "persisting": 0})
        row["delivered"] += before[(lint, shape, message)]
        row["corrected"] += after[(lint, shape, message)]
        row["persisting"] += min(before[(lint, shape, message)], after[(lint, shape, message)])
    lints = [{"lint": lint, "shape": shape, **row, "cleared": row["delivered"] - row["persisting"],
              "new": row["corrected"] - row["persisting"]}
             for (lint, shape), row in sorted(rows.items(), key=lambda kv: (kv[0][0], kv[0][1] or ""))]
    totals = {k: sum(r[k] for r in lints) for k in ("delivered", "corrected", "persisting", "cleared", "new")}
    return {"not_compared": not_run, "totals": totals, "lints": lints}


def store_corrected_doctor(db: Session, run_id: str, corrected: bytes) -> dict | None:
    """Run the doctor on the corrected copy; store its report, and its
    comparison with the delivered run's report when that is stored. Returns
    the comparison, None without a delivered report."""
    storage = get_storage()
    report = doctor_on_corrected(db, run_id, corrected)
    storage.put_file(run_id, corrected_doctor_key(run_id), json.dumps(report, indent=1).encode("utf-8"))
    delivered = _artifact_bytes(db, run_id, f"{run_id}{DOCTOR_SUFFIX}")
    if delivered is None:
        logger.info("Run %s has no doctor report to compare its corrected copy with", run_id)
        return None
    comparison = {"uid": run_id, **compare_doctor(json.loads(delivered), report)}
    storage.put_file(run_id, corrected_doctor_compare_key(run_id),
                     json.dumps(comparison, indent=1, sort_keys=True).encode("utf-8"))
    return comparison
