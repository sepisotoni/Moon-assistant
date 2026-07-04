from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ── Lazy engine ────────────────────────────────────────────────────────────────
# We intentionally do NOT call get_settings() at module level.  Doing so would
# require DISCORD_TOKEN to be present even when Alembic is just running a
# migration and has no interest in the Discord token.  Instead we build the
# engine the first time _get_engine() is called (i.e. when the first actual DB
# session is opened), which is always after the .env file has been loaded.
# ──────────────────────────────────────────────────────────────────────────────
_engine = None
_session_factory = None


def _get_engine():
    global _engine, _session_factory
    if _engine is None:
        from bot.config import get_settings
        s = get_settings()
        _engine = create_async_engine(s.database_url, echo=s.db_echo, pool_pre_ping=True)
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    return _engine, _session_factory


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield an AsyncSession, committing on success and rolling back on error."""
    _, factory = _get_engine()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_engine():
    """Return the engine (for setup_hook create_all calls)."""
    engine, _ = _get_engine()
    return engine
