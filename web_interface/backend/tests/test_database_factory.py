"""Coverage for the RDS TLS verification path and the explicit DB auth mode.

PyMySQL's ssl dict: a truthy `ssl={'ssl': True}` with no `ca` key resolves to
CERT_NONE / check_hostname=False -- encrypted but unauthenticated. Setting
`ca` to the vendored RDS CA bundle makes PyMySQL verify the server
certificate and hostname instead (confirmed against pymysql==1.1.1 --
the version this repo pins in requirements.txt and
web_interface/backend/requirements.txt -- `Connection._create_ssl_ctx`,
pymysql/connections.py:370-390: `hasnoca = ca is None and capath is None`;
with `ca` set, `verify_mode` defaults to `CERT_REQUIRED` and
`check_hostname` defaults to `True`).

`DB_AUTH_MODE` (#778) is the other half: the branch used to be selected by
`DB_PASSWORD` being truthy, so a stray password in a deployed environment
silently downgraded IAM + verified TLS to password auth with no TLS, and
bypassed the CA guard along with it.
"""
import hashlib
import re
from pathlib import Path

import pytest

from app import database_factory
from app.database_factory import (
    DB_AUTH_MODE_ENV,
    DB_AUTH_MODE_IAM,
    DB_AUTH_MODE_PASSWORD,
    DB_CONNECT_TIMEOUT_SECONDS,
    DB_MAX_OVERFLOW,
    DB_POOL_SIZE,
    DB_POOL_TIMEOUT_SECONDS,
    DB_PASSWORD_ENV,
    DEFAULT_DB_AUTH_MODE,
    RDS_CA_PATH,
    RDS_CA_SHA256,
    _iam_connect_kwargs,
    _resolve_db_auth_mode,
    create_cviche_engine,
)


@pytest.fixture
def capture_engine(monkeypatch):
    """Capture the URL and engine kwargs create_cviche_engine compiles.

    Recording the real create_engine call is how these tests reach the
    `creator` callable itself rather than only the pure helpers -- a helper
    test cannot see whether the wire actually calls it.
    """
    captured = {}
    real_create_engine = database_factory.create_engine

    def recording_create_engine(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return real_create_engine(url, **kwargs)

    monkeypatch.setattr(database_factory, "create_engine", recording_create_engine)
    return captured


@pytest.fixture
def record_pymysql_connect(monkeypatch):
    """Replace pymysql.connect with a recorder so `creator` can be invoked."""
    calls = []

    def fake_connect(**kwargs):
        calls.append(kwargs)
        return object()

    monkeypatch.setattr(database_factory.pymysql, "connect", fake_connect)
    return calls


def _iam_env(monkeypatch):
    """The deployed shape: no DB_AUTH_MODE, no DB_PASSWORD."""
    monkeypatch.delenv(DB_AUTH_MODE_ENV, raising=False)
    monkeypatch.delenv(DB_PASSWORD_ENV, raising=False)


def _password_env(monkeypatch, password="local-dev-password"):
    monkeypatch.setenv(DB_AUTH_MODE_ENV, DB_AUTH_MODE_PASSWORD)
    monkeypatch.setenv(DB_PASSWORD_ENV, password)


# --- TLS / CA bundle (#689) -------------------------------------------------

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


def test_rds_ca_bundle_matches_pinned_checksum():
    """The committed bundle hashes to RDS_CA_SHA256.

    Re-vendoring the PEM without updating the constant (or updating the
    constant without re-vendoring) fails here rather than at pod boot.
    """
    actual = hashlib.sha256(RDS_CA_PATH.read_bytes()).hexdigest()
    assert actual == RDS_CA_SHA256


def test_engine_construction_fails_closed_on_missing_ca_bundle(monkeypatch):
    """The IAM shape + a missing CA bundle must raise at factory time, not
    silently degrade to an unauthenticated connection at first use."""
    _iam_env(monkeypatch)
    monkeypatch.setattr(
        database_factory,
        "RDS_CA_PATH",
        RDS_CA_PATH.parent / "does-not-exist.pem",
    )
    with pytest.raises(RuntimeError, match="does-not-exist.pem"):
        create_cviche_engine(
            db_host="localhost", db_port="3306", db_name="test", db_user="test"
        )


def test_engine_construction_fails_closed_on_tampered_ca_bundle(monkeypatch, tmp_path):
    """A bundle that exists but does not match RDS_CA_SHA256 refuses to boot.

    File-exists is not integrity: without the checksum the app would verify
    RDS server certificates against whatever PEM is on disk.
    """
    tampered = tmp_path / "rds-global-bundle.pem"
    tampered.write_bytes(RDS_CA_PATH.read_bytes() + b"\n# an extra trust anchor\n")
    _iam_env(monkeypatch)
    monkeypatch.setattr(database_factory, "RDS_CA_PATH", tampered)
    with pytest.raises(RuntimeError, match="does not match its pinned sha256"):
        create_cviche_engine(
            db_host="localhost", db_port="3306", db_name="test", db_user="test"
        )


def test_password_mode_is_unaffected_by_the_ca_guard(monkeypatch):
    """Local compose (DB_AUTH_MODE=password) builds an engine even with no CA
    bundle on disk -- the guard only applies to the IAM branch."""
    _password_env(monkeypatch)
    monkeypatch.setattr(
        database_factory,
        "RDS_CA_PATH",
        RDS_CA_PATH.parent / "does-not-exist.pem",
    )
    # Engine construction is lazy (the creator only runs on connect), so this
    # never touches the network.
    engine = create_cviche_engine(
        db_host="localhost", db_port="3306", db_name="test", db_user="test"
    )
    assert engine is not None


# --- Explicit auth mode (#778.2) --------------------------------------------

def test_auth_mode_defaults_to_iam_when_unset(monkeypatch):
    _iam_env(monkeypatch)
    assert _resolve_db_auth_mode() == DB_AUTH_MODE_IAM
    assert DEFAULT_DB_AUTH_MODE == DB_AUTH_MODE_IAM


def test_auth_mode_normalises_case_and_whitespace(monkeypatch):
    monkeypatch.setenv(DB_AUTH_MODE_ENV, "  PassWord \n")
    assert _resolve_db_auth_mode() == DB_AUTH_MODE_PASSWORD


@pytest.mark.parametrize("bad", ["iamm", "none", "", "   ", "IAM_AUTH"])
def test_unknown_auth_mode_fails_closed(monkeypatch, bad):
    """An unrecognised (or explicitly empty) mode raises and names the
    allowed set, rather than falling through to a branch by accident."""
    monkeypatch.setenv(DB_AUTH_MODE_ENV, bad)
    with pytest.raises(RuntimeError, match="not a known database auth mode"):
        _resolve_db_auth_mode()


def test_password_mode_without_password_raises(monkeypatch):
    """DB_AUTH_MODE=password with no DB_PASSWORD must not connect anonymously."""
    monkeypatch.setenv(DB_AUTH_MODE_ENV, DB_AUTH_MODE_PASSWORD)
    monkeypatch.delenv(DB_PASSWORD_ENV, raising=False)
    with pytest.raises(RuntimeError, match="unset or empty"):
        create_cviche_engine(
            db_host="localhost", db_port="3306", db_name="test", db_user="test"
        )


def test_password_mode_with_empty_password_raises(monkeypatch):
    monkeypatch.setenv(DB_AUTH_MODE_ENV, DB_AUTH_MODE_PASSWORD)
    monkeypatch.setenv(DB_PASSWORD_ENV, "")
    with pytest.raises(RuntimeError, match="unset or empty"):
        create_cviche_engine(
            db_host="localhost", db_port="3306", db_name="test", db_user="test"
        )


def test_stray_db_password_does_not_downgrade_to_password_auth(
    monkeypatch, capture_engine, record_pymysql_connect
):
    """THE security regression the reviewer described on #778.

    A DB_PASSWORD that leaks into a deployed environment (an operator export,
    a copied ConfigMap) must NOT flip the engine to password auth with no TLS.
    With DB_AUTH_MODE unset the connect must still be the IAM shape: an
    RDS auth token, the CA bundle, and hostname verification.
    """
    monkeypatch.delenv(DB_AUTH_MODE_ENV, raising=False)
    monkeypatch.setenv(DB_PASSWORD_ENV, "leaked-into-prod")

    class _FakeRds:
        def generate_db_auth_token(self, **kwargs):
            return "iam-token"

    monkeypatch.setattr(database_factory.boto3, "client", lambda *a, **k: _FakeRds())
    create_cviche_engine(
        db_host="db.example.rds.amazonaws.com", db_port="3306",
        db_name="cviche", db_user="cviche_app_user",
    )
    capture_engine["kwargs"]["creator"]()

    assert len(record_pymysql_connect) == 1
    connect_kwargs = record_pymysql_connect[0]
    assert connect_kwargs["password"] == "iam-token"
    assert connect_kwargs["password"] != "leaked-into-prod"
    assert connect_kwargs["ssl"] == {"ca": str(RDS_CA_PATH), "check_hostname": True}


# --- URL construction (#778.1) ----------------------------------------------

def test_url_escapes_special_characters_in_user_and_name(monkeypatch, capture_engine):
    """A db_user or db_name containing URL-special characters round-trips.

    String concatenation put these straight into the URL, where '@' and '/'
    re-parse as host and path separators.
    """
    _iam_env(monkeypatch)
    create_cviche_engine(
        db_host="db.example.rds.amazonaws.com",
        db_port="3306",
        db_name="cviche/db%name",
        db_user="user@corp:role",
    )
    url = capture_engine["url"]
    assert url.username == "user@corp:role"
    assert url.database == "cviche/db%name"
    assert url.host == "db.example.rds.amazonaws.com"
    assert url.port == 3306
    assert url.drivername == "mysql+pymysql"
    # The credential lives in the `creator` callable, never in the URL.
    assert url.password is None


def test_url_carries_no_password_in_password_mode(monkeypatch, capture_engine):
    _password_env(monkeypatch, password="local-dev-password")
    create_cviche_engine(
        db_host="db", db_port="3306", db_name="cviche", db_user="root"
    )
    assert capture_engine["url"].password is None
    assert "local-dev-password" not in capture_engine["url"].render_as_string(
        hide_password=False
    )


# --- One boto3 client per engine (#778.3) -----------------------------------

def test_rds_client_is_built_once_across_many_connects(
    monkeypatch, capture_engine, record_pymysql_connect
):
    """boto3.client('rds', ...) is constructed once per engine, not per connect."""
    _iam_env(monkeypatch)
    constructed = []

    class _FakeRds:
        def generate_db_auth_token(self, **kwargs):
            return "iam-token"

    def fake_client(*args, **kwargs):
        constructed.append((args, kwargs))
        return _FakeRds()

    monkeypatch.setattr(database_factory.boto3, "client", fake_client)
    create_cviche_engine(
        db_host="db.example.rds.amazonaws.com", db_port="3306",
        db_name="cviche", db_user="cviche_app_user",
    )
    creator = capture_engine["kwargs"]["creator"]
    for _ in range(5):
        creator()

    assert len(record_pymysql_connect) == 5
    assert len(constructed) == 1


def test_rds_client_is_not_built_at_factory_time(monkeypatch, capture_engine):
    """Compiling the engine acquires no AWS dependency -- the client is lazy."""
    _iam_env(monkeypatch)

    def exploding_client(*args, **kwargs):
        raise AssertionError("boto3.client must not be called at factory time")

    monkeypatch.setattr(database_factory.boto3, "client", exploding_client)
    engine = create_cviche_engine(
        db_host="db.example.rds.amazonaws.com", db_port="3306",
        db_name="cviche", db_user="cviche_app_user",
    )
    assert engine is not None


def test_password_mode_never_builds_an_rds_client(
    monkeypatch, capture_engine, record_pymysql_connect
):
    """A password-mode process with no AWS credentials never touches boto3."""
    _password_env(monkeypatch)

    def exploding_client(*args, **kwargs):
        raise AssertionError("boto3.client must not be called in password mode")

    monkeypatch.setattr(database_factory.boto3, "client", exploding_client)
    create_cviche_engine(
        db_host="db", db_port="3306", db_name="cviche", db_user="root"
    )
    capture_engine["kwargs"]["creator"]()
    assert record_pymysql_connect[0]["password"] == "local-dev-password"


# --- Bounded connect (#778.5) -----------------------------------------------

def test_iam_connect_is_time_bounded(
    monkeypatch, capture_engine, record_pymysql_connect
):
    """The IAM connect passes connect_timeout, so a black-holed RDS endpoint
    fails before the readiness probe's own 5s deadline instead of hanging."""
    _iam_env(monkeypatch)

    class _FakeRds:
        def generate_db_auth_token(self, **kwargs):
            return "iam-token"

    monkeypatch.setattr(database_factory.boto3, "client", lambda *a, **k: _FakeRds())
    create_cviche_engine(
        db_host="db.example.rds.amazonaws.com", db_port="3306",
        db_name="cviche", db_user="cviche_app_user",
    )
    capture_engine["kwargs"]["creator"]()
    assert record_pymysql_connect[0]["connect_timeout"] == DB_CONNECT_TIMEOUT_SECONDS
    assert 0 < DB_CONNECT_TIMEOUT_SECONDS < 5


def test_password_connect_is_time_bounded(
    monkeypatch, capture_engine, record_pymysql_connect
):
    """The password branch is bounded too -- both branches or neither."""
    _password_env(monkeypatch)
    create_cviche_engine(
        db_host="db", db_port="3306", db_name="cviche", db_user="root"
    )
    capture_engine["kwargs"]["creator"]()
    assert record_pymysql_connect[0]["connect_timeout"] == DB_CONNECT_TIMEOUT_SECONDS


def test_engine_pool_sizing_is_explicit(monkeypatch, capture_engine):
    """pool_size / max_overflow / pool_timeout are passed to create_engine
    and reach the live pool, not left to SQLAlchemy defaults (#784)."""
    _password_env(monkeypatch)
    engine = create_cviche_engine(
        db_host="db", db_port="3306", db_name="cviche", db_user="root"
    )
    kwargs = capture_engine["kwargs"]
    assert kwargs["pool_size"] == DB_POOL_SIZE
    assert kwargs["max_overflow"] == DB_MAX_OVERFLOW
    assert kwargs["pool_timeout"] == DB_POOL_TIMEOUT_SECONDS
    assert engine.pool.size() == DB_POOL_SIZE
    assert engine.pool._max_overflow == DB_MAX_OVERFLOW
    assert engine.pool._timeout == DB_POOL_TIMEOUT_SECONDS


# RDS max_connections on the production instance, measured 2026-09-29 (#784).
RDS_MAX_CONNECTIONS = 318
_HPA_PATCHES = sorted(
    (Path(__file__).resolve().parents[3] / "k8s" / "overlays").glob("*/hpa-patch.yaml")
)


def test_pool_budget_fits_rds_max_connections():
    """Every overlay's HPA maxReplicas x 1 uvicorn worker x (pool + overflow)
    fits under RDS max_connections (#784). Reads the real manifests, so raising
    maxReplicas or a pool value without redoing the budget fails here."""
    assert _HPA_PATCHES, "no k8s/overlays/*/hpa-patch.yaml found"
    for patch_file in _HPA_PATCHES:
        max_replicas = int(re.search(r"maxReplicas:\s*(\d+)", patch_file.read_text()).group(1))
        worst_case = max_replicas * (DB_POOL_SIZE + DB_MAX_OVERFLOW)
        assert worst_case <= RDS_MAX_CONNECTIONS, f"{patch_file}: {worst_case} > {RDS_MAX_CONNECTIONS}"
