"""Vector store abstraction (doc section 3.3 / 4.2 step 10 "Index"). Stores
only what's needed for similarity search and filtering - chunk_id, the
embedding, error_category and index_version - so a backend never duplicates
chunk content. Callers (kb/retrieval.py) join the returned chunk_ids back
against the KBChunk relational table for content/source_ref/etc.

Two implementations:
- InMemoryVectorStore: pure Python, used for tests and any environment
  running with VECTOR_STORE_BACKEND=memory.
- PgVectorStore: real implementation against Postgres + the pgvector
  extension (doc 2.3: "Approved internal vector database or pgvector").
  Maintains its own kb_embeddings table via raw DDL, deliberately outside
  the SQLAlchemy ORM metadata, so the relational schema (db/models.py)
  stays dialect-agnostic and this table's vector dimension/index type can
  be rebuilt independently of an Alembic migration.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from math import sqrt

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class VectorRecord:
    chunk_id: str
    embedding: list[float]
    error_category: str
    index_version: str
    retired_at: datetime | None = None


@dataclass(frozen=True)
class ScoredChunkId:
    chunk_id: str
    similarity: float


class VectorStore(ABC):
    @abstractmethod
    async def upsert(self, records: list[VectorRecord]) -> None: ...

    @abstractmethod
    async def query(
        self,
        query_vector: list[float],
        *,
        top_n: int,
        index_version: str,
        error_category: str | None = None,
        min_similarity: float = 0.0,
    ) -> list[ScoredChunkId]: ...

    @abstractmethod
    async def get_active_index_version(self) -> str | None: ...

    @abstractmethod
    async def set_active_index_version(self, index_version: str) -> None: ...

    @abstractmethod
    async def delete_index_version(self, index_version: str) -> None:
        """Used to discard a candidate index that failed evaluation (doc 4.2 step 11)."""


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sqrt(sum(x * x for x in a)) or 1.0
    norm_b = sqrt(sum(y * y for y in b)) or 1.0
    return dot / (norm_a * norm_b)


class InMemoryVectorStore(VectorStore):
    def __init__(self) -> None:
        self._records: dict[str, VectorRecord] = {}
        self._active_index_version: str | None = None

    async def upsert(self, records: list[VectorRecord]) -> None:
        for r in records:
            self._records[r.chunk_id] = r

    async def query(
        self,
        query_vector: list[float],
        *,
        top_n: int,
        index_version: str,
        error_category: str | None = None,
        min_similarity: float = 0.0,
    ) -> list[ScoredChunkId]:
        now = datetime.now(UTC)
        candidates = [
            r
            for r in self._records.values()
            if r.index_version == index_version
            and (r.retired_at is None or r.retired_at > now)
            and (error_category is None or r.error_category == error_category)
        ]
        scored = [
            ScoredChunkId(chunk_id=r.chunk_id, similarity=_cosine_similarity(query_vector, r.embedding))
            for r in candidates
        ]
        scored = [s for s in scored if s.similarity >= min_similarity]
        scored.sort(key=lambda s: s.similarity, reverse=True)
        return scored[:top_n]

    async def get_active_index_version(self) -> str | None:
        return self._active_index_version

    async def set_active_index_version(self, index_version: str) -> None:
        self._active_index_version = index_version

    async def delete_index_version(self, index_version: str) -> None:
        self._records = {k: v for k, v in self._records.items() if v.index_version != index_version}


class PgVectorStore(VectorStore):
    """Real backend. Requires `CREATE EXTENSION IF NOT EXISTS vector;` to
    have been run on the target database (see deploy/ and
    scripts/seed_kb_sample_corpus.py which both do this on first use)."""

    def __init__(self, session: AsyncSession, embed_dim: int):
        self._session = session
        self._embed_dim = embed_dim

    async def ensure_schema(self) -> None:
        await self._session.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await self._session.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS kb_embeddings (
                    chunk_id VARCHAR(36) PRIMARY KEY,
                    embedding vector({self._embed_dim}) NOT NULL,
                    error_category VARCHAR(64) NOT NULL,
                    index_version VARCHAR(64) NOT NULL,
                    retired_at TIMESTAMPTZ NULL
                )
                """
            )
        )
        await self._session.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_kb_embeddings_index_version "
                "ON kb_embeddings (index_version)"
            )
        )
        await self._session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS kb_active_index (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    index_version VARCHAR(64) NOT NULL,
                    CHECK (id = 1)
                )
                """
            )
        )
        await self._session.commit()

    async def upsert(self, records: list[VectorRecord]) -> None:
        for r in records:
            await self._session.execute(
                text(
                    """
                    INSERT INTO kb_embeddings (chunk_id, embedding, error_category, index_version, retired_at)
                    VALUES (:chunk_id, :embedding, :error_category, :index_version, :retired_at)
                    ON CONFLICT (chunk_id) DO UPDATE SET
                        embedding = EXCLUDED.embedding,
                        error_category = EXCLUDED.error_category,
                        index_version = EXCLUDED.index_version,
                        retired_at = EXCLUDED.retired_at
                    """
                ),
                {
                    "chunk_id": r.chunk_id,
                    "embedding": str(r.embedding),
                    "error_category": r.error_category,
                    "index_version": r.index_version,
                    "retired_at": r.retired_at,
                },
            )
        await self._session.commit()

    async def query(
        self,
        query_vector: list[float],
        *,
        top_n: int,
        index_version: str,
        error_category: str | None = None,
        min_similarity: float = 0.0,
    ) -> list[ScoredChunkId]:
        sql = """
            SELECT chunk_id, 1 - (embedding <=> :query_vector) AS similarity
            FROM kb_embeddings
            WHERE index_version = :index_version
              AND (retired_at IS NULL OR retired_at > now())
              AND (:error_category IS NULL OR error_category = :error_category)
            ORDER BY embedding <=> :query_vector
            LIMIT :top_n
        """
        result = await self._session.execute(
            text(sql),
            {
                "query_vector": str(query_vector),
                "index_version": index_version,
                "error_category": error_category,
                "top_n": top_n,
            },
        )
        rows = result.fetchall()
        return [
            ScoredChunkId(chunk_id=row.chunk_id, similarity=row.similarity)
            for row in rows
            if row.similarity >= min_similarity
        ]

    async def get_active_index_version(self) -> str | None:
        result = await self._session.execute(text("SELECT index_version FROM kb_active_index WHERE id = 1"))
        row = result.first()
        return row.index_version if row else None

    async def set_active_index_version(self, index_version: str) -> None:
        await self._session.execute(
            text(
                """
                INSERT INTO kb_active_index (id, index_version) VALUES (1, :v)
                ON CONFLICT (id) DO UPDATE SET index_version = EXCLUDED.index_version
                """
            ),
            {"v": index_version},
        )
        await self._session.commit()

    async def delete_index_version(self, index_version: str) -> None:
        await self._session.execute(
            text("DELETE FROM kb_embeddings WHERE index_version = :v"), {"v": index_version}
        )
        await self._session.commit()
