"""Builds the configured VectorStore. The in-memory backend is a process-
wide singleton (there's nothing to scope to a request); the pgvector
backend wraps the caller's AsyncSession and so is built fresh per
unit-of-work rather than cached.
"""
from __future__ import annotations

from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncSession

from geniebot.kb.vector_store import InMemoryVectorStore, PgVectorStore, VectorStore
from geniebot.llm.client import EMBED_DIM
from geniebot.settings import get_settings


@lru_cache
def _shared_memory_store() -> InMemoryVectorStore:
    return InMemoryVectorStore()


def get_vector_store(session: AsyncSession) -> VectorStore:
    settings = get_settings()
    if settings.vector_store_backend == "pgvector":
        return PgVectorStore(session, EMBED_DIM)
    return _shared_memory_store()


async def ensure_vector_store_schema(session: AsyncSession) -> None:
    """Creates the pgvector extension and kb_embeddings/kb_active_index
    tables if the configured backend needs them (PgVectorStore.ensure_schema
    is idempotent - IF NOT EXISTS throughout). No-op for the in-memory
    backend. Call once at process startup (main.py lifespan,
    worker_main.py) before anything tries to query those tables."""
    settings = get_settings()
    if settings.vector_store_backend == "pgvector":
        await PgVectorStore(session, EMBED_DIM).ensure_schema()
