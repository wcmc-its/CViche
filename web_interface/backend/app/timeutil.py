"""The backend's one timestamp convention: every instant is UTC (#798).

In Python a timestamp is a timezone-aware UTC ``datetime`` from ``utc_now()``.
(``rate_limiter`` also reads an aware Eastern clock, on purpose: quota days
start at Eastern midnight.) In the database it is a naive ``DATETIME`` holding
UTC wall-clock time -- MariaDB's ``DATETIME`` has no zone, and SQLAlchemy's
``DateTime(timezone=True)`` changes nothing on MySQL/MariaDB. ``UTCDateTime``
is the documented equivalent: every model timestamp column uses it, so a value
is converted to naive UTC on the way in and comes back aware UTC on the way
out, and a value read back never meets the aware clock as a naive operand.
``func.now()`` server defaults agree because ``database_factory`` pins each
connection's session ``time_zone`` to UTC.

Before this, every write was a naive local-clock read (the pod's zone) and
correctness rested on that zone happening to match the database's. Nothing
pinned it.
"""
from datetime import datetime, timezone

from sqlalchemy.engine import Dialect
from sqlalchemy.types import DateTime, TypeDecorator


def utc_now() -> datetime:
    """The current instant as an aware UTC datetime."""
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """``value`` as an aware UTC datetime.

    A naive value is taken to be UTC already, which is the stored convention:
    it is what the database hands back and what a naive fixture means.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class UTCDateTime(TypeDecorator):
    """A ``DATETIME`` column that stores naive UTC and loads aware UTC."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return as_utc(value).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return as_utc(value)
