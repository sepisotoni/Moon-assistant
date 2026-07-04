"""Shared pytest fixtures for the Phase 2/3 test suite.

These fixtures spin up a fully in-memory async SQLite database (via aiosqlite) and patch
``bot.database.session.get_session`` so every repository/service that calls ``get_session()``
automatically gets the in-memory session.  No real Postgres instance is needed.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import bot.database  # noqa: F401 – registers all ORM models
from bot.database.base import Base


# ---------------------------------------------------------------------------
# Event loop (session-scoped)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ---------------------------------------------------------------------------
# In-memory async SQLite engine (module-scoped so each test file gets a fresh DB)
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture(scope="module")
async def db_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="module")
async def session_factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)


# ---------------------------------------------------------------------------
# Patch get_session so every repository uses the in-memory DB automatically.
# Use this fixture in any test that exercises a repository or service that
# calls ``get_session()``.
# ---------------------------------------------------------------------------
@pytest.fixture
def patched_get_session(session_factory):
    """Replaces ``bot.database.session.get_session`` with one backed by SQLite."""

    @asynccontextmanager
    async def _fake_get_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    targets = [
        "bot.database.session.get_session",
        "bot.services.archive_service.get_session",
        "bot.services.search_service.get_session",
        "bot.services.logging_service.get_session",
        "bot.knowledge.indexer.get_session",
        "bot.knowledge.retriever.get_session",
        "bot.knowledge.learning_service.get_session",
        "bot.moderation.service.get_session",
        "bot.moderation.intelligence_service.get_session",
        "bot.investigation.tools.get_session",
        "bot.repositories.ai_repository.get_session",
        "bot.repositories.ai_state_repository.get_session",
        "bot.repositories.knowledge_repository.get_session",
        "bot.repositories.moderation_intel_repository.get_session",
    ]
    patchers = [patch(t, new=_fake_get_session) for t in targets]
    for p in patchers:
        p.start()
    yield _fake_get_session
    for p in patchers:
        p.stop()


# ---------------------------------------------------------------------------
# Minimal Discord stubs
# ---------------------------------------------------------------------------
@pytest.fixture
def mock_guild():
    guild = MagicMock()
    guild.id = 123456789
    guild.name = "Test Guild"
    guild.text_channels = []
    guild.threads = []
    return guild


@pytest.fixture
def mock_member(mock_guild):
    member = MagicMock()
    member.id = 987654321
    member.display_name = "TestMember"
    member.bot = False
    member.roles = []
    member.guild = mock_guild
    member.guild_permissions = MagicMock(administrator=False, moderate_members=True, manage_messages=True)
    return member


@pytest.fixture
def mock_founder_member(mock_guild):
    member = MagicMock()
    member.id = 111111111
    member.display_name = "FounderUser"
    member.bot = False
    owner_role = MagicMock()
    owner_role.name = "Founder"
    member.roles = [owner_role]
    member.guild = mock_guild
    member.guild_permissions = MagicMock(administrator=False)
    return member


@pytest.fixture
def mock_message(mock_guild, mock_member):
    msg = MagicMock()
    msg.id = 555555555
    msg.guild = mock_guild
    msg.author = mock_member
    msg.content = "Hello world!"
    msg.attachments = []
    msg.reference = None
    msg.channel = MagicMock()
    msg.channel.id = 222222222
    msg.channel.name = "general"
    return msg


# ---------------------------------------------------------------------------
# Mock AI provider (no HTTP calls)
# ---------------------------------------------------------------------------
@pytest.fixture
def mock_ai_response():
    from bot.ai.base import AIResponse
    return AIResponse(text="This is a test AI response.", provider="mock", model="mock-model")


@pytest.fixture
def mock_provider(mock_ai_response):
    from bot.ai.base import AIProvider

    class MockProvider(AIProvider):
        name = "mock"
        async def generate(self, messages, **kwargs):
            return mock_ai_response

    return MockProvider()


@pytest.fixture
def mock_orchestrator():
    from bot.ai.orchestrator import AIDecision
    orch = AsyncMock()
    orch.generate_for_task = AsyncMock(return_value=AIDecision(
        text="Mock AI decision.",
        confidence=0.9,
        evidence_count=2,
        retrieval_summary="mock retrieval",
        provider="mock",
        model="mock-model",
    ))
    return orch
