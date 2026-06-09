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
import pytest
from sqlalchemy import select
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm import selectinload

from app.models import (
    User, Run, Step, Log, LLMUsage, Feedback, RunMetrics, Consent, SystemConfig,
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
