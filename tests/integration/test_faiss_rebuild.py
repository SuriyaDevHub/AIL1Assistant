"""kb/factory.py::ensure_vector_store_schema's faiss branch - a missing or
corrupt local index file (this backend's own failure mode - an interrupted
write; the other two backends don't have it) is recoverable from KBChunk,
which already durably holds every embedding regardless of vector store
backend."""
from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

from geniebot.db.models import Classification as DBClassification
from geniebot.db.models import DocType, KBChunk
from geniebot.kb import factory as kb_factory
from geniebot.llm.client import EMBED_DIM
from geniebot.settings import get_settings

_VEC = [0.5] * EMBED_DIM


def _set_env(**kwargs: str | None) -> dict[str, str | None]:
    saved: dict[str, str | None] = {}
    for k, v in kwargs.items():
        saved[k] = os.environ.get(k)
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    return saved


def _restore_env(saved: dict[str, str | None]) -> None:
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.mark.asyncio
async def test_ensure_schema_rebuilds_missing_active_index_from_kbchunk(session, tmp_path):
    saved = _set_env(VECTOR_STORE_BACKEND="faiss", FAISS_INDEX_DIR=str(tmp_path))
    get_settings.cache_clear()
    kb_factory._shared_faiss_store.cache_clear()
    try:
        chunk = KBChunk(
            doc_id="doc-1", doc_type=DocType.SOP, content="reconnect after a transient blip",
            embedding=_VEC, error_category="CONNECTIVITY", source_ref="SOP-1",
            classification=DBClassification.INTERNAL, index_version="v1-seed",
            effective_from=datetime.now(UTC),
        )
        session.add(chunk)
        await session.commit()

        store = kb_factory._shared_faiss_store()
        await store.set_active_index_version("v1-seed")
        # No .faiss file has ever been written for v1-seed at this point -
        # simulates a fresh/missing local index file, same as a corrupt one
        # would look once _get_index catches the read failure.
        assert not store.has_data("v1-seed")

        await kb_factory.ensure_vector_store_schema(session)

        assert store.has_data("v1-seed")
        result = await store.query(_VEC, top_n=5, index_version="v1-seed")
        assert [r.chunk_id for r in result] == [chunk.chunk_id]
    finally:
        _restore_env(saved)
        get_settings.cache_clear()
        kb_factory._shared_faiss_store.cache_clear()
