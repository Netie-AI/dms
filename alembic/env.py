"""Alembic env — DMS schema migrations against DATABASE_URL (psycopg)."""

from __future__ import annotations

import os
from logging.config import fileConfig

from sqlalchemy import create_engine, pool, text

from alembic import context

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "postgresql://dms:dms@127.0.0.1:5432/dms")
    # SQLAlchemy 2 + psycopg3
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema="dms",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        connection.execute(text("CREATE SCHEMA IF NOT EXISTS dms"))
        connection.commit()
        context.configure(
            connection=connection,
            version_table_schema="dms",
            include_schemas=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
