"""Tests for MODE-01: auth mode config endpoint, mode guard, and auth_method column."""
import json
from unittest.mock import patch

from app.models import User, SystemConfig


def test_config_endpoint_simple(client, seed_simple_mode):
    """GET /api/auth/config returns {"mode": "simple"} when auth_mode is simple."""
    response = client.get("/api/auth/config")
    assert response.status_code == 200
    assert response.json() == {"mode": "simple"}


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
