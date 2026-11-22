"""Database engine and migrations."""

from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config import DatabaseSettings

type Sessions = async_sessionmaker[AsyncSession]


def create_engine(settings: DatabaseSettings) -> AsyncEngine:
    """Create the application engine."""
    return create_async_engine(settings.url(), pool_pre_ping=True)


def create_sessions(engine: AsyncEngine) -> Sessions:
    """Return the session factory; loaded objects stay usable after commit."""
    return async_sessionmaker(engine, expire_on_commit=False)


def migrate(settings: DatabaseSettings) -> None:
    """Upgrade the database schema to the latest revision."""
    config = Config()
    config.set_main_option("script_location", "src.db:migrations")
    config.attributes["url"] = settings.url()
    command.upgrade(config, "head")
