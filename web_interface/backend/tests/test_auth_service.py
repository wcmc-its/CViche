"""The simple-auth login workflow, driven directly -- no TestClient (#343).

Cases, one test each unless named together:
  allowlisted email -> LoginSession, provisioned row, decodable token
  email normalized, display name stripped; allowlist matched case-insensitively
  admin_users -> admin; dropped from admin_users -> demoted to user
  not allowlisted / allowed_users unset / blank email -> NOT_ALLOWLISTED, no row
  store unwritable -> SESSION_STORE_UNAVAILABLE, row still provisioned
  epoch unreadable -> SESSION_STATE_UNAVAILABLE
  disabled user -> ACCOUNT_DISABLED, no session minted, LOGIN_FAILED (account_disabled)
  audit lines: LOGIN_SUCCESS / LOGIN_FAILED extras, under app.api.auth_routes
"""
import json
import logging
from unittest.mock import MagicMock

import pytest
import redis.exceptions

import app.session_idle as session_idle
from app.auth import decode_session_cookie
from app.models import SystemConfig, User
from app.services.auth_service import LoginRejection, LoginSession, authenticate_simple_login
from app.session_idle import IdleSessionStore

_ROUTE_LOGGER = "app.api.auth_routes"


def _config(db, **values):
    """Upsert SystemConfig rows; session_epoch defaults to 0 (the lifespan
    seeder writes it in a real app, and minting refuses without it)."""
    values.setdefault("session_epoch", 0)
    for key, value in values.items():
        row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
        if row is None:
            db.add(SystemConfig(key=key, value=json.dumps(value)))
        else:
            row.value = json.dumps(value)
    db.commit()


@pytest.fixture
def allowlist(db):
    _config(db, allowed_users=["test@example.com"], admin_users=["admin@example.com"])


def _events(caplog, name):
    return [r for r in caplog.records if r.getMessage() == name]


def test_allowlisted_email_signs_in(db, allowlist):
    outcome = authenticate_simple_login(db, "test@example.com", "Test User")

    assert isinstance(outcome, LoginSession)
    user = db.query(User).one()
    assert outcome.user.id == user.id
    assert (user.email, user.display_name, user.auth_method, user.role) == (
        "test@example.com", "Test User", "simple", "user",
    )
    payload = decode_session_cookie(outcome.token)
    assert (payload["user_id"], payload["epoch"]) == (user.id, 0)


def test_email_is_normalized_and_display_name_stripped(db):
    _config(db, allowed_users=["TEST@example.com"])

    outcome = authenticate_simple_login(db, "  Test@Example.COM ", "  Test User  ")

    assert isinstance(outcome, LoginSession)
    assert (outcome.user.email, outcome.user.display_name) == ("test@example.com", "Test User")


def test_admin_users_grants_admin_and_removal_demotes(db):
    _config(db, allowed_users=["boss@example.com"], admin_users=["BOSS@example.com"])
    assert authenticate_simple_login(db, "boss@example.com", "Boss").user.role == "admin"

    _config(db, admin_users=[])
    outcome = authenticate_simple_login(db, "boss@example.com", "Boss")

    assert outcome.user.role == "user"
    assert db.query(User).count() == 1


@pytest.mark.parametrize("email", ["stranger@example.com", "", "   "])
def test_email_outside_the_allowlist_is_rejected_with_no_row(db, allowlist, email):
    assert authenticate_simple_login(db, email, "S") is LoginRejection.NOT_ALLOWLISTED
    assert db.query(User).count() == 0


def test_unset_allowed_users_rejects_everyone(db):
    _config(db, allowed_users=None)

    assert authenticate_simple_login(db, "test@example.com", "T") is LoginRejection.NOT_ALLOWLISTED


def test_rejection_logs_the_raw_email_under_the_route_logger(db, allowlist, caplog):
    with caplog.at_level(logging.INFO, logger=_ROUTE_LOGGER):
        authenticate_simple_login(db, " Stranger@Example.com", "S")

    warning = next(r for r in caplog.records if r.levelno == logging.WARNING)
    assert warning.getMessage() == "Login rejected for unrecognised email:  Stranger@Example.com"
    [failed] = _events(caplog, "LOGIN_FAILED")
    assert (failed.name, failed.email, failed.reason) == (
        _ROUTE_LOGGER, " Stranger@Example.com", "not_allowlisted",
    )
    assert not _events(caplog, "LOGIN_SUCCESS")


def test_success_logs_login_success_under_the_route_logger(db, allowlist, caplog):
    with caplog.at_level(logging.INFO, logger=_ROUTE_LOGGER):
        outcome = authenticate_simple_login(db, "test@example.com", "Test User")

    [event] = _events(caplog, "LOGIN_SUCCESS")
    assert event.name == _ROUTE_LOGGER
    assert (event.user_id, event.email, event.role, event.auth_method) == (
        outcome.user.id, "test@example.com", "user", "simple",
    )
    assert not _events(caplog, "LOGIN_FAILED")


def test_unwritable_store_is_rejected_after_provisioning(db, allowlist, monkeypatch, caplog):
    store = IdleSessionStore("redis://fake", 1200)
    store._client = MagicMock()
    store._client.set.side_effect = redis.exceptions.ConnectionError("valkey down")
    monkeypatch.setattr(session_idle, "_store", store)

    with caplog.at_level(logging.INFO, logger=_ROUTE_LOGGER):
        outcome = authenticate_simple_login(db, "test@example.com", "Test User")

    assert outcome is LoginRejection.SESSION_STORE_UNAVAILABLE
    assert db.query(User).one().email == "test@example.com"
    [failed] = _events(caplog, "LOGIN_FAILED")
    assert (failed.email, failed.reason) == ("test@example.com", "session_store_unavailable")
    assert [r.reason for r in _events(caplog, "SESSION_STORE_UNAVAILABLE")] == ["start"]
    assert not _events(caplog, "LOGIN_SUCCESS")


def test_unreadable_epoch_is_rejected(db, allowlist, caplog):
    _config(db, session_epoch="abc")

    with caplog.at_level(logging.INFO, logger=_ROUTE_LOGGER):
        outcome = authenticate_simple_login(db, "test@example.com", "Test User")

    assert outcome is LoginRejection.SESSION_STATE_UNAVAILABLE
    [failed] = _events(caplog, "LOGIN_FAILED")
    assert failed.reason == "session_state_unavailable"
    assert not _events(caplog, "SESSION_STORE_UNAVAILABLE")


def test_disabled_user_is_refused_before_a_session_is_minted(db, allowlist, monkeypatch, caplog):
    """A disabled account used to get a session, refused only on its next
    request (get_current_user), with a LOGIN_SUCCESS audit line for a login
    that could never be used. Login now refuses it outright."""
    from app.services import auth_service
    db.add(User(email="test@example.com", display_name="T", role="user", status="disabled"))
    db.commit()
    monkeypatch.setattr(auth_service, "create_session_cookie",
                        lambda *a, **k: pytest.fail("no session for a disabled account"))

    with caplog.at_level(logging.INFO, logger=_ROUTE_LOGGER):
        outcome = authenticate_simple_login(db, "test@example.com", "Test User")

    assert outcome is LoginRejection.ACCOUNT_DISABLED
    assert db.query(User).one().status == "disabled"
    [failed] = _events(caplog, "LOGIN_FAILED")
    assert (failed.email, failed.reason) == ("test@example.com", "account_disabled")
    assert not _events(caplog, "LOGIN_SUCCESS")
