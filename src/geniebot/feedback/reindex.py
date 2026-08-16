"""Re-indexing - doc 4.5 bullets 4-5: superseded content is marked retired
(not deleted) and excluded from retrieval; index rebuilds are versioned,
with a regression in evaluation metrics triggering rollback to the prior
index_version. The versioned-rebuild-with-evaluate/promote machinery
itself lives in kb/build_pipeline.py (run_full_build); this module adds
the two feedback-loop-specific pieces: marking old chunks retired when a
newer version of the same document is approved, and a thin wrapper for
triggering a scheduled full rebuild (see scripts/ for how that gets
invoked - the doc doesn't specify a trigger mechanism beyond "index
rebuilds are versioned").
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from geniebot.audit import ledger
from geniebot.db.models import KBChunk
from geniebot.kb.build_pipeline import BuildReport, EvalCase, run_full_build
from geniebot.kb.vector_store import VectorStore
from geniebot.llm.client import LLMClient


async def mark_superseded(
    session: AsyncSession, *, source_ref: str, index_version: str, retired_at: datetime | None = None
) -> int:
    """Marks every non-retired chunk for `source_ref` in `index_version` as
    retired - used when an updated version of a document (e.g. a revised
    SOP) is indexed and the old content should stop being returned by
    retrieval without losing audit history (doc 4.5 bullet 4)."""
    retired_at = retired_at or datetime.now(UTC)
    result = await session.execute(
        select(KBChunk).where(
            KBChunk.source_ref == source_ref,
            KBChunk.index_version == index_version,
            KBChunk.retired_at.is_(None),
        )
    )
    rows = list(result.scalars().all())
    for row in rows:
        row.retired_at = retired_at
    if rows:
        await ledger.record(
            session,
            incident_id=None,
            event_type="KB_CHUNKS_RETIRED",
            payload={"source_ref": source_ref, "index_version": index_version, "count": len(rows)},
        )
        await session.commit()
    return len(rows)


async def trigger_scheduled_reindex(
    *,
    extractors: list,
    session: AsyncSession,
    vector_store: VectorStore,
    llm_client: LLMClient,
    taxonomy: dict,
    embedding_model: str,
    eval_set: list[EvalCase],
    current_recall: float,
    current_precision: float,
) -> BuildReport:
    """Convenience wrapper: builds a fresh, timestamped index_version from
    the full extractor set (initial-corpus sources plus anything captured
    via the feedback loop, if those extractors are included) and
    evaluates/promotes it per doc 4.2 steps 11-12."""
    index_version = f"v{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"
    return await run_full_build(
        extractors=extractors,
        session=session,
        vector_store=vector_store,
        llm_client=llm_client,
        taxonomy=taxonomy,
        index_version=index_version,
        embedding_model=embedding_model,
        eval_set=eval_set,
        current_recall=current_recall,
        current_precision=current_precision,
    )
