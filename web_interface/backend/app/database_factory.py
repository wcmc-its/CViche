import logging
import os
import boto3
import pymysql
from pathlib import Path
from sqlalchemy import create_engine
from app.config_loader import get_config

logger = logging.getLogger(__name__)


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
    
    engine_kwargs = {
        "pool_pre_ping": True,
        # Recycle pooled connections after 1800s (30 min) so a connection is never
        # reused after RDS wait_timeout or a load balancer has silently dropped it.
        # Comfortably under RDS's default 8h wait_timeout; pool_pre_ping is the
        # backstop for anything that dies sooner.
        "pool_recycle": 1800,
        "creator": lambda: pymysql.connect(
            host=db_host,
            port=int(db_port),
            user=db_user,
            password=boto3.client('rds', region_name=aws_region).generate_db_auth_token(
                DBHostname=db_host, Port=int(db_port), DBUsername=db_user, Region=aws_region
            ),
            database=db_name,
            ssl={'ssl': True}
        )
    }

    return create_engine(DATABASE_URL, **engine_kwargs)