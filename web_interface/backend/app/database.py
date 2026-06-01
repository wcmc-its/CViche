"""Database configuration and session management."""
import os
import boto3
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from app.base_class import Base

# Read DATABASE_URL from environment; default to SQLite for local dev.
# The default is an absolute path anchored at the backend directory so it is
# stable regardless of the process working directory (the pipeline
# orchestrator pins cwd to the repo root while runs execute).

from app.config_loader import get_config
# 1. Extract routing parameters using your configuration loader
db_host, source = get_config("db", "DB_HOST", default="")
db_port, source = get_config("db", "DB_PORT", default="")
db_user, source = get_config("db", "DB_USER", default="")
db_name, source = get_config("db", "DB_NAME", default="")
aws_region = os.environ.get("AWS_REGION", "us-east-1")

engine_kwargs = {}

# 2. Determine Database URL Backend Type
if db_host and db_port and db_user and db_name:
    # MariaDB / MySQL Configuration Path
    # Note: We omit the password from the static string because we inject it dynamically via a pool creator callback
    DATABASE_URL = f"mysql+pymysql://{db_user}@{db_host}:{db_port}/{db_name}"
    
    # Enable pre-ping to drop stale connections gracefully
    engine_kwargs["pool_pre_ping"] = True

# 3. Define the Dynamic IAM Token Generation Callback Function
    def generate_iam_db_token():
        # Initializes an isolated RDS client. Boto3 automatically reads the projected
        # IRSA files/tokens exposed to the pod by the EKS ServiceAccount webhook.
        rds_client = boto3.client('rds', region_name=aws_region)
        
        # Generates a fresh ephemeral token string (Valid for 15 minutes)
        token = rds_client.generate_db_auth_token(
            DBHostname=db_host,
            Port=int(db_port),
            DBUsername=db_user,
            Region=aws_region
        )
        return token

    # 4. Inject the token creator and enforce mandatory SSL encryption
    import pymysql
    engine_kwargs["creator"] = lambda: pymysql.connect(
        host=db_host,
        port=int(db_port),
        user=db_user,
        password=generate_iam_db_token(), # ◄ Dynamically fetched on every new connection
        database=db_name,
        ssl={'ssl': True} # ◄ AWS IAM database authentication strictly requires SSL
    )

else:
    # Fallback Path: SQLite Local Development
    _default_db = Path(__file__).resolve().parent.parent / "cviche_dev.db"
    DATABASE_URL = f"sqlite:///{_default_db}"
    engine_kwargs["connect_args"] = {"check_same_thread": False}


# 5. Initialize Engine and Session Factories exactly as before
engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
   

def get_db():
    """Dependency for getting database sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Initialize database tables."""
    from app.models import Run, Step, Log, LLMUsage, RunMetrics, User, SystemConfig, Consent, Feedback  # noqa: F401
    Base.metadata.create_all(bind=engine)
