from __future__ import annotations

import os

# Must be set before geniebot.settings.get_settings() is first called
# (it's lru_cached process-wide) - the test suite runs entirely against
# SQLite and MockLLMClient/MockJiraServer/MockMailClient, so force every
# backend selector to its local/mock value for the whole test session
# regardless of any .env a developer happens to have lying around (e.g. one
# configured with a real LLM_BACKEND=openai for a live demo - without this,
# tests would make real, billed network calls and fail on rate limits
# instead of running hermetically).
os.environ.setdefault("VECTOR_STORE_BACKEND", "memory")
os.environ.setdefault("LLM_BACKEND", "mock")
os.environ.setdefault("AUTH_BACKEND", "mock")
os.environ.setdefault("JIRA_BACKEND", "mock")
os.environ.setdefault("MAIL_BACKEND", "mock")
# ingestion/factory.py's get_event_source() defaults to LOCAL_S3_ROOT=./local_s3
# (the same directory the dev demo drops sample logs into) - point tests at
# their own scratch directory instead, or a test posting to /ingest/trigger
# would read/write real demo data.
os.environ.setdefault("LOCAL_S3_ROOT", "./.test_local_s3")

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from geniebot.db import models  # noqa: F401 - registers tables on Base.metadata
from geniebot.db.base import Base
from geniebot.kb.vector_store import InMemoryVectorStore
from geniebot.llm.mock_client import MockLLMClient


@pytest_asyncio.fixture
async def engine():
    # StaticPool keeps a single shared connection alive for the whole test,
    # which is required for SQLite ":memory:" under async SQLAlchemy -
    # otherwise every checkout gets its own empty in-memory database.
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine):
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with sessionmaker() as s:
        yield s


@pytest_asyncio.fixture
async def sessionmaker_fixture(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture
def vector_store():
    return InMemoryVectorStore()


@pytest_asyncio.fixture
def llm_client():
    return MockLLMClient()
