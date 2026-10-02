"""Filtering and facet counts for the admin "all runs" view.

GET /runs?scope=all lists every user's runs; GET /runs/filter-options feeds its
three filter dropdowns (Department, Faculty, Run by), the Feedback and
Input format filters, and the status pills' counts.
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
from app.services.input_format import INPUT_FORMAT_OTHER, INPUT_FORMAT_WCM
from app.models import Feedback, Run, RunState, User
from app.services.quality_score_service import BAND_RED
from app.schemas import (
    FacultyOption, FeedbackFilterCounts, InputFormatFilterCounts, FeedbackReviewer, FilterCount,
    RunByOption, RunBySummary, RunFeedbackSummary, RunFilterOptions, StatusFilterCounts,
)

# Run.submission_type of a faculty member uploading their own CV
# (upload.py's Literal["own_cv", "authorized_admin"]).
OWN_CV_SUBMISSION_TYPE = "own_cv"

# Run.submission_type of an authorized admin submitting on a faculty member's behalf.
AUTHORIZED_ADMIN_SUBMISSION_TYPE = "authorized_admin"

# The run_by filter value meaning "the faculty member themselves".
RUN_BY_SELF = "self"

# The run_by filter value meaning "someone submitted it on their behalf".
RUN_BY_ON_BEHALF = "on_behalf"

# Run states the "Running" pill lists: in the queue or being processed.
ACTIVE_RUN_STATES = (RunState.QUEUED, RunState.RUNNING)


class RunScope(StrEnum):
    MINE = "mine"
    ALL = "all"


class FeedbackFilter(StrEnum):
    """The ``feedback`` filter: runs with any feedback, or complete runs with none."""
    GIVEN = "given"
    NEEDED = "needed"


class InputFormatFilter(StrEnum):
    """The ``input_format`` filter: CVs written in the WCM template, in another
    format, or not yet classified (a NULL column)."""
    WCM = INPUT_FORMAT_WCM
    OTHER = INPUT_FORMAT_OTHER
    UNKNOWN = "unknown"


class StatusFilter(StrEnum):
    """The ``status`` filter behind the runs-list pills. RED (score band) is admin-only."""
    RUNNING = "running"
    FAILED = "failed"
    RED = "red"


@dataclass(frozen=True)
class RunFilters:
    """The admin runs filters. None/False = not filtering on that facet."""
    run_by_user_id: int | None = None
    run_by_self: bool = False
    run_by_on_behalf: bool = False
    faculty: str | None = None
    department: str | None = None
    feedback: FeedbackFilter | None = None
    input_format: InputFormatFilter | None = None
    status: StatusFilter | None = None


def parse_feedback_filter(feedback: str | None) -> FeedbackFilter | None:
    """``feedback`` query param -> FeedbackFilter; blank is None, unknown is a 422."""
    if not feedback:
        return None
    try:
        return FeedbackFilter(feedback)
    except ValueError:
        allowed = " or ".join(f'"{f.value}"' for f in FeedbackFilter)
        raise validation_error(f"feedback must be {allowed}") from None


def parse_input_format_filter(input_format: str | None) -> InputFormatFilter | None:
    """``input_format`` query param -> InputFormatFilter; blank is None, unknown is a 422."""
    if not input_format:
        return None
    try:
        return InputFormatFilter(input_format)
    except ValueError:
        allowed = ", ".join(f'"{f.value}"' for f in InputFormatFilter)
        raise validation_error(f"input_format must be one of {allowed}") from None


def parse_status_filter(status: str | None) -> StatusFilter | None:
    """``status`` query param -> StatusFilter; blank is None, unknown is a 422."""
    if not status:
        return None
    try:
        return StatusFilter(status)
    except ValueError:
        allowed = ", ".join(f'"{f.value}"' for f in StatusFilter)
        raise validation_error(f"status must be one of {allowed}") from None


def parse_run_filters(run_by: str | None, faculty: str | None,
                      department: str | None, feedback: str | None = None,
                      input_format: str | None = None,
                      status: str | None = None) -> RunFilters:
    """Build RunFilters from the raw query params. ``run_by`` is a user id, "self"
    or "on_behalf", ``feedback`` is "given" or "needed", ``input_format`` is "wcm",
    "other" or "unknown", ``status`` is "running", "failed" or "red"; anything else
    is a 422. Blank strings mean "no filter"."""
    run_by_user_id = None
    run_by_self = False
    run_by_on_behalf = False
    if run_by:
        if run_by == RUN_BY_SELF:
            run_by_self = True
        elif run_by == RUN_BY_ON_BEHALF:
            run_by_on_behalf = True
        else:
            try:
                run_by_user_id = int(run_by)
            except ValueError:
                raise validation_error(
                    f'run_by must be a user id, "{RUN_BY_SELF}" or "{RUN_BY_ON_BEHALF}"') from None
    return RunFilters(run_by_user_id=run_by_user_id, run_by_self=run_by_self,
                      run_by_on_behalf=run_by_on_behalf,
                      faculty=faculty or None, department=department or None,
                      feedback=parse_feedback_filter(feedback),
                      input_format=parse_input_format_filter(input_format),
                      status=parse_status_filter(status))


def _run_by_clause(filters: RunFilters) -> ColumnElement[bool] | None:
    if filters.run_by_self:
        return Run.submission_type == OWN_CV_SUBMISSION_TYPE
    if filters.run_by_on_behalf:
        return Run.submission_type == AUTHORIZED_ADMIN_SUBMISSION_TYPE
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


def input_format_clause(input_format: InputFormatFilter | None) -> ColumnElement[bool] | None:
    """The predicate for the ``input_format`` filter (None = not filtering)."""
    if input_format is None:
        return None
    if input_format is InputFormatFilter.UNKNOWN:
        return Run.input_format.is_(None)
    return Run.input_format == input_format.value


def status_clause(status: StatusFilter | None) -> ColumnElement[bool] | None:
    """The predicate for the ``status`` filter (None = not filtering).

    Failed lists the failed runs themselves, not the rest of their faculty
    member's group: a group's visible top row is then always a failed run, so the
    table matches the pill and its count."""
    if status is StatusFilter.RUNNING:
        return Run.status.in_(ACTIVE_RUN_STATES)
    if status is StatusFilter.FAILED:
        return Run.status == RunState.FAILED
    if status is StatusFilter.RED:
        return Run.quality_band == BAND_RED
    return None


def _clauses(filters: RunFilters, *, skip_department: bool = False,
             skip_faculty: bool = False, skip_run_by: bool = False,
             skip_feedback: bool = False,
             skip_input_format: bool = False,
             skip_status: bool = False) -> list[ColumnElement[bool]]:
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
    input_format = None if skip_input_format else input_format_clause(filters.input_format)
    if input_format is not None:
        clauses.append(input_format)
    status = None if skip_status else status_clause(filters.status)
    if status is not None:
        clauses.append(status)
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


def _submission_type_count(db: Session, filters: RunFilters, submission_type: str) -> int:
    return (
        db.query(func.count(Run.id))
        .select_from(Run).outerjoin(User, Run.user_id == User.id)
        .filter(Run.submission_type == submission_type,
                *_clauses(filters, skip_run_by=True))
        .scalar() or 0
    )


def _status_counts(db: Session, filters: RunFilters, *,
                   owner_user_id: int | None = None) -> StatusFilterCounts:
    """Runs per pill. Applies every filter except ``status`` (the pills are one
    choice among themselves); ``awaiting_feedback`` is the feedback=needed count.
    ``owner_user_id`` counts only that user's own runs (the member view)."""
    query = (
        db.query(func.count(Run.id),
                 func.count(case((status_clause(StatusFilter.RUNNING), 1))),
                 func.count(case((status_clause(StatusFilter.FAILED), 1))),
                 func.count(case((status_clause(StatusFilter.RED), 1))),
                 func.count(case((_needs_feedback(), 1))))
        .select_from(Run).outerjoin(User, Run.user_id == User.id)
        .filter(*_clauses(filters, skip_status=True))
    )
    if owner_user_id is not None:
        query = query.filter(Run.user_id == owner_user_id)
    total, running, failed, red, awaiting = query.one()
    return StatusFilterCounts(all=total, running=running, awaiting_feedback=awaiting,
                              failed=failed, red=red)


def my_status_counts(db: Session, user_id: int) -> StatusFilterCounts:
    """The status pill counts over one member's own runs only. ``red`` is the
    admin score band, so it is always 0 here."""
    counts = _status_counts(db, RunFilters(), owner_user_id=user_id)
    return counts.model_copy(update={"red": 0})


def _feedback_counts(db: Session, filters: RunFilters) -> FeedbackFilterCounts:
    given, needed = (
        db.query(func.count(case((_has_feedback(), 1))),
                 func.count(case((_needs_feedback(), 1))))
        .select_from(Run).outerjoin(User, Run.user_id == User.id)
        .filter(*_clauses(filters, skip_feedback=True))
        .one()
    )
    return FeedbackFilterCounts(given=given, needed=needed)


def _input_format_counts(db: Session, filters: RunFilters) -> InputFormatFilterCounts:
    wcm, other, unknown = (
        db.query(func.count(case((Run.input_format == INPUT_FORMAT_WCM, 1))),
                 func.count(case((Run.input_format == INPUT_FORMAT_OTHER, 1))),
                 func.count(case((Run.input_format.is_(None), 1))))
        .select_from(Run).outerjoin(User, Run.user_id == User.id)
        .filter(*_clauses(filters, skip_input_format=True))
        .one()
    )
    return InputFormatFilterCounts(wcm=wcm, other=other, unknown=unknown)


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
    """Options and run counts for the admin filters (eight aggregate queries)."""
    return RunFilterOptions(
        departments=_department_counts(db, filters),
        faculty=_faculty_counts(db, filters),
        run_by=_run_by_counts(db, filters),
        self_count=_submission_type_count(db, filters, OWN_CV_SUBMISSION_TYPE),
        on_behalf_count=_submission_type_count(db, filters, AUTHORIZED_ADMIN_SUBMISSION_TYPE),
        status=_status_counts(db, filters),
        feedback=_feedback_counts(db, filters),
        input_format=_input_format_counts(db, filters),
    )
