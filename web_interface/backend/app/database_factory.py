import os
import boto3
import pymysql
from pathlib import Path
from sqlalchemy import create_engine
from app.config_loader import get_config

def create_cviche_engine(override_user: str = None):
    """
    Common factory that sets up an SQLAlchemy engine with dynamic 
    AWS IAM token generation and mandatory SSL encryption.
    """
    # 1. Extract base parameters using your existing config loader
    db_host, _ = get_config("db", "DB_HOST", default="")
    db_port, _ = get_config("db", "DB_PORT", default="")
    db_name, _ = get_config("db", "DB_NAME", default="")
    
    # Use the explicitly passed override user, fallback to config file, fallback to string
    if override_user:
        db_user = override_user
    else:
        db_user, _ = get_config("db", "DB_USER", default="")
        
    aws_region = os.environ.get("AWS_REGION", "us-east-1")
    engine_kwargs = {}

    # 2. Determine Database Engine Target Topology
    if db_host and db_port and db_user and db_name:
        # MariaDB / MySQL Path
        DATABASE_URL = f"mysql+pymysql://{db_user}@{db_host}:{db_port}/{db_name}"
        engine_kwargs["pool_pre_ping"] = True

        # Inner dynamic token generation helper function
        def generate_iam_db_token():
            rds_client = boto3.client('rds', region_name=aws_region)
            return rds_client.generate_db_auth_token(
                DBHostname=db_host,
                Port=int(db_port),
                DBUsername=db_user,
                Region=aws_region
            )

        # Inject the pool connection creator interceptor with forced SSL activation
        engine_kwargs["creator"] = lambda: pymysql.connect(
            host=db_host,
            port=int(db_port),
            user=db_user,
            password=generate_iam_db_token(),
            database=db_name,
            ssl={'ssl': True}
        )
    else:
        # Fallback Local Path: SQLite
        _default_db = Path(__file__).resolve().parent.parent / "cviche_dev.db"
        DATABASE_URL = f"sqlite:///{_default_db}"
        engine_kwargs["connect_args"] = {"check_same_thread": False}

    # 3. Compile and return the live connection resource
    return create_engine(DATABASE_URL, **engine_kwargs)