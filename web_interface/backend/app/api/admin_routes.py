"""Admin dashboard API endpoints. All endpoints require admin role."""
import csv
import io
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session, contains_eager

from app.database import get_db
from app.models import User, Run, Feedback, SystemConfig, Consent
from app.auth import require_admin, get_session_epoch
from app.errors import not_found, validation_error
from app.schemas import (
    AdminStats,
    AdminUser,
    AdminRunEntry,
    AdminRunsResponse,
    AdminConfigResponse,
    AdminConfigUpdate,
    AdminUserUpdate,
    QualityScoreResult,
)
from app.services.admin_service import get_users_with_stats, get_single_user_stats
from app.services.quality_score_service import get_cached_score, compute_and_cache_score
from app.services.run_service import reap_orphaned_created_runs
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /api/admin/stats
# ---------------------------------------------------------------------------
@router.get("/admin/stats", response_model=AdminStats)
async def get_stats(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Return overview statistics for the admin dashboard."""
    total_runs = db.query(func.count(Run.id)).scalar() or 0

    thirty_days_ago = datetime.now() - timedelta(days=30)
    active_users = (
        db.query(func.count(func.distinct(Run.user_id)))
        .filter(Run.started_at >= thirty_days_ago, Run.user_id.isnot(None))
        .scalar()
        or 0
    )

    total_cost = db.query(func.sum(Run.total_cost)).scalar() or 0.0

    completed_runs = (
        db.query(func.count(Run.id)).filter(Run.status == "complete").scalar() or 0
    )
    runs_with_feedback = (
        db.query(func.count(func.distinct(Feedback.run_id))).scalar() or 0
    )
    feedback_rate = (
        (runs_with_feedback / completed_runs * 100) if completed_runs > 0 else 0.0
    )

    # CV-to-WCM conversion time, aggregated server-side over completed runs and
    # returned on this existing stats call (the dashboard already makes it), so the
    # admin overview gets avg/p95 without a second round-trip. The aggregate has to
    # be computed here rather than on the client because /admin/runs is paginated --
    # the browser never holds the whole population. Prefer the persisted pipeline
    # duration; fall back to wall-clock for runs that predate the column.
    #
    # Select only the three duration columns rather than hydrating a full Run ORM
    # object per completed run (#128) -- at scale that was the dominant cost here.
    # avg/p95 stay in Python: the wall-clock fallback needs a per-dialect timestamp
    # diff the SQLite test suite can't exercise, and the nearest-rank p95 is already
    # portable and correct.
    durations = sorted(
        total if total is not None
        else int((completed_at - started_at).total_seconds())
        for total, started_at, completed_at in db.query(
            Run.total_duration_seconds, Run.started_at, Run.completed_at
        )
        .filter(Run.status == "complete", Run.started_at.isnot(None), Run.completed_at.isnot(None))
        .all()
    )
    avg_duration_seconds = round(sum(durations) / len(durations), 1) if durations else None
    # Nearest-rank p95 over the sorted durations (portable; modest run volume).
    p95_duration_seconds = (
        durations[min(len(durations) - 1, max(0, round(0.95 * (len(durations) - 1))))]
        if durations else None
    )

    return AdminStats(
        total_runs=total_runs,
        active_users=active_users,
        total_cost=round(total_cost, 4),
        feedback_rate=round(feedback_rate, 1),
        avg_duration_seconds=avg_duration_seconds,
        p95_duration_seconds=p95_duration_seconds,
    )


# ---------------------------------------------------------------------------
# GET /api/admin/users
# ---------------------------------------------------------------------------
@router.get("/admin/users", response_model=list[AdminUser])
async def get_users(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Return all users with per-user stats."""
    return get_users_with_stats(db)


# ---------------------------------------------------------------------------
# PUT /api/admin/users/{user_id}
# ---------------------------------------------------------------------------
@router.put("/admin/users/{user_id}", response_model=AdminUser)
async def update_user(
    user_id: int,
    body: AdminUserUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Update a user's role, status, or limits."""
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise not_found("User not found.")

    changes = {}

    # Validate: can't remove last admin
    if body.role is not None and body.role != target.role:
        if target.role == "admin" and body.role == "user":
            admin_count = (
                db.query(func.count(User.id))
                .filter(User.role == "admin", User.status == "active")
                .scalar()
            )
            if admin_count <= 1:
                raise validation_error("Cannot remove the last admin.")
        changes["role"] = {"old": target.role, "new": body.role}
        target.role = body.role

    # Validate: can't disable yourself
    if body.status is not None and body.status != target.status:
        if target.id == admin.id and body.status == "disabled":
            raise validation_error("Cannot disable your own account.")
        # If disabling the last admin, block it
        if target.role == "admin" and body.status == "disabled":
            active_admin_count = (
                db.query(func.count(User.id))
                .filter(
                    User.role == "admin",
                    User.status == "active",
                    User.id != target.id,
                )
                .scalar()
            )
            if active_admin_count < 1:
                raise validation_error("Cannot disable the last active admin.")
        changes["status"] = {"old": target.status, "new": body.status}
        target.status = body.status

    if body.daily_limit is not None:
        changes["daily_limit"] = {"old": target.daily_limit, "new": body.daily_limit}
        target.daily_limit = body.daily_limit if body.daily_limit > 0 else None

    if body.monthly_limit is not None:
        changes["monthly_limit"] = {
            "old": target.monthly_limit,
            "new": body.monthly_limit,
        }
        target.monthly_limit = body.monthly_limit if body.monthly_limit > 0 else None

    db.commit()
    db.refresh(target)

    if changes:
        logger.info(
            "admin_user_updated: admin=%s target_user=%s changes=%s",
            admin.email,
            target.email,
            json.dumps(changes),
        )

    # Re-fetch stats for updated user using service
    stats = get_single_user_stats(target, db)

    return AdminUser(
        id=target.id,
        email=target.email,
        display_name=target.display_name,
        role=target.role,
        status=target.status,
        daily_limit=target.daily_limit,
        monthly_limit=target.monthly_limit,
        runs_today=stats["runs_today"],
        total_runs=stats["total_runs"],
        total_cost=stats["total_cost"],
        feedback_count=stats["feedback_count"],
        completed_run_count=stats["completed_run_count"],
        last_active_at=target.last_active_at,
        created_at=target.created_at,
    )


# ---------------------------------------------------------------------------
# DELETE /api/admin/feedback/{feedback_id}
# ---------------------------------------------------------------------------
@router.delete("/admin/feedback/{feedback_id}", status_code=204)
async def delete_feedback(
    feedback_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Hard-delete a single feedback submission.

    Used to purge a garbage/abusive response that would otherwise pollute the
    aggregated Feedback Insights. 404 if the row does not exist. The deletion is
    logged with the acting admin and the affected run so the action is auditable.
    """
    feedback = db.query(Feedback).filter(Feedback.id == feedback_id).first()
    if not feedback:
        raise not_found("Feedback not found.")

    run_id = feedback.run_id
    db.delete(feedback)
    db.commit()

    logger.info(
        "admin_feedback_deleted: admin=%s feedback_id=%s run_id=%s",
        admin.email,
        feedback_id,
        run_id,
    )


# ---------------------------------------------------------------------------
# POST /api/admin/runs/reap-orphans
# ---------------------------------------------------------------------------
@router.post("/admin/runs/reap-orphans")
async def reap_orphan_runs(
    dry_run: bool = Query(False, description="Preview candidates without deleting anything."),
    older_than_hours: Optional[int] = Query(
        None, ge=1, description="Override the age threshold in hours (default 24)."
    ),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Reap runs stuck at status='created' (uploaded but never started) along
    with their child rows and leftover storage objects (runs/{id}/input/ and the
    by-submitter index).

    These accumulate when an upload's run is never started -- a declined
    WCM-template upload, or a failed/abandoned start -- and are hidden from the
    Runs dashboard but stay visible in S3. Call with ?dry_run=true first to
    preview which runs would be removed.
    """
    result = reap_orphaned_created_runs(
        db, older_than_hours=older_than_hours, dry_run=dry_run
    )
    logger.info(
        "admin_reap_orphans: admin=%s dry_run=%s candidates=%d reaped=%d objects=%d",
        admin.email, dry_run, result["candidates"], result["reaped"], result["objects_deleted"],
    )
    return result


# ---------------------------------------------------------------------------
# GET /api/admin/runs
# ---------------------------------------------------------------------------
@router.get("/admin/runs", response_model=AdminRunsResponse)
async def get_runs(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    user: Optional[str] = Query(None, description="Filter by user email"),
    status: Optional[str] = Query(
        None,
        description=(
            "Filter by run status. Omit for the default view, which hides "
            "never-started 'created' runs (abandoned/declined uploads). Pass a "
            "specific status for an exact match, or 'all' to include every "
            "status, never-started runs included."
        ),
    ),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Return all runs paginated, with optional filters."""
    # Eager-load run.user via the outer join (the relationship is
    # lazy="raise_on_sql"). contains_eager populates run.user from the joined
    # columns -- one query, no per-row lookup -- while the outer join still
    # lets us filter by user email.
    query = db.query(Run).outerjoin(Run.user).options(contains_eager(Run.user))

    if user:
        query = query.filter(User.email.ilike(f"%{user}%"))

    # Status filtering. The default admin view hides never-started "created"
    # runs -- these accumulate as clutter when users upload a blank WCM template
    # and decline to proceed, leaving a run that is never advanced. An explicit
    # status filters to exactly that status (including "created" to inspect the
    # abandoned ones); the "all" sentinel opts back in to every status.
    if status == "all":
        pass
    elif status:
        query = query.filter(Run.status == status)
    else:
        query = query.filter(Run.status != "created")

    total = query.count()
    rows = query.order_by(Run.started_at.desc()).offset(offset).limit(limit).all()

    # Build run entries with feedback status
    run_ids = [run.id for run in rows]
    feedback_run_ids = set()
    if run_ids:
        feedback_rows = (
            db.query(Feedback.run_id)
            .filter(Feedback.run_id.in_(run_ids))
            .distinct()
            .all()
        )
        feedback_run_ids = {row.run_id for row in feedback_rows}

    # Read cached advisory quality scores in parallel (small JSON per run; only
    # present for runs already scored — None otherwise). Admin-only / paginated.
    cached_scores: dict[str, dict] = {}
    if run_ids:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for rid, score in zip(run_ids, pool.map(get_cached_score, run_ids)):
                if score:
                    cached_scores[rid] = score

    entries = []
    for run in rows:
        # Prefer the persisted pipeline duration so the admin table matches the
        # run status/history API; fall back to wall-clock for runs that predate
        # the column. (Still blank for in-flight runs with no completed_at.)
        if run.total_duration_seconds is not None:
            duration = run.total_duration_seconds
        elif run.started_at and run.completed_at:
            duration = int((run.completed_at - run.started_at).total_seconds())
        else:
            duration = None

        score = cached_scores.get(run.id)
        entries.append(
            AdminRunEntry(
                run_id=run.id,
                user_email=run.user.email if run.user else None,
                user_display_name=run.user.display_name if run.user else None,
                filename=run.filename,
                status=run.status,
                duration_seconds=duration,
                total_cost=round(run.total_cost or 0, 4),
                started_at=run.started_at,
                has_feedback=run.id in feedback_run_ids,
                quality_score=score.get("totalScore") if score else None,
                quality_band=score.get("band") if score else None,
            )
        )

    return AdminRunsResponse(
        runs=entries,
        total=total,
        has_more=(offset + limit) < total,
        offset=offset,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# POST /api/admin/run/{run_id}/score  -- compute/backfill the advisory score
# ---------------------------------------------------------------------------
@router.post("/admin/run/{run_id}/score", response_model=QualityScoreResult)
def compute_run_score(
    run_id: str,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Compute (or refresh) the advisory quality score for a run and cache it.

    Used to backfill runs created before scoring existed, or to refresh after a
    re-run. Sync def so FastAPI runs the (blocking) storage I/O off the loop.
    """
    run = db.query(Run).filter(Run.id == run_id).first()
    if not run:
        raise not_found("Run not found")

    result = compute_and_cache_score(run_id)
    if not result:
        raise not_found("No scorable outputs available for this run")

    return QualityScoreResult(
        run_id=run_id,
        totalScore=result.get("totalScore", 0),
        band=result.get("band", ""),
        dimensionScores=result.get("dimensionScores", []),
        flags=result.get("flags", []),
    )


# ---------------------------------------------------------------------------
# GET /api/admin/config
# ---------------------------------------------------------------------------
@router.get("/admin/config", response_model=AdminConfigResponse)
async def get_config(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Return current system configuration values."""
    configs = db.query(SystemConfig).all()
    config_dict = {}
    for c in configs:
        config_dict[c.key] = json.loads(c.value)

    return AdminConfigResponse(
        allowed_users=config_dict.get("allowed_users", []),
        admin_users=config_dict.get("admin_users", []),
        rate_limit_daily=config_dict.get("rate_limit_daily", 10),
        rate_limit_monthly=config_dict.get("rate_limit_monthly", 50),
        consent_version=config_dict.get("consent_version", "1.0"),
        auth_mode=config_dict.get("auth_mode", "simple"),
    )


# ---------------------------------------------------------------------------
# PUT /api/admin/config
# ---------------------------------------------------------------------------
@router.put("/admin/config", response_model=AdminConfigResponse)
async def update_config(
    body: AdminConfigUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Update system configuration values."""
    changes = {}

    def _update_key(key: str, new_value) -> None:
        """Update a SystemConfig row, logging old/new."""
        row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
        if row:
            old_value = json.loads(row.value)
            if old_value != new_value:
                changes[key] = {"old": old_value, "new": new_value}
                row.value = json.dumps(new_value)
                row.updated_by = admin.id
        else:
            changes[key] = {"old": None, "new": new_value}
            db.add(
                SystemConfig(
                    key=key, value=json.dumps(new_value), updated_by=admin.id
                )
            )

    if body.allowed_users is not None:
        _update_key("allowed_users", body.allowed_users)

    if body.admin_users is not None:
        # Validate: at least one admin must remain
        if len(body.admin_users) < 1:
            raise validation_error("At least one admin user is required.")
        # Ensure all admin users are in allowed users list
        allowed = body.allowed_users
        if allowed is None:
            # Use current allowed list
            row = (
                db.query(SystemConfig)
                .filter(SystemConfig.key == "allowed_users")
                .first()
            )
            allowed = json.loads(row.value) if row else []

        for admin_email in body.admin_users:
            if admin_email.lower() not in [a.lower() for a in allowed]:
                raise validation_error(f"Admin user {admin_email} must also be in the allowed users list.")
        _update_key("admin_users", body.admin_users)

    if body.rate_limit_daily is not None:
        if body.rate_limit_daily < 1:
            raise validation_error("Daily rate limit must be positive.")
        _update_key("rate_limit_daily", body.rate_limit_daily)

    if body.rate_limit_monthly is not None:
        if body.rate_limit_monthly < 1:
            raise validation_error("Monthly rate limit must be positive.")
        _update_key("rate_limit_monthly", body.rate_limit_monthly)

    if body.consent_version is not None:
        _update_key("consent_version", body.consent_version)

    db.commit()

    if changes:
        logger.info(
            "admin_config_changed: admin=%s changes=%s",
            admin.email,
            json.dumps(changes),
        )

    # Return updated config
    return await get_config(db=db, admin=admin)


# ---------------------------------------------------------------------------
# POST /api/admin/sessions/revoke-all
# ---------------------------------------------------------------------------
@router.post("/admin/sessions/revoke-all")
async def revoke_all_sessions(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Invalidate every active session ("sign out everyone").

    Bumps the global session epoch in SystemConfig. Each cookie carries the
    epoch in force when it was minted, so the next request from any existing
    session -- including the admin who pressed this -- fails the epoch check in
    get_current_user (and the websocket auth) and is bounced to login. This is
    the only way to revoke stateless signed-cookie sessions before their TTL --
    use it after a credential leak, a permissions change, or to force re-auth.
    """
    new_epoch = get_session_epoch(db) + 1
    row = db.query(SystemConfig).filter(SystemConfig.key == "session_epoch").first()
    if row:
        row.value = json.dumps(new_epoch)
        row.updated_by = admin.id
    else:
        db.add(SystemConfig(key="session_epoch", value=json.dumps(new_epoch),
                            updated_by=admin.id))
    db.commit()

    logger.info("admin_sessions_revoked_all: admin=%s new_epoch=%d", admin.email, new_epoch)
    return {
        "message": "All sessions revoked. Everyone must log in again.",
        "session_epoch": new_epoch,
    }


# ---------------------------------------------------------------------------
# CSV formula-injection guard (OWASP): a cell whose text starts with a formula
# trigger is interpreted as a formula by Excel/Sheets/LibreOffice. Prefix such
# cells with a single quote so they render as literal text.
# ---------------------------------------------------------------------------
_CSV_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def _sanitize_csv_cell(value):
    if isinstance(value, str) and value and value[0] in _CSV_FORMULA_TRIGGERS:
        return "'" + value
    return value


class _SafeCsvWriter:
    """csv.writer wrapper that neutralizes formula injection in every cell."""

    def __init__(self, f):
        self._writer = csv.writer(f)

    def writerow(self, row):
        self._writer.writerow([_sanitize_csv_cell(c) for c in row])


# ---------------------------------------------------------------------------
# GET /api/admin/export/{export_type}
# ---------------------------------------------------------------------------
@router.get("/admin/export/{export_type}")
async def export_csv(
    export_type: str,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Export data as CSV. Supported types: runs, users, consent, feedback."""
    if export_type not in ("runs", "users", "consent", "feedback"):
        raise validation_error(f"Invalid export type: {export_type}. Must be one of: runs, users, consent, feedback.")

    logger.info(
        "admin_export: admin=%s export_type=%s", admin.email, export_type
    )

    output = io.StringIO()
    writer = _SafeCsvWriter(output)

    if export_type == "runs":
        writer.writerow([
            "run_id", "user_email", "filename", "file_type", "status",
            "started_at", "completed_at", "total_cost", "total_tokens",
            "input_tokens", "output_tokens", "submission_type", "error_message",
        ])
        rows = (
            db.query(Run)
            .outerjoin(Run.user)
            .options(contains_eager(Run.user))
            .order_by(Run.started_at.desc())
            .all()
        )
        for run in rows:
            writer.writerow([
                run.id,
                run.user.email if run.user else "",
                run.filename,
                run.file_type,
                run.status,
                run.started_at.isoformat() if run.started_at else "",
                run.completed_at.isoformat() if run.completed_at else "",
                run.total_cost,
                run.total_tokens,
                run.input_tokens,
                run.output_tokens,
                run.submission_type or "",
                run.error_message or "",
            ])

    elif export_type == "users":
        writer.writerow([
            "id", "email", "display_name", "role", "status",
            "daily_limit", "monthly_limit", "consent_version",
            "consent_date", "created_at", "last_active_at",
        ])
        users = db.query(User).order_by(User.created_at.desc()).all()
        for u in users:
            writer.writerow([
                u.id,
                u.email,
                u.display_name,
                u.role,
                u.status,
                u.daily_limit or "",
                u.monthly_limit or "",
                u.consent_version or "",
                u.consent_date.isoformat() if u.consent_date else "",
                u.created_at.isoformat() if u.created_at else "",
                u.last_active_at.isoformat() if u.last_active_at else "",
            ])

    elif export_type == "consent":
        writer.writerow([
            "id", "user_id", "user_email", "consent_version",
            "consent_text_hash", "ip_address", "user_agent", "timestamp",
        ])
        rows = (
            db.query(Consent)
            .outerjoin(Consent.user)
            .options(contains_eager(Consent.user))
            .order_by(Consent.timestamp.desc())
            .all()
        )
        for consent in rows:
            writer.writerow([
                consent.id,
                consent.user_id,
                consent.user.email if consent.user else "",
                consent.consent_version,
                consent.consent_text_hash,
                consent.ip_address or "",
                consent.user_agent or "",
                consent.timestamp.isoformat() if consent.timestamp else "",
            ])

    elif export_type == "feedback":
        writer.writerow([
            "id", "run_id", "user_email", "reviewer_role",
            "overall_accuracy", "overall_completeness", "overall_usefulness",
            "manual_conversion_effort", "correction_effort",
            "enrichment_quality", "summary_generated", "summary_quality",
            "issue_missing_content", "issue_split_merged", "issue_wrong_section",
            "issue_inaccurate", "issue_ai_enrichment", "issue_formatting",
            "issue_locations", "biggest_issue", "likelihood_to_recommend",
            "submitted_at",
        ])
        rows = (
            db.query(Feedback)
            .outerjoin(Feedback.user)
            .options(contains_eager(Feedback.user))
            .order_by(Feedback.submitted_at.desc())
            .all()
        )
        for fb in rows:
            writer.writerow([
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
                fb.submitted_at.isoformat() if fb.submitted_at else "",
            ])

    output.seek(0)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="cviche_{export_type}_{datetime.now().strftime("%Y%m%d")}.csv"'
        },
    )
