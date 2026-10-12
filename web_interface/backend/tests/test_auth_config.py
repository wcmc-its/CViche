"""Tests for MODE-01: auth mode config endpoint, mode guard, and auth_method column."""
import json
import logging
from unittest.mock import MagicMock, patch

import pytest

from app.auth import (
    COOKIE_NAME,
    SESSION_TTL,
    decode_session_cookie,
    get_cookie_settings,
)
from app.login_throttle import LoginThrottle
from app.models import SystemConfig, User
from app.services.config_service import (
    LOGIN_RATE_LIMIT_MAX,
    LOGIN_RATE_LIMIT_WINDOW,
    MAX_UPLOAD_MB,
)


def test_config_endpoint_simple(client, seed_simple_mode):
    """GET /api/auth/config returns the mode and the upload cap when auth_mode is simple."""
    response = client.get("/api/auth/config")
    assert response.status_code == 200
    assert response.json() == {"mode": "simple", "max_upload_mb": MAX_UPLOAD_MB}


def test_config_endpoint_advertises_the_cap_the_upload_route_enforces(client, seed_simple_mode):
    """#109: the frontend checks file size against max_upload_mb instead of
    its own hard-coded copy, so the value must be the one /upload and email
    intake enforce (MAX_UPLOAD_SIZE), not a second definition."""
    from app.api import upload
    from app.services import inbound_mail

    advertised = client.get("/api/auth/config").json()["max_upload_mb"] * 1024 * 1024

    assert advertised == upload.MAX_UPLOAD_SIZE == inbound_mail.MAX_UPLOAD_SIZE


def test_config_endpoint_saml(client, seed_saml_mode):
    """GET /api/auth/config returns mode=saml with discovery_url when auth_mode is saml."""
    response = client.get("/api/auth/config")
    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "saml"
    assert data["discovery_url"] == "https://login.weill.cornell.edu/discovery"


def test_config_endpoint_no_auth_required(client, seed_simple_mode):
    """GET /api/auth/config returns 200 even without a session cookie (public endpoint)."""
    # No cookies set -- should still work
    response = client.get("/api/auth/config")
    assert response.status_code == 200


def test_login_blocked_in_saml_mode(client, seed_saml_mode):
    """POST /api/auth/login returns 403 sso_required when auth_mode is saml."""
    response = client.post(
        "/api/auth/login",
        json={"email": "test@example.com", "display_name": "Test User"},
    )
    assert response.status_code == 403
    data = response.json()
    assert data["error"] == "sso_required"
    assert "SSO" in data["message"]


def test_login_works_in_simple_mode(client, seed_simple_mode):
    """POST /api/auth/login returns 200 with user data when auth_mode is simple."""
    response = client.post(
        "/api/auth/login",
        json={"email": "test@example.com", "display_name": "Test User"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "test@example.com"


def test_user_auth_method_default(db):
    """Creating a User without specifying auth_method results in 'simple' default."""
    user = User(email="x@y.com", display_name="X", role="user")
    db.add(user)
    db.commit()
    db.refresh(user)
    assert user.auth_method == "simple"


def test_login_sets_auth_method(client, db, seed_simple_mode):
    """After POST /api/auth/login succeeds, the User record has auth_method='simple'."""
    response = client.post(
        "/api/auth/login",
        json={"email": "test@example.com", "display_name": "Test User"},
    )
    assert response.status_code == 200
    user = db.query(User).filter(User.email == "test@example.com").first()
    assert user is not None
    assert user.auth_method == "simple"


# ---------------------------------------------------------------------------
# POST /api/auth/login's HTTP contract, pinned before its workflow moved into
# app/services/auth_service.py (#343, CODING_STANDARDS.md §6.3). The audit
# lines and the two 503 mint failures are pinned in test_auth_audit_events.py
# and test_session_identity_resolution.py.
# ---------------------------------------------------------------------------

_LOGIN = {"email": "test@example.com", "display_name": "Test User"}


@pytest.fixture
def login_throttle(monkeypatch):
    """A fresh per-test throttle: the process-wide one is shared by every
    login in the suite (10 per minute per IP), so these tests neither spend
    nor depend on it."""
    throttle = LoginThrottle(None, LOGIN_RATE_LIMIT_MAX, LOGIN_RATE_LIMIT_WINDOW)
    monkeypatch.setattr("app.api.auth_routes.get_login_throttle", lambda: throttle)
    return throttle


def _set_config(db, **values):
    for key, value in values.items():
        db.query(SystemConfig).filter(SystemConfig.key == key).one().value = json.dumps(value)
    db.commit()


def test_login_in_saml_mode_is_refused_before_the_throttle_counts_it(client, seed_saml_mode, monkeypatch):
    throttle = MagicMock()
    monkeypatch.setattr("app.api.auth_routes.get_login_throttle", lambda: throttle)

    response = client.post("/api/auth/login", json=_LOGIN)

    assert response.status_code == 403
    assert response.json() == {
        "error": "sso_required",
        "message": "This instance uses SSO. Please use the SSO login button.",
    }
    throttle.allow.assert_not_called()


def test_throttled_login_is_429_before_the_allowlist_is_read(client, db, seed_simple_mode, monkeypatch, caplog):
    throttle = MagicMock()
    throttle.allow.return_value = False
    monkeypatch.setattr("app.api.auth_routes.get_login_throttle", lambda: throttle)

    with caplog.at_level(logging.INFO, logger="app.api.auth_routes"):
        response = client.post("/api/auth/login", json=_LOGIN)

    assert response.status_code == 429
    assert response.json() == {
        "error": "rate_limited",
        "message": "Too many login attempts. Please try again later.",
    }
    assert "set-cookie" not in response.headers
    assert db.query(User).count() == 0
    assert not [r for r in caplog.records if r.getMessage() in ("LOGIN_FAILED", "LOGIN_SUCCESS")]


def test_throttle_is_keyed_on_the_last_forwarded_hop(client, seed_simple_mode, monkeypatch):
    throttle = MagicMock()
    throttle.allow.return_value = True
    monkeypatch.setattr("app.api.auth_routes.get_login_throttle", lambda: throttle)

    client.post("/api/auth/login", json=_LOGIN, headers={"X-Forwarded-For": "1.2.3.4, 5.6.7.8"})
    client.post("/api/auth/login", json=_LOGIN)

    assert [c.args for c in throttle.allow.call_args_list] == [("5.6.7.8",), ("testclient",)]


def test_login_not_allowlisted_is_403_with_no_user_and_no_cookie(client, db, seed_simple_mode, login_throttle):
    response = client.post("/api/auth/login", json={"email": "stranger@example.com", "display_name": "S"})

    assert response.status_code == 403
    assert response.json() == {"error": "forbidden", "message": "Email not in the allowed users list."}
    assert "set-cookie" not in response.headers
    assert db.query(User).count() == 0


def test_login_success_body_and_cookie_attributes(client, db, seed_simple_mode, login_throttle):
    response = client.post("/api/auth/login", json=_LOGIN)

    assert response.status_code == 200
    user = db.query(User).one()
    assert response.json() == {
        "user_id": user.id, "email": "test@example.com", "display_name": "Test User", "role": "user",
    }
    token = response.cookies[COOKIE_NAME]
    assert decode_session_cookie(token)["user_id"] == user.id
    secure = "; Secure" if get_cookie_settings()["secure"] else ""
    assert response.headers["set-cookie"] == (
        f"{COOKIE_NAME}={token}; HttpOnly; Max-Age={SESSION_TTL}; Path=/; SameSite=lax{secure}"
    )


def test_login_normalizes_the_email_and_strips_the_display_name(client, db, seed_simple_mode, login_throttle):
    response = client.post(
        "/api/auth/login", json={"email": "  Test@Example.COM ", "display_name": "  Test User  "},
    )

    assert response.status_code == 200
    assert response.json()["email"] == "test@example.com"
    user = db.query(User).one()
    assert (user.email, user.display_name) == ("test@example.com", "Test User")


def test_login_role_follows_admin_users_on_every_login(client, db, seed_simple_mode, login_throttle):
    """admin_users is read on each login, so removal from it demotes."""
    _set_config(db, allowed_users=["Boss@Example.com"], admin_users=["boss@example.COM"])
    first = client.post("/api/auth/login", json={"email": "boss@example.com", "display_name": "Boss"})
    assert first.json()["role"] == "admin"

    _set_config(db, admin_users=[])
    second = client.post("/api/auth/login", json={"email": "boss@example.com", "display_name": "Boss"})

    assert second.json()["role"] == "user"
    assert second.json()["user_id"] == first.json()["user_id"]
    assert db.query(User).one().role == "user"


def test_login_updates_the_existing_user_row(client, db, seed_simple_mode, login_throttle):
    existing = User(email="test@example.com", display_name="Old Name", role="user", auth_method="saml")
    db.add(existing)
    db.commit()

    response = client.post("/api/auth/login", json=_LOGIN)

    assert response.json()["user_id"] == existing.id
    db.refresh(existing)
    assert (existing.display_name, existing.auth_method) == ("Test User", "simple")
    assert db.query(User).count() == 1


def test_login_of_a_disabled_user_is_refused_with_no_cookie(client, db, seed_simple_mode, login_throttle):
    """A disabled account gets a 403 carrying the same account_disabled body
    get_current_user answers with, and no session cookie."""
    db.add(User(email="test@example.com", display_name="Test User", role="user", status="disabled"))
    db.commit()

    response = client.post("/api/auth/login", json=_LOGIN)

    assert response.status_code == 403
    assert response.json()["error"] == "account_disabled"
    assert COOKIE_NAME not in response.cookies
    assert db.query(User).one().status == "disabled"


def test_saml_config_seeded(db):
    """After seed_system_config(), SystemConfig contains all 4 SAML keys."""
    saml_yaml_config = {
        "auth": {"mode": "simple"},
        "saml": {
            "entity_id": "https://cviche.med.cornell.edu/shibboleth",
            "idp_metadata_url": "https://shibboleth.weill.cornell.edu/idp/metadata",
            "discovery_url": "https://login.weill.cornell.edu/discovery",
            "cert_dir": "/etc/cviche/certs",
        },
        "allowed_users": ["test@example.com"],
        "admin_users": [],
        "rate_limits": {"daily": 10, "monthly": 50},
        "consent": {"version": "1.0"},
    }
    with patch("app.config_loader.load_yaml_config", return_value=saml_yaml_config):
        from app.config_loader import seed_system_config
        seed_system_config(db)

    expected_keys = [
        "saml_entity_id",
        "saml_idp_metadata_url",
        "saml_discovery_url",
        "saml_cert_dir",
    ]
    for key in expected_keys:
        row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
        assert row is not None, f"SystemConfig key '{key}' not found"
        # Verify value is JSON-decodable
        value = json.loads(row.value)
        assert isinstance(value, str), f"SystemConfig key '{key}' value is not a string"


def _yaml(mode="simple", entity_id="", idp_metadata_url="", allowed=None):
    return {
        "auth": {"mode": mode},
        "saml": {
            "entity_id": entity_id,
            "idp_metadata_url": idp_metadata_url,
            "discovery_url": "",
            "cert_dir": "",
        },
        "allowed_users": allowed if allowed is not None else [],
        "admin_users": [],
        "rate_limits": {"daily": 10, "monthly": 50},
        "consent": {"version": "1.0"},
    }


def test_file_managed_keys_reconciled_on_reseed(db):
    """Re-seeding with a changed YAML updates file-managed keys (auth_mode, saml_*).

    Regression: the original insert-if-absent seed left auth_mode stuck on its
    first value, so flipping the configmap to 'saml' never took effect.
    """
    from app.config_loader import seed_system_config

    # First boot: simple mode, empty SAML.
    with patch("app.config_loader.load_yaml_config", return_value=_yaml(mode="simple")):
        seed_system_config(db)
    assert json.loads(db.query(SystemConfig).filter_by(key="auth_mode").first().value) == "simple"

    # Configmap edited to SAML + real endpoints, pod restarts -> re-seed.
    with patch("app.config_loader.load_yaml_config", return_value=_yaml(
        mode="saml",
        entity_id="https://cviche.weill.cornell.edu/shibboleth",
        idp_metadata_url="https://login-proxy.weill.cornell.edu/idp/metadata.php",
    )):
        seed_system_config(db)

    assert json.loads(db.query(SystemConfig).filter_by(key="auth_mode").first().value) == "saml"
    assert json.loads(db.query(SystemConfig).filter_by(key="saml_entity_id").first().value) == \
        "https://cviche.weill.cornell.edu/shibboleth"
    assert json.loads(db.query(SystemConfig).filter_by(key="saml_idp_metadata_url").first().value) == \
        "https://login-proxy.weill.cornell.edu/idp/metadata.php"


def test_admin_managed_keys_not_clobbered_on_reseed(db):
    """Re-seeding must NOT overwrite admin-editable keys (allowed_users) -- admin
    edits via the UI survive redeploys."""
    from app.config_loader import seed_system_config

    with patch("app.config_loader.load_yaml_config", return_value=_yaml(allowed=["seed@example.com"])):
        seed_system_config(db)

    # Admin edits allowed_users at runtime (simulating PUT /api/admin/config).
    row = db.query(SystemConfig).filter_by(key="allowed_users").first()
    row.value = json.dumps(["admin-added@example.com"])
    db.commit()

    # Pod restarts with the original YAML -> re-seed must preserve the admin edit.
    with patch("app.config_loader.load_yaml_config", return_value=_yaml(allowed=["seed@example.com"])):
        seed_system_config(db)

    assert json.loads(db.query(SystemConfig).filter_by(key="allowed_users").first().value) == \
        ["admin-added@example.com"]


def test_ed_staff_group_seeded_from_yaml_and_empty_when_absent(db):
    """ed.staff_group reaches SystemConfig like admin_group does. A YAML with no
    staff_group (every existing deploy) seeds "" -- nobody is staff."""
    from app.config_loader import seed_system_config

    with patch("app.config_loader.load_yaml_config", return_value=_yaml()):
        seed_system_config(db)
    assert json.loads(db.query(SystemConfig).filter_by(key="ed_staff_group").first().value) == ""

    staff_dn = "cn=ITS:Library:CViche/staff-role,ou=application security,ou=groups,dc=weill,dc=cornell,dc=edu"
    with patch("app.config_loader.load_yaml_config",
               return_value={**_yaml(), "ed": {"staff_group": staff_dn}}):
        seed_system_config(db)
    assert json.loads(db.query(SystemConfig).filter_by(key="ed_staff_group").first().value) == staff_dn


# ---------------------------------------------------------------------------
# GET/PUT /api/admin/config: the admin-editable keys, their validation and
# the audit line, pinned before they move out of the route handlers (#335).
# ---------------------------------------------------------------------------

def _admin_config_call(client, db, method, body=None):
    """Call /api/admin/config as a real, committed admin row."""
    from types import SimpleNamespace

    from app.auth import require_admin
    from app.main import app

    boss = db.query(User).filter_by(email="boss@example.com").first()
    if boss is None:
        boss = User(email="boss@example.com", display_name="Boss", role="admin")
        db.add(boss)
        db.commit()
    acting = SimpleNamespace(id=boss.id, role="admin", email=boss.email)
    app.dependency_overrides[require_admin] = lambda: acting
    try:
        return getattr(client, method)("/api/admin/config", **({"json": body} if body is not None else {}))
    finally:
        app.dependency_overrides.pop(require_admin, None)


def _config_row(db, key):
    return db.query(SystemConfig).filter_by(key=key).first()


def test_admin_config_get_reads_each_key_and_defaults_a_missing_one(client, db, seed_simple_mode):
    for key in ("rate_limit_daily", "auth_mode"):
        db.delete(_config_row(db, key))
    db.commit()

    body = _admin_config_call(client, db, "get").json()

    assert body == {"allowed_users": ["test@example.com"], "admin_users": ["admin@example.com"],
                    "rate_limit_daily": 10, "rate_limit_monthly": 50, "consent_version": "1.0",
                    "auth_mode": "simple"}


@pytest.mark.parametrize("body, message", [
    ({"admin_users": []}, "At least one admin user is required."),
    ({"allowed_users": ["a@example.com"], "admin_users": ["b@example.com"]},
     "Admin user b@example.com must also be in the allowed users list."),
    # no allowed_users in the body: checked against the stored list
    ({"admin_users": ["nobody@example.com"]},
     "Admin user nobody@example.com must also be in the allowed users list."),
    ({"rate_limit_daily": 0}, "Daily rate limit must be positive."),
    ({"rate_limit_monthly": 0}, "Monthly rate limit must be positive."),
    # checked in field order: admin_users before the rate limits
    ({"admin_users": [], "rate_limit_daily": 0}, "At least one admin user is required."),
])
def test_admin_config_put_rejects_and_commits_nothing(client, db, seed_simple_mode, body, message):
    resp = _admin_config_call(client, db, "put", body)

    assert resp.status_code == 422
    assert resp.json()["detail"]["message"] == message
    db.rollback()  # what the request session's close does: only a commit survives
    assert json.loads(_config_row(db, "allowed_users").value) == ["test@example.com"]
    assert json.loads(_config_row(db, "admin_users").value) == ["admin@example.com"]


def test_admin_config_put_writes_changed_keys_audits_them_and_returns_the_config(
        client, db, seed_simple_mode, caplog):
    """Admin emails match the allowed list case-insensitively; a key whose value
    is unchanged is neither rewritten nor logged."""
    import logging

    with caplog.at_level(logging.INFO):
        resp = _admin_config_call(client, db, "put", {
            "allowed_users": ["admin@example.com", "new@example.com"],
            "admin_users": ["ADMIN@Example.com"],
            "rate_limit_daily": 10,
            "rate_limit_monthly": 60,
        })

    assert resp.status_code == 200
    assert resp.json() == {"allowed_users": ["admin@example.com", "new@example.com"],
                           "admin_users": ["ADMIN@Example.com"], "rate_limit_daily": 10,
                           "rate_limit_monthly": 60, "consent_version": "1.0", "auth_mode": "simple"}
    boss_id = db.query(User).filter_by(email="boss@example.com").one().id
    db.rollback()  # only a committed write survives
    assert _config_row(db, "allowed_users").updated_by == boss_id
    assert _config_row(db, "rate_limit_monthly").updated_by == boss_id
    assert _config_row(db, "rate_limit_daily").updated_by is None
    audit = [r.getMessage() for r in caplog.records if r.getMessage().startswith("admin_config_changed")]
    assert audit == [
        "admin_config_changed: admin=boss@example.com changes="
        '{"allowed_users": {"old": ["test@example.com"], "new": ["admin@example.com", "new@example.com"]}, '
        '"admin_users": {"old": ["admin@example.com"], "new": ["ADMIN@Example.com"]}, '
        '"rate_limit_monthly": {"old": 50, "new": 60}}'
    ]


def test_admin_config_put_checks_admins_against_the_stored_allowed_list(client, db, seed_simple_mode):
    resp = _admin_config_call(client, db, "put", {"admin_users": ["TEST@example.com"]})

    assert resp.status_code == 200
    assert resp.json()["admin_users"] == ["TEST@example.com"]


def test_admin_config_put_creates_a_missing_key_and_skips_the_log_when_nothing_changed(
        client, db, seed_simple_mode, caplog):
    import logging

    db.delete(_config_row(db, "consent_version"))
    db.commit()

    with caplog.at_level(logging.INFO):
        created = _admin_config_call(client, db, "put", {"consent_version": "2.0"})
        unchanged = _admin_config_call(client, db, "put", {"consent_version": "2.0", "rate_limit_daily": 10})

    assert (created.status_code, unchanged.status_code) == (200, 200)
    db.expire_all()
    row = _config_row(db, "consent_version")
    assert (json.loads(row.value), row.updated_by is not None) == ("2.0", True)
    audit = [r.getMessage() for r in caplog.records if r.getMessage().startswith("admin_config_changed")]
    assert audit == ['admin_config_changed: admin=boss@example.com changes={"consent_version": {"old": null, "new": "2.0"}}']
