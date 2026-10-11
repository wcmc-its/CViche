"""The backend's UTC timestamp convention (#798, app/timeutil.py)."""
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, create_engine, select, text
from sqlalchemy.types import DateTime

from app.base_class import Base
from app.timeutil import UTCDateTime, as_utc, utc_now

EASTERN_SUMMER = timezone(timedelta(hours=-4))


@pytest.fixture
def non_utc_process_zone(monkeypatch):
    """Run the test with the process's local zone set far from UTC."""
    monkeypatch.setenv("TZ", "Asia/Kolkata")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.fixture
def stamps():
    engine = create_engine("sqlite://")
    table = Table(
        "stamps", MetaData(),
        Column("id", Integer, primary_key=True),
        Column("at", UTCDateTime),
    )
    table.metadata.create_all(engine)
    with engine.connect() as conn:
        yield conn, table


def test_utc_now_is_aware_utc():
    now = utc_now()
    assert now.utcoffset() == timedelta(0)
    assert abs(now - datetime.now(timezone.utc)) < timedelta(seconds=5)


def test_utc_now_ignores_a_non_utc_process_zone(non_utc_process_zone):
    # The local zone is UTC+5:30 here; a naive datetime.now() would be 5.5h off.
    assert datetime.now().astimezone().utcoffset() == timedelta(hours=5, minutes=30)
    assert abs(utc_now() - datetime.now(timezone.utc)) < timedelta(seconds=5)


def test_as_utc_reads_a_naive_value_as_utc():
    assert as_utc(datetime(2026, 6, 11, 9, 0)) == datetime(2026, 6, 11, 9, 0, tzinfo=timezone.utc)


def test_as_utc_converts_an_aware_value_to_utc():
    converted = as_utc(datetime(2026, 6, 11, 9, 0, tzinfo=EASTERN_SUMMER))
    assert converted == datetime(2026, 6, 11, 13, 0, tzinfo=timezone.utc)
    assert converted.tzinfo is timezone.utc


def test_column_stores_naive_utc_and_loads_aware_utc(stamps):
    conn, table = stamps
    conn.execute(table.insert().values(id=1, at=datetime(2026, 6, 11, 9, 0, tzinfo=EASTERN_SUMMER)))
    raw = conn.execute(text("SELECT at FROM stamps WHERE id = 1")).scalar_one()
    assert raw.startswith("2026-06-11 13:00:00")  # UTC wall-clock, no offset
    loaded = conn.execute(select(table.c.at)).scalar_one()
    assert loaded == datetime(2026, 6, 11, 13, 0, tzinfo=timezone.utc)
    assert loaded.tzinfo is timezone.utc


def test_column_reads_a_naive_bind_as_utc(stamps):
    conn, table = stamps
    conn.execute(table.insert().values(id=1, at=datetime(2026, 6, 11, 9, 0)))
    assert conn.execute(select(table.c.at)).scalar_one() == datetime(2026, 6, 11, 9, 0, tzinfo=timezone.utc)


def test_comparison_binds_convert_to_utc(stamps):
    # A filter cutoff in another zone compares as the same instant in UTC.
    conn, table = stamps
    conn.execute(table.insert().values(id=1, at=datetime(2026, 6, 11, 13, 0, tzinfo=timezone.utc)))
    cutoff = datetime(2026, 6, 11, 8, 30, tzinfo=EASTERN_SUMMER)  # 12:30 UTC
    assert conn.execute(select(table.c.id).where(table.c.at > cutoff)).scalar_one() == 1


def test_a_non_utc_process_zone_stores_the_same_instant(stamps, non_utc_process_zone):
    conn, table = stamps
    before = datetime.now(timezone.utc)
    conn.execute(table.insert().values(id=1, at=utc_now()))
    raw = conn.execute(text("SELECT at FROM stamps WHERE id = 1")).scalar_one()
    stored = datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
    assert timedelta(0) <= stored - before < timedelta(seconds=5)


def test_every_model_timestamp_column_uses_utc_datetime():
    import app.models  # noqa: F401 -- registers every table on Base.metadata

    plain = [
        f"{table.name}.{column.name}"
        for table in Base.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, DateTime)
    ]
    assert plain == [], "use app.timeutil.UTCDateTime, not DateTime"


def test_no_naive_clock_read_in_the_app():
    """utc_now() is the app's clock (#798): a bare datetime.now() or utcnow()
    is a naive value that the stored-UTC convention would misread."""
    naive_clock = re.compile(r"datetime\.now\(\s*\)|\.utcnow\(")
    app_dir = Path(__file__).resolve().parent.parent / "app"
    hits = [
        f"{path.relative_to(app_dir)}:{number}"
        for path in sorted(app_dir.rglob("*.py"))
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if naive_clock.search(line) and not line.lstrip().startswith("#")
    ]
    assert hits == [], "use app.timeutil.utc_now()"
