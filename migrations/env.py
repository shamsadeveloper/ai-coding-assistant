"""Alembic environment: uses the application's settings, engine factory and models."""

from alembic import context

import app.models  # noqa: F401  registers every table on Base.metadata
from app.core.config import get_settings
from app.core.database import Base, create_db_engine

config = context.config
target_metadata = Base.metadata


def database_url() -> str:
    # Tests and tools can pass a URL explicitly; otherwise use the app configuration.
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(url=database_url(), target_metadata=target_metadata, literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_db_engine(database_url())
    with engine.connect() as connection:
        # Batch mode lets SQLite (which cannot ALTER most columns) apply future migrations.
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
