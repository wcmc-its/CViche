import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context
import boto3
# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

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
    database_url = f"mysql+pymysql://{db_user}@{db_host}:{db_port}/{db_name}"
    
    # Enable pre-ping to drop stale connections gracefully
    engine_kwargs["pool_pre_ping"] = True
    
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
    database_url = f"sqlite:///./cviche_dev.db"
    
# Override sqlalchemy.url from environment variable (same source as database.py)
#database_url = os.environ.get(
#    "CVICHE_DATABASE_URL",
#    "sqlite:///./cviche_dev.db"
#)
config.set_main_option("sqlalchemy.url", database_url)

# Import models so Alembic can detect them for autogenerate
from app.models import User, SystemConfig, Consent, Feedback, Run, Step, Log, LLMUsage, RunMetrics  # noqa: F401, E402
#from app.database import Base  # noqa: E402

from app.base_class import Base
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        **engine_kwargs,  # ◄ CRITICAL: unpack the kwargs dictionary here!
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

