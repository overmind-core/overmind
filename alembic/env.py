"""
Core alembic environment.

Only imports core models (no enterprise). This ensures core migrations
only cover core tables (users, projects, tokens, traces, spans, etc.).
"""

from logging.config import fileConfig
from sqlalchemy.engine import Connection
from alembic import context
import asyncio
import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from overmind.db.base import Base
import overmind.models  # noqa: F401 — register core models with metadata

from overmind.db.session import get_engine_instance

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    engine = get_engine_instance()
    url = str(engine.url)
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = get_engine_instance()

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
