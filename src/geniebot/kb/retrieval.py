"""Retrieval design - doc section 4.4.

Query construction embeds the normalised error signature plus failing
module, not the raw log. Filtering restricts by error_category (and, where
the vector store backend supports it, environment) and excludes retired
chunks. Top-N is configurable. Re-ranking prefers recent, higher-confidence
resolutions when similarity scores are close - "higher-confidence" is
approximated here as doc_type priority (a resolved_incident or an SOP
outranks a generic runbook at equal similarity) since the chunk data model
(doc 3.3) doesn't carry a per-chunk confidence field; revisit if one gets
added. Empty result handling returns an explicit no-precedent signal rather
than a silently-empty list.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from geniebot.db.models import KBChunk
from geniebot.kb.vector_store import VectorStore
from geniebot.llm.client import LLMClient
from geniebot.schemas.kb import RetrievalResult, RetrievedChunk

_DOC_TYPE_PRIORITY = {"resolved_incident": 0, "sop": 1, "runbook": 2, "template": 3}


def build_query_text(error_signature: str, failing_module: str) -> str:
    return f"{error_signature.strip()} {failing_module.strip()}".strip()


async def retrieve(
    session: AsyncSession,
    vector_store: VectorStore,
    llm_client: LLMClient,
    *,
    error_signature: str,
    failing_module: str,
    error_category: str | None,
    index_version: str,
    top_n: int,
    min_similarity: float,
    rerank_margin: float,
    embedding_model: str,
) -> RetrievalResult:
    query_text = build_query_text(error_signature, failing_module)
    embedding = await llm_client.embed([query_text], model=embedding_model)
    query_vector = embedding.vectors[0]

    # Over-fetch so re-ranking has a real pool to work with beyond top_n.
    scored = await vector_store.query(
        query_vector,
        top_n=max(top_n * 3, top_n),
        index_version=index_version,
        error_category=error_category,
        min_similarity=min_similarity,
    )
    if not scored:
        return RetrievalResult(chunks=[], no_precedent=True)

    chunk_ids = [s.chunk_id for s in scored]
    result = await session.execute(select(KBChunk).where(KBChunk.chunk_id.in_(chunk_ids)))
    rows = {row.chunk_id: row for row in result.scalars().all()}

    now = datetime.now(UTC)
    similarity_by_id = {s.chunk_id: s.similarity for s in scored}
    candidates: list[RetrievedChunk] = []
    for chunk_id, similarity in similarity_by_id.items():
        row = rows.get(chunk_id)
        if row is None:
            continue
        if row.retired_at is not None and row.retired_at <= now:
            continue
        candidates.append(
            RetrievedChunk(
                chunk_id=row.chunk_id,
                content=row.content,
                source_ref=row.source_ref,
                error_category=row.error_category,
                similarity=similarity,
                effective_from=row.effective_from,
                parent_summary=row.parent_summary,
            )
        )

    if not candidates:
        return RetrievalResult(chunks=[], no_precedent=True)

    ranked = _rerank(candidates, rows, rerank_margin)
    return RetrievalResult(chunks=ranked[:top_n], no_precedent=False)


def _rerank(
    candidates: list[RetrievedChunk], rows: dict[str, KBChunk], margin: float
) -> list[RetrievedChunk]:
    def sort_key(chunk: RetrievedChunk) -> tuple:
        row = rows[chunk.chunk_id]
        doc_priority = _DOC_TYPE_PRIORITY.get(row.doc_type.value, 9)
        return (-round(chunk.similarity / margin) if margin > 0 else -chunk.similarity, doc_priority, -chunk.effective_from.timestamp())

    return sorted(candidates, key=sort_key)
