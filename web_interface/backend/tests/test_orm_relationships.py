"""ORM relationship() mappings (#131, step 1).

These are additive, model-layer-only mappings. Every relationship is declared
lazy="raise_on_sql", so callers must eager-load the paths they touch and a stray
lazy load raises instead of silently emitting SQL (or detaching outside the
request/session scope). These tests pin both halves of that contract:
  * eager-loaded access resolves, including the back-populated reverse side;
  * un-eager-loaded access raises rather than lazy-loading.
They also exercise the configure_mappers() graph (any back_populates typo would
raise at the first query below).
"""
import os

import pytest
from sqlalchemy import Text, select
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm import selectinload

from app.models import (
    Consent,
    Feedback,
    LLMUsage,
    Log,
    Run,
    RunMetrics,
    Step,
    SystemConfig,
    User,
)

os.environ.setdefault("CVICHE_SESSION_SECRET", "test-secret-not-for-production")

# The 6 issue_* columns were String(20) since the feedback table's creation;
# the frontend sends full sentences into each, and MySQL strict mode raised
# 1406 on any real answer (#606).
_FEEDBACK_ISSUE_COLUMNS = (
    "issue_missing_content",
    "issue_split_merged",
    "issue_wrong_section",
    "issue_inaccurate",
    "issue_ai_enrichment",
    "issue_formatting",
)


def _seed_full_run(db):
    """Create one user + one fully-populated run; return the user id."""
    user = User(email="rel@example.com", display_name="Rel User")
    db.add(user)
    db.flush()  # assign user.id

    run = Run(id="REL00001", filename="cv.docx", file_type="docx",
              status="complete", user_id=user.id)
    db.add(run)
    db.add_all([
        Step(run_id=run.id, step_number=1, step_name="parse", status="complete"),
        Step(run_id=run.id, step_number=2, step_name="score", status="complete"),
        Log(run_id=run.id, message="started"),
        LLMUsage(run_id=run.id, step_number=1, model="haiku", prompt_tokens=10,
                 completion_tokens=5, total_tokens=15, cost=0.001),
        Feedback(run_id=run.id, user_id=user.id, reviewer_role="faculty",
                 overall_usefulness=5, manual_conversion_effort="low",
                 correction_effort="low", likelihood_to_recommend=5),
        RunMetrics(run_id=run.id, word_count=1200),
        Consent(user_id=user.id, consent_version="1.0", consent_text_hash="abc123"),
    ])
    db.commit()
    return user.id


def test_eager_loaded_run_relationships_resolve(db):
    _seed_full_run(db)
    db.expire_all()  # force a clean load through the eager options
    run = db.execute(
        select(Run).options(
            selectinload(Run.user),
            selectinload(Run.steps),
            selectinload(Run.logs),
            selectinload(Run.llm_usage),
            selectinload(Run.feedback),
            selectinload(Run.metrics),
        ).where(Run.id == "REL00001")
    ).scalar_one()

    assert run.user is not None and run.user.email == "rel@example.com"
    assert sorted(s.step_number for s in run.steps) == [1, 2]
    assert len(run.logs) == 1
    assert len(run.llm_usage) == 1
    assert len(run.feedback) == 1
    assert run.metrics is not None and run.metrics.word_count == 1200  # one-to-one


def test_user_side_collections_resolve(db):
    uid = _seed_full_run(db)
    db.expire_all()
    user = db.execute(
        select(User).options(
            selectinload(User.runs),
            selectinload(User.consents),
            selectinload(User.feedback),
        ).where(User.id == uid)
    ).scalar_one()
    assert [r.id for r in user.runs] == ["REL00001"]
    assert len(user.consents) == 1
    assert len(user.feedback) == 1


def test_back_populates_reverse_side(db):
    _seed_full_run(db)
    db.expire_all()
    step = db.execute(
        select(Step).options(selectinload(Step.run))
        .where(Step.run_id == "REL00001").limit(1)
    ).scalar_one()
    assert step.run.id == "REL00001"
    metrics = db.execute(
        select(RunMetrics).options(selectinload(RunMetrics.run))
        .where(RunMetrics.run_id == "REL00001")
    ).scalar_one()
    assert metrics.run.id == "REL00001"


def test_lazy_access_without_eager_load_raises(db):
    _seed_full_run(db)
    db.expire_all()
    run = db.get(Run, "REL00001")  # no eager options
    with pytest.raises(InvalidRequestError):
        list(run.steps)
    with pytest.raises(InvalidRequestError):
        _ = run.user


def test_run_user_is_none_for_anonymous_run(db):
    db.add(Run(id="ANON0001", filename="cv.pdf", file_type="pdf",
               status="complete", user_id=None))
    db.commit()
    db.expire_all()
    run = db.execute(
        select(Run).options(selectinload(Run.user)).where(Run.id == "ANON0001")
    ).scalar_one()
    assert run.user is None  # nullable FK -> empty many-to-one


def test_system_config_updated_by_user(db):
    admin = User(email="admin@example.com", display_name="Admin", role="admin")
    db.add(admin)
    db.flush()
    db.add(SystemConfig(key="k", value='"v"', updated_by=admin.id))
    db.commit()
    db.expire_all()
    cfg = db.execute(
        select(SystemConfig).options(selectinload(SystemConfig.updated_by_user))
        .where(SystemConfig.key == "k")
    ).scalar_one()
    assert cfg.updated_by_user is not None
    assert cfg.updated_by_user.email == "admin@example.com"


def test_feedback_issue_columns_are_text() -> None:
    """Each issue_* column is Text (not String) -- isinstance is the correct
    check here because sqlalchemy.Text subclasses String, so a String(20)
    column would also pass a `isinstance(type_, String)` assertion; the
    reverse (Text is-not-instance-of the narrower String(20)) does not hold,
    so this direction is the one that actually distinguishes them."""
    for column_name in _FEEDBACK_ISSUE_COLUMNS:
        column_type = Feedback.__table__.columns[column_name].type
        assert isinstance(column_type, Text), (
            f"{column_name} is {column_type!r}, expected Text"
        )


def test_submit_feedback_long_issue_text_roundtrips(client, db):
    """A long, real-sentence-shaped answer in issue_missing_content survives
    the full submit_feedback path unmodified -- no truncation at the Pydantic
    schema, the route, or the ORM layer (#606)."""
    from app.auth import get_current_user
    from app.main import app
    from app.models import Run, User

    user = User(email="longtext@example.com", display_name="Reviewer", role="user")
    db.add(user)
    db.flush()
    db.add(Run(id="_LNGTX", filename="cv.docx", file_type="docx",
               status="complete", user_id=user.id))
    db.commit()
    db.refresh(user)

    long_answer = ("This section is missing the fellowship training entirely. " * 6)[:300]
    assert len(long_answer) == 300  # would have overflowed the old String(20)

    app.dependency_overrides[get_current_user] = lambda: user
    try:
        resp = client.post(
            "/api/run/_LNGTX/feedback",
            json={
                "reviewer_role": "self",
                "overall_usefulness": 3,
                "manual_conversion_effort": "1-2 hours",
                "correction_effort": "1-2 hours",
                "likelihood_to_recommend": 3,
                "issue_missing_content": long_answer,
            },
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 201
    saved = db.query(Feedback).filter(Feedback.run_id == "_LNGTX").one()
    assert saved.issue_missing_content == long_answer
