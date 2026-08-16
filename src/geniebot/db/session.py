from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from geniebot.settings import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(settings.database_url, pool_pre_ping=True, future=True)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a scoped AsyncSession."""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        yield session


async def init_models(engine: AsyncEngine | None = None) -> None:
    """Create tables directly from ORM metadata.

    Used by tests (SQLite) and local first-run bootstrap. Real environments
    use Alembic migrations (db/migrations/) instead, so schema changes are
    reviewed and reversible.
    """
    from geniebot.db import models  # noqa: F401 - registers all tables on Base.metadata
    from geniebot.db.base import Base

    engine = engine or get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
