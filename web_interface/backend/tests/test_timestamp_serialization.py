"""Regression guard for the "5 hours ago" timezone bug.

Timestamps are written with naive ``datetime.now()`` (the server's wall clock,
no tzinfo). Before the fix, Pydantic serialized those naive values with no
timezone designator (``2026-06-11T09:00:00``), so the browser's ``new Date()``
interpreted them in the *viewer's* local zone -- making every timestamp wrong by
the viewer's offset (a just-uploaded file showed "5 hours ago" from UTC+5:30).

The ``TZDateTime`` serializer in ``app.schemas`` attaches the server's own offset
so the emitted instant is unambiguous. These tests pin that contract: every
datetime the API returns must carry a timezone, while Python-mode access stays a
real ``datetime`` for internal callers.
"""
from datetime import datetime, timezone, timedelta

from app.schemas import RunSummary, AdminUser


def _run(**overrides):
    base = dict(
        run_id="r", filename="cv.docx", status="complete",
        started_at=datetime(2026, 6, 11, 9, 0, 0),
        total_cost=0.0, total_duration_seconds=1,
    )
    base.update(overrides)
    return RunSummary(**base)


def test_naive_datetime_serializes_with_timezone():
    emitted = _run().model_dump(mode="json")["started_at"]
    # Must round-trip to an aware datetime -- never a bare naive string.
    assert datetime.fromisoformat(emitted).tzinfo is not None, emitted


def test_none_datetime_stays_null():
    assert _run(completed_at=None).model_dump(mode="json")["completed_at"] is None


def test_python_mode_preserves_datetime_type():
    # Internal callers (model.started_at, model_dump()) still get a datetime,
    # not the serialized string -- ``when_used='json'`` only touches JSON output.
    m = _run()
    assert isinstance(m.started_at, datetime)
    assert isinstance(m.model_dump()["started_at"], datetime)


def test_already_aware_datetime_offset_preserved():
    aware = datetime(2026, 6, 11, 9, 0, tzinfo=timezone(timedelta(hours=-4)))
    user = AdminUser(id=1, email="a@b.c", display_name="A", role="user",
                     status="active", last_active_at=aware, created_at=None)
    assert user.model_dump(mode="json")["last_active_at"] == "2026-06-11T09:00:00-04:00"
