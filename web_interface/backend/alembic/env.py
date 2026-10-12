import os
from logging.config import fileConfig

import boto3
from sqlalchemy import engine_from_config, pool

from alembic import context
from app.config_loader import get_config
from app.database_factory import create_cviche_engine

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)



#config.set_main_option("sqlalchemy.url", database_url)

# Import models so Alembic can detect them for autogenerate
#from app.database import Base  # noqa: E402
from app.base_class import Base
from app.models import (  # noqa: F401, E402
    Consent,
    Feedback,
    LLMUsage,
    Log,
    Run,
    RunMetrics,
    Step,
    SystemConfig,
    User,
)

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
    # 1. Extract routing parameters using in configuration loader
    db_host, _ = get_config("db", "DB_HOST", default="")
    db_port, _ = get_config("db", "DB_PORT", default="")
    db_name, _ = get_config("db", "DB_NAME", default="")
    migrate_user, _ = get_config("db", "MIGRATE_USER", default="")


    # 2. Pass them directly to the factory
    connectable = create_cviche_engine(
        db_host=db_host,
        db_port=db_port,
        db_name=db_name,
        db_user=migrate_user
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

