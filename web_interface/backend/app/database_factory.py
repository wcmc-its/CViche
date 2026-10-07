import hashlib
import logging
import os
import threading
from pathlib import Path

import boto3
import pymysql
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

from app.config_loader import get_config

logger = logging.getLogger(__name__)

# Which credential the engine authenticates with. Set EXPLICITLY, never
# inferred from which environment variables happen to be present: until #778
# the branch was chosen by `DB_PASSWORD` being truthy, so a stray DB_PASSWORD
# in a deployed environment silently downgraded RDS IAM + verified TLS to
# password auth with no TLS at all -- and skipped the CA-bundle guard below
# with it, because that guard keyed off the same absent password.
#
# This is an ALLOWLIST, the same shape as app/main.py's SECURE_AUTH_MODES /
# _guard_deployed_auth_mode: any value not listed here -- a typo, an
# empty-after-strip value from a ConfigMap key that failed to render, a future
# mode nobody wired up in this file -- fails closed rather than falling
# through to whichever branch happens to be last.
DB_AUTH_MODE_ENV = "DB_AUTH_MODE"
DB_PASSWORD_ENV = "DB_PASSWORD"
DB_AUTH_MODE_IAM = "iam"
DB_AUTH_MODE_PASSWORD = "password"
DB_AUTH_MODES = frozenset({DB_AUTH_MODE_IAM, DB_AUTH_MODE_PASSWORD})
# The default is the SECURE mode: an environment that says nothing gets IAM +
# verified TLS, and only an operator who deliberately asks for it gets the
# local-compose password path.
DEFAULT_DB_AUTH_MODE = DB_AUTH_MODE_IAM

_UNKNOWN_AUTH_MODE_MSG = (
    "[SECURITY] {env}={value!r} is not a known database auth mode (allowed: "
    "{allowed}). Refusing to start rather than guess which credential to use. "
    "Leave {env} unset for the default ({default}: RDS IAM token over "
    "certificate-verified TLS), or set it to '{password_mode}' for the local "
    "compose stack in web_interface/docker-compose.yml."
)

_PASSWORD_MODE_WITHOUT_PASSWORD_MSG = (
    "{env}={password_mode} but {password_env} is unset or empty: refusing to "
    "open a database connection with no credential. Set {password_env}, or "
    "unset {env} to use the default ({default}) RDS IAM path."
)

# Vendored AWS RDS CA bundle (web_interface/backend/rds_ca/rds-global-bundle.pem).
# The IAM-auth path below (every deployed environment) verifies the RDS server
# certificate against this file instead of trusting any CA. See
# rds_ca/README.md for source URL, sha256 and fetch date. This directory is
# NOT web_interface/backend/certs/ -- that one is .gitignore'd (auto-generated
# SAML SP private keys); a public CA bundle must not live in the secrets dir.
RDS_CA_PATH = Path(__file__).resolve().parent.parent / "rds_ca" / "rds-global-bundle.pem"

# sha256 of the committed bundle. The file merely existing says nothing about
# what is in it, so without this the app trusts whatever PEM happens to sit at
# RDS_CA_PATH -- the README's checksum is documentation, not enforcement
# (mrj4001 review on #778). Re-vendoring the bundle means updating BOTH this
# constant and rds_ca/README.md; test_rds_ca_bundle_matches_pinned_checksum
# fails the build if they drift apart.
RDS_CA_SHA256 = "e5bb2084ccf45087bda1c9bffdea0eb15ee67f0b91646106e466714f9de3c7e3"

# [SECURITY] a truthy PyMySQL `ssl` dict with no `ca` key falls back to
# CERT_NONE / check_hostname=False -- encrypted but unauthenticated. Refusing
# to boot on a missing bundle is the same fail-closed shape as app/main.py's
# SECURE_AUTH_MODES guard: _guard_deployed_auth_mode raises RuntimeError at
# app/main.py:229 when a deployed instance's auth mode isn't secure (#689).
_MISSING_CA_BUNDLE_MSG = (
    "[SECURITY] RDS CA bundle not found at {path}: the IAM-auth database path "
    "cannot verify the RDS server certificate without it. Refusing to start "
    "rather than silently fall back to an unauthenticated (CERT_NONE) TLS "
    "connection. Re-vendor the bundle from "
    "https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem."
)

_TAMPERED_CA_BUNDLE_MSG = (
    "[SECURITY] RDS CA bundle at {path} does not match its pinned sha256 "
    "(expected {expected}, found {actual}): the IAM-auth database path would "
    "verify RDS server certificates against an unknown trust anchor. Refusing "
    "to start. If AWS rotated the RDS CAs, re-fetch "
    "https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem and "
    "update RDS_CA_SHA256 here and the checksum in rds_ca/README.md together."
)

# Bound the TCP connect and TLS handshake. PyMySQL blocks on the socket
# indefinitely without this, so a black-holed RDS endpoint -- a security-group
# change, DNS still resolving to a dead ENI -- pins a worker thread forever
# instead of failing. Certificate verification (#689) and the explicit auth
# mode above both add new ways a *first* connect can fail, so it has to fail in
# bounded time.
#
# 3s is picked against the readiness probe in k8s/base/backend/deployments.yaml
# (readinessProbe timeoutSeconds: 5, periodSeconds: 10): /readyz runs the DB
# `SELECT 1` first (app/main.py:415), so failing the connect at 3s leaves the
# handler time to return a 503 whose body names the failing `db` check --
# diagnosable in the pod logs -- instead of letting the kubelet time the probe
# out with no explanation. It is also well under the startupProbe's 10s and the
# livenessProbe's 15s periods, and roughly 30x the cost of an in-VPC connect
# plus handshake, so it cannot trip on a healthy RDS.
DB_CONNECT_TIMEOUT_SECONDS = 3

# Connection-pool sizing (#784). Set explicitly so a SQLAlchemy default change
# cannot silently move the connection budget; the values equal the defaults
# (5 / 10 / 30) that production has been running.
#
# Budget (one engine per uvicorn process; docker-entrypoint.sh passes no
# --workers and no k8s manifest overrides it, so 1 worker per pod):
#   maxReplicas (4, k8s/overlays/{dev,prod}/hpa-patch.yaml) x 1 worker
#   x (POOL_SIZE + MAX_OVERFLOW = 15) = 60 connections worst case
# against RDS max_connections = 318, leaving 258 for the db-migration init
# container / alembic, admin sessions and rolling-deploy surge pods.
# Measured 2026-09-29 on the production RDS instance (namespace cviche-dev):
# SHOW VARIABLES LIKE 'max_connections' = 318, Max_used_connections = 15,
# Threads_connected = 8. Raising any value or the worker count means
# re-doing this arithmetic.
DB_POOL_SIZE = 5
DB_MAX_OVERFLOW = 10
DB_POOL_TIMEOUT_SECONDS = 30


def _resolve_db_auth_mode() -> str:
    """Resolve DB_AUTH_MODE to a member of DB_AUTH_MODES, or raise.

    Unset means DEFAULT_DB_AUTH_MODE. Anything else must be in the allowlist
    after strip+lower -- values arrive from ConfigMaps and shell exports with
    stray case and whitespace ("IAM", "password "), and an exact-match compare
    would send those down an unintended branch. An explicitly-set but empty
    value is a broken config, not a request for the default, so it raises.

    DB_PASSWORD has no influence here by design: that inference is exactly the
    silent security downgrade this replaces.
    """
    raw = os.environ.get(DB_AUTH_MODE_ENV)
    if raw is None:
        return DEFAULT_DB_AUTH_MODE
    mode = raw.strip().lower()
    if mode not in DB_AUTH_MODES:
        raise RuntimeError(
            _UNKNOWN_AUTH_MODE_MSG.format(
                env=DB_AUTH_MODE_ENV,
                value=raw,
                allowed=", ".join(sorted(DB_AUTH_MODES)),
                default=DEFAULT_DB_AUTH_MODE,
                password_mode=DB_AUTH_MODE_PASSWORD,
            )
        )
    return mode


def _verify_rds_ca_bundle() -> None:
    """Fail closed unless the vendored CA bundle is present AND unmodified.

    Both halves refuse to boot for the same reason: on the IAM path the bundle
    is the only trust anchor for the RDS server certificate, so a missing file
    degrades TLS to unauthenticated (see _MISSING_CA_BUNDLE_MSG) and an
    unexpected file means verifying against something nobody reviewed.
    """
    if not RDS_CA_PATH.is_file():
        raise RuntimeError(_MISSING_CA_BUNDLE_MSG.format(path=RDS_CA_PATH))
    actual = hashlib.sha256(RDS_CA_PATH.read_bytes()).hexdigest()
    if actual != RDS_CA_SHA256:
        raise RuntimeError(
            _TAMPERED_CA_BUNDLE_MSG.format(
                path=RDS_CA_PATH, expected=RDS_CA_SHA256, actual=actual
            )
        )


def _require_db_password() -> str:
    """Return DB_PASSWORD for the password mode, or raise if it is unusable.

    Local dev (docker compose) runs MariaDB with password auth and no TLS, so
    the IAM-token path cannot reach it. An operator who asked for password mode
    and supplied no password gets an error, not a credential-less connection
    attempt.
    """
    password = os.environ.get(DB_PASSWORD_ENV)
    if not password:
        raise RuntimeError(
            _PASSWORD_MODE_WITHOUT_PASSWORD_MSG.format(
                env=DB_AUTH_MODE_ENV,
                password_mode=DB_AUTH_MODE_PASSWORD,
                password_env=DB_PASSWORD_ENV,
                default=DEFAULT_DB_AUTH_MODE,
            )
        )
    return password


def _iam_connect_kwargs(host: str, port: int, user: str, token: str, name: str) -> dict:
    """Build the pymysql.connect kwargs for the IAM-token (deployed) path.

    Pure -- no network, no boto3 call -- so it is unit-testable without
    touching AWS or a live database. `ssl={'ca': ..., 'check_hostname': True}`
    makes PyMySQL verify the server certificate against RDS_CA_PATH and check
    the hostname (PyMySQL's `_create_ssl_ctx`: a `ca` key selects
    `CERT_REQUIRED` and defaults `check_hostname` to True); the previous
    `ssl={'ssl': True}` shape had no `ca` key, which resolves to `CERT_NONE`
    and `check_hostname=False` -- encrypted but unauthenticated (#689).
    """
    return {
        "host": host,
        "port": port,
        "user": user,
        "password": token,
        "database": name,
        "ssl": {"ca": str(RDS_CA_PATH), "check_hostname": True},
        "connect_timeout": DB_CONNECT_TIMEOUT_SECONDS,
    }


def create_cviche_engine(db_host: str, db_port: str, db_name: str, db_user: str):
    """
    Pure engine compiler. Accepts configuration values directly,
    eliminating pathing or duplicate file-reading bugs.
    """
    auth_mode = _resolve_db_auth_mode()

    # NOTE: this module is imported both by the app (root logger at INFO) and by
    # alembic (root at WARNING per alembic.ini), so this line shows in the app
    # logs and is filtered during migrations -- which is fine for a diagnostic.
    logger.info(
        "DB factory: compiling engine -> host=%s port=%s user=%s auth_mode=%s",
        db_host, db_port, db_user, auth_mode,
    )

    if not all([db_host, db_port, db_name, db_user]):
        raise RuntimeError(
            f"Database factory received incomplete configurations! "
            f"Given: HOST='{db_host}', PORT='{db_port}', USER='{db_user}', NAME='{db_name}'"
        )

    port = int(db_port)
    db_password = None
    if auth_mode == DB_AUTH_MODE_PASSWORD:
        db_password = _require_db_password()
    if auth_mode == DB_AUTH_MODE_IAM:
        # Fail loud at factory time, not first-connect time: a missing or
        # swapped CA bundle must never silently degrade to an unauthenticated
        # TLS connection. Keyed off the resolved mode, so a stray DB_PASSWORD
        # can no longer route around it.
        _verify_rds_ca_bundle()

    # URL.create() percent-escapes each component instead of splicing it into a
    # string, so a db_user or db_name containing '@', ':', '/' or '%' can no
    # longer corrupt (or silently re-parse) the URL. There is still no password
    # in the URL on either branch -- the `creator` callable owns the credential
    # -- and that stays true here.
    database_url = URL.create(
        "mysql+pymysql",
        username=db_user,
        host=db_host,
        port=port,
        database=db_name,
    )

    aws_region = os.environ.get("AWS_REGION", "us-east-1")
    # One RDS client for the life of the engine, not one per pool connect:
    # boto3.client() resolves endpoints and builds a request signer, which is
    # pure overhead repeated on every connect (mrj4001 review on #778).
    # Constructed lazily on the first IAM connect so password-mode processes --
    # local compose, the test suite -- never build one, and merely calling this
    # factory acquires no AWS dependency. Credentials still resolve through the
    # default boto3 provider chain (IRSA in EKS), unchanged.
    rds_client = None
    rds_client_lock = threading.Lock()

    def _rds_auth_token() -> str:
        nonlocal rds_client
        if rds_client is None:
            # boto3 client *construction* is not thread-safe and the pool can
            # open connections from several threads at once; the built client
            # is safe to share.
            with rds_client_lock:
                if rds_client is None:
                    rds_client = boto3.client("rds", region_name=aws_region)
        return rds_client.generate_db_auth_token(
            DBHostname=db_host, Port=port, DBUsername=db_user, Region=aws_region
        )

    def _connect():
        if auth_mode == DB_AUTH_MODE_PASSWORD:
            return pymysql.connect(
                host=db_host, port=port, user=db_user,
                password=db_password, database=db_name,
                connect_timeout=DB_CONNECT_TIMEOUT_SECONDS,
            )
        return pymysql.connect(
            **_iam_connect_kwargs(db_host, port, db_user, _rds_auth_token(), db_name)
        )

    engine_kwargs = {
        "pool_pre_ping": True,
        # Recycle pooled connections after 1800s (30 min) so a connection is never
        # reused after RDS wait_timeout or a load balancer has silently dropped it.
        # Comfortably under RDS's default 8h wait_timeout; pool_pre_ping is the
        # backstop for anything that dies sooner.
        "pool_recycle": 1800,
        "pool_size": DB_POOL_SIZE,
        "max_overflow": DB_MAX_OVERFLOW,
        "pool_timeout": DB_POOL_TIMEOUT_SECONDS,
        "creator": _connect,
    }

    return create_engine(database_url, **engine_kwargs)
