"""Builds the configured VectorStore. The in-memory and faiss backends are
both process-wide singletons (nothing to scope to a request - faiss's is
also file-backed, but still single-process only, see
kb/faiss_vector_store.py's module docstring); the pgvector backend wraps
the caller's AsyncSession and so is built fresh per unit-of-work rather
than cached.
"""
from __future__ import annotations

from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from geniebot.db.models import KBChunk
from geniebot.kb.vector_store import InMemoryVectorStore, PgVectorStore, VectorRecord, VectorStore
from geniebot.llm.client import EMBED_DIM
from geniebot.settings import get_settings


@lru_cache
def _shared_memory_store() -> InMemoryVectorStore:
    return InMemoryVectorStore()


@lru_cache
def _shared_faiss_store():
    from geniebot.kb.faiss_vector_store import FaissVectorStore

    return FaissVectorStore(get_settings().faiss_index_dir)


def get_vector_store(session: AsyncSession) -> VectorStore:
    settings = get_settings()
    if settings.vector_store_backend == "pgvector":
        return PgVectorStore(session, EMBED_DIM)
    if settings.vector_store_backend == "faiss":
        return _shared_faiss_store()
    return _shared_memory_store()


async def ensure_vector_store_schema(session: AsyncSession) -> None:
    """Creates the pgvector extension and kb_embeddings/kb_active_index
    tables if the configured backend needs them (PgVectorStore.ensure_schema
    is idempotent - IF NOT EXISTS throughout). For faiss, rebuilds the
    active index_version's local index from KBChunk if its file is
    missing or fails to load - KBChunk.embedding already durably holds
    every embedding regardless of vector store backend
    (kb/vector_store.py's own docstring), so a corrupt/missing local
    FAISS file (the one failure mode this backend has that the other two
    don't - an interrupted write) is always recoverable from it. No-op for
    the in-memory backend. Call once at process startup (main.py lifespan,
    worker_main.py) before anything tries to query those tables/files."""
    settings = get_settings()
    if settings.vector_store_backend == "pgvector":
        await PgVectorStore(session, EMBED_DIM).ensure_schema()
    elif settings.vector_store_backend == "faiss":
        store = _shared_faiss_store()
        active_version = await store.get_active_index_version()
        if active_version is None:
            return
        if store.has_data(active_version):
            return
        result = await session.execute(
            select(KBChunk).where(
                KBChunk.index_version == active_version, KBChunk.embedding.is_not(None)
            )
        )
        records = [
            VectorRecord(
                chunk_id=chunk.chunk_id,
                embedding=chunk.embedding,
                error_category=chunk.error_category,
                index_version=chunk.index_version,
                retired_at=chunk.retired_at,
            )
            for chunk in result.scalars().all()
        ]
        if records:
            await store.upsert(records)
