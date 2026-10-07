"""CSV exports behind GET /api/admin/export/{export_type} (#335).

Four tables -- runs, users, consent, feedback -- streamed as CSV in bounded
chunks (#128), every cell guarded against formula injection (#333). Admins may
export any of them; staff only _STAFF_EXPORT_TYPES. The route handler wraps
open_export's chunks in the download response.
"""
import csv
import io
import logging
from collections.abc import Callable, Iterable, Iterator
from datetime import datetime
from typing import NamedTuple

from sqlalchemy.orm import Query as SAQuery
from sqlalchemy.orm import Session, contains_eager

from app.errors import forbidden, validation_error
from app.models import Consent, Feedback, Run, User

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CSV formula-injection guard (OWASP): a cell whose text starts with a formula
# trigger is interpreted as a formula by Excel/Sheets/LibreOffice. Prefix such
# cells with a single quote so they render as literal text.
# ---------------------------------------------------------------------------
_CSV_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def _sanitize_csv_cell(value: object) -> object:
    if isinstance(value, str) and value and value[0] in _CSV_FORMULA_TRIGGERS:
        return "'" + value
    return value


class _SafeCsvWriter:
    """csv.writer wrapper that neutralizes formula injection in every cell."""

    def __init__(self, f: io.StringIO) -> None:
        self._writer = csv.writer(f)

    def writerow(self, row: Iterable[object]) -> None:
        self._writer.writerow([_sanitize_csv_cell(c) for c in row])


# ---------------------------------------------------------------------------
# Streams: rows are fetched in batches of _CSV_CHUNK_ROWS (Query.yield_per, which
# also turns on a server-side cursor) and each batch is emitted as one chunk, so
# memory stays bounded by the batch size rather than by the table size (#128).
# ---------------------------------------------------------------------------
_CSV_CHUNK_ROWS = 500


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value else ""


class _CsvExport(NamedTuple):
    header: tuple[str, ...]
    query: Callable[[Session], SAQuery]
    row: Callable[..., list[object]]


_RUN_HEADER = (
    "run_id", "user_email", "filename", "file_type", "status",
    "started_at", "completed_at", "total_cost", "total_tokens",
    "input_tokens", "output_tokens", "submission_type", "error_message",
)
_USER_HEADER = (
    "id", "email", "display_name", "role", "status",
    "daily_limit", "monthly_limit", "consent_version",
    "consent_date", "created_at", "last_active_at",
)
_CONSENT_HEADER = (
    "id", "user_id", "user_email", "consent_version",
    "consent_text_hash", "ip_address", "user_agent", "timestamp",
)
_FEEDBACK_HEADER = (
    "id", "run_id", "user_email", "reviewer_role",
    "overall_accuracy", "overall_completeness", "overall_usefulness",
    "manual_conversion_effort", "correction_effort",
    "enrichment_quality", "summary_generated", "summary_quality",
    "issue_missing_content", "issue_split_merged", "issue_wrong_section",
    "issue_inaccurate", "issue_ai_enrichment", "issue_formatting",
    "issue_locations", "biggest_issue", "likelihood_to_recommend",
    "submitted_at",
)


def _run_row(run: Run) -> list[object]:
    return [
        run.id,
        run.user.email if run.user else "",
        run.filename,
        run.file_type,
        run.status,
        _iso(run.started_at),
        _iso(run.completed_at),
        run.total_cost,
        run.total_tokens,
        run.input_tokens,
        run.output_tokens,
        run.submission_type or "",
        run.error_message or "",
    ]


def _user_row(u: User) -> list[object]:
    return [
        u.id,
        u.email,
        u.display_name,
        u.role,
        u.status,
        u.daily_limit or "",
        u.monthly_limit or "",
        u.consent_version or "",
        _iso(u.consent_date),
        _iso(u.created_at),
        _iso(u.last_active_at),
    ]


def _consent_row(consent: Consent) -> list[object]:
    return [
        consent.id,
        consent.user_id,
        consent.user.email if consent.user else "",
        consent.consent_version,
        consent.consent_text_hash,
        consent.ip_address or "",
        consent.user_agent or "",
        _iso(consent.timestamp),
    ]


def _feedback_row(fb: Feedback) -> list[object]:
    return [
        fb.id,
        fb.run_id,
        fb.user.email if fb.user else "",
        fb.reviewer_role,
        fb.overall_accuracy,
        fb.overall_completeness,
        fb.overall_usefulness,
        fb.manual_conversion_effort,
        fb.correction_effort,
        fb.enrichment_quality,
        fb.summary_generated,
        fb.summary_quality,
        fb.issue_missing_content or "",
        fb.issue_split_merged or "",
        fb.issue_wrong_section or "",
        fb.issue_inaccurate or "",
        fb.issue_ai_enrichment or "",
        fb.issue_formatting or "",
        fb.issue_locations or "",
        fb.biggest_issue or "",
        fb.likelihood_to_recommend,
        _iso(fb.submitted_at),
    ]


_CSV_EXPORTS = {
    "runs": _CsvExport(
        _RUN_HEADER,
        lambda db: (
            db.query(Run).outerjoin(Run.user).options(contains_eager(Run.user))
            .order_by(Run.started_at.desc())
        ),
        _run_row,
    ),
    "users": _CsvExport(
        _USER_HEADER,
        lambda db: db.query(User).order_by(User.created_at.desc()),
        _user_row,
    ),
    "consent": _CsvExport(
        _CONSENT_HEADER,
        lambda db: (
            db.query(Consent).outerjoin(Consent.user)
            .options(contains_eager(Consent.user))
            .order_by(Consent.timestamp.desc())
        ),
        _consent_row,
    ),
    "feedback": _CsvExport(
        _FEEDBACK_HEADER,
        lambda db: (
            db.query(Feedback).outerjoin(Feedback.user)
            .options(contains_eager(Feedback.user))
            .order_by(Feedback.submitted_at.desc())
        ),
        _feedback_row,
    ),
}


def _drain(buffer: io.StringIO) -> str:
    """Return everything written to `buffer` so far and empty it."""
    text = buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)
    return text


def _iter_csv_chunks(export_type: str, db: Session) -> Iterator[str]:
    """Yield the CSV for `export_type` in chunks of _CSV_CHUNK_ROWS rows.

    The header rides in the first chunk. Bytes are identical to writing the
    whole table to one buffer: same rows, same order, same csv dialect.
    """
    spec = _CSV_EXPORTS[export_type]
    buffer = io.StringIO()
    writer = _SafeCsvWriter(buffer)
    writer.writerow(spec.header)
    for count, record in enumerate(spec.query(db).yield_per(_CSV_CHUNK_ROWS), 1):
        writer.writerow(spec.row(record))
        if count % _CSV_CHUNK_ROWS == 0:
            yield _drain(buffer)
    tail = _drain(buffer)
    if tail:
        yield tail


# Exports staff (read-only, UserRole.STAFF) may download: feedback backs the
# Feedback Insights tab and carries no cost. runs carries total_cost (admin-only,
# #1111); users and consent are user-management data.
_STAFF_EXPORT_TYPES = frozenset({"feedback"})


def open_export(db: Session, viewer: User, export_type: str) -> Iterator[str]:
    """Check ``viewer`` may download ``export_type``, audit-log the export, and
    return its CSV chunks -- lazily: no row is read until the response iterates.

    An unknown type is a 422, checked before the role; a type staff may not
    export is a 403.
    """
    if export_type not in _CSV_EXPORTS:
        raise validation_error(f"Invalid export type: {export_type}. Must be one of: runs, users, consent, feedback.")
    if viewer.role != "admin" and export_type not in _STAFF_EXPORT_TYPES:
        raise forbidden("Admin access required for this export.")

    logger.info(
        "admin_export: user=%s role=%s export_type=%s", viewer.email, viewer.role, export_type
    )
    return _iter_csv_chunks(export_type, db)
