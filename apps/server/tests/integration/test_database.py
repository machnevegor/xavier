import asyncio
import multiprocessing
from concurrent.futures import ProcessPoolExecutor

import pytest
from sqlalchemy import text

from src.config import Settings
from src.db.engine import create_engine, migrate

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


async def test_migrations_run_and_database_answers(settings: Settings) -> None:
    await asyncio.to_thread(migrate, settings.database)
    engine = create_engine(settings.database)
    try:
        async with engine.connect() as connection:
            assert (await connection.execute(text("SELECT 1"))).scalar_one() == 1
    finally:
        await engine.dispose()


async def test_concurrent_migrations_are_serialized(settings: Settings) -> None:
    # Alembic keeps global state, so concurrent runs are separate processes, like api replicas.
    loop = asyncio.get_running_loop()
    with ProcessPoolExecutor(3, mp_context=multiprocessing.get_context("spawn")) as pool:
        runs = [loop.run_in_executor(pool, migrate, settings.database) for _ in range(3)]
        try:
            await asyncio.wait_for(asyncio.gather(*runs), timeout=60)
        except TimeoutError:
            pool.kill_workers()
            raise
