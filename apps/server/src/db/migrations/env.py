"""Alembic environment: online migrations over asyncpg."""

import asyncio

from alembic import context
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from src.db.models import Base

# Serializes concurrent `xavier migrate` runs, for example several api replicas starting at once.
_MIGRATION_LOCK_ID = 0x58_41_56_49_45_52


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        connection.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": _MIGRATION_LOCK_ID})
        context.run_migrations()


async def _main() -> None:
    engine = create_async_engine(context.config.attributes["url"], poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run)
    finally:
        await engine.dispose()


asyncio.run(_main())
