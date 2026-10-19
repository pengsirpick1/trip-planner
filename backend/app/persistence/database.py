"""Async SQLAlchemy infrastructure; repositories own all database access."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import Settings


def create_session_factory(settings: Settings) -> async_sessionmaker[AsyncSession]:
    if not settings.database_url:
        raise ValueError("DATABASE_URL is required for MySQL persistence")
    engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_seconds,
        pool_recycle=settings.db_pool_recycle_seconds,
    )
    return async_sessionmaker(engine, expire_on_commit=False)


async def dispose_session_factory(sessions: async_sessionmaker[AsyncSession]) -> None:
    """Release the shared SQLAlchemy engine during FastAPI shutdown."""
    bind = sessions.kw.get("bind")
    if bind is not None:
        await bind.dispose()
