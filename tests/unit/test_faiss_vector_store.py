"""Local FAISS vector store (kb/faiss_vector_store.py) - an interim,
restart-durable alternative to VECTOR_STORE_BACKEND=memory. The property
that actually matters is persistence across process restarts, simulated
here by constructing a fresh FaissVectorStore instance pointed at the
same directory an earlier instance wrote to."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from geniebot.kb.faiss_vector_store import FaissVectorStore
from geniebot.kb.vector_store import VectorRecord
from geniebot.llm.client import EMBED_DIM

_DIM = EMBED_DIM  # FaissVectorStore sizes its index to EMBED_DIM - vectors must match


def _vec(seed: float) -> list[float]:
    # distinct-but-similar vectors so cosine similarity ordering is stable
    return [seed] + [seed * 0.1] * (_DIM - 1)


@pytest.mark.asyncio
async def test_query_on_empty_store_returns_empty_list(tmp_path):
    store = FaissVectorStore(str(tmp_path))
    result = await store.query(_vec(1.0), top_n=5, index_version="v1")
    assert result == []


@pytest.mark.asyncio
async def test_persists_across_fresh_instances(tmp_path):
    store_a = FaissVectorStore(str(tmp_path))
    await store_a.upsert(
        [VectorRecord(chunk_id="c1", embedding=_vec(1.0), error_category="CONNECTIVITY", index_version="v1")]
    )
    await store_a.set_active_index_version("v1")

    store_b = FaissVectorStore(str(tmp_path))  # simulates a process restart
    result = await store_b.query(_vec(1.0), top_n=5, index_version="v1")
    assert [r.chunk_id for r in result] == ["c1"]
    assert await store_b.get_active_index_version() == "v1"


@pytest.mark.asyncio
async def test_query_filters_by_error_category(tmp_path):
    store = FaissVectorStore(str(tmp_path))
    await store.upsert(
        [
            VectorRecord(chunk_id="c1", embedding=_vec(1.0), error_category="CONNECTIVITY", index_version="v1"),
            VectorRecord(chunk_id="c2", embedding=_vec(1.01), error_category="AUTH_EXPIRED", index_version="v1"),
        ]
    )
    result = await store.query(_vec(1.0), top_n=5, index_version="v1", error_category="AUTH_EXPIRED")
    assert [r.chunk_id for r in result] == ["c2"]


@pytest.mark.asyncio
async def test_query_excludes_retired_chunks(tmp_path):
    store = FaissVectorStore(str(tmp_path))
    past = datetime.now(UTC) - timedelta(days=1)
    future = datetime.now(UTC) + timedelta(days=1)
    await store.upsert(
        [
            VectorRecord(chunk_id="retired", embedding=_vec(1.0), error_category="X", index_version="v1", retired_at=past),
            VectorRecord(chunk_id="not-yet", embedding=_vec(1.0), error_category="X", index_version="v1", retired_at=future),
            VectorRecord(chunk_id="never", embedding=_vec(1.0), error_category="X", index_version="v1"),
        ]
    )
    result = await store.query(_vec(1.0), top_n=5, index_version="v1")
    assert set(r.chunk_id for r in result) == {"not-yet", "never"}


@pytest.mark.asyncio
async def test_query_respects_top_n_and_min_similarity(tmp_path):
    store = FaissVectorStore(str(tmp_path))
    await store.upsert(
        [
            VectorRecord(chunk_id="close", embedding=_vec(1.0), error_category="X", index_version="v1"),
            VectorRecord(chunk_id="far", embedding=[1.0] + [0.0] * (_DIM - 1), error_category="X", index_version="v1"),
        ]
    )
    result = await store.query(_vec(1.0), top_n=1, index_version="v1")
    assert len(result) == 1
    assert result[0].chunk_id == "close"

    result_filtered = await store.query(_vec(1.0), top_n=5, index_version="v1", min_similarity=0.999)
    assert [r.chunk_id for r in result_filtered] == ["close"]


@pytest.mark.asyncio
async def test_delete_index_version_removes_only_that_version(tmp_path):
    store = FaissVectorStore(str(tmp_path))
    await store.upsert(
        [
            VectorRecord(chunk_id="v1-chunk", embedding=_vec(1.0), error_category="X", index_version="v1"),
            VectorRecord(chunk_id="v2-chunk", embedding=_vec(1.0), error_category="X", index_version="v2"),
        ]
    )
    await store.delete_index_version("v1")

    assert await store.query(_vec(1.0), top_n=5, index_version="v1") == []
    result_v2 = await store.query(_vec(1.0), top_n=5, index_version="v2")
    assert [r.chunk_id for r in result_v2] == ["v2-chunk"]
    assert not (tmp_path / "v1.faiss").exists()


@pytest.mark.asyncio
async def test_reupsert_replaces_rather_than_duplicates(tmp_path):
    store = FaissVectorStore(str(tmp_path))
    await store.upsert(
        [VectorRecord(chunk_id="c1", embedding=_vec(1.0), error_category="OLD_CAT", index_version="v1")]
    )
    await store.upsert(
        [VectorRecord(chunk_id="c1", embedding=_vec(2.0), error_category="NEW_CAT", index_version="v1")]
    )

    result = await store.query(_vec(2.0), top_n=10, index_version="v1")
    assert len(result) == 1  # not two stale+fresh entries for the same chunk_id
    assert result[0].chunk_id == "c1"

    # the old category no longer matches - confirms the old entry was
    # actually replaced, not just shadowed by a newer duplicate
    assert await store.query(_vec(2.0), top_n=10, index_version="v1", error_category="OLD_CAT") == []


@pytest.mark.asyncio
async def test_active_index_version_round_trips(tmp_path):
    store = FaissVectorStore(str(tmp_path))
    assert await store.get_active_index_version() is None
    await store.set_active_index_version("v3")
    assert await store.get_active_index_version() == "v3"
