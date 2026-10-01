"""Filtering and facet counts for the admin "all runs" view.

GET /runs?scope=all lists every user's runs; GET /runs/filter-options feeds its
three filter dropdowns (Department, Faculty, Run by) and the Feedback filter.
Both build on the same
filter predicates so a dropdown's counts always describe what picking that
option would list.

Cascade (mirrors the redesign mockup): each dropdown's counts apply the OTHER
two filters but not its own, so picking a department narrows the faculty and
run-by lists while the department list stays complete.
"""
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import ColumnElement, and_, case, func, select
from sqlalchemy.orm import Query, Session, contains_eager

from app.errors import validation_error
from app.models import Feedback, Run, RunState, User
from app.schemas import (
    FacultyOption, FeedbackFilterCounts, FeedbackReviewer, FilterCount,
    RunByOption, RunBySummary, RunFeedbackSummary, RunFilterOptions,
)

# Run.submission_type of a faculty member uploading their own CV
# (upload.py's Literal["own_cv", "authorized_admin"]).
OWN_CV_SUBMISSION_TYPE = "own_cv"

# The run_by filter value meaning "the faculty member themselves".
RUN_BY_SELF = "self"


class RunScope(StrEnum):
    MINE = "mine"
    ALL = "all"


class FeedbackFilter(StrEnum):
    """The ``feedback`` filter: runs with any feedback, or complete runs with none."""
    GIVEN = "given"
    NEEDED = "needed"


@dataclass(frozen=True)
class RunFilters:
    """The admin runs filters. None/False = not filtering on that facet."""
    run_by_user_id: int | None = None
    run_by_self: bool = False
    faculty: str | None = None
    department: str | None = None
    feedback: FeedbackFilter | None = None


def parse_feedback_filter(feedback: str | None) -> FeedbackFilter | None:
    """``feedback`` query param -> FeedbackFilter; blank is None, unknown is a 422."""
    if not feedback:
        return None
    try:
        return FeedbackFilter(feedback)
    except ValueError:
        allowed = " or ".join(f'"{f.value}"' for f in FeedbackFilter)
        raise validation_error(f"feedback must be {allowed}") from None


def parse_run_filters(run_by: str | None, faculty: str | None,
                      department: str | None, feedback: str | None = None) -> RunFilters:
    """Build RunFilters from the raw query params. ``run_by`` is a user id or the
    literal "self", ``feedback`` is "given" or "needed"; anything else is a 422.
    Blank strings mean "no filter"."""
    run_by_user_id = None
    run_by_self = False
    if run_by:
        if run_by == RUN_BY_SELF:
            run_by_self = True
        else:
            try:
                run_by_user_id = int(run_by)
            except ValueError:
                raise validation_error(
                    f'run_by must be a user id or "{RUN_BY_SELF}"') from None
    return RunFilters(run_by_user_id=run_by_user_id, run_by_self=run_by_self,
                      faculty=faculty or None, department=department or None,
                      feedback=parse_feedback_filter(feedback))


def _run_by_clause(filters: RunFilters) -> ColumnElement[bool] | None:
    if filters.run_by_self:
        return Run.submission_type == OWN_CV_SUBMISSION_TYPE
    if filters.run_by_user_id is not None:
        # Includes the user's own_cv runs: a faculty member who ran their own
        # CV is listed under their own name.
        return Run.user_id == filters.run_by_user_id
    return None


def _has_feedback() -> ColumnElement[bool]:
    """Correlated EXISTS: this run has a Feedback row from any reviewer."""
    return select(Feedback.id).where(Feedback.run_id == Run.id).exists()


def _needs_feedback() -> ColumnElement[bool]:
    return and_(Run.status == RunState.COMPLETE, ~_has_feedback())


def feedback_clause(feedback: FeedbackFilter | None) -> ColumnElement[bool] | None:
    """The predicate for the ``feedback`` filter (None = not filtering)."""
    if feedback is FeedbackFilter.GIVEN:
        return _has_feedback()
    if feedback is FeedbackFilter.NEEDED:
        return _needs_feedback()
    return None


def _clauses(filters: RunFilters, *, skip_department: bool = False,
             skip_faculty: bool = False, skip_run_by: bool = False,
             skip_feedback: bool = False) -> list[ColumnElement[bool]]:
    """The active filter predicates, minus the facet(s) being counted."""
    clauses = []
    if filters.department and not skip_department:
        clauses.append(User.department == filters.department)
    if filters.faculty and not skip_faculty:
        clauses.append(Run.cv_owner_name == filters.faculty)
    run_by = None if skip_run_by else _run_by_clause(filters)
    if run_by is not None:
        clauses.append(run_by)
    feedback = None if skip_feedback else feedback_clause(filters.feedback)
    if feedback is not None:
        clauses.append(feedback)
    return clauses


def filtered_runs_query(db: Session, filters: RunFilters) -> Query:
    """Every user's runs matching ``filters``, with Run.user loaded from the
    same outer join (Run.user is lazy="raise_on_sql"; runs without a user stay)."""
    return (
        db.query(Run)
        .outerjoin(User, Run.user_id == User.id)
        .options(contains_eager(Run.user))
        .filter(*_clauses(filters))
    )


def run_by_summary(user: User | None) -> RunBySummary | None:
    if user is None:
        return None
    return RunBySummary(id=user.id, display_name=user.display_name, cwid=user.cwid,
                        email=user.email, department=user.department)


def _department_counts(db: Session, filters: RunFilters) -> list[FilterCount]:
    rows = (
        db.query(User.department, func.count(Run.id))
        .select_from(Run).join(User, Run.user_id == User.id)
        .filter(User.department.isnot(None),
                *_clauses(filters, skip_department=True))
        .group_by(User.department).order_by(User.department).all()
    )
    return [FilterCount(value=value, count=count) for value, count in rows]


def _faculty_counts(db: Session, filters: RunFilters) -> list[FacultyOption]:
    rows = (
        db.query(Run.cv_owner_name, func.count(Run.id), func.max(Run.started_at))
        .select_from(Run).outerjoin(User, Run.user_id == User.id)
        .filter(Run.cv_owner_name.isnot(None),
                *_clauses(filters, skip_faculty=True))
        # Most recently run first, so the short list matches the runs table
        # (alphabetical showed only A-names in its first eight).
        .group_by(Run.cv_owner_name)
        .order_by(func.max(Run.started_at).desc(), Run.cv_owner_name).all()
    )
    return [FacultyOption(value=value, count=count, last_run_at=last)
            for value, count, last in rows]


def _run_by_counts(db: Session, filters: RunFilters) -> list[RunByOption]:
    # Group by every selected column, not just User.id: MySQL/MariaDB with
    # ONLY_FULL_GROUP_BY does not treat the other columns as functionally
    # dependent on the key in every version.
    user_columns = (User.id, User.display_name, User.cwid, User.email, User.department)
    rows = (
        db.query(*user_columns, func.count(Run.id))
        .select_from(Run).join(User, Run.user_id == User.id)
        .filter(*_clauses(filters, skip_run_by=True))
        .group_by(*user_columns).order_by(User.display_name, User.id).all()
    )
    return [RunByOption(id=uid, display_name=name, cwid=cwid, email=email,
                        department=department, count=count)
            for uid, name, cwid, email, department, count in rows]


def _self_count(db: Session, filters: RunFilters) -> int:
    return (
        db.query(func.count(Run.id))
        .select_from(Run).outerjoin(User, Run.user_id == User.id)
        .filter(Run.submission_type == OWN_CV_SUBMISSION_TYPE,
                *_clauses(filters, skip_run_by=True))
        .scalar() or 0
    )


def _feedback_counts(db: Session, filters: RunFilters) -> FeedbackFilterCounts:
    given, needed = (
        db.query(func.count(case((_has_feedback(), 1))),
                 func.count(case((_needs_feedback(), 1))))
        .select_from(Run).outerjoin(User, Run.user_id == User.id)
        .filter(*_clauses(filters, skip_feedback=True))
        .one()
    )
    return FeedbackFilterCounts(given=given, needed=needed)


def load_feedback_summaries(db: Session, run_ids: list[str], current_user_id: int,
                            *, with_reviewers: bool) -> dict[str, RunFeedbackSummary]:
    """Feedback summary per run id for one page of runs: one GROUP BY query, plus
    one query for the reviewer rows when ``with_reviewers`` (admin scope). Runs
    without feedback are absent from the result."""
    if not run_ids:
        return {}
    rows = (
        db.query(Feedback.run_id, func.count(Feedback.id), func.max(Feedback.submitted_at),
                 func.max(case((Feedback.user_id == current_user_id, 1), else_=0)))
        .filter(Feedback.run_id.in_(run_ids))
        .group_by(Feedback.run_id).all()
    )
    reviewers = _reviewers_by_run(db, run_ids) if with_reviewers else {}
    return {
        run_id: RunFeedbackSummary(count=count, given_by_me=bool(mine), last_at=last_at,
                                   reviewers=reviewers.get(run_id, []) if with_reviewers else None)
        for run_id, count, last_at, mine in rows
    }


def load_run_feedback_with_reviewers(db: Session, run_id: str) -> list[tuple[Feedback, str]]:
    """Every feedback row on one run with its reviewer's display name, newest first."""
    return (
        db.query(Feedback, User.display_name)
        .join(User, User.id == Feedback.user_id)
        .filter(Feedback.run_id == run_id)
        .order_by(Feedback.submitted_at.desc(), Feedback.id.desc())
        .all()
    )


def empty_feedback_summary(*, with_reviewers: bool) -> RunFeedbackSummary:
    return RunFeedbackSummary(reviewers=[] if with_reviewers else None)


def _reviewers_by_run(db: Session, run_ids: list[str]) -> dict[str, list[FeedbackReviewer]]:
    rows = (
        db.query(Feedback.run_id, User.display_name, Feedback.reviewer_role, Feedback.submitted_at)
        .join(User, User.id == Feedback.user_id)
        .filter(Feedback.run_id.in_(run_ids))
        .order_by(Feedback.submitted_at.desc(), Feedback.id.desc()).all()
    )
    by_run: dict[str, list[FeedbackReviewer]] = {}
    for run_id, display_name, role, submitted_at in rows:
        by_run.setdefault(run_id, []).append(
            FeedbackReviewer(display_name=display_name, role=role, submitted_at=submitted_at))
    return by_run


def build_filter_options(db: Session, filters: RunFilters) -> RunFilterOptions:
    """Options and run counts for the admin filters (five aggregate queries)."""
    return RunFilterOptions(
        departments=_department_counts(db, filters),
        faculty=_faculty_counts(db, filters),
        run_by=_run_by_counts(db, filters),
        self_count=_self_count(db, filters),
        feedback=_feedback_counts(db, filters),
    )
