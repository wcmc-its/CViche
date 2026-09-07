import logging
import os
import boto3
import pymysql
from pathlib import Path
from sqlalchemy import create_engine
from app.config_loader import get_config

logger = logging.getLogger(__name__)

# Vendored AWS RDS CA bundle (web_interface/backend/rds_ca/rds-global-bundle.pem).
# The IAM-auth path below (every deployed environment) verifies the RDS server
# certificate against this file instead of trusting any CA. See
# rds_ca/README.md for source URL, sha256 and fetch date. This directory is
# NOT web_interface/backend/certs/ -- that one is .gitignore'd (auto-generated
# SAML SP private keys), and a public CA bundle needs to stay force-tracked.
RDS_CA_PATH = Path(__file__).resolve().parent.parent / "rds_ca" / "rds-global-bundle.pem"

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
    }


def create_cviche_engine(db_host: str, db_port: str, db_name: str, db_user: str):
    """
    Pure engine compiler. Accepts configuration values directly,
    eliminating pathing or duplicate file-reading bugs.
    """
    # NOTE: this module is imported both by the app (root logger at INFO) and by
    # alembic (root at WARNING per alembic.ini), so this line shows in the app
    # logs and is filtered during migrations -- which is fine for a diagnostic.
    logger.info("DB factory: compiling engine -> host=%s port=%s user=%s", db_host, db_port, db_user)

    if not all([db_host, db_port, db_name, db_user]):
        raise RuntimeError(
            f"Database factory received incomplete configurations! "
            f"Given: HOST='{db_host}', PORT='{db_port}', USER='{db_user}', NAME='{db_name}'"
        )

    DATABASE_URL = f"mysql+pymysql://{db_user}@{db_host}:{db_port}/{db_name}"
    aws_region = os.environ.get("AWS_REGION", "us-east-1")

    # Local dev (docker compose) runs MariaDB with password auth and no TLS, so
    # the IAM-token path below cannot reach it. Setting DB_PASSWORD selects
    # password auth; deployed environments leave it unset and are unaffected.
    db_password = os.environ.get("DB_PASSWORD")

    # Fail loud at factory time (not first-connect time) on the IAM branch --
    # a missing CA bundle must never silently degrade to an unauthenticated
    # TLS connection. Local compose (DB_PASSWORD set) is unaffected.
    if not db_password and not RDS_CA_PATH.is_file():
        raise RuntimeError(_MISSING_CA_BUNDLE_MSG.format(path=RDS_CA_PATH))

    def _connect():
        if db_password:
            return pymysql.connect(
                host=db_host, port=int(db_port), user=db_user,
                password=db_password, database=db_name,
            )
        token = boto3.client('rds', region_name=aws_region).generate_db_auth_token(
            DBHostname=db_host, Port=int(db_port), DBUsername=db_user, Region=aws_region
        )
        return pymysql.connect(
            **_iam_connect_kwargs(db_host, int(db_port), db_user, token, db_name)
        )

    engine_kwargs = {
        "pool_pre_ping": True,
        # Recycle pooled connections after 1800s (30 min) so a connection is never
        # reused after RDS wait_timeout or a load balancer has silently dropped it.
        # Comfortably under RDS's default 8h wait_timeout; pool_pre_ping is the
        # backstop for anything that dies sooner.
        "pool_recycle": 1800,
        "creator": _connect,
    }

    return create_engine(DATABASE_URL, **engine_kwargs)