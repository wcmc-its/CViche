"""Coverage for the RDS TLS verification path (#689).

PyMySQL's ssl dict: a truthy `ssl={'ssl': True}` with no `ca` key resolves to
CERT_NONE / check_hostname=False -- encrypted but unauthenticated. Setting
`ca` to the vendored RDS CA bundle makes PyMySQL verify the server
certificate and hostname instead (confirmed against pymysql==1.1.1 --
the version this repo pins in requirements.txt and
web_interface/backend/requirements.txt -- `Connection._create_ssl_ctx`,
pymysql/connections.py:370-390: `hasnoca = ca is None and capath is None`;
with `ca` set, `verify_mode` defaults to `CERT_REQUIRED` and
`check_hostname` defaults to `True`).
"""
import pytest

from app.database_factory import (
    RDS_CA_PATH,
    _iam_connect_kwargs,
    create_cviche_engine,
)


def test_iam_kwargs_verify_ca_and_hostname():
    """The IAM branch's ssl dict names the CA bundle and enables hostname check."""
    kwargs = _iam_connect_kwargs(
        host="db.example.rds.amazonaws.com",
        port=3306,
        user="cviche_app_user",
        token="fake-iam-token",
        name="cviche",
    )
    assert kwargs["ssl"] == {"ca": str(RDS_CA_PATH), "check_hostname": True}
    assert kwargs["host"] == "db.example.rds.amazonaws.com"
    assert kwargs["port"] == 3306
    assert kwargs["user"] == "cviche_app_user"
    assert kwargs["password"] == "fake-iam-token"
    assert kwargs["database"] == "cviche"


def test_rds_ca_bundle_is_vendored_in_the_repo():
    """The bundle the IAM branch points at actually exists on disk."""
    assert RDS_CA_PATH.is_file()


def test_engine_construction_fails_closed_on_missing_ca_bundle(monkeypatch):
    """No DB_PASSWORD (the deployed/IAM shape) + a missing CA bundle must
    raise at factory time, not silently degrade to an unauthenticated
    connection at first use."""
    monkeypatch.delenv("DB_PASSWORD", raising=False)
    monkeypatch.setattr(
        "app.database_factory.RDS_CA_PATH",
        RDS_CA_PATH.parent / "does-not-exist.pem",
    )
    with pytest.raises(RuntimeError, match="does-not-exist.pem"):
        create_cviche_engine(
            db_host="localhost", db_port="3306", db_name="test", db_user="test"
        )


def test_password_auth_path_is_unaffected_by_the_ca_guard(monkeypatch):
    """Local compose (DB_PASSWORD set) builds an engine even with no CA bundle
    on disk -- the guard only applies to the IAM (no-password) branch."""
    monkeypatch.setenv("DB_PASSWORD", "local-dev-password")
    monkeypatch.setattr(
        "app.database_factory.RDS_CA_PATH",
        RDS_CA_PATH.parent / "does-not-exist.pem",
    )
    # Engine construction is lazy (the creator lambda only runs on connect),
    # so this never touches the network.
    engine = create_cviche_engine(
        db_host="localhost", db_port="3306", db_name="test", db_user="test"
    )
    assert engine is not None
